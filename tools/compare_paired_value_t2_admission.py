#!/usr/bin/env python3
"""Compare admission and frame outcomes for two paired-value T2 campaigns."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


POLICY = "paired_value_duplication_t2"


class ComparisonError(RuntimeError):
    """Raised when two campaigns cannot be compared exactly."""


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object from a file."""
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ComparisonError(f"{path}: expected a JSON object")
    return value


def read_csv_by_frame(path: Path) -> dict[int, dict[str, str]]:
    """Read a CSV and index its unique rows by frame ID."""
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None or "frame_id" not in reader.fieldnames:
            raise ComparisonError(f"{path}: missing frame_id column")
        rows: dict[int, dict[str, str]] = {}
        for row in reader:
            frame_id = int(row["frame_id"])
            if frame_id in rows:
                raise ComparisonError(f"{path}: duplicate frame_id {frame_id}")
            rows[frame_id] = row
    return rows


def resolve_aggregate(path: Path) -> Path:
    """Resolve a campaign, runs directory, or aggregate path."""
    path = path.resolve()
    if path.is_file():
        return path
    direct = path / "aggregate.json"
    nested = path / "runs" / "aggregate.json"
    if direct.is_file():
        return direct
    if nested.is_file():
        return nested
    raise ComparisonError(f"{path}: cannot find aggregate.json")


def policy_runs(aggregate_path: Path) -> dict[tuple[int, int], tuple[dict[str, Any], Path]]:
    """Return policy run records and local run directories by seed/run."""
    aggregate = read_json(aggregate_path)
    result: dict[tuple[int, int], tuple[dict[str, Any], Path]] = {}
    for run in aggregate.get("runs", []):
        if run.get("policy") != POLICY:
            continue
        key = (int(run["seed"]), int(run["run"]))
        if key in result:
            raise ComparisonError(f"{aggregate_path}: duplicate policy unit {key}")
        run_id = str(run["run_id"])
        run_dir = aggregate_path.parent / run_id
        if not run_dir.is_dir():
            raise ComparisonError(f"{aggregate_path}: missing local run directory {run_id}")
        result[key] = (run, run_dir)
    if not result:
        raise ComparisonError(f"{aggregate_path}: no {POLICY} runs")
    return result


def as_optional_float(value: str) -> float | None:
    """Parse a possibly empty finite floating-point CSV field."""
    if value == "":
        return None
    result = float(value)
    if result != result or result in (float("inf"), float("-inf")):
        raise ComparisonError(f"non-finite numeric field: {value}")
    return result


def percentile(values: list[float], probability: float) -> float:
    """Return the Hyndman-Fan type-7 percentile."""
    ordered = sorted(values)
    if not ordered:
        raise ComparisonError("cannot summarize an empty numeric population")
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def numeric_summary(values: Iterable[float]) -> dict[str, float | int | None]:
    """Summarize a numeric population without hiding an empty set."""
    values = list(values)
    if not values:
        return {
            "count": 0,
            "minimum": None,
            "p10": None,
            "median": None,
            "mean": None,
            "p90": None,
            "maximum": None,
        }
    return {
        "count": len(values),
        "minimum": min(values),
        "p10": percentile(values, 0.10),
        "median": percentile(values, 0.50),
        "mean": statistics.fmean(values),
        "p90": percentile(values, 0.90),
        "maximum": max(values),
    }


def is_action(row: dict[str, str]) -> bool:
    """Return whether a decision row launched the secondary copy."""
    launched = row.get("secondary_launched") == "1"
    if launched != (row.get("decision_status") == "action"):
        raise ComparisonError("decision status and secondary_launched disagree")
    return launched


def frame_outcome(frame: dict[str, str], primary_copy_id: str) -> tuple[bool, bool]:
    """Return primary-copy and final-union deadline misses for one frame."""
    if primary_copy_id not in {"0", "1"}:
        raise ComparisonError(f"unsupported primary_copy_id {primary_copy_id}")
    completion = as_optional_float(frame[f"copy_{primary_copy_id}_completion_us"])
    deadline = float(frame["generation_time_us"]) + float(frame["deadline_us"])
    primary_miss = completion is None or completion > deadline
    final_miss = frame["deadline_miss"] == "1"
    return primary_miss, final_miss


