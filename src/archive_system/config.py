from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field

class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_pages: int = Field(default=10, ge=1)
    max_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    runtime_dir: Path = Path.home() / ".local/share/smartgov-archive"
    workers: int = Field(default=1, ge=1, le=1)
    model_dir: Path = Path.home() / ".local/share/smartgov-models/tesseract"
    accepted_types: tuple[str, ...] = ("application/pdf", "image/png", "image/jpeg")

    @classmethod
    def from_environment(cls):
        import os
        values={}
        for key,variable in [('runtime_dir','SMARTGOV_DATA_DIR'),('model_dir','SMARTGOV_MODEL_DIR'),('max_pages','SMARTGOV_MAX_PAGES'),('max_bytes','SMARTGOV_MAX_BYTES')]:
            if variable in os.environ:values[key]=os.environ[variable]
        return cls(**values)
