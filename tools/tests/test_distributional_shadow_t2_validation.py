#!/usr/bin/env python3
"""Focused fail-closed tests for distributional shadow-T2 validation."""

from __future__ import annotations

import copy
import hashlib
import json
import lzma
import math
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from analyze_primary_tail_t4_campaign import (  # noqa: E402
    DECLARED_NEUTRAL_ENVIRONMENT,
    DECLARED_TOPOLOGY_WIFI,
)
from validate_outputs import (  # noqa: E402
    DISTRIBUTIONAL_SHADOW_T2_CANONICAL_RESERVATION_US,
    DISTRIBUTIONAL_SHADOW_T2_CONFIG,
    DISTRIBUTIONAL_SHADOW_T2_POLICY,
    PAIRED_TEMPORAL_T2_CANONICAL_FRAME_PROFILE,
    PAIRED_TEMPORAL_T2_GENERALIZATION_FRAME_PROFILE,
    PAIRED_VALUE_T2_METER_CONFIG,
    PAIRED_VALUE_T2_PREDICTION_CONFIG,
    ValidationError,
    _distributional_shadow_t2_descriptor_cost_matches_profile,
    _distributional_shadow_t2_distribution,
    _distributional_shadow_t2_expected_unsettled,
    _distributional_shadow_t2_model_replay_context,
    _distributional_shadow_t2_model_result,
    _distributional_shadow_t2_opportunity_cost,
    _distributional_shadow_t2_xz_json,
    _paired_meter_close,
    _validate_distributional_shadow_t2_config,
)


