#!/usr/bin/env python3
"""Generate the frozen held-out environment qualification matrix."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Sequence

import yaml

import run_experiments


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / (
    "experiments/model-selection/"
    "environment-generalization-qualification-execution-v1.json"
)
OUTPUT_CONFIG = ROOT / (
    "experiments/configs/"
    "environment_generalization_closed_loop_qualification_v1.yaml"
)
OUTPUT_MANIFEST = ROOT / (
    "experiments/model-selection/"
    "environment-generalization-qualification-artifacts-v1.json"
)
PHASE = "closed_loop_qualification"
ARM_IDS = (
    "str_mlo_nmaxinflights_1",
    "score_aware_t2_v2",
    "distributional_shadow_t2",
)
EXCLUDED_AUDIT_CHECKOUTS = (
    "ff6d8b8fcb5882c04ba53b84f12c0e23e3686d62",
    "d66313bcd9542158f48d16269449656dfa9dcc2b",
    "de49f8b37746889dee10f1f31370ae6830d82c13",
    "3bf5bb830351c50761386c0fb9295137c83696da",
)
REPAIR_COMMITS = (
    "a33d2c244a1c2134c8b8b4e436a6a749949f4701",
    "d4a55e68442b3122451856c4b65ec4551cf338ba",
    "648a56a4370d3662193ea7a717ed5a37d73d63c2",
    "28b77ce2aa9ede45d9b79dcc658b69b2343e4049",
    "e7a8b3ed8d28f8094ce08978bf27db3ff99f4cee",
    "2e2b2c68cf1d0ab157f72420e9a89ee31313c1e7",
)
LIMITED_INSPECTED_OUTPUT_FIELDS = (
    "sent_packets",
    "sent_bytes",
    "redundant_bytes",
    "link_0_bytes",
    "link_1_bytes",
    "background_tx_bytes",
    "background_rx_bytes",
    "finalized_frames",
)


class QualificationGenerationError(RuntimeError):
    """Raised when the frozen qualification execution boundary differs."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise QualificationGenerationError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise QualificationGenerationError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationGenerationError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise QualificationGenerationError(f"{path}: expected a JSON object")
    return value


