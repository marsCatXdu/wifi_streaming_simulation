#!/usr/bin/env python3
"""Evaluate removing the learned cost divisor from frozen temporal T2 heads.

This is a retrospective engineering ablation.  It verifies and reuses the
existing primary-only temporal model bundle without fitting any model.  The
bounded cost-free policy set is selected on the original calibration runs and
is evaluated once on the already-opened engineering test runs.  Fresh seeds
remain untouched and are not confirmation evidence.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import pickle
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import train_temporal_t2_value as trainer


ABLATION_SCHEMA_VERSION = 1
ABLATION_ID = "frozen_temporal_heads_cost_denominator_ablation_v1"
OUTPUT_CANDIDATES = "temporal_t2_cost_ablation_candidates.csv"
OUTPUT_METRICS = "temporal_t2_cost_ablation_metrics.json"
OUTPUT_REPORT = "temporal_t2_cost_ablation.md"
OUTPUT_FIGURE = "temporal_t2_cost_ablation.png"
OUTPUT_MANIFEST = "artifact_manifest.json"
OUTPUT_FILES = (
    OUTPUT_CANDIDATES,
    OUTPUT_METRICS,
    OUTPUT_REPORT,
    OUTPUT_FIGURE,
    OUTPUT_MANIFEST,
)

RAW_RANKERS = (
    "deadline_value",
    "completed_late18_value",
    "balanced_normalized_value",
    "legacy_bad12_value",
)
RANKER_ORDER = tuple(
    ranker
    for raw in RAW_RANKERS
    for ranker in (f"{raw}_per_cost", raw)
)


class CostAblationError(RuntimeError):
    """Raised when the frozen ablation contract cannot be established."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise CostAblationError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CostAblationError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise CostAblationError(f"{path}: expected a JSON object")
    return value


