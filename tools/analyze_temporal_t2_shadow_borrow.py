#!/usr/bin/env python3
"""Evaluate repayment-enforced future credit after a T2 shadow-price test."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import analyze_temporal_t2_distributional_frontier as static
import analyze_temporal_t2_online_allocator as online
import crossfit_temporal_t2_distributions as crossfit


ANALYSIS_SCHEMA_VERSION = 1
ANALYSIS_ID = "temporal-t2-shadow-priced-borrow-repay-v1"
DESIGN_CONTRACT = Path(
    "experiments/model-selection/temporal-t2-shadow-priced-borrow-repay-v1.json"
)
DESIGN_CONTRACT_SHA256 = (
    "95913ef204aa5b9bc36b785cbc5bcf1d9a28d13a8da39d940262a83849bba8c7"
)
OBJECTIVE = "deadline_rescue"
FRAME_GATE = "p_frames_only"
CREDIT_MODES = ("strict_current_credit", "shadow_borrow_repay")
REASONS = (
    "gate",
    "nonpositive_reward",
    "opportunity_price",
    "current_credit",
    "horizon_credit",
    "strict_action",
    "borrowed_action",
)

OUTPUT_METRICS = "temporal_t2_shadow_borrow.json"
OUTPUT_REPORT = "temporal_t2_shadow_borrow.md"
OUTPUT_FIGURE = "temporal_t2_shadow_borrow.png"
OUTPUT_MANIFEST = "artifact_manifest.json"


class ShadowBorrowError(RuntimeError):
    """Raised when the frozen borrow/repay screen cannot be established."""


@dataclass(frozen=True)
class ReplayTrace:
    """Exact row decisions and per-run accounting for one replay policy."""

    policy: np.ndarray
    reasons: np.ndarray
    run_details: dict[static.Unit, dict[str, Any]]
    totals: dict[str, int]


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise ShadowBorrowError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ShadowBorrowError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise ShadowBorrowError(f"{path}: expected a JSON object")
    return value


def _validate_contract() -> dict[str, Any]:
    path = _repository_root() / DESIGN_CONTRACT
    if _sha256(path) != DESIGN_CONTRACT_SHA256:
        raise ShadowBorrowError("shadow-borrow design-contract hash differs")
    contract = _read_json(path)
    policy = contract.get("frozen_policy", {})
    accounting = contract.get("credit_accounting", {})
    if (
        contract.get("schema_version") != 1
        or contract.get("analysis_id") != ANALYSIS_ID
        or contract.get("status")
        != "frozen_after_online_v1_before_borrow_replay"
        or policy.get("objective") != OBJECTIVE
        or policy.get("frame_gate") != FRAME_GATE
        or policy.get("regime_modes") != list(online.REGIME_MODES)
        or accounting.get("initial_credit_us")
        != int(online.INITIAL_CREDIT_US)
        or accounting.get("positive_balance_capacity_us")
        != int(online.CAPACITY_US)
        or accounting.get("causal_refill_rate_us_per_us")
        != float(online.REFILL_RATE)
        or accounting.get("maximum_total_generated_credit_us")
        != int(online.MAXIMUM_GENERATED_CREDIT_US)
    ):
        raise ShadowBorrowError("shadow-borrow design semantics differ")
    return contract


def _load_online_v1(path: Path | str, contract: dict[str, Any]) -> dict[str, Any]:
    source = Path(path).resolve()
    manifest_path = source / online.OUTPUT_MANIFEST
    metrics_path = source / online.OUTPUT_METRICS
    expected_source = contract["source_result"]
    if (
        _sha256(manifest_path) != expected_source["artifact_manifest_sha256"]
        or _sha256(metrics_path) != expected_source["metrics_sha256"]
    ):
        raise ShadowBorrowError("online-v1 frozen source identity differs")
    manifest = _read_json(manifest_path)
    result = _read_json(metrics_path)
    artifacts = manifest.get("artifacts_sha256")
    expected = {
        online.OUTPUT_METRICS,
        online.OUTPUT_REPORT,
        online.OUTPUT_FIGURE,
    }
    if (
        manifest.get("analysis_id") != online.ANALYSIS_ID
        or result.get("analysis_id") != online.ANALYSIS_ID
        or not isinstance(artifacts, dict)
        or set(artifacts) != expected
    ):
        raise ShadowBorrowError("online-v1 artifact closure differs")
    for name, digest in artifacts.items():
        if not isinstance(digest, str) or _sha256(source / name) != digest:
            raise ShadowBorrowError(f"online-v1 artifact hash differs: {name}")
    return result


def credit_decision(
    balance_us: Decimal,
    remaining_refill_us: Decimal,
    cost_us: Decimal,
    credit_mode: str,
) -> str:
    """Classify exact credit after a candidate clears opportunity pricing."""

    if (
        credit_mode not in CREDIT_MODES
        or remaining_refill_us < 0
        or cost_us <= 0
    ):
        raise ShadowBorrowError("invalid credit-decision input")
    if balance_us >= cost_us:
        return "strict_action"
    if credit_mode == "strict_current_credit":
        return "current_credit"
    if balance_us + remaining_refill_us >= cost_us:
        return "borrowed_action"
    return "horizon_credit"


def replay_policy(
    data: static.FrontierDataset,
    context: online.AllocatorContext,
    evaluation_rewards: np.ndarray,
    reference_rewards_by_fold: np.ndarray,
    regime_mode: str,
    credit_mode: str,
) -> ReplayTrace:
    """Replay one frozen shadow policy with exact strict or repayable credit."""

    if (
        regime_mode not in online.REGIME_MODES
        or credit_mode not in CREDIT_MODES
        or evaluation_rewards.shape != (len(data.seeds),)
        or reference_rewards_by_fold.shape
        != (crossfit.FOLD_COUNT, len(data.seeds))
        or not np.all(np.isfinite(evaluation_rewards))
        or np.any(evaluation_rewards < 0)
    ):
        raise ShadowBorrowError("shadow-borrow replay input differs")
    policy = np.zeros(len(data.seeds), dtype=bool)
    reasons = np.full(len(data.seeds), "unclassified", dtype="U24")
    run_details: dict[static.Unit, dict[str, Any]] = {}
    totals: Counter[str] = Counter({reason: 0 for reason in REASONS})
    indices_by_unit = context.indices_by_unit
    for evaluation_fold in range(crossfit.FOLD_COUNT):
        curves, cutpoints = online.build_shadow_curves(
            data,
            context,
            reference_rewards_by_fold[evaluation_fold],
            evaluation_fold,
            FRAME_GATE,
            regime_mode,
        )
        evaluation_units = [
            unit
            for unit, indices in indices_by_unit.items()
            if int(data.folds[indices[0]]) == evaluation_fold
        ]
        if len(evaluation_units) != crossfit.GROUPS_PER_FOLD:
            raise ShadowBorrowError("outer-fold evaluation coverage differs")
        for unit in evaluation_units:
            balance = online.INITIAL_CREDIT_US
            last_time = Decimal(0)
            spend = Decimal(0)
            minimum_balance = balance
            actions = 0
            strict_actions = 0
            borrowed_actions = 0
            busy_sum = 0.0
            busy_count = 0
            predicted_reward = 0.0
            for index in indices_by_unit[unit]:
                current_time = online.decision_time_us(
                    int(data.frame_ids[index])
                )
                if current_time < last_time:
                    raise ShadowBorrowError("run decisions are not chronological")
                balance = min(
                    online.CAPACITY_US,
                    balance + online.REFILL_RATE * (current_time - last_time),
                )
                last_time = current_time
                busy_sum += float(data.primary_busy_20ms[index])
                busy_count += 1
                time_bin = int(context.time_bins[index])
                regime = (
                    0
                    if regime_mode == "global"
                    else online._regime(
                        busy_sum / busy_count, cutpoints[time_bin]
                    )
                )
                if data.frame_types[index] != "P_FRAME":
                    reason = "gate"
                else:
                    reward = float(evaluation_rewards[index])
                    if reward <= 0:
                        reason = "nonpositive_reward"
                    else:
                        cost = Decimal(data.canonical_cost_text[index])
                        remaining_refill = online.REFILL_RATE * (
                            online.MEASUREMENT_DURATION_US - current_time
                        )
                        remaining_budget = balance + remaining_refill
                        opportunity_cost = curves[
                            (time_bin, regime)
                        ].opportunity_cost(remaining_budget)
                        density = reward / float(cost)
                        if density < opportunity_cost:
                            reason = "opportunity_price"
                        else:
                            reason = credit_decision(
                                balance,
                                remaining_refill,
                                cost,
                                credit_mode,
                            )
                            if reason in ("strict_action", "borrowed_action"):
                                balance -= cost
                                spend += cost
                                actions += 1
                                predicted_reward += reward
                                policy[index] = True
                                if reason == "strict_action":
                                    strict_actions += 1
                                else:
                                    borrowed_actions += 1
                                if balance < -remaining_refill:
                                    raise ShadowBorrowError(
                                        "action exceeds causally repayable credit"
                                    )
                reasons[index] = reason
                totals[reason] += 1
                minimum_balance = min(minimum_balance, balance)
            final_balance = min(
                online.CAPACITY_US,
                balance
                + online.REFILL_RATE
                * (online.MEASUREMENT_DURATION_US - last_time),
            )
            if (
                spend > online.MAXIMUM_GENERATED_CREDIT_US
                or final_balance < 0
                or balance
                < -online.REFILL_RATE
                * (online.MEASUREMENT_DURATION_US - last_time)
            ):
                raise ShadowBorrowError("borrow/repay accounting does not close")
            run_details[unit] = {
                "actions": actions,
                "strict_actions": strict_actions,
                "borrowed_actions": borrowed_actions,
                "canonical_reservation_us": float(spend),
                "predicted_reward": predicted_reward,
                "balance_after_last_candidate_us": float(balance),
                "balance_at_measurement_stop_us": float(final_balance),
                "maximum_debt_us": float(max(Decimal(0), -minimum_balance)),
            }
    if (
        set(run_details) != set(data.units)
        or np.any(reasons == "unclassified")
        or sum(totals.values()) != len(data.seeds)
        or totals["strict_action"] + totals["borrowed_action"]
        != int(np.sum(policy))
    ):
        raise ShadowBorrowError("borrow/repay row coverage differs")
    return ReplayTrace(policy, reasons, run_details, dict(totals))


def _decision_summary(
    data: static.FrontierDataset,
    rewards: np.ndarray,
    reasons: np.ndarray,
) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    primary_miss = data.primary_deadline_miss == 1
    for reason in REASONS:
        selected = reasons == reason
        count = int(np.sum(selected))
        misses = int(np.sum(selected & primary_miss))
        summary[reason] = {
            "frame_count": count,
            "primary_deadline_misses": misses,
            "primary_miss_fraction": misses / count if count else 0.0,
            "mean_predicted_reward": (
                float(np.mean(rewards[selected])) if count else 0.0
            ),
            "canonical_reservation_us_if_all_acted": float(
                np.sum(data.canonical_cost_us[selected])
            ),
        }
    return summary


def _time_bin_summary(
    data: static.FrontierDataset,
    context: online.AllocatorContext,
    reasons: np.ndarray,
) -> list[dict[str, Any]]:
    rows = []
    primary_miss = data.primary_deadline_miss == 1
    for time_bin in range(online.TIME_BIN_COUNT):
        in_bin = context.time_bins == time_bin
        decisions = {}
        for reason in REASONS:
            selected = in_bin & (reasons == reason)
            decisions[reason] = {
                "frame_count": int(np.sum(selected)),
                "primary_deadline_misses": int(
                    np.sum(selected & primary_miss)
                ),
            }
        rows.append(
            {
                "time_bin": time_bin,
                "start_us": time_bin * online.TIME_BIN_WIDTH_US,
                "stop_us": (time_bin + 1) * online.TIME_BIN_WIDTH_US,
                "decisions": decisions,
            }
        )
    return rows


def _debt_summary(
    run_details: dict[static.Unit, dict[str, Any]],
) -> dict[str, Any]:
    values = tuple(run_details.values())
    debt = np.asarray([row["maximum_debt_us"] for row in values])
    borrowed = np.asarray([row["borrowed_actions"] for row in values])
    final_balance = np.asarray(
        [row["balance_at_measurement_stop_us"] for row in values]
    )
    return {
        "runs_with_debt": int(np.sum(debt > 0)),
        "mean_maximum_debt_us": float(np.mean(debt)),
        "maximum_debt_us": float(np.max(debt)),
        "mean_borrowed_actions_per_run": float(np.mean(borrowed)),
        "maximum_borrowed_actions_per_run": int(np.max(borrowed)),
        "minimum_final_balance_us": float(np.min(final_balance)),
        "maximum_final_balance_us": float(np.max(final_balance)),
    }


def _transition_summary(
    data: static.FrontierDataset,
    rewards: np.ndarray,
    baseline: np.ndarray,
    candidate: np.ndarray,
) -> dict[str, dict[str, Any]]:
    if (
        baseline.shape != (len(data.seeds),)
        or candidate.shape != baseline.shape
        or baseline.dtype != bool
        or candidate.dtype != bool
    ):
        raise ShadowBorrowError("action-transition masks differ")
    masks = {
        "common": baseline & candidate,
        "strict_only": baseline & ~candidate,
        "borrow_only": candidate & ~baseline,
        "neither": ~baseline & ~candidate,
    }
    primary_miss = data.primary_deadline_miss == 1
    result: dict[str, dict[str, Any]] = {}
    for name, selected in masks.items():
        count = int(np.sum(selected))
        misses = int(np.sum(selected & primary_miss))
        result[name] = {
            "frame_count": count,
            "primary_deadline_misses": misses,
            "primary_miss_fraction": misses / count if count else 0.0,
            "mean_predicted_reward": (
                float(np.mean(rewards[selected])) if count else 0.0
            ),
            "median_predicted_reward": (
                float(np.median(rewards[selected])) if count else 0.0
            ),
            "median_frame_id": (
                float(np.median(data.frame_ids[selected])) if count else None
            ),
            "canonical_reservation_us": float(
                np.sum(data.canonical_cost_us[selected])
            ),
        }
    if sum(row["frame_count"] for row in result.values()) != len(data.seeds):
        raise ShadowBorrowError("action-transition coverage differs")
    return result


def _policy_cdf_components(
    data: static.FrontierDataset,
    cdf: np.ndarray,
    policy: np.ndarray,
    bootstrap: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    phi0, phi1 = crossfit.doubly_robust_cdf_components(
        data.outcome_bins, data.treatment, data.propensity, cdf
    )
    values = phi0 + policy[:, None] * (phi1 - phi0)
    return static._pooled_cluster_means(
        values, data.indices_by_unit(), bootstrap
    )


def _paired_policy_delta(
    data: static.FrontierDataset,
    cdf: np.ndarray,
    candidate: np.ndarray,
    baseline: np.ndarray,
    bootstrap: np.ndarray,
) -> dict[str, Any]:
    candidate_cdf, candidate_bootstrap = _policy_cdf_components(
        data, cdf, candidate, bootstrap
    )
    baseline_cdf, baseline_bootstrap = _policy_cdf_components(
        data, cdf, baseline, bootstrap
    )
    miss_point = -(candidate_cdf[-1] - baseline_cdf[-1])
    miss_samples = -(
        candidate_bootstrap[:, -1] - baseline_bootstrap[:, -1]
    )
    candidate_tail = (
        candidate_cdf[-1] - candidate_cdf[1]
    ) / candidate_cdf[-1]
    baseline_tail = (
        baseline_cdf[-1] - baseline_cdf[1]
    ) / baseline_cdf[-1]
    candidate_tail_samples = (
        candidate_bootstrap[:, -1] - candidate_bootstrap[:, 1]
    ) / candidate_bootstrap[:, -1]
    baseline_tail_samples = (
        baseline_bootstrap[:, -1] - baseline_bootstrap[:, 1]
    ) / baseline_bootstrap[:, -1]
    primary = data.primary_deadline_miss == 1
    return {
        "action_count": int(np.sum(candidate) - np.sum(baseline)),
        "captured_primary_deadline_misses": int(
            np.sum(candidate & primary) - np.sum(baseline & primary)
        ),
        "dr_deadline_miss_probability": static._interval(
            float(miss_point), miss_samples
        ),
        "dr_completed_late18_ratio": static._interval(
            float(candidate_tail - baseline_tail),
            candidate_tail_samples - baseline_tail_samples,
        ),
    }


def _record(
    result: dict[str, Any], regime_mode: str, credit_mode: str
) -> dict[str, Any]:
    matches = [
        row
        for row in result["policies"]
        if row["regime_mode"] == regime_mode
        and row["credit_mode"] == credit_mode
    ]
    if len(matches) != 1:
        raise ShadowBorrowError("cannot resolve shadow-borrow policy record")
    return matches[0]


def _verify_strict_baseline(
    trace: ReplayTrace,
    evaluated: dict[str, Any],
    production_trace: tuple[
        np.ndarray, dict[static.Unit, dict[str, Any]], dict[str, int]
    ],
    frozen_record: dict[str, Any],
) -> None:
    production_policy, production_runs, production_totals = production_trace
    mapped_totals = {
        "gate_rejections": trace.totals["gate"],
        "nonpositive_reward_rejections": trace.totals[
            "nonpositive_reward"
        ],
        "opportunity_cost_rejections": trace.totals["opportunity_price"],
        "current_credit_rejections": trace.totals["current_credit"],
        "actions": trace.totals["strict_action"],
    }
    if (
        not np.array_equal(trace.policy, production_policy)
        or mapped_totals != production_totals
        or trace.totals["borrowed_action"] != 0
        or trace.totals["horizon_credit"] != 0
    ):
        raise ShadowBorrowError("strict online-v1 decision reproduction differs")
    for unit, values in production_runs.items():
        reproduced = trace.run_details[unit]
        for key in (
            "actions",
            "canonical_reservation_us",
            "predicted_reward",
            "balance_after_last_candidate_us",
        ):
            if reproduced[key] != values[key]:
                raise ShadowBorrowError(
                    f"strict online-v1 run accounting differs: {unit} {key}"
                )
    for key in (
        "action_count",
        "captured_primary_deadline_misses",
        "dr_policy_deadline_miss_probability",
        "dr_policy_completed_late18_ratio",
        "mean_canonical_reservation_us_per_run",
    ):
        if evaluated[key] != frozen_record[key]:
            raise ShadowBorrowError(
                f"strict online-v1 evaluated metric differs: {key}"
            )


def analyze_shadow_borrow(
    data: static.FrontierDataset,
    static_result: dict[str, Any],
    reference: online.ShadowReferenceDataset,
    online_v1_result: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate the one frozen borrow mechanism and its exact baseline."""

    variant = reference.selected_variant
    if variant != contract["frozen_policy"]["predictor"]:
        raise ShadowBorrowError("frozen predictor selection differs")
    cdf = data.cdf_by_variant[variant]
    rewards = static.objective_rewards(cdf)[OBJECTIVE]
    reference_rewards = np.full(
        (crossfit.FOLD_COUNT, len(data.seeds)), np.nan, dtype=float
    )
    for fold in range(crossfit.FOLD_COUNT):
        reference_rewards[fold] = static.objective_rewards(
            reference.cdf_by_evaluation_fold[fold]
        )[OBJECTIVE]
    context = online.build_allocator_context(data)
    bootstrap = static._bootstrap_indices(len(data.units))
    static_comparator = online._static_policy(
        static_result, variant, OBJECTIVE, FRAME_GATE
    )
    records: list[dict[str, Any]] = []
    policies: dict[tuple[str, str], np.ndarray] = {}
    for regime_mode in online.REGIME_MODES:
        frozen_record = online._record(
            online_v1_result, variant, regime_mode, OBJECTIVE, FRAME_GATE
        )
        production_trace = online.replay_online_policy(
            data,
            context,
            rewards,
            reference_rewards,
            FRAME_GATE,
            regime_mode,
        )
        for credit_mode in CREDIT_MODES:
            trace = replay_policy(
                data,
                context,
                rewards,
                reference_rewards,
                regime_mode,
                credit_mode,
            )
            evaluated = static.evaluate_policy(
                data, cdf, trace.policy, trace.run_details, bootstrap
            )
            if credit_mode == "strict_current_credit":
                _verify_strict_baseline(
                    trace,
                    evaluated,
                    production_trace,
                    frozen_record,
                )
            records.append(
                {
                    "variant": variant,
                    "objective": OBJECTIVE,
                    "frame_gate": FRAME_GATE,
                    "regime_mode": regime_mode,
                    "credit_mode": credit_mode,
                    "future_evaluation_score_visibility": False,
                    "baseline_online_v1_exact_reproduction": (
                        credit_mode == "strict_current_credit"
                    ),
                    "decision_outcomes": _decision_summary(
                        data, rewards, trace.reasons
                    ),
                    "time_bin_outcomes": _time_bin_summary(
                        data, context, trace.reasons
                    ),
                    "debt": _debt_summary(trace.run_details),
                    **evaluated,
                }
            )
            policies[(regime_mode, credit_mode)] = trace.policy
    for record in records:
        regime_mode = record["regime_mode"]
        baseline_policy = policies[
            (regime_mode, "strict_current_credit")
        ]
        candidate_policy = policies[(regime_mode, record["credit_mode"])]
        record["action_transition_from_strict"] = _transition_summary(
            data,
            rewards,
            baseline_policy,
            candidate_policy,
        )
        record["borrow_minus_strict"] = _paired_policy_delta(
            data,
            cdf,
            candidate_policy,
            baseline_policy,
            bootstrap,
        )
        record["static_372ms_comparator"] = {
            key: static_comparator[key]
            for key in (
                "action_count",
                "captured_primary_deadline_misses",
                "primary_miss_capture_fraction",
                "dr_policy_deadline_miss_probability",
                "dr_policy_completed_late18_ratio",
                "mean_canonical_reservation_us_per_run",
            )
        }
    primary = next(
        row
        for row in records
        if row["regime_mode"]
        == contract["frozen_policy"]["primary_candidate_regime"]
        and row["credit_mode"] == "shadow_borrow_repay"
    )
    thresholds = contract["engineering_screen"]
    checks = {
        "primary_miss_capture_fraction": {
            "actual": primary["primary_miss_capture_fraction"],
            "required_minimum": thresholds[
                "minimum_primary_miss_capture_fraction"
            ],
            "pass": primary["primary_miss_capture_fraction"]
            >= thresholds["minimum_primary_miss_capture_fraction"],
        },
        "dr_deadline_miss_probability": {
            "actual": primary["dr_policy_deadline_miss_probability"],
            "required_maximum": thresholds[
                "maximum_dr_deadline_miss_probability"
            ],
            "pass": primary["dr_policy_deadline_miss_probability"]
            <= thresholds["maximum_dr_deadline_miss_probability"],
        },
        "dr_completed_late18_ratio": {
            "actual": primary["dr_policy_completed_late18_ratio"],
            "required_maximum": thresholds[
                "maximum_dr_completed_late18_ratio"
            ],
            "pass": primary["dr_policy_completed_late18_ratio"]
            <= thresholds["maximum_dr_completed_late18_ratio"],
        },
        "canonical_reservation": {
            "actual_maximum_us_per_run": primary[
                "maximum_canonical_reservation_us_per_run"
            ],
            "required_maximum_us_per_run": thresholds[
                "maximum_canonical_reservation_us_per_run"
            ],
            "pass": primary["maximum_canonical_reservation_us_per_run"]
            <= thresholds["maximum_canonical_reservation_us_per_run"],
        },
        "repayment": {
            "actual_minimum_final_balance_us": primary["debt"][
                "minimum_final_balance_us"
            ],
            "required_minimum_final_balance_us": 0,
            "pass": primary["debt"]["minimum_final_balance_us"] >= 0,
        },
    }
    return {
        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
        "analysis_id": ANALYSIS_ID,
        "evidence_role": contract["evidence_role"],
        "design_contract": {
            "path": str(DESIGN_CONTRACT),
            "sha256": DESIGN_CONTRACT_SHA256,
        },
        "population": static_result["population"],
        "selected_predictor": online_v1_result["selected_predictor"],
        "mechanism": contract["credit_accounting"],
        "policies": records,
        "engineering_screen": {
            "primary_policy": thresholds["primary_policy"],
            "checks": checks,
            "overall_pass": all(item["pass"] for item in checks.values()),
            "decision": thresholds["decision"],
        },
        "bootstrap": static_result["bootstrap"],
        "interpretation_limits": contract["interpretation_limits"],
    }


