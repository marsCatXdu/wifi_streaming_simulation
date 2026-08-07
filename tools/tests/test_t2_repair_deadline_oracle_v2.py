#!/usr/bin/env python3
"""Focused tests for corrected T2 deadline-oracle replay."""

from __future__ import annotations

import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import run_t2_repair_deadline_oracle_v2 as runner  # noqa: E402


def _outcome_row(frame_id: int, received: str, missing: str) -> dict[str, str]:
    return {
        "run_id": "baseline",
        "frame_id": str(frame_id),
        "source_packet_count": "2",
        "received_source_packet_indices": received,
        "missing_source_packet_indices": missing,
        "copy_0_source_packet_indices": received,
        "copy_1_source_packet_indices": "",
        "link_0_source_packet_indices": "",
        "link_1_source_packet_indices": received,
        "received_coded_repair_indices": "",
    }


class T2RepairDeadlineOracleV2Test(unittest.TestCase):
    def test_frozen_contract_closes_predecessor_and_diagnosis(self) -> None:
        contract = runner.validate_contract()
        self.assertEqual(contract["experiment_id"], "t2_repair_deadline_oracle_v2")
        self.assertEqual(contract["execution"]["new_simulation_run_count"], 20)

    def test_sidecar_excludes_a_first_packet_after_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "baseline"
            root.mkdir()
            frame_fields = [
                "frame_id",
                "packet_count",
                "generation_time_us",
                "deadline_us",
                "union_first_packet_us",
            ]
            with (root / "frames.csv").open("w", newline="", encoding="utf-8") as out:
                writer = csv.DictWriter(out, fieldnames=frame_fields)
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "frame_id": "0",
                            "packet_count": "2",
                            "generation_time_us": "1000",
                            "deadline_us": "100",
                            "union_first_packet_us": "1050",
                        },
                        {
                            "frame_id": "1",
                            "packet_count": "2",
                            "generation_time_us": "2000",
                            "deadline_us": "100",
                            "union_first_packet_us": "2200",
                        },
                        {
                            "frame_id": "2",
                            "packet_count": "2",
                            "generation_time_us": "3000",
                            "deadline_us": "100",
                            "union_first_packet_us": "",
                        },
                    ]
                )
            outcomes = [
                _outcome_row(0, "0", "1"),
                _outcome_row(1, "0", "1"),
                _outcome_row(2, "", "0;1"),
            ]
            (root / "frame_packet_outcomes.csv").write_bytes(
                runner._serialize_csv(outcomes)
            )
            content, provenance = runner.derive_deadline_sidecar(root)
            rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))
            self.assertEqual(rows[0]["received_source_packet_indices"], "0")
            self.assertEqual(rows[0]["missing_source_packet_indices"], "1")
            self.assertEqual(rows[1]["received_source_packet_indices"], "")
            self.assertEqual(rows[1]["missing_source_packet_indices"], "0;1")
            self.assertEqual(rows[2]["missing_source_packet_indices"], "0;1")
            self.assertEqual(provenance["action_frame_count"], 3)
            self.assertEqual(provenance["repair_packet_count"], 5)
            self.assertEqual(provenance["late_first_arrival_correction_count"], 1)


if __name__ == "__main__":
    unittest.main()
