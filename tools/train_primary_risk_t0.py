#!/usr/bin/env python3
"""Train a treatment-free OBSS T0 predictor for primary-copy deadline risk."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import resource
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import yaml
from joblib.hashing import NumpyHasher

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from prediction.calibration import fit_platt
from prediction.features import CATEGORICAL_VOCABULARIES, encode_value
from prediction.metrics import (
    average_precision_tied,
    probability_metrics,
    topk_metrics,
)
from prediction.models import CANDIDATES, fit_pipeline, ranking_score
from prediction.online_replay import (
    MODEL_BUNDLE_SCHEMA_VERSION,
    FrozenPredictor,
    ModelBundle,
    read_model_bundle,
    write_model_bundle,
)

TRAINING_SCHEMA_VERSION = 1
PREDICTOR_FINGERPRINT_METHOD = "joblib_numpy_hasher_sha256"
REQUIRED_CONFIG_KEYS = {
    "primary_risk_training_schema_version",
    "artifact_id",
    "model_id",
    "dataset",
    "base_bundle",
    "target",
    "expected_population",
    "split",
    "model",
    "calibration",
    "ranking_budgets",
    "confidence_level",
    "risk_density_operating_point",
}


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of one file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    """Return a stable ASCII JSON representation."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def provenance_sha256(value: dict[str, Any]) -> str:
    """Hash one canonical provenance object."""
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def is_sha256(value: Any) -> bool:
    """Return whether a value is one lowercase hexadecimal SHA-256 digest."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def load_config(path: Path) -> dict[str, Any]:
    """Load and strictly validate the frozen training configuration."""
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("primary-risk training YAML root must be a mapping")
    missing = sorted(REQUIRED_CONFIG_KEYS - value.keys())
    if missing:
        raise ValueError(f"primary-risk training config is missing: {', '.join(missing)}")
    if value["primary_risk_training_schema_version"] != TRAINING_SCHEMA_VERSION:
        raise ValueError("unsupported primary-risk training schema")
    dataset = value["dataset"]
    if not is_sha256(dataset.get("manifest_sha256")) or not is_sha256(
        dataset.get("sha256")
    ):
        raise ValueError("primary-risk dataset digests are not frozen SHA-256 values")
    target = value["target"]
    expected_target = {
        "target_id": "primary_copy_deadline_miss",
        "source_column": "deadline_miss",
        "source_equivalence": "fixed_link_copy_0_equals_union",
        "deadline_comparator": "completion_us_strictly_greater_than_deadline",
        "incomplete_is_miss": True,
        "treatment_free": True,
        "scenario_name": "obss_only",
        "selected_policy": "fixed_link_1",
        "path_id": 1,
        "copy_id": 0,
        "stage": "T0",
        "feature_set": "F0+F1-degraded",
        "degradation_profile": "polling_1ms",
    }
    if target != expected_target:
        raise ValueError("primary-risk target contract changed")
    split = value["split"]
    folds = int(split.get("fold_count", 0))
    roles = split.get("role_by_fold")
    if folds != 4 or roles != ["test", "calibration", "training", "training"]:
        raise ValueError("primary-risk split contract changed")
    if value["calibration"].get("method") != "platt":
        raise ValueError("primary-risk calibration must use Platt scaling")
    if value["risk_density_operating_point"].get("threshold_comparator") != "strict_greater":
        raise ValueError("risk-density threshold must match the controller's strict gate")
    operating = value["risk_density_operating_point"]
    budgets = list(map(float, operating.get("estimated_airtime_budget_fractions", [])))
    if budgets != [0.005, 0.007, 0.0095]:
        raise ValueError("risk-density budget candidates changed")
    if float(operating.get("formal_maximum_budget_fraction", -1)) != budgets[-1]:
        raise ValueError("risk-density formal maximum changed")
    required_recall = 1.0 - float(operating["reference_mlo_miss_rate"]) / float(
        operating["reference_primary_miss_rate"]
    )
    if not np.isclose(
        required_recall,
        float(operating["minimum_plausible_primary_miss_recall"]),
        rtol=0,
        atol=1e-12,
    ):
        raise ValueError("risk-density defeat-recall target is inconsistent")
    return value


def assign_group_roles(
    groups: list[str] | set[str], split: dict[str, Any]
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Assign groups to frozen folds without consulting outcomes."""
    seed = str(split["seed"])
    fold_count = int(split["fold_count"])
    roles = list(split["role_by_fold"])
    ordered = sorted(
        set(groups),
        key=lambda group: hashlib.sha256(f"{seed}:{group}".encode()).digest(),
    )
    assignment: dict[str, str] = {}
    entries = []
    for position, group in enumerate(ordered):
        fold = position % fold_count
        assignment[group] = roles[fold]
        entries.append(
            {
                "run_group_id": group,
                "order": position,
                "fold": fold,
                "split_role": roles[fold],
                "ordering_sha256": hashlib.sha256(f"{seed}:{group}".encode()).hexdigest(),
            }
        )
    return assignment, entries


