#!/usr/bin/env python3
"""Run the frozen two-stage T2 repair mechanism experiment."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from run_experiments import (
    NS3_UPSTREAM_COMMIT,
    ROOT,
    atomic_json,
    build_experiment_manifest,
    canonical_json,
    derive_run_id,
    expand_config,
    load_yaml,
    matrix_sha256,
    project_commit,
    run_one,
    sha256_file,
    validate_existing_manifest,
    write_experiment_description,
)
from validate_outputs import validate_run


ORACLE_POLICY = "mechanism_oracle_repair_t2"
BASELINE_POLICY = "fixed_link_1"
DERIVATION_SCHEMA_VERSION = 1
PHASE1_ARMS = {
    ("dual_interface", "fixed_link_1"),
    ("dual_interface", "full_duplication"),
    ("dual_interface", "mechanism_full_copy_t2"),
    ("dual_interface", "mechanism_systematic_fec_t2"),
    ("mlo_str", "fixed_link_0"),
}
ARM_IDS = {
    ("dual_interface", "fixed_link_1"): "single_5ghz_no_redundancy",
    ("dual_interface", "full_duplication"): "full_copy_t0",
    ("dual_interface", "mechanism_full_copy_t2"): "full_copy_t2",
    ("dual_interface", ORACLE_POLICY): "oracle_eventual_missing_repair_t2",
    ("dual_interface", "mechanism_systematic_fec_t2"):
        "ideal_systematic_fec_12p5_t2",
    ("mlo_str", "fixed_link_0"): "str_mlo_nmaxinflights_1",
}


def _unit_key(spec: dict[str, Any]) -> tuple[str, int, int]:
    return (spec["scenario"]["scenario_id"], spec["seed"], spec["run"])


def _contract_path(declaration: dict[str, Any], key: str) -> Path:
    value = declaration.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"mechanism contract {key} must be a nonempty string")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"mechanism contract {key} must be project-relative")
    path = (ROOT / relative).resolve(strict=True)
    path.relative_to(ROOT.resolve())
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"mechanism contract {key} must be a regular file")
    return path


def validate_mechanism_contract(document: dict[str, Any]) -> dict[str, Any]:
    """Validate the frozen contract and exact qualification-scenario closure."""
    declaration = document.get("mechanism_contract")
    if not isinstance(declaration, dict) or set(declaration) != {"id", "path", "sha256"}:
        raise ValueError("mechanism_contract must contain exactly id, path, and sha256")
    contract_path = _contract_path(declaration, "path")
    observed_sha = sha256_file(contract_path)
    if observed_sha != declaration["sha256"]:
        raise ValueError(
            f"mechanism contract hash drift: expected {declaration['sha256']}, "
            f"observed {observed_sha}"
        )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    if (
        contract.get("experiment_id") != declaration["id"]
        or contract.get("status") != "pre_result_freeze"
    ):
        raise ValueError("mechanism contract identity or pre-result status differs")
    source = contract.get("source_scenario_catalog")
    if not isinstance(source, dict) or source.get("phase") != "closed_loop_qualification":
        raise ValueError("mechanism contract has an invalid scenario source")
    catalog_path = _contract_path({"path": source.get("path")}, "path")
    catalog_sha = sha256_file(catalog_path)
    if catalog_sha != source.get("sha256"):
        raise ValueError("mechanism scenario catalog hash drift")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog_rows = {
        row["scenario_id"]: row
        for row in catalog["phases"]["closed_loop_qualification"]["scenarios"]
    }
    selected = contract["selection_scope"]["scenarios"]
    selected_ids = {row["scenario_id"] for row in selected}
    declared_rows = document.get("scenario_instances")
    if not isinstance(declared_rows, list):
        raise ValueError("mechanism matrix lacks scenario_instances")
    declared_by_id = {row.get("scenario_id"): row for row in declared_rows}
    if set(declared_by_id) != selected_ids or len(declared_rows) != len(selected_ids):
        raise ValueError("mechanism matrix scenario selection differs from the contract")
    for selection in selected:
        scenario_id = selection["scenario_id"]
        expected = catalog_rows[scenario_id]
        declared = declared_by_id[scenario_id]
        for key in ("family_id", "parameter_sample", "seeds", "runs", "config"):
            if canonical_json(declared.get(key)) != canonical_json(expected.get(key)):
                raise ValueError(f"scenario {scenario_id} differs from the frozen catalog")
        if declared["seeds"] != selection["seeds"]:
            raise ValueError(f"scenario {scenario_id} seed set differs from the contract")
    if any(seed in range(1301, 1349) for row in declared_rows for seed in row["seeds"]):
        raise ValueError("reserved confirmation seed used by mechanism experiment")
    return {
        "id": declaration["id"],
        "path": declaration["path"],
        "sha256": observed_sha,
        "source_scenario_catalog_sha256": catalog_sha,
    }


def build_campaign_specs(
    document: dict[str, Any],
    project_git_commit: str,
    shard_index: int,
    shard_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve one balanced shard and derive its paired oracle specifications."""
    if shard_count < 1 or not 0 <= shard_index < shard_count:
        raise ValueError("invalid shard index/count")
    phase1_all = expand_config(document)
    observed_arms = {
        (spec["config"]["topology"], spec["config"]["policy"])
        for spec in phase1_all
    }
    if observed_arms != PHASE1_ARMS:
        raise ValueError("phase-1 arm set differs from the frozen mechanism contract")
    units = sorted({_unit_key(spec) for spec in phase1_all})
    if len(units) != 20 or len(phase1_all) != 100:
        raise ValueError("frozen mechanism matrix must contain 20 units and 100 phase-1 runs")
    selected_units = {
        unit for position, unit in enumerate(units) if position % shard_count == shard_index
    }
    phase1 = [spec for spec in phase1_all if _unit_key(spec) in selected_units]
    for spec in phase1:
        spec["run_id"] = derive_run_id(
            spec["config"],
            spec["seed"],
            spec["run"],
            NS3_UPSTREAM_COMMIT,
            project_git_commit,
            scenario=spec["scenario"],
        )
        spec["arm_id"] = ARM_IDS[
            (spec["config"]["topology"], spec["config"]["policy"])
        ]
    baselines = {
        _unit_key(spec): spec
        for spec in phase1
        if spec["config"]["policy"] == BASELINE_POLICY
    }
    if set(baselines) != selected_units:
        raise ValueError("every selected unit must have one primary-only baseline")
    oracle_specs: list[dict[str, Any]] = []
    pairings: list[dict[str, Any]] = []
    for unit in sorted(selected_units):
        baseline = baselines[unit]
        oracle_config = copy.deepcopy(baseline["config"])
        oracle_config["policy"] = ORACLE_POLICY
        prediction = oracle_config.setdefault("prediction", {})
        prediction["secondary_airtime_meter_enabled"] = True
        prediction["mechanism_oracle_packet_outcome_file"] = (
            f"{baseline['run_id']}/frame_packet_outcomes.csv"
        )
        oracle = {
            "config": oracle_config,
            "seed": baseline["seed"],
            "run": baseline["run"],
            "scenario": copy.deepcopy(baseline["scenario"]),
            "arm_id": ARM_IDS[("dual_interface", ORACLE_POLICY)],
            "paired_baseline_run_id": baseline["run_id"],
        }
        oracle["run_id"] = derive_run_id(
            oracle["config"],
            oracle["seed"],
            oracle["run"],
            NS3_UPSTREAM_COMMIT,
            project_git_commit,
            scenario=oracle["scenario"],
        )
        oracle_specs.append(oracle)
        pairings.append({
            "scenario_id": unit[0],
            "seed": unit[1],
            "run": unit[2],
            "baseline_run_id": baseline["run_id"],
            "oracle_run_id": oracle["run_id"],
        })
    return phase1, oracle_specs, pairings


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def validate_oracle_pairs(
    output_root: Path,
    pairings: list[dict[str, Any]],
    project_git_commit: str,
    contract: dict[str, Any],
    shard_index: int,
    shard_count: int,
) -> dict[str, Any]:
    """Prove that every oracle plan and primary outcome matches its baseline."""
    evidence = []
    total_frames = 0
    total_repair_packets = 0
    for pair in pairings:
        baseline_dir = output_root / pair["baseline_run_id"]
        oracle_dir = output_root / pair["oracle_run_id"]
        baseline_path = baseline_dir / "frame_packet_outcomes.csv"
        oracle_path = oracle_dir / "frame_packet_outcomes.csv"
        actions_path = oracle_dir / "mechanism_t2_actions.csv"
        baseline_rows = {int(row["frame_id"]): row for row in _read_csv(baseline_path)}
        oracle_rows = {int(row["frame_id"]): row for row in _read_csv(oracle_path)}
        action_rows = {int(row["frame_id"]): row for row in _read_csv(actions_path)}
        if set(baseline_rows) != set(oracle_rows) or set(baseline_rows) != set(action_rows):
            raise ValueError("oracle pair has mismatched frame coverage")
        for frame_id, baseline in baseline_rows.items():
            oracle = oracle_rows[frame_id]
            action = action_rows[frame_id]
            if (
                baseline["source_packet_count"] != oracle["source_packet_count"]
                or baseline["link_1_source_packet_indices"]
                != oracle["link_1_source_packet_indices"]
                or baseline["copy_0_source_packet_indices"]
                != oracle["copy_0_source_packet_indices"]
            ):
                raise ValueError(
                    f"oracle primary outcome drift for {pair['scenario_id']} "
                    f"seed {pair['seed']} frame {frame_id}"
                )
            expected = baseline["missing_source_packet_indices"]
            if action["action_packet_indices"] != expected:
                raise ValueError(
                    f"oracle repair plan drift for {pair['scenario_id']} "
                    f"seed {pair['seed']} frame {frame_id}"
                )
            expected_flag = "1" if expected else "0"
            if action["requested"] != expected_flag or action["launched"] != expected_flag:
                raise ValueError("oracle repair request/launch differs from its exact plan")
            total_repair_packets += 0 if not expected else len(expected.split(";"))
        total_frames += len(baseline_rows)
        evidence.append({
            **pair,
            "frame_count": len(baseline_rows),
            "repair_packet_count": sum(
                0 if not row["missing_source_packet_indices"]
                else len(row["missing_source_packet_indices"].split(";"))
                for row in baseline_rows.values()
            ),
            "primary_packet_outcomes_identical": True,
            "repair_plan_matches_baseline_eventual_missing_set": True,
            "baseline_packet_outcomes_sha256": sha256_file(baseline_path),
            "oracle_packet_outcomes_sha256": sha256_file(oracle_path),
            "oracle_actions_sha256": sha256_file(actions_path),
        })
    result = {
        "schema_version": 1,
        "mechanism_contract": contract,
        "project_commit": project_git_commit,
        "ns3_upstream_commit": NS3_UPSTREAM_COMMIT,
        "shard": {"index": shard_index, "count": shard_count},
        "pair_count": len(evidence),
        "frame_count": total_frames,
        "repair_packet_count": total_repair_packets,
        "all_primary_packet_outcomes_identical": True,
        "all_repair_plans_match_baseline_eventual_missing_sets": True,
        "pairs": evidence,
    }
    atomic_json(output_root / "oracle_pair_validation.json", result)
    return result


