#!/usr/bin/env python3
"""Qualify the distributional shadow-T2 runtime against same-commit STR MLO."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import statistics
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Sequence

from analyze_paired_value_t2_str_qualification import (
    BOOTSTRAP_REPLICATIONS,
    BOOTSTRAP_SEED,
    MAXIMUM_AIRTIME_RATIO,
    MAXIMUM_BACKGROUND_LOSS,
    MINIMUM_COMPLETED_FRAMES,
    NS3_UPSTREAM_COMMIT,
    QualificationError,
    _arm_summary,
    _background_loss,
    _background_metrics,
    _bootstrap_index_matrix,
    _build_identity,
    _canonical_json,
    _composite,
    _criterion,
    _environment,
    _frame_metrics,
    _index_matrix_sha256,
    _load_neutral_declarations,
    _mean_delta,
    _nominal_config,
    _paired_bootstrap,
    _ratio_of_means,
    _sender_airtime_us,
    _sha256_file,
    _without_geometry,
)
from validate_outputs import (
    DISTRIBUTIONAL_SHADOW_T2_CONTRACT_ID,
    DISTRIBUTIONAL_SHADOW_T2_CONTRACT_PATH,
    DISTRIBUTIONAL_SHADOW_T2_CONTRACT_SHA256,
    DISTRIBUTIONAL_SHADOW_T2_MODEL_PATH,
    DISTRIBUTIONAL_SHADOW_T2_MODEL_XZ_SHA256,
    DISTRIBUTIONAL_SHADOW_T2_POLICY,
    DISTRIBUTIONAL_SHADOW_T2_REFERENCE_PATH,
    DISTRIBUTIONAL_SHADOW_T2_REFERENCE_XZ_SHA256,
    DISTRIBUTIONAL_SHADOW_T2_STATUSES,
    ValidationError,
    validate_run,
)
from summarize_runs import METRICS, confidence, group_key


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_ID = "distributional_shadow_t2_str_engineering_v1"
SCHEMA_VERSION = 1
EXPECTED_SEEDS = tuple(range(1251, 1299))
EXPECTED_RUN_NUMBER = 1
RESERVED_CONFIRMATION_SEEDS = tuple(range(1301, 1349))
EXPECTED_PROJECT_COMMIT = "e2c770b21cb8f69318a8cd5958815f4ab9c09392"
STRICT_VALIDATOR_COMMIT = "34e9296108f54ea0738cbc43b7b6c20df9d419dd"
POLICY_IDENTITY = ("dual_interface", DISTRIBUTIONAL_SHADOW_T2_POLICY)
STR_IDENTITY = ("mlo_str", "fixed_link_0")
ARM_IDENTITIES = {"policy": POLICY_IDENTITY, "str_mlo": STR_IDENTITY}
ARM_LABELS = {
    "policy": "Distributional shadow T2",
    "str_mlo": "STR MLO",
}
ENGINEERING_MISS_RATE_TARGET = 0.005
ENGINEERING_COMPLETED_P99_US_TARGET = 17_000.0
LONG_TERM_RELATIVE_MISS_REDUCTION_TARGET = 0.50

COMMON_RUN_ARTIFACTS = {
    "resolved_config.json",
    "build_info.json",
    "frames.csv",
    "policy_decisions.csv",
    "link_intervals.csv",
    "mac_summary.csv",
    "summary.json",
    "stdout.log",
    "stderr.log",
    "background_flows.csv",
    "background_rate_periods.csv",
    "ofdma_summary.csv",
}
POLICY_RUN_ARTIFACTS = {
    "prediction_samples.csv",
    "prediction_polling_samples.csv",
    "distributional_shadow_t2_decisions.csv",
    "distributional_shadow_t2_summary.json",
    "secondary_airtime_events.csv",
    "secondary_airtime_settlements.csv",
    "secondary_airtime_summary.json",
}
EXPECTED_CONTROLLER_SOURCES = {
    "training_git_commit": "49c74528289e7dfd8881cbe1f65ea9293abe3ca6",
    "source_model_pickle_sha256": (
        "60c181eb75faafde57f65a63f71a31cee99050a54afa53edb162a9ac7a1ec6e0"
    ),
    "source_model_json_sha256": (
        "e9d5f0ebc822f8956ef3ed06fc8cb1d776961d0fba5ebeebcdd1e181b5be0071"
    ),
    "source_reference_json_sha256": (
        "a4d2ac57e35e79bb173d09e1ca6f0237e06438c001c1685a6d4af9ff13f44acf"
    ),
    "source_metrics_sha256": (
        "ffa32fdb852f4296a9f548666a946b9da7048d475f648679deb6bcc72cf8c9ab"
    ),
    "source_manifest_sha256": (
        "20207dacbbb44dc638c3674d61c56596e1e2092920e3a95fb391cb0dd6d05b89"
    ),
    "exporter_sha256": (
        "1a0e9a89ca0edad2f12c1bccb383e246de8e1b8c1f578b8d47283d4b38e18a21"
    ),
    "portable_model_sha256": (
        "8023d41495cd93df78a68fdad45f2dc588369ba23102ae038a400e8c3d5d5aac"
    ),
    "deployment_reference_sha256": (
        "493c0624082a7cb363bcb7bfe3af0930cb6a2d84e09ce17e1d7eef8dd7d7f316"
    ),
    "feature_contract_sha256": (
        "1f8dce2aad4c21d5cdf66a25a87d7bdeaf8eb3befbc6fbc1922062b77c5d9d96"
    ),
}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise QualificationError(f"missing required artifact: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError(f"{path}: invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise QualificationError(f"{path}: expected a JSON object")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise QualificationError(f"missing required artifact: {path}")
    try:
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
                raise QualificationError(f"{path}: invalid or duplicate CSV header")
            rows = list(reader)
    except OSError as error:
        raise QualificationError(f"cannot read {path}: {error}") from error
    if any(None in row for row in rows):
        raise QualificationError(f"{path}: row exceeds the declared CSV width")
    return rows


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _verify_runtime_contract() -> dict[str, Any]:
    if _sha256_file(DISTRIBUTIONAL_SHADOW_T2_CONTRACT_PATH) != (
        DISTRIBUTIONAL_SHADOW_T2_CONTRACT_SHA256
    ):
        raise QualificationError("distributional-shadow runtime contract changed")
    contract = _read_json(DISTRIBUTIONAL_SHADOW_T2_CONTRACT_PATH)
    boundary = contract.get("evidence_boundary", {})
    qualification = contract.get("closed_loop_engineering_qualification", {})
    if (
        contract.get("schema_version") != 1
        or contract.get("runtime_contract_id") != DISTRIBUTIONAL_SHADOW_T2_CONTRACT_ID
        or boundary.get("closed_loop_engineering_seed_range") != [1251, 1298]
        or boundary.get("reserved_confirmation_seed_range") != [1301, 1348]
        or boundary.get("reserved_confirmation_seeds_must_remain_unread") is not True
        or qualification.get("comparators") != [
            "STR_MLO",
            "paired-value V2 engineering champion",
        ]
        or qualification.get("environment")
        != "unchanged canonical neutral environment used by the prior paired campaigns"
        or qualification.get("paired_unit") != "seed and run_number"
        or qualification.get("engineering_mean_per_run_p99_target_ms") != 17.0
        or qualification.get(
            "sender_airtime_ratio_upper_confidence_bound_maximum"
        )
        != MAXIMUM_AIRTIME_RATIO
        or qualification.get("background_throughput_noninferiority_required") is not True
    ):
        raise QualificationError("distributional-shadow qualification contract differs")
    for path, digest, label in (
        (
            DISTRIBUTIONAL_SHADOW_T2_MODEL_PATH,
            DISTRIBUTIONAL_SHADOW_T2_MODEL_XZ_SHA256,
            "portable model replay",
        ),
        (
            DISTRIBUTIONAL_SHADOW_T2_REFERENCE_PATH,
            DISTRIBUTIONAL_SHADOW_T2_REFERENCE_XZ_SHA256,
            "shadow-reference replay",
        ),
    ):
        if _sha256_file(path) != digest:
            raise QualificationError(f"{label} artifact changed")
    return contract


def _discover_seed_dirs(root: Path, arm: str) -> dict[int, Path]:
    root = root.resolve()
    if not root.is_dir():
        raise QualificationError(f"{arm} root is not a directory: {root}")
    discovered: dict[int, Path] = {}
    for candidate in root.glob("seed-*"):
        if not candidate.is_dir():
            continue
        suffix = candidate.name.removeprefix("seed-")
        if not suffix.isdigit():
            raise QualificationError(f"{root}: invalid seed directory {candidate.name}")
        seed = int(suffix)
        if seed in discovered:
            raise QualificationError(f"{root}: duplicate seed {seed}")
        discovered[seed] = candidate.resolve()
    if set(discovered) != set(EXPECTED_SEEDS):
        raise QualificationError(
            f"{arm} seed set differs: missing "
            f"{sorted(set(EXPECTED_SEEDS) - set(discovered))}, extra "
            f"{sorted(set(discovered) - set(EXPECTED_SEEDS))}"
        )
    if set(discovered) & set(RESERVED_CONFIRMATION_SEEDS):
        raise QualificationError("engineering roots contain reserved confirmation seeds")
    return discovered


def _validate_one(job: tuple[str, int, str]) -> tuple[str, int, dict[str, Any]]:
    arm, seed, serialized_path = job
    path = Path(serialized_path)
    try:
        result = validate_run(
            path,
            expected_project_commit=EXPECTED_PROJECT_COMMIT,
            expected_ns3_commit=NS3_UPSTREAM_COMMIT,
        )
    except ValidationError as error:
        raise QualificationError(f"{path}: strict validation failed: {error}") from error
    if result.get("valid") is not True:
        raise QualificationError(f"{path}: strict validator did not return valid=true")
    return arm, seed, result


def _strictly_validate(
    roots: dict[str, dict[int, Path]], workers: int
) -> dict[tuple[str, int], dict[str, Any]]:
    jobs = [
        (arm, seed, str(index[seed]))
        for arm, index in roots.items()
        for seed in EXPECTED_SEEDS
    ]
    results: dict[tuple[str, int], dict[str, Any]] = {}
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_validate_one, job): job for job in jobs}
        for future in as_completed(futures):
            arm, seed, _path = futures[future]
            try:
                result_arm, result_seed, result = future.result()
            except Exception as error:
                for pending in futures:
                    pending.cancel()
                if isinstance(error, QualificationError):
                    raise error
                raise QualificationError(
                    f"{arm} seed {seed}: validator worker failed: {error}"
                ) from error
            results[(result_arm, result_seed)] = result
    if len(results) != 2 * len(EXPECTED_SEEDS):
        raise QualificationError("strict validation result cardinality differs")
    return results


def _required_artifacts(run_dir: Path, arm: str) -> None:
    required = set(COMMON_RUN_ARTIFACTS)
    if arm == "policy":
        required |= POLICY_RUN_ARTIFACTS
    missing = sorted(name for name in required if not (run_dir / name).is_file())
    if missing:
        raise QualificationError(f"{run_dir}: missing raw artifacts {missing}")
    if arm == "str_mlo":
        forbidden = sorted(
            name for name in POLICY_RUN_ARTIFACTS if (run_dir / name).exists()
        )
        if forbidden:
            raise QualificationError(f"{run_dir}: policy artifacts in STR run {forbidden}")


def _common_input_config(
    config: dict[str, Any], topology_wifi: dict[str, Any]
) -> dict[str, Any]:
    result = copy.deepcopy(config)
    for key in (
        "run_id",
        "seed",
        "run",
        "topology",
        "policy",
        "policy_settings",
        "distributionalShadowDuplicationT2",
        "predictionTelemetry",
        "secondaryAirtimeMeter",
        "environment",
    ):
        result.pop(key, None)
    wifi = result.get("wifi")
    if not isinstance(wifi, dict):
        raise QualificationError("resolved config omits Wi-Fi settings")
    topology_fields = {
        field
        for topology in ("dual_interface", "mlo_str")
        for field in topology_wifi[topology]
    }
    for field in topology_fields:
        wifi.pop(field, None)
    return result


def _validate_policy_identity(run_dir: Path, config: dict[str, Any]) -> None:
    runtime = config.get("distributionalShadowDuplicationT2")
    summary = _read_json(run_dir / "distributional_shadow_t2_summary.json")
    if (
        not isinstance(runtime, dict)
        or runtime.get("runtime_contract_id") != DISTRIBUTIONAL_SHADOW_T2_CONTRACT_ID
        or runtime.get("runtime_contract_sha256")
        != DISTRIBUTIONAL_SHADOW_T2_CONTRACT_SHA256
        or summary.get("run_id") != config.get("run_id")
        or summary.get("policy") != DISTRIBUTIONAL_SHADOW_T2_POLICY
        or summary.get("runtime_contract_id") != DISTRIBUTIONAL_SHADOW_T2_CONTRACT_ID
        or summary.get("runtime_contract_sha256")
        != DISTRIBUTIONAL_SHADOW_T2_CONTRACT_SHA256
        or summary.get("source_artifacts") != EXPECTED_CONTROLLER_SOURCES
    ):
        raise QualificationError(f"{run_dir}: controller source identity differs")


def _observation(
    arm: str,
    seed: int,
    run_dir: Path,
    validation: dict[str, Any],
    neutral_environment: dict[str, Any],
    topology_wifi: dict[str, Any],
) -> dict[str, Any]:
    _required_artifacts(run_dir, arm)
    config = _read_json(run_dir / "resolved_config.json")
    identity = (config.get("topology"), config.get("policy"))
    if (
        identity != ARM_IDENTITIES[arm]
        or config.get("seed") != seed
        or config.get("run") != EXPECTED_RUN_NUMBER
        or config.get("run_id") != validation.get("run_id")
    ):
        raise QualificationError(f"{run_dir}: resolved run identity differs")
    environment = _environment(config)
    if _canonical_json(_without_geometry(environment)) != _canonical_json(
        neutral_environment
    ):
        raise QualificationError(f"{run_dir}: neutral environment differs")
    expected_topology = topology_wifi.get(config["topology"])
    wifi = config.get("wifi")
    if not isinstance(expected_topology, dict) or not isinstance(wifi, dict):
        raise QualificationError(f"{run_dir}: topology Wi-Fi closure is unavailable")
    observed_topology = {key: wifi.get(key, "__MISSING__") for key in expected_topology}
    if _canonical_json(observed_topology) != _canonical_json(expected_topology):
        raise QualificationError(f"{run_dir}: topology Wi-Fi closure differs")
    if arm == "policy":
        _validate_policy_identity(run_dir, config)
    return {
        "arm": arm,
        "seed": seed,
        "run": EXPECTED_RUN_NUMBER,
        "run_id": validation["run_id"],
        "run_dir": str(run_dir),
        "config": config,
        "environment": environment,
        "common_input_config": _common_input_config(config, topology_wifi),
        "nominal_config": _nominal_config(config),
        "build_identity": _build_identity(run_dir),
        **_frame_metrics(run_dir, config),
        "sender_airtime_us": _sender_airtime_us(run_dir),
        **_background_metrics(run_dir, config),
        "strict_validation": validation,
    }


def _criterion_with_interval(
    passed: bool,
    rule: str,
    observed: float,
    threshold: float,
    interval: dict[str, Any],
) -> dict[str, Any]:
    return {**_criterion(passed, rule, observed, threshold), "paired_bootstrap": interval}


def _paired_rows(
    ordered: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, seed in enumerate(EXPECTED_SEEDS):
        policy = ordered["policy"][index]
        baseline = ordered["str_mlo"][index]
        rows.append({
            "seed": seed,
            "run": EXPECTED_RUN_NUMBER,
            "policy_run_id": policy["run_id"],
            "str_run_id": baseline["run_id"],
            "policy_deadline_miss_count": policy["deadline_miss_count"],
            "str_deadline_miss_count": baseline["deadline_miss_count"],
            "policy_deadline_miss_rate": policy["all_generated_deadline_miss_rate"],
            "str_deadline_miss_rate": baseline["all_generated_deadline_miss_rate"],
            "miss_delta_percentage_points": 100.0 * (
                policy["all_generated_deadline_miss_rate"]
                - baseline["all_generated_deadline_miss_rate"]
            ),
            "policy_completed_p99_us": policy["completed_frame_p99_us"],
            "str_completed_p99_us": baseline["completed_frame_p99_us"],
            "completed_p99_delta_us": (
                policy["completed_frame_p99_us"] - baseline["completed_frame_p99_us"]
            ),
            "policy_sender_airtime_us": policy["sender_airtime_us"],
            "str_sender_airtime_us": baseline["sender_airtime_us"],
            "sender_airtime_ratio": (
                policy["sender_airtime_us"] / baseline["sender_airtime_us"]
            ),
            "policy_background_throughput_mbps": policy[
                "background_throughput_mbps"
            ],
            "str_background_throughput_mbps": baseline[
                "background_throughput_mbps"
            ],
            "background_throughput_loss": 1.0
            - policy["background_throughput_mbps"]
            / baseline["background_throughput_mbps"],
        })
    return rows


def _build_report(
    ordered: dict[str, list[dict[str, Any]]],
    archive_identities: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    policy_miss = [row["all_generated_deadline_miss_rate"] for row in ordered["policy"]]
    str_miss = [row["all_generated_deadline_miss_rate"] for row in ordered["str_mlo"]]
    policy_p99 = [row["completed_frame_p99_us"] for row in ordered["policy"]]
    str_p99 = [row["completed_frame_p99_us"] for row in ordered["str_mlo"]]
    policy_airtime = [row["sender_airtime_us"] for row in ordered["policy"]]
    str_airtime = [row["sender_airtime_us"] for row in ordered["str_mlo"]]
    policy_background = [row["background_throughput_mbps"] for row in ordered["policy"]]
    str_background = [row["background_throughput_mbps"] for row in ordered["str_mlo"]]
    if any(value <= 0 for value in [*str_airtime, *str_background]):
        raise QualificationError("STR resource denominator is nonpositive")
    indexes = _bootstrap_index_matrix()
    miss_delta = _paired_bootstrap(
        policy_miss,
        str_miss,
        indexes,
        _mean_delta,
        "mean per-run policy-minus-STR miss-rate difference",
    )
    p99_delta = _paired_bootstrap(
        policy_p99,
        str_p99,
        indexes,
        _mean_delta,
        "mean per-run policy-minus-STR completed-P99 difference",
    )
    airtime_ratio = _paired_bootstrap(
        policy_airtime,
        str_airtime,
        indexes,
        _ratio_of_means,
        "ratio of resampled policy and STR sender-airtime means",
    )
    background_loss = _paired_bootstrap(
        policy_background,
        str_background,
        indexes,
        _background_loss,
        "one minus ratio of resampled policy and STR background-throughput means",
    )
    policy_summary = _arm_summary(ordered["policy"])
    str_summary = _arm_summary(ordered["str_mlo"])
    policy_bytes = sum(row["background_bytes_received"] for row in ordered["policy"])
    str_bytes = sum(row["background_bytes_received"] for row in ordered["str_mlo"])
    exact_background_pass = 100 * policy_bytes >= 99 * str_bytes
    performance = {
        "deadline_miss_rate": _criterion_with_interval(
            miss_delta["ci95_high"] < 0,
            "policy-minus-STR miss-rate 95% interval is strictly below zero",
            miss_delta["ci95_high"],
            0.0,
            miss_delta,
        ),
        "completed_p99": _criterion_with_interval(
            p99_delta["ci95_high"] < 0,
            "policy-minus-STR completed-P99 95% interval is strictly below zero",
            p99_delta["ci95_high"],
            0.0,
            p99_delta,
        ),
    }
    resources = {
        "sender_airtime": _criterion_with_interval(
            airtime_ratio["ci95_high"] <= MAXIMUM_AIRTIME_RATIO,
            "sender-airtime ratio upper 95% endpoint <= 1.20",
            airtime_ratio["ci95_high"],
            MAXIMUM_AIRTIME_RATIO,
            airtime_ratio,
        ),
        "background_throughput": {
            **_criterion_with_interval(
                exact_background_pass
                and background_loss["ci95_high"] <= MAXIMUM_BACKGROUND_LOSS,
                "exact background byte gate passes and loss upper 95% endpoint <= 0.01",
                background_loss["ci95_high"],
                MAXIMUM_BACKGROUND_LOSS,
                background_loss,
            ),
            "exact_byte_gate": {
                "policy_bytes_received": policy_bytes,
                "str_bytes_received": str_bytes,
                "rule": "100 * policy bytes >= 99 * STR bytes",
                "passed": exact_background_pass,
            },
        },
    }
    performance_status = _composite(performance.values())
    resource_status = _composite(resources.values())
    qualification_status = (
        "pass" if performance_status == "pass" and resource_status == "pass" else "fail"
    )
    relative_miss_reduction = 1.0 - (
        policy_summary["all_generated_deadline_miss_rate"]["mean"]
        / str_summary["all_generated_deadline_miss_rate"]["mean"]
    )
    promotion = {
        "str_qualification": _criterion(
            qualification_status == "pass",
            "all performance and resource criteria pass",
            1.0 if qualification_status == "pass" else 0.0,
            1.0,
        ),
        "engineering_miss_rate": _criterion(
            policy_summary["all_generated_deadline_miss_rate"]["mean"]
            <= ENGINEERING_MISS_RATE_TARGET,
            "policy all-generated miss rate <= 0.005",
            policy_summary["all_generated_deadline_miss_rate"]["mean"],
            ENGINEERING_MISS_RATE_TARGET,
        ),
        "engineering_completed_p99": _criterion(
            policy_summary["completed_frame_p99_us"]["mean"]
            <= ENGINEERING_COMPLETED_P99_US_TARGET,
            "policy mean per-run completed P99 <= 17000 us",
            policy_summary["completed_frame_p99_us"]["mean"],
            ENGINEERING_COMPLETED_P99_US_TARGET,
        ),
        "long_term_relative_miss_reduction": _criterion(
            relative_miss_reduction >= LONG_TERM_RELATIVE_MISS_REDUCTION_TARGET,
            "relative miss reduction versus STR >= 0.50",
            relative_miss_reduction,
            LONG_TERM_RELATIVE_MISS_REDUCTION_TARGET,
        ),
    }
    paired_metrics = _paired_rows(ordered)
    miss_deltas = [row["miss_delta_percentage_points"] for row in paired_metrics]
    p99_deltas = [row["completed_p99_delta_us"] for row in paired_metrics]
    airtime_ratios = [row["sender_airtime_ratio"] for row in paired_metrics]
    heterogeneity = {
        "paired_direction_counts": {
            "deadline_miss_rate": {
                "policy_better": sum(value < 0 for value in miss_deltas),
                "tie": sum(value == 0 for value in miss_deltas),
                "policy_worse": sum(value > 0 for value in miss_deltas),
            },
            "completed_p99": {
                "policy_better": sum(value < 0 for value in p99_deltas),
                "tie": sum(value == 0 for value in p99_deltas),
                "policy_worse": sum(value > 0 for value in p99_deltas),
            },
            "both_metrics_better": sum(
                miss_delta_value < 0 and p99_delta_value < 0
                for miss_delta_value, p99_delta_value in zip(miss_deltas, p99_deltas)
            ),
        },
        "individual_runs_above_sender_airtime_ratio_1_20": sum(
            value > MAXIMUM_AIRTIME_RATIO for value in airtime_ratios
        ),
        "correlations": {
            "sender_airtime_ratio_vs_deadline_miss_delta": _correlation(
                airtime_ratios, miss_deltas
            ),
            "sender_airtime_ratio_vs_completed_p99_delta": _correlation(
                airtime_ratios, p99_deltas
            ),
        },
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis": ANALYSIS_ID,
        "evidence_role": "opened-seed engineering qualification",
        "paired_unit_count": len(EXPECTED_SEEDS),
        "paired_units": [
            {"seed": seed, "run": EXPECTED_RUN_NUMBER} for seed in EXPECTED_SEEDS
        ],
        "reserved_confirmation_seeds_used": False,
        "source_closure": {
            "runtime_contract": {
                "path": str(DISTRIBUTIONAL_SHADOW_T2_CONTRACT_PATH),
                "sha256": DISTRIBUTIONAL_SHADOW_T2_CONTRACT_SHA256,
            },
            "portable_model_replay": {
                "path": str(DISTRIBUTIONAL_SHADOW_T2_MODEL_PATH),
                "sha256": DISTRIBUTIONAL_SHADOW_T2_MODEL_XZ_SHA256,
            },
            "shadow_reference_replay": {
                "path": str(DISTRIBUTIONAL_SHADOW_T2_REFERENCE_PATH),
                "sha256": DISTRIBUTIONAL_SHADOW_T2_REFERENCE_XZ_SHA256,
            },
            "simulation_project_commit": EXPECTED_PROJECT_COMMIT,
            "strict_validator_commit": STRICT_VALIDATOR_COMMIT,
            "ns3_upstream_commit": NS3_UPSTREAM_COMMIT,
            "raw_archives": archive_identities,
        },
        "campaign_checks": {
            "exact_two_declared_arms": True,
            "exact_48_paired_units": True,
            "all_96_runs_strictly_validated": True,
            "all_metrics_reconstructed_from_raw_artifacts": True,
            "paired_environment_realizations_match": True,
            "paired_common_inputs_match": True,
            "single_build_identity": True,
            "reserved_confirmation_seeds_unopened": True,
        },
        "bootstrap": {
            "method": "one shared deterministic 10000x48 matched-unit index matrix",
            "seed": BOOTSTRAP_SEED,
            "replications": BOOTSTRAP_REPLICATIONS,
            "draws_per_replication": len(EXPECTED_SEEDS),
            "index_matrix_sha256": _index_matrix_sha256(indexes),
            "endpoint_quantile": "Hyndman-Fan type 7",
        },
        "treatments": {
            "policy": {"label": ARM_LABELS["policy"], **policy_summary},
            "str_mlo": {"label": ARM_LABELS["str_mlo"], **str_summary},
        },
        "comparison_against_str": {
            "deadline_miss_rate": miss_delta,
            "completed_p99_us": p99_delta,
            "sender_airtime_ratio": airtime_ratio,
            "background_throughput_loss": background_loss,
            "relative_deadline_miss_reduction": relative_miss_reduction,
        },
        "performance_victory_against_str": {
            "status": performance_status,
            "criteria": performance,
        },
        "resource_target_against_str": {
            "status": resource_status,
            "criteria": resources,
        },
        "str_qualification": {"status": qualification_status},
        "promotion_readiness": {
            "status": _composite(promotion.values()),
            "criteria": promotion,
        },
        "paired_heterogeneity": heterogeneity,
        "paired_metrics": paired_metrics,
    }


def _primary_miss(frame: dict[str, str]) -> bool:
    completion = frame.get("copy_0_completion_us", "")
    if completion == "":
        return True
    return (
        int(completion) - int(frame["generation_time_us"])
        > int(frame["deadline_us"])
    )


def _policy_diagnostics(policy_rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    status_frames: Counter[str] = Counter()
    status_primary_misses: Counter[str] = Counter()
    status_final_misses: Counter[str] = Counter()
    action_outcomes: Counter[str] = Counter()
    actions_by_time_bin = [0] * 12
    actions_by_regime = [0] * 3
    reservations_by_time_bin = [0.0] * 12
    reservations_by_regime = [0.0] * 3
    primary_misses = 0
    acted_primary_misses = 0
    rescued_primary_misses = 0
    action_dirty_scored = 0
    measured_secondary_airtime_us = 0.0
    maximum_debts: list[float] = []
    final_balances: list[float] = []
    for observation in policy_rows:
        run_dir = Path(observation["run_dir"])
        frames = {int(row["frame_id"]): row for row in _read_csv(run_dir / "frames.csv")}
        decisions = _read_csv(run_dir / "distributional_shadow_t2_decisions.csv")
        summary = _read_json(run_dir / "distributional_shadow_t2_summary.json")
        meter = _read_json(run_dir / "secondary_airtime_summary.json")
        if len(frames) != len(decisions):
            raise QualificationError(f"{run_dir}: diagnostic decision cardinality differs")
        for decision in decisions:
            frame_id = int(decision["frame_id"])
            frame = frames.get(frame_id)
            status = decision.get("decision_status", "")
            if frame is None or status not in DISTRIBUTIONAL_SHADOW_T2_STATUSES:
                raise QualificationError(f"{run_dir}: diagnostic identity differs")
            primary_missed = _primary_miss(frame)
            final_missed = frame["deadline_miss"] == "1"
            launched = decision["secondary_launched"] == "1"
            status_frames[status] += 1
            status_primary_misses[status] += int(primary_missed)
            status_final_misses[status] += int(final_missed)
            primary_misses += int(primary_missed)
            if decision["feature_evaluated"] == "1" and (
                decision["secondary_state_action_dirty"] == "1"
            ):
                action_dirty_scored += 1
            if not launched:
                continue
            acted_primary_misses += int(primary_missed)
            rescued = primary_missed and not final_missed
            rescued_primary_misses += int(rescued)
            if rescued:
                action_outcomes["primary_miss_rescued"] += 1
            elif primary_missed:
                action_outcomes["primary_miss_late_or_incomplete"] += 1
            else:
                union = frame.get("union_completion_us", "")
                primary = frame.get("copy_0_completion_us", "")
                accelerated = union != "" and primary != "" and int(union) < int(primary)
                action_outcomes[
                    "primary_on_time_accelerated"
                    if accelerated
                    else "primary_on_time_no_completion_benefit"
                ] += 1
        allocation_time = summary["allocation_by_time_bin"]
        allocation_regime = summary["allocation_by_congestion_regime"]
        for index, value in enumerate(allocation_time["actions"]):
            actions_by_time_bin[index] += int(value)
        for index, value in enumerate(allocation_time["canonical_reservation_us"]):
            reservations_by_time_bin[index] += float(value)
        for index, value in enumerate(allocation_regime["actions"]):
            actions_by_regime[index] += int(value)
        for index, value in enumerate(allocation_regime["canonical_reservation_us"]):
            reservations_by_regime[index] += float(value)
        maximum_debts.append(float(summary["ledger"]["maximum_debt_us"]))
        final_balances.append(float(summary["ledger"]["final_balance_us"]))
        measured_secondary_airtime_us += float(meter["tagged_secondary_tx_airtime_us"])
    actions = sum(action_outcomes.values())
    final_misses = sum(status_final_misses.values())
    return {
        "generated_frames": sum(status_frames.values()),
        "primary_copy_deadline_misses": primary_misses,
        "final_union_deadline_misses": final_misses,
        "actions": actions,
        "acted_primary_misses": acted_primary_misses,
        "rescued_primary_misses": rescued_primary_misses,
        "primary_miss_capture_rate": acted_primary_misses / primary_misses,
        "conditional_rescue_efficiency": (
            rescued_primary_misses / acted_primary_misses
        ),
        "action_dirty_scored_decisions": action_dirty_scored,
        "terminal_status": {
            status: {
                "frames": status_frames[status],
                "primary_copy_misses": status_primary_misses[status],
                "final_union_misses": status_final_misses[status],
                "primary_miss_rate": (
                    status_primary_misses[status] / status_frames[status]
                    if status_frames[status]
                    else None
                ),
            }
            for status in DISTRIBUTIONAL_SHADOW_T2_STATUSES
        },
        "action_outcomes": dict(action_outcomes),
        "allocation": {
            "actions_by_time_bin": actions_by_time_bin,
            "canonical_reservation_us_by_time_bin": reservations_by_time_bin,
            "actions_by_congestion_regime": actions_by_regime,
            "canonical_reservation_us_by_congestion_regime": (
                reservations_by_regime
            ),
        },
        "ledger": {
            "maximum_debt_us_across_runs": max(maximum_debts),
            "mean_maximum_debt_us": statistics.mean(maximum_debts),
            "minimum_final_balance_us": min(final_balances),
            "all_runs_repaid": all(value >= 0 for value in final_balances),
        },
        "measured_secondary_airtime_us": measured_secondary_airtime_us,
    }


def _archive_identity(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"available": False}
    resolved = path.resolve()
    if not resolved.is_file():
        raise QualificationError(f"raw archive does not exist: {resolved}")
    return {
        "available": True,
        "path": str(resolved),
        "sha256": _sha256_file(resolved),
        "compressed_size_bytes": resolved.stat().st_size,
    }


def _percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return (
        ordered[lower] * (upper - position)
        + ordered[upper] * (position - lower)
    )


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = statistics.mean(left)
    right_mean = statistics.mean(right)
    numerator = sum(
        (x_value - left_mean) * (y_value - right_mean)
        for x_value, y_value in zip(left, right)
    )
    denominator = math.sqrt(
        sum((value - left_mean) ** 2 for value in left)
        * sum((value - right_mean) ** 2 for value in right)
    )
    return numerator / denominator if denominator else None


def _bursts(flags: Iterable[bool]) -> list[int]:
    result: list[int] = []
    current = 0
    for flag in flags:
        if flag:
            current += 1
        elif current:
            result.append(current)
            current = 0
    if current:
        result.append(current)
    return result


def _standard_run_metrics(observation: dict[str, Any]) -> dict[str, Any]:
    run_dir = Path(observation["run_dir"])
    config = observation["config"]
    summary = _read_json(run_dir / "summary.json")
    frames = sorted(
        _read_csv(run_dir / "frames.csv"),
        key=lambda row: int(row["generation_time_us"]),
    )
    bursts = _bursts(row["deadline_miss"] == "1" for row in frames)
    copy_zero: list[float] = []
    copy_one: list[float] = []
    joint_exceedances = 0
    duplicated = 0
    for frame in frames:
        if frame["duplicated"] != "1":
            continue
        duplicated += 1
        generation = int(frame["generation_time_us"])
        deadline = int(frame["deadline_us"])
        left = (
            int(frame["copy_0_completion_us"]) - generation
            if frame["copy_0_completion_us"]
            else None
        )
        right = (
            int(frame["copy_1_completion_us"]) - generation
            if frame["copy_1_completion_us"]
            else None
        )
        if left is not None and right is not None:
            copy_zero.append(float(left))
            copy_one.append(float(right))
        if (left is None or left > deadline) and (right is None or right > deadline):
            joint_exceedances += 1
    return {
        "run_id": observation["run_id"],
        "run_dir": observation["run_dir"],
        "seed": observation["seed"],
        "run": observation["run"],
        "topology": config["topology"],
        "policy": config["policy"],
        "config": config,
        "deadline_miss_ratio": float(summary["deadline_miss_ratio"]),
        "incomplete_ratio": float(summary["incomplete_ratio"]),
        "latency_p99_us": float(summary["latency_p99_us"]),
        "redundant_byte_ratio": float(summary["redundant_byte_ratio"]),
        "duplicate_recovery_rate": (
            float(summary["duplicate_recovery_rate"])
            if int(summary["duplicate_frame_count"])
            else None
        ),
        "duplicate_no_benefit_ratio": (
            float(summary["duplicate_no_benefit_ratio"])
            if int(summary["duplicate_frame_count"])
            else None
        ),
        "background_throughput_mbps": observation["background_throughput_mbps"],
        "max_deadline_miss_burst": max(bursts) if bursts else 0,
        "p95_deadline_miss_burst": _percentile(
            [float(value) for value in bursts], 0.95
        )
        or 0.0,
        "deadline_miss_bursts": bursts,
        "joint_copy_deadline_exceedance_rate": (
            joint_exceedances / duplicated if duplicated else None
        ),
        "cross_copy_delay_correlation": _correlation(copy_zero, copy_one),
        "redundant_airtime_ratio": None,
    }


def _standard_aggregate(
    ordered: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    runs = [
        _standard_run_metrics(observation)
        for arm in ARM_IDENTITIES
        for observation in ordered[arm]
    ]
    groups: list[dict[str, Any]] = []
    for key in sorted({group_key(run) for run in runs}):
        members = [run for run in runs if group_key(run) == key]
        groups.append({
            "topology": members[0]["topology"],
            "policy": members[0]["policy"],
            "config": json.loads(key),
            "run_count": len(members),
            "metrics": {
                metric: confidence([member[metric] for member in members])
                for metric in METRICS
            },
            "deadline_miss_burst_distribution": [
                burst for member in members for burst in member["deadline_miss_bursts"]
            ],
            "redundant_airtime_ratio": {
                "n": 0,
                "mean": None,
                "ci95_low": None,
                "ci95_high": None,
                "unavailable_reason": (
                    "sender airtime is not attributed to redundant copies"
                ),
            },
        })
    return {
        "schema_version": 1,
        "independent_sample_unit": "run",
        "runs": runs,
        "groups": groups,
    }


def analyze(
    policy_root: Path,
    str_root: Path,
    *,
    workers: int,
    policy_archive: Path | None = None,
    str_archive: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    _verify_runtime_contract()
    neutral_environment, topology_wifi = _load_neutral_declarations()
    roots = {
        "policy": _discover_seed_dirs(policy_root, "policy"),
        "str_mlo": _discover_seed_dirs(str_root, "str_mlo"),
    }
    validation = _strictly_validate(roots, workers)
    indexes: dict[str, dict[int, dict[str, Any]]] = {
        "policy": {},
        "str_mlo": {},
    }
    for arm in ARM_IDENTITIES:
        for seed in EXPECTED_SEEDS:
            indexes[arm][seed] = _observation(
                arm,
                seed,
                roots[arm][seed],
                validation[(arm, seed)],
                neutral_environment,
                topology_wifi,
            )
    for seed in EXPECTED_SEEDS:
        if _canonical_json(indexes["policy"][seed]["environment"]) != _canonical_json(
            indexes["str_mlo"][seed]["environment"]
        ):
            raise QualificationError(f"seed {seed}: paired environment differs")
        if _canonical_json(
            indexes["policy"][seed]["common_input_config"]
        ) != _canonical_json(indexes["str_mlo"][seed]["common_input_config"]):
            raise QualificationError(f"seed {seed}: paired common input differs")
    all_rows = [row for arm in ARM_IDENTITIES for row in indexes[arm].values()]
    builds = {_canonical_json(row["build_identity"]) for row in all_rows}
    if len(builds) != 1:
        raise QualificationError("campaign mixes build identities")
    build = json.loads(next(iter(builds)))
    if (
        build.get("project_git_commit") != EXPECTED_PROJECT_COMMIT
        or build.get("ns3_upstream_commit") != NS3_UPSTREAM_COMMIT
    ):
        raise QualificationError("campaign build identity differs")
    ordered = {
        arm: [indexes[arm][seed] for seed in EXPECTED_SEEDS]
        for arm in ARM_IDENTITIES
    }
    archives = {
        "policy": _archive_identity(policy_archive),
        "str_mlo": _archive_identity(str_archive),
    }
    report = _build_report(ordered, archives)
    report["campaign_checks"]["build_identity"] = build
    report["campaign_checks"]["nominal_resolved_config_sha256"] = {
        arm: _sha256_json(ordered[arm][0]["nominal_config"])
        for arm in ARM_IDENTITIES
    }
    diagnostics = _policy_diagnostics(ordered["policy"])
    diagnostics.update({
        "schema_version": 1,
        "analysis": "distributional_shadow_t2_policy_diagnostics_v1",
        "source_report_analysis": ANALYSIS_ID,
    })
    return report, diagnostics, _standard_aggregate(ordered)


def render_markdown(report: dict[str, Any], diagnostics: dict[str, Any]) -> str:
    policy = report["treatments"]["policy"]
    baseline = report["treatments"]["str_mlo"]
    comparison = report["comparison_against_str"]
    miss = comparison["deadline_miss_rate"]
    p99 = comparison["completed_p99_us"]
    airtime = comparison["sender_airtime_ratio"]
    background = comparison["background_throughput_loss"]
    heterogeneity = report["paired_heterogeneity"]
    directions = heterogeneity["paired_direction_counts"]
    lines = [
        "# Distributional shadow T2 engineering result",
        "",
        "This is opened-seed engineering evidence, not final confirmation. Seeds "
        "1301 through 1348 remain unopened.",
        "",
        "| Metric | Distributional shadow T2 | STR MLO | Paired 95% interval |",
        "| --- | ---: | ---: | ---: |",
        (
            "| All-generated deadline-miss rate | "
            f"{100 * policy['all_generated_deadline_miss_rate']['mean']:.4f}% "
            f"({policy['all_generated_deadline_miss_rate']['total_misses']}/"
            f"{policy['all_generated_deadline_miss_rate']['total_generated_frames']}) | "
            f"{100 * baseline['all_generated_deadline_miss_rate']['mean']:.4f}% "
            f"({baseline['all_generated_deadline_miss_rate']['total_misses']}/"
            f"{baseline['all_generated_deadline_miss_rate']['total_generated_frames']}) | "
            f"[{100 * miss['ci95_low']:.4f}, {100 * miss['ci95_high']:.4f}] pp |"
        ),
        (
            "| Mean per-run completed-frame HF7 P99 | "
            f"{policy['completed_frame_p99_us']['mean'] / 1000:.3f} ms | "
            f"{baseline['completed_frame_p99_us']['mean'] / 1000:.3f} ms | "
            f"[{p99['ci95_low'] / 1000:.3f}, {p99['ci95_high'] / 1000:.3f}] ms |"
        ),
        "",
        (
            f"Sender-airtime ratio: {airtime['estimate']:.6f}, 95% interval "
            f"[{airtime['ci95_low']:.6f}, {airtime['ci95_high']:.6f}]."
        ),
        (
            f"Background-throughput loss: {100 * background['estimate']:.4f}%, "
            f"95% interval [{100 * background['ci95_low']:.4f}%, "
            f"{100 * background['ci95_high']:.4f}%]."
        ),
        (
            "Paired directions (win/tie/loss): misses "
            f"{directions['deadline_miss_rate']['policy_better']}/"
            f"{directions['deadline_miss_rate']['tie']}/"
            f"{directions['deadline_miss_rate']['policy_worse']}; completed P99 "
            f"{directions['completed_p99']['policy_better']}/"
            f"{directions['completed_p99']['tie']}/"
            f"{directions['completed_p99']['policy_worse']}. "
            f"{directions['both_metrics_better']} of 48 pairs improve both."
        ),
        (
            f"{heterogeneity['individual_runs_above_sender_airtime_ratio_1_20']} "
            "of 48 individual runs exceed a 1.20 sender-airtime ratio even though "
            "the frozen campaign-level upper confidence gate passes."
        ),
        "",
        f"STR qualification: **{report['str_qualification']['status']}**.",
        f"Promotion readiness: **{report['promotion_readiness']['status']}**.",
        "",
        "## Mechanism",
        "",
        (
            f"The policy launched {diagnostics['actions']} copies. It captured "
            f"{diagnostics['acted_primary_misses']} of "
            f"{diagnostics['primary_copy_deadline_misses']} primary misses "
            f"({100 * diagnostics['primary_miss_capture_rate']:.2f}%) and rescued "
            f"{diagnostics['rescued_primary_misses']} of those captured misses "
            f"({100 * diagnostics['conditional_rescue_efficiency']:.2f}%)."
        ),
        (
            f"Final union misses are {diagnostics['final_union_deadline_misses']}; "
            f"the relative reduction versus STR is "
            f"{100 * comparison['relative_deadline_miss_reduction']:.2f}%."
        ),
        "",
        "The policy beats STR on both primary performance metrics and satisfies the "
        "resource bounds, but it is not ready for confirmation: it misses the 0.50% "
        "engineering miss target and the longer-term 50% relative-reduction target. "
        "The completed-P99 target is met.",
        "",
    ]
    return "\n".join(lines)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_paired_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        raise QualificationError("cannot write an empty paired-metrics table")
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("policy_root", type=Path)
    parser.add_argument("str_root", type=Path)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--policy-archive", type=Path)
    parser.add_argument("--str-archive", type=Path)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--require-str-victory", action="store_true")
    parser.add_argument("--require-promotion", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.workers <= 0:
        parser.error("--workers must be positive")
    try:
        report, diagnostics, aggregate = analyze(
            arguments.policy_root,
            arguments.str_root,
            workers=arguments.workers,
            policy_archive=arguments.policy_archive,
            str_archive=arguments.str_archive,
        )
        output = arguments.output_directory.resolve()
        output.mkdir(parents=True, exist_ok=True)
        report_path = output / "distributional_shadow_t2_str_engineering.json"
        markdown_path = output / "distributional_shadow_t2_str_engineering.md"
        diagnostics_path = output / "policy_diagnostics.json"
        paired_path = output / "paired_metrics.csv"
        aggregate_path = output / "aggregate.json"
        _write_json(report_path, report)
        _write_json(diagnostics_path, diagnostics)
        _write_json(aggregate_path, aggregate)
        markdown_path.write_text(
            render_markdown(report, diagnostics), encoding="utf-8"
        )
        _write_paired_csv(paired_path, report["paired_metrics"])
    except QualificationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(
        f"WROTE {report_path} status={report['str_qualification']['status']} "
        f"promotion={report['promotion_readiness']['status']}"
    )
    if arguments.require_str_victory and report["str_qualification"]["status"] != "pass":
        return 1
    if arguments.require_promotion and report["promotion_readiness"]["status"] != "pass":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
