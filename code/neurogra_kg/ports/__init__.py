"""Replaceable infrastructure boundaries."""

from typing import Protocol, Sequence
from pydantic import BaseModel

from ..contracts.domain import DocumentInput, KnowledgeStatement, ParsedDocument


class LLM(Protocol):
    def execute(self, task: str, payload: dict, schema: type[BaseModel], budget=None) -> BaseModel: ...


class Embedding(Protocol):
    def embed(self, texts: Sequence[str], budget=None) -> list[list[float]]: ...
    def model_digest(self) -> str: ...


class Parser(Protocol):
    def parse(self, document: DocumentInput) -> ParsedDocument: ...


class GraphStore(Protocol):
    def publish(self, release_id: str, statements: Sequence[KnowledgeStatement]) -> None: ...
    def neighbors(self, release_id: str, terms: list[str], max_hops: int, limit: int) -> list[str]: ...


class VectorStore(Protocol):
    def search(self, vector: Sequence[float], top_k: int) -> list[tuple[str, float]]: ...
