#!/usr/bin/env python3
"""Synthetic result-fixture checks for the multi-arm T4 campaign analyzer."""

from __future__ import annotations

import copy
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from analyze_primary_tail_t4_campaign import (  # noqa: E402
    CampaignError,
    DECLARED_CAMPAIGN_IDENTITIES,
    DECLARED_FULL_T4_GATES,
    DECLARED_NEUTRAL_ENVIRONMENT,
    DECLARED_SOURCE_ARTIFACTS,
    DECLARED_TOPOLOGY_WIFI,
    Thresholds,
    analyze_campaign,
    render_markdown,
)
from validate_outputs import (  # noqa: E402
    PRIMARY_T0_MODEL_ID,
    PRIMARY_T0_SOURCE_MODEL_SHA256,
    PRIMARY_TARGET_ID,
    PRIMARY_TARGET_PROVENANCE_SHA256,
    PRIMARY_TAIL_T4_COMBINER_SHA256,
    PRIMARY_TAIL_T4_COMPLETED_MODEL_SHA256,
    PRIMARY_TAIL_T4_COMPLETED_TARGET_ID,
    PRIMARY_TAIL_T4_FEATURE_CONTRACT_SHA256,
    PRIMARY_TAIL_T4_MODEL_ID,
    PRIMARY_TAIL_T4_PRIMARY_MODEL_SHA256,
    PRIMARY_TAIL_T4_PRIMARY_TARGET_ID,
    PRIMARY_TAIL_T4_SOURCE_MODEL_SHA256,
    PRIMARY_TAIL_T4_TARGET_PROVENANCE_SHA256,
)
from run_experiments import (  # noqa: E402
    NS3_UPSTREAM_COMMIT,
    derive_run_id,
    expand_config,
    load_yaml,
)


PROJECT_COMMIT = "a" * 40
NS3_COMMIT = NS3_UPSTREAM_COMMIT


def _scorers() -> dict[str, dict[str, Any]]:
    return {
        "T0": {
            "sample_offset_us": 0,
            "score_name": "primary_miss_calibrated_probability",
            "score_kind": "calibrated_primary_miss_probability",
            "model_id": PRIMARY_T0_MODEL_ID,
            "primary_miss_target_id": PRIMARY_TARGET_ID,
            "completed_tail_target_id": "",
            "source_model_sha256": PRIMARY_T0_SOURCE_MODEL_SHA256,
            "target_provenance_sha256": PRIMARY_TARGET_PROVENANCE_SHA256,
            "feature_contract_sha256": "",
            "combiner_sha256": "",
            "primary_miss_model_sha256": "",
            "completed_tail_model_sha256": "",
            "feature_count": 86,
        },
        "T4": {
            "sample_offset_us": 4000,
            "score_name": "admission_score",
            "score_kind": "weighted_head_probability_admission_score",
            "model_id": PRIMARY_TAIL_T4_MODEL_ID,
            "primary_miss_target_id": PRIMARY_TAIL_T4_PRIMARY_TARGET_ID,
            "completed_tail_target_id": PRIMARY_TAIL_T4_COMPLETED_TARGET_ID,
            "source_model_sha256": PRIMARY_TAIL_T4_SOURCE_MODEL_SHA256,
            "target_provenance_sha256": PRIMARY_TAIL_T4_TARGET_PROVENANCE_SHA256,
            "feature_contract_sha256": PRIMARY_TAIL_T4_FEATURE_CONTRACT_SHA256,
            "combiner_sha256": PRIMARY_TAIL_T4_COMBINER_SHA256,
            "primary_miss_model_sha256": PRIMARY_TAIL_T4_PRIMARY_MODEL_SHA256,
            "completed_tail_model_sha256": PRIMARY_TAIL_T4_COMPLETED_MODEL_SHA256,
            "feature_count": 101,
        },
    }


