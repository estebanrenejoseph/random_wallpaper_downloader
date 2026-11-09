# Sources/wallhaven.py

import os
import random
from typing import Any, Dict, List, Optional, Tuple

from models import SourceSpec
from network import http_get_json

WALLHAVEN_PURITY: Dict[str, str] = {
    "sfw": "100",
    "sketchy": "010",
    "sfw+sketchy": "110",
}

def _wallhaven_tag_query(tags: Optional[List[str]]) -> str:
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

SPEC = SourceSpec(
    name="Wallhaven",
    function=source_wallhaven,
    supports_tags=True,
    description="Wallhaven: Multiple tags supported. Wrap multi-word tags in quotes.",
    purity_options=[
        ("sfw", "SFW"),
        ("sketchy", "SKETCHY"),
        ("sfw+sketchy", "SFW+SKETCHY"),
    ],
    category_options=[
        ("general", "General"),
        ("anime", "Anime"),
        ("people", "People"),
    ]
)
