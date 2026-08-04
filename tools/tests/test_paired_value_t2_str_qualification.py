#!/usr/bin/env python3
"""Focused tests for strict paired-value T2 qualification against STR MLO."""

from __future__ import annotations

import contextlib
import copy
import csv
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_paired_value_t2_str_qualification as qualification


PROJECT_COMMIT = "a" * 40


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _strict_validation_stub(
    run_dir: Path,
    expected_run_id: str | None = None,
    expected_project_commit: str | None = None,
    expected_ns3_commit: str | None = None,
) -> dict[str, object]:
    del run_dir
    if expected_project_commit != PROJECT_COMMIT:
        raise AssertionError("analyzer did not pass the manifest project commit")
    if expected_ns3_commit != qualification.NS3_UPSTREAM_COMMIT:
        raise AssertionError("analyzer did not pass the manifest ns-3 commit")
    return {"valid": True, "run_id": expected_run_id, "frame_count": 120}


class SyntheticCampaign:
    """One exact 48-pair raw campaign with deliberately simple outcomes."""

    def __init__(
        self,
        root: Path,
        profile: qualification.QualificationProfile = qualification.V1_PROFILE,
    ) -> None:
        self.root = root
        self.profile = profile
        self.seeds = [seed for seed, _ in profile.expected_seed_run_units]
        self.config_path = root / "synthetic_str_qualification.yaml"
        self.aggregate_path = root / "aggregate.json"
        self.manifest_path = root / "experiment_manifest.json"
        self.run_dirs: dict[tuple[str, int], Path] = {}
        self.aggregate_runs: dict[tuple[str, int], dict[str, object]] = {}
        self.manifest_runs: dict[tuple[str, int], dict[str, object]] = {}
        self._create()

    def _matrix_document(self) -> dict[str, object]:
        return {
            "name": f"synthetic-paired-value-t2-str-qualification-{self.profile.key}",
            "runtime_contract": {
                "id": self.profile.runtime_contract_id,
                "path": str(
                    self.profile.runtime_contract_path.relative_to(
                        qualification.REPOSITORY_ROOT
                    )
                ),
                "sha256": self.profile.runtime_contract_sha256,
                "source_artifacts": qualification.SOURCE_ARTIFACTS,
            },
            "base": {"fixture_contract": "v1"},
            "seeds": self.seeds,
            "runs": [1],
            "topologies": [
                {"name": "dual_interface"},
                {"name": "mlo_str"},
            ],
            "policies": [
                {
                    "name": qualification.POLICY_NAME,
                    "topologies": ["dual_interface"],
                },
                {"name": "fixed_link_0", "topologies": ["mlo_str"]},
            ],
        }

    def _resolved_config(
        self,
        arm: str,
        seed: int,
        run_id: str,
        neutral: dict[str, object],
        topology_wifi: dict[str, object],
    ) -> dict[str, object]:
        topology, policy = qualification.ARM_IDENTITIES[arm]
        environment = copy.deepcopy(neutral)
        shared_wifi = environment.pop("shared_target_wifi")
        background = environment["background"]
        assert isinstance(background, dict)
        obss = background["obss"]
        assert isinstance(obss, dict)
        obss["bsses"] = [
            {"bss_id": index, "seeded_geometry_token": seed * 10 + index}
            for index in range(4)
        ]
        wifi = {**shared_wifi, **copy.deepcopy(topology_wifi[topology])}
        config: dict[str, object] = {
            **environment,
            "run_id": run_id,
            "seed": seed,
            "run": 1,
            "topology": topology,
            "policy": policy,
            "wifi": wifi,
        }
        if arm == "policy":
            config["environment"] = "unchanged_neutral_mixed4x4"
            config["pairedValueDuplicationT2"] = {
                "runtime_contract_id": self.profile.runtime_contract_id,
                "runtime_contract_sha256": self.profile.runtime_contract_sha256,
            }
        return config

    @staticmethod
    def _frame_rows(run_id: str, arm: str) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for frame_id in range(120):
            if arm == "policy":
                latency = 35_000 if frame_id < 2 else 10_000
            else:
                latency = 40_000 if frame_id < 4 else 20_000
            rows.append({
                "run_id": run_id,
                "frame_id": frame_id,
                "generation_time_us": 1_000_000 + frame_id * 33_333,
                "deadline_us": 33_333,
                "union_latency_us": latency,
                "deadline_miss": int(latency > 33_333),
                "incomplete": 0,
            })
        return rows

    def _default_airtime(self, arm: str, seed: int) -> int:
        offset = seed - self.seeds[0]
        return (
            110_000 + 250 * offset
            if arm == "policy"
            else 100_000 + 100 * offset
        )

    def _default_background_bytes(self, arm: str, seed: int) -> int:
        offset = seed - self.seeds[0]
        return (
            995_000 + 900 * offset
            if arm == "policy"
            else 1_000_000 + 1_000 * offset
        )

    def _write_run(self, run_dir: Path, config: dict[str, object], arm: str) -> None:
        run_dir.mkdir()
        run_id = str(config["run_id"])
        _write_json(run_dir / "resolved_config.json", config)
        _write_json(
            run_dir / "build_info.json",
            {
                "ns3_version": "ns-3-dev",
                "ns3_upstream_commit": qualification.NS3_UPSTREAM_COMMIT,
                "project_git_commit": PROJECT_COMMIT,
                "compiler": "synthetic-c++",
                "build_profile": "release",
            },
        )
        _write_csv(
            run_dir / "frames.csv",
            [
                "run_id",
                "frame_id",
                "generation_time_us",
                "deadline_us",
                "union_latency_us",
                "deadline_miss",
                "incomplete",
            ],
            self._frame_rows(run_id, arm),
        )
        seed = int(config["seed"])
        total_airtime = self._default_airtime(arm, seed)
        _write_csv(
            run_dir / "link_intervals.csv",
            ["link_id", "phy_tx_time_us"],
            [
                {"link_id": 0, "phy_tx_time_us": total_airtime // 2},
                {"link_id": 1, "phy_tx_time_us": total_airtime // 2},
            ],
        )
        background_bytes = self._default_background_bytes(arm, seed)
        _write_csv(
            run_dir / "background_flows.csv",
            ["bytes_received"],
            [{"bytes_received": background_bytes}],
        )
        for name in qualification.REQUIRED_RUN_ARTIFACTS - {
            "resolved_config.json",
            "build_info.json",
            "frames.csv",
            "link_intervals.csv",
            "background_flows.csv",
        }:
            (run_dir / name).write_text("", encoding="utf-8")
        if arm == "policy":
            for name in qualification.POLICY_RUN_ARTIFACTS - {"paired_value_t2_summary.json"}:
                (run_dir / name).write_text("", encoding="utf-8")
            _write_json(
                run_dir / "paired_value_t2_summary.json",
                {
                    "run_id": run_id,
                    "policy": qualification.POLICY_NAME,
                    "runtime_contract_id": self.profile.runtime_contract_id,
                    "runtime_contract_sha256": self.profile.runtime_contract_sha256,
                    "source_artifacts": qualification.SOURCE_ARTIFACTS,
                },
            )

    def _create(self) -> None:
        document = self._matrix_document()
        _write_json(self.config_path, document)
        neutral, topology_wifi = qualification._load_neutral_declarations()
        declared_specs = qualification._expand_config(document)
        aggregate_runs: list[dict[str, object]] = []
        manifest_runs: list[dict[str, object]] = []
        for spec in declared_specs:
            spec_config = spec["config"]
            identity = (spec_config["topology"], spec_config["policy"])
            arm = next(
                name
                for name, expected in qualification.ARM_IDENTITIES.items()
                if identity == expected
            )
            seed = int(spec["seed"])
            run_id = qualification._derive_run_id(
                spec_config,
                seed,
                int(spec["run"]),
                qualification.NS3_UPSTREAM_COMMIT,
                PROJECT_COMMIT,
                {
                    "runtime_contract_id": self.profile.runtime_contract_id,
                    "runtime_contract_sha256": self.profile.runtime_contract_sha256,
                    "source_artifacts": qualification.SOURCE_ARTIFACTS,
                },
            )
            resolved = self._resolved_config(
                arm, seed, run_id, neutral, topology_wifi
            )
            run_dir = self.root / run_id
            self._write_run(run_dir, resolved, arm)
            aggregate_run: dict[str, object] = {
                "run_id": run_id,
                "run_dir": str(run_dir),
                "seed": seed,
                "run": 1,
                "topology": identity[0],
                "policy": identity[1],
                "config": resolved,
                # These deliberately wrong values prove that the analyzer uses raw files.
                "deadline_miss_ratio": 0.999,
                "latency_p99_us": 1,
                "background_throughput_mbps": 0,
            }
            manifest_run: dict[str, object] = {
                "run_id": run_id,
                "status": "complete",
                "seed": seed,
                "run": 1,
                "directory": run_id,
                "config": spec_config,
                "command": None,
            }
            aggregate_runs.append(aggregate_run)
            manifest_runs.append(manifest_run)
            self.run_dirs[(arm, seed)] = run_dir
            self.aggregate_runs[(arm, seed)] = aggregate_run
            self.manifest_runs[(arm, seed)] = manifest_run
        _write_json(
            self.aggregate_path,
            {"schema_version": 1, "runs": aggregate_runs, "groups": []},
        )
        _write_json(
            self.manifest_path,
            {
                "schema_version": qualification.MANIFEST_SCHEMA_VERSION,
                "experiment": document["name"],
                "matrix_sha256": qualification._sha256_json(document),
                "config_file": str(self.config_path),
                "project_commit": PROJECT_COMMIT,
                "ns3_upstream_commit": qualification.NS3_UPSTREAM_COMMIT,
                "runtime_contract_id": self.profile.runtime_contract_id,
                "runtime_contract_sha256": self.profile.runtime_contract_sha256,
                "source_artifacts": qualification.SOURCE_ARTIFACTS,
                "runs": manifest_runs,
            },
        )

    def rewrite_frames(
        self,
        arm: str,
        *,
        policy_worse: bool = False,
        incomplete_count: int = 0,
    ) -> None:
        for seed in self.seeds:
            run_dir = self.run_dirs[(arm, seed)]
            run_id = str(self.aggregate_runs[(arm, seed)]["run_id"])
            rows = self._frame_rows(run_id, arm)
            if policy_worse:
                for row in rows:
                    frame_id = int(row["frame_id"])
                    latency = 45_000 if frame_id < 8 else 25_000
                    row["union_latency_us"] = latency
                    row["deadline_miss"] = int(latency > 33_333)
            if incomplete_count:
                for row in rows[-incomplete_count:]:
                    row["union_latency_us"] = ""
                    row["deadline_miss"] = 1
                    row["incomplete"] = 1
            _write_csv(
                run_dir / "frames.csv",
                [
                    "run_id",
                    "frame_id",
                    "generation_time_us",
                    "deadline_us",
                    "union_latency_us",
                    "deadline_miss",
                    "incomplete",
                ],
                rows,
            )

    def rewrite_airtime(self, arm: str, total_airtime: int) -> None:
        for seed in self.seeds:
            _write_csv(
                self.run_dirs[(arm, seed)] / "link_intervals.csv",
                ["link_id", "phy_tx_time_us"],
                [
                    {"link_id": 0, "phy_tx_time_us": total_airtime // 2},
                    {"link_id": 1, "phy_tx_time_us": total_airtime - total_airtime // 2},
                ],
            )

    def rewrite_background(self, arm: str, received_bytes: int) -> None:
        for seed in self.seeds:
            _write_csv(
                self.run_dirs[(arm, seed)] / "background_flows.csv",
                ["bytes_received"],
                [{"bytes_received": received_bytes}],
            )

    def restore_resources(self) -> None:
        for arm in qualification.ARM_IDENTITIES:
            for seed in self.seeds:
                total_airtime = self._default_airtime(arm, seed)
                _write_csv(
                    self.run_dirs[(arm, seed)] / "link_intervals.csv",
                    ["link_id", "phy_tx_time_us"],
                    [
                        {"link_id": 0, "phy_tx_time_us": total_airtime // 2},
                        {
                            "link_id": 1,
                            "phy_tx_time_us": total_airtime - total_airtime // 2,
                        },
                    ],
                )
                _write_csv(
                    self.run_dirs[(arm, seed)] / "background_flows.csv",
                    ["bytes_received"],
                    [{"bytes_received": self._default_background_bytes(arm, seed)}],
                )


class PairedValueT2StrQualificationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.campaign = SyntheticCampaign(Path(cls.temporary.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def setUp(self) -> None:
        # Restore the three mutable metric artifacts to the passing fixture.
        self.campaign.rewrite_frames("policy")
        self.campaign.rewrite_frames("str_mlo")
        self.campaign.restore_resources()

    def analyze(self) -> dict[str, object]:
        with mock.patch.object(
            qualification, "validate_run", side_effect=_strict_validation_stub
        ) as validator:
            report = qualification.analyze_campaign(self.campaign.root)
        self.assertEqual(validator.call_count, qualification.EXPECTED_RUN_COUNT)
        return report

    def assert_qualification_fails_before_inference(self, pattern: str) -> None:
        """Assert evidence rejection occurs before resampling or gate construction."""
        with mock.patch.object(
            qualification, "validate_run", side_effect=_strict_validation_stub
        ), mock.patch.object(
            qualification, "_bootstrap_index_matrix"
        ) as matrix_factory, mock.patch.object(
            qualification, "_paired_bootstrap"
        ) as bootstrap, mock.patch.object(
            qualification, "_criterion"
        ) as criterion, self.assertRaisesRegex(
            qualification.QualificationError, pattern
        ):
            qualification.analyze_campaign(self.campaign.root)
        matrix_factory.assert_not_called()
        bootstrap.assert_not_called()
        criterion.assert_not_called()

    def test_valid_raw_fixture_passes_all_three_statuses(self) -> None:
        report = self.analyze()
        self.assertEqual(report["paired_unit_count"], 48)
        self.assertNotIn("qualification_profile", report)
        self.assertEqual(report["evidence_role"], "engineering_qualification")
        self.assertFalse(report["confirmation_eligibility"]["eligible"])
        self.assertFalse(report["confirmation_eligibility"]["reserved_units_used"])
        self.assertEqual(
            [unit["seed"] for unit in report["paired_units"]],
            list(range(1201, 1249)),
        )
        self.assertEqual(report["performance_victory_against_str"]["status"], "pass")
        self.assertEqual(report["resource_target_against_str"]["status"], "pass")
        self.assertEqual(report["overall"]["status"], "pass")
        policy = report["treatments"]["policy"]
        baseline = report["treatments"]["str_mlo"]
        self.assertAlmostEqual(policy["all_generated_deadline_miss_rate"]["mean"], 2 / 120)
        self.assertAlmostEqual(baseline["all_generated_deadline_miss_rate"]["mean"], 4 / 120)
        # Two late completions intentionally raise the policy P99 above its ordinary 10 ms.
        self.assertAlmostEqual(policy["completed_frame_p99_us"]["mean"], 30_250.0)
        self.assertAlmostEqual(baseline["completed_frame_p99_us"]["mean"], 40_000.0)
        airtime = report["comparison_against_str"]["sender_phy_tx_airtime_ratio"]
        background = report["comparison_against_str"]["background_throughput_loss"]
        self.assertAlmostEqual(
            airtime["paired_bootstrap"]["ci95_low"],
            1.1269461811940664,
            places=15,
        )
        self.assertAlmostEqual(
            airtime["paired_bootstrap"]["ci95_high"],
            1.137497212140873,
            places=15,
        )
        self.assertAlmostEqual(
            background["paired_bootstrap"]["ci95_low"],
            0.0068284908667402,
            places=15,
        )
        self.assertAlmostEqual(
            background["paired_bootstrap"]["ci95_high"],
            0.007544453680987773,
            places=15,
        )
        self.assertNotIn("emlsr", json.dumps(report).lower())

    def test_run_id_has_runner_frozen_vector(self) -> None:
        config = {
            "policy": "paired_value_duplication_t2",
            "prediction": {"prediction_telemetry_enabled": True},
            "topology": "dual_interface",
        }
        contract = {
            "runtime_contract_id": "fixture-runtime-v1",
            "runtime_contract_sha256": "0123456789abcdef" * 4,
            "source_artifacts": {
                "model": {
                    "path": "model.bin",
                    "sha256": "fedcba9876543210" * 4,
                }
            },
        }
        self.assertEqual(
            qualification._derive_run_id(
                config,
                1201,
                1,
                "ns3-commit",
                "project-commit",
                contract,
            ),
            "e0b8aa0e0f2204d22748",
        )

    def test_actual_qualification_matrix_matches_runner_expansion_and_ids(self) -> None:
        import run_experiments as runner

        path = (
            qualification.REPOSITORY_ROOT
            / "experiments/configs/paired_value_t2_str_qualification_v1.yaml"
        )
        runner_document = runner.load_yaml(path)
        analyzer_document = qualification._load_yaml(path)
        self.assertEqual(
            qualification._canonical_json(analyzer_document),
            qualification._canonical_json(runner_document),
        )
        runner_specs = runner.expand_config(runner_document)
        analyzer_specs = qualification._expand_config(analyzer_document)
        self.assertEqual(len(runner_specs), qualification.EXPECTED_RUN_COUNT)
        self.assertEqual(
            qualification._canonical_json(analyzer_specs),
            qualification._canonical_json(runner_specs),
        )
        runtime_contract = runner.validate_runtime_contract(runner_document)
        self.assertIsNotNone(runtime_contract)
        analyzer_ids = [
            qualification._derive_run_id(
                spec["config"],
                spec["seed"],
                spec["run"],
                qualification.NS3_UPSTREAM_COMMIT,
                PROJECT_COMMIT,
                runtime_contract,
            )
            for spec in analyzer_specs
        ]
        runner_ids = [
            runner.derive_run_id(
                spec["config"],
                spec["seed"],
                spec["run"],
                qualification.NS3_UPSTREAM_COMMIT,
                PROJECT_COMMIT,
                runtime_contract,
            )
            for spec in runner_specs
        ]
        self.assertEqual(analyzer_ids, runner_ids)
        self.assertEqual(len(set(analyzer_ids)), qualification.EXPECTED_RUN_COUNT)

    def test_score_aware_profile_validates_fresh_engineering_boundary(self) -> None:
        profile = qualification.SCORE_AWARE_V2_PROFILE
        matrix_path = (
            qualification.REPOSITORY_ROOT
            / "experiments/configs/paired_value_t2_score_aware_str_engineering_v2.yaml"
        )
        document = qualification._load_yaml(matrix_path)
        specs = qualification._expand_config(document)
        self.assertEqual(len(specs), qualification.EXPECTED_RUN_COUNT)
        self.assertEqual(
            sorted({(spec["seed"], spec["run"]) for spec in specs}),
            list(profile.expected_seed_run_units),
        )
        self.assertEqual(
            document["runtime_contract"],
            {
                "id": profile.runtime_contract_id,
                "path": str(
                    profile.runtime_contract_path.relative_to(
                        qualification.REPOSITORY_ROOT
                    )
                ),
                "sha256": profile.runtime_contract_sha256,
                "source_artifacts": qualification.SOURCE_ARTIFACTS,
            },
        )

        with tempfile.TemporaryDirectory() as temporary:
            campaign = SyntheticCampaign(Path(temporary), profile)
            with mock.patch.object(
                qualification, "validate_run", side_effect=_strict_validation_stub
            ) as validator:
                report = qualification.analyze_campaign(campaign.root, profile)
        self.assertEqual(validator.call_count, qualification.EXPECTED_RUN_COUNT)
        self.assertEqual(report["qualification_profile"], profile.key)
        self.assertEqual(report["analysis"], profile.analysis_id)
        self.assertEqual(report["paired_unit_count"], qualification.EXPECTED_PAIR_COUNT)
        self.assertEqual(
            [unit["seed"] for unit in report["paired_units"]],
            list(range(1251, 1299)),
        )
        self.assertEqual(report["treatments"]["policy"]["label"], profile.policy_label)
        self.assertEqual(
            report["source_closure"]["runtime_contract"]["sha256"],
            profile.runtime_contract_sha256,
        )
        markdown = qualification.render_markdown(report)
        self.assertIn(f"# {profile.markdown_title}", markdown)
        self.assertIn(f"| Metric | {profile.policy_label} | STR MLO |", markdown)

    def test_full_horizon_profile_reuses_opened_engineering_boundary(self) -> None:
        profile = qualification.FULL_HORIZON_V3_PROFILE
        matrix_path = (
            qualification.REPOSITORY_ROOT
            / "experiments/configs/paired_value_t2_full_horizon_str_engineering_v3.yaml"
        )
        document = qualification._load_yaml(matrix_path)
        specs = qualification._expand_config(document)
        self.assertEqual(len(specs), qualification.EXPECTED_RUN_COUNT)
        self.assertEqual(
            sorted({(spec["seed"], spec["run"]) for spec in specs}),
            list(profile.expected_seed_run_units),
        )
        self.assertEqual(
            document["runtime_contract"],
            {
                "id": profile.runtime_contract_id,
                "path": str(
                    profile.runtime_contract_path.relative_to(
                        qualification.REPOSITORY_ROOT
                    )
                ),
                "sha256": profile.runtime_contract_sha256,
                "source_artifacts": qualification.SOURCE_ARTIFACTS,
            },
        )

        with tempfile.TemporaryDirectory() as temporary:
            campaign = SyntheticCampaign(Path(temporary), profile)
            with mock.patch.object(
                qualification, "validate_run", side_effect=_strict_validation_stub
            ) as validator:
                report = qualification.analyze_campaign(campaign.root, profile)
        self.assertEqual(validator.call_count, qualification.EXPECTED_RUN_COUNT)
        self.assertEqual(report["qualification_profile"], profile.key)
        self.assertEqual(report["analysis"], profile.analysis_id)
        self.assertEqual(report["paired_unit_count"], qualification.EXPECTED_PAIR_COUNT)
        self.assertEqual(
            [unit["seed"] for unit in report["paired_units"]],
            list(range(1251, 1299)),
        )
        self.assertEqual(report["treatments"]["policy"]["label"], profile.policy_label)
        self.assertEqual(
            report["source_closure"]["runtime_contract"]["sha256"],
            profile.runtime_contract_sha256,
        )
        markdown = qualification.render_markdown(report)
        self.assertIn(f"# {profile.markdown_title}", markdown)
        self.assertIn(f"| Metric | {profile.policy_label} | STR MLO |", markdown)

    def test_remaining_refill_profile_reuses_opened_engineering_boundary(self) -> None:
        profile = qualification.REMAINING_REFILL_V4_PROFILE
        matrix_path = (
            qualification.REPOSITORY_ROOT
            / "experiments/configs/paired_value_t2_remaining_refill_str_engineering_v4.yaml"
        )
        document = qualification._load_yaml(matrix_path)
        specs = qualification._expand_config(document)
        self.assertEqual(len(specs), qualification.EXPECTED_RUN_COUNT)
        self.assertEqual(
            sorted({(spec["seed"], spec["run"]) for spec in specs}),
            list(profile.expected_seed_run_units),
        )
        self.assertEqual(
            document["runtime_contract"],
            {
                "id": profile.runtime_contract_id,
                "path": str(
                    profile.runtime_contract_path.relative_to(
                        qualification.REPOSITORY_ROOT
                    )
                ),
                "sha256": profile.runtime_contract_sha256,
                "source_artifacts": qualification.SOURCE_ARTIFACTS,
            },
        )

        with tempfile.TemporaryDirectory() as temporary:
            campaign = SyntheticCampaign(Path(temporary), profile)
            with mock.patch.object(
                qualification, "validate_run", side_effect=_strict_validation_stub
            ) as validator:
                report = qualification.analyze_campaign(campaign.root, profile)
        self.assertEqual(validator.call_count, qualification.EXPECTED_RUN_COUNT)
        self.assertEqual(report["qualification_profile"], profile.key)
        self.assertEqual(report["analysis"], profile.analysis_id)
        self.assertEqual(report["paired_unit_count"], qualification.EXPECTED_PAIR_COUNT)
        self.assertEqual(
            [unit["seed"] for unit in report["paired_units"]],
            list(range(1251, 1299)),
        )
        self.assertEqual(report["treatments"]["policy"]["label"], profile.policy_label)
        self.assertEqual(
            report["source_closure"]["runtime_contract"]["sha256"],
            profile.runtime_contract_sha256,
        )
        markdown = qualification.render_markdown(report)
        self.assertIn(f"# {profile.markdown_title}", markdown)
        self.assertIn(f"| Metric | {profile.policy_label} | STR MLO |", markdown)

    def test_runner_built_manifest_rejects_count_membership_and_entry_drift(self) -> None:
        import run_experiments as runner

        document = qualification._load_yaml(self.campaign.config_path)
        runtime_contract = {
            "runtime_contract_id": qualification.RUNTIME_CONTRACT_ID,
            "runtime_contract_sha256": qualification.RUNTIME_CONTRACT_SHA256,
            "source_artifacts": qualification.SOURCE_ARTIFACTS,
        }
        specs: list[dict[str, object]] = []
        for spec in qualification._expand_config(document):
            run_id = qualification._derive_run_id(
                spec["config"],
                spec["seed"],
                spec["run"],
                qualification.NS3_UPSTREAM_COMMIT,
                PROJECT_COMMIT,
                runtime_contract,
            )
            specs.append({**spec, "run_id": run_id, "completed": True})
        canonical = runner.build_experiment_manifest(
            str(document["name"]),
            runner.matrix_sha256(document),
            self.campaign.config_path,
            PROJECT_COMMIT,
            specs,
            runtime_contract,
        )
        original = self.campaign.manifest_path.read_bytes()
        self.addCleanup(self.campaign.manifest_path.write_bytes, original)
        self.assertEqual(
            qualification._canonical_json(canonical),
            qualification._canonical_json(json.loads(original)),
        )

        def replace_membership(manifest: dict[str, object]) -> None:
            runs = manifest["runs"]
            assert isinstance(runs, list)
            runs[0]["run_id"] = "f" * 20
            runs[0]["directory"] = "f" * 20

        def change_status(manifest: dict[str, object]) -> None:
            manifest["runs"][0]["status"] = "running"

        def change_directory(manifest: dict[str, object]) -> None:
            manifest["runs"][0]["directory"] = "wrong-directory"

        def change_config(manifest: dict[str, object]) -> None:
            manifest["runs"][0]["config"]["fixture_contract"] = "v2"

        mutations = (
            ("wrong count", lambda value: value["runs"].pop(), "manifest, aggregate"),
            ("wrong membership", replace_membership, "manifest, aggregate"),
            ("status drift", change_status, "not canonical/complete"),
            ("directory drift", change_directory, "not canonical/complete"),
            ("config drift", change_config, "identity mismatch"),
        )
        for label, mutate, expected in mutations:
            with self.subTest(label=label):
                drifted = copy.deepcopy(canonical)
                mutate(drifted)
                _write_json(self.campaign.manifest_path, drifted)
                self.assert_qualification_fails_before_inference(expected)

    def test_near_matrix_count_and_membership_drift_fail_before_inference(self) -> None:
        config_original = self.campaign.config_path.read_bytes()
        manifest_original = self.campaign.manifest_path.read_bytes()
        self.addCleanup(self.campaign.config_path.write_bytes, config_original)
        self.addCleanup(self.campaign.manifest_path.write_bytes, manifest_original)
        original_document = json.loads(config_original)
        original_manifest = json.loads(manifest_original)
        mutations = (
            ("wrong count", list(range(1201, 1248))),
            ("wrong membership", [*range(1201, 1248), 1301]),
        )
        for label, seeds in mutations:
            with self.subTest(label=label):
                document = copy.deepcopy(original_document)
                document["seeds"] = seeds
                manifest = copy.deepcopy(original_manifest)
                manifest["matrix_sha256"] = qualification._sha256_json(document)
                _write_json(self.campaign.config_path, document)
                _write_json(self.campaign.manifest_path, manifest)
                self.assert_qualification_fails_before_inference(
                    "manifest, aggregate, and matrix differ"
                )

    def test_one_shared_bootstrap_matrix_is_deterministic(self) -> None:
        original_bootstrap = qualification._paired_bootstrap
        matrix_ids: list[int] = []
        statistic_descriptions: list[str] = []

        def recording_bootstrap(
            policy: list[float],
            baseline: list[float],
            indexes: tuple[tuple[int, ...], ...],
            statistic: object,
            statistic_description: str,
        ) -> dict[str, object]:
            matrix_ids.append(id(indexes))
            statistic_descriptions.append(statistic_description)
            return original_bootstrap(
                policy,
                baseline,
                indexes,
                statistic,
                statistic_description,
            )

        with mock.patch.object(
            qualification, "validate_run", side_effect=_strict_validation_stub
        ), mock.patch.object(
            qualification, "_paired_bootstrap", side_effect=recording_bootstrap
        ), mock.patch.object(
            qualification,
            "_bootstrap_index_matrix",
            wraps=qualification._bootstrap_index_matrix,
        ) as matrix_factory:
            first = qualification.analyze_campaign(self.campaign.root)
        self.assertEqual(matrix_factory.call_count, 1)
        self.assertEqual(len(matrix_ids), 4)
        self.assertEqual(len(set(matrix_ids)), 1)
        self.assertEqual(
            statistic_descriptions,
            [
                "mean per-run policy-minus-STR miss-rate difference",
                "mean per-run policy-minus-STR completed-P99 difference",
                "ratio of resampled policy and STR sender-airtime means",
                (
                    "one minus ratio of resampled policy and STR "
                    "background-throughput means"
                ),
            ],
        )
        self.assertEqual(
            first["bootstrap"]["index_matrix_sha256"],
            "2df6a6e3458bf1336ba8dc652db4cb8a07de2cb2bfb6ea0dee06778e1c97080d",
        )
        second = self.analyze()
        self.assertEqual(first["comparison_against_str"], second["comparison_against_str"])
        self.assertEqual(first["bootstrap"], second["bootstrap"])

    def test_missing_raw_artifact_aborts_before_inference(self) -> None:
        path = self.campaign.run_dirs[("policy", 1201)] / "paired_value_t2_decisions.csv"
        original = path.read_bytes()
        path.unlink()
        self.addCleanup(path.write_bytes, original)
        self.assert_qualification_fails_before_inference("missing raw artifacts")

    def test_malformed_raw_outcomes_and_airtime_fail_before_inference(self) -> None:
        run_dir = self.campaign.run_dirs[("policy", 1201)]
        frames_path = run_dir / "frames.csv"
        airtime_path = run_dir / "link_intervals.csv"
        frames_original = frames_path.read_bytes()
        airtime_original = airtime_path.read_bytes()
        self.addCleanup(frames_path.write_bytes, frames_original)
        self.addCleanup(airtime_path.write_bytes, airtime_original)

        with frames_path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            frame_columns = reader.fieldnames or []
            frame_rows = list(reader)
        frame_rows[0]["deadline_miss"] = "0"
        _write_csv(frames_path, frame_columns, frame_rows)
        self.assert_qualification_fails_before_inference(
            "deadline-miss flag disagrees with raw outcome"
        )

        frames_path.write_bytes(frames_original)
        _write_csv(
            airtime_path,
            ["link_id", "phy_tx_time_us"],
            [
                {"link_id": 0, "phy_tx_time_us": 50_000},
                {"link_id": 0, "phy_tx_time_us": 50_000},
            ],
        )
        self.assert_qualification_fails_before_inference("duplicate link airtime row")

    def test_policy_source_closure_drift_fails_before_inference(self) -> None:
        path = self.campaign.run_dirs[("policy", 1201)] / "paired_value_t2_summary.json"
        original = path.read_bytes()
        self.addCleanup(path.write_bytes, original)
        summary = json.loads(original)
        summary["source_artifacts"]["canonical_model_pickle"]["sha256"] = "0" * 64
        _write_json(path, summary)
        self.assert_qualification_fails_before_inference(
            "controller source/runtime identity mismatch"
        )

    def test_runtime_manifest_corruption_aborts_before_inference(self) -> None:
        original = self.campaign.manifest_path.read_bytes()
        self.addCleanup(self.campaign.manifest_path.write_bytes, original)
        manifest = json.loads(original)
        manifest["runtime_contract_sha256"] = "0" * 64
        _write_json(self.campaign.manifest_path, manifest)
        self.assert_qualification_fails_before_inference("source closure mismatch")

    def test_unmatched_environment_and_mixed_build_are_fatal(self) -> None:
        run_dir = self.campaign.run_dirs[("policy", 1201)]
        config_path = run_dir / "resolved_config.json"
        aggregate_original = self.campaign.aggregate_path.read_bytes()
        config_original = config_path.read_bytes()
        self.addCleanup(self.campaign.aggregate_path.write_bytes, aggregate_original)
        self.addCleanup(config_path.write_bytes, config_original)
        config = json.loads(config_original)
        config["background"]["obss"]["bsses"][0]["seeded_geometry_token"] += 1
        _write_json(config_path, config)
        aggregate = json.loads(aggregate_original)
        for run in aggregate["runs"]:
            if run["run_id"] == config["run_id"]:
                run["config"] = config
        _write_json(self.campaign.aggregate_path, aggregate)
        self.assert_qualification_fails_before_inference(
            "unmatched environment realization"
        )

        # Cleanup executes after the test, so restore explicitly before the build corruption.
        self.campaign.aggregate_path.write_bytes(aggregate_original)
        config_path.write_bytes(config_original)
        build_path = run_dir / "build_info.json"
        build_original = build_path.read_bytes()
        self.addCleanup(build_path.write_bytes, build_original)
        build = json.loads(build_original)
        build["compiler"] = "different-c++"
        _write_json(build_path, build)
        self.assert_qualification_fails_before_inference("mixes build identities")

    def test_policy_label_is_ignored_but_neutral_field_drift_is_fatal(self) -> None:
        # The passing fixture carries the policy-only provenance label while the
        # baseline does not; independent environment reconstruction still pairs it.
        self.assertEqual(self.analyze()["overall"]["status"], "pass")
        run_dir = self.campaign.run_dirs[("policy", 1201)]
        config_path = run_dir / "resolved_config.json"
        aggregate_original = self.campaign.aggregate_path.read_bytes()
        config_original = config_path.read_bytes()
        self.addCleanup(self.campaign.aggregate_path.write_bytes, aggregate_original)
        self.addCleanup(config_path.write_bytes, config_original)
        config = json.loads(config_original)
        config["propagation"]["rss_dbm"] = -49
        _write_json(config_path, config)
        aggregate = json.loads(aggregate_original)
        for run in aggregate["runs"]:
            if run["run_id"] == config["run_id"]:
                run["config"] = config
        _write_json(self.campaign.aggregate_path, aggregate)
        self.assert_qualification_fails_before_inference(
            "environment differs from frozen neutral closure"
        )

    def test_insufficient_completed_frames_is_fatal(self) -> None:
        self.campaign.rewrite_frames("policy", incomplete_count=21)
        self.assert_qualification_fails_before_inference("only 99 completed frames")

    def test_raw_frame_estimators_keep_deadline_equality_and_incomplete_semantics(self) -> None:
        expected_per_run_p99: list[float] = []
        pooled_completed: list[float] = []
        expected_per_run_miss_rates: list[float] = []
        for offset, seed in enumerate(range(1201, 1249)):
            completed_count = 100 if offset < 24 else 200
            ordinary_latency = 10_000 if offset < 24 else 20_000
            completed_latencies = [ordinary_latency] * (completed_count - 1) + [33_333]
            rows: list[dict[str, object]] = []
            run_id = str(self.campaign.aggregate_runs[("policy", seed)]["run_id"])
            for frame_id, latency in enumerate(completed_latencies):
                rows.append({
                    "run_id": run_id,
                    "frame_id": frame_id,
                    "generation_time_us": 1_000_000 + frame_id * 10_000,
                    "deadline_us": 33_333,
                    "union_latency_us": latency,
                    "deadline_miss": 0,
                    "incomplete": 0,
                })
            rows.append({
                "run_id": run_id,
                "frame_id": completed_count,
                "generation_time_us": 1_000_000 + completed_count * 10_000,
                "deadline_us": 33_333,
                "union_latency_us": "",
                "deadline_miss": 1,
                "incomplete": 1,
            })
            _write_csv(
                self.campaign.run_dirs[("policy", seed)] / "frames.csv",
                [
                    "run_id",
                    "frame_id",
                    "generation_time_us",
                    "deadline_us",
                    "union_latency_us",
                    "deadline_miss",
                    "incomplete",
                ],
                rows,
            )
            expected_per_run_p99.append(
                qualification._type7_quantile(completed_latencies, 0.99)
            )
            pooled_completed.extend(completed_latencies)
            expected_per_run_miss_rates.append(1 / (completed_count + 1))

        report = self.analyze()
        policy = report["treatments"]["policy"]
        reported_p99 = policy["completed_frame_p99_us"]["mean"]
        self.assertAlmostEqual(
            reported_p99,
            sum(expected_per_run_p99) / len(expected_per_run_p99),
        )
        self.assertNotAlmostEqual(
            reported_p99,
            qualification._type7_quantile(pooled_completed, 0.99),
        )
        miss = policy["all_generated_deadline_miss_rate"]
        self.assertAlmostEqual(
            miss["mean"],
            sum(expected_per_run_miss_rates) / len(expected_per_run_miss_rates),
        )
        # Each run has one latency exactly equal to the deadline and one incomplete
        # frame. Only the incomplete frame is a miss, and it remains in the
        # all-generated denominator.
        self.assertEqual(miss["total_misses"], 48)
        self.assertEqual(miss["total_generated_frames"], 24 * 101 + 24 * 201)
        self.assertEqual(policy["completed_frame_count"]["total"], 24 * 100 + 24 * 200)

    def test_nonpositive_str_resource_denominators_fail_before_inference(self) -> None:
        cases = (
            (
                "sender airtime",
                lambda: self.campaign.rewrite_airtime("str_mlo", 0),
                "STR sender-airtime denominator contains a nonpositive run",
            ),
            (
                "background throughput",
                lambda: self.campaign.rewrite_background("str_mlo", 0),
                "STR background byte denominator contains a nonpositive run",
            ),
        )
        for label, corrupt, expected in cases:
            with self.subTest(label=label):
                self.campaign.restore_resources()
                corrupt()
                self.assert_qualification_fails_before_inference(expected)

    def test_performance_and_resource_gates_have_frozen_boundaries(self) -> None:
        self.campaign.rewrite_frames("policy", policy_worse=True)
        self.campaign.rewrite_airtime("str_mlo", 100_000)
        self.campaign.rewrite_airtime("policy", 120_000)
        self.campaign.rewrite_background("str_mlo", 1_000_000)
        self.campaign.rewrite_background("policy", 990_000)
        report = self.analyze()
        self.assertEqual(report["performance_victory_against_str"]["status"], "fail")
        # Exactly 1.20 fails because the airtime bound is exclusive.
        self.assertEqual(
            report["resource_target_against_str"]["criteria"]["sender_airtime_ratio"]["status"],
            "fail",
        )
        # Exactly one percent loss passes because the throughput bound is inclusive.
        self.assertEqual(
            report["resource_target_against_str"]["criteria"]
            ["background_throughput_loss"]["status"],
            "pass",
        )
        self.assertEqual(report["overall"]["status"], "fail")

    def test_background_loss_just_above_one_percent_fails_resource_only(self) -> None:
        self.campaign.rewrite_background("str_mlo", 1_000_000)
        self.campaign.rewrite_background("policy", 989_999)
        report = self.analyze()
        self.assertEqual(report["performance_victory_against_str"]["status"], "pass")
        self.assertEqual(report["resource_target_against_str"]["status"], "fail")
        exact = report["comparison_against_str"]["background_throughput_loss"][
            "exact_gate_arithmetic"
        ]
        self.assertFalse(exact["passed"])
        self.assertEqual(
            100 * exact["policy_background_bytes_received"],
            4_751_995_200,
        )
        self.assertEqual(
            99 * exact["str_background_bytes_received"],
            4_752_000_000,
        )
        self.assertEqual(report["overall"]["status"], "fail")

    def test_require_pass_writes_failed_report_then_exits_one(self) -> None:
        self.campaign.rewrite_airtime("str_mlo", 100_000)
        self.campaign.rewrite_airtime("policy", 120_000)
        output = self.campaign.root / "failed-analysis.json"
        if output.exists():
            output.unlink()
        with mock.patch.object(
            qualification, "validate_run", side_effect=_strict_validation_stub
        ), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = qualification.main([
                str(self.campaign.root),
                "--format",
                "json",
                "--json-output",
                str(output),
                "--require-pass",
            ])
        self.assertEqual(result, 1)
        self.assertTrue(output.is_file())
        self.assertEqual(json.loads(output.read_text())["overall"]["status"], "fail")

    def test_validation_failure_exits_two_without_emitting_report(self) -> None:
        output = self.campaign.root / "must-not-exist.json"
        if output.exists():
            output.unlink()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            qualification,
            "validate_run",
            side_effect=qualification.ValidationError("synthetic strict failure"),
        ), mock.patch.object(
            qualification, "_bootstrap_index_matrix"
        ) as matrix_factory, mock.patch.object(
            qualification, "_paired_bootstrap"
        ) as bootstrap, mock.patch.object(
            qualification, "_criterion"
        ) as criterion, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = qualification.main([
                str(self.campaign.root),
                "--format",
                "json",
                "--json-output",
                str(output),
            ])
        self.assertEqual(result, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("validation error:", stderr.getvalue())
        self.assertFalse(output.exists())
        matrix_factory.assert_not_called()
        bootstrap.assert_not_called()
        criterion.assert_not_called()


if __name__ == "__main__":
    unittest.main()
