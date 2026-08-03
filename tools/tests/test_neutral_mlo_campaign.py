from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from analyze_neutral_mlo_campaign import (
    CampaignError,
    Thresholds,
    analyze_campaign,
    confidence_interval,
    render_markdown,
)


TREATMENTS = {
    "adaptive": ("dual_interface", "adaptive_airtime_duplication"),
    "str_mlo": ("mlo_str", "fixed_link_0"),
    "emlsr_mlo": ("mlo_emlsr", "fixed_link_0"),
}


def _write_link_airtime(path: Path, by_link_us: tuple[float, float]) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=["link_id", "phy_tx_time_us"])
        writer.writeheader()
        for link_id, airtime_us in enumerate(by_link_us):
            writer.writerow({"link_id": link_id, "phy_tx_time_us": airtime_us})


def _config(
    run_id: str,
    seed: int,
    run_number: int,
    treatment: str,
) -> dict[str, Any]:
    topology, policy = TREATMENTS[treatment]
    result: dict[str, Any] = {
        "run_id": run_id,
        "seed": seed,
        "run": run_number,
        "topology": topology,
        "policy": policy,
        "duration_s": 10,
        "warmup_s": 1,
        "measurement_start_s": 1,
        "measurement_stop_s": 11,
        "stream": {
            "source": "synthetic",
            "fps": 30,
            "deadline_us": 33333,
        },
        "propagation": {
            "model": "log_distance_nakagami",
            "random_stream_base": 5000,
        },
        "background": {
            "profile": "none",
            "correlation": {"mode": "independent"},
            "obss": {
                "profile": "mixed4x4",
                "stations_per_bss": 4,
                "bsses": [{"seed_marker": seed, "run_marker": run_number}],
            },
        },
        "wifi": {
            "standard": "802.11be",
            "station_manager": "ConstantRateWifiManager",
            "data_mode": "EhtMcs5",
            "data_modes_per_link": ["EhtMcs5", "EhtMcs5"],
            "channel_settings": [
                "{1, 20, BAND_2_4GHZ, 0}",
                "{36, 20, BAND_5GHZ, 0}",
            ],
            "frequency_ranges": [
                "WIFI_SPECTRUM_2_4_GHZ",
                "WIFI_SPECTRUM_5_GHZ",
            ],
            "treatment_mode": treatment,
        },
    }
    if treatment == "adaptive":
        result["adaptiveAirtimeDuplication"] = {
            "model_id": "frozen-primary-risk-t0",
            "budget_fraction": 0.0095,
        }
    return result


def _write_campaign(
    root: Path,
    pairs: list[tuple[int, int]],
    include_ignored: bool = True,
    treatments: tuple[str, ...] = ("adaptive", "str_mlo", "emlsr_mlo"),
    matrix_id: str = "synthetic-matrix",
) -> tuple[Path, dict[str, Any]]:
    root.mkdir()
    rows: list[dict[str, Any]] = []
    for treatment in treatments:
        topology, policy = TREATMENTS[treatment]
        for index, (seed, run_number) in enumerate(pairs):
            run_id = f"{treatment}-{seed}-{run_number}"
            run_dir = root / run_id
            run_dir.mkdir()
            config = _config(run_id, seed, run_number, treatment)
            (run_dir / "resolved_config.json").write_text(
                json.dumps(config), encoding="utf-8"
            )
            (run_dir / "build_info.json").write_text(json.dumps({
                "ns3_version": "ns-3.48",
                "ns3_upstream_commit": "upstream-commit",
                "project_git_commit": "project-commit",
                "host": f"host-for-{matrix_id}",
            }), encoding="utf-8")
            if treatment == "adaptive":
                miss = 0.010 + index * 0.001
                p99_us = 8_000 + index * 100
                airtime = (80_000 + index * 100, 35_000 + index * 100)
                background = 99.0 + index * 0.1
                meter = {
                    "tagged_secondary_tx_airtime_us": 80_000.0,
                    "tagged_secondary_tx_airtime_fraction": 0.008,
                    "measurement_duration_us": 10_000_000.0,
                    "maximum_budget_debt_us": 2_000.0 + index,
                    "estimated_action_airtime_us": 72_000.0,
                    "actual_to_estimated_airtime_ratio": 1.1,
                    "forced_reservation_settlements": 0,
                    "budget_fraction": 0.0095,
                    "initial_bucket_capacity_us": 9_500.0,
                    "finite_run_budget_us": 104_500.0,
                    "budget_excess_us": 0.0,
                }
                (run_dir / "secondary_airtime_summary.json").write_text(
                    json.dumps(meter), encoding="utf-8"
                )
            elif treatment == "str_mlo":
                miss = 0.030 + index * 0.001
                p99_us = 17_000 + index * 100
                airtime = (45_000 + index * 100, 55_000 + index * 100)
                background = 100.0 + index * 0.1
            else:
                miss = 0.040 + index * 0.001
                p99_us = 18_000 + index * 100
                airtime = (50_000 + index * 100, 50_000 + index * 100)
                background = 100.0 + index * 0.1
            _write_link_airtime(run_dir / "link_intervals.csv", airtime)
            rows.append({
                "run_id": run_id,
                "run_dir": f"/stale/workstation/path/{run_id}",
                "seed": seed,
                "run": run_number,
                "topology": topology,
                "policy": policy,
                "config": config,
                "deadline_miss_ratio": miss,
                "latency_p99_us": p99_us,
                "background_throughput_mbps": background,
            })
    if include_ignored:
        rows.append({
            "run_id": "ignored-fixed-5ghz",
            "seed": pairs[0][0],
            "run": pairs[0][1],
            "topology": "dual_interface",
            "policy": "fixed_link_1",
        })
    aggregate = {
        "schema_version": 1,
        "independent_sample_unit": "run",
        "matrix_id": matrix_id,
        "runs": rows,
    }
    path = root / "aggregate.json"
    path.write_text(json.dumps(aggregate), encoding="utf-8")
    return path, aggregate


