from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from export_prediction_models_v1 import (
    STAGES,
    canonical_sha256,
    emit_model_source,
    export_identity,
)


def model_manifest() -> dict[str, object]:
    provenance = {
        "target_id": "primary_copy_deadline_miss",
        "source_column": "deadline_miss",
        "source_equivalence": "fixed_link_copy_0_equals_union",
        "treatment_free": True,
        "path_id": 1,
        "copy_id": 0,
        "stage": "T0",
    }
    return {
        "model_id": "commodity_polling_1ms_obss_primary_t0_v1",
        "target_provenance": provenance,
        "target_provenance_sha256": canonical_sha256(provenance),
    }


class ExportIdentityTests(unittest.TestCase):
    def test_accepts_manifest_declared_primary_target(self) -> None:
        identity = export_identity(model_manifest())
        self.assertEqual(identity.model_id, "commodity_polling_1ms_obss_primary_t0_v1")
        self.assertEqual(identity.target_id, "primary_copy_deadline_miss")

    def test_rejects_tampered_provenance(self) -> None:
        manifest = model_manifest()
        manifest["target_provenance"]["stage"] = "T1"
        with self.assertRaisesRegex(ValueError, "checksum"):
            export_identity(manifest)

    def test_rejects_union_or_adaptive_target(self) -> None:
        manifest = model_manifest()
        provenance = manifest["target_provenance"]
        provenance["source_equivalence"] = "adaptive_union"
        manifest["target_provenance_sha256"] = canonical_sha256(provenance)
        with self.assertRaisesRegex(ValueError, "treatment-free primary-copy"):
            export_identity(manifest)

    def test_rejects_boolean_copy_identifier(self) -> None:
        manifest = model_manifest()
        provenance = manifest["target_provenance"]
        provenance["copy_id"] = False
        manifest["target_provenance_sha256"] = canonical_sha256(provenance)
        with self.assertRaisesRegex(ValueError, "treatment-free primary-copy"):
            export_identity(manifest)

    def test_generated_source_embeds_manifest_identity(self) -> None:
        identity = export_identity(model_manifest())
        predictor = {
            "transforms": [],
            "nodes": [],
            "trees": [],
            "baseline": 0.0,
            "platt_coefficient": 1.0,
            "platt_intercept": 0.0,
        }
        source = emit_model_source(
            {stage: copy.deepcopy(predictor) for stage in STAGES},
            ("feature",),
            "a" * 64,
            identity,
        )
        self.assertIn(identity.model_id, source)
        self.assertIn(identity.target_id, source)
        self.assertIn(identity.target_provenance_sha256, source)
        self.assertIn("GetTargetProvenanceSha256()", source)


if __name__ == "__main__":
    unittest.main()
