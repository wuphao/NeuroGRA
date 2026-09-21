"""Protocols for replaceable model clients."""

from __future__ import annotations

from typing import Protocol, Sequence

from pydantic import BaseModel


class ExtractionClient(Protocol):
    """Structured extraction provider boundary."""

    def extract(self, prompt: str, output_schema: type[BaseModel]) -> BaseModel:
        """Return a Pydantic-validated extraction payload."""


class EmbeddingClient(Protocol):
    """Embedding provider boundary."""

    @property
    def dimension(self) -> int:
        """Embedding vector dimension."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed text batch in order."""
