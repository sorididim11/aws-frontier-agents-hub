"""Submit tool schema builder — Bedrock toolSpec 생성 유틸리티."""

from __future__ import annotations


def build_submit_tool(name: str, description: str, schema: dict) -> dict:
    """Bedrock toolSpec 형식의 submit tool 정의 생성."""
    return {
        "toolSpec": {
            "name": name,
            "description": description,
            "inputSchema": {"json": schema},
        }
    }