def _validate_source(path_text: str, expected: str, label: str) -> Path:
    path = (ROOT / path_text).resolve()
    _require(path.is_relative_to(ROOT), f"{label} escapes the repository")
    _require(path.is_file() and not path.is_symlink(), f"{label} is absent")
    _require(_sha256(path) == expected, f"{label} hash differs")
    return path


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    """Load and validate the complete qualification execution contract."""

    contract = _read_json(path.resolve())
    _require(
        set(contract)
        == {
            "schema_version",
            "runtime_contract_id",
            "status",
            "pre_outcome_runtime_amendment",
            "purpose",
            "parent_generalization_contract",
            "scenario_catalog",
            "base_config",
            "campaign",
            "arms",
            "runtime_outputs",
            "analysis",
            "interpretation_limits",
        },
        "qualification contract keys differ",
    )
    _require(contract["schema_version"] == 1, "contract schema differs")
    _require(
        contract["runtime_contract_id"]
        == "environment-generalization-qualification-execution-v1"
        and contract["status"]
        == "amended_before_closed_loop_qualification_outcomes",
        "contract identity differs",
    )
    amendment = contract["pre_outcome_runtime_amendment"]
    preflight = amendment.get("configuration_preflight")
    builder_amendment = amendment.get("dataset_builder_compatibility_amendment")
    inspection = amendment.get("outcome_inspection_disclosure")
    _require(
        isinstance(amendment, dict)
        and set(amendment)
        == {
            "amendment_stage",
            "outcome_inspection_disclosure",
            "failed_attempt_roots_are_not_qualification_evidence",
            "all_576_runs_must_be_reexecuted_from_one_clean_repaired_commit",
            "partial_run_reuse_allowed",
            "mixed_build_evidence_allowed",
            "reserved_confirmation_seeds_remain_unopened",
            "excluded_execution_audits",
            "repair_commits",
            "configuration_preflight",
            "dataset_builder_compatibility_amendment",
        }
        and amendment.get("amendment_stage")
        == "four_excluded_execution_audits_before_qualification_outcomes"
        and inspection
        == {
            "headline_deadline_or_latency_metrics_inspected": False,
            "limited_error_log_aggregate_fields_inspected": True,
            "limited_fields": list(LIMITED_INSPECTED_OUTPUT_FIELDS),
            "limited_fields_used_for_policy_model_threshold_or_gate_selection": False,
        }
        and amendment.get("failed_attempt_roots_are_not_qualification_evidence")
        is True
        and amendment.get(
            "all_576_runs_must_be_reexecuted_from_one_clean_repaired_commit"
        )
        is True
        and amendment.get("partial_run_reuse_allowed") is False
        and amendment.get("mixed_build_evidence_allowed") is False
        and amendment.get("reserved_confirmation_seeds_remain_unopened") is True,
        "pre-outcome runtime amendment differs",
    )
    audits = amendment.get("excluded_execution_audits")
    _require(
        isinstance(audits, list)
        and len(audits) == 4
        and all(isinstance(audit, dict) for audit in audits)
        and tuple(audit.get("checkout") for audit in audits)
        == EXCLUDED_AUDIT_CHECKOUTS,
        "excluded execution audits differ",
    )
    first_audit, second_audit, third_audit, fourth_audit = audits
    _require(
        set(first_audit)
        == {
            "checkout",
            "campaign_state",
            "performance_outcomes_inspected",
            "counts",
            "failure",
        }
        and first_audit["campaign_state"]
        == "stopped_after_metadata_only_contract_failure"
        and first_audit["performance_outcomes_inspected"] is False
        and first_audit["counts"]
        == {
            "manifest_complete_runs": 85,
            "retained_policy_attempt_directories": 226,
            "immediate_abort_logs": 40,
            "in_flight_runs_stopped": 64,
        },
        "first excluded execution audit differs",
    )
    second_counts = second_audit.get("counts")
    process_diagnostics = second_audit.get("process_failure_diagnostics")
    validator_classes = second_audit.get("validator_rejection_classes")
    _require(
        set(second_audit)
        == {
            "checkout",
            "campaign_state",
            "remote_checkout",
            "output_root",
            "service",
            "performance_outcomes_inspected",
            "counts",
            "process_failure_diagnostics",
            "validator_rejection_classes",
        }
        and second_audit["campaign_state"]
        == "all_attempts_finished_but_campaign_invalid"
        and second_audit["remote_checkout"]
        == "/home/jingweili/wifi_streaming_qualification_d66313b"
        and second_audit["output_root"]
        == (
            "/home/jingweili/wifi_streaming_qualification_d66313b/results/"
            "environment_generalization_closed_loop_qualification_v1/runs"
        )
        and second_audit["service"] == "wifi-qualification-d66313b.service"
        and second_audit["performance_outcomes_inspected"] is False
        and isinstance(second_counts, dict)
        and second_counts
        == {
            "scheduled_runs": 576,
            "finished_attempts": 576,
            "retained_manifest_runs": 335,
            "excluded_attempts": 241,
            "process_failures": 134,
            "validator_rejections": 107,
        }
        and second_counts["retained_manifest_runs"]
        + second_counts["excluded_attempts"]
        == second_counts["scheduled_runs"]
        and process_diagnostics
        == {
            "v2_deadline_contract_abort": 44,
            "distributional_deadline_contract_abort": 44,
            "v2_i_frame_metadata_contract_abort": 20,
            "distributional_i_frame_metadata_contract_abort": 20,
            "phy_polling_fraction_abort": 4,
            "distributional_final_reconciliation_abort": 1,
            "process_failure_without_abort_marker": 1,
        }
        and sum(process_diagnostics.values()) == second_counts["process_failures"]
        and validator_classes
        == {
            "secondary_airtime_summary_maximum_debt_differs_from_exact_replay": 91,
            "paired_value_ordered_and_sklearn_cost_heads_diverge": 13,
            "paired_value_reservation_checkpoint_feasibility_failed": 3,
        }
        and sum(validator_classes.values()) == second_counts["validator_rejections"],
        "second excluded execution audit differs",
    )
    third_counts = third_audit.get("counts")
    third_inspection = third_audit.get("outcome_inspection")
    validator_environment = third_audit.get("validator_environment")
    _require(
        set(third_audit)
        == {
            "checkout",
            "campaign_state",
            "remote_checkout",
            "output_root",
            "service",
            "counts",
            "failure",
            "validator_environment",
            "outcome_inspection",
        }
        and third_audit["campaign_state"]
        == "stopped_after_solver_version_validator_failure"
        and third_audit["remote_checkout"]
        == "/home/jingweili/wifi_streaming_qualification_de49f8b"
        and third_audit["output_root"]
        == (
            "/home/jingweili/wifi_streaming_qualification_de49f8b/results/"
            "environment_generalization_closed_loop_qualification_v1/runs"
        )
        and third_audit["service"] == "wifi-qualification-de49f8b.service"
        and third_counts
        == {
            "scheduled_runs": 576,
            "retained_manifest_runs": 121,
            "failed_completed_attempts": 1,
            "interrupted_attempt_directories": 64,
        }
        and validator_environment
        == {
            "failing_scipy_version": "1.11.4",
            "newer_scipy_replay_version": "1.17.1",
            "presolve_false_result": "false_infeasible",
            "presolve_true_result": "feasible_witness_passed_independent_replay",
        }
        and third_inspection == inspection,
        "third excluded execution audit differs",
    )
    fourth_counts = fourth_audit.get("counts")
    fourth_inspection = fourth_audit.get("outcome_inspection")
    fourth_validator = fourth_audit.get("validator_environment")
    _require(
        set(fourth_audit)
        == {
            "checkout",
            "campaign_state",
            "remote_checkout",
            "output_root",
            "service",
            "counts",
            "failure",
            "validator_environment",
            "outcome_inspection",
        }
        and fourth_audit["campaign_state"]
        == "stopped_after_load_dependent_validator_timeout"
        and fourth_audit["remote_checkout"]
        == "/home/jingweili/wifi_streaming_qualification_3bf5bb8"
        and fourth_audit["output_root"]
        == (
            "/home/jingweili/wifi_streaming_qualification_3bf5bb8/results/"
            "environment_generalization_closed_loop_qualification_v1/runs"
        )
        and fourth_audit["service"] == "wifi-qualification-3bf5bb8.service"
        and fourth_counts
        == {
            "scheduled_runs": 576,
            "retained_manifest_runs": 121,
            "failed_completed_attempts": 1,
            "interrupted_or_unretained_attempt_directories": 64,
        }
        and fourth_validator
        == {
            "scipy_version": "1.11.4",
            "failed_run_id": "7ea792adf5b6eaa071a2",
            "same_run_passed_preflight": True,
            "isolated_replay_after_stop": "valid_1800_frames",
            "failed_budget": (
                "shared_30_seconds_across_all_components_and_representations"
            ),
            "repaired_budget": "60_seconds_per_component_representation",
            "repaired_concurrent_stress_replays": 64,
            "repaired_concurrent_stress_failures": 0,
            "repaired_stress_elapsed_s": 66.936,
        }
        and fourth_inspection
        == {
            "headline_deadline_or_latency_metrics_inspected": False,
            "limited_performance_aggregate_fields_inspected": False,
            "failure_and_completion_metadata_inspected": True,
            "metadata_used_for_policy_model_threshold_or_gate_selection": False,
        },
        "fourth excluded execution audit differs",
    )
    repairs = amendment.get("repair_commits")
    _require(
        isinstance(repairs, list)
        and tuple(repair.get("commit") for repair in repairs) == REPAIR_COMMITS
        and all(set(repair) == {"commit", "repair"} for repair in repairs),
        "runtime repair commit closure differs",
    )
    _require(
        isinstance(preflight, dict)
        and set(preflight)
        == {
            "path",
            "sha256",
            "required_unique_scenario_arm_configurations",
            "workers",
            "command",
        }
        and preflight["required_unique_scenario_arm_configurations"] == 144
        and preflight["workers"] == 64,
        "configuration preflight contract differs",
    )
    _validate_source(preflight["path"], preflight["sha256"], "configuration preflight")
    _require(
        isinstance(builder_amendment, dict)
        and set(builder_amendment) == {"path", "sha256"},
        "dataset-builder compatibility amendment declaration differs",
    )
    _validate_source(
        builder_amendment["path"],
        builder_amendment["sha256"],
        "dataset-builder compatibility amendment",
    )
    parent_spec = contract["parent_generalization_contract"]
    parent_path = _validate_source(
        parent_spec["path"], parent_spec["sha256"], "parent contract"
    )
    parent = _read_json(parent_path)
    catalog_spec = contract["scenario_catalog"]
    catalog_path = _validate_source(
        catalog_spec["path"], catalog_spec["sha256"], "scenario catalog"
    )
    catalog = _read_json(catalog_path)
    phase = catalog.get("phases", {}).get(PHASE)
    _require(isinstance(phase, dict), "qualification scenario phase is absent")
    _require(
        catalog_spec["phase"] == PHASE
        and phase.get("scenario_list_sha256")
        == catalog_spec["scenario_list_sha256"],
        "qualification scenario-list identity differs",
    )
    base_spec = contract["base_config"]
    base_path = _validate_source(
        base_spec["path"], base_spec["file_sha256"], "qualification base config"
    )
    base = run_experiments.load_yaml(base_path)
    _require(
        run_experiments.matrix_sha256(base) == base_spec["resolved_sha256"],
        "resolved qualification base config differs",
    )
    campaign = contract["campaign"]
    expected_counts = {
        "family_count": 6,
        "scenarios_per_family": 8,
        "scenario_count": 48,
        "replicates_per_scenario": 4,
        "paired_unit_count": 192,
        "arm_count": 3,
        "simulation_run_count": 576,
        "worker_wave_count": 9,
        "workers": 64,
    }
    _require(
        all(campaign.get(key) == value for key, value in expected_counts.items()),
        "qualification campaign dimensions differ",
    )
    _require(
        campaign["simulation_run_count"]
        == campaign["paired_unit_count"] * campaign["arm_count"]
        and campaign["simulation_run_count"]
        == campaign["workers"] * campaign["worker_wave_count"],
        "qualification worker-wave arithmetic differs",
    )
    arms = contract["arms"]
    _require(
        isinstance(arms, list)
        and tuple(arm.get("arm_id") for arm in arms) == ARM_IDS,
        "qualification arm order differs",
    )
    parent_arms = parent["closed_loop_qualification"]["actual_simulation_arms"]
    for arm, parent_arm in zip(arms, parent_arms, strict=True):
        _require(
            all(arm.get(key) == parent_arm.get(key) for key in parent_arm),
            f"qualification arm differs from parent: {arm.get('arm_id')}",
        )
        _require(isinstance(arm.get("config"), dict), "arm config is invalid")
    _require(
        "prediction.paired_temporal_t2_frame_profile" not in arms[0]["config"]
        and all(
            arm["config"].get("prediction.paired_temporal_t2_frame_profile")
            == "environment_generalization_v1"
            for arm in arms[1:]
        ),
        "qualification policy frame-profile selection differs",
    )
    sources = contract["runtime_outputs"]["controller_summary_json"][
        "required_source_artifacts_exact"
    ]
    _require(isinstance(sources, dict) and sources, "source closure is empty")
    for name, source in sources.items():
        _require(
            isinstance(name, str)
            and isinstance(source, dict)
            and set(source) == {"path", "sha256"},
            "source closure entry differs",
        )
        _validate_source(source["path"], source["sha256"], name)
    parent_phase = parent["sampling"]["phases"][PHASE]
    _require(
        phase["scenario_count"] == campaign["scenario_count"]
        and phase["paired_unit_count"] == campaign["paired_unit_count"]
        and parent_phase["expected_simulation_run_count"]
        == campaign["simulation_run_count"],
        "catalog, parent, and execution counts differ",
    )
    _require(
        contract["analysis"]["aggregate_and_family_gates_source"]
        == "parent_generalization_contract.closed_loop_qualification"
        and contract["analysis"]["bootstrap"]["replications"] == 10_000,
        "qualification analysis boundary differs",
    )
    return contract


