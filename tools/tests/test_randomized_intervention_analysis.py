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
    RANDOMIZED_PREDICTION_COLUMNS,
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
    primary_latency_us: int | None = None,
    t4_primary_actionable: bool = True,
) -> dict[str, object]:
    if primary_latency_us is None and latency_us is not None:
        primary_latency_us = latency_us
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
        "primary_latency_us": primary_latency_us,
        "t4_primary_actionable": t4_primary_actionable,
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
        primary_latency_us=20_000,
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
        primary_latency_us=40_000,
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
        t4_primary_actionable=False,
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
    records: list[dict[str, object]] | None = None,
) -> None:
    directory.mkdir()
    source_records = RECORDS if records is None else records
    config = {
        "run_id": run_id,
        "seed": seed,
        "run": 1,
        "policy": "randomized_full_copy_exploration",
        "topology": "dual_interface",
        "duration_s": 1,
        "background": {
            "profile": "mixed4x4",
            "obss": {
                "area_min_x_m": -15,
                "area_max_x_m": 15,
                "placement_stream_base": 6000,
                "bsses": [{
                    "bss_id": 0,
                    "link_id": 0,
                    "ssid": "test-obss-0",
                    "standard": "802.11ax",
                    "ap": [-2.0, 3.0],
                    "stas": [[-1.0, 3.0], [-3.0, 4.0]],
                }],
            },
        },
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
    prediction_rows: list[dict[str, object]] = []
    settlement_rows: list[dict[str, object]] = []
    propensities = {
        "CONTROL": 0.4,
        "FULL_COPY_T2": 0.3,
        "FULL_COPY_T4": 0.3,
    }
    for record in source_records:
        frame_id = int(record["frame_id"])
        incomplete = record["latency_us"] is None
        generation_us = 1_000_000 + frame_id * 33_333
        completion_us = (
            "" if incomplete else generation_us + int(record["latency_us"])
        )
        primary_completion_us = (
            ""
            if record["primary_latency_us"] is None
            else generation_us + int(record["primary_latency_us"])
        )
        secondary_completion_us = (
            "" if not record["launched"] else completion_us
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
            "copy_0_completion_us": primary_completion_us,
            "copy_1_completion_us": secondary_completion_us,
            "deadline_miss": int(bool(record["miss"])),
            "incomplete": int(incomplete),
        })
        for offset_us, stage in ((2000, "T2"), (4000, "T4")):
            for path_id, copy_id in ((1, 0), (0, 1)):
                primary = path_id == 1
                actionable = True
                if primary and offset_us == 2000:
                    actionable = record["reason"] != "primary_not_actionable_t2"
                elif primary and offset_us == 4000:
                    actionable = bool(record["t4_primary_actionable"])
                prediction_rows.append({
                    "run_id": run_id,
                    "frame_id": frame_id,
                    "path_id": path_id,
                    "copy_id": copy_id,
                    "sample_stage": stage,
                    "sample_offset_us": offset_us,
                    "sample_time_ns": generation_us * 1000 + offset_us * 1000,
                    "latest_feature_event_time_ns": generation_us * 1000,
                    "latest_feature_event_sequence": frame_id,
                    "generation_time_ns": generation_us * 1000,
                    "deadline_time_ns": (generation_us + 33_333) * 1000,
                    "sender_mac_complete": int(not actionable),
                    "actionable": int(actionable),
                    "frame_size_bytes": 12_000,
                    "frame_packet_count": 10,
                    "frame_type": "P_FRAME",
                    "packets_submitted": 0,
                    "application_socket_packet_bytes_submitted": 0,
                    "packets_remaining_to_submit": 10,
                    "frame_packets_mac_enqueued": 0,
                    "frame_packets_mac_dequeued": 0,
                    "frame_packets_tx_succeeded": 0,
                    "frame_mpdu_attempt_failures": 0,
                    "frame_packets_terminally_dropped": 0,
                    "frame_packets_currently_queued": 0,
                    "frame_mac_service_bytes_currently_queued": 0,
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
        directory / "prediction_samples.csv",
        RANDOMIZED_PREDICTION_COLUMNS,
        prediction_rows,
    )
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
            "tagged_secondary_tx_airtime_us": sum(
                float(record["measured_us"])
                for record in source_records
                if record["launched"]
            ),
            "estimated_action_airtime_us": sum(
                float(record["estimated_us"])
                for record in source_records
                if record["launched"]
            ),
            "forced_reservation_settlements": sum(
                bool(record["fallback"])
                for record in source_records
                if record["launched"]
            ),
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
            self.assertEqual(report["analysis_schema_version"], 2)
            self.assertFalse(report["causal_model_fitted"])
            self.assertFalse(report["inference"]["causal_claim_made"])
            self.assertFalse(
                report["primary_copy_placebo_diagnostics"][
                    "mechanistic_rescue_claim_allowed"
                ]
            )
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

            balance = report["assignment_balance"]["all_assigned_frames"]
            self.assertEqual(balance["arms"]["CONTROL"]["observed_count"], 2)
            self.assertAlmostEqual(
                balance["arms"]["CONTROL"]["expected_count"], 2.8
            )
            self.assertAlmostEqual(
                balance["arms"]["FULL_COPY_T2"][
                    "observed_minus_configured_fraction"
                ],
                2 / 7 - 0.3,
            )

            t2_contrasts = report["randomized_arm_contrasts"][
                "t2_common_eligible_itt"
            ]["contrasts"]
            deadline = t2_contrasts["t2_vs_control"]["deadline_miss"]
            self.assertEqual(deadline["left"]["event_count"], 1)
            self.assertEqual(deadline["right"]["event_count"], 0)
            self.assertEqual(deadline["observed_rate_difference"], 0.5)
            self.assertAlmostEqual(
                deadline["horvitz_thompson_rate_difference"], 2 / 3
            )
            ten_ms = t2_contrasts["t4_regime_vs_control"][
                "unconditional_latency_tail"
            ]["thresholds"][0]
            self.assertEqual(ten_ms["threshold_ms"], 10)
            self.assertEqual(ten_ms["contrast"]["left"]["event_count"], 2)
            self.assertEqual(ten_ms["contrast"]["right"]["event_count"], 0)

            t4_risk = report["randomized_arm_contrasts"][
                "t4_common_risk_set"
            ]
            self.assertEqual(t4_risk["frame_count"], 2)
            self.assertEqual(
                t4_risk["arm_counts"], {"CONTROL": 1, "FULL_COPY_T4": 1}
            )
            self.assertAlmostEqual(
                t4_risk["configured_conditional_probabilities"]["FULL_COPY_T4"],
                0.3 / 0.7,
            )
            self.assertEqual(
                t4_risk["contrasts"]["t4_regime_vs_control"][
                    "deadline_miss"
                ]["observed_rate_difference"],
                0,
            )

            placebo = report["primary_copy_placebo_diagnostics"]["contrasts"]
            self.assertEqual(
                placebo["t4_regime_vs_control"]["deadline_miss"]["left"][
                    "event_count"
                ],
                1,
            )
            diversity = report["realized_diversity_among_launched"]
            self.assertEqual(diversity["launched_frame_count"], 2)
            self.assertEqual(diversity["deadline_rescue_count"], 1)
            self.assertEqual(diversity["realized_diversity_benefit_count"], 2)
            self.assertEqual(
                diversity["capped_latency_saving_us_per_launch"]["p50"],
                (12_000 + 21_333) / 2,
            )
            cost = report["exposure_cost_distribution"]
            self.assertEqual(cost["measured_airtime_us"]["count"], 2)
            self.assertEqual(cost["measured_airtime_us"]["p50"], 100)
            self.assertAlmostEqual(
                cost["measured_to_estimated_ratio"]["p50"],
                (110 / 125 + 90 / 100) / 2,
            )
            self.assertEqual(report["per_run_counts"][0]["launched_count"], 2)
            self.assertEqual(
                report["per_run_counts"][0]["deadline_rescue_count_among_launched"],
                1,
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
            self.assertEqual(observation.primary_latency_us, 40_000)
            self.assertTrue(observation.primary_deadline_miss)
            self.assertTrue(observation.t4_primary_actionable)
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

    def test_pools_seed_realized_positions_but_not_environment_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            _make_run(first, run_id="first", seed=17)
            _make_run(second, run_id="second", seed=18)
            config_path = second / "resolved_config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            bss = config["background"]["obss"]["bsses"][0]
            bss["ap"] = [8.0, -4.0]
            bss["stas"] = [[7.0, -4.0], [9.0, -3.0]]
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with mock.patch(
                "analyze_randomized_intervention.validate_run",
                return_value={"valid": True},
            ):
                forward = analyze_randomized_runs([first, second])
                reverse = analyze_randomized_runs([second, first])
            self.assertEqual(forward["run_count"], 2)
            interval = forward["randomized_arm_contrasts"][
                "t2_common_eligible_itt"
            ]["contrasts"]["t2_vs_control"]["deadline_miss"][
                "run_cluster_bootstrap"
            ]["observed_rate_difference"]
            self.assertTrue(interval["available"])
            self.assertEqual(interval["cluster_count"], 2)
            self.assertEqual(interval["requested_replicates"], 20_000)
            self.assertEqual(interval["ci95_low"], 0.5)
            self.assertEqual(interval["ci95_high"], 0.5)
            self.assertTrue(
                forward["primary_copy_placebo_diagnostics"][
                    "any_unadjusted_cluster_ci_excludes_zero"
                ]
            )
            self.assertEqual(
                [item["run_id"] for item in forward["per_run_counts"]],
                ["first", "second"],
            )
            self.assertEqual(
                json.dumps(forward, sort_keys=True),
                json.dumps(reverse, sort_keys=True),
            )

            config["background"]["obss"]["bsses"][0]["standard"] = "802.11n"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with mock.patch(
                "analyze_randomized_intervention.validate_run",
                return_value={"valid": True},
            ):
                with self.assertRaisesRegex(AnalysisError, "environment differs"):
                    analyze_randomized_runs([first, second])

    def test_t4_conditional_ht_bootstraps_heterogeneous_run_clusters(self) -> None:
        first_records = [
            _record(
                0,
                "FULL_COPY_T4",
                eligible=True,
                reason="eligible",
                status="launch_rejected_t4",
                latency_us=40_000,
                miss=True,
                attempted=True,
                noncompliance=True,
            ),
            _record(
                1,
                "CONTROL",
                eligible=True,
                reason="eligible",
                status="control_no_launch",
                latency_us=8_000,
                miss=False,
            ),
        ]
        second_records = [
            _record(
                0,
                "FULL_COPY_T4",
                eligible=True,
                reason="eligible",
                status="launch_rejected_t4",
                latency_us=8_000,
                miss=False,
                attempted=True,
                noncompliance=True,
            ),
            _record(
                1,
                "CONTROL",
                eligible=True,
                reason="eligible",
                status="control_no_launch",
                latency_us=40_000,
                miss=True,
            ),
            _record(
                2,
                "CONTROL",
                eligible=True,
                reason="eligible",
                status="control_no_launch",
                latency_us=40_000,
                miss=True,
            ),
            _record(
                3,
                "CONTROL",
                eligible=True,
                reason="eligible",
                status="control_no_launch",
                latency_us=8_000,
                miss=False,
            ),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            _make_run(first, run_id="first", seed=17, records=first_records)
            _make_run(second, run_id="second", seed=18, records=second_records)
            with mock.patch(
                "analyze_randomized_intervention.validate_run",
                return_value={"valid": True},
            ):
                report = analyze_randomized_runs(
                    [first, second], bootstrap_replicates=2_000
                )

        self.assertEqual(
            [item["assigned_frame_count"] for item in report["per_run_counts"]],
            [2, 4],
        )
        deadline = report["randomized_arm_contrasts"]["t4_common_risk_set"][
            "contrasts"
        ]["t4_regime_vs_control"]["deadline_miss"]
        self.assertEqual(deadline["observed_rate_difference"], 0)
        self.assertAlmostEqual(
            deadline["left"]["known_propensity_normalizer"], 0.7
        )
        self.assertAlmostEqual(
            deadline["left"]["known_propensity_weight_sum"], 14 / 3
        )
        self.assertAlmostEqual(
            deadline["right"]["known_propensity_weight_sum"], 7
        )
        self.assertAlmostEqual(
            deadline["horvitz_thompson_rate_difference"], -7 / 36
        )

        intervals = deadline["run_cluster_bootstrap"]
        observed = intervals["observed_rate_difference"]
        self.assertEqual(observed["valid_replicates"], 2_000)
        self.assertEqual(observed["invalid_replicates"], 0)
        self.assertAlmostEqual(observed["ci95_low"], -2 / 3)
        self.assertAlmostEqual(observed["ci95_high"], 1)
        ht = intervals["horvitz_thompson_rate_difference"]
        self.assertAlmostEqual(ht["estimate"], -7 / 36)
        self.assertAlmostEqual(ht["ci95_low"], -7 / 8)
        self.assertAlmostEqual(ht["ci95_high"], 7 / 6)

    def test_unconditional_tail_counts_incomplete_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            _make_run(run_dir)
            frame_path = run_dir / "frames.csv"
            with frame_path.open(newline="", encoding="utf-8") as source:
                rows = list(csv.DictReader(source))
            frame = next(row for row in rows if row["frame_id"] == "3")
            frame["union_completion_us"] = ""
            frame["union_latency_us"] = ""
            frame["copy_0_completion_us"] = ""
            frame["incomplete"] = "1"
            _write_csv(frame_path, set(rows[0]), rows)
            with mock.patch(
                "analyze_randomized_intervention.validate_run",
                return_value={"valid": True},
            ):
                report = analyze_randomized_runs([run_dir])
            threshold = report["randomized_arm_contrasts"][
                "t2_common_eligible_itt"
            ]["contrasts"]["t2_vs_control"]["unconditional_latency_tail"][
                "thresholds"
            ][-1]
            self.assertEqual(threshold["threshold_ms"], 20)
            self.assertEqual(threshold["contrast"]["left"]["assigned_count"], 2)
            self.assertEqual(threshold["contrast"]["left"]["event_count"], 1)
            self.assertEqual(
                threshold["contrast"]["left"]["observed_event_rate"], 0.5
            )

    def test_rejects_incomplete_paired_t4_prediction_join(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            _make_run(run_dir)
            prediction_path = run_dir / "prediction_samples.csv"
            with prediction_path.open(newline="", encoding="utf-8") as source:
                rows = list(csv.DictReader(source))
            rows.pop()
            _write_csv(prediction_path, set(rows[0]), rows)
            with mock.patch(
                "analyze_randomized_intervention.validate_run",
                return_value={"valid": True},
            ):
                with self.assertRaisesRegex(
                    AnalysisError, "paired sample coverage mismatch"
                ):
                    analyze_randomized_runs([run_dir])

    def test_rejects_duplicate_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            _make_run(run_dir)
            with self.assertRaisesRegex(AnalysisError, "more than once"):
                analyze_randomized_runs([run_dir, run_dir])


if __name__ == "__main__":
    unittest.main()
