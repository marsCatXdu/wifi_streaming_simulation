#!/usr/bin/env python3
"""Focused tests for the distributional-shadow/V2 comparison."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from compare_distributional_shadow_t2_v2 import _transition  # noqa: E402


class DistributionalShadowT2V2ComparisonTest(unittest.TestCase):
    def test_action_transition_partition(self) -> None:
        self.assertEqual(_transition(True, True), "common_action")
        self.assertEqual(_transition(True, False), "v2_only")
        self.assertEqual(_transition(False, True), "candidate_only")
        self.assertEqual(_transition(False, False), "neither")


if __name__ == "__main__":
    unittest.main()
