"""Common local OCR boundary; confidence values are engine scores, not guarantees."""
from dataclasses import dataclass
from typing import Protocol
from PIL.Image import Image
from archive_system.domain.models import TextRegion

@dataclass(frozen=True)
class OCRText:
    text: str
    regions: tuple[TextRegion, ...] = ()
    confidence: float | None = None

class OCREngine(Protocol):
    def recognize(self, image: Image) -> OCRText: ...
