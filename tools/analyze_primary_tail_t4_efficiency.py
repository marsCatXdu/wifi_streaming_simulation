#!/usr/bin/env python3
"""Diagnose frame-level selection and rescue efficiency in a T4 campaign."""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from analyze_primary_tail_t4_campaign import (
    ALLOWED_DECISIONS,
    BASELINES,
    DECLARED_BALANCED_ARM,
    DECLARED_DEFICIT_ARM,
    DECLARED_SERIALIZED_T4_GATES,
    CampaignError,
    Thresholds,
    _close,
    _finite,
    _flag,
    _integer,
    _pair_key,
    _percentile,
    _read_config,
    _read_csv,
    _read_json,
    _run_directory,
    analyze_campaign as validate_campaign,
)
from validate_outputs import ValidationError


FRAME_COLUMNS = {
    "run_id",
    "frame_id",
    "generation_time_us",
    "packet_count",
    "frame_type",
    "deadline_us",
    "duplicated",
    "union_completion_us",
    "union_latency_us",
    "copy_0_completion_us",
    "copy_1_completion_us",
    "deadline_miss",
    "incomplete",
    "completion_mode",
}

DECISION_COLUMNS = {
    "run_id",
    "frame_id",
    "sample_stage",
    "sample_offset_us",
    "actionable",
    "decision",
    "secondary_launched",
}

SETTLEMENT_COLUMNS = {
    "run_id",
    "frame_id",
    "measured_airtime_us",
    "fallback",
}

STAGES = ("T0", "T4")
OUTCOMES = (
    "primary_on_time_latency_benefit",
    "primary_on_time_no_benefit",
    "primary_miss_deadline_rescue",
    "primary_miss_late_completion",
    "primary_miss_incomplete",
)
PRICE_QUALIFIED_DECISIONS = {"action", "airtime_deferred", "launch_rejected"}
TOKEN_QUALIFIED_DECISIONS = {"action", "launch_rejected"}
PRICE_GATE_EVALUATED_DECISIONS = PRICE_QUALIFIED_DECISIONS | {"price_rejected"}


@dataclass(frozen=True)
class Frame:
    """One reconstructed frame outcome."""

    run_id: str
    frame_id: int
    generation_time_us: int
    packet_count: int
    frame_type: str
    deadline_us: int
    duplicated: bool
    union_completion_us: int | None
    union_latency_us: int | None
    primary_completion_us: int | None
    secondary_completion_us: int | None
    deadline_miss: bool
    incomplete: bool
    completion_mode: str

    @property
    def key(self) -> tuple[str, int]:
        """Return an identifier unique within the campaign."""
        return self.run_id, self.frame_id

    @property
    def absolute_deadline_us(self) -> int:
        """Return the absolute application deadline."""
        return self.generation_time_us + self.deadline_us

    @property
    def primary_latency_us(self) -> int | None:
        """Return primary-copy latency when that copy completed."""
        if self.primary_completion_us is None:
            return None
        return self.primary_completion_us - self.generation_time_us

    @property
    def primary_miss(self) -> bool:
        """Return whether the independently observable primary copy missed."""
        if self.deadline_us == 0:
            return False
        return (
            self.primary_completion_us is None
            or self.primary_completion_us > self.absolute_deadline_us
        )


@dataclass(frozen=True)
class Decision:
    """One validated controller sample."""

    frame_key: tuple[str, int]
    stage: str
    actionable: bool
    decision: str
    launched: bool


@dataclass(frozen=True)
class Action:
    """One launched secondary action joined to its factual outcome."""

    frame: Frame
    stage: str
    measured_airtime_us: float
    fallback: bool
    outcome: str
    duplicate_recovery: bool
    duplicate_no_benefit: bool


@dataclass(frozen=True)
class RunEvidence:
    """Evidence used by the diagnostic for one campaign run."""

    seed: int
    run: int
    run_id: str
    run_dir: Path
    frames: tuple[Frame, ...]
    decisions: tuple[Decision, ...] = ()
    actions: tuple[Action, ...] = ()
    validated_summary_p99_us: float | None = None

    @property
    def pair(self) -> tuple[int, int]:
        """Return the independent paired unit."""
        return self.seed, self.run


def _optional_integer(value: Any, description: str) -> int | None:
    if value in (None, ""):
        return None
    return _integer(value, description)


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _optional_percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    return _percentile(values, probability)


