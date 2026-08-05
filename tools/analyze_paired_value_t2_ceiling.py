#!/usr/bin/env python3
"""Decompose closed-loop temporal-T2 action, ranking, and evidence ceilings.

The analysis deliberately distinguishes quantities that are exactly observed
from quantities that require missing counterfactual secondary-copy outcomes.
Canonical reservation frontiers use a separate budget for every simulation
run; unused credit is never pooled across independent seeds.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from compare_paired_value_t2_admission import (
    ComparisonError,
    active_policy_score,
    as_optional_float,
    percentile,
    read_csv_by_frame,
    read_json,
    resolve_aggregate,
    sha256_file,
)


POLICY = "paired_value_duplication_t2"
DEFAULT_TARGET_MISS_RATE = 0.004
TAIL_THRESHOLD_US = 18_000.0


class CeilingError(RuntimeError):
    """Raised when source campaigns cannot support the ceiling analysis."""


Unit = tuple[int, int]


@dataclass(frozen=True)
class FrameEvidence:
    """One policy decision joined to its generated-frame outcome."""

    frame_id: int
    feature_evaluated: bool
    passes_threshold: bool
    action: bool
    decision_status: str
    score: float | None
    canonical_cost_us: Decimal | None
    primary_miss: bool
    primary_completed_late18: bool
    final_miss: bool
    union_latency_us: float | None


@dataclass(frozen=True)
class RunEvidence:
    """One seed/run policy arm and its resource contract."""

    unit: Unit
    run_id: str
    frames: dict[int, FrameEvidence]
    measurement_duration_us: Decimal
    guard_fraction: Decimal
    initial_credit_us: Decimal
    measured_secondary_airtime_us: float

    @property
    def refill_budget_us(self) -> Decimal:
        """Return credit generated during the measurement interval."""
        return self.guard_fraction * self.measurement_duration_us

    @property
    def finite_run_budget_us(self) -> Decimal:
        """Return startup credit plus measurement-interval refill."""
        return self.initial_credit_us + self.refill_budget_us


@dataclass(frozen=True)
class CampaignEvidence:
    """All policy runs from one matched campaign."""

    label: str
    aggregate_path: Path
    manifest: dict[str, Any]
    runs: dict[Unit, RunEvidence]


def _flag(row: dict[str, str], field: str, source: Path) -> bool:
    """Parse one required binary CSV field."""
    value = row.get(field)
    if value not in {"0", "1"}:
        raise CeilingError(f"{source}: {field} is not binary")
    return value == "1"


def _optional_flag(
    row: dict[str, str], field: str, source: Path
) -> bool | None:
    """Parse one optional binary CSV field."""
    value = row.get(field, "")
    if value == "":
        return None
    if value not in {"0", "1"}:
        raise CeilingError(f"{source}: {field} is not binary or empty")
    return value == "1"


def _decimal(value: Any, context: str) -> Decimal:
    """Parse a finite decimal value exactly from serialized text."""
    try:
        result = Decimal(str(value))
    except Exception as error:
        raise CeilingError(f"{context}: invalid decimal {value!r}") from error
    if not result.is_finite():
        raise CeilingError(f"{context}: non-finite decimal")
    return result


def _primary_outcome(
    decision: dict[str, str], frame: dict[str, str], source: Path
) -> tuple[bool, bool]:
    """Return primary deadline-miss and completed-late18 indicators."""
    copy_id = decision.get("primary_copy_id")
    if copy_id not in {"0", "1"}:
        raise CeilingError(f"{source}: unsupported primary copy {copy_id!r}")
    completion = as_optional_float(frame.get(f"copy_{copy_id}_completion_us", ""))
    generation = float(frame["generation_time_us"])
    deadline = generation + float(frame["deadline_us"])
    latency = None if completion is None else completion - generation
    return (
        completion is None or completion > deadline,
        latency is not None and latency > TAIL_THRESHOLD_US,
    )


def _load_run(unit: Unit, run_id: str, run_dir: Path) -> RunEvidence:
    """Load one raw policy run and validate the fields used by this analysis."""
    decision_path = run_dir / "paired_value_t2_decisions.csv"
    frame_path = run_dir / "frames.csv"
    config_path = run_dir / "resolved_config.json"
    summary_path = run_dir / "secondary_airtime_summary.json"
    for path in (decision_path, frame_path, config_path, summary_path):
        if not path.is_file():
            raise CeilingError(f"{run_dir}: missing {path.name}")

    decisions = read_csv_by_frame(decision_path)
    frames = read_csv_by_frame(frame_path)
    if decisions.keys() != frames.keys():
        raise CeilingError(f"{run_dir}: decision and frame IDs differ")

    resolved = read_json(config_path)
    if resolved.get("policy") != POLICY:
        raise CeilingError(f"{config_path}: expected policy {POLICY}")
    settings = resolved.get("pairedValueDuplicationT2")
    if not isinstance(settings, dict):
        raise CeilingError(f"{config_path}: missing paired T2 settings")
    start_ns = int(settings["measurement_start_ns"])
    stop_ns = int(settings["measurement_stop_ns"])
    if stop_ns <= start_ns or (stop_ns - start_ns) % 1000:
        raise CeilingError(f"{config_path}: invalid measurement interval")
    duration_us = Decimal(stop_ns - start_ns) / Decimal(1000)
    fraction = _decimal(settings["budget_fraction"], f"{config_path}: fraction")

    airtime_summary = read_json(summary_path)
    initial_credit = _decimal(
        airtime_summary["initial_bucket_capacity_us"],
        f"{summary_path}: initial credit",
    )
    measured = float(airtime_summary["tagged_secondary_tx_airtime_us"])
    if measured < 0 or not math.isfinite(measured):
        raise CeilingError(f"{summary_path}: invalid measured airtime")

    evidence: dict[int, FrameEvidence] = {}
    for frame_id, decision in decisions.items():
        frame = frames[frame_id]
        feature_evaluated = _flag(decision, "feature_evaluated", decision_path)
        action = _flag(decision, "secondary_launched", decision_path)
        if action != (decision.get("decision_status") == "action"):
            raise CeilingError(f"{decision_path}: action status differs for {frame_id}")
        score_text = active_policy_score(decision)
        score = as_optional_float(score_text)
        passes_value = _optional_flag(
            decision, "passes_score_threshold", decision_path
        )
        passes_threshold = bool(passes_value)
        cost_text = decision.get("canonical_reserved_airtime_us", "")
        cost = (
            _decimal(cost_text, f"{decision_path}: frame {frame_id} cost")
            if cost_text
            else None
        )
        if feature_evaluated:
            if score is None or cost is None or cost <= 0 or passes_value is None:
                raise CeilingError(
                    f"{decision_path}: evaluated frame {frame_id} lacks score/cost"
                )
        elif score is not None or cost is not None or passes_value not in {None, False}:
            raise CeilingError(
                f"{decision_path}: unevaluated frame {frame_id} has model evidence"
            )
        primary_miss, primary_late18 = _primary_outcome(
            decision, frame, frame_path
        )
        evidence[frame_id] = FrameEvidence(
            frame_id=frame_id,
            feature_evaluated=feature_evaluated,
            passes_threshold=passes_threshold,
            action=action,
            decision_status=decision["decision_status"],
            score=score,
            canonical_cost_us=cost,
            primary_miss=primary_miss,
            primary_completed_late18=primary_late18,
            final_miss=_flag(frame, "deadline_miss", frame_path),
            union_latency_us=as_optional_float(frame.get("union_latency_us", "")),
        )

    return RunEvidence(
        unit=unit,
        run_id=run_id,
        frames=evidence,
        measurement_duration_us=duration_us,
        guard_fraction=fraction,
        initial_credit_us=initial_credit,
        measured_secondary_airtime_us=measured,
    )


def load_campaign(path: Path, label: str) -> CampaignEvidence:
    """Load every paired-value policy arm from one aggregate."""
    aggregate_path = resolve_aggregate(path)
    aggregate = read_json(aggregate_path)
    manifest_path = aggregate_path.parent / "experiment_manifest.json"
    manifest = read_json(manifest_path)
    runs: dict[Unit, RunEvidence] = {}
    for record in aggregate.get("runs", []):
        if record.get("policy") != POLICY:
            continue
        unit = (int(record["seed"]), int(record["run"]))
        if unit in runs:
            raise CeilingError(f"{aggregate_path}: duplicate policy unit {unit}")
        run_id = str(record["run_id"])
        runs[unit] = _load_run(unit, run_id, aggregate_path.parent / run_id)
    if not runs:
        raise CeilingError(f"{aggregate_path}: no {POLICY} runs")
    source = {
        "aggregate_path": str(aggregate_path),
        "aggregate_sha256": sha256_file(aggregate_path),
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "experiment": manifest.get("experiment", ""),
        "project_commit": manifest.get("project_commit", ""),
        "matrix_sha256": manifest.get("matrix_sha256", ""),
    }
    return CampaignEvidence(
        label=label,
        aggregate_path=aggregate_path,
        manifest=source,
        runs=runs,
    )


def _check_campaign_alignment(
    reference: CampaignEvidence,
    campaigns: Iterable[CampaignEvidence],
    *,
    require_primary_labels: bool,
) -> None:
    """Require identical units and frames, optionally including miss labels."""
    for campaign in campaigns:
        if campaign.runs.keys() != reference.runs.keys():
            raise CeilingError(f"{campaign.label}: seed/run units differ")
        for unit, reference_run in reference.runs.items():
            run = campaign.runs[unit]
            if run.frames.keys() != reference_run.frames.keys():
                raise CeilingError(f"{campaign.label} {unit}: frame IDs differ")
            for frame_id, frame in run.frames.items():
                expected = reference_run.frames[frame_id]
                if require_primary_labels and frame.primary_miss != expected.primary_miss:
                    raise CeilingError(
                        f"{campaign.label} {unit} frame {frame_id}: "
                        "primary miss label differs"
                    )


def _selected_summary(frames: Iterable[FrameEvidence]) -> dict[str, Any]:
    """Summarize a selected action population."""
    rows = list(frames)
    cost = sum(
        (frame.canonical_cost_us or Decimal(0) for frame in rows), Decimal(0)
    )
    return {
        "actions": len(rows),
        "canonical_reserved_airtime_us": float(cost),
        "captured_primary_misses": sum(frame.primary_miss for frame in rows),
        "captured_primary_completed_late18": sum(
            frame.primary_completed_late18 for frame in rows
        ),
    }


def _score_ordered_selection(
    frames: Iterable[FrameEvidence], budget_us: Decimal
) -> list[FrameEvidence]:
    """Spend a static canonical budget in descending scalar-score order."""
    ordered = sorted(
        (frame for frame in frames if frame.feature_evaluated),
        key=lambda frame: (-float(frame.score), frame.frame_id),
    )
    selected: list[FrameEvidence] = []
    spent = Decimal(0)
    for frame in ordered:
        cost = frame.canonical_cost_us
        if cost is None:
            raise CeilingError("evaluated score selection lacks canonical cost")
        if spent + cost <= budget_us:
            selected.append(frame)
            spent += cost
    return selected


def _score_count_selection(
    frames: Iterable[FrameEvidence], count: int
) -> list[FrameEvidence]:
    """Select a fixed count in descending scalar-score order."""
    if count < 0:
        raise CeilingError("negative factual action count")
    ordered = sorted(
        (frame for frame in frames if frame.feature_evaluated),
        key=lambda frame: (-float(frame.score), frame.frame_id),
    )
    return ordered[:count]


def _perfect_primary_selection(
    frames: Iterable[FrameEvidence], budget_us: Decimal
) -> list[FrameEvidence]:
    """Maximize captured primary misses under one static canonical budget.

    Every miss has unit value, so selecting missed frames by nondecreasing cost
    is an exact maximum-cardinality knapsack solution.
    """
    misses = sorted(
        (
            frame
            for frame in frames
            if frame.feature_evaluated and frame.primary_miss
        ),
        key=lambda frame: (frame.canonical_cost_us, frame.frame_id),
    )
    selected: list[FrameEvidence] = []
    spent = Decimal(0)
    for frame in misses:
        cost = frame.canonical_cost_us
        if cost is None:
            raise CeilingError("oracle candidate lacks canonical cost")
        if spent + cost <= budget_us:
            selected.append(frame)
            spent += cost
    return selected


def _campaign_factual(campaign: CampaignEvidence) -> dict[str, Any]:
    """Summarize exact factual policy behavior."""
    all_frames = [
        frame for run in campaign.runs.values() for frame in run.frames.values()
    ]
    actions = [frame for frame in all_frames if frame.action]
    acted_primary = [frame for frame in actions if frame.primary_miss]
    rescues = [frame for frame in acted_primary if not frame.final_miss]
    p99 = [
        percentile(
            [
                frame.union_latency_us
                for frame in run.frames.values()
                if frame.union_latency_us is not None
            ],
            0.99,
        )
        for run in campaign.runs.values()
    ]
    selected = _selected_summary(actions)
    selected.update(
        {
            "all_generated_frames": len(all_frames),
            "primary_misses": sum(frame.primary_miss for frame in all_frames),
            "final_misses": sum(frame.final_miss for frame in all_frames),
            "final_miss_rate": sum(frame.final_miss for frame in all_frames)
            / len(all_frames),
            "acted_primary_misses": len(acted_primary),
            "acted_primary_misses_rescued": len(rescues),
            "conditional_rescue_rate": (
                len(rescues) / len(acted_primary) if acted_primary else None
            ),
            "mean_per_run_completed_p99_us": statistics.fmean(p99),
            "mean_measured_secondary_airtime_us": statistics.fmean(
                run.measured_secondary_airtime_us for run in campaign.runs.values()
            ),
        }
    )
    return selected


def _minimum_uniform_score_cap(
    campaign: CampaignEvidence, required_capture: int
) -> dict[str, Any]:
    """Find the smallest common per-run score cap reaching a capture target."""
    ordered_runs = [
        sorted(
            (
                frame
                for frame in run.frames.values()
                if frame.feature_evaluated
            ),
            key=lambda frame: (-float(frame.score), frame.frame_id),
        )
        for run in campaign.runs.values()
    ]
    maximum = max(len(rows) for rows in ordered_runs)
    cap: int | None = None
    for candidate_cap in range(maximum + 1):
        captured = sum(
            sum(frame.primary_miss for frame in rows[:candidate_cap])
            for rows in ordered_runs
        )
        if captured >= required_capture:
            cap = candidate_cap
            break
    if cap is None:
        raise CeilingError(
            f"{campaign.label}: scalar score never reaches target capture"
        )
    selected = [frame for rows in ordered_runs for frame in rows[:cap]]
    per_run_costs = [
        sum(
            (frame.canonical_cost_us or Decimal(0) for frame in rows[:cap]),
            Decimal(0),
        )
        for rows in ordered_runs
    ]
    result = _selected_summary(selected)
    result.update(
        {
            "uniform_action_cap_per_run": cap,
            "maximum_per_run_canonical_reserved_airtime_us": float(
                max(per_run_costs)
            ),
        }
    )
    return result


def _frontier_for_campaign(
    campaign: CampaignEvidence, required_capture: int
) -> dict[str, Any]:
    """Build static score frontiers for one campaign's active scalar score."""
    factual_count: list[FrameEvidence] = []
    refill_score: list[FrameEvidence] = []
    finite_score: list[FrameEvidence] = []
    threshold_refill: list[FrameEvidence] = []
    threshold_costs: list[Decimal] = []
    threshold_runs_over_refill = 0
    threshold_runs_over_finite = 0
    for run in campaign.runs.values():
        rows = list(run.frames.values())
        action_count = sum(frame.action for frame in rows)
        factual_count.extend(_score_count_selection(rows, action_count))
        refill_score.extend(_score_ordered_selection(rows, run.refill_budget_us))
        finite_score.extend(_score_ordered_selection(rows, run.finite_run_budget_us))
        threshold = [
            frame for frame in rows if frame.feature_evaluated and frame.passes_threshold
        ]
        threshold_refill.extend(
            _score_ordered_selection(threshold, run.refill_budget_us)
        )
        cost = sum(
            (frame.canonical_cost_us or Decimal(0) for frame in threshold),
            Decimal(0),
        )
        threshold_costs.append(cost)
        threshold_runs_over_refill += cost > run.refill_budget_us
        threshold_runs_over_finite += cost > run.finite_run_budget_us

    all_threshold = [
        frame
        for run in campaign.runs.values()
        for frame in run.frames.values()
        if frame.feature_evaluated and frame.passes_threshold
    ]
    total_refill = sum(
        (run.refill_budget_us for run in campaign.runs.values()), Decimal(0)
    )
    total_threshold_cost = sum(threshold_costs, Decimal(0))
    pooled_threshold = _selected_summary(all_threshold)
    pooled_threshold.update(
        {
            "campaign_wide_cost_within_pooled_refill": (
                total_threshold_cost <= total_refill
            ),
            "runs_exceeding_refill_budget": threshold_runs_over_refill,
            "runs_exceeding_finite_run_budget": threshold_runs_over_finite,
            "mean_per_run_canonical_reserved_airtime_us": float(
                statistics.fmean(threshold_costs)
            ),
            "interpretation": (
                "aggregate pooled-credit sensitivity only; independent runs "
                "cannot transfer unused budget"
            ),
        }
    )
    return {
        "same_factual_action_count_score_order": _selected_summary(factual_count),
        "per_run_refill_budget_score_order": _selected_summary(refill_score),
        "per_run_finite_budget_score_order": _selected_summary(finite_score),
        "per_run_refill_budget_threshold_only": _selected_summary(
            threshold_refill
        ),
        "pooled_all_threshold_passers_sensitivity": pooled_threshold,
        "minimum_uniform_score_cap_for_target": _minimum_uniform_score_cap(
            campaign, required_capture
        ),
    }


