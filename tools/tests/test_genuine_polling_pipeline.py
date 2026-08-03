from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import run_genuine_polling_pipeline as pipeline


class GenuinePollingPipelineTests(unittest.TestCase):
    def test_analysis_seed_comes_from_authoritative_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "analysis.yaml"
            config.write_text("analysis_seed: 20260725\n")
            self.assertEqual(pipeline.read_analysis_seed(config), 20260725)

    def test_phase_checkpoint_skips_completed_action(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "state.json"
            state = {"schema_version": 1, "completed_phases": []}
            calls: list[str] = []
            with patch.object(pipeline, "STATE", state_path):
                pipeline.run_phase(state, "phase", lambda: calls.append("run"))
                pipeline.run_phase(state, "phase", lambda: calls.append("rerun"))
            self.assertEqual(calls, ["run"])
            self.assertEqual(
                json.loads(state_path.read_text())["completed_phases"], ["phase"]
            )

    def test_failed_attempt_logs_are_preserved_by_round(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            attempt = root / ".run.attempt-7"
            attempt.mkdir()
            (attempt / "stdout.log").write_text("failure\n")
            pipeline.preserve_failed_attempts(root, 2)
            preserved = root / "failed_attempts/round_2/run.attempt-7/stdout.log"
            self.assertEqual(preserved.read_text(), "failure\n")

    def test_closed_matrix_rejects_legacy_predictor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "run"
            run.mkdir()
            (run / "resolved_config.json").write_text(json.dumps({
                "policy": "selective_duplication",
                "selectiveDuplication": {
                    "model_id": "commodity_polling_1ms_legacy_frame_delayed_v1"
                },
            }))
            with patch.object(pipeline, "run_matrix"):
                with self.assertRaisesRegex(ValueError, "legacy predictor"):
                    pipeline.run_closed_matrix("phase", Path("config"), root, 1)

    def test_closed_matrix_accepts_primary_t0_predictor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "run"
            run.mkdir()
            (run / "resolved_config.json").write_text(json.dumps({
                "policy": "selective_duplication",
                "selectiveDuplication": {
                    "model_id": "commodity_polling_1ms_obss_primary_t0_v1"
                },
            }))
            with patch.object(pipeline, "run_matrix"):
                pipeline.run_closed_matrix("phase", Path("config"), root, 1)

    def test_evaluation_preserves_incomplete_output_and_uses_frozen_seed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evaluation = root / "evaluation"
            evaluation.mkdir()
            (evaluation / "plots").mkdir()
            commands: list[list[str]] = []
            with (
                patch.object(pipeline, "RESULTS", root),
                patch.object(
                    pipeline,
                    "command",
                    lambda _phase, arguments: commands.append(arguments),
                ),
            ):
                pipeline.run_evaluation(root / "dataset", evaluation, 20260725)
            self.assertFalse(evaluation.exists())
            self.assertTrue(
                (root / "failed_phase_outputs/evaluation_attempt_1/plots").is_dir()
            )
            seed_index = commands[0].index("--seed")
            self.assertEqual(commands[0][seed_index + 1], "20260725")


if __name__ == "__main__":
    unittest.main()