def _load_frames(run_dir: Path, run_id: str) -> tuple[Frame, ...]:
    path = run_dir / "frames.csv"
    rows = _read_csv(path, FRAME_COLUMNS)
    if not rows:
        raise CampaignError(f"{path}: no frame evidence")
    frames: dict[int, Frame] = {}
    for row in rows:
        if row.get("run_id") != run_id:
            raise CampaignError(f"{path}: run_id mismatch")
        frame_id = _integer(row.get("frame_id"), f"{path}: frame_id")
        if frame_id in frames:
            raise CampaignError(f"{path}: duplicate frame {frame_id}")
        generation = _integer(
            row.get("generation_time_us"), f"{path}: generation_time_us"
        )
        deadline = _integer(row.get("deadline_us"), f"{path}: deadline_us")
        packet_count = _integer(row.get("packet_count"), f"{path}: packet_count")
        if packet_count <= 0:
            raise CampaignError(f"{path}: frame {frame_id} has no packets")
        frame_type = row.get("frame_type", "")
        if not frame_type:
            raise CampaignError(f"{path}: frame {frame_id} has no frame type")
        union_completion = _optional_integer(
            row.get("union_completion_us"), f"{path}: union_completion_us"
        )
        union_latency = _optional_integer(
            row.get("union_latency_us"), f"{path}: union_latency_us"
        )
        primary_completion = _optional_integer(
            row.get("copy_0_completion_us"), f"{path}: copy_0_completion_us"
        )
        secondary_completion = _optional_integer(
            row.get("copy_1_completion_us"), f"{path}: copy_1_completion_us"
        )
        incomplete = _flag(row.get("incomplete"), f"{path}: incomplete")
        miss = _flag(row.get("deadline_miss"), f"{path}: deadline_miss")
        if incomplete != (union_completion is None):
            raise CampaignError(
                f"{path}: frame {frame_id} completion and incomplete flag disagree"
            )
        if (union_completion is None) != (union_latency is None):
            raise CampaignError(
                f"{path}: frame {frame_id} union completion and latency disagree"
            )
        if union_completion is not None:
            if union_completion < generation:
                raise CampaignError(f"{path}: frame {frame_id} completes before generation")
            if union_latency != union_completion - generation:
                raise CampaignError(f"{path}: frame {frame_id} union latency is inconsistent")
        for label, completion in (
            ("primary", primary_completion),
            ("secondary", secondary_completion),
        ):
            if completion is not None and completion < generation:
                raise CampaignError(
                    f"{path}: frame {frame_id} {label} copy completes before generation"
                )
            if (
                completion is not None
                and union_completion is not None
                and union_completion > completion
            ):
                raise CampaignError(
                    f"{path}: frame {frame_id} union completes after a complete copy"
                )
        if union_completion is None and (
            primary_completion is not None or secondary_completion is not None
        ):
            raise CampaignError(
                f"{path}: frame {frame_id} has a complete copy but no union completion"
            )
        expected_miss = deadline > 0 and (
            union_completion is None or union_completion > generation + deadline
        )
        if miss != expected_miss:
            raise CampaignError(f"{path}: frame {frame_id} deadline flag is inconsistent")
        frames[frame_id] = Frame(
            run_id=run_id,
            frame_id=frame_id,
            generation_time_us=generation,
            packet_count=packet_count,
            frame_type=frame_type,
            deadline_us=deadline,
            duplicated=_flag(row.get("duplicated"), f"{path}: duplicated"),
            union_completion_us=union_completion,
            union_latency_us=union_latency,
            primary_completion_us=primary_completion,
            secondary_completion_us=secondary_completion,
            deadline_miss=miss,
            incomplete=incomplete,
            completion_mode=row.get("completion_mode", ""),
        )
    return tuple(frames[frame_id] for frame_id in sorted(frames))


def _action_outcome(frame: Frame) -> str:
    if not frame.primary_miss:
        if (
            frame.union_completion_us is not None
            and frame.primary_completion_us is not None
            and frame.union_completion_us < frame.primary_completion_us
        ):
            return "primary_on_time_latency_benefit"
        return "primary_on_time_no_benefit"
    if not frame.deadline_miss:
        return "primary_miss_deadline_rescue"
    if frame.union_completion_us is not None:
        return "primary_miss_late_completion"
    return "primary_miss_incomplete"


