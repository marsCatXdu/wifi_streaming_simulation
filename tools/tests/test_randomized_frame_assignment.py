from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from randomized_frame_assignment import (
    ALGORITHM_ID,
    ExplorationArm,
    assign_frame,
)


class GoldenVectorTests(unittest.TestCase):
    def test_matches_cpp_splitmix64_v1_vectors(self) -> None:
        golden_cases = (
            (0x0, 0x0, 0x0, 0x0, 0x2130748AAAC80268, "0x1.0983a45556400p-3", ExplorationArm.FULL_COPY_T2, 0.2),
            (0x1, 0x0, 0x0, 0x0, 0xE28195DDD9EE4956, "0x1.c5032bbbb3dc9p-1", ExplorationArm.CONTROL, 0.3),
            (0x0, 0x1, 0x0, 0x0, 0xD1C0270687984B37, "0x1.a3804e0d0f309p-1", ExplorationArm.CONTROL, 0.3),
            (0x0, 0x0, 0x1, 0x0, 0xD9EEF7F073D37C42, "0x1.b3ddefe0e7a6fp-1", ExplorationArm.CONTROL, 0.3),
            (0x0, 0x0, 0x0, 0x1, 0x2A4F111B3BE57715, "0x1.527888d9df2b8p-3", ExplorationArm.FULL_COPY_T2, 0.2),
            (0xDECAFBAD12345678, 123, 17, 999999, 0xC55C5329FE091C3B, "0x1.8ab8a653fc123p-1", ExplorationArm.CONTROL, 0.3),
            ((1 << 64) - 1, (1 << 64) - 1, (1 << 64) - 1, (1 << 64) - 1, 0x9C666618C8F279D7, "0x1.38cccc3191e4fp-1", ExplorationArm.FULL_COPY_T4, 0.5),
        )

        self.assertEqual(ALGORITHM_ID, "splitmix64_v1")
        for salt, seed, run, frame_id, raw_draw, unit_hex, arm, propensity in golden_cases:
            with self.subTest(salt=salt, seed=seed, run=run, frame_id=frame_id):
                assignment = assign_frame(salt, seed, run, frame_id, 0.2, 0.5)
                self.assertEqual(assignment.raw_draw, raw_draw)
                self.assertEqual(assignment.unit_draw, float.fromhex(unit_hex))
                self.assertEqual(assignment.arm, arm)
                self.assertAlmostEqual(assignment.arm_probability, propensity)


class BoundaryTests(unittest.TestCase):
    def test_half_open_arm_boundaries_match_cpp_contract(self) -> None:
        baseline = assign_frame(0, 0, 0, 0, 0.0, 0.0)
        self.assertEqual(baseline.arm, ExplorationArm.CONTROL)
        self.assertGreaterEqual(baseline.unit_draw, 0.0)
        self.assertLess(baseline.unit_draw, 1.0)

        at_t2_upper = assign_frame(0, 0, 0, 0, baseline.unit_draw, 0.5)
        self.assertEqual(at_t2_upper.arm, ExplorationArm.FULL_COPY_T4)
        below_t2_upper = assign_frame(
            0, 0, 0, 0, math.nextafter(baseline.unit_draw, 1.0), 0.5
        )
        self.assertEqual(below_t2_upper.arm, ExplorationArm.FULL_COPY_T2)

        at_t4_upper = assign_frame(0, 0, 0, 0, 0.0, baseline.unit_draw)
        self.assertEqual(at_t4_upper.arm, ExplorationArm.CONTROL)
        below_t4_upper = assign_frame(
            0, 0, 0, 0, 0.0, math.nextafter(baseline.unit_draw, 1.0)
        )
        self.assertEqual(below_t4_upper.arm, ExplorationArm.FULL_COPY_T4)

        self.assertEqual(
            assign_frame(0, 0, 0, 0, 1.0, 0.0).arm,
            ExplorationArm.FULL_COPY_T2,
        )
        self.assertEqual(
            assign_frame(0, 0, 0, 0, 0.0, 1.0).arm,
            ExplorationArm.FULL_COPY_T4,
        )

    def test_rejects_invalid_probabilities(self) -> None:
        invalid_probabilities = (
            (-0.1, 0.0),
            (0.0, -0.1),
            (0.8, 0.3),
            (1.1, 0.0),
            (0.0, 1.1),
            (math.inf, 0.0),
            (0.0, math.inf),
            (math.nan, 0.0),
            (0.0, math.nan),
            (0.0, -math.inf),
            (True, 0.0),
            (0.0, False),
        )
        for t2_probability, t4_probability in invalid_probabilities:
            with self.subTest(t2=t2_probability, t4=t4_probability):
                with self.assertRaises(ValueError):
                    assign_frame(0, 0, 0, 0, t2_probability, t4_probability)

    def test_rejects_values_outside_uint64_contract(self) -> None:
        for value in (-1, 1 << 64, True, 1.0):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    assign_frame(value, 0, 0, 0, 0.2, 0.5)


class DeterminismTests(unittest.TestCase):
    def test_repeated_assignment_is_identical_and_bounded(self) -> None:
        first = assign_frame(0x123456789ABCDEF0, 9, 41, 73, 0.2, 0.5)
        repeated = assign_frame(0x123456789ABCDEF0, 9, 41, 73, 0.2, 0.5)
        self.assertEqual(first, repeated)

        for frame_id in range(1000):
            assignment = assign_frame(
                0x123456789ABCDEF0, 9, 41, frame_id, 0.2, 0.5
            )
            self.assertGreaterEqual(assignment.unit_draw, 0.0)
            self.assertLess(assignment.unit_draw, 1.0)


if __name__ == "__main__":
    unittest.main()
