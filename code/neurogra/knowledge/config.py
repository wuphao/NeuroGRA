"""Configuration loading and result-affecting fingerprinting."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, PositiveInt, field_validator, model_validator

from neurogra.knowledge.schemas.common import StrictBaseModel


class PathConfig(StrictBaseModel):
    data_root: Path = Field(default_factory=lambda: Path("data/knowledge"))
    output_root: Path = Field(default_factory=lambda: Path("output/knowledge"))


class DatabaseConfig(StrictBaseModel):
    path: Path = Field(default_factory=lambda: Path("data/knowledge/registry.sqlite"))


class ParsingConfig(StrictBaseModel):
    adapter: Literal["docling", "pypdf_basic"] = "docling"
    ocr_policy: Literal["auto", "always", "never"] = "auto"
    min_text_chars_per_page: int = Field(default=20, ge=0)


class ChunkingConfig(StrictBaseModel):
    target_tokens: PositiveInt = 450
    max_tokens: PositiveInt = 800
    preserve_clause_context: bool = True

    @model_validator(mode="after")
    def max_tokens_must_cover_target(self) -> "ChunkingConfig":
        if self.max_tokens < self.target_tokens:
            raise ValueError("chunking.max_tokens must be greater than or equal to target_tokens")
        return self


class ExtractionConfig(StrictBaseModel):
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    prompt_version: str = "v1"
    max_transport_attempts: PositiveInt = 3

    @field_validator("base_url")
    @classmethod
    def base_url_must_be_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.rstrip("/")
        if not normalized:
            raise ValueError("extraction.base_url must be a non-empty URL when provided")
        return normalized

    @field_validator("api_key_env")
    @classmethod
    def api_key_env_must_be_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.strip() != value or not value:
            raise ValueError("extraction.api_key_env must be a non-empty environment variable name")
        return value

    def resolve_api_key(self) -> str | None:
        if self.api_key_env is None:
            return None
        return os.environ.get(self.api_key_env)


class EmbeddingConfig(StrictBaseModel):
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    dimension: PositiveInt | None = None

    @field_validator("base_url")
    @classmethod
    def base_url_must_be_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.rstrip("/")
        if not normalized:
            raise ValueError("embedding.base_url must be a non-empty URL when provided")
        return normalized

    def resolve_api_key(self) -> str | None:
        if self.api_key_env is None:
            return None
        return os.environ.get(self.api_key_env)


class GraphStoreConfig(StrictBaseModel):
    provider: Literal["jsonl", "neo4j"] = "jsonl"
    uri: str | None = None
    username: str | None = None
    password: str | None = None
    password_env: str | None = None
    database: str | None = None

    @field_validator("uri", "username", "password", "password_env", "database")
    @classmethod
    def optional_strings_must_be_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.strip() != value or not value:
            raise ValueError("graph_store string fields must be non-empty and must not have surrounding whitespace")
        return value

    @model_validator(mode="after")
    def neo4j_requires_connection_fields(self) -> "GraphStoreConfig":
        if self.provider == "neo4j":
            if not self.uri:
                raise ValueError("graph_store.uri is required when graph_store.provider is neo4j")
            if not self.username:
                raise ValueError("graph_store.username is required when graph_store.provider is neo4j")
            if not self.password and not self.password_env:
                raise ValueError(
                    "graph_store.password or graph_store.password_env is required when graph_store.provider is neo4j"
                )
        return self

    def resolve_password(self) -> str | None:
        if self.password_env is not None:
            return os.environ.get(self.password_env)
        return self.password


class RetrievalConfig(StrictBaseModel):
    lexical_top_k: PositiveInt = 20
    vector_top_k: PositiveInt = 20
    rrf_k: PositiveInt = 60
    allow_degraded: bool = False


class ReviewConfig(StrictBaseModel):
    require_clause_approval: bool = True


class BuildConfig(StrictBaseModel):
    schema_version: Literal["1"] = "1"
    paths: PathConfig = Field(default_factory=PathConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    parsing: ParsingConfig = Field(default_factory=ParsingConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    graph_store: GraphStoreConfig = Field(default_factory=GraphStoreConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)

    @model_validator(mode="after")
    def database_path_must_live_under_data_root(self) -> "BuildConfig":
        data_root = self.paths.data_root
        db_path = self.database.path
        if not db_path.is_absolute() and not data_root.is_absolute():
            try:
                db_path.relative_to(data_root)
            except ValueError as exc:
                raise ValueError("database.path must live under paths.data_root") from exc
        return self

    def fingerprint(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={
                "extraction": {"api_key_env"},
                "embedding": {"api_key_env"},
                "graph_store": {"password", "password_env"},
            },
        )
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def resolve_path(self, path: Path) -> Path:
        if path.is_absolute():
            return path
        return Path.cwd() / path


def load_config(path: str | Path | None = None) -> BuildConfig:
    """Load YAML configuration and validate it strictly."""

    config_path = Path(path) if path is not None else Path("configs/knowledge.default.yaml")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ValueError(f"Config file not found: {config_path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Config root must be a mapping")
    return BuildConfig.model_validate(raw)


def dump_schema(path: str | Path) -> None:
    """Write the BuildConfig JSON Schema for review and tooling."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    schema = BuildConfig.model_json_schema()
    output_path.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def normalize_config_dict(config: BuildConfig) -> dict[str, Any]:
    """Return a JSON-safe config representation for CLI output."""

    return config.model_dump(mode="json")