def primary_miss_from_fixed_frame(row: dict[str, str], expected_path: int) -> int:
    """Validate fixed-copy equivalence and derive the primary deadline label."""
    if int(row["primary_link"]) != expected_path:
        raise ValueError("fixed-link frame primary path changed")
    if row["duplicated"].lower() not in {"0", "false"}:
        raise ValueError("primary-risk source frame received a duplication treatment")
    if row["copy_1_completion_us"]:
        raise ValueError("primary-risk source frame contains copy 1")
    if row["copy_0_completion_us"] != row["union_completion_us"]:
        raise ValueError("fixed-link copy-0 and union completion differ")
    generation_us = int(row["generation_time_us"])
    deadline_us = int(row["deadline_us"])
    if deadline_us <= 0:
        raise ValueError("primary-risk source frame has no positive deadline")
    completion = row["copy_0_completion_us"]
    primary_miss = int(not completion or int(completion) > generation_us + deadline_us)
    if int(row["deadline_miss"]) != primary_miss:
        raise ValueError("fixed-link union label is not copy-0 equivalent")
    return primary_miss


def estimate_whole_copy_airtime_us(
    frame_size_bytes: np.ndarray,
    packet_count: np.ndarray,
    estimator: dict[str, Any],
) -> np.ndarray:
    """Reproduce the controller's nominal whole-copy airtime estimate."""
    sizes = np.asarray(frame_size_bytes, dtype=np.float64)
    packets = np.asarray(packet_count, dtype=np.float64)
    if np.any(sizes <= 0) or np.any(packets <= 0):
        raise ValueError("airtime estimation requires positive frame descriptors")
    expected_service = sizes + packets * (
        int(estimator["streaming_header_bytes"])
        + int(estimator["expected_mac_service_overhead_bytes"])
    )
    mac_bytes = expected_service + packets * int(
        estimator["additional_airtime_bytes_per_packet"]
    )
    nominal = float(estimator["phy_preamble_us"]) + (
        8.0 * mac_bytes / float(estimator["phy_data_rate_bps"])
    ) * 1e6
    return (
        float(estimator["cost_safety_factor"])
        * float(estimator["retry_inflation"])
        * nominal
    )


def select_risk_density_threshold(
    probability: np.ndarray,
    normalized_cost: np.ndarray,
    estimated_cost_us: np.ndarray,
    budget_us: float,
) -> tuple[float, np.ndarray]:
    """Select the strict density gate with greatest cost not over budget.

    Labels are deliberately absent from this interface so they cannot affect
    operating-point selection.
    """
    probability = np.asarray(probability, dtype=np.float64)
    normalized_cost = np.asarray(normalized_cost, dtype=np.float64)
    estimated_cost_us = np.asarray(estimated_cost_us, dtype=np.float64)
    if not (
        len(probability) == len(normalized_cost) == len(estimated_cost_us)
        and len(probability) > 0
    ):
        raise ValueError("risk-density vectors must have one nonempty common length")
    if (
        not np.all(np.isfinite(probability))
        or not np.all(np.isfinite(normalized_cost))
        or not np.all(np.isfinite(estimated_cost_us))
        or np.any((probability < 0) | (probability > 1))
        or np.any(normalized_cost <= 0)
        or np.any(estimated_cost_us <= 0)
        or not np.isfinite(budget_us)
        or budget_us < 0
    ):
        raise ValueError("invalid risk-density operating-point input")
    density = probability / normalized_cost
    best_threshold = float(np.max(density))
    best_action = density > best_threshold
    best_cost = 0.0
    candidates = np.unique(np.concatenate((density, np.asarray([0.0]))))
    for threshold in candidates:
        action = density > threshold
        cost = float(estimated_cost_us[action].sum())
        if cost <= budget_us + 1e-9 and cost > best_cost + 1e-9:
            best_threshold = float(threshold)
            best_action = action
            best_cost = cost
    return best_threshold, best_action


def physical_feature_names(predictor: FrozenPredictor) -> tuple[str, ...]:
    """Map the deployed feature contract to recorded dataset columns."""
    return tuple(
        f"polling_1ms_{name}" if name in predictor.f1_feature_names else name
        for name in predictor.feature_names
    )


def predictor_fingerprint(predictor: Any) -> str:
    """Return a canonical SHA-256 fingerprint of one fitted predictor."""
    return NumpyHasher(hash_name="sha256", coerce_mmap=False).hash(predictor)


def preserved_predictor_fingerprints(
    base: dict[tuple[str, str], Any],
    output: dict[tuple[str, str], Any],
    replaced_key: tuple[str, str],
) -> list[dict[str, Any]]:
    """Prove that every non-replaced predictor has identical serialized state."""
    if set(base) != set(output):
        raise ValueError("output predictor key set differs from base bundle")
    fingerprints = []
    for key in sorted(base):
        if key == replaced_key:
            continue
        base_digest = predictor_fingerprint(base[key])
        output_digest = predictor_fingerprint(output[key])
        if base_digest != output_digest:
            raise ValueError(f"preserved predictor changed: {key[0]} {key[1]}")
        fingerprints.append(
            {
                "pipeline_id": key[0],
                "stage": key[1],
                "method": PREDICTOR_FINGERPRINT_METHOD,
                "sha256": base_digest,
            }
        )
    return fingerprints


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return value


