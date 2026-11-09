#Sources/nasa.py

from typing import Any, Dict, List, Optional, Tuple

from models import SourceSpec
from network import http_get_json

def source_nasa(
    tags: Optional[List[str]] = None,
    content: str = "sfw",
    purity: str = "sfw",
    rating: str = "safe",
    categories: Optional[List[str]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """Fetches today's Astronomy Picture of the Day (APOD) from NASA."""
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

SPEC = SourceSpec(
    name="NASA",
    function=source_nasa,
    supports_tags=False,
    description="NASA APOD: Astronomy Picture of the Day (No search tags or filters supported).",
)
