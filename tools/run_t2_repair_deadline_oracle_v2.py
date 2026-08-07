#!/usr/bin/env python3
"""Run only the corrected paired-potential T2 deadline-repair oracle arms."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Sequence

from run_experiments import (
    NS3_UPSTREAM_COMMIT,
    ROOT,
    atomic_json,
    build_experiment_manifest,
    canonical_json,
    derive_run_id,
    matrix_sha256,
    project_commit,
    run_one,
    sha256_file,
    validate_existing_manifest,
)
from run_t2_repair_mechanism import (
    _resolve_simulation_commit,
    _verify_streaming_executable,
)
from validate_outputs import validate_run


CONTRACT_PATH = ROOT / "experiments/model-selection/t2-repair-deadline-oracle-v2.json"
CONTRACT_SHA256 = "2df18a10f7b584af516d56c77350175f30e7b10c190fbd7f61da598b7b592cea"
V1_ARM = "single_5ghz_no_redundancy"
V2_ARM = "oracle_deadline_missing_repair_t2_v2"
RUNTIME_POLICY = "mechanism_oracle_repair_t2"
SIDE_CAR_DIRECTORY = "deadline_sources"
SIDE_CAR_FILE = "frame_packet_deadline_outcomes.csv"
SIDE_CAR_PROVENANCE = "deadline_sidecar_provenance.json"
PAIR_VALIDATION_FILE = "deadline_oracle_pair_validation.json"
PACKET_OUTCOME_COLUMNS = (
    "run_id",
    "frame_id",
    "source_packet_count",
    "received_source_packet_indices",
    "missing_source_packet_indices",
    "copy_0_source_packet_indices",
    "copy_1_source_packet_indices",
    "link_0_source_packet_indices",
    "link_1_source_packet_indices",
    "received_coded_repair_indices",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {path}: {error}") from error
    _require(isinstance(value, dict), f"{path}: JSON root is not an object")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            _require(
                reader.fieldnames is not None,
                f"{path}: CSV header is missing",
            )
            rows = list(reader)
    except (OSError, csv.Error) as error:
        raise ValueError(f"cannot read {path}: {error}") from error
    _require(bool(rows), f"{path}: CSV is empty")
    return rows


def _rows_by_frame(path: Path) -> dict[int, dict[str, str]]:
    rows = _read_csv(path)
    result = {int(row["frame_id"]): row for row in rows}
    _require(len(result) == len(rows), f"{path}: duplicate frame ID")
    return result


def _indices(value: str) -> set[int]:
    if not value:
        return set()
    try:
        result = {int(item) for item in value.split(";")}
    except ValueError as error:
        raise ValueError(f"invalid packet-index list: {value}") from error
    _require(all(index >= 0 for index in result), "negative packet index")
    return result


def _format_indices(values: Iterable[int]) -> str:
    return ";".join(str(value) for value in sorted(values))


def _tree_identity(root: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    file_count = 0
    byte_count = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        _require(not path.is_symlink(), f"run tree contains a symlink: {path}")
        relative = str(path.relative_to(root))
        size = path.stat().st_size
        file_hash = sha256_file(path)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_hash.encode("ascii"))
        digest.update(b"\n")
        file_count += 1
        byte_count += size
    _require(file_count > 0, f"{root}: empty run tree")
    return {
        "run_id": root.name,
        "file_count": file_count,
        "bytes": byte_count,
        "tree_sha256": digest.hexdigest(),
    }


def _clean_runner_identity() -> str:
    commit = project_commit()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    _require(not status.strip(), "deadline-oracle runner requires a clean worktree")
    return commit


def _project_file(path_value: str, label: str) -> Path:
    path = Path(path_value)
    _require(not path.is_absolute() and ".." not in path.parts, f"invalid {label} path")
    resolved = (ROOT / path).resolve(strict=True)
    resolved.relative_to(ROOT.resolve())
    _require(resolved.is_file() and not resolved.is_symlink(), f"invalid {label} file")
    return resolved


def validate_contract() -> dict[str, Any]:
    _require(sha256_file(CONTRACT_PATH) == CONTRACT_SHA256, "V2 contract hash drift")
    contract = _read_json(CONTRACT_PATH)
    _require(
        contract.get("experiment_id") == "t2_repair_deadline_oracle_v2"
        and contract.get("status") == "pre_result_freeze",
        "V2 contract identity or status differs",
    )
    predecessor = contract["predecessor"]
    predecessor_path = _project_file(predecessor["contract_path"], "predecessor")
    _require(
        sha256_file(predecessor_path) == predecessor["contract_sha256"],
        "V1 contract hash drift",
    )
    diagnosis = contract["failure_diagnosis"]
    for key, hash_key in (
        ("artifact_manifest_path", "artifact_manifest_sha256"),
        ("diagnostic_path", "diagnostic_sha256"),
    ):
        path = _project_file(diagnosis[key], key)
        _require(sha256_file(path) == diagnosis[hash_key], f"{key} hash drift")
    return contract


def _diagnostic_tree_identities(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    path = _project_file(
        contract["failure_diagnosis"]["artifact_manifest_path"],
        "diagnostic artifact manifest",
    )
    artifact = _read_json(path)
    rows = artifact.get("source_closure", {}).get("strict_validation_run_trees")
    _require(isinstance(rows, list) and len(rows) == 60, "diagnostic tree closure differs")
    result = {row["run_id"]: row for row in rows}
    _require(len(result) == len(rows), "diagnostic tree identities contain duplicates")
    return result


def _serialize_csv(rows: Sequence[dict[str, str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=list(PACKET_OUTCOME_COLUMNS),
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def derive_deadline_sidecar(baseline_dir: Path) -> tuple[bytes, dict[str, Any]]:
    frames_path = baseline_dir / "frames.csv"
    outcomes_path = baseline_dir / "frame_packet_outcomes.csv"
    frames = _rows_by_frame(frames_path)
    outcomes = _rows_by_frame(outcomes_path)
    _require(set(frames) == set(outcomes), "baseline frame/outcome coverage differs")
    output_rows = []
    action_frames = 0
    repair_packets = 0
    late_corrections = 0
    for frame_id in sorted(frames):
        frame = frames[frame_id]
        outcome = outcomes[frame_id]
        packet_count = int(outcome["source_packet_count"])
        _require(
            int(frame["packet_count"]) == packet_count and packet_count > 0,
            "baseline frame/outcome packet count differs",
        )
        universe = set(range(packet_count))
        primary = _indices(outcome["link_1_source_packet_indices"])
        _require(
            primary == _indices(outcome["copy_0_source_packet_indices"])
            == _indices(outcome["received_source_packet_indices"])
            and _indices(outcome["missing_source_packet_indices"])
            == universe - primary
            and not _indices(outcome["copy_1_source_packet_indices"])
            and not _indices(outcome["link_0_source_packet_indices"])
            and not _indices(outcome["received_coded_repair_indices"]),
            "baseline packet-outcome semantics differ",
        )
        first_value = frame["union_first_packet_us"]
        first_arrival_us = int(first_value) if first_value else None
        deadline_at_us = int(frame["generation_time_us"]) + int(frame["deadline_us"])
        late_first = (
            first_arrival_us is not None and first_arrival_us > deadline_at_us
        )
        timely = set() if first_arrival_us is None or late_first else primary
        missing = universe - timely
        late_corrections += int(late_first and bool(primary))
        action_frames += int(bool(missing))
        repair_packets += len(missing)
        formatted_timely = _format_indices(timely)
        output_rows.append(
            {
                "run_id": baseline_dir.name,
                "frame_id": str(frame_id),
                "source_packet_count": str(packet_count),
                "received_source_packet_indices": formatted_timely,
                "missing_source_packet_indices": _format_indices(missing),
                "copy_0_source_packet_indices": formatted_timely,
                "copy_1_source_packet_indices": "",
                "link_0_source_packet_indices": "",
                "link_1_source_packet_indices": formatted_timely,
                "received_coded_repair_indices": "",
            }
        )
    content = _serialize_csv(output_rows)
    return content, {
        "schema_version": 1,
        "derivation_schema_version": 2,
        "baseline_run_id": baseline_dir.name,
        "frame_count": len(output_rows),
        "action_frame_count": action_frames,
        "repair_packet_count": repair_packets,
        "late_first_arrival_correction_count": late_corrections,
        "source_files": {
            "frames.csv": sha256_file(frames_path),
            "frame_packet_outcomes.csv": sha256_file(outcomes_path),
        },
        "sidecar_sha256": hashlib.sha256(content).hexdigest(),
        "sidecar_bytes": len(content),
    }


def _persist_sidecar(
    output_root: Path,
    baseline_dir: Path,
    expected_tree: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    observed_tree = _tree_identity(baseline_dir)
    _require(
        canonical_json(observed_tree) == canonical_json(expected_tree),
        f"baseline run tree drift: {baseline_dir.name}",
    )
    content, provenance = derive_deadline_sidecar(baseline_dir)
    provenance["baseline_tree_identity"] = observed_tree
    relative = Path(SIDE_CAR_DIRECTORY) / baseline_dir.name / SIDE_CAR_FILE
    path = output_root / relative
    provenance_path = path.parent / SIDE_CAR_PROVENANCE
    if path.exists() or provenance_path.exists():
        _require(
            path.is_file()
            and provenance_path.is_file()
            and path.read_bytes() == content
            and _read_json(provenance_path) == provenance,
            f"existing deadline sidecar differs: {baseline_dir.name}",
        )
    else:
        path.parent.mkdir(parents=True, exist_ok=False)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(content)
        os.replace(temporary, path)
        atomic_json(provenance_path, provenance)
    return str(relative), provenance


def _load_baselines(
    baseline_root: Path,
    shard_index: int,
    contract: dict[str, Any],
    diagnostic_trees: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest_path = baseline_root / "experiment_manifest.json"
    expected_hash = contract["input_closure"]["v1_manifest_sha256_by_shard"][
        str(shard_index)
    ]
    _require(sha256_file(manifest_path) == expected_hash, "V1 shard manifest hash drift")
    manifest = _read_json(manifest_path)
    predecessor = contract["predecessor"]
    _require(
        manifest.get("project_commit") == predecessor["simulation_project_commit"]
        and manifest.get("ns3_upstream_commit") == NS3_UPSTREAM_COMMIT
        and manifest.get("shard") == {"index": shard_index, "count": 2},
        "V1 shard identity differs",
    )
    baselines = [row for row in manifest["runs"] if row.get("arm_id") == V1_ARM]
    _require(len(baselines) == 10, "V1 shard must contain ten primary baselines")
    seen_units = set()
    for baseline in baselines:
        run_id = baseline["run_id"]
        run_dir = baseline_root / baseline["directory"]
        validate_run(
            run_dir,
            expected_run_id=run_id,
            expected_project_commit=predecessor["simulation_project_commit"],
            expected_ns3_commit=NS3_UPSTREAM_COMMIT,
        )
        _require(run_id in diagnostic_trees, f"baseline {run_id} absent from diagnosis")
        unit = (
            baseline["scenario"]["scenario_id"],
            baseline["seed"],
            baseline["run"],
        )
        _require(unit not in seen_units, "duplicate V1 baseline unit")
        seen_units.add(unit)
    return sorted(
        baselines,
        key=lambda row: (row["scenario"]["scenario_id"], row["seed"], row["run"]),
    ), {
        "path": str(manifest_path),
        "bytes": manifest_path.stat().st_size,
        "sha256": expected_hash,
    }


def build_specs(
    baselines: Sequence[dict[str, Any]],
    baseline_root: Path,
    output_root: Path,
    simulation_commit: str,
    diagnostic_trees: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    specs = []
    pairings = []
    sources = []
    for baseline in baselines:
        baseline_id = baseline["run_id"]
        relative_source, provenance = _persist_sidecar(
            output_root,
            baseline_root / baseline["directory"],
            diagnostic_trees[baseline_id],
        )
        config = copy.deepcopy(baseline["config"])
        config["policy"] = RUNTIME_POLICY
        prediction = config.setdefault("prediction", {})
        prediction["secondary_airtime_meter_enabled"] = True
        prediction["mechanism_oracle_packet_outcome_file"] = relative_source
        spec = {
            "config": config,
            "seed": baseline["seed"],
            "run": baseline["run"],
            "scenario": copy.deepcopy(baseline["scenario"]),
            "arm_id": V2_ARM,
            "paired_baseline_run_id": baseline_id,
            "deadline_sidecar_sha256": provenance["sidecar_sha256"],
        }
        spec["run_id"] = derive_run_id(
            config,
            spec["seed"],
            spec["run"],
            NS3_UPSTREAM_COMMIT,
            simulation_commit,
            scenario=spec["scenario"],
        )
        specs.append(spec)
        pairings.append(
            {
                "scenario_id": spec["scenario"]["scenario_id"],
                "seed": spec["seed"],
                "run": spec["run"],
                "baseline_run_id": baseline_id,
                "oracle_run_id": spec["run_id"],
                "deadline_sidecar": relative_source,
                "deadline_sidecar_sha256": provenance["sidecar_sha256"],
            }
        )
        sources.append(provenance)
    _require(len({spec["run_id"] for spec in specs}) == len(specs), "duplicate V2 run ID")
    return specs, pairings, sources


def _decorate_manifest_run(
    item: dict[str, Any], specs_by_id: dict[str, dict[str, Any]]
) -> None:
    spec = specs_by_id[item["run_id"]]
    item["arm_id"] = spec["arm_id"]
    item["paired_baseline_run_id"] = spec["paired_baseline_run_id"]
    item["deadline_sidecar_sha256"] = spec["deadline_sidecar_sha256"]


def _link_row(run_dir: Path, link_id: int) -> dict[str, str]:
    selected = [
        row
        for row in _read_csv(run_dir / "link_intervals.csv")
        if int(row["link_id"]) == link_id
    ]
    _require(len(selected) == 1, f"{run_dir}: link row is not unique")
    return selected[0]


def _mac_row(run_dir: Path, link_id: int) -> dict[str, str]:
    selected = [
        row
        for row in _read_csv(run_dir / "mac_summary.csv")
        if int(row["link_id"]) == link_id and int(row["node_id"]) == 0
    ]
    _require(len(selected) == 1, f"{run_dir}: sender MAC row is not unique")
    return selected[0]


def validate_pairs(
    output_root: Path,
    baseline_root: Path,
    pairings: Sequence[dict[str, Any]],
    contract: dict[str, Any],
    shard_index: int,
    simulation_commit: str,
) -> dict[str, Any]:
    evidence = []
    total_frames = 0
    total_actions = 0
    total_packets = 0
    total_primary_drift_frames = 0
    total_primary_drift_packets = 0
    total_deadline_misses = 0
    for pair in pairings:
        baseline_dir = baseline_root / pair["baseline_run_id"]
        oracle_dir = output_root / pair["oracle_run_id"]
        validate_run(
            oracle_dir,
            expected_run_id=pair["oracle_run_id"],
            expected_project_commit=simulation_commit,
            expected_ns3_commit=NS3_UPSTREAM_COMMIT,
        )
        source_path = output_root / pair["deadline_sidecar"]
        _require(
            sha256_file(source_path) == pair["deadline_sidecar_sha256"],
            "deadline sidecar hash drift after execution",
        )
        sources = _rows_by_frame(source_path)
        actions = _rows_by_frame(oracle_dir / "mechanism_t2_actions.csv")
        baseline_outcomes = _rows_by_frame(
            baseline_dir / "frame_packet_outcomes.csv"
        )
        oracle_outcomes = _rows_by_frame(oracle_dir / "frame_packet_outcomes.csv")
        oracle_frames = _rows_by_frame(oracle_dir / "frames.csv")
        coverage = set(sources)
        _require(
            coverage
            == set(actions)
            == set(baseline_outcomes)
            == set(oracle_outcomes)
            == set(oracle_frames),
            "V2 pair frame coverage differs",
        )
        pair_actions = 0
        pair_packets = 0
        pair_drift_frames = 0
        pair_drift_packets = 0
        for frame_id in coverage:
            plan = _indices(sources[frame_id]["missing_source_packet_indices"])
            action = actions[frame_id]
            action_plan = _indices(action["action_packet_indices"])
            active = bool(plan)
            _require(
                action_plan == plan
                and (action["requested"] == "1") == active
                and (action["launched"] == "1") == active,
                f"V2 action differs from deadline plan for frame {frame_id}",
            )
            pair_actions += int(active)
            pair_packets += len(plan)
            baseline_primary = _indices(
                baseline_outcomes[frame_id]["link_1_source_packet_indices"]
            )
            oracle_primary = _indices(
                oracle_outcomes[frame_id]["link_1_source_packet_indices"]
            )
            drift = baseline_primary ^ oracle_primary
            pair_drift_frames += int(bool(drift))
            pair_drift_packets += len(drift)
        baseline_link = _link_row(baseline_dir, 1)
        oracle_link = _link_row(oracle_dir, 1)
        link_fields = (
            "application_bytes_sent",
            "application_bytes_received",
            "phy_tx_time_us",
        )
        _require(
            all(baseline_link[field] == oracle_link[field] for field in link_fields),
            "V2 primary aggregate link counters differ",
        )
        baseline_mac = _mac_row(baseline_dir, 1)
        oracle_mac = _mac_row(oracle_dir, 1)
        mac_fields = ("successful_mpdus", "retransmissions")
        _require(
            all(baseline_mac[field] == oracle_mac[field] for field in mac_fields),
            "V2 primary aggregate MAC counters differ",
        )
        misses = sum(
            int(row["deadline_miss"] == "1") for row in oracle_frames.values()
        )
        total_frames += len(coverage)
        total_actions += pair_actions
        total_packets += pair_packets
        total_primary_drift_frames += pair_drift_frames
        total_primary_drift_packets += pair_drift_packets
        total_deadline_misses += misses
        evidence.append(
            {
                **pair,
                "frame_count": len(coverage),
                "action_frame_count": pair_actions,
                "repair_packet_count": pair_packets,
                "receiver_primary_drift_frames": pair_drift_frames,
                "receiver_primary_drift_packets": pair_drift_packets,
                "deadline_misses": misses,
                "primary_aggregate_link_counters_identical": True,
                "primary_aggregate_mac_counters_identical": True,
                "oracle_tree_identity": _tree_identity(oracle_dir),
            }
        )
    closure = contract["input_closure"]
    _require(
        total_actions
        == closure["deadline_corrected_action_frames_by_shard"][str(shard_index)]
        and total_packets
        == closure["deadline_corrected_repair_packets_by_shard"][str(shard_index)],
        "V2 aggregate action plan differs from the frozen correction",
    )
    result = {
        "schema_version": 1,
        "experiment_id": contract["experiment_id"],
        "contract_sha256": CONTRACT_SHA256,
        "simulation_project_commit": simulation_commit,
        "ns3_upstream_commit": NS3_UPSTREAM_COMMIT,
        "shard": {"index": shard_index, "count": 2},
        "pair_count": len(evidence),
        "frame_count": total_frames,
        "action_frame_count": total_actions,
        "repair_packet_count": total_packets,
        "receiver_primary_drift_frames": total_primary_drift_frames,
        "receiver_primary_drift_packets": total_primary_drift_packets,
        "deadline_misses": total_deadline_misses,
        "all_runs_strictly_validated": True,
        "all_actions_match_deadline_sidecars": True,
        "all_primary_aggregate_link_counters_identical": True,
        "all_primary_aggregate_mac_counters_identical": True,
        "interpretation": contract["validity"]["paired_potential_interpretation"],
        "pairs": sorted(evidence, key=lambda row: (row["scenario_id"], row["seed"])),
    }
    atomic_json(output_root / PAIR_VALIDATION_FILE, result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--shard-index", required=True, type=int, choices=(0, 1))
    parser.add_argument("--workers", type=int, default=10)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)
    _require(args.workers > 0, "workers must be positive")
    runner_commit = _clean_runner_identity()
    contract = validate_contract()
    simulation_commit = _resolve_simulation_commit(
        contract["predecessor"]["simulation_project_commit"], runner_commit
    )
    baseline_root = args.baseline_root.resolve(strict=True)
    output_root = args.output_root.resolve()
    _require(
        baseline_root != output_root and not output_root.is_relative_to(baseline_root),
        "V2 output root must be separate from the V1 baseline root",
    )
    if output_root.exists() and any(output_root.iterdir()) and not args.resume:
        raise FileExistsError("nonempty V2 output root requires --resume")
    output_root.mkdir(parents=True, exist_ok=True)
    diagnostic_trees = _diagnostic_tree_identities(contract)
    baselines, baseline_manifest = _load_baselines(
        baseline_root, args.shard_index, contract, diagnostic_trees
    )
    specs, pairings, source_derivations = build_specs(
        baselines,
        baseline_root,
        output_root,
        simulation_commit,
        diagnostic_trees,
    )
    closure = contract["input_closure"]
    _require(
        sum(row["action_frame_count"] for row in source_derivations)
        == closure["deadline_corrected_action_frames_by_shard"][str(args.shard_index)]
        and sum(row["repair_packet_count"] for row in source_derivations)
        == closure["deadline_corrected_repair_packets_by_shard"][str(args.shard_index)]
        and sum(row["late_first_arrival_correction_count"] for row in source_derivations)
        == closure["late_first_arrival_corrections_by_shard"][str(args.shard_index)],
        "derived V2 sidecar totals differ from the frozen contract",
    )
    identity = {
        "contract_sha256": CONTRACT_SHA256,
        "baseline_manifest": {
            "bytes": baseline_manifest["bytes"],
            "sha256": baseline_manifest["sha256"],
        },
        "shard": {"index": args.shard_index, "count": 2},
        "source_derivations": source_derivations,
    }
    matrix_sha = matrix_sha256(identity)
    experiment = f"t2-repair-deadline-oracle-v2-shard-{args.shard_index}-of-2"
    manifest_path = output_root / "experiment_manifest.json"
    expected_ids = {spec["run_id"] for spec in specs}
    validate_existing_manifest(
        manifest_path,
        experiment,
        matrix_sha,
        simulation_commit,
        expected_ids,
    )
    previous_runs = []
    if manifest_path.exists():
        previous = _read_json(manifest_path)
        previous_runs = copy.deepcopy(previous["runs"])
    for spec in specs:
        completed = output_root / spec["run_id"]
        if completed.exists():
            validate_run(
                completed,
                expected_run_id=spec["run_id"],
                expected_project_commit=simulation_commit,
                expected_ns3_commit=NS3_UPSTREAM_COMMIT,
            )
            spec["completed"] = True
    manifest = build_experiment_manifest(
        experiment,
        matrix_sha,
        CONTRACT_PATH,
        simulation_commit,
        specs,
    )
    if previous_runs:
        generated_runs = {row["run_id"]: row for row in manifest["runs"]}
        for row in previous_runs:
            _require(row["run_id"] in generated_runs, "previous V2 run is outside matrix")
            generated_runs[row["run_id"]] = row
        manifest["runs"] = [
            generated_runs[run_id] for run_id in sorted(generated_runs)
        ]
    manifest["deadline_oracle_contract"] = {
        "id": contract["experiment_id"],
        "path": str(CONTRACT_PATH.relative_to(ROOT)),
        "sha256": CONTRACT_SHA256,
    }
    manifest["shard"] = {"index": args.shard_index, "count": 2}
    manifest["baseline_manifest"] = baseline_manifest
    manifest["pairings"] = pairings
    manifest["source_derivations"] = source_derivations
    manifest["continuation"] = {
        "simulation_project_commit": simulation_commit,
        "orchestration_project_commit": runner_commit,
        "expected_executable_sha256": contract["predecessor"][
            "simulation_executable_sha256"
        ],
    }
    specs_by_id = {spec["run_id"]: spec for spec in specs}
    for item in manifest["runs"]:
        _decorate_manifest_run(item, specs_by_id)
    atomic_json(manifest_path, manifest)
    if args.prepare_only:
        print(
            f"PREPARED shard={args.shard_index} runs={len(specs)} "
            f"repair_packets={sum(row['repair_packet_count'] for row in source_derivations)}",
            flush=True,
        )
        return 0
    executable = _verify_streaming_executable(
        contract["predecessor"]["simulation_executable_sha256"]
    )
    manifest["continuation"]["executable"] = executable
    atomic_json(manifest_path, manifest)
    pending = [spec for spec in specs if not spec.get("completed")]
    failures = []
    with ThreadPoolExecutor(max_workers=min(args.workers, max(1, len(pending)))) as executor:
        futures = {
            executor.submit(
                run_one,
                spec,
                output_root,
                output_root,
                simulation_commit,
            ): spec
            for spec in pending
        }
        for future in as_completed(futures):
            spec = futures[future]
            try:
                item = future.result()
                _decorate_manifest_run(item, specs_by_id)
                manifest["runs"].append(item)
                manifest["runs"].sort(key=lambda row: row["run_id"])
                atomic_json(manifest_path, manifest)
                print(f"COMPLETE {item['run_id']}", flush=True)
            except Exception as error:
                failures.append(f"{spec['run_id']}: {error}")
                print(f"FAILED {spec['run_id']}: {error}", file=sys.stderr, flush=True)
    if failures:
        raise RuntimeError("\n".join(failures))
    validation = validate_pairs(
        output_root,
        baseline_root,
        pairings,
        contract,
        args.shard_index,
        simulation_commit,
    )
    print(
        f"VALIDATED shard={args.shard_index} pairs={validation['pair_count']} "
        f"misses={validation['deadline_misses']} "
        f"primary_drift_frames={validation['receiver_primary_drift_frames']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
