"""Configuration independent of the host application's configuration types."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from .contracts.domain import Contract


class PathsConfig(Contract):
    data_root: Path = Path("data/kg")
    output_root: Path = Path("output/kg")


class LLMConfig(Contract):
    provider: Literal["ollama"] = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3.8:27b"
    temperature: float = Field(default=0, ge=0, le=2)
    concurrency: Literal[1] = 1
    timeout_seconds: float = Field(default=300, gt=0)
    max_transport_attempts: int = Field(default=2, ge=1, le=3)
    max_format_repair_attempts: int = Field(default=1, ge=0, le=1)
    context_tokens: int = Field(default=16384, ge=1024)
    max_output_tokens: int = Field(default=4096, ge=128)
    # Character bound is explicitly conservative, not an exact token count.
    max_input_chars: int = Field(default=14000, ge=256)
    thinking: Literal["auto", "enabled", "disabled"] = "disabled"
    structured_output: Literal["json_schema", "json"] = "json_schema"
    keep_alive: str = "0"


class EmbeddingConfig(Contract):
    provider: Literal["ollama"] = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3-embedding:0.6b"
    dimension: int | Literal["auto"] = "auto"
    batch_size: int = Field(default=4, ge=1, le=64)
    concurrency: Literal[1] = 1
    timeout_seconds: float = Field(default=120, gt=0)
    normalize: Literal["l2"] = "l2"
    truncate: Literal[False] = False
    query_template_version: Literal["raw_query_v1"] = "raw_query_v1"
    document_template_version: Literal["raw_document_v1"] = "raw_document_v1"
    keep_alive: str = "0"


class ConstructionConfig(Contract):
    localize_zh: bool = True
    alignment_weights: dict[str, float] = Field(default_factory=lambda: {
        "structure": 0.5, "entity": 0.2, "semantic": 0.3})
    alignment_threshold: float = Field(default=0.55, ge=0, le=1)
    prompt_version: Literal["v1"] = "v1"
    parser: Literal["pymupdf", "structured_json"] = "pymupdf"
    unit_max_chars: int = Field(default=6000, ge=500)


class RetrievalConfig(Contract):
    lexical_top_k: int = Field(default=30, gt=0)
    vector_top_k: int = Field(default=30, gt=0)
    graph_top_k: int = Field(default=30, gt=0)
    graph_max_hops: int = Field(default=2, ge=1, le=4)
    rrf_k: int = Field(default=60, gt=0)
    top_k: int = Field(default=10, gt=0)
    max_supplement_rounds: int = Field(default=2, ge=0, le=10)
    allow_explicit_degradation: bool = True


class CompletionConfig(Contract):
    enabled: bool = False
    model_type: Literal["complex"] = "complex"
    max_candidate_edges: int = Field(default=10, ge=1)
    dimension: int = Field(default=64, ge=1)
    epochs: int = Field(default=200, ge=1)
    learning_rate: float = Field(default=0.01, gt=0)
    seed: int = 42


class GraphConfig(Contract):
    provider: Literal["neo4j", "memory"] = "neo4j"
    uri: str = "bolt://127.0.0.1:7687"
    username: str = "neo4j"
    password_env: str = "NEUROGRA_KG_NEO4J_PASSWORD"
    password_file: Path | None = None
    database: str = "neo4j"


class KGConfig(Contract):
    schema_version: Literal["1"] = "1"
    root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2])
    paths: PathsConfig = Field(default_factory=PathsConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    construction: ConstructionConfig = Field(default_factory=ConstructionConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    completion: CompletionConfig = Field(default_factory=CompletionConfig)
    graph: GraphConfig = Field(default_factory=GraphConfig)

    def resolve(self, path: str | Path) -> Path:
        value = Path(path)
        return (value if value.is_absolute() else self.root / value).resolve()


def load_config(path: str | Path | None = None) -> KGConfig:
    if path is None:
        return KGConfig()
    path = Path(path).resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("kg configuration must be a mapping")
    raw.setdefault("root", str(path.parent.parent))
    return KGConfig.model_validate(raw)
