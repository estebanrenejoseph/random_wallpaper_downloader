# Sources/nekos.py

import random
from typing import Any, Dict, List, Optional, Tuple

from models import SourceSpec
from network import http_get_json, http_post_json

NEKOS_CONTENT: Dict[str, bool] = {
    "sfw": False,
    "nsfw": True,
}

def _nekos_image_url(item: Dict[str, Any]) -> str:
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

SPEC = SourceSpec(
    name="Nekos.moe",
    function=source_nekos,
    supports_tags=True,
    description="Nekos.moe: Multiple tags allowed. Use quotes for multi-word tags.",
    content_options=[
        ("sfw", "SFW"),
        ("nsfw", "NSFW"),
    ]
)
