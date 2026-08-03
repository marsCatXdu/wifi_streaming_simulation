#!/usr/bin/env python3
"""Focused checks for the frame-level T4 efficiency diagnostic."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from analyze_primary_tail_t4_campaign import (  # noqa: E402
    DECLARED_DEFICIT_ARM,
    DECLARED_SERIALIZED_T4_GATES,
    CampaignError,
)
from analyze_primary_tail_t4_efficiency import (  # noqa: E402
    OUTCOMES,
    SETTLEMENT_COLUMNS,
    _airtime_efficiency,
    analyze_efficiency,
)


class EfficiencyFixture:
    """Build a small seven-arm campaign after mocking the strict validator."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.runs: list[dict[str, Any]] = []
        self.directories: dict[str, Path] = {}
        self.full_arms = sorted(DECLARED_SERIALIZED_T4_GATES)
        self.arm_ids = [*self.full_arms, DECLARED_DEFICIT_ARM]
        self._write_baseline("str_mlo", "mlo_str")
        self._write_baseline("emlsr_mlo", "mlo_emlsr")
        for arm_id in self.full_arms:
            self._write_adaptive(arm_id, "full_copy")
        self._write_adaptive(DECLARED_DEFICIT_ARM, "primary_deficit")
        self.aggregate_path = self.root / "aggregate.json"
        self.write_aggregate()

    @staticmethod
    def _write_csv(
        path: Path,
        rows: list[dict[str, Any]],
        *,
        fieldnames: list[str] | None = None,
    ) -> None:
        names = fieldnames or list(rows[0])
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=names)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _frame(
        run_id: str,
        frame_id: int,
        *,
        union_latency: int | None,
        primary_latency: int | None,
        deadline_us: int = 100,
        duplicated: bool = False,
    ) -> dict[str, Any]:
        generation = 1_000_000 + 1_000 * frame_id
        union_completion = (
            "" if union_latency is None else str(generation + union_latency)
        )
        primary_completion = (
            "" if primary_latency is None else str(generation + primary_latency)
        )
        deadline_miss = union_latency is None or union_latency > deadline_us
        secondary_completion = ""
        if duplicated and union_latency is not None and (
            primary_latency is None or union_latency < primary_latency
        ):
            secondary_completion = str(generation + union_latency)
        return {
            "run_id": run_id,
            "frame_id": str(frame_id),
            "generation_time_us": str(generation),
            "packet_count": "2",
            "frame_type": "I_FRAME" if frame_id == 0 else "P_FRAME",
            "deadline_us": str(deadline_us),
            "duplicated": "1" if duplicated else "0",
            "union_completion_us": union_completion,
            "union_latency_us": "" if union_latency is None else str(union_latency),
            "copy_0_completion_us": primary_completion,
            "copy_1_completion_us": secondary_completion,
            "deadline_miss": "1" if deadline_miss else "0",
            "incomplete": "1" if union_latency is None else "0",
            "completion_mode": (
                ""
                if union_latency is None
                else ("link_0_only" if secondary_completion else "link_1_only")
            ),
        }

    def _config(self, run_id: str, topology: str, policy: str) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "seed": 43,
            "run": 1,
            "topology": topology,
            "policy": policy,
        }

    def _register(
        self,
        label: str,
        topology: str,
        policy: str,
        config: dict[str, Any],
    ) -> Path:
        directory = self.root / label
        directory.mkdir()
        (directory / "resolved_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        self.runs.append({
            "run_id": label,
            "run_dir": str(directory),
            "seed": 43,
            "run": 1,
            "topology": topology,
            "policy": policy,
            "config": config,
        })
        self.directories[label] = directory
        return directory

    def _write_baseline(self, label: str, topology: str) -> None:
        config = self._config(label, topology, "fixed_link_0")
        directory = self._register(
            label, topology, "fixed_link_0", config
        )
        specifications = [
            (None, None),
            (80, 80),
            (90, 90),
            (160, 160),
            (70, 70),
            (60, 60),
        ]
        frames = [
            self._frame(
                label,
                frame_id,
                union_latency=union,
                primary_latency=primary,
            )
            for frame_id, (union, primary) in enumerate(specifications)
        ]
        self._write_csv(directory / "frames.csv", frames)

    def _write_adaptive(self, arm_id: str, mechanism: str) -> None:
        label = arm_id
        if mechanism == "full_copy":
            policy = "adaptive_airtime_duplication"
            key = "adaptiveAirtimeDuplication"
            gate = DECLARED_SERIALIZED_T4_GATES[arm_id]
        else:
            policy = "adaptive_deficit_duplication"
            key = "adaptiveDeficitDuplication"
            gate = DECLARED_SERIALIZED_T4_GATES["full_copy_cost_0p0080"]
        config = self._config(label, "dual_interface", policy)
        config[key] = {"decision_offset_shadow_prices": {"4000": gate}}
        directory = self._register(label, "dual_interface", policy, config)
        specifications = [
            (50, None, True),
            (70, 90, True),
            (None, None, False),
            (150, None, True),
            (80, 80, False),
            (60, 60, True),
        ]
        frames = [
            self._frame(
                label,
                frame_id,
                union_latency=union,
                primary_latency=primary,
                duplicated=duplicated,
            )
            for frame_id, (union, primary, duplicated) in enumerate(specifications)
        ]
        self._write_csv(directory / "frames.csv", frames)
        decisions: list[dict[str, Any]] = []
        for frame_id in range(6):
            t0_decision = "action" if frame_id == 0 else "frame_type_restricted"
            t4_decision = {
                0: "already_resolved",
                1: "action",
                2: "price_rejected",
                3: "action",
                4: "price_rejected",
                5: "action",
            }[frame_id]
            for stage, offset, decision in (
                ("T0", 0, t0_decision),
                ("T4", 4000, t4_decision),
            ):
                actionable = decision not in {
                    "frame_type_restricted",
                    "already_resolved",
                }
                decisions.append({
                    "run_id": label,
                    "frame_id": str(frame_id),
                    "sample_stage": stage,
                    "sample_offset_us": str(offset),
                    "actionable": "1" if actionable else "0",
                    "decision": decision,
                    "secondary_launched": "1" if decision == "action" else "0",
                })
        self._write_csv(directory / "adaptive_airtime_decisions.csv", decisions)
        settlements = [
            {
                "run_id": label,
                "frame_id": str(frame_id),
                "measured_airtime_us": str(measured),
                "fallback": "0",
            }
            for frame_id, measured in ((0, 10), (1, 20), (3, 30), (5, 40))
        ]
        self._write_csv(directory / "secondary_airtime_settlements.csv", settlements)
        (directory / "summary.json").write_text(
            json.dumps({"latency_p99_us": 147.2}), encoding="utf-8"
        )

    def write_aggregate(self) -> None:
        self.aggregate_path.write_text(
            json.dumps({"schema_version": 1, "runs": self.runs}),
            encoding="utf-8",
        )

    def validation_report(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "analysis": "neutral_primary_tail_t4_multi_arm_vs_mlo",
            "paired_unit_count": 1,
            "paired_units": [{"seed": 43, "run": 1}],
            "source_aggregates": [str(self.aggregate_path.resolve())],
            "ignored_noncampaign_run_count": 0,
            "arms": {arm_id: {} for arm_id in self.arm_ids},
            "comparisons": {arm_id: {} for arm_id in self.full_arms},
            "campaign_checks": {
                "complete_paired_arm_matrix": True,
                "all_runs_core_validated": True,
                "frame_csv_p99_quantization_bound_us": 1.0,
            },
        }


