"""Local bulk caption export. Never calls a paid transcript provider."""
import html
import json
import re
import threading
from pathlib import Path

from fastapi import HTTPException
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import IpBlocked, RequestBlocked
from yt_dlp import YoutubeDL

from backend.app import QuietLogger, TimeoutSession, channel_url, video_id


def iter_videos(source):
    """A channel or one video URL/ID per line; preserve lazy channel pagination."""
    seen = set()
    for value in source.splitlines():
        value = value.strip()
        if not value:
            continue
        try:
            ident = video_id(value)
        except HTTPException:
            target = channel_url(value)
            options = dict(quiet=True, no_warnings=True, logger=QuietLogger(),
                           extract_flat='in_playlist', lazy_playlist=True,
                           skip_download=True, socket_timeout=20, retries=1,
                           extractor_retries=1, ignoreerrors=True)
            with YoutubeDL(options) as ydl:
                found_tab = False
                for tab in ('videos', 'shorts', 'streams'):
                    data = ydl.extract_info(target.removesuffix('/videos') + '/' + tab, download=False)
                    if not data:
                        continue
                    found_tab = True
                    for entry in data.get('entries', []):
                        if entry and re.fullmatch(r'[A-Za-z0-9_-]{11}', str(entry.get('id', ''))):
                            ident = entry['id']
                            if ident not in seen:
                                seen.add(ident)
                                yield {'id': ident, 'title': entry.get('title') or ident}
                if not found_tab:
                    raise RuntimeError('YouTube не вернул список видео канала.')
        else:
            if ident not in seen:
                seen.add(ident)
                yield {'id': ident, 'title': ident}


def fetch_native(ident):
    # Deliberately bypass the hosted API and SUPADATA_API_KEY, even when set.
    with TimeoutSession() as session:
        tracks = list(YouTubeTranscriptApi(http_client=session).list(ident))
        if not tracks:
            raise RuntimeError('Нет доступных субтитров.')
        track = min(tracks, key=lambda t: ({'ru': 0, 'en': 1}.get(t.language_code.split('-')[0], 2), t.is_generated))
        segments = [{'start': s.start, 'duration': s.duration, 'text': html.unescape(s.text)} for s in track.fetch()]
        if not segments:
            raise RuntimeError('Пустые субтитры.')
        return {'id': ident, 'language': track.language_code, 'segments': segments}


def text_document(video, data):
    lines = [video['title'], f"https://www.youtube.com/watch?v={video['id']}",
             f"Язык: {data['language']}", '']
    for segment in data['segments']:
        seconds = max(0, int(segment['start']))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        lines.append(f'[{hours:02}:{minutes:02}:{seconds:02}] {segment["text"]}')
    return '\n'.join(lines) + '\n'


def export(source, folder, notify=lambda event: None, cancel=None):
    cancel = cancel or threading.Event()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    report = {'saved': 0, 'cached': 0, 'skipped': [], 'cancelled': False, 'blocked': False}
    try:
        with (folder / 'all-transcripts.txt').open('w', encoding='utf-8') as combined:
            for video in iter_videos(source):
                if cancel.is_set():
                    report['cancelled'] = True
                    break
                ident = video['id']
                notify({'message': f'Получаю: {video["title"]}', **report})
                cache_file = folder / f'{ident}.json'
                cached = False
                try:
                    data = None
                    if cache_file.exists():
                        try:
                            previous = json.loads(cache_file.read_text(encoding='utf-8'))
                            if previous.get('id') == ident and previous.get('segments') and previous.get('language'):
                                data = previous
                                cached = True
                        except (ValueError, OSError):
                            pass
                    if data is None:
                        data = fetch_native(ident)
                        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                    document = text_document(video, data)
                    (folder / f'{ident}.txt').write_text(document, encoding='utf-8')
                    combined.write(document + '\n' + '=' * 72 + '\n\n')
                    combined.flush()
                    report['saved'] += 1
                    report['cached'] += int(cached)
                except (IpBlocked, RequestBlocked):
                    report['blocked'] = True
                    report['skipped'].append({'id': ident, 'reason': 'YouTube заблокировал запросы с текущего IP. Выгрузка остановлена.'})
                    break
                except Exception as error:
                    report['skipped'].append({'id': ident, 'reason': str(error)[:700]})
                notify({'message': f'Сохранено: {report["saved"]}; пропущено: {len(report["skipped"])}', **report})
                if not cached and cancel.wait(1.5):
                    report['cancelled'] = True
                    break
    finally:
        (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report
