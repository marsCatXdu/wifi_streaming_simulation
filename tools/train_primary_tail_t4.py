#!/usr/bin/env python3
"""Build the frozen engineering T4 primary-miss and tail-risk artifact."""

from __future__ import annotations

import argparse
import copy
import csv
import importlib.metadata
import json
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import yaml

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from prediction.calibration import fit_platt
from prediction.features import CATEGORICAL_VOCABULARIES, encode_value
from prediction.models import CANDIDATES, fit_pipeline, ranking_score
from prediction.metrics import probability_metrics
from prediction.online_replay import FrozenPredictor, read_model_bundle
from prediction.primary_tail import (
    PRIMARY_TAIL_BUNDLE_SCHEMA_VERSION,
    PrimaryTailT4Bundle,
    read_primary_tail_bundle,
    write_primary_tail_bundle,
)
from train_primary_risk_t0 import (
    canonical_json,
    is_sha256,
    physical_feature_names,
    predictor_fingerprint,
    primary_miss_from_fixed_frame,
    provenance_sha256,
    sha256_file,
)

TRAINING_SCHEMA_VERSION = 2
REQUIRED_CONFIG_KEYS = {
    "primary_tail_t4_training_schema_version",
    "artifact_id",
    "model_id",
    "dataset",
    "base_bundle",
    "target",
    "expected_population",
    "split",
    "model",
    "calibration",
    "hybrid",
    "packetization",
    "expected_artifact",
}
HEAD_NAMES = ("primary_miss", "completed_tail")


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return value


def _write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8") as output:
        json.dump(value, output, indent=2, sort_keys=True, allow_nan=False)
        output.write("\n")


def load_config(path: Path) -> dict[str, Any]:
    """Load and strictly validate the frozen engineering configuration."""
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("primary-tail training YAML root must be a mapping")
    missing = sorted(REQUIRED_CONFIG_KEYS - value.keys())
    if missing:
        raise ValueError(f"primary-tail training config is missing: {', '.join(missing)}")
    unknown = sorted(value.keys() - REQUIRED_CONFIG_KEYS)
    if unknown:
        raise ValueError(
            "action-neutral primary-tail model config has unknown top-level keys: "
            + ", ".join(unknown)
        )
    if value["primary_tail_t4_training_schema_version"] != TRAINING_SCHEMA_VERSION:
        raise ValueError("unsupported primary-tail training schema")
    dataset = value["dataset"]
    if (
        not is_sha256(dataset.get("manifest_sha256"))
        or not is_sha256(dataset.get("validation_sha256"))
        or not is_sha256(dataset.get("sha256"))
    ):
        raise ValueError("primary-tail dataset digests are not frozen SHA-256 values")
    if (
        dataset.get("validation_status") != "PASS"
        or dataset.get("split_sufficiency_status") != "insufficient_data"
    ):
        raise ValueError("primary-tail dataset validation status changed")
    base = value["base_bundle"]
    if (
        not is_sha256(base.get("sha256"))
        or base.get("pipeline_id") != "exportable_driver_polling_1ms"
        or base.get("stage") != "T4"
        or base.get("feature_set") != "F0+F1-degraded+F2-exportable"
        or base.get("degradation_profile") != "polling_1ms"
        or int(base.get("expected_feature_count", 0)) != 101
    ):
        raise ValueError("primary-tail base predictor contract changed")
    target = value["target"]
    expected_target = {
        "source_column": "deadline_miss",
        "source_equivalence": "fixed_link_copy_0_equals_union",
        "deadline_comparator": "completion_us_strictly_greater_than_deadline",
        "incomplete_is_miss": True,
        "treatment_free": True,
        "scenario_name": "obss_only",
        "selected_policy": "fixed_link_1",
        "path_id": 1,
        "copy_id": 0,
        "stage": "T4",
        "feature_set": "F0+F1-degraded+F2-exportable",
        "degradation_profile": "polling_1ms",
        "actionable_only": True,
    }
    if any(target.get(key) != expected for key, expected in expected_target.items()):
        raise ValueError("primary-tail target contract changed")
    heads = target.get("heads", {})
    if target.get("source_oracle_features_enabled") is not False:
        raise ValueError("primary-tail source must keep oracle features disabled")
    if set(heads) != set(HEAD_NAMES):
        raise ValueError("primary-tail target head set changed")
    if (
        heads["primary_miss"].get("target_id") != "primary_miss_t4_v1"
        or heads["primary_miss"].get("fit_population") != "t4_actionable"
        or heads["completed_tail"].get("target_id")
        != "completed_primary_latency_ge_12500us_t4_v1"
        or heads["completed_tail"].get("fit_population")
        != "t4_actionable_and_primary_complete"
        or int(heads["completed_tail"].get("latency_threshold_us", 0)) != 12500
    ):
        raise ValueError("primary-tail label definitions changed")
    split = value["split"]
    training = list(map(int, split.get("deployment_training_seeds", [])))
    calibration = list(map(int, split.get("calibration_seeds", [])))
    folds_by_seed = [list(map(int, fold)) for fold in split.get("engineering_folds_by_seed", [])]
    if (
        split.get("status") != "engineering_meta_selected_no_independent_ood_claim"
        or split.get("independent_evaluation")
        != "fresh_seed_closed_loop_simulation_only"
        or int(split.get("fold_count", 0)) != 4
        or len(training) != 18
        or len(calibration) != 6
        or len(set(training)) != 18
        or len(set(calibration)) != 6
        or set(training) & set(calibration)
        or len(folds_by_seed) != 4
        or any(len(fold) != 6 or len(set(fold)) != 6 for fold in folds_by_seed)
        or set().union(*map(set, folds_by_seed)) != set(training) | set(calibration)
        or set(folds_by_seed[1]) != set(calibration)
    ):
        raise ValueError("primary-tail 18/6 deployment split changed")
    hybrid = value["hybrid"]
    if (
        hybrid.get("output_name") != "admission_score"
        or hybrid.get("score_kind") != "weighted_head_probability_admission_score"
        or hybrid.get("combiner") != "weighted_arithmetic_mean"
        or float(hybrid.get("primary_miss_weight", -1)) != 1.0
        or float(hybrid.get("completed_tail_weight", -1)) != 0.2
        or float(hybrid.get("normalization", -1)) != 1.2
        or hybrid.get("selection_status") != "engineering_meta_selected"
    ):
        raise ValueError("primary-tail hybrid combiner changed")
    if int(value["packetization"].get("payload_bytes_per_packet", 0)) != 1200:
        raise ValueError("primary-tail source packetization changed")
    if value["calibration"].get("method") != "platt":
        raise ValueError("primary-tail calibration must use Platt scaling")
    return value


