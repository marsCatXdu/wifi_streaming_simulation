#!/usr/bin/env python3
"""Audit and analyze a paired neutral multi-arm primary-tail T4 campaign."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import random
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from run_experiments import (
    MANIFEST_SCHEMA_VERSION,
    NS3_UPSTREAM_COMMIT,
    derive_run_id,
    expand_config,
    load_yaml,
)
from validate_outputs import ValidationError, _validate_adaptive_config, validate_run


BASELINES = {
    "str_mlo": ("mlo_str", "fixed_link_0"),
    "emlsr_mlo": ("mlo_emlsr", "fixed_link_0"),
}

ADAPTIVE_POLICIES = {
    "adaptive_airtime_duplication": (
        "adaptiveAirtimeDuplication",
        "full_copy",
        "full_forward",
        "full_forward+whole_copy_priced",
    ),
    "adaptive_deficit_duplication": (
        "adaptiveDeficitDuplication",
        "primary_deficit",
        "primary_unacknowledged_reverse",
        "primary_unacknowledged+whole_copy_priced",
    ),
}

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

DECISION_COLUMNS = {
    "run_id",
    "frame_id",
    "sample_stage",
    "sample_offset_us",
    "sample_time_ns",
    "actionable",
    "admission_score",
    "score_name",
    "score_kind",
    "score_model_id",
    "score_source_model_sha256",
    "score_target_provenance_sha256",
    "score_feature_contract_sha256",
    "score_combiner_sha256",
    "primary_miss_probability",
    "completed_tail_probability",
    "admission_airtime_us",
    "estimated_airtime_us",
    "admission_packet_count",
    "configured_admission_packet_cost",
    "effective_admission_packet_cost",
    "reference_airtime_us",
    "shadow_price",
    "dual_shadow_price",
    "shadow_price_source",
    "normalized_cost",
    "net_utility",
    "airtime_budget_fraction",
    "bucket_capacity_us",
    "bucket_balance_us",
    "initial_bucket_capacity_us",
    "reserved_airtime_us",
    "available_airtime_us",
    "measured_airtime_total_us",
    "decision",
    "secondary_launched",
    "frame_packet_count",
    "primary_acked_packets",
    "primary_acked_packet_indices",
    "secondary_packet_count",
    "secondary_packet_indices",
    "secondary_packet_order",
}

METER_FIELDS = {
    "tagged_secondary_tx_airtime_us",
    "tagged_secondary_tx_airtime_fraction",
    "measurement_duration_us",
    "maximum_budget_debt_us",
    "estimated_action_airtime_us",
    "actual_to_estimated_airtime_ratio",
    "forced_reservation_settlements",
    "budget_fraction",
    "initial_bucket_capacity_us",
    "finite_run_budget_us",
    "budget_excess_us",
}

ALLOWED_DECISIONS = {
    "price_rejected",
    "airtime_deferred",
    "action",
    "already_resolved",
    "not_actionable",
    "launch_rejected",
    "no_primary_deficit",
    "frame_type_restricted",
}

BOOTSTRAP_SEED = 0x5434_4D4C_4F
MAX_FRAME_CSV_P99_QUANTIZATION_US = 1.0
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

DECLARED_SOURCE_ARTIFACTS = {
    "experiments/configs/primary_tail_t4_operating_profiles_v1.yaml": (
        "ca986176837e6dd1541a2b9ff1ef920edbb8730eddb514ef11ce1514de47166e"
    ),
    "tools/evaluate_primary_tail_t4_profiles.py": (
        "85cab26e4af86e28996ec160eca8b0754e053378ac60138111ee2d648895b09c"
    ),
    "results/primary_tail_t4_corrected_v1/models/operating_profile_diagnostics.json": (
        "7ad68f210fd0d41474e548c35e85ab36de72696cf3679816c0e22161b9eaabfa"
    ),
}

DECLARED_CAMPAIGN_IDENTITIES = {
    False: {
        "experiment": "closed-loop-primary-tail-t4-campaign-v1",
        "config_file": "closed_loop_primary_tail_t4_campaign_v1.yaml",
        "config_sha256": (
            "b52c9fb413b3384177f284194e55b3cb70a9c14e38c1603ee2cc60f359c7e632"
        ),
        "matrix_sha256": (
            "8f0ed807b07588962cef2d57743abd15f937076b1acc782a565cc1be90b83fe3"
        ),
    },
    True: {
        "experiment": "closed-loop-primary-tail-t4-preflight-v1",
        "config_file": "closed_loop_primary_tail_t4_preflight_v1.yaml",
        "config_sha256": (
            "5b37e6c2fc1b02b135f9a93edffaf52d48bc30ab5fcccb2e51fab34b4d3407a0"
        ),
        "matrix_sha256": (
            "e435deec6be893bedca947a1a27317549c9b765561077a8e1256a003006e8a05"
        ),
    },
}

# Frozen development campaign declared by
# experiments/configs/closed_loop_primary_tail_t4_campaign_v1.yaml. The
# C++ emits floating-point values with 12 significant digits.  Keep both the
# source gates and their exact resolved-config spellings: a nearby value must
# not silently enter the declared campaign through a generic float tolerance.
DECLARED_FULL_T4_GATES = {
    "full_copy_cost_0p0055": 0.07905991394024306,
    "full_copy_cost_0p0080": 0.04585237128013217,
    "full_copy_cost_0p0100": 0.030390547162877975,
    "full_copy_cost_0p0120": 0.02073164341662734,
}
DECLARED_SERIALIZED_T4_GATES = {
    name: float(format(value, ".12g"))
    for name, value in DECLARED_FULL_T4_GATES.items()
}
DECLARED_BALANCED_ARM = "full_copy_cost_0p0080"
DECLARED_DEFICIT_ARM = "primary_deficit_iso_cost_0p0080"
DECLARED_ARM_IDS = set(DECLARED_FULL_T4_GATES) | {DECLARED_DEFICIT_ARM}
DECLARED_PAIRS = {(seed, 1) for seed in range(43, 55)}
DECLARED_PREFLIGHT_PAIRS = {(43, 1)}

DECLARED_EMLSR_DISABLED = {
    "activated": False,
    "profile": "not_applicable",
    "manager": "not_applicable",
    "ap_manager": "not_applicable",
    "link_ids": [],
    "main_phy_id": 0,
    "padding_delay_us": 0,
    "transition_delay_us": 0,
    "transition_timeout_us": 0,
    "medium_sync_duration_us": 0,
    "msd_ofdm_ed_threshold_dbm": 0,
    "msd_max_n_txops": 0,
    "channel_switch_delay_us": 0,
    "switch_aux_phy": False,
    "aux_phy_tx_capable": False,
    "aux_phy_channel_width_mhz": 0,
    "aux_phy_max_modulation_class": "not_applicable",
    "put_aux_phy_to_sleep": False,
    "in_device_interference": False,
    "use_notified_mac_header": False,
    "reset_cam_state": False,
    "allow_ul_txop_in_rx": False,
    "interrupt_switch": False,
    "use_aux_phy_cca": False,
    "switch_main_phy_back_delay_us": 0,
    "keep_main_phy_after_dl_txop": False,
    "check_access_on_main_phy_link": False,
    "min_ac_to_skip_check_access": "not_applicable",
    "ap_use_notified_mac_header": False,
    "ap_early_switch_to_listening": False,
    "ap_wait_trans_delay_on_psdu_rx_error": False,
    "ap_update_cw_after_failed_icf": False,
    "ap_report_failed_icf": False,
    "cam_generate_backoff_without_tx": False,
    "cam_proactive_backoff": False,
    "cam_reset_backoff_threshold_us": 0,
    "cam_n_slots_left": 0,
    "cam_n_slots_left_min_delay_us": 0,
    "notify_mac_header_rx_end": False,
    "main_phy_frequency_ranges": [],
}

DECLARED_TOPOLOGY_WIFI = {
    "dual_interface": {
        "static_association": False,
        "tid_to_link_mapping_ul": "not_applicable",
        "str_mode": "not_applicable",
        "multi_link_mode": "not_applicable",
        "application_socket_count": 2,
        "emlsr": DECLARED_EMLSR_DISABLED,
    },
    "mlo_str": {
        "static_association": True,
        "tid_to_link_mapping_ul": "0 0,1",
        "str_mode": "STR",
        "multi_link_mode": "STR",
        "application_socket_count": 1,
        "emlsr": DECLARED_EMLSR_DISABLED,
    },
    "mlo_emlsr": {
        "static_association": True,
        "tid_to_link_mapping_ul": "0 0,1",
        "str_mode": "not_applicable",
        "multi_link_mode": "EMLSR",
        "application_socket_count": 1,
        "emlsr": {
            "activated": True,
            "profile": "advanced_sta_ap_fixed_aux_v4",
            "manager": "ns3::AdvancedEmlsrManager",
            "ap_manager": "ns3::AdvancedApEmlsrManager",
            "link_ids": [0, 1],
            "main_phy_id": 1,
            "padding_delay_us": 128,
            "transition_delay_us": 128,
            "transition_timeout_us": 0,
            "medium_sync_duration_us": 5472,
            "msd_ofdm_ed_threshold_dbm": -72,
            "msd_max_n_txops": 1,
            "channel_switch_delay_us": 100,
            "switch_aux_phy": False,
            "aux_phy_tx_capable": False,
            "aux_phy_channel_width_mhz": 20,
            "aux_phy_max_modulation_class": "OFDM",
            "put_aux_phy_to_sleep": False,
            "in_device_interference": False,
            "use_notified_mac_header": True,
            "reset_cam_state": False,
            "allow_ul_txop_in_rx": False,
            "interrupt_switch": False,
            "use_aux_phy_cca": False,
            "switch_main_phy_back_delay_us": 5000,
            "keep_main_phy_after_dl_txop": False,
            "check_access_on_main_phy_link": True,
            "min_ac_to_skip_check_access": "AC_BK",
            "ap_use_notified_mac_header": True,
            "ap_early_switch_to_listening": False,
            "ap_wait_trans_delay_on_psdu_rx_error": True,
            "ap_update_cw_after_failed_icf": True,
            "ap_report_failed_icf": True,
            "cam_generate_backoff_without_tx": False,
            "cam_proactive_backoff": False,
            "cam_reset_backoff_threshold_us": 0,
            "cam_n_slots_left": 0,
            "cam_n_slots_left_min_delay_us": 25,
            "notify_mac_header_rx_end": True,
            "main_phy_frequency_ranges": [
                "WIFI_SPECTRUM_2_4_GHZ",
                "WIFI_SPECTRUM_5_GHZ",
            ],
        },
    },
}

DECLARED_NEUTRAL_ENVIRONMENT = {
    "duration_s": 60,
    "warmup_s": 1,
    "measurement_start_s": 1,
    "measurement_stop_s": 61,
    "stream": {
        "source": "synthetic",
        "trace_file": "",
        "fps": 30,
        "frame_size_bytes": 12000,
        "gop_length": 60,
        "keyframe_size_multiplier": 4,
        "payload_size_bytes": 1200,
        "deadline_us": 33333,
        "emission_mode": "burst",
    },
    "propagation": {
        "model": "log_distance_nakagami",
        "rss_dbm": -50,
        "station_distance_m": 10,
        "path_loss_exponent": 3,
        "reference_loss_2_4_ghz_db": 40.046,
        "reference_loss_5_ghz_db": 46.678,
        "nakagami_distance_1_m": 80,
        "nakagami_distance_2_m": 200,
        "nakagami_m0": 1.5,
        "nakagami_m1": 0.75,
        "nakagami_m2": 0.75,
        "random_stream_base": 5000,
    },
    "background": {
        "profile": "none",
        "traffic": "none",
        "direction": "uplink",
        "stations_per_link": [0, 0],
        "standards_per_link": ["802.11be", "802.11be"],
        "station_standards_per_link": [[], []],
        "application_streams": [],
        "association_mode": "not_applicable",
        "rate_mbps_per_station": 20,
        "packet_size_bytes": 1200,
        "near_distance_m": 2,
        "far_distance_m": 15,
        "random_stream_base": 1000,
        "random_on_mean_ms": 100,
        "random_off_mean_ms": 100,
        "obss": {
            "profile": "mixed4x4",
            "stations_per_bss": 4,
            "min_rate_mbps": 0.5,
            "max_rate_mbps": 8,
            "ul_min_rate_mbps": 0.5,
            "ul_max_rate_mbps": 3,
            "dl_min_rate_mbps": 2,
            "dl_max_rate_mbps": 8,
            "on_mean_ms": 100,
            "off_mean_ms": 300,
            "station_manager": "minstrel_ht",
            "manager_update_ms": 50,
            "use_latest_amendment_only": True,
            "packet_size_bytes": 1200,
            "area_min_x_m": -15,
            "area_max_x_m": 15,
            "area_min_y_m": -10,
            "area_max_y_m": 10,
            "sta_min_distance_m": 2,
            "sta_max_distance_m": 6,
            "placement_stream_base": 6000,
            "application_stream_base": 7000,
            "wifi_stream_base": 8000,
        },
        "correlation": {
            "mode": "independent",
            "trace": "",
            "common_on_mean_ms": 100,
            "common_off_mean_ms": 100,
            "local_on_mean_ms": 100,
            "local_off_mean_ms": 100,
            "common_on_duration_ms": 0,
            "common_off_duration_ms": 0,
            "local_on_duration_ms": 0,
            "local_off_duration_ms": 0,
        },
    },
    "shared_target_wifi": {
        "standard": "802.11be",
        "station_manager": "ConstantRateWifiManager",
        "data_mode": "EhtMcs5",
        "control_mode": "ErpOfdmRate24Mbps,OfdmRate24Mbps",
        "guard_interval": "800ns",
        "channel_settings": [
            "{1, 20, BAND_2_4GHZ, 0}",
            "{36, 20, BAND_5GHZ, 0}",
        ],
        "frequency_ranges": [
            "WIFI_SPECTRUM_2_4_GHZ",
            "WIFI_SPECTRUM_5_GHZ",
        ],
        "data_modes_per_link": ["EhtMcs5", "EhtMcs5"],
        "queue_max_packets": 500,
        "queue_max_delay_ms": 500,
        "max_ampdu_size_bytes": 65535,
        "max_amsdu_size_bytes": 0,
        "sta_max_inflights": 1,
        "ul_ofdma_enabled": False,
        "ul_ofdma_scope": "all_he_eht_aps",
        "ul_ofdma_access_interval_ms": 20,
        "ul_ofdma_bsrp_enabled": True,
        "ul_ofdma_max_stations": 4,
        "ul_ofdma_psdu_size_bytes": 1200,
        "block_ack_enabled": True,
        "frame_retry_limit": 7,
        "rts_cts_threshold_bytes": 4692480,
        "fragmentation_threshold_bytes": 65535,
        "access_category": "AC_BE",
        "txop_limit_us": 0,
        "application_duplication": False,
    },
}


class CampaignError(ValueError):
    """Raised when campaign inputs or controller evidence are invalid."""


@dataclass(frozen=True)
class Thresholds:
    """Predeclared neutral-campaign success criteria."""

    expected_pair_count: int = 12
    minimum_strict_wins: int = 9
    maximum_airtime_ratio: float = 1.20
    maximum_background_loss: float = 0.01
    maximum_budget_excess_us: float = 1.0

    def __post_init__(self) -> None:
        if self.expected_pair_count <= 0:
            raise ValueError("expected_pair_count must be positive")
        if not 0 <= self.minimum_strict_wins <= self.expected_pair_count:
            raise ValueError("minimum_strict_wins must be in [0, expected_pair_count]")
        if not math.isfinite(self.maximum_airtime_ratio) or self.maximum_airtime_ratio <= 0:
            raise ValueError("maximum_airtime_ratio must be positive and finite")
        if (
            not math.isfinite(self.maximum_background_loss)
            or not 0 <= self.maximum_background_loss <= 1
        ):
            raise ValueError("maximum_background_loss must be a fraction in [0, 1]")
        if not math.isfinite(self.maximum_budget_excess_us) or self.maximum_budget_excess_us < 0:
            raise ValueError("maximum_budget_excess_us must be nonnegative and finite")


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as error:
        raise CampaignError("campaign metadata contains a non-finite or non-JSON value") from error


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _finite(value: Any, description: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool):
        raise CampaignError(f"{description} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise CampaignError(f"{description} must be a finite number") from error
    if not math.isfinite(result) or (nonnegative and result < 0):
        qualifier = "nonnegative " if nonnegative else ""
        raise CampaignError(f"{description} must be a {qualifier}finite number")
    return result


def _integer(value: Any, description: str, *, nonnegative: bool = True) -> int:
    if isinstance(value, bool):
        raise CampaignError(f"{description} must be an integer")
    text = str(value)
    pattern = r"[0-9]+" if nonnegative else r"-?(?:0|[1-9][0-9]*)"
    if re.fullmatch(pattern, text) is None:
        raise CampaignError(f"{description} must be an integer")
    return int(text)


def _flag(value: Any, description: str) -> bool:
    if value not in ("0", "1", 0, 1, False, True):
        raise CampaignError(f"{description} must be 0 or 1")
    return str(int(value)) == "1"


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise CampaignError("cannot calculate a percentile of an empty sequence")
    index = (len(ordered) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def paired_bootstrap(
    left: list[float],
    right: list[float],
    statistic: Callable[[list[float], list[float]], float],
    *,
    replicates: int = 20_000,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Return a deterministic percentile paired-bootstrap interval."""
    if len(left) != len(right) or not left:
        raise CampaignError("paired bootstrap requires two nonempty equal-length samples")
    if replicates < 1:
        raise CampaignError("bootstrap replicate count must be positive")
    if not all(math.isfinite(value) for value in [*left, *right]):
        raise CampaignError("paired bootstrap input contains a non-finite value")
    point = statistic(left, right)
    if not math.isfinite(point):
        raise CampaignError("paired bootstrap statistic is non-finite")
    generator = random.Random(seed)
    samples: list[float] = []
    count = len(left)
    for _ in range(replicates):
        indexes = [generator.randrange(count) for _ in range(count)]
        value = statistic(
            [left[index] for index in indexes],
            [right[index] for index in indexes],
        )
        if not math.isfinite(value):
            raise CampaignError("paired bootstrap replicate is non-finite")
        samples.append(value)
    return {
        "method": "paired percentile bootstrap",
        "confidence_level": 0.95,
        "paired_unit_count": count,
        "replicates": replicates,
        "seed": seed,
        "estimate": point,
        "ci95_low": _percentile(samples, 0.025),
        "ci95_high": _percentile(samples, 0.975),
    }