def build_document(contract: dict[str, Any]) -> dict[str, Any]:
    """Build the executable YAML overlay from the frozen scenario catalog."""

    catalog = _read_json(ROOT / contract["scenario_catalog"]["path"])
    scenarios = copy.deepcopy(catalog["phases"][PHASE]["scenarios"])
    source_artifacts = copy.deepcopy(
        contract["runtime_outputs"]["controller_summary_json"][
            "required_source_artifacts_exact"
        ]
    )
    topologies: list[dict[str, Any]] = []
    topology_names: set[str] = set()
    policies: list[dict[str, Any]] = []
    for arm in contract["arms"]:
        if arm["topology"] not in topology_names:
            topology_names.add(arm["topology"])
            topologies.append({"name": arm["topology"]})
        policy = {
            "name": arm["policy"],
            "topologies": [arm["topology"]],
        }
        if arm["config"]:
            policy["config"] = copy.deepcopy(arm["config"])
        policies.append(policy)
    return {
        "extends": str(Path(contract["base_config"]["path"]).name),
        "name": contract["campaign"]["name"],
        "output_root": contract["campaign"]["output_root"],
        "workers": contract["campaign"]["workers"],
        "generalization_contract": {
            "id": "environment-generalization-v1",
            "path": contract["parent_generalization_contract"]["path"],
            "sha256": contract["parent_generalization_contract"]["sha256"],
            "phase": PHASE,
        },
        "runtime_contract": {
            "id": contract["runtime_contract_id"],
            "path": str(CONTRACT_PATH.relative_to(ROOT)),
            "sha256": _sha256(CONTRACT_PATH),
            "source_artifacts": source_artifacts,
        },
        "seeds": [1],
        "runs": [1],
        "scenario_instances": scenarios,
        "topologies": topologies,
        "policies": policies,
    }


