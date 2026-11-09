# Sources/reddit.py

import random
import re
import requests
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

from models import SourceSpec
from network import HTTP_TIMEOUT

_REDDIT_CACHE: Dict[str, List[Dict[str, str]]] = {}

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

    subreddits = [t.strip().lstrip("r/").lstrip("/") for t in (tags or []) if t.strip()]
    if not subreddits:
        subreddits = default_subreddits

    target_sub = random.choice(subreddits)

    if target_sub not in _REDDIT_CACHE or not _REDDIT_CACHE[target_sub]:
        url = f"https://www.reddit.com/r/{target_sub}/hot.rss"
        headers = {
            "User-Agent": "desktop:wallpaper-viewer:v1.0.0 (by /u/wallpaper_client)",
            "Accept": "application/atom+xml,application/xml,text/xml",
        }

        try:
            response = requests.get(url, headers=headers, timeout=HTTP_TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            if response.status_code == 429:
                raise RuntimeError(
                    f"Reddit rate limit reached (HTTP 429). Cycling through cached items..."
                ) from exc
            raise

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise RuntimeError(f"Failed to parse RSS XML feed from r/{target_sub}.") from exc

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)

        if not entries:
            raise RuntimeError(f"No RSS entries found on subreddit r/{target_sub}.")

        fetched_posts: List[Dict[str, str]] = []
        image_regex = re.compile(
            r'href="(https://(?:i\.imgur\.com|i\.redd\.it|preview\.redd\.it)/[^"]+\.(?:jpg|jpeg|png|webp))"',
            re.IGNORECASE,
        )

        for entry in entries:
            title_elem = entry.find("atom:title", ns)
            content_elem = entry.find("atom:content", ns)
            link_elem = entry.find("atom:link", ns)

            title = title_elem.text if title_elem is not None else "Untitled"
            content_html = content_elem.text if content_elem is not None else ""
            permalink = link_elem.attrib.get("href", "") if link_elem is not None else ""

            matches = image_regex.findall(content_html)
            if matches:
                image_url = matches[0].replace("&amp;", "&")
                fetched_posts.append({
                    "url": image_url,
                    "title": title,
                    "permalink": permalink,
                })

        if not fetched_posts:
            raise RuntimeError(
                f"No direct image posts found in the r/{target_sub} RSS feed."
            )

        _REDDIT_CACHE[target_sub] = fetched_posts

    cached_index = random.randrange(len(_REDDIT_CACHE[target_sub]))
    item = _REDDIT_CACHE[target_sub].pop(cached_index)

    image_url = item["url"]
    post_title = f"Reddit r/{target_sub} — {item['title']}"

    return image_url, post_title, {
        "subreddit": target_sub,
        "permalink": item["permalink"],
    }

SPEC = SourceSpec(
    name="Reddit",
    function=source_reddit,
    supports_tags=True,
    description="Reddit: Enter subreddit names separated by spaces (e.g. wallpapers earthporn).",
)
