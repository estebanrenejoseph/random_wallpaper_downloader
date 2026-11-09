# Sources/wikimedia.py

import random
import threading
import time
import requests
from typing import Any, Dict, List, Optional, Tuple

from models import SourceSpec
from network import http_get_json

# --- Tunables -------------------------------------------------------------
# Wikimedia asks for a descriptive User-Agent with contact info; generic ones
# are throttled much harder. Replace the contact part with your own.
WIKIMEDIA_USER_AGENT = (
    "random-wallpaper-viewer/1.0 (local desktop application)"
)
MIN_REQUEST_INTERVAL = 1.0      # seconds between two API calls
REFILL_THRESHOLD = 5            # refill when fewer unseen images remain
MAX_ITEMS_PER_QUERY = 300
THUMB_WIDTH = 1920              # must be a standard thumbnail size (1280, 1920, 3840...)
MIN_WIDTH = 1280                # ignore low-resolution images
BACKOFF_BASE = 30               # seconds, doubled on each consecutive 429
BACKOFF_MAX = 30 * 60

# --- Shared state ---------------------------------------------------------
_LOCK = threading.Lock()
# query -> {"items": [...], "seen": set(urls), "offset": int}
_WIKI_CACHE: Dict[str, Dict[str, Any]] = {}
_blocked_until: float = 0.0
_backoff_level: int = 0
_last_request: float = 0.0


def _throttle() -> None:
    """Enforces a minimum delay between two API requests."""
    global _last_request
    wait = _last_request + MIN_REQUEST_INTERVAL - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()


def _handle_rate_limit(response: Optional[requests.Response]) -> None:
    """Records a cooldown from Retry-After, with exponential backoff."""
    global _blocked_until, _backoff_level
    try:
        retry_after = float(response.headers.get("Retry-After", 0)) if response is not None else 0.0
    except ValueError:
        retry_after = 0.0
    delay = min(max(retry_after, BACKOFF_BASE * (2 ** _backoff_level)), BACKOFF_MAX)
    _backoff_level += 1
    _blocked_until = time.time() + delay
    raise RuntimeError(f"Wikimedia rate limit reached (HTTP 429). Pausing requests for {int(delay)}s.")


def _refill_cache(search_query: str, entry: Dict[str, Any]) -> None:
    """Fetches the next page of search results and merges valid images into the cache."""
    global _backoff_level
    now = time.time()
    if now < _blocked_until:
        raise RuntimeError(f"Wikimedia cooldown active, retry in {int(_blocked_until - now)}s.")

    params: Dict[str, Any] = {
        "action": "query",
        "generator": "search",
        "gsrsearch": search_query,
        "gsrnamespace": 6,
        "gsrlimit": 50,
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        # Ask for a 1920px thumbnail: served from the CDN cache and far less
        # throttled than full-size originals on upload.wikimedia.org.
        "iiurlwidth": THUMB_WIDTH,
        "format": "json",
    }
    if entry["offset"]:
        params["gsroffset"] = entry["offset"]

    _throttle()
    try:
        data: Any = http_get_json(
            "https://commons.wikimedia.org/w/api.php",
            params=params,
            headers={"User-Agent": WIKIMEDIA_USER_AGENT},
        )
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 429:
            _handle_rate_limit(exc.response)
        raise

    _backoff_level = 0

    # Continue from the next page next time, or restart from the beginning.
    entry["offset"] = (
        data.get("continue", {}).get("gsroffset", 0) if isinstance(data, dict) else 0
    )

    pages: Dict[str, Any] = data.get("query", {}).get("pages", {}) if isinstance(data, dict) else {}
    valid_mimes = {"image/jpeg", "image/png", "image/webp"}
    known = {i["url"] for i in entry["items"]}
    new_items: List[Dict[str, Any]] = []

    for page in pages.values():
        image_info_list = page.get("imageinfo", [])
        if not image_info_list:
            continue

        info = image_info_list[0]
        url = info.get("thumburl") or info.get("url", "")
        mime = info.get("mime", "").lower()
        width = info.get("width") or 0
        height = info.get("height") or 0

        clean_url = url.split("?")[0].lower()
        is_valid_ext = clean_url.endswith((".jpg", ".jpeg", ".png", ".webp"))
        is_valid_mime = mime in valid_mimes

        if not url or url in known or not (is_valid_mime or is_valid_ext):
            continue
        if width < MIN_WIDTH or width < height:
            continue

        known.add(url)
        new_items.append({
            "url": url,
            "title": page.get("title", "Wikimedia Commons File").replace("File:", ""),
            "width": width,
            "height": height,
            "extmetadata": info.get("extmetadata", {}),
        })

    entry["items"] = (new_items + entry["items"])[:MAX_ITEMS_PER_QUERY]


def source_wikimedia(
    tags: Optional[List[str]] = None,
    content: str = "sfw",
    purity: str = "sfw",
    rating: str = "safe",
    categories: Optional[List[str]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    if purity != "sfw" or rating != "safe" or categories or content != "sfw":
        raise ValueError("For Wikimedia Commons, use keyword tags for search queries.")

    user_tags: List[str] = [tag.strip() for tag in (tags or []) if tag.strip()]
    search_query = " ".join(user_tags) if user_tags else "landscape"

    with _LOCK:
        entry = _WIKI_CACHE.setdefault(
            search_query, {"items": [], "seen": set(), "offset": 0}
        )

        def unseen() -> List[Dict[str, Any]]:
            return [i for i in entry["items"] if i["url"] not in entry["seen"]]

        if len(unseen()) < REFILL_THRESHOLD:
            try:
                _refill_cache(search_query, entry)
            except Exception:
                # Only fail if there is nothing at all to show from the cache.
                if not entry["items"]:
                    raise

        candidates = unseen()
        if not candidates:
            # Everything has been shown: recycle instead of refetching.
            entry["seen"].clear()
            candidates = list(entry["items"])
        if not candidates:
            raise RuntimeError(
                f"Wikimedia Commons found no usable landscape images (JPG/PNG/WebP) for '{search_query}'."
            )

        item = random.choice(candidates)
        entry["seen"].add(item["url"])

    metadata_obj = item.get("extmetadata", {})
    artist = metadata_obj.get("Artist", {}).get("value", "Unknown")
    license_name = metadata_obj.get("LicenseShortName", {}).get("value", "Public Domain / CC")

    return item["url"], f"Wikimedia — {item['title']}", {
        "width": item.get("width"),
        "height": item.get("height"),
        "artist": artist,
        "license": license_name,
    }

SPEC = SourceSpec(
    name="Wikimedia Commons",
    function=source_wikimedia,
    supports_tags=True,
    description="Wikimedia: Enter search keywords (e.g. landscape nature).",
)
