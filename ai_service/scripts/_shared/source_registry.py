"""Registry helpers for pipeline data sources."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_service.scripts._shared.paths import PIPELINE_SOURCES_DIR


def source_manifest_path(source_id: str) -> Path:
    return PIPELINE_SOURCES_DIR / f"{source_id}.json"


def save_source_manifest(source_id: str, payload: dict[str, Any]) -> Path:
    path = source_manifest_path(source_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_source_manifest(source_id: str) -> dict[str, Any]:
    path = source_manifest_path(source_id)
    return json.loads(path.read_text(encoding="utf-8"))


def list_source_manifests() -> list[dict[str, Any]]:
    results = []
    for path in sorted(PIPELINE_SOURCES_DIR.glob("*.json")):
        results.append(json.loads(path.read_text(encoding="utf-8")))
    return results


def enabled_source_ids() -> list[str]:
    return [
        src["source_id"]
        for src in list_source_manifests()
        if src.get("enabled", True)
    ]
