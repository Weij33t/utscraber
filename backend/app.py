"""Read-only YouTube adapter. No Google account or API key is used."""
import copy
import html
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from requests import Session
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    IpBlocked, NoTranscriptFound, RequestBlocked, TranscriptsDisabled,
    VideoUnavailable, VideoUnplayable,
)
from youtube_transcript_api.proxies import GenericProxyConfig
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="Текстом · YouTube API", version="1.0.0")
origins = os.getenv("ALLOWED_ORIGINS", "http://127.0.0.1:8000,http://localhost:8000").split(",")
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in origins],
                   allow_methods=["GET"], allow_headers=["Content-Type"])
slots = threading.BoundedSemaphore(3)
cache_lock = threading.Lock()
cache = {}
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def video_id(value: str) -> str:
    value = value.strip()
    if VIDEO_ID.fullmatch(value):
        return value
    url = urlparse(value if "://" in value else "https://" + value)
    host = (url.hostname or "").lower()
    parts = url.path.strip("/").split("/")
    result = ""
    if host in {"youtu.be", "www.youtu.be"}:
        result = parts[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parts[0] == "watch":
            result = parse_qs(url.query).get("v", [""])[0]
        elif parts[0] in {"shorts", "embed", "live"} and len(parts) > 1:
            result = parts[1]
    if not VIDEO_ID.fullmatch(result):
        raise HTTPException(400, "Нужна ссылка на видео YouTube или его ID из 11 символов.")
    return result


def channel_url(value: str) -> str:
    value = value.strip()
    if CHANNEL_ID.fullmatch(value):
        return f"https://www.youtube.com/channel/{value}/videos"
    if value.startswith("@"):
        value = "https://www.youtube.com/" + value
    url = urlparse(value if "://" in value else "https://" + value)
    if (url.hostname or "").lower() not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        raise HTTPException(400, "Нужна ссылка на канал YouTube или @имя канала.")
    parts = url.path.strip("/").split("/")
    if parts[0].startswith("@") and re.fullmatch(r"@[\w.%-]{1,100}", parts[0]):
        path = parts[0]
    elif len(parts) >= 2 and parts[0] in {"channel", "c", "user"} and re.fullmatch(r"[\w.%-]{1,100}", parts[1]):
        if parts[0] == "channel" and not CHANNEL_ID.fullmatch(parts[1]):
            raise HTTPException(400, "Некорректный ID канала.")
        path = "/".join(parts[:2])
    else:
        raise HTTPException(400, "Не удалось распознать ссылку на канал.")
    return f"https://www.youtube.com/{path}/videos"


def cached(key, producer, ttl=600):
    with cache_lock:
        item = cache.get(key)
        if item and item[0] > time.monotonic():
            return copy.deepcopy(item[1])
    result = producer()
    with cache_lock:
        if len(cache) >= 256:
            cache.pop(next(iter(cache)))
        cache[key] = (time.monotonic() + ttl, result)
    return copy.deepcopy(result)


@contextmanager
def upstream_slot():
    if not slots.acquire(blocking=False):
        raise HTTPException(429, "Сервер занят. Попробуйте через несколько секунд.", headers={"Retry-After": "10"})
    try:
        yield
    finally:
        slots.release()


class QuietLogger:
    def debug(self, message): pass
    def warning(self, message): pass
    def error(self, message): pass


def extract(url, **options):
    config = dict(quiet=True, no_warnings=True, logger=QuietLogger(), skip_download=True,
                  extract_flat="in_playlist", socket_timeout=15, retries=1,
                  extractor_retries=1, ignoreerrors=True, **options)
    if os.getenv("YOUTUBE_PROXY"):
        config["proxy"] = os.environ["YOUTUBE_PROXY"]
    try:
        with YoutubeDL(config) as ydl:
            data = ydl.extract_info(url, download=False)
        if not data:
            raise HTTPException(502, "YouTube не вернул данные. Возможно, доступ с IP сервера заблокирован.")
        return data
    except DownloadError as exc:
        raise HTTPException(502, "YouTube не ответил. Повторите позже или настройте YOUTUBE_PROXY на сервере.") from exc


def video_card(entry):
    ident = str(entry.get("id", ""))
    if not VIDEO_ID.fullmatch(ident):
        return None
    return {"id": ident, "title": entry.get("title") or "Видео YouTube",
            "channel": entry.get("channel") or entry.get("uploader") or "Канал YouTube",
            "channelId": entry.get("channel_id"),
            "channelUrl": entry.get("channel_url") or entry.get("uploader_url"),
            "duration": entry.get("duration"), "views": entry.get("view_count"),
            "thumbnail": f"https://i.ytimg.com/vi/{ident}/hqdefault.jpg"}


@app.get("/api/health")
def health():
    return {"ok": True, "service": "tekstom", "version": "1.0.0",
            "transcriptProvider": "supadata" if os.getenv("SUPADATA_API_KEY", "").strip() else "youtube"}


@app.get("/api/search")
def search(q: str = Query(min_length=1, max_length=200)):
    q = q.strip()
    if not q:
        raise HTTPException(400, "Введите поисковый запрос.")
    def load():
        with upstream_slot():
            data = extract(f"ytsearch24:{q}")
        cards = [video_card(e) for e in data.get("entries", []) if e]
        return {"videos": [c for c in cards if c]}
    return cached(("search", q), load)


@app.get("/api/channel")
def channel(url: str = Query(max_length=300), page: int = Query(1, ge=1, le=40)):
    target = channel_url(url)
    def load():
        with upstream_slot():
            data = extract(target, playliststart=(page-1)*24+1, playlistend=page*24+1)
        entries = [e for e in data.get("entries", []) if e]
        cards = [video_card({"channel": data.get("channel") or data.get("uploader"),
                             "channel_id": data.get("channel_id"), "channel_url": target, **e})
                 for e in entries[:24]]
        return {"channel": {"id": data.get("channel_id") or data.get("id") or target,
                            "title": data.get("channel") or data.get("uploader") or data.get("title", "Канал").removesuffix(" - Videos"),
                            "url": target}, "videos": [c for c in cards if c],
                "nextPage": page+1 if len(entries) > 24 else None}
    return cached(("channel", target, page), load)


class TimeoutSession(Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", 20)
        return super().request(method, url, **kwargs)


def supadata_transcript(ident, lang, key):
    """Fetch existing captions only; never spend credits on AI transcription."""
    params = {"url": f"https://www.youtube.com/watch?v={ident}", "mode": "native", "text": "false"}
    if lang:
        params["lang"] = lang
    def read(response):
        if response.status_code in {401, 403}:
            raise HTTPException(503, "Сервис субтитров не подключён: проверьте SUPADATA_API_KEY в настройках Render.")
        if response.status_code in {402, 429}:
            raise HTTPException(429, "Лимит сервиса субтитров исчерпан или запросы идут слишком часто. Попробуйте позже.")
        if response.status_code in {206, 404}:
            raise HTTPException(404, "У видео нет доступных субтитров. Распознавание аудио не включено.")
        response.raise_for_status()
        return response.json()
    try:
        with httpx.Client(headers={"x-api-key": key}, timeout=15) as client:
            response = client.get("https://api.supadata.ai/v1/transcript", params=params)
            data = read(response)
            if response.status_code == 202:
                job = data.get("jobId", "")
                if not re.fullmatch(r"[\w-]{1,100}", job):
                    raise ValueError("Invalid transcript job")
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    time.sleep(2)
                    data = read(client.get(f"https://api.supadata.ai/v1/transcript/{job}"))
                    if data.get("status") == "completed":
                        data = data.get("result", data)
                        break
                    if data.get("status") == "failed":
                        raise HTTPException(502, "Сервис не смог получить субтитры этого видео.")
                else:
                    raise HTTPException(504, "Сервис субтитров отвечает слишком долго. Попробуйте позже.")
            content = data.get("content")
            if not isinstance(content, list):
                raise ValueError("Expected timestamped transcript")
            segments = [{"start": float(s["offset"]) / 1000,
                         "duration": float(s.get("duration", 0)) / 1000,
                         "text": html.unescape(s["text"])} for s in content]
            if not segments:
                raise HTTPException(404, "YouTube вернул пустые субтитры.")
            language = data["lang"]
            languages = list(dict.fromkeys([language, *data.get("availableLangs", [])]))
            return {"language": language, "generated": None,
                    "languages": [{"code": code, "name": code, "generated": None} for code in languages],
                    "segments": segments}
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(502, "Не удалось получить субтитры от внешнего сервиса. Попробуйте позже.") from exc


def transcript_response(ident, captions):
    title, author = "Видео YouTube", "Канал YouTube"
    try:
        response = httpx.get("https://www.youtube.com/oembed", params={"url": f"https://www.youtube.com/watch?v={ident}", "format": "json"}, timeout=8)
        response.raise_for_status()
        meta = response.json()
        title, author = meta.get("title", title), meta.get("author_name", author)
        author_url = meta.get("author_url")
    except (httpx.HTTPError, ValueError):
        author_url = None
    return {"video": {"id": ident, "title": title, "channel": author, "channelUrl": author_url,
                      "thumbnail": f"https://i.ytimg.com/vi/{ident}/hqdefault.jpg"}, **captions}


@app.get("/api/transcript")
def transcript(url: str = Query(max_length=500), lang: str | None = Query(None, pattern=r"^[a-zA-Z0-9-]{1,20}$")):
    ident = video_id(url)
    def load():
        key = os.getenv("SUPADATA_API_KEY", "").strip()
        if key:
            with upstream_slot():
                captions = supadata_transcript(ident, lang, key)
            return transcript_response(ident, captions)
        with upstream_slot(), TimeoutSession() as session:
            proxy = os.getenv("YOUTUBE_PROXY")
            api = YouTubeTranscriptApi(http_client=session, proxy_config=GenericProxyConfig(https_url=proxy, http_url=proxy) if proxy else None)
            try:
                tracks = list(api.list(ident))
                if not tracks:
                    raise HTTPException(404, "У этого видео нет доступных субтитров.")
                if lang:
                    candidates = [t for t in tracks if t.language_code == lang]
                    if not candidates:
                        raise HTTPException(404, "Субтитров на выбранном языке нет.")
                    track = sorted(candidates, key=lambda t: t.is_generated)[0]
                else:
                    def rank(t):
                        code = t.language_code.split("-")[0]
                        return ({"ru": 0, "en": 1}.get(code, 2), t.is_generated)
                    track = sorted(tracks, key=rank)[0]
                fetched = track.fetch()
                segments = [{"start": s.start, "duration": s.duration, "text": html.unescape(s.text)} for s in fetched]
                if not segments:
                    raise HTTPException(404, "YouTube вернул пустые субтитры.")
            except (IpBlocked, RequestBlocked) as exc:
                raise HTTPException(503, "YouTube заблокировал IP сервера. Повторите позже или настройте YOUTUBE_PROXY; авторизация посетителя не нужна.") from exc
            except (TranscriptsDisabled, NoTranscriptFound) as exc:
                raise HTTPException(404, "У видео нет доступных субтитров. Автоматическое распознавание аудио пока не подключено.") from exc
            except (VideoUnavailable, VideoUnplayable) as exc:
                raise HTTPException(404, "Видео недоступно, удалено или имеет ограничения.") from exc
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(502, "Не удалось получить субтитры от YouTube. Попробуйте позже.") from exc
        return transcript_response(ident, {"language": track.language_code, "generated": track.is_generated,
                "languages": [{"code": t.language_code, "name": t.language, "generated": t.is_generated} for t in tracks],
                "segments": segments})
    return cached(("transcript", ident, lang), load, ttl=3600)


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
