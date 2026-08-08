#!/usr/bin/env python3
"""Closure tests for the 1.5x-background WMM realism matrix."""

from __future__ import annotations

import copy
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from run_experiments import (  # noqa: E402
    cli_arguments,
    expand_config,
    load_yaml,
    validate_runtime_contract,
)
import analyze_scenario15_wmm_realism_bg150_matrix as analysis  # noqa: E402
import plot_scenario15_wmm_realism_bg150_matrix as plot  # noqa: E402


BASELINE = ROOT / "experiments/configs/scenario15_wmm_realism_matrix_v1.yaml"
CONFIG = ROOT / "experiments/configs/scenario15_wmm_realism_bg150_matrix_v1.yaml"
PREFLIGHT = (
    ROOT / "experiments/configs/scenario15_wmm_realism_bg150_matrix_preflight_v1.yaml"
)
SHARDS = [
    ROOT
    / f"experiments/configs/scenario15_wmm_realism_bg150_matrix_v1_shard{index}.yaml"
    for index in range(2)
]
CONTRACT = (
    ROOT
    / "experiments/model-selection/scenario15-wmm-realism-bg150-matrix-v1.json"
)
RATE_FIELDS = {
    "obss_ul_min_rate_mbps": (0.5, 0.75),
    "obss_ul_max_rate_mbps": (3, 4.5),
    "obss_dl_min_rate_mbps": (2, 3),
    "obss_dl_max_rate_mbps": (8, 12),
}


def identity(spec: dict) -> tuple:
    return (
        spec["scenario"]["scenario_id"],
        spec["seed"],
        spec["run"],
        spec["config"]["topology"],
        spec["config"]["policy"],
    )