def primary_outcome_from_fixed_frame(
    row: dict[str, str], expected_path: int
) -> dict[str, Any]:
    """Derive treatment-free primary miss, completion, and latency outcomes."""
    miss = primary_miss_from_fixed_frame(row, expected_path)
    generation_us = int(row["generation_time_us"])
    completion_text = row["copy_0_completion_us"]
    complete = bool(completion_text)
    if bool(int(row["incomplete"])) == complete:
        raise ValueError("fixed-link completion and incomplete fields disagree")
    latency_us = None if not complete else int(completion_text) - generation_us
    if latency_us is not None and latency_us < 0:
        raise ValueError("fixed-link primary completion precedes generation")
    union_latency = row.get("union_latency_us", "")
    if complete and union_latency and int(union_latency) != latency_us:
        raise ValueError("fixed-link union latency differs from copy-0 latency")
    return {
        "miss": miss,
        "complete": complete,
        "latency_us": latency_us,
        "frame_size_bytes": int(row["frame_size_bytes"]),
        "frame_packet_count": int(row["packet_count"]),
        "generation_time_us": generation_us,
    }


def _audit_source_outcomes(
    manifest: dict[str, Any], config: dict[str, Any]
) -> tuple[
    dict[tuple[int, int, int], dict[str, Any]],
    dict[str, Any],
    dict[str, tuple[int, int]],
]:
    """Audit source files and recover copy-0 outcomes independently of Parquet."""
    target = config["target"]
    population = config["expected_population"]
    split = config["split"]
    expected_seeds = set(map(int, split["deployment_training_seeds"])) | set(
        map(int, split["calibration_seeds"])
    )
    selected = [
        run
        for run in manifest["included_runs"]
        if run["scenario_name"] == target["scenario_name"]
        and run["selected_policy"] == target["selected_policy"]
        and int(run["selected_path"]) == int(target["path_id"])
    ]
    if (
        len(selected) != int(population["run_count"])
        or {int(item["seed"]) for item in selected} != expected_seeds
        or any(int(item["run"]) != 1 for item in selected)
    ):
        raise ValueError("treatment-free source seed/run population changed")
    outcomes: dict[tuple[int, int, int], dict[str, Any]] = {}
    identity_by_run_id: dict[str, tuple[int, int]] = {}
    source_files: dict[str, Any] = {}
    total_misses = 0
    for run in sorted(selected, key=lambda item: (int(item["seed"]), int(item["run"]))):
        seed = int(run["seed"])
        run_number = int(run["run"])
        run_id = str(run["run_id"])
        if run_id in identity_by_run_id:
            raise ValueError(f"duplicate source run ID: {run_id}")
        identity_by_run_id[run_id] = (seed, run_number)
        run_dir = Path(run["source_directory"])
        frame_path = run_dir / "frames.csv"
        resolved_path = run_dir / "resolved_config.json"
        build_path = run_dir / "build_info.json"
        checksums = manifest["source_checksums"].get(run_id, {})
        observed_checksums = {
            "frames.csv": sha256_file(frame_path),
            "resolved_config.json": sha256_file(resolved_path),
            "build_info.json": sha256_file(build_path),
        }
        if any(checksums.get(name) != digest for name, digest in observed_checksums.items()):
            raise ValueError(f"source provenance checksum changed: seed {seed}")
        resolved = _json(resolved_path)
        build = _json(build_path)
        telemetry = resolved.get("predictionTelemetry", {})
        if (
            resolved.get("run_id") != run_id
            or int(resolved.get("seed", -1)) != seed
            or int(resolved.get("run", -1)) != run_number
            or resolved.get("policy") != target["selected_policy"]
            or resolved.get("topology") != "dual_interface"
            or int(resolved.get("duration_s", -1))
            != int(population["measurement_duration_s"])
            or telemetry.get("telemetry_schema_version")
            not in config["dataset"]["telemetry_schema_versions"]
            or telemetry.get("oracle_features_enabled")
            is not target["source_oracle_features_enabled"]
            or build.get("project_git_commit")
            not in config["dataset"]["project_git_commits"]
            or build.get("ns3_upstream_commit")
            not in config["dataset"]["ns3_upstream_commits"]
        ):
            raise ValueError(f"source treatment/provenance changed: seed {seed}")
        frame_count = 0
        miss_count = 0
        with frame_path.open(newline="", encoding="utf-8") as source:
            for row in csv.DictReader(source):
                if row.get("run_id") != run_id:
                    raise ValueError(f"source frame run ID changed: seed {seed}")
                key = (seed, run_number, int(row["frame_id"]))
                if key in outcomes:
                    raise ValueError(f"duplicate primary outcome: {key}")
                outcome = primary_outcome_from_fixed_frame(
                    row, int(target["path_id"])
                )
                outcomes[key] = outcome
                frame_count += 1
                miss_count += int(outcome["miss"])
        if (
            frame_count != int(population["frames_per_run"])
            or frame_count != int(run["frame_count"])
            or miss_count != int(run["miss_count"])
        ):
            raise ValueError(f"source frame counts changed: seed {seed}")
        total_misses += miss_count
        source_files[str(seed)] = {
            "seed": seed,
            "run": run_number,
            "run_group_id": str(run["run_group_id"]),
            "run_id": run_id,
            "source_file_sha256": observed_checksums,
        }
    if len(outcomes) != int(population["frame_count"]):
        raise ValueError("primary-tail source outcome count changed")
    if total_misses != int(population["miss_count"]):
        raise ValueError("primary-tail source miss count changed")
    source_audit = {
        "join_key": ["seed", "run", "frame_id"],
        "run_count": len(selected),
        "frame_count": len(outcomes),
        "miss_count": total_misses,
        "runs_by_seed": source_files,
    }
    return outcomes, source_audit, identity_by_run_id


