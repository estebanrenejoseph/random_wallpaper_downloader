# Sources/safebooru.py

import random
from typing import Any, Dict, List, Optional, Tuple

from models import SourceSpec
from network import http_get_json

def source_safebooru(
    tags: Optional[List[str]] = None,
    content: str = "sfw",
    purity: str = "sfw",
    rating: str = "safe",
    categories: Optional[List[str]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    if content != "sfw" or purity != "sfw" or rating != "safe" or categories:
        raise ValueError("Safebooru is an SFW-only source and does not support content or rating filters.")

    user_tags: List[str] = [tag.strip() for tag in (tags or []) if tag.strip()]

    params: Dict[str, Any] = {
        "page": "dapi",
        "s": "post",
        "q": "index",
        "json": 1,
        "limit": 50,
    }

    if user_tags:
        params["tags"] = " ".join(user_tags)

    data: Any = http_get_json(
        "https://safebooru.org/index.php",
        params=params,
    )

    posts: List[Dict[str, Any]] = data if isinstance(data, list) else []
    if not posts:
        raise RuntimeError("Safebooru found no images matching these criteria.")

    item: Dict[str, Any] = random.choice(posts)

    image_url: Optional[str] = item.get("file_url")
    if not image_url and item.get("directory") and item.get("image"):
        image_url = f"https://safebooru.org/images/{item['directory']}/{item['image']}"

    if image_url and image_url.startswith("//"):
        image_url = "https:" + image_url
    elif image_url and image_url.startswith("/"):
        image_url = "https://safebooru.org" + image_url

    if not image_url:
        raise RuntimeError("Safebooru returned post metadata but no usable image URL.")

    return image_url, f"Safebooru #{item.get('id', '?')}", {
        "rating": "s",
        "score": item.get("score"),
        "width": item.get("width"),
        "height": item.get("height"),
        "tags": item.get("tags", ""),
    }

SPEC = SourceSpec(
    name="Safebooru",
    function=source_safebooru,
    supports_tags=True,
    description="Safebooru: Multiple tags allowed (SFW only).",
)
