#!/usr/bin/env python3
"""Analyze factual mechanism arms with the corrected deadline-repair replay."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Sequence

import analyze_t2_repair_mechanism as mechanism
import run_t2_repair_deadline_oracle_v2 as runner


ANALYSIS_ID = "t2-repair-deadline-oracle-analysis-v2"
SIMULATION_PROJECT_COMMIT = "791bb2d27fb910be1393227de251c7c74e7f4b40"
ORCHESTRATION_PROJECT_COMMIT = "fcb8474185a327b031085721c3033e60018f9daf"
REJECTED_V1_ARM = "oracle_eventual_missing_repair_t2"
V2_SOURCE_ARM = "oracle_deadline_missing_repair_t2_v2"
ANALYSIS_REPAIR_ARM = REJECTED_V1_ARM
FACTUAL_ARMS = tuple(arm for arm in mechanism.ARM_ORDER if arm != REJECTED_V1_ARM)
ARM_LABELS = {
    **mechanism.ARM_LABELS,
    ANALYSIS_REPAIR_ARM: "Deadline repair T2",
}


def _validate_recovery(
    root: Path,
    manifest: dict[str, Any],
    shard_index: int,
) -> dict[str, Any]:
    path = root / "attempt_recovery.json"
    recovery = mechanism._read_json(path)
    expected_count = mechanism.RECOVERY_COUNTS[shard_index]
    recovered = recovery.get("recovered")
    mechanism._require(
        recovery.get("schema_version") == 1
        and recovery.get("simulation_project_commit") == SIMULATION_PROJECT_COMMIT
        and recovery.get("validator_project_commit")
        == mechanism.RECOVERY_ORCHESTRATION_COMMIT
        and recovery.get("ns3_upstream_commit")
        == manifest.get("ns3_upstream_commit")
        and recovery.get("recovered_count") == expected_count
        and recovery.get("all_recovered_attempts_strictly_validated") is True
        and isinstance(recovered, list)
        and len(recovered) == expected_count,
        f"{root}: FEC recovery closure differs",
    )
    manifest_ids = {item["run_id"] for item in manifest["runs"]}
    recovered_ids = set()
    for row in recovered:
        mechanism._require(
            isinstance(row, dict)
            and row.get("arm_id") == "ideal_systematic_fec_12p5_t2"
            and row.get("state") == "promoted"
            and isinstance(row.get("run_id"), str)
            and re.fullmatch(r"[0-9a-f]{20}", row["run_id"]) is not None
            and isinstance(row.get("file_count"), int)
            and row["file_count"] > 0
            and isinstance(row.get("bytes"), int)
            and row["bytes"] > 0
            and isinstance(row.get("tree_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", row["tree_sha256"]) is not None,
            f"{root}: invalid FEC recovery row",
        )
        recovered_ids.add(row["run_id"])
    mechanism._require(
        len(recovered_ids) == expected_count and recovered_ids <= manifest_ids,
        f"{root}: FEC recovery run identities differ",
    )
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": mechanism._sha256(path),
        "recovered_count": expected_count,
        "all_promoted": True,
        "orchestration_project_commit": mechanism.RECOVERY_ORCHESTRATION_COMMIT,
    }


def _roots_by_shard(
    roots: Sequence[Path],
    label: str,
) -> dict[int, tuple[Path, dict[str, Any]]]:
    mechanism._require(len(roots) == 2, f"exactly two {label} roots are required")
    result: dict[int, tuple[Path, dict[str, Any]]] = {}
    for root in roots:
        manifest = mechanism._read_json(root / "experiment_manifest.json")
        shard = manifest.get("shard", {})
        identity = (int(shard.get("index", -1)), int(shard.get("count", -1)))
        mechanism._require(identity[1] == 2 and identity[0] in {0, 1},
                           f"{root}: invalid {label} shard identity")
        mechanism._require(identity[0] not in result,
                           f"duplicate {label} shard {identity[0]}")
        result[identity[0]] = (root, manifest)
    mechanism._require(set(result) == {0, 1}, f"{label} shard set is incomplete")
    return result


def _validate_v1_manifest(
    root: Path,
    manifest: dict[str, Any],
    shard_index: int,
    contract: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest_path = root / "experiment_manifest.json"
    expected_hash = contract["input_closure"]["v1_manifest_sha256_by_shard"][
        str(shard_index)
    ]
    mechanism._require(
        mechanism._sha256(manifest_path) == expected_hash,
        f"{root}: V1 manifest hash drift",
    )
    mechanism._require(
        manifest.get("schema_version") == 2
        and manifest.get("mechanism_contract", {}).get("id")
        == "t2_repair_mechanism_v1"
        and manifest.get("project_commit") == SIMULATION_PROJECT_COMMIT
        and len(manifest.get("runs", [])) == 60,
        f"{root}: incompatible V1 factual manifest",
    )
    continuation = manifest.get("continuation", {})
    executable = continuation.get("expected_executable", {})
    mechanism._require(
        continuation.get("simulation_project_commit")
        == SIMULATION_PROJECT_COMMIT
        and continuation.get("orchestration_project_commit")
        == mechanism.RECOVERY_ORCHESTRATION_COMMIT
        and continuation.get("recovery_report") is None
        and executable.get("sha256") == mechanism.SIMULATION_EXECUTABLE_SHA256
        and isinstance(executable.get("bytes"), int)
        and executable["bytes"] > 0,
        f"{root}: V1 continuation identity differs",
    )
    factual = [
        item for item in manifest["runs"] if item.get("arm_id") != REJECTED_V1_ARM
    ]
    rejected = [
        item for item in manifest["runs"] if item.get("arm_id") == REJECTED_V1_ARM
    ]
    mechanism._require(
        len(factual) == 50
        and len(rejected) == 10
        and all(item.get("arm_id") in FACTUAL_ARMS for item in factual),
        f"{root}: V1 factual/rejected-arm split differs",
    )
    recovery = _validate_recovery(root, manifest, shard_index)
    return factual, {
        "root": str(root),
        "manifest": {
            "path": str(manifest_path),
            "bytes": manifest_path.stat().st_size,
            "sha256": expected_hash,
        },
        "attempt_recovery": recovery,
        "included_factual_runs": len(factual),
        "excluded_v1_oracle_runs": len(rejected),
    }


def _pair_projection(pair: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "scenario_id",
        "seed",
        "run",
        "baseline_run_id",
        "oracle_run_id",
        "frame_count",
        "action_frame_count",
        "repair_packet_count",
        "receiver_primary_drift_frames",
        "receiver_primary_drift_packets",
        "deadline_misses",
    )
    return {field: pair[field] for field in fields}


def _validate_v2_manifest(
    root: Path,
    manifest: dict[str, Any],
    baseline_root: Path,
    baseline_manifest: dict[str, Any],
    shard_index: int,
    contract: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    manifest_path = root / "experiment_manifest.json"
    pair_path = root / runner.PAIR_VALIDATION_FILE
    pair_validation = mechanism._read_json(pair_path)
    expected_v1_hash = contract["input_closure"][
        "v1_manifest_sha256_by_shard"
    ][str(shard_index)]
    continuation = manifest.get("continuation", {})
    executable = continuation.get("executable", {})
    mechanism._require(
        manifest.get("schema_version") == 2
        and manifest.get("experiment")
        == f"t2-repair-deadline-oracle-v2-shard-{shard_index}-of-2"
        and manifest.get("project_commit") == SIMULATION_PROJECT_COMMIT
        and manifest.get("ns3_upstream_commit")
        == baseline_manifest.get("ns3_upstream_commit")
        and manifest.get("deadline_oracle_contract", {}).get("id")
        == contract["experiment_id"]
        and manifest.get("deadline_oracle_contract", {}).get("sha256")
        == runner.CONTRACT_SHA256
        and manifest.get("baseline_manifest", {}).get("sha256")
        == expected_v1_hash
        and continuation.get("simulation_project_commit")
        == SIMULATION_PROJECT_COMMIT
        and continuation.get("orchestration_project_commit")
        == ORCHESTRATION_PROJECT_COMMIT
        and continuation.get("expected_executable_sha256")
        == mechanism.SIMULATION_EXECUTABLE_SHA256
        and executable.get("sha256") == mechanism.SIMULATION_EXECUTABLE_SHA256
        and isinstance(executable.get("bytes"), int)
        and executable["bytes"] > 0
        and len(manifest.get("runs", [])) == 10,
        f"{root}: incompatible or incomplete V2 manifest",
    )
    closure = contract["input_closure"]
    mechanism._require(
        pair_validation.get("schema_version") == 1
        and pair_validation.get("experiment_id") == contract["experiment_id"]
        and pair_validation.get("contract_sha256") == runner.CONTRACT_SHA256
        and pair_validation.get("simulation_project_commit")
        == SIMULATION_PROJECT_COMMIT
        and pair_validation.get("ns3_upstream_commit")
        == baseline_manifest.get("ns3_upstream_commit")
        and pair_validation.get("shard") == {"index": shard_index, "count": 2}
        and pair_validation.get("pair_count") == 10
        and pair_validation.get("frame_count") == 18_000
        and pair_validation.get("action_frame_count")
        == closure["deadline_corrected_action_frames_by_shard"][str(shard_index)]
        and pair_validation.get("repair_packet_count")
        == closure["deadline_corrected_repair_packets_by_shard"][str(shard_index)]
        and pair_validation.get("all_runs_strictly_validated") is True
        and pair_validation.get("all_actions_match_deadline_sidecars") is True
        and pair_validation.get("all_primary_aggregate_link_counters_identical")
        is True
        and pair_validation.get("all_primary_aggregate_mac_counters_identical")
        is True,
        f"{root}: V2 pair closure differs",
    )
    v1_by_id = {item["run_id"]: item for item in baseline_manifest["runs"]}
    v2_by_id = {item["run_id"]: item for item in manifest["runs"]}
    mechanism._require(
        len(v2_by_id) == 10
        and all(item.get("arm_id") == V2_SOURCE_ARM for item in v2_by_id.values()),
        f"{root}: V2 run identities differ",
    )
    manifest_pairings = {
        item["oracle_run_id"]: item for item in manifest.get("pairings", [])
    }
    validation_pairs = {
        item["oracle_run_id"]: item for item in pair_validation.get("pairs", [])
    }
    mechanism._require(
        set(manifest_pairings) == set(v2_by_id) == set(validation_pairs),
        f"{root}: V2 pairing coverage differs",
    )
    jobs = []
    pair_rows = []
    for oracle_id in sorted(v2_by_id):
        item = v2_by_id[oracle_id]
        pairing = manifest_pairings[oracle_id]
        validation = validation_pairs[oracle_id]
        baseline_id = pairing["baseline_run_id"]
        mechanism._require(
            baseline_id in v1_by_id
            and v1_by_id[baseline_id].get("arm_id")
            == "single_5ghz_no_redundancy"
            and item.get("paired_baseline_run_id") == baseline_id
            and pairing["scenario_id"] == item["scenario"]["scenario_id"]
            and pairing["seed"] == item["seed"]
            and pairing["run"] == item["run"]
            and validation["baseline_run_id"] == baseline_id,
            f"{root}: V2 baseline pairing differs for {oracle_id}",
        )
        sidecar = root / pairing["deadline_sidecar"]
        mechanism._require(
            mechanism._sha256(sidecar) == pairing["deadline_sidecar_sha256"]
            == item["deadline_sidecar_sha256"],
            f"{root}: deadline sidecar hash drift for {oracle_id}",
        )
        jobs.append(
            {
                **item,
                "arm_id": ANALYSIS_REPAIR_ARM,
                "source_arm_id": V2_SOURCE_ARM,
                "run_dir": str(root / item["directory"]),
                "project_commit": manifest["project_commit"],
                "ns3_upstream_commit": manifest["ns3_upstream_commit"],
            }
        )
        pair_rows.append({"shard_index": shard_index, **_pair_projection(validation)})
    source = {
        "root": str(root),
        "manifest": {
            "path": str(manifest_path),
            "bytes": manifest_path.stat().st_size,
            "sha256": mechanism._sha256(manifest_path),
        },
        "pair_validation": {
            "path": str(pair_path),
            "bytes": pair_path.stat().st_size,
            "sha256": mechanism._sha256(pair_path),
        },
        "included_corrected_runs": len(jobs),
        "receiver_primary_drift_frames": pair_validation[
            "receiver_primary_drift_frames"
        ],
        "receiver_primary_drift_packets": pair_validation[
            "receiver_primary_drift_packets"
        ],
        "baseline_root": str(baseline_root),
    }
    return jobs, source, pair_rows


def _load_jobs(
    factual_roots: Sequence[Path],
    oracle_roots: Sequence[Path],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    contract = runner.validate_contract()
    factual = _roots_by_shard(factual_roots, "factual")
    corrected = _roots_by_shard(oracle_roots, "corrected")
    jobs: list[dict[str, Any]] = []
    source_shards = []
    pair_rows = []
    seen: set[str] = set()
    for shard_index in (0, 1):
        factual_root, factual_manifest = factual[shard_index]
        corrected_root, corrected_manifest = corrected[shard_index]
        factual_items, factual_source = _validate_v1_manifest(
            factual_root,
            factual_manifest,
            shard_index,
            contract,
        )
        for item in factual_items:
            run_id = item["run_id"]
            mechanism._require(run_id not in seen, f"duplicate run ID {run_id}")
            seen.add(run_id)
            jobs.append(
                {
                    **item,
                    "run_dir": str(factual_root / item["directory"]),
                    "project_commit": factual_manifest["project_commit"],
                    "ns3_upstream_commit": factual_manifest["ns3_upstream_commit"],
                }
            )
        corrected_jobs, corrected_source, corrected_pairs = _validate_v2_manifest(
            corrected_root,
            corrected_manifest,
            factual_root,
            factual_manifest,
            shard_index,
            contract,
        )
        for item in corrected_jobs:
            mechanism._require(item["run_id"] not in seen,
                               f"duplicate run ID {item['run_id']}")
            seen.add(item["run_id"])
            jobs.append(item)
        pair_rows.extend(corrected_pairs)
        source_shards.append(
            {
                "shard_index": shard_index,
                "factual": factual_source,
                "corrected": corrected_source,
            }
        )
    counts = {arm: 0 for arm in mechanism.ARM_ORDER}
    units: dict[tuple[str, int, int], set[str]] = {}
    for job in jobs:
        counts[job["arm_id"]] += 1
        key = (job["scenario"]["scenario_id"], job["seed"], job["run"])
        units.setdefault(key, set()).add(job["arm_id"])
    mechanism._require(
        len(jobs) == 120
        and all(count == 20 for count in counts.values())
        and len(units) == 20
        and all(arms == set(mechanism.ARM_ORDER) for arms in units.values()),
        "combined factual/V2 paired grid is incomplete",
    )
    return jobs, {
        "simulation_project_commit": SIMULATION_PROJECT_COMMIT,
        "analysis_contract": {
            "path": str(runner.CONTRACT_PATH),
            "bytes": runner.CONTRACT_PATH.stat().st_size,
            "sha256": runner.CONTRACT_SHA256,
        },
        "shards": source_shards,
        "included_factual_runs": 100,
        "included_corrected_runs": 20,
        "excluded_rejected_v1_oracle_runs": 20,
        "paired_unit_count": len(units),
        "paired_potential_interpretation": contract["validity"][
            "paired_potential_interpretation"
        ],
    }, sorted(pair_rows, key=lambda row: (row["scenario_id"], row["seed"]))


def _transition_decomposition(
    grid: dict[tuple[str, int, int], dict[str, dict[str, Any]]]
) -> dict[str, int | float]:
    primary_misses = 0
    repair_misses = 0
    rescued = 0
    introduced = 0
    both_miss = 0
    both_success = 0
    for unit in grid.values():
        primary = unit["single_5ghz_no_redundancy"]["miss_flags"]
        repair = unit[ANALYSIS_REPAIR_ARM]["miss_flags"]
        mechanism._require(len(primary) == len(repair),
                           "primary/repair frame count differs")
        for primary_miss, repair_miss in zip(primary, repair):
            primary_misses += int(primary_miss)
            repair_misses += int(repair_miss)
            rescued += int(primary_miss and not repair_miss)
            introduced += int(not primary_miss and repair_miss)
            both_miss += int(primary_miss and repair_miss)
            both_success += int(not primary_miss and not repair_miss)
    return {
        "primary_only_deadline_misses": primary_misses,
        "deadline_repair_deadline_misses": repair_misses,
        "primary_misses_rescued": rescued,
        "primary_successes_changed_to_miss": introduced,
        "both_miss": both_miss,
        "both_success": both_success,
        "net_misses_avoided": primary_misses - repair_misses,
        "rescue_fraction_of_primary_misses": (
            rescued / primary_misses if primary_misses else 0.0
        ),
    }


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    mechanism._require(bool(rows), f"refusing to write empty table {path.name}")
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _raw_tree_rows(
    factual_roots: Sequence[Path],
    oracle_roots: Sequence[Path],
) -> list[dict[str, Any]]:
    rows = []
    for source_kind, roots in (
        ("factual_v1", factual_roots),
        ("deadline_repair_v2", oracle_roots),
    ):
        indexed = _roots_by_shard(roots, source_kind)
        for shard_index, (root, _) in indexed.items():
            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                rows.append(
                    {
                        "source_kind": source_kind,
                        "shard_index": shard_index,
                        "relative_path": str(path.relative_to(root)),
                        "bytes": path.stat().st_size,
                        "sha256": mechanism._sha256(path),
                    }
                )
    return rows


def _render_drift_plot(pair_rows: Sequence[dict[str, Any]], output: Path) -> list[Path]:
    _, plt = mechanism._plot_modules()
    scenarios = list(mechanism.SCENARIO_LABELS)
    frame_rates = []
    for scenario in scenarios:
        members = [row for row in pair_rows if row["scenario_id"] == scenario]
        frames = sum(int(row["frame_count"]) for row in members)
        frame_rates.append(
            100 * sum(int(row["receiver_primary_drift_frames"]) for row in members)
            / frames
        )
    figure, axis = plt.subplots(figsize=(9.2, 5.2))
    labels = [mechanism.SCENARIO_LABELS[scenario] for scenario in scenarios]
    bars = axis.bar(labels, frame_rates, color="#e45756")
    axis.bar_label(bars, labels=[f"{value:.1f}%" for value in frame_rates], padding=3)
    axis.set_ylabel("Frames with receiver primary-set drift (%)")
    axis.set_title("Paired-potential boundary by scenario")
    axis.tick_params(axis="x", rotation=20)
    axis.grid(axis="y", alpha=0.25)
    paths = mechanism._finish(figure, output, "receiver_primary_drift_by_scenario")
    plt.close(figure)
    return paths


def _report_markdown(report: dict[str, Any]) -> str:
    aggregate = report["aggregate"]
    decisive = report["decisive_test"]
    lines = [
        "# Deadline-correct T2 packet-repair mechanism result",
        "",
        "**Status: complete paired-potential replay; stop before the next iteration.**",
        "",
        "This report combines the 100 immutable factual runs with 20 corrected",
        "deadline-repair runs. All 20 rejected V1 oracle outputs remain excluded.",
        "The repair arm is privileged and nondeployable. Receiver primary packet",
        "sets may drift, so it is not described as an exact within-run oracle.",
        "",
        "Primary outcomes include every generated frame. Completed-frame P99 is",
        "descriptive only because it is survivor-conditioned.",
        "",
        "## Aggregate result",
        "",
        "| Arm | Misses | Miss rate | Censored mean | Sender airtime |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for arm in mechanism.ARM_ORDER:
        row = aggregate[arm]
        lines.append(
            f"| {ARM_LABELS[arm]} | {row['deadline_misses']:,} | "
            f"{100 * row['all_generated_deadline_miss_rate']:.4f}% | "
            f"{row['all_generated_censored_mean_us'] / 1000:.3f} ms | "
            f"{row['sender_airtime_mean_us'] / 1000:.2f} ms/run |"
        )
    miss = decisive["miss_delta"]
    airtime = decisive["airtime_ratio"]
    lines += [
        "",
        "## Decisive resource question",
        "",
        f"- Original equal-airtime decision: **{decisive['equal_airtime_decision']}**.",
        f"- 1.20 engineering sensitivity: **{decisive['engineering_1p20_decision']}**.",
        f"- Deadline repair minus STR misses: {100 * miss['estimate']:+.4f} pp "
        f"(paired 95% CI {100 * miss['ci95_low']:+.4f} to "
        f"{100 * miss['ci95_high']:+.4f}).",
        f"- Deadline repair / STR sender airtime: {airtime['estimate']:.4f} "
        f"(paired 95% CI {airtime['ci95_low']:.4f} to "
        f"{airtime['ci95_high']:.4f}).",
        f"- Equal-airtime joint bootstrap probability: "
        f"{100 * decisive['equal_airtime_joint_probability']:.2f}%.",
        f"- 1.20 joint bootstrap probability: "
        f"{100 * decisive['engineering_1p20_joint_probability']:.2f}%.",
        "",
        decisive["interpretation"],
        "",
        "## Paired-potential boundary",
        "",
        f"Receiver primary packet sets drift in "
        f"{report['receiver_primary_drift']['frames']:,} of "
        f"{report['receiver_primary_drift']['total_frames']:,} frames "
        f"({100 * report['receiver_primary_drift']['frame_rate']:.2f}%).",
        report["source_closure"]["paired_potential_interpretation"],
        "",
        "## Primary-only transition decomposition",
        "",
    ]
    for key, value in report["transition_decomposition"].items():
        lines.append(f"- {key.replace('_', ' ')}: {value}")
    lines += [
        "",
        "## Next boundary",
        "",
        report["next_boundary"],
        "",
    ]
    return "\n".join(lines)


def analyze(
    factual_roots: Sequence[Path],
    oracle_roots: Sequence[Path],
    output: Path,
    workers: int,
) -> dict[str, Any]:
    identity = mechanism._git_identity()
    jobs, source, pair_rows = _load_jobs(factual_roots, oracle_roots)
    mechanism._descends_from(source["simulation_project_commit"], identity["project_commit"])
    observations = mechanism._collect(jobs, workers)
    grid = mechanism._paired_grid(observations)
    aggregate = {
        arm: mechanism._summarize(
            [row for row in observations if row["arm_id"] == arm]
        )
        for arm in mechanism.ARM_ORDER
    }
    scenarios = {
        scenario: {
            arm: mechanism._summarize(
                [
                    row
                    for row in observations
                    if row["scenario_id"] == scenario and row["arm_id"] == arm
                ]
            )
            for arm in mechanism.ARM_ORDER
        }
        for scenario in mechanism.SCENARIO_LABELS
    }
    bootstrap = mechanism._bootstrap(grid)
    comparison = bootstrap["versus_str"][ANALYSIS_REPAIR_ARM]
    miss_better = comparison["miss_delta"]["estimate"] < 0
    equal_airtime = comparison["airtime_ratio"]["estimate"] <= 1.0
    engineering_airtime = comparison["airtime_ratio"]["estimate"] <= 1.20
    equal_decision = "PASS" if miss_better and equal_airtime else "FAIL"
    engineering_decision = "PASS" if miss_better and engineering_airtime else "FAIL"
    if equal_decision == "PASS":
        interpretation = (
            "The privileged packet-level repair action forms a lower-left point "
            "than STR at equal measured airtime."
        )
    elif engineering_decision == "PASS":
        interpretation = (
            "The action fails the original equal-airtime test but meets the 1.20 "
            "engineering sensitivity while improving all-generated reliability."
        )
    elif miss_better:
        interpretation = (
            "The action improves reliability but fails both the equal-airtime and "
            "1.20 resource limits; prediction cannot remove this action cost."
        )
    else:
        interpretation = (
            "The privileged action does not improve all-generated reliability; a "
            "larger predictor cannot repair this action-space limit."
        )
    drift_frames = sum(int(row["receiver_primary_drift_frames"]) for row in pair_rows)
    drift_packets = sum(int(row["receiver_primary_drift_packets"]) for row in pair_rows)
    total_frames = sum(int(row["frame_count"]) for row in pair_rows)
    primary_airtime = aggregate["single_5ghz_no_redundancy"]["sender_airtime_mean_us"]
    str_airtime = aggregate["str_mlo_nmaxinflights_1"]["sender_airtime_mean_us"]
    report = {
        "schema_version": 1,
        "analysis": ANALYSIS_ID,
        "status": "complete_stop_before_next_iteration",
        "analyzer_identity": identity,
        "source_closure": source,
        "estimands": {
            "primary": "deadline misses over every generated frame",
            "stable_latency": (
                "min(union completion latency, frame deadline) over every generated frame"
            ),
            "completed_p99": "descriptive only; survivor-conditioned",
            "repair_interpretation": "paired no-repair deadline potential replay",
        },
        "aggregate": aggregate,
        "scenarios": scenarios,
        "bootstrap": bootstrap,
        "transition_decomposition": _transition_decomposition(grid),
        "receiver_primary_drift": {
            "frames": drift_frames,
            "packets": drift_packets,
            "total_frames": total_frames,
            "frame_rate": drift_frames / total_frames,
        },
        "decisive_test": {
            "equal_airtime_decision": equal_decision,
            "engineering_1p20_decision": engineering_decision,
            "miss_delta": comparison["miss_delta"],
            "relative_miss_reduction": comparison["relative_miss_reduction"],
            "airtime_ratio": comparison["airtime_ratio"],
            "censored_mean_delta_us": comparison["censored_mean_delta_us"],
            "equal_airtime_joint_probability": bootstrap[
                "oracle_joint_point_success_bootstrap_probability"
            ],
            "engineering_1p20_joint_probability": bootstrap[
                "oracle_joint_1p20_success_bootstrap_probability"
            ],
            "primary_only_airtime_floor": {
                "airtime_ratio": primary_airtime / str_airtime,
                "repair_headroom_us_per_run_at_equal_airtime": (
                    str_airtime - primary_airtime
                ),
                "repair_headroom_us_per_run_at_1p20": (
                    1.20 * str_airtime - primary_airtime
                ),
            },
            "interpretation": interpretation,
        },
        "next_boundary": (
            "Stop here for review. Do not redesign the action, train another "
            "predictor, or open confirmation seeds in this iteration."
        ),
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    mechanism._require(not output.exists(), f"output already exists: {output}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        (temporary / "deadline_oracle_v2_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (temporary / "REPORT.md").write_text(
            _report_markdown(report), encoding="utf-8"
        )
        _write_csv(
            temporary / "run_metrics.csv",
            mechanism._flat_summary_rows(observations),
        )
        _write_csv(
            temporary / "aggregate_metrics.csv",
            [
                {"arm_id": arm, "arm_label": ARM_LABELS[arm], **aggregate[arm]}
                for arm in mechanism.ARM_ORDER
            ],
        )
        _write_csv(
            temporary / "scenario_metrics.csv",
            [
                {
                    "scenario_id": scenario,
                    "arm_id": arm,
                    "arm_label": ARM_LABELS[arm],
                    **scenarios[scenario][arm],
                }
                for scenario in mechanism.SCENARIO_LABELS
                for arm in mechanism.ARM_ORDER
            ],
        )
        _write_csv(temporary / "pair_closure.csv", pair_rows)
        raw_rows = _raw_tree_rows(factual_roots, oracle_roots)
        _write_csv(temporary / "raw_run_tree_manifest.csv", raw_rows)
        plot_dir = temporary / "plots"
        plot_dir.mkdir()
        mechanism._render_plots(
            observations,
            grid,
            plot_dir,
            arm_labels=ARM_LABELS,
            repair_label="Deadline repair",
        )
        _render_drift_plot(pair_rows, plot_dir)
        artifacts = {}
        for path in sorted(item for item in temporary.rglob("*") if item.is_file()):
            relative = str(path.relative_to(temporary))
            artifacts[relative] = {
                "bytes": path.stat().st_size,
                "sha256": mechanism._sha256(path),
            }
        manifest = {
            "schema_version": 1,
            "analysis": ANALYSIS_ID,
            "analyzer_identity": identity,
            "source_closure": source,
            "counts": {
                "strictly_validated_runs": len(observations),
                "paired_units": len(grid),
                "excluded_rejected_v1_oracle_runs": 20,
                "png_figures": len(list(plot_dir.glob("*.png"))),
                "raw_files": len(raw_rows),
            },
            "artifacts": artifacts,
        }
        (temporary / "analysis_artifact_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(temporary, output)
    except Exception:
        raise
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--factual-roots", nargs=2, required=True, type=Path)
    parser.add_argument("--oracle-roots", nargs=2, required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    report = analyze(
        [path.resolve() for path in args.factual_roots],
        [path.resolve() for path in args.oracle_roots],
        args.output.resolve(),
        args.workers,
    )
    decisive = report["decisive_test"]
    print(
        f"EQUAL_AIRTIME {decisive['equal_airtime_decision']} "
        f"ENGINEERING_1P20 {decisive['engineering_1p20_decision']} "
        f"miss_delta={decisive['miss_delta']['estimate']:.8f} "
        f"airtime_ratio={decisive['airtime_ratio']['estimate']:.6f}"
    )
    print(f"REPORT {args.output.resolve() / 'REPORT.md'}")


if __name__ == "__main__":
    main()
