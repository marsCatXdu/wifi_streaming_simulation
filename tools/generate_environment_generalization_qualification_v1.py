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
        and contract["status"] == "frozen_before_closed_loop_qualification_runs",
        "contract identity differs",
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
