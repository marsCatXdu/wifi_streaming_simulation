#!/usr/bin/env python3
"""Tests for the frozen temporal T2 C++ model exporter."""

from __future__ import annotations

import copy
import json
import math
import pickle
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import export_temporal_t2_value_model_v1 as exporter


ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = ROOT / "results/randomized_full_copy_exploration_collection_v1"
ARTIFACT = CAMPAIGN / "temporal_t2_primary_only_two_objective_v1"
DATASET = CAMPAIGN / "temporal_dataset"
SELECTION = (
    ROOT
    / "experiments/model-selection/temporal-t2-primary-only-two-objective-v1.json"
)
MODEL_SOURCE = (
    ROOT / "contrib/wifi-streaming/model/temporal-t2-value-model-data-v1.cc"
)
GOLDEN_HEADER = (
    ROOT / "contrib/wifi-streaming/test/temporal-t2-value-model-golden-v1.h"
)


class TemporalT2ExporterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = exporter.validate_source(ARTIFACT, SELECTION, DATASET)
        cls.payload = exporter.export_payload(cls.source)
        cls.digests = exporter._component_digests(cls.source, cls.payload)

    def _copy_inputs(self, root: Path) -> tuple[Path, Path]:
        artifact = root / "artifact"
        artifact.mkdir()
        for name in (
            "artifact_manifest.json",
            "temporal_t2_value_models.pkl",
            "temporal_t2_value_policy_candidates.csv",
            "temporal_t2_value_training_metrics.json",
        ):
            shutil.copy2(ARTIFACT / name, artifact / name)
        selection = root / "selection.json"
        shutil.copy2(SELECTION, selection)
        return artifact, selection

    def test_extracts_only_selected_runtime_components(self) -> None:
        self.assertEqual(len(self.source.feature_names), 246)
        self.assertTrue(all("secondary" not in name for name in self.source.feature_names))
        self.assertEqual(len(self.payload["primary_bad12"]["trees"]), 64)
        self.assertEqual(len(self.payload["primary_bad12"]["nodes"]), 832)
        self.assertEqual(len(self.payload["treated_bad12"]["trees"]), 64)
        self.assertEqual(len(self.payload["treated_bad12"]["nodes"]), 832)
        self.assertEqual(len(self.payload["cost"]["coefficients"]), 262)
        self.assertNotIn("deadline_miss", json.dumps(self.payload))
        self.assertNotIn("completed_late", json.dumps(self.payload))
        self.assertEqual(
            self.digests["source_model"],
            "dff01b0f8319320489709c4039d97011f35439aa92adedbe167fe61b9de7bcb8",
        )
        self.assertEqual(
            self.digests["source_metrics"],
            "35929f0638b03ec79f2f3967dd947265c3d73b7fa51f487299cc1d96a555a014",
        )
        self.assertEqual(
            self.digests["source_manifest"],
            "b3af02b647c7671a631f3d43ebece75781989889358c845335d4003610a8208f",
        )

    def test_model_source_is_deterministic_and_checkable(self) -> None:
        first = exporter.emit_model_source(self.source, self.payload, self.digests)
        second = exporter.emit_model_source(self.source, self.payload, self.digests)
        self.assertEqual(first, second)
        self.assertIn("legacy_bad12_value_per_cost", first)
        self.assertNotIn("/tmp/temporal-t2-two-objective-final", first)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "generated.cc"
            exporter.write_or_check(path, first, False)
            exporter.write_or_check(path, first, True)
            path.write_text(first + "// stale\n", encoding="utf-8")
            with self.assertRaisesRegex(
                exporter.TemporalT2ExportError, "generated file is stale"
            ):
                exporter.write_or_check(path, first, True)

    def test_checked_in_generated_files_are_current(self) -> None:
        model_source = exporter.emit_model_source(
            self.source, self.payload, self.digests
        )
        self.assertEqual(
            MODEL_SOURCE.read_text(encoding="utf-8"),
            model_source,
        )
        cases = exporter.golden_inputs(self.source)
        golden_header = exporter.emit_golden_header(
            self.source, self.digests, cases
        )
        self.assertEqual(
            GOLDEN_HEADER.read_text(encoding="utf-8"),
            golden_header,
        )

    def test_log_airtime_diagnostic_is_raw_ridge_prediction(self) -> None:
        values = np.zeros(246, dtype=float)
        values[67] = 1.0
        result = exporter.sklearn_result(self.source, values)
        quantized = exporter._quantized_vector(values).reshape(1, -1)
        raw = float(self.source.cost_model.predict(quantized)[0])
        self.assertEqual(result["predicted_log_airtime"], raw)
        self.assertNotEqual(
            result["predicted_log_airtime"],
            raw + math.log(self.source.smearing_factor),
        )

    def test_rejects_model_hash_tampering_before_unpickle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact, selection = self._copy_inputs(Path(temporary))
            model = artifact / "temporal_t2_value_models.pkl"
            model.write_bytes(model.read_bytes() + b"tamper")
            with self.assertRaisesRegex(
                exporter.TemporalT2ExportError, "artifact hash differs"
            ):
                exporter._validate_copied_source_for_testing(
                    artifact, selection, DATASET
                )

    def test_rejects_manifest_selected_contract_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact, selection = self._copy_inputs(Path(temporary))
            path = artifact / "artifact_manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["selected_policy_contract"]["ranker"] = (
                "deadline_value_per_cost"
            )
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                exporter.TemporalT2ExportError, "canonical source manifest identity differs"
            ):
                exporter._validate_copied_source_for_testing(
                    artifact, selection, DATASET
                )

    def test_rejects_frozen_selection_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact, selection = self._copy_inputs(Path(temporary))
            value = json.loads(selection.read_text(encoding="utf-8"))
            value["primary_policy"]["threshold_comparator"] = "score > threshold"
            selection.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(
                exporter.TemporalT2ExportError,
                "frozen selection file hash differs",
            ):
                exporter._validate_copied_source_for_testing(
                    artifact, selection, DATASET
                )

    def test_rejects_rehashed_bundle_policy_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact, selection = self._copy_inputs(Path(temporary))
            model_path = artifact / "temporal_t2_value_models.pkl"
            with model_path.open("rb") as source_file:
                bundle = pickle.load(source_file)
            bundle = copy.deepcopy(bundle)
            bundle["selected_policy"]["ranker"] = "deadline_value_per_cost"
            with model_path.open("wb") as destination:
                pickle.dump(bundle, destination, protocol=4)
            manifest_path = artifact / "artifact_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts_sha256"][model_path.name] = exporter.sha256_file(
                model_path
            )
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                exporter.TemporalT2ExportError, "canonical source manifest identity differs"
            ):
                exporter._validate_copied_source_for_testing(
                    artifact, selection, DATASET
                )

    def test_rejects_pristine_copy_outside_canonical_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact, selection = self._copy_inputs(Path(temporary))
            with self.assertRaisesRegex(
                exporter.TemporalT2ExportError,
                "repository canonical closure",
            ):
                exporter.validate_source(artifact, selection, DATASET)

    def test_rejects_invalid_tree_structure(self) -> None:
        pipeline = copy.deepcopy(self.source.primary_head)
        pipeline.named_steps["classifier"]._predictors[0][0].nodes[0][
            "feature_idx"
        ] = 65535
        with self.assertRaisesRegex(
            exporter.TemporalT2ExportError, "tree feature escapes input"
        ):
            exporter._hgb_data(pipeline, 246, "tampered primary bad12")


if __name__ == "__main__":
    unittest.main()
