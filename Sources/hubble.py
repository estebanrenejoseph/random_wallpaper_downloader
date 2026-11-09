# Sources/hubble.py

import random
from typing import Any, Dict, List, Optional, Tuple

from models import SourceSpec
from network import http_get_json

def source_hubble(
    tags: Optional[List[str]] = None,
    content: str = "sfw",
    purity: str = "sfw",
    rating: str = "safe",
    categories: Optional[List[str]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    if purity != "sfw" or rating != "safe" or categories or content != "sfw":
        raise ValueError("For Hubble/Webb source, use keyword tags for search queries.")

    user_tags: List[str] = [tag.strip() for tag in (tags or []) if tag.strip()]

    if user_tags:
        search_query = random.choice(user_tags)
    else:
        search_query = random.choice(["hubble", "webb", "nebula", "galaxy", "deep field", "star"])

    params: Dict[str, Any] = {
        "q": search_query,
        "media_type": "image",
    }

    data: Any = http_get_json(
        "https://images-api.nasa.gov/search",
        params=params,
    )

    items: List[Dict[str, Any]] = data.get("collection", {}).get("items", []) if isinstance(data, dict) else []

    if not items and user_tags:
        data = http_get_json("https://images-api.nasa.gov/search", params={"q": "hubble", "media_type": "image"})
        items = data.get("collection", {}).get("items", []) if isinstance(data, dict) else []

    if not items:
        raise RuntimeError("NASA Space Telescope Library found no images matching these criteria.")

    valid_items: List[Tuple[str, Dict[str, Any]]] = []
    for item in items:
        item_data_list = item.get("data", [])
        if not item_data_list:
            continue
        item_data = item_data_list[0]

        links = item.get("links", [])
        image_url = None
        for link in links:
            href = link.get("href", "")
            clean_href = href.split("?")[0].lower()
            if clean_href.endswith((".jpg", ".jpeg", ".png", ".webp")):
                image_url = href
                break

        if not image_url and links:
            image_url = links[0].get("href")

        if image_url:
            valid_items.append((image_url, item_data))

    if not valid_items:
        raise RuntimeError("NASA Library returned search results, but no direct image URLs were valid.")

    selected_url, selected_meta = random.choice(valid_items)
    nasa_id = selected_meta.get("nasa_id", "?")
    title = selected_meta.get("title", "NASA Space Imagery")

    return selected_url, f"NASA Telescope #{nasa_id} — {title}", {
        "nasa_id": nasa_id,
        "date_created": selected_meta.get("date_created"),
        "center": selected_meta.get("center"),
        "keywords": selected_meta.get("keywords", []),
    }

SPEC = SourceSpec(
    name="Hubble & James Webb",
    function=source_hubble,
    supports_tags=True,
    description="Hubble/Webb: Enter search keywords (e.g. nebula galaxy).",
)
