"""Root conftest — shared pytest fixtures for all test categories."""
import os
import pytest

# Set mock LLM provider for all tests — no API calls, no keys required
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("SQLITE_DB_PATH", ":memory:")
os.environ.setdefault("KUZU_DB_PATH", "/tmp/test_knowledge_graph")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
