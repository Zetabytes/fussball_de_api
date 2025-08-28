from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Dict

from cachetools import TTLCache
import os
import pickle
import httpx
import hashlib
import json
import logging
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)


@dataclass
class HttpCacheEntry:
    """
    Generic cache entry for any HTTP request.
    Stores the original and final URLs, headers, content, and validation metadata.
    """
    url: str
    final_url: str
    status_code: int
    headers: Dict[str, str]
    content: Optional[bytes]
    text: Optional[str]
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    expires_at: Optional[datetime] = None
    content_file: Optional[str] = None

    def load_content(self) -> Optional[bytes]:
        if self.content_file and Path(self.content_file).exists():
            return Path(self.content_file).read_bytes()
        return None

    def load_text(self) -> Optional[str]:
        content = self.load_content()
        if content is not None:
            try:
                return content.decode("utf-8")
            except UnicodeDecodeError:
                return None
        return None


@dataclass
class FetchedResponse:
    """
    Lightweight replacement for httpx.Response used inside caching.
    Contains already-decompressed data (safe to read directly).
    """
    url: str
    status_code: int
    headers: Dict[str, str]
    content: bytes
    text: Optional[str]


# Unified HTTP response cache
http_cache = TTLCache(maxsize=1024, ttl=settings.CACHE_TTL_GAMES)

# Object-level cache for full club info responses
from .schemas import FullClubInfoResponse
club_info_cache: Dict[str, FullClubInfoResponse] = {}

CACHE_DIR: Path = settings.CACHE_DIR
CACHE_DIR.mkdir(parents=True, exist_ok=True)

CACHE_DUMP_FILE = CACHE_DIR / "fussball_cache.json"


def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def fetch_url(
    url: str,
    method: str = "GET",
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    ttl: int = settings.CACHE_TTL_GAMES,
) -> Optional[FetchedResponse]:
    """
    Transparent cache-enabled HTTP fetch.
    Returns cached or live response as a FetchedResponse.
    """
    entry: Optional[HttpCacheEntry] = http_cache.get(url)
    now = datetime.now(timezone.utc)

    if entry and entry.expires_at and entry.expires_at > now:
        logger.debug(f"CACHE HIT: {url}")
        return FetchedResponse(
            url=entry.final_url,
            status_code=entry.status_code,
            headers=entry.headers,
            content=entry.load_content() or b"",
            text=entry.load_text(),
        )

    headers = {}
    if entry and entry.etag:
        headers["If-None-Match"] = entry.etag
    if entry and entry.last_modified:
        headers["If-Modified-Since"] = entry.last_modified

    # If cache entry exists but expired, try a HEAD request first
    if entry and entry.expires_at and entry.expires_at <= now:
        try:
            with httpx.Client(follow_redirects=True) as client:
                head_resp = client.head(url, headers={})
            if head_resp.status_code == 200:
                new_etag = head_resp.headers.get("ETag")
                new_last_mod = head_resp.headers.get("Last-Modified")
                if (new_etag and new_etag == entry.etag) or (
                    new_last_mod and new_last_mod == entry.last_modified
                ):
                    logger.debug(f"HEAD check: no change for {url}, extending TTL")
                    entry.expires_at = now + timedelta(seconds=ttl)
                    http_cache[url] = entry
                    return FetchedResponse(
                        url=entry.final_url,
                        status_code=entry.status_code,
                        headers=entry.headers,
                        content=entry.load_content() or b"",
                        text=entry.load_text(),
                    )
                else:
                    logger.debug(f"HEAD check: resource changed for {url}, will refetch.")
        except httpx.RequestError as e:
            logger.warning(f"HEAD request failed for {url}: {e}")

    try:
        with httpx.Client(follow_redirects=True) as client:
            resp = client.request(method, url, headers=headers)
    except httpx.RequestError as e:
        logger.error(f"HTTP request failed: {e}")
        return None

    if resp.status_code == 304 and entry:
        entry.expires_at = now + timedelta(seconds=ttl)
        http_cache[url] = entry
        return FetchedResponse(
            url=entry.final_url,
            status_code=entry.status_code,
            headers=entry.headers,
            content=entry.load_content() or b"",
            text=entry.load_text(),
        )

    if resp.status_code >= 400:
        logger.warning(f"Caching negative response {resp.status_code} for {url}")

        # Store negative entry with short TTL (max 5 minutes)
        negative_entry = HttpCacheEntry(
            url=url,
            final_url=str(resp.url),
            status_code=resp.status_code,
            headers=dict(resp.headers),
            content=None,
            text=None,
            etag=None,
            last_modified=None,
            expires_at=now + timedelta(seconds=min(300, ttl)),
        )
        http_cache[url] = negative_entry

        return FetchedResponse(
            url=str(resp.url),
            status_code=resp.status_code,
            headers=dict(resp.headers),
            content=b"",
            text=None,
        )

    content_bytes = resp.read()
    text_str = resp.text

    hash_value = _url_hash(url)
    content_file = CACHE_DIR / f"{hash_value}.bin"
    content_file.write_bytes(content_bytes)

    metadata = {
        "url": url,
        "final_url": str(resp.url),
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "etag": resp.headers.get("ETag"),
        "last_modified": resp.headers.get("Last-Modified"),
        "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
        "content_file": str(content_file),
    }
    meta_file = CACHE_DIR / f"{hash_value}_metadata.json"
    meta_file.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    new_entry = HttpCacheEntry(
        url=url,
        final_url=str(resp.url),
        status_code=resp.status_code,
        headers=dict(resp.headers),
        content=None,
        text=None,
        etag=resp.headers.get("ETag"),
        last_modified=resp.headers.get("Last-Modified"),
        expires_at=now + timedelta(seconds=ttl),
        content_file=str(content_file),
    )
    http_cache[url] = new_entry

    return FetchedResponse(
        url=str(resp.url),
        status_code=resp.status_code,
        headers=dict(resp.headers),
        content=content_bytes,
        text=text_str,
    )


