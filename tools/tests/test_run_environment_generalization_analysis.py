#!/usr/bin/env python3
"""Focused tests for the environment-generalization pipeline entry point."""

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


if __name__ == "__main__":
    unittest.main()
