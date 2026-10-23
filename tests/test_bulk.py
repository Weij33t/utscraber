import json
import threading
from unittest.mock import Mock

from backend import bulk
from youtube_transcript_api._errors import IpBlocked


def captions(ident):
    return {'id': ident, 'language': 'ru', 'segments': [{'start': 3723, 'duration': 2, 'text': 'Пример'}]}


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
    assert (tmp_path / 'dQw4w9WgXcQ.txt').exists()
    assert not (tmp_path / 'gkh07IUN7E0.txt').exists()