def _load_frozen_bundle(
    model_dir: Path, dataset: trainer.TemporalDataset
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Verify all source artifacts before loading the local pickle bundle."""

    model_dir = model_dir.resolve()
    manifest_path = model_dir / trainer.OUTPUT_MANIFEST
    manifest = _read_json(manifest_path)
    if set(manifest) != {
        "manifest_schema_version",
        "hash_algorithm",
        "artifacts_sha256",
        "selected_policy_contract",
    }:
        raise CostAblationError("source model manifest schema differs")
    if (
        manifest.get("manifest_schema_version") != 1
        or manifest.get("hash_algorithm") != "sha256"
    ):
        raise CostAblationError("source model manifest identity differs")
    hashes = manifest.get("artifacts_sha256")
    expected_artifacts = {
        trainer.OUTPUT_MODEL,
        trainer.OUTPUT_CANDIDATES,
        trainer.OUTPUT_METRICS,
    }
    if not isinstance(hashes, dict) or set(hashes) != expected_artifacts:
        raise CostAblationError("source model manifest closure differs")
    for name, expected in hashes.items():
        if not isinstance(expected, str) or len(expected) != 64:
            raise CostAblationError(f"source model digest is malformed: {name}")
        if _sha256(model_dir / name) != expected:
            raise CostAblationError(f"source model artifact hash differs: {name}")

    metrics = _read_json(model_dir / trainer.OUTPUT_METRICS)
    if metrics.get("dataset_artifacts_sha256") != dataset.manifest[
        "artifacts_sha256"
    ]:
        raise CostAblationError("source model and temporal dataset hashes differ")
    if metrics.get("model_spec_id") != trainer.MODEL_SPEC_ID:
        raise CostAblationError("source model specification differs")

    try:
        with (model_dir / trainer.OUTPUT_MODEL).open("rb") as source:
            bundle = pickle.load(source)
    except (OSError, pickle.UnpicklingError, EOFError) as error:
        raise CostAblationError(f"cannot load verified source model: {error}") from error
    if not isinstance(bundle, dict):
        raise CostAblationError("source model bundle is not a dictionary")
    if (
        bundle.get("model_bundle_schema_version")
        != trainer.MODEL_BUNDLE_SCHEMA_VERSION
        or bundle.get("training_schema_version") != trainer.TRAINING_SCHEMA_VERSION
        or bundle.get("feature_contract_id")
        != trainer.temporal_builder.FEATURE_CONTRACT_ID
        or bundle.get("model_spec_id") != trainer.MODEL_SPEC_ID
        or bundle.get("selection_id") != trainer.SELECTION_ID
    ):
        raise CostAblationError("source model bundle identity differs")

    families = bundle.get("feature_families")
    if not isinstance(families, dict) or tuple(families) != trainer.FEATURE_FAMILY_ORDER:
        raise CostAblationError("source model feature-family order differs")
    for family in trainer.FEATURE_FAMILY_ORDER:
        value = families.get(family)
        if not isinstance(value, dict):
            raise CostAblationError(f"source feature family is malformed: {family}")
        names = tuple(value.get("ordered_feature_names", ()))
        if names != dataset.family_feature_names[family]:
            raise CostAblationError(f"source feature order differs: {family}")
        if value.get("contains_secondary_feature") is not False:
            raise CostAblationError(
                f"source family unexpectedly contains secondary state: {family}"
            )
        heads = value.get("heads")
        required_heads = {
            f"{target}:primary_need" for target in trainer.VALUE_TARGETS
        } | {
            f"{target}:treated_bad" for target in trainer.VALUE_TARGETS
        } | {
            "log_cost_given_launch",
            "log_cost_smearing_factor",
            "training_risk_normalizers",
        }
        if not isinstance(heads, dict) or set(heads) != required_heads:
            raise CostAblationError(f"source value-head closure differs: {family}")

    selected = bundle.get("selected_policy")
    contract = manifest.get("selected_policy_contract")
    if not isinstance(selected, dict) or not isinstance(contract, dict):
        raise CostAblationError("source selected-policy contract is absent")
    expected_contract = {
        "feature_family": selected.get("feature_family"),
        "ordered_feature_names": list(selected.get("ordered_feature_names", ())),
        "ranker": selected.get("ranker"),
        "frame_gate": selected.get("frame_gate"),
        "score_threshold_float32": selected.get("score_threshold"),
        "feature_adapter_id": trainer.FEATURE_ADAPTER_ID,
        "score_adapter_id": trainer.SCORE_ADAPTER_ID,
    }
    if contract != expected_contract:
        raise CostAblationError("source selected policy differs from its manifest")
    if selected.get("ranker") not in trainer.RANKER_ORDER:
        raise CostAblationError("source selected ranker is outside the frozen V1 set")
    if bundle.get("evaluation_nuisances") is None:
        raise CostAblationError("source evaluation nuisances are absent")
    return bundle, metrics, manifest


def _candidate_scores(heads: dict[str, Any], x: np.ndarray) -> dict[str, np.ndarray]:
    """Return paired cost-normalized and raw scores from identical frozen heads."""

    deltas: dict[str, np.ndarray] = {}
    for target in trainer.VALUE_TARGETS:
        need = trainer._probability(heads[f"{target}:primary_need"], x)
        treated_bad = trainer._probability(heads[f"{target}:treated_bad"], x)
        deltas[target] = np.maximum(need - treated_bad, 0.0)
    normalizers = heads["training_risk_normalizers"]
    raw = {
        "deadline_value": deltas[trainer.TARGET_DEADLINE],
        "completed_late18_value": deltas[trainer.TARGET_LATE18],
        "balanced_normalized_value": 0.5
        * (
            deltas[trainer.TARGET_DEADLINE]
            / normalizers[trainer.TARGET_DEADLINE]["normalizer"]
            + deltas[trainer.TARGET_LATE18]
            / normalizers[trainer.TARGET_LATE18]["normalizer"]
        ),
        "legacy_bad12_value": deltas[trainer.TARGET_BAD12],
    }
    result = trainer._candidate_scores(heads, x)
    result.update(
        {
            name: trainer.audited._quantize_candidate_scores(values)
            for name, values in raw.items()
        }
    )
    if tuple(result) != tuple(trainer.RANKER_ORDER) + RAW_RANKERS:
        raise CostAblationError("paired ranker score closure differs")
    return {name: result[name] for name in RANKER_ORDER}


def _source_record(
    records: Sequence[dict[str, Any]], selected: dict[str, Any]
) -> dict[str, Any]:
    matches = [
        record
        for record in records
        if record["feature_family"] == selected.get("feature_family")
        and record["ranker"] == selected.get("ranker")
        and record["frame_gate"] == selected.get("frame_gate")
        and math.isclose(
            record["requested_action_fraction"],
            float(selected.get("requested_action_fraction")),
            rel_tol=0.0,
            abs_tol=1e-15,
        )
    ]
    if len(matches) != 1:
        raise CostAblationError("cannot resolve the frozen source candidate")
    record = matches[0]
    if record["score_threshold"] != selected.get("score_threshold"):
        raise CostAblationError("recomputed source score threshold differs")
    return record


def _verify_metric_projection(
    recomputed: dict[str, Any], stored: dict[str, Any], context: str
) -> None:
    """Verify every common scalar field from the frozen evidence record."""

    # Candidate ordinals differ because this paired grid interleaves four new
    # cost-free rankers with the original four rankers.
    ignored = {
        "candidate_ordinal",
        "run_cluster_uncertainty",
        "frozen_float32_score_threshold",
    }
    for name, expected in stored.items():
        if name in ignored or name not in recomputed:
            continue
        actual = recomputed[name]
        if isinstance(expected, bool) or isinstance(actual, bool):
            equal = actual == expected
        elif isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            equal = math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-12)
        else:
            equal = actual == expected
        if not equal:
            raise CostAblationError(f"{context}: stored metric differs: {name}")


def _policy_on_role(
    dataset: trainer.TemporalDataset,
    bundle: dict[str, Any],
    indices: np.ndarray,
    policy_contract: dict[str, Any],
    components: dict[str, tuple[np.ndarray, np.ndarray]],
    cost_phi1: np.ndarray,
) -> tuple[np.ndarray, dict[str, float]]:
    family = policy_contract["feature_family"]
    ranker = policy_contract["ranker"]
    family_data = dataset.stage_for_family(family)
    scores = _candidate_scores(
        bundle["feature_families"][family]["heads"],
        family_data.matrix[indices],
    )[ranker]
    policy = trainer._apply_threshold(
        scores,
        trainer._frame_gate(dataset.data, indices, policy_contract["frame_gate"]),
        policy_contract["score_threshold"],
    )
    metrics = trainer._policy_metrics(policy, components, cost_phi1)
    trainer._require_feasible_policy_metrics(metrics, f"{ranker} policy")
    return policy, metrics


def _record_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "selected"}


def evaluate_cost_ablation(
    dataset: trainer.TemporalDataset,
    model_dir: Path | str,
    *,
    random_seed: int = trainer.RANDOM_SEED,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the calibration-only selection and one engineering-test evaluation."""

    model_path = Path(model_dir).resolve()
    bundle, source_metrics, source_manifest = _load_frozen_bundle(
        model_path, dataset
    )
    base_data = dataset.data
    calibration = base_data.indices("calibration")
    test = base_data.indices("test")
    if len(calibration) == 0 or len(test) == 0:
        raise CostAblationError("calibration or engineering-test role is empty")

    preferred_data = dataset.stage_for_family("primary_compact_physics_temporal")
    nuisances = bundle["evaluation_nuisances"]
    calibration_components, calibration_cost = trainer._evaluation_components(
        preferred_data, calibration, nuisances
    )
    calibration_scores: dict[tuple[str, str], np.ndarray] = {}
    for family in trainer.FEATURE_FAMILY_ORDER:
        values = _candidate_scores(
            bundle["feature_families"][family]["heads"],
            dataset.family_matrices[family][calibration],
        )
        for ranker in RANKER_ORDER:
            calibration_scores[(family, ranker)] = values[ranker]

    records: list[dict[str, Any]] = []
    ordinal = 0
    for family in trainer.FEATURE_FAMILY_ORDER:
        for ranker in RANKER_ORDER:
            scores = calibration_scores[(family, ranker)]
            for gate_name in trainer.FRAME_GATES:
                gate = trainer._frame_gate(base_data, calibration, gate_name)
                for fraction in trainer.REQUESTED_ACTION_FRACTIONS:
                    threshold = trainer._threshold_for_global_fraction(
                        scores, gate, fraction
                    )
                    policy = trainer._apply_threshold(scores, gate, threshold)
                    record = trainer._candidate_record(
                        ordinal=ordinal,
                        family=family,
                        ranker=ranker,
                        gate_name=gate_name,
                        fraction=fraction,
                        threshold=threshold,
                        policy=policy,
                        components=calibration_components,
                        cost_phi1=calibration_cost,
                    )
                    record["signal"] = ranker.removesuffix("_per_cost")
                    record["cost_normalized"] = int(ranker.endswith("_per_cost"))
                    record["source_frozen"] = 0
                    records.append(record)
                    ordinal += 1

    source_contract = bundle["selected_policy"]
    source_record = _source_record(records, source_contract)
    source_record["source_frozen"] = 1
    _verify_metric_projection(
        source_record,
        source_metrics["selected_calibration_policy"],
        "frozen calibration policy",
    )

    raw_records = [record for record in records if not record["cost_normalized"]]
    selected = trainer._select(raw_records)
    selected_contract = {
        "feature_family": selected["feature_family"],
        "ranker": selected["ranker"],
        "frame_gate": selected["frame_gate"],
        "score_threshold": selected["score_threshold"],
        "requested_action_fraction": selected["requested_action_fraction"],
    }

    selected_calibration_policy = trainer._apply_threshold(
        calibration_scores[(selected["feature_family"], selected["ranker"])],
        trainer._frame_gate(base_data, calibration, selected["frame_gate"]),
        selected["score_threshold"],
    )
    selected_calibration_uncertainty = trainer._cluster_uncertainty(
        base_data,
        calibration,
        selected_calibration_policy,
        calibration_components,
        calibration_cost,
        seed=random_seed,
        context="temporal-t2-cost-ablation:calibration",
    )

    # This branch starts only after the cost-free calibration winner is frozen.
    test_components, test_cost = trainer._evaluation_components(
        preferred_data, test, nuisances
    )
    source_test_policy, source_test = _policy_on_role(
        dataset,
        bundle,
        test,
        source_contract,
        test_components,
        test_cost,
    )
    _verify_metric_projection(
        source_test,
        source_metrics["engineering_test_policy"],
        "frozen engineering-test policy",
    )
    selected_test_policy, selected_test = _policy_on_role(
        dataset,
        bundle,
        test,
        selected_contract,
        test_components,
        test_cost,
    )
    selected_test["run_cluster_uncertainty"] = trainer._cluster_uncertainty(
        base_data,
        test,
        selected_test_policy,
        test_components,
        test_cost,
        seed=random_seed,
        context="temporal-t2-cost-ablation:test",
    )
    selected_test.update(
        {
            "row_count": len(test),
            "run_count": len({base_data.rows[index].group for index in test}),
            "frozen_feature_family": selected["feature_family"],
            "frozen_ranker": selected["ranker"],
            "frozen_frame_gate": selected["frame_gate"],
            "frozen_float32_score_threshold": selected["score_threshold"],
        }
    )
    source_test_summary = dict(source_test)
    source_test_summary.update(
        {
            "row_count": len(test),
            "run_count": len({base_data.rows[index].group for index in test}),
            "frozen_feature_family": source_contract["feature_family"],
            "frozen_ranker": source_contract["ranker"],
            "frozen_frame_gate": source_contract["frame_gate"],
            "frozen_float32_score_threshold": source_contract["score_threshold"],
            "run_cluster_uncertainty": source_metrics["engineering_test_policy"][
                "run_cluster_uncertainty"
            ],
        }
    )

    selected_summary = _record_summary(selected)
    selected_summary["run_cluster_uncertainty"] = selected_calibration_uncertainty
    result = {
        "ablation_schema_version": ABLATION_SCHEMA_VERSION,
        "ablation_id": ABLATION_ID,
        "evidence_status": (
            "retrospective_previously_opened_engineering_evidence_not_confirmation"
        ),
        "decisive_confirmation": "fresh closed-loop seeds 1301+ remain untouched",
        "scientific_question": (
            "Does removing the learned per-frame secondary-airtime divisor improve "
            "ranking when all fitted outcome heads and evaluation nuisances are fixed?"
        ),
        "model_refit": False,
        "test_role_used_during_selection": False,
        "selection_role": "calibration",
        "selection_pool": "cost-free rankers only",
        "candidate_count": len(records),
        "cost_free_candidate_count": len(raw_records),
        "ranker_order": list(RANKER_ORDER),
        "feature_family_order": list(trainer.FEATURE_FAMILY_ORDER),
        "frame_gate_order": list(trainer.FRAME_GATES),
        "requested_action_fractions": list(trainer.REQUESTED_ACTION_FRACTIONS),
        "dataset_dir": str(dataset.path),
        "dataset_artifacts_sha256": dataset.manifest["artifacts_sha256"],
        "source_model_dir": str(model_path),
        "source_model_artifacts_sha256": source_manifest["artifacts_sha256"],
        "source_model_manifest_sha256": _sha256(
            model_path / trainer.OUTPUT_MANIFEST
        ),
        "source_frozen_calibration_policy": _record_summary(source_record),
        "selected_cost_free_calibration_policy": selected_summary,
        "source_frozen_engineering_test_policy": source_test_summary,
        "selected_cost_free_engineering_test_policy": selected_test,
        "calibration_balanced_min_improvement_delta": (
            selected["balanced_min_relative_improvement"]
            - source_record["balanced_min_relative_improvement"]
        ),
        "engineering_test_deltas": {
            "deadline_miss_probability": (
                selected_test["dr_policy_deadline_miss"]
                - source_test["dr_policy_deadline_miss"]
            ),
            "completed_late18_ratio": (
                selected_test["dr_policy_completed_late18_ratio"]
                - source_test["dr_policy_completed_late18_ratio"]
            ),
            "airtime_us_per_eligible_frame": (
                selected_test["dr_airtime_us_per_eligible_frame"]
                - source_test["dr_airtime_us_per_eligible_frame"]
            ),
            "action_fraction": (
                selected_test["realized_action_fraction"]
                - source_test["realized_action_fraction"]
            ),
        },
        "interpretation_guardrails": [
            "The engineering test split was already opened by V1 and is descriptive.",
            "This result isolates score ranking; it does not validate a runtime guard.",
            "No secondary-link feature is introduced by this ablation.",
            "Fresh seeds 1301+ are not read or consumed.",
        ],
        "random_seed": random_seed,
    }
    return result, records


CANDIDATE_FIELDS = (
    "candidate_ordinal",
    "feature_family",
    "signal",
    "ranker",
    "cost_normalized",
    "frame_gate",
    "requested_action_fraction",
    "score_threshold",
    "realized_action_fraction",
    "dr_airtime_us_per_eligible_frame",
    "dr_treat_none_deadline_miss",
    "dr_policy_deadline_miss",
    "dr_deadline_miss_benefit",
    "deadline_miss_relative_improvement",
    "dr_treat_none_completed_late18_numerator",
    "dr_policy_completed_late18_numerator",
    "dr_treat_none_completion_probability",
    "dr_policy_completion_probability",
    "dr_treat_none_completed_late18_ratio",
    "dr_policy_completed_late18_ratio",
    "completed_late18_relative_improvement",
    "dr_treat_none_bad12",
    "dr_policy_bad12",
    "dr_bad12_benefit",
    "balanced_min_relative_improvement",
    "mean_relative_improvement",
    "admissible",
    "rejection_reason",
    "source_frozen",
    "selected",
)


def _write_candidates(path: Path, records: Sequence[dict[str, Any]]) -> None:
    try:
        with path.open("w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(
                destination, fieldnames=CANDIDATE_FIELDS, lineterminator="\n"
            )
            writer.writeheader()
            for record in records:
                writer.writerow(
                    {
                        field: (
                            format(record[field], ".17g")
                            if isinstance(record[field], float)
                            else record[field]
                        )
                        for field in CANDIDATE_FIELDS
                    }
                )
    except OSError as error:
        raise CostAblationError(f"cannot write {path}: {error}") from error


def _write_json(path: Path, value: Any) -> None:
    try:
        path.write_text(
            json.dumps(
                trainer.audited._finite_json(value),
                indent=2,
                sort_keys=True,
                ensure_ascii=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise CostAblationError(f"cannot write {path}: {error}") from error


def render_report(result: dict[str, Any]) -> str:
    """Render the compact human-readable result accompanying raw artifacts."""

    source_cal = result["source_frozen_calibration_policy"]
    selected_cal = result["selected_cost_free_calibration_policy"]
    source_test = result["source_frozen_engineering_test_policy"]
    selected_test = result["selected_cost_free_engineering_test_policy"]
    delta = result["engineering_test_deltas"]
    return "\n".join(
        [
            "# Temporal T2 cost-denominator ablation",
            "",
            "This is retrospective engineering evidence on already-opened runs, not confirmation.",
            "No model was refit and fresh seeds 1301+ were not read.",
            "",
            "## Selected policies",
            "",
            "| Evidence role | Ranker | Family | Gate | Requested action fraction |",
            "| --- | --- | --- | --- | ---: |",
            (
                f"| Frozen source | {source_cal['ranker']} | "
                f"{source_cal['feature_family']} | {source_cal['frame_gate']} | "
                f"{source_cal['requested_action_fraction']:.1%} |"
            ),
            (
                f"| Cost-free winner | {selected_cal['ranker']} | "
                f"{selected_cal['feature_family']} | {selected_cal['frame_gate']} | "
                f"{selected_cal['requested_action_fraction']:.1%} |"
            ),
            "",
            "## Engineering-test estimates",
            "",
            "| Metric | Frozen source | Cost-free winner | Delta (winner - source) |",
            "| --- | ---: | ---: | ---: |",
            (
                "| Deadline-miss probability | "
                f"{source_test['dr_policy_deadline_miss']:.6%} | "
                f"{selected_test['dr_policy_deadline_miss']:.6%} | "
                f"{delta['deadline_miss_probability']:+.6%} |"
            ),
            (
                "| Completed-late18 ratio | "
                f"{source_test['dr_policy_completed_late18_ratio']:.6%} | "
                f"{selected_test['dr_policy_completed_late18_ratio']:.6%} | "
                f"{delta['completed_late18_ratio']:+.6%} |"
            ),
            (
                "| DR airtime (us/eligible frame) | "
                f"{source_test['dr_airtime_us_per_eligible_frame']:.3f} | "
                f"{selected_test['dr_airtime_us_per_eligible_frame']:.3f} | "
                f"{delta['airtime_us_per_eligible_frame']:+.3f} |"
            ),
            (
                "| Action fraction | "
                f"{source_test['realized_action_fraction']:.6%} | "
                f"{selected_test['realized_action_fraction']:.6%} | "
                f"{delta['action_fraction']:+.6%} |"
            ),
            "",
            "The calibration winner is chosen without consulting engineering-test outcomes.",
            "The test estimates remain descriptive because that split was opened previously.",
            "",
        ]
    )


def plot_result(
    result: dict[str, Any], records: Sequence[dict[str, Any]], output: Path
) -> None:
    """Plot the paired calibration ranking and engineering-test estimates."""

    source_cal = result["source_frozen_calibration_policy"]
    selected_cal = result["selected_cost_free_calibration_policy"]
    source_test = result["source_frozen_engineering_test_policy"]
    selected_test = result["selected_cost_free_engineering_test_policy"]
    signal = source_cal["signal"]
    family = source_cal["feature_family"]
    gate = source_cal["frame_gate"]
    paired = [
        record
        for record in records
        if record["signal"] == signal
        and record["feature_family"] == family
        and record["frame_gate"] == gate
    ]

    figure, axes = plt.subplots(2, 2, figsize=(12.5, 8.6))
    axis = axes[0, 0]
    for normalized, label, color, marker in (
        (1, "Learned cost divisor", "#4c78a8", "o"),
        (0, "No cost divisor", "#e45756", "s"),
    ):
        values = sorted(
            (record for record in paired if record["cost_normalized"] == normalized),
            key=lambda record: record["requested_action_fraction"],
        )
        axis.plot(
            [100 * record["requested_action_fraction"] for record in values],
            [100 * record["balanced_min_relative_improvement"] for record in values],
            label=label,
            color=color,
            marker=marker,
            linewidth=2,
        )
    axis.set_title(f"Calibration: matched {signal}")
    axis.set_xlabel("Requested actions (% of eligible frames)")
    axis.set_ylabel("Worst objective improvement (%)")
    axis.grid(alpha=0.25)
    axis.legend(frameon=False)

    axis = axes[0, 1]
    raw = [record for record in records if not record["cost_normalized"]]
    normalized = [record for record in records if record["cost_normalized"]]
    axis.scatter(
        [100 * record["deadline_miss_relative_improvement"] for record in normalized],
        [100 * record["completed_late18_relative_improvement"] for record in normalized],
        s=14,
        alpha=0.22,
        color="#4c78a8",
        label="Cost-normalized candidates",
    )
    axis.scatter(
        [100 * record["deadline_miss_relative_improvement"] for record in raw],
        [100 * record["completed_late18_relative_improvement"] for record in raw],
        s=14,
        alpha=0.22,
        color="#e45756",
        label="Cost-free candidates",
    )
    for record, label, color, marker in (
        (source_cal, "Frozen source", "#1f4e79", "o"),
        (selected_cal, "Cost-free winner", "#b22222", "*"),
    ):
        axis.scatter(
            100 * record["deadline_miss_relative_improvement"],
            100 * record["completed_late18_relative_improvement"],
            s=150,
            marker=marker,
            color=color,
            edgecolor="white",
            linewidth=0.8,
            label=label,
            zorder=5,
        )
    axis.axvline(50, color="black", linestyle="--", linewidth=0.8, alpha=0.5)
    axis.axhline(50, color="black", linestyle="--", linewidth=0.8, alpha=0.5)
    axis.set_title("Calibration objective plane")
    axis.set_xlabel("Deadline-miss improvement (%)")
    axis.set_ylabel("Completed-late18 improvement (%)")
    axis.grid(alpha=0.2)
    axis.legend(frameon=False, fontsize=8)

    labels = ["Deadline miss", "Completed >18 ms"]
    positions = np.arange(len(labels))
    width = 0.36
    axis = axes[1, 0]
    axis.bar(
        positions - width / 2,
        [
            100 * source_test["dr_policy_deadline_miss"],
            100 * source_test["dr_policy_completed_late18_ratio"],
        ],
        width,
        label="Frozen source",
        color="#4c78a8",
    )
    axis.bar(
        positions + width / 2,
        [
            100 * selected_test["dr_policy_deadline_miss"],
            100 * selected_test["dr_policy_completed_late18_ratio"],
        ],
        width,
        label="Cost-free winner",
        color="#e45756",
    )
    axis.set_xticks(positions, labels)
    axis.set_ylabel("Engineering-test probability (%)")
    axis.set_title("Already-opened engineering test")
    axis.grid(axis="y", alpha=0.25)
    axis.legend(frameon=False)

    axis = axes[1, 1]
    names = ["Frozen source", "Cost-free winner"]
    positions = np.arange(2)
    action_bars = axis.bar(
        positions,
        [
            100 * source_test["realized_action_fraction"],
            100 * selected_test["realized_action_fraction"],
        ],
        width=0.55,
        color=["#4c78a8", "#e45756"],
        alpha=0.8,
    )
    axis.set_xticks(positions, names)
    axis.set_ylabel("Actions (% of eligible frames)")
    axis.set_title("Engineering-test resource proxies")
    axis.grid(axis="y", alpha=0.25)
    airtime_axis = axis.twinx()
    airtime_axis.plot(
        positions,
        [
            source_test["dr_airtime_us_per_eligible_frame"],
            selected_test["dr_airtime_us_per_eligible_frame"],
        ],
        color="#222222",
        marker="D",
        linewidth=1.8,
        label="DR airtime",
    )
    airtime_axis.set_ylabel("DR airtime (us/eligible frame)")
    axis.legend([action_bars], ["Action fraction"], frameon=False, loc="upper left")
    airtime_axis.legend(frameon=False, loc="upper right")

    figure.suptitle(
        "Frozen temporal T2 heads: learned cost-denominator ablation",
        fontsize=14,
    )
    figure.tight_layout()
    try:
        figure.savefig(
            output,
            dpi=180,
            bbox_inches="tight",
            metadata={"Software": "wifi_streaming_simulation"},
        )
    finally:
        plt.close(figure)


def _repository_provenance() -> dict[str, Any]:
    source = Path(__file__).resolve()
    repository = source.parent.parent

    def git(*arguments: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(repository), *arguments],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
            )
        except (OSError, subprocess.CalledProcessError):
            return None
        return completed.stdout.strip()

    status = git("status", "--porcelain=v1", "--untracked-files=all")
    return {
        "evaluator_source_path": str(source.relative_to(repository)),
        "evaluator_source_sha256": _sha256(source),
        "trainer_source_sha256": _sha256(Path(trainer.__file__).resolve()),
        "repository_git_commit": git("rev-parse", "HEAD"),
        "repository_worktree_clean": status == "",
        "repository_worktree_status_sha256": (
            hashlib.sha256(status.encode("utf-8")).hexdigest()
            if status is not None
            else None
        ),
    }


def write_outputs(
    result: dict[str, Any],
    records: Sequence[dict[str, Any]],
    output_dir: Path | str,
) -> None:
    """Publish checksum-bound analysis artifacts without overwriting paths."""

    output = Path(output_dir).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.mkdir()
    except FileExistsError as error:
        raise CostAblationError(f"refusing to overwrite output directory: {output}") from error
    except OSError as error:
        raise CostAblationError(f"cannot reserve output directory {output}: {error}") from error
    temporary: Path | None = None
    published: list[tuple[Path, int, int]] = []
    try:
        temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
        final_result = dict(result)
        final_result["provenance"] = _repository_provenance()
        _write_candidates(temporary / OUTPUT_CANDIDATES, records)
        _write_json(temporary / OUTPUT_METRICS, final_result)
        (temporary / OUTPUT_REPORT).write_text(
            render_report(final_result), encoding="utf-8"
        )
        plot_result(final_result, records, temporary / OUTPUT_FIGURE)
        manifest = {
            "manifest_schema_version": 1,
            "hash_algorithm": "sha256",
            "ablation_id": ABLATION_ID,
            "artifacts_sha256": {
                name: _sha256(temporary / name)
                for name in (
                    OUTPUT_CANDIDATES,
                    OUTPUT_METRICS,
                    OUTPUT_REPORT,
                    OUTPUT_FIGURE,
                )
            },
        }
        _write_json(temporary / OUTPUT_MANIFEST, manifest)
        for name in OUTPUT_FILES:
            source = temporary / name
            destination = output / name
            source_stat = source.stat(follow_symlinks=False)
            os.link(source, destination)
            published.append((destination, source_stat.st_dev, source_stat.st_ino))
            source.unlink()
        temporary.rmdir()
    except BaseException:
        for path, expected_device, expected_inode in reversed(published):
            try:
                actual = path.stat(follow_symlinks=False)
                if actual.st_dev == expected_device and actual.st_ino == expected_inode:
                    path.unlink()
            except FileNotFoundError:
                pass
        if temporary is not None and temporary.exists():
            shutil.rmtree(temporary)
        try:
            output.rmdir()
        except OSError:
            pass
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="evaluate a frozen temporal T2 learned-cost-denominator ablation"
    )
    parser.add_argument("dataset_dir", type=Path)
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--random-seed", type=int, default=trainer.RANDOM_SEED)
    args = parser.parse_args(argv)
    dataset = trainer.load_temporal_dataset(args.dataset_dir)
    result, records = evaluate_cost_ablation(
        dataset, args.model_dir, random_seed=args.random_seed
    )
    write_outputs(result, records, args.output_dir)
    selected = result["selected_cost_free_calibration_policy"]
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "selected_cost_free_policy": {
                    key: selected[key]
                    for key in (
                        "feature_family",
                        "ranker",
                        "frame_gate",
                        "score_threshold",
                    )
                },
                "status": "PASS",
                "test_role_used_during_selection": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
