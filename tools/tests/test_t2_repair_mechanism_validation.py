#!/usr/bin/env python3
"""Focused coded-completion validation tests for the mechanism experiment."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from validate_outputs import (  # noqa: E402
    ValidationError,
    _validate_mechanism_frame_completion,
)


def frame(*, packets: int, unique: int, incomplete: bool, mode: str) -> dict[str, str]:
    return {
        "packet_count": str(packets),
        "unique_packets_received": str(unique),
        "incomplete": "1" if incomplete else "0",
        "completion_mode": mode,
    }


class T2RepairMechanismValidationTest(unittest.TestCase):
    def test_coded_symbol_can_complete_missing_source_packet(self) -> None:
        _validate_mechanism_frame_completion(
            frame(packets=10, unique=9, incomplete=False, mode="coded_repair"),
            set(range(9)),
            [10],
            "mechanism_systematic_fec_t2",
        )

    def test_non_fec_frame_still_requires_every_source_packet(self) -> None:
        with self.assertRaisesRegex(
            ValidationError, "completion state differs from innovative packet evidence"
        ):
            _validate_mechanism_frame_completion(
                frame(packets=10, unique=9, incomplete=False, mode="link_1_only"),
                set(range(9)),
                [],
                "mechanism_full_copy_t2",
            )

    def test_sufficient_coded_evidence_cannot_be_marked_incomplete(self) -> None:
        with self.assertRaisesRegex(
            ValidationError, "completion state differs from innovative packet evidence"
        ):
            _validate_mechanism_frame_completion(
                frame(packets=10, unique=9, incomplete=True, mode="none"),
                set(range(9)),
                [10],
                "mechanism_systematic_fec_t2",
            )

    def test_insufficient_coded_evidence_cannot_be_marked_complete(self) -> None:
        with self.assertRaisesRegex(
            ValidationError, "completion state differs from innovative packet evidence"
        ):
            _validate_mechanism_frame_completion(
                frame(packets=10, unique=8, incomplete=False, mode="coded_repair"),
                set(range(8)),
                [10],
                "mechanism_systematic_fec_t2",
            )

    def test_coded_completion_requires_matching_mode(self) -> None:
        with self.assertRaisesRegex(ValidationError, "lacks coded_repair mode"):
            _validate_mechanism_frame_completion(
                frame(packets=10, unique=9, incomplete=False, mode="link_1_only"),
                set(range(9)),
                [10],
                "mechanism_systematic_fec_t2",
            )

    def test_frame_and_packet_outcome_source_counts_must_match(self) -> None:
        with self.assertRaisesRegex(ValidationError, "source count differs"):
            _validate_mechanism_frame_completion(
                frame(packets=10, unique=8, incomplete=True, mode="none"),
                set(range(7)),
                [],
                "mechanism_systematic_fec_t2",
            )


if __name__ == "__main__":
    unittest.main()