def _perfect_primary_frontier(reference: CampaignEvidence) -> dict[str, Any]:
    """Build exact primary-miss capture oracles under per-run budgets."""
    refill: list[FrameEvidence] = []
    finite: list[FrameEvidence] = []
    per_run_miss_costs: list[Decimal] = []
    for run in reference.runs.values():
        refill.extend(_perfect_primary_selection(run.frames.values(), run.refill_budget_us))
        finite.extend(
            _perfect_primary_selection(run.frames.values(), run.finite_run_budget_us)
        )
        per_run_miss_costs.append(
            sum(
                (
                    frame.canonical_cost_us or Decimal(0)
                    for frame in run.frames.values()
                    if frame.feature_evaluated and frame.primary_miss
                ),
                Decimal(0),
            )
        )
    result = {
        "per_run_refill_budget": _selected_summary(refill),
        "per_run_finite_budget": _selected_summary(finite),
        "maximum_per_run_cost_to_act_on_every_eligible_primary_miss_us": float(
            max(per_run_miss_costs)
        ),
        "mean_per_run_cost_to_act_on_every_eligible_primary_miss_us": float(
            statistics.fmean(per_run_miss_costs)
        ),
        "oracle_information": "future primary deadline-miss label",
        "secondary_outcome_assumption": "reported separately; not identified here",
    }
    return result