def outcome_summary(
    frame_ids: Iterable[int],
    decisions: dict[int, dict[str, str]],
    frames: dict[int, dict[str, str]],
) -> dict[str, float | int]:
    """Summarize primary and final outcomes for a frame subset."""
    frame_ids = list(frame_ids)
    primary_misses = 0
    acted_primary_misses = 0
    final_misses = 0
    rescued = 0
    actions = 0
    for frame_id in frame_ids:
        decision = decisions[frame_id]
        primary_miss, final_miss = frame_outcome(
            frames[frame_id], decision["primary_copy_id"]
        )
        action = is_action(decision)
        primary_misses += int(primary_miss)
        acted_primary_misses += int(action and primary_miss)
        final_misses += int(final_miss)
        actions += int(action)
        rescued += int(action and primary_miss and not final_miss)
    count = len(frame_ids)
    return {
        "frames": count,
        "actions": actions,
        "primary_misses": primary_misses,
        "primary_miss_rate": primary_misses / count if count else 0.0,
        "acted_primary_misses": acted_primary_misses,
        "final_misses": final_misses,
        "final_miss_rate": final_misses / count if count else 0.0,
        "acted_primary_misses_rescued": rescued,
    }


def campaign_manifest(aggregate_path: Path) -> dict[str, str]:
    """Return manifest provenance for an aggregate."""
    path = aggregate_path.parent / "experiment_manifest.json"
    manifest = read_json(path)
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "experiment": str(manifest.get("experiment", "")),
        "matrix_sha256": str(manifest.get("matrix_sha256", "")),
        "project_commit": str(manifest.get("project_commit", "")),
        "runtime_contract_id": str(manifest.get("runtime_contract_id", "")),
        "runtime_contract_sha256": str(manifest.get("runtime_contract_sha256", "")),
    }


