from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field
from neurogra_kg.config import EmbeddingConfig


class KBConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2])
    data_root: Path = Path("data/kb")
    output_root: Path = Path("output/kb")
    chunk_chars: int = Field(default=1200, ge=100, le=6000)
    overlap_chars: int = Field(default=120, ge=0, le=500)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)

    def resolve(self, path):
        path = Path(path)
        return (path if path.is_absolute() else self.root / path).resolve()


def load_config(path=None):
    if path is None:
        return KBConfig()
    path = Path(path).resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("kb_configuration_must_be_mapping")
    raw.setdefault("root", str(path.parent.parent))
    return KBConfig.model_validate(raw)
