# models.py

"""Data models representing image payloads and provider specifications."""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict

@dataclass
class ImageData:
    """Stores binary image content alongside its metadata and network properties.

    Attributes:
        data: Raw binary payload of the image downloaded into memory.
        url: Direct HTTP/HTTPS network URL of the image asset.
        source: Key identifier matching the source provider (e.g., 'nasa', 'wallhaven').
        title: Human-readable title or unique string identifier for the image.
        content_type: MIME type extracted from the HTTP response headers.
        metadata: Provider-specific details (e.g., tags, dimensions, NSFW flags).
    """

    data: bytes
    url: str
    source: str
    title: str = ""
    content_type: str = "application/octet-stream"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceSpec:
    """Immutable specification metadata and configuration for an image provider.

    Attributes:
        name: Human-readable provider name displayed in the interface.
        function: Handler function executing the provider's API search and fetching logic.
        supports_tags: Indicates whether the provider accepts keyword search queries.
        description: Summary describing the provider's dataset and filter capabilities.
    """

    name: str
    function: Callable[..., Any]
    supports_tags: bool
    description: str
