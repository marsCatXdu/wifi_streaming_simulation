#!/usr/bin/env python3
"""Build a causal, frame-labelled Increment-2 prediction dataset."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from prediction_dataset import (
    ATTACHED_COLUMNS,
    DATASET_SCHEMA_VERSION,
    DERIVED_COLUMNS,
    SPLIT_ROLES,
    SourceRun,
    derive_age,
    discover_source_runs,
    feature_dictionary,
    csv_header,
    iter_csv,
    load_regime,
    make_run_group_id,
    read_csv,
    read_yaml,
    scenario_name,
    selected_link,
    sha256_file,
    validate_analysis_config,
    validate_ood_run_filter,
)
from validate_outputs import validate_run

POLLING_KEY_AND_METADATA = {
    "polling_schema_version",
    "run_id",
    "frame_id",
    "path_id",
    "copy_id",
    "sample_stage",
    "sample_offset_us",
    "report_available",
    "capture_time_ns",
    "available_time_ns",
    "staleness_us",
    "latest_feature_event_time_ns",
    "latest_feature_event_sequence",
    "feature_support_mask",
}


def _polling_output_columns(header: list[str]) -> list[str]:
    """Return deterministic dataset columns for one polling sidecar schema."""
    columns = [
        "polling_1ms_schema_version",
        "polling_1ms_report_available",
        "polling_1ms_capture_time_ns",
        "polling_1ms_available_time_ns",
        "polling_1ms_staleness_us",
        "polling_1ms_latest_feature_event_time_ns",
        "polling_1ms_latest_feature_event_sequence",
        "polling_1ms_feature_support_mask",
    ]
    columns.extend(
        f"polling_1ms_{name}" for name in header if name not in POLLING_KEY_AND_METADATA
    )
    columns.extend(
        [
            "polling_1ms_last_positive_ack_age_us",
            "polling_1ms_last_attempt_age_us",
        ]
    )
    if len(columns) != len(set(columns)):
        raise ValueError("polling sidecar maps to duplicate dataset columns")
    return columns


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        dir=path.parent,
        delete=False,
        encoding="utf-8",
    ) as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")
        temporary = Path(output.name)
    os.replace(temporary, path)


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return value


def _split_groups(
    groups: dict[str, list[dict[str, Any]]],
    analysis: dict[str, Any],
) -> tuple[dict[str, str], str]:
    required_ood = set(analysis["required_ood_scenarios"])
    ood_groups = sorted(
        group_id
        for group_id, runs in groups.items()
        if (
            runs[0]["scenario_name"] in required_ood
            or runs[0]["scenario_name"].startswith("obss_")
        )
    )
    id_groups = sorted(set(groups) - set(ood_groups))
    split_seed = int(analysis["split_seed"])
    id_groups.sort(
        key=lambda group_id: hashlib.sha256(
            f"{split_seed}:{group_id}".encode()
        ).digest()
    )
    minimums = {
        "validation_selection": int(
            analysis["minimum_run_groups_validation_selection"]
        ),
        "validation_calibration": int(
            analysis["minimum_run_groups_validation_calibration"]
        ),
        "in_distribution_test": int(analysis["minimum_run_groups_id_test"]),
    }
    required_id_groups = sum(minimums.values()) + 1
    assignment: dict[str, str] = {
        group_id: "out_of_distribution_test" for group_id in ood_groups
    }
    if len(id_groups) >= required_id_groups:
        cursor = 0
        for role in (
            "validation_selection",
            "validation_calibration",
            "in_distribution_test",
        ):
            count = minimums[role]
            for group_id in id_groups[cursor:cursor + count]:
                assignment[group_id] = role
            cursor += count
        for group_id in id_groups[cursor:]:
            assignment[group_id] = "training"
        sufficiency = "pending_class_check"
    else:
        smoke_roles = (
            "training",
            "validation_selection",
            "validation_calibration",
            "in_distribution_test",
        )
        for index, group_id in enumerate(id_groups):
            assignment[group_id] = smoke_roles[index % len(smoke_roles)]
        sufficiency = "insufficient_data"
    if set(assignment) != set(groups):
        raise ValueError("split assignment did not cover every run group")
    if not set(assignment.values()) <= SPLIT_ROLES:
        raise ValueError("split assignment produced an invalid role")
    return assignment, sufficiency


def _write_dataset(
    output_dir: Path,
    rows: Any,
    columns: list[str],
    requested_format: str,
) -> tuple[Path, str, str | None, str | None]:
    fallback_reason = None
    dependency_version = None
    if requested_format == "parquet":
        try:
            import pyarrow as pa  # type: ignore[import-not-found]
            import pyarrow.parquet as pq  # type: ignore[import-not-found]
        except ImportError:
            fallback_reason = "pyarrow is unavailable; explicit CSV fallback used"
        else:
            path = output_dir / "labelled_samples.parquet"
            writer = None
            schema = None
            try:
                for batch in rows:
                    if not batch:
                        continue
                    table = pa.Table.from_pylist(batch, schema=schema)
                    if writer is None:
                        schema = table.schema
                        writer = pq.ParquetWriter(path, table.schema)
                    writer.write_table(table)
            finally:
                if writer is not None:
                    writer.close()
            if writer is None:
                raise ValueError("labelled dataset is empty")
            dependency_version = pa.__version__
            return path, "parquet", None, dependency_version

    path = output_dir / "labelled_samples.csv"
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for batch in rows:
            writer.writerows(batch)
    return path, "csv", fallback_reason, dependency_version


def _frame_and_miss_counts(
    rows: list[dict[str, Any]],
    split_assignment: dict[str, str],
) -> dict[str, dict[str, int]]:
    frames_by_role: dict[str, set[tuple[str, str]]] = defaultdict(set)
    misses_by_role: dict[str, set[tuple[str, str]]] = defaultdict(set)
    groups_by_role: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        role = split_assignment[row["run_group_id"]]
        frame = (row["run_id"], row["frame_id"])
        frames_by_role[role].add(frame)
        groups_by_role[role].add(row["run_group_id"])
        if str(row["deadline_miss"]) == "1":
            misses_by_role[role].add(frame)
    return {
        role: {
            "run_group_count": len(groups_by_role[role]),
            "frame_count": len(frames_by_role[role]),
            "miss_count": len(misses_by_role[role]),
        }
        for role in sorted(SPLIT_ROLES)
    }


def _ood_scenario_counts(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    frames: dict[str, dict[tuple[str, str], int]] = defaultdict(dict)
    groups: dict[str, set[str]] = defaultdict(set)
    runs: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        scenario = row["scenario_name"]
        if not scenario.startswith("obss_"):
            continue
        frames[scenario][(row["run_id"], row["frame_id"])] = int(
            row["deadline_miss"]
        )
        groups[scenario].add(row["run_group_id"])
        runs[scenario].add(row["run_id"])
    return {
        scenario: {
            "run_group_count": len(groups[scenario]),
            "run_count": len(runs[scenario]),
            "frame_count": len(scenario_frames),
            "miss_count": sum(scenario_frames.values()),
        }
        for scenario, scenario_frames in sorted(frames.items())
    }


def _resolve_split_sufficiency(
    initial_status: str,
    split_counts: dict[str, dict[str, int]],
    ood_counts: dict[str, dict[str, int]],
    groups: dict[str, list[dict[str, Any]]],
    assignment: dict[str, str],
    analysis: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    minima = {
        "validation_selection": analysis[
            "minimum_run_groups_validation_selection"
        ],
        "validation_calibration": analysis[
            "minimum_run_groups_validation_calibration"
        ],
        "in_distribution_test": analysis["minimum_run_groups_id_test"],
    }
    for role, minimum in minima.items():
        if split_counts[role]["run_group_count"] < minimum:
            reasons.append(
                f"{role} has {split_counts[role]['run_group_count']} groups; "
                f"requires {minimum}"
            )
        frame_count = split_counts[role]["frame_count"]
        miss_count = split_counts[role]["miss_count"]
        if frame_count == 0 or miss_count == 0 or miss_count == frame_count:
            reasons.append(f"{role} does not contain both outcome classes")

    minimum_ood = analysis["minimum_run_groups_per_required_ood_scenario"]
    for scenario in analysis["required_ood_scenarios"]:
        scenario_groups = {
            group_id
            for group_id, runs in groups.items()
            if (
                runs[0]["scenario_name"] == scenario
                and assignment[group_id] == "out_of_distribution_test"
            )
        }
        if len(scenario_groups) < minimum_ood:
            reasons.append(
                f"{scenario} has {len(scenario_groups)} groups; requires {minimum_ood}"
            )
        counts = ood_counts.get(
            scenario,
            {"frame_count": 0, "miss_count": 0},
        )
        if (
            counts["frame_count"] == 0
            or counts["miss_count"] == 0
            or counts["miss_count"] == counts["frame_count"]
        ):
            reasons.append(f"{scenario} does not contain both outcome classes")
    if initial_status == "insufficient_data" or reasons:
        return "insufficient_data", reasons
    return "pass", []


def _joined_batches(
    records: list[dict[str, Any]],
    loads: dict[str, Any],
    split_assignment: dict[str, str],
    raw_header: list[str],
    stats: dict[str, Any],
) -> Any:
    """Yield one sorted run at a time and update bounded build statistics."""
    for record in sorted(records, key=lambda item: item["run_id"]):
        source: SourceRun = record["source"]
        config = record["config"]
        run_id = record["run_id"]
        path_id = record["selected_path"]
        group_id = record["run_group_id"]
        role = split_assignment[group_id]
        background = config["background"]
        background_profile = background["profile"]
        correlation_mode = (
            "none"
            if background_profile == "none"
            else background["correlation"]["mode"]
        )
        regime = load_regime(config, loads, path_id)
        _, frames = read_csv(source.run_dir / "frames.csv")
        frames_by_id = {row["frame_id"]: row for row in frames}
        if len(frames_by_id) != len(frames):
            raise ValueError(f"{source.run_dir}: duplicate frame IDs")
        polling_by_key: dict[tuple[str, str, str, str, str], dict[str, str]] = {}
        polling_header, polling_rows = read_csv(
            source.run_dir / "prediction_polling_samples.csv"
        )
        for polling in polling_rows:
            polling_key = tuple(
                polling[name]
                for name in (
                    "frame_id",
                    "path_id",
                    "copy_id",
                    "sample_stage",
                    "sample_offset_us",
                )
            )
            if polling_key in polling_by_key:
                raise ValueError(
                    f"{source.run_dir}: duplicate periodic observation key {polling_key}"
                )
            polling_by_key[polling_key] = polling

        batch: list[dict[str, Any]] = []
        sample_keys: set[tuple[str, str, str, str, str]] = set()
        sampled_frames: dict[str, int] = {}
        for sample in iter_csv(source.run_dir / "prediction_samples.csv"):
            key = (
                sample["frame_id"],
                sample["path_id"],
                sample["copy_id"],
                sample["sample_stage"],
                sample["sample_offset_us"],
            )
            if key in sample_keys:
                raise ValueError(f"duplicate dataset sample key: {(run_id, *key)}")
            sample_keys.add(key)
            polling = polling_by_key.pop(key, None)
            if polling is None:
                raise ValueError(
                    f"{source.run_dir}: sample has no recorded periodic observation"
                )
            if sample["run_id"] != run_id:
                raise ValueError(f"{source.run_dir}: sample run ID mismatch")
            if int(sample["path_id"]) != path_id or int(sample["copy_id"]) != 0:
                raise ValueError(f"{source.run_dir}: sample uses the wrong fixed path")
            frame = frames_by_id.get(sample["frame_id"])
            if frame is None:
                raise ValueError(f"{source.run_dir}: sample has no matching frame")
            if int(frame["primary_link"]) != path_id:
                raise ValueError(f"{source.run_dir}: frame uses the wrong fixed path")
            incomplete = int(frame["incomplete"])
            deadline_miss = int(frame["deadline_miss"])
            completion_us = frame["union_completion_us"]
            latency_us = frame["union_latency_us"]
            if incomplete:
                if completion_us or latency_us or deadline_miss != 1:
                    raise ValueError(f"{source.run_dir}: invalid incomplete-frame label")
                completion_ns: int | None = None
                final_latency: int | None = None
            else:
                if not completion_us or not latency_us:
                    raise ValueError(f"{source.run_dir}: complete frame lacks timing")
                completion_ns = int(completion_us) * 1000
                final_latency = int(latency_us)
            previous_label = sampled_frames.setdefault(sample["frame_id"], deadline_miss)
            if previous_label != deadline_miss:
                raise ValueError(f"{source.run_dir}: frame label changed")
            sample_time_ns = int(sample["sample_time_ns"])
            capture_time_ns = int(polling["capture_time_ns"])
            available_time_ns = int(polling["available_time_ns"])
            staleness_us = float(polling["staleness_us"])
            if available_time_ns > sample_time_ns:
                raise ValueError(
                    f"{source.run_dir}: periodic report was not causally available"
                )
            expected_staleness = (sample_time_ns - capture_time_ns) // 1000
            if abs(staleness_us - expected_staleness) > 1e-9:
                raise ValueError(f"{source.run_dir}: periodic staleness is inconsistent")
            if not 1000 <= staleness_us < 2000:
                raise ValueError(
                    f"{source.run_dir}: genuine 1 ms report has {staleness_us} us staleness"
                )
            packet_count = int(sample["frame_packet_count"])
            tx_succeeded = int(sample["frame_packets_tx_succeeded"])
            if tx_succeeded > packet_count:
                raise ValueError(f"{source.run_dir}: acknowledged packets exceed frame")
            attached = {
                "dataset_schema_version": DATASET_SCHEMA_VERSION,
                "frame_complete": 1 - incomplete,
                "frame_completion_time_ns": completion_ns,
                "frame_latency_us": final_latency,
                "deadline_miss": deadline_miss,
                "run_seed": config["seed"],
                "run_number": config["run"],
                "scenario_name": record["scenario_name"],
                "background_profile": background_profile,
                "correlation_mode": correlation_mode,
                "selected_policy": config["policy"],
                "run_group_id": group_id,
                "miss_regime": regime,
                "split_role": role,
            }
            derived = {
                "last_positive_ack_age_us": derive_age(
                    sample_time_ns,
                    sample["last_positive_ack_time_ns"],
                    "last_positive_ack_time_ns",
                ),
                "last_attempt_age_us": derive_age(
                    sample_time_ns,
                    sample["last_tx_attempt_time_ns"],
                    "last_tx_attempt_time_ns",
                ),
                "queue_oldest_age_us": derive_age(
                    sample_time_ns,
                    sample["mac_queue_oldest_enqueue_time_ns"],
                    "mac_queue_oldest_enqueue_time_ns",
                ),
                "frame_packets_not_acknowledged": packet_count - tx_succeeded,
            }
            polling_attached = {
                "polling_1ms_schema_version": polling["polling_schema_version"],
                "polling_1ms_report_available": polling["report_available"],
                "polling_1ms_capture_time_ns": polling["capture_time_ns"],
                "polling_1ms_available_time_ns": polling["available_time_ns"],
                "polling_1ms_staleness_us": polling["staleness_us"],
                "polling_1ms_latest_feature_event_time_ns": polling[
                    "latest_feature_event_time_ns"
                ],
                "polling_1ms_latest_feature_event_sequence": polling[
                    "latest_feature_event_sequence"
                ],
                "polling_1ms_feature_support_mask": polling["feature_support_mask"],
            }
            for name in polling_header:
                if name not in POLLING_KEY_AND_METADATA:
                    polling_attached[f"polling_1ms_{name}"] = polling[name]
            polling_attached["polling_1ms_last_positive_ack_age_us"] = derive_age(
                capture_time_ns,
                polling["last_positive_ack_time_ns"],
                "polling last_positive_ack_time_ns",
            )
            polling_attached["polling_1ms_last_attempt_age_us"] = derive_age(
                capture_time_ns,
                polling["last_tx_attempt_time_ns"],
                "polling last_tx_attempt_time_ns",
            )
            batch.append(
                {**attached, **sample, **derived, **polling_attached}
            )

        if polling_by_key:
            raise ValueError(f"{source.run_dir}: orphan periodic observation rows")

        batch.sort(
            key=lambda row: (
                row["run_id"],
                int(row["frame_id"]),
                int(row["sample_offset_us"]),
                int(row["path_id"]),
                int(row["copy_id"]),
            )
        )
        split_entry = stats["split"][role]
        split_entry["groups"].add(group_id)
        split_entry["frame_count"] += len(sampled_frames)
        split_entry["miss_count"] += sum(sampled_frames.values())
        scenario = record["scenario_name"]
        if scenario.startswith("obss_"):
            ood_entry = stats["ood"][scenario]
            ood_entry["groups"].add(group_id)
            ood_entry["runs"].add(run_id)
            ood_entry["frame_count"] += len(sampled_frames)
            ood_entry["miss_count"] += sum(sampled_frames.values())
        stats["frame_count"] += len(sampled_frames)
        stats["miss_count"] += sum(sampled_frames.values())
        stats["sample_count"] += len(batch)
        stats["included_runs"].append(
            {
                "run_id": run_id,
                "run_group_id": group_id,
                "source_directory": str(source.run_dir),
                "source_root": str(source.source_root),
                "scenario_name": scenario,
                "selected_policy": config["policy"],
                "selected_path": path_id,
                "seed": config["seed"],
                "run": config["run"],
                "frame_count": len(frames),
                "sample_count": len(batch),
                "miss_count": sum(
                    int(frame["deadline_miss"]) for frame in frames_by_id.values()
                ),
                "split_role": role,
            }
        )
        yield batch


def _build_dataset_at(
    inputs: list[Path],
    output_dir: Path,
    analysis_path: Path,
    loads_path: Path,
    requested_format: str,
    scenario_filters: set[str] | None = None,
    command: list[str] | None = None,
) -> dict[str, Any]:
    analysis_path = analysis_path.resolve()
    loads_path = loads_path.resolve()
    analysis = read_yaml(analysis_path)
    loads = read_yaml(loads_path)
    validate_analysis_config(analysis)
    if loads.get("load_schema_version") != 1:
        raise ValueError("unsupported prediction load schema")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    sources = discover_source_runs(inputs)
    records: list[dict[str, Any]] = []
    excluded_runs: list[dict[str, str]] = []
    seen_run_ids: set[str] = set()
    raw_header: list[str] | None = None
    polling_header: list[str] | None = None
    for source in sources:
        validated = validate_run(source.run_dir)
        config = _json(source.run_dir / "resolved_config.json")
        build = _json(source.run_dir / "build_info.json")
        run_id = config["run_id"]
        if run_id != validated["run_id"]:
            raise ValueError(f"{source.run_dir}: validated run ID changed")
        if run_id in seen_run_ids:
            raise ValueError(f"duplicate run_id across inputs: {run_id}")
        seen_run_ids.add(run_id)
        prediction = config.get("predictionTelemetry")
        if not isinstance(prediction, dict) or not prediction.get("enabled", False):
            raise ValueError(f"{source.run_dir}: prediction telemetry is disabled")
        if config.get("topology") != "dual_interface":
            raise ValueError(f"{source.run_dir}: prediction dataset requires dual_interface")
        path_id = selected_link(config.get("policy", ""))
        name = scenario_name(config)
        validate_ood_run_filter(config, name, analysis)
        if scenario_filters is not None and name not in scenario_filters:
            excluded_runs.append(
                {"run_id": run_id, "reason": "scenario_filter", "scenario_name": name}
            )
            continue
        sample_header = csv_header(source.run_dir / "prediction_samples.csv")
        if raw_header is None:
            raw_header = sample_header
        elif sample_header != raw_header:
            raise ValueError(f"{source.run_dir}: prediction sample header differs")
        candidate_polling_header = csv_header(
            source.run_dir / "prediction_polling_samples.csv"
        )
        if polling_header is None:
            polling_header = candidate_polling_header
        elif candidate_polling_header != polling_header:
            raise ValueError(f"{source.run_dir}: prediction polling header differs")
        group_id = make_run_group_id(source, config, build)
        records.append(
            {
                "source": source,
                "run_id": run_id,
                "config": config,
                "build": build,
                "scenario_name": name,
                "selected_path": path_id,
                "run_group_id": group_id,
            }
        )
    if not records:
        raise ValueError("scenario filters excluded every source run")
    if raw_header is None or polling_header is None:
        raise ValueError("prediction sample schemas were not discovered")

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[record["run_group_id"]].append(record)
    for group_id, members in groups.items():
        policies = [member["config"]["policy"] for member in members]
        if len(policies) != len(set(policies)):
            raise ValueError(f"run group {group_id} repeats a fixed-link policy")
        if len(members) > 2:
            raise ValueError(f"run group {group_id} contains more than two runs")
        if len({member["scenario_name"] for member in members}) != 1:
            raise ValueError(f"run group {group_id} spans scenarios")

    split_assignment, initial_sufficiency = _split_groups(groups, analysis)
    stats: dict[str, Any] = {
        "sample_count": 0,
        "frame_count": 0,
        "miss_count": 0,
        "included_runs": [],
        "split": defaultdict(
            lambda: {"groups": set(), "frame_count": 0, "miss_count": 0}
        ),
        "ood": defaultdict(
            lambda: {
                "groups": set(),
                "runs": set(),
                "frame_count": 0,
                "miss_count": 0,
            }
        ),
    }
    source_checksums: dict[str, dict[str, str]] = {}
    for record in records:
        source = record["source"]
        source_checksums[record["run_id"]] = {
            name: sha256_file(source.run_dir / name)
            for name in (
                "prediction_samples.csv",
                "prediction_polling_samples.csv",
                "frames.csv",
                "resolved_config.json",
                "build_info.json",
            )
        }

    columns = (
        ATTACHED_COLUMNS
        + raw_header
        + DERIVED_COLUMNS
        + _polling_output_columns(polling_header)
    )
    if len(columns) != len(set(columns)):
        raise ValueError("dataset schema contains duplicate columns")
    dataset_path, actual_format, fallback_reason, pyarrow_version = _write_dataset(
        output_dir,
        _joined_batches(records, loads, split_assignment, raw_header, stats),
        columns,
        requested_format,
    )

    split_counts = {
        role: {
            "run_group_count": len(stats["split"][role]["groups"]),
            "frame_count": stats["split"][role]["frame_count"],
            "miss_count": stats["split"][role]["miss_count"],
        }
        for role in sorted(SPLIT_ROLES)
    }
    ood_counts = {
        scenario: {
            "run_group_count": len(entry["groups"]),
            "run_count": len(entry["runs"]),
            "frame_count": entry["frame_count"],
            "miss_count": entry["miss_count"],
        }
        for scenario, entry in sorted(stats["ood"].items())
    }
    split_status, split_reasons = _resolve_split_sufficiency(
        initial_sufficiency,
        split_counts,
        ood_counts,
        groups,
        split_assignment,
        analysis,
    )
    splits = {
        "split_schema_version": 1,
        "split_seed": analysis["split_seed"],
        "analysis_config": str(analysis_path),
        "analysis_config_sha256": sha256_file(analysis_path),
        "sufficiency_status": split_status,
        "sufficiency_reasons": split_reasons,
        "groups": [
            {
                "run_group_id": group_id,
                "split_role": split_assignment[group_id],
                "scenario_name": groups[group_id][0]["scenario_name"],
                "run_ids": sorted(member["run_id"] for member in groups[group_id]),
            }
            for group_id in sorted(groups)
        ],
        "counts_by_role": split_counts,
        "counts_by_ood_scenario": ood_counts,
    }
    _atomic_json(output_dir / "splits.json", splits)

    project_commits = sorted({record["build"]["project_git_commit"] for record in records})
    ns3_commits = sorted({record["build"]["ns3_upstream_commit"] for record in records})
    build_profiles = sorted({record["build"]["build_profile"] for record in records})
    manifest = {
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "requested_format": requested_format,
        "actual_format": actual_format,
        "format_fallback_reason": fallback_reason,
        "dataset_file": dataset_path.name,
        "dataset_sha256": sha256_file(dataset_path),
        "analysis_config": str(analysis_path),
        "analysis_config_sha256": sha256_file(analysis_path),
        "analysis_schema_version": analysis["analysis_schema_version"],
        "load_config": str(loads_path),
        "load_config_sha256": sha256_file(loads_path),
        "load_schema_version": loads["load_schema_version"],
        "telemetry_schema_versions": sorted({
            record["config"]["predictionTelemetry"]["telemetry_schema_version"]
            for record in records
        }),
        "support_mask_versions": sorted({
            record["config"]["predictionTelemetry"]["feature_support_mask_version"]
            for record in records
        }),
        "project_git_commits": project_commits,
        "ns3_upstream_commits": ns3_commits,
        "build_profiles": build_profiles,
        "source_roots": sorted({str(source.source_root) for source in sources}),
        "included_runs": sorted(
            stats["included_runs"], key=lambda item: item["run_id"]
        ),
        "excluded_runs": sorted(excluded_runs, key=lambda item: item["run_id"]),
        "rejected_runs": [],
        "source_checksums": source_checksums,
        "feature_dictionary": feature_dictionary(columns),
        "label_definitions": {
            "frame_complete": "1 when frames.csv incomplete is 0",
            "frame_completion_time_ns": "frames.csv union_completion_us multiplied by 1000",
            "frame_latency_us": "frames.csv union_latency_us",
            "deadline_miss": "frames.csv deadline_miss; incomplete frames are misses",
            "stage": "prediction_samples.csv sample_stage and sample_offset_us",
        },
        "counts": {
            "run_count": len(records),
            "run_group_count": len(groups),
            "frame_count": stats["frame_count"],
            "sample_count": stats["sample_count"],
            "miss_count": stats["miss_count"],
        },
        "counts_by_split_role": split_counts,
        "counts_by_ood_scenario": ood_counts,
        "required_ood_scenarios": analysis["required_ood_scenarios"],
        "split_sufficiency_status": split_status,
        "command": command or [],
        "dependencies": {
            "python": platform.python_version(),
            "pyyaml": yaml.__version__,
            "pyarrow": pyarrow_version,
        },
    }
    _atomic_json(output_dir / "dataset_manifest.json", manifest)

    from validate_prediction_dataset import validate_dataset

    validation = validate_dataset(output_dir, analysis_path)
    _atomic_json(output_dir / "dataset_validation.json", validation)
    manifest["validation_status"] = validation["status"]
    manifest["dataset_validation_file"] = "dataset_validation.json"
    _atomic_json(output_dir / "dataset_manifest.json", manifest)
    return manifest


def build_dataset(
    inputs: list[Path],
    output_dir: Path,
    analysis_path: Path,
    loads_path: Path,
    requested_format: str,
    scenario_filters: set[str] | None = None,
    command: list[str] | None = None,
) -> dict[str, Any]:
    """Build and validate privately, then atomically publish the directory."""
    output_dir = output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if output_dir.exists():
        if any(output_dir.iterdir()):
            raise FileExistsError(f"dataset output directory is not empty: {output_dir}")
        output_dir.rmdir()
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{output_dir.name}.building-",
            dir=output_dir.parent,
        )
    )
    try:
        manifest = _build_dataset_at(
            inputs=inputs,
            output_dir=staging,
            analysis_path=analysis_path,
            loads_path=loads_path,
            requested_format=requested_format,
            scenario_filters=scenario_filters,
            command=command,
        )
        os.replace(staging, output_dir)
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--analysis-config", required=True, type=Path)
    parser.add_argument("--load-config", required=True, type=Path)
    parser.add_argument("--format", required=True, choices=("csv", "parquet"))
    parser.add_argument("--scenario", action="append", dest="scenarios")
    args = parser.parse_args()
    manifest = build_dataset(
        inputs=[path.resolve() for path in args.inputs],
        output_dir=args.output_dir,
        analysis_path=args.analysis_config,
        loads_path=args.load_config,
        requested_format=args.format,
        scenario_filters=set(args.scenarios) if args.scenarios else None,
        command=[sys.executable, *sys.argv],
    )
    print(
        f"WROTE {args.output_dir} runs={manifest['counts']['run_count']} "
        f"frames={manifest['counts']['frame_count']} "
        f"samples={manifest['counts']['sample_count']} "
        f"format={manifest['actual_format']}"
    )


if __name__ == "__main__":
    main()