def _load_adaptive_evidence(
    run_dir: Path,
    run_id: str,
    frames: tuple[Frame, ...],
) -> tuple[tuple[Decision, ...], tuple[Action, ...]]:
    frame_by_id = {frame.frame_id: frame for frame in frames}
    decision_path = run_dir / "adaptive_airtime_decisions.csv"
    decision_rows = _read_csv(decision_path, DECISION_COLUMNS)
    decisions: list[Decision] = []
    seen_samples: set[tuple[int, str]] = set()
    action_stage_by_frame: dict[int, str] = {}
    for row in decision_rows:
        if row.get("run_id") != run_id:
            raise CampaignError(f"{decision_path}: run_id mismatch")
        frame_id = _integer(row.get("frame_id"), f"{decision_path}: frame_id")
        if frame_id not in frame_by_id:
            raise CampaignError(f"{decision_path}: unknown frame {frame_id}")
        stage = row.get("sample_stage", "")
        offset = _integer(row.get("sample_offset_us"), f"{decision_path}: offset")
        expected_stage = {0: "T0", 4000: "T4"}.get(offset)
        if stage not in STAGES or stage != expected_stage:
            raise CampaignError(f"{decision_path}: invalid frame/stage sample")
        sample = (frame_id, stage)
        if sample in seen_samples:
            raise CampaignError(f"{decision_path}: duplicate frame/stage sample {sample}")
        seen_samples.add(sample)
        decision = row.get("decision", "")
        if decision not in ALLOWED_DECISIONS:
            raise CampaignError(f"{decision_path}: unknown decision {decision!r}")
        launched = _flag(
            row.get("secondary_launched"), f"{decision_path}: secondary_launched"
        )
        if launched != (decision == "action"):
            raise CampaignError(f"{decision_path}: action and launch flag disagree")
        if launched:
            if frame_id in action_stage_by_frame:
                raise CampaignError(f"{decision_path}: frame {frame_id} launched twice")
            action_stage_by_frame[frame_id] = stage
        decisions.append(
            Decision(
                frame_key=(run_id, frame_id),
                stage=stage,
                actionable=_flag(row.get("actionable"), f"{decision_path}: actionable"),
                decision=decision,
                launched=launched,
            )
        )
    expected_samples = {(frame.frame_id, stage) for frame in frames for stage in STAGES}
    if seen_samples != expected_samples:
        raise CampaignError(f"{decision_path}: expected exactly T0 and T4 for every frame")

    settlement_path = run_dir / "secondary_airtime_settlements.csv"
    settlement_rows = _read_csv(settlement_path, SETTLEMENT_COLUMNS)
    settlements: dict[int, tuple[float, bool]] = {}
    for row in settlement_rows:
        if row.get("run_id") != run_id:
            raise CampaignError(f"{settlement_path}: run_id mismatch")
        frame_id = _integer(row.get("frame_id"), f"{settlement_path}: frame_id")
        if frame_id in settlements:
            raise CampaignError(f"{settlement_path}: duplicate frame {frame_id}")
        settlements[frame_id] = (
            _finite(
                row.get("measured_airtime_us"),
                f"{settlement_path}: measured_airtime_us",
                nonnegative=True,
            ),
            _flag(row.get("fallback"), f"{settlement_path}: fallback"),
        )
    if set(settlements) != set(action_stage_by_frame):
        raise CampaignError(
            f"{settlement_path}: settlement frames do not exactly match launched actions"
        )

    actions: list[Action] = []
    for frame_id in sorted(action_stage_by_frame):
        frame = frame_by_id[frame_id]
        if not frame.duplicated:
            raise CampaignError(
                f"{run_dir}/frames.csv: launched frame {frame_id} is not duplicated"
            )
        measured, fallback = settlements[frame_id]
        recovery = frame.union_completion_us is not None and (
            frame.primary_completion_us is None
            or frame.union_completion_us < frame.primary_completion_us
        )
        no_benefit = (
            frame.union_completion_us is not None
            and frame.primary_completion_us is not None
            and frame.union_completion_us == frame.primary_completion_us
        )
        actions.append(
            Action(
                frame=frame,
                stage=action_stage_by_frame[frame_id],
                measured_airtime_us=measured,
                fallback=fallback,
                outcome=_action_outcome(frame),
                duplicate_recovery=recovery,
                duplicate_no_benefit=no_benefit,
            )
        )
    if {frame.frame_id for frame in frames if frame.duplicated} != set(action_stage_by_frame):
        raise CampaignError(
            f"{run_dir}/frames.csv: duplicated frames do not exactly match launched actions"
        )
    return tuple(decisions), tuple(actions)


def _validated_classification(
    config: dict[str, Any], run: dict[str, Any], run_dir: Path
) -> tuple[str, str | None] | None:
    aggregate_identity = (run.get("topology"), run.get("policy"))
    resolved_identity = (config.get("topology"), config.get("policy"))
    if aggregate_identity != resolved_identity:
        raise CampaignError(f"{run_dir}: aggregate and resolved topology/policy disagree")
    for baseline, identity in BASELINES.items():
        if resolved_identity == identity:
            return baseline, None
    if resolved_identity == ("dual_interface", "adaptive_airtime_duplication"):
        adaptive = config.get("adaptiveAirtimeDuplication")
        if not isinstance(adaptive, dict):
            raise CampaignError(f"{run_dir}: missing validated full-copy controller config")
        prices = adaptive.get("decision_offset_shadow_prices")
        if not isinstance(prices, dict):
            raise CampaignError(f"{run_dir}: missing validated stage prices")
        gate = _finite(prices.get("4000"), f"{run_dir}: T4 gate")
        matches = [
            arm_id
            for arm_id, declared in DECLARED_SERIALIZED_T4_GATES.items()
            if gate == declared
        ]
        if len(matches) != 1:
            raise CampaignError(f"{run_dir}: full-copy gate no longer matches validation")
        return "full_copy", matches[0]
    if resolved_identity == ("dual_interface", "adaptive_deficit_duplication"):
        adaptive = config.get("adaptiveDeficitDuplication")
        if not isinstance(adaptive, dict):
            raise CampaignError(f"{run_dir}: missing validated deficit controller config")
        prices = adaptive.get("decision_offset_shadow_prices")
        gate = _finite(
            prices.get("4000") if isinstance(prices, dict) else None,
            f"{run_dir}: T4 gate",
        )
        if gate != DECLARED_SERIALIZED_T4_GATES[DECLARED_BALANCED_ARM]:
            raise CampaignError(f"{run_dir}: deficit gate no longer matches validation")
        return "primary_deficit", DECLARED_DEFICIT_ARM
    return None