def _decorate_manifest_run(item: dict[str, Any], specs_by_id: dict[str, dict[str, Any]]) -> None:
    spec = specs_by_id[item["run_id"]]
    item["arm_id"] = spec["arm_id"]
    if "paired_baseline_run_id" in spec:
        item["paired_baseline_run_id"] = spec["paired_baseline_run_id"]


def _execute_phase(
    specs: list[dict[str, Any]],
    output_root: Path,
    config_dir: Path,
    project_git_commit: str,
    workers: int,
    manifest: dict[str, Any],
    manifest_path: Path,
    specs_by_id: dict[str, dict[str, Any]],
) -> None:
    pending = [spec for spec in specs if not spec.get("completed")]
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(pending)))) as executor:
        futures = {
            executor.submit(
                run_one, spec, output_root, config_dir, project_git_commit
            ): spec
            for spec in pending
        }
        for future in as_completed(futures):
            spec = futures[future]
            try:
                result = future.result()
                _decorate_manifest_run(result, specs_by_id)
                manifest["runs"].append(result)
                manifest["runs"].sort(key=lambda item: item["run_id"])
                atomic_json(manifest_path, manifest)
                print(f"COMPLETE {spec['arm_id']} {result['run_id']}", flush=True)
            except Exception as error:  # Keep every already-validated run in the manifest.
                failures.append(f"{spec['run_id']}: {error}")
                print(f"FAILED {spec['arm_id']} {spec['run_id']}: {error}", file=sys.stderr)
    if failures:
        raise RuntimeError("\n".join(failures))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "config",
        nargs="?",
        type=Path,
        default=ROOT / "experiments/configs/t2_repair_mechanism_v1.yaml",
    )
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config_path = args.config.resolve()
    document = load_yaml(config_path)
    contract = validate_mechanism_contract(document)
    workers = args.workers or int(document.get("workers", 1))
    if workers < 1:
        parser.error("workers must be positive")
    commit = project_commit()
    phase1, oracle, pairings = build_campaign_specs(
        document, commit, args.shard_index, args.shard_count
    )
    all_specs = [*phase1, *oracle]
    if args.output_root is not None:
        output_root = args.output_root.resolve()
    else:
        declared = Path(document["output_root"]).resolve()
        output_root = (
            declared
            if args.shard_count == 1
            else declared.parent / f"shard_{args.shard_index}_of_{args.shard_count}" / "runs"
        )
    if args.dry_run:
        print(json.dumps({
            "contract": contract,
            "shard": {"index": args.shard_index, "count": args.shard_count},
            "output_root": str(output_root),
            "phase1_runs": len(phase1),
            "oracle_runs": len(oracle),
            "paired_units": pairings,
        }, indent=2, sort_keys=True))
        return

    output_root.mkdir(parents=True, exist_ok=True)
    seen = {spec["run_id"] for spec in all_specs}
    if len(seen) != len(all_specs):
        raise ValueError("mechanism campaign derived duplicate run IDs")
    for spec in all_specs:
        completed = output_root / spec["run_id"]
        if completed.exists():
            validate_run(completed, spec["run_id"], commit, NS3_UPSTREAM_COMMIT)
            if not args.resume:
                raise FileExistsError(f"completed duplicate rejected: {spec['run_id']}")
            spec["completed"] = True

    experiment = (
        f"{document['name']}-shard-{args.shard_index}-of-{args.shard_count}"
    )
    identity_document = {
        "matrix": document,
        "oracle_derivation_schema_version": DERIVATION_SCHEMA_VERSION,
        "shard": {"index": args.shard_index, "count": args.shard_count},
    }
    matrix_sha = matrix_sha256(identity_document)
    manifest_path = output_root / "experiment_manifest.json"
    validate_existing_manifest(
        manifest_path,
        experiment,
        matrix_sha,
        commit,
        seen,
    )
    write_experiment_description(document, all_specs, output_root)
    if not args.no_build:
        subprocess.run(
            [str(ROOT / "ns3"), "build", "streaming-experiment"],
            cwd=ROOT,
            check=True,
        )
    manifest = build_experiment_manifest(
        experiment,
        matrix_sha,
        config_path,
        commit,
        all_specs,
    )
    manifest["mechanism_contract"] = contract
    manifest["oracle_derivation_schema_version"] = DERIVATION_SCHEMA_VERSION
    manifest["shard"] = {"index": args.shard_index, "count": args.shard_count}
    manifest["pairings"] = pairings
    specs_by_id = {spec["run_id"]: spec for spec in all_specs}
    for item in manifest["runs"]:
        _decorate_manifest_run(item, specs_by_id)
    atomic_json(manifest_path, manifest)

    print(f"PHASE1 {len(phase1)} runs", flush=True)
    _execute_phase(
        phase1,
        output_root,
        config_path.parent,
        commit,
        workers,
        manifest,
        manifest_path,
        specs_by_id,
    )
    print(f"PHASE2_ORACLE {len(oracle)} runs", flush=True)
    _execute_phase(
        oracle,
        output_root,
        output_root,
        commit,
        workers,
        manifest,
        manifest_path,
        specs_by_id,
    )
    validation = validate_oracle_pairs(
        output_root,
        pairings,
        commit,
        contract,
        args.shard_index,
        args.shard_count,
    )
    print(
        f"ORACLE_PAIRS {validation['pair_count']} frames={validation['frame_count']} "
        f"repair_packets={validation['repair_packet_count']}",
        flush=True,
    )
    print(f"MANIFEST {manifest_path}", flush=True)


if __name__ == "__main__":
    main()
