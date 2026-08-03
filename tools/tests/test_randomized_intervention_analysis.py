#!/usr/bin/env python3
"""Focused tests for randomized-intervention descriptive analysis."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from analyze_randomized_intervention import (
    AnalysisError,
    analyze_randomized_runs,
    load_randomized_run,
)
from validate_outputs import (
    FRAME_COLUMNS,
    RANDOMIZED_ASSIGNMENT_COLUMNS,
    RANDOMIZED_EXECUTION_COLUMNS,
    SECONDARY_AIRTIME_SETTLEMENT_COLUMNS,
    ValidationError,
)


def _write_csv(
    path: Path, columns: set[str], rows: list[dict[str, object]]
) -> None:
    fieldnames = sorted(columns)
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=fieldnames)
        writer.writeheader()
        for source in rows:
            row = {field: "" for field in fieldnames}
            row.update(source)
            writer.writerow(row)


def _record(
    frame_id: int,
    arm: str,
    *,
    eligible: bool,
    reason: str,
    status: str,
    latency_us: int | None,
    miss: bool,
    attempted: bool = False,
    launched: bool = False,
    noncompliance: bool = False,
    nominal_us: float = 50.0,
    estimated_us: float = 62.5,
    measured_us: float | None = None,
    released_us: float | None = None,
    fallback: bool = False,
) -> dict[str, object]:
    return {
        "frame_id": frame_id,
        "arm": arm,
        "eligible": eligible,
        "reason": reason,
        "status": status,
        "latency_us": latency_us,
        "miss": miss,
        "attempted": attempted,
        "launched": launched,
        "noncompliance": noncompliance,
        "nominal_us": nominal_us,
        "estimated_us": estimated_us,
        "measured_us": measured_us,
        "released_us": released_us,
        "fallback": fallback,
    }


RECORDS = [
    _record(
        0,
        "CONTROL",
        eligible=True,
        reason="eligible",
        status="control_no_launch",
        latency_us=10_000,
        miss=False,
    ),
    _record(
        1,
        "CONTROL",
        eligible=False,
        reason="outside_assignment_window",
        status="not_exposed_ineligible_t2",
        latency_us=None,
        miss=True,
    ),
    _record(
        2,
        "FULL_COPY_T2",
        eligible=True,
        reason="eligible",
        status="launched_t2",
        latency_us=8_000,
        miss=False,
        attempted=True,
        launched=True,
        nominal_us=100.0,
        estimated_us=125.0,
        measured_us=110.0,
        released_us=15.0,
    ),
    _record(
        3,
        "FULL_COPY_T2",
        eligible=True,
        reason="eligible",
        status="launch_rejected_t2",
        latency_us=40_000,
        miss=True,
        attempted=True,
        noncompliance=True,
        nominal_us=100.0,
        estimated_us=125.0,
    ),
    _record(
        4,
        "FULL_COPY_T4",
        eligible=True,
        reason="eligible",
        status="launched_t4",
        latency_us=12_000,
        miss=False,
        attempted=True,
        launched=True,
        nominal_us=80.0,
        estimated_us=100.0,
        measured_us=90.0,
        released_us=10.0,
        fallback=True,
    ),
    _record(
        5,
        "FULL_COPY_T4",
        eligible=True,
        reason="eligible",
        status="primary_not_actionable_t4",
        latency_us=11_000,
        miss=False,
        nominal_us=80.0,
        estimated_us=100.0,
    ),
    _record(
        6,
        "FULL_COPY_T4",
        eligible=False,
        reason="primary_not_actionable_t2",
        status="not_exposed_ineligible_t2",
        latency_us=40_000,
        miss=True,
    ),
]


def _make_run(
    directory: Path,
    run_id: str = "randomized-analysis-test",
    seed: int = 17,
) -> None:
    directory.mkdir()
    config = {
        "run_id": run_id,
        "seed": seed,
        "run": 1,
        "policy": "randomized_full_copy_exploration",
        "topology": "dual_interface",
        "duration_s": 1,
        "background": {"profile": "mixed4x4"},
        "randomizedIntervention": {
            "csv_schema_version": 1,
            "assignment_algorithm": "splitmix64_v1",
            "assignment_salt": 99,
            "arm_probabilities": {
                "CONTROL": 0.4,
                "FULL_COPY_T2": 0.3,
                "FULL_COPY_T4": 0.3,
            },
            "stages": ["T2", "T4"],
            "stage_offsets_us": [2000, 4000],
            "common_eligibility_rule": "test_common_rule",
            "intervention": "canonical_full_secondary_copy",
            "cost_estimator_id": "test_one_ppdu_estimator",
        },
    }
    (directory / "resolved_config.json").write_text(
        json.dumps(config), encoding="utf-8"
    )
    (directory / "build_info.json").write_text(
        json.dumps({
            "ns3_version": "ns-3.48",
            "ns3_upstream_commit": "upstream",
            "project_git_commit": "project",
            "compiler": "compiler",
            "build_profile": "optimized",
        }),
        encoding="utf-8",
    )

    frame_rows: list[dict[str, object]] = []
    assignment_rows: list[dict[str, object]] = []
    execution_rows: list[dict[str, object]] = []
    settlement_rows: list[dict[str, object]] = []
    propensities = {
        "CONTROL": 0.4,
        "FULL_COPY_T2": 0.3,
        "FULL_COPY_T4": 0.3,
    }
    for record in RECORDS:
        frame_id = int(record["frame_id"])
        incomplete = record["latency_us"] is None
        generation_us = 1_000_000 + frame_id * 33_333
        completion_us = (
            "" if incomplete else generation_us + int(record["latency_us"])
        )
        frame_rows.append({
            "run_id": run_id,
            "frame_id": frame_id,
            "generation_time_us": generation_us,
            "frame_size_bytes": 12_000,
            "packet_count": 10,
            "frame_type": "P_FRAME",
            "deadline_us": 33_333,
            "policy": "randomized_full_copy_exploration",
            "primary_link": 1,
            "duplicated": int(bool(record["launched"])),
            "union_completion_us": completion_us,
            "union_latency_us": "" if incomplete else record["latency_us"],
            "deadline_miss": int(bool(record["miss"])),
            "incomplete": int(incomplete),
        })
        common = {
            "schema_version": 1,
            "run_id": run_id,
            "frame_id": frame_id,
            "eligible_t2": int(bool(record["eligible"])),
            "eligibility_reason": record["reason"],
            "assigned_arm": record["arm"],
            "propensity": propensities[str(record["arm"])],
            "nominal_airtime_us": record["nominal_us"],
            "estimated_airtime_us": record["estimated_us"],
        }
        assignment_rows.append(common)
        execution_rows.append({
            **common,
            "execution_stage": (
                "T4"
                if record["eligible"] and record["arm"] == "FULL_COPY_T4"
                else "T2"
            ),
            "attempted": int(bool(record["attempted"])),
            "launched": int(bool(record["launched"])),
            "noncompliance": int(bool(record["noncompliance"])),
            "status": record["status"],
        })
        if record["launched"]:
            settlement_rows.append({
                "run_id": run_id,
                "frame_id": frame_id,
                "settlement_time_ns": 2_000_000_000 + frame_id,
                "released_airtime_us": record["released_us"],
                "measured_airtime_us": record["measured_us"],
                "nominal_airtime_us": record["nominal_us"],
                "fallback": int(bool(record["fallback"])),
            })

    _write_csv(directory / "frames.csv", FRAME_COLUMNS, frame_rows)
    _write_csv(
        directory / "randomized_intervention_assignments.csv",
        RANDOMIZED_ASSIGNMENT_COLUMNS,
        assignment_rows,
    )
    _write_csv(
        directory / "randomized_intervention_executions.csv",
        RANDOMIZED_EXECUTION_COLUMNS,
        execution_rows,
    )
    _write_csv(
        directory / "secondary_airtime_settlements.csv",
        SECONDARY_AIRTIME_SETTLEMENT_COLUMNS,
        settlement_rows,
    )
    (directory / "secondary_airtime_summary.json").write_text(
        json.dumps({
            "measurement_duration_us": 1_000_000,
            "tagged_secondary_tx_airtime_us": 200,
            "estimated_action_airtime_us": 225,
            "forced_reservation_settlements": 1,
            "maximum_budget_debt_us": 0,
        }),
        encoding="utf-8",
    )


class RandomizedInterventionAnalysisTest(unittest.TestCase):
    def test_reports_assignment_execution_outcomes_and_airtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            _make_run(run_dir)
            with mock.patch(
                "analyze_randomized_intervention.validate_run",
                return_value={"valid": True},
            ) as validator:
                report = analyze_randomized_runs([run_dir])

            validator.assert_called_once_with(run_dir.resolve())
            self.assertEqual(report["analysis_schema_version"], 1)
            self.assertFalse(report["causal_model_fitted"])
            self.assertEqual(report["overall"]["assigned_frame_count"], 7)
            self.assertEqual(report["overall"]["eligible_t2_count"], 5)
            self.assertAlmostEqual(
                report["overall"]["outcomes_all_assigned"]["deadline_miss_rate"],
                3 / 7,
            )
            self.assertEqual(
                report["overall"]["outcomes_all_assigned"]["complete_count"], 6
            )
            self.assertEqual(
                report["overall"]["meter"]["tagged_secondary_tx_airtime_us"],
                200,
            )
            self.assertAlmostEqual(
                report["overall"]["meter"]["actual_to_estimated_airtime_ratio"],
                200 / 225,
            )

            control = report["arms"]["CONTROL"]
            self.assertEqual(control["assigned_count"], 2)
            self.assertEqual(control["eligible_t2_count"], 1)
            self.assertEqual(control["exposed_count"], 0)
            self.assertEqual(
                control["eligibility_reason_counts"],
                {"eligible": 1, "outside_assignment_window": 1},
            )

            t2 = report["arms"]["FULL_COPY_T2"]
            self.assertEqual(t2["attempted_count"], 2)
            self.assertEqual(t2["exposed_count"], 1)
            self.assertEqual(t2["noncompliance_count"], 1)
            self.assertEqual(t2["protocol_compliant_eligible_count"], 1)
            self.assertEqual(t2["protocol_compliance_rate_among_eligible"], 0.5)
            self.assertAlmostEqual(
                t2["outcomes_common_eligible_t2"]["complete_latency_p99_us"],
                39_680,
            )
            self.assertEqual(
                t2["measured_airtime"]["measured_exposure_airtime_us"], 110
            )

            t4 = report["arms"]["FULL_COPY_T4"]
            self.assertEqual(t4["assigned_count"], 3)
            self.assertEqual(t4["eligible_t2_count"], 2)
            self.assertEqual(t4["attempted_count"], 1)
            self.assertEqual(t4["protocol_compliant_eligible_count"], 2)
            self.assertEqual(t4["protocol_compliance_rate_among_eligible"], 1.0)
            self.assertEqual(
                t4["execution_status_counts"]["primary_not_actionable_t4"], 1
            )
            self.assertEqual(
                t4["measured_airtime"]["fallback_settlement_count"], 1
            )

    def test_exposes_frame_level_join_for_later_models(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            _make_run(run_dir)
            with mock.patch(
                "analyze_randomized_intervention.validate_run",
                return_value={"valid": True},
            ):
                loaded = load_randomized_run(run_dir)
            observation = next(
                item for item in loaded.observations if item.frame_id == 4
            )
            self.assertEqual(observation.arm, "FULL_COPY_T4")
            self.assertTrue(observation.eligible_t2)
            self.assertTrue(observation.launched)
            self.assertEqual(observation.union_latency_us, 12_000)
            self.assertEqual(observation.measured_airtime_us, 90)

    def test_authoritative_validation_happens_before_loading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing"
            with mock.patch(
                "analyze_randomized_intervention.validate_run",
                side_effect=ValidationError("invalid randomized ledger"),
            ) as validator:
                with self.assertRaisesRegex(
                    ValidationError, "invalid randomized ledger"
                ):
                    load_randomized_run(missing)
            validator.assert_called_once_with(missing.resolve())

    def test_rejects_pooling_different_randomized_designs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            _make_run(first, run_id="first", seed=17)
            _make_run(second, run_id="second", seed=18)
            config_path = second / "resolved_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["randomizedIntervention"]["assignment_salt"] = 100
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with mock.patch(
                "analyze_randomized_intervention.validate_run",
                return_value={"valid": True},
            ):
                with self.assertRaisesRegex(AnalysisError, "design differs"):
                    analyze_randomized_runs([first, second])

    def test_rejects_duplicate_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            _make_run(run_dir)
            with self.assertRaisesRegex(AnalysisError, "more than once"):
                analyze_randomized_runs([run_dir, run_dir])


if __name__ == "__main__":
    unittest.main()
