from __future__ import annotations

import csv
import hashlib
import json
import math
import pickle
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from train_randomized_value import (
    CANDIDATE_SPECS,
    COMPARISON_CONTRACTS,
    DATASET_COLUMNS,
    DEFAULT_MAX_DR_ASSIGNMENT_AIRTIME_US_PER_FRAME,
    FEATURE_ADAPTER_ID,
    FEATURE_COLUMNS,
    FOLD_ALGORITHM,
    NON_FEATURE_COLUMNS,
    RANDOMIZED_INTERVENTION_CONTRACT,
    SCORE_ADAPTER_ID,
    SPLIT_ALGORITHM,
    TrainingError,
    _apply_score_threshold,
    _assignment_cost_dr,
    _candidate_scores,
    _cluster_bootstrap_policy_uncertainty,
    _cross_fit_partitions,
    _dr_benefit_pseudo_outcome,
    _duan_smearing_factor,
    _expected_assignment_cost,
    _positive_float_or_none,
    _quantize_candidate_scores,
    _select_policy_record,
    _threshold_for_fraction,
    expected_run_splits,
    grouped_four_folds,
    load_randomized_dataset,
    train_value_models,
)

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def split_counts(count: int) -> dict[str, int]:
    assignments = expected_run_splits([(1000 + index, 1) for index in range(count)])
    return {
        role: sum(value == role for value in assignments.values())
        for role in ("train", "calibration", "test")
    }


class ConstantModel:
    def __init__(self, value: float):
        self.value = value

    def predict(self, matrix: np.ndarray) -> np.ndarray:
        return np.full(len(matrix), self.value, dtype=float)


def outcome_row(
    frame: int, treatment: bool, launched: bool, rescue_success: bool
) -> dict[str, str]:
    primary_bad_deadline = frame % 2 == 0 or treatment
    treatment_did_not_rescue = not launched or not rescue_success
    union_bad_deadline = primary_bad_deadline and (
        not treatment or treatment_did_not_rescue
    )
    primary_bad_12000 = frame % 2 == 0 or treatment
    union_bad_12000 = primary_bad_12000 and (
        not treatment or treatment_did_not_rescue
    )
    primary_bad_12500 = frame % 3 == 0 or frame % 4 == 0 or treatment
    union_bad_12500 = primary_bad_12500 and (
        not treatment or treatment_did_not_rescue
    )

    primary_incomplete = frame % 11 == 0
    incomplete = primary_incomplete and union_bad_deadline
    return {
        "outcome_incomplete": str(int(incomplete)),
        "outcome_deadline_miss": str(int(union_bad_deadline)),
        "outcome_union_latency_us": "" if incomplete else "11900",
        "outcome_complete_by_10000us": "0",
        "outcome_complete_by_11000us": "0",
        "outcome_complete_by_12000us": str(int(not union_bad_12000)),
        "outcome_complete_by_12500us": str(int(not union_bad_12500)),
        "outcome_primary_incomplete": str(int(primary_incomplete)),
        "outcome_primary_deadline_miss": str(int(primary_bad_deadline)),
        "outcome_primary_latency_us": "" if primary_incomplete else "12800",
        "outcome_primary_complete_by_10000us": "0",
        "outcome_primary_complete_by_11000us": "0",
        "outcome_primary_complete_by_12000us": str(int(not primary_bad_12000)),
        "outcome_primary_complete_by_12500us": str(int(not primary_bad_12500)),
        "outcome_deadline_rescue": str(
            int(primary_bad_deadline and not union_bad_deadline)
        ),
        "outcome_tail_rescue_10000us": "0",
        "outcome_tail_rescue_11000us": "0",
        "outcome_tail_rescue_12000us": str(
            int(primary_bad_12000 and not union_bad_12000)
        ),
        "outcome_tail_rescue_12500us": str(
            int(primary_bad_12500 and not union_bad_12500)
        ),
        "outcome_deadline_capped_latency_saving_us": (
            "700" if primary_bad_deadline and not union_bad_deadline else "0"
        ),
    }


def feature_value(name: str, seed: int, frame: int, stage: str) -> str:
    if name == "x_f0_frame_type":
        return "I_FRAME" if frame == 0 else "P_FRAME"
    if name == "x_f0_frame_age_us":
        return str(2000 if stage == "T2" else 4000)
    if name == "x_primary_frame_packets_currently_queued" and frame == 7:
        return ""
    return format((seed % 9 + frame) / 30, ".6f")


