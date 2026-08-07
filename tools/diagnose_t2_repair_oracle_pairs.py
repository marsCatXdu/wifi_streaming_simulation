#!/usr/bin/env python3
"""Diagnose failed primary closure in the frozen T2 repair oracle pairs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Sequence

from validate_outputs import ValidationError, validate_run


ANALYSIS_ID = "t2-repair-oracle-pair-diagnostic-v1"
STR_ARM = "str_mlo_nmaxinflights_1"
BASELINE_ARM = "single_5ghz_no_redundancy"
ORACLE_ARM = "oracle_eventual_missing_repair_t2"
DIAGNOSTIC_ARMS = (STR_ARM, BASELINE_ARM, ORACLE_ARM)


class OraclePairDiagnosticError(RuntimeError):
    """Raised when the frozen oracle evidence cannot be diagnosed safely."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise OraclePairDiagnosticError(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_identity(root: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    file_count = 0
    byte_count = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = str(path.relative_to(root))
        file_digest = _sha256(path)
        size = path.stat().st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OraclePairDiagnosticError(f"cannot read {path}: {error}") from error
    _require(isinstance(value, dict), f"{path}: JSON root is not an object")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
    except (OSError, csv.Error) as error:
        raise OraclePairDiagnosticError(f"cannot read {path}: {error}") from error
    _require(bool(rows), f"{path}: CSV is empty")
    return rows


def _git_identity() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    _require(len(head) == 40 and not status.strip(),
             "diagnostic must run from a clean committed checkout")
    return {"project_commit": head, "worktree_clean": True}


def _indices(value: str) -> set[int]:
    if not value:
        return set()
    try:
        result = {int(item) for item in value.split(";")}
    except ValueError as error:
        raise OraclePairDiagnosticError(
            f"invalid packet-index list: {value}"
        ) from error
    _require(all(index >= 0 for index in result), "negative packet index")
    return result


def _format_indices(values: Iterable[int]) -> str:
    return ";".join(str(value) for value in sorted(values))


def _optional_int(value: str) -> int | None:
    if value == "":
        return None
    try:
        return int(value)
    except ValueError as error:
        raise OraclePairDiagnosticError(f"invalid integer: {value}") from error


def _rows_by_frame(path: Path) -> dict[int, dict[str, str]]:
    rows = _read_csv(path)
    result = {int(row["frame_id"]): row for row in rows}
    _require(len(result) == len(rows), f"{path}: duplicate frame ID")
    return result


def _snapshot_rows(path: Path) -> dict[int, dict[tuple[int, int], dict[str, str]]]:
    result: dict[int, dict[tuple[int, int], dict[str, str]]] = {}
    for row in _read_csv(path):
        frame_id = int(row["frame_id"])
        key = (int(row["path_id"]), int(row["copy_id"]))
        frame = result.setdefault(frame_id, {})
        _require(key not in frame, f"{path}: duplicate snapshot identity")
        frame[key] = row
    return result


def _deadline_missing_set(
    frame: dict[str, str], outcome: dict[str, str]
) -> tuple[set[int], bool]:
    """Return source packets absent at the deadline in a primary-only run."""
    packet_count = int(outcome["source_packet_count"])
    universe = set(range(packet_count))
    first_arrival_us = _optional_int(frame["union_first_packet_us"])
    deadline_at_us = int(frame["generation_time_us"]) + int(frame["deadline_us"])
    late_first_arrival = (
        first_arrival_us is not None and first_arrival_us > deadline_at_us
    )
    if first_arrival_us is None or late_first_arrival:
        return universe, late_first_arrival
    return universe - _indices(outcome["link_1_source_packet_indices"]), False


def _snapshot_differences(
    baseline: dict[tuple[int, int], dict[str, str]],
    oracle: dict[tuple[int, int], dict[str, str]],
) -> list[str]:
    if set(baseline) != set(oracle):
        return ["snapshot_identity"]
    differences: set[str] = set()
    for key in baseline:
        left = baseline[key]
        right = oracle[key]
        if set(left) != set(right):
            differences.add("snapshot_columns")
            continue
        for field in left:
            if field != "run_id" and left[field] != right[field]:
                differences.add(field)
    return sorted(differences)


