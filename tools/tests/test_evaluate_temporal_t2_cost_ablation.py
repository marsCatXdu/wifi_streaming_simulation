#!/usr/bin/env python3
"""Focused tests for the frozen temporal T2 cost-denominator ablation."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TOOLS = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TESTS))

import evaluate_temporal_t2_cost_ablation as ablation  # noqa: E402
import train_temporal_t2_value as trainer  # noqa: E402
from test_train_temporal_t2_value import make_temporal_dataset  # noqa: E402


class CostDenominatorAblationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.dataset_dir = self.root / "temporal"
        make_temporal_dataset(self.dataset_dir)
        self.dataset = trainer.load_temporal_dataset(self.dataset_dir)
        self.model_dir = self.root / "model"
        with mock.patch.object(trainer, "BOOTSTRAP_REPLICATIONS", 20):
            trainer.train_temporal_t2_value(self.dataset, self.model_dir)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_selects_only_cost_free_policy_and_writes_bound_plot(self) -> None:
        # The compact synthetic fixture exercises mechanics, not the real
        # campaign's predeclared 50% scientific gate.
        with (
            mock.patch.object(trainer, "BOOTSTRAP_REPLICATIONS", 20),
            mock.patch.object(trainer, "MIN_RELATIVE_IMPROVEMENT", -1_000_000.0),
            mock.patch.object(
                trainer, "MAX_DR_AIRTIME_US_PER_ELIGIBLE_FRAME", 1_000_000.0
            ),
        ):
            result, records = ablation.evaluate_cost_ablation(
                self.dataset, self.model_dir
            )
        self.assertFalse(result["model_refit"])
        self.assertFalse(result["test_role_used_during_selection"])
        self.assertEqual(result["candidate_count"], 384)
        self.assertEqual(result["cost_free_candidate_count"], 192)
        selected = result["selected_cost_free_calibration_policy"]
        self.assertIn(selected["ranker"], ablation.RAW_RANKERS)
        self.assertFalse(selected["cost_normalized"])
        self.assertEqual(sum(record["selected"] for record in records), 1)
        self.assertEqual(sum(record["source_frozen"] for record in records), 1)

        output = self.root / "ablation"
        ablation.write_outputs(result, records, output)
        for name in ablation.OUTPUT_FILES:
            self.assertTrue((output / name).is_file(), name)
        self.assertGreater((output / ablation.OUTPUT_FIGURE).stat().st_size, 1000)
        manifest = json.loads(
            (output / ablation.OUTPUT_MANIFEST).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["ablation_id"], ablation.ABLATION_ID)
        self.assertEqual(
            set(manifest["artifacts_sha256"]),
            set(ablation.OUTPUT_FILES) - {ablation.OUTPUT_MANIFEST},
        )
        with self.assertRaisesRegex(ablation.CostAblationError, "refusing to overwrite"):
            ablation.write_outputs(result, records, output)

    def test_rejects_model_mutation_before_unpickling(self) -> None:
        model_path = self.model_dir / trainer.OUTPUT_MODEL
        model_path.write_bytes(model_path.read_bytes() + b"mutation")
        with self.assertRaisesRegex(
            ablation.CostAblationError, "source model artifact hash differs"
        ):
            ablation.evaluate_cost_ablation(self.dataset, self.model_dir)


if __name__ == "__main__":
    unittest.main()