def compare_campaigns(
    baseline_path: Path,
    candidate_path: Path,
    baseline_label: str,
    candidate_label: str,
) -> dict[str, Any]:
    """Compare admission decisions and outcomes across two matched campaigns."""
    baseline_aggregate = resolve_aggregate(baseline_path)
    candidate_aggregate = resolve_aggregate(candidate_path)
    baseline_runs = policy_runs(baseline_aggregate)
    candidate_runs = policy_runs(candidate_aggregate)
    if baseline_runs.keys() != candidate_runs.keys():
        missing_candidate = sorted(baseline_runs.keys() - candidate_runs.keys())
        missing_baseline = sorted(candidate_runs.keys() - baseline_runs.keys())
        raise ComparisonError(
            "paired seed/run units differ: "
            f"missing candidate={missing_candidate}, missing baseline={missing_baseline}"
        )

    transition_ids: dict[str, list[tuple[tuple[int, int], int]]] = {
        "common_action": [],
        "baseline_only_action": [],
        "candidate_only_action": [],
        "neither_action": [],
    }
    baseline_decisions_all: dict[tuple[tuple[int, int], int], dict[str, str]] = {}
    candidate_decisions_all: dict[tuple[tuple[int, int], int], dict[str, str]] = {}
    baseline_frames_all: dict[tuple[tuple[int, int], int], dict[str, str]] = {}
    candidate_frames_all: dict[tuple[tuple[int, int], int], dict[str, str]] = {}
    status_transitions: Counter[str] = Counter()
    tier_transitions: Counter[str] = Counter()
    score_differences = 0
    threshold_differences = 0
    primary_miss_transitions: Counter[str] = Counter()
    final_miss_transitions: Counter[str] = Counter()

    for unit in sorted(baseline_runs):
        _, baseline_dir = baseline_runs[unit]
        _, candidate_dir = candidate_runs[unit]
        baseline_decisions = read_csv_by_frame(baseline_dir / "paired_value_t2_decisions.csv")
        candidate_decisions = read_csv_by_frame(candidate_dir / "paired_value_t2_decisions.csv")
        baseline_frames = read_csv_by_frame(baseline_dir / "frames.csv")
        candidate_frames = read_csv_by_frame(candidate_dir / "frames.csv")
        frame_ids = baseline_decisions.keys()
        if frame_ids != candidate_decisions.keys():
            raise ComparisonError(f"{unit}: decision frame IDs differ")
        if frame_ids != baseline_frames.keys() or frame_ids != candidate_frames.keys():
            raise ComparisonError(f"{unit}: decision and outcome frame IDs differ")

        for frame_id in frame_ids:
            key = (unit, frame_id)
            baseline_decision = baseline_decisions[frame_id]
            candidate_decision = candidate_decisions[frame_id]
            baseline_frame = baseline_frames[frame_id]
            candidate_frame = candidate_frames[frame_id]
            baseline_decisions_all[key] = baseline_decision
            candidate_decisions_all[key] = candidate_decision
            baseline_frames_all[key] = baseline_frame
            candidate_frames_all[key] = candidate_frame

            baseline_action = is_action(baseline_decision)
            candidate_action = is_action(candidate_decision)
            if baseline_action and candidate_action:
                transition = "common_action"
            elif baseline_action:
                transition = "baseline_only_action"
            elif candidate_action:
                transition = "candidate_only_action"
            else:
                transition = "neither_action"
            transition_ids[transition].append(key)

            status_transitions[
                f"{baseline_decision['decision_status']} -> "
                f"{candidate_decision['decision_status']}"
            ] += 1
            if baseline_action or candidate_action:
                tier_transitions[
                    f"{baseline_decision.get('admission_tier', '') or 'none'} -> "
                    f"{candidate_decision.get('admission_tier', '') or 'none'}"
                ] += 1
            score_differences += int(
                baseline_decision.get("value_per_cost_score_float32", "")
                != candidate_decision.get("value_per_cost_score_float32", "")
            )
            threshold_differences += int(
                baseline_decision.get("passes_score_threshold", "")
                != candidate_decision.get("passes_score_threshold", "")
            )

            baseline_primary, baseline_final = frame_outcome(
                baseline_frame, baseline_decision["primary_copy_id"]
            )
            candidate_primary, candidate_final = frame_outcome(
                candidate_frame, candidate_decision["primary_copy_id"]
            )
            primary_miss_transitions[
                f"{'miss' if baseline_primary else 'on_time'} -> "
                f"{'miss' if candidate_primary else 'on_time'}"
            ] += 1
            final_miss_transitions[
                f"{'miss' if baseline_final else 'on_time'} -> "
                f"{'miss' if candidate_final else 'on_time'}"
            ] += 1

    transition_summaries: dict[str, Any] = {}
    for transition, keys in transition_ids.items():
        baseline_subset_decisions = {index: baseline_decisions_all[index] for index in keys}
        candidate_subset_decisions = {index: candidate_decisions_all[index] for index in keys}
        baseline_subset_frames = {index: baseline_frames_all[index] for index in keys}
        candidate_subset_frames = {index: candidate_frames_all[index] for index in keys}
        baseline_scores = [
            value
            for key in keys
            if (value := as_optional_float(
                baseline_decisions_all[key].get("value_per_cost_score_float32", "")
            ))
            is not None
        ]
        candidate_scores = [
            value
            for key in keys
            if (value := as_optional_float(
                candidate_decisions_all[key].get("value_per_cost_score_float32", "")
            ))
            is not None
        ]
        baseline_risks = [
            value
            for key in keys
            if (value := as_optional_float(
                baseline_decisions_all[key].get("primary_bad12_probability", "")
            ))
            is not None
        ]
        candidate_risks = [
            value
            for key in keys
            if (value := as_optional_float(
                candidate_decisions_all[key].get("primary_bad12_probability", "")
            ))
            is not None
        ]
        candidate_times = [
            int(candidate_decisions_all[key]["generation_time_ns"]) / 1e9 for key in keys
        ]
        transition_summaries[transition] = {
            "count": len(keys),
            "baseline_outcomes": outcome_summary(
                keys, baseline_subset_decisions, baseline_subset_frames
            ),
            "candidate_outcomes": outcome_summary(
                keys, candidate_subset_decisions, candidate_subset_frames
            ),
            "baseline_score": numeric_summary(baseline_scores),
            "candidate_score": numeric_summary(candidate_scores),
            "baseline_primary_bad12_probability": numeric_summary(baseline_risks),
            "candidate_primary_bad12_probability": numeric_summary(candidate_risks),
            "generation_time_s": numeric_summary(candidate_times),
        }

    all_keys = list(baseline_decisions_all)
    baseline_action_count = sum(is_action(row) for row in baseline_decisions_all.values())
    candidate_action_count = sum(is_action(row) for row in candidate_decisions_all.values())
    baseline_all_outcomes = outcome_summary(
        all_keys, baseline_decisions_all, baseline_frames_all
    )
    candidate_all_outcomes = outcome_summary(
        all_keys, candidate_decisions_all, candidate_frames_all
    )

    return {
        "schema_version": 1,
        "analysis": "paired_value_t2_admission_campaign_comparison",
        "evidence_role": "diagnostic_only_not_a_qualification_gate",
        "labels": {"baseline": baseline_label, "candidate": candidate_label},
        "paired_units": len(baseline_runs),
        "frame_rows": len(all_keys),
        "sources": {
            "baseline": campaign_manifest(baseline_aggregate),
            "candidate": campaign_manifest(candidate_aggregate),
        },
        "decision_invariants": {
            "score_different_rows": score_differences,
            "score_threshold_pass_different_rows": threshold_differences,
        },
        "admission": {
            "baseline_actions": baseline_action_count,
            "candidate_actions": candidate_action_count,
            "net_candidate_actions": candidate_action_count - baseline_action_count,
            "transitions": transition_summaries,
            "terminal_status_transitions": dict(sorted(status_transitions.items())),
            "action_tier_transitions": dict(sorted(tier_transitions.items())),
        },
        "outcomes": {
            "baseline": baseline_all_outcomes,
            "candidate": candidate_all_outcomes,
            "primary_miss_transitions": dict(sorted(primary_miss_transitions.items())),
            "final_miss_transitions": dict(sorted(final_miss_transitions.items())),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render the compact human-readable comparison."""
    labels = report["labels"]
    admission = report["admission"]
    outcomes = report["outcomes"]
    transitions = admission["transitions"]
    displaced = transitions["baseline_only_action"]
    added = transitions["candidate_only_action"]
    baseline = outcomes["baseline"]
    candidate = outcomes["candidate"]
    rescued_delta = (
        candidate["acted_primary_misses_rescued"]
        - baseline["acted_primary_misses_rescued"]
    )
    final_transitions = outcomes["final_miss_transitions"]
    return "\n".join(
        [
            f"# {labels['baseline']} versus {labels['candidate']} admission comparison",
            "",
            "Diagnostic only; qualification gates remain in the frozen campaign reports.",
            "",
            f"Matched units: {report['paired_units']}; frame rows: {report['frame_rows']}.",
            f"Scores changed on {report['decision_invariants']['score_different_rows']} rows and "
            "score-threshold membership changed on "
            f"{report['decision_invariants']['score_threshold_pass_different_rows']} rows.",
            "",
            "| Metric | Baseline | Candidate | Candidate - baseline |",
            "| --- | ---: | ---: | ---: |",
            f"| Actions | {admission['baseline_actions']} | {admission['candidate_actions']} | "
            f"{admission['net_candidate_actions']:+d} |",
            f"| Acted primary misses | {baseline['acted_primary_misses']} | "
            f"{candidate['acted_primary_misses']} | "
            f"{candidate['acted_primary_misses'] - baseline['acted_primary_misses']:+d} |",
            f"| Rescued acted primary misses | "
            f"{baseline['acted_primary_misses_rescued']} | "
            f"{candidate['acted_primary_misses_rescued']} | "
            f"{rescued_delta:+d} |",
            f"| Final misses | {baseline['final_misses']} | {candidate['final_misses']} | "
            f"{candidate['final_misses'] - baseline['final_misses']:+d} |",
            "",
            f"Common actions: {transitions['common_action']['count']}; "
            f"displaced baseline actions: {displaced['count']}; "
            f"candidate-only actions: {added['count']}.",
            f"Displaced actions contain {displaced['baseline_outcomes']['primary_misses']} "
            f"baseline primary misses; candidate-only actions contain "
            f"{added['candidate_outcomes']['primary_misses']} candidate primary misses.",
            "",
            "Final-miss transitions (baseline -> candidate):",
            "",
            *[
                f"- {name}: {count}"
                for name, count in sorted(final_transitions.items())
            ],
            "",
        ]
    )


def plot_comparison(report: dict[str, Any], output: Path) -> None:
    """Plot the admission shift that distinguishes the candidate campaign."""
    import matplotlib.pyplot as plt

    labels = report["labels"]
    transitions = report["admission"]["transitions"]
    common = transitions["common_action"]
    displaced = transitions["baseline_only_action"]
    added = transitions["candidate_only_action"]
    colors = ["#5f6b76", "#d05a4e", "#238b7e"]
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), constrained_layout=True)

    action_names = ["Common", f"{labels['baseline']} only", f"{labels['candidate']} only"]
    action_counts = [common["count"], displaced["count"], added["count"]]
    bars = axes[0, 0].bar(action_names, action_counts, color=colors)
    axes[0, 0].bar_label(bars, fmt="%d")
    axes[0, 0].set_ylabel("Frames")
    axes[0, 0].set_title("Action-set transition")
    axes[0, 0].tick_params(axis="x", rotation=12)

    shifted_names = [f"{labels['baseline']} only", f"{labels['candidate']} only"]
    miss_rates = [
        100 * displaced["baseline_outcomes"]["primary_miss_rate"],
        100 * added["candidate_outcomes"]["primary_miss_rate"],
    ]
    bars = axes[0, 1].bar(shifted_names, miss_rates, color=colors[1:])
    axes[0, 1].bar_label(bars, fmt="%.2f%%")
    axes[0, 1].set_ylabel("Primary-copy deadline-miss rate (%)")
    axes[0, 1].set_title("Risk captured by shifted actions")
    axes[0, 1].tick_params(axis="x", rotation=12)

    def interval_panel(
        axis: Any,
        summaries: list[dict[str, float | int | None]],
        title: str,
        xlabel: str,
        scale: float = 1.0,
    ) -> None:
        for position, (summary, color) in enumerate(zip(summaries, colors[1:])):
            median = scale * float(summary["median"])
            low = scale * float(summary["p10"])
            high = scale * float(summary["p90"])
            axis.errorbar(
                median,
                position,
                xerr=[[median - low], [high - median]],
                marker="o",
                markersize=8,
                capsize=5,
                color=color,
                linewidth=2,
            )
        axis.set_yticks([0, 1], shifted_names)
        axis.set_xlabel(xlabel)
        axis.set_title(title)
        axis.grid(axis="x", alpha=0.3)

    interval_panel(
        axes[1, 0],
        [displaced["baseline_score"], added["candidate_score"]],
        "Predictor score (median and 10-90% interval)",
        "Value-per-cost score * 1e4",
        1e4,
    )
    interval_panel(
        axes[1, 1],
        [displaced["generation_time_s"], added["generation_time_s"]],
        "Frame time (median and 10-90% interval)",
        "Generation time (s)",
    )
    fig.suptitle(
        f"Admission shift: {labels['baseline']} to {labels['candidate']}\n"
        "Candidate borrowing spends future refill on earlier, lower-risk frames"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180)
    plt.close(fig)


def main() -> None:
    """Run the command-line comparison."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--baseline-label", default="baseline")
    parser.add_argument("--candidate-label", default="candidate")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--plot-output", type=Path)
    args = parser.parse_args()
    try:
        report = compare_campaigns(
            args.baseline,
            args.candidate,
            args.baseline_label,
            args.candidate_label,
        )
    except (ComparisonError, KeyError, ValueError) as error:
        parser.error(str(error))
    markdown = render_markdown(report)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(markdown, encoding="utf-8")
    if args.plot_output:
        plot_comparison(report, args.plot_output)
    print(markdown, end="")


if __name__ == "__main__":
    main()