def _link_row(run_dir: Path, link_id: int) -> dict[str, str]:
    rows = _read_csv(run_dir / "link_intervals.csv")
    selected = [row for row in rows if int(row["link_id"]) == link_id]
    _require(len(selected) == 1, f"{run_dir}: link interval is not unique")
    return selected[0]


def _mac_row(run_dir: Path, link_id: int) -> dict[str, str]:
    rows = _read_csv(run_dir / "mac_summary.csv")
    selected = [
        row
        for row in rows
        if int(row["link_id"]) == link_id and int(row["node_id"]) == 0
    ]
    _require(len(selected) == 1, f"{run_dir}: sender MAC row is not unique")
    return selected[0]


def _validate_and_reduce(job: dict[str, Any]) -> dict[str, Any]:
    run_dir = Path(job["run_dir"])
    try:
        validation = validate_run(
            run_dir,
            expected_run_id=job["run_id"],
            expected_project_commit=job["project_commit"],
            expected_ns3_commit=job["ns3_upstream_commit"],
        )
    except ValidationError as error:
        raise OraclePairDiagnosticError(
            f"{run_dir}: strict validation failed: {error}"
        ) from error
    _require(validation.get("valid") is True, "validator did not return valid")
    config = _read_json(run_dir / "resolved_config.json")
    _require(
        config.get("run_id") == job["run_id"]
        and config.get("seed") == job["seed"]
        and config.get("run") == job["run"]
        and config.get("topology") == job["config"]["topology"]
        and config.get("policy") == job["config"]["policy"],
        f"{run_dir}: resolved identity differs from manifest",
    )
    frames = _read_csv(run_dir / "frames.csv")
    frame_ids = {int(row["frame_id"]) for row in frames}
    _require(len(frame_ids) == len(frames), f"{run_dir}: duplicate frame ID")
    deadline_misses = sum(int(row["deadline_miss"] == "1") for row in frames)
    links = _read_csv(run_dir / "link_intervals.csv")
    airtime_by_link = {
        int(row["link_id"]): float(row["phy_tx_time_us"]) for row in links
    }
    _require(set(airtime_by_link) == {0, 1}, f"{run_dir}: expected two links")
    return {
        "run_id": job["run_id"],
        "arm_id": job["arm_id"],
        "generated_frames": len(frames),
        "deadline_misses": deadline_misses,
        "airtime_link_0_us": airtime_by_link[0],
        "airtime_link_1_us": airtime_by_link[1],
        "sender_airtime_us": sum(airtime_by_link.values()),
    }


