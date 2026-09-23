"""Compatibility imports for existing per-design trace scripts.

Install the project with tracing support: uv sync --extra trace.
"""
from stitch_cli.tracing import TraceConfig, WordmarkTracer

__all__ = ["TraceConfig", "WordmarkTracer"]