def validate_document(
    document: dict[str, Any], contract: dict[str, Any]
) -> list[dict[str, Any]]:
    """Validate exact expansion, pairing, arm coverage, and seed isolation."""

    base = run_experiments.load_yaml(ROOT / contract["base_config"]["path"])
    overlay = copy.deepcopy(document)
    overlay.pop("extends")
    resolved = run_experiments._merge_yaml(base, overlay)
    runtime = run_experiments.validate_runtime_contract(resolved)
    _require(runtime is not None, "qualification runtime contract is absent")
    specs = run_experiments.expand_config(resolved)
    campaign = contract["campaign"]
    _require(
        len(specs) == campaign["simulation_run_count"],
        "qualification expansion count differs",
    )
    by_unit: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    for spec in specs:
        scenario = spec.get("scenario")
        _require(isinstance(scenario, dict), "expanded scenario identity is absent")
        key = (scenario["scenario_id"], spec["seed"], spec["run"])
        by_unit.setdefault(key, []).append(spec)
        _require(
            not 1301 <= spec["seed"] <= 1348,
            "qualification consumes a reserved confirmation seed",
        )
    _require(
        len(by_unit) == campaign["paired_unit_count"],
        "qualification paired-unit count differs",
    )
    expected_arms = {
        (arm["topology"], arm["policy"]) for arm in contract["arms"]
    }
    _require(
        all(
            {(row["config"]["topology"], row["config"]["policy"]) for row in rows}
            == expected_arms
            for rows in by_unit.values()
        ),
        "a qualification paired unit lacks an arm",
    )
    return specs