def _load_indexes(
    validation: dict[str, Any],
) -> tuple[
    dict[str, dict[tuple[int, int], RunEvidence]],
    dict[str, dict[tuple[int, int], RunEvidence]],
    int,
]:
    baselines: dict[str, dict[tuple[int, int], RunEvidence]] = {
        baseline: {} for baseline in BASELINES
    }
    arms: dict[str, dict[tuple[int, int], RunEvidence]] = {
        arm_id: {} for arm_id in validation["arms"]
    }
    ignored = 0
    for serialized in validation["source_aggregates"]:
        aggregate_path = Path(serialized)
        aggregate = _read_json(aggregate_path)
        runs = aggregate.get("runs")
        if not isinstance(runs, list):
            raise CampaignError(f"{aggregate_path}: aggregate must contain a runs list")
        for run in runs:
            if not isinstance(run, dict):
                raise CampaignError(f"{aggregate_path}: run entry is not an object")
            run_dir = _run_directory(run, aggregate_path)
            config = _read_config(run, run_dir)
            classification = _validated_classification(config, run, run_dir)
            if classification is None:
                ignored += 1
                continue
            kind, arm_id = classification
            seed, run_number = _pair_key(run, config, run_dir)
            run_id = run.get("run_id")
            assert isinstance(run_id, str)
            frames = _load_frames(run_dir, run_id)
            decisions: tuple[Decision, ...] = ()
            actions: tuple[Action, ...] = ()
            summary_p99: float | None = None
            if kind not in BASELINES:
                decisions, actions = _load_adaptive_evidence(run_dir, run_id, frames)
                summary = _read_json(run_dir / "summary.json")
                summary_p99 = _finite(
                    summary.get("latency_p99_us"),
                    f"{run_dir}/summary.json: latency_p99_us",
                    nonnegative=True,
                )
            evidence = RunEvidence(
                seed=seed,
                run=run_number,
                run_id=run_id,
                run_dir=run_dir,
                frames=frames,
                decisions=decisions,
                actions=actions,
                validated_summary_p99_us=summary_p99,
            )
            index = baselines[kind] if kind in BASELINES else arms.get(arm_id or "")
            if index is None:
                raise CampaignError(f"{run_dir}: arm is absent from strict validation report")
            if evidence.pair in index:
                raise CampaignError(f"duplicate {kind} evidence for seed/run {evidence.pair}")
            index[evidence.pair] = evidence

    expected_pairs = {
        (int(item["seed"]), int(item["run"])) for item in validation["paired_units"]
    }
    for name, index in [*baselines.items(), *arms.items()]:
        if set(index) != expected_pairs:
            raise CampaignError(f"{name}: frame diagnostic paired-unit set is incomplete")
    if ignored != validation.get("ignored_noncampaign_run_count"):
        raise CampaignError("diagnostic and strict validator disagree on ignored runs")
    return baselines, arms, ignored


def _selection_funnel(decisions: Iterable[Decision]) -> dict[str, Any]:
    rows = list(decisions)
    decision_counts = {
        decision: sum(row.decision == decision for row in rows)
        for decision in sorted(ALLOWED_DECISIONS)
    }
    actionable = sum(row.actionable for row in rows)
    gate_evaluated = sum(
        row.decision in PRICE_GATE_EVALUATED_DECISIONS for row in rows
    )
    price_qualified = sum(
        row.decision in PRICE_QUALIFIED_DECISIONS for row in rows
    )
    token_qualified = sum(
        row.decision in TOKEN_QUALIFIED_DECISIONS for row in rows
    )
    launched = sum(row.launched for row in rows)
    if not (
        launched
        <= token_qualified
        <= price_qualified
        <= gate_evaluated
        <= actionable
        <= len(rows)
    ):
        raise CampaignError("validated controller decisions do not form a nested funnel")
    return {
        "sampled_count": len(rows),
        "actionable_count": actionable,
        "price_gate_evaluated_count": gate_evaluated,
        "excluded_before_price_gate_count": len(rows) - gate_evaluated,
        "price_qualified_count": price_qualified,
        "token_qualified_count": token_qualified,
        "launched_count": launched,
        "price_qualified_rate_given_actionable": _safe_ratio(
            price_qualified, actionable
        ),
        "price_qualified_rate_given_price_gate_evaluated": _safe_ratio(
            price_qualified, gate_evaluated
        ),
        "token_qualified_rate_given_price_qualified": _safe_ratio(
            token_qualified, price_qualified
        ),
        "launch_rate_given_token_qualified": _safe_ratio(launched, token_qualified),
        "decision_counts": decision_counts,
    }


def _action_outcomes(actions: Iterable[Action]) -> dict[str, Any]:
    rows = list(actions)
    counts = {outcome: sum(row.outcome == outcome for row in rows) for outcome in OUTCOMES}
    primary_misses = sum(row.frame.primary_miss for row in rows)
    rescues = counts["primary_miss_deadline_rescue"]
    recoveries = sum(row.duplicate_recovery for row in rows)
    quantified_accelerations = [
        float(row.frame.primary_completion_us - row.frame.union_completion_us)
        for row in rows
        if row.frame.primary_completion_us is not None
        and row.frame.union_completion_us is not None
        and row.frame.union_completion_us < row.frame.primary_completion_us
    ]
    rescue_slack = [
        float(row.frame.deadline_us - row.frame.union_latency_us)
        for row in rows
        if row.outcome == "primary_miss_deadline_rescue"
        and row.frame.union_latency_us is not None
    ]
    return {
        "action_count": len(rows),
        "acted_primary_on_time_count": len(rows) - primary_misses,
        "acted_primary_miss_count": primary_misses,
        "deadline_rescue_count": rescues,
        "duplicate_recovery_count": recoveries,
        "duplicate_no_benefit_count": sum(row.duplicate_no_benefit for row in rows),
        "primary_incomplete_union_recovery_count": sum(
            row.duplicate_recovery and row.frame.primary_completion_us is None
            for row in rows
        ),
        "primary_miss_rate_given_action": _safe_ratio(primary_misses, len(rows)),
        "deadline_rescue_rate_given_action": _safe_ratio(rescues, len(rows)),
        "deadline_rescue_rate_given_acted_primary_miss": _safe_ratio(
            rescues, primary_misses
        ),
        "duplicate_recovery_rate_given_action": _safe_ratio(recoveries, len(rows)),
        "outcome_counts": counts,
        "quantified_primary_completion_acceleration_us": _value_summary(
            quantified_accelerations
        ),
        "deadline_rescue_slack_us": _value_summary(rescue_slack),
        "completion_mode_counts": {
            mode: sum(row.frame.completion_mode == mode for row in rows)
            for mode in sorted({row.frame.completion_mode for row in rows})
        },
    }