class DistributionalShadowT2ValidationTest(unittest.TestCase):
    @staticmethod
    def resolved_config() -> dict[str, object]:
        config = copy.deepcopy(DECLARED_NEUTRAL_ENVIRONMENT)
        wifi = config.pop("shared_target_wifi")
        wifi.update(copy.deepcopy(DECLARED_TOPOLOGY_WIFI["dual_interface"]))
        config.update({
            "topology": "dual_interface",
            "policy": DISTRIBUTIONAL_SHADOW_T2_POLICY,
            "environment": "unchanged_neutral_mixed4x4",
            "wifi": wifi,
            "distributionalShadowDuplicationT2": copy.deepcopy(
                DISTRIBUTIONAL_SHADOW_T2_CONFIG
            ),
            "predictionTelemetry": copy.deepcopy(
                PAIRED_VALUE_T2_PREDICTION_CONFIG
            ),
            "secondaryAirtimeMeter": copy.deepcopy(PAIRED_VALUE_T2_METER_CONFIG),
        })
        config.pop("randomizedIntervention", None)
        return config

    def test_accepts_frozen_config_and_rejects_environment_or_policy_drift(self) -> None:
        config = self.resolved_config()
        _validate_distributional_shadow_t2_config(config)

        changed_environment = copy.deepcopy(config)
        changed_environment["wifi"]["queue_max_packets"] = 501
        with self.assertRaisesRegex(ValidationError, "shared target Wi-Fi differs"):
            _validate_distributional_shadow_t2_config(changed_environment)

        changed_policy = copy.deepcopy(config)
        changed_policy["distributionalShadowDuplicationT2"][
            "cost_safety_factor"
        ] = 1.250001
        with self.assertRaisesRegex(ValidationError, "controller object exists|differs"):
            _validate_distributional_shadow_t2_config(changed_policy)

    def test_accepts_only_exact_adaptive_mcs_ablation(self) -> None:
        config = self.resolved_config()
        config["wifi"].update({
            "mcs_mode": "adaptive",
            "station_manager": "MinstrelHtWifiManager",
            "data_mode": "manager_selected",
            "control_mode": "manager_selected,manager_selected",
            "data_modes_per_link": ["manager_selected", "manager_selected"],
            "adaptive_mcs_update_interval_ms": 50,
            "adaptive_mcs_use_latest_amendment_only": True,
            "adaptive_mcs_random_stream_base": 900000,
            "adaptive_mcs_random_stream_count": 8,
        })
        _validate_distributional_shadow_t2_config(config)

        config["wifi"]["adaptive_mcs_update_interval_ms"] = 51
        with self.assertRaisesRegex(ValidationError, "MCS provenance differs"):
            _validate_distributional_shadow_t2_config(config)

    def test_accepts_bounded_generalization_profile_and_rejects_bad_cadence(self) -> None:
        config = self.resolved_config()
        config["pairedTemporalT2FrameProfile"] = "environment_generalization_v1"
        config["environment"] = "held_out_environment_generalization_v1"
        config["stream"].update({
            "fps": 60,
            "frame_size_bytes": 8200,
            "gop_length": 90,
            "keyframe_size_multiplier": 3.613,
            "deadline_us": 16667,
        })
        config["propagation"]["station_distance_m"] = 12.425
        config["background"]["obss"]["dl_max_rate_mbps"] = 13.1084
        _validate_distributional_shadow_t2_config(config)

        bad_deadline = copy.deepcopy(config)
        bad_deadline["stream"]["deadline_us"] = 33333
        with self.assertRaisesRegex(ValidationError, "outside the frozen domain"):
            _validate_distributional_shadow_t2_config(bad_deadline)

    def test_generalization_uses_dynamic_descriptor_cost(self) -> None:
        canonical_nominal = (
            DISTRIBUTIONAL_SHADOW_T2_CANONICAL_RESERVATION_US / 1.25
        )
        self.assertTrue(
            _distributional_shadow_t2_descriptor_cost_matches_profile(
                canonical_nominal,
                DISTRIBUTIONAL_SHADOW_T2_CANONICAL_RESERVATION_US,
                canonical_nominal,
                PAIRED_TEMPORAL_T2_CANONICAL_FRAME_PROFILE,
            )
        )
        dynamic_nominal = 1000.0
        dynamic_reserved = 1.25 * dynamic_nominal
        self.assertFalse(
            _distributional_shadow_t2_descriptor_cost_matches_profile(
                dynamic_nominal,
                dynamic_reserved,
                dynamic_nominal,
                PAIRED_TEMPORAL_T2_CANONICAL_FRAME_PROFILE,
            )
        )
        self.assertTrue(
            _distributional_shadow_t2_descriptor_cost_matches_profile(
                dynamic_nominal,
                dynamic_reserved,
                dynamic_nominal,
                PAIRED_TEMPORAL_T2_GENERALIZATION_FRAME_PROFILE,
            )
        )
        self.assertFalse(
            _distributional_shadow_t2_descriptor_cost_matches_profile(
                dynamic_nominal,
                dynamic_reserved + 1.0,
                dynamic_nominal,
                PAIRED_TEMPORAL_T2_GENERALIZATION_FRAME_PROFILE,
            )
        )
        self.assertFalse(
            _distributional_shadow_t2_descriptor_cost_matches_profile(
                dynamic_nominal,
                dynamic_reserved,
                dynamic_nominal,
                "unknown_profile",
            )
        )

    def test_portable_model_has_expected_golden_results(self) -> None:
        context = _distributional_shadow_t2_model_replay_context()
        self.assertEqual(len(context["feature_names"]), 308)
        np = context["numpy"]
        cases = (
            (
                np.zeros(308),
                0.014917826997087436,
                0.027630278964484445,
                [
                    3.971159997487642,
                    0.24224003982463202,
                    -0.7682272765818775,
                    -1.4409980100782542,
                    -2.943162086358887,
                    -0.17542479903138114,
                ],
            ),
            (
                np.full(308, np.nan),
                0.0037000996284167753,
                0.0211339986366037,
                [
                    3.5753605435511617,
                    0.6901555318611595,
                    -0.6204368864247369,
                    -2.2113660305094838,
                    -3.1791093900007237,
                    -1.9214396248468746,
                ],
            ),
        )
        for features, expected_reward, expected_tail_gain, expected_logits in cases:
            result = _distributional_shadow_t2_model_result(features)
            self.assertAlmostEqual(
                result["deadline_rescue_reward"], expected_reward, delta=5e-15
            )
            self.assertAlmostEqual(
                result["tail18_cdf_gain"], expected_tail_gain, delta=5e-15
            )
            for actual, expected in zip(result["control"]["logits"], expected_logits):
                self.assertAlmostEqual(actual, expected, delta=5e-15)

    def test_opportunity_price_uses_exact_affordability_boundary(self) -> None:
        context = _distributional_shadow_t2_model_replay_context()
        curve = context["reference"]["bins"][0]["congestion_tertile"][0]
        first_density = float(curve["density_descending"][0])
        training_runs = int(curve["training_run_count"])
        canonical_cost = 1983.760667318285

        self.assertTrue(math.isinf(_distributional_shadow_t2_opportunity_cost(0, 0, 0.0)))
        first_affordable = canonical_cost / training_runs
        for _ in range(4):
            if not math.isinf(
                _distributional_shadow_t2_opportunity_cost(0, 0, first_affordable)
            ):
                break
            first_affordable = math.nextafter(first_affordable, math.inf)
        self.assertEqual(
            _distributional_shadow_t2_opportunity_cost(0, 0, first_affordable),
            first_density,
        )
        self.assertTrue(
            math.isinf(
                _distributional_shadow_t2_opportunity_cost(
                    0, 0, math.nextafter(first_affordable, -math.inf)
                )
            )
        )

    def test_action_dirty_diagnostic_is_scored_only(self) -> None:
        action_times = {10: 100, 11: 200}
        settlement_times = {10: 250, 11: 400}
        self.assertEqual(
            _distributional_shadow_t2_expected_unsettled(
                225, True, action_times, settlement_times
            ),
            2,
        )
        self.assertEqual(
            _distributional_shadow_t2_expected_unsettled(
                250, True, action_times, settlement_times
            ),
            2,
        )
        self.assertEqual(
            _distributional_shadow_t2_expected_unsettled(
                251, True, action_times, settlement_times
            ),
            1,
        )
        self.assertEqual(
            _distributional_shadow_t2_expected_unsettled(
                225, False, action_times, settlement_times
            ),
            0,
        )

    def test_nearest_rank_summary_and_meter_serialization(self) -> None:
        self.assertEqual(
            _distributional_shadow_t2_distribution([4.0, 1.0, 3.0, 2.0]),
            {
                "finite_count": 4,
                "minimum": 1.0,
                "p50": 2.0,
                "p90": 4.0,
                "p99": 4.0,
                "maximum": 4.0,
                "mean": 2.5,
            },
        )
        exact = 132639.28007619435
        serialized = float(format(exact, ".12g"))
        self.assertTrue(_paired_meter_close(serialized, exact))
        self.assertFalse(_paired_meter_close(math.nextafter(serialized, math.inf), exact))

    def test_compressed_replay_artifact_rejects_byte_drift(self) -> None:
        encoded = json.dumps({"value": 1}, separators=(",", ":")).encode("utf-8")
        compressed = lzma.compress(encoded, preset=9)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "fixture.json.xz"
            path.write_bytes(compressed)
            value = _distributional_shadow_t2_xz_json(
                path,
                hashlib.sha256(compressed).hexdigest(),
                hashlib.sha256(encoded).hexdigest(),
                "fixture",
            )
            self.assertEqual(value, {"value": 1})
            path.write_bytes(compressed[:-1] + bytes([compressed[-1] ^ 1]))
            with self.assertRaisesRegex(ValidationError, "compressed fixture differs"):
                _distributional_shadow_t2_xz_json(
                    path,
                    hashlib.sha256(compressed).hexdigest(),
                    hashlib.sha256(encoded).hexdigest(),
                    "fixture",
                )


if __name__ == "__main__":
    unittest.main()
