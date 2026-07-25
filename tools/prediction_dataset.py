"""Shared contracts for Increment-2 prediction datasets."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

DATASET_SCHEMA_VERSION = 1
ATTACHED_COLUMNS = [
    "dataset_schema_version",
    "frame_complete",
    "frame_completion_time_ns",
    "frame_latency_us",
    "deadline_miss",
    "run_seed",
    "run_number",
    "scenario_name",
    "background_profile",
    "correlation_mode",
    "selected_policy",
    "run_group_id",
    "miss_regime",
    "split_role",
]
DERIVED_COLUMNS = [
    "last_positive_ack_age_us",
    "last_attempt_age_us",
    "queue_oldest_age_us",
    "frame_packets_not_acknowledged",
]
SPLIT_ROLES = {
    "training",
    "validation_selection",
    "validation_calibration",
    "in_distribution_test",
    "out_of_distribution_test",
}
REQUIRED_ANALYSIS_KEYS = {
    "analysis_schema_version",
    "analysis_seed",
    "split_seed",
    "bootstrap_seed",
    "minimum_run_groups_validation_selection",
    "minimum_run_groups_validation_calibration",
    "minimum_run_groups_id_test",
    "minimum_run_groups_per_required_ood_scenario",
    "pr_auc_metric",
    "calibration_bin_count",
    "calibration_method",
    "confidence_level",
    "bootstrap_replicates",
    "minimum_rescue_time_us",
    "required_ood_scenarios",
    "ood_formal_aggregation",
    "pooled_ood_decision_use",
    "screening_budget",
    "minimum_f2_recall",
    "minimum_random_multiple",
    "minimum_heuristic_gain",
    "minimum_f2_incremental_gain",
    "minimum_ood_retention",
    "minimum_later_stage_gain",
    "maximum_fixed_threshold_action_rate_overshoot",
}

OUTCOME_COLUMNS = {
    "frame_complete",
    "frame_completion_time_ns",
    "frame_latency_us",
    "deadline_miss",
}
CONTEXT_COLUMNS = {
    "dataset_schema_version",
    "run_id",
    "frame_id",
    "path_id",
    "copy_id",
    "sample_stage",
    "sample_offset_us",
    "sample_time_ns",
    "latest_feature_event_time_ns",
    "latest_feature_event_sequence",
    "generation_time_ns",
    "deadline_time_ns",
    "run_seed",
    "run_number",
    "scenario_name",
    "background_profile",
    "correlation_mode",
    "selected_policy",
    "run_group_id",
    "miss_regime",
    "split_role",
    "feature_support_mask",
}
ELIGIBILITY_COLUMNS = {"sender_mac_complete", "actionable"}
F0_COLUMNS = {
    "frame_age_us",
    "deadline_slack_us",
    "frame_size_bytes",
    "frame_packet_count",
    "frame_type",
    "packets_submitted",
    "packets_remaining_to_submit",
    "application_socket_packet_bytes_submitted",
}
F2_COLUMNS = {
    "frame_packets_mac_enqueued",
    "frame_packets_mac_dequeued",
    "frame_packets_tx_succeeded",
    "frame_mpdu_attempt_failures",
    "frame_packets_terminally_dropped",
    "frame_packets_currently_queued",
    "frame_mac_service_bytes_currently_queued",
    "mac_queue_packets",
    "mac_queue_service_bytes",
    "mac_queue_oldest_enqueue_time_ns",
    "packets_ahead_of_frame",
    "mac_service_bytes_ahead_of_frame",
    "frame_packets_pending_primary",
    "frame_mac_service_bytes_not_acknowledged",
    "frame_mac_service_bytes_pending_primary",
    "frame_packets_not_acknowledged",
    "queue_oldest_age_us",
}
F3_COLUMNS = {
    "current_cw",
    "remaining_backoff_slots",
    "nav_remaining_us",
    "current_phy_state",
    "channel_access_status",
    "medium_busy_now",
    "expected_access_reason_within_slack",
}
PROVENANCE_PREFIXES = ("history_coverage_",)
PROVENANCE_COLUMNS = {"telemetry_schema_version", "feature_support_mask"}
ABSOLUTE_TIMESTAMP_COLUMNS = {
    "last_tx_attempt_time_ns",
    "last_positive_ack_time_ns",
    "mac_queue_oldest_enqueue_time_ns",
}


@dataclass(frozen=True)
class SourceRun:
    """One run and its nominal batch configuration, when available."""

    run_dir: Path
    source_root: Path
    nominal_config: dict[str, Any] | None


def read_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: YAML root must be a mapping")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError(f"{path}: missing CSV header")
        return list(reader.fieldnames), list(reader)


def discover_source_runs(inputs: Iterable[Path]) -> list[SourceRun]:
    result: list[SourceRun] = []
    seen_paths: set[Path] = set()
    for raw_input in inputs:
        source_root = raw_input.resolve()
        manifest_path = source_root / "experiment_manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            runs = manifest.get("runs")
            if not isinstance(runs, list):
                raise ValueError(f"{manifest_path}: runs must be a list")
            for item in runs:
                if not isinstance(item, dict):
                    raise ValueError(f"{manifest_path}: malformed run entry")
                status = item.get("status")
                if status != "complete":
                    raise ValueError(
                        f"{manifest_path}: run {item.get('run_id')} has status {status}"
                    )
                directory = item.get("directory")
                if not isinstance(directory, str) or not directory:
                    raise ValueError(f"{manifest_path}: run directory is missing")
                run_dir = (source_root / directory).resolve()
                nominal = item.get("config")
                if nominal is not None and not isinstance(nominal, dict):
                    raise ValueError(f"{manifest_path}: run config must be a mapping")
                if run_dir in seen_paths:
                    raise ValueError(f"duplicate source run directory: {run_dir}")
                seen_paths.add(run_dir)
                result.append(SourceRun(run_dir, source_root, copy.deepcopy(nominal)))
        elif (source_root / "summary.json").is_file():
            if source_root in seen_paths:
                raise ValueError(f"duplicate source run directory: {source_root}")
            seen_paths.add(source_root)
            result.append(SourceRun(source_root, source_root, None))
        else:
            raise ValueError(
                f"{source_root}: expected experiment_manifest.json or a run directory"
            )
    if not result:
        raise ValueError("no source runs discovered")
    return result


def validate_analysis_config(config: dict[str, Any]) -> None:
    missing = sorted(REQUIRED_ANALYSIS_KEYS - config.keys())
    if missing:
        raise ValueError(f"analysis configuration is missing: {', '.join(missing)}")
    required_ood = config["required_ood_scenarios"]
    if not isinstance(required_ood, list) or not required_ood:
        raise ValueError("required_ood_scenarios must be a nonempty list")
    if len(required_ood) != len(set(required_ood)):
        raise ValueError("required_ood_scenarios contains duplicates")
    scenarios = config.get("ood_scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("ood_scenarios must be a list")
    by_id = {
        item.get("scenario_id"): item
        for item in scenarios
        if isinstance(item, dict) and isinstance(item.get("scenario_id"), str)
    }
    if len(by_id) != len(scenarios):
        raise ValueError("ood_scenarios contains malformed or duplicate entries")
    for scenario in required_ood:
        entry = by_id.get(scenario)
        if entry is None or entry.get("status") != "required":
            raise ValueError(f"required OOD scenario is not frozen as required: {scenario}")
        run_filter = entry.get("run_filter")
        if not isinstance(run_filter, dict) or run_filter.get("scenario_id") != scenario:
            raise ValueError(f"required OOD scenario lacks an exact run filter: {scenario}")
    for key in (
        "minimum_run_groups_validation_selection",
        "minimum_run_groups_validation_calibration",
        "minimum_run_groups_id_test",
        "minimum_run_groups_per_required_ood_scenario",
    ):
        value = config[key]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{key} must be a positive integer")


def validate_ood_run_filter(
    config: dict[str, Any],
    name: str,
    analysis: dict[str, Any],
) -> None:
    entries = {
        entry["scenario_id"]: entry
        for entry in analysis["ood_scenarios"]
    }
    entry = entries.get(name)
    if entry is None:
        if name.startswith("obss_"):
            raise ValueError(f"OBSS scenario is absent from analysis configuration: {name}")
        return
    run_filter = entry["run_filter"]
    background = config.get("background", {})
    obss = background.get("obss", {})
    wifi = config.get("wifi", {})
    observed = {
        "scenario_id": name,
        "topology": config.get("topology"),
        "policies": config.get("policy"),
        "wifi_standard": wifi.get("standard"),
        "data_mode": wifi.get("data_mode"),
        "ul_ofdma_enabled": wifi.get("ul_ofdma_enabled"),
        "obss_profile": obss.get("profile", "none"),
        "obss_stations_per_bss": obss.get("stations_per_bss"),
        "obss_ul_min_rate_mbps": obss.get("ul_min_rate_mbps"),
        "obss_ul_max_rate_mbps": obss.get("ul_max_rate_mbps"),
        "obss_dl_min_rate_mbps": obss.get("dl_min_rate_mbps"),
        "obss_dl_max_rate_mbps": obss.get("dl_max_rate_mbps"),
        "obss_on_mean_ms": obss.get("on_mean_ms"),
        "obss_off_mean_ms": obss.get("off_mean_ms"),
        "obss_station_manager": obss.get("station_manager"),
        "background_profile": background.get("profile"),
        "background_traffic": background.get("traffic"),
        "background_rate_mbps_per_station": background.get(
            "rate_mbps_per_station"
        ),
        "background_random_on_mean_ms": background.get("random_on_mean_ms"),
        "background_random_off_mean_ms": background.get("random_off_mean_ms"),
        "propagation_model": config.get("propagation", {}).get("model"),
    }
    for key, expected in run_filter.items():
        if key not in observed:
            raise ValueError(f"unsupported OOD run-filter key: {key}")
        matches = (
            observed[key] in expected
            if key == "policies" and isinstance(expected, list)
            else observed[key] == expected
        )
        if not matches:
            raise ValueError(
                f"OOD run filter mismatch for {name}: "
                f"{key}={observed[key]!r}, expected {expected!r}"
            )


def selected_link(policy: str) -> int:
    if policy == "fixed_link_0":
        return 0
    if policy == "fixed_link_1":
        return 1
    raise ValueError(f"prediction dataset requires a fixed-link policy, got {policy}")


def scenario_name(config: dict[str, Any]) -> str:
    background = config.get("background", {})
    profile = background.get("profile")
    obss_profile = background.get("obss", {}).get("profile", "none")
    if obss_profile == "mixed4x4":
        if profile == "none":
            return "obss_only"
        if profile == "legacy_mixed8":
            return "obss_plus_legacy_mixed8"
        raise ValueError(f"unsupported OBSS background profile: {profile}")
    if obss_profile != "none":
        raise ValueError(f"unsupported OBSS profile: {obss_profile}")
    if profile == "none":
        return "stage_a_none"
    if profile != "legacy_mixed8":
        raise ValueError(f"unsupported Stage-A background profile: {profile}")
    mode = background.get("correlation", {}).get("mode")
    if mode not in {
        "independent",
        "common_bursts",
        "mixed_common_and_independent",
    }:
        raise ValueError(f"unsupported Stage-A correlation mode: {mode}")
    return f"stage_a_{mode}"


def _resolved_group_config(config: dict[str, Any]) -> dict[str, Any]:
    background = copy.deepcopy(config.get("background", {}))
    background.pop("application_streams", None)
    obss = background.get("obss")
    if isinstance(obss, dict):
        obss.pop("bsses", None)
    wifi = copy.deepcopy(config.get("wifi", {}))
    for key in ("application_socket_count", "application_duplication"):
        wifi.pop(key, None)
    return {
        "topology": config.get("topology"),
        "duration_s": config.get("duration_s"),
        "stream": copy.deepcopy(config.get("stream")),
        "propagation": copy.deepcopy(config.get("propagation")),
        "wifi": wifi,
        "background": background,
        "predictionTelemetry": copy.deepcopy(config.get("predictionTelemetry")),
    }


def make_run_group_id(
    source: SourceRun,
    resolved_config: dict[str, Any],
    build_info: dict[str, Any],
) -> str:
    if source.nominal_config is not None:
        group_config = copy.deepcopy(source.nominal_config)
        group_config.pop("policy", None)
    else:
        group_config = _resolved_group_config(resolved_config)
    identity = {
        "scenario_name": scenario_name(resolved_config),
        "seed": resolved_config["seed"],
        "run": resolved_config["run"],
        "config": group_config,
        "project_git_commit": build_info["project_git_commit"],
        "ns3_upstream_commit": build_info["ns3_upstream_commit"],
    }
    return hashlib.sha256(canonical_json(identity).encode()).hexdigest()[:20]


def load_regime(
    config: dict[str, Any],
    loads: dict[str, Any],
    selected_path: int,
) -> str:
    name = scenario_name(config)
    if name == "stage_a_none":
        return "unloaded"
    if name.startswith("obss_"):
        return "ood"
    background = config["background"]
    mode = background["correlation"]["mode"]
    rate = float(background["rate_mbps_per_station"])
    for selection in loads.get("selected_loads", []):
        if selection.get("correlation_mode") != mode or selection.get("link") != selected_path:
            continue
        for regime in ("low", "medium", "high"):
            selected = selection.get(regime, {})
            if math.isclose(
                float(selected.get("rate_mbps_per_station", -1)),
                rate,
                rel_tol=0,
                abs_tol=1e-9,
            ):
                return regime
    return "off_target"


def feature_tier(column: str) -> str:
    if column in OUTCOME_COLUMNS:
        return "outcome"
    if column in CONTEXT_COLUMNS:
        return "metadata"
    if column in ELIGIBILITY_COLUMNS:
        return "eligibility"
    if column in F0_COLUMNS:
        return "F0"
    if column in F2_COLUMNS:
        return "F2"
    if column in F3_COLUMNS:
        return "F3"
    if column in PROVENANCE_COLUMNS or column.startswith(PROVENANCE_PREFIXES):
        return "provenance"
    return "F1-ideal"


def feature_dictionary(columns: Iterable[str]) -> dict[str, dict[str, Any]]:
    result = {}
    for column in columns:
        tier = feature_tier(column)
        model_eligible = tier in {"F0", "F1-ideal", "F2", "F3"}
        if column in ABSOLUTE_TIMESTAMP_COLUMNS:
            model_eligible = False
        result[column] = {
            "tier": tier,
            "model_eligible": model_eligible,
        }
    return result


def optional_nonnegative_int(value: str, field: str) -> int | None:
    if value == "":
        return None
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"invalid integer {field}: {value}") from error
    if parsed < 0:
        raise ValueError(f"negative integer {field}: {value}")
    return parsed


def derive_age(sample_time_ns: int, source_time: str, field: str) -> str:
    source = optional_nonnegative_int(source_time, field)
    if source is None:
        return ""
    if source > sample_time_ns:
        raise ValueError(f"{field} exceeds sample_time_ns")
    return format((sample_time_ns - source) / 1000.0, ".15g")


def type7_quantile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = quantile * (len(ordered) - 1)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)