class NeutralMloCampaignTest(unittest.TestCase):
    def test_reports_complete_paired_campaign_and_separate_verdicts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, _ = _write_campaign(
                Path(temporary) / "runs",
                [(31, 1), (32, 1), (33, 1), (34, 1)],
            )
            report = analyze_campaign(path)

        self.assertEqual(report["paired_unit_count"], 4)
        self.assertEqual(report["ignored_nonheadline_run_count"], 1)
        self.assertEqual(report["independent_sample_unit"], ["seed", "run"])
        comparison = report["comparisons"]["str_mlo"]
        self.assertAlmostEqual(
            comparison["deadline_miss_ratio"]
            ["paired_difference_adaptive_minus_mlo"]["mean"],
            -0.02,
        )
        self.assertAlmostEqual(
            comparison["completed_frame_p99_us"]
            ["paired_difference_adaptive_minus_mlo"]["mean"],
            -9000.0,
        )
        self.assertAlmostEqual(
            comparison["summed_sender_phy_tx_airtime"]
            ["paired_relative_increase"]["ratio_of_paired_means"],
            0.15 / 1.003,
            places=7,
        )
        self.assertEqual(
            comparison["summed_sender_phy_tx_airtime"]
            ["paired_us_difference_adaptive_minus_mlo"]["mean"],
            15_000.0,
        )
        self.assertEqual(
            set(comparison["summed_sender_phy_tx_airtime"]["per_band"]),
            {"2.4GHz", "5GHz"},
        )
        self.assertEqual(comparison["defeat_status"], "pass")
        self.assertEqual(comparison["ideal_pareto_status"], "pass")
        self.assertEqual(
            report["comparisons"]["emlsr_mlo"]["ideal_pareto_status"], "pass"
        )
        self.assertEqual(
            report["overall_status"]["meets_ideal_pareto_goal_against_both"], "pass"
        )
        diagnostics = report["adaptive_secondary_budget_diagnostics"]
        self.assertEqual(diagnostics["maximum_observed_budget_debt_us"], 2003.0)
        self.assertEqual(diagnostics["maximum_observed_budget_excess_us"], 0.0)
        self.assertIn("Adaptive versus STR MLO", render_markdown(report))
        self.assertIn("Per-band sender-airtime change", render_markdown(report))

    def test_merges_adaptive_and_baselines_from_different_result_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            adaptive_root = parent / "adaptive-point-seven"
            baseline_root = parent / "baseline-point-five"
            adaptive_root.mkdir()
            baseline_root.mkdir()
            _write_campaign(
                adaptive_root / "runs",
                [(35, 1), (36, 1), (37, 1)],
                include_ignored=False,
                treatments=("adaptive",),
                matrix_id="adaptive-only",
            )
            _write_campaign(
                baseline_root / "runs",
                [(35, 1), (36, 1), (37, 1)],
                include_ignored=False,
                treatments=("str_mlo", "emlsr_mlo"),
                matrix_id="reused-baselines",
            )
            report = analyze_campaign([adaptive_root, baseline_root])
        self.assertEqual(report["paired_unit_count"], 3)
        self.assertEqual(len(report["source_aggregates"]), 2)
        self.assertEqual(
            report["campaign_checks"]["build_identity"]["project_git_commit"],
            "project-commit",
        )

    def test_seed_and_run_together_define_independent_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, _ = _write_campaign(
                Path(temporary) / "runs",
                [(41, 1), (41, 2)],
                include_ignored=False,
            )
            report = analyze_campaign(path)
        self.assertEqual(report["paired_unit_count"], 2)
        self.assertEqual(
            report["paired_units"],
            [{"seed": 41, "run": 1}, {"seed": 41, "run": 2}],
        )

    def test_one_pair_cannot_claim_confidence_interval_victory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, _ = _write_campaign(
                Path(temporary) / "runs", [(51, 1)], include_ignored=False
            )
            report = analyze_campaign(path)
        comparison = report["comparisons"]["str_mlo"]
        self.assertEqual(
            comparison["criteria"]["deadline_miss_ratio_ci_below_zero"]["status"],
            "insufficient_data",
        )
        self.assertEqual(comparison["defeat_status"], "insufficient_data")

    def test_rejects_incomplete_three_treatment_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, aggregate = _write_campaign(
                Path(temporary) / "runs", [(61, 1), (62, 1)], include_ignored=False
            )
            aggregate["runs"] = [
                row for row in aggregate["runs"]
                if not (row["topology"] == "mlo_emlsr" and row["seed"] == 62)
            ]
            path.write_text(json.dumps(aggregate), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "incomplete paired treatment matrix"):
                analyze_campaign(path)

    def test_rejects_duplicate_treatment_unit_across_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            first, _ = _write_campaign(
                parent / "first", [(65, 1), (66, 1)], include_ignored=False
            )
            second, _ = _write_campaign(
                parent / "second",
                [(65, 1), (66, 1)],
                include_ignored=False,
                treatments=("adaptive",),
            )
            with self.assertRaisesRegex(CampaignError, "duplicate Adaptive dual-interface"):
                analyze_campaign([first, second])

    def test_rejects_inconsistent_commit_across_merged_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            adaptive, _ = _write_campaign(
                parent / "adaptive",
                [(67, 1), (68, 1)],
                include_ignored=False,
                treatments=("adaptive",),
            )
            baselines, aggregate = _write_campaign(
                parent / "baselines",
                [(67, 1), (68, 1)],
                include_ignored=False,
                treatments=("str_mlo", "emlsr_mlo"),
            )
            run_id = aggregate["runs"][0]["run_id"]
            build_path = baselines.parent / run_id / "build_info.json"
            build = json.loads(build_path.read_text(encoding="utf-8"))
            build["project_git_commit"] = "different-commit"
            build_path.write_text(json.dumps(build), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "inconsistent project/ns-3 commits"):
                analyze_campaign([adaptive, baselines])

    def test_rejects_unmatched_environment_realization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runs"
            path, aggregate = _write_campaign(root, [(71, 1), (72, 1)], include_ignored=False)
            target = next(
                row for row in aggregate["runs"]
                if row["topology"] == "mlo_emlsr" and row["seed"] == 72
            )
            target["config"]["background"]["obss"]["bsses"] = [{"different": True}]
            resolved = root / target["run_id"] / "resolved_config.json"
            resolved.write_text(json.dumps(target["config"]), encoding="utf-8")
            path.write_text(json.dumps(aggregate), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "environment and target-radio conditions"):
                analyze_campaign(path)

    def test_rejects_unmatched_target_radio_conditions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runs"
            path, aggregate = _write_campaign(root, [(73, 1), (74, 1)], include_ignored=False)
            target = next(
                row for row in aggregate["runs"]
                if row["topology"] == "mlo_emlsr" and row["seed"] == 74
            )
            target["config"]["wifi"]["data_mode"] = "EhtMcs6"
            resolved = root / target["run_id"] / "resolved_config.json"
            resolved.write_text(json.dumps(target["config"]), encoding="utf-8")
            path.write_text(json.dumps(aggregate), encoding="utf-8")
            with self.assertRaisesRegex(CampaignError, "environment and target-radio conditions"):
                analyze_campaign(path)

    def test_budget_threshold_is_independent_of_outcome_thresholds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "runs"
            path, _ = _write_campaign(root, [(81, 1), (82, 1)], include_ignored=False)
            for seed in (81, 82):
                meter_path = root / f"adaptive-{seed}-1" / "secondary_airtime_summary.json"
                meter = json.loads(meter_path.read_text(encoding="utf-8"))
                meter["budget_excess_us"] = 2.0
                meter_path.write_text(json.dumps(meter), encoding="utf-8")
            report = analyze_campaign(path, Thresholds(max_budget_excess_us=1.0))
        self.assertEqual(
            report["adaptive_secondary_budget_criteria"]
            ["finite_run_budget_conformance"]["status"],
            "fail",
        )
        self.assertEqual(report["comparisons"]["str_mlo"]["defeat_status"], "fail")

    def test_optional_debt_limit_participates_in_controller_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path, _ = _write_campaign(
                Path(temporary) / "runs", [(83, 1), (84, 1)], include_ignored=False
            )
            report = analyze_campaign(path, Thresholds(max_budget_debt_us=1_000.0))
        self.assertEqual(
            report["adaptive_secondary_budget_criteria"]["maximum_budget_debt"]["status"],
            "fail",
        )
        self.assertEqual(report["adaptive_controller_integrity_status"], "fail")
        self.assertEqual(report["comparisons"]["str_mlo"]["defeat_status"], "fail")

    def test_confidence_interval_uses_student_t(self) -> None:
        interval = confidence_interval([0.0, 2.0])
        self.assertAlmostEqual(interval["mean"], 1.0)
        self.assertAlmostEqual(interval["ci95_low"], 1.0 - 12.706204736, places=6)
        self.assertAlmostEqual(interval["ci95_high"], 1.0 + 12.706204736, places=6)


if __name__ == "__main__":
    unittest.main()
