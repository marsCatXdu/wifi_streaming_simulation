#!/usr/bin/env python3
"""Focused tests for frozen environment-generalization policy primitives."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_environment_generalization_lofo as analyzer  # noqa: E402
import environment_generalization_lofo as lofo  # noqa: E402
import environment_generalization_policy as policy  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class EnvironmentGeneralizationPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = policy.load_policy_contract()
        cls.lofo_contract = lofo.load_contract()

    def test_current_loader_uses_a_hash_bound_compatibility_contract(self) -> None:
        compatibility_path = ROOT / policy.LOADER_COMPATIBILITY_PATH
        self.assertEqual(
            _sha256(compatibility_path), policy.LOADER_COMPATIBILITY_SHA256
        )
        source = self.contract["sources"]["dataset_loader"]
        policy._validate_dataset_loader_compatibility(
            source, _sha256(ROOT / source["path"])
        )

    def test_exact_two_cost_knapsack_obeys_lexicographic_objectives(self) -> None:
        primary = np.asarray([5.0, 4.0, 0.0, 9.0, 1.0])
        secondary = np.asarray([0.0, 100.0, 10.0, 0.0, 100.0])
        chosen, spend, reward = policy.exact_two_cost_knapsack(
            np.arange(5),
            primary,
            secondary,
            ("P_FRAME", "P_FRAME", "P_FRAME", "I_FRAME", "I_FRAME"),
            np.asarray([10, 20, 30, 40, 50]),
            ("4", "4", "4", "7", "7"),
            14,
        )
        self.assertEqual(set(chosen), {0, 3})
        self.assertEqual(str(spend), "11")
        self.assertEqual(reward, (14.0, 0.0))

        tie_chosen, _, tie_reward = policy.exact_two_cost_knapsack(
            np.arange(3),
            np.asarray([1.0, 1.0, 2.0]),
            np.asarray([1.0, 1.0, 10.0]),
            ("P_FRAME", "P_FRAME", "I_FRAME"),
            np.asarray([1, 2, 3]),
            ("5", "5", "10"),
            10,
        )
        self.assertEqual(tie_chosen, (2,))
        self.assertEqual(tie_reward, (2.0, 10.0))

    def test_exact_knapsack_rejects_nonconstant_per_type_cost(self) -> None:
        with self.assertRaisesRegex(policy.PolicyError, "not constant"):
            policy.exact_two_cost_knapsack(
                np.arange(2),
                np.ones(2),
                np.zeros(2),
                ("P_FRAME", "P_FRAME"),
                np.asarray([1, 2]),
                ("4", "5"),
                10,
            )

    def _minimal_data(self) -> SimpleNamespace:
        return SimpleNamespace(
            run_ids=("run-a", "run-a", "run-a", "run-a"),
            scenario_ids=("scenario-a",) * 4,
            family_ids=("family-a",) * 4,
            seeds=np.asarray([7, 7, 7, 7]),
            run_numbers=np.asarray([1, 1, 1, 1]),
            frame_ids=np.asarray([0, 1, 2, 3]),
            frame_types=("P_FRAME", "P_FRAME", "I_FRAME", "I_FRAME"),
            canonical_reservation_texts=("6", "6", "11", "11"),
        )

    def test_uniform_replay_is_deterministic_and_never_exceeds_budget(self) -> None:
        data = self._minimal_data()
        first = list(policy.uniform_policy_replications(data, 17, 4, 1234))
        second = list(policy.uniform_policy_replications(data, 17, 4, 1234))
        self.assertEqual(len(first), 4)
        for (first_index, first_trace), (second_index, second_trace) in zip(
            first, second, strict=True
        ):
            self.assertEqual(first_index, second_index)
            np.testing.assert_array_equal(
                first_trace.action_probability, second_trace.action_probability
            )
            details = first_trace.run_details["run-a"]
            self.assertLessEqual(details["canonical_reservation_us"], 17)
            self.assertEqual(
                details["actions"], int(np.sum(first_trace.action_probability))
            )

    def test_deployment_exploration_logs_fallback_forcing_and_budget(self) -> None:
        data = SimpleNamespace(
            run_ids=("run-a",) * 6,
            scenario_ids=("scenario-a",) * 6,
            family_ids=("family-a",) * 6,
            seeds=np.full(6, 7),
            run_numbers=np.ones(6, dtype=int),
            frame_ids=np.arange(6),
            canonical_reservation_texts=("60",) * 6,
        )
        draws = {0: 0.5, 1: 0.001, 2: 0.005, 3: 0.015, 4: 0.5, 5: 0.5}

        def assignment(
            salt: int,
            seed: int,
            run: int,
            frame_id: int,
            t2_probability: float,
            t4_probability: float,
        ) -> SimpleNamespace:
            del salt, seed, run, t2_probability, t4_probability
            return SimpleNamespace(unit_draw=draws[frame_id])

        base = np.asarray([1, 0, 0, 1, 1, 0], dtype=float)
        hard = np.asarray([1, 0, 0, 0, 0, 0], dtype=np.int8)
        soft = np.asarray([0, 1, 0, 0, 0, 0], dtype=np.int8)
        with mock.patch.object(
            policy.randomized_frame_assignment, "assign_frame", side_effect=assignment
        ):
            trace = policy.apply_deployment_exploration(
                data, base, hard, soft, budget_us=100, contract=self.contract
            )
        np.testing.assert_array_equal(trace.executed_action, [0, 1, 0, 0, 0, 0])
        np.testing.assert_allclose(
            trace.assignment_action_probability, [0, 0.002, 0.01, 0.99, 0.99, 0.01]
        )
        np.testing.assert_array_equal(trace.assigned_forced_t2, [0, 1, 1, 0, 0, 0])
        np.testing.assert_array_equal(
            trace.assigned_forced_control, [0, 0, 0, 1, 0, 0]
        )
        np.testing.assert_array_equal(
            trace.execution_compliance, [1, 1, 0, 1, 0, 1]
        )
        self.assertEqual(trace.route[0], "hard_ood_fallback")
        self.assertEqual(trace.route[1], "soft_ood_forced_t2")
        self.assertEqual(trace.route[2], "in_support_forced_t2_budget_rejected")
        self.assertEqual(
            trace.run_details["run-a"]["canonical_reservation_text_us"], "60"
        )

    def test_policy_value_components_match_known_propensity_formulas(self) -> None:
        data = SimpleNamespace(
            treatment=np.asarray([0, 1], dtype=np.int8),
            propensity=np.asarray([0.25, 0.25]),
            outcome_bins=np.asarray([0, 2]),
            deadline_threshold_indices=np.asarray([1, 1]),
            completed_late18=np.asarray([0, 1]),
            contract={"completion_distribution": {"thresholds_us": [10_000, 18_000]}},
        )
        cdf = np.asarray(
            [
                [[0.4, 0.7], [0.6, 0.8]],
                [[0.2, 0.3], [0.5, 0.6]],
            ]
        )
        components = policy.policy_value_components(
            data, cdf, np.asarray([0.0, 1.0])
        )
        phi0, phi1 = analyzer.doubly_robust_cdf_components(
            data.outcome_bins, data.treatment, data.propensity, cdf
        )
        np.testing.assert_allclose(
            components["deadline_miss"],
            1.0 - np.asarray([phi0[0, 1], phi1[1, 1]]),
        )
        np.testing.assert_allclose(components["completed_late18"], [0.0, 4.0])

    def test_prediction_loader_requires_exact_row_alignment_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset = root / "dataset"
            output = root / "lofo"
            dataset.mkdir()
            output.mkdir()
            for name, value in (
                ("artifact_manifest.json", "{}\n"),
                ("dataset_metadata.json", "{}\n"),
                ("environment_randomized_t2_temporal.csv", "dummy\n"),
            ):
                (dataset / name).write_text(value, encoding="utf-8")
            data = SimpleNamespace(
                path=dataset,
                contract=self.lofo_contract,
                run_ids=("run-a", "run-a"),
                seeds=np.asarray([1, 1]),
                run_numbers=np.asarray([2, 2]),
                frame_ids=np.asarray([10, 11]),
                scenario_ids=("scenario-a", "scenario-a"),
                family_ids=("radio_propagation", "radio_propagation"),
                parameter_samples=np.asarray([0, 0]),
                treatment=np.asarray([0, 1]),
                propensity=np.asarray([0.2, 0.2]),
                outcome_bins=np.asarray([0, 1]),
                deadline_miss=np.asarray([0, 0]),
                primary_deadline_miss=np.asarray([0, 1]),
                deadline_us=np.asarray([33_333, 33_333]),
                deadline_threshold_indices=np.asarray([6, 6]),
                canonical_reservation_us=np.asarray([10.0, 10.0]),
            )
            thresholds = self.lofo_contract["completion_distribution"][
                "thresholds_us"
            ]
            cdf = np.empty((2, 2, len(thresholds)), dtype=float)
            cdf[:, 0, :] = np.linspace(0.1, 0.8, len(thresholds))
            cdf[:, 1, :] = np.linspace(0.2, 0.9, len(thresholds))
            ood = {
                "score": np.asarray([1.0, 2.0]),
                "threshold": np.asarray([10.0, 10.0]),
                "hard_failure": np.asarray([0, 0]),
                "soft_ood": np.asarray([0, 0]),
                "fallback": np.asarray([0, 0]),
            }
            prediction_path = output / analyzer.OUTPUT_PREDICTIONS
            analyzer._write_predictions(prediction_path, data, cdf, ood)
            metrics = {
                "analysis_id": "environment-generalization-lofo-v1",
                "analysis_schema_version": analyzer.ANALYSIS_SCHEMA_VERSION,
                "design_contract": {"sha256": lofo.CONTRACT_SHA256},
                "analysis_sources_sha256": {
                    self.contract["sources"][name]["path"]: self.contract[
                        "sources"
                    ][name]["sha256"]
                    for name in ("dataset_loader", "lofo_predictor")
                },
                "dataset": {
                    "artifact_manifest_sha256": _sha256(
                        dataset / "artifact_manifest.json"
                    ),
                    "dataset_metadata_sha256": _sha256(
                        dataset / "dataset_metadata.json"
                    ),
                    "dataset_csv_sha256": _sha256(
                        dataset / "environment_randomized_t2_temporal.csv"
                    ),
                    "row_count": 2,
                },
            }
            metrics_path = output / analyzer.OUTPUT_METRICS
            metrics_path.write_text(
                json.dumps(metrics, sort_keys=True) + "\n", encoding="utf-8"
            )
            manifest = {
                "manifest_schema_version": 1,
                "hash_algorithm": "sha256",
                "artifacts_sha256": {
                    analyzer.OUTPUT_PREDICTIONS: _sha256(prediction_path),
                    analyzer.OUTPUT_METRICS: _sha256(metrics_path),
                },
            }
            (output / analyzer.OUTPUT_MANIFEST).write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
            )
            loaded = policy.load_lofo_predictions(output, data)
            np.testing.assert_allclose(loaded.cdf, cdf)
            self.assertEqual(loaded.path, output)


if __name__ == "__main__":
    unittest.main()
