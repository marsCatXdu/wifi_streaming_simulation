#!/usr/bin/env python3
"""Tests for complete environment-generalization reliability plots."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from plot_environment_generalization_complete_reliability import (  # noqa: E402
    EXPECTED_PLOTS,
    _burst_lengths,
)


class CompleteEnvironmentReliabilityPlotTest(unittest.TestCase):
    def test_burst_lengths_keep_terminal_burst(self) -> None:
        self.assertEqual(
            _burst_lengths([False, True, True, False, True, False, True, True, True]),
            [2, 1, 3],
        )

    def test_plot_contract_includes_requested_distribution_views(self) -> None:
        self.assertIn("completion_latency_cdf", EXPECTED_PLOTS)
        self.assertIn("completion_latency_pdf", EXPECTED_PLOTS)
        self.assertIn("deadline_miss_burst_cdf", EXPECTED_PLOTS)
        self.assertIn("compound_p23_exact", EXPECTED_PLOTS)
        self.assertEqual(len(EXPECTED_PLOTS), len(set(EXPECTED_PLOTS)))


if __name__ == "__main__":
    unittest.main()
