#!/usr/bin/env python3
"""Strictly qualify the frozen paired-value T2 policy against STR MLO."""

from __future__ import annotations

import argparse
import ast
import copy
import csv
import hashlib
import itertools
import json
import math
import random
import re
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import yaml

from validate_outputs import ValidationError, validate_run


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_CONTRACT_PATH = (
    REPOSITORY_ROOT
    / "experiments/model-selection/paired-value-duplication-t2-runtime-v1.json"
)
RUNTIME_CONTRACT_ID = "paired-value-duplication-t2-runtime-v1"
RUNTIME_CONTRACT_SHA256 = (
    "b9b9caf6cf49e73cb0669107576a17790f59bda4875c43f676caa426393dbf41"
)
NEUTRAL_SOURCE_PATH = REPOSITORY_ROOT / "tools/analyze_primary_tail_t4_campaign.py"
NEUTRAL_SOURCE_SHA256 = (
    "667c43df8a5f9dc57a22647eef6dc8fcad02d19c7dc187a17227bf5cb1b02d47"
)
NEUTRAL_ENVIRONMENT_SHA256 = (
    "5d1774e3b38f27908de3d845953cad825e3f4207d738996952315e63097382dc"
)
TOPOLOGY_WIFI_SHA256 = (
    "5d42bd03cc5efa2d42465b10da61207e5ddf0386121d9e125c1b5afc44462107"
)

POLICY_NAME = "paired_value_duplication_t2"
POLICY_IDENTITY = ("dual_interface", POLICY_NAME)
STR_IDENTITY = ("mlo_str", "fixed_link_0")
ARM_IDENTITIES = {"policy": POLICY_IDENTITY, "str_mlo": STR_IDENTITY}
ARM_LABELS = {"policy": "Paired-value T2", "str_mlo": "STR MLO"}

EXPECTED_SEED_RUN_UNITS = tuple((seed, 1) for seed in range(1201, 1249))
RESERVED_FINAL_CONFIRMATION_SEEDS = tuple(range(1301, 1349))
EXPECTED_PAIR_COUNT = 48
EXPECTED_RUN_COUNT = 96
BOOTSTRAP_SEED = 20260804
BOOTSTRAP_REPLICATIONS = 10_000
CONFIDENCE_LEVEL = 0.95
MINIMUM_COMPLETED_FRAMES = 100
MAXIMUM_AIRTIME_RATIO = 1.20
MAXIMUM_BACKGROUND_LOSS = 0.01
MANIFEST_SCHEMA_VERSION = 2
NS3_UPSTREAM_COMMIT = "d2add90b452d600cfb4859baed8e9ea633519447"
PREDICTION_SCHEMA_VERSIONS = {
    "telemetry_schema_version": 3,
    "polling_schema_version": 1,
    "event_schema_version": 2,
    "feature_support_mask_version": 2,
}
RUN_ID_RUNTIME_CONTRACT_KEY = "runtime_contract"

BUILD_IDENTITY_FIELDS = (
    "ns3_version",
    "ns3_upstream_commit",
    "project_git_commit",
    "compiler",
    "build_profile",
)
ENVIRONMENT_KEYS = (
    "duration_s",
    "warmup_s",
    "measurement_start_s",
    "measurement_stop_s",
    "stream",
    "propagation",
    "background",
)
SHARED_WIFI_KEYS = (
    "standard",
    "station_manager",
    "data_mode",
    "control_mode",
    "guard_interval",
    "channel_settings",
    "frequency_ranges",
    "data_modes_per_link",
    "queue_max_packets",
    "queue_max_delay_ms",
    "max_ampdu_size_bytes",
    "max_amsdu_size_bytes",
    "sta_max_inflights",
    "ul_ofdma_enabled",
    "ul_ofdma_scope",
    "ul_ofdma_access_interval_ms",
    "ul_ofdma_bsrp_enabled",
    "ul_ofdma_max_stations",
    "ul_ofdma_psdu_size_bytes",
    "block_ack_enabled",
    "frame_retry_limit",
    "rts_cts_threshold_bytes",
    "fragmentation_threshold_bytes",
    "access_category",
    "txop_limit_us",
    "application_duplication",
)

REQUIRED_RUN_ARTIFACTS = {
    "resolved_config.json",
    "build_info.json",
    "frames.csv",
    "policy_decisions.csv",
    "link_intervals.csv",
    "mac_summary.csv",
    "summary.json",
    "stdout.log",
    "background_flows.csv",
    "background_rate_periods.csv",
    "ofdma_summary.csv",
}
POLICY_RUN_ARTIFACTS = {
    "prediction_samples.csv",
    "prediction_polling_samples.csv",
    "paired_value_t2_decisions.csv",
    "paired_value_t2_summary.json",
    "secondary_airtime_events.csv",
    "secondary_airtime_settlements.csv",
    "secondary_airtime_summary.json",
}

SOURCE_ARTIFACTS = {
    "frozen_selection": {
        "path": "experiments/model-selection/temporal-t2-primary-only-two-objective-v1.json",
        "sha256": "c7f886a4ca1a29b9fbd2e25d19d78f994d7136ecdea4f6a16db77eacacf5ce9f",
    },
    "canonical_fit_manifest": {
        "path": (
            "results/randomized_full_copy_exploration_collection_v1/"
            "temporal_t2_primary_only_two_objective_v1/artifact_manifest.json"
        ),
        "sha256": "b3af02b647c7671a631f3d43ebece75781989889358c845335d4003610a8208f",
    },
    "canonical_model_pickle": {
        "path": (
            "results/randomized_full_copy_exploration_collection_v1/"
            "temporal_t2_primary_only_two_objective_v1/temporal_t2_value_models.pkl"
        ),
        "sha256": "dff01b0f8319320489709c4039d97011f35439aa92adedbe167fe61b9de7bcb8",
    },
    "canonical_candidates": {
        "path": (
            "results/randomized_full_copy_exploration_collection_v1/"
            "temporal_t2_primary_only_two_objective_v1/"
            "temporal_t2_value_policy_candidates.csv"
        ),
        "sha256": "7cbd5c622838df0a2f752c3bf9f4c54f333f7d280a9240cb80eda19efb1c28bb",
    },
    "canonical_metrics": {
        "path": (
            "results/randomized_full_copy_exploration_collection_v1/"
            "temporal_t2_primary_only_two_objective_v1/"
            "temporal_t2_value_training_metrics.json"
        ),
        "sha256": "35929f0638b03ec79f2f3967dd947265c3d73b7fa51f487299cc1d96a555a014",
    },
}


@dataclass(frozen=True)
class QualificationProfile:
    """Frozen evidence boundary for one two-arm engineering campaign."""

    key: str
    runtime_contract_path: Path
    runtime_contract_id: str
    runtime_contract_sha256: str
    expected_seed_run_units: tuple[tuple[int, int], ...]
    policy_label: str
    analysis_id: str
    markdown_title: str
    contract_kind: str


V1_PROFILE = QualificationProfile(
    key="v1",
    runtime_contract_path=RUNTIME_CONTRACT_PATH,
    runtime_contract_id=RUNTIME_CONTRACT_ID,
    runtime_contract_sha256=RUNTIME_CONTRACT_SHA256,
    expected_seed_run_units=EXPECTED_SEED_RUN_UNITS,
    policy_label=ARM_LABELS["policy"],
    analysis_id="paired_value_t2_str_qualification",
    markdown_title="Paired-value T2 qualification against STR MLO",
    contract_kind="base_v1",
)
SCORE_AWARE_V2_PROFILE = QualificationProfile(
    key="score-aware-v2",
    runtime_contract_path=(
        REPOSITORY_ROOT
        / "experiments/model-selection/paired-value-duplication-t2-score-aware-emergency-v2.json"
    ),
    runtime_contract_id="paired-value-duplication-t2-score-aware-emergency-v2",
    runtime_contract_sha256=(
        "bdc5b2a944475d1cc31749100e333a2eb2059e106eaf86d918855b721ab3fcda"
    ),
    expected_seed_run_units=tuple((seed, 1) for seed in range(1251, 1299)),
    policy_label="Score-aware T2 V2",
    analysis_id="paired_value_t2_score_aware_str_engineering",
    markdown_title="Score-aware T2 V2 engineering against STR MLO",
    contract_kind="score_aware_v2",
)
FULL_HORIZON_V3_PROFILE = QualificationProfile(
    key="full-horizon-v3",
    runtime_contract_path=(
        REPOSITORY_ROOT
        / "experiments/model-selection/paired-value-duplication-t2-full-horizon-carryover-v3.json"
    ),
    runtime_contract_id="paired-value-duplication-t2-full-horizon-carryover-v3",
    runtime_contract_sha256=(
        "16ccbbfc19ac5c6b824c65b5f00fd0a8792610ea9239e9277390f51eda83f9d8"
    ),
    expected_seed_run_units=tuple((seed, 1) for seed in range(1251, 1299)),
    policy_label="Full-horizon T2 V3",
    analysis_id="paired_value_t2_full_horizon_str_engineering",
    markdown_title="Full-horizon T2 V3 engineering against STR MLO",
    contract_kind="full_horizon_v3",
)
REMAINING_REFILL_V4_PROFILE = QualificationProfile(
    key="remaining-refill-v4",
    runtime_contract_path=(
        REPOSITORY_ROOT
        / "experiments/model-selection/"
        "paired-value-duplication-t2-remaining-refill-borrowing-v4.json"
    ),
    runtime_contract_id="paired-value-duplication-t2-remaining-refill-borrowing-v4",
    runtime_contract_sha256=(
        "0b5d31861c862e1b4fb31231936ecd144958939308b21566e97405a29de0d9dd"
    ),
    expected_seed_run_units=tuple((seed, 1) for seed in range(1251, 1299)),
    policy_label="Remaining-refill T2 V4",
    analysis_id="paired_value_t2_remaining_refill_str_engineering",
    markdown_title="Remaining-refill T2 V4 engineering against STR MLO",
    contract_kind="remaining_refill_v4",
)
COST_FREE_V5_PROFILE = QualificationProfile(
    key="cost-free-v5",
    runtime_contract_path=(
        REPOSITORY_ROOT
        / "experiments/model-selection/"
        "paired-value-duplication-t2-cost-free-score-aware-v5.json"
    ),
    runtime_contract_id="paired-value-duplication-t2-cost-free-score-aware-v5",
    runtime_contract_sha256=(
        "b7fb00982ae090fe1142b39adf0ad6d26d253741dd5059ed95637dd86047ba96"
    ),
    expected_seed_run_units=tuple((seed, 1) for seed in range(1251, 1299)),
    policy_label="Cost-free T2 V5",
    analysis_id="paired_value_t2_cost_free_str_engineering",
    markdown_title="Cost-free T2 V5 engineering against STR MLO",
    contract_kind="cost_free_v5",
)
PROFILES = {
    profile.key: profile
    for profile in (
        V1_PROFILE,
        SCORE_AWARE_V2_PROFILE,
        FULL_HORIZON_V3_PROFILE,
        REMAINING_REFILL_V4_PROFILE,
        COST_FREE_V5_PROFILE,
    )
}


