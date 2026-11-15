from collections import Counter
from dataclasses import dataclass
import unicodedata

@dataclass(frozen=True)
class TextQuality:
    usable: bool
    reasons: tuple[str, ...]


def assess_text(text: str, *, has_image: bool = False, visitor_text: str | None = None) -> TextQuality:
    visible = [c for c in text if not c.isspace()]
    reasons = []
    if len(visible) < 20:
        reasons.append('too_short')
    if visible:
        bad = sum(c == '\ufffd' or unicodedata.category(c) in ('Co','Cs','Cc','Cn') for c in visible)
        if bad / len(visible) > 0.02:
            reasons.append('invalid_unicode')
        if sum(c.isalnum() for c in visible) / len(visible) < 0.6:
            reasons.append('low_text_ratio')
        if Counter(visible).most_common(1)[0][1] / len(visible) > 0.35:
            reasons.append('repetitive_text')
    if has_image and len(visible) < 80:
        reasons.append('sparse_text_over_image')
    # pypdf can drop LTR runs when Arabic/Latin switch inside one PDF text object.
    if visitor_text is not None:
        extracted = Counter(c for c in text if c.isalnum())
        observed = Counter(c for c in visitor_text if c.isalnum())
        if observed - extracted:
            reasons.append('lost_mixed_direction_runs')
    return TextQuality(not reasons, tuple(reasons))
