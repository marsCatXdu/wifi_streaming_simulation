#!/usr/bin/env python3
"""Checks for the neutral randomized full-copy collection campaigns."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from run_experiments import (  # noqa: E402
    cli_arguments,
    expand_config,
    load_yaml,
    write_experiment_description,
)


CONFIG_DIR = ROOT / "experiments" / "configs"
PILOT = CONFIG_DIR / "randomized_full_copy_exploration_pilot_v1.yaml"
COLLECTION = CONFIG_DIR / "randomized_full_copy_exploration_collection_v1.yaml"
PARENT = CONFIG_DIR / "closed_loop_adaptive_airtime_obss.yaml"


class RandomizedInterventionCampaignTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parent = load_yaml(PARENT)
        self.pilot = load_yaml(PILOT)
        self.collection = load_yaml(COLLECTION)

    def test_pilot_and_collection_use_disjoint_reserved_seed_ranges(self) -> None:
        pilot_specs = expand_config(self.pilot)
        collection_specs = expand_config(self.collection)
        self.assertEqual(len(pilot_specs), 12)
        self.assertEqual(len(collection_specs), 96)
        self.assertEqual({spec["seed"] for spec in pilot_specs}, set(range(1001, 1013)))
        self.assertEqual(
            {spec["seed"] for spec in collection_specs}, set(range(1101, 1197))
        )
        self.assertTrue(
            {spec["seed"] for spec in pilot_specs}.isdisjoint(
                spec["seed"] for spec in collection_specs
            )
        )
        self.assertTrue(all(spec["seed"] < 1301 for spec in collection_specs))
        self.assertEqual(self.pilot["workers"], 64)
        self.assertEqual(self.collection["workers"], 64)

    def test_campaigns_preserve_neutral_environment_and_one_treatment(self) -> None:
        for document in (self.pilot, self.collection):
            self.assertEqual(document["base"], self.parent["base"])
            self.assertEqual(document["base"]["obss"]["obss_profile"], "mixed4x4")
            specs = expand_config(document)
            self.assertEqual(
                {
                    (spec["config"]["topology"], spec["config"]["policy"])
                    for spec in specs
                },
                {("dual_interface", "randomized_full_copy_exploration")},
            )

    def test_randomization_and_telemetry_settings_are_exact(self) -> None:
        expected = {
            "--randomizedAssignmentSalt=5927104639973545521",
            "--randomizedT2Probability=0.08",
            "--randomizedT4Probability=0.12",
            "--randomizedAssignmentStopGuardUs=534000",
            "--predictionSampleOffsetsUs=0,2000,4000",
            "--predictionPollingIntervalUs=1000",
            "--predictionEventLogEnabled=0",
            "--predictionOracleFeaturesEnabled=0",
            "--secondaryAirtimeMeterEnabled=1",
        }
        for document in (self.pilot, self.collection):
            config = expand_config(document)[0]["config"]
            prediction = config["prediction"]
            self.assertTrue(prediction["prediction_telemetry_enabled"])
            self.assertEqual(
                prediction["prediction_sample_offsets_us"], [0, 2000, 4000]
            )
            self.assertEqual(
                prediction["prediction_history_windows_us"], [1000, 5000, 20000]
            )
            self.assertEqual(prediction["prediction_polling_interval_us"], 1000)
            self.assertEqual(prediction["prediction_polling_report_delay_us"], 1000)
            self.assertFalse(prediction["prediction_event_log_enabled"])
            self.assertFalse(prediction["prediction_oracle_features_enabled"])
            self.assertTrue(prediction["secondary_airtime_meter_enabled"])
            self.assertEqual(
                prediction["randomized_assignment_salt"], 5927104639973545521
            )
            self.assertEqual(prediction["randomized_t2_probability"], 0.08)
            self.assertEqual(prediction["randomized_t4_probability"], 0.12)
            self.assertAlmostEqual(
                1
                - prediction["randomized_t2_probability"]
                - prediction["randomized_t4_probability"],
                0.80,
            )
            self.assertEqual(
                prediction["randomized_assignment_stop_guard_us"], 534000
            )
            self.assertTrue(expected <= set(cli_arguments(config, CONFIG_DIR)))

    def test_description_explains_randomized_assignment_and_measured_cost(self) -> None:
        specs = expand_config(self.pilot)
        with tempfile.TemporaryDirectory() as directory:
            write_experiment_description(self.pilot, specs, Path(directory))
            description = (Path(directory) / "DESCRIPTION.rst").read_text()
        self.assertIn("Randomized delayed full-copy exploration", description)
        self.assertIn("control, T2 full-copy, or T4", description)
        self.assertIn("not token-gated", description)
        self.assertIn("Assignment and execution are logged separately", description)
        self.assertIn("secondary airtime meter observes treatment cost", description)
        self.assertIn("probabilities are 0.8, 0.08, and 0.12", description)
        self.assertIn("assignment salt 5927104639973545521", description)


if __name__ == "__main__":
    unittest.main()
