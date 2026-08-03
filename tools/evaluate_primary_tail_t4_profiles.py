#!/usr/bin/env python3
"""Evaluate named T4 operating profiles for the neutral primary-tail model."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
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

from prediction.features import encode_value
from prediction.online_replay import FrozenPredictor, read_model_bundle
from generate_primary_tail_t4_cpp_v1 import (
    evaluate_exported_head,
    validate_artifacts,
)
from train_primary_risk_t0 import (
    canonical_json,
    estimate_whole_copy_airtime_us,
    is_sha256,
    physical_feature_names,
    select_risk_density_threshold,
    sha256_file,
)
from train_primary_tail_t4 import (
    _audit_source_outcomes,
    _load_target_rows,
    load_config as load_model_config,
)

PROFILE_SCHEMA_VERSION = 1
REQUIRED_CONFIG_KEYS = {
    "primary_tail_t4_operating_profiles_schema_version",
    "analysis_id",
    "evidence_status",
    "independent_ood_claim",
    "model_artifact",
    "dataset",
    "t0_dependency",
    "airtime_estimator",
    "threshold_selection",
    "profiles",
    "expected_diagnostics",
    "limitations",
}
RUNTIME_EXPORT_KEYS = {
    "export_sha256",
    "export_manifest_sha256",
    "target_provenance_sha256",
    "feature_contract_sha256",
    "combiner_sha256",
    "primary_miss_model_sha256",
    "completed_tail_model_sha256",
}
FULL_COPY_PROFILE = "full_copy_primary"
DEFICIT_PROFILE = "primary_deficit_iso_ablation"


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return value


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as output:
        output.write(_canonical_bytes(value))
        temporary = Path(output.name)
    os.replace(temporary, path)


def load_profile_config(path: Path) -> dict[str, Any]:
    """Load and strictly validate the profile-only configuration."""
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("primary-tail profile YAML root must be a mapping")
    missing = sorted(REQUIRED_CONFIG_KEYS - value.keys())
    unknown = sorted(value.keys() - REQUIRED_CONFIG_KEYS)
    if missing:
        raise ValueError(f"primary-tail profile config is missing: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"primary-tail profile config has unknown keys: {', '.join(unknown)}")
    if value["primary_tail_t4_operating_profiles_schema_version"] != PROFILE_SCHEMA_VERSION:
        raise ValueError("unsupported primary-tail operating-profile schema")
    if (
        value["evidence_status"] != "post_selection_engineering_sensitivity"
        or value["independent_ood_claim"] is not False
    ):
        raise ValueError("primary-tail profile evidence status changed")
    model = value["model_artifact"]
    if (
        set(model)
        != {
            "model_id",
            "bundle_schema_version",
            "artifact_scope",
            "bundle_sha256",
            "manifest_sha256",
            "target_provenance_sha256",
            "training_config_sha256",
            "runtime_export",
        }
        or model.get("model_id") != "primary_tail_t4_obss_v1"
        or not is_sha256(model.get("bundle_sha256"))
        or not is_sha256(model.get("manifest_sha256"))
        or not is_sha256(model.get("target_provenance_sha256"))
        or not is_sha256(model.get("training_config_sha256"))
        or int(model.get("bundle_schema_version", 0)) != 2
        or model.get("artifact_scope") != "action_neutral_two_head_model"
    ):
        raise ValueError("neutral primary-tail model identity changed")
    runtime_export = model["runtime_export"]
    if set(runtime_export) != RUNTIME_EXPORT_KEYS or any(
        not is_sha256(runtime_export.get(name)) for name in RUNTIME_EXPORT_KEYS
    ):
        raise ValueError("primary-tail runtime export identity changed")
    dataset = value["dataset"]
    if set(dataset) != {"sha256", "manifest_sha256", "validation_sha256"} or any(
        not is_sha256(dataset.get(name))
        for name in ("sha256", "manifest_sha256", "validation_sha256")
    ):
        raise ValueError("profile dataset identity is not frozen")
    t0 = value["t0_dependency"]
    equivalence = t0.get("corrected_source_equivalence", {})
    if (
        set(t0)
        != {
            "model_id",
            "bundle_sha256",
            "manifest_sha256",
            "target_provenance_sha256",
            "pipeline_id",
            "stage",
            "expected_feature_count",
            "eligible_frame_type",
            "risk_density_threshold",
            "threshold_comparator",
            "cost_mode",
            "corrected_source_equivalence",
        }
        or set(equivalence)
        != {
            "reference_dataset_sha256",
            "reference_dataset_manifest_sha256",
            "join_key",
            "required_stages",
            "required_relation",
            "expected_stage_audit",
        }
        or t0.get("model_id") != "commodity_polling_1ms_obss_primary_t0_v1"
        or not is_sha256(t0.get("bundle_sha256"))
        or not is_sha256(t0.get("manifest_sha256"))
        or not is_sha256(t0.get("target_provenance_sha256"))
        or t0.get("pipeline_id") != "commodity_polling_1ms"
        or t0.get("stage") != "T0"
        or int(t0.get("expected_feature_count", 0)) != 86
        or t0.get("eligible_frame_type") != "I_FRAME"
        or float(t0.get("risk_density_threshold", -1)) != 0.034
        or t0.get("threshold_comparator") != "strict_greater"
        or t0.get("cost_mode") != "whole_secondary_copy"
        or not is_sha256(equivalence.get("reference_dataset_sha256"))
        or not is_sha256(equivalence.get("reference_dataset_manifest_sha256"))
        or equivalence.get("join_key") != ["seed", "run", "frame_id"]
        or equivalence.get("required_stages") != ["T0", "T4"]
        or equivalence.get("required_relation")
        != "exact_outcomes_and_physical_features"
    ):
        raise ValueError("profile T0 dependency contract changed")
    estimator = value["airtime_estimator"]
    required_estimator = {
        "cost_safety_factor",
        "retry_inflation",
        "phy_preamble_us",
        "phy_data_rate_bps",
        "streaming_header_bytes",
        "expected_mac_service_overhead_bytes",
        "additional_airtime_bytes_per_packet",
        "payload_bytes_per_packet",
        "reference_frame_size_bytes",
        "reference_packet_count",
        "expected_reference_airtime_us",
    }
    if (
        set(estimator) != required_estimator
        or int(estimator.get("payload_bytes_per_packet", 0)) != 1200
    ):
        raise ValueError("profile airtime estimator contract changed")
    selection = value["threshold_selection"]
    if (
        set(selection)
        != {
            "partition",
            "calibration_seeds",
            "uses_labels",
            "threshold_comparator",
            "joint_t0_cost_accounting",
            "representation_stabilization",
        }
        or selection.get("partition") != "calibration_seeds"
        or selection.get("uses_labels") is not False
        or selection.get("threshold_comparator") != "strict_greater"
        or selection.get("joint_t0_cost_accounting")
        != "subtract_raw_t0_i_gate_cost_before_residual_t4_selection"
        or selection.get("representation_stabilization")
        != "label_free_all_engineering_nearest_density_midpoint_preserving_preselected_strict_mask"
        or selection.get("calibration_seeds") != [401, 404, 409, 418, 419, 422]
    ):
        raise ValueError("profile threshold-selection contract changed")
    profiles = value["profiles"]
    if set(profiles) != {FULL_COPY_PROFILE, DEFICIT_PROFILE}:
        raise ValueError("primary-tail named profile set changed")
    full = profiles[FULL_COPY_PROFILE]
    budgets = list(map(float, full.get("absolute_nominal_budget_fractions", [])))
    if (
        set(full)
        != {
            "role",
            "t4_action_mode",
            "admission_cost_mode",
            "absolute_nominal_budget_fractions",
            "candidate_designations",
            "token_bucket_fraction",
            "bucket_horizon_us",
            "initial_bucket_horizon_us",
            "runtime_bucket_accounting",
            "offline_replay_accounting",
        }
        or full.get("role") != "primary_closed_loop_candidate_family"
        or full.get("t4_action_mode") != "whole_secondary_copy"
        or full.get("admission_cost_mode") != "whole_secondary_copy"
        or budgets != [0.0055, 0.008, 0.01, 0.012]
        or full.get("candidate_designations")
        != {"balanced": 0.008, "strong_tail": 0.01}
        or float(full.get("token_bucket_fraction", -1)) != 0.02
    ):
        raise ValueError("full-copy primary profile changed")
    deficit = profiles[DEFICIT_PROFILE]
    if (
        set(deficit)
        != {
            "role",
            "t4_action_mode",
            "admission_cost_mode",
            "decision_source",
            "token_bucket_fraction",
            "bucket_horizon_us",
            "initial_bucket_horizon_us",
            "runtime_bucket_accounting",
            "offline_replay_accounting",
        }
        or deficit.get("role") != "mechanism_iso_ablation_not_deployment"
        or deficit.get("t4_action_mode")
        != "reverse_primary_unacknowledged_packets"
        or deficit.get("admission_cost_mode") != "whole_secondary_copy"
        or deficit.get("decision_source") != "full_copy_primary_same_budget"
        or float(deficit.get("token_bucket_fraction", -1)) != 0.02
    ):
        raise ValueError("primary-deficit ablation changed")
    for profile in profiles.values():
        if (
            int(profile.get("bucket_horizon_us", 0)) != 10_000_000
            or int(profile.get("initial_bucket_horizon_us", 0)) != 2_000_000
            or profile.get("runtime_bucket_accounting")
            != "measured_secondary_sender_phy_airtime"
            or profile.get("offline_replay_accounting")
            != "whole_copy_admission_price_debited_immediately"
        ):
            raise ValueError("profile token-bucket shape changed")
    return value


def estimate_primary_deficit_airtime_us(
    frame_size_bytes: np.ndarray,
    packet_count: np.ndarray,
    packets_tx_succeeded: np.ndarray,
    estimator: dict[str, Any],
) -> np.ndarray:
    """Estimate nominal reverse-deficit airtime from synchronous T4 state."""
    sizes = np.asarray(frame_size_bytes, dtype=np.int64)
    packets = np.asarray(packet_count, dtype=np.int64)
    succeeded = np.asarray(packets_tx_succeeded, dtype=np.int64)
    payload = int(estimator["payload_bytes_per_packet"])
    if (
        np.any(packets <= 0)
        or np.any(succeeded < 0)
        or np.any(succeeded > packets)
        or np.any(sizes != packets * payload)
    ):
        raise ValueError("T4 frame descriptors do not match frozen packetization")
    deficit = packets - succeeded
    result = np.zeros(len(deficit), dtype=np.float64)
    positive = deficit > 0
    if np.any(positive):
        result[positive] = estimate_whole_copy_airtime_us(
            payload * deficit[positive], deficit[positive], estimator
        )
    return result


def _manifest_identities(
    manifest: dict[str, Any], target: dict[str, Any]
) -> dict[str, tuple[int, int]]:
    result = {}
    for run in manifest["included_runs"]:
        if (
            run["scenario_name"] == target["scenario_name"]
            and run["selected_policy"] == target["selected_policy"]
            and int(run["selected_path"]) == int(target["path_id"])
        ):
            result[str(run["run_id"])] = (int(run["seed"]), int(run["run"]))
    return result


def _contract_table(
    dataset_path: Path,
    manifest: dict[str, Any],
    predictor: FrozenPredictor,
    stage: str,
    target: dict[str, Any],
) -> tuple[dict[str, list[Any]], dict[tuple[int, int, int], int], list[str]]:
    physical = list(physical_feature_names(predictor))
    metadata = [
        "run_id",
        "frame_id",
        "deadline_miss",
        "frame_complete",
        "frame_latency_us",
    ]
    table = pq.read_table(
        dataset_path,
        columns=list(dict.fromkeys(metadata + physical)),
        filters=[
            ("sample_stage", "=", stage),
            ("path_id", "=", str(target["path_id"])),
            ("copy_id", "=", str(target["copy_id"])),
            ("scenario_name", "=", target["scenario_name"]),
            ("selected_policy", "=", target["selected_policy"]),
        ],
    )
    values = table.to_pydict()
    identities = _manifest_identities(manifest, target)
    lookup = {}
    for position, (run_id, frame_id) in enumerate(
        zip(values["run_id"], values["frame_id"], strict=True)
    ):
        if str(run_id) not in identities:
            raise ValueError(f"equivalence run ID is absent from its manifest: {run_id}")
        seed, run = identities[str(run_id)]
        key = (seed, run, int(frame_id))
        if key in lookup:
            raise ValueError(f"equivalence dataset contains duplicate key: {key}")
        lookup[key] = position
    return values, lookup, physical


def _canonical_scalar(value: Any) -> Any:
    if isinstance(value, float) and not np.isfinite(value):
        return "NaN" if np.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
    return value


def audit_corrected_source_equivalence(
    corrected_dataset_path: Path,
    corrected_manifest: dict[str, Any],
    reference_dataset_dir: Path,
    t0_predictor: FrozenPredictor,
    t4_predictor: FrozenPredictor,
    model_config: dict[str, Any],
    profile_config: dict[str, Any],
) -> dict[str, Any]:
    """Prove corrected-source equivalence for the reused T0 model."""
    frozen = profile_config["t0_dependency"]["corrected_source_equivalence"]
    reference_manifest_path = reference_dataset_dir / "dataset_manifest.json"
    reference_manifest = _json(reference_manifest_path)
    reference_dataset_path = reference_dataset_dir / reference_manifest["dataset_file"]
    if (
        sha256_file(reference_manifest_path)
        != frozen["reference_dataset_manifest_sha256"]
        or reference_manifest.get("dataset_sha256")
        != frozen["reference_dataset_sha256"]
        or sha256_file(reference_dataset_path) != frozen["reference_dataset_sha256"]
    ):
        raise ValueError("T0 reference dataset differs from frozen equivalence source")
    result = {
        "relation": frozen["required_relation"],
        "join_key": frozen["join_key"],
        "reference_dataset_sha256": frozen["reference_dataset_sha256"],
        "corrected_dataset_sha256": profile_config["dataset"]["sha256"],
        "stages": {},
    }
    predictors = {"T0": t0_predictor, "T4": t4_predictor}
    for stage in frozen["required_stages"]:
        corrected, corrected_lookup, corrected_physical = _contract_table(
            corrected_dataset_path,
            corrected_manifest,
            predictors[stage],
            stage,
            model_config["target"],
        )
        reference, reference_lookup, reference_physical = _contract_table(
            reference_dataset_path,
            reference_manifest,
            predictors[stage],
            stage,
            model_config["target"],
        )
        if corrected_physical != reference_physical or set(corrected_lookup) != set(
            reference_lookup
        ):
            raise ValueError(f"corrected/reference {stage} contract keys differ")
        ordered_keys = sorted(corrected_lookup)
        digest = hashlib.sha256()
        compared_columns = [
            "deadline_miss",
            "frame_complete",
            "frame_latency_us",
            *corrected_physical,
        ]
        for column in compared_columns:
            corrected_values = [
                _canonical_scalar(corrected[column][corrected_lookup[key]])
                for key in ordered_keys
            ]
            reference_values = [
                _canonical_scalar(reference[column][reference_lookup[key]])
                for key in ordered_keys
            ]
            if corrected_values != reference_values:
                raise ValueError(f"corrected/reference {stage} column differs: {column}")
            digest.update(canonical_json([column, corrected_values]).encode())
        observed = {
            "frame_count": len(ordered_keys),
            "physical_feature_count": len(corrected_physical),
            "physical_feature_names_sha256": hashlib.sha256(
                canonical_json(corrected_physical).encode()
            ).hexdigest(),
            "keyed_values_sha256": digest.hexdigest(),
            "exactly_equal": True,
        }
        expected = frozen["expected_stage_audit"].get(stage)
        if expected is not None and observed != expected:
            raise ValueError(f"corrected/reference {stage} audit differs from frozen pins")
        result["stages"][stage] = observed
    return result


def _load_t0_rows(
    dataset_path: Path,
    predictor: FrozenPredictor,
    model_config: dict[str, Any],
    source_outcomes: dict[tuple[int, int, int], dict[str, Any]],
    identity_by_run_id: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    target = model_config["target"]
    physical = list(physical_feature_names(predictor))
    metadata = [
        "run_id",
        "frame_id",
        "run_group_id",
        "sample_time_ns",
        "frame_size_bytes",
        "frame_packet_count",
        "frame_type",
        "actionable",
        "deadline_miss",
    ]
    table = pq.read_table(
        dataset_path,
        columns=list(dict.fromkeys(metadata + physical)),
        filters=[
            ("sample_stage", "=", "T0"),
            ("path_id", "=", str(target["path_id"])),
            ("copy_id", "=", str(target["copy_id"])),
            ("scenario_name", "=", target["scenario_name"]),
            ("selected_policy", "=", target["selected_policy"]),
        ],
    )
    if table.num_rows != int(model_config["expected_population"]["frame_count"]):
        raise ValueError("profile T0 row count differs from frozen population")
    values = table.to_pydict()
    matrix = np.empty((table.num_rows, len(predictor.feature_names)), dtype=np.float32)
    for index, (logical, recorded) in enumerate(
        zip(predictor.feature_names, physical, strict=True)
    ):
        matrix[:, index] = np.asarray(
            [
                encode_value(logical, "" if item is None else str(item))
                for item in values[recorded]
            ],
            dtype=np.float32,
        )
    run_id = np.asarray(values["run_id"], dtype=object)
    frame = np.asarray(values["frame_id"], dtype=np.int64)
    try:
        identities = [identity_by_run_id[str(item)] for item in run_id]
    except KeyError as error:
        raise ValueError(f"profile T0 run ID is absent from source: {error}") from error
    seed = np.asarray([item[0] for item in identities], dtype=np.int64)
    run_number = np.asarray([item[1] for item in identities], dtype=np.int64)
    keys = list(zip(seed.tolist(), run_number.tolist(), frame.tolist(), strict=True))
    if len(set(keys)) != table.num_rows:
        raise ValueError("profile T0 rows contain duplicate frame keys")
    sizes = np.asarray(values["frame_size_bytes"], dtype=np.int64)
    packets = np.asarray(values["frame_packet_count"], dtype=np.int64)
    miss = np.asarray(values["deadline_miss"], dtype=np.int8)
    for position, key in enumerate(keys):
        outcome = source_outcomes.get(key)
        if (
            outcome is None
            or int(miss[position]) != int(outcome["miss"])
            or int(sizes[position]) != int(outcome["frame_size_bytes"])
            or int(packets[position]) != int(outcome["frame_packet_count"])
        ):
            raise ValueError(f"profile T0 source outcome mismatch at row {position}")
    actionable = np.asarray(
        [str(item).lower() in {"1", "true"} for item in values["actionable"]],
        dtype=bool,
    )
    if not np.all(actionable):
        raise ValueError("profile T0 population contains a non-actionable row")
    return {
        "matrix": matrix,
        "group": np.asarray(values["run_group_id"], dtype=object),
        "run": run_id,
        "seed": seed,
        "run_number": run_number,
        "frame": frame,
        "sample_time_ns": np.asarray(values["sample_time_ns"], dtype=np.int64),
        "frame_size_bytes": sizes,
        "frame_packet_count": packets,
        "frame_type": np.asarray(values["frame_type"], dtype=object),
        "actionable": actionable,
    }


def _align_t0_to_t4(t0: dict[str, Any], t4: dict[str, Any]) -> np.ndarray:
    lookup = {
        (int(seed), int(run), int(frame)): index
        for index, (seed, run, frame) in enumerate(
            zip(t0["seed"], t0["run_number"], t0["frame"], strict=True)
        )
    }
    t4_keys = [
        (int(seed), int(run), int(frame))
        for seed, run, frame in zip(
            t4["seed"], t4["run_number"], t4["frame"], strict=True
        )
    ]
    if set(lookup) != set(t4_keys):
        raise ValueError("profile T0 and T4 frame keys differ")
    return np.asarray([lookup[key] for key in t4_keys], dtype=np.int64)


def stabilize_strict_threshold(
    density: np.ndarray, selected: np.ndarray
) -> tuple[float, dict[str, Any]]:
    """Place a strict gate inside the open selected/rejected density gap."""
    values = np.asarray(density, dtype=np.float64)
    mask = np.asarray(selected, dtype=bool)
    if (
        values.ndim != 1
        or mask.ndim != 1
        or len(values) != len(mask)
        or len(values) == 0
        or not np.all(np.isfinite(values))
        or not np.any(mask)
        or not np.any(~mask)
    ):
        raise ValueError("strict-gate stabilization requires two finite nonempty classes")
    maximum_rejected = float(np.max(values[~mask]))
    minimum_selected = float(np.min(values[mask]))
    if not maximum_rejected < minimum_selected:
        raise ValueError("selected and rejected risk densities have no open gap")
    threshold = maximum_rejected + (minimum_selected - maximum_rejected) / 2.0
    if not maximum_rejected < threshold < minimum_selected:
        threshold = float(np.nextafter(maximum_rejected, minimum_selected))
    if not maximum_rejected < threshold < minimum_selected:
        raise ValueError("strict-gate density gap has no representable interior value")
    stabilized = values > threshold
    if not np.array_equal(stabilized, mask):
        raise AssertionError("strict-gate stabilization changed the selected action mask")
    return threshold, {
        "maximum_rejected_risk_density": maximum_rejected,
        "minimum_selected_risk_density": minimum_selected,
        "margin_above_maximum_rejected": threshold - maximum_rejected,
        "margin_below_minimum_selected": minimum_selected - threshold,
        "stabilization_action_mask_unchanged": True,
    }


def select_joint_t4_threshold(
    partition: np.ndarray,
    t0_actionable: np.ndarray,
    t0_frame_type: np.ndarray,
    t0_probability: np.ndarray,
    t0_cost_us: np.ndarray,
    t4_actionable: np.ndarray,
    t4_admission_score: np.ndarray,
    t4_admission_cost_us: np.ndarray,
    t0_threshold: float,
    reference_cost_us: float,
    absolute_budget_us: float,
    stabilization_partition: np.ndarray | None = None,
) -> tuple[float, dict[str, Any]]:
    """Select a residual T4 gate after charging raw T0-I crossings.

    The operating budget and initial strict boundary use ``partition`` only.
    ``stabilization_partition`` may widen the label-free score population used
    solely to place an equivalent, representable threshold inside the nearest
    open density gap. The interface deliberately has no outcome-label input.
    """
    arrays = (
        partition,
        t0_actionable,
        t0_frame_type,
        t0_probability,
        t0_cost_us,
        t4_actionable,
        t4_admission_score,
        t4_admission_cost_us,
    )
    if len({len(item) for item in arrays}) != 1 or len(partition) == 0:
        raise ValueError("joint threshold vectors must have one nonempty length")
    if reference_cost_us <= 0 or absolute_budget_us < 0:
        raise ValueError("joint threshold has an invalid airtime scale")
    stability_partition = (
        partition
        if stabilization_partition is None
        else np.asarray(stabilization_partition, dtype=bool)
    )
    if len(stability_partition) != len(partition):
        raise ValueError("strict-gate stabilization partition has the wrong length")
    t0_density = t0_probability / (t0_cost_us / reference_cost_us)
    all_t0_crossing = (
        t0_actionable
        & (t0_frame_type == "I_FRAME")
        & (t0_density > t0_threshold)
    )
    t0_crossing = partition & all_t0_crossing
    t0_cost = float(t0_cost_us[t0_crossing].sum())
    remaining_budget = max(0.0, absolute_budget_us - t0_cost)
    candidate = partition & t4_actionable & ~t0_crossing
    positions = np.flatnonzero(candidate)
    selection_boundary, selected = select_risk_density_threshold(
        t4_admission_score[positions],
        t4_admission_cost_us[positions] / reference_cost_us,
        t4_admission_cost_us[positions],
        remaining_budget,
    )
    selection_density = t4_admission_score[positions] / (
        t4_admission_cost_us[positions] / reference_cost_us
    )
    stability_candidate = stability_partition & t4_actionable & ~all_t0_crossing
    stability_positions = np.flatnonzero(stability_candidate)
    stability_density = t4_admission_score[stability_positions] / (
        t4_admission_cost_us[stability_positions] / reference_cost_us
    )
    stability_selected = stability_density > selection_boundary
    threshold, stabilization = stabilize_strict_threshold(
        stability_density, stability_selected
    )
    if not np.array_equal(selection_density > threshold, selected):
        raise AssertionError(
            "strict-gate representation stabilization changed calibration selection"
        )
    t4_cost = float(t4_admission_cost_us[positions][selected].sum())
    return threshold, {
        "selection_partition_rows": int(partition.sum()),
        "selection_uses_labels": False,
        "absolute_budget_us": float(absolute_budget_us),
        "raw_t0_i_gate_crossings": int(t0_crossing.sum()),
        "raw_t0_i_cost_us": t0_cost,
        "residual_t4_candidate_rows": len(positions),
        "residual_t4_gate_crossings": int(selected.sum()),
        "residual_t4_cost_us": t4_cost,
        "combined_planned_cost_us": t0_cost + t4_cost,
        "unused_budget_us": absolute_budget_us - t0_cost - t4_cost,
        "selection_boundary_risk_density": float(selection_boundary),
        "risk_density_threshold": float(threshold),
        "selection_action_mask_unchanged": True,
        "representation_stabilization": (
            "label_free_nearest_density_midpoint_preserving_preselected_strict_mask"
        ),
        "stabilization_partition_rows": int(stability_partition.sum()),
        "stabilization_candidate_rows": len(stability_positions),
        "stabilization_uses_labels": False,
        **stabilization,
    }


def apply_raw_joint_gate(
    partition: np.ndarray,
    t0_actionable: np.ndarray,
    t0_frame_type: np.ndarray,
    t0_probability: np.ndarray,
    t0_cost_us: np.ndarray,
    t4_actionable: np.ndarray,
    t4_admission_score: np.ndarray,
    t4_admission_cost_us: np.ndarray,
    t0_threshold: float,
    t4_threshold: float,
    reference_cost_us: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Apply the staged gates without a runtime bucket or outcome labels."""
    t0_density = t0_probability / (t0_cost_us / reference_cost_us)
    t4_density = t4_admission_score / (t4_admission_cost_us / reference_cost_us)
    t0_action = (
        partition
        & t0_actionable
        & (t0_frame_type == "I_FRAME")
        & (t0_density > t0_threshold)
    )
    t4_action = (
        partition
        & ~t0_action
        & t4_actionable
        & (t4_density > t4_threshold)
    )
    return t0_action, t4_action, {
        "threshold_comparator": "strict_greater",
        "t0_i": {
            "risk_density_threshold": t0_threshold,
            "actions": int(t0_action.sum()),
            "admission_priced_cost_us": float(t0_cost_us[t0_action].sum()),
        },
        "residual_t4": {
            "risk_density_threshold": t4_threshold,
            "actions": int(t4_action.sum()),
            "admission_priced_cost_us": float(
                t4_admission_cost_us[t4_action].sum()
            ),
        },
        "combined": {
            "actions": int((t0_action | t4_action).sum()),
            "admission_priced_cost_us": float(
                t0_cost_us[t0_action].sum()
                + t4_admission_cost_us[t4_action].sum()
            ),
        },
    }