def save_caches_to_file():
    """
    Saves redirects and club_info_cache metadata as JSON in CACHE_DUMP_FILE.
    Uses flush + fsync to ensure data is written fully to disk.
    """
    try:
        # Only persist the prewarmed club (if configured), to avoid storing arbitrary clubs
        target_id = settings.PREWARM_CLUB_ID
        if target_id:
            filtered_cache = {
                target_id: model.model_dump()
                for cid, model in club_info_cache.items()
                if cid == target_id
            }
        else:
            filtered_cache = {}

        data = {
            "redirects": {url: entry.final_url for url, entry in http_cache.items()},
            "club_info_cache": filtered_cache,
        }
        with open(CACHE_DUMP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        logger.info(f"Saved caches to {CACHE_DUMP_FILE}")
    except Exception as e:
        logger.error(f"Failed to save caches: {e}")


def load_caches_from_file():
    """
    Loads redirects and club_info_cache from CACHE_DUMP_FILE if it exists.
    Entries from metadata.json files are rebuilt into http_cache.
    """
    if not CACHE_DUMP_FILE.exists():
        return

    try:
        file_size = CACHE_DUMP_FILE.stat().st_size
        if file_size > 10 * 1024 * 1024:  # 10 MB
            logger.warning(
                f"Cache file {CACHE_DUMP_FILE} exceeds 10 MB ({file_size} bytes). Deleting..."
            )
            CACHE_DUMP_FILE.unlink(missing_ok=True)
            return

        with open(CACHE_DUMP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        for url, final in data.get("redirects", {}).items():
            hash_value = _url_hash(url)
            meta_file = CACHE_DIR / f"{hash_value}_metadata.json"
            if meta_file.exists():
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    expires_at = (
                        datetime.fromisoformat(meta.get("expires_at"))
                        if meta.get("expires_at")
                        else None
                    )
                    entry = HttpCacheEntry(
                        url=meta["url"],
                        final_url=meta["final_url"],
                        status_code=meta["status_code"],
                        headers=meta.get("headers", {}),
                        etag=meta.get("etag"),
                        last_modified=meta.get("last_modified"),
                        expires_at=expires_at,
                        content_file=meta.get("content_file"),
                        content=None,
                        text=None,
                    )
                    http_cache[url] = entry
                except Exception as e:
                    logger.error(f"Failed to restore cache entry for {url}: {e}")

        from .schemas import FullClubInfoResponse
        loaded: Dict[str, FullClubInfoResponse] = {}
        for club_id, payload in data.get("club_info_cache", {}).items():
            try:
                loaded[club_id] = FullClubInfoResponse(**payload)
            except Exception as e:
                logger.error(f"Failed to restore club_info_cache for {club_id}: {e}")

        target_id = settings.PREWARM_CLUB_ID
        if target_id:
            # Keep only the prewarmed club in RAM
            filtered = {target_id: loaded[target_id]} if target_id in loaded else {}
            club_info_cache.clear()
            club_info_cache.update(filtered)
            logger.info(
                "Loaded prewarmed club_info_cache for %s (entries: %d)",
                target_id,
                len(filtered),
            )
        else:
            # No prewarm configured -> avoid keeping any club cache in RAM
            club_info_cache.clear()
            logger.info("No PREWARM_CLUB_ID set; cleared club_info_cache on load.")
    except Exception as e:
        logger.error(f"Failed to load caches: {e}")