def _factual_outcomes(frames: Iterable[Frame], actions: Iterable[Action]) -> dict[str, Any]:
    frame_rows = list(frames)
    action_rows = list(actions)
    action_keys = {action.frame.key for action in action_rows}
    primary_misses = sum(frame.primary_miss for frame in frame_rows)
    result = _action_outcomes(action_rows)
    result.update({
        "frame_count": len(frame_rows),
        "primary_on_time_count": len(frame_rows) - primary_misses,
        "primary_deadline_miss_count": primary_misses,
        "union_deadline_miss_count": sum(frame.deadline_miss for frame in frame_rows),
        "unacted_primary_miss_count": sum(
            frame.primary_miss and frame.key not in action_keys for frame in frame_rows
        ),
        "primary_state_counts": {
            "on_time": sum(not frame.primary_miss for frame in frame_rows),
            "late": sum(
                frame.primary_completion_us is not None and frame.primary_miss
                for frame in frame_rows
            ),
            "incomplete": sum(
                frame.primary_completion_us is None for frame in frame_rows
            ),
        },
    })
    return result


def _airtime_efficiency(actions: Iterable[Action]) -> dict[str, Any]:
    rows = list(actions)
    total = sum(row.measured_airtime_us for row in rows)
    rescues = [row for row in rows if row.outcome == "primary_miss_deadline_rescue"]
    recoveries = [row for row in rows if row.duplicate_recovery]
    rescue_airtime = sum(row.measured_airtime_us for row in rescues)
    by_outcome: dict[str, Any] = {}
    for outcome in OUTCOMES:
        selected = [row for row in rows if row.outcome == outcome]
        measured = sum(row.measured_airtime_us for row in selected)
        by_outcome[outcome] = {
            "action_count": len(selected),
            "measured_airtime_us": measured,
            "mean_measured_airtime_per_action_us": _safe_ratio(
                measured, len(selected)
            ),
            "share_of_measured_airtime": _safe_ratio(measured, total),
        }
    return {
        "action_count": len(rows),
        "fallback_settlement_count": sum(row.fallback for row in rows),
        "total_measured_airtime_us": total,
        "mean_measured_airtime_per_action_us": _safe_ratio(total, len(rows)),
        "total_measured_airtime_per_deadline_rescue_us": _safe_ratio(
            total, len(rescues)
        ),
        "successful_rescue_airtime_per_deadline_rescue_us": _safe_ratio(
            rescue_airtime, len(rescues)
        ),
        "total_measured_airtime_per_duplicate_recovery_us": _safe_ratio(
            total, len(recoveries)
        ),
        "deadline_rescues_per_measured_airtime_ms": _safe_ratio(
            len(rescues) * 1000.0, total
        ),
        "duplicate_recoveries_per_measured_airtime_ms": _safe_ratio(
            len(recoveries) * 1000.0, total
        ),
        "measured_airtime_on_deadline_rescues_us": rescue_airtime,
        "measured_airtime_not_on_deadline_rescues_us": total - rescue_airtime,
        "share_of_measured_airtime_on_deadline_rescues": _safe_ratio(
            rescue_airtime, total
        ),
        "by_factual_outcome": by_outcome,
    }


