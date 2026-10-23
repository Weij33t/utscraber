import json
import threading
from unittest.mock import Mock

import pytest

from backend import bulk
from youtube_transcript_api._errors import IpBlocked


def captions(ident):
    return {'id': ident, 'language': 'ru', 'segments': [{'start': 3723, 'duration': 2, 'text': 'Пример'}]}


@pytest.fixture(autouse=True)
def title_lookup(monkeypatch):
    lookup = Mock(return_value='Разбор игры')
    monkeypatch.setattr(bulk, 'fetch_title', lookup)
    return lookup


def test_channel_tabs_and_duplicate_video_links(monkeypatch):
    extractor = Mock()
    extractor.extract_info.side_effect = [
        {'entries': iter([{'id': 'dQw4w9WgXcQ', 'title': 'Title'}, None])},
        {'entries': [{'id': 'dQw4w9WgXcQ'}, {'id': 'gkh07IUN7E0'}]}, None]
    wrapper = Mock()
    wrapper.__enter__ = Mock(return_value=extractor)
    wrapper.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(bulk, 'YoutubeDL', Mock(return_value=wrapper))
    videos = list(bulk.iter_videos('@channel\nhttps://youtu.be/dQw4w9WgXcQ'))
    assert [v['id'] for v in videos] == ['dQw4w9WgXcQ', 'gkh07IUN7E0']
    assert [c.args[0].rsplit('/', 1)[-1] for c in extractor.extract_info.call_args_list] == ['videos', 'shorts', 'streams']


def test_bulk_export_reuses_cache_without_provider_calls(monkeypatch, tmp_path):
    monkeypatch.setenv('SUPADATA_API_KEY', 'must-not-be-used')
    ident = 'dQw4w9WgXcQ'
    (tmp_path / f'{ident}.json').write_text(json.dumps(captions(ident)), encoding='utf-8')
    fetch = Mock(side_effect=AssertionError('Cache should avoid upstream'))
    monkeypatch.setattr(bulk, 'fetch_native', fetch)
    report = bulk.export(ident, tmp_path)
    assert report['saved'] == report['cached'] == 1
    fetch.assert_not_called()
    text = (tmp_path / 'all-transcripts.txt').read_text(encoding='utf-8')
    assert '[01:02:03] Пример' in text
    assert 'must-not-be-used' not in text


def test_bulk_block_stops_remaining_videos_and_writes_report(monkeypatch, tmp_path):
    fetch = Mock(side_effect=IpBlocked('dQw4w9WgXcQ'))
    monkeypatch.setattr(bulk, 'fetch_native', fetch)
    report = bulk.export('dQw4w9WgXcQ\ngkh07IUN7E0', tmp_path)
    assert report['blocked'] is True
    assert len(report['skipped']) == 1
    fetch.assert_called_once()
    assert json.loads((tmp_path / 'report.json').read_text(encoding='utf-8')) == report


def test_bulk_cancel_preserves_completed_files(monkeypatch, tmp_path):
    cancel = threading.Event()
    def fetch(ident):
        cancel.set()
        return captions(ident)
    monkeypatch.setattr(bulk, 'fetch_native', fetch)
    report = bulk.export('dQw4w9WgXcQ\ngkh07IUN7E0', tmp_path, cancel=cancel)
    assert report['cancelled'] is True
    assert report['saved'] == 1
    assert (tmp_path / report['files'][0]['path']).exists()
    assert not list(tmp_path.rglob('*gkh07IUN7E0*.txt'))


def test_named_files_use_video_title_topic_and_unique_id(monkeypatch, tmp_path, title_lookup):
    first, second = 'dQw4w9WgXcQ', 'gkh07IUN7E0'
    for ident in (first, second):
        (tmp_path / f'{ident}.json').write_text(json.dumps(captions(ident)), encoding='utf-8')
    title_lookup.return_value = 'Разбор: DONK / Nuke?'
    report = bulk.export(first + '\n' + second, tmp_path)
    paths = [tmp_path / f['path'] for f in report['files']]
    assert len(set(paths)) == 2
    for path in paths:
        assert path.parent.name == 'CS2 — Разборы'
        assert 'Разбор DONK Nuke' in path.name
        assert not any(c in path.name for c in ':?/*')
        assert 'Тема: CS2 — Разборы' in path.read_text(encoding='utf-8')
    title_lookup.reset_mock()
    bulk.export(first, tmp_path, topic='../Моя: тема')
    title_lookup.assert_not_called()
    stored = json.loads((tmp_path / f'{first}.json').read_text(encoding='utf-8'))
    assert stored['title'] == 'Разбор: DONK / Nuke?'
    assert stored['topicSource'] == 'manual'
    manual_paths = list((tmp_path / 'texts').rglob(f'*{first}*.txt'))
    assert len(manual_paths) == 2
    assert all(p.resolve().is_relative_to(tmp_path.resolve()) for p in manual_paths)


def test_topic_falls_back_to_text_and_handles_unknown_content():
    assert bulk.detect_topic('Видео 123', [{'text': 'Показываю раскидку гранат и смоки.'}]) == 'CS2 — Тактика и гранаты'
    assert bulk.detect_topic('Разбор микры', [{'text': 'Показываю раскидку гранат и смоки.'}]) == 'CS2 — Разборы'
    assert bulk.detect_topic('DONK СЛОМАЛ РЕЙТИНГ HLTV — РАЗБОР МИКРЫ ДОНКА', []) == 'CS2 — Разборы'
    assert bulk.detect_topic('Поездка в горы', [{'text': 'Красивый закат.'}]) == 'Другие темы'
    assert bulk.safe_name('CON.txt') == '_CON.txt'
    assert bulk.safe_name('...') == 'Без названия'
