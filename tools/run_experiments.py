#!/usr/bin/env python3
"""Expand a YAML experiment matrix and run validated ns-3 jobs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

import yaml

from plot_results import plot
from plot_ofdma_comparison import plot_ofdma_comparison
from plot_selective_duplication import plot_selective_control
from plot_adaptive_airtime_duplication import plot_adaptive_airtime
from summarize_runs import summarize, write_outputs
from validate_outputs import validate_run

ROOT = Path(__file__).resolve().parents[1]
NS3_UPSTREAM_COMMIT = "d2add90b452d600cfb4859baed8e9ea633519447"
MANIFEST_SCHEMA_VERSION = 2
PREDICTION_SCHEMA_VERSIONS = {
    "telemetry_schema_version": 3,
    "polling_schema_version": 1,
    "event_schema_version": 2,
    "feature_support_mask_version": 2,
}
CLI_KEYS = {
    "duration": "duration", "fps": "fps", "frame_size": "frameSize",
    "gop_length": "gopLength", "keyframe_size_multiplier": "keyframeSizeMultiplier",
    "payload_size": "payloadSize", "deadline_us": "deadlineUs",
    "fixed_rss_dbm": "fixedRssDbm", "station_distance_m": "stationDistanceM",
    "propagation_model": "propagationModel",
    "path_loss_exponent": "pathLossExponent",
    "reference_loss_2_4_ghz_db": "referenceLoss2GhzDb",
    "reference_loss_5_ghz_db": "referenceLoss5GhzDb",
    "nakagami_distance_1_m": "nakagamiDistance1M",
    "nakagami_distance_2_m": "nakagamiDistance2M",
    "nakagami_m0": "nakagamiM0", "nakagami_m1": "nakagamiM1",
    "nakagami_m2": "nakagamiM2",
    "propagation_stream_base": "propagationStreamBase",
    "emission_mode": "emissionMode",
    "source": "source", "trace_file": "traceFile", "wifi_standard": "wifiStandard",
    "queue_max_packets": "queueMaxPackets", "queue_max_delay_ms": "queueMaxDelayMs",
    "mlo_sta_max_inflights": "mloStaMaxInflights",
    "ul_ofdma_enabled": "ulOfdmaEnabled", "ul_ofdma_scope": "ulOfdmaScope",
    "ul_ofdma_access_interval_ms": "ulOfdmaAccessIntervalMs",
    "ul_ofdma_bsrp_enabled": "ulOfdmaBsrpEnabled",
    "ul_ofdma_max_stations": "ulOfdmaMaxStations",
    "ul_ofdma_psdu_size": "ulOfdmaPsduSize",
    "max_ampdu_size": "maxAmpduSize", "max_amsdu_size": "maxAmsduSize",
    "frame_retry_limit": "frameRetryLimit", "txop_limit_us": "txopLimitUs",
    "rts_cts_threshold": "rtsCtsThreshold",
    "fragmentation_threshold": "fragmentationThreshold",
    "guard_interval_ns": "guardIntervalNs", "static_link_0_score": "staticLink0Score",
    "static_link_1_score": "staticLink1Score", "background_traffic": "backgroundTraffic",
    "background_profile": "backgroundProfile",
    "background_direction": "backgroundDirection",
    "background_stations_0": "backgroundStations0",
    "background_stations_1": "backgroundStations1",
    "background_rate_mbps": "backgroundRateMbps",
    "background_packet_size": "backgroundPacketSize",
    "background_standard_0": "backgroundStandard0",
    "background_standard_1": "backgroundStandard1",
    "correlation_mode": "correlationMode", "correlation_trace": "correlationTrace",
    "background_stream_base": "backgroundStreamBase",
    "common_on_mean_ms": "commonOnMeanMs", "common_off_mean_ms": "commonOffMeanMs",
    "local_on_mean_ms": "localOnMeanMs", "local_off_mean_ms": "localOffMeanMs",
    "common_on_duration_ms": "commonOnDurationMs",
    "common_off_duration_ms": "commonOffDurationMs",
    "local_on_duration_ms": "localOnDurationMs",
    "local_off_duration_ms": "localOffDurationMs",
    "random_on_mean_ms": "randomOnMeanMs",
    "random_off_mean_ms": "randomOffMeanMs",
    "obss_profile": "obssProfile",
    "obss_stations_per_bss": "obssStationsPerBss",
    "obss_ul_min_rate_mbps": "obssUlMinRateMbps",
    "obss_ul_max_rate_mbps": "obssUlMaxRateMbps",
    "obss_dl_min_rate_mbps": "obssDlMinRateMbps",
    "obss_dl_max_rate_mbps": "obssDlMaxRateMbps",
    "obss_on_mean_ms": "obssOnMeanMs",
    "obss_off_mean_ms": "obssOffMeanMs",
    "obss_station_manager": "obssStationManager",
    "obss_manager_update_ms": "obssManagerUpdateMs",
    "obss_packet_size": "obssPacketSize",
    "obss_area_min_x_m": "obssAreaMinXM",
    "obss_area_max_x_m": "obssAreaMaxXM",
    "obss_area_min_y_m": "obssAreaMinYM",
    "obss_area_max_y_m": "obssAreaMaxYM",
    "obss_sta_min_distance_m": "obssStaMinDistanceM",
    "obss_sta_max_distance_m": "obssStaMaxDistanceM",
    "obss_placement_stream_base": "obssPlacementStreamBase",
    "obss_application_stream_base": "obssApplicationStreamBase",
    "obss_wifi_stream_base": "obssWifiStreamBase",
    "prediction_telemetry_enabled": "predictionTelemetryEnabled",
    "prediction_sample_offsets_us": "predictionSampleOffsetsUs",
    "prediction_history_windows_us": "predictionHistoryWindowsUs",
    "prediction_polling_interval_us": "predictionPollingIntervalUs",
    "prediction_polling_report_delay_us": "predictionPollingReportDelayUs",
    "prediction_event_log_enabled": "predictionEventLogEnabled",
    "prediction_oracle_features_enabled": "predictionOracleFeaturesEnabled",
    "selective_duplication_threshold": "selectiveDuplicationThreshold",
    "selective_duplication_frame_budget": "selectiveDuplicationFrameBudget",
    "selective_duplication_burst_horizon_frames": "selectiveDuplicationBurstHorizonFrames",
    "selective_duplication_decision_offsets_us": "selectiveDuplicationDecisionOffsetsUs",
    "secondary_airtime_meter_enabled": "secondaryAirtimeMeterEnabled",
    "adaptive_airtime_budget_fraction": "adaptiveAirtimeBudgetFraction",
    "adaptive_airtime_bucket_horizon_us": "adaptiveAirtimeBucketHorizonUs",
    "adaptive_airtime_initial_shadow_price": "adaptiveAirtimeInitialShadowPrice",
    "adaptive_airtime_dual_step": "adaptiveAirtimeDualStep",
    "adaptive_airtime_cost_safety_factor": "adaptiveAirtimeCostSafetyFactor",
    "adaptive_airtime_cost_ewma_alpha": "adaptiveAirtimeCostEwmaAlpha",
    "adaptive_airtime_decision_offsets_us": "adaptiveAirtimeDecisionOffsetsUs",
    "full_duplication_primary_path": "fullDuplicationPrimaryPath",
}


def project_commit(root: Path = ROOT) -> str:
    status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root,
        text=True,
    ).strip()
    if status:
        raise RuntimeError(
            "tracked project changes are uncommitted; commit them before running experiments"
        )
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def matrix_sha256(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(document).encode()).hexdigest()


def validate_existing_manifest(
    path: Path,
    experiment: str,
    matrix_sha: str,
    project_git_commit: str,
    expected_run_ids: set[str],
) -> None:
    """Reject resume roots belonging to different code or matrix content."""
    if not path.exists():
        return
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot resume from invalid manifest {path}: {error}") from error
    expected_identity = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "experiment": experiment,
        "matrix_sha256": matrix_sha,
        "project_commit": project_git_commit,
        "ns3_upstream_commit": NS3_UPSTREAM_COMMIT,
    }
    mismatches = [
        key for key, expected in expected_identity.items()
        if manifest.get(key) != expected
    ]
    if mismatches:
        raise ValueError(
            f"output root belongs to a different experiment identity "
            f"({', '.join(mismatches)}); choose a new output root"
        )
    runs = manifest.get("runs")
    if not isinstance(runs, list):
        raise ValueError("experiment manifest has an invalid runs list")
    recorded = {
        item.get("run_id") for item in runs if isinstance(item, dict)
    }
    if None in recorded or not recorded <= expected_run_ids:
        raise ValueError(
            "experiment manifest contains runs outside the current matrix; "
            "choose a new output root"
        )


def _nested_leaf(value: Any, leaf: str) -> Any:
    if not isinstance(value, dict):
        return None
    if leaf in value:
        return value[leaf]
    matches = [
        found for child in value.values()
        if (found := _nested_leaf(child, leaf)) is not None
    ]
    if len(matches) > 1:
        raise ValueError(f"duplicate configuration leaf: {leaf}")
    return matches[0] if matches else None


def derive_run_id(resolved: dict[str, Any], seed: int, run: int,
                  ns3_commit: str, project_git_commit: str) -> str:
    identity = {
        "config": resolved, "seed": seed, "run": run,
        "ns3_commit": ns3_commit, "project_commit": project_git_commit,
    }
    prediction_enabled = _nested_leaf(resolved, "prediction_telemetry_enabled")
    if prediction_enabled is not None and not isinstance(prediction_enabled, bool):
        raise ValueError("prediction_telemetry_enabled must be Boolean")
    if prediction_enabled:
        identity["prediction_schema_versions"] = PREDICTION_SCHEMA_VERSIONS
    return hashlib.sha256(canonical_json(identity).encode()).hexdigest()[:20]


def set_dotted(target: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    if not all(parts):
        raise ValueError(f"invalid sweep key: {dotted!r}")
    node = target
    for part in parts[:-1]:
        existing = node.setdefault(part, {})
        if not isinstance(existing, dict):
            raise ValueError(f"sweep key traverses scalar: {dotted}")
        node = existing
    node[parts[-1]] = value


def _entries(values: Any, kind: str) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{kind} must be a nonempty list")
    result = []
    for item in values:
        entry = {"name": item} if isinstance(item, str) else copy.deepcopy(item)
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ValueError(f"invalid {kind} entry: {item!r}")
        result.append(entry)
    return result


def expand_config(document: dict[str, Any]) -> list[dict[str, Any]]:
    base = document.get("base", {})
    if not isinstance(base, dict):
        raise ValueError("base must be a mapping")
    seeds = document.get("seeds", [1])
    runs = document.get("runs", [1])
    if not all(isinstance(value, int) and value > 0 for value in [*seeds, *runs]):
        raise ValueError("seeds and runs must contain positive integers")
    topologies = _entries(document.get("topologies", ["single_link"]), "topologies")
    policies = _entries(document.get("policies", ["fixed_link_0"]), "policies")
    sweep = document.get("sweep", {})
    if not isinstance(sweep, dict) or not all(isinstance(v, list) and v for v in sweep.values()):
        raise ValueError("sweep must map dotted keys to nonempty lists")
    sweep_keys = sorted(sweep)
    sweep_products: Iterable[tuple[Any, ...]] = itertools.product(
        *(sweep[key] for key in sweep_keys)
    ) if sweep_keys else [()]
    expanded = []
    sweep_values = list(sweep_products)
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
            or not all(
                isinstance(value, int) and value > 0
                for value in [*policy_seeds, *policy_runs]
            )
        ):
            raise ValueError("policy seeds and runs must contain positive integers")
        for seed, run, values in itertools.product(policy_seeds, policy_runs, sweep_values):
            resolved = copy.deepcopy(base)
            resolved["topology"] = topology["name"]
            resolved["policy"] = policy["name"]
            for overlay in (topology.get("config", {}), policy.get("config", {})):
                if not isinstance(overlay, dict):
                    raise ValueError("topology/policy config must be a mapping")
                for key, value in overlay.items():
                    set_dotted(resolved, key, value)
            for key, value in zip(sweep_keys, values):
                set_dotted(resolved, key, value)
            expanded.append({"config": resolved, "seed": seed, "run": run})
    if not expanded:
        raise ValueError("matrix expansion produced no compatible runs")
    return expanded


def _flatten(value: dict[str, Any], prefix: str = "") -> Iterable[tuple[str, Any]]:
    for key in sorted(value):
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value[key], dict):
            yield from _flatten(value[key], dotted)
        else:
            yield dotted, value[key]


def cli_arguments(config: dict[str, Any], config_dir: Path) -> list[str]:
    arguments = []
    for dotted, value in _flatten(config):
        if dotted in {"topology", "policy"}:
            cli_key = dotted
        else:
            leaf = dotted.rsplit(".", 1)[-1]
            if leaf not in CLI_KEYS:
                raise ValueError(f"no C++ CLI translation for {dotted}")
            cli_key = CLI_KEYS[leaf]
        if cli_key in {"traceFile", "correlationTrace"} and value:
            path = Path(str(value))
            value = str((config_dir / path).resolve()) if not path.is_absolute() else str(path)
        if isinstance(value, bool):
            value = "1" if value else "0"
        elif isinstance(value, list):
            if not value or not all(isinstance(item, int) and not isinstance(item, bool)
                                    for item in value):
                raise ValueError(f"{dotted} must be a nonempty integer list")
            value = ",".join(str(item) for item in value)
        arguments.append(f"--{cli_key}={value}")
    return arguments


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")
        temporary = Path(output.name)
    os.replace(temporary, path)


def write_experiment_description(document: dict[str, Any],
                                 specs: list[dict[str, Any]],
                                 output_root: Path) -> None:
    """Write the resolved node, association, and approach description."""
    base = document.get("base", {})
    wifi = base.get("wifi", {})
    stream = base.get("stream", {})
    background = base.get("background", {})
    obss = base.get("obss", {})
    ofdma_states = sorted({
        bool(spec["config"].get("wifi", {}).get("ul_ofdma_enabled", False))
        for spec in specs
    })
    treatment_seeds: dict[tuple[str, str, int, bool], set[int]] = {}
    for spec in specs:
        config = spec["config"]
        config_wifi = config.get("wifi", {})
        key = (
            config["topology"],
            config["policy"],
            int(config_wifi.get("mlo_sta_max_inflights", 1)),
            bool(config_wifi.get("ul_ofdma_enabled", False)),
        )
        treatment_seeds.setdefault(key, set()).add(spec["seed"])
    seed_counts = {len(seeds) for seeds in treatment_seeds.values()}
    seed_count = next(iter(seed_counts)) if len(seed_counts) == 1 else None
    ofdma_scope = wifi.get("ul_ofdma_scope", "all_he_eht_aps")
    state_text = ", ".join("enabled" if state else "disabled" for state in ofdma_states)
    has_legacy = any(
        spec["config"].get("background", {}).get("background_profile") == "legacy_mixed8"
        for spec in specs
    )
    has_obss = any(
        spec["config"].get("obss", {}).get("obss_profile") == "mixed4x4"
        for spec in specs
    )
    if not has_obss:
        ofdma_obss_text = "No independent OBSS APs are installed in this matrix."
    elif ofdma_scope == "all_he_eht_aps":
        ofdma_obss_text = "The independent HE and EHT OBSS APs also use the scheduler."
    else:
        ofdma_obss_text = "The independent OBSS APs remain EDCA-only in this matrix."
    prediction_configs = [
        spec["config"].get("prediction", {})
        for spec in specs
        if spec["config"].get("prediction", {}).get("prediction_telemetry_enabled", False)
    ]
    prediction = prediction_configs[0] if prediction_configs else {}
    approaches = sorted({
        (
            spec["config"]["topology"],
            spec["config"]["policy"],
            int(spec["config"].get("wifi", {}).get("mlo_sta_max_inflights", 1)),
        )
        for spec in specs
    })
    has_selective = any(policy == "selective_duplication" for _, policy, _ in approaches)
    has_adaptive = any(policy in {
        "adaptive_airtime_duplication", "adaptive_deficit_duplication",
    } for _, policy, _ in approaches)
    has_deficit = any(policy == "adaptive_deficit_duplication"
                      for _, policy, _ in approaches)
    approach_lines: list[str] = []
    for topology, policy, inflights in approaches:
        if topology == "dual_interface" and policy == "fixed_link_0":
            approach_lines += [
                "* ``Single 2.4 GHz interface``: one dual-radio 802.11be STA",
                "  associates through separate non-MLO interfaces and sends only",
                "  through the 2.4 GHz interface.",
            ]
        elif topology == "dual_interface" and policy == "fixed_link_1":
            approach_lines += [
                "* ``Single 5 GHz interface``: the same dual-radio association",
                "  sends only through the 5 GHz interface.",
            ]
        elif topology == "dual_interface" and policy == "full_duplication":
            approach_lines += [
                "* ``Application full duplication``: each frame is sent over both",
                "  independent non-MLO 802.11be interfaces. The primary-copy path",
                "  is recorded in each resolved run configuration.",
            ]
        elif topology == "dual_interface" and policy == "selective_duplication":
            approach_lines += [
                "* ``Closed-loop selective duplication``: each frame starts on",
                "  the 5 GHz interface. The frozen calibrated four-stage predictor",
                "  may causally launch a delayed 2.4 GHz copy, subject to the",
                "  configured probability threshold and online frame-token budget.",
            ]
        elif topology == "dual_interface" and policy == "adaptive_airtime_duplication":
            approach_lines += [
                "* ``Closed-loop adaptive airtime duplication``: each frame starts",
                "  on the 5 GHz interface. The frozen predictor may launch a delayed",
                "  2.4 GHz copy using a shadow-price utility and secondary PHY TX",
                "  airtime token budget.",
            ]
        elif topology == "dual_interface" and policy == "adaptive_deficit_duplication":
            approach_lines += [
                "* ``Adaptive primary-deficit duplication``: each frame starts",
                "  on the 5 GHz interface. Adaptive admission launches only the",
                "  primary-unacknowledged packet indexes on 2.4 GHz in reverse",
                "  order under the secondary PHY TX airtime token budget.",
            ]
        elif topology == "mlo_str":
            approach_lines += [
                f"* ``STR MLO NMaxInflights={inflights}``: one two-link 802.11be STR",
                f"  MLD uses a BE NMaxInflights value of {inflights}.",
            ]
        elif topology == "mlo_emlsr":
            approach_lines += [
                "* ``EMLSR MLO``: one two-link 802.11be EMLSR MLD uses the",
                "  predeclared advanced STA/AP fixed-aux profile. PHY 1 starts as the",
                "  5 GHz main PHY and may switch across both bands; the 2.4 GHz",
                "  auxiliary PHY is fixed and not TX capable. Per-link successful",
                "  MPDUs and sender PHY TX airtime are recorded without conditioning",
                "  experiment acceptance on which link wins access.",
            ]
        else:
            approach_lines += [
                f"* ``{topology}/{policy}``: see the resolved run configuration.",
            ]

    lines = [
        document.get("name", "Wi-Fi streaming experiment"),
        "=" * len(document.get("name", "Wi-Fi streaming experiment")),
        "",
        "Purpose",
        "-------",
        "",
        f"This matrix compares {len(approaches)} target-sender approach(es) under identical",
        f"traffic, propagation, and random seeds. UL OFDMA states: {state_text}.",
        "",
        "Target devices and approaches",
        "-----------------------------",
        "",
        *approach_lines,
        "",
        "Both target links are 20 MHz: channel 1 at 2.4 GHz and channel 36 at",
        "5 GHz. Target data uses EHT MCS 5, an 800 ns guard interval, BE",
        "traffic, A-MPDU aggregation, and a 500-packet MAC queue.",
        f"The target STA is 10 m from the target AP and generates {stream.get('fps', 30)}",
        f"frames/s for {stream.get('duration', 60)} s. Interframes are",
        f"{stream.get('frame_size', 12000)} bytes; every",
        f"{stream.get('gop_length', 60)}th frame is multiplied by",
        f"{stream.get('keyframe_size_multiplier', 4)} and packetized into",
        f"{stream.get('payload_size', 1200)}-byte UDP payloads.",
        "",
    ]
    if has_selective or has_adaptive:
        if has_selective and has_adaptive:
            controller_names = "The selective and adaptive arms"
            controller_verb = "feed"
        else:
            controller_names = "The selective arm" if has_selective else "The adaptive arm"
            controller_verb = "feeds"
        action_text = (
            f"{controller_names} {controller_verb} these snapshots to the frozen "
            "F0+F1-degraded commodity predictor; receiver outcomes never enter "
            "the decision."
        )
        if has_deficit:
            action_text += (
                " Primary-deficit packet selection additionally reads the exact "
                "causal primary per-packet ACK state."
            )
    else:
        action_text = "Adaptive actions are disabled in this telemetry matrix."
    if has_legacy:
        lines += [
            "Same-BSS contention devices",
            "---------------------------",
            "",
            "Sixteen independent non-MLD uplink STAs associate with the target",
            "AP: eight on each target link. The 2.4 GHz BSS has three 802.11n,",
            "three 802.11ax, and two 802.11be STAs. The 5 GHz BSS has two each",
            "of 802.11n, 802.11ac, 802.11ax, and 802.11be. Each STA generates",
            f"independent UDP ON/OFF traffic at {background.get('background_rate_mbps', 2)}",
            "Mbps while ON, with 100 ms mean ON and OFF durations.",
            "",
        ]
    if has_obss:
        count = int(obss.get("obss_stations_per_bss", 4))
        lines += [
            "Overlapping BSS devices",
            "-----------------------",
            "",
            f"Four independent APs each serve {count} same-standard STAs:",
            "",
            "* one 802.11n BSS on the 2.4 GHz target channel;",
            "* one 802.11ax BSS on the 2.4 GHz target channel;",
            "* one 802.11ac BSS on the 5 GHz target channel;",
            "* one 802.11be BSS on the 5 GHz target channel.",
            "",
            "These 16 OBSS STAs do not associate with the target AP. Every OBSS",
            "STA has independent uplink and downlink UDP ON/OFF flows. A new",
            f"UL rate is drawn uniformly from {obss.get('obss_ul_min_rate_mbps', 0.5)}-",
            f"{obss.get('obss_ul_max_rate_mbps', 3)} Mbps and a new DL rate from",
            f"{obss.get('obss_dl_min_rate_mbps', 2)}-{obss.get('obss_dl_max_rate_mbps', 8)}",
            "Mbps for each ON period. Mean ON and OFF durations are 100 ms and",
            "300 ms. OBSS PHY rates use Minstrel-HT with the latest amendment",
            "only and a 50 ms update interval.",
            "",
        ]
    if prediction.get("prediction_telemetry_enabled", False):
        offsets = ", ".join(
            str(value) for value in prediction.get(
                "prediction_sample_offsets_us", [0, 1000, 2000, 4000]
            )
        )
        windows = ", ".join(
            str(value) for value in prediction.get(
                "prediction_history_windows_us", [1000, 5000, 20000]
            )
        )
        lines += [
            "Prediction telemetry",
            "--------------------",
            "",
            "The primary-link sender records passive, receiver-independent causal",
            f"snapshots at offsets {offsets} us. Rolling MAC/PHY windows are",
            f"{windows} us. F1 reports are captured by a frame-independent "
            f"{prediction.get('prediction_polling_interval_us', 1000)} us clock and become "
            f"available after {prediction.get('prediction_polling_report_delay_us', 1000)} us. "
            "Raw prediction events are "
            f"{'enabled' if prediction.get('prediction_event_log_enabled') else 'disabled'};",
            f"causal oracle fields are "
            f"{'enabled' if prediction.get('prediction_oracle_features_enabled') else 'disabled'}.",
            "A-MSDU, fragmentation, and UL OFDMA are disabled for telemetry validity.",
            action_text,
            "",
        ]
    target_sta_count = 1
    extra_sta_count = (16 if has_legacy else 0) + (16 if has_obss else 0)
    has_dual = any(topology == "dual_interface" for topology, _, _ in approaches)
    has_mlo = any(
        topology in {"mlo_str", "mlo_emlsr"}
        for topology, _, _ in approaches
    )
    if has_dual and has_mlo:
        target_ap_description = (
            "The target AP is one logical node using either two independent AP "
            "interfaces or one two-link AP MLD."
        )
    elif has_mlo:
        target_ap_description = "The target AP is one logical two-link AP MLD."
    else:
        target_ap_description = (
            "The target AP is one logical node with two independent AP interfaces."
        )
    extra_ap_description = (
        "Four additional AP nodes provide the OBSS profile."
        if has_obss else
        "No additional AP nodes are installed."
    )
    lines += [
        "Device counts per approach",
        "--------------------------",
        "",
        f"Each approach contains {target_sta_count} logical target STA node and up to",
        f"{extra_sta_count} contention STA nodes, according to treatment. {target_ap_description}",
        extra_ap_description,
        "",
        "UL OFDMA configuration",
        "----------------------",
        "",
        "When disabled, all uplink data uses normal EDCA channel access. When",
        "enabled, ``RrMultiUserScheduler`` is installed on the target EHT AP.",
        ofdma_obss_text,
        "HT and VHT APs remain EDCA-only. Only associated HE/EHT STAs are",
        "trigger eligible; OFDMA does not coordinate stations across BSS",
        "boundaries.",
        "",
        f"The scheduler requests access every {wifi.get('ul_ofdma_access_interval_ms', 5)}",
        "ms, enables BSRP, allocates RUs to at most",
        f"{wifi.get('ul_ofdma_max_stations', 4)} STAs, and uses",
        f"{wifi.get('ul_ofdma_psdu_size', 1200)} bytes as the fallback solicited",
        f"PSDU size. Each treatment uses {seed_count} RNG seeds and is paired by seed."
        if seed_count is not None
        else "PSDU size. Treatment seed counts differ; see the experiment manifest.",
        "",
    ]
    (output_root / "DESCRIPTION.rst").write_text("\n".join(lines), encoding="utf-8")


def _merge_yaml(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_yaml(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_yaml(path: Path, seen: set[Path] | None = None) -> dict[str, Any]:
    path = path.resolve()
    visited = set() if seen is None else set(seen)
    if path in visited:
        raise ValueError(f"experiment YAML inheritance cycle at {path}")
    visited.add(path)
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("experiment YAML root must be a mapping")
    extends = value.pop("extends", None)
    if extends is None:
        return value
    if not isinstance(extends, str) or not extends:
        raise ValueError("experiment YAML extends must be a nonempty path")
    parent = Path(extends)
    if not parent.is_absolute():
        parent = path.parent / parent
    return _merge_yaml(load_yaml(parent, visited), value)


def run_one(spec: dict[str, Any], output_root: Path, config_dir: Path,
            project_git_commit: str) -> dict[str, Any]:
    run_id = spec["run_id"]
    final = output_root / run_id
    if final.exists():
        validate_run(final, run_id, project_git_commit, NS3_UPSTREAM_COMMIT)
        raise FileExistsError(f"completed duplicate rejected: {run_id}")
    for stale in output_root.glob(f".{run_id}.attempt-*"):
        shutil.rmtree(stale)
    attempt = output_root / f".{run_id}.attempt-{os.getpid()}"
    log_path = output_root / f".{run_id}.stdout-{os.getpid()}.tmp"
    arguments = cli_arguments(spec["config"], config_dir)
    arguments += [
        f"--seed={spec['seed']}", f"--run={spec['run']}", f"--runId={run_id}",
        f"--outputDir={attempt}", f"--projectGitCommit={project_git_commit}",
    ]
    command = [str(ROOT / "ns3"), "run", "streaming-experiment", "--no-build", "--", *arguments]
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.run(command, cwd=ROOT, stdout=log,
                                     stderr=subprocess.STDOUT, text=True)
        if attempt.is_dir():
            shutil.move(str(log_path), attempt / "stdout.log")
        if process.returncode:
            if attempt.is_dir():
                failure_log = attempt / "stdout.log"
            else:
                failure_log = output_root / f"{run_id}.failed.stdout.log"
                os.replace(log_path, failure_log)
            raise RuntimeError(
                f"run {run_id} failed ({process.returncode}); see {failure_log}"
            )
        validate_run(attempt, run_id, project_git_commit, NS3_UPSTREAM_COMMIT)
        os.replace(attempt, final)
    finally:
        if log_path.exists():
            log_path.unlink()
    return {
        "run_id": run_id, "status": "complete", "seed": spec["seed"], "run": spec["run"],
        "directory": str(final.relative_to(output_root)), "config": spec["config"],
        "command": command,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--no-analysis", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="keep validated completed runs and execute missing runs")
    args = parser.parse_args()
    document = load_yaml(args.config.resolve())
    workers = args.workers or int(document.get("workers", 1))
    if workers < 1:
        parser.error("workers must be positive")
    output_root = (args.output_root or Path(document.get("output_root", "results"))).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    commit = project_commit()
    specs = expand_config(document)
    seen = set()
    for spec in specs:
        spec["run_id"] = derive_run_id(spec["config"], spec["seed"], spec["run"],
                                       NS3_UPSTREAM_COMMIT, commit)
        if spec["run_id"] in seen:
            raise ValueError(f"duplicate resolved run in matrix: {spec['run_id']}")
        seen.add(spec["run_id"])
        completed = output_root / spec["run_id"]
        if completed.exists():
            validate_run(completed, spec["run_id"], commit, NS3_UPSTREAM_COMMIT)
            if not args.resume:
                raise FileExistsError(f"completed duplicate rejected: {spec['run_id']}")
            spec["completed"] = True
    experiment = str(document.get("name", args.config.stem))
    resolved_matrix_sha = matrix_sha256(document)
    manifest_path = output_root / "experiment_manifest.json"
    validate_existing_manifest(
        manifest_path,
        experiment,
        resolved_matrix_sha,
        commit,
        seen,
    )
    write_experiment_description(document, specs, output_root)
    if not args.no_build:
        subprocess.run([str(ROOT / "ns3"), "build", "streaming-experiment"],
                       cwd=ROOT, check=True)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "experiment": experiment,
        "matrix_sha256": resolved_matrix_sha,
        "config_file": str(args.config.resolve()), "project_commit": commit,
        "ns3_upstream_commit": NS3_UPSTREAM_COMMIT,
        "runs": [
            {
                "run_id": spec["run_id"],
                "status": "complete",
                "seed": spec["seed"],
                "run": spec["run"],
                "directory": spec["run_id"],
                "config": spec["config"],
                "command": None,
            }
            for spec in specs
            if spec.get("completed")
        ],
    }
    atomic_json(manifest_path, manifest)
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(run_one, spec, output_root, args.config.resolve().parent, commit): spec
            for spec in specs
            if not spec.get("completed")
        }
        for future in as_completed(futures):
            try:
                result = future.result()
                manifest["runs"].append(result)
                manifest["runs"].sort(key=lambda item: item["run_id"])
                atomic_json(manifest_path, manifest)
                print(f"COMPLETE {result['run_id']}")
            except Exception as error:
                failures.append(str(error))
                print(f"FAILED {futures[future]['run_id']}: {error}", file=sys.stderr)
    if failures:
        raise SystemExit("\n".join(failures))
    if not args.no_analysis:
        aggregate = summarize([output_root / spec["run_id"] for spec in specs])
        aggregate_json = output_root / "aggregate.json"
        aggregate_csv = output_root / "aggregate.csv"
        write_outputs(aggregate, aggregate_json, aggregate_csv)
        plot(aggregate, output_root / "plots")
        plot_selective_control(aggregate, output_root)
        plot_adaptive_airtime(aggregate, output_root)
        ofdma_states = {
            bool(run.get("config", {}).get("wifi", {}).get("ul_ofdma_enabled", False))
            for run in aggregate["runs"]
        }
        if ofdma_states == {False, True}:
            plot_ofdma_comparison(aggregate, output_root / "plots" / "ofdma_comparison")
        print(f"ANALYSIS {aggregate_json} {aggregate_csv} {output_root / 'plots'}")
    print(f"MANIFEST {manifest_path}")


if __name__ == "__main__":
    main()