def _resolve_aggregate(path: Path) -> Path:
    path = path.resolve()
    candidates = [path] if path.is_file() else [
        path / "aggregate.json",
        path / "runs" / "aggregate.json",
    ]
    matches = [candidate for candidate in candidates if candidate.is_file()]
    if len(matches) != 1:
        raise CampaignError(
            f"expected exactly one aggregate for {path}; tried "
            + ", ".join(str(candidate) for candidate in candidates)
        )
    return matches[0]


def _run_directory(run: dict[str, Any], aggregate_path: Path) -> Path:
    serialized = run.get("run_dir")
    candidates: list[Path] = []
    if isinstance(serialized, str) and serialized:
        candidates.append(Path(serialized))
        candidates.append(aggregate_path.parent / serialized)
    run_id = run.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise CampaignError(f"{aggregate_path}: run has no nonempty run_id")
    candidates.append(aggregate_path.parent / run_id)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    raise CampaignError(
        f"cannot locate run directory for {run_id}; tried "
        + ", ".join(str(candidate) for candidate in candidates)
    )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CampaignError(f"missing campaign input: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CampaignError(f"{path}: expected a JSON object")
    return value


def _sha256_file(path: Path) -> str:
    try:
        with path.open("rb") as source:
            digest = hashlib.sha256()
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise CampaignError(f"cannot read declared campaign artifact {path}: {error}") from error
    return digest.hexdigest()


def _validate_declared_source_artifacts(preflight: bool) -> dict[str, str]:
    identity = DECLARED_CAMPAIGN_IDENTITIES[preflight]
    relative_campaign = f"experiments/configs/{identity['config_file']}"
    expected = {
        **DECLARED_SOURCE_ARTIFACTS,
        relative_campaign: identity["config_sha256"],
    }
    observed: dict[str, str] = {}
    for relative, expected_digest in expected.items():
        digest = _sha256_file(REPOSITORY_ROOT / relative)
        if digest != expected_digest:
            raise CampaignError(
                f"declared campaign artifact checksum changed: {relative}; "
                f"expected {expected_digest}, found {digest}"
            )
        observed[relative] = digest
    return observed


def _declared_manifest_specs(
    preflight: bool,
    project_commit: str,
    ns3_commit: str,
) -> dict[str, dict[str, Any]]:
    identity = DECLARED_CAMPAIGN_IDENTITIES[preflight]
    campaign_path = (
        REPOSITORY_ROOT / "experiments" / "configs" / identity["config_file"]
    )
    try:
        document = load_yaml(campaign_path)
        if _sha256_json(document) != identity["matrix_sha256"]:
            raise CampaignError(
                f"{campaign_path}: inherited campaign matrix checksum changed"
            )
        specs = expand_config(document)
    except (OSError, ValueError) as error:
        raise CampaignError(f"cannot expand declared campaign {campaign_path}: {error}") from error
    result: dict[str, dict[str, Any]] = {}
    for spec in specs:
        run_id = derive_run_id(
            spec["config"],
            spec["seed"],
            spec["run"],
            ns3_commit,
            project_commit,
        )
        if run_id in result:
            raise CampaignError(f"{campaign_path}: duplicate derived run ID {run_id}")
        result[run_id] = spec
    return result


def _validate_manifest(
    aggregate_path: Path,
    aggregate: dict[str, Any],
    *,
    preflight: bool,
) -> dict[str, Any]:
    manifest_path = aggregate_path.parent / "experiment_manifest.json"
    manifest = _read_json(manifest_path)
    identity = DECLARED_CAMPAIGN_IDENTITIES[preflight]
    expected_fields = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "experiment": identity["experiment"],
        "matrix_sha256": identity["matrix_sha256"],
    }
    mismatches = [
        field
        for field, expected in expected_fields.items()
        if manifest.get(field) != expected
    ]
    if mismatches:
        raise CampaignError(
            f"{manifest_path}: campaign identity mismatch in {', '.join(mismatches)}"
        )
    config_file = manifest.get("config_file")
    if not isinstance(config_file, str) or Path(config_file).name != identity["config_file"]:
        raise CampaignError(f"{manifest_path}: unexpected campaign config_file")
    project_commit = manifest.get("project_commit")
    ns3_commit = manifest.get("ns3_upstream_commit")
    if (
        not isinstance(project_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", project_commit) is None
    ):
        raise CampaignError(f"{manifest_path}: invalid project_commit")
    if ns3_commit != NS3_UPSTREAM_COMMIT:
        raise CampaignError(f"{manifest_path}: unexpected ns3_upstream_commit")
    declared_by_id = _declared_manifest_specs(
        preflight,
        project_commit,
        ns3_commit,
    )

    aggregate_runs = aggregate.get("runs")
    manifest_runs = manifest.get("runs")
    if not isinstance(aggregate_runs, list) or not isinstance(manifest_runs, list):
        raise CampaignError(f"{manifest_path}: manifest and aggregate require runs lists")
    aggregate_by_id: dict[str, dict[str, Any]] = {}
    for run in aggregate_runs:
        if not isinstance(run, dict):
            raise CampaignError(f"{aggregate_path}: run entry is not an object")
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise CampaignError(f"{aggregate_path}: aggregate run has no run_id")
        if run_id in aggregate_by_id:
            raise CampaignError(f"{aggregate_path}: duplicate aggregate run_id {run_id}")
        aggregate_by_id[run_id] = run

    manifest_by_id: dict[str, dict[str, Any]] = {}
    for item in manifest_runs:
        if not isinstance(item, dict):
            raise CampaignError(f"{manifest_path}: run entry is not an object")
        run_id = item.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise CampaignError(f"{manifest_path}: manifest run has no run_id")
        if run_id in manifest_by_id:
            raise CampaignError(f"{manifest_path}: duplicate manifest run_id {run_id}")
        if item.get("status") != "complete" or item.get("directory") != run_id:
            raise CampaignError(
                f"{manifest_path}: run {run_id} is not a completed canonical entry"
            )
        manifest_by_id[run_id] = item
    if set(manifest_by_id) != set(aggregate_by_id):
        raise CampaignError(
            f"{manifest_path}: manifest and aggregate run-id sets differ"
        )
    if set(manifest_by_id) != set(declared_by_id):
        raise CampaignError(
            f"{manifest_path}: manifest run-id set differs from the declared matrix"
        )

    expected_run_count = 7 if preflight else 84
    if len(manifest_by_id) != expected_run_count:
        raise CampaignError(
            f"{manifest_path}: expected {expected_run_count} completed runs, "
            f"found {len(manifest_by_id)}"
        )
    for run_id, item in manifest_by_id.items():
        aggregate_run = aggregate_by_id[run_id]
        declared = declared_by_id[run_id]
        seed = _integer(item.get("seed"), f"{manifest_path}: {run_id} seed")
        run_number = _integer(item.get("run"), f"{manifest_path}: {run_id} run")
        if (
            seed != aggregate_run.get("seed")
            or run_number != aggregate_run.get("run")
            or seed != declared["seed"]
            or run_number != declared["run"]
        ):
            raise CampaignError(
                f"{manifest_path}: run {run_id} seed/run disagrees with its declaration"
            )
        manifest_config = item.get("config")
        if not isinstance(manifest_config, dict) or _canonical_json(
            manifest_config
        ) != _canonical_json(declared["config"]):
            raise CampaignError(
                f"{manifest_path}: run {run_id} config differs from the declared matrix"
            )
        if (
            manifest_config.get("topology") != aggregate_run.get("topology")
            or manifest_config.get("policy") != aggregate_run.get("policy")
        ):
            raise CampaignError(
                f"{manifest_path}: run {run_id} topology/policy disagrees with aggregate"
            )
    return {
        "path": str(manifest_path.resolve()),
        "sha256": _sha256_file(manifest_path),
        "schema_version": manifest["schema_version"],
        "experiment": manifest["experiment"],
        "matrix_sha256": manifest["matrix_sha256"],
        "config_file": config_file,
        "project_commit": project_commit,
        "ns3_upstream_commit": ns3_commit,
        "completed_run_count": len(manifest_by_id),
        "expanded_matrix_identity_verified": True,
        "derived_run_ids_verified": True,
    }


def _read_csv(path: Path, required: set[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise CampaignError(f"missing campaign input: {path}")
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        fieldnames = reader.fieldnames or []
        if len(fieldnames) != len(set(fieldnames)):
            raise CampaignError(f"{path}: duplicate CSV columns")
        missing = sorted(required - set(fieldnames))
        if missing:
            raise CampaignError(f"{path}: missing columns {', '.join(missing)}")
        rows = list(reader)
    if any(None in row for row in rows):
        raise CampaignError(f"{path}: row has more values than the header")
    return rows


def _read_config(run: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    config = _read_json(run_dir / "resolved_config.json")
    embedded = run.get("config")
    if embedded is not None and _canonical_json(embedded) != _canonical_json(config):
        raise CampaignError(f"{run_dir}: aggregate and resolved configurations disagree")
    if config.get("run_id") != run.get("run_id"):
        raise CampaignError(f"{run_dir}: resolved run_id disagrees with aggregate")
    return config


def _pair_key(run: dict[str, Any], config: dict[str, Any], run_dir: Path) -> tuple[int, int]:
    seed = _integer(run.get("seed", config.get("seed")), f"{run_dir}: seed")
    run_number = _integer(run.get("run", config.get("run")), f"{run_dir}: run")
    if seed <= 0 or run_number <= 0:
        raise CampaignError(f"{run_dir}: seed and run must be positive")
    if config.get("seed") != seed or config.get("run") != run_number:
        raise CampaignError(f"{run_dir}: aggregate and resolved seed/run disagree")
    return seed, run_number


def _environment(config: dict[str, Any]) -> dict[str, Any]:
    missing = [key for key in ENVIRONMENT_KEYS if key not in config]
    if missing:
        raise CampaignError(f"resolved config is missing environment fields {missing}")
    wifi = config.get("wifi")
    if not isinstance(wifi, dict):
        raise CampaignError("resolved config is missing wifi settings")
    return {
        **{key: config[key] for key in ENVIRONMENT_KEYS},
        "shared_target_wifi": {
            key: wifi.get(key, "__MISSING__") for key in SHARED_WIFI_KEYS
        },
    }


def _validate_topology_wifi(config: dict[str, Any], run_dir: Path) -> None:
    topology = config.get("topology")
    expected = DECLARED_TOPOLOGY_WIFI.get(topology)
    if expected is None:
        raise CampaignError(f"{run_dir}: undeclared campaign topology {topology!r}")
    wifi = config.get("wifi")
    if not isinstance(wifi, dict):
        raise CampaignError(f"{run_dir}: resolved config is missing wifi settings")
    actual = {field: wifi.get(field, "__MISSING__") for field in expected}
    if _canonical_json(actual) != _canonical_json(expected):
        raise CampaignError(
            f"{run_dir}: topology-specific Wi-Fi configuration differs from declaration"
        )


def _environment_without_generated_geometry(environment: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(environment)
    obss = result.get("background", {}).get("obss")
    if isinstance(obss, dict):
        obss.pop("bsses", None)
    return result


def _nominal_config(config: dict[str, Any]) -> dict[str, Any]:
    """Remove only run identity and the seeded OBSS geometry."""
    result = copy.deepcopy(config)
    for field in ("run_id", "seed", "run"):
        result.pop(field, None)
    obss = result.get("background", {}).get("obss")
    if isinstance(obss, dict):
        obss.pop("bsses", None)
    return result


def _build_identity(run_dir: Path) -> dict[str, str]:
    build = _read_json(run_dir / "build_info.json")
    result: dict[str, str] = {}
    for field in BUILD_IDENTITY_FIELDS:
        value = build.get(field)
        if not isinstance(value, str) or not value:
            raise CampaignError(f"{run_dir}/build_info.json: invalid {field}")
        result[field] = value
    return result


def _sender_airtime_us(run_dir: Path) -> float:
    rows = _read_csv(run_dir / "link_intervals.csv", {"link_id", "phy_tx_time_us"})
    if not rows:
        raise CampaignError(f"{run_dir}/link_intervals.csv: no rows")
    seen: set[int] = set()
    total = 0.0
    for row in rows:
        link = _integer(row["link_id"], f"{run_dir}: link_id")
        if link in seen:
            raise CampaignError(f"{run_dir}: duplicate link-{link} airtime row")
        seen.add(link)
        total += _finite(
            row["phy_tx_time_us"], f"{run_dir}: sender PHY airtime", nonnegative=True
        )
    if seen != {0, 1}:
        raise CampaignError(f"{run_dir}: headline runs require exactly links 0 and 1")
    return total


def _validated_headline_metrics(
    run: dict[str, Any],
    run_dir: Path,
    config: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    run_id = run.get("run_id")
    try:
        validation = validate_run(
            run_dir,
            expected_run_id=run_id,
            expected_project_commit=manifest["project_commit"],
            expected_ns3_commit=manifest["ns3_upstream_commit"],
        )
    except ValidationError as error:
        raise CampaignError(f"{run_dir}: core run validation failed: {error}") from error
    if validation.get("valid") is not True or validation.get("run_id") != run_id:
        raise CampaignError(f"{run_dir}: core run validation returned an invalid identity")

    summary = _read_json(run_dir / "summary.json")
    frames = _read_csv(
        run_dir / "frames.csv",
        {"deadline_miss", "incomplete", "union_latency_us"},
    )
    if not frames:
        raise CampaignError(f"{run_dir}/frames.csv: no headline frame evidence")
    misses = sum(
        _flag(row.get("deadline_miss"), f"{run_dir}/frames.csv: deadline_miss")
        for row in frames
    )
    completed_latencies = [
        _finite(
            row.get("union_latency_us"),
            f"{run_dir}/frames.csv: union_latency_us",
            nonnegative=True,
        )
        for row in frames
        if not _flag(row.get("incomplete"), f"{run_dir}/frames.csv: incomplete")
    ]
    if not completed_latencies:
        raise CampaignError(f"{run_dir}: no completed frames for the P99 headline")
    miss_ratio = misses / len(frames)
    csv_completed_p99_us = _percentile(completed_latencies, 0.99)

    duration_s = _finite(config.get("duration_s"), f"{run_dir}: duration_s")
    if duration_s <= 0:
        raise CampaignError(f"{run_dir}: duration_s must be positive")
    background_flows = _read_csv(
        run_dir / "background_flows.csv",
        {"bytes_received"},
    )
    background_bytes_received = sum(
        _integer(
            row.get("bytes_received"),
            f"{run_dir}/background_flows.csv: bytes_received",
        )
        for row in background_flows
    )
    summary_background_bytes = _integer(
        summary.get("background_bytes_received"),
        f"{run_dir}/summary.json: background_bytes_received",
    )
    if background_bytes_received != summary_background_bytes:
        raise CampaignError(
            f"{run_dir}: background flow bytes disagree with summary"
        )
    background_mbps = background_bytes_received * 8.0 / duration_s / 1e6

    summary_miss = _finite(
        summary.get("deadline_miss_ratio"),
        f"{run_dir}/summary.json: deadline_miss_ratio",
        nonnegative=True,
    )
    summary_p99 = _finite(
        summary.get("latency_p99_us"),
        f"{run_dir}/summary.json: latency_p99_us",
        nonnegative=True,
    )
    summary_background = _finite(
        summary.get("background_throughput_mbps"),
        f"{run_dir}/summary.json: background_throughput_mbps",
        nonnegative=True,
    )
    p99_quantization_delta_us = abs(csv_completed_p99_us - summary_p99)
    if p99_quantization_delta_us > MAX_FRAME_CSV_P99_QUANTIZATION_US:
        raise CampaignError(
            f"{run_dir}: completed-frame P99 exceeds the 1 us frame-CSV "
            "quantization bound"
        )
    if not _close(summary_miss, miss_ratio) or not _close(
        summary_background, background_mbps
    ):
        raise CampaignError(
            f"{run_dir}: summary headline differs from reconstructed raw evidence"
        )
    aggregate_values = {
        "deadline_miss_ratio": run.get("deadline_miss_ratio"),
        "completed_frame_p99_us": run.get("latency_p99_us"),
        "background_throughput_mbps": run.get("background_throughput_mbps"),
    }
    summary_values = {
        "deadline_miss_ratio": summary_miss,
        "completed_frame_p99_us": summary_p99,
        "background_throughput_mbps": summary_background,
    }
    for metric, value in aggregate_values.items():
        aggregate_number = _finite(
            value,
            f"{run_dir}: aggregate {metric}",
            nonnegative=True,
        )
        if not _close(aggregate_number, summary_values[metric]):
            raise CampaignError(
                f"{run_dir}: aggregate {metric} differs from validated summary"
            )
    return {
        **summary_values,
        "frame_csv_completed_p99_us": csv_completed_p99_us,
        "frame_csv_p99_quantization_delta_us": p99_quantization_delta_us,
        "frame_csv_p99_quantization_bound_us": (
            MAX_FRAME_CSV_P99_QUANTIZATION_US
        ),
        "core_run_validation": validation,
        "headline_metrics_validated_against_raw_evidence": True,
    }


def _adaptive_identity(
    config: dict[str, Any], run_dir: Path
) -> tuple[str, str, dict[str, Any], str]:
    policy = config.get("policy")
    topology = config.get("topology")
    if policy not in ADAPTIVE_POLICIES or topology != "dual_interface":
        raise CampaignError(f"{run_dir}: invalid adaptive topology/policy identity")
    key, mechanism, packet_selection, operating_profile = ADAPTIVE_POLICIES[policy]
    adaptive = config.get(key)
    if not isinstance(adaptive, dict):
        raise CampaignError(f"{run_dir}: missing {key} object")
    try:
        offsets = _validate_adaptive_config(adaptive, packet_selection)
    except ValidationError as error:
        raise CampaignError(f"{run_dir}: {error}") from error
    if offsets != [0, 4000] or adaptive.get("stages") != ["T0", "T4"]:
        raise CampaignError(f"{run_dir}: campaign requires exact T0/T4 staged control")
    if adaptive.get("i_frame_only_decision_offsets_us") != [0]:
        raise CampaignError(f"{run_dir}: campaign requires I-only T0 admission")
    if adaptive.get("operating_profile") != operating_profile:
        raise CampaignError(f"{run_dir}: unexpected operating profile")
    if adaptive.get("effective_admission_packet_cost") != "whole_copy":
        raise CampaignError(f"{run_dir}: campaign requires whole-copy admission pricing")
    telemetry = config.get("predictionTelemetry")
    if not isinstance(telemetry, dict) or (
        telemetry.get("enabled") is not True
        or telemetry.get("sample_offsets_us") != [0, 4000]
        or telemetry.get("history_windows_us") != [1000, 5000, 20000]
        or telemetry.get("polling_interval_us") != 1000
        or telemetry.get("polling_report_delay_us") != 1000
        or telemetry.get("polling_schema_version") != 1
        or telemetry.get("telemetry_schema_version") != 3
        or telemetry.get("event_schema_version") != 2
        or telemetry.get("feature_support_mask_version") != 2
        or telemetry.get("event_log_enabled") is not False
        or telemetry.get("oracle_features_enabled") is not False
    ):
        raise CampaignError(f"{run_dir}: invalid causal T0/T4 polling telemetry contract")
    required_explicit = {
        "score_contract",
        "stage_scorers",
        "admission_feature_set",
        "packet_selection_feature_set",
        "packet_selection",
        "configured_admission_packet_cost",
        "effective_admission_packet_cost",
        "operating_profile",
        "budget_fraction",
        "bucket_horizon_us",
        "initial_bucket_horizon_us",
        "initial_bucket_capacity_us",
        "initial_shadow_price",
        "dual_step",
        "admission_uses_retry_inflation",
        "admission_cost_definition",
        "reservation_cost_definition",
        "cost_safety_factor",
        "cost_ewma_alpha",
        "decision_offsets_us",
        "shadow_price_mode",
        "decision_offset_shadow_prices",
        "i_frame_only_decision_offsets_us",
    }
    missing = sorted(required_explicit - adaptive.keys())
    if missing:
        raise CampaignError(f"{run_dir}: staged controller omits {', '.join(missing)}")
    numeric_contract = {
        "budget_fraction": 0.02,
        "initial_bucket_capacity_us": 40_000.0,
        "initial_shadow_price": 0.034,
        "dual_step": 0.0,
        "cost_safety_factor": 1.25,
        "cost_ewma_alpha": 0.1,
    }
    for field, expected in numeric_contract.items():
        actual = _finite(adaptive.get(field), f"{run_dir}: {field}")
        if not _close(actual, expected):
            raise CampaignError(f"{run_dir}: {field} differs from declared campaign")
    if (
        adaptive.get("budget_fraction") != 0.02
        or adaptive.get("bucket_horizon_us") != 10_000_000
        or adaptive.get("initial_bucket_horizon_us") != 2_000_000
        or adaptive.get("admission_uses_retry_inflation") is not False
        or adaptive.get("decision_offset_shadow_prices", {}).keys() != {"0", "4000"}
        or float(adaptive["decision_offset_shadow_prices"]["0"]) != 0.034
    ):
        raise CampaignError(f"{run_dir}: staged controller differs from declared campaign")
    t4_price = _finite(
        adaptive["decision_offset_shadow_prices"]["4000"], f"{run_dir}: T4 price"
    )
    if mechanism == "full_copy":
        if adaptive.get("configured_admission_packet_cost") != "launched_packet_set":
            raise CampaignError(f"{run_dir}: full-copy campaign cost basis changed")
        matches = [
            name
            for name, expected in DECLARED_SERIALIZED_T4_GATES.items()
            if t4_price == expected
        ]
        if len(matches) != 1:
            raise CampaignError(f"{run_dir}: undeclared full-copy T4 gate {t4_price}")
        arm_id = matches[0]
    else:
        balanced_gate = DECLARED_SERIALIZED_T4_GATES[DECLARED_BALANCED_ARM]
        if (
            adaptive.get("configured_admission_packet_cost") != "whole_copy"
            or t4_price != balanced_gate
        ):
            raise CampaignError(f"{run_dir}: deficit arm is not the declared balanced ISO")
        arm_id = DECLARED_DEFICIT_ARM
    return mechanism, arm_id, adaptive, key


def _classify(
    config: dict[str, Any], run: dict[str, Any], run_dir: Path
) -> tuple[str, str | None, dict[str, Any] | None, str | None] | None:
    aggregate_identity = (run.get("topology"), run.get("policy"))
    resolved_identity = (config.get("topology"), config.get("policy"))
    if aggregate_identity != resolved_identity:
        raise CampaignError(f"{run_dir}: aggregate and resolved topology/policy disagree")
    for baseline, identity in BASELINES.items():
        if resolved_identity == identity:
            return baseline, None, None, None
    adaptive_like = (
        resolved_identity[0] == "dual_interface"
        and str(resolved_identity[1]).startswith("adaptive_")
    )
    if config.get("policy") in ADAPTIVE_POLICIES or adaptive_like:
        mechanism, arm_id, adaptive, key = _adaptive_identity(config, run_dir)
        return mechanism, arm_id, adaptive, key
    return None


def _number_from_row(
    row: dict[str, str], key: str, run_dir: Path, *, nonnegative: bool = False
) -> float:
    return _finite(
        row.get(key),
        f"{run_dir}/adaptive_airtime_decisions.csv: {key}",
        nonnegative=nonnegative,
    )


def _optional_probability(row: dict[str, str], key: str, run_dir: Path) -> float | None:
    value = row.get(key, "")
    if value == "":
        return None
    result = _finite(value, f"{run_dir}/adaptive_airtime_decisions.csv: {key}")
    if not 0 <= result <= 1:
        raise CampaignError(f"{run_dir}: {key} is outside [0, 1]")
    return result


def _indices(value: str, description: str) -> list[int]:
    if value == "":
        return []
    tokens = value.split(";")
    if any(re.fullmatch(r"[0-9]+", token) is None for token in tokens):
        raise CampaignError(f"{description}: malformed packet indices")
    result = [int(token) for token in tokens]
    if len(result) != len(set(result)):
        raise CampaignError(f"{description}: duplicate packet indices")
    return result


def _audit_meter(
    run_dir: Path,
    config: dict[str, Any],
    adaptive: dict[str, Any],
    estimated_action_airtime_us: float,
    minimum_available_us: float,
    thresholds: Thresholds,
) -> dict[str, Any]:
    meter_config = config.get("secondaryAirtimeMeter")
    if not isinstance(meter_config, dict) or meter_config.get("enabled") is not True:
        raise CampaignError(f"{run_dir}: adaptive campaign requires the secondary meter")
    if (
        meter_config.get("path_id") != 0
        or meter_config.get("copy_id") != 1
        or meter_config.get("definition") != "secondary_sender_phy_tx_airtime"
    ):
        raise CampaignError(f"{run_dir}: invalid secondary meter identity")
    summary = _read_json(run_dir / "secondary_airtime_summary.json")
    missing = sorted(METER_FIELDS - summary.keys())
    if missing:
        raise CampaignError(f"{run_dir}: meter summary omits {', '.join(missing)}")
    values = {
        field: _finite(
            summary[field], f"{run_dir}/secondary_airtime_summary.json: {field}", nonnegative=True
        )
        for field in METER_FIELDS
    }
    forced_settlements = summary["forced_reservation_settlements"]
    if (
        not isinstance(forced_settlements, int)
        or isinstance(forced_settlements, bool)
        or forced_settlements < 0
    ):
        raise CampaignError(f"{run_dir}: invalid forced reservation settlement count")
    duration_us = (
        _finite(config.get("measurement_stop_s"), f"{run_dir}: measurement_stop_s")
        - _finite(config.get("measurement_start_s"), f"{run_dir}: measurement_start_s")
    ) * 1_000_000.0
    if duration_us <= 0 or not _close(values["measurement_duration_us"], duration_us):
        raise CampaignError(f"{run_dir}: invalid meter measurement duration")
    tagged = values["tagged_secondary_tx_airtime_us"]
    if not _close(values["tagged_secondary_tx_airtime_fraction"], tagged / duration_us):
        raise CampaignError(f"{run_dir}: inconsistent tagged airtime fraction")
    fraction = _finite(adaptive["budget_fraction"], f"{run_dir}: budget fraction")
    initial_capacity = _finite(
        adaptive["initial_bucket_capacity_us"], f"{run_dir}: initial bucket capacity"
    )
    finite_budget = fraction * duration_us + initial_capacity
    expected_excess = max(0.0, tagged - finite_budget)
    if (
        not _close(values["budget_fraction"], fraction)
        or not _close(values["initial_bucket_capacity_us"], initial_capacity)
        or not _close(values["finite_run_budget_us"], finite_budget)
        or not _close(values["budget_excess_us"], expected_excess)
    ):
        raise CampaignError(f"{run_dir}: meter budget accounting is inconsistent")
    if values["budget_excess_us"] > thresholds.maximum_budget_excess_us:
        raise CampaignError(f"{run_dir}: secondary airtime exceeds the finite-run budget")
    if not _close(values["estimated_action_airtime_us"], estimated_action_airtime_us):
        raise CampaignError(f"{run_dir}: meter action estimates do not match decisions")
    observed_debt = max(0.0, -minimum_available_us)
    if values["maximum_budget_debt_us"] + 1e-9 < observed_debt:
        raise CampaignError(f"{run_dir}: meter misses observed reservation debt")
    return {
        "tagged_secondary_tx_airtime_us": tagged,
        "tagged_secondary_tx_airtime_fraction": values[
            "tagged_secondary_tx_airtime_fraction"
        ],
        "finite_run_budget_us": finite_budget,
        "budget_excess_us": values["budget_excess_us"],
        "maximum_budget_debt_us": values["maximum_budget_debt_us"],
        "forced_reservation_settlements": forced_settlements,
    }


def _audit_adaptive(
    run_dir: Path,
    config: dict[str, Any],
    adaptive: dict[str, Any],
    mechanism: str,
    thresholds: Thresholds,
) -> dict[str, Any]:
    frame_rows = _read_csv(
        run_dir / "frames.csv", {"frame_id", "frame_type", "packet_count"}
    )
    frames: dict[int, dict[str, Any]] = {}
    for row in frame_rows:
        frame_id = _integer(row.get("frame_id"), f"{run_dir}/frames.csv: frame_id")
        if frame_id in frames:
            raise CampaignError(f"{run_dir}/frames.csv: duplicate frame {frame_id}")
        frame_type = row.get("frame_type")
        if frame_type not in {"I_FRAME", "P_FRAME"}:
            raise CampaignError(f"{run_dir}/frames.csv: invalid frame type")
        frames[frame_id] = {
            "frame_type": frame_type,
            "packet_count": _integer(
                row.get("packet_count"), f"{run_dir}/frames.csv: packet_count"
            ),
        }
    if not frames:
        raise CampaignError(f"{run_dir}/frames.csv: no frames")

    rows = _read_csv(run_dir / "adaptive_airtime_decisions.csv", DECISION_COLUMNS)
    if len(rows) != 2 * len(frames):
        raise CampaignError(f"{run_dir}: adaptive decision cardinality is not two per frame")
    expected_scorers = adaptive["stage_scorers"]
    offset_prices = {
        int(offset): _finite(price, f"{run_dir}: offset price")
        for offset, price in adaptive["decision_offset_shadow_prices"].items()
    }
    capacity = _finite(adaptive["budget_fraction"], f"{run_dir}: budget fraction") * _integer(
        adaptive["bucket_horizon_us"], f"{run_dir}: bucket horizon"
    )
    initial_capacity = _finite(
        adaptive["initial_bucket_capacity_us"], f"{run_dir}: initial capacity"
    )
    expected_initial = _finite(
        adaptive["budget_fraction"], f"{run_dir}: budget fraction"
    ) * _integer(adaptive["initial_bucket_horizon_us"], f"{run_dir}: initial horizon")
    if not _close(initial_capacity, expected_initial):
        raise CampaignError(f"{run_dir}: initial bucket capacity is inconsistent")

    seen: set[tuple[int, int]] = set()
    launched_frames: set[int] = set()
    previous_time = -1
    previous_measured = 0.0
    reference_airtime: float | None = None
    last_t0_time: int | None = None
    last_t0_measured = 0.0
    last_t0_dual = _finite(
        adaptive["initial_shadow_price"], f"{run_dir}: initial shadow price"
    )
    minimum_available = math.inf
    estimated_action_total = 0.0
    decision_counts = {name: 0 for name in sorted(ALLOWED_DECISIONS)}
    stage_action_counts = {"T0": 0, "T4": 0}
    trace: dict[tuple[int, int], dict[str, Any]] = {}

    for row in rows:
        frame_id = _integer(row.get("frame_id"), f"{run_dir}: decision frame_id")
        offset = _integer(row.get("sample_offset_us"), f"{run_dir}: sample offset")
        if frame_id not in frames or offset not in {0, 4000}:
            raise CampaignError(f"{run_dir}: decision references an unknown frame or stage")
        key = (frame_id, offset)
        if key in seen:
            raise CampaignError(f"{run_dir}: duplicate decision for frame/stage {key}")
        seen.add(key)
        stage = f"T{offset // 1000}"
        if row.get("sample_stage") != stage:
            raise CampaignError(f"{run_dir}: decision stage/offset mismatch")
        sample_time = _integer(row.get("sample_time_ns"), f"{run_dir}: sample time")
        if sample_time < previous_time:
            raise CampaignError(f"{run_dir}: adaptive decisions are not chronological")
        previous_time = sample_time
        scorer = expected_scorers[stage]
        for row_key, scorer_key in (
            ("score_name", "score_name"),
            ("score_kind", "score_kind"),
            ("score_model_id", "model_id"),
            ("score_source_model_sha256", "source_model_sha256"),
            ("score_target_provenance_sha256", "target_provenance_sha256"),
            ("score_feature_contract_sha256", "feature_contract_sha256"),
            ("score_combiner_sha256", "combiner_sha256"),
        ):
            if row.get(row_key) != str(scorer.get(scorer_key, "")):
                raise CampaignError(f"{run_dir}: {stage} score provenance mismatch")

        score = _number_from_row(row, "admission_score", run_dir)
        primary = _optional_probability(row, "primary_miss_probability", run_dir)
        tail = _optional_probability(row, "completed_tail_probability", run_dir)
        if not 0 <= score <= 1 or primary is None:
            raise CampaignError(f"{run_dir}: invalid {stage} score")
        if stage == "T0":
            if tail is not None or not _close(score, primary):
                raise CampaignError(f"{run_dir}: invalid T0 probability score")
        elif tail is None or not _close(score, (primary + 0.2 * tail) / 1.2):
            raise CampaignError(f"{run_dir}: invalid T4 weighted score")

        actionable = _flag(row.get("actionable"), f"{run_dir}: actionable")
        admission = _number_from_row(row, "admission_airtime_us", run_dir, nonnegative=True)
        estimated = _number_from_row(row, "estimated_airtime_us", run_dir, nonnegative=True)
        reference = _number_from_row(row, "reference_airtime_us", run_dir)
        shadow = _number_from_row(row, "shadow_price", run_dir)
        dual_shadow = _number_from_row(row, "dual_shadow_price", run_dir)
        normalized = _number_from_row(row, "normalized_cost", run_dir, nonnegative=True)
        if reference <= 0 or not 0 <= shadow <= 1 or not 0 <= dual_shadow <= 1:
            raise CampaignError(f"{run_dir}: invalid gate price or reference airtime")
        if reference_airtime is None:
            reference_airtime = reference
        elif not _close(reference, reference_airtime):
            raise CampaignError(f"{run_dir}: reference airtime changed within a run")
        expected_shadow = offset_prices.get(offset, dual_shadow)
        if shadow != expected_shadow:
            raise CampaignError(f"{run_dir}: effective stage price mismatch")
        expected_source = "offset_override" if offset in offset_prices else "global_dual"
        if row.get("shadow_price_source") != expected_source:
            raise CampaignError(f"{run_dir}: stage price source mismatch")

        utility_text = row.get("net_utility", "")
        try:
            utility = float(utility_text)
        except ValueError as error:
            raise CampaignError(f"{run_dir}: invalid net utility") from error
        if admission > 0:
            if (
                not math.isfinite(utility)
                or not _close(normalized, admission / reference)
                or not _close(utility, score - shadow * normalized)
            ):
                raise CampaignError(f"{run_dir}: utility/gate arithmetic mismatch")
        elif not (_close(normalized, 0.0) and math.isnan(utility)):
            raise CampaignError(f"{run_dir}: descriptor-free decision has invalid utility")

        row_fraction = _number_from_row(row, "airtime_budget_fraction", run_dir)
        row_capacity = _number_from_row(row, "bucket_capacity_us", run_dir)
        balance = _number_from_row(row, "bucket_balance_us", run_dir)
        row_initial = _number_from_row(row, "initial_bucket_capacity_us", run_dir)
        reserved = _number_from_row(row, "reserved_airtime_us", run_dir, nonnegative=True)
        available = _number_from_row(row, "available_airtime_us", run_dir)
        measured = _number_from_row(row, "measured_airtime_total_us", run_dir, nonnegative=True)
        if (
            not _close(row_fraction, float(adaptive["budget_fraction"]))
            or not _close(row_capacity, capacity)
            or not _close(row_initial, initial_capacity)
            or balance > capacity + 1e-9
            or not _close(available, balance - reserved)
            or measured + 1e-9 < previous_measured
        ):
            raise CampaignError(f"{run_dir}: loose-bucket accounting is inconsistent")
        previous_measured = measured
        minimum_available = min(minimum_available, available)
        if offset == 0:
            if last_t0_time is None:
                expected_dual = float(adaptive["initial_shadow_price"])
            else:
                elapsed_us = (sample_time - last_t0_time) / 1000.0
                measured_delta = measured - last_t0_measured
                expected_dual = min(
                    1.0,
                    max(
                        0.0,
                        last_t0_dual
                        + float(adaptive["dual_step"])
                        * (
                            measured_delta
                            - float(adaptive["budget_fraction"]) * elapsed_us
                        )
                        / reference,
                    ),
                )
            if not _close(dual_shadow, expected_dual):
                raise CampaignError(f"{run_dir}: dual shadow-price recurrence mismatch")
            last_t0_time = sample_time
            last_t0_measured = measured
            last_t0_dual = dual_shadow
        elif last_t0_time is None or not _close(dual_shadow, last_t0_dual):
            raise CampaignError(f"{run_dir}: dual shadow price changed outside T0")

        frame_packet_count = _integer(row.get("frame_packet_count"), f"{run_dir}: frame packets")
        if frame_packet_count != frames[frame_id]["packet_count"]:
            raise CampaignError(f"{run_dir}: decision/frame packet count mismatch")
        selected_count = _integer(
            row.get("secondary_packet_count"), f"{run_dir}: secondary packet count"
        )
        selected_indices = _indices(
            row.get("secondary_packet_indices", ""), f"{run_dir}: decision"
        )
        if (
            len(selected_indices) != selected_count
            or any(index >= frame_packet_count for index in selected_indices)
        ):
            raise CampaignError(f"{run_dir}: invalid selected packet set")
        admission_count = _integer(
            row.get("admission_packet_count"), f"{run_dir}: admission packet count"
        )
        configured_cost = adaptive["configured_admission_packet_cost"]
        effective_cost = adaptive["effective_admission_packet_cost"]
        if (
            row.get("configured_admission_packet_cost") != configured_cost
            or row.get("effective_admission_packet_cost") != effective_cost
        ):
            raise CampaignError(f"{run_dir}: configured/effective packet cost mismatch")
        expected_admission_count = frame_packet_count if admission > 0 else selected_count
        if admission_count != expected_admission_count:
            raise CampaignError(f"{run_dir}: whole-copy admission packet count mismatch")

        decision = row.get("decision")
        if decision not in ALLOWED_DECISIONS:
            raise CampaignError(f"{run_dir}: unknown adaptive decision")
        decision_counts[decision] += 1
        launched = _flag(row.get("secondary_launched"), f"{run_dir}: secondary launch")
        if launched != (decision == "action"):
            raise CampaignError(f"{run_dir}: launch/action mismatch")
        if decision == "action":
            if (
                not actionable
                or utility <= 0
                or estimated <= 0
                or available + 1e-9 < estimated
                or frame_id in launched_frames
            ):
                raise CampaignError(f"{run_dir}: invalid or duplicate adaptive action")
            if offset == 0 and frames[frame_id]["frame_type"] != "I_FRAME":
                raise CampaignError(f"{run_dir}: T0 launched a non-I frame")
            if mechanism == "full_copy" and offset == 4000:
                if (
                    admission_count != frame_packet_count
                    or selected_count != frame_packet_count
                    or selected_indices != list(range(frame_packet_count))
                    or row.get("secondary_packet_order") != "full_forward"
                ):
                    raise CampaignError(f"{run_dir}: full-copy T4 action is not a whole frame")
            launched_frames.add(frame_id)
            stage_action_counts[stage] += 1
            estimated_action_total += estimated
        elif decision == "price_rejected":
            if not actionable or estimated <= 0 or utility > 0:
                raise CampaignError(f"{run_dir}: invalid price rejection")
        elif decision == "airtime_deferred":
            if not actionable or estimated <= 0 or utility <= 0 or available + 1e-9 >= estimated:
                raise CampaignError(f"{run_dir}: invalid airtime deferral")
        elif decision == "not_actionable":
            if actionable or estimated <= 0:
                raise CampaignError(f"{run_dir}: invalid not-actionable decision")
        elif decision == "no_primary_deficit":
            if mechanism != "primary_deficit" or estimated != 0 or launched:
                raise CampaignError(f"{run_dir}: invalid no-primary-deficit decision")
        elif decision == "already_resolved":
            if frame_id not in launched_frames or estimated != 0:
                raise CampaignError(f"{run_dir}: invalid already-resolved decision")
        elif decision == "frame_type_restricted":
            if offset != 0 or frames[frame_id]["frame_type"] == "I_FRAME" or launched:
                raise CampaignError(f"{run_dir}: invalid frame-type restriction")
        elif not (actionable and estimated > 0 and utility > 0 and available + 1e-9 >= estimated):
            raise CampaignError(f"{run_dir}: invalid launch rejection")

        trace[key] = {
            "sample_time_ns": sample_time,
            "score": score,
            "price_gate": bool(admission > 0 and utility > 0),
            "pre_budget_gate": bool(actionable and admission > 0 and utility > 0),
            "action": launched,
            "launched_packet_indices": selected_indices if launched else [],
            "launched_packet_order": row.get("secondary_packet_order") if launched else "none",
        }

    expected = {(frame_id, offset) for frame_id in frames for offset in (0, 4000)}
    if seen != expected:
        raise CampaignError(f"{run_dir}: missing adaptive frame/stage decisions")
    meter = _audit_meter(
        run_dir,
        config,
        adaptive,
        estimated_action_total,
        minimum_available,
        thresholds,
    )
    return {
        "decision_row_count": len(rows),
        "action_count": len(launched_frames),
        "stage_action_count": stage_action_counts,
        "decision_counts": decision_counts,
        "minimum_available_airtime_us": minimum_available,
        "bucket_binding_event_count": decision_counts["airtime_deferred"],
        "loose_bucket_unconstrained": decision_counts["airtime_deferred"] == 0,
        "meter": meter,
        "decision_trace": trace,
    }


def _arm_summary(adaptive: dict[str, Any], mechanism: str) -> dict[str, Any]:
    prices = adaptive["decision_offset_shadow_prices"]
    return {
        "mechanism": mechanism,
        "operating_profile": adaptive["operating_profile"],
        "t0_shadow_price": prices.get("0"),
        "t4_shadow_price": prices.get("4000"),
        "budget_fraction": adaptive["budget_fraction"],
        "bucket_horizon_us": adaptive["bucket_horizon_us"],
        "initial_bucket_horizon_us": adaptive["initial_bucket_horizon_us"],
        "configured_admission_packet_cost": adaptive[
            "configured_admission_packet_cost"
        ],
        "effective_admission_packet_cost": adaptive[
            "effective_admission_packet_cost"
        ],
        "admission_uses_retry_inflation": adaptive[
            "admission_uses_retry_inflation"
        ],
        "dual_step": adaptive["dual_step"],
        "cost_safety_factor": adaptive["cost_safety_factor"],
        "cost_ewma_alpha": adaptive["cost_ewma_alpha"],
        "stage_scorers": copy.deepcopy(adaptive["stage_scorers"]),
        "controller_config_sha256": _sha256_json(adaptive),
    }


def _observation(
    run: dict[str, Any],
    run_dir: Path,
    config: dict[str, Any],
    classification: tuple[str, str | None, dict[str, Any] | None, str | None],
    manifest: dict[str, Any],
    thresholds: Thresholds,
) -> dict[str, Any]:
    kind, arm_id, adaptive, _ = classification
    pair = _pair_key(run, config, run_dir)
    headline = _validated_headline_metrics(
        run,
        run_dir,
        config,
        manifest,
    )
    miss = headline["deadline_miss_ratio"]
    p99 = headline["completed_frame_p99_us"]
    background = headline["background_throughput_mbps"]
    if miss > 1:
        raise CampaignError(f"{run_dir}: deadline miss ratio exceeds one")
    _validate_topology_wifi(config, run_dir)
    environment = _environment(config)
    if _canonical_json(
        _environment_without_generated_geometry(environment)
    ) != _canonical_json(DECLARED_NEUTRAL_ENVIRONMENT):
        raise CampaignError(f"{run_dir}: environment differs from declared neutral campaign")
    result = {
        "run_id": run["run_id"],
        "run_dir": str(run_dir),
        "pair": pair,
        "kind": kind,
        "arm_id": arm_id,
        "config": config,
        "environment": environment,
        "nominal_config": _nominal_config(config),
        "build_identity": _build_identity(run_dir),
        "deadline_miss_ratio": miss,
        "completed_frame_p99_us": p99,
        "background_throughput_mbps": background,
        "sender_airtime_us": _sender_airtime_us(run_dir),
        "evidence_validation": {
            "core_run_validation": headline["core_run_validation"],
            "headline_metrics_validated_against_raw_evidence": headline[
                "headline_metrics_validated_against_raw_evidence"
            ],
            "frame_csv_completed_p99_us": headline[
                "frame_csv_completed_p99_us"
            ],
            "frame_csv_p99_quantization_delta_us": headline[
                "frame_csv_p99_quantization_delta_us"
            ],
            "frame_csv_p99_quantization_bound_us": headline[
                "frame_csv_p99_quantization_bound_us"
            ],
        },
    }
    if adaptive is not None:
        result["adaptive_config"] = adaptive
        result["controller_audit"] = _audit_adaptive(
            run_dir, config, adaptive, kind, thresholds
        )
    return result


def _comparison_seed(arm_id: str, baseline: str) -> int:
    digest = hashlib.sha256(f"{arm_id}:{baseline}".encode("utf-8")).digest()
    return BOOTSTRAP_SEED ^ int.from_bytes(digest[:8], "big")


def _mean_delta(left: list[float], right: list[float]) -> float:
    return statistics.mean(
        left_value - right_value
        for left_value, right_value in zip(left, right)
    )


def _ratio_of_means(left: list[float], right: list[float]) -> float:
    denominator = statistics.mean(right)
    if denominator <= 0:
        raise CampaignError("relative comparison has a nonpositive baseline mean")
    return statistics.mean(left) / denominator


def _background_loss(left: list[float], right: list[float]) -> float:
    return 1.0 - _ratio_of_means(left, right)


def _criterion(passed: bool, rule: str, observed: Any, threshold: Any) -> dict[str, Any]:
    return {
        "status": "pass" if passed else "fail",
        "rule": rule,
        "observed": observed,
        "threshold": threshold,
    }


def _compare(
    adaptive: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
    arm_id: str,
    baseline_name: str,
    thresholds: Thresholds,
    bootstrap_replicates: int,
) -> dict[str, Any]:
    seed = _comparison_seed(arm_id, baseline_name)
    miss_left = [row["deadline_miss_ratio"] for row in adaptive]
    miss_right = [row["deadline_miss_ratio"] for row in baseline]
    p99_left = [row["completed_frame_p99_us"] for row in adaptive]
    p99_right = [row["completed_frame_p99_us"] for row in baseline]
    airtime_left = [row["sender_airtime_us"] for row in adaptive]
    airtime_right = [row["sender_airtime_us"] for row in baseline]
    background_left = [row["background_throughput_mbps"] for row in adaptive]
    background_right = [row["background_throughput_mbps"] for row in baseline]
    miss = paired_bootstrap(
        miss_left, miss_right, _mean_delta, replicates=bootstrap_replicates, seed=seed
    )
    p99 = paired_bootstrap(
        p99_left, p99_right, _mean_delta, replicates=bootstrap_replicates, seed=seed + 1
    )
    airtime = paired_bootstrap(
        airtime_left,
        airtime_right,
        _ratio_of_means,
        replicates=bootstrap_replicates,
        seed=seed + 2,
    )
    background = paired_bootstrap(
        background_left,
        background_right,
        _background_loss,
        replicates=bootstrap_replicates,
        seed=seed + 3,
    )
    miss_wins = sum(left < right for left, right in zip(miss_left, miss_right))
    p99_wins = sum(left < right for left, right in zip(p99_left, p99_right))
    expected_count = thresholds.expected_pair_count
    criteria = {
        "complete_expected_pair_count": _criterion(
            len(adaptive) == expected_count,
            "paired unit count equals the predeclared campaign size",
            len(adaptive),
            expected_count,
        ),
        "deadline_miss_delta_ci_below_zero": _criterion(
            miss["ci95_high"] < 0,
            "upper paired-bootstrap 95% CI of adaptive-minus-MLO miss delta < 0",
            miss["ci95_high"],
            0.0,
        ),
        "deadline_miss_strict_wins": _criterion(
            len(adaptive) == expected_count and miss_wins >= thresholds.minimum_strict_wins,
            "strict paired miss wins >= predeclared count",
            miss_wins,
            thresholds.minimum_strict_wins,
        ),
        "completed_p99_delta_ci_below_zero": _criterion(
            p99["ci95_high"] < 0,
            "upper paired-bootstrap 95% CI of adaptive-minus-MLO P99 delta < 0",
            p99["ci95_high"],
            0.0,
        ),
        "completed_p99_strict_wins": _criterion(
            len(adaptive) == expected_count and p99_wins >= thresholds.minimum_strict_wins,
            "strict paired completed-P99 wins >= predeclared count",
            p99_wins,
            thresholds.minimum_strict_wins,
        ),
        "summed_sender_airtime_ratio": _criterion(
            airtime["ci95_high"] <= thresholds.maximum_airtime_ratio,
            "upper paired-bootstrap 95% CI of summed sender PHY-airtime ratio <= limit",
            airtime["ci95_high"],
            thresholds.maximum_airtime_ratio,
        ),
        "background_throughput_loss": _criterion(
            background["ci95_high"] <= thresholds.maximum_background_loss,
            "upper paired-bootstrap 95% CI of background-throughput loss <= limit",
            background["ci95_high"],
            thresholds.maximum_background_loss,
        ),
    }
    return {
        "baseline": baseline_name,
        "paired_unit_count": len(adaptive),
        "deadline_miss_ratio": {
            "adaptive_minus_mlo_delta": miss,
            "strict_win_count": miss_wins,
        },
        "completed_frame_p99_us": {
            "adaptive_minus_mlo_delta": p99,
            "strict_win_count": p99_wins,
        },
        "summed_sender_phy_airtime_ratio": airtime,
        "background_throughput_loss_fraction": background,
        "criteria": criteria,
        "status": (
            "pass"
            if all(item["status"] == "pass" for item in criteria.values())
            else "fail"
        ),
    }


ISO_IGNORED_CONFIG_FIELDS = {
    "packet_selection_feature_set",
    "packet_selection",
    "operating_profile",
    "configured_admission_packet_cost",
}


def _iso_config(adaptive: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in adaptive.items()
        if key not in ISO_IGNORED_CONFIG_FIELDS
    }


def _iso_resolved_config(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize only the declared full-versus-deficit mechanism differences."""
    result = copy.deepcopy(row["config"])
    for field in ("run_id", "seed", "run", "topology", "policy"):
        result.pop(field, None)
    adaptive = None
    for field in ("adaptiveAirtimeDuplication", "adaptiveDeficitDuplication"):
        value = result.pop(field, None)
        if value is not None:
            if adaptive is not None:
                raise CampaignError("ISO run contains both adaptive controller objects")
            adaptive = value
    if not isinstance(adaptive, dict):
        raise CampaignError("ISO run is missing its adaptive controller object")
    result["adaptiveController"] = _iso_config(adaptive)
    return result


def _iso_difference(
    full: list[dict[str, Any]], deficit: list[dict[str, Any]]
) -> dict[str, Any]:
    totals = {
        "compared_decision_count": 0,
        "post_divergence_decision_count": 0,
        "score_difference_count": 0,
        "price_gate_difference_count": 0,
        "pre_budget_gate_difference_count": 0,
        "action_difference_count": 0,
        "launched_packet_selection_or_order_difference_count": 0,
    }
    by_stage = {
        stage: {key: 0 for key in totals}
        for stage in ("T0", "T4")
    }
    first_mechanism_divergences: list[dict[str, Any]] = []
    first_divergence_stage_counts = {"T0": 0, "T4": 0}
    for full_row, deficit_row in zip(full, deficit):
        if full_row["pair"] != deficit_row["pair"]:
            raise CampaignError("internal error: ISO rows are not paired")
        full_trace = full_row["controller_audit"]["decision_trace"]
        deficit_trace = deficit_row["controller_audit"]["decision_trace"]
        if set(full_trace) != set(deficit_trace):
            raise CampaignError("ISO mechanisms have different decision sample keys")
        mechanism_diverged = False
        for key in sorted(full_trace, key=lambda item: full_trace[item]["sample_time_ns"]):
            offset = key[1]
            stage = f"T{offset // 1000}"
            left = full_trace[key]
            right = deficit_trace[key]
            totals["compared_decision_count"] += 1
            by_stage[stage]["compared_decision_count"] += 1
            packet_selection_or_order_differs = (
                left["action"]
                and right["action"]
                and (
                    left["launched_packet_indices"] != right["launched_packet_indices"]
                    or left["launched_packet_order"] != right["launched_packet_order"]
                )
            )
            action_mechanism_differs = left["action"] != right["action"]
            if not mechanism_diverged:
                # At entry to the first mechanism-changing decision, both
                # controllers still have the same causal history. Scores and
                # both price gates therefore must agree even when packet
                # selection makes the launches at this sample differ.
                if (
                    left["score"] != right["score"]
                    or left["price_gate"] != right["price_gate"]
                    or left["pre_budget_gate"] != right["pre_budget_gate"]
                ):
                    raise CampaignError(
                        f"seed/run {full_row['pair']} full/deficit score or gate "
                        "differs before causal mechanism divergence"
                    )
                if action_mechanism_differs or packet_selection_or_order_differs:
                    mechanism_diverged = True
                    first_divergence_stage_counts[stage] += 1
                    seed, run_number = full_row["pair"]
                    first_mechanism_divergences.append({
                        "seed": seed,
                        "run": run_number,
                        "frame_id": key[0],
                        "sample_offset_us": key[1],
                        "sample_time_ns": min(
                            left["sample_time_ns"], right["sample_time_ns"]
                        ),
                        "launch_decision_differs": action_mechanism_differs,
                        "launched_packet_selection_or_order_differs": (
                            packet_selection_or_order_differs
                        ),
                    })
                continue

            totals["post_divergence_decision_count"] += 1
            by_stage[stage]["post_divergence_decision_count"] += 1
            for field, counter in (
                ("price_gate", "price_gate_difference_count"),
                ("pre_budget_gate", "pre_budget_gate_difference_count"),
                ("action", "action_difference_count"),
            ):
                if left[field] != right[field]:
                    totals[counter] += 1
                    by_stage[stage][counter] += 1
            if not _close(left["score"], right["score"]):
                totals["score_difference_count"] += 1
                by_stage[stage]["score_difference_count"] += 1
            if packet_selection_or_order_differs:
                totals["launched_packet_selection_or_order_difference_count"] += 1
                by_stage[stage][
                    "launched_packet_selection_or_order_difference_count"
                ] += 1
    return {
        **totals,
        "by_stage": by_stage,
        "mechanism_iso_scope": "whole_policy_from_first_intervention_not_t4_only",
        "paired_units_with_causal_mechanism_divergence": len(
            first_mechanism_divergences
        ),
        "first_causal_mechanism_divergence_stage_counts": (
            first_divergence_stage_counts
        ),
        "first_causal_mechanism_divergence_by_unit": first_mechanism_divergences,
        "interpretation": (
            "Score, price-gate, and pre-budget-gate identity is required through "
            "entry to the first packet-selection/order or action divergence. This "
            "is a whole-policy mechanism audit, not a T4-only ablation: the two "
            "policies can first intervene differently at T0. Differences after "
            "that boundary are descriptive because causal state has diverged."
        ),
    }


def analyze_campaign(
    inputs: Path | Iterable[Path],
    thresholds: Thresholds = Thresholds(),
    *,
    bootstrap_replicates: int = 20_000,
    expected_obss_profile: str | None = "mixed4x4",
    preflight: bool = False,
) -> dict[str, Any]:
    """Validate, pair, audit, and compare one neutral multi-arm T4 campaign."""
    paths = [inputs] if isinstance(inputs, Path) else list(inputs)
    if not paths:
        raise CampaignError("at least one campaign result root is required")
    expected_pairs = DECLARED_PREFLIGHT_PAIRS if preflight else DECLARED_PAIRS
    expected_pair_count = 1 if preflight else 12
    expected_strict_wins = 1 if preflight else 9
    if (
        thresholds.expected_pair_count != expected_pair_count
        or thresholds.minimum_strict_wins < expected_strict_wins
        or thresholds.maximum_airtime_ratio > 1.20
        or thresholds.maximum_background_loss > 0.01
        or thresholds.maximum_budget_excess_us > 1.0
    ):
        raise CampaignError(
            "thresholds weaken the declared preflight criteria"
            if preflight
            else "thresholds weaken the declared 12-pair neutral-campaign criteria"
        )
    source_artifact_sha256 = _validate_declared_source_artifacts(preflight)
    aggregates = [_resolve_aggregate(path) for path in paths]
    manifest_identities: list[dict[str, Any]] = []
    baselines: dict[str, dict[tuple[int, int], dict[str, Any]]] = {
        name: {} for name in BASELINES
    }
    arms: dict[str, dict[str, Any]] = {}
    ignored = 0
    for aggregate_path in aggregates:
        aggregate = _read_json(aggregate_path)
        runs = aggregate.get("runs")
        if not isinstance(runs, list):
            raise CampaignError(f"{aggregate_path}: aggregate must contain a runs list")
        manifest_identity = _validate_manifest(
            aggregate_path,
            aggregate,
            preflight=preflight,
        )
        manifest_identities.append(manifest_identity)
        for run in runs:
            if not isinstance(run, dict):
                raise CampaignError(f"{aggregate_path}: run entry is not an object")
            run_dir = _run_directory(run, aggregate_path)
            config = _read_config(run, run_dir)
            classification = _classify(config, run, run_dir)
            if classification is None:
                ignored += 1
                continue
            observation = _observation(
                run,
                run_dir,
                config,
                classification,
                manifest_identity,
                thresholds,
            )
            observation["source_aggregate"] = str(aggregate_path)
            kind, arm_id, adaptive, _ = classification
            pair = observation["pair"]
            if kind in BASELINES:
                index = baselines[kind]
                if pair in index:
                    raise CampaignError(f"duplicate {kind} baseline for seed/run {pair}")
                index[pair] = observation
            else:
                assert arm_id is not None and adaptive is not None
                arm = arms.setdefault(
                    arm_id,
                    {
                        "mechanism": kind,
                        "adaptive_config": adaptive,
                        "settings": _arm_summary(adaptive, kind),
                        "runs": {},
                    },
                )
                if pair in arm["runs"]:
                    raise CampaignError(f"duplicate {arm_id} treatment for seed/run {pair}")
                arm["runs"][pair] = observation

    if any(not index for index in baselines.values()):
        missing = [name for name, index in baselines.items() if not index]
        raise CampaignError(f"campaign is missing baseline(s): {', '.join(missing)}")
    if not any(arm["mechanism"] == "full_copy" for arm in arms.values()):
        raise CampaignError("campaign has no exact staged full-copy T4 arm")
    if set(arms) != DECLARED_ARM_IDS:
        raise CampaignError(
            "campaign adaptive-arm set differs from declaration: "
            f"missing {sorted(DECLARED_ARM_IDS - set(arms))}; "
            f"extra {sorted(set(arms) - DECLARED_ARM_IDS)}"
        )
    all_pairs = set(next(iter(baselines.values())))
    matrix_indexes: list[tuple[str, set[tuple[int, int]]]] = [
        (name, set(index)) for name, index in baselines.items()
    ] + [(arm_id, set(arm["runs"])) for arm_id, arm in arms.items()]
    if any(pairs != all_pairs for _, pairs in matrix_indexes):
        details = [
            f"{name} missing {sorted(all_pairs - pairs)} extra {sorted(pairs - all_pairs)}"
            for name, pairs in matrix_indexes
            if pairs != all_pairs
        ]
        raise CampaignError("incomplete paired arm matrix: " + "; ".join(details))
    pairs = sorted(all_pairs)
    if all_pairs != expected_pairs:
        raise CampaignError(
            f"campaign seed/run set differs from {'preflight' if preflight else 'declaration'}: "
            f"expected {sorted(expected_pairs)}, found {pairs}"
        )

    every_index = [*baselines.values(), *(arm["runs"] for arm in arms.values())]
    build_identities = {
        _canonical_json(row["build_identity"])
        for index in every_index
        for row in index.values()
    }
    if len(build_identities) != 1:
        raise CampaignError("campaign mixes build identities")
    build_identity = json.loads(next(iter(build_identities)))
    for manifest in manifest_identities:
        if (
            manifest["project_commit"] != build_identity["project_git_commit"]
            or manifest["ns3_upstream_commit"]
            != build_identity["ns3_upstream_commit"]
        ):
            raise CampaignError(
                f"{manifest['path']}: manifest commit identity disagrees with run builds"
            )
    for pair in pairs:
        environments = {
            _canonical_json(index[pair]["environment"]) for index in every_index
        }
        if len(environments) != 1:
            raise CampaignError(f"seed/run {pair} mixes environment realizations")
        if expected_obss_profile is not None:
            profile = next(iter(every_index))[pair]["environment"]["background"].get(
                "obss", {}
            ).get("profile")
            if profile != expected_obss_profile:
                raise CampaignError(
                    f"seed/run {pair} expected OBSS profile {expected_obss_profile!r}, "
                    f"found {profile!r}"
                )

    nominal_hashes: dict[str, str] = {}
    named_indexes = [
        *((name, index) for name, index in baselines.items()),
        *((arm_id, arm["runs"]) for arm_id, arm in arms.items()),
    ]
    for name, index in named_indexes:
        hashes = {_sha256_json(row["nominal_config"]) for row in index.values()}
        if len(hashes) != 1:
            raise CampaignError(f"{name} nominal configuration changed within campaign")
        nominal_hashes[name] = next(iter(hashes))

    ordered_baselines = {
        name: [index[pair] for pair in pairs] for name, index in baselines.items()
    }
    comparisons: dict[str, Any] = {}
    arm_reports: dict[str, Any] = {}
    for arm_id in sorted(arms):
        arm = arms[arm_id]
        ordered = [arm["runs"][pair] for pair in pairs]
        audit = {
            "run_count": len(ordered),
            "decision_rows": sum(
                row["controller_audit"]["decision_row_count"] for row in ordered
            ),
            "actions": sum(row["controller_audit"]["action_count"] for row in ordered),
            "t0_actions": sum(
                row["controller_audit"]["stage_action_count"]["T0"] for row in ordered
            ),
            "t4_actions": sum(
                row["controller_audit"]["stage_action_count"]["T4"] for row in ordered
            ),
            "bucket_binding_events": sum(
                row["controller_audit"]["bucket_binding_event_count"] for row in ordered
            ),
            "all_runs_loose_bucket_unconstrained": all(
                row["controller_audit"]["loose_bucket_unconstrained"] for row in ordered
            ),
            "maximum_budget_excess_us": max(
                row["controller_audit"]["meter"]["budget_excess_us"] for row in ordered
            ),
            "maximum_budget_debt_us": max(
                row["controller_audit"]["meter"]["maximum_budget_debt_us"] for row in ordered
            ),
        }
        arm_reports[arm_id] = {
            "settings": arm["settings"],
            "controller_audit": audit,
        }
        if arm["mechanism"] == "full_copy":
            comparisons[arm_id] = {
                baseline: _compare(
                    ordered,
                    ordered_baselines[baseline],
                    arm_id,
                    baseline,
                    thresholds,
                    bootstrap_replicates,
                )
                for baseline in BASELINES
            }

    iso_pairs: list[dict[str, Any]] = []
    full_by_iso: dict[str, list[str]] = {}
    for arm_id, arm in arms.items():
        if arm["mechanism"] == "full_copy":
            full_by_iso.setdefault(_sha256_json(_iso_config(arm["adaptive_config"])), []).append(
                arm_id
            )
    for deficit_id, deficit in sorted(arms.items()):
        if deficit["mechanism"] != "primary_deficit":
            continue
        iso_key = _sha256_json(_iso_config(deficit["adaptive_config"]))
        matches = full_by_iso.get(iso_key, [])
        if len(matches) != 1:
            raise CampaignError(
                f"deficit arm {deficit_id} has {len(matches)} full-copy ISO matches; expected one"
            )
        full_id = matches[0]
        full_ordered = [arms[full_id]["runs"][pair] for pair in pairs]
        deficit_ordered = [deficit["runs"][pair] for pair in pairs]
        for full_row, deficit_row in zip(full_ordered, deficit_ordered):
            if _canonical_json(_iso_resolved_config(full_row)) != _canonical_json(
                _iso_resolved_config(deficit_row)
            ):
                raise CampaignError(
                    f"seed/run {full_row['pair']} full/deficit ISO configuration mismatch"
                )
        iso_pairs.append({
            "full_copy_arm_id": full_id,
            "primary_deficit_arm_id": deficit_id,
            "mechanism_iso_scope": (
                "whole_policy_from_first_intervention_not_t4_only"
            ),
            "configuration_identity_verified": True,
            "ignored_mechanism_fields": sorted(ISO_IGNORED_CONFIG_FIELDS),
            "decision_differences": _iso_difference(full_ordered, deficit_ordered),
        })

    full_statuses = [
        comparison[baseline]["status"]
        for comparison in comparisons.values()
        for baseline in BASELINES
    ]
    return {
        "schema_version": 1,
        "analysis": "neutral_primary_tail_t4_multi_arm_vs_mlo",
        "independent_sample_unit": ["seed", "run"],
        "confidence_interval": (
            "deterministic two-sided 95% paired percentile bootstrap over seed/run units"
        ),
        "paired_unit_count": len(pairs),
        "paired_units": [
            {"seed": seed, "run": run_number} for seed, run_number in pairs
        ],
        "source_aggregates": [str(path) for path in aggregates],
        "ignored_noncampaign_run_count": ignored,
        "preflight": preflight,
        "campaign_checks": {
            "complete_paired_arm_matrix": True,
            "paired_environment_realizations_match": True,
            "single_build_identity": True,
            "build_identity": build_identity,
            "manifest_identity_verified": True,
            "all_runs_core_validated": True,
            "all_headline_metrics_validated_against_raw_evidence": True,
            "frame_csv_p99_quantization_bound_us": (
                MAX_FRAME_CSV_P99_QUANTIZATION_US
            ),
            "maximum_observed_frame_csv_p99_quantization_delta_us": max(
                row["evidence_validation"][
                    "frame_csv_p99_quantization_delta_us"
                ]
                for index in every_index
                for row in index.values()
            ),
            "manifests": manifest_identities,
            "declared_source_artifact_sha256": source_artifact_sha256,
            "nominal_config_sha256": nominal_hashes,
            "expected_obss_profile": expected_obss_profile,
            "declared_seed_run_units": [
                {"seed": seed, "run": run_number}
                for seed, run_number in sorted(expected_pairs)
            ],
            "declared_full_copy_t4_gates": copy.deepcopy(DECLARED_FULL_T4_GATES),
            "declared_serialized_full_copy_t4_gates": copy.deepcopy(
                DECLARED_SERIALIZED_T4_GATES
            ),
            "declared_balanced_deficit_t4_gate": DECLARED_FULL_T4_GATES[
                DECLARED_BALANCED_ARM
            ],
            "declared_neutral_environment_sha256": _sha256_json(
                DECLARED_NEUTRAL_ENVIRONMENT
            ),
            "arm_count": len(arms),
            "full_copy_arm_count": len(comparisons),
            "primary_deficit_arm_count": sum(
                arm["mechanism"] == "primary_deficit" for arm in arms.values()
            ),
            "accepted_runtime_stage_scorers": copy.deepcopy(
                next(iter(arms.values()))["adaptive_config"]["stage_scorers"]
            ),
        },
        "thresholds": {
            "expected_pair_count": thresholds.expected_pair_count,
            "minimum_strict_wins": thresholds.minimum_strict_wins,
            "maximum_summed_sender_airtime_ratio": thresholds.maximum_airtime_ratio,
            "maximum_background_throughput_loss_fraction": thresholds.maximum_background_loss,
            "maximum_budget_excess_us": thresholds.maximum_budget_excess_us,
        },
        "arms": arm_reports,
        "comparisons": comparisons,
        "mechanism_iso_pairs": iso_pairs,
        "overall_status": {
            "all_full_copy_comparisons_pass": bool(full_statuses) and all(
                status == "pass" for status in full_statuses
            ),
            "passing_full_copy_arms": [
                arm_id
                for arm_id, comparison in comparisons.items()
                if all(comparison[baseline]["status"] == "pass" for baseline in BASELINES)
            ],
        },
    }


def _interval(item: dict[str, Any], scale: float = 1.0, digits: int = 3) -> str:
    return (
        f"{item['estimate'] * scale:.{digits}f} "
        f"[{item['ci95_low'] * scale:.{digits}f}, "
        f"{item['ci95_high'] * scale:.{digits}f}]"
    )


def render_markdown(report: dict[str, Any]) -> str:
    """Render a concise review-oriented campaign report."""
    maximum_p99_quantization = report["campaign_checks"][
        "maximum_observed_frame_csv_p99_quantization_delta_us"
    ]
    lines = [
        "# Primary-tail T4 neutral campaign",
        "",
        f"Paired units: {report['paired_unit_count']}; full-copy arms: "
        f"{report['campaign_checks']['full_copy_arm_count']}; deficit ISO arms: "
        f"{report['campaign_checks']['primary_deficit_arm_count']}.",
        "Declared neutral environment SHA-256: `"
        f"{report['campaign_checks']['declared_neutral_environment_sha256']}`.",
        "Manifest, campaign configuration, operating profile, evaluator, and "
        "offline-profile diagnostics identities were verified.",
        "Every run passed core validation; miss rate and background throughput "
        "were reconstructed exactly from raw records. Completed-frame P99 uses "
        "the validated summary's sub-microsecond timestamps and agrees with the "
        "integer-microsecond frame CSV within the declared 1 us quantization bound "
        f"(maximum observed {maximum_p99_quantization:.6f} us).",
        "",
        "Accepted runtime scorer identity (shared by every adaptive arm):",
        "",
        "```json",
        json.dumps(
            report["campaign_checks"]["accepted_runtime_stage_scorers"],
            indent=2,
            sort_keys=True,
        ),
        "```",
        "",
    ]
    for arm_id, baselines in report["comparisons"].items():
        settings = report["arms"][arm_id]["settings"]
        audit = report["arms"][arm_id]["controller_audit"]
        lines += [
            f"## `{arm_id}`",
            "",
            f"T0 price {settings['t0_shadow_price']}; T4 price "
            f"{settings['t4_shadow_price']}; measured-airtime budget "
            f"{100 * settings['budget_fraction']:.2f}%. Controller actions "
            f"T0/T4: {audit['t0_actions']}/{audit['t4_actions']}; bucket deferrals: "
            f"{audit['bucket_binding_events']}.",
            "",
            "| Baseline | Miss delta pp (95% CI), wins | P99 delta ms (95% CI), wins | "
            "Airtime ratio (95% CI) | Background loss (95% CI) | Status |",
            "|---|---:|---:|---:|---:|:---:|",
        ]
        for baseline in BASELINES:
            result = baselines[baseline]
            miss = result["deadline_miss_ratio"]
            p99 = result["completed_frame_p99_us"]
            lines.append(
                f"| {baseline} | {_interval(miss['adaptive_minus_mlo_delta'], 100, 3)}, "
                f"{miss['strict_win_count']}/{result['paired_unit_count']} | "
                f"{_interval(p99['adaptive_minus_mlo_delta'], 0.001, 3)}, "
                f"{p99['strict_win_count']}/{result['paired_unit_count']} | "
                f"{_interval(result['summed_sender_phy_airtime_ratio'], 1, 3)} | "
                f"{_interval(result['background_throughput_loss_fraction'], 100, 2)}% | "
                f"{result['status'].upper()} |"
            )
        lines.append("")
    if report["mechanism_iso_pairs"]:
        lines += [
            "## Whole-policy full-copy versus deficit ISO audit",
            "",
            "This is an ISO audit from entry through the first causal "
            "intervention, not a T4-only ablation; the policies may first "
            "diverge at T0.",
            "",
            "| Full arm | Deficit arm | First boundary T0/T4 | Post-boundary "
            "score differences | Post-boundary gate differences | Post-boundary "
            "action differences |",
            "|---|---|---:|---:|---:|---:|",
        ]
        for item in report["mechanism_iso_pairs"]:
            differences = item["decision_differences"]
            stages = differences["first_causal_mechanism_divergence_stage_counts"]
            lines.append(
                f"| `{item['full_copy_arm_id']}` | `{item['primary_deficit_arm_id']}` | "
                f"{stages['T0']}/{stages['T4']} | "
                f"{differences['score_difference_count']} | "
                f"{differences['pre_budget_gate_difference_count']} | "
                f"{differences['action_difference_count']} |"
            )
        lines.append("")
    passing = report["overall_status"]["passing_full_copy_arms"]
    lines.append(
        "Passing against both MLO modes: "
        + (", ".join(f"`{arm}`" for arm in passing) if passing else "none")
        + "."
    )
    return "\n".join(lines) + "\n"


def _positive_integer(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return result


def _fraction(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise argparse.ArgumentTypeError("expected a fraction in [0, 1]")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs", nargs="+", type=Path, help="result roots, run roots, or aggregate files"
    )
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    parser.add_argument("--bootstrap-replicates", type=_positive_integer, default=20_000)
    parser.add_argument("--expected-pair-count", type=_positive_integer, default=12)
    parser.add_argument("--minimum-strict-wins", type=int, default=9)
    parser.add_argument("--maximum-airtime-ratio", type=float, default=1.20)
    parser.add_argument("--maximum-background-loss", type=_fraction, default=0.01)
    parser.add_argument("--maximum-budget-excess-us", type=float, default=1.0)
    parser.add_argument("--expected-obss-profile", default="mixed4x4")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="require the declared seven arms for seed 43/run 1 only",
    )
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="exit 1 unless at least one full-copy arm passes against both MLO modes",
    )
    args = parser.parse_args()
    try:
        thresholds = Thresholds(
            expected_pair_count=1 if args.preflight else args.expected_pair_count,
            minimum_strict_wins=1 if args.preflight else args.minimum_strict_wins,
            maximum_airtime_ratio=args.maximum_airtime_ratio,
            maximum_background_loss=args.maximum_background_loss,
            maximum_budget_excess_us=args.maximum_budget_excess_us,
        )
        report = analyze_campaign(
            args.inputs,
            thresholds,
            bootstrap_replicates=args.bootstrap_replicates,
            expected_obss_profile=args.expected_obss_profile,
            preflight=args.preflight,
        )
    except (CampaignError, ValidationError, ValueError, json.JSONDecodeError, OSError) as error:
        parser.error(str(error))
    serialized = json.dumps(report, indent=2, sort_keys=True) + "\n"
    markdown = render_markdown(report)
    if args.json_output is not None:
        args.json_output.write_text(serialized, encoding="utf-8")
    if args.markdown_output is not None:
        args.markdown_output.write_text(markdown, encoding="utf-8")
    print(serialized if args.format == "json" else markdown, end="")
    if args.require_pass and not report["overall_status"]["passing_full_copy_arms"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
