"""Prompt loader — reads .md prompt templates from this directory."""

import os
from functools import lru_cache

_DIR = os.path.dirname(__file__)


@lru_cache(maxsize=None)
def load(name: str) -> str:
    path = os.path.join(_DIR, f"{name}.md")
    with open(path, encoding="utf-8") as f:
        return f.read()
