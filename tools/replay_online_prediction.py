#!/usr/bin/env python3
"""Train frozen 5 GHz predictors and replay individual simulation runs."""

from __future__ import annotations

import argparse
import gc
import json
import os
import resource
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from evaluate_prediction import ROLES, _load_config, load_stage
from prediction.features import build_feature_sets
from prediction.metrics import topk_metrics
from prediction.online_replay import (
    MODEL_BUNDLE_SCHEMA_VERSION,
    ModelBundle,
    aggregate_metrics,
    fit_frozen_predictor,
    load_replay_config,
    read_frame_labels,
    read_model_bundle,
    replay_scores,
    score_individual_run,
    sha256_file,
    write_csv,
    write_json,
    write_model_bundle,
)
from prediction.online_reporting import (
    plot_miss_outcomes,
    plot_recall_heatmap,
    plot_recall_resource_tradeoff,
    plot_warning_lead_cdf,
    write_replay_report,
)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return value


def _atomic_directory(destination: Path, operation: Any) -> Any:
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.building-",
            dir=destination.parent,
        )
    )
    backup = destination.with_name(f".{destination.name}.previous")
    try:
        result = operation(staging)
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            os.replace(destination, backup)
        try:
            os.replace(staging, destination)
        except BaseException:
            if backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return result
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def train_bundle(args: argparse.Namespace) -> dict[str, Any]:
    """Fit and atomically publish the frozen model bundle."""
    started = time.perf_counter()
    dataset_dir = args.dataset_dir.resolve()
    analysis_path = args.analysis_config.resolve()
    replay_path = args.replay_config.resolve()
    analysis = _load_config(analysis_path)
    replay = load_replay_config(replay_path)
    if replay["analysis_schema_version"] != analysis["analysis_schema_version"]:
        raise ValueError("replay and analysis schema versions differ")
    manifest = _json(dataset_dir / "dataset_manifest.json")
    dataset_path = dataset_dir / manifest["dataset_file"]
    if manifest["analysis_config_sha256"] != sha256_file(analysis_path):
        raise ValueError("dataset and analysis configuration checksums differ")
    if not args.skip_dataset_checksum and sha256_file(dataset_path) != manifest["dataset_sha256"]:
        raise ValueError("dataset checksum mismatch")
    sets = build_feature_sets(
        manifest["feature_dictionary"], analysis["f2_exportable_allowlist"]
    )
    feature_names = tuple(sorted(set().union(*map(set, sets.by_tier.values()))))
    pipeline_specs = {item["pipeline_id"]: item for item in replay["pipelines"]}
    group_codes: dict[str, int] = {}
    run_codes: dict[str, int] = {}
    scenario_codes: dict[str, int] = {}
    regime_codes: dict[str, int] = {}
    correlation_codes: dict[str, int] = {}
    predictors = {}
    median_frame_size = None
    p99_frame_size = None
    capacity = int(manifest["counts"]["frame_count"])
    for stage in replay["stages"]:
        data = load_stage(
            dataset_path,
            stage,
            capacity,
            feature_names,
            group_codes,
            run_codes,
            scenario_codes,
            regime_codes,
            correlation_codes,
        )
        if stage == "T0":
            statistics_mask = (data.link == replay["primary_link"]) & np.isin(
                data.role,
                [ROLES["training"], ROLES["validation_calibration"]],
            )
            sizes = data.column("frame_size_bytes")[statistics_mask]
            if len(sizes) == 0:
                raise ValueError("no training/calibration frame sizes for path 1")
            median_frame_size = float(np.median(sizes))
            p99_frame_size = float(np.quantile(sizes, 0.99, method="linear"))
        for pipeline_id, spec in pipeline_specs.items():
            print(
                f"fitting {pipeline_id} {stage}",
                file=sys.stderr,
                flush=True,
            )
            predictors[(pipeline_id, stage)] = fit_frozen_predictor(
                data,
                int(replay["primary_link"]),
                stage,
                spec,
                sets,
                analysis,
            )
            gc.collect()
        del data
        gc.collect()
    if median_frame_size is None or p99_frame_size is None:
        raise AssertionError("T0 training statistics were not collected")

    bundle = ModelBundle(
        schema_version=MODEL_BUNDLE_SCHEMA_VERSION,
        replay_config_sha256=sha256_file(replay_path),
        analysis_config_sha256=sha256_file(analysis_path),
        dataset_sha256=manifest["dataset_sha256"],
        primary_link=int(replay["primary_link"]),
        feature_dictionary=manifest["feature_dictionary"],
        predictors=predictors,
        median_frame_size_bytes=median_frame_size,
        p99_frame_size_bytes=p99_frame_size,
    )

    def publish(staging: Path) -> dict[str, Any]:
        model_path = staging / "model_bundle.pkl"
        write_model_bundle(model_path, bundle)
        bundle_manifest = {
            "model_bundle_schema_version": MODEL_BUNDLE_SCHEMA_VERSION,
            "model_file": model_path.name,
            "model_sha256": sha256_file(model_path),
            "dataset": str(dataset_path),
            "dataset_sha256": bundle.dataset_sha256,
            "analysis_config": str(analysis_path),
            "analysis_config_sha256": bundle.analysis_config_sha256,
            "replay_config": str(replay_path),
            "replay_config_sha256": bundle.replay_config_sha256,
            "primary_link": bundle.primary_link,
            "primary_band": replay["primary_band"],
            "median_frame_size_bytes": bundle.median_frame_size_bytes,
            "p99_frame_size_bytes": bundle.p99_frame_size_bytes,
            "predictors": [
                {
                    "pipeline_id": predictor.pipeline_id,
                    "stage": predictor.stage,
                    "feature_set": predictor.feature_set,
                    "evidence_role": predictor.evidence_role,
                    "model": predictor.model_name,
                    "selection_recall": predictor.selection_recall,
                    "feature_count": len(predictor.feature_names),
                    "degradation_profile": None
                    if predictor.degradation_profile is None
                    else predictor.degradation_profile["profile_id"],
                }
                for predictor in predictors.values()
            ],
            "runtime_seconds": time.perf_counter() - started,
            "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        }
        write_json(staging / "model_bundle_manifest.json", bundle_manifest)
        return bundle_manifest

    return _atomic_directory(args.output_dir, publish)


def _selected_runs(
    dataset_manifest: dict[str, Any],
    replay: dict[str, Any],
    requested_ids: set[str],
    max_runs: int | None,
) -> list[dict[str, Any]]:
    selected = [
        dict(item)
        for item in dataset_manifest["included_runs"]
        if item["selected_path"] == replay["primary_link"]
        and item["split_role"] in replay["replay_split_roles"]
        and (not requested_ids or item["run_id"] in requested_ids)
    ]
    selected.sort(key=lambda item: item["run_id"])
    if requested_ids - {item["run_id"] for item in selected}:
        missing = sorted(requested_ids - {item["run_id"] for item in selected})
        raise ValueError(f"requested run IDs are not eligible 5 GHz test runs: {missing}")
    if max_runs is not None:
        selected = selected[:max_runs]
    if not selected:
        raise ValueError("no eligible individual runs selected")
    return selected


def _verify_run_is_5ghz(run: dict[str, Any]) -> None:
    config = _json(Path(run["source_directory"]) / "resolved_config.json")
    if config.get("policy") != "fixed_link_1":
        raise ValueError(f"{run['run_id']}: expected fixed_link_1")
    ranges = config.get("wifi", {}).get("frequency_ranges", [])
    if len(ranges) < 2 or ranges[1] != "WIFI_SPECTRUM_5_GHZ":
        raise ValueError(f"{run['run_id']}: path 1 is not 5 GHz")


def _offline_topk_upper_bounds(
    records: list[dict[str, Any]],
    replay: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compute explicit future-aware ranking bounds for comparison only."""
    output = []
    for role in replay["replay_split_roles"]:
        role_rows = [row for row in records if row["split_role"] == role]
        scenarios = sorted({row["scenario_name"] for row in role_rows})
        for scenario in scenarios + ["__all_selected__"]:
            scenario_rows = (
                role_rows
                if scenario == "__all_selected__"
                else [row for row in role_rows if row["scenario_name"] == scenario]
            )
            for stage in replay["stages"]:
                selected = [row for row in scenario_rows if row["stage"] == stage]
                if not selected:
                    continue
                labels = np.asarray([row["deadline_miss"] for row in selected], dtype=np.int8)
                scores = np.asarray([row["ranking_score"] for row in selected], dtype=float)
                for budget in map(float, replay["budgets"]):
                    output.append(
                        {
                            "bound_type": "future_aware_global_topk",
                            "split_role": role,
                            "scenario_name": scenario,
                            "pipeline_id": "commodity_polling_1ms",
                            "stage": stage,
                            **topk_metrics(
                                labels,
                                scores,
                                budget,
                                float(replay["confidence_level"]),
                            ),
                        }
                    )
    return output


def replay_runs(args: argparse.Namespace) -> dict[str, Any]:
    """Replay selected raw run directories and atomically publish results."""
    started = time.perf_counter()
    dataset_dir = args.dataset_dir.resolve()
    replay_path = args.replay_config.resolve()
    replay = load_replay_config(replay_path)
    dataset_manifest = _json(dataset_dir / "dataset_manifest.json")
    bundle_dir = args.bundle_dir.resolve()
    bundle_manifest = _json(bundle_dir / "model_bundle_manifest.json")
    bundle_path = bundle_dir / bundle_manifest["model_file"]
    if sha256_file(bundle_path) != bundle_manifest["model_sha256"]:
        raise ValueError("model bundle checksum mismatch")
    bundle = read_model_bundle(bundle_path)
    if bundle.replay_config_sha256 != sha256_file(replay_path):
        raise ValueError("model bundle was trained for a different replay contract")
    if bundle.dataset_sha256 != dataset_manifest["dataset_sha256"]:
        raise ValueError("model bundle and dataset manifest differ")
    requested = set(args.run_id or [])
    selected = _selected_runs(dataset_manifest, replay, requested, args.max_runs)

    def publish(staging: Path) -> dict[str, Any]:
        run_root = staging / "runs"
        run_root.mkdir()
        plot_root = staging / "plots"
        plot_root.mkdir()
        all_metrics = []
        selected_audits = []
        upper_bound_records = []
        completed = []
        for index, run in enumerate(selected, start=1):
            _verify_run_is_5ghz(run)
            run_dir = Path(run["source_directory"])
            print(
                f"replaying {index}/{len(selected)} {run['run_id']}",
                file=sys.stderr,
                flush=True,
            )
            scores = score_individual_run(run_dir, bundle, int(replay["primary_link"]))
            labels = read_frame_labels(run_dir)
            metadata = {
                "run_id": run["run_id"],
                "run_group_id": run["run_group_id"],
                "split_role": run["split_role"],
                "scenario_name": run["scenario_name"],
                "selected_policy": run["selected_policy"],
                "primary_link": replay["primary_link"],
                "primary_band": replay["primary_band"],
            }
            metrics, audits = replay_scores(
                scores,
                labels,
                replay,
                bundle.median_frame_size_bytes,
                bundle.p99_frame_size_bytes,
                metadata,
            )
            output = run_root / run["run_id"]
            output.mkdir()
            write_csv(output / "online_replay_metrics.csv", metrics)
            write_csv(output / "online_replay_events.csv", audits)
            if replay.get("write_frame_scores", False):
                write_csv(output / "online_frame_scores.csv", scores)
            write_json(
                output / "online_replay_run.json",
                {
                    **metadata,
                    "source_directory": str(run_dir),
                    "source_prediction_samples_sha256": sha256_file(
                        run_dir / "prediction_samples.csv"
                    ),
                    "source_frames_sha256": sha256_file(run_dir / "frames.csv"),
                    "metric_rows": len(metrics),
                    "audit_event_rows": len(audits),
                    "score_rows": len(scores),
                },
            )
            all_metrics.extend(metrics)
            upper_bound_records.extend(
                {
                    **row,
                    "deadline_miss": labels[row["frame_id"]],
                    "split_role": run["split_role"],
                    "scenario_name": run["scenario_name"],
                }
                for row in scores
                if row["pipeline_id"] == "commodity_polling_1ms"
            )
            selected_audits.extend(
                row
                for row in audits
                if row["decision"] == "action"
                and row["pipeline_id"] == "commodity_polling_1ms"
                and row["decision_policy"] == "sequential"
            )
            completed.append(run["run_id"])
        aggregate = aggregate_metrics(
            all_metrics,
            confidence=float(replay["confidence_level"]),
            bootstrap_replicates=int(replay["bootstrap_replicates"]),
            bootstrap_seed=int(replay["bootstrap_seed"]),
        )
        write_csv(staging / "per_run_metrics.csv", all_metrics)
        write_csv(staging / "aggregate_metrics.csv", aggregate)
        upper_bounds = _offline_topk_upper_bounds(upper_bound_records, replay)
        write_csv(staging / "offline_topk_upper_bound.csv", upper_bounds)
        for role in replay["replay_split_roles"]:
            for budget_kind in replay["budget_kinds"]:
                plot_recall_heatmap(
                    plot_root / f"{role}_{budget_kind}_recall_heatmap.png",
                    aggregate,
                    role,
                    budget_kind,
                )
                plot_recall_heatmap(
                    plot_root / f"{role}_{budget_kind}_precision_heatmap.png",
                    aggregate,
                    role,
                    budget_kind,
                    metric="precision",
                )
                plot_recall_resource_tradeoff(
                    plot_root / f"{role}_{budget_kind}_recall_tradeoff.png",
                    aggregate,
                    role,
                    budget_kind,
                )
                plot_miss_outcomes(
                    plot_root / f"{role}_{budget_kind}_miss_outcomes.png",
                    aggregate,
                    role,
                    budget_kind,
                )
        plot_warning_lead_cdf(
            plot_root / "warning_lead_time_cdf.png",
            selected_audits,
        )
        write_replay_report(
            staging / "online_replay_report.md",
            aggregate,
            len(completed),
            upper_bounds,
        )
        result_manifest = {
            "online_replay_schema_version": replay["online_replay_schema_version"],
            "replay_config": str(replay_path),
            "replay_config_sha256": sha256_file(replay_path),
            "model_bundle": str(bundle_path),
            "model_bundle_sha256": sha256_file(bundle_path),
            "dataset_manifest": str(dataset_dir / "dataset_manifest.json"),
            "dataset_sha256": dataset_manifest["dataset_sha256"],
            "primary_link": replay["primary_link"],
            "primary_band": replay["primary_band"],
            "run_count": len(completed),
            "run_ids": completed,
            "per_run_metric_rows": len(all_metrics),
            "aggregate_metric_rows": len(aggregate),
            "runtime_seconds": time.perf_counter() - started,
            "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
            "interpretation": (
                "Actions are hypothetical warnings; recorded deadline outcomes are unchanged."
            ),
        }
        write_json(staging / "online_replay_manifest.json", result_manifest)
        return result_manifest

    return _atomic_directory(args.output_dir, publish)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    train = subparsers.add_parser("train", help="fit and freeze 5 GHz model bundles")
    train.add_argument("dataset_dir", type=Path)
    train.add_argument("--analysis-config", required=True, type=Path)
    train.add_argument("--replay-config", required=True, type=Path)
    train.add_argument("--output-dir", required=True, type=Path)
    train.add_argument("--skip-dataset-checksum", action="store_true")

    replay = subparsers.add_parser("replay", help="replay individual raw test runs")
    replay.add_argument("dataset_dir", type=Path)
    replay.add_argument("--bundle-dir", required=True, type=Path)
    replay.add_argument("--replay-config", required=True, type=Path)
    replay.add_argument("--output-dir", required=True, type=Path)
    replay.add_argument("--run-id", action="append")
    replay.add_argument("--max-runs", type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if getattr(args, "max_runs", None) is not None and args.max_runs <= 0:
        raise SystemExit("error: --max-runs must be positive")
    try:
        result = train_bundle(args) if args.command == "train" else replay_runs(args)
    except (OSError, ValueError) as error:
        raise SystemExit(f"error: {error}") from error
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