class Scenario15WmmRealismBg150MatrixTest(unittest.TestCase):
    def test_analyzer_is_bound_to_the_frozen_execution_boundary(self) -> None:
        contract = analysis._verify_contract()
        self.assertEqual(contract["campaign"]["simulation_run_count"], 120)
        self.assertEqual(
            analysis.EXPECTED_PROJECT_COMMIT,
            "460d4222a80916132631e2f3fa259630cca3c922",
        )
        self.assertEqual(analysis.BASELINE_PROJECT_COMMIT, analysis.common.EXPECTED_PROJECT_COMMIT)

    def test_contract_declares_only_the_offered_load_treatment(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        self.assertEqual(
            contract["runtime_contract_id"],
            "scenario15-wmm-realism-bg150-matrix-v1",
        )
        self.assertEqual(contract["status"], "frozen_before_outcomes")
        self.assertEqual(contract["background_offered_load_treatment"]["scale"], 1.5)
        self.assertFalse(contract["campaign"]["reserved_confirmation_seeds_used"])
        self.assertEqual(contract["campaign"]["simulation_run_count"], 120)

    def test_full_matrix_differs_from_baseline_only_in_four_rates(self) -> None:
        baseline = {identity(spec): spec for spec in expand_config(load_yaml(BASELINE))}
        treatment = {identity(spec): spec for spec in expand_config(load_yaml(CONFIG))}
        self.assertEqual(set(treatment), set(baseline))
        self.assertEqual(len(treatment), 120)
        for key in sorted(treatment):
            left = copy.deepcopy(baseline[key]["config"])
            right = copy.deepcopy(treatment[key]["config"])
            for field, (expected_baseline, expected_treatment) in RATE_FIELDS.items():
                self.assertEqual(left["obss"].pop(field), expected_baseline)
                self.assertEqual(right["obss"].pop(field), expected_treatment)
            self.assertEqual(left, right)
            self.assertEqual(baseline[key]["scenario"], treatment[key]["scenario"])

    def test_runtime_contract_and_cli_are_closed(self) -> None:
        document = load_yaml(CONFIG)
        runtime = validate_runtime_contract(document)
        self.assertEqual(
            runtime["runtime_contract_id"],
            "scenario15-wmm-realism-bg150-matrix-v1",
        )
        self.assertEqual(
            runtime["runtime_contract_sha256"],
            "a74cd5678e68d4152ced46c1b0b664c5d8005b5854cee8fb7d73d0fef656d80e",
        )
        arguments = cli_arguments(expand_config(document)[0]["config"], CONFIG.parent)
        for argument in (
            "--obssUlMinRateMbps=0.75",
            "--obssUlMaxRateMbps=4.5",
            "--obssDlMinRateMbps=3",
            "--obssDlMaxRateMbps=12",
        ):
            self.assertIn(argument, arguments)

    def test_preflight_and_shards_preserve_complete_paired_units(self) -> None:
        preflight = expand_config(load_yaml(PREFLIGHT))
        shards = [expand_config(load_yaml(path)) for path in SHARDS]
        self.assertEqual(len(preflight), 12)
        self.assertEqual([len(specs) for specs in shards], [60, 60])
        self.assertEqual({spec["seed"] for spec in preflight}, {1251})
        self.assertEqual({spec["seed"] for spec in shards[0]}, set(range(1251, 1256)))
        self.assertEqual({spec["seed"] for spec in shards[1]}, set(range(1256, 1261)))
        self.assertTrue(
            {identity(spec) for spec in shards[0]}.isdisjoint(
                {identity(spec) for spec in shards[1]}
            )
        )
        for specs in (preflight, *shards):
            self.assertFalse({spec["seed"] for spec in specs} & set(range(1301, 1349)))
            for spec in specs:
                for field, (_, expected) in RATE_FIELDS.items():
                    self.assertEqual(spec["config"]["obss"][field], expected)

    def test_resolved_config_comparison_removes_only_load_and_run_identity(self) -> None:
        original = {
            "run_id": "example",
            "same": 7,
            "background": {
                "obss": {
                    **{
                        field: values[0]
                        for field, values in analysis.RESOLVED_RATE_FIELDS.items()
                    },
                    "same": 9,
                }
            },
        }
        self.assertEqual(
            analysis._config_without_load(original),
            {"same": 7, "background": {"obss": {"same": 9}}},
        )
        self.assertIn("run_id", original)
        self.assertTrue(
            set(analysis.RESOLVED_RATE_FIELDS)
            <= set(original["background"]["obss"])
        )

    def test_primary_copy_diagnostic_reconciles_rescue_outcomes(self) -> None:
        fields = (
            "frame_id",
            "duplicated",
            "deadline_miss",
            "copy_0_completion_us",
            "generation_time_us",
            "deadline_us",
        )
        rows = [
            [0, 0, 0, 50, 0, 100],
            [8, 0, 1, "", 0, 100],
            [9, 1, 0, "", 0, 100],
            [10, 1, 1, 150, 0, 100],
            [11, 1, 0, 50, 0, 100],
        ]
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            with (run_dir / "frames.csv").open(
                "w", newline="", encoding="utf-8"
            ) as stream:
                writer = csv.writer(stream)
                writer.writerow(fields)
                writer.writerows(rows)
            result = analysis._primary_copy_diagnostic(run_dir, 3, 2)
            self.assertEqual(result["primary_copy_deadline_miss_count"], 3)
            self.assertEqual(
                result["acted_primary_copy_deadline_miss_count"], 2
            )
            self.assertEqual(
                result["rescued_primary_copy_deadline_miss_count"], 1
            )
            self.assertEqual(result["residual_acted_deadline_miss_count"], 1)
            self.assertEqual(
                result["nonacted_primary_copy_deadline_miss_count"], 1
            )
            self.assertEqual(result["startup_deadline_miss_count"], 0)
            self.assertEqual(result["steady_deadline_miss_count"], 2)
            self.assertEqual(result["primary_copy_deadline_miss_frame_ids"], (8, 9, 10))

            rows[1][3] = 50
            with (run_dir / "frames.csv").open(
                "w", newline="", encoding="utf-8"
            ) as stream:
                writer = csv.writer(stream)
                writer.writerow(fields)
                writer.writerows(rows)
            with self.assertRaisesRegex(
                analysis.common.AnalysisError,
                "union missed despite an on-time primary copy",
            ):
                analysis._primary_copy_diagnostic(run_dir, 3, 2)

    def test_plotter_accepts_only_the_current_report_schema(self) -> None:
        treatments = {
            profile: {
                arm: {"completed_frame_count": 10}
                for arm in analysis.common.ARM_IDENTITIES
            }
            for profile in analysis.common.PROFILE_SPECS
        }
        series = {
            f"{profile}:{arm}": {
                "profile": profile,
                "arm": arm,
                "completed_frame_count": 10,
            }
            for profile in analysis.common.PROFILE_SPECS
            for arm in analysis.common.ARM_IDENTITIES
        }
        report = {
            "schema_version": analysis.SCHEMA_VERSION,
            "analysis": analysis.ANALYSIS_ID,
            "campaign_checks": {
                "strictly_validated_run_count": 120,
                "freshly_validated_baseline_run_count": 120,
            },
            "treatments": treatments,
            "baseline_treatments": treatments,
        }
        plot_data = {
            "schema_version": 1,
            "analysis": analysis.PLOT_DATA_ID,
            "series": series,
            "baseline_series": series,
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report_path = directory / "report.json"
            plot_path = directory / "plot.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            plot_path.write_text(json.dumps(plot_data), encoding="utf-8")
            loaded, _ = plot._load_inputs(report_path, plot_path)
            self.assertEqual(loaded["schema_version"], analysis.SCHEMA_VERSION)

            report["schema_version"] = analysis.SCHEMA_VERSION - 1
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(
                analysis.common.AnalysisError,
                "plot inputs are not the formal load repeat",
            ):
                plot._load_inputs(report_path, plot_path)


if __name__ == "__main__":
    unittest.main()