def _adaptive(mechanism: str, t4_price: float) -> dict[str, Any]:
    deficit = mechanism == "primary_deficit"
    return {
        "score_contract": "stage_specific",
        "stage_scorers": _scorers(),
        "admission_feature_set": "stage_specific_compiled",
        "packet_selection_feature_set": (
            "F2-primary-frame-ack-state" if deficit else "none"
        ),
        "packet_selection": (
            "primary_unacknowledged_reverse" if deficit else "full_forward"
        ),
        "degradation_profile": "polling_1ms",
        "configured_admission_packet_cost": (
            "whole_copy" if deficit else "launched_packet_set"
        ),
        "effective_admission_packet_cost": "whole_copy",
        "operating_profile": (
            "primary_unacknowledged+whole_copy_priced"
            if deficit
            else "full_forward+whole_copy_priced"
        ),
        "stages": ["T0", "T4"],
        "primary_path": 1,
        "secondary_path": 0,
        "budget_definition": "secondary_sender_phy_tx_airtime",
        "budget_fraction": 0.02,
        "bucket_horizon_us": 10_000_000,
        "initial_bucket_horizon_us": 2_000_000,
        "initial_bucket_capacity_us": 40_000.0,
        "initial_shadow_price": 0.034,
        "dual_step": 0.0,
        "admission_uses_retry_inflation": False,
        "admission_cost_definition": (
            "nominal_estimated_whole_copy_secondary_sender_phy_tx_airtime"
        ),
        "reservation_cost_definition": (
            "retry_inflated_estimated_launched_packet_set_"
            "secondary_sender_phy_tx_airtime"
        ),
        "cost_safety_factor": 1.25,
        "cost_ewma_alpha": 0.1,
        "decision_offsets_us": [0, 4000],
        "shadow_price_mode": "offset_override_with_global_dual_fallback",
        "decision_offset_shadow_prices": {
            "0": 0.034,
            "4000": float(format(t4_price, ".12g")),
        },
        "i_frame_only_decision_offsets_us": [0],
    }


def _common_config(
    seed: int, run_id: str, topology: str, policy: str
) -> dict[str, Any]:
    environment = copy.deepcopy(DECLARED_NEUTRAL_ENVIRONMENT)
    wifi = environment.pop("shared_target_wifi")
    wifi.update(copy.deepcopy(DECLARED_TOPOLOGY_WIFI[topology]))
    environment["background"]["obss"]["bsses"] = [
        {"seed_realization": seed}
    ]
    return {
        "run_id": run_id,
        "seed": seed,
        "run": 1,
        "topology": topology,
        "policy": policy,
        **environment,
        "wifi": wifi,
    }


def _decision_rows(
    run_id: str, mechanism: str, t4_price: float, frame_count: int
) -> list[dict[str, str]]:
    deficit = mechanism == "primary_deficit"
    adaptive = _adaptive(mechanism, t4_price)
    specs: list[tuple[int, int, str, float]] = []
    for frame_id in range(frame_count):
        specs.append((
            frame_id,
            0,
            "action" if frame_id == 0 else "frame_type_restricted",
            0.8,
        ))
        specs.append((
            frame_id,
            4000,
            (
                "already_resolved"
                if frame_id == 0
                else ("price_rejected" if deficit else "action")
            ),
            0.01 if deficit and frame_id != 0 else 0.8,
        ))
    rows = []
    for frame_id, offset, decision, score in specs:
        stage = f"T{offset // 1000}"
        scorer = adaptive["stage_scorers"][stage]
        absent = decision == "already_resolved"
        admission = 0.0 if absent else 10.0
        if absent:
            estimated, selected, order = 0.0, [], "none"
        elif deficit and offset == 4000:
            estimated, selected, order = 5.0, [1], "primary_unacknowledged_reverse"
        elif deficit:
            estimated, selected, order = 10.0, [1, 0], "primary_unacknowledged_reverse"
        else:
            estimated, selected, order = 10.0, [0, 1], "full_forward"
        shadow = float(adaptive["decision_offset_shadow_prices"][str(offset)])
        normalized = admission / 10.0
        utility = score - shadow * normalized if admission else float("nan")
        rows.append({
            "run_id": run_id,
            "frame_id": str(frame_id),
            "sample_stage": stage,
            "sample_offset_us": str(offset),
            "sample_time_ns": str(frame_id * 33_000_000 + offset * 1000),
            "actionable": "1",
            "admission_score": str(score),
            "score_name": str(scorer["score_name"]),
            "score_kind": str(scorer["score_kind"]),
            "score_model_id": str(scorer["model_id"]),
            "score_source_model_sha256": str(scorer["source_model_sha256"]),
            "score_target_provenance_sha256": str(
                scorer["target_provenance_sha256"]
            ),
            "score_feature_contract_sha256": str(
                scorer["feature_contract_sha256"]
            ),
            "score_combiner_sha256": str(scorer["combiner_sha256"]),
            "primary_miss_probability": str(score),
            "completed_tail_probability": "" if offset == 0 else str(score),
            "admission_airtime_us": str(admission),
            "estimated_airtime_us": str(estimated),
            "admission_packet_count": "0" if absent else "2",
            "configured_admission_packet_cost": str(
                adaptive["configured_admission_packet_cost"]
            ),
            "effective_admission_packet_cost": "whole_copy",
            "reference_airtime_us": "10",
            "shadow_price": str(shadow),
            "dual_shadow_price": "0.034",
            "shadow_price_source": "offset_override",
            "normalized_cost": str(normalized),
            "net_utility": str(utility),
            "airtime_budget_fraction": "0.02",
            "bucket_capacity_us": "200000",
            "bucket_balance_us": "40000",
            "initial_bucket_capacity_us": "40000",
            "reserved_airtime_us": "0",
            "available_airtime_us": "40000",
            "measured_airtime_total_us": "0",
            "decision": decision,
            "secondary_launched": "1" if decision == "action" else "0",
            "frame_packet_count": "2",
            "primary_acked_packets": "0",
            "primary_acked_packet_indices": "",
            "secondary_packet_count": str(len(selected)),
            "secondary_packet_indices": ";".join(map(str, selected)),
            "secondary_packet_order": order,
        })
    return rows