class PrimaryTailT4EfficiencyAnalysisTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = EfficiencyFixture(self.root)
        self.validation_patch = mock.patch(
            "analyze_primary_tail_t4_efficiency.validate_campaign",
            return_value=self.fixture.validation_report(),
        )
        self.validate_campaign = self.validation_patch.start()

    def tearDown(self) -> None:
        self.validation_patch.stop()
        self.temporary.cleanup()

    def analyze(self) -> dict[str, Any]:
        return analyze_efficiency(
            self.root,
            bootstrap_replicates=7,
            preflight=True,
        )

    def test_report_is_deterministic_and_strict_validation_runs_first(self) -> None:
        first = self.analyze()
        second = self.analyze()
        self.assertEqual(first, second)
        self.assertEqual(self.validate_campaign.call_count, 2)
        _, kwargs = self.validate_campaign.call_args
        self.assertTrue(kwargs["preflight"])
        self.assertEqual(kwargs["bootstrap_replicates"], 7)
        self.assertEqual(first["paired_unit_count"], 1)
        self.assertEqual(list(first["arms"]), sorted(self.fixture.arm_ids))
        self.assertTrue(first["campaign_validation"]["all_runs_core_validated"])
        json.dumps(first, allow_nan=False, sort_keys=True)

    def test_selection_outcome_and_airtime_accounting(self) -> None:
        pooled = self.analyze()["arms"]["full_copy_cost_0p0080"][
            "pooled_frame_diagnostics"
        ]
        funnel = pooled["selection_funnel"]
        self.assertEqual(funnel["sampled_count"], 12)
        self.assertEqual(funnel["actionable_count"], 6)
        self.assertEqual(funnel["price_gate_evaluated_count"], 6)
        self.assertEqual(funnel["excluded_before_price_gate_count"], 6)
        self.assertEqual(funnel["price_qualified_count"], 4)
        self.assertEqual(funnel["token_qualified_count"], 4)
        self.assertEqual(funnel["launched_count"], 4)
        outcomes = pooled["factual_outcomes"]
        self.assertEqual(outcomes["frame_count"], 6)
        self.assertEqual(outcomes["primary_deadline_miss_count"], 3)
        self.assertEqual(outcomes["union_deadline_miss_count"], 2)
        self.assertEqual(outcomes["unacted_primary_miss_count"], 1)
        self.assertEqual(outcomes["deadline_rescue_count"], 1)
        self.assertEqual(outcomes["duplicate_recovery_count"], 3)
        self.assertEqual(outcomes["duplicate_no_benefit_count"], 1)
        self.assertEqual(
            outcomes["outcome_counts"],
            {
                "primary_on_time_latency_benefit": 1,
                "primary_on_time_no_benefit": 1,
                "primary_miss_deadline_rescue": 1,
                "primary_miss_late_completion": 1,
                "primary_miss_incomplete": 0,
            },
        )
        self.assertEqual(outcomes["primary_incomplete_union_recovery_count"], 2)
        self.assertEqual(
            outcomes["quantified_primary_completion_acceleration_us"]["sum"], 20
        )
        self.assertEqual(outcomes["deadline_rescue_slack_us"]["mean"], 50)
        airtime = pooled["airtime_efficiency"]
        self.assertEqual(airtime["total_measured_airtime_us"], 100)
        self.assertEqual(
            airtime["total_measured_airtime_per_deadline_rescue_us"], 100
        )
        self.assertEqual(
            airtime["successful_rescue_airtime_per_deadline_rescue_us"], 10
        )
        self.assertAlmostEqual(
            airtime["total_measured_airtime_per_duplicate_recovery_us"],
            100 / 3,
        )
        self.assertEqual(
            airtime["by_factual_outcome"]["primary_miss_late_completion"][
                "measured_airtime_us"
            ],
            30,
        )

    def test_t0_t4_attribution_and_p99_censoring(self) -> None:
        pooled = self.analyze()["arms"]["full_copy_cost_0p0080"][
            "pooled_frame_diagnostics"
        ]
        by_stage = pooled["by_launch_stage"]
        self.assertEqual(
            by_stage["T0"]["factual_action_outcomes"]["deadline_rescue_count"], 1
        )
        self.assertEqual(
            by_stage["T0"]["airtime_efficiency"]["total_measured_airtime_us"], 10
        )
        self.assertEqual(
            by_stage["T4"]["factual_action_outcomes"]["action_count"], 3
        )
        self.assertEqual(
            by_stage["T4"]["airtime_efficiency"]["total_measured_airtime_us"], 90
        )
        censoring = pooled["p99_censoring"]
        self.assertAlmostEqual(censoring["all_union_completed_p99_us"], 147.2)
        self.assertAlmostEqual(censoring["primary_copy_p99_us"], 89.8)
        self.assertAlmostEqual(
            censoring["union_p99_on_primary_completed_population_us"], 79.8
        )
        self.assertAlmostEqual(
            censoring["fixed_population_primary_minus_union_p99_gain_us"], 10
        )
        self.assertAlmostEqual(
            censoring["completion_set_composition_shift_p99_us"], 67.4
        )
        self.assertEqual(censoring["primary_incomplete_count"], 3)
        self.assertEqual(censoring["primary_incomplete_to_union_on_time_count"], 1)
        self.assertEqual(censoring["primary_incomplete_to_union_late_count"], 1)
        self.assertEqual(censoring["primary_incomplete_to_union_incomplete_count"], 1)
        top = censoring["top_one_percent_with_ties"]
        self.assertEqual(top["frame_count"], 1)
        self.assertEqual(top["launch_stage_counts"], {"T0": 0, "T4": 1})
        self.assertEqual(top["primary_incomplete_late_entrant_count"], 1)
        per_run = self.analyze()["arms"]["full_copy_cost_0p0080"][
            "by_paired_unit"
        ][0]["p99_censoring"]
        self.assertEqual(per_run["validated_summary_headline_union_p99_us"], 147.2)
        self.assertEqual(per_run["frame_csv_headline_quantization_delta_us"], 0)

    def test_frame_type_strata_reconcile_overall_and_by_stage(self) -> None:
        pooled = self.analyze()["arms"]["full_copy_cost_0p0080"][
            "pooled_frame_diagnostics"
        ]
        by_type = pooled["by_frame_type"]
        self.assertEqual(list(by_type), ["I_FRAME", "P_FRAME"])
        self.assertEqual(by_type["I_FRAME"]["factual_outcomes"]["frame_count"], 1)
        self.assertEqual(
            by_type["I_FRAME"]["factual_outcomes"]["deadline_rescue_count"], 1
        )
        self.assertEqual(
            by_type["I_FRAME"]["airtime_efficiency"]["total_measured_airtime_us"],
            10,
        )
        self.assertEqual(by_type["P_FRAME"]["factual_outcomes"]["frame_count"], 5)
        self.assertEqual(
            by_type["P_FRAME"]["factual_outcomes"]["unacted_primary_miss_count"],
            1,
        )
        self.assertEqual(
            by_type["P_FRAME"]["airtime_efficiency"]["total_measured_airtime_us"],
            90,
        )
        for field in ("frame_count", "action_count", "deadline_rescue_count"):
            self.assertEqual(
                sum(item["factual_outcomes"][field] for item in by_type.values()),
                pooled["factual_outcomes"][field],
            )
        self.assertEqual(
            sum(
                item["airtime_efficiency"]["total_measured_airtime_us"]
                for item in by_type.values()
            ),
            pooled["airtime_efficiency"]["total_measured_airtime_us"],
        )

        by_stage = pooled["by_launch_stage"]
        t0_types = by_stage["T0"]["by_frame_type"]
        t4_types = by_stage["T4"]["by_frame_type"]
        self.assertEqual(
            t0_types["I_FRAME"]["factual_action_outcomes"]["action_count"], 1
        )
        self.assertEqual(
            t0_types["P_FRAME"]["factual_action_outcomes"]["action_count"], 0
        )
        self.assertEqual(
            t4_types["I_FRAME"]["airtime_efficiency"]["total_measured_airtime_us"],
            0,
        )
        self.assertEqual(
            t4_types["P_FRAME"]["factual_action_outcomes"]["action_count"], 3
        )
        self.assertEqual(
            t4_types["P_FRAME"]["factual_action_outcomes"]["outcome_counts"][
                "primary_on_time_no_benefit"
            ],
            1,
        )
        for stage in ("T0", "T4"):
            stage_report = by_stage[stage]
            self.assertEqual(
                sum(
                    item["factual_action_outcomes"]["action_count"]
                    for item in stage_report["by_frame_type"].values()
                ),
                stage_report["factual_action_outcomes"]["action_count"],
            )
            self.assertEqual(
                sum(
                    item["airtime_efficiency"]["total_measured_airtime_us"]
                    for item in stage_report["by_frame_type"].values()
                ),
                stage_report["airtime_efficiency"]["total_measured_airtime_us"],
            )

    def test_common_complete_mlo_frame_accounting(self) -> None:
        comparison = self.analyze()["mlo_frame_comparisons"][
            "full_copy_cost_0p0080"
        ]["str_mlo"]["pooled_frame_comparison"]
        self.assertEqual(
            comparison["deadline_outcome_quadrants"],
            {
                "both_on_time": 3,
                "adaptive_on_time_mlo_miss": 1,
                "adaptive_miss_mlo_on_time": 1,
                "both_miss": 1,
            },
        )
        self.assertEqual(
            comparison["completion_set_counts"],
            {
                "common_complete": 4,
                "adaptive_only_complete": 1,
                "mlo_only_complete": 1,
                "both_incomplete": 0,
            },
        )
        deltas = comparison["common_complete_adaptive_minus_mlo_latency"]
        self.assertEqual(deltas["count"], 4)
        self.assertEqual(deltas["mean_us"], -2.5)
        self.assertAlmostEqual(deltas["p99_us"], 9.7)
        self.assertEqual(deltas["adaptive_faster_count"], 2)
        self.assertEqual(deltas["equal_count"], 1)
        self.assertEqual(deltas["adaptive_slower_count"], 1)
        fixed_set = comparison["common_complete_latency_statistics_us"]
        self.assertAlmostEqual(fixed_set["adaptive"]["p99"], 147.9)
        self.assertAlmostEqual(fixed_set["mlo"]["p99"], 157.6)
        self.assertAlmostEqual(fixed_set["adaptive_minus_mlo"]["p99"], -9.7)

    def test_rejects_missing_settlement_and_misaligned_offered_frame(self) -> None:
        path = (
            self.fixture.directories["full_copy_cost_0p0080"]
            / "secondary_airtime_settlements.csv"
        )
        with path.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        self.fixture._write_csv(path, rows[:-1])
        with self.assertRaisesRegex(CampaignError, "do not exactly match"):
            self.analyze()

        self.tearDown()
        self.setUp()
        path = self.fixture.directories["str_mlo"] / "frames.csv"
        with path.open(newline="", encoding="utf-8") as source:
            frames = list(csv.DictReader(source))
        frames[0]["packet_count"] = "3"
        self.fixture._write_csv(path, frames)
        with self.assertRaisesRegex(CampaignError, "offered frame differs"):
            self.analyze()

    def test_zero_action_efficiency_uses_json_null_not_nonfinite_values(self) -> None:
        efficiency = _airtime_efficiency([])
        self.assertIsNone(efficiency["mean_measured_airtime_per_action_us"])
        self.assertIsNone(
            efficiency["total_measured_airtime_per_deadline_rescue_us"]
        )
        self.assertEqual(
            set(efficiency["by_factual_outcome"]), set(OUTCOMES)
        )
        json.dumps(efficiency, allow_nan=False)

    def test_rejects_empty_settlement_csv_with_required_schema_preserved(self) -> None:
        path = (
            self.fixture.directories["full_copy_cost_0p0080"]
            / "secondary_airtime_settlements.csv"
        )
        self.fixture._write_csv(path, [], fieldnames=sorted(SETTLEMENT_COLUMNS))
        with self.assertRaisesRegex(CampaignError, "do not exactly match"):
            self.analyze()


if __name__ == "__main__":
    unittest.main()
