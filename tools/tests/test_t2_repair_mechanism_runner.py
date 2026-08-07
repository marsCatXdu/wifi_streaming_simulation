#!/usr/bin/env python3
"""Focused tests for the frozen two-stage T2 mechanism campaign."""

from __future__ import annotations

import hashlib
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

from run_experiments import cli_arguments, load_yaml  # noqa: E402
import run_t2_repair_mechanism as runner  # noqa: E402
from run_t2_repair_mechanism import (  # noqa: E402
    ARM_IDS,
    PHASE1_ARMS,
    build_campaign_specs,
    validate_mechanism_contract,
)


CONFIG = ROOT / "experiments/configs/t2_repair_mechanism_v1.yaml"


class T2RepairMechanismRunnerTest(unittest.TestCase):
    def test_contract_closes_exact_representative_scenarios(self) -> None:
        document = load_yaml(CONFIG)
        contract = validate_mechanism_contract(document)
        self.assertEqual(contract["id"], "t2_repair_mechanism_v1")
        self.assertEqual(
            contract["sha256"],
            "bb5f0c805497a741a3f5155573712a615c151da8f6181f6e3e89f6e8d4a94511",
        )
        self.assertEqual(len(document["scenario_instances"]), 5)
        self.assertEqual(
            {row["family_id"] for row in document["scenario_instances"]},
            {"radio_propagation", "obss_intensity", "legacy_coexistence", "compound_shift"},
        )
        self.assertTrue(all(len(row["seeds"]) == 4 for row in document["scenario_instances"]))

    def test_two_shards_preserve_complete_paired_units_and_derive_oracles(self) -> None:
        document = load_yaml(CONFIG)
        shards = [
            build_campaign_specs(document, "project-commit", index, 2)
            for index in range(2)
        ]
        all_units: set[tuple[str, int, int]] = set()
        all_run_ids: set[str] = set()
        for phase1, oracle, pairings in shards:
            self.assertEqual(len(phase1), 50)
            self.assertEqual(len(oracle), 10)
            self.assertEqual(len(pairings), 10)
            units = {
                (spec["scenario"]["scenario_id"], spec["seed"], spec["run"])
                for spec in phase1
            }
            self.assertEqual(len(units), 10)
            self.assertTrue(all_units.isdisjoint(units))
            all_units.update(units)
            for unit in units:
                members = [
                    spec for spec in phase1
                    if (spec["scenario"]["scenario_id"], spec["seed"], spec["run"])
                    == unit
                ]
                self.assertEqual(
                    {
                        (spec["config"]["topology"], spec["config"]["policy"])
                        for spec in members
                    },
                    PHASE1_ARMS,
                )
            for spec in [*phase1, *oracle]:
                self.assertNotIn(spec["run_id"], all_run_ids)
                all_run_ids.add(spec["run_id"])
                self.assertEqual(
                    spec["arm_id"],
                    ARM_IDS[(spec["config"]["topology"], spec["config"]["policy"])],
                )
                self.assertTrue(cli_arguments(
                    spec["config"],
                    Path("/campaign-root") if spec in oracle else CONFIG.parent,
                ))
            for spec in oracle:
                source = spec["config"]["prediction"][
                    "mechanism_oracle_packet_outcome_file"
                ]
                self.assertEqual(
                    source,
                    f"{spec['paired_baseline_run_id']}/frame_packet_outcomes.csv",
                )
        self.assertEqual(len(all_units), 20)
        self.assertEqual(len(all_run_ids), 120)

    def test_invalid_shard_is_rejected(self) -> None:
        document = load_yaml(CONFIG)
        with self.assertRaisesRegex(ValueError, "invalid shard"):
            build_campaign_specs(document, "project-commit", 2, 2)

    def test_recovery_validates_hashes_and_promotes_without_execution(self) -> None:
        run_id = "0123456789abcdefabcd"
        spec = {"run_id": run_id, "arm_id": "ideal_systematic_fec_12p5_t2"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            attempt = root / f".{run_id}.attempt-123"
            attempt.mkdir()
            (attempt / "evidence.txt").write_text("complete\n", encoding="utf-8")
            with mock.patch.object(runner, "validate_run") as validate:
                report = runner.recover_valid_attempts(
                    root,
                    [spec],
                    "a" * 40,
                    "b" * 40,
                    1,
                )
            validate.assert_called_once_with(
                attempt,
                run_id,
                "a" * 40,
                runner.NS3_UPSTREAM_COMMIT,
            )
            self.assertFalse(attempt.exists())
            self.assertTrue((root / run_id / "evidence.txt").is_file())
            self.assertEqual(report["recovered_count"], 1)
            self.assertTrue(report["all_recovered_attempts_strictly_validated"])
            row = report["recovered"][0]
            self.assertEqual(row["state"], "promoted")
            self.assertEqual(row["file_count"], 1)
            persisted = json.loads(
                (root / "attempt_recovery.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted, report)

    def test_recovery_rejects_attempt_outside_frozen_matrix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".fedcba9876543210abcd.attempt-1").mkdir()
            with self.assertRaisesRegex(ValueError, "unknown preserved attempt"):
                runner.recover_valid_attempts(
                    root,
                    [],
                    "a" * 40,
                    "b" * 40,
                    None,
                )

    def test_executable_hash_gate_requires_exact_binary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "ns3.48-streaming-experiment-default"
            executable.write_bytes(b"frozen executable")
            expected = hashlib.sha256(executable.read_bytes()).hexdigest()
            with mock.patch.object(runner, "EXECUTABLE_DIRECTORY", root):
                identity = runner._verify_streaming_executable(expected)
                self.assertEqual(identity["sha256"], expected)
                with self.assertRaisesRegex(ValueError, "binary hash drift"):
                    runner._verify_streaming_executable("0" * 64)

    def test_resume_retains_only_closed_recovery_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIsNone(runner._recovery_report_name(root))
            report = {
                "schema_version": 1,
                "recovered_count": 1,
                "all_recovered_attempts_strictly_validated": True,
                "recovered": [{"run_id": "a" * 20, "state": "promoted"}],
            }
            (root / "attempt_recovery.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
            self.assertEqual(
                runner._recovery_report_name(root), "attempt_recovery.json"
            )
            report["recovered"][0]["state"] = "validated_attempt"
            (root / "attempt_recovery.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "report is not closed"):
                runner._recovery_report_name(root)


if __name__ == "__main__":
    unittest.main()
