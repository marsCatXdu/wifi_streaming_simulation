#!/usr/bin/env python3
"""Tests for held-out environment qualification plots."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from analyze_environment_generalization_qualification import (  # noqa: E402
    ANALYSIS_CONTRACT_PATH,
    ARM_IDS,
    EXPECTED_PLOTS,
)
from plot_environment_generalization_qualification import (  # noqa: E402
    DISTRIBUTIONAL_V2_COMPARISON,
    PLOT_MANIFEST_NAME,
    STR_COMPARISONS,
    write_plots,
)


class EnvironmentGeneralizationQualificationPlotTest(unittest.TestCase):
    def _interval(self, estimate: float, spread: float) -> dict[str, float]:
        return {
            "estimate": estimate,
            "ci95_low": estimate - spread,
            "ci95_high": estimate + spread,
        }

    def _report(self) -> dict[str, object]:
        treatments = {}
        values = {
            "str_mlo_nmaxinflights_1": (0.10, 20_000.0, 300_000.0, 50.0),
            "score_aware_t2_v2": (0.05, 16_000.0, 345_000.0, 49.8),
            "distributional_shadow_t2": (0.075, 18_000.0, 330_000.0, 49.9),
        }
        metrics = (
            "all_generated_deadline_miss_rate",
            "completed_frame_hf7_p99_us",
            "sender_airtime_us",
            "background_throughput_mbps",
        )
        for arm, row in values.items():
            treatments[arm] = {
                "metrics": {
                    metric: self._interval(value, abs(value) * 0.02)
                    for metric, value in zip(metrics, row, strict=True)
                }
            }
        comparisons = {}
        for arm, comparison in STR_COMPARISONS.items():
            candidate = values[arm]
            baseline = values["str_mlo_nmaxinflights_1"]
            comparisons[comparison] = {
                "aggregate": {
                    "deadline_miss_delta": self._interval(candidate[0] - baseline[0], 0.005),
                    "completed_p99_delta_us": self._interval(candidate[1] - baseline[1], 300),
                    "sender_airtime_ratio": self._interval(candidate[2] / baseline[2], 0.01),
                    "background_throughput_loss": self._interval(
                        1 - candidate[3] / baseline[3], 0.001
                    ),
                }
            }
        comparisons[DISTRIBUTIONAL_V2_COMPARISON] = {"aggregate": {}}
        return {
            "source_closure": {
                "analysis_contract": {
                    "path": str(ANALYSIS_CONTRACT_PATH.relative_to(ROOT)),
                    "sha256": hashlib.sha256(ANALYSIS_CONTRACT_PATH.read_bytes()).hexdigest(),
                }
            },
            "treatments": treatments,
            "comparisons": comparisons,
        }

    def _tables(self) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        families = tuple(
            (
                "radio_propagation",
                "obss_intensity",
                "obss_geometry_mac",
                "video_workload",
                "legacy_coexistence",
                "compound_shift",
            )
        )
        comparisons = [*STR_COMPARISONS.values(), DISTRIBUTIONAL_V2_COMPARISON]
        family_rows = []
        scenario_rows = []
        for comparison_index, comparison in enumerate(comparisons):
            for family_index, family in enumerate(families):
                miss = -0.01 + 0.001 * family_index + 0.002 * comparison_index
                p99 = -1000 + 100 * family_index + 200 * comparison_index
                family_rows.append(
                    {
                        "comparison_id": comparison,
                        "family_id": family,
                        "deadline_miss_delta": str(miss),
                        "deadline_miss_delta__ci95_low": str(miss - 0.002),
                        "deadline_miss_delta__ci95_high": str(miss + 0.002),
                        "completed_p99_delta_us": str(p99),
                        "completed_p99_delta_us__ci95_low": str(p99 - 200),
                        "completed_p99_delta_us__ci95_high": str(p99 + 200),
                        "sender_airtime_ratio": str(1.1 + 0.01 * family_index),
                        "background_throughput_loss": str(0.005 + 0.001 * family_index),
                    }
                )
                for scenario_index in range(8):
                    scenario_rows.append(
                        {
                            "comparison_id": comparison,
                            "family_id": family,
                            "scenario_id": f"{family}-{scenario_index}",
                            "deadline_miss_delta": str(
                                miss + 0.0002 * scenario_index
                            ),
                            "completed_p99_delta_us": str(
                                p99 + 20 * scenario_index
                            ),
                        }
                    )
        return family_rows, scenario_rows

    def _historical(self) -> dict[str, dict[str, object]]:
        return {
            arm: {
                "latencies_us": [
                    5000.0 + 100 * index + 500 * arm_index
                    for index in range(300)
                ],
                "bursts": [1, 1, 2, 3, 5, 8],
                "generated": 1000,
                "completed": 950 - 10 * arm_index,
                "misses": 100 - 20 * arm_index,
                "run_count": 192,
            }
            for arm_index, arm in enumerate(ARM_IDS)
        }

    def test_renders_every_predeclared_figure_in_both_formats(self) -> None:
        report = self._report()
        family_rows, scenario_rows = self._tables()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "plots"
            result = write_plots(
                output,
                report,
                family_rows,
                scenario_rows,
                self._historical(),
                {"path": "/tmp/analysis.json", "bytes": 1, "sha256": "0" * 64},
            )
            self.assertEqual(result, output.resolve())
            manifest = json.loads((output / PLOT_MANIFEST_NAME).read_text())
            expected = {
                f"{name}.{suffix}"
                for name in EXPECTED_PLOTS
                for suffix in ("png", "pdf")
            }
            self.assertEqual(set(manifest["artifacts"]), expected)
            self.assertEqual(len(expected), 22)
            for name, identity in manifest["artifacts"].items():
                path = output / name
                self.assertGreater(identity["bytes"], 0)
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                    identity["sha256"],
                )


if __name__ == "__main__":
    unittest.main()
