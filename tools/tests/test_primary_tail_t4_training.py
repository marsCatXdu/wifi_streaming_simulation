from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

import numpy as np
import yaml

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from prediction.online_replay import FrozenPredictor
from prediction.primary_tail import (
    PRIMARY_TAIL_BUNDLE_SCHEMA_VERSION,
    PrimaryTailT4Bundle,
    read_primary_tail_bundle,
    validate_primary_tail_bundle,
    write_primary_tail_bundle,
)
from train_primary_tail_t4 import (
    combine_probabilities,
    load_config,
    primary_outcome_from_fixed_frame,
    sha256_file,
)


def fixed_frame() -> dict[str, str]:
    return {
        "generation_time_us": "1000",
        "deadline_us": "100",
        "primary_link": "1",
        "duplicated": "0",
        "union_completion_us": "1090",
        "union_latency_us": "90",
        "copy_0_completion_us": "1090",
        "copy_1_completion_us": "",
        "deadline_miss": "0",
        "incomplete": "0",
        "frame_size_bytes": "12000",
        "packet_count": "10",
    }


def dummy_predictor() -> FrozenPredictor:
    return FrozenPredictor(
        pipeline_id="exportable_driver_polling_1ms",
        feature_set="F0+F1-degraded+F2-exportable",
        evidence_role="engineering_meta_selected",
        stage="T4",
        feature_names=("deadline_slack_us",),
        f1_feature_names=(),
        degradation_profile={"profile_id": "polling_1ms"},
        model_name="histogram_gradient_boosting",
        selection_recall=0.0,
        pipeline=("deterministic",),
        calibrator=("deterministic",),
    )


def dummy_bundle() -> PrimaryTailT4Bundle:
    predictor = dummy_predictor()
    return PrimaryTailT4Bundle(
        schema_version=PRIMARY_TAIL_BUNDLE_SCHEMA_VERSION,
        artifact_id="artifact",
        model_id="model",
        dataset_sha256="a" * 64,
        dataset_manifest_sha256="b" * 64,
        dataset_validation_sha256="d" * 64,
        training_config_sha256="c" * 64,
        primary_link=1,
        pipeline_id="exportable_driver_polling_1ms",
        stage="T4",
        heads={"primary_miss": predictor, "completed_tail": copy.deepcopy(predictor)},
        target_ids={"primary_miss": "miss", "completed_tail": "tail"},
        tail_threshold_us=12500,
        miss_weight=1.0,
        tail_weight=0.2,
        score_normalization=1.2,
        score_name="admission_score",
        score_kind="weighted_head_probability_admission_score",
        combiner="weighted_arithmetic_mean",
        evidence_status="engineering_meta_selected_no_independent_ood_claim",
    )


class PrimaryOutcomeTests(unittest.TestCase):
    def test_derives_completed_latency_and_inclusive_deadline(self) -> None:
        row = fixed_frame()
        outcome = primary_outcome_from_fixed_frame(row, 1)
        self.assertEqual(outcome["latency_us"], 90)
        self.assertEqual(outcome["miss"], 0)
        row["union_completion_us"] = "1100"
        row["copy_0_completion_us"] = "1100"
        row["union_latency_us"] = "100"
        self.assertEqual(primary_outcome_from_fixed_frame(row, 1)["miss"], 0)
        row["union_completion_us"] = "1101"
        row["copy_0_completion_us"] = "1101"
        row["union_latency_us"] = "101"
        row["deadline_miss"] = "1"
        self.assertEqual(primary_outcome_from_fixed_frame(row, 1)["miss"], 1)

    def test_rejects_completion_inconsistency(self) -> None:
        row = fixed_frame()
        row["incomplete"] = "1"
        with self.assertRaisesRegex(ValueError, "completion and incomplete"):
            primary_outcome_from_fixed_frame(row, 1)


class ModelScoreTests(unittest.TestCase):
    def test_combines_the_two_probabilities_exactly(self) -> None:
        config = {
            "primary_miss_weight": 1.0,
            "completed_tail_weight": 0.2,
            "normalization": 1.2,
        }
        observed = combine_probabilities(
            np.asarray([0.6, 0.3]), np.asarray([0.3, 0.9]), config
        )
        np.testing.assert_allclose(observed, [0.55, 0.4])


class BundleTests(unittest.TestCase):
    def test_schema_has_no_operating_policy_fields(self) -> None:
        names = {field.name for field in fields(PrimaryTailT4Bundle)}
        self.assertTrue(
            names.isdisjoint(
                {
                    "risk_density_threshold",
                    "deficit_airtime_estimator",
                    "t0_i_guard",
                    "token_bucket",
                }
            )
        )

    def test_round_trip_is_byte_deterministic(self) -> None:
        bundle = dummy_bundle()
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.pkl"
            second = Path(temporary) / "second.pkl"
            write_primary_tail_bundle(first, bundle)
            write_primary_tail_bundle(second, bundle)
            self.assertEqual(sha256_file(first), sha256_file(second))
            self.assertEqual(read_primary_tail_bundle(first).tail_weight, 0.2)

    def test_rejects_inconsistent_combiner(self) -> None:
        bundle = dummy_bundle()
        bundle.score_normalization = 1.0
        with self.assertRaisesRegex(ValueError, "normalization"):
            validate_primary_tail_bundle(bundle)


class FrozenConfigurationTests(unittest.TestCase):
    def test_repository_configuration_is_action_neutral(self) -> None:
        path = TOOLS.parent / "experiments/configs/primary_tail_t4_obss_v1.yaml"
        config = load_config(path)
        split = config["split"]
        self.assertEqual(split["calibration_seeds"], [401, 404, 409, 418, 419, 422])
        self.assertEqual(len(split["deployment_training_seeds"]), 18)
        self.assertEqual(config["dataset"]["validation_status"], "PASS")
        self.assertEqual(config["dataset"]["split_sufficiency_status"], "insufficient_data")
        self.assertEqual(
            config["hybrid"]["score_kind"],
            "weighted_head_probability_admission_score",
        )
        self.assertEqual(config["packetization"]["payload_bytes_per_packet"], 1200)
        self.assertNotIn("risk_density_operating_point", config)
        self.assertNotIn("runtime_safety_token_bucket", config)
        self.assertNotIn("t0_i_guard", config)

    def test_rejects_overlapping_seed_roles(self) -> None:
        path = TOOLS.parent / "experiments/configs/primary_tail_t4_obss_v1.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config["split"]["deployment_training_seeds"][0] = 401
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "changed.yaml"
            changed.write_text(yaml.safe_dump(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "18/6 deployment split"):
                load_config(changed)

    def test_rejects_oracle_enabled_source(self) -> None:
        path = TOOLS.parent / "experiments/configs/primary_tail_t4_obss_v1.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config["target"]["source_oracle_features_enabled"] = True
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "changed.yaml"
            changed.write_text(yaml.safe_dump(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "oracle features disabled"):
                load_config(changed)

    def test_rejects_unknown_top_level_section(self) -> None:
        path = TOOLS.parent / "experiments/configs/primary_tail_t4_obss_v1.yaml"
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        config["controller_options"] = {"cost_mode": "full_copy"}
        with tempfile.TemporaryDirectory() as temporary:
            changed = Path(temporary) / "changed.yaml"
            changed.write_text(yaml.safe_dump(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown top-level keys"):
                load_config(changed)


if __name__ == "__main__":
    unittest.main()
