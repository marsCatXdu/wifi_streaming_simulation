from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from analyze_paired_value_t2_str_qualification import QualificationError
from plot_paired_value_t2_str_qualification import (
    _TestOnlyContract,
    generate_plots,
    paired_rows,
    policy_admission_diagnostics,
)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class PairedQualificationPlottingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runs_root = self.root / "runs"
        self.runs_root.mkdir()
        self.test_contract = _TestOnlyContract(((1, 1), (2, 1)))
        self.run_specs = [
            (1, "policy-1", "policy", 50, 120, 900),
            (1, "str-1", "str_mlo", 150, 100, 1000),
            (2, "policy-2", "policy", 200, 80, 1100),
            (2, "str-2", "str_mlo", 100, 100, 1000),
        ]
        aggregate_runs: list[dict[str, object]] = []
        manifest_runs: list[dict[str, object]] = []
        for seed, run_id, arm, latency_us, airtime_us, background_bytes in self.run_specs:
            topology, policy = {
                "policy": ("dual_interface", "paired_value_duplication_t2"),
                "str_mlo": ("mlo_str", "fixed_link_0"),
            }[arm]
            config = {
                "run_id": run_id,
                "seed": seed,
                "run": 1,
                "topology": topology,
                "policy": policy,
                "measurement_start_s": 0,
                "measurement_stop_s": 1,
            }
            run_dir = self.runs_root / run_id
            run_dir.mkdir()
            frames: list[dict[str, object]] = []
            for frame_id in range(100):
                primary_completion: object = frame_id + latency_us
                if arm == "policy" and seed == 1 and frame_id < 4:
                    primary_completion = frame_id + 80
                elif arm == "policy" and seed == 1 and 8 <= frame_id < 10:
                    primary_completion = ""
                frames.append({
                    "run_id": run_id,
                    "frame_id": frame_id,
                    "generation_time_us": frame_id,
                    "deadline_us": 100,
                    "union_latency_us": latency_us,
                    "union_completion_us": frame_id + latency_us,
                    "copy_0_completion_us": primary_completion,
                    "duplicated": int(arm == "policy" and frame_id < 10),
                    "deadline_miss": int(latency_us > 100),
                    "incomplete": 0,
                })
            _write_csv(
                run_dir / "frames.csv",
                list(frames[0]),
                frames,
            )
            _write_csv(
                run_dir / "link_intervals.csv",
                ["link_id", "phy_tx_time_us"],
                [
                    {"link_id": 0, "phy_tx_time_us": airtime_us // 2},
                    {"link_id": 1, "phy_tx_time_us": airtime_us - airtime_us // 2},
                ],
            )
            _write_csv(
                run_dir / "background_flows.csv",
                ["bytes_received"],
                [{"bytes_received": background_bytes}],
            )
            (run_dir / "resolved_config.json").write_text(
                json.dumps(config), encoding="utf-8"
            )
            if arm == "policy":
                decisions: list[dict[str, object]] = []
                for frame_id in range(100):
                    if frame_id < 10:
                        status = "action"
                        evaluated, score_passed, guard_considered = 1, 1, 1
                        guard_admitted, launch_attempted, launched = 1, 1, 1
                        actionable = 1
                        descriptor_checked, descriptor_available = 1, 1
                    elif frame_id < 20:
                        status = "airtime_guard_rejected"
                        evaluated, score_passed, guard_considered = 1, 1, 1
                        guard_admitted, launch_attempted, launched = 0, 0, 0
                        actionable = 1
                        descriptor_checked, descriptor_available = 1, 1
                    elif frame_id < 40:
                        status = "below_score_threshold"
                        evaluated, score_passed, guard_considered = 1, 0, 0
                        guard_admitted, launch_attempted, launched = 0, 0, 0
                        actionable = 1
                        descriptor_checked, descriptor_available = 1, 1
                    elif frame_id == 40:
                        status = "descriptor_unavailable"
                        evaluated, score_passed, guard_considered = 0, "", 0
                        guard_admitted, launch_attempted, launched = 0, 0, 0
                        actionable = 1
                        descriptor_checked, descriptor_available = 1, 0
                    elif frame_id == 41:
                        status = "launch_rejected"
                        evaluated, score_passed, guard_considered = 1, 1, 1
                        guard_admitted, launch_attempted, launched = 1, 1, 0
                        actionable = 1
                        descriptor_checked, descriptor_available = 1, 1
                    else:
                        status = "not_actionable"
                        evaluated, score_passed, guard_considered = 0, "", 0
                        guard_admitted, launch_attempted, launched = 0, 0, 0
                        actionable = 0
                        descriptor_checked, descriptor_available = 0, 0
                    decisions.append(
                        {
                            "run_id": run_id,
                            "frame_id": frame_id,
                            "decision_status": status,
                            "primary_copy_id": 0,
                            "secondary_copy_id": 1,
                            "frame_type": "P_FRAME",
                            "primary_actionable": actionable,
                            "inside_decision_window": 1,
                            "history_ready": 1,
                            "descriptor_checked": descriptor_checked,
                            "descriptor_available": descriptor_available,
                            "feature_evaluated": evaluated,
                            "passes_score_threshold": score_passed,
                            "guard_admission_considered": guard_considered,
                            "guard_admitted": guard_admitted,
                            "launch_attempted": launch_attempted,
                            "secondary_launched": launched,
                        }
                    )
                _write_csv(
                    run_dir / "paired_value_t2_decisions.csv",
                    list(decisions[0]),
                    decisions,
                )
            aggregate_runs.append(
                {
                    "run_id": run_id,
                    "run_dir": str(run_dir),
                    "seed": seed,
                    "run": 1,
                    "topology": topology,
                    "policy": policy,
                    "config": config,
                }
            )
            manifest_runs.append(
                {
                    "run_id": run_id,
                    "seed": seed,
                    "run": 1,
                    "directory": run_id,
                    "status": "complete",
                    "topology": topology,
                    "policy": policy,
                    "config": config,
                }
            )

        self.aggregate_path = self.runs_root / "aggregate.json"
        self.aggregate_path.write_text(
            json.dumps({"runs": aggregate_runs}), encoding="utf-8"
        )
        manifest_path = self.runs_root / "experiment_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "project_commit": "1" * 40,
                    "ns3_upstream_commit": "2" * 40,
                    "runs": manifest_runs,
                }
            ),
            encoding="utf-8",
        )
        manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        self.report_path = self.root / "paired_value_t2_str_qualification.json"
        self.report_path.write_text(
            json.dumps(self._report(manifest_sha)), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _report(manifest_sha: str) -> dict[str, object]:
        treatment = lambda miss, p99, airtime, background: {
            "generated_frame_count": {"total": 200},
            "all_generated_deadline_miss_rate": {"mean": miss},
            "completed_frame_p99_us": {"mean": p99},
            "sender_phy_tx_airtime_us": {"mean": airtime},
            "background_throughput_mbps": {"mean": background},
        }
        interval = lambda estimate, low, high: {
            "estimate": estimate,
            "ci95_low": low,
            "ci95_high": high,
        }
        return {
            "schema_version": 1,
            "analysis": "paired_value_t2_str_qualification",
            "paired_unit_count": 2,
            "paired_units": [{"seed": 1, "run": 1}, {"seed": 2, "run": 1}],
            "campaign_checks": {
                "all_metrics_reconstructed_from_raw_artifacts": True,
                "all_runs_strictly_validated": True,
                "exact_48_paired_units": True,
                "exact_two_declared_arms": True,
                "manifests": [{"sha256": manifest_sha}],
            },
            "treatments": {
                "policy": treatment(0.5, 125.0, 100.0, 0.008),
                "str_mlo": treatment(0.5, 125.0, 100.0, 0.008),
            },
            "comparison_against_str": {
                "all_generated_deadline_miss_rate": {
                    "paired_policy_minus_str": interval(0.0, -0.5, 0.5)
                },
                "completed_frame_p99_us": {
                    "paired_policy_minus_str": interval(0.0, -100.0, 100.0)
                },
                "sender_phy_tx_airtime_ratio": {
                    "estimate": 1.0,
                    "paired_bootstrap": interval(1.0, 0.8, 1.2),
                },
                "background_throughput_loss": {
                    "estimate": 0.0,
                    "paired_bootstrap": interval(0.0, -0.1, 0.1),
                },
            },
            "resource_target_against_str": {
                "criteria": {
                    "sender_airtime_ratio": {
                        "threshold": 1.2,
                        "status": "pass",
                    },
                    "background_throughput_loss": {
                        "threshold": 0.01,
                        "status": "pass",
                    },
                }
            },
        }

    def test_reconstructs_pairs_and_generates_all_outputs(self) -> None:
        rows, report, resolved = paired_rows(
            self.aggregate_path,
            self.report_path,
            _test_contract=self.test_contract,
        )
        self.assertEqual(resolved, self.aggregate_path.resolve())
        self.assertEqual([row["seed"] for row in rows], [1, 2])
        self.assertEqual(rows[0]["miss_delta_percentage_points"], -100.0)
        self.assertEqual(rows[0]["completed_p99_delta_us"], -100.0)
        self.assertEqual(rows[0]["sender_airtime_ratio"], 1.2)
        self.assertAlmostEqual(rows[0]["background_throughput_loss"], 0.1)
        diagnostics = policy_admission_diagnostics(
            resolved, report, _test_contract=self.test_contract
        )
        self.assertEqual(diagnostics["funnel"][0]["count"], 200)
        self.assertEqual(diagnostics["funnel"][-1]["count"], 20)
        self.assertEqual(
            [item["count"] for item in diagnostics["action_outcomes"]],
            [4, 4, 2, 10],
        )
        statuses = {item["status"]: item["count"] for item in diagnostics["statuses"]}
        self.assertEqual(statuses["descriptor_unavailable"], 2)
        self.assertEqual(statuses["launch_rejected"], 2)

        output_directory = self.root / "plots"
        outputs = generate_plots(
            self.aggregate_path,
            report_path=self.report_path,
            output_directory=output_directory,
            _test_contract=self.test_contract,
        )
        self.assertEqual(
            [path.name for path in outputs],
            [
                "paired_metric_deltas.png",
                "resource_gates.png",
                "paired_performance_tradeoff.png",
                "policy_admission_diagnostics.png",
                "policy_admission_diagnostics.json",
                "paired_metrics.csv",
            ],
        )
        self.assertTrue(all(path.is_file() and path.stat().st_size > 0 for path in outputs))
        with outputs[-1].open(newline="", encoding="utf-8") as source:
            csv_rows = list(csv.DictReader(source))
        self.assertEqual([int(row["seed"]) for row in csv_rows], [1, 2])
        self.assertEqual(float(csv_rows[1]["sender_airtime_ratio"]), 0.8)
        diagnostic_document = json.loads(outputs[-2].read_text(encoding="utf-8"))
        self.assertEqual(
            diagnostic_document["evidence_role"],
            "diagnostic_only_not_a_qualification_gate",
        )
        self.assertEqual(
            diagnostic_document["provenance"]["strict_report_sha256"],
            hashlib.sha256(self.report_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            diagnostic_document["provenance"]["manifest_sha256"],
            json.loads(self.report_path.read_text(encoding="utf-8"))[
                "campaign_checks"
            ]["manifests"][0]["sha256"],
        )
        self.assertEqual(
            diagnostic_document["provenance"]["expected_paired_unit_count"], 2
        )
        self.assertEqual(
            diagnostic_document["provenance"]["validated_run_count"], 0
        )
        self.assertFalse(
            diagnostic_document["provenance"][
                "all_runs_freshly_strict_validated"
            ]
        )

    def test_production_contract_rejects_synthetic_pair_count(self) -> None:
        with self.assertRaisesRegex(QualificationError, "frozen 48-unit contract"):
            paired_rows(self.aggregate_path, self.report_path)

    def test_rejects_seed_and_config_reassignment(self) -> None:
        aggregate = json.loads(self.aggregate_path.read_text(encoding="utf-8"))
        for run in aggregate["runs"]:
            if run["policy"] == "paired_value_duplication_t2":
                run["seed"] = 3 - run["seed"]
                run["config"]["seed"] = run["seed"]
        self.aggregate_path.write_text(json.dumps(aggregate), encoding="utf-8")
        with self.assertRaisesRegex(QualificationError, "identity mismatch"):
            paired_rows(
                self.aggregate_path,
                self.report_path,
                _test_contract=self.test_contract,
            )

    def test_rejects_mutated_decision_launch_evidence(self) -> None:
        path = self.runs_root / "policy-1" / "paired_value_t2_decisions.csv"
        with path.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
            columns = list(rows[0])
        row = rows[10]
        self.assertEqual(row["decision_status"], "airtime_guard_rejected")
        row.update(
            {
                "decision_status": "action",
                "guard_admitted": "1",
                "launch_attempted": "1",
                "secondary_launched": "1",
            }
        )
        _write_csv(path, columns, rows)
        report = json.loads(self.report_path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(QualificationError, "frame/action evidence"):
            policy_admission_diagnostics(
                self.aggregate_path,
                report,
                _test_contract=self.test_contract,
            )

    def test_fresh_validation_covers_every_run(self) -> None:
        strict_test_contract = _TestOnlyContract(
            ((1, 1), (2, 1)), skip_strict_run_validation=False
        )

        def fake_validate(run_dir: Path, **kwargs: object) -> dict[str, object]:
            return {"valid": True, "run_id": kwargs["expected_run_id"]}

        with mock.patch(
            "plot_paired_value_t2_str_qualification.validate_run",
            side_effect=fake_validate,
        ) as validator:
            paired_rows(
                self.aggregate_path,
                self.report_path,
                _test_contract=strict_test_contract,
            )
        self.assertEqual(validator.call_count, 4)
        for call in validator.call_args_list:
            self.assertEqual(call.kwargs["expected_project_commit"], "1" * 40)
            self.assertEqual(call.kwargs["expected_ns3_commit"], "2" * 40)

    def test_rejects_report_from_another_manifest(self) -> None:
        report = json.loads(self.report_path.read_text(encoding="utf-8"))
        report["campaign_checks"]["manifests"][0]["sha256"] = "0" * 64
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(
            QualificationError, "not bound to this campaign manifest"
        ):
            paired_rows(
                self.aggregate_path,
                self.report_path,
                _test_contract=self.test_contract,
            )


if __name__ == "__main__":
    unittest.main()