def resolved_matrix_sha256(
    document: dict[str, Any], contract: dict[str, Any]
) -> str:
    """Return the runner's hash of the fully inherited qualification matrix."""

    base = run_experiments.load_yaml(ROOT / contract["base_config"]["path"])
    overlay = copy.deepcopy(document)
    overlay.pop("extends")
    return run_experiments.matrix_sha256(run_experiments._merge_yaml(base, overlay))


def _render_yaml(document: dict[str, Any]) -> bytes:
    header = (
        "# Generated by tools/generate_environment_generalization_qualification_v1.py.\n"
        "# Do not edit; update the frozen execution contract and regenerate.\n"
    )
    body = yaml.safe_dump(
        document,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=False,
    )
    return (header + body).encode("ascii")


def _render_json(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            document,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def generate_artifacts() -> dict[Path, bytes]:
    """Return the deterministic qualification config and provenance manifest."""

    contract = load_contract()
    document = build_document(contract)
    specs = validate_document(document, contract)
    config = _render_yaml(document)
    manifest = {
        "schema_version": 1,
        "manifest_id": "environment-generalization-qualification-artifacts-v1",
        "contract": {
            "path": str(CONTRACT_PATH.relative_to(ROOT)),
            "sha256": _sha256(CONTRACT_PATH),
        },
        "generator": {
            "path": str(Path(__file__).resolve().relative_to(ROOT)),
            "sha256": _sha256(Path(__file__).resolve()),
        },
        "generated_artifacts": {
            "qualification_config": {
                "path": str(OUTPUT_CONFIG.relative_to(ROOT)),
                "bytes": len(config),
                "sha256": hashlib.sha256(config).hexdigest(),
            }
        },
        "campaign_counts": {
            "scenarios": contract["campaign"]["scenario_count"],
            "paired_units": contract["campaign"]["paired_unit_count"],
            "simulation_runs": len(specs),
            "workers": contract["campaign"]["workers"],
            "worker_waves": contract["campaign"]["worker_wave_count"],
        },
        "resolved_matrix_sha256": resolved_matrix_sha256(document, contract),
        "invariants": {
            "qualification_scenarios_were_predeclared": True,
            "every_paired_unit_has_all_three_arms": True,
            "reserved_confirmation_seed_overlap_count": 0,
            "simulation_run_count_is_a_64_worker_multiple": True,
        },
        "reproduction": [
            "python3 tools/generate_environment_generalization_qualification_v1.py --check"
        ],
    }
    return {OUTPUT_CONFIG: config, OUTPUT_MANIFEST: _render_json(manifest)}


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    artifacts = generate_artifacts()
    if args.check:
        for path, expected in artifacts.items():
            try:
                observed = path.read_bytes()
            except OSError as error:
                raise QualificationGenerationError(
                    f"cannot read generated artifact {path}: {error}"
                ) from error
            _require(observed == expected, f"generated artifact is stale: {path}")
    else:
        for path, content in artifacts.items():
            _write_atomic(path, content)
    print(
        json.dumps(
            {
                "check": args.check,
                "artifact_count": len(artifacts),
                "simulation_runs": 576,
                "worker_waves": 9,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