def _compare_pair(
    baseline_job: dict[str, Any], oracle_job: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    baseline_dir = Path(baseline_job["run_dir"])
    oracle_dir = Path(oracle_job["run_dir"])
    baseline_outcomes = _rows_by_frame(baseline_dir / "frame_packet_outcomes.csv")
    oracle_outcomes = _rows_by_frame(oracle_dir / "frame_packet_outcomes.csv")
    baseline_frames = _rows_by_frame(baseline_dir / "frames.csv")
    oracle_frames = _rows_by_frame(oracle_dir / "frames.csv")
    actions = _rows_by_frame(oracle_dir / "mechanism_t2_actions.csv")
    baseline_snapshots = _snapshot_rows(baseline_dir / "mechanism_t2_snapshots.csv")
    oracle_snapshots = _snapshot_rows(oracle_dir / "mechanism_t2_snapshots.csv")
    coverage = set(baseline_outcomes)
    _require(
        coverage
        == set(oracle_outcomes)
        == set(baseline_frames)
        == set(oracle_frames)
        == set(actions)
        == set(baseline_snapshots)
        == set(oracle_snapshots),
        "oracle pair frame coverage differs",
    )

    diagnostic_rows: list[dict[str, Any]] = []
    primary_drift_frames = 0
    primary_drift_packets = 0
    baseline_only_packets = 0
    oracle_only_packets = 0
    correction_frames = 0
    omitted_deadline_packets = 0
    snapshot_drift_frames = 0
    flawed_oracle_misses_from_omission = 0
    late_first_consistent_drift_frames = 0
    current_plan_frames = 0
    current_plan_packets = 0
    corrected_plan_frames = 0
    corrected_plan_packets = 0
    first_primary_drift_frame_id: int | None = None
    first_plan_correction_frame_id: int | None = None
    first_snapshot_drift_frame_id: int | None = None
    all_actions_match_sidecar = True

    for frame_id in sorted(coverage):
        baseline = baseline_outcomes[frame_id]
        oracle = oracle_outcomes[frame_id]
        baseline_frame = baseline_frames[frame_id]
        oracle_frame = oracle_frames[frame_id]
        action = actions[frame_id]
        packet_count = int(baseline["source_packet_count"])
        _require(
            packet_count == int(oracle["source_packet_count"]),
            "source packet count differs within an oracle pair",
        )
        universe = set(range(packet_count))
        baseline_primary = _indices(baseline["link_1_source_packet_indices"])
        oracle_primary = _indices(oracle["link_1_source_packet_indices"])
        _require(
            baseline_primary == _indices(baseline["copy_0_source_packet_indices"])
            and oracle_primary == _indices(oracle["copy_0_source_packet_indices"]),
            "primary link/copy packet evidence differs",
        )
        current_plan = _indices(baseline["missing_source_packet_indices"])
        action_plan = _indices(action["action_packet_indices"])
        expected_requested = bool(current_plan)
        action_matches = (
            action_plan == current_plan
            and (action["requested"] == "1") == expected_requested
            and (action["launched"] == "1") == expected_requested
        )
        all_actions_match_sidecar &= action_matches
        corrected_plan, late_first = _deadline_missing_set(baseline_frame, baseline)
        omitted = corrected_plan - current_plan
        unexpected = current_plan - corrected_plan
        _require(
            not unexpected,
            "baseline sidecar marks a timely packet as deadline-missing",
        )
        baseline_only = baseline_primary - oracle_primary
        oracle_only = oracle_primary - baseline_primary
        primary_drift = bool(baseline_only or oracle_only)
        snapshot_fields = _snapshot_differences(
            baseline_snapshots[frame_id], oracle_snapshots[frame_id]
        )
        oracle_union_missing = _indices(oracle["missing_source_packet_indices"])
        oracle_miss = oracle_frame["deadline_miss"] == "1"

        primary_drift_frames += int(primary_drift)
        primary_drift_packets += len(baseline_only | oracle_only)
        baseline_only_packets += len(baseline_only)
        oracle_only_packets += len(oracle_only)
        correction_frames += int(bool(omitted))
        omitted_deadline_packets += len(omitted)
        snapshot_drift_frames += int(bool(snapshot_fields))
        flawed_oracle_misses_from_omission += int(
            oracle_miss and bool(oracle_union_missing & omitted)
        )
        late_first_consistent_drift_frames += int(
            primary_drift and late_first and not oracle_only and baseline_only <= omitted
        )
        current_plan_frames += int(bool(current_plan))
        current_plan_packets += len(current_plan)
        corrected_plan_frames += int(bool(corrected_plan))
        corrected_plan_packets += len(corrected_plan)
        if primary_drift and first_primary_drift_frame_id is None:
            first_primary_drift_frame_id = frame_id
        if omitted and first_plan_correction_frame_id is None:
            first_plan_correction_frame_id = frame_id
        if snapshot_fields and first_snapshot_drift_frame_id is None:
            first_snapshot_drift_frame_id = frame_id

        if primary_drift or omitted or snapshot_fields or not action_matches:
            diagnostic_rows.append(
                {
                    "scenario_id": baseline_job["scenario"]["scenario_id"],
                    "family_id": baseline_job["scenario"]["family_id"],
                    "seed": baseline_job["seed"],
                    "run": baseline_job["run"],
                    "frame_id": frame_id,
                    "packet_count": packet_count,
                    "generation_time_us": baseline_frame["generation_time_us"],
                    "deadline_at_us": (
                        int(baseline_frame["generation_time_us"])
                        + int(baseline_frame["deadline_us"])
                    ),
                    "baseline_first_arrival_us": baseline_frame[
                        "union_first_packet_us"
                    ],
                    "oracle_first_arrival_us": oracle_frame["union_first_packet_us"],
                    "late_first_arrival_artifact": int(late_first),
                    "baseline_primary_indices": _format_indices(baseline_primary),
                    "oracle_primary_indices": _format_indices(oracle_primary),
                    "baseline_only_primary_indices": _format_indices(baseline_only),
                    "oracle_only_primary_indices": _format_indices(oracle_only),
                    "sidecar_plan_indices": _format_indices(current_plan),
                    "deadline_corrected_plan_indices": _format_indices(corrected_plan),
                    "omitted_deadline_packet_indices": _format_indices(omitted),
                    "action_matches_sidecar": int(action_matches),
                    "pre_t2_snapshot_differing_fields": ";".join(snapshot_fields),
                    "oracle_union_missing_indices": _format_indices(
                        oracle_union_missing
                    ),
                    "oracle_deadline_miss": int(oracle_miss),
                }
            )

    baseline_link = _link_row(baseline_dir, 1)
    oracle_link = _link_row(oracle_dir, 1)
    baseline_mac = _mac_row(baseline_dir, 1)
    oracle_mac = _mac_row(oracle_dir, 1)
    result = {
        "scenario_id": baseline_job["scenario"]["scenario_id"],
        "family_id": baseline_job["scenario"]["family_id"],
        "parameter_sample": baseline_job["scenario"]["parameter_sample"],
        "seed": baseline_job["seed"],
        "run": baseline_job["run"],
        "baseline_run_id": baseline_job["run_id"],
        "oracle_run_id": oracle_job["run_id"],
        "frame_count": len(coverage),
        "primary_drift_frames": primary_drift_frames,
        "primary_drift_packets": primary_drift_packets,
        "baseline_only_primary_packets": baseline_only_packets,
        "oracle_only_primary_packets": oracle_only_packets,
        "deadline_plan_correction_frames": correction_frames,
        "omitted_deadline_packets": omitted_deadline_packets,
        "pre_t2_snapshot_drift_frames": snapshot_drift_frames,
        "late_first_consistent_primary_drift_frames": (
            late_first_consistent_drift_frames
        ),
        "other_primary_drift_frames": (
            primary_drift_frames - late_first_consistent_drift_frames
        ),
        "flawed_oracle_misses_containing_omitted_packet": (
            flawed_oracle_misses_from_omission
        ),
        "current_sidecar_plan_frames": current_plan_frames,
        "current_sidecar_plan_packets": current_plan_packets,
        "deadline_corrected_plan_frames": corrected_plan_frames,
        "deadline_corrected_plan_packets": corrected_plan_packets,
        "first_primary_drift_frame_id": first_primary_drift_frame_id,
        "first_deadline_plan_correction_frame_id": first_plan_correction_frame_id,
        "first_pre_t2_snapshot_drift_frame_id": first_snapshot_drift_frame_id,
        "all_actions_match_baseline_sidecar": all_actions_match_sidecar,
        "primary_link_application_bytes_sent_delta": (
            int(oracle_link["application_bytes_sent"])
            - int(baseline_link["application_bytes_sent"])
        ),
        "primary_link_application_bytes_received_delta": (
            int(oracle_link["application_bytes_received"])
            - int(baseline_link["application_bytes_received"])
        ),
        "primary_link_phy_tx_time_us_delta": (
            int(oracle_link["phy_tx_time_us"]) - int(baseline_link["phy_tx_time_us"])
        ),
        "primary_mac_successful_mpdus_delta": (
            int(oracle_mac["successful_mpdus"])
            - int(baseline_mac["successful_mpdus"])
        ),
        "primary_mac_retransmissions_delta": (
            int(oracle_mac["retransmissions"])
            - int(baseline_mac["retransmissions"])
        ),
    }
    _require(all_actions_match_sidecar, "oracle action differs from sidecar")
    return result, diagnostic_rows


def _load_campaign(
    shard_roots: Sequence[Path],
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], dict[str, Any]]], dict[str, Any]]:
    _require(len(shard_roots) == 2, "exactly two shard roots are required")
    validation_jobs: list[dict[str, Any]] = []
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    sources = []
    shards: set[tuple[int, int]] = set()
    commits: set[str] = set()
    units: dict[tuple[str, int, int], dict[str, dict[str, Any]]] = {}
    for root in shard_roots:
        manifest_path = root / "experiment_manifest.json"
        manifest = _read_json(manifest_path)
        shard = manifest.get("shard", {})
        shards.add((int(shard.get("index", -1)), int(shard.get("count", -1))))
        _require(
            manifest.get("schema_version") == 2
            and manifest.get("mechanism_contract", {}).get("id")
            == "t2_repair_mechanism_v1"
            and len(manifest.get("runs", [])) == 60,
            f"{root}: incompatible mechanism manifest",
        )
        commits.add(manifest["project_commit"])
        for item in manifest["runs"]:
            arm = item.get("arm_id")
            if arm not in DIAGNOSTIC_ARMS:
                continue
            job = {
                **item,
                "run_dir": str(root / item["directory"]),
                "project_commit": manifest["project_commit"],
                "ns3_upstream_commit": manifest["ns3_upstream_commit"],
            }
            key = (item["scenario"]["scenario_id"], item["seed"], item["run"])
            _require(arm not in units.setdefault(key, {}), "duplicate unit arm")
            units[key][arm] = job
            validation_jobs.append(job)
        sources.append(
            {
                "root": str(root),
                "manifest_path": str(manifest_path),
                "manifest_sha256": _sha256(manifest_path),
                "manifest_bytes": manifest_path.stat().st_size,
                "oracle_pair_validation_present": (
                    root / "oracle_pair_validation.json"
                ).exists(),
            }
        )
    _require(shards == {(0, 2), (1, 2)}, "shard identities differ")
    _require(len(commits) == 1, "simulation commits differ")
    _require(
        len(units) == 20
        and all(set(arms) == set(DIAGNOSTIC_ARMS) for arms in units.values()),
        "diagnostic STR/baseline/oracle grid is incomplete",
    )
    for arms in units.values():
        oracle = arms[ORACLE_ARM]
        baseline = arms[BASELINE_ARM]
        _require(
            oracle.get("paired_baseline_run_id") == baseline["run_id"],
            "oracle manifest pairing differs",
        )
        pairs.append((baseline, oracle))
    run_trees = [
        _tree_identity(Path(job["run_dir"]))
        for job in sorted(validation_jobs, key=lambda item: item["run_id"])
    ]
    return validation_jobs, pairs, {
        "simulation_project_commit": next(iter(commits)),
        "shards": sources,
        "pair_count": len(pairs),
        "strict_validation_run_trees": run_trees,
    }


