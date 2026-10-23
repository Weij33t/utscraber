from importlib import import_module
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

mod = import_module("backend.app")
client = TestClient(mod.app)


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    mod.cache.clear()
    monkeypatch.delenv("SUPADATA_API_KEY", raising=False)


@pytest.mark.parametrize("url", [
    "dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ?t=12",
    "youtube.com/watch?v=dQw4w9WgXcQ", "https://m.youtube.com/shorts/dQw4w9WgXcQ",
    "https://youtube.com/live/dQw4w9WgXcQ",
])
def test_video_urls(url):
    assert mod.video_id(url) == "dQw4w9WgXcQ"


@pytest.mark.parametrize("url", ["https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ", "https://localhost/a", "https://youtube.com/watch?v=no"])
def test_reject_urls(url):
    with pytest.raises(HTTPException):
        mod.video_id(url)


def test_channel_validation():
    assert mod.channel_url("@пример").endswith("/@пример/videos")
    with pytest.raises(HTTPException):
        mod.channel_url("https://example.com/@name")


def test_health_and_static():
    assert client.get("/api/health").json()["service"] == "tekstom"
    assert "Текстом" in client.get("/").text


def test_search_cache_filters_failed_entries(monkeypatch):
    extract = Mock(return_value={"entries": [None, {"id": "invalid"}, {"id": "dQw4w9WgXcQ", "title": "Test", "channel": "Author"}]})
    monkeypatch.setattr(mod, "extract", extract)
    for _ in range(2):
        res = client.get("/api/search", params={"q": "hello"})
        assert res.status_code == 200
        assert len(res.json()["videos"]) == 1
    extract.assert_called_once()


def test_channel_pagination(monkeypatch):
    extract = Mock(return_value={"id": "channel", "channel": "Author", "entries": [{"id": "dQw4w9WgXcQ"}] * 25})
    monkeypatch.setattr(mod, "extract", extract)
    data = client.get("/api/channel", params={"url": "@author", "page": 2}).json()
    assert len(data["videos"]) == 24
    assert data["videos"][0]["channel"] == "Author"
    assert data["nextPage"] == 3
    assert extract.call_args.kwargs == {"playliststart": 25, "playlistend": 49}


def test_transcript_languages_cache_and_unescape(monkeypatch):
    en = SimpleNamespace(language_code="en", language="English", is_generated=False, fetch=Mock())
    ru = SimpleNamespace(language_code="ru", language="Russian", is_generated=True,
                         fetch=Mock(return_value=[SimpleNamespace(start=12.5, duration=4, text="A &amp; Б")]))
    api = Mock(); api.list.return_value = [en, ru]
    monkeypatch.setattr(mod, "YouTubeTranscriptApi", Mock(return_value=api))
    response = Mock(); response.json.return_value = {"title": "Title", "author_name": "Author"}
    monkeypatch.setattr(mod.httpx, "get", Mock(return_value=response))
    for _ in range(2):
        res = client.get("/api/transcript", params={"url": "dQw4w9WgXcQ"})
        assert res.status_code == 200
        data = res.json()
        assert data["language"] == "ru"
        assert data["segments"][0] == {"start": 12.5, "duration": 4, "text": "A & Б"}
    ru.fetch.assert_called_once()
    en.fetch.assert_not_called()


def test_youtube_block_is_actionable_error(monkeypatch):
    api = Mock(); api.list.side_effect = mod.IpBlocked("dQw4w9WgXcQ")
    monkeypatch.setattr(mod, "YouTubeTranscriptApi", Mock(return_value=api))
    res = client.get("/api/transcript", params={"url": "dQw4w9WgXcQ"})
    assert res.status_code == 503
    assert "YOUTUBE_PROXY" in res.json()["detail"]


def test_invalid_parameters_never_reach_upstream(monkeypatch):
    extract = Mock(); monkeypatch.setattr(mod, "extract", extract)
    assert client.get("/api/channel", params={"url": "https://localhost", "page": 1}).status_code == 400
    assert client.get("/api/channel", params={"url": "@name", "page": 0}).status_code == 422
    extract.assert_not_called()


def test_hosted_transcript_native_mode_timestamps_and_cache(monkeypatch):
    monkeypatch.setenv("SUPADATA_API_KEY", "test-private-key")
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"lang": "ru", "availableLangs": ["ru", "en"],
            "content": [{"offset": 12500, "duration": 3400, "text": "A &amp; Б"}]})
    real_client = httpx.Client
    monkeypatch.setattr(mod.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    response = Mock(); response.json.return_value = {"title": "Test"}
    monkeypatch.setattr(mod.httpx, "get", Mock(return_value=response))
    direct = Mock(); monkeypatch.setattr(mod, "YouTubeTranscriptApi", direct)
    for _ in range(2):
        response = client.get('/api/transcript', params={"url": "dQw4w9WgXcQ", "lang": "ru"})
        assert response.status_code == 200
        data = response.json()
        assert data["segments"] == [{"start": 12.5, "duration": 3.4, "text": "A & Б"}]
        assert data["generated"] is None
        assert [x['code'] for x in data['languages']] == ['ru', 'en']
        assert "test-private-key" not in response.text
    assert len(calls) == 1
    assert dict(calls[0].url.params) == {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mode": "native", "text": "false", "lang": "ru"}
    assert calls[0].headers['x-api-key'] == 'test-private-key'
    direct.assert_not_called()


@pytest.mark.parametrize('upstream,status', [(401, 503), (402, 429), (429, 429), (206, 404), (500, 502)])
def test_hosted_transcript_failures_do_not_expose_credentials(monkeypatch, upstream, status):
    real_client = httpx.Client
    monkeypatch.setattr(mod.httpx, "Client", lambda **kw: real_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(upstream, json={"secret": "test-private-key"})), **kw))
    with pytest.raises(HTTPException) as error:
        mod.supadata_transcript('dQw4w9WgXcQ', None, 'test-private-key')
    assert error.value.status_code == status
    assert 'test-private-key' not in error.value.detail


def test_hosted_transcript_polls_existing_job_without_resubmitting(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(202, json={'jobId': 'job-123'})
        return httpx.Response(200, json={'status': 'completed', 'result': {
            'lang': 'en', 'content': [{'text': 'Hello', 'offset': 0, 'duration': 1000}]}})
    real_client = httpx.Client
    monkeypatch.setattr(mod.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(mod.time, 'sleep', lambda seconds: None)
    data = mod.supadata_transcript('dQw4w9WgXcQ', None, 'test-key')
    assert data['segments'][0]['duration'] == 1
    assert calls == ['/v1/transcript', '/v1/transcript/job-123']
