#!/usr/bin/env python3
"""Shared data, split, and OOD primitives for environment LOFO analysis."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

import build_environment_generalization_dataset as environment_builder
import build_randomized_intervention_dataset as base_builder
import build_randomized_temporal_dataset as temporal_builder
import train_temporal_t2_value as trainer


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = Path(
    "experiments/model-selection/environment-generalization-lofo-v1.json"
)
CONTRACT_SHA256 = "1566fba76e39f9e677d1c133199a47ff5275f4b3981149dd5871f599a278a9d4"
MODEL_FEATURE_FAMILY = "primary_secondary_hgb64_plus_sender_context"
MODEL_FEATURE_COUNT = 313
PRIOR_FEATURE_COUNT = 308
OOD_CONTEXT_COUNT = 16
OOD_ENCODED_COUNT = 32


class LofoError(RuntimeError):
    """Raised when a LOFO contract, dataset, split, or OOD invariant differs."""


@dataclass(frozen=True)
class LofoDataset:
    """Validated randomized observations and frozen encoded feature matrices."""

    path: Path
    metadata: dict[str, Any]
    artifact_manifest: dict[str, Any]
    contract: dict[str, Any]
    model_matrix: np.ndarray
    model_feature_names: tuple[str, ...]
    ood_context: np.ndarray
    ood_context_names: tuple[str, ...]
    run_ids: tuple[str, ...]
    seeds: np.ndarray
    run_numbers: np.ndarray
    frame_ids: np.ndarray
    scenario_ids: tuple[str, ...]
    family_ids: tuple[str, ...]
    parameter_samples: np.ndarray
    treatment: np.ndarray
    propensity: np.ndarray
    outcome_bins: np.ndarray
    union_latencies_us: np.ndarray
    primary_latencies_us: np.ndarray
    deadline_us: np.ndarray
    deadline_threshold_indices: np.ndarray
    deadline_miss: np.ndarray
    primary_deadline_miss: np.ndarray
    completed_late18: np.ndarray
    canonical_reservation_us: np.ndarray
    canonical_reservation_texts: tuple[str, ...]
    frame_types: tuple[str, ...]


@dataclass(frozen=True)
class RobustOodModel:
    """One robustly scaled, shrinkage-Mahalanobis context model."""

    context_names: tuple[str, ...]
    optional_missing_indices: tuple[int, ...]
    medians: np.ndarray
    center: np.ndarray
    scale: np.ndarray
    inverse_covariance: np.ndarray
    application_minimum: np.ndarray
    application_maximum: np.ndarray

    def encode(self, context: np.ndarray) -> np.ndarray:
        """Impute and robustly scale raw context rows."""

        matrix = _context_matrix(context, len(self.context_names))
        missing = np.isnan(matrix)
        imputed = np.where(missing, self.medians, matrix)
        encoded = np.concatenate((imputed, missing.astype(float)), axis=1)
        return (encoded - self.center) / self.scale

    def scores(self, context: np.ndarray) -> np.ndarray:
        """Return squared Mahalanobis distances for raw context rows."""

        scaled = self.encode(context)
        scores = np.einsum(
            "ij,jk,ik->i", scaled, self.inverse_covariance, scaled, optimize=True
        )
        if not np.all(np.isfinite(scores)) or np.any(scores < -1e-9):
            raise LofoError("OOD detector produced invalid distances")
        return np.maximum(scores, 0.0)

    def hard_failures(self, context: np.ndarray) -> np.ndarray:
        """Return rows that must fail closed before distance thresholding."""

        matrix = _context_matrix(context, len(self.context_names))
        missing = np.isnan(matrix)
        allowed = np.zeros(len(self.context_names), dtype=bool)
        allowed[list(self.optional_missing_indices)] = True
        required_missing = np.any(missing[:, ~allowed], axis=1)
        application = matrix[:, : len(environment_builder.ENVIRONMENT_FEATURE_COLUMNS)]
        outside = np.any(
            (application < self.application_minimum)
            | (application > self.application_maximum)
            | ~np.isfinite(application),
            axis=1,
        )
        return required_missing | outside


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise LofoError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LofoError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise LofoError(f"{path}: expected a JSON object")
    return value


def load_contract() -> dict[str, Any]:
    """Load and fully bind the frozen LOFO analysis contract."""

    path = ROOT / CONTRACT_PATH
    if _sha256(path) != CONTRACT_SHA256:
        raise LofoError("LOFO analysis contract hash differs")
    contract = _read_json(path)
    if (
        contract.get("schema_version") != 1
        or contract.get("analysis_id") != "environment-generalization-lofo-v1"
        or contract.get("status")
        != "frozen_before_randomized_collection_results_read"
    ):
        raise LofoError("LOFO analysis contract identity differs")
    for source in (
        contract.get("parent_contract"),
        {
            "path": contract.get("dataset_contract", {}).get("builder_path"),
            "sha256": contract.get("dataset_contract", {}).get("builder_sha256"),
        },
        contract.get("predictor", {}).get("prior_distribution_contract"),
        contract.get("predictor", {}).get("prior_runtime_contract"),
    ):
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("path"), str)
            or not isinstance(source.get("sha256"), str)
            or _sha256(ROOT / source["path"]) != source["sha256"]
        ):
            raise LofoError("LOFO source artifact hash differs")
    thresholds = contract.get("completion_distribution", {}).get("thresholds_us")
    predictor = contract.get("predictor", {})
    crossfit = contract.get("cross_fitting", {})
    detector = contract.get("ood_detector", {})
    if (
        not isinstance(thresholds, list)
        or thresholds != sorted(set(thresholds))
        or contract["completion_distribution"].get("class_count")
        != len(thresholds) + 1
        or predictor.get("feature_family") != MODEL_FEATURE_FAMILY
        or predictor.get("encoded_feature_count") != MODEL_FEATURE_COUNT
        or crossfit.get("outer_fold_count")
        != len(crossfit.get("outer_family_order", []))
        or detector.get("raw_context_feature_count") != OOD_CONTEXT_COUNT
        or detector.get("encoded_context_feature_count") != OOD_ENCODED_COUNT
    ):
        raise LofoError("LOFO analysis contract semantics differ")
    return contract


def completion_bin(latency_us: float | None, thresholds_us: Sequence[int]) -> int:
    """Map one optional latency to the frozen ordered completion classes."""

    if latency_us is None:
        return len(thresholds_us)
    if not math.isfinite(latency_us) or latency_us < 0:
        raise LofoError("latency must be finite and nonnegative or absent")
    return int(np.searchsorted(np.asarray(thresholds_us), latency_us, side="left"))


def _optional_number(row: dict[str, str], field: str, context: str) -> float | None:
    if row.get(field, "") == "":
        return None
    try:
        value = float(row[field])
    except (KeyError, ValueError) as error:
        raise LofoError(f"{context}: invalid {field}") from error
    if not math.isfinite(value) or value < 0:
        raise LofoError(f"{context}: invalid {field}")
    return value


def _positive_number(row: dict[str, str], field: str, context: str) -> float:
    try:
        value = float(row[field])
    except (KeyError, ValueError) as error:
        raise LofoError(f"{context}: invalid {field}") from error
    if not math.isfinite(value) or value <= 0:
        raise LofoError(f"{context}: non-positive {field}")
    return value


def _flag(row: dict[str, str], field: str, context: str) -> int:
    value = row.get(field)
    if value not in {"0", "1"}:
        raise LofoError(f"{context}: invalid flag {field}")
    return int(value)


def _integer(row: dict[str, str], field: str, context: str) -> int:
    try:
        value = int(row[field])
    except (KeyError, ValueError) as error:
        raise LofoError(f"{context}: invalid integer {field}") from error
    return value


def _feature_layout() -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    primary_base_columns = tuple(
        name
        for name in base_builder.FEATURE_COLUMNS
        if not name.startswith("x_secondary_")
    )
    numeric_primary_base = tuple(
        name for name in primary_base_columns if name != "x_f0_frame_type"
    )
    primary_base_names = numeric_primary_base + tuple(
        f"x_f0_frame_type={name}" for name in trainer.audited.FRAME_TYPES
    )
    secondary_names = tuple(
        name
        for name in temporal_builder.FEATURE_COLUMNS
        if name.startswith("x_secondary_")
    )
    primary_names = (
        primary_base_names
        + trainer.COMPACT_PRIMARY_PHYSICS_NAMES
        + trainer.PRIMARY_TEMPORAL_COLUMNS
    )
    combined_names = primary_names + secondary_names
    model_names = combined_names + environment_builder.ENVIRONMENT_FEATURE_COLUMNS
    consumed_raw = set(numeric_primary_base) | set(trainer.PRIMARY_TEMPORAL_COLUMNS)
    consumed_raw |= set(secondary_names) | {"x_f0_frame_type"}
    validation_only = tuple(
        name
        for name in temporal_builder.FEATURE_COLUMNS
        if name not in consumed_raw
    )
    if (
        len(primary_base_names) != 68
        or len(primary_names) != 246
        or len(combined_names) != PRIOR_FEATURE_COUNT
        or len(model_names) != MODEL_FEATURE_COUNT
        or len(set(model_names)) != len(model_names)
    ):
        raise LofoError("model feature layout differs")
    return (
        numeric_primary_base,
        primary_base_names,
        secondary_names,
        model_names,
        validation_only,
    )


def _validate_dataset_artifacts(
    path: Path, contract: dict[str, Any], expected_phase: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    metadata_path = path / environment_builder.OUTPUT_METADATA
    manifest_path = path / environment_builder.OUTPUT_MANIFEST
    metadata = _read_json(metadata_path)
    manifest = _read_json(manifest_path)
    if (
        set(manifest)
        != {"manifest_schema_version", "hash_algorithm", "artifacts_sha256"}
        or manifest.get("manifest_schema_version") != 1
        or manifest.get("hash_algorithm") != "sha256"
    ):
        raise LofoError("dataset artifact manifest schema differs")
    hashes = manifest.get("artifacts_sha256")
    expected_artifacts = {
        environment_builder.OUTPUT_CSV,
        environment_builder.OUTPUT_METADATA,
    }
    if not isinstance(hashes, dict) or set(hashes) != expected_artifacts:
        raise LofoError("dataset artifact closure differs")
    for name, expected_hash in hashes.items():
        if not isinstance(expected_hash, str) or _sha256(path / name) != expected_hash:
            raise LofoError(f"dataset artifact hash differs: {name}")
    if (
        metadata.get("dataset_schema_version") != 1
        or metadata.get("feature_contract_id")
        != contract["dataset_contract"]["feature_contract_id"]
        or metadata.get("comparison", {}).get("file")
        != environment_builder.OUTPUT_CSV
        or metadata.get("comparison", {}).get("analysis_stage") != "T2"
        or metadata.get("comparison", {}).get("arms")
        != contract["dataset_contract"]["arms"]
        or metadata.get("non_feature_columns")
        != list(environment_builder.NON_FEATURE_COLUMNS)
        or metadata.get("feature_contract", {}).get("feature_columns")
        != list(environment_builder.FEATURE_COLUMNS)
        or metadata.get("split", {}).get("stored_split_role")
        != contract["dataset_contract"]["stored_split_role"]
        or metadata.get("experiment_identity", {}).get("phase") != expected_phase
        or metadata.get("generalization_contract", {}).get("sha256")
        != contract["parent_contract"]["sha256"]
    ):
        raise LofoError("dataset metadata contract differs")
    builder_hashes = metadata.get("builder_sources_sha256")
    expected_builder_paths = {
        str(source.relative_to(ROOT)): source
        for source in environment_builder.BUILDER_SOURCES
    }
    if not isinstance(builder_hashes, dict) or set(builder_hashes) != set(
        expected_builder_paths
    ):
        raise LofoError("dataset builder source closure differs")
    for name, source in expected_builder_paths.items():
        if builder_hashes[name] != _sha256(source):
            raise LofoError(f"dataset builder source hash differs: {name}")
    if (
        builder_hashes[str(Path(environment_builder.__file__).resolve().relative_to(ROOT))]
        != contract["dataset_contract"]["builder_sha256"]
    ):
        raise LofoError("dataset builder hash differs from LOFO contract")
    source_rows = metadata.get("source_runs")
    if not isinstance(source_rows, list) or not source_rows:
        raise LofoError("dataset has no source runs")
    sources: dict[str, dict[str, Any]] = {}
    for item in source_rows:
        if not isinstance(item, dict):
            raise LofoError("dataset source run is invalid")
        run_id = item.get("run_id")
        if (
            not isinstance(run_id, str)
            or not run_id
            or run_id in sources
            or not isinstance(item.get("scenario_id"), str)
            or not isinstance(item.get("family_id"), str)
            or not isinstance(item.get("seed"), int)
            or not isinstance(item.get("run_number"), int)
            or not isinstance(item.get("parameter_sample"), int)
        ):
            raise LofoError("dataset source identity differs")
        sources[run_id] = item

    parent = _read_json(ROOT / contract["parent_contract"]["path"])
    phase_contract = parent["sampling"]["phases"][expected_phase]
    expected_runs = phase_contract["expected_run_count"]
    expected_scenarios = (
        len(parent["sampling"]["family_order"])
        * phase_contract["parameter_samples_per_family"]
    )
    replicates = phase_contract["replicates_per_parameter_sample"]
    scenario_counts = Counter(item["scenario_id"] for item in sources.values())
    if (
        len(sources) != expected_runs
        or len(scenario_counts) != expected_scenarios
        or set(scenario_counts.values()) != {replicates}
        or set(item["family_id"] for item in sources.values())
        != set(parent["sampling"]["family_order"])
    ):
        raise LofoError("dataset scenario/run coverage differs")
    return metadata, manifest, sources


def load_dataset(
    dataset_dir: Path | str, *, expected_phase: str = "randomized_collection"
) -> LofoDataset:
    """Stream a source-closed generalization dataset into bounded arrays."""

    contract = load_contract()
    path = Path(dataset_dir).resolve()
    metadata, artifact_manifest, sources = _validate_dataset_artifacts(
        path, contract, expected_phase
    )
    row_count = metadata["comparison"].get("row_count")
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count <= 0:
        raise LofoError("dataset row count is invalid")
    (
        numeric_primary_base,
        primary_base_names,
        secondary_names,
        model_names,
        validation_only,
    ) = _feature_layout()
    model = np.empty((row_count, len(model_names)), dtype=np.float32)
    context_names = tuple(
        _read_json(ROOT / contract["parent_contract"]["path"])["model_evaluation"][
            "observable_environment_features"
        ]
    )
    if len(context_names) != OOD_CONTEXT_COUNT or not set(context_names) <= set(
        environment_builder.FEATURE_COLUMNS
    ):
        raise LofoError("OOD context feature closure differs")
    ood_context = np.empty((row_count, len(context_names)), dtype=float)
    run_ids: list[str] = []
    scenario_ids: list[str] = []
    family_ids: list[str] = []
    frame_types: list[str] = []
    reservation_texts: list[str] = []
    seeds = np.empty(row_count, dtype=np.int32)
    run_numbers = np.empty(row_count, dtype=np.int16)
    frame_ids = np.empty(row_count, dtype=np.int32)
    parameter_samples = np.empty(row_count, dtype=np.int16)
    treatment = np.empty(row_count, dtype=np.int8)
    propensity = np.empty(row_count, dtype=float)
    outcome_bins = np.empty(row_count, dtype=np.int8)
    union_latencies = np.full(row_count, np.nan, dtype=float)
    primary_latencies = np.full(row_count, np.nan, dtype=float)
    deadlines = np.empty(row_count, dtype=np.int32)
    deadline_indices = np.empty(row_count, dtype=np.int8)
    deadline_miss = np.empty(row_count, dtype=np.int8)
    primary_deadline_miss = np.empty(row_count, dtype=np.int8)
    completed_late18 = np.empty(row_count, dtype=np.int8)
    reservations = np.empty(row_count, dtype=float)
    thresholds = contract["completion_distribution"]["thresholds_us"]
    threshold_indices = {value: index for index, value in enumerate(thresholds)}
    expected_treatment_probability = contract["dataset_contract"]["propensity"][
        "conditional_t2_probability"
    ]
    control_probability = contract["dataset_contract"]["propensity"][
        "control_assignment_probability"
    ]
    t2_probability = contract["dataset_contract"]["propensity"][
        "t2_assignment_probability"
    ]
    seen: set[tuple[int, int, int]] = set()
    prior_key: tuple[int, int, int] | None = None
    family_rows: Counter[str] = Counter()
    scenario_rows: Counter[str] = Counter()
    arms_seen: set[str] = set()
    csv_path = path / environment_builder.OUTPUT_CSV
    try:
        with csv_path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or tuple(reader.fieldnames) != tuple(
                environment_builder.DATASET_COLUMNS
            ):
                raise LofoError("generalization CSV exact schema differs")
            for index, row in enumerate(reader):
                if index >= row_count:
                    raise LofoError("generalization CSV has extra rows")
                source = f"generalization CSV line {index + 2}"
                if None in row or any(value is None for value in row.values()):
                    raise LofoError(f"{source}: malformed row")
                run_id = row.get("run_id", "")
                source_run = sources.get(run_id)
                seed = _integer(row, "seed", source)
                run_number = _integer(row, "run_number", source)
                frame_id = _integer(row, "frame_id", source)
                scenario_id = row.get("scenario_id", "")
                family_id = row.get("family_id", "")
                parameter_sample = _integer(row, "parameter_sample", source)
                key = (seed, run_number, frame_id)
                arm = row.get("assigned_arm", "")
                assigned_treatment = _flag(row, "treatment", source)
                attempted = _flag(row, "attempted", source)
                launched = _flag(row, "launched", source)
                noncompliance = _flag(row, "noncompliance", source)
                if (
                    source_run is None
                    or source_run["seed"] != seed
                    or source_run["run_number"] != run_number
                    or source_run["scenario_id"] != scenario_id
                    or source_run["family_id"] != family_id
                    or source_run["parameter_sample"] != parameter_sample
                    or row.get("dataset_schema_version") != "1"
                    or row.get("split_role")
                    != contract["dataset_contract"]["stored_split_role"]
                    or row.get("analysis_stage") != "T2"
                    or arm not in {"CONTROL", "FULL_COPY_T2"}
                    or assigned_treatment != int(arm == "FULL_COPY_T2")
                    or _flag(row, "eligible_t2", source) != 1
                    or _flag(row, "decision_primary_actionable", source) != 1
                    or frame_id < max(temporal_builder.LAGS)
                    or not (launched <= attempted <= assigned_treatment)
                    or noncompliance != int(assigned_treatment == 1 and launched == 0)
                    or key in seen
                    or (prior_key is not None and key <= prior_key)
                ):
                    raise LofoError(f"{source}: identity or execution differs")
                row_propensity = _positive_number(
                    row, "treatment_probability", source
                )
                assigned_probability = _positive_number(
                    row, "assigned_arm_probability", source
                )
                if not math.isclose(
                    row_propensity,
                    expected_treatment_probability,
                    rel_tol=0.0,
                    abs_tol=1e-15,
                ) or not math.isclose(
                    assigned_probability,
                    t2_probability if assigned_treatment else control_probability,
                    rel_tol=0.0,
                    abs_tol=1e-15,
                ):
                    raise LofoError(f"{source}: propensity differs")

                deadline = _integer(row, "env_deadline_us", source)
                if deadline not in threshold_indices:
                    raise LofoError(f"{source}: deadline is absent from frozen thresholds")
                try:
                    outcomes = trainer._validate_outcomes(row, source, float(deadline))
                except trainer.TemporalTrainingError as error:
                    raise LofoError(str(error)) from error
                latency = _optional_number(row, "outcome_union_latency_us", source)
                primary_latency = _optional_number(
                    row, "outcome_primary_latency_us", source
                )
                if launched == 0 and outcomes["measured_airtime_us"] != 0:
                    raise LofoError(f"{source}: unlaunched airtime differs")
                frame_type = row.get("x_f0_frame_type", "")
                if frame_type not in trainer.audited.FRAME_TYPES:
                    raise LofoError(f"{source}: frame type differs")

                for output_index, name in enumerate(numeric_primary_base):
                    model[index, output_index] = trainer._float32_numeric(
                        row[name], name, source
                    )
                one_hot_offset = len(numeric_primary_base)
                for category_index, category in enumerate(trainer.audited.FRAME_TYPES):
                    model[index, one_hot_offset + category_index] = float(
                        frame_type == category
                    )
                temporal_offset = len(primary_base_names) + len(
                    trainer.COMPACT_PRIMARY_PHYSICS_NAMES
                )
                for temporal_index, name in enumerate(trainer.PRIMARY_TEMPORAL_COLUMNS):
                    model[index, temporal_offset + temporal_index] = (
                        trainer._float32_numeric(row[name], name, source)
                    )
                secondary_offset = 246
                for secondary_index, name in enumerate(secondary_names):
                    model[index, secondary_offset + secondary_index] = (
                        trainer._float32_numeric(row[name], name, source)
                    )
                environment_offset = PRIOR_FEATURE_COUNT
                for environment_index, name in enumerate(
                    environment_builder.ENVIRONMENT_FEATURE_COLUMNS
                ):
                    model[index, environment_offset + environment_index] = (
                        trainer._float32_numeric(row[name], name, source)
                    )
                for name in validation_only:
                    trainer._float32_numeric(row[name], name, source)
                for context_index, name in enumerate(context_names):
                    raw = row[name]
                    ood_context[index, context_index] = (
                        math.nan
                        if raw == ""
                        else trainer._float32_numeric(raw, name, source)
                    )

                run_ids.append(run_id)
                seeds[index] = seed
                run_numbers[index] = run_number
                frame_ids[index] = frame_id
                scenario_ids.append(scenario_id)
                family_ids.append(family_id)
                parameter_samples[index] = parameter_sample
                treatment[index] = assigned_treatment
                propensity[index] = row_propensity
                outcome_bins[index] = completion_bin(latency, thresholds)
                if latency is not None:
                    union_latencies[index] = latency
                if primary_latency is not None:
                    primary_latencies[index] = primary_latency
                deadlines[index] = deadline
                deadline_indices[index] = threshold_indices[deadline]
                deadline_miss[index] = int(outcomes[trainer.TARGET_DEADLINE])
                primary_deadline_miss[index] = int(
                    outcomes[f"primary_bad_{trainer.TARGET_DEADLINE}"]
                )
                completed_late18[index] = int(outcomes[trainer.TARGET_LATE18])
                reservation_text = row.get("action_estimated_airtime_us", "")
                reservations[index] = _positive_number(
                    row, "action_estimated_airtime_us", source
                )
                reservation_texts.append(reservation_text)
                frame_types.append(frame_type)
                seen.add(key)
                prior_key = key
                family_rows[family_id] += 1
                scenario_rows[scenario_id] += 1
                arms_seen.add(arm)
    except OSError as error:
        raise LofoError(f"cannot read {csv_path}: {error}") from error
    if len(run_ids) != row_count or arms_seen != {"CONTROL", "FULL_COPY_T2"}:
        raise LofoError("generalization CSV row/arm coverage differs")
    if dict(sorted(family_rows.items())) != metadata["scenario_contract"][
        "family_row_counts"
    ] or dict(sorted(scenario_rows.items())) != metadata["scenario_contract"][
        "scenario_row_counts"
    ]:
        raise LofoError("generalization CSV scenario counts differ from metadata")

    compact_start = len(primary_base_names)
    compact_stop = compact_start + len(trainer.COMPACT_PRIMARY_PHYSICS_NAMES)
    primary_base_wide = model[:, : len(primary_base_names)].astype(float)
    model[:, compact_start:compact_stop] = trainer._compact_primary_physics(
        primary_base_wide, primary_base_names
    )
    del primary_base_wide
    if np.any(np.isinf(model)):
        raise LofoError("model feature matrix overflows float32")
    return LofoDataset(
        path=path,
        metadata=metadata,
        artifact_manifest=artifact_manifest,
        contract=contract,
        model_matrix=model,
        model_feature_names=model_names,
        ood_context=ood_context,
        ood_context_names=context_names,
        run_ids=tuple(run_ids),
        seeds=seeds,
        run_numbers=run_numbers,
        frame_ids=frame_ids,
        scenario_ids=tuple(scenario_ids),
        family_ids=tuple(family_ids),
        parameter_samples=parameter_samples,
        treatment=treatment,
        propensity=propensity,
        outcome_bins=outcome_bins,
        union_latencies_us=union_latencies,
        primary_latencies_us=primary_latencies,
        deadline_us=deadlines,
        deadline_threshold_indices=deadline_indices,
        deadline_miss=deadline_miss,
        primary_deadline_miss=primary_deadline_miss,
        completed_late18=completed_late18,
        canonical_reservation_us=reservations,
        canonical_reservation_texts=tuple(reservation_texts),
        frame_types=tuple(frame_types),
    )


def assign_inner_scenario_folds(
    scenario_families: dict[str, str],
    training_families: Sequence[str],
    fold_count: int,
    salt: str,
) -> dict[str, int]:
    """Assign whole scenarios to balanced folds within every training family."""

    if fold_count <= 1 or not salt or len(set(training_families)) != len(training_families):
        raise LofoError("invalid inner-fold inputs")
    unknown = set(scenario_families.values()) - set(training_families)
    if unknown:
        raise LofoError("scenario mapping contains a non-training family")
    assignment: dict[str, int] = {}
    for family in training_families:
        scenarios = [
            scenario for scenario, found_family in scenario_families.items()
            if found_family == family
        ]
        if not scenarios or len(scenarios) % fold_count:
            raise LofoError(f"{family}: scenarios cannot balance across inner folds")

        def key(scenario: str) -> tuple[bytes, str]:
            return hashlib.sha256(f"{salt}|{scenario}".encode("ascii")).digest(), scenario

        for ordinal, scenario in enumerate(sorted(scenarios, key=key)):
            assignment[scenario] = ordinal % fold_count
    if set(assignment) != set(scenario_families):
        raise LofoError("inner-fold assignment is incomplete")
    for family in training_families:
        sizes = Counter(
            assignment[scenario]
            for scenario, found_family in scenario_families.items()
            if found_family == family
        )
        if set(sizes) != set(range(fold_count)) or len(set(sizes.values())) != 1:
            raise LofoError(f"{family}: inner-fold sizes differ")
    return assignment


def _context_matrix(context: np.ndarray, feature_count: int) -> np.ndarray:
    matrix = np.asarray(context, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] != feature_count:
        raise LofoError("OOD context matrix shape differs")
    if np.any(np.isinf(matrix)):
        raise LofoError("OOD context contains infinite values")
    return matrix


def fit_ood_model(
    context: np.ndarray,
    context_names: Sequence[str],
    optional_missing_features: Sequence[str],
    shrinkage: float,
    eigenvalue_floor: float,
) -> RobustOodModel:
    """Fit the frozen robust shrinkage-Mahalanobis detector."""

    names = tuple(context_names)
    matrix = _context_matrix(context, len(names))
    if len(matrix) < 2 or len(set(names)) != len(names):
        raise LofoError("OOD fit data or names are invalid")
    optional = tuple(names.index(name) for name in optional_missing_features)
    allowed = np.zeros(len(names), dtype=bool)
    allowed[list(optional)] = True
    if np.any(np.isnan(matrix[:, ~allowed])):
        raise LofoError("OOD training context misses a required feature")
    if not 0 < shrinkage <= 1 or eigenvalue_floor <= 0:
        raise LofoError("OOD covariance parameters are invalid")
    with np.errstate(all="ignore"):
        medians = np.nanmedian(matrix, axis=0)
    if not np.all(np.isfinite(medians)):
        raise LofoError("OOD context feature is entirely missing")
    missing = np.isnan(matrix)
    imputed = np.where(missing, medians, matrix)
    encoded = np.concatenate((imputed, missing.astype(float)), axis=1)
    center = np.median(encoded, axis=0)
    scale = np.median(np.abs(encoded - center), axis=0)
    scale = np.where(scale > 0, scale, 1.0)
    scaled = (encoded - center) / scale
    covariance = np.cov(scaled, rowvar=False, bias=False)
    if covariance.shape != (2 * len(names), 2 * len(names)) or not np.all(
        np.isfinite(covariance)
    ):
        raise LofoError("OOD covariance is invalid")
    shrunk = (1.0 - shrinkage) * covariance + shrinkage * np.eye(len(covariance))
    eigenvalues, eigenvectors = np.linalg.eigh(shrunk)
    eigenvalues = np.maximum(eigenvalues, eigenvalue_floor)
    inverse = (eigenvectors / eigenvalues) @ eigenvectors.T
    application_count = len(environment_builder.ENVIRONMENT_FEATURE_COLUMNS)
    application = matrix[:, :application_count]
    if np.any(~np.isfinite(application)):
        raise LofoError("application context cannot be missing during OOD fit")
    return RobustOodModel(
        context_names=names,
        optional_missing_indices=optional,
        medians=medians,
        center=center,
        scale=scale,
        inverse_covariance=inverse,
        application_minimum=np.min(application, axis=0),
        application_maximum=np.max(application, axis=0),
    )


def calibrate_ood_threshold(
    context: np.ndarray,
    scenario_ids: Sequence[str],
    scenario_families: dict[str, str],
    training_families: Sequence[str],
    assignment: dict[str, int],
    context_names: Sequence[str],
    optional_missing_features: Sequence[str],
    shrinkage: float,
    eigenvalue_floor: float,
    quantile: float,
) -> tuple[float, np.ndarray]:
    """Return group-OOF context scores and the frozen higher quantile."""

    matrix = _context_matrix(context, len(context_names))
    scenarios = np.asarray(scenario_ids, dtype=object)
    if len(scenarios) != len(matrix) or not 0 < quantile < 1:
        raise LofoError("OOD calibration inputs are invalid")
    folds = set(assignment.values())
    if folds != set(range(len(folds))):
        raise LofoError("OOD calibration folds are not contiguous")
    scores = np.full(len(matrix), np.nan, dtype=float)
    training_family_set = set(training_families)
    eligible = np.asarray(
        [scenario_families.get(str(scenario)) in training_family_set for scenario in scenarios]
    )
    for fold in sorted(folds):
        validation = np.asarray(
            [assignment.get(str(scenario)) == fold for scenario in scenarios]
        ) & eligible
        fit = np.asarray(
            [assignment.get(str(scenario)) != fold for scenario in scenarios]
        ) & eligible
        if not np.any(validation) or not np.any(fit):
            raise LofoError("OOD calibration fold is empty")
        model = fit_ood_model(
            matrix[fit],
            context_names,
            optional_missing_features,
            shrinkage,
            eigenvalue_floor,
        )
        scores[validation] = model.scores(matrix[validation])
    if not np.all(np.isfinite(scores[eligible])) or np.any(np.isfinite(scores[~eligible])):
        raise LofoError("OOD OOF score coverage differs")
    threshold = float(np.quantile(scores[eligible], quantile, method="higher"))
    if not math.isfinite(threshold) or threshold < 0:
        raise LofoError("OOD threshold is invalid")
    return threshold, scores