def replay_joint_t0_i_t4(
    group: np.ndarray,
    frame_id: np.ndarray,
    partition: np.ndarray,
    t0_sample_time_ns: np.ndarray,
    t0_actionable: np.ndarray,
    t0_frame_type: np.ndarray,
    t0_probability: np.ndarray,
    t0_cost_us: np.ndarray,
    t4_sample_time_ns: np.ndarray,
    t4_actionable: np.ndarray,
    t4_admission_score: np.ndarray,
    t4_admission_cost_us: np.ndarray,
    t0_threshold: float,
    t4_threshold: float,
    reference_cost_us: float,
    budget_fraction: float,
    bucket_horizon_us: int,
    initial_bucket_horizon_us: int,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Replay staged decisions through one causal per-run token bucket.

    The interface deliberately has no outcome-label input.
    """
    arrays = (
        group,
        frame_id,
        partition,
        t0_sample_time_ns,
        t0_actionable,
        t0_frame_type,
        t0_probability,
        t0_cost_us,
        t4_sample_time_ns,
        t4_actionable,
        t4_admission_score,
        t4_admission_cost_us,
    )
    if len({len(item) for item in arrays}) != 1 or len(group) == 0:
        raise ValueError("joint replay vectors must have one nonempty common length")
    if (
        not 0 < budget_fraction <= 1
        or bucket_horizon_us <= 0
        or initial_bucket_horizon_us <= 0
        or initial_bucket_horizon_us > bucket_horizon_us
        or reference_cost_us <= 0
    ):
        raise ValueError("joint replay token-bucket configuration is invalid")
    t0_density = t0_probability / (t0_cost_us / reference_cost_us)
    t4_density = np.full(len(group), -np.inf, dtype=np.float64)
    positive_t4_cost = t4_admission_cost_us > 0
    t4_density[positive_t4_cost] = t4_admission_score[positive_t4_cost] / (
        t4_admission_cost_us[positive_t4_cost] / reference_cost_us
    )
    t0_crossing = (
        partition
        & t0_actionable
        & (t0_frame_type == "I_FRAME")
        & (t0_density > t0_threshold)
    )
    t0_action = np.zeros(len(group), dtype=bool)
    t4_crossing = np.zeros(len(group), dtype=bool)
    t4_action = np.zeros(len(group), dtype=bool)
    t0_rejected = np.zeros(len(group), dtype=bool)
    t4_rejected = np.zeros(len(group), dtype=bool)
    capacity = budget_fraction * float(bucket_horizon_us)
    initial = budget_fraction * float(initial_bucket_horizon_us)
    per_group = []
    for name in sorted(set(map(str, group[partition]))):
        positions = np.flatnonzero(partition & (group == name))
        events = []
        for index in positions:
            events.append(
                (
                    int(t0_sample_time_ns[index]),
                    0,
                    int(frame_id[index]),
                    "T0",
                    index,
                )
            )
            events.append(
                (
                    int(t4_sample_time_ns[index]),
                    1,
                    int(frame_id[index]),
                    "T4",
                    index,
                )
            )
        events.sort()
        balance = initial
        last_ns = events[0][0]
        resolved: set[int] = set()
        for now_ns, _, _, stage, index in events:
            if now_ns < last_ns:
                raise ValueError("joint causal replay time moved backwards")
            balance = min(
                capacity,
                balance + budget_fraction * ((now_ns - last_ns) / 1000.0),
            )
            last_ns = now_ns
            if stage == "T0":
                if not t0_crossing[index]:
                    continue
                cost = float(t0_cost_us[index])
                if balance + 1e-9 >= cost:
                    t0_action[index] = True
                    resolved.add(index)
                    balance = max(0.0, balance - cost)
                else:
                    t0_rejected[index] = True
                continue
            if index in resolved or not (partition[index] and t4_actionable[index]):
                continue
            t4_crossing[index] = t4_density[index] > t4_threshold
            if not t4_crossing[index]:
                continue
            cost = float(t4_admission_cost_us[index])
            if not np.isfinite(cost) or cost <= 0:
                raise ValueError("joint T4 gate crossing has invalid cost")
            if balance + 1e-9 >= cost:
                t4_action[index] = True
                resolved.add(index)
                balance = max(0.0, balance - cost)
            else:
                t4_rejected[index] = True
        mask = group == name
        per_group.append(
            {
                "run_group_id": name,
                "t0_i_gate_crossings": int(t0_crossing[mask].sum()),
                "t0_i_actions": int(t0_action[mask].sum()),
                "t0_i_bucket_rejections": int(t0_rejected[mask].sum()),
                "residual_t4_gate_crossings": int(t4_crossing[mask].sum()),
                "residual_t4_actions": int(t4_action[mask].sum()),
                "residual_t4_bucket_rejections": int(t4_rejected[mask].sum()),
                "admission_priced_cost_us": float(
                    t0_cost_us[mask & t0_action].sum()
                    + t4_admission_cost_us[mask & t4_action].sum()
                ),
                "final_balance_us": balance,
            }
        )
    combined = t0_action | t4_action
    return t0_action, t4_action, {
        "event_order": "sample_time_ns_then_stage_then_frame_id",
        "shared_token_bucket": True,
        "budget_fraction": budget_fraction,
        "bucket_horizon_us": bucket_horizon_us,
        "initial_bucket_horizon_us": initial_bucket_horizon_us,
        "capacity_us": capacity,
        "initial_balance_us": initial,
        "t0_i": {
            "risk_density_threshold": t0_threshold,
            "gate_crossings": int(t0_crossing.sum()),
            "actions": int(t0_action.sum()),
            "bucket_rejections": int(t0_rejected.sum()),
            "admission_priced_cost_us": float(t0_cost_us[t0_action].sum()),
        },
        "residual_t4": {
            "risk_density_threshold": t4_threshold,
            "gate_crossings": int(t4_crossing.sum()),
            "actions": int(t4_action.sum()),
            "bucket_rejections": int(t4_rejected.sum()),
            "admission_priced_cost_us": float(
                t4_admission_cost_us[t4_action].sum()
            ),
        },
        "combined": {
            "actions": int(combined.sum()),
            "bucket_rejections": int(t0_rejected.sum() + t4_rejected.sum()),
            "admission_priced_cost_us": float(
                t0_cost_us[t0_action].sum()
                + t4_admission_cost_us[t4_action].sum()
            ),
        },
        "per_run_group": per_group,
    }


def _stage_metrics(
    data: dict[str, Any],
    partition: np.ndarray,
    action: np.ndarray,
    cost_us: np.ndarray,
) -> dict[str, Any]:
    selected = partition & action
    result = {
        "action_count": int(selected.sum()),
        "mechanism_nominal_cost_us": float(cost_us[selected].sum()),
        "frame_type_actions": {},
    }
    for frame_type in sorted(set(map(str, data["frame_type"][partition]))):
        result["frame_type_actions"][frame_type] = int(
            (selected & (data["frame_type"] == frame_type)).sum()
        )
    for name in ("primary_miss", "completed_tail"):
        result[f"{name}_selected_positive_count"] = int(
            (selected & (data[name] == 1)).sum()
        )
    return result


def outcome_selection_metrics(
    data: dict[str, Any],
    partition: np.ndarray,
    t0_action: np.ndarray,
    t4_action: np.ndarray,
    t0_cost_us: np.ndarray,
    t4_cost_us: np.ndarray,
    measurement_duration_us: float,
) -> dict[str, Any]:
    """Attach labels after a replay has frozen its actions."""
    combined = t0_action | t4_action
    selected = partition & combined
    actionable = partition & data["actionable"]
    duration = len(set(map(str, data["group"][partition]))) * measurement_duration_us
    result = {
        "frame_count": int(partition.sum()),
        "actionable_t4_frame_count": int(actionable.sum()),
        "t0_i": _stage_metrics(data, partition, t0_action, t0_cost_us),
        "residual_t4": _stage_metrics(data, partition, t4_action, t4_cost_us),
        "combined": _stage_metrics(
            data,
            partition,
            combined,
            np.where(t0_action, t0_cost_us, t4_cost_us),
        ),
    }
    total_cost = float(
        t0_cost_us[partition & t0_action].sum()
        + t4_cost_us[partition & t4_action].sum()
    )
    result["combined"]["mechanism_nominal_cost_us"] = total_cost
    result["combined"]["mechanism_nominal_cost_fraction"] = total_cost / duration
    for name in ("primary_miss", "completed_tail"):
        positive = partition & (data[name] == 1)
        actionable_positive = actionable & (data[name] == 1)
        result[name] = {
            "all_frame_positive_count": int(positive.sum()),
            "all_frame_selected_positive_count": int((positive & selected).sum()),
            "all_frame_selection_recall": (
                float((positive & selected).sum() / positive.sum())
                if np.any(positive)
                else None
            ),
            "actionable_t4_positive_count": int(actionable_positive.sum()),
            "actionable_t4_selected_positive_count": int(
                (actionable_positive & selected).sum()
            ),
            "actionable_t4_selection_recall": (
                float(
                    (actionable_positive & selected).sum()
                    / actionable_positive.sum()
                )
                if np.any(actionable_positive)
                else None
            ),
        }
    return result


def _action_identity(
    data: dict[str, Any], t0_action: np.ndarray, t4_action: np.ndarray
) -> str:
    selected = []
    for index in np.flatnonzero(t0_action):
        selected.append(
            [
                int(data["seed"][index]),
                int(data["run_number"][index]),
                int(data["frame"][index]),
                "T0",
            ]
        )
    for index in np.flatnonzero(t4_action):
        selected.append(
            [
                int(data["seed"][index]),
                int(data["run_number"][index]),
                int(data["frame"][index]),
                "T4",
            ]
        )
    return hashlib.sha256(canonical_json(sorted(selected)).encode()).hexdigest()


def _receiver_release_mismatch_audit(
    manifest: dict[str, Any],
    data: dict[str, Any],
    source_outcomes: dict[tuple[int, int, int], dict[str, Any]],
) -> dict[str, Any]:
    """Compare T4 MAC-ACK count with final application-visible unique packets."""
    unique_packets = {}
    identities = _manifest_identities(
        manifest,
        {"scenario_name": "obss_only", "selected_policy": "fixed_link_1", "path_id": 1},
    )
    for run in manifest["included_runs"]:
        run_id = str(run["run_id"])
        if run_id not in identities:
            continue
        seed, run_number = identities[run_id]
        with (Path(run["source_directory"]) / "frames.csv").open(
            newline="", encoding="utf-8"
        ) as source:
            for row in csv.DictReader(source):
                unique_packets[(seed, run_number, int(row["frame_id"]))] = int(
                    row["unique_packets_received"]
                )
    mismatch = np.zeros(len(data["frame"]), dtype=bool)
    final_unique = np.empty(len(data["frame"]), dtype=np.int64)
    for index, (seed, run, frame, acked) in enumerate(
        zip(
            data["seed"],
            data["run_number"],
            data["frame"],
            data["frame_packets_tx_succeeded"],
            strict=True,
        )
    ):
        key = (int(seed), int(run), int(frame))
        if key not in source_outcomes or key not in unique_packets:
            raise ValueError(f"receiver-release audit is missing frame key {key}")
        final_unique[index] = unique_packets[key]
        mismatch[index] = final_unique[index] < int(acked)
    miss = data["primary_miss"] == 1
    actionable_miss = miss & data["actionable"]
    return {
        "interpretation": "mac_ack_progress_can_exceed_application_released_packets",
        "primary_miss_count": int(miss.sum()),
        "primary_miss_mismatch_count": int((miss & mismatch).sum()),
        "primary_miss_mismatch_fraction": float((miss & mismatch).sum() / miss.sum()),
        "actionable_t4_primary_miss_count": int(actionable_miss.sum()),
        "actionable_t4_primary_miss_mismatch_count": int(
            (actionable_miss & mismatch).sum()
        ),
        "maximum_acked_minus_final_unique_packets": int(
            np.max(data["frame_packets_tx_succeeded"][mismatch] - final_unique[mismatch])
        ),
    }


def _load_artifacts(args: argparse.Namespace, profile: dict[str, Any]) -> dict[str, Any]:
    model_config_path = args.model_config.resolve()
    model_config = load_model_config(model_config_path)
    model_pin = profile["model_artifact"]
    if sha256_file(model_config_path) != model_pin["training_config_sha256"]:
        raise ValueError("profile model-training config checksum changed")
    model_dir = args.model_dir.resolve()
    model_manifest_path = model_dir / "primary_tail_t4_manifest.json"
    model_manifest = _json(model_manifest_path)
    model_path = model_dir / model_manifest["model_file"]
    if (
        sha256_file(model_manifest_path) != model_pin["manifest_sha256"]
        or sha256_file(model_path) != model_pin["bundle_sha256"]
        or model_manifest.get("model_sha256") != model_pin["bundle_sha256"]
        or model_manifest.get("model_id") != model_pin["model_id"]
        or model_manifest.get("target_provenance_sha256")
        != model_pin["target_provenance_sha256"]
        or model_manifest.get("artifact_scope") != model_pin["artifact_scope"]
        or model_manifest.get("operating_profile_included") is not False
    ):
        raise ValueError("neutral primary-tail artifact differs from profile pins")
    export_path = model_dir / "primary_tail_t4_export.json"
    export_manifest_path = model_dir / "primary_tail_t4_export_manifest.json"
    export_manifest = _json(export_manifest_path)
    runtime_pin = model_pin["runtime_export"]
    if sha256_file(export_manifest_path) != runtime_pin["export_manifest_sha256"]:
        raise ValueError("primary-tail runtime export manifest differs from profile pin")
    export_payload, bundle, runtime_digests, _ = validate_artifacts(
        export_path,
        export_manifest,
        model_manifest,
        model_path,
    )
    expected_runtime_digests = {
        "export": runtime_pin["export_sha256"],
        "target_provenance": runtime_pin["target_provenance_sha256"],
        "feature_contract": runtime_pin["feature_contract_sha256"],
        "combiner": runtime_pin["combiner_sha256"],
        "primary_miss_model": runtime_pin["primary_miss_model_sha256"],
        "completed_tail_model": runtime_pin["completed_tail_model_sha256"],
    }
    if runtime_digests != expected_runtime_digests:
        raise ValueError("primary-tail runtime export digest chain changed")

    dataset_dir = args.dataset_dir.resolve()
    dataset_manifest_path = dataset_dir / "dataset_manifest.json"
    dataset_validation_path = dataset_dir / "dataset_validation.json"
    dataset_manifest = _json(dataset_manifest_path)
    dataset_path = dataset_dir / dataset_manifest["dataset_file"]
    dataset_pin = profile["dataset"]
    if (
        sha256_file(dataset_manifest_path) != dataset_pin["manifest_sha256"]
        or sha256_file(dataset_validation_path) != dataset_pin["validation_sha256"]
        or sha256_file(dataset_path) != dataset_pin["sha256"]
        or dataset_manifest.get("dataset_sha256") != dataset_pin["sha256"]
    ):
        raise ValueError("profile dataset differs from frozen pins")

    t0_pin = profile["t0_dependency"]
    t0_dir = args.t0_bundle_dir.resolve()
    t0_manifest_path = t0_dir / "model_bundle_manifest.json"
    t0_manifest = _json(t0_manifest_path)
    t0_model_path = t0_dir / t0_manifest["model_file"]
    if (
        sha256_file(t0_manifest_path) != t0_pin["manifest_sha256"]
        or sha256_file(t0_model_path) != t0_pin["bundle_sha256"]
        or t0_manifest.get("model_sha256") != t0_pin["bundle_sha256"]
        or t0_manifest.get("model_id") != t0_pin["model_id"]
        or t0_manifest.get("target_provenance_sha256")
        != t0_pin["target_provenance_sha256"]
    ):
        raise ValueError("profile T0 dependency differs from frozen pins")
    t0_bundle = read_model_bundle(t0_model_path)
    t0_predictor = t0_bundle.predictors.get((t0_pin["pipeline_id"], t0_pin["stage"]))
    if t0_predictor is None or len(t0_predictor.feature_names) != int(
        t0_pin["expected_feature_count"]
    ):
        raise ValueError("profile T0 predictor contract changed")
    return {
        "model_config": model_config,
        "model_manifest": model_manifest,
        "bundle": bundle,
        "runtime_export_payload": export_payload,
        "runtime_export_digests": runtime_digests,
        "dataset_manifest": dataset_manifest,
        "dataset_path": dataset_path,
        "t0_predictor": t0_predictor,
    }


def _predict_runtime_export(
    payload: dict[str, Any], matrix: np.ndarray, actionable: np.ndarray
) -> np.ndarray:
    """Evaluate the deployment export independently of sklearn."""
    features = np.asarray(matrix)
    mask = np.asarray(actionable, dtype=bool)
    if (
        features.ndim != 2
        or mask.ndim != 1
        or len(features) != len(mask)
        or features.shape[1] != len(payload["feature_names"])
    ):
        raise ValueError("runtime-export prediction matrix has an invalid shape")
    combiner = payload["combiner"]
    miss_weight = float(combiner["primary_miss_weight"])
    tail_weight = float(combiner["completed_tail_weight"])
    normalization = float(combiner["normalization"])
    scores = np.zeros(len(features), dtype=np.float64)
    for position in np.flatnonzero(mask):
        _, miss = evaluate_exported_head(
            payload["heads"]["primary_miss"], features[position]
        )
        _, tail = evaluate_exported_head(
            payload["heads"]["completed_tail"], features[position]
        )
        scores[position] = (miss_weight * miss + tail_weight * tail) / normalization
    if not np.all(np.isfinite(scores[mask])) or np.any(
        (scores[mask] < 0) | (scores[mask] > 1)
    ):
        raise ValueError("runtime export produced an invalid admission score")
    return scores


def _diagnostic_pins(result: dict[str, Any]) -> dict[str, Any]:
    pins = {
        "runtime_export_audit": result["runtime_export_audit"],
        "outcome_population": result["outcome_population"],
        "corrected_source_equivalence": result["corrected_source_equivalence"][
            "stages"
        ],
        "receiver_release_mismatch": {
            key: result["receiver_release_mismatch"][key]
            for key in (
                "primary_miss_count",
                "primary_miss_mismatch_count",
                "actionable_t4_primary_miss_count",
                "actionable_t4_primary_miss_mismatch_count",
            )
        },
        FULL_COPY_PROFILE: {},
        DEFICIT_PROFILE: {},
    }
    for budget, report in result["profiles"][FULL_COPY_PROFILE]["budgets"].items():
        raw = report["all_engineering"]["raw_gate"]
        realized = report["all_engineering"]["bucket_realized"]
        calibration_realized = report["calibration"]["bucket_realized"]
        pins[FULL_COPY_PROFILE][budget] = {
            "risk_density_threshold": report["threshold_selection"][
                "risk_density_threshold"
            ],
            "selection_boundary_risk_density": report["threshold_selection"][
                "selection_boundary_risk_density"
            ],
            "maximum_rejected_risk_density": report["threshold_selection"][
                "maximum_rejected_risk_density"
            ],
            "minimum_selected_risk_density": report["threshold_selection"][
                "minimum_selected_risk_density"
            ],
            "margin_above_maximum_rejected": report["threshold_selection"][
                "margin_above_maximum_rejected"
            ],
            "margin_below_minimum_selected": report["threshold_selection"][
                "margin_below_minimum_selected"
            ],
            "selection_action_mask_unchanged": report["threshold_selection"][
                "selection_action_mask_unchanged"
            ],
            "stabilization_action_mask_unchanged": report[
                "threshold_selection"
            ]["stabilization_action_mask_unchanged"],
            "representation_stabilization": report["threshold_selection"][
                "representation_stabilization"
            ],
            "stabilization_partition_rows": report["threshold_selection"][
                "stabilization_partition_rows"
            ],
            "stabilization_candidate_rows": report["threshold_selection"][
                "stabilization_candidate_rows"
            ],
            "stabilization_uses_labels": report["threshold_selection"][
                "stabilization_uses_labels"
            ],
            "calibration_runtime_gate_mask_difference_count": report[
                "calibration"
            ]["runtime_export_gate_parity"]["gate_mask_difference_count"],
            "all_engineering_runtime_gate_mask_difference_count": report[
                "all_engineering"
            ]["runtime_export_gate_parity"]["gate_mask_difference_count"],
            "calibration_combined_planned_cost_us": report["threshold_selection"][
                "combined_planned_cost_us"
            ],
            "calibration_combined_planned_cost_fraction": report[
                "threshold_selection"
            ]["combined_planned_cost_fraction"],
            "calibration_bucket_rejections": calibration_realized["admission"][
                "combined"
            ]["bucket_rejections"],
            "raw_action_identity_sha256": raw["action_identity_sha256"],
            "raw_combined_actions": raw["admission"]["combined"]["actions"],
            "raw_admission_priced_cost_us": raw["admission"]["combined"][
                "admission_priced_cost_us"
            ],
            "raw_admission_priced_cost_fraction": raw["admission"]["combined"][
                "admission_priced_cost_fraction"
            ],
            "raw_primary_miss_selected": raw["outcomes"]["primary_miss"][
                "all_frame_selected_positive_count"
            ],
            "raw_completed_tail_selected": raw["outcomes"]["completed_tail"][
                "all_frame_selected_positive_count"
            ],
            "raw_i_actions": raw["outcomes"]["combined"]["frame_type_actions"][
                "I_FRAME"
            ],
            "raw_p_actions": raw["outcomes"]["combined"]["frame_type_actions"][
                "P_FRAME"
            ],
            "realized_action_identity_sha256": realized[
                "action_identity_sha256"
            ],
            "realized_combined_actions": realized["admission"]["combined"][
                "actions"
            ],
            "realized_bucket_rejections": realized["admission"]["combined"][
                "bucket_rejections"
            ],
            "realized_admission_priced_cost_us": realized["admission"]["combined"][
                "admission_priced_cost_us"
            ],
            "realized_admission_priced_cost_fraction": realized["admission"][
                "combined"
            ]["admission_priced_cost_fraction"],
            "realized_primary_miss_selected": realized["outcomes"]["primary_miss"][
                "all_frame_selected_positive_count"
            ],
            "realized_completed_tail_selected": realized["outcomes"][
                "completed_tail"
            ]["all_frame_selected_positive_count"],
            "realized_i_actions": realized["outcomes"]["combined"][
                "frame_type_actions"
            ]["I_FRAME"],
            "realized_p_actions": realized["outcomes"]["combined"][
                "frame_type_actions"
            ]["P_FRAME"],
        }
    for budget, report in result["profiles"][DEFICIT_PROFILE]["budgets"].items():
        raw = report["all_engineering"]["raw_gate"]
        realized = report["all_engineering"]["bucket_realized"]
        pins[DEFICIT_PROFILE][budget] = {
            "raw_action_identity_sha256": raw["action_identity_sha256"],
            "raw_mechanism_nominal_cost_us": raw["outcomes"]["combined"][
                "mechanism_nominal_cost_us"
            ],
            "raw_mechanism_nominal_cost_fraction": raw["outcomes"]["combined"][
                "mechanism_nominal_cost_fraction"
            ],
            "realized_action_identity_sha256": realized[
                "action_identity_sha256"
            ],
            "realized_mechanism_nominal_cost_us": realized["outcomes"][
                "combined"
            ]["mechanism_nominal_cost_us"],
            "realized_mechanism_nominal_cost_fraction": realized["outcomes"][
                "combined"
            ]["mechanism_nominal_cost_fraction"],
        }
    return pins


def _verify_expected(expected: dict[str, Any], observed: dict[str, Any]) -> None:
    if set(expected) != set(observed):
        raise ValueError("profile expected-diagnostic section set changed")

    def compare(path: str, wanted: Any, actual: Any) -> None:
        if isinstance(wanted, dict):
            if not isinstance(actual, dict) or set(wanted) != set(actual):
                raise ValueError(f"profile diagnostic keys changed: {path}")
            for key in wanted:
                compare(f"{path}.{key}", wanted[key], actual[key])
        elif isinstance(wanted, float):
            if not np.isclose(float(actual), wanted, rtol=0, atol=1e-9):
                raise ValueError(f"profile diagnostic float changed: {path}")
        elif actual != wanted:
            raise ValueError(f"profile diagnostic value changed: {path}")

    compare("expected_diagnostics", expected, observed)


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    """Evaluate all frozen named profiles and optionally publish diagnostics."""
    profile_path = args.profile_config.resolve()
    profile = load_profile_config(profile_path)
    loaded = _load_artifacts(args, profile)
    model_config = loaded["model_config"]
    dataset_manifest = loaded["dataset_manifest"]
    dataset_path = loaded["dataset_path"]
    bundle = loaded["bundle"]
    runtime_export_payload = loaded["runtime_export_payload"]
    t0_predictor = loaded["t0_predictor"]
    source_outcomes, source_audit, identity_by_run_id = _audit_source_outcomes(
        dataset_manifest, model_config
    )
    t4 = _load_target_rows(
        dataset_path,
        bundle.heads["primary_miss"],
        model_config,
        source_outcomes,
        identity_by_run_id,
    )
    t0 = _load_t0_rows(
        dataset_path,
        t0_predictor,
        model_config,
        source_outcomes,
        identity_by_run_id,
    )
    t0_by_t4 = _align_t0_to_t4(t0, t4)
    t0_aligned = {
        key: value[t0_by_t4]
        for key, value in t0.items()
        if isinstance(value, np.ndarray) and len(value) == len(t0_by_t4)
    }
    equivalence = audit_corrected_source_equivalence(
        dataset_path,
        dataset_manifest,
        args.t0_reference_dataset_dir.resolve(),
        t0_predictor,
        bundle.heads["primary_miss"],
        model_config,
        profile,
    )
    t0_probability = t0_predictor.predict(t0_aligned["matrix"])[1]
    t4_score = np.zeros(len(t4["frame"]), dtype=np.float64)
    positions = np.flatnonzero(t4["actionable"])
    t4_prediction = bundle.predict(t4["matrix"][positions])
    t4_score[positions] = t4_prediction["admission_score"]
    runtime_t4_score = _predict_runtime_export(
        runtime_export_payload, t4["matrix"], t4["actionable"]
    )
    if not np.all(np.isfinite(t0_probability)) or not np.all(
        np.isfinite(t4_score[positions])
    ):
        raise ValueError("profile model produced a nonfinite score")
    maximum_runtime_score_difference = float(
        np.max(np.abs(runtime_t4_score[positions] - t4_score[positions]))
    )

    estimator = profile["airtime_estimator"]
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
        raise ValueError("profile reference airtime changed")
    t0_cost = estimate_whole_copy_airtime_us(
        t0_aligned["frame_size_bytes"], t0_aligned["frame_packet_count"], estimator
    )
    full_t4_cost = estimate_whole_copy_airtime_us(
        t4["frame_size_bytes"], t4["frame_packet_count"], estimator
    )
    deficit_t4_cost = estimate_primary_deficit_airtime_us(
        t4["frame_size_bytes"],
        t4["frame_packet_count"],
        t4["frame_packets_tx_succeeded"],
        estimator,
    )
    if np.any(t4["actionable"] & (deficit_t4_cost <= 0)):
        raise ValueError("actionable T4 frame has no deficit cost")

    calibration_seeds = set(profile["threshold_selection"]["calibration_seeds"])
    calibration = np.asarray(
        [int(seed) in calibration_seeds for seed in t4["seed"]], dtype=bool
    )
    all_rows = np.ones(len(t4["frame"]), dtype=bool)
    duration_us = float(model_config["expected_population"]["measurement_duration_s"]) * 1e6
    selection_duration = len(calibration_seeds) * duration_us
    t0_threshold = float(profile["t0_dependency"]["risk_density_threshold"])
    common_replay = {
        "group": t4["group"],
        "frame_id": t4["frame"],
        "t0_sample_time_ns": t0_aligned["sample_time_ns"],
        "t0_actionable": t0_aligned["actionable"],
        "t0_frame_type": t0_aligned["frame_type"],
        "t0_probability": t0_probability,
        "t0_cost_us": t0_cost,
        "t4_sample_time_ns": t4["sample_time_ns"],
        "t4_actionable": t4["actionable"],
        "t4_admission_score": t4_score,
        "t0_threshold": t0_threshold,
        "reference_cost_us": reference_cost,
    }

    full_config = profile["profiles"][FULL_COPY_PROFILE]
    deficit_config = profile["profiles"][DEFICIT_PROFILE]
    full_reports = {}
    deficit_reports = {}
    for budget_fraction in map(
        float, full_config["absolute_nominal_budget_fractions"]
    ):
        threshold, selection = select_joint_t4_threshold(
            calibration,
            t0_aligned["actionable"],
            t0_aligned["frame_type"],
            t0_probability,
            t0_cost,
            t4["actionable"],
            t4_score,
            full_t4_cost,
            t0_threshold,
            reference_cost,
            budget_fraction * selection_duration,
            stabilization_partition=all_rows,
        )
        selection["combined_planned_cost_fraction"] = (
            selection["combined_planned_cost_us"] / selection_duration
        )
        full_partitions = {}
        deficit_partitions = {}
        for partition_name, partition in (
            ("calibration", calibration),
            ("all_engineering", all_rows),
        ):
            partition_duration = (
                len(set(map(str, t4["group"][partition]))) * duration_us
            )
            raw_t0, raw_t4, raw_admission = apply_raw_joint_gate(
                partition,
                t0_aligned["actionable"],
                t0_aligned["frame_type"],
                t0_probability,
                t0_cost,
                t4["actionable"],
                t4_score,
                full_t4_cost,
                t0_threshold,
                threshold,
                reference_cost,
            )
            runtime_t0, runtime_t4, _ = apply_raw_joint_gate(
                partition,
                t0_aligned["actionable"],
                t0_aligned["frame_type"],
                t0_probability,
                t0_cost,
                t4["actionable"],
                runtime_t4_score,
                full_t4_cost,
                t0_threshold,
                threshold,
                reference_cost,
            )
            gate_difference_count = int(np.count_nonzero(raw_t4 ^ runtime_t4))
            if not np.array_equal(raw_t0, runtime_t0) or gate_difference_count != 0:
                raise ValueError(
                    "stabilized sklearn and runtime-export T4 gate masks differ"
                )
            raw_admission["combined"]["admission_priced_cost_fraction"] = (
                raw_admission["combined"]["admission_priced_cost_us"]
                / partition_duration
            )
            t0_action, t4_action, replay = replay_joint_t0_i_t4(
                partition=partition,
                t4_admission_cost_us=full_t4_cost,
                t4_threshold=threshold,
                budget_fraction=float(full_config["token_bucket_fraction"]),
                bucket_horizon_us=int(full_config["bucket_horizon_us"]),
                initial_bucket_horizon_us=int(
                    full_config["initial_bucket_horizon_us"]
                ),
                **common_replay,
            )
            replay["combined"]["admission_priced_cost_fraction"] = (
                replay["combined"]["admission_priced_cost_us"]
                / partition_duration
            )
            raw_identity = _action_identity(t4, raw_t0, raw_t4)
            realized_identity = _action_identity(t4, t0_action, t4_action)
            full_partitions[partition_name] = {
                "runtime_export_gate_parity": {
                    "actionable_score_count": int(
                        (partition & t4["actionable"]).sum()
                    ),
                    "gate_mask_difference_count": gate_difference_count,
                    "sklearn_action_identity_sha256": raw_identity,
                    "runtime_export_action_identity_sha256": _action_identity(
                        t4, runtime_t0, runtime_t4
                    ),
                    "exact_gate_mask_match": True,
                },
                "raw_gate": {
                    "admission": raw_admission,
                    "action_identity_sha256": raw_identity,
                    "outcomes": outcome_selection_metrics(
                        t4,
                        partition,
                        raw_t0,
                        raw_t4,
                        t0_cost,
                        full_t4_cost,
                        duration_us,
                    ),
                },
                "bucket_realized": {
                    "admission": replay,
                    "action_identity_sha256": realized_identity,
                    "outcomes": outcome_selection_metrics(
                        t4,
                        partition,
                        t0_action,
                        t4_action,
                        t0_cost,
                        full_t4_cost,
                        duration_us,
                    ),
                },
            }
            deficit_partitions[partition_name] = {
                "raw_gate": {
                    "admission_decision_source": (
                        f"{FULL_COPY_PROFILE}.{format(budget_fraction, '.12g')}."
                        f"{partition_name}.raw_gate"
                    ),
                    "action_identity_sha256": raw_identity,
                    "outcomes": outcome_selection_metrics(
                        t4,
                        partition,
                        raw_t0,
                        raw_t4,
                        t0_cost,
                        deficit_t4_cost,
                        duration_us,
                    ),
                },
                "bucket_realized": {
                    "admission_decision_source": (
                        f"{FULL_COPY_PROFILE}.{format(budget_fraction, '.12g')}."
                        f"{partition_name}.bucket_realized"
                    ),
                    "action_identity_sha256": realized_identity,
                    "outcomes": outcome_selection_metrics(
                        t4,
                        partition,
                        t0_action,
                        t4_action,
                        t0_cost,
                        deficit_t4_cost,
                        duration_us,
                    ),
                },
            }
            if (
                full_partitions[partition_name]["raw_gate"][
                    "action_identity_sha256"
                ]
                != deficit_partitions[partition_name]["raw_gate"][
                    "action_identity_sha256"
                ]
                or full_partitions[partition_name]["bucket_realized"][
                    "action_identity_sha256"
                ]
                != deficit_partitions[partition_name]["bucket_realized"][
                    "action_identity_sha256"
                ]
            ):
                raise AssertionError("ISO mechanism ablation action identity changed")
        full_reports[format(budget_fraction, ".12g")] = {
            "threshold_selection": selection,
            **full_partitions,
        }
        deficit_reports[format(budget_fraction, ".12g")] = {
            "threshold_and_admission_source": (
                f"{FULL_COPY_PROFILE}.{format(budget_fraction, '.12g')}"
            ),
            **deficit_partitions,
        }

    result = {
        "primary_tail_t4_operating_profile_diagnostics_schema_version": 1,
        "analysis_id": profile["analysis_id"],
        "evidence_status": profile["evidence_status"],
        "independent_ood_claim": False,
        "model_artifact": copy.deepcopy(profile["model_artifact"]),
        "runtime_export_audit": {
            "validation": "generator_validate_artifacts",
            "actionable_score_count": len(positions),
            "maximum_absolute_admission_score_difference": (
                maximum_runtime_score_difference
            ),
            **loaded["runtime_export_digests"],
        },
        "dataset": copy.deepcopy(profile["dataset"]),
        "profile_config_sha256": sha256_file(profile_path),
        "evaluator": "tools/evaluate_primary_tail_t4_profiles.py",
        "evaluator_sha256": sha256_file(Path(__file__).resolve()),
        "source_audit": source_audit,
        "outcome_population": {
            "source_seed_count": len(set(map(int, t4["seed"]))),
            "run_group_count": len(set(map(str, t4["group"]))),
            "frame_count": len(t4["frame"]),
            "actionable_t4_frame_count": int(t4["actionable"].sum()),
            "primary_miss_count": int((t4["primary_miss"] == 1).sum()),
            "actionable_t4_primary_miss_count": int(
                (t4["actionable"] & (t4["primary_miss"] == 1)).sum()
            ),
            "completed_tail_count": int((t4["completed_tail"] == 1).sum()),
            "actionable_t4_completed_tail_count": int(
                (t4["actionable"] & (t4["completed_tail"] == 1)).sum()
            ),
        },
        "corrected_source_equivalence": equivalence,
        "receiver_release_mismatch": _receiver_release_mismatch_audit(
            dataset_manifest, t4, source_outcomes
        ),
        "reference_airtime_us": reference_cost,
        "profiles": {
            FULL_COPY_PROFILE: {
                "role": full_config["role"],
                "t4_action_mode": full_config["t4_action_mode"],
                "admission_cost_mode": full_config["admission_cost_mode"],
                "candidate_designations": copy.deepcopy(
                    full_config["candidate_designations"]
                ),
                "safety_bucket_fraction": float(
                    full_config["token_bucket_fraction"]
                ),
                "runtime_bucket_accounting": full_config[
                    "runtime_bucket_accounting"
                ],
                "offline_replay_accounting": full_config[
                    "offline_replay_accounting"
                ],
                "budgets": full_reports,
            },
            DEFICIT_PROFILE: {
                "role": deficit_config["role"],
                "t4_action_mode": deficit_config["t4_action_mode"],
                "admission_cost_mode": deficit_config["admission_cost_mode"],
                "decision_source": deficit_config["decision_source"],
                "safety_bucket_fraction": float(
                    deficit_config["token_bucket_fraction"]
                ),
                "runtime_bucket_accounting": deficit_config[
                    "runtime_bucket_accounting"
                ],
                "offline_replay_accounting": deficit_config[
                    "offline_replay_accounting"
                ],
                "budgets": deficit_reports,
            },
        },
        "limitations": copy.deepcopy(profile["limitations"]),
    }
    observed_pins = _diagnostic_pins(result)
    if args.observed_pins_only:
        return {
            "status": "observed_profile_pins_only_no_diagnostic_published",
            "expected_diagnostics": observed_pins,
        }
    _verify_expected(profile["expected_diagnostics"], observed_pins)
    _atomic_json(args.output.resolve(), result)
    return {
        "status": "PASS",
        "output": args.output.name,
        "output_sha256": sha256_file(args.output.resolve()),
        "expected_diagnostics": observed_pins,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--model-config", required=True, type=Path)
    parser.add_argument("--t0-bundle-dir", required=True, type=Path)
    parser.add_argument("--t0-reference-dataset-dir", required=True, type=Path)
    parser.add_argument("--profile-config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--observed-pins-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    try:
        result = evaluate(parse_args())
    except (OSError, ValueError) as error:
        raise SystemExit(f"error: {error}") from error
    print(canonical_json(result))


if __name__ == "__main__":
    main()