def _strict_validate(
    jobs: Sequence[dict[str, Any]], workers: int
) -> dict[str, dict[str, Any]]:
    if workers == 1:
        results = {job["run_id"]: _validate_and_reduce(job) for job in jobs}
        _require(len(results) == len(jobs), "strict validation result count differs")
        return results
    results: dict[str, dict[str, Any]] = {}
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_validate_and_reduce, job): job for job in jobs
        }
        for future in as_completed(futures):
            job = futures[future]
            result = future.result()
            results[job["run_id"]] = result
    _require(len(results) == len(jobs), "strict validation result count differs")
    return results


def _metric_summary(
    jobs: Sequence[dict[str, Any]], observations: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    result = {}
    for arm in DIAGNOSTIC_ARMS:
        members = [observations[job["run_id"]] for job in jobs if job["arm_id"] == arm]
        generated = sum(row["generated_frames"] for row in members)
        misses = sum(row["deadline_misses"] for row in members)
        result[arm] = {
            "runs": len(members),
            "generated_frames": generated,
            "deadline_misses": misses,
            "deadline_miss_rate": misses / generated,
            "mean_sender_airtime_us": sum(
                row["sender_airtime_us"] for row in members
            )
            / len(members),
            "mean_primary_link_airtime_us": sum(
                row["airtime_link_1_us"] for row in members
            )
            / len(members),
            "mean_secondary_link_airtime_us": sum(
                row["airtime_link_0_us"] for row in members
            )
            / len(members),
        }
    result["observed_flawed_oracle_vs_str"] = {
        "miss_rate_delta": (
            result[ORACLE_ARM]["deadline_miss_rate"]
            - result[STR_ARM]["deadline_miss_rate"]
        ),
        "sender_airtime_ratio": (
            result[ORACLE_ARM]["mean_sender_airtime_us"]
            / result[STR_ARM]["mean_sender_airtime_us"]
        ),
    }
    result["primary_only_airtime_floor_vs_str"] = (
        result[BASELINE_ARM]["mean_sender_airtime_us"]
        / result[STR_ARM]["mean_sender_airtime_us"]
    )
    return result


def _aggregate_pairs(pair_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    fields = (
        "frame_count",
        "primary_drift_frames",
        "primary_drift_packets",
        "baseline_only_primary_packets",
        "oracle_only_primary_packets",
        "deadline_plan_correction_frames",
        "omitted_deadline_packets",
        "pre_t2_snapshot_drift_frames",
        "late_first_consistent_primary_drift_frames",
        "other_primary_drift_frames",
        "flawed_oracle_misses_containing_omitted_packet",
        "current_sidecar_plan_frames",
        "current_sidecar_plan_packets",
        "deadline_corrected_plan_frames",
        "deadline_corrected_plan_packets",
    )
    result = {field: sum(int(row[field]) for row in pair_rows) for field in fields}
    result.update(
        {
            "pair_count": len(pair_rows),
            "pairs_with_primary_drift": sum(
                int(row["primary_drift_frames"] > 0) for row in pair_rows
            ),
            "pairs_requiring_deadline_plan_correction": sum(
                int(row["deadline_plan_correction_frames"] > 0) for row in pair_rows
            ),
            "all_actions_match_baseline_sidecar": all(
                row["all_actions_match_baseline_sidecar"] for row in pair_rows
            ),
            "all_primary_link_aggregate_counters_identical": all(
                row["primary_link_application_bytes_sent_delta"] == 0
                and row["primary_link_application_bytes_received_delta"] == 0
                and row["primary_link_phy_tx_time_us_delta"] == 0
                and row["primary_mac_successful_mpdus_delta"] == 0
                and row["primary_mac_retransmissions_delta"] == 0
                for row in pair_rows
            ),
        }
    )
    result["deadline_plan_packet_increase_ratio"] = (
        result["deadline_corrected_plan_packets"]
        / result["current_sidecar_plan_packets"]
    )
    return result


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    _require(bool(rows), f"refusing to write empty table {path.name}")
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _report_markdown(report: dict[str, Any]) -> str:
    aggregate = report["aggregate_pair_diagnostic"]
    metrics = report["observed_policy_metrics"]
    oracle = metrics[ORACLE_ARM]
    baseline = metrics[BASELINE_ARM]
    str_row = metrics[STR_ARM]
    contrast = metrics["observed_flawed_oracle_vs_str"]
    return "\n".join(
        [
            "# T2 oracle pair-closure diagnosis",
            "",
            "**Status: the existing oracle arm is not a valid deadline-repair oracle.**",
            "",
            f"All {report['strict_validated_run_count']} STR/baseline/oracle runs pass "
            "individual strict validation. All oracle actions exactly match the frozen "
            "baseline sidecar, but the sidecar is not consistently deadline-censored.",
            "",
            "## Exact closure result",
            "",
            f"- {aggregate['primary_drift_frames']} of "
            f"{aggregate['frame_count']:,} paired frames have primary-set drift, across "
            f"{aggregate['pairs_with_primary_drift']} pairs.",
            f"- Exactly {aggregate['deadline_plan_correction_frames']} frames omit "
            f"{aggregate['omitted_deadline_packets']} packets that arrived only after "
            "the deadline in the baseline.",
            f"- The sidecar requests {aggregate['current_sidecar_plan_packets']:,} "
            f"repair packets. A deadline-correct plan requests "
            f"{aggregate['deadline_corrected_plan_packets']:,}, an increase of "
            f"{100 * (aggregate['deadline_plan_packet_increase_ratio'] - 1):.2f}%.",
            f"- {aggregate['late_first_consistent_primary_drift_frames']} primary-set "
            "differences have the direct late-first censoring signature; "
            f"{aggregate['other_primary_drift_frames']} do not.",
            f"- Pre-T2 snapshot drift occurs in "
            f"{aggregate['pre_t2_snapshot_drift_frames']} frames.",
            "- Primary-link aggregate application bytes, PHY TX airtime, successful "
            "MPDUs, and retransmissions are identical in every pair: "
            f"{aggregate['all_primary_link_aggregate_counters_identical']}.",
            "",
            "The receiver creates frame state on first packet arrival. If the first "
            "primary packet arrives after the deadline, the primary-only run records "
            "that late packet before immediate finalization. A timely secondary repair "
            "creates state earlier, so the paired oracle run finalizes at the deadline "
            "and ignores the same late primary packet. The sidecar consequently omits "
            "that packet from the repair plan. Later receiver-set and snapshot "
            "differences cannot be treated as an exact packet counterfactual, even "
            "though the aggregate primary transmission counters remain identical.",
            "",
            "## Observed flawed replay (not an oracle estimate)",
            "",
            "| Arm | Misses | Miss rate | Sender airtime |",
            "| --- | ---: | ---: | ---: |",
            f"| STR MLO | {str_row['deadline_misses']:,} | "
            f"{100 * str_row['deadline_miss_rate']:.4f}% | "
            f"{str_row['mean_sender_airtime_us'] / 1000:.2f} ms/run |",
            f"| 5 GHz only | {baseline['deadline_misses']:,} | "
            f"{100 * baseline['deadline_miss_rate']:.4f}% | "
            f"{baseline['mean_sender_airtime_us'] / 1000:.2f} ms/run |",
            f"| Existing flawed repair replay | {oracle['deadline_misses']:,} | "
            f"{100 * oracle['deadline_miss_rate']:.4f}% | "
            f"{oracle['mean_sender_airtime_us'] / 1000:.2f} ms/run |",
            "",
            f"The flawed replay is {100 * contrast['miss_rate_delta']:+.4f} percentage "
            f"points versus STR at {contrast['sender_airtime_ratio']:.4f}x sender "
            "airtime. The primary-only airtime floor is already "
            f"{metrics['primary_only_airtime_floor_vs_str']:.4f}x STR.",
            "",
            "These observed oracle-arm outcomes are retained only to diagnose the "
            "failure. They must not be used as the privileged packet-repair ceiling.",
            "",
            "## Repair boundary",
            "",
            "A valid replay must define the privileged plan as every primary packet "
            "absent at the frame deadline. For a baseline whose first arrival is late, "
            "that means all source packets, not the sidecar complement after lazy state "
            "creation. The 100 factual runs remain valid and need no rerun.",
            "",
        ]
    )


def _artifact_manifest(
    output_dir: Path, source: dict[str, Any], analyzer: dict[str, Any]
) -> dict[str, Any]:
    artifacts = {}
    for path in sorted(output_dir.rglob("*")):
        if path.is_file() and path.name != "analysis_artifact_manifest.json":
            relative = str(path.relative_to(output_dir))
            artifacts[relative] = {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
    return {
        "schema_version": 1,
        "analysis": ANALYSIS_ID,
        "analyzer_identity": analyzer,
        "source_closure": source,
        "artifacts": artifacts,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shard-root", action="append", required=True, type=Path, dest="shard_roots"
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--workers", type=int, default=min(20, os.cpu_count() or 1)
    )
    args = parser.parse_args(argv)
    _require(args.workers > 0, "workers must be positive")
    analyzer = _git_identity()
    jobs, pairs, source = _load_campaign(args.shard_roots)
    observations = _strict_validate(jobs, args.workers)
    pair_summaries = []
    diagnostic_frames = []
    for baseline, oracle in pairs:
        pair, frames = _compare_pair(baseline, oracle)
        pair_summaries.append(pair)
        diagnostic_frames.extend(frames)
    pair_summaries.sort(key=lambda row: (row["scenario_id"], row["seed"], row["run"]))
    diagnostic_frames.sort(
        key=lambda row: (row["scenario_id"], row["seed"], row["run"], row["frame_id"])
    )
    report = {
        "schema_version": 1,
        "analysis": ANALYSIS_ID,
        "analyzer_identity": analyzer,
        "source_closure": source,
        "strict_validated_run_count": len(observations),
        "aggregate_pair_diagnostic": _aggregate_pairs(pair_summaries),
        "observed_policy_metrics": _metric_summary(jobs, observations),
        "pairs": pair_summaries,
        "diagnostic_frame_count": len(diagnostic_frames),
        "diagnostic_frame_table": "diagnostic_frames.csv",
        "interpretation": {
            "existing_oracle_is_admissible": False,
            "reason": (
                "the baseline sidecar includes a first primary packet that arrived "
                "after the deadline, so its complement under-repairs collapse frames"
            ),
            "factual_runs_require_rerun": False,
        },
    }
    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=args.output_dir.parent, prefix=f".{args.output_dir.name}."
    ) as temporary:
        staging = Path(temporary) / args.output_dir.name
        staging.mkdir()
        (staging / "oracle_pair_diagnostic.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (staging / "REPORT.md").write_text(
            _report_markdown(report), encoding="utf-8"
        )
        _write_csv(staging / "pair_summary.csv", pair_summaries)
        _write_csv(staging / "diagnostic_frames.csv", diagnostic_frames)
        manifest = _artifact_manifest(staging, source, analyzer)
        (staging / "analysis_artifact_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        _require(not args.output_dir.exists(), "output directory already exists")
        os.replace(staging, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