def _p99_censoring(
    frames: Iterable[Frame],
    actions: Iterable[Action],
    *,
    validated_summary_p99_us: float | None = None,
) -> dict[str, Any]:
    frame_rows = list(frames)
    action_by_key = {action.frame.key: action for action in actions}
    all_union = [
        float(frame.union_latency_us)
        for frame in frame_rows
        if frame.union_latency_us is not None
    ]
    primary_complete = [
        frame for frame in frame_rows if frame.primary_latency_us is not None
    ]
    primary_latencies = [float(frame.primary_latency_us) for frame in primary_complete]
    fixed_union = [float(frame.union_latency_us) for frame in primary_complete]
    all_p99 = _optional_percentile(all_union, 0.99)
    primary_p99 = _optional_percentile(primary_latencies, 0.99)
    fixed_union_p99 = _optional_percentile(fixed_union, 0.99)
    top_tail = [
        frame
        for frame in frame_rows
        if all_p99 is not None
        and frame.union_latency_us is not None
        and frame.union_latency_us >= all_p99
    ]
    top_actions = [
        action_by_key[frame.key] for frame in top_tail if frame.key in action_by_key
    ]
    result = {
        "scope": "integer_microsecond_frame_csv",
        "percentile_method": "linear type-7",
        "all_union_completed_count": len(all_union),
        "all_union_completed_p99_us": all_p99,
        "primary_completed_count": len(primary_complete),
        "primary_copy_p99_us": primary_p99,
        "union_p99_on_primary_completed_population_us": fixed_union_p99,
        "fixed_population_primary_minus_union_p99_gain_us": (
            None
            if primary_p99 is None or fixed_union_p99 is None
            else primary_p99 - fixed_union_p99
        ),
        "completion_set_composition_shift_p99_us": (
            None
            if all_p99 is None or fixed_union_p99 is None
            else all_p99 - fixed_union_p99
        ),
        "primary_incomplete_count": sum(
            frame.primary_completion_us is None for frame in frame_rows
        ),
        "primary_incomplete_to_union_on_time_count": sum(
            frame.primary_completion_us is None
            and frame.union_completion_us is not None
            and not frame.deadline_miss
            for frame in frame_rows
        ),
        "primary_incomplete_to_union_late_count": sum(
            frame.primary_completion_us is None
            and frame.union_completion_us is not None
            and frame.deadline_miss
            for frame in frame_rows
        ),
        "primary_incomplete_to_union_incomplete_count": sum(
            frame.primary_completion_us is None
            and frame.union_completion_us is None
            for frame in frame_rows
        ),
        "top_one_percent_with_ties": {
            "threshold_us": all_p99,
            "frame_count": len(top_tail),
            "deadline_miss_count": sum(frame.deadline_miss for frame in top_tail),
            "action_count": len(top_actions),
            "launch_stage_counts": {
                stage: sum(action.stage == stage for action in top_actions)
                for stage in STAGES
            },
            "action_outcome_counts": {
                outcome: sum(action.outcome == outcome for action in top_actions)
                for outcome in OUTCOMES
            },
            "primary_incomplete_late_entrant_count": sum(
                frame.primary_completion_us is None and frame.deadline_miss
                for frame in top_tail
            ),
        },
    }
    if validated_summary_p99_us is not None:
        result["validated_summary_headline_union_p99_us"] = validated_summary_p99_us
        result["frame_csv_headline_quantization_delta_us"] = (
            None if all_p99 is None else abs(validated_summary_p99_us - all_p99)
        )
    return result


def _frame_type_action_diagnostics(
    actions: Iterable[Action], frame_types: Iterable[str]
) -> dict[str, Any]:
    action_rows = list(actions)
    return {
        frame_type: {
            "factual_action_outcomes": _action_outcomes(
                row for row in action_rows if row.frame.frame_type == frame_type
            ),
            "airtime_efficiency": _airtime_efficiency(
                row for row in action_rows if row.frame.frame_type == frame_type
            ),
        }
        for frame_type in sorted(set(frame_types))
    }


def _frame_type_diagnostics(
    frames: Iterable[Frame], actions: Iterable[Action]
) -> dict[str, Any]:
    frame_rows = list(frames)
    action_rows = list(actions)
    return {
        frame_type: {
            "factual_outcomes": _factual_outcomes(
                (row for row in frame_rows if row.frame_type == frame_type),
                (row for row in action_rows if row.frame.frame_type == frame_type),
            ),
            "airtime_efficiency": _airtime_efficiency(
                row for row in action_rows if row.frame.frame_type == frame_type
            ),
        }
        for frame_type in sorted({row.frame_type for row in frame_rows})
    }


def _stage_diagnostics(
    decisions: Iterable[Decision],
    actions: Iterable[Action],
    frame_types: Iterable[str],
) -> dict[str, Any]:
    decision_rows = list(decisions)
    action_rows = list(actions)
    types = tuple(sorted(set(frame_types)))
    return {
        stage: {
            "selection_funnel": _selection_funnel(
                row for row in decision_rows if row.stage == stage
            ),
            "factual_action_outcomes": _action_outcomes(
                row for row in action_rows if row.stage == stage
            ),
            "airtime_efficiency": _airtime_efficiency(
                row for row in action_rows if row.stage == stage
            ),
            "by_frame_type": _frame_type_action_diagnostics(
                (row for row in action_rows if row.stage == stage), types
            ),
        }
        for stage in STAGES
    }


def _run_diagnostic(evidence: RunEvidence) -> dict[str, Any]:
    return {
        "seed": evidence.seed,
        "run": evidence.run,
        "run_id": evidence.run_id,
        "selection_funnel": _selection_funnel(evidence.decisions),
        "factual_outcomes": _factual_outcomes(evidence.frames, evidence.actions),
        "airtime_efficiency": _airtime_efficiency(evidence.actions),
        "p99_censoring": _p99_censoring(
            evidence.frames,
            evidence.actions,
            validated_summary_p99_us=evidence.validated_summary_p99_us,
        ),
        "by_frame_type": _frame_type_diagnostics(evidence.frames, evidence.actions),
        "by_launch_stage": _stage_diagnostics(
            evidence.decisions,
            evidence.actions,
            (frame.frame_type for frame in evidence.frames),
        ),
    }


