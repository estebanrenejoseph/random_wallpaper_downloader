# Sources/wikimedia.py

import random
from typing import Any, Dict, List, Optional, Tuple

from models import SourceSpec
from network import http_get_json

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

    params: Dict[str, Any] = {
        "action": "query",
        "generator": "search",
        "gsrsearch": search_query,
        "gsrnamespace": 6,
        "gsrlimit": 50,
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "format": "json",
    }

    data: Any = http_get_json(
        "https://commons.wikimedia.org/w/api.php",
        params=params,
    )

    pages: Dict[str, Any] = data.get("query", {}).get("pages", {}) if isinstance(data, dict) else {}
    if not pages:
        raise RuntimeError(f"Wikimedia Commons found no items matching '{search_query}'.")

    valid_items: List[Dict[str, Any]] = []
    valid_mimes = {"image/jpeg", "image/png", "image/webp"}

    for page in pages.values():
        image_info_list = page.get("imageinfo", [])
        if not image_info_list:
            continue

        info = image_info_list[0]
        url = info.get("url", "")
        mime = info.get("mime", "").lower()

        clean_url = url.split("?")[0].lower()
        is_valid_ext = clean_url.endswith((".jpg", ".jpeg", ".png", ".webp"))
        is_valid_mime = mime in valid_mimes

        if url and (is_valid_mime or is_valid_ext):
            valid_items.append({
                "url": url,
                "title": page.get("title", "Wikimedia Commons File").replace("File:", ""),
                "width": info.get("width"),
                "height": info.get("height"),
                "extmetadata": info.get("extmetadata", {}),
            })

    if not valid_items:
        raise RuntimeError(f"Wikimedia Commons returned files for '{search_query}', but no raster images (JPG/PNG).")

    item = random.choice(valid_items)
    image_url = item["url"]
    clean_title = item["title"]

    metadata_obj = item.get("extmetadata", {})
    artist = metadata_obj.get("Artist", {}).get("value", "Unknown")
    license_name = metadata_obj.get("LicenseShortName", {}).get("value", "Public Domain / CC")

    return image_url, f"Wikimedia — {clean_title}", {
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
