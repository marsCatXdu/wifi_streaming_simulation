#!/usr/bin/env python3
"""Focused tests for the scenario-15 paired WMM analysis and figures."""

from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_scenario15_wmm_comparison as analysis  # noqa: E402
import plot_scenario15_wmm_comparison as plotting  # noqa: E402


def _interval(estimate: float, spread: float = 0.01) -> dict[str, float]:
    return {
        "estimate": estimate,
        "ci95_low": estimate - spread,
        "ci95_high": estimate + spread,
    }


class Scenario15WmmAnalysisTest(unittest.TestCase):
    def test_frozen_contract_matches_analyzer(self) -> None:
        contract = analysis._verify_contract()
        self.assertEqual(contract["implementation_commit"], analysis.EXPECTED_IMPLEMENTATION_COMMIT)

    def test_hf7_and_burst_reconstruction(self) -> None:
        self.assertEqual(analysis._hf7([0, 10], 0.25), 2.5)
        self.assertEqual(analysis._hf7_ordered([0, 10, 20], 0.75), 15.0)
        self.assertEqual(
            analysis._burst_lengths([False, True, True, False, True, False]),
            [2, 1],
        )
        with self.assertRaises(analysis.AnalysisError):
            analysis._hf7([], 0.5)

    def test_paired_comparison_preserves_seed_pairing(self) -> None:
        left = []
        right = []
        for seed in analysis.EXPECTED_SEEDS:
            base = float(seed - analysis.EXPECTED_SEEDS[0] + 1)
            common = {
                "seed": seed,
                "completed_frame_p99_us": 10_000 + base,
                "background_throughput_mbps": 20 + base / 100,
            }
            left.append(
                {
                    **common,
                    "all_generated_deadline_miss_rate": base / 10_000,
                    "sender_airtime_us": 120,
                }
            )
            right.append(
                {
                    **common,
                    "all_generated_deadline_miss_rate": base / 10_000 + 0.001,
                    "sender_airtime_us": 100,
                }
            )
        result = analysis._comparison(left, right, analysis._bootstrap_indexes())
        self.assertAlmostEqual(result["deadline_miss_rate_delta"]["estimate"], -0.001)
        self.assertAlmostEqual(result["completed_frame_p99_delta_us"]["estimate"], 0.0)
        self.assertAlmostEqual(result["sender_airtime_ratio"]["estimate"], 1.2)
        self.assertEqual(result["deadline_miss_direction_counts"], {"better": 48, "tie": 0, "worse": 0})

    def test_plot_series_has_normalized_run_band_densities(self) -> None:
        rows = [
            {
                "seed": 1,
                "completed_latencies_us": [1000.0, 2000.0, 3000.0],
                "deadline_censored_latencies_us": [1000.0, 2000.0, 3000.0, 33_333.0],
                "deadline_miss_bursts": [1, 2],
            },
            {
                "seed": 2,
                "completed_latencies_us": [1500.0, 2500.0, 3500.0],
                "deadline_censored_latencies_us": [1500.0, 2500.0, 3500.0, 33_333.0],
                "deadline_miss_bursts": [3],
            },
        ]
        result = analysis._plot_series(rows)
        density = result["completed_pdf"]
        area = sum(density["pooled_density_per_ms"]) * density["bin_width_us"] / 1000
        self.assertTrue(math.isclose(area, 1.0))
        self.assertEqual(result["completed_frame_count"], 6)
        self.assertEqual(result["deadline_miss_burst_cdf"]["lengths"], [1, 2, 3])

    def test_full_figure_suite_renders_from_compact_inputs(self) -> None:
        series = {}
        treatments: dict[str, dict[str, object]] = {}
        for mode_index, mode in enumerate(analysis.WMM_PROFILES):
            treatments[mode] = {}
            for arm_index, arm in enumerate(analysis.ARM_IDENTITIES):
                rows = [
                    {
                        "seed": 1,
                        "completed_latencies_us": [10_000.0, 12_000.0, 15_000.0, 34_000.0],
                        "deadline_censored_latencies_us": [10_000.0, 12_000.0, 15_000.0, 33_333.0],
                        "deadline_miss_bursts": [1],
                    },
                    {
                        "seed": 2,
                        "completed_latencies_us": [11_000.0, 13_000.0, 16_000.0, 35_000.0],
                        "deadline_censored_latencies_us": [11_000.0, 13_000.0, 16_000.0, 33_333.0],
                        "deadline_miss_bursts": [2],
                    },
                ]
                cell = analysis._plot_series(rows)
                series[f"{mode}:{arm}"] = {
                    "wmm_mode": mode,
                    "arm": arm,
                    "label": analysis.ARM_LABELS[arm],
                    **cell,
                }
                miss = 0.01 + 0.001 * arm_index - 0.001 * mode_index
                treatments[mode][arm] = {
                    "completed_frame_count": cell["completed_frame_count"],
                    "all_generated_deadline_miss_rate": _interval(miss, 0.0005),
                    "completed_frame_p99_us": _interval(18_000 + 500 * arm_index, 100),
                    "sender_airtime_us": _interval(200_000 + 10_000 * arm_index, 1_000),
                }
        comparison = {
            "deadline_miss_rate_delta": _interval(-0.001, 0.0005),
            "completed_frame_p99_delta_us": _interval(-500, 100),
            "sender_airtime_ratio": _interval(1.05, 0.01),
            "background_throughput_loss": _interval(0.0, 0.01),
        }
        report = {
            "schema_version": 1,
            "analysis": analysis.ANALYSIS_ID,
            "campaign_checks": {"strictly_validated_run_count": 288},
            "treatments": treatments,
            "wmm_effects": {arm: comparison for arm in analysis.ARM_IDENTITIES},
            "within_mode_comparisons": {
                mode: {
                    "score_aware_t2_v2_minus_str_mlo": comparison,
                    "distributional_shadow_t2_minus_str_mlo": comparison,
                }
                for mode in analysis.WMM_PROFILES
            },
        }
        plot_data = {
            "schema_version": 1,
            "analysis": "scenario15_wmm_plot_data_v1",
            "series": series,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report_path = root / "report.json"
            plot_path = root / "plot.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            plot_path.write_text(json.dumps(plot_data), encoding="utf-8")
            manifest = plotting.generate(report_path, plot_path, root / "figures")
            self.assertEqual(manifest["figure_count"], 11)
            self.assertEqual(len(manifest["files"]), 22)
            self.assertTrue(all(item["size_bytes"] > 1000 for item in manifest["files"].values()))


if __name__ == "__main__":
    unittest.main()