def _arm_diagnostic(
    mechanism: str, ordered: list[RunEvidence]
) -> dict[str, Any]:
    frames = [frame for evidence in ordered for frame in evidence.frames]
    decisions = [row for evidence in ordered for row in evidence.decisions]
    actions = [row for evidence in ordered for row in evidence.actions]
    return {
        "mechanism": mechanism,
        "paired_unit_count": len(ordered),
        "pooled_frame_diagnostics": {
            "scope": "descriptive_pool_across_validated_seed_run_units",
            "selection_funnel": _selection_funnel(decisions),
            "factual_outcomes": _factual_outcomes(frames, actions),
            "airtime_efficiency": _airtime_efficiency(actions),
            "p99_censoring": _p99_censoring(frames, actions),
            "by_frame_type": _frame_type_diagnostics(frames, actions),
            "by_launch_stage": _stage_diagnostics(
                decisions,
                actions,
                (frame.frame_type for frame in frames),
            ),
        },
        "by_paired_unit": [_run_diagnostic(evidence) for evidence in ordered],
    }


def _latency_delta_summary(deltas: list[float]) -> dict[str, Any]:
    return {
        "count": len(deltas),
        "mean_us": statistics.mean(deltas) if deltas else None,
        "p50_us": _optional_percentile(deltas, 0.50),
        "p90_us": _optional_percentile(deltas, 0.90),
        "p99_us": _optional_percentile(deltas, 0.99),
        "minimum_us": min(deltas) if deltas else None,
        "maximum_us": max(deltas) if deltas else None,
        "adaptive_faster_count": sum(delta < 0 for delta in deltas),
        "equal_count": sum(_close(delta, 0.0) for delta in deltas),
        "adaptive_slower_count": sum(delta > 0 for delta in deltas),
    }


