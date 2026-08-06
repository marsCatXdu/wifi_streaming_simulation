#!/usr/bin/env python3
"""Tests for the frozen environment-generalization qualification matrix."""

from __future__ import annotations

import copy
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

from generate_environment_generalization_qualification_v1 import (  # noqa: E402
    ARM_IDS,
    EXCLUDED_AUDIT_CHECKOUTS,
    LIMITED_INSPECTED_OUTPUT_FIELDS,
    OUTPUT_CONFIG,
    OUTPUT_MANIFEST,
    QualificationGenerationError,
    REPAIR_COMMITS,
    build_document,
    generate_artifacts,
    load_contract,
    resolved_matrix_sha256,
    validate_document,
)
from run_experiments import (  # noqa: E402
    expand_config,
    load_yaml,
    validate_runtime_contract,
)


class EnvironmentGeneralizationQualificationGeneratorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract()
        cls.document = build_document(cls.contract)
        cls.specs = validate_document(cls.document, cls.contract)

    def test_campaign_is_nine_complete_64_worker_waves(self) -> None:
        campaign = self.contract["campaign"]
        self.assertEqual(campaign["workers"], 64)
        self.assertEqual(campaign["simulation_run_count"], 576)
        self.assertEqual(campaign["worker_wave_count"], 9)
        self.assertEqual(len(self.specs), 576)
        self.assertEqual(
            campaign["simulation_run_count"],
            campaign["workers"] * campaign["worker_wave_count"],
        )

    def test_every_held_out_unit_has_the_three_frozen_arms(self) -> None:
        expected_arms = {
            (arm["topology"], arm["policy"])
            for arm in self.contract["arms"]
        }
        self.assertEqual(
            tuple(arm["arm_id"] for arm in self.contract["arms"]), ARM_IDS
        )
        by_unit: dict[tuple[str, int, int], list[dict[str, object]]] = {}
        for spec in self.specs:
            scenario = spec["scenario"]
            key = (scenario["scenario_id"], spec["seed"], spec["run"])
            by_unit.setdefault(key, []).append(spec)
        self.assertEqual(len(by_unit), 192)
        for rows in by_unit.values():
            self.assertEqual(len(rows), 3)
            self.assertEqual(
                {
                    (row["config"]["topology"], row["config"]["policy"])
                    for row in rows
                },
                expected_arms,
            )

    def test_qualification_uses_only_its_predeclared_seed_range(self) -> None:
        seeds = {spec["seed"] for spec in self.specs}
        self.assertEqual(seeds, set(range(21001, 21193)))
        self.assertFalse(seeds & set(range(1301, 1349)))
        families = {spec["scenario"]["family_id"] for spec in self.specs}
        self.assertEqual(
            families,
            {
                "radio_propagation",
                "obss_intensity",
                "obss_geometry_mac",
                "video_workload",
                "legacy_coexistence",
                "compound_shift",
            },
        )

    def test_generated_artifacts_are_deterministic_and_current(self) -> None:
        first = generate_artifacts()
        second = generate_artifacts()
        self.assertEqual(first, second)
        self.assertEqual(set(first), {OUTPUT_CONFIG, OUTPUT_MANIFEST})
        for path, expected in first.items():
            self.assertEqual(path.read_bytes(), expected)
        manifest = json.loads(OUTPUT_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["campaign_counts"]["simulation_runs"], 576)
        self.assertEqual(manifest["campaign_counts"]["worker_waves"], 9)
        self.assertEqual(
            manifest["resolved_matrix_sha256"],
            "4db729f7a801c8b911ee3cb9acd9ed996e4568a3f7274d6158e547b4db462694",
        )
        self.assertEqual(
            manifest["resolved_matrix_sha256"],
            resolved_matrix_sha256(self.document, self.contract),
        )

    def test_generated_yaml_carries_the_hash_verified_runtime_closure(self) -> None:
        resolved = load_yaml(OUTPUT_CONFIG)
        runtime = validate_runtime_contract(resolved)
        self.assertIsNotNone(runtime)
        self.assertEqual(
            runtime["runtime_contract_id"],
            "environment-generalization-qualification-execution-v1",
        )
        expected_sources = self.contract["runtime_outputs"][
            "controller_summary_json"
        ]["required_source_artifacts_exact"]
        self.assertEqual(
            runtime["source_artifacts"],
            expected_sources,
        )
        self.assertEqual(len(expand_config(resolved)), 576)

    def test_source_hash_and_runtime_declaration_mutations_fail_closed(self) -> None:
        drifted_contract = copy.deepcopy(self.contract)
        contract_sources = drifted_contract["runtime_outputs"][
            "controller_summary_json"
        ]["required_source_artifacts_exact"]
        contract_sources["scenario_catalog"]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "contract.json"
            path.write_text(json.dumps(drifted_contract), encoding="utf-8")
            with self.assertRaisesRegex(
                QualificationGenerationError, "scenario_catalog hash differs"
            ):
                load_contract(path)

        drifted_document = copy.deepcopy(self.document)
        document_sources = drifted_document["runtime_contract"]["source_artifacts"]
        document_sources["scenario_catalog"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "source_artifacts differ"):
            validate_document(drifted_document, self.contract)

    def test_pre_outcome_builder_amendment_is_hash_bound(self) -> None:
        amendment = self.contract["pre_outcome_runtime_amendment"][
            "dataset_builder_compatibility_amendment"
        ]
        self.assertEqual(
            hashlib.sha256((ROOT / amendment["path"]).read_bytes()).hexdigest(),
            amendment["sha256"],
        )

    def test_pre_outcome_audits_forbid_partial_or_mixed_build_reuse(self) -> None:
        amendment = self.contract["pre_outcome_runtime_amendment"]
        inspection = amendment["outcome_inspection_disclosure"]
        self.assertFalse(inspection["headline_deadline_or_latency_metrics_inspected"])
        self.assertTrue(inspection["limited_error_log_aggregate_fields_inspected"])
        self.assertEqual(
            tuple(inspection["limited_fields"]), LIMITED_INSPECTED_OUTPUT_FIELDS
        )
        self.assertFalse(
            inspection[
                "limited_fields_used_for_policy_model_threshold_or_gate_selection"
            ]
        )
        self.assertFalse(amendment["partial_run_reuse_allowed"])
        self.assertFalse(amendment["mixed_build_evidence_allowed"])
        self.assertEqual(
            tuple(
                audit["checkout"]
                for audit in amendment["excluded_execution_audits"]
            ),
            EXCLUDED_AUDIT_CHECKOUTS,
        )
        self.assertEqual(
            tuple(repair["commit"] for repair in amendment["repair_commits"]),
            REPAIR_COMMITS,
        )
        second_counts = amendment["excluded_execution_audits"][1]["counts"]
        self.assertEqual(second_counts["retained_manifest_runs"], 335)
        self.assertEqual(second_counts["excluded_attempts"], 241)
        self.assertEqual(
            second_counts["retained_manifest_runs"]
            + second_counts["excluded_attempts"],
            576,
        )
        third_audit = amendment["excluded_execution_audits"][2]
        self.assertEqual(third_audit["counts"]["retained_manifest_runs"], 121)
        self.assertEqual(third_audit["counts"]["failed_completed_attempts"], 1)
        self.assertEqual(
            third_audit["counts"]["interrupted_attempt_directories"], 64
        )
        self.assertEqual(third_audit["outcome_inspection"], inspection)
        fourth_audit = amendment["excluded_execution_audits"][3]
        self.assertEqual(fourth_audit["counts"]["retained_manifest_runs"], 121)
        self.assertEqual(fourth_audit["counts"]["failed_completed_attempts"], 1)
        self.assertEqual(
            fourth_audit["counts"][
                "interrupted_or_unretained_attempt_directories"
            ],
            64,
        )
        self.assertEqual(
            fourth_audit["validator_environment"][
                "repaired_concurrent_stress_replays"
            ],
            64,
        )
        self.assertEqual(
            fourth_audit["validator_environment"][
                "repaired_concurrent_stress_failures"
            ],
            0,
        )

    def test_pre_outcome_audit_count_mutation_fails_closed(self) -> None:
        drifted_contract = copy.deepcopy(self.contract)
        second_audit = drifted_contract["pre_outcome_runtime_amendment"][
            "excluded_execution_audits"
        ][1]
        second_audit["validator_rejection_classes"][
            "paired_value_reservation_checkpoint_feasibility_failed"
        ] = 2
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "contract.json"
            path.write_text(json.dumps(drifted_contract), encoding="utf-8")
            with self.assertRaisesRegex(
                QualificationGenerationError,
                "second excluded execution audit differs",
            ):
                load_contract(path)

    def test_third_pre_outcome_audit_mutation_fails_closed(self) -> None:
        drifted_contract = copy.deepcopy(self.contract)
        third_audit = drifted_contract["pre_outcome_runtime_amendment"][
            "excluded_execution_audits"
        ][2]
        third_audit["validator_environment"]["presolve_true_result"] = "unknown"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "contract.json"
            path.write_text(json.dumps(drifted_contract), encoding="utf-8")
            with self.assertRaisesRegex(
                QualificationGenerationError,
                "third excluded execution audit differs",
            ):
                load_contract(path)

    def test_fourth_pre_outcome_audit_mutation_fails_closed(self) -> None:
        drifted_contract = copy.deepcopy(self.contract)
        fourth_audit = drifted_contract["pre_outcome_runtime_amendment"][
            "excluded_execution_audits"
        ][3]
        fourth_audit["validator_environment"][
            "repaired_concurrent_stress_failures"
        ] = 1
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "contract.json"
            path.write_text(json.dumps(drifted_contract), encoding="utf-8")
            with self.assertRaisesRegex(
                QualificationGenerationError,
                "fourth excluded execution audit differs",
            ):
                load_contract(path)


if __name__ == "__main__":
    unittest.main()