def _write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as output:
        json.dump(value, output, indent=2, sort_keys=True, allow_nan=False)
        output.write("\n")


def _audit_source_frames(
    manifest: dict[str, Any], config: dict[str, Any]
) -> tuple[dict[tuple[str, int], int], dict[str, Any]]:
    target = config["target"]
    population = config["expected_population"]
    selected = [
        run
        for run in manifest["included_runs"]
        if run["scenario_name"] == target["scenario_name"]
        and run["selected_policy"] == target["selected_policy"]
        and int(run["selected_path"]) == int(target["path_id"])
    ]
    if len(selected) != int(population["run_count"]):
        raise ValueError("treatment-free source run count differs from frozen population")
    labels: dict[tuple[str, int], int] = {}
    groups: dict[str, list[str]] = defaultdict(list)
    audited_checksums: dict[str, dict[str, str]] = {}
    durations: dict[str, int] = {}
    for run in sorted(selected, key=lambda item: item["run_id"]):
        run_dir = Path(run["source_directory"])
        frame_path = run_dir / "frames.csv"
        if not frame_path.is_file():
            raise ValueError(f"source frame file is unavailable: {frame_path}")
        checksums = manifest["source_checksums"][run["run_id"]]
        expected_checksum = checksums["frames.csv"]
        observed_checksum = sha256_file(frame_path)
        if observed_checksum != expected_checksum:
            raise ValueError(f"source frame checksum changed: {run['run_id']}")
        resolved_path = run_dir / "resolved_config.json"
        build_path = run_dir / "build_info.json"
        if (
            sha256_file(resolved_path) != checksums["resolved_config.json"]
            or sha256_file(build_path) != checksums["build_info.json"]
        ):
            raise ValueError(f"source provenance checksum changed: {run['run_id']}")
        resolved = _json(resolved_path)
        build = _json(build_path)
        if (
            resolved.get("run_id") != run["run_id"]
            or resolved.get("policy") != target["selected_policy"]
            or int(resolved.get("duration_s", -1))
            != int(population["measurement_duration_s"])
            or resolved.get("topology") != "dual_interface"
            or resolved.get("predictionTelemetry", {}).get("telemetry_schema_version")
            not in config["dataset"]["telemetry_schema_versions"]
            or build.get("project_git_commit")
            not in config["dataset"]["project_git_commits"]
            or build.get("ns3_upstream_commit")
            not in config["dataset"]["ns3_upstream_commits"]
        ):
            raise ValueError(f"source run treatment or duration changed: {run['run_id']}")
        frame_count = miss_count = 0
        with frame_path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            for row in reader:
                if row.get("run_id") != run["run_id"]:
                    raise ValueError(
                        f"source frame run ID differs from manifest: {run['run_id']}"
                    )
                frame_id = int(row["frame_id"])
                key = (run["run_id"], frame_id)
                if key in labels:
                    raise ValueError(f"duplicate primary label: {key}")
                label = primary_miss_from_fixed_frame(row, int(target["path_id"]))
                labels[key] = label
                frame_count += 1
                miss_count += label
        if frame_count != int(population["frames_per_run"]):
            raise ValueError(f"source frame count changed: {run['run_id']}")
        if frame_count != int(run["frame_count"]) or miss_count != int(run["miss_count"]):
            raise ValueError(f"source manifest counts changed: {run['run_id']}")
        groups[run["run_group_id"]].append(run["run_id"])
        audited_checksums[run["run_id"]] = {
            "frames.csv": observed_checksum,
            "resolved_config.json": checksums["resolved_config.json"],
            "build_info.json": checksums["build_info.json"],
        }
        durations[run["run_id"]] = int(resolved["duration_s"])
    if len(groups) != int(population["run_group_count"]):
        raise ValueError("treatment-free run-group count differs from frozen population")
    if len(labels) != int(population["frame_count"]):
        raise ValueError("treatment-free frame count differs from frozen population")
    if sum(labels.values()) != int(population["miss_count"]):
        raise ValueError("treatment-free miss count differs from frozen population")
    if any(len(runs) != 1 for runs in groups.values()):
        raise ValueError("path-1 target population contains repeated run groups")
    return labels, {
        "run_count": len(selected),
        "run_group_count": len(groups),
        "frame_count": len(labels),
        "miss_count": sum(labels.values()),
        "run_ids_by_group": dict(sorted(groups.items())),
        "source_file_sha256": audited_checksums,
        "measurement_duration_s_by_run": durations,
    }


def _encode_column(name: str, values: list[Any]) -> np.ndarray:
    return np.asarray(
        [encode_value(name, "" if value is None else str(value)) for value in values],
        dtype=np.float32,
    )