class SyntheticCampaign:
    """Construct the complete declared 12-unit, seven-arm campaign."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.runs: list[dict[str, Any]] = []
        self.directories: dict[tuple[str, int], Path] = {}
        self.manifest_configs: dict[str, dict[str, Any]] = {}
        campaign_path = (
            TOOLS.parent
            / "experiments/configs/closed_loop_primary_tail_t4_campaign_v1.yaml"
        )
        self.declared_specs = expand_config(load_yaml(campaign_path))
        arms: list[tuple[Any, ...]] = [
            ("str", "mlo_str", "fixed_link_0", None, 0.20, 150_000, 1000, 100),
            ("emlsr", "mlo_emlsr", "fixed_link_0", None, 0.20, 140_000, 1020, 100),
        ]
        for index, (arm_name, gate) in enumerate(DECLARED_FULL_T4_GATES.items()):
            label = "balanced" if arm_name == "full_copy_cost_0p0080" else arm_name
            arms.append((
                label,
                "dual_interface",
                "adaptive_airtime_duplication",
                gate,
                0.0,
                100_000 + 2_000 * index,
                1120 + 10 * index,
                99.5,
            ))
        balanced_gate = DECLARED_FULL_T4_GATES["full_copy_cost_0p0080"]
        arms.append((
            "deficit",
            "dual_interface",
            "adaptive_deficit_duplication",
            balanced_gate,
            0.0,
            105_000,
            1100,
            99.5,
        ))
        for seed in range(43, 55):
            for arm in arms:
                self._write_run(seed, *arm)
        self.write_aggregate()

    def _write_run(
        self,
        seed: int,
        label: str,
        topology: str,
        policy: str,
        t4_price: float | None,
        miss: float,
        p99: float,
        airtime: float,
        background: float,
    ) -> None:
        candidates = [
            spec
            for spec in self.declared_specs
            if spec["seed"] == seed
            and spec["run"] == 1
            and spec["config"]["topology"] == topology
            and spec["config"]["policy"] == policy
        ]
        if policy == "adaptive_airtime_duplication":
            assert t4_price is not None
            candidates = [
                spec
                for spec in candidates
                if float(
                    spec["config"]["prediction"][
                        "adaptive_airtime_decision_offset_shadow_prices"
                    ].rsplit(":", 1)[1]
                )
                == t4_price
            ]
        if len(candidates) != 1:
            raise AssertionError(
                f"fixture found {len(candidates)} declared specs for {label}/{seed}"
            )
        manifest_config = candidates[0]["config"]
        run_id = derive_run_id(
            manifest_config,
            seed,
            1,
            NS3_COMMIT,
            PROJECT_COMMIT,
        )
        directory = self.root / run_id
        directory.mkdir()
        config = _common_config(seed, run_id, topology, policy)
        if t4_price is not None:
            mechanism = "primary_deficit" if "deficit" in policy else "full_copy"
            key = (
                "adaptiveDeficitDuplication"
                if mechanism == "primary_deficit"
                else "adaptiveAirtimeDuplication"
            )
            config[key] = _adaptive(mechanism, t4_price)
            config["predictionTelemetry"] = {
                "enabled": True,
                "sample_offsets_us": [0, 4000],
                "history_windows_us": [1000, 5000, 20000],
                "polling_interval_us": 1000,
                "polling_report_delay_us": 1000,
                "polling_schema_version": 1,
                "telemetry_schema_version": 3,
                "event_schema_version": 2,
                "feature_support_mask_version": 2,
                "event_log_enabled": False,
                "oracle_features_enabled": False,
            }
            config["secondaryAirtimeMeter"] = {
                "enabled": True,
                "path_id": 0,
                "copy_id": 1,
                "definition": "secondary_sender_phy_tx_airtime",
            }
        (directory / "resolved_config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        (directory / "build_info.json").write_text(
            json.dumps({
                "ns3_version": "3-dev",
                "ns3_upstream_commit": NS3_COMMIT,
                "project_git_commit": PROJECT_COMMIT,
                "compiler": "gcc 14",
                "build_profile": "optimized",
            }),
            encoding="utf-8",
        )
        self._write_csv(
            directory / "link_intervals.csv",
            [
                {"link_id": 0, "phy_tx_time_us": airtime * 0.4},
                {"link_id": 1, "phy_tx_time_us": airtime * 0.6},
            ],
        )
        frame_count = 5
        miss_count = round(miss * frame_count)
        if miss_count / frame_count != miss:
            raise AssertionError("synthetic miss ratio is not exactly representable")
        frames = [
            {
                "frame_id": frame_id,
                "frame_type": "I_FRAME" if frame_id == 0 else "P_FRAME",
                "packet_count": 2,
                "deadline_miss": "1" if frame_id < miss_count else "0",
                "incomplete": "0",
                "union_latency_us": str(p99),
            }
            for frame_id in range(frame_count)
        ]
        self._write_csv(directory / "frames.csv", frames)
        duration_s = float(config["duration_s"])
        background_bytes = round(background * duration_s * 1e6 / 8.0)
        self._write_csv(
            directory / "background_flows.csv",
            [{"bytes_received": background_bytes}],
        )
        (directory / "summary.json").write_text(
            json.dumps({
                "deadline_miss_ratio": miss,
                "latency_p99_us": p99,
                "background_bytes_received": background_bytes,
                "background_throughput_mbps": background,
            }),
            encoding="utf-8",
        )
        if t4_price is not None:
            mechanism = "primary_deficit" if "deficit" in policy else "full_copy"
            decisions = _decision_rows(run_id, mechanism, t4_price, frame_count)
            self._write_csv(directory / "adaptive_airtime_decisions.csv", decisions)
            estimate = sum(
                float(row["estimated_airtime_us"])
                for row in decisions
                if row["decision"] == "action"
            )
            tagged = 100.0
            (directory / "secondary_airtime_summary.json").write_text(
                json.dumps({
                    "tagged_secondary_tx_airtime_us": tagged,
                    "tagged_secondary_tx_airtime_fraction": tagged / 60_000_000,
                    "measurement_duration_us": 60_000_000,
                    "maximum_budget_debt_us": 0,
                    "estimated_action_airtime_us": estimate,
                    "actual_to_estimated_airtime_ratio": tagged / estimate,
                    "forced_reservation_settlements": 0,
                    "budget_fraction": 0.02,
                    "initial_bucket_capacity_us": 40_000,
                    "finite_run_budget_us": 1_240_000,
                    "budget_excess_us": 0,
                }),
                encoding="utf-8",
            )
        self.runs.append({
            "run_id": run_id,
            "run_dir": str(directory),
            "seed": seed,
            "run": 1,
            "topology": topology,
            "policy": policy,
            "deadline_miss_ratio": miss,
            "latency_p99_us": p99,
            "background_throughput_mbps": background,
            "config": config,
        })
        self.directories[(label, seed)] = directory
        self.manifest_configs[run_id] = copy.deepcopy(manifest_config)

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.DictWriter(output, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def write_aggregate(self) -> None:
        (self.root / "aggregate.json").write_text(
            json.dumps({"schema_version": 1, "runs": self.runs}), encoding="utf-8"
        )
        preflight = bool(self.runs) and all(run["seed"] == 43 for run in self.runs)
        identity = DECLARED_CAMPAIGN_IDENTITIES[preflight]
        manifest_runs = [
            {
                "run_id": run["run_id"],
                "status": "complete",
                "seed": run["seed"],
                "run": run["run"],
                "directory": run["run_id"],
                "config": copy.deepcopy(self.manifest_configs[run["run_id"]]),
                "command": None,
            }
            for run in self.runs
        ]
        (self.root / "experiment_manifest.json").write_text(
            json.dumps({
                "schema_version": 2,
                "experiment": identity["experiment"],
                "matrix_sha256": identity["matrix_sha256"],
                "config_file": str(self.root / identity["config_file"]),
                "project_commit": PROJECT_COMMIT,
                "ns3_upstream_commit": NS3_COMMIT,
                "runs": manifest_runs,
            }),
            encoding="utf-8",
        )

    def edit_decisions(
        self, label: str, seed: int, update: Callable[[list[dict[str, str]]], None]
    ) -> None:
        path = self.directories[(label, seed)] / "adaptive_airtime_decisions.csv"
        with path.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        update(rows)
        self._write_csv(path, rows)

    def edit_config(
        self, label: str, seed: int, update: Callable[[dict[str, Any]], None]
    ) -> None:
        path = self.directories[(label, seed)] / "resolved_config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        update(config)
        path.write_text(json.dumps(config), encoding="utf-8")
        for run in self.runs:
            if run["run_id"] == config["run_id"]:
                run["config"] = copy.deepcopy(config)
        self.write_aggregate()

    def edit_all_configs(self, update: Callable[[dict[str, Any]], None]) -> None:
        for run in self.runs:
            path = Path(run["run_dir"]) / "resolved_config.json"
            config = json.loads(path.read_text(encoding="utf-8"))
            update(config)
            path.write_text(json.dumps(config), encoding="utf-8")
            run["config"] = copy.deepcopy(config)
        self.write_aggregate()


class PrimaryTailT4CampaignAnalysisTest(unittest.TestCase):
    def setUp(self) -> None:
        self.validate_run_patch = mock.patch(
            "analyze_primary_tail_t4_campaign.validate_run",
            side_effect=lambda _run_dir, **kwargs: {
                "valid": True,
                "run_id": kwargs["expected_run_id"],
                "frame_count": 5,
            },
        )
        self.validate_run = self.validate_run_patch.start()
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = SyntheticCampaign(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()
        self.validate_run_patch.stop()

    def analyze(self) -> dict[str, Any]:
        return analyze_campaign(
            self.root, Thresholds(), bootstrap_replicates=200
        )

    def test_report_is_multi_arm_paired_deterministic_and_passing(self) -> None:
        first = self.analyze()
        self.assertEqual(self.validate_run.call_count, 84)
        self.assertEqual(first, self.analyze())
        self.assertEqual(first["paired_unit_count"], 12)
        self.assertEqual(first["campaign_checks"]["full_copy_arm_count"], 4)
        self.assertEqual(first["campaign_checks"]["primary_deficit_arm_count"], 1)
        self.assertTrue(first["campaign_checks"]["all_runs_core_validated"])
        self.assertTrue(
            first["campaign_checks"][
                "all_headline_metrics_validated_against_raw_evidence"
            ]
        )
        self.assertEqual(
            first["campaign_checks"]["accepted_runtime_stage_scorers"], _scorers()
        )
        self.assertEqual(len(first["mechanism_iso_pairs"]), 1)
        iso = first["mechanism_iso_pairs"][0]
        self.assertEqual(
            iso["mechanism_iso_scope"],
            "whole_policy_from_first_intervention_not_t4_only",
        )
        difference = iso["decision_differences"]
        self.assertEqual(
            difference["first_causal_mechanism_divergence_stage_counts"],
            {"T0": 12, "T4": 0},
        )
        self.assertEqual(difference["score_difference_count"], 48)
        self.assertEqual(difference["pre_budget_gate_difference_count"], 48)
        self.assertEqual(difference["action_difference_count"], 48)
        self.assertEqual(len(first["overall_status"]["passing_full_copy_arms"]), 4)
        json.dumps(first, allow_nan=False)
        for baselines in first["comparisons"].values():
            for comparison in baselines.values():
                self.assertEqual(comparison["status"], "pass")
                self.assertEqual(
                    comparison["deadline_miss_ratio"]["strict_win_count"], 12
                )
                self.assertLessEqual(
                    comparison["summed_sender_phy_airtime_ratio"]["ci95_high"],
                    1.20,
                )
                background = comparison["background_throughput_loss_fraction"]
                criterion = comparison["criteria"]["background_throughput_loss"]
                self.assertEqual(criterion["observed"], background["ci95_high"])
        self.assertEqual(
            first["campaign_checks"]["declared_source_artifact_sha256"],
            DECLARED_SOURCE_ARTIFACTS
            | {
                "experiments/configs/closed_loop_primary_tail_t4_campaign_v1.yaml": (
                    DECLARED_CAMPAIGN_IDENTITIES[False]["config_sha256"]
                )
            },
        )
        self.assertIn(
            "Whole-policy full-copy versus deficit ISO audit",
            render_markdown(first),
        )

    def test_rejects_weakened_declared_success_thresholds(self) -> None:
        with self.assertRaisesRegex(CampaignError, "thresholds weaken"):
            analyze_campaign(
                self.root,
                Thresholds(maximum_airtime_ratio=1.21),
                bootstrap_replicates=50,
            )

    def test_rejects_tampered_aggregate_or_raw_frame_headline(self) -> None:
        self.fixture.runs[0]["deadline_miss_ratio"] = 0.21
        self.fixture.write_aggregate()
        with self.assertRaisesRegex(CampaignError, "differs from validated summary"):
            self.analyze()

        self.tearDown()
        self.setUp()
        path = self.fixture.directories[("str", 43)] / "frames.csv"
        with path.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        rows[0]["deadline_miss"] = "0"
        self.fixture._write_csv(path, rows)
        with self.assertRaisesRegex(CampaignError, "reconstructed raw evidence"):
            self.analyze()

    def test_accepts_only_the_declared_frame_csv_p99_quantization_bound(self) -> None:
        directory = self.fixture.directories[("str", 43)]
        summary_path = directory / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["latency_p99_us"] = 149_999.5
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        for run in self.fixture.runs:
            if run["run_id"] == directory.name:
                run["latency_p99_us"] = 149_999.5
        self.fixture.write_aggregate()
        report = self.analyze()
        self.assertEqual(
            report["campaign_checks"][
                "maximum_observed_frame_csv_p99_quantization_delta_us"
            ],
            0.5,
        )

        summary["latency_p99_us"] = 149_998.9
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        for run in self.fixture.runs:
            if run["run_id"] == directory.name:
                run["latency_p99_us"] = 149_998.9
        self.fixture.write_aggregate()
        with self.assertRaisesRegex(CampaignError, "1 us frame-CSV quantization"):
            self.analyze()

    def test_preflight_accepts_only_seed43_with_the_same_declared_arms(self) -> None:
        self.fixture.runs = [run for run in self.fixture.runs if run["seed"] == 43]
        self.fixture.write_aggregate()
        report = analyze_campaign(
            self.root,
            Thresholds(expected_pair_count=1, minimum_strict_wins=1),
            bootstrap_replicates=50,
            preflight=True,
        )
        self.assertTrue(report["preflight"])
        self.assertEqual(report["paired_units"], [{"seed": 43, "run": 1}])
        with self.assertRaisesRegex(CampaignError, "campaign identity mismatch"):
            analyze_campaign(self.root, bootstrap_replicates=50)

    def test_rejects_missing_declared_arm_and_uniform_environment_change(self) -> None:
        removed = {
            self.fixture.directories[("full_copy_cost_0p0120", seed)].name
            for seed in range(43, 55)
        }
        self.fixture.runs = [
            run
            for run in self.fixture.runs
            if run["run_id"] not in removed
        ]
        self.fixture.write_aggregate()
        with self.assertRaisesRegex(CampaignError, "run-id set differs from the declared matrix"):
            self.analyze()

        self.tearDown()
        self.setUp()
        self.fixture.edit_all_configs(
            lambda config: config["background"]["obss"].update({
                "ul_max_rate_mbps": 2.5
            })
        )
        with self.assertRaisesRegex(CampaignError, "environment differs from declared"):
            self.analyze()

    def test_rejects_score_identity_gate_and_packet_selection_corruption(self) -> None:
        self.fixture.edit_decisions(
            "balanced",
            43,
            lambda rows: rows[0].update({"score_model_id": "wrong"}),
        )
        with self.assertRaisesRegex(CampaignError, "score provenance mismatch"):
            self.analyze()

        self.tearDown()
        self.setUp()
        self.fixture.edit_decisions(
            "balanced", 43, lambda rows: rows[3].update({"net_utility": "0.123"})
        )
        with self.assertRaisesRegex(CampaignError, "utility/gate arithmetic"):
            self.analyze()

        self.tearDown()
        self.setUp()
        self.fixture.edit_decisions(
            "balanced",
            43,
            lambda rows: rows[3].update({"admission_packet_count": "1"}),
        )
        with self.assertRaisesRegex(CampaignError, "admission packet count"):
            self.analyze()

    def test_rejects_nearby_undeclared_gate_and_topology_wifi_drift(self) -> None:
        self.fixture.edit_config(
            "balanced",
            43,
            lambda config: config["adaptiveAirtimeDuplication"][
                "decision_offset_shadow_prices"
            ].update({"4000": 0.0458523712806}),
        )
        with self.assertRaisesRegex(CampaignError, "undeclared full-copy T4 gate"):
            self.analyze()

        self.tearDown()
        self.setUp()
        self.fixture.edit_config(
            "str",
            43,
            lambda config: config["wifi"].update({
                "tid_to_link_mapping_ul": "0 1"
            }),
        )
        with self.assertRaisesRegex(CampaignError, "topology-specific Wi-Fi"):
            self.analyze()

    def test_rejects_manifest_identity_or_run_set_drift(self) -> None:
        path = self.root / "experiment_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["matrix_sha256"] = "0" * 64
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(CampaignError, "campaign identity mismatch"):
            self.analyze()

        self.tearDown()
        self.setUp()
        path = self.root / "experiment_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["runs"].pop()
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(CampaignError, "run-id sets differ"):
            self.analyze()

        self.tearDown()
        self.setUp()
        path = self.root / "experiment_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["runs"][0]["config"]["stream"]["fps"] = 29
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(CampaignError, "config differs from the declared matrix"):
            self.analyze()

    def test_rejects_self_consistent_but_wrong_compiled_identity(self) -> None:
        wrong_digest = "f" * 64

        def change_config(config: dict[str, Any]) -> None:
            config["adaptiveAirtimeDuplication"]["stage_scorers"]["T4"][
                "feature_contract_sha256"
            ] = wrong_digest

        def change_decisions(rows: list[dict[str, str]]) -> None:
            for row in rows:
                if row["sample_stage"] == "T4":
                    row["score_feature_contract_sha256"] = wrong_digest

        self.fixture.edit_config("balanced", 43, change_config)
        self.fixture.edit_decisions("balanced", 43, change_decisions)
        with self.assertRaisesRegex(CampaignError, "invalid adaptive airtime provenance"):
            self.analyze()

    def test_rejects_score_or_gate_mismatch_at_iso_causal_boundary(self) -> None:
        def change_boundary_score(rows: list[dict[str, str]]) -> None:
            row = rows[0]
            row["admission_score"] = "0.8000000000005"
            row["primary_miss_probability"] = "0.8000000000005"
            row["net_utility"] = "0.7660000000005"

        self.fixture.edit_decisions("deficit", 43, change_boundary_score)
        with self.assertRaisesRegex(CampaignError, "before causal mechanism divergence"):
            self.analyze()

    def test_rejects_non_i_t0_launch_and_budget_excess(self) -> None:
        def launch_p_frame(rows: list[dict[str, str]]) -> None:
            rows[2]["decision"] = "action"
            rows[2]["secondary_launched"] = "1"

        self.fixture.edit_decisions("balanced", 43, launch_p_frame)
        with self.assertRaisesRegex(CampaignError, "T0 launched a non-I frame"):
            self.analyze()

        self.tearDown()
        self.setUp()
        path = (
            self.fixture.directories[("balanced", 43)]
            / "secondary_airtime_summary.json"
        )
        meter = json.loads(path.read_text(encoding="utf-8"))
        meter.update({
            "tagged_secondary_tx_airtime_us": 1_240_002,
            "tagged_secondary_tx_airtime_fraction": 1_240_002 / 60_000_000,
            "budget_excess_us": 2,
        })
        path.write_text(json.dumps(meter), encoding="utf-8")
        with self.assertRaisesRegex(CampaignError, "exceeds the finite-run budget"):
            self.analyze()

    def test_rejects_missing_pairs_mixed_environment_and_nonfinite_metric(self) -> None:
        balanced_43 = self.fixture.directories[("balanced", 43)].name
        self.fixture.runs = [
            run for run in self.fixture.runs if run["run_id"] != balanced_43
        ]
        self.fixture.write_aggregate()
        with self.assertRaisesRegex(CampaignError, "run-id set differs from the declared matrix"):
            self.analyze()

        self.tearDown()
        self.setUp()
        self.fixture.edit_config(
            "balanced",
            43,
            lambda config: config["propagation"].update({"random_stream_base": 5001}),
        )
        with self.assertRaisesRegex(CampaignError, "environment differs from declared"):
            self.analyze()

        self.tearDown()
        self.setUp()
        self.fixture.runs[0]["deadline_miss_ratio"] = float("nan")
        self.fixture.write_aggregate()
        with self.assertRaisesRegex(CampaignError, "finite number"):
            self.analyze()

    def test_rejects_duplicate_pairs_and_mixed_build_identity(self) -> None:
        self.fixture.runs.append(copy.deepcopy(self.fixture.runs[0]))
        self.fixture.write_aggregate()
        with self.assertRaisesRegex(CampaignError, "duplicate aggregate run_id"):
            self.analyze()

        self.tearDown()
        self.setUp()
        path = self.fixture.directories[("balanced", 43)] / "build_info.json"
        build = json.loads(path.read_text(encoding="utf-8"))
        build["compiler"] = "clang 19"
        path.write_text(json.dumps(build), encoding="utf-8")
        with self.assertRaisesRegex(CampaignError, "mixes build identities"):
            self.analyze()

    def test_rejects_nominal_drift_and_non_iso_deficit(self) -> None:
        self.fixture.edit_config(
            "balanced",
            43,
            lambda config: config["predictionTelemetry"].update({
                "oracle_features_enabled": True
            }),
        )
        with self.assertRaisesRegex(CampaignError, "causal T0/T4 polling"):
            self.analyze()

        self.tearDown()
        self.setUp()
        self.fixture.edit_config(
            "deficit",
            43,
            lambda config: config["adaptiveDeficitDuplication"].update({
                "cost_ewma_alpha": 0.2
            }),
        )
        with self.assertRaisesRegex(CampaignError, "differs from declared campaign"):
            self.analyze()

        self.tearDown()
        self.setUp()
        for seed in range(43, 55):
            self.fixture.edit_config(
                "deficit",
                seed,
                lambda config: config.update({"packet_event_logs_enabled": True}),
            )
        with self.assertRaisesRegex(CampaignError, "full/deficit ISO configuration mismatch"):
            self.analyze()


if __name__ == "__main__":
    unittest.main()
