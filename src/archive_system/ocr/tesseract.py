"""Persistent native API: one loaded ara+eng model per process/configuration."""
import hashlib
from functools import lru_cache
from pathlib import Path
from threading import Lock
from PIL import Image
from archive_system.domain.models import Box, TextRegion
from .base import OCRText

WEIGHTS = {
    'ara.traineddata': 'e3206d3dc87fd50c24a0fb9f01838615911d25168f4e64415244b67d2bb3e729',
    'eng.traineddata': '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2',
}

class MissingModelError(RuntimeError):
    pass

class TesseractEngine:
    def __init__(self, model_dir: Path, psm: int = 6):
        if psm not in (3, 6):
            raise ValueError("UNSUPPORTED_PAGE_SEGMENTATION")
        self.psm = psm
        for name, digest in WEIGHTS.items():
            file = model_dir / name
            if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
                raise MissingModelError(f'LOCAL_MODEL_MISSING_OR_CHANGED: {name}; run explicit setup')
        import tesserocr
        self.version = tesserocr.tesseract_version().splitlines()[0]
        self._api = tesserocr.PyTessBaseAPI(path=str(model_dir), lang='ara+eng',
                                          oem=tesserocr.OEM.LSTM_ONLY, psm=psm)
        self._lock = Lock()

    def recognize(self, image: Image.Image) -> OCRText:
        from tesserocr import RIL
        with self._lock:
            self._api.SetImage(image.convert('RGB'))
            self._api.SetSourceResolution(200)
            self._api.Recognize()
            text = self._api.GetUTF8Text()
            regions = []
            iterator = self._api.GetIterator()
            if iterator:
                while True:
                    word = None if iterator.Empty(RIL.WORD) else iterator.GetUTF8Text(RIL.WORD)
                    box = iterator.BoundingBox(RIL.WORD)
                    if word and box and box[2] > box[0] and box[3] > box[1]:
                        regions.append(TextRegion(text=word, box=Box(x=box[0],y=box[1],width=box[2]-box[0],height=box[3]-box[1],unit='pixel'),
                                                  confidence=max(0,min(1,iterator.Confidence(RIL.WORD)/100))))
                    if not iterator.Next(RIL.WORD):
                        break
            confidence = max(0,min(1,self._api.MeanTextConf()/100)) if text.strip() else None
            self._api.Clear()  # releases page results, retains loaded model
            return OCRText(text=text, regions=tuple(regions), confidence=confidence)

@lru_cache(maxsize=1)
def _cached_tesseract(model_dir: Path, psm: int) -> TesseractEngine:
    return TesseractEngine(model_dir, psm=psm)

def get_tesseract(model_dir: Path, psm: int = 6) -> TesseractEngine:
    return _cached_tesseract(model_dir.resolve(), psm)