def _support_diagnostic(
    reference: CampaignEvidence, support: Iterable[CampaignEvidence]
) -> dict[str, Any]:
    """Quantify observed and missing secondary outcomes on oracle candidates."""
    support = list(support)
    observed = ever_rescued = always_rescued = conflicting = observations = 0
    rescue_observations = 0
    primary_label_differences: dict[str, int] = {
        campaign.label: 0 for campaign in support
    }
    eligible_misses = 0
    guard_rejected_misses = 0
    guard_observed = guard_ever_rescued = 0
    for unit, reference_run in reference.runs.items():
        for frame_id, reference_frame in reference_run.frames.items():
            if not reference_frame.feature_evaluated or not reference_frame.primary_miss:
                continue
            eligible_misses += 1
            outcomes: list[bool] = []
            for campaign in support:
                frame = campaign.runs[unit].frames[frame_id]
                primary_label_differences[campaign.label] += (
                    frame.primary_miss != reference_frame.primary_miss
                )
                if frame.action:
                    outcomes.append(frame.primary_miss and not frame.final_miss)
            if outcomes:
                observed += 1
                observations += len(outcomes)
                rescue_observations += sum(outcomes)
                ever_rescued += any(outcomes)
                always_rescued += all(outcomes)
                conflicting += len(set(outcomes)) > 1
            if reference_frame.decision_status == "airtime_guard_rejected":
                guard_rejected_misses += 1
                guard_observed += bool(outcomes)
                guard_ever_rescued += bool(outcomes) and any(outcomes)
    return {
        "support_campaigns": [campaign.label for campaign in support],
        "eligible_primary_miss_label_differences_from_reference": (
            primary_label_differences
        ),
        "eligible_primary_misses": eligible_misses,
        "frames_with_at_least_one_action_outcome": observed,
        "frames_without_an_action_outcome": eligible_misses - observed,
        "frames_ever_rescued": ever_rescued,
        "frames_always_rescued_when_observed": always_rescued,
        "frames_with_policy_dependent_rescue_outcome": conflicting,
        "action_outcome_observations": observations,
        "rescue_observations": rescue_observations,
        "action_observation_rescue_rate": (
            rescue_observations / observations if observations else None
        ),
        "reference_guard_rejected_primary_misses": guard_rejected_misses,
        "guard_rejected_with_other_policy_action_outcome": guard_observed,
        "guard_rejected_ever_rescued_in_support": guard_ever_rescued,
        "exact_secondary_outcome_oracle_identified": False,
        "reason": (
            "some candidate actions are never observed and common actions can "
            "change outcome across closed-loop policies because traffic interferes"
        ),
    }


