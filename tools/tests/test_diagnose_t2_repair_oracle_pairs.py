#!/usr/bin/env python3
"""Focused tests for T2 oracle pair-closure diagnosis."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import diagnose_t2_repair_oracle_pairs as diagnostic  # noqa: E402


class T2RepairOraclePairDiagnosticTest(unittest.TestCase):
    def test_late_first_arrival_requires_all_packets(self) -> None:
        frame = {
            "generation_time_us": "1000000",
            "deadline_us": "33333",
            "union_first_packet_us": "1040000",
        }
        outcome = {
            "source_packet_count": "3",
            "link_1_source_packet_indices": "0",
        }
        missing, late = diagnostic._deadline_missing_set(frame, outcome)
        self.assertTrue(late)
        self.assertEqual(missing, {0, 1, 2})

    def test_timely_first_arrival_uses_deadline_finalized_set(self) -> None:
        frame = {
            "generation_time_us": "1000000",
            "deadline_us": "33333",
            "union_first_packet_us": "1002500",
        }
        outcome = {
            "source_packet_count": "3",
            "link_1_source_packet_indices": "0;2",
        }
        missing, late = diagnostic._deadline_missing_set(frame, outcome)
        self.assertFalse(late)
        self.assertEqual(missing, {1})

    def test_snapshot_comparison_ignores_only_run_id(self) -> None:
        baseline = {
            (1, 0): {"run_id": "baseline", "frame_id": "3", "queue": "8"}
        }
        oracle = {
            (1, 0): {"run_id": "oracle", "frame_id": "3", "queue": "8"}
        }
        self.assertEqual(diagnostic._snapshot_differences(baseline, oracle), [])
        oracle[(1, 0)]["queue"] = "9"
        self.assertEqual(
            diagnostic._snapshot_differences(baseline, oracle), ["queue"]
        )


if __name__ == "__main__":
    unittest.main()
