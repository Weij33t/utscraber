"""Local bulk caption export. Never calls a paid transcript provider."""
import html
import json
import re
import threading
from pathlib import Path

import httpx
from fastapi import HTTPException
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import IpBlocked, RequestBlocked
from yt_dlp import YoutubeDL

from backend.app import QuietLogger, TimeoutSession, channel_url, video_id


TOPICS = {
    'CS2 — Разборы': r'разбор|демк|демо|\bdemo\b|анализ матч',
    'CS2 — Тактика и гранаты': r'тактик|раскид|гранат|смок|\bsmoke|флеш|\bflash|молотов|ретейк|\bretake|стратег|\butility\b',
    'CS2 — Аим и движение': r'\bаим|\baim|стрельб|микр|движени|\bmovement|стрейф|\bstrafe|трениров',
    'CS2 — Настройки': r'настройк|конфиг|\bcfg\b|прицел|чувствительност|\bsensitivity|\bсенс|\bfps\b|оптимизац',
    'CS2 — Киберспорт и новости': r'новост|турнир|\bmajor\b|мейджор|трансфер|обновлен|патч|киберспорт|рейтинг|\bhltv\b',
    'Программирование': r'\bpython\b|\bjavascript\b|\btypescript\b|программирован|разработк|\bкод\b|\bapi\b',
}


def safe_name(value, limit=90):
    """Keep readable Unicode names without Windows path syntax or devices."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', str(value))
    value = re.sub(r'\s+', ' ', value).strip(' .')[:limit].rstrip(' .')
    value = value or 'Без названия'
    if re.fullmatch(r'(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', value):
        value = '_' + value
    return value


def detect_topic(title, segments):
    """Free keyword grouping, not an AI summary; prefer the video's title."""
    for text in (title, ' '.join(s['text'] for s in segments[:80])[:10000]):
        scores = {topic: len(set(re.findall(pattern, text.casefold()))) *
                  (2 if topic == 'CS2 — Разборы' else 0.5 if topic == 'CS2 — Киберспорт и новости' else 1)
                  for topic, pattern in TOPICS.items()}
        best = max(scores, key=scores.get)
        if scores[best]:
            return best
    return 'Другие темы'


def fetch_title(ident):
    try:
        response = httpx.get('https://www.youtube.com/oembed',
                             params={'url': f'https://www.youtube.com/watch?v={ident}', 'format': 'json'}, timeout=8)
        response.raise_for_status()
        return response.json().get('title') or ident
    except (httpx.HTTPError, ValueError):
        return ident


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
    lines = [data.get('title') or video['title'], f"Тема: {data.get('topic', 'Другие темы')}",
             f"https://www.youtube.com/watch?v={video['id']}",
             f"Язык: {data['language']}", '']
    for segment in data['segments']:
        seconds = max(0, int(segment['start']))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        lines.append(f'[{hours:02}:{minutes:02}:{seconds:02}] {segment["text"]}')
    return '\n'.join(lines) + '\n'


def export(source, folder, notify=lambda event: None, cancel=None, topic=None, pause_seconds=8):
    cancel = cancel or threading.Event()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    report = {'saved': 0, 'cached': 0, 'skipped': [], 'cancelled': False, 'blocked': False, 'files': [],
              'source': source, 'topic': topic, 'pauseSeconds': pause_seconds, 'running': True}
    def checkpoint():
        temporary = folder / '.report.json.tmp'
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(folder / 'report.json')
    checkpoint()
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
                    data['title'] = (video['title'] if video['title'] != ident else
                                     data.get('title') or fetch_title(ident))
                    data['topic'] = topic.strip() if topic and topic.strip() else detect_topic(data['title'], data['segments'])
                    data['topicSource'] = 'manual' if topic and topic.strip() else 'keywords'
                    cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                    document = text_document(video, data)
                    topic_name = safe_name(data['topic'], 40)
                    destination = folder / 'texts' / topic_name
                    destination.mkdir(parents=True, exist_ok=True)
                    # Include the ID so identical titles cannot overwrite one another.
                    prefix = safe_name(topic_name, 30) + ' — '
                    suffix = f' [{ident}].txt'
                    title_limit = min(90, max(12, 240 - len(str(destination.resolve())) - len(prefix) - len(suffix) - 1))
                    name = prefix + safe_name(data['title'], title_limit) + suffix
                    path = destination / name
                    path.write_text(document, encoding='utf-8')
                    report['files'].append({'id': ident, 'title': data['title'], 'topic': data['topic'],
                                            'path': path.relative_to(folder).as_posix()})
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
                report['lastVideoId'] = ident
                checkpoint()
                if not cached and cancel.wait(pause_seconds):
                    report['cancelled'] = True
                    break
    finally:
        report['running'] = False
        checkpoint()
    return report
