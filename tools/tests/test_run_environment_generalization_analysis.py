#!/usr/bin/env python3
"""Focused tests for the environment-generalization pipeline entry point."""

from __future__ import annotations

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

import run_environment_generalization_analysis as pipeline  # noqa: E402


class RunEnvironmentGeneralizationAnalysisTest(unittest.TestCase):
    def _collection(self, root: Path, *, status: str = "complete") -> None:
        rows = []
        for index in range(pipeline.EXPECTED_RUN_COUNT):
            run_id = f"{index:020x}"
            (root / run_id).mkdir()
            rows.append(
                {
                    "run_id": run_id,
                    "directory": run_id,
                    "status": status if index == 0 else "complete",
                }
            )
        (root / "experiment_manifest.json").write_text(
            json.dumps({"runs": rows}, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def test_resolves_exact_complete_manifest_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._collection(root)
            manifest, directories = pipeline.resolve_run_directories(root)
            self.assertEqual(manifest, root / "experiment_manifest.json")
            self.assertEqual(len(directories), pipeline.EXPECTED_RUN_COUNT)
            self.assertEqual(directories[0].name, "00000000000000000000")
            self.assertEqual(directories[-1].name, f"{383:020x}")

    def test_rejects_any_incomplete_run_before_source_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._collection(root, status="running")
            with self.assertRaisesRegex(pipeline.PipelineError, "incomplete"):
                pipeline.resolve_run_directories(root)

    def test_stage_manifest_requires_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "artifact_manifest.json"
            path.write_text(
                json.dumps({"hash_algorithm": "sha512"}) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(pipeline.PipelineError, "algorithm"):
                pipeline._stage_manifest(path)

    def test_reusable_stage_rehashes_every_declared_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "value.txt"
            artifact.write_text("original\n", encoding="utf-8")
            manifest = {
                "manifest_schema_version": 1,
                "hash_algorithm": "sha256",
                "artifacts_sha256": {
                    artifact.name: pipeline._sha256(artifact),
                },
            }
            (root / "artifact_manifest.json").write_text(
                json.dumps(manifest) + "\n", encoding="utf-8"
            )
            self.assertEqual(
                pipeline._validate_artifact_directory(root, {artifact.name}),
                manifest,
            )
            artifact.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(pipeline.PipelineError, "artifact differs"):
                pipeline._validate_artifact_directory(root, {artifact.name})

    def test_resume_skips_only_the_validated_dataset_and_lofo_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            (output / pipeline.OUTPUT_DATASET).mkdir()
            (output / pipeline.OUTPUT_LOFO).mkdir()
            config = root / "config.yaml"
            config.write_text("name: fixture\n", encoding="utf-8")
            experiment_manifest = root / "experiment_manifest.json"
            experiment_manifest.write_text("{}\n", encoding="utf-8")
            run_directories = tuple(
                root / f"run-{index}" for index in range(pipeline.EXPECTED_RUN_COUNT)
            )

            def git_value(*args: str) -> str:
                return "fixture-commit" if args == ("rev-parse", "HEAD") else ""

            with mock.patch.object(
                pipeline,
                "resolve_run_directories",
                return_value=(experiment_manifest, run_directories),
            ), mock.patch.object(pipeline, "_git", side_effect=git_value), mock.patch.object(
                pipeline, "_validate_reusable_prefix"
            ) as validate_prefix, mock.patch.object(
                pipeline, "_run"
            ) as run_stage, mock.patch.object(
                pipeline,
                "_stage_manifest",
                return_value={"path": "fixture", "sha256": "0" * 64},
            ), mock.patch.object(
                pipeline, "_sha256", return_value="1" * 64
            ):
                result = pipeline.run_pipeline(
                    root / "runs",
                    output,
                    config,
                    resume_completed_prefix=True,
                )
            validate_prefix.assert_called_once()
            self.assertEqual(run_stage.call_count, 2)
            commands = [call.args[0] for call in run_stage.call_args_list]
            self.assertTrue(
                all(
                    str(pipeline.policy_analysis.__file__) in command
                    or str(pipeline.plotting.__file__) in command
                    for command in commands
                )
            )
            self.assertEqual(
                result["reused_completed_stages"],
                [pipeline.OUTPUT_DATASET, pipeline.OUTPUT_LOFO],
            )


if __name__ == "__main__":
    unittest.main()
