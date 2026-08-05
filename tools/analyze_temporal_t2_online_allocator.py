#!/usr/bin/env python3
"""Evaluate the frozen nonclairvoyant temporal-T2 shadow-price allocator."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import analyze_temporal_t2_distributional_frontier as static
import crossfit_temporal_t2_distributions as crossfit
import fit_temporal_t2_shadow_reference as shadow_reference


ANALYSIS_SCHEMA_VERSION = 1
ANALYSIS_ID = "temporal-t2-online-shadow-price-v1"
DESIGN_CONTRACT = Path(
    "experiments/model-selection/temporal-t2-online-shadow-price-v1.json"
)
DESIGN_CONTRACT_SHA256 = (
    "ac6a0718ee12037b3cd4343e2e29c059602414998fb289c172edc06b9700bf6e"
)
TIME_BIN_COUNT = 12
TIME_BIN_WIDTH_US = 5_000_000
MEASUREMENT_DURATION_US = Decimal(60_000_000)
DECISION_OFFSET_US = Decimal(2_000)
INITIAL_CREDIT_US = Decimal(12_000)
CAPACITY_US = Decimal(360_000)
REFILL_RATE = Decimal("0.006")
MAXIMUM_GENERATED_CREDIT_US = Decimal(372_000)
REGIME_MODES = ("global", "congestion_tertile")

OUTPUT_METRICS = "temporal_t2_online_allocator.json"
OUTPUT_REPORT = "temporal_t2_online_allocator.md"
OUTPUT_FIGURE = "temporal_t2_online_allocator.png"
OUTPUT_MANIFEST = "artifact_manifest.json"


class OnlineAllocatorError(RuntimeError):
    """Raised when a causal online replay cannot be established."""


@dataclass(frozen=True)
class ShadowCurve:
    """Exact canonical-cost marginal reward-density curve."""

    density_descending: np.ndarray
    cumulative_p_frames: np.ndarray
    cumulative_i_frames: np.ndarray
    p_cost_us: Decimal
    i_cost_us: Decimal
    training_run_count: int

    def opportunity_cost(self, remaining_budget_us: Decimal) -> float:
        """Return the exact marginal density affordable per training run."""

        if remaining_budget_us < 0 or self.training_run_count <= 0:
            raise OnlineAllocatorError("invalid shadow-price resource state")
        count = len(self.density_descending)
        if count == 0:
            return math.inf
        target = remaining_budget_us * self.training_run_count
        low = 0
        high = count
        while low < high:
            middle = (low + high) // 2
            cost = (
                self.p_cost_us * int(self.cumulative_p_frames[middle])
                + self.i_cost_us * int(self.cumulative_i_frames[middle])
            )
            if cost <= target:
                low = middle + 1
            else:
                high = middle
        affordable = low
        if affordable == 0:
            return math.inf
        if affordable == count:
            return 0.0
        return float(self.density_descending[affordable - 1])


@dataclass(frozen=True)
class AllocatorContext:
    """Data-only causal state reused by every predictor and objective."""

    decision_times: np.ndarray
    time_bins: np.ndarray
    indices_by_unit: dict[static.Unit, np.ndarray]
    training_units_by_fold: dict[int, tuple[static.Unit, ...]]
    regime_units_by_fold_bin: dict[
        tuple[int, int], dict[int, tuple[static.Unit, ...]]
    ]
    cutpoints_by_fold_bin: dict[tuple[int, int], tuple[float, float]]
    p_cost_us: Decimal
    i_cost_us: Decimal


@dataclass(frozen=True)
class ShadowReferenceDataset:
    """Fold-honest score distributions for the selected outer model."""

    path: Path
    metrics: dict[str, Any]
    manifest: dict[str, Any]
    selected_variant: str
    cdf_by_evaluation_fold: np.ndarray


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise OnlineAllocatorError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OnlineAllocatorError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise OnlineAllocatorError(f"{path}: expected a JSON object")
    return value


def _parse_int(text: str, field: str, context: str) -> int:
    try:
        value = int(text)
    except (TypeError, ValueError) as error:
        raise OnlineAllocatorError(f"{context}: invalid {field}") from error
    if str(value) != text:
        raise OnlineAllocatorError(f"{context}: noncanonical {field}")
    return value


def _parse_reference_cdf(
    row: dict[str, str], variant: str, context: str
) -> np.ndarray:
    values = np.empty((2, len(crossfit.THRESHOLDS_US)), dtype=float)
    for arm in (0, 1):
        for threshold_index, threshold in enumerate(crossfit.THRESHOLDS_US):
            field = f"{variant}__arm{arm}_cdf_{threshold}us"
            try:
                value = float(row[field])
            except (KeyError, ValueError) as error:
                raise OnlineAllocatorError(
                    f"{context}: invalid reference probability {field}"
                ) from error
            if not math.isfinite(value) or value < 0 or value > 1:
                raise OnlineAllocatorError(
                    f"{context}: out-of-range reference probability {field}"
                )
            values[arm, threshold_index] = value
    if np.any(np.diff(values, axis=1) < -1e-12):
        raise OnlineAllocatorError(f"{context}: nonmonotone reference CDF")
    return values


def load_shadow_reference(
    reference_dir: Path | str,
    data: static.FrontierDataset,
    static_dir: Path | str,
    static_result: dict[str, Any],
) -> ShadowReferenceDataset:
    """Verify and load the selected model's fold-honest training scores."""

    source_dir = Path(reference_dir).resolve()
    manifest = _read_json(source_dir / shadow_reference.OUTPUT_MANIFEST)
    metrics = _read_json(source_dir / shadow_reference.OUTPUT_METRICS)
    artifacts = manifest.get("artifacts_sha256")
    expected_artifacts = {
        shadow_reference.OUTPUT_PREDICTIONS,
        shadow_reference.OUTPUT_METRICS,
    }
    if (
        manifest.get("manifest_schema_version") != 1
        or manifest.get("hash_algorithm") != "sha256"
        or manifest.get("reference_id") != shadow_reference.REFERENCE_ID
        or metrics.get("reference_schema_version")
        != shadow_reference.REFERENCE_SCHEMA_VERSION
        or metrics.get("reference_id") != shadow_reference.REFERENCE_ID
        or not isinstance(artifacts, dict)
        or set(artifacts) != expected_artifacts
    ):
        raise OnlineAllocatorError("shadow reference artifact closure differs")
    for name, digest in artifacts.items():
        if not isinstance(digest, str) or _sha256(source_dir / name) != digest:
            raise OnlineAllocatorError(f"shadow reference hash differs: {name}")

    selected_variant, selection_record = shadow_reference.select_variant(
        static_result
    )
    if (
        metrics.get("selected_variant") != selected_variant
        or metrics.get("selection_record") != selection_record
        or metrics.get("fold_count") != crossfit.FOLD_COUNT
        or metrics.get("training_groups_per_fold") != 84
    ):
        raise OnlineAllocatorError("shadow reference predictor selection differs")
    fits = metrics.get("fit_records")
    maximum_difference = metrics.get(
        "maximum_reproduced_oof_absolute_difference"
    )
    expected_fits = {
        (fold, arm)
        for fold in range(crossfit.FOLD_COUNT)
        for arm in (0, 1)
    }
    if (
        not isinstance(fits, list)
        or {
            (record.get("fold"), record.get("arm"))
            for record in fits
            if isinstance(record, dict)
        }
        != expected_fits
        or not isinstance(maximum_difference, (int, float))
        or not math.isfinite(maximum_difference)
        or maximum_difference > 1e-12
    ):
        raise OnlineAllocatorError("shadow reference fit reproduction differs")
    static_source_dir = Path(static_dir).resolve()
    actual_source = {
        "temporal_csv_sha256": _sha256(
            data.temporal_dir / shadow_reference.temporal_builder.OUTPUT_CSV
        ),
        "crossfit_manifest_sha256": _sha256(
            data.distribution_dir / crossfit.OUTPUT_MANIFEST
        ),
        "crossfit_predictions_sha256": _sha256(
            data.distribution_dir / crossfit.OUTPUT_PREDICTIONS
        ),
        "crossfit_metrics_sha256": _sha256(
            data.distribution_dir / crossfit.OUTPUT_METRICS
        ),
        "static_metrics_sha256": _sha256(
            static_source_dir / static.OUTPUT_METRICS
        ),
        "static_manifest_sha256": _sha256(
            static_source_dir / static.OUTPUT_MANIFEST
        ),
    }
    if (
        metrics.get("source") != actual_source
        or manifest.get("source_artifacts_sha256") != actual_source
    ):
        raise OnlineAllocatorError("shadow reference source hashes differ")

    row_count = len(data.seeds)
    identities = {
        (
            int(data.seeds[index]),
            int(data.run_numbers[index]),
            int(data.frame_ids[index]),
        ): index
        for index in range(row_count)
    }
    if len(identities) != row_count:
        raise OnlineAllocatorError("temporal row identity is not unique")
    cdf = np.full(
        (
            crossfit.FOLD_COUNT,
            row_count,
            2,
            len(crossfit.THRESHOLDS_US),
        ),
        np.nan,
        dtype=float,
    )
    seen = np.zeros((crossfit.FOLD_COUNT, row_count), dtype=bool)
    prediction_path = source_dir / shadow_reference.OUTPUT_PREDICTIONS
    try:
        with gzip.open(prediction_path, "rt", encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            if reader.fieldnames != shadow_reference._header(selected_variant):
                raise OnlineAllocatorError("shadow reference header differs")
            for line_number, row in enumerate(reader, start=2):
                context = f"{prediction_path}:{line_number}"
                if _parse_int(
                    row.get("reference_schema_version", ""),
                    "reference_schema_version",
                    context,
                ) != shadow_reference.REFERENCE_SCHEMA_VERSION:
                    raise OnlineAllocatorError(
                        f"{context}: reference schema version differs"
                    )
                fold = _parse_int(
                    row.get("evaluation_fold", ""), "evaluation_fold", context
                )
                if fold < 0 or fold >= crossfit.FOLD_COUNT:
                    raise OnlineAllocatorError(f"{context}: invalid evaluation fold")
                identity = (
                    _parse_int(row.get("seed", ""), "seed", context),
                    _parse_int(
                        row.get("run_number", ""), "run_number", context
                    ),
                    _parse_int(row.get("frame_id", ""), "frame_id", context),
                )
                index = identities.get(identity)
                if index is None:
                    raise OnlineAllocatorError(
                        f"{context}: shadow reference identity differs"
                    )
                if int(data.folds[index]) == fold or seen[fold, index]:
                    raise OnlineAllocatorError(
                        f"{context}: shadow reference fold mask differs"
                    )
                cdf[fold, index] = _parse_reference_cdf(
                    row, selected_variant, context
                )
                seen[fold, index] = True
    except (OSError, gzip.BadGzipFile) as error:
        raise OnlineAllocatorError(
            f"cannot read shadow reference predictions: {error}"
        ) from error
    expected_seen = (
        data.folds[None, :]
        != np.arange(crossfit.FOLD_COUNT, dtype=int)[:, None]
    )
    if not np.array_equal(seen, expected_seen):
        raise OnlineAllocatorError("shadow reference row coverage differs")
    if metrics.get("reference_row_count") != int(np.sum(seen)):
        raise OnlineAllocatorError("shadow reference row count differs")
    return ShadowReferenceDataset(
        path=source_dir,
        metrics=metrics,
        manifest=manifest,
        selected_variant=selected_variant,
        cdf_by_evaluation_fold=cdf,
    )


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _validate_contract() -> dict[str, Any]:
    path = _repository_root() / DESIGN_CONTRACT
    if _sha256(path) != DESIGN_CONTRACT_SHA256:
        raise OnlineAllocatorError("online allocator design-contract hash differs")
    contract = _read_json(path)
    if (
        contract.get("schema_version") != 1
        or contract.get("analysis_id") != ANALYSIS_ID
        or contract.get("status") != "frozen_before_cross_fitted_results_read"
        or contract.get("causal_time", {}).get("time_bin_count")
        != TIME_BIN_COUNT
        or contract.get("causal_time", {}).get("time_bin_width_us")
        != TIME_BIN_WIDTH_US
        or contract.get("benefit_outputs_kept_separate")
        != list(static.OBJECTIVES)
        or contract.get("frame_gates") != list(static.FRAME_GATES)
    ):
        raise OnlineAllocatorError("online allocator design semantics differ")
    accounting = contract.get("airtime_accounting", {})
    expected = {
        "budget_fraction": float(REFILL_RATE),
        "initial_credit_us": int(INITIAL_CREDIT_US),
        "full_horizon_capacity_us": int(CAPACITY_US),
        "causal_refill_rate_us_per_us": float(REFILL_RATE),
        "maximum_total_generated_credit_us": int(MAXIMUM_GENERATED_CREDIT_US),
    }
    if any(accounting.get(name) != value for name, value in expected.items()):
        raise OnlineAllocatorError("online allocator airtime contract differs")
    return contract


def decision_time_us(frame_id: int) -> Decimal:
    """Reconstruct the exact fixed-FPS T2 decision time after measurement start."""

    if frame_id < 0:
        raise OnlineAllocatorError("negative frame ID")
    generation_offset_ns = (frame_id * 1_000_000_000 + 15) // 30
    return Decimal(generation_offset_ns) / Decimal(1_000) + DECISION_OFFSET_US


def _canonical_costs(data: static.FrontierDataset) -> tuple[Decimal, Decimal]:
    by_type = {
        frame_type: {
            Decimal(text)
            for text, observed_type in zip(
                data.canonical_cost_text, data.frame_types, strict=True
            )
            if observed_type == frame_type
        }
        for frame_type in ("P_FRAME", "I_FRAME")
    }
    if any(len(values) != 1 for values in by_type.values()):
        raise OnlineAllocatorError("canonical cost is not constant by frame type")
    p_cost = next(iter(by_type["P_FRAME"]))
    i_cost = next(iter(by_type["I_FRAME"]))
    if p_cost <= 0 or i_cost <= 0:
        raise OnlineAllocatorError("canonical cost is non-positive")
    return p_cost, i_cost


def _time_arrays(data: static.FrontierDataset) -> tuple[np.ndarray, np.ndarray]:
    decision = np.asarray(
        [float(decision_time_us(int(frame_id))) for frame_id in data.frame_ids]
    )
    bins = np.minimum(
        (decision // TIME_BIN_WIDTH_US).astype(int), TIME_BIN_COUNT - 1
    )
    if np.any(decision < 0) or np.any(decision > float(MEASUREMENT_DURATION_US)):
        raise OnlineAllocatorError("decision time falls outside measurement")
    return decision, bins


def _training_group_states(
    data: static.FrontierDataset,
    training_units: Sequence[static.Unit],
    decision_times: np.ndarray,
) -> tuple[dict[tuple[static.Unit, int], float], dict[int, tuple[float, float]]]:
    indices_by_unit = data.indices_by_unit()
    states: dict[tuple[static.Unit, int], float] = {}
    cutpoints: dict[int, tuple[float, float]] = {}
    for time_bin in range(TIME_BIN_COUNT):
        bin_stop = (time_bin + 1) * TIME_BIN_WIDTH_US
        values: list[float] = []
        for unit in training_units:
            indices = indices_by_unit[unit]
            causal = indices[decision_times[indices] < bin_stop]
            if len(causal) == 0:
                raise OnlineAllocatorError(
                    "training group lacks a causal congestion observation"
                )
            state = float(np.mean(data.primary_busy_20ms[causal]))
            states[(unit, time_bin)] = state
            values.append(state)
        if len(values) < 3:
            raise OnlineAllocatorError("too few training groups for congestion state")
        quantiles = np.quantile(values, (1.0 / 3.0, 2.0 / 3.0))
        cutpoints[time_bin] = (float(quantiles[0]), float(quantiles[1]))
    return states, cutpoints


def _regime(value: float, cutpoints: tuple[float, float]) -> int:
    return int(np.searchsorted(np.asarray(cutpoints), value, side="right"))


def build_allocator_context(data: static.FrontierDataset) -> AllocatorContext:
    """Precompute fold-honest time and congestion state independent of scores."""

    decision_times, time_bins = _time_arrays(data)
    indices_by_unit = data.indices_by_unit()
    training_units_by_fold: dict[int, tuple[static.Unit, ...]] = {}
    regime_units: dict[tuple[int, int], dict[int, tuple[static.Unit, ...]]] = {}
    cutpoints_by_fold_bin: dict[tuple[int, int], tuple[float, float]] = {}
    for evaluation_fold in range(crossfit.FOLD_COUNT):
        training_units = tuple(
            unit
            for unit, indices in indices_by_unit.items()
            if int(data.folds[indices[0]]) != evaluation_fold
        )
        if len(training_units) != 84:
            raise OnlineAllocatorError("outer-fold training group count differs")
        training_units_by_fold[evaluation_fold] = training_units
        states, cutpoints = _training_group_states(
            data, training_units, decision_times
        )
        for time_bin in range(TIME_BIN_COUNT):
            key = (evaluation_fold, time_bin)
            cutpoints_by_fold_bin[key] = cutpoints[time_bin]
            regime_units[key] = {
                regime: tuple(
                    unit
                    for unit in training_units
                    if _regime(states[(unit, time_bin)], cutpoints[time_bin])
                    == regime
                )
                for regime in range(3)
            }
            if any(not units for units in regime_units[key].values()):
                raise OnlineAllocatorError("empty learned congestion regime")
    p_cost, i_cost = _canonical_costs(data)
    return AllocatorContext(
        decision_times=decision_times,
        time_bins=time_bins,
        indices_by_unit=indices_by_unit,
        training_units_by_fold=training_units_by_fold,
        regime_units_by_fold_bin=regime_units,
        cutpoints_by_fold_bin=cutpoints_by_fold_bin,
        p_cost_us=p_cost,
        i_cost_us=i_cost,
    )


def build_shadow_curves(
    data: static.FrontierDataset,
    context: AllocatorContext,
    training_rewards: np.ndarray,
    evaluation_fold: int,
    frame_gate: str,
    regime_mode: str,
) -> tuple[dict[tuple[int, int], ShadowCurve], dict[int, tuple[float, float]]]:
    """Learn time/resource/congestion shadow curves without evaluation groups."""

    if regime_mode not in REGIME_MODES or frame_gate not in static.FRAME_GATES:
        raise OnlineAllocatorError("unsupported shadow-curve policy")
    expected_finite = data.folds != evaluation_fold
    if (
        training_rewards.shape != (len(data.seeds),)
        or not np.array_equal(np.isfinite(training_rewards), expected_finite)
        or np.any(training_rewards[expected_finite] < 0)
    ):
        raise OnlineAllocatorError("shadow-curve training-score boundary differs")
    training_units = context.training_units_by_fold[evaluation_fold]
    unit_rows = context.indices_by_unit
    cutpoints = {
        time_bin: context.cutpoints_by_fold_bin[(evaluation_fold, time_bin)]
        for time_bin in range(TIME_BIN_COUNT)
    }
    curves: dict[tuple[int, int], ShadowCurve] = {}
    for time_bin in range(TIME_BIN_COUNT):
        if regime_mode == "global":
            grouped_units = {0: training_units}
        else:
            grouped_units = context.regime_units_by_fold_bin[
                (evaluation_fold, time_bin)
            ]
        for regime, selected_units in grouped_units.items():
            if not selected_units:
                raise OnlineAllocatorError("empty learned congestion regime")
            indices = np.concatenate([unit_rows[unit] for unit in selected_units])
            eligible = (
                context.decision_times[indices] >= time_bin * TIME_BIN_WIDTH_US
            )
            if frame_gate == "p_frames_only":
                eligible &= np.asarray(
                    [data.frame_types[int(index)] == "P_FRAME" for index in indices]
                )
            eligible &= training_rewards[indices] > 0
            candidates = indices[eligible]
            if len(candidates):
                density = (
                    training_rewards[candidates]
                    / data.canonical_cost_us[candidates]
                )
                order = np.lexsort(
                    (
                        data.run_numbers[candidates],
                        data.seeds[candidates],
                        data.frame_ids[candidates],
                        -density,
                    )
                )
                ranked = candidates[order]
                ranked_density = density[order]
                p_count = np.cumsum(
                    np.asarray(
                        [data.frame_types[int(index)] == "P_FRAME" for index in ranked],
                        dtype=np.int32,
                    )
                )
                i_count = np.cumsum(
                    np.asarray(
                        [data.frame_types[int(index)] == "I_FRAME" for index in ranked],
                        dtype=np.int32,
                    )
                )
            else:
                ranked_density = np.asarray([], dtype=float)
                p_count = np.asarray([], dtype=np.int32)
                i_count = np.asarray([], dtype=np.int32)
            curves[(time_bin, regime)] = ShadowCurve(
                density_descending=ranked_density,
                cumulative_p_frames=p_count,
                cumulative_i_frames=i_count,
                p_cost_us=context.p_cost_us,
                i_cost_us=context.i_cost_us,
                training_run_count=len(selected_units),
            )
    return curves, cutpoints


def replay_online_policy(
    data: static.FrontierDataset,
    context: AllocatorContext,
    evaluation_rewards: np.ndarray,
    reference_rewards_by_fold: np.ndarray,
    frame_gate: str,
    regime_mode: str,
) -> tuple[np.ndarray, dict[static.Unit, dict[str, Any]], dict[str, int]]:
    """Replay the frozen causal shadow-price policy on every held-out fold."""

    if (
        evaluation_rewards.shape != (len(data.seeds),)
        or reference_rewards_by_fold.shape
        != (crossfit.FOLD_COUNT, len(data.seeds))
        or not np.all(np.isfinite(evaluation_rewards))
        or np.any(evaluation_rewards < 0)
    ):
        raise OnlineAllocatorError("online replay reward schema differs")
    policy = np.zeros(len(evaluation_rewards), dtype=bool)
    details: dict[static.Unit, dict[str, Any]] = {}
    totals = {
        "gate_rejections": 0,
        "nonpositive_reward_rejections": 0,
        "opportunity_cost_rejections": 0,
        "current_credit_rejections": 0,
        "actions": 0,
    }
    indices_by_unit = context.indices_by_unit
    for evaluation_fold in range(crossfit.FOLD_COUNT):
        curves, cutpoints = build_shadow_curves(
            data,
            context,
            reference_rewards_by_fold[evaluation_fold],
            evaluation_fold,
            frame_gate,
            regime_mode,
        )
        evaluation_units = [
            unit
            for unit, indices in indices_by_unit.items()
            if int(data.folds[indices[0]]) == evaluation_fold
        ]
        if len(evaluation_units) != crossfit.GROUPS_PER_FOLD:
            raise OnlineAllocatorError("outer-fold evaluation group count differs")
        for unit in evaluation_units:
            indices = indices_by_unit[unit]
            balance = INITIAL_CREDIT_US
            last_time = Decimal(0)
            spend = Decimal(0)
            actions = 0
            busy_sum = 0.0
            busy_count = 0
            predicted_reward = 0.0
            for index in indices:
                current_time = decision_time_us(int(data.frame_ids[index]))
                if current_time < last_time:
                    raise OnlineAllocatorError("run decisions are not chronological")
                balance = min(
                    CAPACITY_US,
                    balance + REFILL_RATE * (current_time - last_time),
                )
                last_time = current_time
                busy_sum += float(data.primary_busy_20ms[index])
                busy_count += 1
                time_bin = int(context.time_bins[index])
                regime = (
                    0
                    if regime_mode == "global"
                    else _regime(busy_sum / busy_count, cutpoints[time_bin])
                )
                if (
                    frame_gate == "p_frames_only"
                    and data.frame_types[index] != "P_FRAME"
                ):
                    totals["gate_rejections"] += 1
                    continue
                reward = float(evaluation_rewards[index])
                if reward <= 0:
                    totals["nonpositive_reward_rejections"] += 1
                    continue
                cost = Decimal(data.canonical_cost_text[index])
                remaining_refill = REFILL_RATE * (
                    MEASUREMENT_DURATION_US - current_time
                )
                remaining_budget = balance + remaining_refill
                opportunity_cost = curves[(time_bin, regime)].opportunity_cost(
                    remaining_budget
                )
                density = reward / float(cost)
                if density < opportunity_cost:
                    totals["opportunity_cost_rejections"] += 1
                    continue
                if balance < cost:
                    totals["current_credit_rejections"] += 1
                    continue
                balance -= cost
                spend += cost
                actions += 1
                predicted_reward += reward
                policy[index] = True
                totals["actions"] += 1
            if spend > MAXIMUM_GENERATED_CREDIT_US or balance < 0:
                raise OnlineAllocatorError("online replay violates exact credit")
            details[unit] = {
                "actions": actions,
                "canonical_reservation_us": float(spend),
                "predicted_reward": predicted_reward,
                "balance_after_last_candidate_us": float(balance),
            }
    if set(details) != set(data.units) or totals["actions"] != int(np.sum(policy)):
        raise OnlineAllocatorError("online replay coverage differs")
    return policy, details, totals


def _load_static_result(
    static_dir: Path, data: static.FrontierDataset
) -> dict[str, Any]:
    manifest = _read_json(static_dir / static.OUTPUT_MANIFEST)
    result = _read_json(static_dir / static.OUTPUT_METRICS)
    artifacts = manifest.get("artifacts_sha256")
    expected = {static.OUTPUT_METRICS, static.OUTPUT_REPORT, static.OUTPUT_FIGURE}
    if (
        manifest.get("analysis_id") != static.ANALYSIS_ID
        or result.get("analysis_id") != static.ANALYSIS_ID
        or not isinstance(artifacts, dict)
        or set(artifacts) != expected
    ):
        raise OnlineAllocatorError("static frontier artifact closure differs")
    for name, digest in artifacts.items():
        if _sha256(static_dir / name) != digest:
            raise OnlineAllocatorError(f"static frontier hash differs: {name}")
    expected_prediction_hash = _sha256(
        data.distribution_dir / crossfit.OUTPUT_PREDICTIONS
    )
    if result.get("source", {}).get(
        "crossfit_predictions_sha256"
    ) != expected_prediction_hash:
        raise OnlineAllocatorError("static and online cross-fit sources differ")
    return result


def _static_policy(
    result: dict[str, Any],
    variant: str,
    objective: str,
    frame_gate: str,
) -> dict[str, Any]:
    matches = [
        row
        for row in result["policies"]
        if row["variant"] == variant
        and row["objective"] == objective
        and row["frame_gate"] == frame_gate
        and row["budget_us_per_run"] == 372_000
    ]
    if len(matches) != 1:
        raise OnlineAllocatorError("cannot resolve static comparator")
    return matches[0]


def analyze_online_allocator(
    data: static.FrontierDataset,
    static_result: dict[str, Any],
    reference: ShadowReferenceDataset,
) -> dict[str, Any]:
    """Evaluate the selected predictor under the frozen causal allocator."""

    contract = _validate_contract()
    bootstrap = static._bootstrap_indices(len(data.units))
    context = build_allocator_context(data)
    records: list[dict[str, Any]] = []
    variant = reference.selected_variant
    cdf = data.cdf_by_variant[variant]
    evaluation_rewards = static.objective_rewards(cdf)
    reference_rewards = {
        objective: np.full(
            (crossfit.FOLD_COUNT, len(data.seeds)), np.nan, dtype=float
        )
        for objective in static.OBJECTIVES
    }
    for fold in range(crossfit.FOLD_COUNT):
        fold_rewards = static.objective_rewards(
            reference.cdf_by_evaluation_fold[fold]
        )
        for objective in static.OBJECTIVES:
            reference_rewards[objective][fold] = fold_rewards[objective]
    for objective in static.OBJECTIVES:
        for frame_gate in static.FRAME_GATES:
            comparator = _static_policy(
                static_result, variant, objective, frame_gate
            )
            static_comparator = {
                name: comparator[name]
                for name in (
                    "action_count",
                    "captured_primary_deadline_misses",
                    "primary_miss_capture_fraction",
                    "dr_policy_deadline_miss_probability",
                    "dr_policy_completed_late18_ratio",
                    "mean_canonical_reservation_us_per_run",
                )
            }
            for regime_mode in REGIME_MODES:
                policy, run_details, rejection_counts = replay_online_policy(
                    data,
                    context,
                    evaluation_rewards[objective],
                    reference_rewards[objective],
                    frame_gate,
                    regime_mode,
                )
                evaluated = static.evaluate_policy(
                    data, cdf, policy, run_details, bootstrap
                )
                records.append(
                    {
                        "variant": variant,
                        "feature_family": data.metrics["variants"][variant][
                            "feature_family"
                        ],
                        "model_spec_id": data.metrics["variants"][variant][
                            "model_spec_id"
                        ],
                        "objective": objective,
                        "frame_gate": frame_gate,
                        "regime_mode": regime_mode,
                        "future_evaluation_score_visibility": False,
                        "reference_score_population": (
                            "outer-model scores on its 84 training groups"
                        ),
                        "rejection_counts": rejection_counts,
                        **evaluated,
                        "static_372ms_comparator": static_comparator,
                        "gap_from_static_372ms": {
                            "actions": evaluated["action_count"]
                            - comparator["action_count"],
                            "captured_primary_deadline_misses": evaluated[
                                "captured_primary_deadline_misses"
                            ]
                            - comparator["captured_primary_deadline_misses"],
                            "dr_deadline_miss_probability": evaluated[
                                "dr_policy_deadline_miss_probability"
                            ]
                            - comparator["dr_policy_deadline_miss_probability"],
                            "dr_completed_late18_ratio": evaluated[
                                "dr_policy_completed_late18_ratio"
                            ]
                            - comparator["dr_policy_completed_late18_ratio"],
                        },
                    }
                )
    return {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis_id": ANALYSIS_ID,
        "evidence_role": "retrospective_cross_fitted_online_engineering_screen",
        "design_contract": {
            "path": str(DESIGN_CONTRACT),
            "sha256": DESIGN_CONTRACT_SHA256,
        },
        "population": static_result["population"],
        "selected_predictor": {
            "variant": variant,
            "feature_family": data.metrics["variants"][variant][
                "feature_family"
            ],
            "model_spec_id": data.metrics["variants"][variant][
                "model_spec_id"
            ],
            "selection_record": reference.metrics["selection_record"],
            "selection_uses_all_opened_engineering_folds": True,
        },
        "allocator": {
            "time_bin_count": TIME_BIN_COUNT,
            "time_bin_width_us": TIME_BIN_WIDTH_US,
            "regime_modes": list(REGIME_MODES),
            "initial_credit_us": int(INITIAL_CREDIT_US),
            "full_horizon_capacity_us": int(CAPACITY_US),
            "refill_rate_us_per_us": float(REFILL_RATE),
            "maximum_generated_credit_us": int(MAXIMUM_GENERATED_CREDIT_US),
            "future_credit_borrowing": False,
            "measured_settlement_release": False,
        },
        "bootstrap": static_result["bootstrap"],
        "policies": records,
        "interpretation_limits": [
            *contract["interpretation_limits"],
            (
                "The selected predictor was chosen using all 96 opened "
                "engineering groups; this is not independent confirmation."
            ),
            (
                "Each outer model's empirical shadow reference uses in-sample "
                "scores on its 84 training groups. This excludes held-out "
                "outcomes but may retain training-score optimism."
            ),
        ],
    }


def _record(
    result: dict[str, Any],
    variant: str,
    regime_mode: str,
    objective: str = "deadline_rescue",
    frame_gate: str = "p_frames_only",
) -> dict[str, Any]:
    matches = [
        row
        for row in result["policies"]
        if row["variant"] == variant
        and row["regime_mode"] == regime_mode
        and row["objective"] == objective
        and row["frame_gate"] == frame_gate
    ]
    if len(matches) != 1:
        raise OnlineAllocatorError("cannot resolve online policy record")
    return matches[0]


def write_report(path: Path, result: dict[str, Any]) -> None:
    """Write the primary deadline-rescue comparison and caveats."""

    lines = [
        "# Temporal-T2 online shadow-price allocator",
        "",
        (
            "This retrospective screen admits each row using only current causal "
            "state and shadow-price tables learned without its run-group fold. "
            "Only the frozen static-frontier winner is evaluated."
        ),
        "",
        (
            f"Selected predictor: `{result['selected_predictor']['variant']}`. "
            "The outer model scores its own 84 training groups for the shadow "
            "reference; held-out rows and outcomes remain absent."
        ),
        "",
        "## P-frame deadline-rescue policy",
        "",
        (
            "| Predictor | Regime | Actions | Captured primary misses | "
            "Static capture gap | DR miss risk | DR late18 ratio |"
        ),
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    variant = result["selected_predictor"]["variant"]
    for regime_mode in REGIME_MODES:
        row = _record(result, variant, regime_mode)
        lines.append(
            f"| {variant} | {regime_mode} | {row['action_count']:,} | "
            f"{row['captured_primary_deadline_misses']:,} | "
            f"{row['gap_from_static_372ms']['captured_primary_deadline_misses']:+,} | "
            f"{100 * row['dr_policy_deadline_miss_probability']:.3f}% | "
            f"{100 * row['dr_policy_completed_late18_ratio']:.3f}% |"
        )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            *[f"- {item}" for item in result["interpretation_limits"]],
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_figure(path: Path, result: dict[str, Any]) -> None:
    """Plot online-static capture, causal screen, and resource usage."""

    variant = result["selected_predictor"]["variant"]
    colors = {"global": "#4477AA", "congestion_tertile": "#CC6677"}
    figure, axes = plt.subplots(1, 3, figsize=(15, 4.8))
    online_rows = [_record(result, variant, regime) for regime in REGIME_MODES]
    static_row = online_rows[0]["static_372ms_comparator"]
    labels = ("Static 372 ms", "Online global", "Online congestion")
    palette = ("#222222", colors["global"], colors["congestion_tertile"])
    capture = [
        static_row["primary_miss_capture_fraction"],
        *[row["primary_miss_capture_fraction"] for row in online_rows],
    ]
    reservation = [
        static_row["mean_canonical_reservation_us_per_run"] / 1000,
        *[
            row["mean_canonical_reservation_us_per_run"] / 1000
            for row in online_rows
        ],
    ]
    x = np.arange(3)
    axes[0].bar(x, capture, color=palette)
    axes[0].set_xticks(x, labels, rotation=18, ha="right")
    axes[0].set_ylim(0, 1)
    axes[0].set_ylabel("Primary misses captured")
    axes[0].set_title("Online versus static information")
    axes[0].grid(axis="y", alpha=0.25)

    outcome_rows = [static_row, *online_rows]
    for label, color, row in zip(labels, palette, outcome_rows, strict=True):
        axes[1].scatter(
            100 * row["dr_policy_deadline_miss_probability"],
            100 * row["dr_policy_completed_late18_ratio"],
            color=color,
            s=75,
            label=label,
        )
    axes[1].set_xlabel("DR deadline-miss probability (%)")
    axes[1].set_ylabel("DR completed late18 ratio (%)")
    axes[1].set_title("Offline causal screen")
    axes[1].legend(frameon=False, fontsize=8)
    axes[1].grid(alpha=0.25)

    axes[2].bar(x, reservation, color=palette)
    axes[2].axhline(372, color="#666666", linestyle="--", linewidth=1)
    axes[2].set_xticks(x, labels)
    axes[2].tick_params(axis="x", rotation=18)
    axes[2].set_ylabel("Canonical reservation (ms/run)")
    axes[2].set_title("Exact causal resource replay")
    axes[2].grid(axis="y", alpha=0.25)
    figure.suptitle("Temporal-T2 nonclairvoyant shadow-price allocator")
    figure.tight_layout()
    figure.savefig(path, dpi=180, bbox_inches="tight")
    plt.close(figure)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _git_value(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=_repository_root(),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def analyze_to_directory(
    distribution_dir: Path | str,
    temporal_dir: Path | str,
    static_dir: Path | str,
    reference_dir: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Run the frozen online allocator and write checksum-closed artifacts."""

    destination = Path(output_dir).resolve()
    if destination.exists():
        raise OnlineAllocatorError(
            f"refusing to overwrite output directory: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        data = static.load_frontier_dataset(distribution_dir, temporal_dir)
        static_path = Path(static_dir).resolve()
        static_result = _load_static_result(static_path, data)
        reference = load_shadow_reference(
            reference_dir, data, static_path, static_result
        )
        result = analyze_online_allocator(data, static_result, reference)
        result["source"] = {
            "crossfit_manifest_sha256": _sha256(
                data.distribution_dir / crossfit.OUTPUT_MANIFEST
            ),
            "crossfit_metrics_sha256": _sha256(
                data.distribution_dir / crossfit.OUTPUT_METRICS
            ),
            "crossfit_predictions_sha256": _sha256(
                data.distribution_dir / crossfit.OUTPUT_PREDICTIONS
            ),
            "static_manifest_sha256": _sha256(
                static_path / static.OUTPUT_MANIFEST
            ),
            "static_metrics_sha256": _sha256(
                static_path / static.OUTPUT_METRICS
            ),
            "shadow_reference_manifest_sha256": _sha256(
                reference.path / shadow_reference.OUTPUT_MANIFEST
            ),
            "shadow_reference_metrics_sha256": _sha256(
                reference.path / shadow_reference.OUTPUT_METRICS
            ),
            "shadow_reference_predictions_sha256": _sha256(
                reference.path / shadow_reference.OUTPUT_PREDICTIONS
            ),
        }
        result["provenance"] = {
            "project_git_commit": _git_value("rev-parse", "HEAD"),
            "project_git_status_porcelain": _git_value("status", "--porcelain"),
            "tool": str(Path(__file__).resolve().relative_to(_repository_root())),
            "tool_sha256": _sha256(Path(__file__).resolve()),
        }
        metrics_path = temporary / OUTPUT_METRICS
        report_path = temporary / OUTPUT_REPORT
        figure_path = temporary / OUTPUT_FIGURE
        _write_json(metrics_path, result)
        write_report(report_path, result)
        write_figure(figure_path, result)
        manifest = {
            "manifest_schema_version": 1,
            "hash_algorithm": "sha256",
            "analysis_id": ANALYSIS_ID,
            "artifacts_sha256": {
                OUTPUT_METRICS: _sha256(metrics_path),
                OUTPUT_REPORT: _sha256(report_path),
                OUTPUT_FIGURE: _sha256(figure_path),
            },
            "source_artifacts_sha256": result["source"],
        }
        _write_json(temporary / OUTPUT_MANIFEST, manifest)
        os.replace(temporary, destination)
        return result
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution-dir", type=Path, required=True)
    parser.add_argument("--temporal-dir", type=Path, required=True)
    parser.add_argument("--static-dir", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = analyze_to_directory(
            args.distribution_dir,
            args.temporal_dir,
            args.static_dir,
            args.reference_dir,
            args.output_dir,
        )
    except (
        OnlineAllocatorError,
        static.FrontierError,
        shadow_reference.ShadowReferenceError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    variant = result["selected_predictor"]["variant"]
    summary = {
        variant: {
            regime: {
                "actions": _record(result, variant, regime)["action_count"],
                "captured_primary_misses": _record(result, variant, regime)[
                    "captured_primary_deadline_misses"
                ],
                "dr_deadline_miss": _record(result, variant, regime)[
                    "dr_policy_deadline_miss_probability"
                ],
            }
            for regime in REGIME_MODES
        }
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
