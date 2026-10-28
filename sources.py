"sources.py"

"""Provider modules and registry for fetching wallpaper images from external APIs."""

import os
import random
import requests
import re

from typing import Any, Dict, List, Optional, Tuple
from models import SourceSpec
from network import http_get_json, http_post_json
import xml.etree.ElementTree as ET
from network import HTTP_TIMEOUT

# ---------------------------------------------------------------------------
# NASA APOD Source
# ---------------------------------------------------------------------------


def source_nasa(
    tags: Optional[List[str]] = None,
    content: str = "sfw",
    purity: str = "sfw",
    rating: str = "safe",
    categories: Optional[List[str]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """Fetches today's Astronomy Picture of the Day (APOD) from NASA.

    Args:
        tags: Optional tag list (unsupported by NASA APOD).
        content: Content filtering level (unsupported by NASA APOD).
        purity: Purity filter level (unsupported by NASA APOD).
        rating: Rating filter level (unsupported by NASA APOD).
        categories: Category list (unsupported by NASA APOD).

    Returns:
        Tuple[str, str, Dict[str, Any]]: Tuple containing the image URL, title,
            and metadata dictionary (date, copyright).

    Raises:
        ValueError: If any unsupported search parameters or filters are passed.
        RuntimeError: If the API response format is invalid or non-image content is returned.
    """
    if tags or content != "sfw" or purity != "sfw" or rating != "safe" or categories:
        raise ValueError("NASA APOD source does not support search filters or tags.")

    data: Any = http_get_json(
        "https://science.nasa.gov/wp-json/wp/v2/apod-basic",
        params={"api_key": "DEMO_KEY"},
    )

    if isinstance(data, list):
        if not data:
            raise RuntimeError("NASA API returned an empty response.")
        item: Dict[str, Any] = data[0]
    elif isinstance(data, dict):
        item = data
    else:
        raise RuntimeError("Unexpected response structure from NASA API.")

    if item.get("media_type") != "image":
        raise RuntimeError("The current NASA APOD entry is not an image asset.")

    image_url: Optional[str] = item.get("hdurl") or item.get("url")
    if not image_url or not image_url.startswith(("http://", "https://")):
        raise RuntimeError("NASA API did not provide a valid image URL.")

    return image_url, item.get("title", "NASA APOD"), {
        "date": item.get("date"),
        "copyright": item.get("copyright"),
    }


# ---------------------------------------------------------------------------
# Wallhaven Source
# ---------------------------------------------------------------------------

WALLHAVEN_PURITY: Dict[str, str] = {
    "sfw": "100",
    "sketchy": "010",
    "sfw+sketchy": "110",
}


def _wallhaven_tag_query(tags: Optional[List[str]]) -> str:
    """Formats a list of tag keywords into Wallhaven's query syntax format.

    Single-word tags are prefixed with '+', while multi-word tags containing spaces
    are wrapped in quotes (e.g. '+"gothic lolita"').

    Args:
        tags: List of tag search strings.

    Returns:
        str: Space-separated search query string formatted for Wallhaven API.
    """
    parts: List[str] = []

    for tag in tags or []:
        clean: str = tag.strip()
        if not clean:
            continue

        if any(ch.isspace() for ch in clean):
            escaped: str = clean.replace('"', '\\"')
            parts.append(f'+"{escaped}"')
        else:
            parts.append(f"+{clean}")

    return " ".join(parts)


def source_wallhaven(
    tags: Optional[List[str]] = None,
    content: str = "sfw",
    purity: str = "sfw",
    rating: str = "safe",
    categories: Optional[List[str]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """Queries Wallhaven API for wallpapers matching search criteria and picks one randomly.

    Args:
        tags: List of keyword search tags.
        content: Content mode parameter (must be 'sfw').
        purity: Purity filter level ('sfw', 'sketchy', or 'sfw+sketchy').
        rating: Rating filter parameter (must be 'safe').
        categories: List of categories to include ('general', 'anime', 'people').

    Returns:
        Tuple[str, str, Dict[str, Any]]: Tuple containing image URL, title identifier,
            and metadata dictionary (resolution, category, purity, tags).

    Raises:
        ValueError: If invalid parameters or categories are provided.
        RuntimeError: If no matching images were found or no valid URL was provided.
    """
    if purity not in WALLHAVEN_PURITY:
        raise ValueError("Invalid Wallhaven purity parameter.")
    if content != "sfw" or rating != "safe":
        raise ValueError("For Wallhaven, use purity, category, and tags filters.")

    if categories:
        wanted = set(categories)
        category_value = "".join(
            "1" if name in wanted else "0"
            for name in ("general", "anime", "people")
        )
        if category_value == "000":
            raise ValueError("At least one Wallhaven category must be selected.")
    else:
        category_value = "111"

    params: Dict[str, Any] = {
        "categories": category_value,
        "purity": WALLHAVEN_PURITY[purity],
        "sorting": "random",
        "page": 1,
    }

    if tags:
        params["q"] = _wallhaven_tag_query(tags)

    api_key: Optional[str] = os.getenv("WALLHAVEN_API_KEY")
    if api_key:
        params["apikey"] = api_key

    data: Any = http_get_json(
        "https://wallhaven.cc/api/v1/search",
        params=params,
    )
    results: List[Dict[str, Any]] = data.get("data", []) if isinstance(data, dict) else []
    if not results:
        raise RuntimeError(
            "Wallhaven found no images matching these criteria. "
            "Please check your tags, purity settings, categories, or API key."
        )

    item: Dict[str, Any] = random.choice(results)
    image_url: Optional[str] = item.get("path")
    if not image_url:
        raise RuntimeError("Wallhaven did not provide a valid image URL.")

    return image_url, f"Wallhaven #{item.get('id', '?')}", {
        "resolution": (item.get("dimension_x"), item.get("dimension_y")),
        "category": item.get("category"),
        "purity": item.get("purity"),
        "tags": item.get("tags", []),
    }


# ---------------------------------------------------------------------------
# Danbooru Source
# ---------------------------------------------------------------------------

DANBOORU_RATING: Dict[str, str] = {
    "safe": "s",
    "questionable": "q",
    "explicit": "e",
}


def source_danbooru(
    tags: Optional[List[str]] = None,
    content: str = "sfw",
    purity: str = "sfw",
    rating: str = "safe",
    categories: Optional[List[str]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """Queries Danbooru API for random posts matching tag and rating filters.

    Args:
        tags: List of keyword search tags (maximum 2 for anonymous requests).
        content: Content mode parameter (must be 'sfw').
        purity: Purity filter parameter (must be 'sfw').
        rating: Rating filter parameter ('safe', 'questionable', 'explicit', 'all').
        categories: Category parameter (unsupported by Danbooru).

    Returns:
        Tuple[str, str, Dict[str, Any]]: Tuple containing image URL, post identifier,
            and metadata dictionary (rating, score, dimensions, tag string).

    Raises:
        ValueError: If unsupported parameters are used or tag count exceeds the limit.
        RuntimeError: If no posts are returned or no direct image file URL is available.
    """
    if content != "sfw" or purity != "sfw" or categories:
        raise ValueError("For Danbooru, use rating and tags options.")
    if rating not in {"safe", "questionable", "explicit", "all"}:
        raise ValueError("Invalid Danbooru rating parameter.")

    user_tags: List[str] = [tag.strip() for tag in (tags or []) if tag.strip()]
    if len(user_tags) > 2:
        raise ValueError(
            "Danbooru limits anonymous searches to a maximum of 2 user tags."
        )

    query_parts: List[str] = list(user_tags)
    if rating != "all":
        query_parts.append(f"rating:{DANBOORU_RATING[rating]}")

    params: Dict[str, Any] = {
        "tags": " ".join(query_parts),
        "limit": 1,
        "random": "true",
    }

    data: Any = http_get_json(
        "https://danbooru.donmai.us/posts.json",
        params=params,
        headers={
            "User-Agent": "image-viewer-v5/1.0 (Danbooru client)",
            "Accept": "application/json",
        },
    )

    posts: List[Dict[str, Any]] = data.get("posts", []) if isinstance(data, dict) else data
    if not posts:
        raise RuntimeError("Danbooru found no images matching these criteria.")

    post: Dict[str, Any] = posts[0]
    image_url: Optional[str] = (
        post.get("file_url")
        or post.get("large_file_url")
        or post.get("preview_file_url")
    )

    if image_url and image_url.startswith("//"):
        image_url = "https:" + image_url
    elif image_url and image_url.startswith("/"):
        image_url = "https://danbooru.donmai.us" + image_url

    if not image_url:
        raise RuntimeError(
            "Danbooru returned post metadata but no usable image URL."
        )

    return image_url, f"Danbooru #{post.get('id', '?')}", {
        "rating": post.get("rating"),
        "score": post.get("score"),
        "width": post.get("image_width"),
        "height": post.get("image_height"),
        "tags": post.get("tag_string", ""),
    }


# ---------------------------------------------------------------------------
# Nekos.moe Source
# ---------------------------------------------------------------------------

NEKOS_CONTENT: Dict[str, bool] = {
    "sfw": False,
    "nsfw": True,
}


def _nekos_image_url(item: Dict[str, Any]) -> str:
    """Builds a direct public HTTPS file URL from a Nekos.moe image object.

    Args:
        item: Raw image dictionary returned by the Nekos.moe API.

    Returns:
        str: Direct web URL pointing to the image asset.

    Raises:
        RuntimeError: If no valid direct URL or image ID is present.
    """
    direct: Optional[str] = (
        item.get("image_url")
        or item.get("url")
        or item.get("file_url")
    )
    if direct and direct.startswith(("http://", "https://")):
        return direct

    image_id: Optional[str] = item.get("id")
    if not image_id:
        raise RuntimeError("Nekos.moe did not return a valid image identifier.")
    return f"https://nekos.moe/image/{image_id}.jpg"


def source_nekos(
    tags: Optional[List[str]] = None,
    content: str = "sfw",
    purity: str = "sfw",
    rating: str = "safe",
    categories: Optional[List[str]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """Queries Nekos.moe API for images matching content and tag criteria.

    Args:
        tags: List of tag search strings.
        content: Content mode parameter ('sfw' or 'nsfw').
        purity: Purity filter parameter (must be 'sfw').
        rating: Rating filter parameter (must be 'safe').
        categories: Category parameter (unsupported by Nekos.moe).

    Returns:
        Tuple[str, str, Dict[str, Any]]: Tuple containing image URL, image identifier,
            and metadata dictionary (NSFW flag, tags, artist).

    Raises:
        ValueError: If unsupported parameters are passed.
        RuntimeError: If no images match the requested filters.
    """
    if content not in NEKOS_CONTENT:
        raise ValueError("Nekos.moe requires content mode to be 'sfw' or 'nsfw'.")
    if purity != "sfw" or rating != "safe" or categories:
        raise ValueError("For Nekos.moe, use content mode and tags options.")

    user_tags: List[str] = [tag.strip() for tag in (tags or []) if tag.strip()]

    if user_tags:
        payload: Dict[str, Any] = {
            "nsfw": NEKOS_CONTENT[content],
            "tags": user_tags,
            "sort": "relevance",
            "skip": 0,
            "limit": 20,
        }
        data: Any = http_post_json(
            "https://nekos.moe/api/v1/images/search",
            payload=payload,
        )
        items: List[Dict[str, Any]] = data.get("images", []) if isinstance(data, dict) else []
    else:
        data = http_get_json(
            "https://nekos.moe/api/v1/random/image",
            params={
                "nsfw": str(NEKOS_CONTENT[content]).lower(),
                "count": 1,
            },
        )
        items = data.get("images", []) if isinstance(data, dict) else []

    if not items:
        raise RuntimeError("Nekos.moe found no images matching these criteria.")

    item: Dict[str, Any] = random.choice(items)
    image_url: str = _nekos_image_url(item)

    return image_url, f"Nekos.moe #{item.get('id', '?')}", {
        "nsfw": item.get("nsfw"),
        "tags": item.get("tags", []),
        "artist": item.get("artist"),
    }

# ---------------------------------------------------------------------------
# Redit Source
# ---------------------------------------------------------------------------


# Global in-memory cache to prevent HTTP 429 Rate Limit errors
_REDDIT_CACHE: Dict[str, List[Dict[str, str]]] = {}


def source_reddit(
    tags: Optional[List[str]] = None,
    content: str = "sfw",
    purity: str = "sfw",
    rating: str = "safe",
    categories: Optional[List[str]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """Fetches image wallpapers from Reddit subreddits using RSS feeds with local caching.

    Args:
        tags: Optional list of subreddit names (e.g. ['wallpapers', 'earthporn']).
        content: Content mode filter (unsupported by RSS feeds).
        purity: Purity filter parameter (unsupported by Reddit).
        rating: Rating filter parameter (unsupported by Reddit).
        categories: Category list (unsupported by Reddit).

    Returns:
        Tuple[str, str, Dict[str, Any]]: Tuple containing image URL, post title,
            and metadata dictionary (subreddit, permalink).

    Raises:
        ValueError: If unsupported parameters are passed.
        RuntimeError: If rate limited (HTTP 429) or no direct image posts are available.
    """
    if purity != "sfw" or rating != "safe" or categories:
        raise ValueError("For Reddit, use only tags (subreddits) for target communities.")

    default_subreddits = ["wallpapers", "wallpaper", "earthporn", "art"]
    subreddits = [t.strip().lstrip("r/").lstrip("/") for t in (tags or []) if t.strip()]
    if not subreddits:
        subreddits = default_subreddits

    target_sub = random.choice(subreddits)

    # Populate cache from network only if empty for the target subreddit
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

    # Pop a random item from local cache without triggering a network request
    cached_index = random.randrange(len(_REDDIT_CACHE[target_sub]))
    item = _REDDIT_CACHE[target_sub].pop(cached_index)

    image_url = item["url"]
    post_title = f"Reddit r/{target_sub} — {item['title']}"

    return image_url, post_title, {
        "subreddit": target_sub,
        "permalink": item["permalink"],
    }

# ---------------------------------------------------------------------------
# Source Registry
# ---------------------------------------------------------------------------

SOURCES: Dict[str, SourceSpec] = {
    "nasa": SourceSpec(
        "NASA",
        source_nasa,
        False,
        "NASA APOD — Astronomy Picture of the Day",
    ),
    "wallhaven": SourceSpec(
        "Wallhaven",
        source_wallhaven,
        True,
        "Wallpapers with tags, categories, and purity filters",
    ),
    "danbooru": SourceSpec(
        "Danbooru",
        source_danbooru,
        True,
        "Anime artwork database with tags and content rating filters",
    ),
    "nekos": SourceSpec(
        "Nekos.moe",
        source_nekos,
        True,
        "Anime image community with tag and SFW/NSFW filters",
    ),
    "reddit": SourceSpec(
        "Reddit",
        source_reddit,
        True,
        "Images depuis des subreddits (ex. wallpapers, art) avec filtre NSFW",
    ),
}
