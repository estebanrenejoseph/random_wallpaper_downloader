# Sources/yandere.py

import random
from typing import Any, Dict, List, Optional, Tuple

from models import SourceSpec
from network import http_get_json

YANDERE_RATING: Dict[str, str] = {
    "safe": "rating:safe",
    "questionable": "rating:questionable",
    "explicit": "rating:explicit",
}

def source_yandere(
    tags: Optional[List[str]] = None,
    content: str = "sfw",
    purity: str = "sfw",
    rating: str = "safe",
    categories: Optional[List[str]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    if content != "sfw" or purity != "sfw" or categories:
        raise ValueError("For Yande.re, use rating and tags options.")
    if rating not in {"safe", "questionable", "explicit", "all"}:
        raise ValueError("Invalid Yande.re rating parameter.")

    query_parts: List[str] = [tag.strip() for tag in (tags or []) if tag.strip()]
    if rating != "all" and rating in YANDERE_RATING:
        query_parts.append(YANDERE_RATING[rating])

    query_parts.append("order:random")

    params: Dict[str, Any] = {
        "tags": " ".join(query_parts),
        "limit": 50,
    }

    data: Any = http_get_json("https://yande.re/post.json", params=params)
    posts: List[Dict[str, Any]] = data if isinstance(data, list) else []
    if not posts:
        raise RuntimeError("Yande.re found no images matching these criteria.")

    item: Dict[str, Any] = random.choice(posts)
    image_url: Optional[str] = (
        item.get("file_url")
        or item.get("jpeg_url")
        or item.get("sample_url")
    )

    if not image_url:
        raise RuntimeError("Yande.re did not provide a valid image URL.")

    return image_url, f"Yande.re #{item.get('id', '?')}", {
        "rating": item.get("rating"),
        "score": item.get("score"),
        "width": item.get("width"),
        "height": item.get("height"),
        "tags": item.get("tags", ""),
    }

SPEC = SourceSpec(
    name="Yande.re",
    function=source_yandere,
    supports_tags=True,
    description="Yande.re: High-resolution anime artwork board (great Danbooru & Pixiv alternative).",
    rating_options=[
        ("safe", "Safe"),
        ("questionable", "Questionable"),
        ("explicit", "Explicit"),
        ("all", "All"),
    ],
)
