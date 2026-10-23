from unittest.mock import Mock

import pytest

from backend import bulk
from backend.browser_transcript import parse_rows, BrowserSetupError, BrowserStopped


def test_user_dom_timestamps_text_duplicates_and_hours():
    rows = [
        {'timestamp': '0:00', 'text': 'Первый фрагмент'},
        {'timestamp': '0:07', 'text': 'Второй фрагмент'},
        {'timestamp': '0:16', 'text': 'Третий фрагмент'},
        {'timestamp': '0:07', 'text': 'Второй фрагмент'},
        {'timestamp': '16 seconds', 'text': 'Accessibility label'},
        {'timestamp': '1:02:03', 'text': 'Фрагмент через час'},
    ]
    segments = parse_rows(rows, duration=3730)
    assert [s['start'] for s in segments] == [0, 7, 16, 3723]
    assert [s['duration'] for s in segments] == [7, 9, 3707, 7]
    assert segments[1]['text'] == 'Второй фрагмент'


def test_browser_export_reuses_one_session_and_never_calls_direct_api(monkeypatch, tmp_path):
    first, second = 'dQw4w9WgXcQ', 'gkh07IUN7E0'
    monkeypatch.setattr(bulk, 'iter_videos', lambda source: iter([
        {'id': first, 'title': first}, {'id': second, 'title': second}]))
    fake = Mock()
    fake.fetch.side_effect = lambda ident: {'id': ident, 'title': 'Browser title', 'language': 'und',
        'segments': [{'start': 7, 'duration': 9, 'text': 'Текст из HTML'}], 'source': 'browser'}
    wrapper = Mock(__enter__=Mock(return_value=fake), __exit__=Mock(return_value=False))
    launch = Mock(return_value=wrapper)
    direct = Mock(side_effect=AssertionError('Must not call the direct API'))
    monkeypatch.setattr(bulk, 'BrowserTranscriptReader', launch)
    monkeypatch.setattr(bulk, 'fetch_native', direct)
    monkeypatch.setattr(bulk, 'fetch_title', Mock(side_effect=AssertionError('Browser already provided title')))
    report = bulk.export(first + '\n' + second, tmp_path, method='browser', pause_seconds=0)
    assert report['saved'] == 2
    assert report['method'] == 'browser'
    launch.assert_called_once()
    wrapper.__exit__.assert_called_once()
    direct.assert_not_called()
    assert (tmp_path / report['files'][0]['path']).is_file()
    assert '[00:00:07] Текст из HTML' in (tmp_path / 'all-transcripts.txt').read_text(encoding='utf-8')


@pytest.mark.parametrize('error,raises', [(BrowserSetupError('No browser'), True), (BrowserStopped('Closed'), False)])
def test_browser_failures_stop_instead_of_skipping_whole_channel(monkeypatch, tmp_path, error, raises):
    monkeypatch.setattr(bulk, 'iter_videos', lambda source: iter([
        {'id': 'dQw4w9WgXcQ', 'title': 'One'}, {'id': 'gkh07IUN7E0', 'title': 'Two'}]))
    fake = Mock(); fake.fetch.side_effect = error
    wrapper = Mock(__enter__=Mock(return_value=fake), __exit__=Mock(return_value=False))
    monkeypatch.setattr(bulk, 'BrowserTranscriptReader', Mock(return_value=wrapper))
    if raises:
        with pytest.raises(BrowserSetupError):
            bulk.export('ignored', tmp_path, method='browser')
    else:
        report = bulk.export('ignored', tmp_path, method='browser')
        assert report['cancelled'] is True
    fake.fetch.assert_called_once()
    wrapper.__exit__.assert_called_once()
