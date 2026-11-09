# Sources/reddit.py

import html
import random
import re
import threading
import time
import requests
import xml.etree.ElementTree as ET
from http import cookiejar
from typing import Any, Dict, List, Optional, Tuple

from models import SourceSpec
from network import HTTP_TIMEOUT

# --- Tunables -------------------------------------------------------------
MIN_REQUEST_INTERVAL = 2.0      # seconds between two Reddit calls (option 4)
CACHE_TTL = 45 * 60             # seconds before a subreddit cache is refreshed
REFILL_THRESHOLD = 5            # refill when fewer unseen images remain
MAX_POSTS_PER_SUB = 300         # cap on cached posts per subreddit
MIN_WIDTH = 1280                # ignore low-resolution images
BACKOFF_BASE = 30               # seconds, doubled on each consecutive 429
BACKOFF_MAX = 30 * 60

# Different listings give access to far more images than "hot" alone.
_LISTINGS: List[Tuple[str, Dict[str, str]]] = [
    ("hot", {}),
    ("new", {}),
    ("top", {"t": "week"}),
    ("top", {"t": "month"}),
    ("top", {"t": "year"}),
    ("top", {"t": "all"}),
]

_IMAGE_EXT = re.compile(r"\.(?:jpg|jpeg|png|webp)(?:\?.*)?$", re.IGNORECASE)
_IMAGE_HOSTS = ("i.redd.it", "i.imgur.com", "preview.redd.it")
_MIME_EXT = {"image/jpg": "jpg", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

# --- Shared state ---------------------------------------------------------
_LOCK = threading.Lock()


class _NoCookies(cookiejar.CookiePolicy):
    """Cookie policy that refuses to store or send any cookie."""
    netscape = True
    rfc2965 = False
    hide_cookie2 = False

    def set_ok(self, cookie, request):
        return False

    def return_ok(self, cookie, request):
        return False

    def domain_return_ok(self, domain, request):
        return False

    def path_return_ok(self, path, request):
        return False


_SESSION = requests.Session()
_SESSION.cookies.set_policy(_NoCookies())
_SESSION.headers.update({
    "User-Agent": "random-wallpaper-viewer/1.0 (local desktop application)",
    "Accept": "application/json",
})
# sub -> {"posts": [...], "seen": set(urls), "fetched_at": float}
_REDDIT_CACHE: Dict[str, Dict[str, Any]] = {}
_blocked_until: float = 0.0
_backoff_level: int = 0
_last_request: float = 0.0
_use_rss: bool = False          # switched on automatically if the JSON API answers 403

_RSS_IMAGE_REGEX = re.compile(
    r'href="(https://(?:i\.imgur\.com|i\.redd\.it|preview\.redd\.it)/[^"]+\.(?:jpg|jpeg|png|webp))"',
    re.IGNORECASE,
)


def _throttle() -> None:
    """Enforces a minimum delay between two Reddit requests."""
    global _last_request
    wait = _last_request + MIN_REQUEST_INTERVAL - time.time()
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()


def _handle_rate_limit(response: requests.Response) -> None:
    """Records a cooldown from Retry-After, with exponential backoff."""
    global _blocked_until, _backoff_level
    try:
        retry_after = float(response.headers.get("Retry-After", 0))
    except ValueError:
        retry_after = 0.0
    delay = min(max(retry_after, BACKOFF_BASE * (2 ** _backoff_level)), BACKOFF_MAX)
    _backoff_level += 1
    _blocked_until = time.time() + delay
    raise RuntimeError(f"Reddit rate limit reached (HTTP 429). Pausing requests for {int(delay)}s.")


def _best_url(data: Dict[str, Any]) -> Optional[Tuple[str, int, int]]:
    """Returns (url, width, height) for a single-image post, or None."""
    source = (data.get("preview", {}).get("images") or [{}])[0].get("source", {})
    width, height = int(source.get("width", 0)), int(source.get("height", 0))

    direct = data.get("url_overridden_by_dest") or data.get("url") or ""
    host_ok = any(f"//{h}/" in direct for h in _IMAGE_HOSTS)
    if host_ok and _IMAGE_EXT.search(direct):
        return direct, width, height
    if source.get("url"):
        return html.unescape(source["url"]), width, height
    return None


def _extract_images(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extracts usable images (single posts and galleries) from a post."""
    if data.get("over_18"):
        return []

    title = data.get("title", "Untitled")
    permalink = "https://www.reddit.com" + data.get("permalink", "")
    found: List[Tuple[str, int, int]] = []

    if data.get("is_gallery") and data.get("media_metadata"):
        for item in (data.get("gallery_data") or {}).get("items", []):
            meta = data["media_metadata"].get(item.get("media_id"), {})
            ext = _MIME_EXT.get(meta.get("m", ""))
            if meta.get("status") == "valid" and ext:
                size = meta.get("s", {})
                found.append((
                    f"https://i.redd.it/{item['media_id']}.{ext}",
                    int(size.get("x", 0)), int(size.get("y", 0)),
                ))
    else:
        best = _best_url(data)
        if best:
            found.append(best)

    return [
        {"url": url, "title": title, "permalink": permalink}
        for url, w, h in found
        if (w == 0 or (w >= MIN_WIDTH and w >= h))
    ]


def _request(sub: str, listing: str, params: Dict[str, str], rss: bool) -> requests.Response:
    """Sends a throttled request to the JSON or RSS endpoint (no status check)."""
    _throttle()
    headers = (
        {"Accept": "application/atom+xml,application/xml,text/xml"} if rss else None
    )
    try:
        return _SESSION.get(
            f"https://www.reddit.com/r/{sub}/{listing}.{'rss' if rss else 'json'}",
            params=params, headers=headers, timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Network error while contacting Reddit: {exc}") from exc


def _parse_rss(content: bytes, sub: str) -> List[Dict[str, Any]]:
    """Extracts direct image links from an Atom feed."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise RuntimeError(f"Failed to parse RSS XML feed from r/{sub}.") from exc

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    images: List[Dict[str, Any]] = []
    for entry in root.findall("atom:entry", ns):
        title_elem = entry.find("atom:title", ns)
        content_elem = entry.find("atom:content", ns)
        link_elem = entry.find("atom:link", ns)
        matches = _RSS_IMAGE_REGEX.findall(
            content_elem.text if content_elem is not None and content_elem.text else ""
        )
        if matches:
            images.append({
                "url": matches[0].replace("&amp;", "&"),
                "title": title_elem.text if title_elem is not None else "Untitled",
                "permalink": link_elem.attrib.get("href", "") if link_elem is not None else "",
            })
    return images


def _refill_cache(sub: str, entry: Dict[str, Any]) -> None:
    """Fetches one random listing (up to 100 posts) and merges it into the cache."""
    global _backoff_level, _use_rss
    now = time.time()
    if now < _blocked_until:
        raise RuntimeError(f"Reddit cooldown active, retry in {int(_blocked_until - now)}s.")

    listing, extra = random.choice(_LISTINGS)
    params = {"limit": "100", "raw_json": "1", **extra}

    response = _request(sub, listing, params, rss=_use_rss)
    if response.status_code == 403 and not _use_rss:
        # JSON endpoint refused: switch to the RSS feed that worked before.
        _use_rss = True
        response = _request(sub, listing, params, rss=True)

    if response.status_code == 429:
        _handle_rate_limit(response)
    response.raise_for_status()

    if _use_rss:
        found = _parse_rss(response.content, sub)
    else:
        try:
            children = response.json()["data"]["children"]
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f"Unexpected response format from r/{sub}.") from exc
        found = [img for child in children for img in _extract_images(child.get("data", {}))]

    _backoff_level = 0
    known = {p["url"] for p in entry["posts"]}
    new_posts: List[Dict[str, Any]] = []
    for img in found:
        if img["url"] not in known:
            known.add(img["url"])
            new_posts.append(img)

    entry["posts"] = (new_posts + entry["posts"])[:MAX_POSTS_PER_SUB]
    entry["fetched_at"] = time.time()


def _pick_subreddit(subreddits: List[str]) -> str:
    """Chooses a subreddit, restricted to cached ones during a cooldown."""
    remaining = _blocked_until - time.time()
    if remaining > 0:
        cached = [s for s in subreddits if _REDDIT_CACHE.get(s, {}).get("posts")]
        if not cached:
            raise RuntimeError(f"Reddit rate limit active. Try again in {int(remaining)}s.")
        return random.choice(cached)
    return random.choice(subreddits)


def source_reddit(
    tags: Optional[List[str]] = None,
    content: str = "sfw",
    purity: str = "sfw",
    rating: str = "safe",
    categories: Optional[List[str]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    if purity != "sfw" or rating != "safe" or categories:
        raise ValueError("For Reddit, use only tags (subreddits) for target communities.")

    default_subreddits = [
        "wallpapers", "wallpaper", "art",
        "earthporn", "spaceporn",
        "animewallpapers", "AnimeWallpaper", "awwnime",
        "gamewallpapers", "gamerwalls",
        "unixporn",
        "Cyberpunk", "ImaginaryLandscapes", "ImaginaryMindscapes",
    ]

    subreddits = [
        re.sub(r"^/?r/", "", t.strip(), flags=re.IGNORECASE).strip("/")
        for t in (tags or []) if t.strip()
    ]
    subreddits = [s for s in subreddits if s] or default_subreddits

    with _LOCK:
        target_sub = _pick_subreddit(subreddits)
        entry = _REDDIT_CACHE.setdefault(
            target_sub, {"posts": [], "seen": set(), "fetched_at": 0.0}
        )

        def unseen() -> List[Dict[str, Any]]:
            return [p for p in entry["posts"] if p["url"] not in entry["seen"]]

        stale = time.time() - entry["fetched_at"] > CACHE_TTL
        if stale or len(unseen()) < REFILL_THRESHOLD:
            try:
                _refill_cache(target_sub, entry)
            except Exception:
                # Only fail if there is nothing at all to show from the cache.
                if not entry["posts"]:
                    raise

        candidates = unseen()
        if not candidates:
            # Everything has been shown: recycle instead of refetching.
            entry["seen"].clear()
            candidates = list(entry["posts"])
        if not candidates:
            raise RuntimeError(f"No direct image posts found in r/{target_sub}.")

        item = random.choice(candidates)
        entry["seen"].add(item["url"])

    post_title = f"Reddit r/{target_sub} — {item['title']}"
    return item["url"], post_title, {
        "subreddit": target_sub,
        "permalink": item["permalink"],
    }

SPEC = SourceSpec(
    name="Reddit",
    function=source_reddit,
    supports_tags=True,
    description="Reddit: Enter subreddit names separated by spaces (e.g. wallpapers earthporn).",
)
