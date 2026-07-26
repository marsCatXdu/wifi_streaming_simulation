#!/usr/bin/env python3
"""Validate causal online prediction replay artifacts and accounting."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from prediction.online_replay import load_replay_config, sha256_file


class ReplayValidationError(ValueError):
    """Raised when a replay artifact violates its frozen contract."""


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReplayValidationError(f"{path}: JSON root must be an object")
    return value


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ReplayValidationError(f"{path}: missing CSV header")
        return list(reader)


def _header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as source:
        try:
            return next(csv.reader(source))
        except StopIteration as error:
            raise ReplayValidationError(f"{path}: empty CSV") from error


def _integer(row: dict[str, str], name: str) -> int:
    try:
        return int(row[name])
    except (KeyError, ValueError) as error:
        raise ReplayValidationError(f"invalid integer field {name}") from error


def _number(row: dict[str, str], name: str) -> float:
    try:
        value = float(row[name])
    except (KeyError, ValueError) as error:
        raise ReplayValidationError(f"invalid numeric field {name}") from error
    if not math.isfinite(value):
        raise ReplayValidationError(f"nonfinite numeric field {name}")
    return value


def validate_replay(
    replay_root: Path,
    dataset_dir: Path,
    bundle_dir: Path,
    replay_config: Path,
) -> dict[str, Any]:
    """Validate one complete replay root and return an audit report."""
    replay_root = replay_root.resolve()
    dataset = _json(dataset_dir.resolve() / "dataset_manifest.json")
    bundle = _json(bundle_dir.resolve() / "model_bundle_manifest.json")
    config = load_replay_config(replay_config.resolve())
    manifest = _json(replay_root / "online_replay_manifest.json")
    if manifest["primary_link"] != 1 or manifest["primary_band"] != "5GHz":
        raise ReplayValidationError("replay manifest is not path 1 / 5 GHz")
    if manifest["dataset_sha256"] != dataset["dataset_sha256"]:
        raise ReplayValidationError("replay and dataset checksums differ")
    model_path = bundle_dir.resolve() / bundle["model_file"]
    if manifest["model_bundle_sha256"] != sha256_file(model_path):
        raise ReplayValidationError("replay model checksum differs from bundle")
    if manifest["replay_config_sha256"] != sha256_file(replay_config.resolve()):
        raise ReplayValidationError("replay configuration checksum differs")

    expected = {
        item["run_id"]: item
        for item in dataset["included_runs"]
        if item["selected_path"] == 1
        and item["split_role"] in config["replay_split_roles"]
    }
    observed_ids = manifest["run_ids"]
    if len(observed_ids) != len(set(observed_ids)):
        raise ReplayValidationError("duplicate run IDs in replay manifest")
    if set(observed_ids) != set(expected):
        raise ReplayValidationError("replay does not contain exactly the eligible path-1 runs")
    expected_metric_rows = (
        len(config["pipelines"])
        * len(config["decision_policies"])
        * len(config["probability_thresholds"])
        * len(config["budget_kinds"])
        * len(config["budgets"])
    )
    roles = Counter()
    scenarios = Counter()
    total_metric_rows = 0
    for run_id in observed_ids:
        output = replay_root / "runs" / run_id
        run = _json(output / "online_replay_run.json")
        source = Path(run["source_directory"])
        if run["primary_link"] != 1 or run["primary_band"] != "5GHz":
            raise ReplayValidationError(f"{run_id}: wrong primary path or band")
        if run["split_role"] != expected[run_id]["split_role"]:
            raise ReplayValidationError(f"{run_id}: split role differs from dataset")
        if run["scenario_name"] != expected[run_id]["scenario_name"]:
            raise ReplayValidationError(f"{run_id}: scenario differs from dataset")
        if run["source_prediction_samples_sha256"] != sha256_file(
            source / "prediction_samples.csv"
        ):
            raise ReplayValidationError(f"{run_id}: prediction source checksum changed")
        if run["source_frames_sha256"] != sha256_file(source / "frames.csv"):
            raise ReplayValidationError(f"{run_id}: frame outcome checksum changed")
        score_header = _header(output / "online_frame_scores.csv")
        if "deadline_miss" in score_header or "frame_latency_us" in score_header:
            raise ReplayValidationError(f"{run_id}: outcome leaked into score stream")
        metrics = _rows(output / "online_replay_metrics.csv")
        if len(metrics) != expected_metric_rows:
            raise ReplayValidationError(
                f"{run_id}: expected {expected_metric_rows} metrics, got {len(metrics)}"
            )
        for row in metrics:
            frames = _integer(row, "eligible_frames")
            misses = _integer(row, "eligible_misses")
            actions = _integer(row, "actions")
            true_positives = _integer(row, "true_positive_actions")
            false_positives = _integer(row, "false_positive_actions")
            crossings = _integer(row, "threshold_crossings")
            suppressions = _integer(row, "budget_suppressions")
            suppressed_misses = _integer(row, "budget_suppressed_misses")
            negative_misses = _integer(row, "threshold_negative_misses")
            useful = _integer(row, "useful_lead_true_positives")
            if true_positives + false_positives != actions:
                raise ReplayValidationError(f"{run_id}: action outcomes do not reconcile")
            if actions + suppressions != crossings:
                raise ReplayValidationError(f"{run_id}: threshold crossings do not reconcile")
            if true_positives + suppressed_misses + negative_misses != misses:
                raise ReplayValidationError(f"{run_id}: miss dispositions do not reconcile")
            if useful > true_positives or misses > frames:
                raise ReplayValidationError(f"{run_id}: invalid useful or miss count")
            budget = _number(row, "budget")
            capacity = _number(row, "token_capacity")
            if row["budget_kind"] == "frames":
                if actions > budget * frames + capacity + 1e-9:
                    raise ReplayValidationError(f"{run_id}: frame budget exceeded")
            elif row["budget_kind"] == "bytes":
                source_bytes = _integer(row, "eligible_source_bytes")
                action_bytes = _integer(row, "action_bytes")
                if action_bytes > budget * source_bytes + capacity + 1e-9:
                    raise ReplayValidationError(f"{run_id}: byte budget exceeded")
            else:
                raise ReplayValidationError(f"{run_id}: unknown budget kind")
        total_metric_rows += len(metrics)
        roles[run["split_role"]] += 1
        scenarios[run["scenario_name"]] += 1

    aggregate = _rows(replay_root / "aggregate_metrics.csv")
    if not aggregate:
        raise ReplayValidationError("aggregate metrics are empty")
    if not any(row.get("recall_ci_lower", "") for row in aggregate):
        raise ReplayValidationError("aggregate metrics lack bootstrap confidence intervals")
    if total_metric_rows != int(manifest["per_run_metric_rows"]):
        raise ReplayValidationError("per-run metric total differs from manifest")
    if set(roles) != {"in_distribution_test", "out_of_distribution_test"}:
        raise ReplayValidationError("both ID and OOD replay evidence are required")
    for scenario in ("obss_only", "obss_plus_legacy_mixed8"):
        if scenarios[scenario] == 0:
            raise ReplayValidationError(f"missing OOD scenario {scenario}")
    return {
        "status": "PASS",
        "run_count": len(observed_ids),
        "metric_row_count": total_metric_rows,
        "runs_by_split_role": dict(sorted(roles.items())),
        "runs_by_scenario": dict(sorted(scenarios.items())),
        "model_bundle_sha256": manifest["model_bundle_sha256"],
        "dataset_sha256": manifest["dataset_sha256"],
        "source_outcomes_unchanged": True,
        "score_stream_outcome_free": True,
        "frame_and_byte_budgets_reconciled": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay_root", type=Path)
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--bundle-dir", required=True, type=Path)
    parser.add_argument("--replay-config", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        report = validate_replay(
            args.replay_root,
            args.dataset_dir,
            args.bundle_dir,
            args.replay_config,
        )
    except (OSError, ReplayValidationError, ValueError) as error:
        raise SystemExit(f"error: {error}") from error
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
