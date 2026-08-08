#!/usr/bin/env python3
"""Analyze the 1.5x-background WMM matrix and its paired 1.0x baseline."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any, Sequence

import analyze_scenario15_wmm_realism_matrix as common
from run_experiments import (
    NS3_UPSTREAM_COMMIT,
    canonical_json,
    derive_run_id,
    expand_config,
    load_yaml,
    matrix_sha256,
    validate_runtime_contract,
)


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_ID = "scenario15_wmm_realism_bg150_matrix_v1"
PLOT_DATA_ID = "scenario15_wmm_realism_bg150_plot_data_v1"
SCHEMA_VERSION = 1
EXPECTED_PROJECT_COMMIT = "2d56f6cbc4abe55491bb1beb85da1da913ffd2f2"
BASELINE_PROJECT_COMMIT = "d9867b13b7fac8df9b936e717855017a22e0b5fa"
RUNTIME_CONTRACT_ID = "scenario15-wmm-realism-bg150-matrix-v1"
CONTRACT_PATH = (
    ROOT / "experiments/model-selection/scenario15-wmm-realism-bg150-matrix-v1.json"
)
CONTRACT_SHA256 = "a74cd5678e68d4152ced46c1b0b664c5d8005b5854cee8fb7d73d0fef656d80e"
BASELINE_CONTRACT_SHA256 = "252fae821e78892f46addddf29f6ab919afce880a48b4f6d9815bdf97fa51d6e"
TREATMENT_SHARDS = {
    "scenario15-wmm-realism-bg150-matrix-v1-shard0": (
        ROOT / "experiments/configs/scenario15_wmm_realism_bg150_matrix_v1_shard0.yaml"
    ),
    "scenario15-wmm-realism-bg150-matrix-v1-shard1": (
        ROOT / "experiments/configs/scenario15_wmm_realism_bg150_matrix_v1_shard1.yaml"
    ),
}
BASELINE_SHARDS = common.SHARD_CONFIGS
RESOLVED_RATE_FIELDS = {
    "min_rate_mbps": (0.5, 0.75),
    "max_rate_mbps": (8.0, 12.0),
    "ul_min_rate_mbps": (0.5, 0.75),
    "ul_max_rate_mbps": (3.0, 4.5),
    "dl_min_rate_mbps": (2.0, 3.0),
    "dl_max_rate_mbps": (8.0, 12.0),
}
COMPACT_FIELDS = (
    "run_id",
    "seed",
    "run",
    "profile",
    "arm",
    "generated_frame_count",
    "completed_frame_count",
    "incomplete_frame_count",
    "deadline_miss_count",
    "all_generated_deadline_miss_rate",
    "completed_frame_p99_us",
    "deadline_censored_p99_us",
    "deadline_censored_mean_us",
    "sender_airtime_us",
    "background_throughput_mbps",
    "measured_secondary_airtime_us",
    "action_count",
    "max_deadline_miss_burst",
)


def _verify_contract() -> dict[str, Any]:
    if common._sha256_file(CONTRACT_PATH) != CONTRACT_SHA256:
        raise common.AnalysisError("1.5x-background runtime contract checksum changed")
    contract = common._read_json(CONTRACT_PATH)
    campaign = contract.get("campaign", {})
    treatment = contract.get("background_offered_load_treatment", {})
    baseline = contract.get("baseline", {})
    if (
        contract.get("runtime_contract_id") != RUNTIME_CONTRACT_ID
        or contract.get("status") != "frozen_before_outcomes"
        or campaign.get("seeds") != list(common.EXPECTED_SEEDS)
        or campaign.get("simulation_run_count") != common.EXPECTED_RUN_COUNT
        or campaign.get("reserved_confirmation_seeds_used") is not False
        or treatment.get("scale") != 1.5
        or baseline.get("runtime_contract_sha256") != BASELINE_CONTRACT_SHA256
    ):
        raise common.AnalysisError("1.5x-background runtime contract content changed")
    return contract


def _manifest_jobs(
    root: Path,
    shard_configs: dict[str, Path],
    expected_project_commit: str,
    expected_contract_id: str,
    expected_contract_sha256: str,
    load: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = root.resolve()
    manifest_path = root / "experiment_manifest.json"
    manifest = common._read_json(manifest_path)
    experiment = manifest.get("experiment")
    if experiment not in shard_configs:
        raise common.AnalysisError(f"{manifest_path}: unexpected shard {experiment!r}")
    config_path = shard_configs[experiment]
    document = load_yaml(config_path)
    runtime = validate_runtime_contract(document)
    if runtime is None:
        raise common.AnalysisError(f"{config_path}: runtime contract is absent")
    expected_manifest = {
        "schema_version": 2,
        "matrix_sha256": matrix_sha256(document),
        "ns3_upstream_commit": NS3_UPSTREAM_COMMIT,
        "runtime_contract_id": expected_contract_id,
        "runtime_contract_sha256": expected_contract_sha256,
        "source_artifacts": runtime["source_artifacts"],
    }
    for key, expected in expected_manifest.items():
        if canonical_json(manifest.get(key)) != canonical_json(expected):
            raise common.AnalysisError(f"{manifest_path}: {key} differs from frozen shard")
    project_commit = manifest.get("project_commit")
    if project_commit != expected_project_commit:
        raise common.AnalysisError(f"{manifest_path}: execution commit differs")
    specs: dict[str, dict[str, Any]] = {}
    for spec in expand_config(document):
        run_id = derive_run_id(
            spec["config"],
            spec["seed"],
            spec["run"],
            NS3_UPSTREAM_COMMIT,
            project_commit,
            runtime,
            spec.get("scenario"),
        )
        specs[run_id] = spec
    entries = manifest.get("runs")
    if not isinstance(entries, list) or len(entries) != 60:
        raise common.AnalysisError(f"{manifest_path}: shard is not 60 complete runs")
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise common.AnalysisError(f"{manifest_path}: invalid run entry")
        run_id = entry.get("run_id")
        if run_id in seen or run_id not in specs:
            raise common.AnalysisError(f"{manifest_path}: unexpected run {run_id}")
        seen.add(run_id)
        spec = specs[run_id]
        if (
            entry.get("status") != "complete"
            or entry.get("directory") != run_id
            or entry.get("seed") != spec["seed"]
            or entry.get("run") != spec["run"]
            or canonical_json(entry.get("config")) != canonical_json(spec["config"])
            or canonical_json(entry.get("scenario")) != canonical_json(spec["scenario"])
        ):
            raise common.AnalysisError(f"{manifest_path}: run identity differs for {run_id}")
        run_dir = root / run_id
        if not run_dir.is_dir():
            raise common.AnalysisError(f"{manifest_path}: missing run {run_id}")
        jobs.append(
            {
                "run_id": run_id,
                "run_dir": str(run_dir),
                "project_commit": project_commit,
                "seed": spec["seed"],
                "run": spec["run"],
                "scenario": spec["scenario"],
                "load": load,
            }
        )
    if seen != set(specs):
        raise common.AnalysisError(f"{manifest_path}: completed set differs from matrix")
    return {
        "experiment": experiment,
        "path": str(manifest_path),
        "sha256": common._sha256_file(manifest_path),
        "matrix_sha256": manifest["matrix_sha256"],
        "project_commit": project_commit,
        "completed_run_count": len(entries),
    }, jobs


def _observe(job: dict[str, Any]) -> dict[str, Any]:
    row = common._observe(job)
    index = 1 if job["load"] == "bg150" else 0
    obss = row["config"].get("background", {}).get("obss", {})
    for field, expected_pair in RESOLVED_RATE_FIELDS.items():
        if not math.isclose(
            float(obss.get(field, math.nan)), expected_pair[index], rel_tol=0, abs_tol=1e-12
        ):
            raise common.AnalysisError(
                f"{job['run_dir']}: resolved background rate {field} differs"
            )
    row["load"] = job["load"]
    return row


def _collect(
    roots: Sequence[Path],
    shard_configs: dict[str, Path],
    project_commit: str,
    contract_id: str,
    contract_sha256: str,
    load: str,
    workers: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    identities: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    for root in roots:
        identity, shard_jobs = _manifest_jobs(
            root,
            shard_configs,
            project_commit,
            contract_id,
            contract_sha256,
            load,
        )
        identities.append(identity)
        jobs.extend(shard_jobs)
    if len(identities) != 2 or {item["experiment"] for item in identities} != set(
        shard_configs
    ):
        raise common.AnalysisError(f"{load}: exact two formal shards are required")
    if len(jobs) != 120 or len({job["run_id"] for job in jobs}) != 120:
        raise common.AnalysisError(f"{load}: merged matrix is not exactly 120 runs")
    with ProcessPoolExecutor(max_workers=workers) as executor:
        observations = list(executor.map(_observe, jobs))
    identities_seen = {
        canonical_json(row["build_identity"]) for row in observations
    }
    if len(identities_seen) != 1:
        raise common.AnalysisError(f"{load}: multiple build identities")
    return identities, observations


def _row_identity(row: dict[str, Any]) -> tuple[str, str, int, int]:
    return row["profile"], row["arm"], row["seed"], row["run"]


def _config_without_load(config: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(config)
    value.pop("run_id", None)
    obss = value["background"]["obss"]
    for field in RESOLVED_RATE_FIELDS:
        obss.pop(field)
    return value


def _period_rows(path: Path) -> list[dict[str, str]]:
    rows = common._read_csv(path)
    return sorted(
        rows,
        key=lambda row: (
            int(row["bss_id"]),
            int(row["sta_index"]),
            row["direction"],
            int(row["period_index"]),
        ),
    )


def _verify_load_pairing(
    treatment: Sequence[dict[str, Any]], baseline: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    left = {_row_identity(row): row for row in treatment}
    right = {_row_identity(row): row for row in baseline}
    if set(left) != set(right) or len(left) != 120:
        raise common.AnalysisError("1.0x and 1.5x matrices are not exactly paired")
    period_count = 0
    maximum_absolute_rate_error = 0.0
    for key in sorted(left):
        new = left[key]
        old = right[key]
        if canonical_json(_config_without_load(new["config"])) != canonical_json(
            _config_without_load(old["config"])
        ):
            raise common.AnalysisError(f"{key}: resolved config differs beyond load")
        new_periods = _period_rows(Path(new["run_dir"]) / "background_rate_periods.csv")
        old_periods = _period_rows(Path(old["run_dir"]) / "background_rate_periods.csv")
        if len(new_periods) != len(old_periods):
            raise common.AnalysisError(f"{key}: background rate-period count differs")
        for new_period, old_period in zip(new_periods, old_periods):
            new_identity = {
                field: value
                for field, value in new_period.items()
                if field not in {"run_id", "rate_mbps"}
            }
            old_identity = {
                field: value
                for field, value in old_period.items()
                if field not in {"run_id", "rate_mbps"}
            }
            if new_identity != old_identity:
                raise common.AnalysisError(f"{key}: background rate-period timing differs")
            error = abs(float(new_period["rate_mbps"]) - 1.5 * float(old_period["rate_mbps"]))
            maximum_absolute_rate_error = max(maximum_absolute_rate_error, error)
            if error > 1e-9:
                raise common.AnalysisError(f"{key}: generated background rate is not 1.5x")
        period_count += len(new_periods)
    return {
        "paired_run_count": len(left),
        "compared_rate_period_count": period_count,
        "resolved_configs_differ_only_in_rate_fields": True,
        "rate_period_identities_match": True,
        "rate_scale": 1.5,
        "maximum_absolute_rate_error_mbps": maximum_absolute_rate_error,
    }


def _group(rows: Sequence[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["profile"], row["arm"]), []).append(row)
    expected = {
        (profile, arm)
        for profile in common.PROFILE_SPECS
        for arm in common.ARM_IDENTITIES
    }
    if set(grouped) != expected or any(len(value) != 10 for value in grouped.values()):
        raise common.AnalysisError("matrix does not form twelve ten-run cells")
    return grouped


def _standard_comparisons(
    grouped: dict[tuple[str, str], list[dict[str, Any]]],
    indexes: Sequence[Sequence[int]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    within = {
        profile: {
            "score_aware_t2_v2_minus_str_mlo": common._comparison(
                grouped[(profile, "score_aware_t2_v2")],
                grouped[(profile, "str_mlo")],
                indexes,
            ),
            "distributional_shadow_t2_minus_str_mlo": common._comparison(
                grouped[(profile, "distributional_shadow_t2")],
                grouped[(profile, "str_mlo")],
                indexes,
            ),
            "distributional_shadow_t2_minus_score_aware_t2_v2": common._comparison(
                grouped[(profile, "distributional_shadow_t2")],
                grouped[(profile, "score_aware_t2_v2")],
                indexes,
            ),
        }
        for profile in common.PROFILE_SPECS
    }
    profile_effects = {
        profile: {
            arm: common._comparison(
                grouped[(profile, arm)], grouped[("be_be", arm)], indexes
            )
            for arm in common.ARM_IDENTITIES
        }
        for profile in common.PROFILE_SPECS
        if profile != "be_be"
    }
    competitor_effects = {
        profile: {
            arm: common._comparison(
                grouped[(profile, arm)], grouped[("af41_vi_be", arm)], indexes
            )
            for arm in common.ARM_IDENTITIES
        }
        for profile in ("af41_vi_one_vi_per_channel", "af41_vi_all_vi")
    }
    return within, profile_effects, competitor_effects


def _plot_series(
    grouped: dict[tuple[str, str], list[dict[str, Any]]]
) -> dict[str, Any]:
    return {
        f"{profile}:{arm}": {
            "profile": profile,
            "arm": arm,
            "profile_label": common.PROFILE_SPECS[profile]["label"],
            "arm_label": common.ARM_LABELS[arm],
            **common._plot_series(grouped[(profile, arm)]),
        }
        for profile in common.PROFILE_SPECS
        for arm in common.ARM_IDENTITIES
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Scenario-15 WMM realism matrix at 1.5x background offered load",
        "",
        "All cells use the same opened seeds and configuration as the 1.0x matrix, "
        "except that every OBSS ON-period rate is exactly 1.5 times larger.",
        "",
        "| Profile | Approach | Misses | Miss rate | Mean per-run P99 | "
        "Sender airtime | OBSS goodput | Actions |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for profile, specification in common.PROFILE_SPECS.items():
        for arm in common.ARM_IDENTITIES:
            item = report["treatments"][profile][arm]
            lines.append(
                f"| {specification['label']} | {common.ARM_LABELS[arm]} | "
                f"{item['deadline_miss_count']:,} | "
                f"{item['all_generated_deadline_miss_rate']['estimate'] * 100:.4f}% | "
                f"{item['completed_frame_p99_us']['estimate'] / 1000:.3f} ms | "
                f"{item['sender_airtime_us']['estimate'] / 1_000_000:.3f} s/run | "
                f"{item['background_throughput_mbps']['estimate']:.3f} Mbps | "
                f"{item['action_count']:,} |"
            )
    lines.extend(["", "## 1.5x minus 1.0x paired effects", ""])
    for profile in common.PROFILE_SPECS:
        lines.append(f"### {common.PROFILE_SPECS[profile]['label']}")
        lines.append("")
        for arm in common.ARM_IDENTITIES:
            item = report["load_effects_vs_bg100"][profile][arm]
            miss = item["deadline_miss_rate_delta"]
            p99 = item["completed_frame_p99_delta_us"]
            airtime = item["sender_airtime_ratio"]
            lines.append(
                f"- {common.ARM_LABELS[arm]}: miss delta "
                f"{miss['estimate'] * 100:+.4f} pp "
                f"[{miss['ci95_low'] * 100:+.4f}, {miss['ci95_high'] * 100:+.4f}]; "
                f"P99 delta {p99['estimate'] / 1000:+.3f} ms "
                f"[{p99['ci95_low'] / 1000:+.3f}, {p99['ci95_high'] / 1000:+.3f}]; "
                f"airtime ratio {airtime['estimate']:.4f} "
                f"[{airtime['ci95_low']:.4f}, {airtime['ci95_high']:.4f}]."
            )
        lines.append("")
    lines.extend(
        [
            "## Evidence boundary",
            "",
            f"All {report['campaign_checks']['strictly_validated_run_count']} new runs "
            "and all 120 retained baseline runs passed fresh strict validation. "
            "Every generated background rate period matched the baseline timing and "
            "was exactly 1.5 times its baseline rate within serialization tolerance. "
            "Seeds 1301 through 1348 were not used.",
            "",
        ]
    )
    return "\n".join(lines)


def analyze(
    treatment_roots: Sequence[Path],
    baseline_roots: Sequence[Path],
    workers: int,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    contract = _verify_contract()
    treatment_identities, treatment_rows = _collect(
        treatment_roots,
        TREATMENT_SHARDS,
        EXPECTED_PROJECT_COMMIT,
        RUNTIME_CONTRACT_ID,
        CONTRACT_SHA256,
        "bg150",
        workers,
    )
    baseline_identities, baseline_rows = _collect(
        baseline_roots,
        BASELINE_SHARDS,
        BASELINE_PROJECT_COMMIT,
        common.RUNTIME_CONTRACT_ID,
        common.CONTRACT_SHA256,
        "bg100",
        workers,
    )
    load_pairing = _verify_load_pairing(treatment_rows, baseline_rows)
    treatment_grouped = _group(treatment_rows)
    baseline_grouped = _group(baseline_rows)
    indexes = common._bootstrap_indexes()
    treatments = {
        profile: {
            arm: common._treatment_summary(treatment_grouped[(profile, arm)], indexes)
            for arm in common.ARM_IDENTITIES
        }
        for profile in common.PROFILE_SPECS
    }
    baseline_treatments = {
        profile: {
            arm: common._treatment_summary(baseline_grouped[(profile, arm)], indexes)
            for arm in common.ARM_IDENTITIES
        }
        for profile in common.PROFILE_SPECS
    }
    within, profile_effects, competitor_effects = _standard_comparisons(
        treatment_grouped, indexes
    )
    load_effects = {
        profile: {
            arm: common._comparison(
                treatment_grouped[(profile, arm)],
                baseline_grouped[(profile, arm)],
                indexes,
            )
            for arm in common.ARM_IDENTITIES
        }
        for profile in common.PROFILE_SPECS
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "analysis": ANALYSIS_ID,
        "evidence_role": "opened-seed paired 1.5x background-load sensitivity",
        "contract": {
            "path": str(CONTRACT_PATH.relative_to(ROOT)),
            "sha256": CONTRACT_SHA256,
            "status": contract["status"],
        },
        "bootstrap": {
            "replications": common.BOOTSTRAP_REPLICATIONS,
            "seed": common.BOOTSTRAP_SEED,
            "paired_unit_count": len(common.EXPECTED_SEEDS),
            "index_matrix_sha256": common._bootstrap_hash(indexes),
        },
        "campaign_checks": {
            "formal_shards": treatment_identities,
            "baseline_shards": baseline_identities,
            "merged_run_count": len(treatment_rows),
            "strictly_validated_run_count": len(treatment_rows),
            "freshly_validated_baseline_run_count": len(baseline_rows),
            "exact_twelve_treatment_cells": True,
            "exact_ten_paired_seeds": True,
            "reserved_confirmation_seeds_used": False,
            "load_pairing": load_pairing,
        },
        "treatments": treatments,
        "baseline_treatments": baseline_treatments,
        "within_profile_comparisons": within,
        "profile_effects_vs_be_be": profile_effects,
        "competitor_effects_with_target_vi": competitor_effects,
        "load_effects_vs_bg100": load_effects,
    }
    plot_data = {
        "schema_version": 1,
        "analysis": PLOT_DATA_ID,
        "series": _plot_series(treatment_grouped),
        "baseline_series": _plot_series(baseline_grouped),
    }
    compact_rows = [
        {"load": row["load"], **{field: row[field] for field in COMPACT_FIELDS}}
        for row in sorted(
            [*baseline_rows, *treatment_rows],
            key=lambda item: (
                item["load"],
                item["seed"],
                item["profile"],
                item["arm"],
            ),
        )
    ]
    return report, plot_data, compact_rows


def _comparison_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scopes = {
        "within_profile_bg150": report["within_profile_comparisons"],
        "profile_vs_be_be_bg150": report["profile_effects_vs_be_be"],
        "competitor_with_target_vi_bg150": report[
            "competitor_effects_with_target_vi"
        ],
        "bg150_minus_bg100": {
            profile: {arm: values for arm, values in arms.items()}
            for profile, arms in report["load_effects_vs_bg100"].items()
        },
    }
    for scope, groups in scopes.items():
        for group, comparisons in groups.items():
            for name, values in comparisons.items():
                rows.append(
                    {
                        "scope": scope,
                        "group": group,
                        "comparison": name,
                        **{
                            f"{metric}_{field}": values[metric][field]
                            for metric in (
                                "deadline_miss_rate_delta",
                                "completed_frame_p99_delta_us",
                                "deadline_censored_mean_delta_us",
                                "sender_airtime_ratio",
                                "background_throughput_loss",
                                "measured_secondary_airtime_delta_us",
                            )
                            for field in ("estimate", "ci95_low", "ci95_high")
                        },
                    }
                )
    return rows


def _write_outputs(
    output: Path,
    report: dict[str, Any],
    plot_data: dict[str, Any],
    rows: Sequence[dict[str, Any]],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "scenario15_wmm_realism_bg150_matrix.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "scenario15_wmm_realism_bg150_matrix.md").write_text(
        _render_markdown(report), encoding="utf-8"
    )
    (output / "plot_data.json").write_text(
        json.dumps(plot_data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with (output / "run_metrics.csv").open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    comparisons = _comparison_rows(report)
    with (output / "paired_comparisons.csv").open(
        "w", newline="", encoding="utf-8"
    ) as target:
        writer = csv.DictWriter(target, fieldnames=list(comparisons[0]))
        writer.writeheader()
        writer.writerows(comparisons)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("treatment_roots", nargs=2, type=Path)
    parser.add_argument("--baseline-roots", nargs=2, type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("workers must be positive")
    report, plot_data, rows = analyze(
        args.treatment_roots, args.baseline_roots, args.workers
    )
    _write_outputs(args.output_directory.resolve(), report, plot_data, rows)
    print(
        f"WROTE {args.output_directory.resolve()} "
        f"new_runs={report['campaign_checks']['strictly_validated_run_count']} "
        f"baseline_runs={report['campaign_checks']['freshly_validated_baseline_run_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
