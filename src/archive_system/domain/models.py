from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator

class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

class DocumentType(StrEnum):
    LETTER = "letter"
    INVOICE = "invoice"
    UNKNOWN = "unknown"

class Status(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"

class ValidationStatus(StrEnum):
    VALID = "valid"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    INVALID = "invalid"
    NEEDS_REVIEW = "needs_review"

FIELD_NAMES = {
    DocumentType.LETTER: ("letter_number", "date", "sender", "recipient", "subject"),
    DocumentType.INVOICE: ("invoice_number", "date", "supplier", "total", "currency"),
    DocumentType.UNKNOWN: (),
}

class Box(Contract):
    # Coordinates in the page's declared pixel or PDF-point space.
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    unit: Literal["pixel", "point"]

class Source(Contract):
    page: int = Field(ge=1)
    snippet: str = Field(min_length=1)
    box: Box | None = None

class Document(Contract):
    id: UUID
    original_name: str = Field(min_length=1)
    media_type: Literal["application/pdf", "image/png", "image/jpeg"]
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_path: str = Field(min_length=1)
    page_count: int | None = Field(default=None, ge=1)
    status: Status = Status.RECEIVED
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def ordered_times(self):
        if self.updated_at < self.created_at:
            raise ValueError("updated_at precedes created_at")
        return self

class TextRegion(Contract):
    text: str
    box: Box
    confidence: float | None = Field(default=None, ge=0, le=1)

class Page(Contract):
    document_id: UUID
    run_id: UUID
    number: int = Field(ge=1)
    text: str
    source: Literal["digital", "ocr"]
    regions: tuple[TextRegion, ...] = ()
    confidence: float | None = Field(default=None, ge=0, le=1)

class ProcessingRun(Contract):
    id: UUID
    document_id: UUID
    engine: str
    engine_version: str
    model_version: str | None = None
    settings: dict[str, str | int | float | bool]
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    timings_seconds: dict[str, float] = Field(default_factory=dict)
    errors: tuple[str, ...] = ()
    status: Status = Status.PROCESSING

    @model_validator(mode="after")
    def consistent(self):
        if any(t < 0 for t in self.timings_seconds.values()):
            raise ValueError("negative timing")
        if self.finished_at and self.finished_at < self.started_at:
            raise ValueError("finish precedes start")
        if self.status in (Status.COMPLETED, Status.NEEDS_REVIEW, Status.FAILED) and not self.finished_at:
            raise ValueError("terminal run requires finished_at")
        if self.status == Status.COMPLETED and self.errors:
            raise ValueError("failed run cannot be completed")
        if self.status == Status.FAILED and not self.errors:
            raise ValueError("failed run requires an error code")
        return self

class Classification(Contract):
    document_id: UUID
    run_id: UUID
    category: DocumentType
    method: str
    reasons: tuple[str, ...] = ()
    # Rule scores are not calibrated probabilities.
    confidence: float | None = Field(default=None, ge=0, le=1)

class ExtractedField(Contract):
    id: UUID
    document_id: UUID
    run_id: UUID
    name: str = Field(min_length=1)
    raw_value: str | None = None
    normalized_value: str | None = None
    sources: tuple[Source, ...] = ()
    status: ValidationStatus
    reason: str | None = None
    calendar: Literal["gregorian", "hijri", "unknown"] = "unknown"

    @model_validator(mode="after")
    def evidence(self):
        if self.status == ValidationStatus.MISSING:
            if self.raw_value is not None or self.normalized_value is not None or self.sources:
                raise ValueError("missing field cannot contain a guessed value or evidence")
        elif not self.sources:
            raise ValueError("nonmissing fields require source evidence")
        if self.status == ValidationStatus.VALID and (not self.raw_value or not self.normalized_value):
            raise ValueError("valid field requires raw and normalized values")
        if self.status == ValidationStatus.AMBIGUOUS and self.normalized_value is not None:
            raise ValueError("ambiguous field cannot select a normalized value")
        return self

class Review(Contract):
    id: UUID
    document_id: UUID
    run_id: UUID
    target: Literal["field", "classification", "page", "confirmation"]
    field_id: UUID | None = None
    page_number: int | None = Field(default=None, ge=1)
    calendar: Literal["gregorian", "hijri", "unknown"] = "unknown"
    corrected_value: str | None = Field(max_length=200000)
    created_at: AwareDatetime
    note: str | None = None

    @model_validator(mode="after")
    def target_valid(self):
        if (self.target == "field") != (self.field_id is not None):
            raise ValueError("field target requires field_id only")
        if (self.target == "page") != (self.page_number is not None):
            raise ValueError("page target requires page_number only")
        return self
