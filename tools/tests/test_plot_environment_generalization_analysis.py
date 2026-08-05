#!/usr/bin/env python3
"""Focused tests for environment-generalization result plotting."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_environment_generalization_lofo as lofo_analysis  # noqa: E402
import analyze_environment_generalization_policy as policy_analysis  # noqa: E402
import plot_environment_generalization_analysis as plotting  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _manifest(directory: Path, names: set[str]) -> None:
    _write_json(
        directory / "artifact_manifest.json",
        {
            "manifest_schema_version": 1,
            "hash_algorithm": "sha256",
            "artifacts_sha256": {
                name: _sha256(directory / name) for name in names
            },
        },
    )


class PlotEnvironmentGeneralizationAnalysisTest(unittest.TestCase):
    families = (
        "radio_propagation",
        "obss_intensity",
        "obss_geometry_mac",
        "video_workload",
        "legacy_coexistence",
        "compound_shift",
    )

    def _policy_result(self, row_count: int) -> dict[str, object]:
        miss_values = (0.02, 0.015, 0.012, 0.008)
        fractions = (0.0, 0.4167, 0.6667, 1.0)
        policies: dict[str, object] = {}
        contrasts: dict[str, object] = {}
        for index, policy_id in enumerate(policy_analysis.POLICY_ORDER):
            miss = miss_values[index]
            policies[policy_id] = {
                "policy_value": {
                    "deadline_miss": {
                        "estimate": miss,
                        "ci_lower": miss - 0.002,
                        "ci_upper": miss + 0.002,
                    },
                    "completed_late18": {
                        "estimate": 0.03 - index * 0.003,
                        "ci_lower": 0.02,
                        "ci_upper": 0.04,
                    },
                },
                "resource": {
                    "hierarchical_mean_actions_per_run": index * 100.0,
                    "hierarchical_mean_canonical_reservation_us_per_run": (
                        index * 110_000.0
                    ),
                },
                "family_value": {
                    family: {
                        "deadline_miss": miss + family_index * 0.001,
                        "completed_late18": 0.03,
                        "bounded_on_time_late18": 0.01,
                    }
                    for family_index, family in enumerate(self.families)
                },
            }
            contrasts[policy_id] = {
                "fraction_of_oracle_deadline_gain_realized": {
                    "estimate": fractions[index],
                    "ci_lower": fractions[index] - 0.1,
                    "ci_upper": fractions[index] + 0.1,
                }
            }
        return {
            "analysis_id": "environment-generalization-policy-replay-v1",
            "population": {"row_count": row_count},
            "resource": {"budget_us_per_60s_run": 372_000},
            "policies": policies,
            "contrasts_against_resource_oracle": contrasts,
        }

    def _lofo_metrics(self, row_count: int) -> dict[str, object]:
        return {
            "analysis_id": "environment-generalization-lofo-v1",
            "dataset": {"row_count": row_count},
            "diagnostics": {
                "family_metrics": {
                    family: {
                        "control_deadline_miss_auc": 0.75 + index * 0.02,
                        "mean_predicted_deadline_rescue": 0.03 + index * 0.005,
                        "ood": {"fallback_fraction": index * 0.02},
                    }
                    for index, family in enumerate(self.families)
                }
            },
        }

    def test_checksum_closed_inputs_render_all_figures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lofo_dir = root / "lofo"
            policy_dir = root / "policy"
            output_dir = root / "plots"
            lofo_dir.mkdir()
            policy_dir.mkdir()
            row_count = 100
            lofo_metrics = self._lofo_metrics(row_count)
            _write_json(lofo_dir / lofo_analysis.OUTPUT_METRICS, lofo_metrics)
            (lofo_dir / lofo_analysis.OUTPUT_PREDICTIONS).write_bytes(b"predictions")
            _manifest(
                lofo_dir,
                {lofo_analysis.OUTPUT_METRICS, lofo_analysis.OUTPUT_PREDICTIONS},
            )

            policy_metrics = self._policy_result(row_count)
            policy_metrics["provenance"] = {
                "prediction_manifest_sha256": _sha256(
                    lofo_dir / lofo_analysis.OUTPUT_MANIFEST
                )
            }
            _write_json(policy_dir / policy_analysis.OUTPUT_METRICS, policy_metrics)
            (policy_dir / policy_analysis.OUTPUT_REPORT).write_text(
                "report\n", encoding="utf-8"
            )
            (policy_dir / policy_analysis.OUTPUT_FAMILY_CSV).write_text(
                "family\n", encoding="utf-8"
            )
            action_fields = [
                "deadline_rescue_probability",
                "tail18_acceleration_probability",
                "primary_deadline_risk",
                "canonical_reservation_us",
                "ood_fallback",
                *[
                    f"action_probability_{policy_id}"
                    for policy_id in policy_analysis.POLICY_ORDER
                ],
            ]
            with gzip.open(
                policy_dir / policy_analysis.OUTPUT_ACTIONS,
                mode="wt",
                encoding="utf-8",
                newline="",
            ) as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=action_fields, lineterminator="\n"
                )
                writer.writeheader()
                for index in range(row_count):
                    writer.writerow(
                        {
                            "deadline_rescue_probability": index / row_count,
                            "tail18_acceleration_probability": index / 200,
                            "primary_deadline_risk": 1 - index / 200,
                            "canonical_reservation_us": 1000 + index * 10,
                            "ood_fallback": int(index % 10 == 0),
                            **{
                                f"action_probability_{policy_id}": (
                                    1
                                    if policy_id
                                    == "cross_fitted_scenario_resource_oracle_v1"
                                    and index >= row_count // 2
                                    else 0
                                )
                                for policy_id in policy_analysis.POLICY_ORDER
                            },
                        }
                    )
            _manifest(
                policy_dir,
                {
                    policy_analysis.OUTPUT_METRICS,
                    policy_analysis.OUTPUT_REPORT,
                    policy_analysis.OUTPUT_FAMILY_CSV,
                    policy_analysis.OUTPUT_ACTIONS,
                },
            )
            manifest = plotting.plot_all(lofo_dir, policy_dir, output_dir)
            self.assertEqual(
                set(manifest["artifacts_sha256"]), set(plotting.PLOT_FILES)
            )
            for name in plotting.PLOT_FILES:
                path = output_dir / name
                self.assertGreater(path.stat().st_size, 10_000)
                self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