def write_manifest(root: Path) -> None:
    manifest = {
        "manifest_schema_version": 1,
        "hash_algorithm": "sha256",
        "artifacts_sha256": {
            name: sha256(root / name)
            for name in (
                "randomized_t2.csv",
                "randomized_t4_wait.csv",
                "dataset_metadata.json",
            )
        },
    }
    (root / "artifact_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def make_dataset(root: Path, run_count: int = 12) -> None:
    root.mkdir(parents=True)
    groups = [(1000 + index, 1) for index in range(run_count)]
    splits = expected_run_splits(groups)
    assignments = [
        {"seed": seed, "run_number": run_number, "split_role": splits[(seed, run_number)]}
        for seed, run_number in sorted(groups)
    ]
    row_counts: dict[str, int] = {}
    for stage, filename in (("T2", "randomized_t2.csv"), ("T4", "randomized_t4_wait.csv")):
        treatment_probability = 0.08 if stage == "T2" else 0.12
        conditional_probability = treatment_probability / (
            treatment_probability + 0.8
        )
        assignment_modulus = 12 if stage == "T2" else 8
        rows: list[dict[str, str]] = []
        for seed, run_number in groups:
            for frame in range(15):
                assignment_phase = (frame + seed) % assignment_modulus
                treatment = assignment_phase == 0
                launched = treatment and (seed + (0 if stage == "T2" else 1)) % 5 != 0
                rescue_success = launched and (seed + frame) % 3 != 0
                arm = f"FULL_COPY_{stage}" if treatment else "CONTROL"
                row = {
                    "dataset_schema_version": "1",
                    "run_id": f"run-{seed}",
                    "seed": str(seed),
                    "run_number": str(run_number),
                    "split_role": splits[(seed, run_number)],
                    "frame_id": str(frame),
                    "analysis_stage": stage,
                    "assigned_arm": arm,
                    "treatment": str(int(treatment)),
                    "treatment_probability": format(conditional_probability, ".17g"),
                    "assigned_arm_probability": (
                        format(treatment_probability, ".17g")
                        if treatment
                        else "0.8"
                    ),
                    "eligible_t2": "1",
                    "decision_primary_actionable": "1",
                    "attempted": str(int(treatment)),
                    "launched": str(int(launched)),
                    "noncompliance": str(int(treatment and not launched)),
                    "execution_stage": stage if treatment else "NONE",
                    "execution_status": (
                        "launched"
                        if launched
                        else "launch_rejected"
                        if treatment
                        else "control_no_launch"
                    ),
                    "outcome_secondary_airtime_us": (
                        str(100 + frame + seed % 7) if launched else "0"
                    ),
                    "outcome_secondary_released_airtime_us": "0",
                    "outcome_secondary_fallback": "0",
                    "action_nominal_airtime_us": "100",
                    "action_estimated_airtime_us": "125",
                }
                row.update(outcome_row(frame, treatment, launched, rescue_success))
                row.update(
                    {
                        name: feature_value(name, seed, frame, stage)
                        for name in FEATURE_COLUMNS
                    }
                )
                rows.append(row)
        with (root / filename).open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=DATASET_COLUMNS,
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(rows)
        row_counts[stage] = len(rows)

    metadata = {
        "dataset_schema_version": 1,
        "feature_contract_id": "randomized_intervention_leakage_safe_v1",
        "comparisons": {
            stage: {**contract, "row_count": row_counts[stage]}
            for stage, contract in COMPARISON_CONTRACTS.items()
        },
        "feature_contract": {
            "feature_columns": list(FEATURE_COLUMNS),
            "categorical_features": ["x_f0_frame_type"],
        },
        "non_feature_columns": list(NON_FEATURE_COLUMNS),
        "environment_compatibility": {
            "invariant_projection": {
                "randomizedIntervention": json.loads(
                    json.dumps(RANDOMIZED_INTERVENTION_CONTRACT)
                )
            }
        },
        "split": {
            "algorithm": SPLIT_ALGORITHM,
            "unit": "(seed, run_number)",
            "target_ratio": {"train": 64, "calibration": 16, "test": 16},
            "counts": split_counts(run_count),
            "assignments": assignments,
        },
    }
    (root / "dataset_metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_manifest(root)


class SplitTests(unittest.TestCase):
    def test_split_and_fold_units_are_whole_runs(self) -> None:
        groups = [(1000 + index, 1) for index in range(96)]
        splits = expected_run_splits(groups)
        self.assertEqual(
            {role: list(splits.values()).count(role) for role in ("train", "calibration", "test")},
            {"train": 64, "calibration": 16, "test": 16},
        )
        folds = grouped_four_folds(group for group, role in splits.items() if role == "train")
        self.assertEqual(set(folds.values()), {0, 1, 2, 3})
        self.assertEqual([list(folds.values()).count(fold) for fold in range(4)], [16] * 4)


class NumericEstimatorTests(unittest.TestCase):
    def test_airtime_ceiling_defaults_on_and_none_is_explicit(self) -> None:
        self.assertEqual(DEFAULT_MAX_DR_ASSIGNMENT_AIRTIME_US_PER_FRAME, 400.0)
        self.assertEqual(_positive_float_or_none("400"), 400.0)
        self.assertIsNone(_positive_float_or_none("none"))

    def test_dr_benefit_formula_has_y0_minus_y1_sign_at_p_point_two(self) -> None:
        observed = _dr_benefit_pseudo_outcome(
            np.asarray([0.0, 1.0]),
            np.asarray([1.0, 0.0]),
            np.asarray([0.2, 0.2]),
            np.asarray([0.6, 0.6]),
            np.asarray([0.3, 0.3]),
        )
        np.testing.assert_allclose(observed, [1.8, 0.8])
        self.assertTrue(np.all(observed > 0))

    def test_calibration_threshold_is_transferred_without_test_requantile(self) -> None:
        calibration = np.asarray([0.1, 0.9, 0.8, 0.2])
        threshold = _threshold_for_fraction(calibration, 0.5)
        self.assertEqual(threshold, float(np.float32(0.8)))
        np.testing.assert_array_equal(
            _apply_score_threshold(calibration, threshold),
            [False, True, True, False],
        )
        test = np.asarray([0.79, 0.81, 0.1, 0.95])
        np.testing.assert_array_equal(
            _apply_score_threshold(test, threshold),
            [False, True, False, True],
        )

    def test_final_score_and_threshold_share_float32_boundary(self) -> None:
        float_below_one = float(np.nextafter(np.float32(1.0), np.float32(0.0)))
        raw_scores = np.asarray([float_below_one, 1.0 - 2**-25, 1.0 + 2**-25])
        quantized = _quantize_candidate_scores(raw_scores)
        np.testing.assert_array_equal(quantized, [float_below_one, 1.0, 1.0])
        mask = _apply_score_threshold(raw_scores, 1.0 + 2**-25)
        np.testing.assert_array_equal(mask, [False, True, True])

    def test_hurdle_smearing_and_assignment_cost_include_nonlaunch(self) -> None:
        observed_log = np.log(np.asarray([101.0, 404.0]))
        predicted_log = np.log(np.asarray([101.0, 101.0]))
        smearing = _duan_smearing_factor(observed_log, predicted_log)
        self.assertAlmostEqual(smearing, 2.5)
        expected = _expected_assignment_cost(
            np.asarray([1.0, 0.0, 0.5]),
            np.log(np.asarray([101.0, 101.0, 101.0])),
            smearing,
        )
        np.testing.assert_allclose(expected, [251.5, 0.0, 125.75])
        dr_cost = _assignment_cost_dr(
            np.asarray([200.0, 0.0]),
            np.asarray([1.0, 0.0]),
            np.asarray([0.2, 0.2]),
            np.asarray([100.0, 100.0]),
        )
        np.testing.assert_allclose(dr_cost, [600.0, 100.0])

    def test_launch_conditioned_rescue_scores_are_assignment_scores(self) -> None:
        components = {
            "ridge_dr": ConstantModel(0.3),
            "hgb_dr": ConstantModel(0.4),
            "primary_need": ConstantModel(0.8),
            "conditional_rescue": ConstantModel(0.5),
            "direct_realized_rescue": ConstantModel(0.6),
            "assignment_launch_probability": ConstantModel(0.25),
            "log_measured_cost_given_launch": ConstantModel(math.log(101.0)),
            "log_cost_smearing_factor": 1.0,
        }
        scores = _candidate_scores(components, np.zeros((1, 1)))
        self.assertAlmostEqual(
            scores["mechanistic_need_x_conditional_rescue"][0], 0.1
        )
        self.assertAlmostEqual(scores["direct_realized_rescue"][0], 0.15)
        self.assertAlmostEqual(
            scores["mechanistic_rescue_per_predicted_airtime"][0], 0.004
        )
        components["assignment_launch_probability"] = ConstantModel(0.0)
        no_launch = _candidate_scores(components, np.zeros((1, 1)))
        self.assertEqual(no_launch["mechanistic_need_x_conditional_rescue"][0], 0.0)
        self.assertEqual(no_launch["direct_realized_rescue"][0], 0.0)
        self.assertEqual(
            no_launch["mechanistic_rescue_per_predicted_airtime"][0], 0.0
        )

    def test_airtime_ceiling_rejects_higher_benefit_candidate(self) -> None:
        records = [
            {
                "candidate": "over_budget",
                "requested_treatment_fraction": 0.1,
                "realized_treatment_fraction": 0.1,
                "dr_policy_benefit": 0.2,
                "dr_policy_measured_airtime_us_per_frame": 401.0,
            },
            {
                "candidate": "within_budget",
                "requested_treatment_fraction": 0.1,
                "realized_treatment_fraction": 0.1,
                "dr_policy_benefit": 0.1,
                "dr_policy_measured_airtime_us_per_frame": 399.0,
            },
        ]
        selected, audited = _select_policy_record(
            records,
            max_treatment_fraction=0.2,
            max_dr_assignment_airtime_us_per_frame=400.0,
        )
        self.assertEqual(selected["candidate"], "within_budget")
        rejected = next(row for row in audited if row["candidate"] == "over_budget")
        self.assertEqual(rejected["admissible"], 0)
        self.assertEqual(rejected["rejection_reason"], "assignment_airtime")


class LoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "dataset"
        make_dataset(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_loads_known_propensity_and_unconditional_labels(self) -> None:
        dataset = load_randomized_dataset(self.root)
        self.assertEqual(set(dataset.stages), {"T2", "T4"})
        self.assertEqual(len(dataset.stages["T2"].rows), 180)
        self.assertEqual(dataset.feature_columns, FEATURE_COLUMNS)
        row = dataset.stages["T2"].rows[0]
        self.assertAlmostEqual(row.propensity, 0.08 / 0.88)
        self.assertEqual(row.outcomes["bad_tail_12000us"], 1.0)
        self.assertEqual(row.outcomes["primary_bad_bad_tail_12000us"], 1.0)
        for stage, expected in (("T2", 0.08 / 0.88), ("T4", 0.12 / 0.92)):
            stage_rows = dataset.stages[stage].rows
            empirical = sum(item.treatment for item in stage_rows) / len(stage_rows)
            self.assertLess(abs(empirical - expected), 0.03)
            for group in {item.group for item in stage_rows}:
                group_rows = [item for item in stage_rows if item.group == group]
                self.assertIn(sum(item.treatment for item in group_rows), {1, 2})
                self.assertEqual(len(group_rows), 15)

    def test_cross_fit_partitions_exclude_whole_runs(self) -> None:
        data = load_randomized_dataset(self.root).stages["T2"]
        train = data.indices("train")
        partitions = _cross_fit_partitions(data, train)
        held_occurrences: dict[tuple[int, int], int] = {}
        for held, fitted in partitions:
            held_groups = {data.rows[index].group for index in held}
            fitted_groups = {data.rows[index].group for index in fitted}
            self.assertFalse(held_groups & fitted_groups)
            for group in held_groups:
                held_occurrences[group] = held_occurrences.get(group, 0) + 1
                self.assertEqual(
                    sum(data.rows[index].group == group for index in held), 15
                )
        self.assertEqual(set(held_occurrences.values()), {1})

    def test_float32_runtime_precision_boundary_is_applied_before_fit(self) -> None:
        path = self.root / "randomized_t2.csv"
        with path.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        rows[0]["x_f0_frame_size_bytes"] = "16777217"
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=DATASET_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        write_manifest(self.root)
        data = load_randomized_dataset(self.root).stages["T2"]
        encoded_index = data.encoded_feature_names.index("x_f0_frame_size_bytes")
        row_index = next(index for index, row in enumerate(data.rows) if row.key == (1000, 1, 0))
        self.assertEqual(data.matrix[row_index, encoded_index], 16777216.0)
        self.assertNotEqual(data.matrix[row_index, encoded_index], 16777217.0)

    def test_whole_run_bootstrap_is_non_degenerate_for_heterogeneous_runs(self) -> None:
        data = load_randomized_dataset(self.root).stages["T2"]
        all_test = data.indices("test")
        groups = sorted({data.rows[index].group for index in all_test})
        self.assertEqual(len(groups), 2)
        indices = np.asarray(
            [index for index in all_test if data.rows[index].group == groups[0]]
            + [
                index
                for index in all_test
                if data.rows[index].group == groups[1]
            ][:5],
            dtype=int,
        )
        self.assertEqual(
            [sum(data.rows[index].group == group for index in indices) for group in groups],
            [15, 5],
        )
        phi0 = np.asarray(
            [1.0 if data.rows[index].group == groups[0] else 3.0 for index in indices]
        )
        phi1 = np.zeros(len(indices))
        cost = np.asarray(
            [10.0 if data.rows[index].group == groups[0] else 100.0 for index in indices]
        )
        uncertainty = _cluster_bootstrap_policy_uncertainty(
            data,
            indices,
            np.ones(len(indices), dtype=bool),
            phi0,
            phi1,
            cost,
            seed=7,
            context="T2:bad_tail_12000us:test",
        )
        benefit = uncertainty["estimands"]["dr_policy_benefit"]
        airtime = uncertainty["estimands"][
            "dr_policy_measured_airtime_us_per_frame"
        ]
        self.assertAlmostEqual(benefit["estimate"], 1.5)
        self.assertEqual(benefit["ci_lower"], 1.0)
        self.assertEqual(benefit["ci_upper"], 3.0)
        self.assertAlmostEqual(airtime["estimate"], 32.5)
        self.assertEqual(airtime["ci_lower"], 10.0)
        self.assertEqual(airtime["ci_upper"], 100.0)

    def test_rejects_metadata_split_leakage_even_after_rehash(self) -> None:
        path = self.root / "randomized_t2.csv"
        with path.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        rows[0]["split_role"] = "test"
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=DATASET_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        write_manifest(self.root)
        with self.assertRaisesRegex(TrainingError, "identity or arm contract"):
            load_randomized_dataset(self.root)

    def test_rejects_rescue_label_not_derived_from_factual_primary(self) -> None:
        path = self.root / "randomized_t4_wait.csv"
        with path.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        rows[4]["outcome_tail_rescue_12500us"] = str(
            1 - int(rows[4]["outcome_tail_rescue_12500us"])
        )
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=DATASET_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        write_manifest(self.root)
        with self.assertRaisesRegex(TrainingError, "rescue arithmetic"):
            load_randomized_dataset(self.root)

    def test_rejects_source_hash_mutation(self) -> None:
        path = self.root / "randomized_t2.csv"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(TrainingError, "artifact hash differs"):
            load_randomized_dataset(self.root)

    def test_rejects_rehashed_outcome_promoted_to_feature(self) -> None:
        metadata_path = self.root / "dataset_metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["non_feature_columns"].remove("outcome_deadline_miss")
        metadata["feature_contract"]["feature_columns"].insert(
            0, "outcome_deadline_miss"
        )
        metadata_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        write_manifest(self.root)
        with self.assertRaisesRegex(TrainingError, "builder contract"):
            load_randomized_dataset(self.root)

    def test_rejects_rehashed_comparison_estimand_mutations(self) -> None:
        metadata_path = self.root / "dataset_metadata.json"
        original = metadata_path.read_text(encoding="utf-8")
        mutations = (
            ("T2", "population", "all_frames"),
            ("T2", "estimand", "as_treated"),
            ("T4", "population", "common_T2_eligible"),
            ("T4", "estimand", "binary_assignment_ITT"),
            ("T4", "excludes", "none"),
        )
        for stage, field, changed in mutations:
            with self.subTest(stage=stage, field=field):
                metadata = json.loads(original)
                metadata["comparisons"][stage][field] = changed
                metadata_path.write_text(
                    json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                write_manifest(self.root)
                with self.assertRaisesRegex(TrainingError, "comparison contract"):
                    load_randomized_dataset(self.root)

    def test_rejects_rehashed_conditional_probability_denominator_change(self) -> None:
        metadata_path = self.root / "dataset_metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        randomized = metadata["environment_compatibility"]["invariant_projection"][
            "randomizedIntervention"
        ]
        randomized["arm_probabilities"]["CONTROL"] = 0.79
        metadata_path.write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        write_manifest(self.root)
        with self.assertRaisesRegex(TrainingError, "intervention design contract"):
            load_randomized_dataset(self.root)


class TrainingTests(unittest.TestCase):
    def test_emits_deterministic_honest_stage_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset_path = root / "dataset"
            make_dataset(dataset_path)
            dataset = load_randomized_dataset(dataset_path)
            first = root / "first"
            second = root / "second"
            first_metrics = train_value_models(dataset, first)
            second_metrics = train_value_models(dataset, second)

            for name in (
                "value_models.pkl",
                "value_policy_candidates.csv",
                "value_training_metrics.json",
                "artifact_manifest.json",
            ):
                self.assertEqual(sha256(first / name), sha256(second / name), name)
            self.assertFalse(
                first_metrics["model_selection"]["test_role_used_during_selection"]
            )
            self.assertEqual(
                first_metrics["model_selection"][
                    "maximum_calibration_dr_assignment_airtime_us_per_frame"
                ],
                DEFAULT_MAX_DR_ASSIGNMENT_AIRTIME_US_PER_FRAME,
            )
            self.assertEqual(
                first_metrics["feature_adapter"]["adapter_id"], FEATURE_ADAPTER_ID
            )
            self.assertEqual(
                first_metrics["score_adapter"]["adapter_id"], SCORE_ADAPTER_ID
            )
            self.assertEqual(
                len(first_metrics["provenance"]["trainer_source_sha256"]), 64
            )
            self.assertIn("deferred", first_metrics["sequential_policy_replay"])
            for stage in ("T2", "T4"):
                for target in (
                    "deadline_miss",
                    "bad_tail_12000us",
                    "bad_tail_12500us",
                ):
                    result = first_metrics["stages"][stage]["targets"][target]
                    self.assertEqual(
                        result["cross_fitted_nuisance"]["algorithm"], FOLD_ALGORITHM
                    )
                    self.assertEqual(len(result["cross_fitted_nuisance"]["folds"]), 4)
                    self.assertEqual(result["heldout_test_policy"]["run_count"], 2)
                    uncertainty = result["heldout_test_policy"][
                        "run_cluster_uncertainty"
                    ]
                    self.assertEqual(uncertainty["replications"], 2000)
                    self.assertEqual(uncertainty["unit"], "(seed, run_number)")
                    self.assertEqual(uncertainty["run_count"], 2)
                    self.assertEqual(
                        uncertainty["evidence_role"],
                        "locked_heldout_after_calibration_selection",
                    )
                    self.assertEqual(
                        set(uncertainty["estimands"]),
                        {
                            "dr_policy_benefit",
                            "dr_policy_bad_outcome",
                            "dr_policy_measured_airtime_us_per_frame",
                        },
                    )
                    self.assertAlmostEqual(
                        uncertainty["estimands"]["dr_policy_benefit"]["estimate"],
                        result["heldout_test_policy"]["dr_policy_benefit"],
                    )

            with (first / "value_models.pkl").open("rb") as source:
                bundle = pickle.load(source)
            self.assertEqual(set(bundle["candidate_specs"]), set(CANDIDATE_SPECS))
            self.assertEqual(bundle["feature_adapter_id"], FEATURE_ADAPTER_ID)
            self.assertEqual(bundle["score_adapter_id"], SCORE_ADAPTER_ID)
            self.assertEqual(
                bundle["score_comparator"], "float32 score >= float32 threshold"
            )
            target_bundle = bundle["stages"]["T2"]["targets"]["bad_tail_12500us"]
            self.assertIn("ridge_dr", target_bundle["components"])
            self.assertIn(
                "assignment_launch_probability", target_bundle["components"]
            )
            self.assertIn(
                "log_measured_cost_given_launch", target_bundle["components"]
            )
            self.assertGreater(
                target_bundle["components"]["log_cost_smearing_factor"], 0.0
            )
            hgb = target_bundle["components"]["hgb_dr"].named_steps["regressor"]
            self.assertEqual(hgb.max_depth, 3)
            self.assertEqual(hgb.max_iter, 64)
            self.assertEqual(hgb.max_leaf_nodes, 7)

    def test_refuses_training_without_honest_heldout_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset_path = root / "dataset"
            make_dataset(dataset_path, run_count=1)
            dataset = load_randomized_dataset(dataset_path)
            with self.assertRaisesRegex(TrainingError, "honest training needs"):
                train_value_models(dataset, root / "output")


if __name__ == "__main__":
    unittest.main()
