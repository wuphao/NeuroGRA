"""Text cleaning and source span construction."""

from neurogra.knowledge.processing.cleaner import SpanBuildResult, build_spans
from neurogra.knowledge.processing.segmenter import ChunkBuildResult, build_chunks

__all__ = ["ChunkBuildResult", "SpanBuildResult", "build_chunks", "build_spans"]
