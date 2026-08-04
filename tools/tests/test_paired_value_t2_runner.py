#!/usr/bin/env python3
"""Focused runner/config tests for paired-value T2 STR qualification."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import run_experiments
from run_experiments import (
    build_experiment_manifest,
    cli_arguments,
    derive_run_id,
    expand_config,
    load_yaml,
    matrix_sha256,
    project_commit,
    run_identity_document,
    validate_existing_manifest,
    validate_runtime_contract,
    write_experiment_description,
)


QUALIFICATION = (
    ROOT / "experiments/configs/paired_value_t2_str_qualification_v1.yaml"
)
PREFLIGHT = ROOT / "experiments/configs/paired_value_t2_str_preflight_v1.yaml"
RUNTIME_CONTRACT = (
    ROOT / "experiments/model-selection/paired-value-duplication-t2-runtime-v1.json"
)


class PairedValueT2RunnerTest(unittest.TestCase):
    def test_qualification_inherits_neutral_matrix_and_is_exactly_paired(self) -> None:
        raw = yaml.safe_load(QUALIFICATION.read_text(encoding="utf-8"))
        self.assertEqual(raw["extends"], "closed_loop_adaptive_airtime_obss.yaml")
        document = load_yaml(QUALIFICATION)
        specs = expand_config(document)

        self.assertEqual(document["name"], "paired-value-t2-str-qualification-v1")
        self.assertEqual(document["workers"], 64)
        self.assertEqual(
            matrix_sha256(document),
            "72fdd59f515e542870988968cc791fff7f011be5e55708effc5fd7a7a4d299fa",
        )
        self.assertEqual(document["runs"], [1])
        self.assertEqual(document["seeds"], list(range(1201, 1249)))
        self.assertTrue(set(document["seeds"]).isdisjoint(range(1301, 1349)))
        self.assertEqual(len(specs), 96)
        for spec in specs:
            self.assertTrue(cli_arguments(spec["config"], QUALIFICATION.parent))
        expected_arms = {
            ("dual_interface", "paired_value_duplication_t2"),
            ("mlo_str", "fixed_link_0"),
        }
        self.assertEqual(
            {
                (spec["config"]["topology"], spec["config"]["policy"])
                for spec in specs
            },
            expected_arms,
        )
        for seed in range(1201, 1249):
            paired = [spec for spec in specs if spec["seed"] == seed]
            self.assertEqual(len(paired), 2)
            self.assertEqual({spec["run"] for spec in paired}, {1})
            self.assertEqual(
                {
                    (spec["config"]["topology"], spec["config"]["policy"])
                    for spec in paired
                },
                expected_arms,
            )

        policy = next(
            spec["config"]
            for spec in specs
            if spec["config"]["policy"] == "paired_value_duplication_t2"
        )
        self.assertEqual(
            policy["prediction"],
            {
                "prediction_telemetry_enabled": True,
                "prediction_sample_offsets_us": [0, 2000],
                "prediction_history_windows_us": [1000, 5000, 20000],
                "prediction_polling_interval_us": 1000,
                "prediction_polling_report_delay_us": 1000,
                "prediction_event_log_enabled": False,
                "prediction_oracle_features_enabled": False,
                "secondary_airtime_meter_enabled": True,
            },
        )
        self.assertEqual(
            {key: policy["stream"][key] for key in (
                "source",
                "duration",
                "fps",
                "frame_size",
                "gop_length",
                "keyframe_size_multiplier",
                "payload_size",
                "deadline_us",
                "emission_mode",
            )},
            {
                "source": "synthetic",
                "duration": 60,
                "fps": 30,
                "frame_size": 12000,
                "gop_length": 60,
                "keyframe_size_multiplier": 4,
                "payload_size": 1200,
                "deadline_us": 33333,
                "emission_mode": "burst",
            },
        )
        self.assertEqual(
            {key: policy["wifi"][key] for key in (
                "wifi_standard",
                "queue_max_delay_ms",
                "max_ampdu_size",
                "max_amsdu_size",
                "txop_limit_us",
                "rts_cts_threshold",
                "fragmentation_threshold",
                "guard_interval_ns",
                "mlo_sta_max_inflights",
            )},
            {
                "wifi_standard": "eht",
                "queue_max_delay_ms": 500,
                "max_ampdu_size": 65535,
                "max_amsdu_size": 0,
                "txop_limit_us": 0,
                "rts_cts_threshold": 4692480,
                "fragmentation_threshold": 65535,
                "guard_interval_ns": 800,
                "mlo_sta_max_inflights": 1,
            },
        )
        self.assertIn("obss", policy)
        self.assertFalse(any(
            leaf.startswith(("adaptive_airtime_", "selective_duplication_"))
            for leaf, _ in run_experiments._flatten(policy)
        ))

        str_mlo = next(
            spec["config"]
            for spec in specs
            if spec["config"]["topology"] == "mlo_str"
        )
        self.assertEqual(str_mlo["wifi"]["mlo_sta_max_inflights"], 1)
        self.assertNotIn("prediction", str_mlo)

        with tempfile.TemporaryDirectory() as temporary:
            write_experiment_description(document, specs, Path(temporary))
            description = (Path(temporary) / "DESCRIPTION.rst").read_text(encoding="utf-8")
        self.assertIn("Paired-value T2 duplication", description)
        self.assertIn("frozen temporal model reads only primary causal telemetry", description)
        self.assertNotIn("Adaptive actions are disabled", description)

    def test_preflight_inherits_contract_but_cannot_be_qualification(self) -> None:
        raw = yaml.safe_load(PREFLIGHT.read_text(encoding="utf-8"))
        self.assertEqual(raw["extends"], "paired_value_t2_str_qualification_v1.yaml")
        document = load_yaml(PREFLIGHT)
        specs = expand_config(document)
        self.assertEqual(document["name"], "paired-value-t2-str-preflight-v1")
        self.assertEqual(document["workers"], 2)
        self.assertEqual(document["seeds"], [43])
        self.assertEqual(len(specs), 2)
        self.assertTrue(str(document["output_root"]).startswith("/tmp/"))
        self.assertEqual(
            validate_runtime_contract(document),
            validate_runtime_contract(load_yaml(QUALIFICATION)),
        )

    def test_real_runtime_contract_and_five_source_artifacts_validate(self) -> None:
        document = load_yaml(QUALIFICATION)
        identity = validate_runtime_contract(document)
        self.assertIsNotNone(identity)
        assert identity is not None
        self.assertEqual(
            identity["runtime_contract_id"],
            "paired-value-duplication-t2-runtime-v1",
        )
        self.assertEqual(identity["runtime_contract_sha256"], hashlib.sha256(
            RUNTIME_CONTRACT.read_bytes()
        ).hexdigest())
        contract = json.loads(RUNTIME_CONTRACT.read_text(encoding="utf-8"))
        expected_sources = contract["runtime_outputs"]["controller_summary_json"][
            "required_source_artifacts_exact"
        ]
        self.assertEqual(identity["source_artifacts"], expected_sources)
        self.assertEqual(len(identity["source_artifacts"]), 5)

    def test_runtime_contract_rejects_contract_and_source_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = {}
            for index in range(5):
                path = root / f"source-{index}.dat"
                path.write_bytes(f"source-{index}".encode())
                sources[f"source_{index}"] = {
                    "path": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            contract = {
                "runtime_contract_id": "fixture-v1",
                "runtime_outputs": {
                    "controller_summary_json": {
                        "required_source_artifacts_exact": sources,
                    }
                },
            }
            contract_path = root / "contract.json"
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            declaration = {
                "runtime_contract": {
                    "id": "fixture-v1",
                    "path": contract_path.name,
                    "sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
                    "source_artifacts": sources,
                }
            }
            self.assertIsNotNone(validate_runtime_contract(declaration, root))

            contract_drift = copy.deepcopy(declaration)
            contract_drift["runtime_contract"]["sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "contract hash drift"):
                validate_runtime_contract(contract_drift, root)

            (root / "source-3.dat").write_bytes(b"drift")
            with self.assertRaisesRegex(ValueError, "source artifact hash drift"):
                validate_runtime_contract(declaration, root)

    def test_contract_identity_is_in_manifest_resume_and_run_id(self) -> None:
        document = load_yaml(QUALIFICATION)
        contract = validate_runtime_contract(document)
        assert contract is not None
        config = {"topology": "dual_interface", "policy": "paired_value_duplication_t2"}
        legacy_run_id = derive_run_id(config, 1201, 1, "ns3", "project")
        contract_run_id = derive_run_id(config, 1201, 1, "ns3", "project", contract)
        self.assertNotEqual(legacy_run_id, contract_run_id)

        specs = [{
            "run_id": contract_run_id,
            "seed": 1201,
            "run": 1,
            "config": config,
            "completed": True,
        }]
        manifest = build_experiment_manifest(
            document["name"], matrix_sha256(document), QUALIFICATION, "project", specs, contract
        )
        for key in ("runtime_contract_id", "runtime_contract_sha256", "source_artifacts"):
            self.assertEqual(manifest[key], contract[key])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "experiment_manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            validate_existing_manifest(
                path,
                document["name"],
                matrix_sha256(document),
                "project",
                {contract_run_id},
                contract,
            )
            drifted = copy.deepcopy(manifest)
            drifted["runtime_contract_sha256"] = "0" * 64
            path.write_text(json.dumps(drifted), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "different experiment identity"):
                validate_existing_manifest(
                    path,
                    document["name"],
                    matrix_sha256(document),
                    "project",
                    {contract_run_id},
                    contract,
                )
            drifted = copy.deepcopy(manifest)
            drifted["source_artifacts"]["frozen_selection"]["sha256"] = "0" * 64
            path.write_text(json.dumps(drifted), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source_artifacts"):
                validate_existing_manifest(
                    path,
                    document["name"],
                    matrix_sha256(document),
                    "project",
                    {contract_run_id},
                    contract,
                )

    def test_contract_run_id_has_a_frozen_cross_tool_vector(self) -> None:
        config = {
            "policy": "paired_value_duplication_t2",
            "prediction": {"prediction_telemetry_enabled": True},
            "topology": "dual_interface",
        }
        contract = {
            "runtime_contract_id": "fixture-runtime-v1",
            "runtime_contract_sha256": "0123456789abcdef" * 4,
            "source_artifacts": {
                "model": {
                    "path": "model.bin",
                    "sha256": "fedcba9876543210" * 4,
                }
            },
        }
        identity = run_identity_document(
            config,
            1201,
            1,
            "ns3-commit",
            "project-commit",
            contract,
        )
        self.assertEqual(identity["runtime_contract"], contract)
        self.assertEqual(
            set(identity),
            {
                "config",
                "seed",
                "run",
                "ns3_commit",
                "project_commit",
                "prediction_schema_versions",
                "runtime_contract",
            },
        )
        self.assertEqual(
            derive_run_id(
                config,
                1201,
                1,
                "ns3-commit",
                "project-commit",
                contract,
            ),
            "e0b8aa0e0f2204d22748",
        )

    def test_non_contract_manifest_and_run_ids_remain_backward_compatible(self) -> None:
        config = {"a": 1}
        historical_identity = {
            "config": config,
            "seed": 1,
            "run": 2,
            "ns3_commit": "ns3",
            "project_commit": "project",
        }
        expected_run_id = hashlib.sha256(json.dumps(
            historical_identity, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()[:20]
        self.assertEqual(
            derive_run_id(config, 1, 2, "ns3", "project"),
            expected_run_id,
        )
        manifest = build_experiment_manifest(
            "legacy", "matrix", Path("legacy.yaml"), "project", []
        )
        self.assertNotIn("runtime_contract_id", manifest)
        self.assertNotIn("runtime_contract_sha256", manifest)
        self.assertNotIn("source_artifacts", manifest)

    def test_project_clean_check_includes_nonignored_untracked_files(self) -> None:
        with mock.patch(
            "run_experiments.subprocess.check_output",
            return_value="?? new-source.cc\n",
        ) as check:
            with self.assertRaisesRegex(RuntimeError, "untracked"):
                project_commit(ROOT)
        self.assertEqual(
            check.call_args_list[0].args[0],
            ["git", "status", "--porcelain", "--untracked-files=all"],
        )

        with mock.patch(
            "run_experiments.subprocess.check_output",
            side_effect=["", "abc123\n"],
        ) as check:
            self.assertEqual(project_commit(ROOT), "abc123")
        self.assertNotIn("--ignored", check.call_args_list[0].args[0])

    def test_invalid_contract_stops_before_build_or_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "invalid.yaml"
            config.write_text(
                "runtime_contract:\n"
                "  id: paired-value-duplication-t2-runtime-v1\n"
                "  path: experiments/model-selection/paired-value-duplication-t2-runtime-v1.json\n"
                f"  sha256: \"{'0' * 64}\"\n"
                "  source_artifacts: {}\n",
                encoding="utf-8",
            )
            with mock.patch.object(sys, "argv", ["run_experiments.py", str(config)]), mock.patch(
                "run_experiments.subprocess.run"
            ) as run:
                with self.assertRaisesRegex(ValueError, "contract hash drift"):
                    run_experiments.main()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
