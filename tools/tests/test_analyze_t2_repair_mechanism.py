#!/usr/bin/env python3
"""Focused tests for the T2 mechanism analysis estimands and pairing."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_t2_repair_mechanism as mechanism  # noqa: E402


def observation(scenario: str, seed: int, arm: str) -> dict[str, object]:
    oracle = arm == "oracle_eventual_missing_repair_t2"
    miss_flags = [False] * 8 + ([False, True] if oracle else [True, True])
    censored = [1000.0] * 8 + ([1000.0, 10_000.0] if oracle else [10_000.0] * 2)
    return {
        "family_id": scenario.split("-")[0],
        "scenario_id": scenario,
        "parameter_sample": 17,
        "seed": seed,
        "run": 1,
        "run_id": f"{scenario}-{seed}-{arm}",
        "arm_id": arm,
        "generated_frames": 10,
        "completed_frames": 10,
        "incomplete_frames": 0,
        "late_completed_frames": sum(miss_flags),
        "deadline_misses": sum(miss_flags),
        "deadline_miss_rate": sum(miss_flags) / 10,
        "completed_p99_us": 10_000.0,
        "censored_mean_us": sum(censored) / 10,
        "censored_latencies_us": censored,
        "completed_latencies_us": censored,
        "miss_flags": miss_flags,
        "miss_bursts": mechanism._burst_lengths(miss_flags),
        "airtime_link_0_us": 0.0 if not oracle else 5.0,
        "airtime_link_1_us": 100.0 if not oracle else 90.0,
        "sender_airtime_us": 100.0 if not oracle else 95.0,
        "background_bytes_received": 1000,
        "snapshots": {},
        "action_censored_latencies_us": [2000.0] if oracle else [],
        "actions": 1 if oracle else 0,
        "completed_actions": 1 if oracle else 0,
        "action_packets": 1 if oracle else 0,
        "build_host": "fixture",
    }


class T2RepairMechanismAnalysisTest(unittest.TestCase):
    def test_recovery_source_closes_exact_continuation_identity(self) -> None:
        run_ids = [f"{index:020x}" for index in range(10)]
        manifest = {
            "project_commit": "a" * 40,
            "ns3_upstream_commit": "b" * 40,
            "runs": [{"run_id": run_id} for run_id in run_ids],
            "continuation": {
                "simulation_project_commit": "a" * 40,
                "orchestration_project_commit": mechanism.RECOVERY_ORCHESTRATION_COMMIT,
                "recovery_report": "attempt_recovery.json",
                "expected_executable": {
                    "path": "/build/ns3.48-streaming-experiment-default",
                    "bytes": 123,
                    "sha256": mechanism.SIMULATION_EXECUTABLE_SHA256,
                },
            },
        }
        recovery = {
            "schema_version": 1,
            "simulation_project_commit": "a" * 40,
            "validator_project_commit": mechanism.RECOVERY_ORCHESTRATION_COMMIT,
            "ns3_upstream_commit": "b" * 40,
            "recovered_count": 10,
            "all_recovered_attempts_strictly_validated": True,
            "recovered": [
                {
                    "run_id": run_id,
                    "arm_id": "ideal_systematic_fec_12p5_t2",
                    "state": "promoted",
                    "file_count": 12,
                    "bytes": 1000,
                    "tree_sha256": f"{index:064x}",
                }
                for index, run_id in enumerate(run_ids)
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "attempt_recovery.json").write_text(
                json.dumps(recovery), encoding="utf-8"
            )
            source = mechanism._recovery_source(root, manifest, 0)
            self.assertEqual(source["recovered_count"], 10)
            recovery["recovered"][0]["state"] = "validated_attempt"
            (root / "attempt_recovery.json").write_text(
                json.dumps(recovery), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                mechanism.MechanismAnalysisError, "invalid recovered-attempt row"
            ):
                mechanism._recovery_source(root, manifest, 0)

    def test_burst_lengths_retain_terminal_and_isolated_misses(self) -> None:
        self.assertEqual(
            mechanism._burst_lengths([False, True, False, True, True]),
            [1, 2],
        )

    def test_summary_uses_every_generated_frame_for_censored_latency(self) -> None:
        row = observation("scenario-a", 1, "str_mlo_nmaxinflights_1")
        summary = mechanism._summarize([row])
        self.assertEqual(summary["generated_frames"], 10)
        self.assertEqual(summary["deadline_misses"], 2)
        self.assertAlmostEqual(summary["all_generated_deadline_miss_rate"], 0.2)
        self.assertAlmostEqual(summary["all_generated_censored_mean_us"], 2800.0)
        self.assertEqual(summary["miss_burst_max_frames"], 2)

    def test_paired_bootstrap_recovers_oracle_lower_left_point(self) -> None:
        scenarios = [f"scenario-{index}" for index in range(5)]
        observations = [
            observation(scenario, seed, arm)
            for scenario in scenarios
            for seed in range(4)
            for arm in mechanism.ARM_ORDER
        ]
        grid = mechanism._paired_grid(observations)
        with mock.patch.object(mechanism, "BOOTSTRAP_REPLICATIONS", 100):
            result = mechanism._bootstrap(grid)
        oracle = result["versus_str"]["oracle_eventual_missing_repair_t2"]
        self.assertAlmostEqual(oracle["miss_delta"]["estimate"], -0.1)
        self.assertAlmostEqual(oracle["airtime_ratio"]["estimate"], 0.95)
        self.assertEqual(
            result["oracle_joint_point_success_bootstrap_probability"], 1.0
        )


if __name__ == "__main__":
    unittest.main()
