# Sources/konachan.py

import random
from typing import Any, Dict, List, Optional, Tuple

from models import SourceSpec
from network import http_get_json

KONACHAN_RATING: Dict[str, str] = {
    "safe": "rating:safe",
    "questionable": "rating:questionable",
    "explicit": "rating:explicit",
}

def source_konachan(
    tags: Optional[List[str]] = None,
    content: str = "sfw",
    purity: str = "sfw",
    rating: str = "safe",
    categories: Optional[List[str]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    if content != "sfw" or purity != "sfw" or categories:
        raise ValueError("For Konachan, use rating and tags options.")
    if rating not in {"safe", "questionable", "explicit", "all"}:
        raise ValueError("Invalid Konachan rating parameter.")

    query_parts: List[str] = [tag.strip() for tag in (tags or []) if tag.strip()]

    if rating != "all" and rating in KONACHAN_RATING:
        query_parts.append(KONACHAN_RATING[rating])

    query_parts.append("order:random")

    params: Dict[str, Any] = {
        "tags": " ".join(query_parts),
        "limit": 50,
    }

    data: Any = http_get_json(
        "https://konachan.com/post.json",
        params=params,
    )

    posts: List[Dict[str, Any]] = data if isinstance(data, list) else []
    if not posts:
        raise RuntimeError("Konachan found no images matching these criteria.")

    item: Dict[str, Any] = random.choice(posts)
    image_url: Optional[str] = (
        item.get("file_url")
        or item.get("jpeg_url")
        or item.get("sample_url")
    )

    if image_url and image_url.startswith("//"):
        image_url = "https:" + image_url

    if not image_url:
        raise RuntimeError("Konachan returned post metadata but no usable image URL.")

    return image_url, f"Konachan #{item.get('id', '?')}", {
        "rating": item.get("rating"),
        "score": item.get("score"),
        "width": item.get("width"),
        "height": item.get("height"),
        "tags": item.get("tags", ""),
    }

SPEC = SourceSpec(
    name="Konachan",
    function=source_konachan,
    supports_tags=True,
    description="Konachan: Multiple tags allowed. Rating filter is available in options.",
    rating_options=[
        ("safe", "Safe"),
        ("questionable", "Questionable"),
        ("explicit", "Explicit"),
        ("all", "All"),
    ]
)
