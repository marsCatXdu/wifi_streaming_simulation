from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from plot_adaptive_airtime_duplication import (
    _ordered_policy_labels,
    _paired_mlo_diagnostics,
    _unique_pair_index,
    plot_adaptive_airtime,
)


def treatment(
    topology: str,
    policy: str,
    miss_ratio: float,
    p99_us: float,
    airtime_us: float,
) -> dict[str, object]:
    return {
        "seed": 17,
        "run": 1,
        "topology": topology,
        "policy": policy,
        "deadline_miss_ratio": miss_ratio,
        "latency_p99_us": p99_us,
        "target_phy_tx_time_us": airtime_us,
    }


class AdaptiveMloPlottingTest(unittest.TestCase):
    def test_keeps_str_and_emlsr_in_labels_and_paired_series(self) -> None:
        adaptive = treatment(
            "dual_interface", "adaptive_airtime_duplication", 0.01, 8000, 120,
        )
        str_mlo = treatment("mlo_str", "fixed_link_0", 0.02, 10000, 100)
        emlsr_mlo = treatment("mlo_emlsr", "fixed_link_0", 0.03, 12000, 80)
        labels = _ordered_policy_labels({
            "Adaptive airtime": [adaptive],
            "STR MLO": [str_mlo],
            "EMLSR MLO": [emlsr_mlo],
        })
        self.assertEqual(labels, ["Adaptive airtime", "STR MLO", "EMLSR MLO"])

        miss, p99, airtime = _paired_mlo_diagnostics(
            {"adaptive_airtime_duplication": [adaptive]},
            {
                "mlo_str": _unique_pair_index([str_mlo], "STR MLO"),
                "mlo_emlsr": _unique_pair_index([emlsr_mlo], "EMLSR MLO"),
            },
        )
        expected_names = [
            "Adaptive airtime - STR MLO",
            "Adaptive airtime - EMLSR MLO",
        ]
        self.assertEqual([name for name, _ in miss], expected_names)
        self.assertEqual([name for name, _ in p99], expected_names)
        self.assertEqual([name for name, _ in airtime], expected_names)
        self.assertAlmostEqual(miss[0][1][0], -0.01)
        self.assertAlmostEqual(miss[1][1][0], -0.02)
        self.assertEqual(p99[0][1], [-2000.0])
        self.assertEqual(p99[1][1], [-4000.0])
        self.assertAlmostEqual(airtime[0][1][0], 0.2)
        self.assertAlmostEqual(airtime[1][1][0], 0.5)

    def test_adaptive_only_matrix_has_no_mlo_pair_series(self) -> None:
        adaptive = treatment(
            "dual_interface", "adaptive_airtime_duplication", 0.01, 8000, 120,
        )
        miss, p99, airtime = _paired_mlo_diagnostics(
            {"adaptive_airtime_duplication": [adaptive]},
            {},
        )
        self.assertEqual(miss, [])
        self.assertEqual(p99, [])
        self.assertEqual(airtime, [])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "adaptive"
            run_dir.mkdir()
            for name, header in (
                (
                    "adaptive_airtime_decisions.csv",
                    [
                        "frame_id",
                        "decision",
                        "estimated_airtime_us",
                        "sample_stage",
                        "sample_time_ns",
                        "shadow_price",
                        "bucket_balance_us",
                    ],
                ),
                (
                    "secondary_airtime_settlements.csv",
                    ["frame_id", "measured_airtime_us"],
                ),
            ):
                with (run_dir / name).open("w", newline="", encoding="utf-8") as output:
                    csv.writer(output).writerow(header)
            analysis_row = {
                **adaptive,
                "run_id": "adaptive",
                "run_dir": str(run_dir),
                "label": "Adaptive airtime",
                "secondary_airtime_fraction": 0.01,
                "incremental_link0_airtime_fraction": None,
                "target_phy_tx_fraction": 0.12,
                "max_miss_burst": 1,
                "p95_miss_burst": 1.0,
            }
            with mock.patch(
                "plot_adaptive_airtime_duplication.summarize_adaptive_runs",
                return_value=[analysis_row],
            ):
                plot_adaptive_airtime({"runs": []}, root)
            self.assertTrue(
                (root / "plots/adaptive_airtime/deadline_miss_ratio.png").is_file()
            )


if __name__ == "__main__":
    unittest.main()
