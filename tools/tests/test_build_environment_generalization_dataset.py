#!/usr/bin/env python3
"""Focused tests for the scenario-aware generalization dataset builder."""

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

import build_randomized_temporal_dataset as temporal  # noqa: E402
import run_experiments as runner  # noqa: E402
from build_environment_generalization_dataset import (  # noqa: E402
    DATASET_COLUMNS,
    ENVIRONMENT_FEATURE_COLUMNS,
    FEATURE_COLUMNS,
    NON_FEATURE_COLUMNS,
    EnvironmentDatasetError,
    _generalization_row,
    _run_paths,
    _validated_manifest,
    static_environment_features,
)


CONFIG = ROOT / "experiments/configs/environment_generalization_randomized_preflight_v1.yaml"
PROJECT_COMMIT = "1" * 40
NS3_COMMIT = "2" * 40


def _manifest() -> dict[str, object]:
    document = runner.load_yaml(CONFIG)
    specs = runner.expand_config(document)
    for spec in specs:
        spec["run_id"] = runner.derive_run_id(
            spec["config"],
            spec["seed"],
            spec["run"],
            NS3_COMMIT,
            PROJECT_COMMIT,
            scenario=spec["scenario"],
        )
        spec["completed"] = True
    return runner.build_experiment_manifest(
        document["name"],
        runner.matrix_sha256(document),
        CONFIG,
        PROJECT_COMMIT,
        specs,
    ) | {"ns3_upstream_commit": NS3_COMMIT}


class EnvironmentGeneralizationDatasetTest(unittest.TestCase):
    def test_static_environment_context_uses_only_known_stream_values(self) -> None:
        features = static_environment_features(
            {
                "stream": {
                    "fps": 45,
                    "frame_size_bytes": 8_200,
                    "gop_length": 30,
                    "keyframe_size_multiplier": 2.004,
                    "deadline_us": 22_222,
                }
            }
        )
        self.assertEqual(tuple(features), ENVIRONMENT_FEATURE_COLUMNS)
        self.assertEqual(features["env_stream_fps"], "45")
        self.assertEqual(features["env_interframe_size_bytes"], "8200")
        self.assertEqual(features["env_gop_length"], "30")
        self.assertEqual(features["env_deadline_us"], "22222")

    def test_static_environment_context_fails_closed(self) -> None:
        valid = {
            "fps": 30,
            "frame_size_bytes": 12_000,
            "gop_length": 60,
            "keyframe_size_multiplier": 4.0,
            "deadline_us": 33_333,
        }
        for key, bad in (
            ("fps", 0),
            ("frame_size_bytes", -1),
            ("gop_length", True),
            ("keyframe_size_multiplier", float("nan")),
            ("deadline_us", None),
        ):
            stream = dict(valid)
            stream[key] = bad
            with self.subTest(key=key), self.assertRaises(EnvironmentDatasetError):
                static_environment_features({"stream": stream})

    def test_frozen_matrix_and_manifest_run_ids_join_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "experiment_manifest.json"
            manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
            recorded, expected, manifest, contract, _, _, phase = _validated_manifest(
                CONFIG, manifest_path
            )
        self.assertEqual(set(recorded), set(expected))
        self.assertEqual(len(expected), 6)
        self.assertEqual(manifest["project_commit"], PROJECT_COMMIT)
        self.assertEqual(contract["contract_id"], "environment-generalization-v1")
        self.assertEqual(phase, "preflight")

    def test_manifest_scenario_mutation_is_rejected(self) -> None:
        manifest = _manifest()
        manifest["runs"][0]["scenario"]["family_id"] = "different_family"
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "experiment_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(EnvironmentDatasetError, "identity differs"):
                _validated_manifest(CONFIG, manifest_path)

    def test_scenario_identity_is_non_feature_metadata(self) -> None:
        temporal_row = {column: "0" for column in temporal.DATASET_COLUMNS}
        scenario = {
            "scenario_id": "radio-propagation-preflight-p24",
            "family_id": "radio_propagation",
            "parameter_sample": 24,
        }
        environment = {
            column: str(index + 1)
            for index, column in enumerate(ENVIRONMENT_FEATURE_COLUMNS)
        }
        row = _generalization_row(temporal_row, scenario, environment)
        self.assertEqual(tuple(row), DATASET_COLUMNS)
        self.assertEqual(row["family_id"], "radio_propagation")
        self.assertTrue(set(ENVIRONMENT_FEATURE_COLUMNS) <= set(FEATURE_COLUMNS))
        self.assertFalse({"scenario_id", "family_id"} & set(FEATURE_COLUMNS))
        self.assertTrue({"scenario_id", "family_id"} <= set(NON_FEATURE_COLUMNS))

    def test_raw_directory_set_must_match_manifest_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("run-a", "run-b"):
                path = root / name
                path.mkdir()
                (path / "resolved_config.json").write_text("{}", encoding="utf-8")
            self.assertEqual(set(_run_paths([root], {"run-a", "run-b"})), {"run-a", "run-b"})
            with self.assertRaisesRegex(EnvironmentDatasetError, "unexpected"):
                _run_paths([root], {"run-a"})

    def test_feature_contract_contains_every_frozen_observable(self) -> None:
        contract = json.loads(
            (
                ROOT / "experiments/model-selection/environment-generalization-v1.json"
            ).read_text(encoding="utf-8")
        )
        observable = contract["model_evaluation"]["observable_environment_features"]
        self.assertTrue(set(observable) <= set(FEATURE_COLUMNS))
        self.assertEqual(
            observable[: len(ENVIRONMENT_FEATURE_COLUMNS)],
            list(ENVIRONMENT_FEATURE_COLUMNS),
        )


if __name__ == "__main__":
    unittest.main()
