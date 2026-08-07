#!/usr/bin/env python3
"""Focused tests for the corrected deadline-repair mechanism analysis."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_t2_repair_deadline_oracle_v2 as analysis  # noqa: E402


class DeadlineOracleV2AnalysisTest(unittest.TestCase):
    def test_transition_decomposition_counts_rescues_and_introduced_misses(
        self,
    ) -> None:
        grid = {
            ("scenario", 1, 1): {
                "single_5ghz_no_redundancy": {
                    "miss_flags": [True, True, False, False]
                },
                analysis.ANALYSIS_REPAIR_ARM: {
                    "miss_flags": [False, True, True, False]
                },
            }
        }
        result = analysis._transition_decomposition(grid)
        self.assertEqual(result["primary_only_deadline_misses"], 2)
        self.assertEqual(result["deadline_repair_deadline_misses"], 2)
        self.assertEqual(result["primary_misses_rescued"], 1)
        self.assertEqual(result["primary_successes_changed_to_miss"], 1)
        self.assertEqual(result["both_miss"], 1)
        self.assertEqual(result["both_success"], 1)
        self.assertEqual(result["net_misses_avoided"], 0)
        self.assertAlmostEqual(result["rescue_fraction_of_primary_misses"], 0.5)

    def test_roots_are_keyed_by_declared_shard_not_argument_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            roots = []
            for shard_index in (1, 0):
                root = Path(directory) / f"shard-{shard_index}"
                root.mkdir()
                (root / "experiment_manifest.json").write_text(
                    json.dumps({"shard": {"index": shard_index, "count": 2}}),
                    encoding="utf-8",
                )
                roots.append(root)
            indexed = analysis._roots_by_shard(roots, "fixture")
            self.assertEqual(indexed[0][0].name, "shard-0")
            self.assertEqual(indexed[1][0].name, "shard-1")

    def test_corrected_arm_label_does_not_claim_exact_oracle(self) -> None:
        self.assertEqual(
            analysis.ARM_LABELS[analysis.ANALYSIS_REPAIR_ARM],
            "Deadline repair T2",
        )


if __name__ == "__main__":
    unittest.main()
