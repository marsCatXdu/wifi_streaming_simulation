#!/usr/bin/env python3
"""Project an optimistic resource-constrained subset of deadline repairs."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Sequence

import analyze_t2_repair_deadline_oracle_v2 as deadline_analysis
import analyze_t2_repair_mechanism as mechanism


ANALYSIS_ID = "t2-repair-resource-subset-sensitivity-v1"


def _rows_by_frame(path: Path) -> dict[int, dict[str, str]]:
    rows = mechanism._read_csv(path)
    result = {int(row["frame_id"]): row for row in rows}
    mechanism._require(len(result) == len(rows), f"{path}: duplicate frame ID")
    return result


def _sender_airtime_us(run_dir: Path) -> float:
    rows = mechanism._read_csv(run_dir / "link_intervals.csv")
    mechanism._require(
        {int(row["link_id"]) for row in rows} == {0, 1},
        f"{run_dir}: expected two sender-link rows",
    )
    return sum(float(row["phy_tx_time_us"]) for row in rows)


def _repair_costs(run_dir: Path) -> dict[int, float]:
    rows = mechanism._read_csv(run_dir / "secondary_airtime_settlements.csv")
    result: dict[int, float] = {}
    for row in rows:
        frame_id = int(row["frame_id"])
        cost = float(row["measured_airtime_us"])
        mechanism._require(
            math.isfinite(cost) and cost >= 0 and frame_id not in result,
            f"{run_dir}: invalid or duplicate repair settlement",
        )
        result[frame_id] = cost
    return result


def _select_cheapest(costs: Sequence[float], budget_us: float) -> dict[str, Any]:
    selected = 0
    spent = 0.0
    for cost in sorted(costs):
        if spent + cost > max(0.0, budget_us) + 1e-9:
            break
        spent += cost
        selected += 1
    return {
        "candidate_rescues": len(costs),
        "selected_rescues": selected,
        "spent_measured_tagged_airtime_us": spent,
        "budget_us": max(0.0, budget_us),
        "raw_headroom_us": budget_us,
    }


def _unit_rows(
    factual_roots: Sequence[Path],
    oracle_roots: Sequence[Path],
) -> list[dict[str, Any]]:
    factual = deadline_analysis._roots_by_shard(factual_roots, "factual")
    corrected = deadline_analysis._roots_by_shard(oracle_roots, "corrected")
    units = []
    for shard_index in (0, 1):
        factual_root, factual_manifest = factual[shard_index]
        corrected_root, corrected_manifest = corrected[shard_index]
        factual_by_id = {
            item["run_id"]: item for item in factual_manifest["runs"]
        }
        factual_by_unit_arm = {
            (
                item["scenario"]["scenario_id"],
                item["seed"],
                item["run"],
                item["arm_id"],
            ): item
            for item in factual_manifest["runs"]
        }
        corrected_by_id = {
            item["run_id"]: item for item in corrected_manifest["runs"]
        }
        for pairing in corrected_manifest["pairings"]:
            primary_item = factual_by_id[pairing["baseline_run_id"]]
            key = (
                primary_item["scenario"]["scenario_id"],
                primary_item["seed"],
                primary_item["run"],
            )
            str_item = factual_by_unit_arm[
                (*key, "str_mlo_nmaxinflights_1")
            ]
            repair_item = corrected_by_id[pairing["oracle_run_id"]]
            primary_dir = factual_root / primary_item["directory"]
            str_dir = factual_root / str_item["directory"]
            repair_dir = corrected_root / repair_item["directory"]
            primary_frames = _rows_by_frame(primary_dir / "frames.csv")
            str_frames = _rows_by_frame(str_dir / "frames.csv")
            repair_frames = _rows_by_frame(repair_dir / "frames.csv")
            actions = _rows_by_frame(repair_dir / "mechanism_t2_actions.csv")
            costs = _repair_costs(repair_dir)
            mechanism._require(
                set(primary_frames) == set(str_frames) == set(repair_frames)
                == set(actions),
                f"{repair_dir}: frame coverage differs",
            )
            primary_misses = {
                frame_id
                for frame_id, row in primary_frames.items()
                if row["deadline_miss"] == "1"
            }
            str_misses = sum(
                row["deadline_miss"] == "1" for row in str_frames.values()
            )
            repair_misses = {
                frame_id
                for frame_id, row in repair_frames.items()
                if row["deadline_miss"] == "1"
            }
            launched = {
                frame_id
                for frame_id, row in actions.items()
                if row["launched"] == "1"
            }
            mechanism._require(
                launched == primary_misses and set(costs) == launched,
                f"{repair_dir}: action/cost set differs from primary misses",
            )
            rescued = primary_misses - repair_misses
            rescue_costs = [costs[frame_id] for frame_id in rescued]
            str_airtime = _sender_airtime_us(str_dir)
            primary_airtime = _sender_airtime_us(primary_dir)
            repair_airtime = _sender_airtime_us(repair_dir)
            units.append(
                {
                    "shard_index": shard_index,
                    "scenario_id": key[0],
                    "seed": key[1],
                    "run": key[2],
                    "generated_frames": len(primary_frames),
                    "str_deadline_misses": str_misses,
                    "primary_deadline_misses": len(primary_misses),
                    "repair_deadline_misses": len(repair_misses),
                    "factual_rescues": len(rescued),
                    "str_sender_airtime_us": str_airtime,
                    "primary_sender_airtime_us": primary_airtime,
                    "repair_sender_airtime_us": repair_airtime,
                    "rescue_costs_us": rescue_costs,
                    "equal_airtime": _select_cheapest(
                        rescue_costs,
                        str_airtime - primary_airtime,
                    ),
                    "engineering_1p20": _select_cheapest(
                        rescue_costs,
                        1.20 * str_airtime - primary_airtime,
                    ),
                }
            )
    mechanism._require(len(units) == 20, "expected twenty paired units")
    return sorted(units, key=lambda row: (row["scenario_id"], row["seed"]))


def _projection(
    units: Sequence[dict[str, Any]],
    budget_key: str,
) -> dict[str, Any]:
    primary_misses = sum(int(row["primary_deadline_misses"]) for row in units)
    str_misses = sum(int(row["str_deadline_misses"]) for row in units)
    generated = sum(int(row["generated_frames"]) for row in units)
    selected = sum(int(row[budget_key]["selected_rescues"]) for row in units)
    projected_misses = primary_misses - selected
    return {
        "budget_scope": "separate nontransferable budget within every paired run",
        "primary_deadline_misses": primary_misses,
        "str_deadline_misses": str_misses,
        "selected_rescues": selected,
        "projected_deadline_misses": projected_misses,
        "projected_deadline_miss_rate": projected_misses / generated,
        "beats_str_on_misses": projected_misses < str_misses,
        "total_raw_headroom_us": sum(
            float(row[budget_key]["raw_headroom_us"]) for row in units
        ),
        "total_nonnegative_budget_us": sum(
            float(row[budget_key]["budget_us"]) for row in units
        ),
        "total_selected_measured_tagged_airtime_us": sum(
            float(row[budget_key]["spent_measured_tagged_airtime_us"])
            for row in units
        ),
    }


def _pooled_projection(
    units: Sequence[dict[str, Any]],
    multiplier: float,
) -> dict[str, Any]:
    primary_misses = sum(int(row["primary_deadline_misses"]) for row in units)
    str_misses = sum(int(row["str_deadline_misses"]) for row in units)
    generated = sum(int(row["generated_frames"]) for row in units)
    headroom = (
        multiplier * sum(float(row["str_sender_airtime_us"]) for row in units)
        - sum(float(row["primary_sender_airtime_us"]) for row in units)
    )
    selection = _select_cheapest(
        [cost for row in units for cost in row["rescue_costs_us"]],
        headroom,
    )
    projected_misses = primary_misses - selection["selected_rescues"]
    return {
        "budget_scope": (
            "optimistic noncausal pooled budget transferable across all twenty runs"
        ),
        **selection,
        "primary_deadline_misses": primary_misses,
        "str_deadline_misses": str_misses,
        "projected_deadline_misses": projected_misses,
        "projected_deadline_miss_rate": projected_misses / generated,
        "beats_str_on_misses": projected_misses < str_misses,
    }


def _flat_unit_rows(units: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in units:
        result.append(
            {
                key: value
                for key, value in row.items()
                if key != "rescue_costs_us" and not isinstance(value, dict)
            }
            | {
                f"equal_airtime_{key}": value
                for key, value in row["equal_airtime"].items()
            }
            | {
                f"engineering_1p20_{key}": value
                for key, value in row["engineering_1p20"].items()
            }
        )
    return result


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    mechanism._require(bool(rows), f"refusing to write empty table {path}")
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _report_markdown(report: dict[str, Any]) -> str:
    equal = report["per_run_equal_airtime_projection"]
    engineering = report["per_run_1p20_projection"]
    pooled = report["pooled_1p20_upper_sensitivity"]
    return "\n".join(
        [
            "# Post-result repair subset resource sensitivity",
            "",
            "This is an optimistic static projection, not a simulated policy result.",
            "It selects only factual rescues and charges their measured tagged PPDU",
            "airtime from the full-action replay. It ignores fixed secondary-link",
            "overhead, policy feedback, changed contention, and causal prediction.",
            "",
            "| Budget | Selected rescues | Projected misses | Beats STR? |",
            "| --- | ---: | ---: | --- |",
            f"| Equal airtime, per run | {equal['selected_rescues']:,} | "
            f"{equal['projected_deadline_misses']:,} | "
            f"{'yes' if equal['beats_str_on_misses'] else 'no'} |",
            f"| 1.20 airtime, per run | {engineering['selected_rescues']:,} | "
            f"{engineering['projected_deadline_misses']:,} | "
            f"{'yes' if engineering['beats_str_on_misses'] else 'no'} |",
            f"| 1.20 airtime, pooled upper sensitivity | "
            f"{pooled['selected_rescues']:,} | "
            f"{pooled['projected_deadline_misses']:,} | "
            f"{'yes' if pooled['beats_str_on_misses'] else 'no'} |",
            "",
            f"STR records {equal['str_deadline_misses']:,} misses; the primary-only",
            f"arm records {equal['primary_deadline_misses']:,}. The equal-airtime",
            "headroom is negative before any repair because primary-only already",
            "uses more airtime than STR.",
            "",
            "A failure even in the pooled 1.20 sensitivity is strong negative",
            "evidence. A pass would show only an optimistic selection ceiling and",
            "would still require a causal, closed-loop experiment.",
            "",
        ]
    )


def analyze(
    factual_roots: Sequence[Path],
    oracle_roots: Sequence[Path],
    output: Path,
) -> dict[str, Any]:
    identity = mechanism._git_identity()
    jobs, source, _ = deadline_analysis._load_jobs(factual_roots, oracle_roots)
    mechanism._require(len(jobs) == 120, "combined source closure differs")
    units = _unit_rows(factual_roots, oracle_roots)
    report = {
        "schema_version": 1,
        "analysis": ANALYSIS_ID,
        "status": "post_result_optimistic_sensitivity_not_policy_evidence",
        "analyzer_identity": identity,
        "source_closure": source,
        "assumptions": {
            "factual_rescues_only": True,
            "cost": "full-action measured per-frame tagged PPDU airtime",
            "fixed_secondary_overhead_ignored": True,
            "selection_is_noncausal": True,
            "closed_loop_interference_after_subsetting_ignored": True,
            "interpretation": "optimistic static upper sensitivity only",
        },
        "per_run_equal_airtime_projection": _projection(units, "equal_airtime"),
        "per_run_1p20_projection": _projection(units, "engineering_1p20"),
        "pooled_equal_airtime_upper_sensitivity": _pooled_projection(units, 1.0),
        "pooled_1p20_upper_sensitivity": _pooled_projection(units, 1.20),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    mechanism._require(not output.exists(), f"output already exists: {output}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        (temporary / "resource_subset_sensitivity.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (temporary / "REPORT.md").write_text(
            _report_markdown(report), encoding="utf-8"
        )
        _write_csv(temporary / "unit_projection.csv", _flat_unit_rows(units))
        artifacts = {}
        for path in sorted(item for item in temporary.rglob("*") if item.is_file()):
            relative = str(path.relative_to(temporary))
            artifacts[relative] = {
                "bytes": path.stat().st_size,
                "sha256": mechanism._sha256(path),
            }
        (temporary / "analysis_artifact_manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "analysis": ANALYSIS_ID,
                    "analyzer_identity": identity,
                    "source_closure": source,
                    "counts": {"paired_units": len(units)},
                    "artifacts": artifacts,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)
    except Exception:
        raise
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factual-roots", nargs=2, required=True, type=Path)
    parser.add_argument("--oracle-roots", nargs=2, required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = analyze(
        [path.resolve() for path in args.factual_roots],
        [path.resolve() for path in args.oracle_roots],
        args.output.resolve(),
    )
    projected = report["pooled_1p20_upper_sensitivity"]
    print(
        f"POOLED_1P20 selected={projected['selected_rescues']} "
        f"projected_misses={projected['projected_deadline_misses']} "
        f"beats_str={projected['beats_str_on_misses']}"
    )


if __name__ == "__main__":
    main()