def _add_projected_misses(
    frontier: dict[str, Any], primary_misses: int, rescue_rate: float
) -> None:
    """Annotate a selected population with explicit rescue sensitivities."""
    captured = int(frontier["captured_primary_misses"])
    frontier["perfect_rescue_final_misses"] = primary_misses - captured
    frontier["reference_rate_projected_final_misses"] = (
        primary_misses - captured * rescue_rate
    )
    frontier["reference_rate_projected_miss_rate"] = (
        frontier["reference_rate_projected_final_misses"]
        / int(frontier.get("all_generated_frames", 1))
        if frontier.get("all_generated_frames")
        else None
    )


def analyze_ceiling(
    campaigns: dict[str, Path],
    reference_label: str,
    support_campaigns: dict[str, Path] | None = None,
    *,
    str_misses: int,
    target_miss_rate: float = DEFAULT_TARGET_MISS_RATE,
) -> dict[str, Any]:
    """Return the closed-loop ceiling decomposition."""
    if reference_label not in campaigns:
        raise CeilingError(f"unknown reference label {reference_label!r}")
    if str_misses <= 0 or not 0 < target_miss_rate < 1:
        raise CeilingError("invalid STR miss count or target rate")
    loaded = {label: load_campaign(path, label) for label, path in campaigns.items()}
    reference = loaded[reference_label]
    extras = {
        label: load_campaign(path, label)
        for label, path in (support_campaigns or {}).items()
        if label not in loaded
    }
    all_support = {**loaded, **extras}
    _check_campaign_alignment(
        reference, loaded.values(), require_primary_labels=True
    )
    _check_campaign_alignment(
        reference, extras.values(), require_primary_labels=False
    )

    factual = {label: _campaign_factual(campaign) for label, campaign in loaded.items()}
    reference_factual = factual[reference_label]
    all_generated = int(reference_factual["all_generated_frames"])
    primary_misses = int(reference_factual["primary_misses"])
    rescue_rate = reference_factual["conditional_rescue_rate"]
    if rescue_rate is None or not 0 < rescue_rate <= 1:
        raise CeilingError("reference campaign has no usable factual rescue rate")

    strict_rate_max = math.ceil(target_miss_rate * all_generated) - 1
    strict_half_str_max = math.ceil(str_misses / 2) - 1
    target_max_misses = min(strict_rate_max, strict_half_str_max)
    perfect_required = primary_misses - target_max_misses
    rate_required = math.ceil(perfect_required / rescue_rate)

    scalar_frontiers = {
        label: _frontier_for_campaign(campaign, rate_required)
        for label, campaign in loaded.items()
    }
    for campaign_frontiers in scalar_frontiers.values():
        for frontier in campaign_frontiers.values():
            frontier["all_generated_frames"] = all_generated
            _add_projected_misses(frontier, primary_misses, rescue_rate)

    oracle = _perfect_primary_frontier(reference)
    for name in ("per_run_refill_budget", "per_run_finite_budget"):
        oracle[name]["all_generated_frames"] = all_generated
        _add_projected_misses(oracle[name], primary_misses, rescue_rate)

    reference_runs = list(reference.runs.values())
    unique_costs = sorted(
        {
            frame.canonical_cost_us
            for run in reference_runs
            for frame in run.frames.values()
            if frame.feature_evaluated
        }
    )
    if None in unique_costs:
        raise CeilingError("evaluated population contains an empty canonical cost")
    refill_budgets = {run.refill_budget_us for run in reference_runs}
    finite_budgets = {run.finite_run_budget_us for run in reference_runs}
    if len(refill_budgets) != 1 or len(finite_budgets) != 1:
        raise CeilingError("resource budgets differ across reference runs")
    refill_budget = next(iter(refill_budgets))
    finite_budget = next(iter(finite_budgets))

    candidate_frames = [
        frame
        for run in reference_runs
        for frame in run.frames.values()
        if frame.feature_evaluated
    ]
    fixed_frames = [
        frame
        for run in reference_runs
        for frame in run.frames.values()
        if not frame.feature_evaluated
    ]
    return {
        "schema_version": 1,
        "analysis": "paired_value_t2_closed_loop_ceiling_decomposition",
        "evidence_role": "engineering_diagnostic_not_a_qualification_gate",
        "reference_campaign": reference_label,
        "sources": {
            label: campaign.manifest for label, campaign in all_support.items()
        },
        "population": {
            "paired_units": len(reference.runs),
            "all_generated_frames": all_generated,
            "feature_evaluated_action_candidates": len(candidate_frames),
            "primary_misses": primary_misses,
            "eligible_primary_misses": sum(
                frame.primary_miss for frame in candidate_frames
            ),
            "fixed_outside_current_candidate_population_misses": sum(
                frame.primary_miss for frame in fixed_frames
            ),
        },
        "target": {
            "strict_miss_rate_below": target_miss_rate,
            "strict_more_than_half_reduction_from_str_misses": str_misses,
            "maximum_integer_final_misses": target_max_misses,
            "required_captured_primary_misses_under_perfect_rescue": perfect_required,
            "required_captured_primary_misses_at_reference_rescue_rate": rate_required,
            "reference_factual_rescue_rate": rescue_rate,
        },
        "canonical_static_resource_contract": {
            "interpretation": (
                "reservation-cost proxy frontier; not an exact replay of measured "
                "airtime settlements or contention"
            ),
            "measurement_duration_us": float(reference_runs[0].measurement_duration_us),
            "budget_fraction": float(reference_runs[0].guard_fraction),
            "initial_credit_us": float(reference_runs[0].initial_credit_us),
            "per_run_refill_budget_us": float(refill_budget),
            "per_run_finite_budget_us": float(finite_budget),
            "unique_evaluated_canonical_costs_us": [
                float(cost) for cost in unique_costs
            ],
            "maximum_actions_at_refill_budget": (
                int(refill_budget // unique_costs[0]) if len(unique_costs) == 1 else None
            ),
            "maximum_actions_at_finite_budget": (
                int(finite_budget // unique_costs[0]) if len(unique_costs) == 1 else None
            ),
            "no_credit_pooling_across_runs": True,
        },
        "factual": factual,
        "scalar_score_frontiers": scalar_frontiers,
        "perfect_primary_information_oracle": oracle,
        "secondary_outcome_support": _support_diagnostic(
            reference, all_support.values()
        ),
        "identification_limits": [
            "Unacted frames have no factual secondary-copy completion outcome.",
            "Common actions can have different union outcomes under different "
            "closed-loop action sets because secondary traffic interferes.",
            "Static canonical reservation sums do not replay transient "
            "reservations, measured airtime debits, settlements, or contention.",
            "Counterfactual completed-frame P99 is not identified by this stage.",
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render a compact scientific interpretation of the decomposition."""
    population = report["population"]
    target = report["target"]
    resource = report["canonical_static_resource_contract"]
    reference = report["reference_campaign"]
    factual = report["factual"]
    frontiers = report["scalar_score_frontiers"]
    oracle = report["perfect_primary_information_oracle"]
    support = report["secondary_outcome_support"]
    lines = [
        "# Temporal-T2 closed-loop ceiling decomposition",
        "",
        "Engineering diagnostic only; this is not a qualification result.",
        "",
        f"Reference campaign: {reference}. Matched units: {population['paired_units']}; "
        f"generated frames: {population['all_generated_frames']:,}.",
        f"Primary misses: {population['primary_misses']:,}; current action-candidate "
        f"misses: {population['eligible_primary_misses']:,}; fixed outside-candidate "
        f"misses: {population['fixed_outside_current_candidate_population_misses']:,}.",
        "",
        "## Factual campaigns",
        "",
        "| Campaign | Actions | Final misses | Miss rate | Mean P99 | "
        "Mean secondary airtime |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, values in factual.items():
        lines.append(
            f"| {label} | {values['actions']:,} | {values['final_misses']:,} | "
            f"{100 * values['final_miss_rate']:.4f}% | "
            f"{values['mean_per_run_completed_p99_us'] / 1000:.3f} ms | "
            f"{values['mean_measured_secondary_airtime_us'] / 1000:.3f} ms/run |"
        )
    lines.extend(
        [
            "",
            "## Canonical reservation frontier",
            "",
            f"Every evaluated candidate costs "
            f"{resource['unique_evaluated_canonical_costs_us'][0]:.6f} us. "
            f"The 0.6% refill-only budget is "
            f"{resource['per_run_refill_budget_us'] / 1000:.3f} ms/run "
            f"({resource['maximum_actions_at_refill_budget']} actions); startup "
            f"credit raises the finite-run proxy to "
            f"{resource['per_run_finite_budget_us'] / 1000:.3f} ms/run "
            f"({resource['maximum_actions_at_finite_budget']} actions).",
            "",
            "| Score | Frontier | Captured primary misses | Perfect-rescue misses | "
            "Reference-rate projected misses |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )
    frontier_names = (
        ("same_factual_action_count_score_order", "same factual action count"),
        ("per_run_refill_budget_score_order", "per-run refill budget"),
        ("per_run_finite_budget_score_order", "per-run finite budget"),
        ("pooled_all_threshold_passers_sensitivity", "pooled threshold sensitivity"),
    )
    for label, values in frontiers.items():
        for key, display in frontier_names:
            item = values[key]
            lines.append(
                f"| {label} | {display} | {item['captured_primary_misses']:,} | "
                f"{item['perfect_rescue_final_misses']:.0f} | "
                f"{item['reference_rate_projected_final_misses']:.2f} |"
            )
    perfect = oracle["per_run_refill_budget"]
    lines.extend(
        [
            f"| Perfect primary information | per-run refill budget | "
            f"{perfect['captured_primary_misses']:,} | "
            f"{perfect['perfect_rescue_final_misses']:.0f} | "
            f"{perfect['reference_rate_projected_final_misses']:.2f} |",
            "",
            f"The target permits at most {target['maximum_integer_final_misses']} "
            f"misses. At {100 * target['reference_factual_rescue_rate']:.2f}% "
            f"rescue efficiency it requires capturing "
            f"{target['required_captured_primary_misses_at_reference_rescue_rate']} "
            "primary misses.",
            "",
        ]
    )
    reference_threshold = frontiers[reference][
        "pooled_all_threshold_passers_sensitivity"
    ]
    required_score = frontiers[reference]["minimum_uniform_score_cap_for_target"]
    lines.extend(
        [
            f"The {reference} threshold set averages "
            f"{reference_threshold['mean_per_run_canonical_reserved_airtime_us'] / 1000:.3f} "
            "ms/run, but "
            f"{reference_threshold['runs_exceeding_refill_budget']} runs exceed the "
            "refill-only budget and "
            f"{reference_threshold['runs_exceeding_finite_run_budget']} exceed the "
            "finite-run proxy. Its pooled projection therefore transfers unused "
            "credit across independent runs and is not implementable.",
            "",
            f"The current {reference} score first captures the required "
            f"{target['required_captured_primary_misses_at_reference_rescue_rate']} "
            f"primary misses at a uniform cap of "
            f"{required_score['uniform_action_cap_per_run']} actions/run, requiring "
            f"up to "
            f"{required_score['maximum_per_run_canonical_reserved_airtime_us'] / 1000:.3f} "
            "ms/run of canonical reservation.",
            "",
            "## Identification boundary",
            "",
            f"Action outcomes are observed for "
            f"{support['frames_with_at_least_one_action_outcome']}/"
            f"{support['eligible_primary_misses']} eligible primary misses; "
            f"{support['frames_without_an_action_outcome']} remain unobserved. "
            f"Among observed frames, {support['frames_with_policy_dependent_rescue_outcome']} "
            "change rescue outcome across policies.",
            "",
            "Consequently, the primary-information oracle is an exact miss-capture "
            "frontier but not an exact secondary-outcome or P99 oracle. The next "
            "stage needs cross-fitted randomized potential-outcome distributions and "
            "an implementable online allocator.",
            "",
        ]
    )
    return "\n".join(lines)


def _parse_labeled_path(value: str) -> tuple[str, Path]:
    """Parse one LABEL=PATH command-line value."""
    if "=" not in value:
        raise argparse.ArgumentTypeError("expected LABEL=PATH")
    label, raw_path = value.split("=", 1)
    if not label or not raw_path:
        raise argparse.ArgumentTypeError("expected nonempty LABEL=PATH")
    return label, Path(raw_path)


def _write_json(path: Path, value: Any) -> None:
    """Write deterministic finite JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """Run the command-line ceiling decomposition."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--campaign",
        action="append",
        type=_parse_labeled_path,
        required=True,
        metavar="LABEL=AGGREGATE",
        help="factual matched campaign; repeat for V2 and V5",
    )
    parser.add_argument(
        "--support-campaign",
        action="append",
        type=_parse_labeled_path,
        default=[],
        metavar="LABEL=AGGREGATE",
        help="additional matched campaign used only for action-outcome support",
    )
    parser.add_argument("--reference-label", required=True)
    parser.add_argument("--str-misses", type=int, required=True)
    parser.add_argument(
        "--target-miss-rate", type=float, default=DEFAULT_TARGET_MISS_RATE
    )
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path, required=True)
    args = parser.parse_args()
    campaigns = dict(args.campaign)
    support = dict(args.support_campaign)
    if len(campaigns) != len(args.campaign) or len(support) != len(
        args.support_campaign
    ):
        parser.error("campaign labels must be unique")
    try:
        report = analyze_ceiling(
            campaigns,
            args.reference_label,
            support,
            str_misses=args.str_misses,
            target_miss_rate=args.target_miss_rate,
        )
        _write_json(args.json_output, report)
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    except (CeilingError, ComparisonError, OSError, ValueError, KeyError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
