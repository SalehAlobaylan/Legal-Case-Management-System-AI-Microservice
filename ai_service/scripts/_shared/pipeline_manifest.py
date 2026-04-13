"""Helpers for versioned pipeline runs and artifact manifests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_service.scripts._shared.paths import (
    AI_SERVICE_ROOT,
    PIPELINE_LATEST_MODEL,
    PIPELINE_LATEST_RUN,
    PIPELINE_MODELS_DIR,
    PIPELINE_RUNS_DIR,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(AI_SERVICE_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def create_run_manifest(
    run_id: str,
    *,
    source_ids: list[str],
    description: str | None = None,
    base_model: str = "BAAI/bge-m3",
    train_enabled: bool = True,
) -> dict[str, Any]:
    data_root = PIPELINE_RUNS_DIR / run_id
    model_root = PIPELINE_MODELS_DIR / run_id
    manifest = {
        "run_id": run_id,
        "description": description or "",
        "status": "created",
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "source_ids": source_ids,
        "base_model": base_model,
        "data_root": _rel(data_root),
        "model_root": _rel(model_root),
        "artifacts": {
            "sources_dir": _rel(data_root / "sources"),
            "merged_raw": _rel(data_root / "merged" / "judgments.jsonl"),
            "citations": _rel(data_root / "citations" / "citations.jsonl"),
            "citations_stats": _rel(data_root / "citations" / "citations_stats.json"),
            "qa_sample": _rel(data_root / "qa" / "qa_sample.csv"),
            "triplets_dir": _rel(data_root / "triplets"),
            "train_jsonl": _rel(data_root / "triplets" / "train.jsonl"),
            "val_jsonl": _rel(data_root / "triplets" / "val.jsonl"),
            "triplets_stats": _rel(data_root / "triplets" / "stats.json"),
            "model_dir": _rel(model_root / "model"),
            "checkpoints_dir": _rel(model_root / "checkpoints"),
            "evaluation_report": _rel(model_root / "evaluation_report.json"),
        },
        "sources": [],
        "stages": {
            "ingest": {"status": "pending", "started_at": None, "finished_at": None},
            "merge": {"status": "pending", "started_at": None, "finished_at": None},
            "citations": {"status": "pending", "started_at": None, "finished_at": None},
            "qa": {"status": "pending", "started_at": None, "finished_at": None},
            "triplets": {"status": "pending", "started_at": None, "finished_at": None},
            "train": {
                "status": "pending" if train_enabled else "skipped",
                "started_at": None,
                "finished_at": None,
            },
        },
        "commands": [],
        "notes": [],
    }
    save_manifest(manifest)
    return manifest


def manifest_path(run_id: str) -> Path:
    return PIPELINE_RUNS_DIR / run_id / "run_manifest.json"


def load_manifest(run_id: str) -> dict[str, Any]:
    return json.loads(manifest_path(run_id).read_text(encoding="utf-8"))


def save_manifest(manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = _utc_now()
    _write_json(manifest_path(manifest["run_id"]), manifest)


def append_note(manifest: dict[str, Any], note: str) -> None:
    manifest.setdefault("notes", []).append({"at": _utc_now(), "message": note})


def register_source_artifact(
    manifest: dict[str, Any],
    *,
    source_id: str,
    source_kind: str,
    output_path: Path,
    metadata: dict[str, Any],
) -> None:
    manifest.setdefault("sources", [])
    manifest["sources"] = [
        s for s in manifest["sources"] if s.get("source_id") != source_id
    ]
    manifest["sources"].append(
        {
            "source_id": source_id,
            "kind": source_kind,
            "artifact": _rel(output_path),
            "metadata": metadata,
        }
    )


def record_command(
    manifest: dict[str, Any],
    *,
    stage: str,
    command: list[str],
    outputs: list[Path] | None = None,
) -> None:
    manifest.setdefault("commands", []).append(
        {
            "stage": stage,
            "at": _utc_now(),
            "command": command,
            "outputs": [_rel(path) for path in (outputs or [])],
        }
    )


def mark_stage(
    manifest: dict[str, Any],
    stage: str,
    *,
    status: str,
    started: bool = False,
    finished: bool = False,
    error: str | None = None,
) -> None:
    info = manifest["stages"].setdefault(stage, {})
    info["status"] = status
    if started:
        info["started_at"] = _utc_now()
    if finished:
        info["finished_at"] = _utc_now()
    if error:
        info["error"] = error


def set_latest_run_alias(run_id: str) -> None:
    _write_json(
        PIPELINE_LATEST_RUN,
        {"run_id": run_id, "manifest": _rel(manifest_path(run_id)), "updated_at": _utc_now()},
    )


def set_latest_model_alias(run_id: str, model_dir: Path) -> None:
    _write_json(
        PIPELINE_LATEST_MODEL,
        {"run_id": run_id, "model_dir": _rel(model_dir), "updated_at": _utc_now()},
    )