def write_report(path: Path, result: dict[str, Any]) -> None:
    """Write the exact mechanism comparison and frozen screen decision."""

    lines = [
        "# Temporal-T2 shadow-priced borrow/repay screen",
        "",
        (
            "This opened-data mechanism screen changes only credit liquidity "
            "after the frozen opportunity-price test. Every borrowed "
            "reservation must be repaid by measurement stop."
        ),
        "",
        "## Policy outcomes",
        "",
        (
            "| Regime | Credit | Actions | Borrowed | Captured primary misses | "
            "Capture | DR miss | DR late18 | Mean reservation | Max debt |"
        ),
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for regime in online.REGIME_MODES:
        for credit_mode in CREDIT_MODES:
            row = _record(result, regime, credit_mode)
            lines.append(
                f"| {regime} | {credit_mode} | {row['action_count']:,} | "
                f"{row['decision_outcomes']['borrowed_action']['frame_count']:,} | "
                f"{row['captured_primary_deadline_misses']:,} | "
                f"{100 * row['primary_miss_capture_fraction']:.2f}% | "
                f"{100 * row['dr_policy_deadline_miss_probability']:.3f}% | "
                f"{100 * row['dr_policy_completed_late18_ratio']:.3f}% | "
                f"{row['mean_canonical_reservation_us_per_run'] / 1000:.2f} ms | "
                f"{row['debt']['maximum_debt_us'] / 1000:.2f} ms |"
            )
    lines.extend(
        [
            "",
            "## Observed primary misses by decision route",
            "",
            (
                "| Regime | Credit | Opportunity reject | Current-credit reject | "
                "Horizon reject | Strict action | Borrowed action | Gate |"
            ),
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for regime in online.REGIME_MODES:
        for credit_mode in CREDIT_MODES:
            row = _record(result, regime, credit_mode)
            outcomes = row["decision_outcomes"]
            values = [
                outcomes[name]["primary_deadline_misses"]
                for name in (
                    "opportunity_price",
                    "current_credit",
                    "horizon_credit",
                    "strict_action",
                    "borrowed_action",
                    "gate",
                )
            ]
            lines.append(
                f"| {regime} | {credit_mode} | "
                + " | ".join(f"{value:,}" for value in values)
                + " |"
            )
    screen = result["engineering_screen"]
    lines.extend(
        [
            "",
            "## Frozen engineering screen",
            "",
            f"Overall: **{'PASS' if screen['overall_pass'] else 'FAIL'}**.",
            "",
            "| Check | Actual | Pass |",
            "| --- | ---: | ---: |",
        ]
    )
    for name, check in screen["checks"].items():
        actual_name = next(key for key in check if key.startswith("actual"))
        lines.append(
            f"| {name} | {check[actual_name]:.6g} | "
            f"{'yes' if check['pass'] else 'no'} |"
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
    """Plot direct capture, decision routing, DR outcomes, and accounting."""

    static_row = _record(
        result, "global", "strict_current_credit"
    )["static_372ms_comparator"]
    rows = [
        _record(result, regime, mode)
        for regime in online.REGIME_MODES
        for mode in CREDIT_MODES
    ]
    labels = ["Static 372", "Strict G", "Borrow G", "Strict C", "Borrow C"]
    colors = ["#222222", "#4477AA", "#66AADD", "#AA4466", "#EE6677"]
    figure, axes = plt.subplots(2, 2, figsize=(14, 9))

    capture = [
        static_row["primary_miss_capture_fraction"],
        *[row["primary_miss_capture_fraction"] for row in rows],
    ]
    x = np.arange(len(labels))
    axes[0, 0].bar(x, capture, color=colors)
    axes[0, 0].set_xticks(x, labels, rotation=18, ha="right")
    axes[0, 0].set_ylim(0, 1)
    axes[0, 0].set_ylabel("Observed primary misses captured")
    axes[0, 0].set_title("Static-to-online allocation gap")
    axes[0, 0].grid(axis="y", alpha=0.25)

    route_names = (
        "strict_action",
        "borrowed_action",
        "current_credit",
        "horizon_credit",
        "opportunity_price",
        "gate",
    )
    route_labels = (
        "strict action",
        "borrowed action",
        "current-credit reject",
        "horizon reject",
        "opportunity reject",
        "I-frame gate",
    )
    route_colors = (
        "#228833",
        "#66CC99",
        "#EE6677",
        "#CC3311",
        "#BBBBBB",
        "#AA4499",
    )
    bottom = np.zeros(len(rows))
    for reason, label, color in zip(
        route_names, route_labels, route_colors, strict=True
    ):
        values = np.asarray(
            [
                row["decision_outcomes"][reason][
                    "primary_deadline_misses"
                ]
                for row in rows
            ]
        )
        axes[0, 1].bar(
            np.arange(len(rows)), values, bottom=bottom, label=label, color=color
        )
        bottom += values
    axes[0, 1].set_xticks(
        np.arange(len(rows)), labels[1:], rotation=18, ha="right"
    )
    axes[0, 1].set_ylabel("Observed primary misses")
    axes[0, 1].set_title("Decision routing")
    axes[0, 1].legend(frameon=False, fontsize=8, ncol=2)
    axes[0, 1].grid(axis="y", alpha=0.25)

    axes[1, 0].scatter(
        100 * static_row["dr_policy_deadline_miss_probability"],
        100 * static_row["dr_policy_completed_late18_ratio"],
        color=colors[0],
        s=90,
        label=labels[0],
    )
    for label, color, row in zip(labels[1:], colors[1:], rows, strict=True):
        axes[1, 0].scatter(
            100 * row["dr_policy_deadline_miss_probability"],
            100 * row["dr_policy_completed_late18_ratio"],
            color=color,
            s=80,
            label=label,
        )
    axes[1, 0].set_xlabel("DR deadline-miss probability (%)")
    axes[1, 0].set_ylabel("DR completed late18 ratio (%)")
    axes[1, 0].set_title("Counterfactual outcome screen")
    axes[1, 0].legend(frameon=False, fontsize=8)
    axes[1, 0].grid(alpha=0.25)

    reservation = [
        static_row["mean_canonical_reservation_us_per_run"] / 1000,
        *[row["mean_canonical_reservation_us_per_run"] / 1000 for row in rows],
    ]
    axes[1, 1].bar(x, reservation, color=colors)
    axes[1, 1].axhline(372, color="#666666", linestyle="--", linewidth=1)
    axes[1, 1].set_xticks(x, labels, rotation=18, ha="right")
    axes[1, 1].set_ylabel("Canonical reservation (ms/run)")
    axes[1, 1].set_title("Exact total resource and maximum debt")
    axes[1, 1].set_ylim(0, 410)
    for index, row in enumerate(rows, start=1):
        axes[1, 1].text(
            index,
            reservation[index] + 5,
            f"max debt\n{row['debt']['maximum_debt_us'] / 1000:.0f} ms",
            ha="center",
            va="bottom",
            fontsize=7,
        )
    axes[1, 1].grid(axis="y", alpha=0.25)
    figure.suptitle("Temporal-T2 shadow-priced future-credit replay")
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
    online_v1_dir: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Run the frozen borrow screen and write checksum-closed artifacts."""

    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ShadowBorrowError(
            f"refusing to overwrite output directory: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        contract = _validate_contract()
        data = static.load_frontier_dataset(distribution_dir, temporal_dir)
        static_path = Path(static_dir).resolve()
        static_result = online._load_static_result(static_path, data)
        reference = online.load_shadow_reference(
            reference_dir, data, static_path, static_result
        )
        online_path = Path(online_v1_dir).resolve()
        online_v1_result = _load_online_v1(online_path, contract)
        result = analyze_shadow_borrow(
            data, static_result, reference, online_v1_result, contract
        )
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
                reference.path / online.shadow_reference.OUTPUT_MANIFEST
            ),
            "shadow_reference_metrics_sha256": _sha256(
                reference.path / online.shadow_reference.OUTPUT_METRICS
            ),
            "shadow_reference_predictions_sha256": _sha256(
                reference.path / online.shadow_reference.OUTPUT_PREDICTIONS
            ),
            "online_v1_manifest_sha256": _sha256(
                online_path / online.OUTPUT_MANIFEST
            ),
            "online_v1_metrics_sha256": _sha256(
                online_path / online.OUTPUT_METRICS
            ),
        }
        result["provenance"] = {
            "project_git_commit": _git_value("rev-parse", "HEAD"),
            "project_git_status_porcelain": _git_value(
                "status", "--porcelain"
            ),
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
            "design_contract_sha256": DESIGN_CONTRACT_SHA256,
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
    parser.add_argument("--online-v1-dir", type=Path, required=True)
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
            args.online_v1_dir,
            args.output_dir,
        )
    except (
        ShadowBorrowError,
        online.OnlineAllocatorError,
        online.shadow_reference.ShadowReferenceError,
        static.FrontierError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    summary = {
        "engineering_screen_pass": result["engineering_screen"]["overall_pass"],
        "policies": {
            f"{row['regime_mode']}:{row['credit_mode']}": {
                "actions": row["action_count"],
                "captured_primary_misses": row[
                    "captured_primary_deadline_misses"
                ],
                "dr_deadline_miss": row[
                    "dr_policy_deadline_miss_probability"
                ],
            }
            for row in result["policies"]
        },
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