def _encode_column(name: str, values: list[Any]) -> np.ndarray:
    return np.asarray(
        [encode_value(name, "" if value is None else str(value)) for value in values],
        dtype=np.float32,
    )


def _load_target_rows(
    dataset_path: Path,
    predictor: FrozenPredictor,
    config: dict[str, Any],
    source_outcomes: dict[tuple[int, int, int], dict[str, Any]],
    identity_by_run_id: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    target = config["target"]
    physical = list(physical_feature_names(predictor))
    metadata = [
        "sample_stage",
        "path_id",
        "copy_id",
        "scenario_name",
        "selected_policy",
        "deadline_miss",
        "frame_complete",
        "frame_latency_us",
        "actionable",
        "run_group_id",
        "run_id",
        "frame_id",
        "generation_time_ns",
        "sample_time_ns",
        "frame_size_bytes",
        "frame_packet_count",
        "frame_packets_tx_succeeded",
        "frame_type",
    ]
    table = pq.read_table(
        dataset_path,
        columns=list(dict.fromkeys(metadata + physical)),
        filters=[
            ("sample_stage", "=", target["stage"]),
            ("path_id", "=", str(target["path_id"])),
            ("copy_id", "=", str(target["copy_id"])),
            ("scenario_name", "=", target["scenario_name"]),
            ("selected_policy", "=", target["selected_policy"]),
        ],
    )
    expected = config["expected_population"]
    if table.num_rows != int(expected["frame_count"]):
        raise ValueError("target T4 dataset row count differs from frozen population")
    values = table.to_pydict()
    matrix = np.empty((table.num_rows, len(predictor.feature_names)), dtype=np.float32)
    for index, (logical, recorded) in enumerate(
        zip(predictor.feature_names, physical, strict=True)
    ):
        matrix[:, index] = _encode_column(logical, values[recorded])
    run = np.asarray(values["run_id"], dtype=object)
    frame = np.asarray(values["frame_id"], dtype=np.int64)
    miss = np.asarray(values["deadline_miss"], dtype=np.int8)
    complete = np.asarray(values["frame_complete"], dtype=np.int8) == 1
    latency = np.asarray(
        [np.nan if item is None else float(item) for item in values["frame_latency_us"]],
        dtype=np.float64,
    )
    sizes = np.asarray(values["frame_size_bytes"], dtype=np.int64)
    packets = np.asarray(values["frame_packet_count"], dtype=np.int64)
    try:
        identities = [identity_by_run_id[str(run_id)] for run_id in run]
    except KeyError as error:
        raise ValueError(f"dataset run ID is absent from source manifest: {error}") from error
    seed = np.asarray([item[0] for item in identities], dtype=np.int64)
    run_number = np.asarray([item[1] for item in identities], dtype=np.int64)
    keys = list(zip(seed.tolist(), run_number.tolist(), frame.tolist(), strict=True))
    if len(set(keys)) != table.num_rows:
        raise ValueError("target T4 dataset contains duplicate frame rows")
    for position, key in enumerate(keys):
        outcome = source_outcomes.get(
            (int(key[0]), int(key[1]), int(key[2]))
        )
        if outcome is None:
            raise ValueError(f"dataset primary outcome is absent from source at row {position}")
        observed_latency = None if not complete[position] else int(latency[position])
        if (
            int(miss[position]) != outcome["miss"]
            or bool(complete[position]) != outcome["complete"]
            or observed_latency != outcome["latency_us"]
            or int(sizes[position]) != outcome["frame_size_bytes"]
            or int(packets[position]) != outcome["frame_packet_count"]
        ):
            raise ValueError(f"dataset primary outcome mismatch at row {position}")
    if np.any(complete & ~np.isfinite(latency)) or np.any(~complete & np.isfinite(latency)):
        raise ValueError("T4 completion and latency fields disagree")
    actionable = np.asarray(
        [str(item).lower() in {"1", "true"} for item in values["actionable"]],
        dtype=bool,
    )
    succeeded = np.asarray(values["frame_packets_tx_succeeded"], dtype=np.int64)
    payload = int(config["packetization"]["payload_bytes_per_packet"])
    if (
        np.any(packets <= 0)
        or np.any(succeeded < 0)
        or np.any(succeeded > packets)
        or np.any(sizes != packets * payload)
    ):
        raise ValueError("T4 frame descriptors do not match frozen packetization")
    if np.any(actionable & (succeeded >= packets)):
        raise ValueError("actionable T4 row has no primary packet deficit")
    threshold_us = int(target["heads"]["completed_tail"]["latency_threshold_us"])
    tail = (complete & (latency >= threshold_us)).astype(np.int8)
    return {
        "matrix": matrix,
        "primary_miss": miss,
        "completed_tail": tail,
        "complete": complete,
        "latency_us": latency,
        "actionable": actionable,
        "group": np.asarray(values["run_group_id"], dtype=object),
        "run": run,
        "seed": seed,
        "run_number": run_number,
        "frame": frame,
        "generation_time_ns": np.asarray(values["generation_time_ns"], dtype=np.int64),
        "sample_time_ns": np.asarray(values["sample_time_ns"], dtype=np.int64),
        "frame_size_bytes": sizes,
        "frame_packet_count": packets,
        "frame_packets_tx_succeeded": succeeded,
        "frame_type": np.asarray(values["frame_type"], dtype=object),
        "physical_feature_names": physical,
    }


def _seed_folds(seeds: np.ndarray, folds_by_seed: list[list[int]]) -> np.ndarray:
    """Map rows to the explicitly frozen engineering fold for their seed."""
    mapping = {
        int(seed): fold
        for fold, members in enumerate(folds_by_seed)
        for seed in members
    }
    try:
        return np.asarray([mapping[int(seed)] for seed in seeds], dtype=np.int8)
    except KeyError as error:
        raise ValueError(f"dataset seed is absent from frozen folds: {error}") from error


def _categorical_indices(predictor: FrozenPredictor) -> tuple[int, ...]:
    return tuple(
        index
        for index, name in enumerate(predictor.feature_names)
        if name.removeprefix("polling_1ms_") in CATEGORICAL_VOCABULARIES
    )


def _fit_head(
    data: dict[str, Any],
    label_name: str,
    training: np.ndarray,
    calibration: np.ndarray,
    base_predictor: FrozenPredictor,
    candidate: Any,
    categorical: tuple[int, ...],
    config: dict[str, Any],
) -> tuple[FrozenPredictor, np.ndarray]:
    label = data[label_name]
    if len(np.unique(label[training])) != 2 or len(np.unique(label[calibration])) != 2:
        raise ValueError(f"{label_name} fitting partition lacks one outcome class")
    seed = int(config["model"]["analysis_seed"])
    fitted = fit_pipeline(
        candidate,
        data["matrix"][training],
        label[training],
        categorical,
        seed,
    )
    calibration_score = ranking_score(fitted, data["matrix"][calibration])
    calibrator = fit_platt(
        calibration_score,
        label[calibration],
        int(config["calibration"]["seed"]),
    )
    probability = calibrator.predict(calibration_score)
    predictor = FrozenPredictor(
        pipeline_id=config["base_bundle"]["pipeline_id"],
        feature_set=base_predictor.feature_set,
        evidence_role="engineering_meta_selected",
        stage="T4",
        feature_names=base_predictor.feature_names,
        f1_feature_names=base_predictor.f1_feature_names,
        degradation_profile=copy.deepcopy(base_predictor.degradation_profile),
        model_name=candidate.name,
        selection_recall=0.0,
        pipeline=fitted,
        calibrator=calibrator,
    )
    return predictor, probability


def combine_probabilities(
    miss_probability: np.ndarray,
    tail_probability: np.ndarray,
    hybrid: dict[str, Any],
) -> np.ndarray:
    """Combine the two frozen calibrated heads."""
    miss = np.asarray(miss_probability, dtype=np.float64)
    tail = np.asarray(tail_probability, dtype=np.float64)
    if miss.shape != tail.shape:
        raise ValueError("primary-tail probability heads have different shapes")
    return (
        float(hybrid["primary_miss_weight"]) * miss
        + float(hybrid["completed_tail_weight"]) * tail
    ) / float(hybrid["normalization"])


def _fit_two_heads(
    data: dict[str, Any],
    ranker_training: np.ndarray,
    calibration_partition: np.ndarray,
    base_predictor: FrozenPredictor,
    candidate: Any,
    categorical: tuple[int, ...],
    config: dict[str, Any],
) -> tuple[dict[str, FrozenPredictor], np.ndarray]:
    actionable_training = ranker_training & data["actionable"]
    actionable_calibration = calibration_partition & data["actionable"]
    miss, miss_probability = _fit_head(
        data,
        "primary_miss",
        actionable_training,
        actionable_calibration,
        base_predictor,
        candidate,
        categorical,
        config,
    )
    completed_training = actionable_training & data["complete"]
    completed_calibration = actionable_calibration & data["complete"]
    tail, _ = _fit_head(
        data,
        "completed_tail",
        completed_training,
        completed_calibration,
        base_predictor,
        candidate,
        categorical,
        config,
    )
    tail_probability = tail.predict(data["matrix"][actionable_calibration])[1]
    combined = combine_probabilities(
        miss_probability, tail_probability, config["hybrid"]
    )
    return {"primary_miss": miss, "completed_tail": tail}, combined


def _calibrator_parameters(predictor: FrozenPredictor) -> tuple[float, float]:
    model = predictor.calibrator.model
    return float(model.coef_[0, 0]), float(model.intercept_[0])


def _verify_expected_artifact(
    config: dict[str, Any],
    heads: dict[str, FrozenPredictor],
    calibration_positions: np.ndarray,
) -> None:
    expected = config["expected_artifact"]["calibration_model"]
    observed = observed_artifact_pins(heads, calibration_positions)
    if observed["calibration_actionable_rows"] != int(
        expected["calibration_actionable_rows"]
    ):
        raise ValueError("calibration actionable-row count differs from frozen artifact")
    for name, prefix in (("primary_miss", "miss"), ("completed_tail", "tail")):
        if observed[f"{prefix}_ranker_sha256"] != expected[f"{prefix}_ranker_sha256"]:
            raise ValueError(f"{name} ranker differs from frozen artifact")
        if not np.isclose(
            observed[f"{prefix}_platt_coefficient"],
            float(expected[f"{prefix}_platt_coefficient"]),
            rtol=0,
            atol=1e-12,
        ) or not np.isclose(
            observed[f"{prefix}_platt_intercept"],
            float(expected[f"{prefix}_platt_intercept"]),
            rtol=0,
            atol=1e-12,
        ):
            raise ValueError(f"{name} calibrator differs from frozen artifact")


def observed_artifact_pins(
    heads: dict[str, FrozenPredictor],
    calibration_positions: np.ndarray,
) -> dict[str, Any]:
    """Return the deterministic values that a curated source config must pin."""
    result: dict[str, Any] = {
        "calibration_actionable_rows": len(calibration_positions),
    }
    for name, prefix in (("primary_miss", "miss"), ("completed_tail", "tail")):
        coefficient, intercept = _calibrator_parameters(heads[name])
        result[f"{prefix}_ranker_sha256"] = predictor_fingerprint(
            heads[name].pipeline
        )
        result[f"{prefix}_platt_coefficient"] = coefficient
        result[f"{prefix}_platt_intercept"] = intercept
    return result


def _head_probability_diagnostics(
    data: dict[str, Any],
    partition: np.ndarray,
    heads: dict[str, FrozenPredictor],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate each calibrated head on its declared population."""
    populations = {
        "primary_miss": partition & data["actionable"],
        "completed_tail": partition & data["actionable"] & data["complete"],
    }
    result = {}
    for name in HEAD_NAMES:
        mask = populations[name]
        probability = heads[name].predict(data["matrix"][mask])[1]
        if not np.all(np.isfinite(probability)):
            raise ValueError(f"primary-tail {name} produced a nonfinite probability")
        result[name] = {
            "fit_population": config["target"]["heads"][name]["fit_population"],
            "row_count": int(mask.sum()),
            "positive_count": int(data[name][mask].sum()),
            **probability_metrics(
                data[name][mask],
                probability,
                int(config["calibration"]["bin_count"]),
            ),
        }
    return result


def _engineering_oof_model_diagnostics(
    data: dict[str, Any],
    folds: np.ndarray,
    base_predictor: FrozenPredictor,
    candidate: Any,
    categorical: tuple[int, ...],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Run post-selection OOF diagnostics for the two heads only."""
    fold_count = int(config["split"]["fold_count"])
    oof_probability = {
        name: np.full(len(folds), np.nan, dtype=np.float64) for name in HEAD_NAMES
    }
    reports = []
    for test_fold in range(fold_count):
        calibration_fold = (test_fold + 1) % fold_count
        test = folds == test_fold
        calibration = folds == calibration_fold
        training = ~(test | calibration)
        heads, _ = _fit_two_heads(
            data,
            training,
            calibration,
            base_predictor,
            candidate,
            categorical,
            config,
        )
        for name, population in (
            ("primary_miss", test & data["actionable"]),
            ("completed_tail", test & data["actionable"] & data["complete"]),
        ):
            oof_probability[name][population] = heads[name].predict(
                data["matrix"][population]
            )[1]
        reports.append(
            {
                "test_fold": test_fold,
                "calibration_fold": calibration_fold,
                "ranker_training_folds": sorted(
                    set(range(fold_count)) - {test_fold, calibration_fold}
                ),
                "heads": _head_probability_diagnostics(data, test, heads, config),
            }
        )
    aggregate = {}
    for name, population in (
        ("primary_miss", data["actionable"]),
        ("completed_tail", data["actionable"] & data["complete"]),
    ):
        probability = oof_probability[name][population]
        if not np.all(np.isfinite(probability)):
            raise AssertionError(f"primary-tail {name} OOF coverage is incomplete")
        aggregate[name] = {
            "fit_population": config["target"]["heads"][name]["fit_population"],
            "row_count": int(population.sum()),
            "positive_count": int(data[name][population].sum()),
            **probability_metrics(
                data[name][population],
                probability,
                int(config["calibration"]["bin_count"]),
            ),
        }
    return {
        "evidence_status": "post_selection_engineering_diagnostic",
        "independent_ood_claim": False,
        "application": "seed_grouped_fit_calibrate_then_test",
        "aggregate_heads": aggregate,
        "folds": reports,
    }


def train(args: argparse.Namespace) -> dict[str, Any]:
    """Train and atomically publish the action-neutral engineering model."""
    config_path = args.config.resolve()
    config = load_config(config_path)
    config_digest = sha256_file(config_path)
    dataset_dir = args.dataset_dir.resolve()
    dataset_manifest_path = dataset_dir / "dataset_manifest.json"
    dataset_validation_path = dataset_dir / "dataset_validation.json"
    dataset_manifest = _json(dataset_manifest_path)
    dataset_validation = _json(dataset_validation_path)
    dataset_path = dataset_dir / dataset_manifest["dataset_file"]
    frozen_dataset = config["dataset"]
    if sha256_file(dataset_manifest_path) != frozen_dataset["manifest_sha256"]:
        raise ValueError("dataset manifest differs from frozen primary-tail config")
    if sha256_file(dataset_validation_path) != frozen_dataset["validation_sha256"]:
        raise ValueError("dataset validation differs from frozen primary-tail config")
    if (
        dataset_manifest.get("validation_status")
        != frozen_dataset["validation_status"]
        or dataset_manifest.get("split_sufficiency_status")
        != frozen_dataset["split_sufficiency_status"]
        or dataset_validation.get("status") != frozen_dataset["validation_status"]
        or dataset_validation.get("split_sufficiency_status")
        != frozen_dataset["split_sufficiency_status"]
        or not dataset_validation.get("checks")
        or any(status != "PASS" for status in dataset_validation["checks"].values())
    ):
        raise ValueError("dataset validation semantics are not frozen PASS checks")
    if (
        dataset_manifest.get("dataset_schema_version") != frozen_dataset["schema_version"]
        or dataset_manifest.get("dataset_sha256") != frozen_dataset["sha256"]
        or sha256_file(dataset_path) != frozen_dataset["sha256"]
    ):
        raise ValueError("dataset differs from frozen primary-tail config")
    for key in (
        "project_git_commits",
        "ns3_upstream_commits",
        "telemetry_schema_versions",
        "support_mask_versions",
    ):
        if dataset_manifest.get(key) != frozen_dataset[key]:
            raise ValueError(f"dataset {key} differs from frozen primary-tail config")

    base_dir = args.base_bundle_dir.resolve()
    base_manifest = _json(base_dir / "model_bundle_manifest.json")
    base_model_path = base_dir / base_manifest["model_file"]
    base_digest = sha256_file(base_model_path)
    if (
        base_digest != config["base_bundle"]["sha256"]
        or base_manifest.get("model_sha256") != base_digest
    ):
        raise ValueError("base model bundle differs from frozen primary-tail config")
    base_bundle = read_model_bundle(base_model_path)
    predictor_key = (
        config["base_bundle"]["pipeline_id"],
        config["base_bundle"]["stage"],
    )
    base_predictor = base_bundle.predictors.get(predictor_key)
    if base_predictor is None:
        raise ValueError("base bundle lacks the frozen exportable T4 predictor")
    if (
        base_predictor.feature_set != config["base_bundle"]["feature_set"]
        or len(base_predictor.feature_names)
        != int(config["base_bundle"]["expected_feature_count"])
        or base_predictor.degradation_profile is None
        or base_predictor.degradation_profile.get("profile_id")
        != config["base_bundle"]["degradation_profile"]
    ):
        raise ValueError("base T4 predictor feature contract changed")

    source_outcomes, source_audit, identity_by_run_id = _audit_source_outcomes(
        dataset_manifest, config
    )
    data = _load_target_rows(
        dataset_path,
        base_predictor,
        config,
        source_outcomes,
        identity_by_run_id,
    )
    split = config["split"]
    training_seeds = set(map(int, split["deployment_training_seeds"]))
    calibration_seeds = set(map(int, split["calibration_seeds"]))
    observed_seeds = set(map(int, data["seed"]))
    if observed_seeds != training_seeds | calibration_seeds:
        raise ValueError("dataset seeds differ from frozen primary-tail split")
    if len(set(map(str, data["group"]))) != int(
        config["expected_population"]["run_group_count"]
    ):
        raise ValueError("dataset run-group count differs from frozen population")
    training = np.asarray(
        [int(seed) in training_seeds for seed in data["seed"]], dtype=bool
    )
    calibration = np.asarray(
        [int(seed) in calibration_seeds for seed in data["seed"]], dtype=bool
    )
    if np.any(training & calibration) or not np.all(training | calibration):
        raise AssertionError("primary-tail split masks are inconsistent")
    folds = _seed_folds(data["seed"], split["engineering_folds_by_seed"])
    if set(map(int, data["seed"][folds == 1])) != calibration_seeds:
        raise ValueError("explicit calibration seeds differ from frozen fold 1")

    candidate = next(
        item for item in CANDIDATES if item.name == config["model"]["name"]
    )
    if candidate.parameters != config["model"]["parameters"]:
        raise ValueError("frozen HGB parameters differ from implementation")
    categorical = _categorical_indices(base_predictor)
    heads, _ = _fit_two_heads(
        data,
        training,
        calibration,
        base_predictor,
        candidate,
        categorical,
        config,
    )
    calibration_positions = np.flatnonzero(calibration & data["actionable"])
    if args.observed_pins_only:
        return {
            "status": "unpinned_inspection_only_no_artifact_published",
            "expected_artifact": {
                "calibration_model": observed_artifact_pins(
                    heads, calibration_positions
                )
            },
            "source_population": {
                "run_group_count": len(set(map(str, data["group"]))),
                "run_count": len(set(map(str, data["run"]))),
                "frame_count": len(data["frame"]),
                "miss_count": int(data["primary_miss"].sum()),
                "frames_per_run": int(
                    len(data["frame"]) / len(set(map(str, data["run"])))
                ),
                "measurement_duration_s": int(
                    config["expected_population"]["measurement_duration_s"]
                ),
            },
        }
    _verify_expected_artifact(config, heads, calibration_positions)

    target_provenance = {
        "source_column": config["target"]["source_column"],
        "source_equivalence": config["target"]["source_equivalence"],
        "treatment_free": True,
        "scenario_name": config["target"]["scenario_name"],
        "selected_policy": config["target"]["selected_policy"],
        "path_id": config["target"]["path_id"],
        "copy_id": config["target"]["copy_id"],
        "stage": "T4",
        "feature_set": base_predictor.feature_set,
        "degradation_profile": config["target"]["degradation_profile"],
        "heads": copy.deepcopy(config["target"]["heads"]),
        "hybrid": copy.deepcopy(config["hybrid"]),
        "split_status": split["status"],
        "independent_evaluation": split["independent_evaluation"],
        "dataset_sha256": frozen_dataset["sha256"],
        "dataset_manifest_sha256": frozen_dataset["manifest_sha256"],
        "dataset_validation_sha256": frozen_dataset["validation_sha256"],
        "training_config_sha256": config_digest,
    }
    bundle = PrimaryTailT4Bundle(
        schema_version=PRIMARY_TAIL_BUNDLE_SCHEMA_VERSION,
        artifact_id=config["artifact_id"],
        model_id=config["model_id"],
        dataset_sha256=frozen_dataset["sha256"],
        dataset_manifest_sha256=frozen_dataset["manifest_sha256"],
        dataset_validation_sha256=frozen_dataset["validation_sha256"],
        training_config_sha256=config_digest,
        primary_link=1,
        pipeline_id=predictor_key[0],
        stage="T4",
        heads=heads,
        target_ids={
            name: config["target"]["heads"][name]["target_id"] for name in HEAD_NAMES
        },
        tail_threshold_us=int(
            config["target"]["heads"]["completed_tail"]["latency_threshold_us"]
        ),
        miss_weight=float(config["hybrid"]["primary_miss_weight"]),
        tail_weight=float(config["hybrid"]["completed_tail_weight"]),
        score_normalization=float(config["hybrid"]["normalization"]),
        score_name=config["hybrid"]["output_name"],
        score_kind=config["hybrid"]["score_kind"],
        combiner=config["hybrid"]["combiner"],
        evidence_status=split["status"],
    )

    group_entries = []
    for seed in sorted(observed_seeds):
        mask = data["seed"] == seed
        groups = sorted(set(map(str, data["group"][mask])))
        if len(groups) != 1:
            raise ValueError(f"seed {seed} maps to multiple run groups")
        group_entries.append(
            {
                "seed": seed,
                "run": int(np.unique(data["run_number"][mask])[0]),
                "run_group_id": groups[0],
                "run_ids": sorted(set(map(str, data["run"][mask]))),
                "hash_fold": int(np.unique(folds[mask])[0]),
                "model_role": (
                    "ranker_training" if seed in training_seeds else "calibration"
                ),
                "frame_count": int(mask.sum()),
                "primary_miss_count": int(data["primary_miss"][mask].sum()),
                "completed_tail_count": int(data["completed_tail"][mask].sum()),
            }
        )
    split_manifest = {
        "primary_tail_split_schema_version": 2,
        "status": split["status"],
        "independent_evaluation": split["independent_evaluation"],
        "assignment_uses_labels": False,
        "ranker_training_seed_count": len(training_seeds),
        "calibration_seed_count": len(calibration_seeds),
        "groups": group_entries,
    }
    diagnostics = {
        "primary_tail_model_diagnostics_schema_version": 2,
        "artifact_scope": "action_neutral_two_head_model",
        "evidence_status": "post_selection_engineering_diagnostic",
        "independent_ood_claim": False,
        "future_test": "fresh_seed_closed_loop_simulation_only",
        "calibration_heads": _head_probability_diagnostics(
            data, calibration, heads, config
        ),
        "out_of_fold": _engineering_oof_model_diagnostics(
            data, folds, base_predictor, candidate, categorical, config
        ),
        "limitations": [
            "All 24 historical groups were consumed during engineering meta-selection.",
            "The model artifact contains no action, cost, gate, or airtime-budget policy.",
            "No offline selected-frame outcome is a closed-loop MLO comparison.",
        ],
    }

    output_dir = args.output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.building-", dir=output_dir.parent)
    )
    backup = output_dir.with_name(f".{output_dir.name}.previous")
    try:
        bundle_path = staging / "primary_tail_t4_bundle.pkl"
        split_path = staging / "group_split.json"
        diagnostics_path = staging / "model_diagnostics.json"
        write_primary_tail_bundle(bundle_path, bundle)
        published = read_primary_tail_bundle(bundle_path)
        if published.model_id != config["model_id"]:
            raise AssertionError("published primary-tail artifact identity changed")
        _write_json(split_path, split_manifest)
        _write_json(diagnostics_path, diagnostics)
        head_manifest = {}
        for name in HEAD_NAMES:
            trained_head = heads[name]
            published_head = published.heads[name]
            coefficient, intercept = _calibrator_parameters(published_head)
            head_manifest[name] = {
                "target_id": config["target"]["heads"][name]["target_id"],
                "fit_population": config["target"]["heads"][name]["fit_population"],
                "fitted_ranker_sha256": predictor_fingerprint(trained_head.pipeline),
                "serialized_ranker_sha256": predictor_fingerprint(
                    published_head.pipeline
                ),
                "serialized_frozen_predictor_sha256": predictor_fingerprint(
                    published_head
                ),
                "platt_coefficient": coefficient,
                "platt_intercept": intercept,
                "feature_count": len(published_head.feature_names),
                "feature_names": list(published_head.feature_names),
                "physical_feature_names": list(
                    physical_feature_names(published_head)
                ),
            }
        manifest = {
            "primary_tail_bundle_schema_version": PRIMARY_TAIL_BUNDLE_SCHEMA_VERSION,
            "artifact_scope": "action_neutral_two_head_model",
            "operating_profile_included": False,
            "artifact_id": config["artifact_id"],
            "model_id": config["model_id"],
            "model_file": bundle_path.name,
            "model_sha256": sha256_file(bundle_path),
            "training_tool": "tools/train_primary_tail_t4.py",
            "training_tool_sha256": sha256_file(Path(__file__).resolve()),
            "training_config": "experiments/configs/primary_tail_t4_obss_v1.yaml",
            "training_config_sha256": config_digest,
            "dataset_file": dataset_manifest["dataset_file"],
            "dataset_schema_version": dataset_manifest["dataset_schema_version"],
            "dataset_sha256": frozen_dataset["sha256"],
            "dataset_manifest_sha256": frozen_dataset["manifest_sha256"],
            "dataset_validation_sha256": frozen_dataset["validation_sha256"],
            "base_model_sha256": base_digest,
            "base_predictor_key": {
                "pipeline_id": predictor_key[0],
                "stage": predictor_key[1],
            },
            "target_provenance": target_provenance,
            "target_provenance_sha256": provenance_sha256(target_provenance),
            "evidence_status": split["status"],
            "independent_evaluation": split["independent_evaluation"],
            "heads": head_manifest,
            "combiner": {
                "output_name": bundle.score_name,
                "score_kind": bundle.score_kind,
                "combiner": bundle.combiner,
                "formula": config["hybrid"]["formula"],
                "primary_miss_weight": bundle.miss_weight,
                "completed_tail_weight": bundle.tail_weight,
                "normalization": bundle.score_normalization,
            },
            "group_split_file": split_path.name,
            "group_split_sha256": sha256_file(split_path),
            "model_diagnostics_file": diagnostics_path.name,
            "model_diagnostics_sha256": sha256_file(diagnostics_path),
            "source_audit": source_audit,
            "dependencies": {
                "python": platform.python_version(),
                "numpy": importlib.metadata.version("numpy"),
                "scipy": importlib.metadata.version("scipy"),
                "scikit_learn": importlib.metadata.version("scikit-learn"),
                "joblib": importlib.metadata.version("joblib"),
                "pyarrow": importlib.metadata.version("pyarrow"),
                "pyyaml": importlib.metadata.version("PyYAML"),
            },
            "determinism": {
                "timestamps_recorded": False,
                "runtime_recorded": False,
                "absolute_invocation_paths_recorded": False,
                "pickle_protocol": "highest",
            },
        }
        manifest_path = staging / "primary_tail_t4_manifest.json"
        _write_json(manifest_path, manifest)
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
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", type=Path)
    parser.add_argument("--base-bundle-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--observed-pins-only",
        action="store_true",
        help="print candidate frozen outputs without publishing an artifact",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        result = train(args)
    except (OSError, ValueError) as error:
        raise SystemExit(f"error: {error}") from error
    print(canonical_json(result))


if __name__ == "__main__":
    main()