def _load_target_rows(
    dataset_path: Path,
    predictor: FrozenPredictor,
    config: dict[str, Any],
    source_labels: dict[tuple[str, int], int],
) -> dict[str, Any]:
    target = config["target"]
    physical_features = list(physical_feature_names(predictor))
    metadata = [
        "sample_stage",
        "path_id",
        "copy_id",
        "scenario_name",
        "selected_policy",
        "deadline_miss",
        "actionable",
        "run_group_id",
        "run_id",
        "frame_id",
        "frame_size_bytes",
        "frame_packet_count",
        "frame_type",
    ]
    columns = list(dict.fromkeys(metadata + physical_features))
    table = pq.read_table(
        dataset_path,
        columns=columns,
        filters=[
            ("sample_stage", "=", target["stage"]),
            ("path_id", "=", str(target["path_id"])),
            ("copy_id", "=", str(target["copy_id"])),
            ("scenario_name", "=", target["scenario_name"]),
            ("selected_policy", "=", target["selected_policy"]),
        ],
    )
    values = table.to_pydict()
    expected = config["expected_population"]
    if table.num_rows != int(expected["frame_count"]):
        raise ValueError("target T0 dataset row count differs from frozen population")
    matrix = np.empty((table.num_rows, len(predictor.feature_names)), dtype=np.float32)
    for index, (name, physical) in enumerate(
        zip(predictor.feature_names, physical_features, strict=True)
    ):
        matrix[:, index] = _encode_column(name, values[physical])
    run_ids = np.asarray(values["run_id"], dtype=object)
    frame_ids = np.asarray(values["frame_id"], dtype=np.int64)
    labels = np.asarray(values["deadline_miss"], dtype=np.int8)
    for position, (run_id, frame_id, label) in enumerate(
        zip(run_ids, frame_ids, labels, strict=True)
    ):
        source_label = source_labels.get((str(run_id), int(frame_id)))
        if source_label is None or source_label != int(label):
            raise ValueError(f"dataset primary label mismatch at row {position}")
    actionable = np.asarray(
        [str(value).lower() in {"1", "true"} for value in values["actionable"]],
        dtype=bool,
    )
    if not np.all(actionable):
        raise ValueError("frozen target population contains a non-actionable T0 row")
    keys = set(zip(run_ids.tolist(), frame_ids.tolist(), strict=True))
    if len(keys) != table.num_rows:
        raise ValueError("target T0 dataset contains duplicate frame rows")
    return {
        "matrix": matrix,
        "label": labels,
        "group": np.asarray(values["run_group_id"], dtype=object),
        "run": run_ids,
        "frame": frame_ids,
        "frame_size_bytes": np.asarray(values["frame_size_bytes"], dtype=np.int64),
        "frame_packet_count": np.asarray(values["frame_packet_count"], dtype=np.int64),
        "frame_type": np.asarray(values["frame_type"], dtype=object),
        "physical_feature_names": physical_features,
    }


def _ranking_metrics(
    label: np.ndarray,
    ranking: np.ndarray,
    probability: np.ndarray,
    config: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "frame_count": len(label),
        "miss_count": int(label.sum()),
        **probability_metrics(
            label,
            probability,
            int(config["calibration"]["bin_count"]),
        ),
        "ranking_average_precision": average_precision_tied(label, ranking),
        "topk": {},
    }
    for budget in map(float, config["ranking_budgets"]):
        result["topk"][format(budget, ".12g")] = topk_metrics(
            label,
            ranking,
            budget,
            float(config["confidence_level"]),
        )
    return result


def _action_metrics(
    label: np.ndarray,
    action: np.ndarray,
    estimated_cost_us: np.ndarray,
    duration_us: float,
) -> dict[str, Any]:
    true_positive = int(np.sum(action & (label == 1)))
    actions = int(action.sum())
    misses = int(label.sum())
    estimated = float(estimated_cost_us[action].sum())
    return {
        "frame_count": len(label),
        "primary_miss_count": misses,
        "action_count": actions,
        "true_positive_count": true_positive,
        "false_positive_count": actions - true_positive,
        "action_rate": actions / len(label) if len(label) else None,
        "primary_miss_recall": true_positive / misses if misses else None,
        "primary_miss_precision": true_positive / actions if actions else None,
        "estimated_airtime_us": estimated,
        "estimated_airtime_fraction": estimated / duration_us if duration_us else None,
    }


def _operating_point_metrics(
    label: np.ndarray,
    probability: np.ndarray,
    normalized_cost: np.ndarray,
    estimated_cost_us: np.ndarray,
    frame_type: np.ndarray,
    duration_us: float,
    threshold: float,
) -> dict[str, Any]:
    action = probability / normalized_cost > threshold
    result = _action_metrics(label, action, estimated_cost_us, duration_us)
    result["risk_density_threshold"] = threshold
    result["strata"] = {}
    for name in sorted(set(map(str, frame_type))):
        mask = frame_type == name
        result["strata"][name] = _action_metrics(
            label[mask],
            action[mask],
            estimated_cost_us[mask],
            duration_us,
        )
    return result


def _predictor_manifest(predictors: dict[tuple[str, str], FrozenPredictor]) -> list[dict[str, Any]]:
    return [
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
        for _, predictor in sorted(predictors.items())
    ]


