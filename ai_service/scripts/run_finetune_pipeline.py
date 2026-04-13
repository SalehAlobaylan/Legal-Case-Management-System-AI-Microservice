#!/usr/bin/env python3
"""Unified fine-tuning pipeline orchestration."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from ai_service.scripts._shared.paths import AI_SERVICE_ROOT, ensure_dirs
from ai_service.scripts._shared.pipeline_manifest import (
    append_note,
    create_run_manifest,
    load_manifest,
    manifest_path,
    mark_stage,
    record_command,
    register_source_artifact,
    save_manifest,
    set_latest_model_alias,
    set_latest_run_alias,
)
from ai_service.scripts._shared.source_registry import (
    enabled_source_ids,
    list_source_manifests,
    load_source_manifest,
    save_source_manifest,
)


def _abs(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return (AI_SERVICE_ROOT.parent / path).resolve()


def _artifact_path(manifest: dict[str, Any], key: str) -> Path:
    return (AI_SERVICE_ROOT / manifest["artifacts"][key]).resolve()


def _legacy_path_str(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(AI_SERVICE_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def _split_stages(raw: str | None) -> list[str]:
    order = ["ingest", "merge", "citations", "qa", "triplets", "train"]
    if not raw:
        return order
    stages = [part.strip() for part in raw.split(",") if part.strip()]
    invalid = [stage for stage in stages if stage not in order]
    if invalid:
        raise ValueError(f"Invalid stages: {invalid}")
    return stages


def _copy_jsonl(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "r", encoding="utf-8") as in_f, open(
        dst, "w", encoding="utf-8"
    ) as out_f:
        shutil.copyfileobj(in_f, out_f)


def _merge_jsonl_by_key(inputs: list[Path], output: Path, key: str) -> dict[str, int]:
    output.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    kept = 0
    duplicates = 0
    invalid = 0
    with open(output, "w", encoding="utf-8") as out_f:
        for input_path in inputs:
            with open(input_path, "r", encoding="utf-8") as in_f:
                for line in in_f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        invalid += 1
                        continue
                    key_val = obj.get(key)
                    if not key_val:
                        invalid += 1
                        continue
                    if key_val in seen:
                        duplicates += 1
                        continue
                    seen.add(key_val)
                    out_f.write(json.dumps(obj, ensure_ascii=False) + "\n")
                    kept += 1
    return {"kept": kept, "duplicates": duplicates, "invalid": invalid}


def _run_command(
    manifest: dict[str, Any],
    *,
    stage: str,
    command: list[str],
    outputs: list[Path] | None = None,
) -> None:
    record_command(manifest, stage=stage, command=command, outputs=outputs)
    save_manifest(manifest)
    subprocess.run(command, check=True, cwd=AI_SERVICE_ROOT.parent)


def _select_sources(args: argparse.Namespace, manifest: dict[str, Any] | None) -> list[str]:
    if args.all_enabled_sources:
        source_ids = enabled_source_ids()
    elif args.source_id:
        source_ids = args.source_id
    elif manifest is not None and manifest.get("source_ids"):
        source_ids = manifest["source_ids"]
    else:
        raise ValueError("Provide --source-id or --all-enabled-sources")
    if not source_ids:
        raise ValueError("No sources selected")
    return source_ids


def _add_jsonl_source(args: argparse.Namespace) -> None:
    payload = {
        "source_id": args.source_id,
        "kind": "jsonl_file",
        "enabled": not args.disabled,
        "description": args.description or "",
        "config": {
            "path": args.path,
            "dedupe_key": args.key,
        },
    }
    path = save_source_manifest(args.source_id, payload)
    logger.info(f"Registered JSONL source at {path}")


def _add_moj_source(args: argparse.Namespace) -> None:
    payload = {
        "source_id": args.source_id,
        "kind": "moj_scrape",
        "enabled": not args.disabled,
        "description": args.description or "",
        "config": {
            "start_page": args.start_page,
            "max_pages": args.max_pages,
            "delay": args.delay,
            "page_gap": args.page_gap,
            "list_fail_sleep": args.list_fail_sleep,
            "waf_cooldown": args.waf_cooldown,
            "court_type": args.court_type,
            "page_size": args.page_size,
        },
    }
    path = save_source_manifest(args.source_id, payload)
    logger.info(f"Registered MOJ source at {path}")


def _list_sources() -> None:
    sources = list_source_manifests()
    if not sources:
        logger.info("No registered sources.")
        return
    print(json.dumps(sources, ensure_ascii=False, indent=2))


def _run_ingest_source(
    manifest: dict[str, Any],
    source: dict[str, Any],
) -> Path:
    source_id = source["source_id"]
    out_path = _artifact_path(manifest, "sources_dir") / source_id / "raw.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    kind = source["kind"]
    config = source.get("config", {})

    if kind == "jsonl_file":
        src = _abs(config["path"])
        if not src.exists():
            raise FileNotFoundError(f"Source file not found: {src}")
        _copy_jsonl(src, out_path)
        metadata = {"input_path": str(src), "dedupe_key": config.get("dedupe_key", "id")}
    elif kind == "moj_scrape":
        command = [
            sys.executable,
            "-m",
            "ai_service.scripts.scrape_moj_judgments",
            "--output",
            str(out_path),
            "--start-page",
            str(config.get("start_page", 1)),
            "--max-pages",
            str(config.get("max_pages", 0)),
            "--delay",
            str(config.get("delay", 0.75)),
            "--page-gap",
            str(config.get("page_gap", 2.0)),
            "--list-fail-sleep",
            str(config.get("list_fail_sleep", 60.0)),
            "--waf-cooldown",
            str(config.get("waf_cooldown", 600.0)),
            "--page-size",
            str(config.get("page_size", 12)),
        ]
        if config.get("court_type") is not None:
            command.extend(["--court-type", str(config["court_type"])])
        _run_command(manifest, stage="ingest", command=command, outputs=[out_path])
        metadata = config
    else:
        raise ValueError(f"Unsupported source kind: {kind}")

    register_source_artifact(
        manifest,
        source_id=source_id,
        source_kind=kind,
        output_path=out_path,
        metadata=metadata,
    )
    return out_path


def _run_pipeline(args: argparse.Namespace) -> None:
    ensure_dirs()
    stages = _split_stages(args.stages)
    run_id = args.run_id

    if manifest_path(run_id).exists():
        manifest = load_manifest(run_id)
        append_note(manifest, "Reusing existing run manifest")
    else:
        manifest = None

    source_ids = _select_sources(args, manifest)
    if manifest is None:
        manifest = create_run_manifest(
            run_id,
            source_ids=source_ids,
            description=args.description,
            base_model=args.base_model,
            train_enabled="train" in stages,
        )
        append_note(manifest, "Pipeline run created by orchestrator")
    else:
        manifest["source_ids"] = source_ids
        manifest["base_model"] = args.base_model

    save_manifest(manifest)

    current_stage = "setup"
    try:
        source_outputs: list[Path] = []
        if "ingest" in stages:
            current_stage = "ingest"
            mark_stage(manifest, "ingest", status="running", started=True)
            save_manifest(manifest)
            for source_id in source_ids:
                source = load_source_manifest(source_id)
                source_outputs.append(_run_ingest_source(manifest, source))
            mark_stage(manifest, "ingest", status="completed", finished=True)
            save_manifest(manifest)
        else:
            source_outputs = [
                _abs(src["artifact"])
                if Path(src["artifact"]).is_absolute()
                else (AI_SERVICE_ROOT / src["artifact"]).resolve()
                for src in manifest.get("sources", [])
            ]

        if "merge" in stages:
            current_stage = "merge"
            mark_stage(manifest, "merge", status="running", started=True)
            save_manifest(manifest)
            merged_raw = _artifact_path(manifest, "merged_raw")
            merge_stats = _merge_jsonl_by_key(source_outputs, merged_raw, key="id")
            append_note(manifest, f"Merged raw records: {merge_stats}")
            mark_stage(manifest, "merge", status="completed", finished=True)
            save_manifest(manifest)

        if "citations" in stages:
            current_stage = "citations"
            mark_stage(manifest, "citations", status="running", started=True)
            save_manifest(manifest)
            merged_raw = _artifact_path(manifest, "merged_raw")
            citations_out = _artifact_path(manifest, "citations")
            citations_stats = _artifact_path(manifest, "citations_stats")
            command = [
                sys.executable,
                "-m",
                "ai_service.scripts.extract_citations",
                "--input",
                str(merged_raw),
                "--output",
                str(citations_out),
            ]
            if args.regulations_cache:
                command.extend(["--regulations-cache", args.regulations_cache])
            _run_command(
                manifest,
                stage="citations",
                command=command,
                outputs=[citations_out, citations_stats],
            )
            mark_stage(manifest, "citations", status="completed", finished=True)
            save_manifest(manifest)

        if "qa" in stages:
            current_stage = "qa"
            status = "skipped" if args.skip_qa else "running"
            mark_stage(manifest, "qa", status=status, started=not args.skip_qa)
            save_manifest(manifest)
            if not args.skip_qa:
                qa_out = _artifact_path(manifest, "qa_sample")
                command = [
                    sys.executable,
                    "-m",
                    "ai_service.scripts.qa_sample",
                    "--input",
                    str(_artifact_path(manifest, "citations")),
                    "--output",
                    str(qa_out),
                    "--n",
                    str(args.qa_sample_size),
                ]
                _run_command(manifest, stage="qa", command=command, outputs=[qa_out])
                mark_stage(manifest, "qa", status="completed", finished=True)
                save_manifest(manifest)

        if "triplets" in stages:
            current_stage = "triplets"
            mark_stage(manifest, "triplets", status="running", started=True)
            save_manifest(manifest)
            triplets_dir = _artifact_path(manifest, "triplets_dir")
            command = [
                sys.executable,
                "-m",
                "ai_service.scripts.build_training_triplets",
                "--citations",
                str(_artifact_path(manifest, "citations")),
                "--output-dir",
                str(triplets_dir),
                "--embedding-model",
                args.embedding_model or args.base_model,
                "--device",
                args.embedding_device,
            ]
            _run_command(
                manifest,
                stage="triplets",
                command=command,
                outputs=[
                    _artifact_path(manifest, "train_jsonl"),
                    _artifact_path(manifest, "val_jsonl"),
                    _artifact_path(manifest, "triplets_stats"),
                ],
            )
            mark_stage(manifest, "triplets", status="completed", finished=True)
            save_manifest(manifest)

        if "train" in stages:
            current_stage = "train"
            mark_stage(manifest, "train", status="running", started=True)
            save_manifest(manifest)
            model_dir = _artifact_path(manifest, "model_dir")
            checkpoints_dir = _artifact_path(manifest, "checkpoints_dir")
            report_path = _artifact_path(manifest, "evaluation_report")
            command = [
                sys.executable,
                "-m",
                "ai_service.scripts.fine_tune_bge",
                "--train",
                str(_artifact_path(manifest, "train_jsonl")),
                "--val",
                str(_artifact_path(manifest, "val_jsonl")),
                "--output-model",
                str(model_dir),
                "--evaluation-report",
                str(report_path),
                "--base-model",
                args.base_model,
                "--batch-size",
                str(args.batch_size),
                "--grad-accum",
                str(args.grad_accum),
                "--epochs",
                str(args.epochs),
                "--lr",
                str(args.lr),
                "--max-seq-length",
                str(args.max_seq_length),
                "--warmup-ratio",
                str(args.warmup_ratio),
                "--weight-decay",
                str(args.weight_decay),
                "--eval-batch-size",
                str(args.eval_batch_size),
                "--checkpoint-path",
                str(checkpoints_dir),
                "--checkpoint-save-steps",
                str(args.checkpoint_save_steps),
                "--checkpoint-save-total-limit",
                str(args.checkpoint_save_total_limit),
            ]
            if args.skip_epoch_eval:
                command.append("--skip-epoch-eval")
            if args.resume_from_checkpoint:
                command.append("--resume-from-checkpoint")
            if args.no_fp16:
                command.append("--no-fp16")

            _run_command(manifest, stage="train", command=command, outputs=[model_dir, report_path])
            mark_stage(manifest, "train", status="completed", finished=True)
            save_manifest(manifest)
            set_latest_model_alias(run_id, model_dir)

        manifest["status"] = "completed"
        save_manifest(manifest)
        set_latest_run_alias(run_id)
        logger.info(f"Pipeline run complete: {manifest_path(run_id)}")
    except Exception as exc:
        manifest["status"] = "failed"
        append_note(manifest, f"Stage {current_stage} failed: {exc}")
        if current_stage in manifest.get("stages", {}):
            mark_stage(manifest, current_stage, status="failed", finished=True, error=str(exc))
        save_manifest(manifest)
        raise


def _import_legacy(args: argparse.Namespace) -> None:
    ensure_dirs()
    run_id = args.run_id
    manifest = create_run_manifest(
        run_id,
        source_ids=[],
        description=args.description or "Imported legacy fine-tuning artifacts",
        base_model=args.base_model,
        train_enabled=bool(args.model_dir),
    )
    manifest["status"] = "legacy-imported"
    manifest["legacy"] = True

    artifact_overrides = {}
    if args.raw:
        artifact_overrides["merged_raw"] = _legacy_path_str(_abs(args.raw))
        mark_stage(manifest, "merge", status="imported", finished=True)
    if args.citations:
        artifact_overrides["citations"] = _legacy_path_str(_abs(args.citations))
        mark_stage(manifest, "citations", status="imported", finished=True)
    if args.triplets_dir:
        triplets_dir = _abs(args.triplets_dir)
        artifact_overrides["triplets_dir"] = _legacy_path_str(triplets_dir)
        artifact_overrides["train_jsonl"] = _legacy_path_str(triplets_dir / "train.jsonl")
        artifact_overrides["val_jsonl"] = _legacy_path_str(triplets_dir / "val.jsonl")
        artifact_overrides["triplets_stats"] = _legacy_path_str(triplets_dir / "stats.json")
        mark_stage(manifest, "triplets", status="imported", finished=True)
    if args.model_dir:
        model_dir = _abs(args.model_dir)
        artifact_overrides["model_dir"] = _legacy_path_str(model_dir)
        mark_stage(manifest, "train", status="imported", finished=True)
    if args.evaluation_report:
        artifact_overrides["evaluation_report"] = _legacy_path_str(_abs(args.evaluation_report))
    manifest["artifacts"].update(artifact_overrides)
    append_note(manifest, "Legacy artifacts imported without moving files")
    save_manifest(manifest)
    set_latest_run_alias(run_id)
    if args.model_dir:
        set_latest_model_alias(run_id, _abs(args.model_dir))
    logger.info(f"Imported legacy run into {manifest_path(run_id)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified fine-tuning pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("source-add-jsonl", help="Register an existing raw judgments JSONL file")
    p.add_argument("--source-id", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--key", default="id")
    p.add_argument("--disabled", action="store_true", default=False)
    p.set_defaults(func=_add_jsonl_source)

    p = sub.add_parser("source-add-moj", help="Register a MOJ scrape source")
    p.add_argument("--source-id", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--start-page", type=int, default=1)
    p.add_argument("--max-pages", type=int, default=0)
    p.add_argument("--delay", type=float, default=0.75)
    p.add_argument("--page-gap", type=float, default=2.0)
    p.add_argument("--list-fail-sleep", type=float, default=60.0)
    p.add_argument("--waf-cooldown", type=float, default=600.0)
    p.add_argument("--court-type", type=int, default=None)
    p.add_argument("--page-size", type=int, default=12)
    p.add_argument("--disabled", action="store_true", default=False)
    p.set_defaults(func=_add_moj_source)

    p = sub.add_parser("source-list", help="List registered sources")
    p.set_defaults(func=lambda args: _list_sources())

    p = sub.add_parser("run", help="Run the unified pipeline")
    p.add_argument("--run-id", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--source-id", action="append")
    p.add_argument("--all-enabled-sources", action="store_true", default=False)
    p.add_argument("--stages", default="")
    p.add_argument("--skip-qa", action="store_true", default=False)
    p.add_argument("--qa-sample-size", type=int, default=200)
    p.add_argument("--regulations-cache", default="")
    p.add_argument("--base-model", default="BAAI/bge-m3")
    p.add_argument("--embedding-model", default="")
    p.add_argument("--embedding-device", default="auto")
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--grad-accum", type=int, default=4)
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--lr", type=float, default=2e-5)
    p.add_argument("--max-seq-length", type=int, default=384)
    p.add_argument("--warmup-ratio", type=float, default=0.1)
    p.add_argument("--weight-decay", type=float, default=0.01)
    p.add_argument("--eval-batch-size", type=int, default=2)
    p.add_argument("--skip-epoch-eval", action="store_true", default=False)
    p.add_argument("--checkpoint-save-steps", type=int, default=250)
    p.add_argument("--checkpoint-save-total-limit", type=int, default=2)
    p.add_argument("--resume-from-checkpoint", action="store_true", default=False)
    p.add_argument("--no-fp16", action="store_true", default=False)
    p.set_defaults(func=_run_pipeline)

    p = sub.add_parser("import-legacy", help="Register existing artifacts as a legacy run")
    p.add_argument("--run-id", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--base-model", default="BAAI/bge-m3")
    p.add_argument("--raw", default="")
    p.add_argument("--citations", default="")
    p.add_argument("--triplets-dir", default="")
    p.add_argument("--model-dir", default="")
    p.add_argument("--evaluation-report", default="")
    p.set_defaults(func=_import_legacy)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