class QualificationError(ValueError):
    """Raised before inference when campaign evidence is not contract-complete."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise QualificationError("metadata contains a non-JSON or non-finite value") from error


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError as error:
        raise QualificationError(f"cannot hash {path}: {error}") from error


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value).encode("utf-8"))


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


def _read_csv(path: Path, required_columns: set[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise QualificationError(f"missing required artifact: {path}")
    try:
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            fieldnames = reader.fieldnames or []
            if len(fieldnames) != len(set(fieldnames)):
                raise QualificationError(f"{path}: duplicate CSV columns")
            missing = sorted(required_columns - set(fieldnames))
            if missing:
                raise QualificationError(f"{path}: missing columns {', '.join(missing)}")
            rows = list(reader)
    except OSError as error:
        raise QualificationError(f"cannot read {path}: {error}") from error
    if any(None in row for row in rows):
        raise QualificationError(f"{path}: row exceeds the declared CSV width")
    return rows


def _finite(value: Any, description: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise QualificationError(f"{description} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise QualificationError(f"{description} must be a finite number") from error
    if not math.isfinite(result) or (nonnegative and result < 0):
        qualifier = "nonnegative " if nonnegative else ""
        raise QualificationError(f"{description} must be a {qualifier}finite number")
    return result


def _integer(value: Any, description: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or re.fullmatch(r"(?:0|[1-9][0-9]*)", str(value)) is None:
        raise QualificationError(f"{description} must be a nonnegative integer")
    result = int(value)
    if positive and result == 0:
        raise QualificationError(f"{description} must be positive")
    return result


def _flag(value: Any, description: str) -> bool:
    if str(value) not in {"0", "1"}:
        raise QualificationError(f"{description} must be 0 or 1")
    return str(value) == "1"


def _safe_literal(node: ast.AST, values: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in values:
        return copy.deepcopy(values[node.id])
    if isinstance(node, ast.List):
        return [_safe_literal(item, values) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_safe_literal(item, values) for item in node.elts)
    if isinstance(node, ast.Set):
        return {_safe_literal(item, values) for item in node.elts}
    if isinstance(node, ast.Dict):
        return {
            _safe_literal(key, values): _safe_literal(value, values)
            for key, value in zip(node.keys, node.values)
        }
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        operand = _safe_literal(node.operand, values)
        if isinstance(operand, bool) or not isinstance(operand, (int, float)):
            raise QualificationError("neutral declaration has a nonnumeric unary literal")
        return -operand if isinstance(node.op, ast.USub) else operand
    raise QualificationError("neutral source declarations are no longer literal constants")


def _load_neutral_declarations() -> tuple[dict[str, Any], dict[str, Any]]:
    if _sha256_file(NEUTRAL_SOURCE_PATH) != NEUTRAL_SOURCE_SHA256:
        raise QualificationError("hash-pinned neutral environment source changed")
    try:
        tree = ast.parse(NEUTRAL_SOURCE_PATH.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as error:
        raise QualificationError(f"cannot parse neutral environment source: {error}") from error
    values: dict[str, Any] = {}
    wanted = {
        "DECLARED_EMLSR_DISABLED",
        "DECLARED_TOPOLOGY_WIFI",
        "DECLARED_NEUTRAL_ENVIRONMENT",
    }
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if not isinstance(target, ast.Name) or target.id not in wanted:
            continue
        values[target.id] = _safe_literal(statement.value, values)
    missing = wanted - values.keys()
    if missing:
        raise QualificationError(f"neutral source omits declarations {sorted(missing)}")
    environment = values["DECLARED_NEUTRAL_ENVIRONMENT"]
    topology_wifi = values["DECLARED_TOPOLOGY_WIFI"]
    if _sha256_json(environment) != NEUTRAL_ENVIRONMENT_SHA256:
        raise QualificationError("neutral environment projection hash changed")
    if _sha256_json(topology_wifi) != TOPOLOGY_WIFI_SHA256:
        raise QualificationError("topology Wi-Fi projection hash changed")
    return environment, topology_wifi


def _verify_analyzer_contract(contract: dict[str, Any]) -> None:
    """Verify the inherited statistical and final-confirmation boundary."""
    analyzer = contract.get("confirmation_analyzer_contract", {})
    confirmation = contract.get("fresh_confirmation_contract", {})
    expected_analyzer = {
        "random_seed": BOOTSTRAP_SEED,
        "replications": BOOTSTRAP_REPLICATIONS,
        "confidence_level": CONFIDENCE_LEVEL,
    }
    if any(analyzer.get(key) != value for key, value in expected_analyzer.items()):
        raise QualificationError("frozen bootstrap contract differs from this analyzer")
    if (
        confirmation.get("seeds") != list(RESERVED_FINAL_CONFIRMATION_SEEDS)
        or confirmation.get("ns3_rng_runs") != [1]
        or confirmation.get("seed_count") != EXPECTED_PAIR_COUNT
    ):
        raise QualificationError("frozen confirmation unit declaration changed")
    completed = analyzer.get("completed_frame_p99", {})
    airtime = analyzer.get("sender_airtime_target", {})
    background = analyzer.get("background_throughput_target", {})
    if (
        completed.get("minimum_completed_frames_per_run") != MINIMUM_COMPLETED_FRAMES
        or airtime.get("exclusive_ratio_upper_bound") != MAXIMUM_AIRTIME_RATIO
        or background.get("inclusive_loss_fraction_upper_bound") != MAXIMUM_BACKGROUND_LOSS
    ):
        raise QualificationError("frozen qualification thresholds changed")


def _verify_source_closure(
    profile: QualificationProfile = V1_PROFILE,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    observed_contract_sha = _sha256_file(profile.runtime_contract_path)
    if observed_contract_sha != profile.runtime_contract_sha256:
        raise QualificationError(
            "runtime contract checksum changed: "
            f"expected {profile.runtime_contract_sha256}, found {observed_contract_sha}"
        )
    contract = _read_json(profile.runtime_contract_path)
    if contract.get("runtime_contract_id") != profile.runtime_contract_id:
        raise QualificationError("runtime contract identity changed")

    if profile.contract_kind == "base_v1":
        if contract.get("selected_policy_contract", {}).get("policy_name") != POLICY_NAME:
            raise QualificationError("runtime contract policy identity changed")
        analyzer_contract = contract
    elif profile.contract_kind == "score_aware_v2":
        expected_inheritance = {
            "runtime_contract_id": RUNTIME_CONTRACT_ID,
            "path": str(RUNTIME_CONTRACT_PATH.relative_to(REPOSITORY_ROOT)),
            "sha256": RUNTIME_CONTRACT_SHA256,
        }
        if _canonical_json(contract.get("inherits")) != _canonical_json(
            expected_inheritance
        ):
            raise QualificationError("score-aware contract inheritance changed")
        if _sha256_file(RUNTIME_CONTRACT_PATH) != RUNTIME_CONTRACT_SHA256:
            raise QualificationError("inherited runtime contract checksum changed")
        analyzer_contract = _read_json(RUNTIME_CONTRACT_PATH)
        if (
            analyzer_contract.get("runtime_contract_id") != RUNTIME_CONTRACT_ID
            or analyzer_contract.get("selected_policy_contract", {}).get("policy_name")
            != POLICY_NAME
            or contract.get("unchanged_contract", {}).get("policy_name") != POLICY_NAME
        ):
            raise QualificationError("score-aware inherited policy identity changed")
        boundary = contract.get("evaluation_boundary", {})
        expected_seeds = [seed for seed, _ in profile.expected_seed_run_units]
        expected_primary_gates = [
            "paired all-generated deadline-miss difference confidence interval below zero",
            (
                "paired mean per-run completed-frame HF7 P99 difference "
                "confidence interval below zero"
            ),
        ]
        if (
            boundary.get("fresh_engineering_seed_start") != min(expected_seeds)
            or boundary.get("fresh_engineering_seed_stop_inclusive") != max(expected_seeds)
            or boundary.get("reserved_confirmation_seed_start")
            != RESERVED_FINAL_CONFIRMATION_SEEDS[0]
            or boundary.get("reserved_confirmation_seed_stop_inclusive")
            != RESERVED_FINAL_CONFIRMATION_SEEDS[-1]
            or boundary.get(
                "reserved_confirmation_seeds_must_remain_unopened_until_engineering_pass"
            )
            is not True
            or boundary.get("reference") != "STR MLO NMaxInflights=1"
            or boundary.get("primary_gates") != expected_primary_gates
            or boundary.get("resource_gates", {}).get(
                "sender_airtime_ratio_strictly_below"
            )
            != MAXIMUM_AIRTIME_RATIO
            or boundary.get("resource_gates", {}).get(
                "background_throughput_loss_fraction_at_most"
            )
            != MAXIMUM_BACKGROUND_LOSS
        ):
            raise QualificationError("score-aware evaluation boundary changed")
    elif profile.contract_kind == "full_horizon_v3":
        expected_inheritance = {
            "runtime_contract_id": SCORE_AWARE_V2_PROFILE.runtime_contract_id,
            "path": str(
                SCORE_AWARE_V2_PROFILE.runtime_contract_path.relative_to(
                    REPOSITORY_ROOT
                )
            ),
            "sha256": SCORE_AWARE_V2_PROFILE.runtime_contract_sha256,
        }
        if _canonical_json(contract.get("inherits")) != _canonical_json(
            expected_inheritance
        ):
            raise QualificationError("full-horizon contract inheritance changed")
        if (
            _sha256_file(SCORE_AWARE_V2_PROFILE.runtime_contract_path)
            != SCORE_AWARE_V2_PROFILE.runtime_contract_sha256
        ):
            raise QualificationError("inherited score-aware contract checksum changed")
        inherited = _read_json(SCORE_AWARE_V2_PROFILE.runtime_contract_path)
        expected_v2_inheritance = {
            "runtime_contract_id": RUNTIME_CONTRACT_ID,
            "path": str(RUNTIME_CONTRACT_PATH.relative_to(REPOSITORY_ROOT)),
            "sha256": RUNTIME_CONTRACT_SHA256,
        }
        if (
            inherited.get("runtime_contract_id")
            != SCORE_AWARE_V2_PROFILE.runtime_contract_id
            or _canonical_json(inherited.get("inherits"))
            != _canonical_json(expected_v2_inheritance)
            or _sha256_file(RUNTIME_CONTRACT_PATH) != RUNTIME_CONTRACT_SHA256
        ):
            raise QualificationError("full-horizon inherited source closure changed")
        analyzer_contract = _read_json(RUNTIME_CONTRACT_PATH)
        if (
            analyzer_contract.get("runtime_contract_id") != RUNTIME_CONTRACT_ID
            or analyzer_contract.get("selected_policy_contract", {}).get("policy_name")
            != POLICY_NAME
            or contract.get("unchanged_contract", {}).get("policy_name")
            != POLICY_NAME
        ):
            raise QualificationError("full-horizon inherited policy identity changed")
        carryover = contract.get("carryover_override", {})
        if (
            carryover.get("profile_id") != "score_aware_full_horizon_v3"
            or carryover.get("budget_max_horizon_us") != 60_000_000
            or carryover.get("budget_capacity_us") != 360_000
            or carryover.get("budget_initial_horizon_us") != 2_000_000
            or carryover.get("budget_initial_credit_us") != 12_000
            or carryover.get("initial_credit_is_not_capacity") is not True
            or carryover.get("no_unearned_full_horizon_credit_at_startup") is not True
            or carryover.get("long_run_refill_rate_changed") is not False
            or carryover.get("emergency_tier_changed") is not False
        ):
            raise QualificationError("full-horizon carry-over override changed")
        boundary = contract.get("evaluation_boundary", {})
        expected_seeds = [seed for seed, _ in profile.expected_seed_run_units]
        expected_primary_gates = [
            "paired all-generated deadline-miss difference confidence interval below zero",
            (
                "paired mean per-run completed-frame HF7 P99 difference "
                "confidence interval below zero"
            ),
        ]
        if (
            boundary.get("engineering_seed_start") != min(expected_seeds)
            or boundary.get("engineering_seed_stop_inclusive") != max(expected_seeds)
            or boundary.get("engineering_seeds_previously_opened") is not True
            or boundary.get("reserved_confirmation_seed_start")
            != RESERVED_FINAL_CONFIRMATION_SEEDS[0]
            or boundary.get("reserved_confirmation_seed_stop_inclusive")
            != RESERVED_FINAL_CONFIRMATION_SEEDS[-1]
            or boundary.get("reserved_confirmation_seeds_must_remain_unopened")
            is not True
            or boundary.get("reference") != "STR MLO NMaxInflights=1"
            or boundary.get("primary_gates") != expected_primary_gates
            or boundary.get("resource_gates", {}).get(
                "sender_airtime_ratio_strictly_below"
            )
            != MAXIMUM_AIRTIME_RATIO
            or boundary.get("resource_gates", {}).get(
                "background_throughput_loss_fraction_at_most"
            )
            != MAXIMUM_BACKGROUND_LOSS
        ):
            raise QualificationError("full-horizon evaluation boundary changed")
    elif profile.contract_kind == "remaining_refill_v4":
        expected_inheritance = {
            "runtime_contract_id": FULL_HORIZON_V3_PROFILE.runtime_contract_id,
            "path": str(
                FULL_HORIZON_V3_PROFILE.runtime_contract_path.relative_to(
                    REPOSITORY_ROOT
                )
            ),
            "sha256": FULL_HORIZON_V3_PROFILE.runtime_contract_sha256,
        }
        if _canonical_json(contract.get("inherits")) != _canonical_json(
            expected_inheritance
        ):
            raise QualificationError("remaining-refill contract inheritance changed")
        if (
            _sha256_file(FULL_HORIZON_V3_PROFILE.runtime_contract_path)
            != FULL_HORIZON_V3_PROFILE.runtime_contract_sha256
        ):
            raise QualificationError("inherited full-horizon contract checksum changed")
        inherited = _read_json(FULL_HORIZON_V3_PROFILE.runtime_contract_path)
        expected_v3_inheritance = {
            "runtime_contract_id": SCORE_AWARE_V2_PROFILE.runtime_contract_id,
            "path": str(
                SCORE_AWARE_V2_PROFILE.runtime_contract_path.relative_to(
                    REPOSITORY_ROOT
                )
            ),
            "sha256": SCORE_AWARE_V2_PROFILE.runtime_contract_sha256,
        }
        if (
            inherited.get("runtime_contract_id")
            != FULL_HORIZON_V3_PROFILE.runtime_contract_id
            or _canonical_json(inherited.get("inherits"))
            != _canonical_json(expected_v3_inheritance)
            or _sha256_file(SCORE_AWARE_V2_PROFILE.runtime_contract_path)
            != SCORE_AWARE_V2_PROFILE.runtime_contract_sha256
            or _sha256_file(RUNTIME_CONTRACT_PATH) != RUNTIME_CONTRACT_SHA256
        ):
            raise QualificationError("remaining-refill inherited source closure changed")
        analyzer_contract = _read_json(RUNTIME_CONTRACT_PATH)
        if (
            analyzer_contract.get("runtime_contract_id") != RUNTIME_CONTRACT_ID
            or analyzer_contract.get("selected_policy_contract", {}).get("policy_name")
            != POLICY_NAME
            or contract.get("unchanged_contract", {}).get("policy_name")
            != POLICY_NAME
        ):
            raise QualificationError("remaining-refill inherited policy identity changed")
        remaining_refill = contract.get("remaining_refill_override", {})
        expected_semantics = [
            "Apply inherited strict admission without change.",
            (
                "If strict admission fails, apply the inherited high-score emergency "
                "threshold and 60000 us debt limit without change."
            ),
            (
                "If neither inherited tier admits, consider every primary-score-"
                "threshold passer for the remaining-refill tier."
            ),
            (
                "Admit the final tier only when current available balance plus refill "
                "causally remaining before measurement stop covers the conservative "
                "canonical reservation."
            ),
            (
                "Do not mutate the guard balance or install future credit at decision "
                "time; measured secondary airtime remains the only debit and later "
                "refill repays any realized debt."
            ),
        ]
        if (
            remaining_refill.get("profile_id")
            != "score_aware_remaining_refill_v4"
            or remaining_refill.get("repayment_stop_ns") != 61_000_000_000
            or remaining_refill.get("remaining_refill_credit_formula")
            != (
                "budget_fraction * (repayment_stop_ns - "
                "last_causal_guard_refill_ns) / 1000"
            )
            or remaining_refill.get("ordered_admission_semantics")
            != expected_semantics
            or remaining_refill.get("strict_admission_changed") is not False
            or remaining_refill.get("high_score_emergency_changed") is not False
            or remaining_refill.get("primary_score_threshold_changed") is not False
            or remaining_refill.get("long_run_refill_rate_changed") is not False
            or remaining_refill.get("startup_credit_changed") is not False
            or remaining_refill.get("learned_cost_used_for_token_accounting") is not False
            or remaining_refill.get("new_tier_can_only_add_actions") is not True
        ):
            raise QualificationError("remaining-refill admission override changed")
        telemetry = contract.get("telemetry_contract", {})
        if (
            telemetry.get("decision_csv_schema_version") != 3
            or telemetry.get("controller_summary_schema_version") != 3
            or telemetry.get("v2_columns_retained_in_order") is not True
            or telemetry.get("decision_columns_appended")
            != [
                "remaining_refill_credit_us",
                "remaining_refill_admission_considered",
                "remaining_refill_admitted",
            ]
            or telemetry.get("admission_tier_values")
            != ["none", "strict", "emergency", "remaining_refill"]
        ):
            raise QualificationError("remaining-refill telemetry contract changed")
        boundary = contract.get("evaluation_boundary", {})
        expected_seeds = [seed for seed, _ in profile.expected_seed_run_units]
        expected_primary_gates = [
            "paired all-generated deadline-miss difference confidence interval below zero",
            (
                "paired mean per-run completed-frame HF7 P99 difference "
                "confidence interval below zero"
            ),
        ]
        if (
            boundary.get("engineering_seed_start") != min(expected_seeds)
            or boundary.get("engineering_seed_stop_inclusive") != max(expected_seeds)
            or boundary.get("engineering_seeds_previously_opened") is not True
            or boundary.get("reserved_confirmation_seed_start")
            != RESERVED_FINAL_CONFIRMATION_SEEDS[0]
            or boundary.get("reserved_confirmation_seed_stop_inclusive")
            != RESERVED_FINAL_CONFIRMATION_SEEDS[-1]
            or boundary.get("reserved_confirmation_seeds_must_remain_unopened")
            is not True
            or boundary.get("reference") != "STR MLO NMaxInflights=1"
            or boundary.get("primary_gates") != expected_primary_gates
            or boundary.get("resource_gates", {}).get(
                "sender_airtime_ratio_strictly_below"
            )
            != MAXIMUM_AIRTIME_RATIO
            or boundary.get("resource_gates", {}).get(
                "background_throughput_loss_fraction_at_most"
            )
            != MAXIMUM_BACKGROUND_LOSS
        ):
            raise QualificationError("remaining-refill evaluation boundary changed")
    elif profile.contract_kind == "cost_free_v5":
        expected_inheritance = {
            "runtime_contract_id": SCORE_AWARE_V2_PROFILE.runtime_contract_id,
            "path": str(
                SCORE_AWARE_V2_PROFILE.runtime_contract_path.relative_to(
                    REPOSITORY_ROOT
                )
            ),
            "sha256": SCORE_AWARE_V2_PROFILE.runtime_contract_sha256,
        }
        if _canonical_json(contract.get("inherits")) != _canonical_json(
            expected_inheritance
        ):
            raise QualificationError("cost-free contract inheritance changed")
        if (
            _sha256_file(SCORE_AWARE_V2_PROFILE.runtime_contract_path)
            != SCORE_AWARE_V2_PROFILE.runtime_contract_sha256
            or _sha256_file(RUNTIME_CONTRACT_PATH) != RUNTIME_CONTRACT_SHA256
        ):
            raise QualificationError("cost-free inherited source closure changed")
        analyzer_contract = _read_json(RUNTIME_CONTRACT_PATH)
        if (
            analyzer_contract.get("runtime_contract_id") != RUNTIME_CONTRACT_ID
            or analyzer_contract.get("selected_policy_contract", {}).get(
                "policy_name"
            )
            != POLICY_NAME
            or contract.get("unchanged_contract", {}).get("policy_name")
            != POLICY_NAME
        ):
            raise QualificationError("cost-free inherited policy identity changed")

        score = contract.get("score_override", {})
        if (
            score.get("profile_id") != "cost_free_score_aware_v5"
            or score.get("ranker") != "legacy_bad12_value"
            or score.get("formula")
            != (
                "float32(max(primary_bad12_probability - "
                "treated_bad12_probability, 0))"
            )
            or score.get("learned_cost_denominator_removed") is not True
            or score.get("learned_cost_retained_as_diagnostic") is not True
            or score.get("learned_cost_used_for_token_accounting") is not False
            or score.get("primary_score_threshold_float32")
            != 0.18692325055599213
            or score.get("primary_score_threshold_float32_bits_hex")
            != "0x3e3f68cf"
            or score.get("primary_requested_global_action_fraction") != 0.165
            or score.get("emergency_score_threshold_float32")
            != 0.3069669306278229
            or score.get("emergency_score_threshold_float32_bits_hex")
            != "0x3e9d2ac5"
            or score.get("emergency_requested_global_action_fraction") != 0.0825
            or score.get("closed_loop_v2_outcomes_not_used_in_threshold_formula")
            is not True
        ):
            raise QualificationError("cost-free score override changed")
        admission = contract.get("admission_inheritance", {})
        if (
            admission.get("v2_strict_admission_changed") is not False
            or admission.get("v2_emergency_debt_limit_changed") is not False
            or admission.get("v2_guard_horizon_or_capacity_changed") is not False
            or admission.get("v2_startup_credit_changed") is not False
            or admission.get("v2_long_run_refill_rate_changed") is not False
            or admission.get("canonical_reserved_cost_used_for_both_tiers")
            is not True
            or admission.get("admission_is_query_only") is not True
        ):
            raise QualificationError("cost-free admission inheritance changed")
        telemetry = contract.get("telemetry_override", {})
        if (
            telemetry.get("decision_csv_schema_version") != 4
            or telemetry.get("controller_summary_schema_version") != 4
            or telemetry.get("v2_columns_retained_in_order") is not True
            or telemetry.get("decision_columns_appended")
            != ["policy_score_float32"]
            or telemetry.get("value_per_cost_score_float32_is_diagnostic_only")
            is not True
            or telemetry.get("admission_tier_values")
            != ["none", "strict", "emergency"]
        ):
            raise QualificationError("cost-free telemetry contract changed")
        evidence = contract.get("selection_evidence", {})
        selection_files = (
            (
                evidence.get("cost_ablation_manifest_path"),
                evidence.get("cost_ablation_manifest_sha256"),
            ),
            (
                evidence.get("cost_ablation_metrics_path"),
                evidence.get("cost_ablation_metrics_sha256"),
            ),
            (
                evidence.get("cost_ablation_candidates_path"),
                evidence.get("cost_ablation_candidates_sha256"),
            ),
        )
        for relative, expected_sha256 in selection_files:
            if (
                not isinstance(relative, str)
                or not isinstance(expected_sha256, str)
                or _sha256_file(REPOSITORY_ROOT / relative) != expected_sha256
            ):
                raise QualificationError("cost-free selection evidence changed")

        boundary = contract.get("evaluation_boundary", {})
        expected_seeds = [seed for seed, _ in profile.expected_seed_run_units]
        expected_primary_gates = [
            "paired all-generated deadline-miss difference confidence interval below zero",
            (
                "paired mean per-run completed-frame HF7 P99 difference "
                "confidence interval below zero"
            ),
        ]
        if (
            boundary.get("engineering_seed_start") != min(expected_seeds)
            or boundary.get("engineering_seed_stop_inclusive") != max(expected_seeds)
            or boundary.get("engineering_seeds_previously_opened") is not True
            or boundary.get("reserved_confirmation_seed_start")
            != RESERVED_FINAL_CONFIRMATION_SEEDS[0]
            or boundary.get("reserved_confirmation_seed_stop_inclusive")
            != RESERVED_FINAL_CONFIRMATION_SEEDS[-1]
            or boundary.get("reserved_confirmation_seeds_must_remain_unopened")
            is not True
            or boundary.get("reference") != "STR MLO NMaxInflights=1"
            or boundary.get("primary_gates") != expected_primary_gates
            or boundary.get("resource_gates", {}).get(
                "sender_airtime_ratio_strictly_below"
            )
            != MAXIMUM_AIRTIME_RATIO
            or boundary.get("resource_gates", {}).get(
                "background_throughput_loss_fraction_at_most"
            )
            != MAXIMUM_BACKGROUND_LOSS
        ):
            raise QualificationError("cost-free evaluation boundary changed")
    else:
        raise QualificationError(f"unsupported qualification profile {profile.key!r}")

    _verify_analyzer_contract(analyzer_contract)
    if len(profile.expected_seed_run_units) != EXPECTED_PAIR_COUNT:
        raise QualificationError("engineering profile does not declare exactly 48 units")
    if set(seed for seed, _ in profile.expected_seed_run_units) & set(
        RESERVED_FINAL_CONFIRMATION_SEEDS
    ):
        raise QualificationError("engineering units overlap reserved final-confirmation seeds")
    contract_sources = (
        contract.get("runtime_outputs", {})
        .get("controller_summary_json", {})
        .get("required_source_artifacts_exact")
    )
    if _canonical_json(contract_sources) != _canonical_json(SOURCE_ARTIFACTS):
        raise QualificationError("runtime contract source-artifact declaration changed")
    for identity in SOURCE_ARTIFACTS.values():
        path = REPOSITORY_ROOT / identity["path"]
        digest = _sha256_file(path)
        if digest != identity["sha256"]:
            raise QualificationError(
                f"hash-pinned source artifact changed: {identity['path']}"
            )
    environment, topology_wifi = _load_neutral_declarations()
    return contract, environment, topology_wifi


def _merge_yaml(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_yaml(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_yaml(path: Path, seen: set[Path] | None = None) -> dict[str, Any]:
    path = path.resolve()
    visited = set() if seen is None else set(seen)
    if path in visited:
        raise QualificationError(f"experiment YAML inheritance cycle at {path}")
    visited.add(path)
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise QualificationError(f"cannot read campaign config {path}: {error}") from error
    if not isinstance(value, dict):
        raise QualificationError(f"{path}: campaign config root must be a mapping")
    value = copy.deepcopy(value)
    extends = value.pop("extends", None)
    if extends is None:
        return value
    if not isinstance(extends, str) or not extends:
        raise QualificationError(f"{path}: extends must be a nonempty path")
    parent = Path(extends)
    if not parent.is_absolute():
        parent = path.parent / parent
    return _merge_yaml(_load_yaml(parent, visited), value)


def _entries(values: Any, description: str) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not values:
        raise QualificationError(f"{description} must be a nonempty list")
    result: list[dict[str, Any]] = []
    for value in values:
        entry = {"name": value} if isinstance(value, str) else copy.deepcopy(value)
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise QualificationError(f"invalid {description} entry {value!r}")
        result.append(entry)
    return result


def _set_dotted(target: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    if not all(parts):
        raise QualificationError(f"invalid dotted campaign key {dotted!r}")
    node = target
    for part in parts[:-1]:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            raise QualificationError(f"campaign key traverses scalar {dotted!r}")
        node = child
    node[parts[-1]] = copy.deepcopy(value)


def _expand_config(document: dict[str, Any]) -> list[dict[str, Any]]:
    base = document.get("base", {})
    seeds = document.get("seeds", [1])
    runs = document.get("runs", [1])
    if not isinstance(base, dict):
        raise QualificationError("campaign base must be a mapping")
    if (
        not isinstance(seeds, list)
        or not isinstance(runs, list)
        or not all(isinstance(value, int) and not isinstance(value, bool) and value > 0
                   for value in [*seeds, *runs])
    ):
        raise QualificationError("campaign seeds and runs must be positive integers")
    topologies = _entries(document.get("topologies", ["single_link"]), "topologies")
    policies = _entries(document.get("policies", ["fixed_link_0"]), "policies")
    sweep = document.get("sweep", {})
    if not isinstance(sweep, dict) or not all(isinstance(v, list) and v for v in sweep.values()):
        raise QualificationError("campaign sweep must map dotted keys to nonempty lists")
    sweep_keys = sorted(sweep)
    sweep_values = list(itertools.product(*(sweep[key] for key in sweep_keys))) if sweep else [()]
    expanded: list[dict[str, Any]] = []
    for topology, policy in itertools.product(topologies, policies):
        compatible = policy.get("topologies")
        if compatible is not None and topology["name"] not in compatible:
            continue
        compatible_policies = topology.get("policies")
        if compatible_policies is not None and policy["name"] not in compatible_policies:
            continue
        policy_seeds = policy.get("seeds", seeds)
        policy_runs = policy.get("runs", runs)
        if (
            not isinstance(policy_seeds, list)
            or not isinstance(policy_runs, list)
            or not all(isinstance(value, int) and not isinstance(value, bool) and value > 0
                       for value in [*policy_seeds, *policy_runs])
        ):
            raise QualificationError("policy seeds and runs must be positive integers")
        for seed, run_number, values in itertools.product(
            policy_seeds, policy_runs, sweep_values
        ):
            resolved = copy.deepcopy(base)
            resolved["topology"] = topology["name"]
            resolved["policy"] = policy["name"]
            for overlay in (topology.get("config", {}), policy.get("config", {})):
                if not isinstance(overlay, dict):
                    raise QualificationError("topology and policy config must be mappings")
                for key, value in overlay.items():
                    _set_dotted(resolved, key, value)
            for key, value in zip(sweep_keys, values):
                _set_dotted(resolved, key, value)
            expanded.append({"config": resolved, "seed": seed, "run": run_number})
    if not expanded:
        raise QualificationError("campaign matrix expands to no runs")
    return expanded


def _nested_leaf(value: Any, leaf: str) -> Any:
    if not isinstance(value, dict):
        return None
    if leaf in value:
        return value[leaf]
    matches = [
        match for child in value.values()
        if (match := _nested_leaf(child, leaf)) is not None
    ]
    if len(matches) > 1:
        raise QualificationError(f"duplicate campaign configuration leaf {leaf}")
    return matches[0] if matches else None


def _derive_run_id(
    config: dict[str, Any],
    seed: int,
    run_number: int,
    ns3_commit: str,
    project_commit: str,
    runtime_contract: dict[str, Any] | None = None,
) -> str:
    identity: dict[str, Any] = {
        "config": config,
        "seed": seed,
        "run": run_number,
        "ns3_commit": ns3_commit,
        "project_commit": project_commit,
    }
    prediction_enabled = _nested_leaf(config, "prediction_telemetry_enabled")
    if prediction_enabled is not None and not isinstance(prediction_enabled, bool):
        raise QualificationError("prediction_telemetry_enabled must be Boolean")
    if prediction_enabled:
        identity["prediction_schema_versions"] = PREDICTION_SCHEMA_VERSIONS
    if runtime_contract is not None:
        required = {
            "runtime_contract_id",
            "runtime_contract_sha256",
            "source_artifacts",
        }
        if set(runtime_contract) != required:
            raise QualificationError(
                "run identity runtime contract must contain exactly "
                "runtime_contract_id, runtime_contract_sha256, and source_artifacts"
            )
        identity[RUN_ID_RUNTIME_CONTRACT_KEY] = copy.deepcopy(runtime_contract)
    return _sha256_json(identity)[:20]


def _resolve_config_file(serialized: str, manifest_path: Path) -> Path:
    candidate = Path(serialized)
    candidates = [candidate]
    if not candidate.is_absolute():
        candidates.append(manifest_path.parent / candidate)
    candidates.append(REPOSITORY_ROOT / "experiments/configs" / candidate.name)
    existing: list[Path] = []
    for item in candidates:
        if item.is_file() and item.resolve() not in existing:
            existing.append(item.resolve())
    if len(existing) != 1:
        raise QualificationError(
            f"{manifest_path}: cannot resolve one campaign config for {serialized!r}"
        )
    return existing[0]


def _validate_manifest(
    aggregate_path: Path,
    aggregate: dict[str, Any],
    profile: QualificationProfile = V1_PROFILE,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest_path = aggregate_path.parent / "experiment_manifest.json"
    manifest = _read_json(manifest_path)
    required = {
        "schema_version",
        "experiment",
        "matrix_sha256",
        "config_file",
        "project_commit",
        "ns3_upstream_commit",
        "runtime_contract_id",
        "runtime_contract_sha256",
        "source_artifacts",
        "runs",
    }
    missing = sorted(required - manifest.keys())
    if missing:
        raise QualificationError(f"{manifest_path}: missing fields {', '.join(missing)}")
    if manifest["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise QualificationError(f"{manifest_path}: unsupported manifest schema")
    if (
        manifest["runtime_contract_id"] != profile.runtime_contract_id
        or manifest["runtime_contract_sha256"] != profile.runtime_contract_sha256
        or _canonical_json(manifest["source_artifacts"]) != _canonical_json(SOURCE_ARTIFACTS)
    ):
        raise QualificationError(f"{manifest_path}: runtime source closure mismatch")
    experiment = manifest["experiment"]
    matrix_sha = manifest["matrix_sha256"]
    project_commit = manifest["project_commit"]
    ns3_commit = manifest["ns3_upstream_commit"]
    config_file = manifest["config_file"]
    if not isinstance(experiment, str) or not experiment:
        raise QualificationError(f"{manifest_path}: invalid experiment identity")
    if not isinstance(matrix_sha, str) or re.fullmatch(r"[0-9a-f]{64}", matrix_sha) is None:
        raise QualificationError(f"{manifest_path}: invalid matrix SHA-256")
    if not isinstance(project_commit, str) or re.fullmatch(r"[0-9a-f]{40}", project_commit) is None:
        raise QualificationError(f"{manifest_path}: invalid project commit")
    if ns3_commit != NS3_UPSTREAM_COMMIT:
        raise QualificationError(f"{manifest_path}: unexpected ns-3 commit")
    if not isinstance(config_file, str) or not config_file:
        raise QualificationError(f"{manifest_path}: invalid config_file")
    local_config = _resolve_config_file(config_file, manifest_path)
    document = _load_yaml(local_config)
    if document.get("name", local_config.stem) != experiment:
        raise QualificationError(f"{manifest_path}: experiment/config identity mismatch")
    if _sha256_json(document) != matrix_sha:
        raise QualificationError(f"{manifest_path}: campaign matrix checksum mismatch")
    declared_runtime = document.get("runtime_contract")
    expected_runtime_declaration = {
        "id": profile.runtime_contract_id,
        "path": str(profile.runtime_contract_path.relative_to(REPOSITORY_ROOT)),
        "sha256": profile.runtime_contract_sha256,
        "source_artifacts": SOURCE_ARTIFACTS,
    }
    if _canonical_json(declared_runtime) != _canonical_json(expected_runtime_declaration):
        raise QualificationError(f"{manifest_path}: config runtime declaration mismatch")
    run_id_runtime_contract = {
        "runtime_contract_id": manifest["runtime_contract_id"],
        "runtime_contract_sha256": manifest["runtime_contract_sha256"],
        "source_artifacts": copy.deepcopy(manifest["source_artifacts"]),
    }
    declared: dict[str, dict[str, Any]] = {}
    for spec in _expand_config(document):
        run_id = _derive_run_id(
            spec["config"],
            spec["seed"],
            spec["run"],
            ns3_commit,
            project_commit,
            run_id_runtime_contract,
        )
        if run_id in declared:
            raise QualificationError(f"{local_config}: duplicate derived run ID {run_id}")
        declared[run_id] = spec
    manifest_runs = manifest["runs"]
    aggregate_runs = aggregate.get("runs")
    if not isinstance(manifest_runs, list) or not isinstance(aggregate_runs, list):
        raise QualificationError(f"{manifest_path}: manifest and aggregate require run lists")
    manifest_by_id: dict[str, dict[str, Any]] = {}
    for item in manifest_runs:
        if not isinstance(item, dict):
            raise QualificationError(f"{manifest_path}: run entry must be an object")
        run_id = item.get("run_id")
        if not isinstance(run_id, str) or not run_id or run_id in manifest_by_id:
            raise QualificationError(f"{manifest_path}: duplicate or invalid manifest run ID")
        if item.get("status") != "complete" or item.get("directory") != run_id:
            raise QualificationError(f"{manifest_path}: run {run_id} is not canonical/complete")
        manifest_by_id[run_id] = item
    aggregate_by_id: dict[str, dict[str, Any]] = {}
    for item in aggregate_runs:
        if not isinstance(item, dict):
            raise QualificationError(f"{aggregate_path}: run entry must be an object")
        run_id = item.get("run_id")
        if not isinstance(run_id, str) or not run_id or run_id in aggregate_by_id:
            raise QualificationError(f"{aggregate_path}: duplicate or invalid aggregate run ID")
        aggregate_by_id[run_id] = item
    if set(manifest_by_id) != set(aggregate_by_id) or set(manifest_by_id) != set(declared):
        raise QualificationError(f"{manifest_path}: manifest, aggregate, and matrix differ")
    for run_id, item in manifest_by_id.items():
        spec = declared[run_id]
        aggregate_run = aggregate_by_id[run_id]
        if (
            item.get("seed") != spec["seed"]
            or item.get("run") != spec["run"]
            or _canonical_json(item.get("config")) != _canonical_json(spec["config"])
            or aggregate_run.get("seed") != spec["seed"]
            or aggregate_run.get("run") != spec["run"]
            or aggregate_run.get("topology") != spec["config"].get("topology")
            or aggregate_run.get("policy") != spec["config"].get("policy")
        ):
            raise QualificationError(f"{manifest_path}: run {run_id} identity mismatch")
    identity = {
        "path": str(manifest_path.resolve()),
        "sha256": _sha256_file(manifest_path),
        "schema_version": manifest["schema_version"],
        "experiment": experiment,
        "matrix_sha256": matrix_sha,
        "config_file": str(local_config),
        "config_file_sha256": _sha256_file(local_config),
        "project_commit": project_commit,
        "ns3_upstream_commit": ns3_commit,
        "runtime_contract_id": profile.runtime_contract_id,
        "runtime_contract_sha256": profile.runtime_contract_sha256,
        "source_artifacts": copy.deepcopy(SOURCE_ARTIFACTS),
        "completed_run_count": len(manifest_by_id),
        "expanded_matrix_identity_verified": True,
        "derived_run_ids_verified": True,
    }
    return identity, aggregate_by_id


def _resolve_aggregate(path: Path) -> Path:
    path = path.resolve()
    candidates = [path] if path.is_file() else [
        path / "aggregate.json",
        path / "runs" / "aggregate.json",
    ]
    matches = [candidate for candidate in candidates if candidate.is_file()]
    if len(matches) != 1:
        raise QualificationError(
            f"expected exactly one aggregate for {path}; tried "
            + ", ".join(str(candidate) for candidate in candidates)
        )
    return matches[0].resolve()


def _run_directory(run: dict[str, Any], aggregate_path: Path) -> Path:
    run_id = run.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise QualificationError(f"{aggregate_path}: run has no run_id")
    candidates = [aggregate_path.parent / run_id]
    serialized = run.get("run_dir")
    if isinstance(serialized, str) and serialized:
        candidates.extend([Path(serialized), aggregate_path.parent / serialized])
    existing: list[Path] = []
    for candidate in candidates:
        if candidate.is_dir() and candidate.resolve() not in existing:
            existing.append(candidate.resolve())
    matching = [candidate for candidate in existing if candidate.name == run_id]
    if len(matching) != 1:
        raise QualificationError(f"cannot resolve one canonical run directory for {run_id}")
    return matching[0]


def _environment(config: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in ENVIRONMENT_KEYS if key not in config]
    if missing:
        raise QualificationError(f"resolved config omits environment fields {missing}")
    wifi = config.get("wifi")
    if not isinstance(wifi, dict):
        raise QualificationError("resolved config omits Wi-Fi settings")
    return {
        **{key: config[key] for key in ENVIRONMENT_KEYS},
        "shared_target_wifi": {
            key: wifi.get(key, "__MISSING__") for key in SHARED_WIFI_KEYS
        },
    }


def _without_geometry(environment: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(environment)
    obss = result.get("background", {}).get("obss")
    if isinstance(obss, dict):
        obss.pop("bsses", None)
    return result


def _nominal_config(config: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(config)
    for key in ("run_id", "seed", "run"):
        result.pop(key, None)
    obss = result.get("background", {}).get("obss")
    if isinstance(obss, dict):
        obss.pop("bsses", None)
    return result


def _common_input_config(
    config: dict[str, Any], topology_wifi: dict[str, Any]
) -> dict[str, Any]:
    """Project inputs that must be identical across the paired arms."""
    result = copy.deepcopy(config)
    for key in (
        "run_id",
        "seed",
        "run",
        "topology",
        "policy",
        "policy_settings",
        "pairedValueDuplicationT2",
        "predictionTelemetry",
        "secondaryAirtimeMeter",
        # Policy provenance only. The actual environment is reconstructed and
        # hash-checked independently for both arms below.
        "environment",
    ):
        result.pop(key, None)
    wifi = result.get("wifi")
    if not isinstance(wifi, dict):
        raise QualificationError("resolved config omits Wi-Fi settings")
    topology_fields = {
        field
        for topology in ARM_IDENTITIES.values()
        for field in topology_wifi[topology[0]]
    }
    for field in topology_fields:
        wifi.pop(field, None)
    return result


def _build_identity(run_dir: Path) -> dict[str, str]:
    build = _read_json(run_dir / "build_info.json")
    identity: dict[str, str] = {}
    for field in BUILD_IDENTITY_FIELDS:
        value = build.get(field)
        if not isinstance(value, str) or not value:
            raise QualificationError(f"{run_dir}/build_info.json: invalid {field}")
        identity[field] = value
    return identity


def _validate_required_artifacts(run_dir: Path, arm: str) -> None:
    required = set(REQUIRED_RUN_ARTIFACTS)
    if arm == "policy":
        required |= POLICY_RUN_ARTIFACTS
    missing = sorted(name for name in required if not (run_dir / name).is_file())
    if missing:
        raise QualificationError(f"{run_dir}: missing raw artifacts {', '.join(missing)}")


def _validate_policy_source_identity(
    run_dir: Path,
    config: dict[str, Any],
    profile: QualificationProfile = V1_PROFILE,
) -> None:
    runtime = config.get("pairedValueDuplicationT2")
    if not isinstance(runtime, dict):
        raise QualificationError(f"{run_dir}: missing pairedValueDuplicationT2 config")
    if (
        runtime.get("runtime_contract_id") != profile.runtime_contract_id
        or runtime.get("runtime_contract_sha256") != profile.runtime_contract_sha256
    ):
        raise QualificationError(f"{run_dir}: resolved runtime contract mismatch")
    summary = _read_json(run_dir / "paired_value_t2_summary.json")
    if (
        summary.get("run_id") != config.get("run_id")
        or summary.get("policy") != POLICY_NAME
        or summary.get("runtime_contract_id") != profile.runtime_contract_id
        or summary.get("runtime_contract_sha256") != profile.runtime_contract_sha256
        or _canonical_json(summary.get("source_artifacts")) != _canonical_json(SOURCE_ARTIFACTS)
    ):
        raise QualificationError(f"{run_dir}: controller source/runtime identity mismatch")


def _frame_metrics(run_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    rows = _read_csv(
        run_dir / "frames.csv",
        {
            "run_id",
            "frame_id",
            "generation_time_us",
            "deadline_us",
            "union_latency_us",
            "deadline_miss",
            "incomplete",
        },
    )
    if not rows:
        raise QualificationError(f"{run_dir}/frames.csv: no generated frames")
    start_us = _finite(
        config.get("measurement_start_s"), f"{run_dir}: measurement_start_s"
    ) * 1_000_000.0
    stop_us = _finite(
        config.get("measurement_stop_s"), f"{run_dir}: measurement_stop_s"
    ) * 1_000_000.0
    if stop_us <= start_us:
        raise QualificationError(f"{run_dir}: invalid measurement window")
    frame_ids: set[int] = set()
    misses = 0
    completed: list[float] = []
    for row in rows:
        if row.get("run_id") != config.get("run_id"):
            raise QualificationError(f"{run_dir}/frames.csv: run_id mismatch")
        frame_id = _integer(row.get("frame_id"), f"{run_dir}: frame_id")
        if frame_id in frame_ids:
            raise QualificationError(f"{run_dir}/frames.csv: duplicate frame_id")
        frame_ids.add(frame_id)
        generation = _finite(
            row.get("generation_time_us"), f"{run_dir}: generation_time_us", nonnegative=True
        )
        if not start_us <= generation < stop_us:
            raise QualificationError(f"{run_dir}: generated frame outside measurement window")
        deadline = _finite(
            row.get("deadline_us"), f"{run_dir}: deadline_us", nonnegative=True
        )
        if deadline <= 0:
            raise QualificationError(f"{run_dir}: frame deadline must be positive")
        incomplete = _flag(row.get("incomplete"), f"{run_dir}: incomplete")
        if incomplete:
            if row.get("union_latency_us", "") != "":
                raise QualificationError(f"{run_dir}: incomplete frame has union latency")
            computed_miss = True
        else:
            latency = _finite(
                row.get("union_latency_us"),
                f"{run_dir}: union_latency_us",
                nonnegative=True,
            )
            completed.append(latency)
            computed_miss = latency > deadline
        if _flag(row.get("deadline_miss"), f"{run_dir}: deadline_miss") != computed_miss:
            raise QualificationError(f"{run_dir}: deadline-miss flag disagrees with raw outcome")
        misses += computed_miss
    if len(completed) < MINIMUM_COMPLETED_FRAMES:
        raise QualificationError(
            f"{run_dir}: only {len(completed)} completed frames; "
            f"minimum is {MINIMUM_COMPLETED_FRAMES}"
        )
    return {
        "generated_frame_count": len(rows),
        "completed_frame_count": len(completed),
        "deadline_miss_count": misses,
        "all_generated_deadline_miss_rate": misses / len(rows),
        "completed_frame_p99_us": _type7_quantile(completed, 0.99),
    }


def _sender_airtime_us(run_dir: Path) -> float:
    rows = _read_csv(run_dir / "link_intervals.csv", {"link_id", "phy_tx_time_us"})
    seen: set[int] = set()
    total = 0.0
    for row in rows:
        link = _integer(row.get("link_id"), f"{run_dir}: link_id")
        if link in seen:
            raise QualificationError(f"{run_dir}: duplicate link airtime row")
        seen.add(link)
        total += _finite(
            row.get("phy_tx_time_us"), f"{run_dir}: phy_tx_time_us", nonnegative=True
        )
    if seen != {0, 1}:
        raise QualificationError(f"{run_dir}: expected exactly sender links 0 and 1")
    return total


def _background_metrics(run_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    rows = _read_csv(run_dir / "background_flows.csv", {"bytes_received"})
    if not rows:
        raise QualificationError(f"{run_dir}/background_flows.csv: no background flows")
    total_bytes = sum(
        _integer(row.get("bytes_received"), f"{run_dir}: background bytes")
        for row in rows
    )
    duration = _finite(
        config.get("measurement_stop_s"), f"{run_dir}: measurement_stop_s"
    ) - _finite(config.get("measurement_start_s"), f"{run_dir}: measurement_start_s")
    if duration <= 0:
        raise QualificationError(f"{run_dir}: invalid measurement duration")
    return {
        "background_bytes_received": total_bytes,
        "background_throughput_mbps": total_bytes * 8.0 / duration / 1_000_000.0,
    }


def _observation(
    run: dict[str, Any],
    run_dir: Path,
    arm: str,
    manifest: dict[str, Any],
    neutral_environment: dict[str, Any],
    topology_wifi: dict[str, Any],
    profile: QualificationProfile = V1_PROFILE,
) -> dict[str, Any]:
    _validate_required_artifacts(run_dir, arm)
    config = _read_json(run_dir / "resolved_config.json")
    if _canonical_json(run.get("config")) != _canonical_json(config):
        raise QualificationError(f"{run_dir}: aggregate/resolved config mismatch")
    if (
        config.get("run_id") != run.get("run_id")
        or config.get("seed") != run.get("seed")
        or config.get("run") != run.get("run")
        or (config.get("topology"), config.get("policy")) != ARM_IDENTITIES[arm]
    ):
        raise QualificationError(f"{run_dir}: resolved run identity mismatch")
    try:
        validation = validate_run(
            run_dir,
            expected_run_id=run["run_id"],
            expected_project_commit=manifest["project_commit"],
            expected_ns3_commit=manifest["ns3_upstream_commit"],
        )
    except ValidationError as error:
        raise QualificationError(f"{run_dir}: strict run validation failed: {error}") from error
    if validation.get("valid") is not True or validation.get("run_id") != run["run_id"]:
        raise QualificationError(f"{run_dir}: strict validator returned an invalid identity")
    environment = _environment(config)
    if _canonical_json(_without_geometry(environment)) != _canonical_json(neutral_environment):
        raise QualificationError(f"{run_dir}: environment differs from frozen neutral closure")
    expected_topology = topology_wifi.get(config["topology"])
    wifi = config.get("wifi")
    if not isinstance(expected_topology, dict) or not isinstance(wifi, dict):
        raise QualificationError(f"{run_dir}: missing topology Wi-Fi closure")
    observed_topology = {
        key: wifi.get(key, "__MISSING__") for key in expected_topology
    }
    if _canonical_json(observed_topology) != _canonical_json(expected_topology):
        raise QualificationError(f"{run_dir}: topology-specific Wi-Fi closure mismatch")
    if arm == "policy":
        _validate_policy_source_identity(run_dir, config, profile)
    else:
        forbidden = POLICY_RUN_ARTIFACTS & {path.name for path in run_dir.iterdir()}
        if forbidden:
            raise QualificationError(
                f"{run_dir}: policy artifacts exist in STR run: {sorted(forbidden)}"
            )
    pair = (
        _integer(run.get("seed"), f"{run_dir}: seed", positive=True),
        _integer(run.get("run"), f"{run_dir}: run", positive=True),
    )
    return {
        "run_id": run["run_id"],
        "run_dir": str(run_dir),
        "pair": pair,
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


def _type7_quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise QualificationError("cannot compute a quantile of an empty sample")
    if not 0 <= probability <= 1:
        raise QualificationError("quantile probability is outside [0, 1]")
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return float(ordered[lower])
    weight = index - lower
    return float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)


def _bootstrap_index_matrix() -> tuple[tuple[int, ...], ...]:
    generator = random.Random(BOOTSTRAP_SEED)
    return tuple(
        tuple(generator.randrange(EXPECTED_PAIR_COUNT) for _ in range(EXPECTED_PAIR_COUNT))
        for _ in range(BOOTSTRAP_REPLICATIONS)
    )


def _index_matrix_sha256(matrix: Sequence[Sequence[int]]) -> str:
    flattened = bytes(index for row in matrix for index in row)
    return _sha256_bytes(flattened)


def _mean_delta(left: Sequence[float], right: Sequence[float]) -> float:
    return statistics.mean(
        left_value - right_value for left_value, right_value in zip(left, right)
    )


def _ratio_of_means(left: Sequence[float], right: Sequence[float]) -> float:
    denominator = statistics.mean(right)
    if denominator <= 0:
        raise QualificationError("bootstrap resource denominator is nonpositive")
    return statistics.mean(left) / denominator


def _background_loss(left: Sequence[float], right: Sequence[float]) -> float:
    return 1.0 - _ratio_of_means(left, right)


def _paired_bootstrap(
    policy: Sequence[float],
    baseline: Sequence[float],
    indexes: Sequence[Sequence[int]],
    statistic: Callable[[Sequence[float], Sequence[float]], float],
    statistic_description: str,
) -> dict[str, Any]:
    if len(policy) != EXPECTED_PAIR_COUNT or len(baseline) != EXPECTED_PAIR_COUNT:
        raise QualificationError("paired bootstrap requires the exact 48-unit samples")
    if not all(math.isfinite(value) for value in [*policy, *baseline]):
        raise QualificationError("paired bootstrap input contains a non-finite value")
    point = statistic(policy, baseline)
    samples: list[float] = []
    for row in indexes:
        value = statistic(
            [policy[index] for index in row],
            [baseline[index] for index in row],
        )
        if not math.isfinite(value):
            raise QualificationError("paired bootstrap replicate is non-finite")
        samples.append(value)
    return {
        "method": "deterministic paired whole-run percentile bootstrap",
        "statistic": statistic_description,
        "confidence_level": CONFIDENCE_LEVEL,
        "paired_unit_count": EXPECTED_PAIR_COUNT,
        "replications": BOOTSTRAP_REPLICATIONS,
        "seed": BOOTSTRAP_SEED,
        "estimate": point,
        "ci95_low": _type7_quantile(samples, 0.025),
        "ci95_high": _type7_quantile(samples, 0.975),
    }


def _criterion(passed: bool, rule: str, observed: float, threshold: float) -> dict[str, Any]:
    return {
        "status": "pass" if passed else "fail",
        "rule": rule,
        "observed": observed,
        "threshold": threshold,
    }


def _composite(criteria: Iterable[dict[str, Any]]) -> str:
    return "pass" if all(item["status"] == "pass" for item in criteria) else "fail"


def _arm_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "run_count": len(rows),
        "generated_frame_count": {
            "total": sum(row["generated_frame_count"] for row in rows),
            "per_run_min": min(row["generated_frame_count"] for row in rows),
            "per_run_max": max(row["generated_frame_count"] for row in rows),
        },
        "completed_frame_count": {
            "total": sum(row["completed_frame_count"] for row in rows),
            "per_run_min": min(row["completed_frame_count"] for row in rows),
            "per_run_max": max(row["completed_frame_count"] for row in rows),
        },
        "all_generated_deadline_miss_rate": {
            "campaign_estimator": "mean of per-run rates",
            "mean": statistics.mean(row["all_generated_deadline_miss_rate"] for row in rows),
            "total_misses": sum(row["deadline_miss_count"] for row in rows),
            "total_generated_frames": sum(row["generated_frame_count"] for row in rows),
        },
        "completed_frame_p99_us": {
            "per_run_quantile": "Hyndman-Fan type 7 at probability 0.99",
            "campaign_estimator": "mean of per-run P99 values",
            "mean": statistics.mean(row["completed_frame_p99_us"] for row in rows),
            "minimum_completed_frames_per_run": MINIMUM_COMPLETED_FRAMES,
        },
        "sender_phy_tx_airtime_us": {
            "definition": "sum of link-0 and link-1 sender PHY TX intervals",
            "mean": statistics.mean(row["sender_airtime_us"] for row in rows),
        },
        "background_throughput_mbps": {
            "definition": "raw received background-flow bytes over measurement duration",
            "mean": statistics.mean(row["background_throughput_mbps"] for row in rows),
        },
    }


def analyze_campaign(
    inputs: Path | Iterable[Path],
    profile: QualificationProfile = V1_PROFILE,
) -> dict[str, Any]:
    """Validate raw evidence and return one frozen 48-pair STR qualification."""
    input_paths = [inputs] if isinstance(inputs, Path) else list(inputs)
    if not input_paths:
        raise QualificationError("at least one campaign input is required")
    _, neutral_environment, topology_wifi = _verify_source_closure(profile)
    aggregate_paths = [_resolve_aggregate(path) for path in input_paths]
    if len(set(aggregate_paths)) != len(aggregate_paths):
        raise QualificationError("duplicate aggregate input")
    indexes: dict[str, dict[tuple[int, int], dict[str, Any]]] = {
        arm: {} for arm in ARM_IDENTITIES
    }
    manifest_identities: list[dict[str, Any]] = []
    run_count = 0
    for aggregate_path in aggregate_paths:
        aggregate = _read_json(aggregate_path)
        manifest, runs_by_id = _validate_manifest(aggregate_path, aggregate, profile)
        manifest_identities.append(manifest)
        for run in runs_by_id.values():
            run_count += 1
            identity = (run.get("topology"), run.get("policy"))
            matches = [arm for arm, expected in ARM_IDENTITIES.items() if identity == expected]
            if len(matches) != 1:
                raise QualificationError(
                    f"{aggregate_path}: undeclared headline arm {identity!r}"
                )
            arm = matches[0]
            run_dir = _run_directory(run, aggregate_path)
            observation = _observation(
                run,
                run_dir,
                arm,
                manifest,
                neutral_environment,
                topology_wifi,
                profile,
            )
            pair = observation["pair"]
            if pair in indexes[arm]:
                raise QualificationError(f"duplicate {arm} run for seed/run {pair}")
            indexes[arm][pair] = observation
    expected_pairs = set(profile.expected_seed_run_units)
    if run_count != EXPECTED_RUN_COUNT:
        raise QualificationError(
            f"campaign has {run_count} runs; expected exactly {EXPECTED_RUN_COUNT}"
        )
    for arm, index in indexes.items():
        if set(index) != expected_pairs:
            raise QualificationError(
                f"{arm} seed/run set differs from declaration: "
                f"missing {sorted(expected_pairs - set(index))}, "
                f"extra {sorted(set(index) - expected_pairs)}"
            )
    pairs = list(profile.expected_seed_run_units)
    for pair in pairs:
        environments = {
            _canonical_json(indexes[arm][pair]["environment"])
            for arm in ARM_IDENTITIES
        }
        if len(environments) != 1:
            raise QualificationError(f"seed/run {pair} has unmatched environment realization")
        common_inputs = {
            _canonical_json(indexes[arm][pair]["common_input_config"])
            for arm in ARM_IDENTITIES
        }
        if len(common_inputs) != 1:
            raise QualificationError(f"seed/run {pair} has unmatched common inputs")
    all_rows = [row for index in indexes.values() for row in index.values()]
    build_identities = {_canonical_json(row["build_identity"]) for row in all_rows}
    if len(build_identities) != 1:
        raise QualificationError("campaign mixes build identities")
    build_identity = json.loads(next(iter(build_identities)))
    for manifest in manifest_identities:
        if (
            manifest["project_commit"] != build_identity["project_git_commit"]
            or manifest["ns3_upstream_commit"] != build_identity["ns3_upstream_commit"]
        ):
            raise QualificationError(
                f"{manifest['path']}: manifest commit identity differs from run build"
            )
    nominal_hashes: dict[str, str] = {}
    for arm, index in indexes.items():
        hashes = {_sha256_json(row["nominal_config"]) for row in index.values()}
        if len(hashes) != 1:
            raise QualificationError(f"{arm} nominal resolved configuration changed")
        nominal_hashes[arm] = next(iter(hashes))
    ordered = {
        arm: [indexes[arm][pair] for pair in pairs] for arm in ARM_IDENTITIES
    }
    policy_miss = [row["all_generated_deadline_miss_rate"] for row in ordered["policy"]]
    str_miss = [row["all_generated_deadline_miss_rate"] for row in ordered["str_mlo"]]
    policy_p99 = [row["completed_frame_p99_us"] for row in ordered["policy"]]
    str_p99 = [row["completed_frame_p99_us"] for row in ordered["str_mlo"]]
    policy_airtime = [row["sender_airtime_us"] for row in ordered["policy"]]
    str_airtime = [row["sender_airtime_us"] for row in ordered["str_mlo"]]
    policy_background = [
        row["background_throughput_mbps"] for row in ordered["policy"]
    ]
    str_background = [
        row["background_throughput_mbps"] for row in ordered["str_mlo"]
    ]
    policy_background_bytes_by_run = [
        row["background_bytes_received"] for row in ordered["policy"]
    ]
    str_background_bytes_by_run = [
        row["background_bytes_received"] for row in ordered["str_mlo"]
    ]
    # Per-run positivity makes every possible paired-bootstrap resample defined,
    # including a replicate that repeatedly selects one baseline unit.
    if any(value <= 0 for value in str_airtime):
        raise QualificationError(
            "STR sender-airtime denominator contains a nonpositive run"
        )
    if any(value <= 0 for value in str_background_bytes_by_run):
        raise QualificationError(
            "STR background byte denominator contains a nonpositive run"
        )
    policy_background_bytes = sum(policy_background_bytes_by_run)
    str_background_bytes = sum(str_background_bytes_by_run)

    # All evidence prerequisites, including resource denominators, must pass
    # before the deterministic bootstrap matrix or any confidence interval is
    # constructed.
    bootstrap_indexes = _bootstrap_index_matrix()
    miss_delta = _paired_bootstrap(
        policy_miss,
        str_miss,
        bootstrap_indexes,
        _mean_delta,
        "mean per-run policy-minus-STR miss-rate difference",
    )
    p99_delta = _paired_bootstrap(
        policy_p99,
        str_p99,
        bootstrap_indexes,
        _mean_delta,
        "mean per-run policy-minus-STR completed-P99 difference",
    )
    airtime_ratio_bootstrap = _paired_bootstrap(
        policy_airtime,
        str_airtime,
        bootstrap_indexes,
        _ratio_of_means,
        "ratio of resampled policy and STR sender-airtime means",
    )
    background_loss_bootstrap = _paired_bootstrap(
        policy_background,
        str_background,
        bootstrap_indexes,
        _background_loss,
        "one minus ratio of resampled policy and STR background-throughput means",
    )
    airtime_ratio = airtime_ratio_bootstrap["estimate"]
    background_loss = background_loss_bootstrap["estimate"]
    background_gate_passed = (
        100 * policy_background_bytes >= 99 * str_background_bytes
    )
    performance_criteria = {
        "all_generated_deadline_miss_delta": _criterion(
            miss_delta["ci95_high"] < 0,
            "upper 95% paired-bootstrap endpoint of policy-minus-STR miss rate < 0",
            miss_delta["ci95_high"],
            0.0,
        ),
        "mean_per_run_completed_p99_delta": _criterion(
            p99_delta["ci95_high"] < 0,
            "upper 95% paired-bootstrap endpoint of policy-minus-STR P99 < 0",
            p99_delta["ci95_high"],
            0.0,
        ),
    }
    resource_criteria = {
        "sender_airtime_ratio": _criterion(
            airtime_ratio < MAXIMUM_AIRTIME_RATIO,
            "ratio of matched-run mean summed sender PHY airtime < 1.20",
            airtime_ratio,
            MAXIMUM_AIRTIME_RATIO,
        ),
        "background_throughput_loss": _criterion(
            background_gate_passed,
            "matched-run mean background-throughput loss <= 0.01",
            background_loss,
            MAXIMUM_BACKGROUND_LOSS,
        ),
    }
    performance_status = _composite(performance_criteria.values())
    resource_status = _composite(resource_criteria.values())
    return {
        "schema_version": 1,
        "analysis": profile.analysis_id,
        **(
            {"qualification_profile": profile.key}
            if profile != V1_PROFILE
            else {}
        ),
        "evidence_role": "engineering_qualification",
        "confirmation_eligibility": {
            "eligible": False,
            "reason": (
                "two-arm engineering qualification; final confirmation requires a "
                "separate frozen three-arm analyzer"
            ),
            "reserved_final_confirmation_seeds": list(RESERVED_FINAL_CONFIRMATION_SEEDS),
            "reserved_units_used": False,
        },
        "independent_sample_unit": ["seed", "run"],
        "paired_unit_count": EXPECTED_PAIR_COUNT,
        "paired_units": [
            {"seed": seed, "run": run_number} for seed, run_number in pairs
        ],
        "source_closure": {
            "runtime_contract": {
                "path": str(profile.runtime_contract_path),
                "runtime_contract_id": profile.runtime_contract_id,
                "sha256": profile.runtime_contract_sha256,
            },
            "neutral_environment_source": {
                "path": str(NEUTRAL_SOURCE_PATH),
                "sha256": NEUTRAL_SOURCE_SHA256,
                "neutral_environment_sha256": NEUTRAL_ENVIRONMENT_SHA256,
                "topology_wifi_sha256": TOPOLOGY_WIFI_SHA256,
            },
            "model_source_artifacts": copy.deepcopy(SOURCE_ARTIFACTS),
        },
        "campaign_checks": {
            "exact_two_declared_arms": True,
            "exact_48_paired_units": True,
            "all_required_raw_artifacts_present": True,
            "all_runs_strictly_validated": True,
            "all_metrics_reconstructed_from_raw_artifacts": True,
            "paired_environment_realizations_match": True,
            "paired_common_inputs_match": True,
            "neutral_environment_closure_verified": True,
            "topology_wifi_closure_verified": True,
            "single_build_identity": True,
            "build_identity": build_identity,
            "complete_manifest_identities_verified": True,
            "manifests": manifest_identities,
            "nominal_resolved_config_sha256": nominal_hashes,
        },
        "bootstrap": {
            "method": "one shared deterministic 10000x48 matched-unit index matrix",
            "seed": BOOTSTRAP_SEED,
            "replications": BOOTSTRAP_REPLICATIONS,
            "draws_per_replication": EXPECTED_PAIR_COUNT,
            "unit_order": "ascending seed then ascending run",
            "index_matrix_sha256": _index_matrix_sha256(bootstrap_indexes),
            "endpoint_quantile": "Hyndman-Fan type 7",
            "reused_for": [
                "all_generated_deadline_miss_delta",
                "mean_per_run_completed_p99_delta",
                "sender_phy_tx_airtime_ratio",
                "background_throughput_loss",
            ],
        },
        "treatments": {
            arm: {
                "label": profile.policy_label if arm == "policy" else ARM_LABELS[arm],
                **_arm_summary(rows),
            }
            for arm, rows in ordered.items()
        },
        "comparison_against_str": {
            "all_generated_deadline_miss_rate": {
                "miss_definition": (
                    "incomplete frame or finite union latency strictly greater than deadline"
                ),
                "paired_policy_minus_str": miss_delta,
            },
            "completed_frame_p99_us": {
                "population": (
                    "all frames with finite union latency, including late completions"
                ),
                "per_run_quantile": "Hyndman-Fan type 7 at probability 0.99",
                "campaign_estimator": "mean of 48 per-run P99 values",
                "paired_policy_minus_str": p99_delta,
            },
            "sender_phy_tx_airtime_ratio": {
                "statistic": "policy matched-run mean / STR matched-run mean",
                "estimate": airtime_ratio,
                "paired_bootstrap": airtime_ratio_bootstrap,
            },
            "background_throughput_loss": {
                "statistic": "1 - policy matched-run mean / STR matched-run mean",
                "estimate": background_loss,
                "exact_gate_arithmetic": {
                    "policy_background_bytes_received": policy_background_bytes,
                    "str_background_bytes_received": str_background_bytes,
                    "rule": "100 * policy bytes >= 99 * STR bytes",
                    "passed": background_gate_passed,
                },
                "paired_bootstrap": background_loss_bootstrap,
            },
        },
        "performance_victory_against_str": {
            "status": performance_status,
            "criteria": performance_criteria,
        },
        "resource_target_against_str": {
            "status": resource_status,
            "criteria": resource_criteria,
        },
        "overall": {
            "status": "pass"
            if performance_status == "pass" and resource_status == "pass"
            else "fail",
            "members": [
                "performance_victory_against_str",
                "resource_target_against_str",
            ],
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    """Render a compact qualification summary after all prerequisites pass."""
    comparison = report["comparison_against_str"]
    miss = comparison["all_generated_deadline_miss_rate"]["paired_policy_minus_str"]
    p99 = comparison["completed_frame_p99_us"]["paired_policy_minus_str"]
    airtime = comparison["sender_phy_tx_airtime_ratio"]
    airtime_bootstrap = airtime["paired_bootstrap"]
    background = comparison["background_throughput_loss"]
    background_bootstrap = background["paired_bootstrap"]
    policy = report["treatments"]["policy"]
    baseline = report["treatments"]["str_mlo"]
    profile_key = report.get("qualification_profile", V1_PROFILE.key)
    profile = PROFILES.get(profile_key)
    if profile is None:
        raise QualificationError(f"unknown report qualification profile {profile_key!r}")
    lines = [
        f"# {profile.markdown_title}",
        "",
        "Evidence role: engineering qualification only; this is not final confirmation.",
        "The reserved final-confirmation seeds were not used.",
        "",
        f"Validated matched seed/run units: {report['paired_unit_count']}.",
        "Every headline value below was reconstructed from raw per-run artifacts.",
        "",
        f"| Metric | {profile.policy_label} | STR MLO | Policy-minus-STR 95% interval |",
        "| --- | ---: | ---: | ---: |",
        (
            "| All-generated deadline-miss rate | "
            f"{100 * policy['all_generated_deadline_miss_rate']['mean']:.4f}% | "
            f"{100 * baseline['all_generated_deadline_miss_rate']['mean']:.4f}% | "
            f"[{100 * miss['ci95_low']:.4f}%, {100 * miss['ci95_high']:.4f}%] |"
        ),
        (
            "| Mean per-run completed-frame HF7 P99 | "
            f"{policy['completed_frame_p99_us']['mean']:.3f} us | "
            f"{baseline['completed_frame_p99_us']['mean']:.3f} us | "
            f"[{p99['ci95_low']:.3f}, {p99['ci95_high']:.3f}] us |"
        ),
        "",
        "Late completions remain in the completed-frame P99 population; misses and "
        "incomplete frames remain in the all-generated denominator.",
        "",
        f"Sender-airtime ratio: {airtime['estimate']:.6f} "
        f"(95% paired-bootstrap interval "
        f"[{airtime_bootstrap['ci95_low']:.6f}, "
        f"{airtime_bootstrap['ci95_high']:.6f}]; "
        f"strict target < {MAXIMUM_AIRTIME_RATIO:.2f}).",
        f"Background-throughput loss: "
        f"{100 * background['estimate']:.4f}% "
        f"(95% paired-bootstrap interval "
        f"[{100 * background_bootstrap['ci95_low']:.4f}%, "
        f"{100 * background_bootstrap['ci95_high']:.4f}%]; "
        f"target <= {100 * MAXIMUM_BACKGROUND_LOSS:.2f}%).",
        "",
        f"Performance victory: **{report['performance_victory_against_str']['status']}**.  ",
        f"Resource target: **{report['resource_target_against_str']['status']}**.  ",
        f"Overall: **{report['overall']['status']}**.",
        "",
    ]
    return "\n".join(lines)


def _write_text(path: Path, content: str) -> None:
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as error:
        raise QualificationError(f"cannot write {path}: {error}") from error


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument(
        "--profile",
        choices=tuple(PROFILES),
        default=V1_PROFILE.key,
        help="frozen runtime and engineering-seed profile to validate",
    )
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="write the validated report, then exit 1 unless the overall target passes",
    )
    args = parser.parse_args(argv)
    try:
        report = analyze_campaign(args.inputs, PROFILES[args.profile])
        serialized = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
        markdown = render_markdown(report)
        if args.json_output is not None:
            _write_text(args.json_output, serialized)
        if args.markdown_output is not None:
            _write_text(args.markdown_output, markdown)
    except (QualificationError, ValidationError, OSError, ValueError) as error:
        print(f"validation error: {error}", file=sys.stderr)
        return 2
    print(serialized if args.format == "json" else markdown, end="")
    if args.require_pass and report["overall"]["status"] != "pass":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
