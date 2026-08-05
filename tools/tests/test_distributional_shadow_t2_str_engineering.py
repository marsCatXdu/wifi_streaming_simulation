#!/usr/bin/env python3
"""Focused tests for distributional shadow-T2 STR qualification."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from analyze_distributional_shadow_t2_str_engineering import (  # noqa: E402
    EXPECTED_SEEDS,
    QualificationError,
    _build_report,
    _discover_seed_dirs,
    render_markdown,
)


def observation(
    seed: int,
    arm: str,
    *,
    miss_rate: float,
    p99_us: float,
    airtime_us: float,
    background_mbps: float = 100.0,
) -> dict[str, object]:
    misses = round(1800 * miss_rate)
    return {
        "seed": seed,
        "run": 1,
        "run_id": f"{arm}-{seed}",
        "generated_frame_count": 1800,
        "completed_frame_count": 1800 - misses,
        "deadline_miss_count": misses,
        "all_generated_deadline_miss_rate": miss_rate,
        "completed_frame_p99_us": p99_us,
        "sender_airtime_us": airtime_us,
        "background_bytes_received": round(background_mbps * 60 * 1_000_000 / 8),
        "background_throughput_mbps": background_mbps,
    }


class DistributionalShadowT2StrEngineeringTest(unittest.TestCase):
    @staticmethod
    def campaign(policy_airtimes: list[float]) -> dict[str, list[dict[str, object]]]:
        return {
            "policy": [
                observation(
                    seed,
                    "policy",
                    miss_rate=0.004,
                    p99_us=16_000.0,
                    airtime_us=policy_airtimes[index],
                )
                for index, seed in enumerate(EXPECTED_SEEDS)
            ],
            "str_mlo": [
                observation(
                    seed,
                    "str",
                    miss_rate=0.01,
                    p99_us=19_000.0,
                    airtime_us=100.0,
                )
                for seed in EXPECTED_SEEDS
            ],
        }

    def test_all_gates_and_promotion_pass_for_decisive_fixture(self) -> None:
        report = _build_report(
            self.campaign([110.0] * len(EXPECTED_SEEDS)),
            {"policy": {"available": False}, "str_mlo": {"available": False}},
        )
        self.assertEqual(report["str_qualification"]["status"], "pass")
        self.assertEqual(report["promotion_readiness"]["status"], "pass")
        self.assertEqual(
            report["comparison_against_str"]["relative_deadline_miss_reduction"],
            0.6,
        )
        self.assertEqual(
            report["paired_heterogeneity"]["paired_direction_counts"]
            ["deadline_miss_rate"],
            {"policy_better": 48, "tie": 0, "policy_worse": 0},
        )
        self.assertEqual(
            report["paired_heterogeneity"]
            ["individual_runs_above_sender_airtime_ratio_1_20"],
            0,
        )
        markdown = render_markdown(
            report,
            {
                "actions": 100,
                "acted_primary_misses": 60,
                "primary_copy_deadline_misses": 80,
                "primary_miss_capture_rate": 0.75,
                "rescued_primary_misses": 58,
                "conditional_rescue_efficiency": 58 / 60,
                "final_union_deadline_misses": 40,
            },
        )
        self.assertIn("STR qualification: **pass**", markdown)

    def test_sender_gate_uses_upper_interval_not_only_point_ratio(self) -> None:
        airtimes = [100.0] * 24 + [138.0] * 24
        report = _build_report(
            self.campaign(airtimes),
            {"policy": {"available": False}, "str_mlo": {"available": False}},
        )
        ratio = report["comparison_against_str"]["sender_airtime_ratio"]
        self.assertLess(ratio["estimate"], 1.20)
        self.assertGreater(ratio["ci95_high"], 1.20)
        self.assertEqual(report["resource_target_against_str"]["status"], "fail")
        self.assertEqual(report["str_qualification"]["status"], "fail")
        self.assertEqual(
            report["paired_heterogeneity"]
            ["individual_runs_above_sender_airtime_ratio_1_20"],
            24,
        )

    def test_seed_discovery_is_exact_and_rejects_reserved_extra(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for seed in EXPECTED_SEEDS:
                (root / f"seed-{seed}").mkdir()
            discovered = _discover_seed_dirs(root, "policy")
            self.assertEqual(tuple(sorted(discovered)), EXPECTED_SEEDS)
            (root / "seed-1301").mkdir()
            with self.assertRaisesRegex(QualificationError, "seed set differs"):
                _discover_seed_dirs(root, "policy")


if __name__ == "__main__":
    unittest.main()