def _cross_validated_metrics(
    data: dict[str, Any],
    split_entries: list[dict[str, Any]],
    candidate: Any,
    categorical: tuple[int, ...],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate four label-blind group folds without producing deployment state."""
    group_fold = {entry["run_group_id"]: int(entry["fold"]) for entry in split_entries}
    folds = np.asarray([group_fold[str(group)] for group in data["group"]], dtype=np.int8)
    ranking = np.full(len(data["label"]), np.nan, dtype=np.float64)
    probability = np.full(len(data["label"]), np.nan, dtype=np.float64)
    fold_metrics = []
    for test_fold in range(int(config["split"]["fold_count"])):
        calibration_fold = (test_fold + 1) % int(config["split"]["fold_count"])
        test = folds == test_fold
        calibration = folds == calibration_fold
        training = ~(test | calibration)
        fitted = fit_pipeline(
            candidate,
            data["matrix"][training],
            data["label"][training],
            categorical,
            int(config["model"]["analysis_seed"]),
        )
        calibration_score = ranking_score(fitted, data["matrix"][calibration])
        calibrator = fit_platt(
            calibration_score,
            data["label"][calibration],
            int(config["calibration"]["seed"]),
        )
        test_score = ranking_score(fitted, data["matrix"][test])
        ranking[test] = test_score
        probability[test] = calibrator.predict(test_score)
        fold_metrics.append(
            {
                "test_fold": test_fold,
                "calibration_fold": calibration_fold,
                "training_folds": sorted(set(range(4)) - {test_fold, calibration_fold}),
                "training_run_group_count": len(set(data["group"][training])),
                "calibration_run_group_count": len(set(data["group"][calibration])),
                "test_run_group_count": len(set(data["group"][test])),
                "metrics": _ranking_metrics(
                    data["label"][test],
                    test_score,
                    probability[test],
                    config,
                ),
            }
        )
    if not np.all(np.isfinite(ranking)) or not np.all(np.isfinite(probability)):
        raise AssertionError("cross-validation did not score every target row")
    return {
        "assignment_uses_labels": False,
        "aggregate_metrics": _ranking_metrics(
            data["label"], probability, probability, config
        ),
        "folds": fold_metrics,
    }


def train(args: argparse.Namespace) -> dict[str, Any]:
    """Train, validate, and atomically publish one target-domain bundle."""
    started = time.perf_counter()
    config_path = args.config.resolve()
    config = load_config(config_path)
    dataset_dir = args.dataset_dir.resolve()
    dataset_manifest_path = dataset_dir / "dataset_manifest.json"
    dataset_manifest = _json(dataset_manifest_path)
    dataset_path = dataset_dir / dataset_manifest["dataset_file"]
    frozen_dataset = config["dataset"]
    if sha256_file(dataset_manifest_path) != frozen_dataset["manifest_sha256"]:
        raise ValueError("dataset manifest file differs from frozen training config")
    if dataset_manifest.get("dataset_schema_version") != frozen_dataset["schema_version"]:
        raise ValueError("dataset schema differs from frozen training config")
    if dataset_manifest.get("dataset_sha256") != frozen_dataset["sha256"]:
        raise ValueError("dataset manifest digest differs from frozen training config")
    if sha256_file(dataset_path) != frozen_dataset["sha256"]:
        raise ValueError("dataset file digest differs from frozen training config")
    for key in (
        "project_git_commits",
        "ns3_upstream_commits",
        "telemetry_schema_versions",
        "support_mask_versions",
    ):
        if dataset_manifest.get(key) != frozen_dataset[key]:
            raise ValueError(f"dataset {key} differs from frozen training config")

    base_dir = args.base_bundle_dir.resolve()
    base_manifest_path = base_dir / "model_bundle_manifest.json"
    base_manifest = _json(base_manifest_path)
    base_model_path = base_dir / base_manifest["model_file"]
    base_digest = sha256_file(base_model_path)
    if (
        base_digest != config["base_bundle"]["sha256"]
        or base_manifest.get("model_sha256") != base_digest
    ):
        raise ValueError("base model bundle differs from frozen training config")
    base_bundle = read_model_bundle(base_model_path)
    pipeline_id = config["base_bundle"]["pipeline_id"]
    old_t0 = base_bundle.predictors.get((pipeline_id, "T0"))
    if old_t0 is None:
        raise ValueError("base bundle lacks the frozen commodity T0 predictor")
    for stage in config["base_bundle"]["preserved_stages"]:
        if (pipeline_id, stage) not in base_bundle.predictors:
            raise ValueError(f"base bundle lacks preserved predictor {stage}")

    source_labels, source_audit = _audit_source_frames(dataset_manifest, config)
    data = _load_target_rows(dataset_path, old_t0, config, source_labels)
    groups = sorted(set(map(str, data["group"])))
    assignment, split_entries = assign_group_roles(groups, config["split"])
    roles = np.asarray([assignment[str(group)] for group in data["group"]], dtype=object)
    train_mask = roles == "training"
    calibration_mask = roles == "calibration"
    test_mask = roles == "test"
    for role, mask in (
        ("training", train_mask),
        ("calibration", calibration_mask),
        ("test", test_mask),
    ):
        if len(np.unique(data["label"][mask])) != 2:
            raise ValueError(f"frozen {role} split lacks one outcome class")

    candidate = next(
        item for item in CANDIDATES if item.name == config["model"]["name"]
    )
    if candidate.parameters != config["model"]["parameters"]:
        raise ValueError("frozen HGB parameters differ from implementation")
    categorical = tuple(
        index
        for index, name in enumerate(old_t0.feature_names)
        if name.removeprefix("polling_1ms_") in CATEGORICAL_VOCABULARIES
    )
    evaluation_fitted = fit_pipeline(
        candidate,
        data["matrix"][train_mask],
        data["label"][train_mask],
        categorical,
        int(config["model"]["analysis_seed"]),
    )
    evaluation_calibration_ranking = ranking_score(
        evaluation_fitted, data["matrix"][calibration_mask]
    )
    evaluation_calibrator = fit_platt(
        evaluation_calibration_ranking,
        data["label"][calibration_mask],
        int(config["calibration"]["seed"]),
    )
    evaluation_calibration_probability = evaluation_calibrator.predict(
        evaluation_calibration_ranking
    )
    test_ranking = ranking_score(evaluation_fitted, data["matrix"][test_mask])
    test_probability = evaluation_calibrator.predict(test_ranking)
    evaluation_calibration_metrics = _ranking_metrics(
        data["label"][calibration_mask],
        evaluation_calibration_ranking,
        evaluation_calibration_probability,
        config,
    )
    test_metrics = _ranking_metrics(
        data["label"][test_mask],
        test_ranking,
        test_probability,
        config,
    )
    cross_validation_metrics = _cross_validated_metrics(
        data, split_entries, candidate, categorical, config
    )

    # After all honest group-held-out metrics are produced, refit the
    # deployment ranker on every non-calibration group. The six calibration
    # groups remain untouched by ranker fitting and alone fit Platt scaling
    # and the risk-density gates. Future simulations are the independent
    # controller test for this deployment model.
    deployment_training_mask = ~calibration_mask
    deployment_fitted = fit_pipeline(
        candidate,
        data["matrix"][deployment_training_mask],
        data["label"][deployment_training_mask],
        categorical,
        int(config["model"]["analysis_seed"]),
    )
    deployment_calibration_ranking = ranking_score(
        deployment_fitted, data["matrix"][calibration_mask]
    )
    deployment_calibrator = fit_platt(
        deployment_calibration_ranking,
        data["label"][calibration_mask],
        int(config["calibration"]["seed"]),
    )
    deployment_calibration_probability = deployment_calibrator.predict(
        deployment_calibration_ranking
    )
    deployment_calibration_metrics = _ranking_metrics(
        data["label"][calibration_mask],
        deployment_calibration_ranking,
        deployment_calibration_probability,
        config,
    )

    estimator = config["risk_density_operating_point"]
    all_cost = estimate_whole_copy_airtime_us(
        data["frame_size_bytes"], data["frame_packet_count"], estimator
    )
    reference_cost = float(
        estimate_whole_copy_airtime_us(
            np.asarray([estimator["reference_frame_size_bytes"]]),
            np.asarray([estimator["reference_packet_count"]]),
            estimator,
        )[0]
    )
    if not np.isclose(
        reference_cost,
        float(estimator["expected_reference_airtime_us"]),
        rtol=1e-12,
        atol=1e-9,
    ):
        raise ValueError("frozen reference airtime differs from controller estimator")
    i_cost = float(
        estimate_whole_copy_airtime_us(np.asarray([48000]), np.asarray([40]), estimator)[0]
    )
    if not np.isclose(
        i_cost,
        float(estimator["expected_i_frame_airtime_us"]),
        rtol=1e-12,
        atol=1e-9,
    ):
        raise ValueError("frozen I-frame airtime differs from controller estimator")
    normalized_cost = all_cost / reference_cost
    measurement_duration_us = (
        int(config["expected_population"]["measurement_duration_s"]) * 1e6
    )
    calibration_duration_us = (
        len(set(data["run"][calibration_mask])) * measurement_duration_us
    )
    test_duration_us = len(set(data["run"][test_mask])) * measurement_duration_us

    # Select gates with the evaluation model's calibration partition, then
    # apply each gate unchanged to the outcome-blind held-out test partition.
    # These are the honest operating-point estimates. They are separate from
    # the deployment gates below because the deployment ranker was refit with
    # the former test groups.
    evaluation_operating_points: dict[str, Any] = {}
    for budget_fraction in map(
        float, estimator["estimated_airtime_budget_fractions"]
    ):
        calibration_budget_us = budget_fraction * calibration_duration_us
        density_threshold, calibration_action = select_risk_density_threshold(
            evaluation_calibration_probability,
            normalized_cost[calibration_mask],
            all_cost[calibration_mask],
            calibration_budget_us,
        )
        expected_action = (
            evaluation_calibration_probability / normalized_cost[calibration_mask]
            > density_threshold
        )
        if not np.array_equal(calibration_action, expected_action):
            raise AssertionError("evaluation risk-density selector disagrees with strict gate")
        key = format(budget_fraction, ".12g")
        evaluation_operating_points[key] = {
            "estimated_airtime_budget_fraction": budget_fraction,
            "threshold_selection_role": "calibration",
            "risk_density_threshold": density_threshold,
            "calibration": _operating_point_metrics(
                data["label"][calibration_mask],
                evaluation_calibration_probability,
                normalized_cost[calibration_mask],
                all_cost[calibration_mask],
                data["frame_type"][calibration_mask],
                calibration_duration_us,
                density_threshold,
            ),
            "heldout_test": _operating_point_metrics(
                data["label"][test_mask],
                test_probability,
                normalized_cost[test_mask],
                all_cost[test_mask],
                data["frame_type"][test_mask],
                test_duration_us,
                density_threshold,
            ),
        }
    evaluation_risk_density = {
        "ranker_training_role": "training",
        "threshold_selection_role": "calibration",
        "heldout_application_role": "test",
        "threshold_selection_uses_labels": False,
        "budget_recommendation_uses_heldout_outcomes": False,
        "threshold_comparator": "strict_greater",
        "operating_points": evaluation_operating_points,
    }

    operating_points: dict[str, Any] = {}
    recommended_budget = None
    minimum_plausible_recall = float(estimator["minimum_plausible_primary_miss_recall"])
    for budget_fraction in map(
        float, estimator["estimated_airtime_budget_fractions"]
    ):
        calibration_budget_us = budget_fraction * calibration_duration_us
        density_threshold, calibration_action = select_risk_density_threshold(
            deployment_calibration_probability,
            normalized_cost[calibration_mask],
            all_cost[calibration_mask],
            calibration_budget_us,
        )
        expected_action = (
            deployment_calibration_probability / normalized_cost[calibration_mask]
            > density_threshold
        )
        if not np.array_equal(calibration_action, expected_action):
            raise AssertionError("risk-density selector disagrees with strict controller gate")
        calibration_operating_metrics = _operating_point_metrics(
            data["label"][calibration_mask],
            deployment_calibration_probability,
            normalized_cost[calibration_mask],
            all_cost[calibration_mask],
            data["frame_type"][calibration_mask],
            calibration_duration_us,
            density_threshold,
        )
        key = format(budget_fraction, ".12g")
        operating_points[key] = {
            "estimated_airtime_budget_fraction": budget_fraction,
            "calibration_budget_us": calibration_budget_us,
            "risk_density_threshold": density_threshold,
            "calibration": calibration_operating_metrics,
        }
        # Thresholds are selected without labels. This recommendation uses
        # calibration outcomes only and never consults the held-out test rows.
        if (
            recommended_budget is None
            and calibration_operating_metrics["primary_miss_recall"]
            >= minimum_plausible_recall
        ):
            recommended_budget = budget_fraction
    operating_point = {
        "fit_scope": "deployment",
        "selection_partition": "calibration",
        "candidate_selection_uses_test_outcomes": False,
        "deployment_ranker_training_roles": ["training", "test"],
        "deployment_calibration_role": "calibration",
        "estimated_airtime_budget_fractions": list(
            map(float, estimator["estimated_airtime_budget_fractions"])
        ),
        "formal_maximum_budget_fraction": float(
            estimator["formal_maximum_budget_fraction"]
        ),
        "minimum_plausible_primary_miss_recall": minimum_plausible_recall,
        "smallest_calibration_budget_meeting_recall_target": recommended_budget,
        "threshold_comparator": "strict_greater",
        "risk_density_definition": "calibrated_probability / normalized_whole_copy_airtime",
        "reference_airtime_us": reference_cost,
        "operating_points": operating_points,
        "estimator": copy.deepcopy(estimator),
    }

    new_t0 = FrozenPredictor(
        pipeline_id=pipeline_id,
        feature_set=old_t0.feature_set,
        evidence_role=old_t0.evidence_role,
        stage="T0",
        feature_names=old_t0.feature_names,
        f1_feature_names=old_t0.f1_feature_names,
        degradation_profile=copy.deepcopy(old_t0.degradation_profile),
        model_name=candidate.name,
        selection_recall=float(
            deployment_calibration_metrics["topk"]["0.1"]["recall"]
        ),
        pipeline=deployment_fitted,
        calibrator=deployment_calibrator,
    )
    predictors = dict(base_bundle.predictors)
    predictors[(pipeline_id, "T0")] = new_t0
    bundle = ModelBundle(
        schema_version=MODEL_BUNDLE_SCHEMA_VERSION,
        replay_config_sha256=base_bundle.replay_config_sha256,
        analysis_config_sha256=base_bundle.analysis_config_sha256,
        dataset_sha256=base_bundle.dataset_sha256,
        primary_link=base_bundle.primary_link,
        feature_dictionary=base_bundle.feature_dictionary,
        predictors=predictors,
        median_frame_size_bytes=base_bundle.median_frame_size_bytes,
        p99_frame_size_bytes=base_bundle.p99_frame_size_bytes,
    )
    target_provenance = {
        **copy.deepcopy(config["target"]),
        "dataset_sha256": frozen_dataset["sha256"],
        "dataset_manifest_sha256": frozen_dataset["manifest_sha256"],
        "training_config_sha256": sha256_file(config_path),
        "split_seed": config["split"]["seed"],
        "evaluation_training_run_group_count": len(set(data["group"][train_mask])),
        "calibration_run_group_count": len(set(data["group"][calibration_mask])),
        "evaluation_test_run_group_count": len(set(data["group"][test_mask])),
        "deployment_training_run_group_count": len(
            set(data["group"][deployment_training_mask])
        ),
        "deployment_refit_uses_evaluation_test_groups": True,
        "future_simulation_is_independent_controller_test": True,
    }

    output_dir = args.output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.building-", dir=output_dir.parent)
    )
    backup = output_dir.with_name(f".{output_dir.name}.previous")
    try:
        model_path = staging / "model_bundle.pkl"
        write_model_bundle(model_path, bundle)
        published_bundle = read_model_bundle(model_path)
        preserved_fingerprints = preserved_predictor_fingerprints(
            base_bundle.predictors,
            published_bundle.predictors,
            (pipeline_id, "T0"),
        )
        model_digest = sha256_file(model_path)
        split_manifest = {
            "primary_risk_split_schema_version": 1,
            "split_seed": config["split"]["seed"],
            "fold_count": config["split"]["fold_count"],
            "role_by_fold": config["split"]["role_by_fold"],
            "groups": [
                {
                    **entry,
                    "run_ids": source_audit["run_ids_by_group"][entry["run_group_id"]],
                }
                for entry in split_entries
            ],
            "counts_by_role": {
                role: {
                    "run_group_count": len(set(data["group"][roles == role])),
                    "run_count": len(set(data["run"][roles == role])),
                    "frame_count": int(np.sum(roles == role)),
                    "miss_count": int(data["label"][roles == role].sum()),
                }
                for role in ("training", "calibration", "test")
            },
        }
        _write_json(staging / "group_splits.json", split_manifest)
        metrics_manifest = {
            "primary_risk_metrics_schema_version": 1,
            "model_id": config["model_id"],
            "target_provenance": target_provenance,
            "evaluation_fit": {
                "ranker_training_role": "training",
                "calibration_role": "calibration",
                "heldout_test_role": "test",
                "calibration_metrics": evaluation_calibration_metrics,
                "heldout_test_metrics": test_metrics,
                "risk_density_operating_points": evaluation_risk_density,
            },
            "cross_validation": cross_validation_metrics,
            "deployment_fit": {
                "ranker_training_roles": ["training", "test"],
                "calibration_role": "calibration",
                "calibration_metrics": deployment_calibration_metrics,
                "independent_test": "future fresh-seed simulation",
                "risk_density_operating_point": operating_point,
            },
        }
        _write_json(staging / "training_metrics.json", metrics_manifest)
        bundle_manifest = {
            "model_bundle_schema_version": MODEL_BUNDLE_SCHEMA_VERSION,
            "model_id": config["model_id"],
            "model_file": model_path.name,
            "model_sha256": model_digest,
            "dataset_schema_version": dataset_manifest["dataset_schema_version"],
            "dataset": os.path.relpath(dataset_path, output_dir),
            "dataset_sha256": frozen_dataset["sha256"],
            "dataset_manifest_sha256": frozen_dataset["manifest_sha256"],
            "f1_observation_source": "recorded_periodic_observation",
            "primary_link": bundle.primary_link,
            "primary_band": "5GHz",
            "training_config": os.path.relpath(config_path, output_dir),
            "training_config_sha256": sha256_file(config_path),
            "base_model_sha256": base_digest,
            "target_provenance": target_provenance,
            "target_provenance_sha256": provenance_sha256(target_provenance),
            "group_splits_file": "group_splits.json",
            "group_splits_sha256": sha256_file(staging / "group_splits.json"),
            "training_metrics_file": "training_metrics.json",
            "training_metrics_sha256": sha256_file(staging / "training_metrics.json"),
            "median_frame_size_bytes": bundle.median_frame_size_bytes,
            "p99_frame_size_bytes": bundle.p99_frame_size_bytes,
            "preserved_predictor_keys": [
                {"pipeline_id": key[0], "stage": key[1]}
                for key in sorted(base_bundle.predictors)
                if key != (pipeline_id, "T0")
            ],
            "preserved_predictor_fingerprints": preserved_fingerprints,
            "replaced_predictor_key": {"pipeline_id": pipeline_id, "stage": "T0"},
            "predictors": _predictor_manifest(predictors),
            "source_audit": source_audit,
            "command": [sys.executable, *sys.argv],
            "dependencies": {
                "python": platform.python_version(),
                "numpy": importlib.metadata.version("numpy"),
                "scipy": importlib.metadata.version("scipy"),
                "scikit_learn": importlib.metadata.version("scikit-learn"),
                "joblib": importlib.metadata.version("joblib"),
                "pyarrow": importlib.metadata.version("pyarrow"),
                "pyyaml": importlib.metadata.version("PyYAML"),
            },
            "runtime_seconds": time.perf_counter() - started,
            "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        }
        _write_json(staging / "model_bundle_manifest.json", bundle_manifest)
        if backup.exists():
            shutil.rmtree(backup)
        if output_dir.exists():
            os.replace(output_dir, backup)
        try:
            os.replace(staging, output_dir)
        except BaseException:
            if backup.exists() and not output_dir.exists():
                os.replace(backup, output_dir)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return bundle_manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", type=Path)
    parser.add_argument("--base-bundle-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        result = train(args)
    except (OSError, ValueError) as error:
        raise SystemExit(f"error: {error}") from error
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
