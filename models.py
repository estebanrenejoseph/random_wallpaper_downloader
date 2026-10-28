# models.py
from dataclasses import dataclass, field

@dataclass
class ImageData:
    """Structure contenant les données binaires et les métadonnées d'une image."""
    data: bytes
    url: str
    source: str
    title: str = ""
    content_type: str = "application/octet-stream"
    metadata: dict = field(default_factory=dict)

@dataclass(frozen=True)
class SourceSpec:
    """Spécification et configuration d'une source d'image."""
    name: str
    function: object
    supports_tags: bool
    description: str