def _value_summary(values: list[float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "sum": sum(values),
        "mean": statistics.mean(values) if values else None,
        "p50": _optional_percentile(values, 0.50),
        "p90": _optional_percentile(values, 0.90),
        "p99": _optional_percentile(values, 0.99),
        "minimum": min(values) if values else None,
        "maximum": max(values) if values else None,
    }


def _aligned_frames(
    adaptive: RunEvidence, baseline: RunEvidence
) -> list[tuple[Frame, Frame]]:
    adaptive_by_id = {frame.frame_id: frame for frame in adaptive.frames}
    baseline_by_id = {frame.frame_id: frame for frame in baseline.frames}
    if set(adaptive_by_id) != set(baseline_by_id):
        raise CampaignError(
            f"seed/run {adaptive.pair}: adaptive and MLO frame-id sets differ"
        )
    aligned: list[tuple[Frame, Frame]] = []
    for frame_id in sorted(adaptive_by_id):
        left = adaptive_by_id[frame_id]
        right = baseline_by_id[frame_id]
        if (
            left.generation_time_us,
            left.deadline_us,
            left.packet_count,
            left.frame_type,
        ) != (
            right.generation_time_us,
            right.deadline_us,
            right.packet_count,
            right.frame_type,
        ):
            raise CampaignError(
                f"seed/run {adaptive.pair} frame {frame_id}: offered frame differs"
            )
        aligned.append((left, right))
    return aligned


def _frame_comparison(aligned: Iterable[tuple[Frame, Frame]]) -> dict[str, Any]:
    rows = list(aligned)
    common_complete = [
        (float(left.union_latency_us), float(right.union_latency_us))
        for left, right in rows
        if left.union_latency_us is not None and right.union_latency_us is not None
    ]
    deltas = [
        left_latency - right_latency for left_latency, right_latency in common_complete
    ]
    adaptive_latencies = [left for left, _ in common_complete]
    mlo_latencies = [right for _, right in common_complete]
    adaptive_statistics = _value_summary(adaptive_latencies)
    mlo_statistics = _value_summary(mlo_latencies)
    statistic_differences = {
        statistic: adaptive_statistics[statistic] - mlo_statistics[statistic]
        for statistic in ("mean", "p50", "p90", "p99")
        if adaptive_statistics[statistic] is not None
        and mlo_statistics[statistic] is not None
    }
    return {
        "frame_count": len(rows),
        "deadline_outcome_quadrants": {
            "both_on_time": sum(
                not left.deadline_miss and not right.deadline_miss
                for left, right in rows
            ),
            "adaptive_on_time_mlo_miss": sum(
                not left.deadline_miss and right.deadline_miss
                for left, right in rows
            ),
            "adaptive_miss_mlo_on_time": sum(
                left.deadline_miss and not right.deadline_miss
                for left, right in rows
            ),
            "both_miss": sum(
                left.deadline_miss and right.deadline_miss for left, right in rows
            ),
        },
        "completion_set_counts": {
            "common_complete": sum(
                left.union_completion_us is not None
                and right.union_completion_us is not None
                for left, right in rows
            ),
            "adaptive_only_complete": sum(
                left.union_completion_us is not None
                and right.union_completion_us is None
                for left, right in rows
            ),
            "mlo_only_complete": sum(
                left.union_completion_us is None
                and right.union_completion_us is not None
                for left, right in rows
            ),
            "both_incomplete": sum(
                left.union_completion_us is None
                and right.union_completion_us is None
                for left, right in rows
            ),
        },
        "common_complete_latency_statistics_us": {
            "adaptive": adaptive_statistics,
            "mlo": mlo_statistics,
            "adaptive_minus_mlo": statistic_differences,
        },
        "common_complete_adaptive_minus_mlo_latency": _latency_delta_summary(deltas),
    }


def _mlo_comparisons(
    validation: dict[str, Any],
    baselines: dict[str, dict[tuple[int, int], RunEvidence]],
    arms: dict[str, dict[tuple[int, int], RunEvidence]],
    pairs: list[tuple[int, int]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for arm_id in sorted(validation["comparisons"]):
        result[arm_id] = {}
        for baseline in BASELINES:
            pooled: list[tuple[Frame, Frame]] = []
            by_unit: list[dict[str, Any]] = []
            for pair in pairs:
                adaptive = arms[arm_id][pair]
                mlo = baselines[baseline][pair]
                aligned = _aligned_frames(adaptive, mlo)
                pooled.extend(aligned)
                by_unit.append({
                    "seed": pair[0],
                    "run": pair[1],
                    "adaptive_run_id": adaptive.run_id,
                    "mlo_run_id": mlo.run_id,
                    **_frame_comparison(aligned),
                })
            result[arm_id][baseline] = {
                "scope": "descriptive_common_random_number_frame_join",
                "independent_inference_unit": ["seed", "run"],
                "paired_unit_count": len(pairs),
                "pooled_frame_comparison": _frame_comparison(pooled),
                "by_paired_unit": by_unit,
            }
    return result


def analyze_efficiency(
    inputs: Path | Iterable[Path],
    *,
    bootstrap_replicates: int = 20_000,
    expected_obss_profile: str | None = "mixed4x4",
    preflight: bool = False,
) -> dict[str, Any]:
    """Strictly validate a campaign and report frame-level efficiency evidence."""
    thresholds = Thresholds(
        expected_pair_count=1 if preflight else 12,
        minimum_strict_wins=1 if preflight else 9,
    )
    validation = validate_campaign(
        inputs,
        thresholds,
        bootstrap_replicates=bootstrap_replicates,
        expected_obss_profile=expected_obss_profile,
        preflight=preflight,
    )
    baselines, arms, ignored = _load_indexes(validation)
    pairs = [
        (int(item["seed"]), int(item["run"]))
        for item in validation["paired_units"]
    ]
    arm_reports = {
        arm_id: _arm_diagnostic(
            "primary_deficit" if arm_id == DECLARED_DEFICIT_ARM else "full_copy",
            [arms[arm_id][pair] for pair in pairs],
        )
        for arm_id in sorted(arms)
    }
    report = {
        "schema_version": 1,
        "analysis": "primary_tail_t4_frame_efficiency",
        "independent_sample_unit": ["seed", "run"],
        "preflight": preflight,
        "paired_unit_count": len(pairs),
        "paired_units": [
            {"seed": seed, "run": run_number} for seed, run_number in pairs
        ],
        "source_aggregates": validation["source_aggregates"],
        "campaign_validation": {
            "strict_campaign_analyzer_schema_version": validation["schema_version"],
            "strict_campaign_analysis": validation["analysis"],
            "complete_paired_arm_matrix": validation["campaign_checks"][
                "complete_paired_arm_matrix"
            ],
            "all_runs_core_validated": validation["campaign_checks"][
                "all_runs_core_validated"
            ],
            "headline_p99_frame_csv_quantization_bound_us": validation[
                "campaign_checks"
            ]["frame_csv_p99_quantization_bound_us"],
            "ignored_noncampaign_run_count": ignored,
        },
        "definitions": {
            "price_gate_evaluated": (
                "validated decisions action, airtime_deferred, launch_rejected, "
                "or price_rejected; excludes ineligible or already-resolved samples"
            ),
            "price_qualified": (
                "validated decisions action, airtime_deferred, or launch_rejected"
            ),
            "token_qualified": "validated decisions action or launch_rejected",
            "deadline_rescue": (
                "primary copy missed its deadline while packet-union completion "
                "was on time"
            ),
            "duplicate_recovery": (
                "union completed and was earlier than the primary copy, or the "
                "primary copy never completed"
            ),
            "airtime": (
                "measured tagged secondary sender PHY transmit airtime settled "
                "to each launched frame"
            ),
            "p99_censoring": (
                "headline uses every union-complete frame; fixed-population contrast "
                "uses only frames whose primary copy completed"
            ),
            "mlo_frame_join": (
                "descriptive frame-level common-random-number accounting; seed/run "
                "remains the inference unit"
            ),
        },
        "arms": arm_reports,
        "mlo_frame_comparisons": _mlo_comparisons(
            validation, baselines, arms, pairs
        ),
    }
    # Fail here, rather than producing non-standard JSON such as NaN/Infinity.
    json.dumps(report, allow_nan=False, sort_keys=True)
    return report


def _positive_integer(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs", nargs="+", type=Path, help="result roots or aggregate files"
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--bootstrap-replicates", type=_positive_integer, default=20_000)
    parser.add_argument("--expected-obss-profile", default="mixed4x4")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="require the declared seven-arm seed 43/run 1 preflight",
    )
    args = parser.parse_args()
    try:
        report = analyze_efficiency(
            args.inputs,
            bootstrap_replicates=args.bootstrap_replicates,
            expected_obss_profile=args.expected_obss_profile,
            preflight=args.preflight,
        )
    except (CampaignError, ValidationError, ValueError, json.JSONDecodeError, OSError) as error:
        parser.error(str(error))
    serialized = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.json_output is not None:
        args.json_output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


if __name__ == "__main__":
    main()
