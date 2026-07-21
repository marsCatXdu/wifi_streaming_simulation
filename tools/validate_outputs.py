#!/usr/bin/env python3
"""Validate one wifi-streaming run directory."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

CORE_FILES = {
    "resolved_config.json",
    "build_info.json",
    "frames.csv",
    "policy_decisions.csv",
    "link_intervals.csv",
    "mac_summary.csv",
    "summary.json",
    "stdout.log",
}
OFDMA_COLUMNS = {
    "device_group", "trigger_frames", "basic_trigger_frames", "bsrp_trigger_frames",
    "ru_grants", "tb_ppdus_transmitted", "tb_bytes_transmitted",
    "tb_mpdus_received", "tb_bytes_received",
}
FRAME_COLUMNS = {
    "run_id", "frame_id", "generation_time_us", "frame_size_bytes", "packet_count",
    "frame_type", "deadline_us", "policy", "primary_link", "duplicated",
    "decision_time_us", "union_first_packet_us", "union_completion_us",
    "union_latency_us", "copy_0_completion_us", "copy_1_completion_us",
    "unique_packets_received", "duplicate_packets_received", "deadline_miss",
    "incomplete", "completion_mode",
}
DECISION_COLUMNS = {
    "run_id", "frame_id", "decision_time_us", "policy", "primary_link",
    "duplicated", "secondary_link", "reason", "primary_score", "secondary_score",
}
LINK_COLUMNS = {
    "timestamp_us", "link_id", "application_bytes_sent",
    "application_bytes_received", "redundant_bytes", "successful_mpdus",
    "failed_mpdus", "retransmissions", "phy_tx_time_us", "phy_rx_time_us",
    "phy_cca_busy_time_us",
}
MAC_COLUMNS = {
    "link_id", "node_id", "device_id", "successful_mpdus", "failed_mpdus",
    "retransmissions", "retry_limit_drops",
}
BACKGROUND_FLOW_COLUMNS = {
    "run_id", "bss_id", "link_id", "standard", "sta_index", "direction",
    "source_node_id", "destination_node_id", "port", "rate_stream", "on_stream",
    "off_stream", "period_count", "bytes_sent", "bytes_received",
}
BACKGROUND_PERIOD_COLUMNS = {
    "run_id", "bss_id", "sta_index", "direction", "period_index", "start_us",
    "end_us", "rate_mbps",
}
SUMMARY_KEYS = {
    "frame_count", "complete_frame_count", "incomplete_frame_count",
    "deadline_miss_count", "complete_ratio", "incomplete_ratio",
    "deadline_miss_ratio", "application_bytes_sent",
    "application_bytes_delivered", "redundant_bytes_sent", "successful_mpdus",
    "duplicate_frame_count", "failed_mpdus", "retransmissions",
    "redundant_byte_ratio", "phy_tx_time_us", "phy_rx_time_us",
    "phy_cca_busy_time_us",
}
BUILD_KEYS = {
    "ns3_version", "ns3_upstream_commit", "project_git_commit", "compiler",
    "build_profile", "execution_timestamp_utc", "host",
}


class ValidationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValidationError(f"{path.name}: invalid JSON: {error}") from error
    _require(isinstance(value, dict), f"{path.name}: root must be an object")
    return value


def _csv(path: Path, required: set[str]) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            _require(reader.fieldnames is not None, f"{path.name}: missing header")
            _require(required <= set(reader.fieldnames), f"{path.name}: missing columns")
            return list(reader)
    except OSError as error:
        raise ValidationError(f"{path.name}: cannot read: {error}") from error


def _integer(row: dict[str, str], key: str, file_name: str) -> int:
    try:
        value = int(row[key])
    except (KeyError, ValueError) as error:
        raise ValidationError(f"{file_name}: invalid integer {key}") from error
    _require(value >= 0, f"{file_name}: negative {key}")
    return value


def _flag(row: dict[str, str], key: str, file_name: str) -> bool:
    _require(row[key] in {"0", "1"}, f"{file_name}: {key} must be 0 or 1")
    return row[key] == "1"


def _close(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)


def validate_run(
    run_dir: Path | str,
    expected_run_id: str | None = None,
    expected_project_commit: str | None = None,
    expected_ns3_commit: str | None = None,
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    missing = sorted(name for name in CORE_FILES if not (run_dir / name).is_file())
    _require(not missing, f"missing core files: {', '.join(missing)}")
    config = _json(run_dir / "resolved_config.json")
    build = _json(run_dir / "build_info.json")
    summary = _json(run_dir / "summary.json")
    _require(BUILD_KEYS <= build.keys(), "build_info.json: missing identity fields")
    _require(SUMMARY_KEYS <= summary.keys(), "summary.json: missing fields")
    run_id = expected_run_id or config.get("run_id")
    _require(isinstance(run_id, str) and run_id, "resolved_config.json: invalid run_id")
    _require(config.get("run_id") == run_id, "resolved_config.json: run_id mismatch")
    _require(isinstance(config.get("seed"), int) and config["seed"] > 0, "invalid seed")
    _require(isinstance(config.get("run"), int) and config["run"] > 0, "invalid run")
    _require(config.get("topology") in {"single_link", "dual_interface", "mlo_str"},
             "resolved_config.json: invalid topology")
    _require(isinstance(config.get("stream"), dict), "resolved_config.json: missing stream")
    wifi = config.get("wifi", {})
    max_inflights = int(wifi.get("sta_max_inflights", 1))
    _require(1 <= max_inflights <= 15,
             "resolved_config.json: invalid STA max inflights")
    _require(max_inflights == 1 or
             (config["topology"] == "mlo_str" and wifi.get("block_ack_enabled") is True),
             "resolved_config.json: multiple inflights require MLO Block Ack")
    ul_ofdma_enabled = wifi.get("ul_ofdma_enabled", False)
    _require(isinstance(ul_ofdma_enabled, bool),
             "resolved_config.json: invalid UL OFDMA enabled flag")
    if ul_ofdma_enabled:
        _require(wifi.get("ul_ofdma_scope") in {"target_aps", "all_he_eht_aps"},
                 "resolved_config.json: invalid UL OFDMA scope")
        _require(int(wifi.get("ul_ofdma_access_interval_ms", 0)) > 0,
                 "resolved_config.json: invalid UL OFDMA access interval")
        _require(1 <= int(wifi.get("ul_ofdma_max_stations", 0)) <= 74,
                 "resolved_config.json: invalid UL OFDMA station count")
        _require(int(wifi.get("ul_ofdma_psdu_size_bytes", 0)) > 0,
                 "resolved_config.json: invalid UL OFDMA PSDU size")
    if "ul_ofdma_enabled" in wifi:
        _require((run_dir / "ofdma_summary.csv").is_file(),
                 "missing core file: ofdma_summary.csv")
        ofdma_rows = _csv(run_dir / "ofdma_summary.csv", OFDMA_COLUMNS)
        _require({row["device_group"] for row in ofdma_rows} ==
                 {"target", "same_bss_background", "obss"},
                 "ofdma_summary.csv: invalid device groups")
        for row in ofdma_rows:
            for column in OFDMA_COLUMNS - {"device_group"}:
                _integer(row, column, "ofdma_summary.csv")
    if expected_project_commit is not None:
        _require(build["project_git_commit"] == expected_project_commit,
                 "build_info.json: project commit mismatch")
    if expected_ns3_commit is not None:
        _require(build["ns3_upstream_commit"] == expected_ns3_commit,
                 "build_info.json: ns-3 commit mismatch")
    _require(all(isinstance(build[key], str) and build[key] for key in BUILD_KEYS),
             "build_info.json: empty build identity")

    frames = _csv(run_dir / "frames.csv", FRAME_COLUMNS)
    decisions = _csv(run_dir / "policy_decisions.csv", DECISION_COLUMNS)
    links = _csv(run_dir / "link_intervals.csv", LINK_COLUMNS)
    mac = _csv(run_dir / "mac_summary.csv", MAC_COLUMNS)
    obss = config.get("background", {}).get("obss", {})
    obss_enabled = obss.get("profile", "none") != "none"
    flows: list[dict[str, str]] = []
    periods: list[dict[str, str]] = []
    if obss_enabled:
        _require((run_dir / "background_flows.csv").is_file(),
                 "missing core file: background_flows.csv")
        _require((run_dir / "background_rate_periods.csv").is_file(),
                 "missing core file: background_rate_periods.csv")
        flows = _csv(run_dir / "background_flows.csv", BACKGROUND_FLOW_COLUMNS)
        periods = _csv(run_dir / "background_rate_periods.csv", BACKGROUND_PERIOD_COLUMNS)
    _require(frames, "frames.csv: no frame rows")
    _require(links, "link_intervals.csv: no link rows")
    for rows, name in ((frames, "frames.csv"), (decisions, "policy_decisions.csv")):
        _require(all(row["run_id"] == run_id for row in rows), f"{name}: run_id mismatch")

    seen: set[int] = set()
    incomplete = misses = complete = delivered = duplicated_frames = 0
    for row in frames:
        frame_id = _integer(row, "frame_id", "frames.csv")
        _require(frame_id not in seen, "frames.csv: duplicate frame_id")
        seen.add(frame_id)
        generation = _integer(row, "generation_time_us", "frames.csv")
        size = _integer(row, "frame_size_bytes", "frames.csv")
        packets = _integer(row, "packet_count", "frames.csv")
        deadline = _integer(row, "deadline_us", "frames.csv")
        unique = _integer(row, "unique_packets_received", "frames.csv")
        is_incomplete = _flag(row, "incomplete", "frames.csv")
        is_miss = _flag(row, "deadline_miss", "frames.csv")
        is_duplicated = _flag(row, "duplicated", "frames.csv")
        duplicated_frames += is_duplicated
        _require(size > 0 and packets > 0, "frames.csv: nonpositive frame size/packet count")
        _require(row["frame_type"] in {
            "UNKNOWN", "I_FRAME", "P_FRAME", "B_FRAME", "PRIORITY_HIGH",
            "PRIORITY_NORMAL", "PRIORITY_LOW",
        }, "frames.csv: invalid frame_type")
        _require(unique <= packets, "frames.csv: received packet count exceeds packet count")
        completion = row["union_completion_us"]
        latency = row["union_latency_us"]
        if is_incomplete:
            incomplete += 1
            _require(not completion and not latency, "frames.csv: incomplete frame has completion")
        else:
            complete += 1
            delivered += size
            _require(bool(completion) and bool(latency), "frames.csv: complete frame lacks latency")
            _require(int(completion) - generation == int(latency),
                     "frames.csv: completion/latency invariant failed")
            _require(unique == packets, "frames.csv: complete frame lacks unique packets")
        if is_miss:
            misses += 1
        if deadline and completion:
            _require(is_miss == (int(latency) > deadline), "frames.csv: deadline flag mismatch")
        elif deadline and is_incomplete:
            _require(is_miss, "frames.csv: incomplete deadline frame must miss")

    total = len(frames)
    stream = config.get("stream", {})
    if stream.get("source") == "trace":
        trace_path = Path(stream.get("trace_file", ""))
        _require(trace_path.is_file(), "resolved_config.json: trace file is not readable")
        trace_rows = _csv(trace_path, {
            "frame_id", "generation_time_us", "size_bytes", "frame_type", "deadline_us",
        })
        _require(len(trace_rows) == total, "frames.csv: trace/frame count mismatch")
        warmup_us = round(float(config["warmup_s"]) * 1_000_000)
        by_id = {int(row["frame_id"]): row for row in frames}
        for trace in trace_rows:
            frame_id = int(trace["frame_id"])
            _require(frame_id in by_id, "frames.csv: trace frame ID missing")
            frame = by_id[frame_id]
            _require(int(frame["generation_time_us"]) ==
                     int(trace["generation_time_us"]) + warmup_us,
                     "frames.csv: trace generation interval changed")
            _require(frame["frame_size_bytes"] == trace["size_bytes"],
                     "frames.csv: trace frame size changed")
            _require(frame["frame_type"] == trace["frame_type"],
                     "frames.csv: trace frame type changed")
            _require(frame["deadline_us"] == trace["deadline_us"],
                     "frames.csv: trace deadline changed")
    elif stream.get("source") == "synthetic" and "gop_length" in stream:
        gop_length = int(stream["gop_length"])
        interframe_size = int(stream["frame_size_bytes"])
        multiplier = float(stream["keyframe_size_multiplier"])
        _require(gop_length > 0 and interframe_size > 0 and multiplier >= 1,
                 "resolved_config.json: invalid synthetic GOP")
        keyframe_size = int(interframe_size * multiplier + 0.5)
        for frame in frames:
            frame_id = int(frame["frame_id"])
            keyframe = frame_id % gop_length == 0
            _require(frame["frame_type"] == ("I_FRAME" if keyframe else "P_FRAME"),
                     "frames.csv: synthetic GOP frame type mismatch")
            _require(int(frame["frame_size_bytes"]) ==
                     (keyframe_size if keyframe else interframe_size),
                     "frames.csv: synthetic GOP frame size mismatch")
    _require(len(decisions) == total, "policy_decisions.csv: decision/frame count mismatch")
    _require({int(row["frame_id"]) for row in decisions} == seen,
             "policy_decisions.csv: frame IDs mismatch")
    frames_by_id = {row["frame_id"]: row for row in frames}
    for decision in decisions:
        frame = frames_by_id[decision["frame_id"]]
        _require(decision["policy"] == frame["policy"] and
                 decision["primary_link"] == frame["primary_link"] and
                 decision["duplicated"] == frame["duplicated"],
                 "policy_decisions.csv: decision/frame mismatch")
    expected = {
        "frame_count": total, "complete_frame_count": complete,
        "incomplete_frame_count": incomplete, "deadline_miss_count": misses,
        "application_bytes_delivered": delivered,
        "duplicate_frame_count": duplicated_frames,
    }
    for key, value in expected.items():
        _require(summary[key] == value, f"summary.json: {key} mismatch")
    for key, value in (
        ("complete_ratio", complete / total),
        ("incomplete_ratio", incomplete / total),
        ("deadline_miss_ratio", misses / total),
    ):
        _require(_close(float(summary[key]), value), f"summary.json: {key} mismatch")
    sent = int(summary["application_bytes_sent"])
    expected_redundant_ratio = int(summary["redundant_bytes_sent"]) / sent if sent else 0
    _require(_close(float(summary["redundant_byte_ratio"]), expected_redundant_ratio),
             "summary.json: redundant_byte_ratio mismatch")

    link_ids = [_integer(row, "link_id", "link_intervals.csv") for row in links]
    _require(len(link_ids) == len(set(link_ids)), "link_intervals.csv: duplicate links")
    for summary_key, column in (
        ("application_bytes_sent", "application_bytes_sent"),
        ("redundant_bytes_sent", "redundant_bytes"),
        ("successful_mpdus", "successful_mpdus"), ("failed_mpdus", "failed_mpdus"),
        ("retransmissions", "retransmissions"), ("phy_tx_time_us", "phy_tx_time_us"),
        ("phy_rx_time_us", "phy_rx_time_us"),
        ("phy_cca_busy_time_us", "phy_cca_busy_time_us"),
    ):
        value = sum(_integer(row, column, "link_intervals.csv") for row in links)
        _require(summary[summary_key] == value, f"per-link total mismatch: {summary_key}")
    _require({int(row["link_id"]) for row in mac} == set(link_ids),
             "mac_summary.csv: link IDs mismatch")
    for key in ("successful_mpdus", "failed_mpdus", "retransmissions"):
        _require(sum(_integer(row, key, "mac_summary.csv") for row in mac) == summary[key],
                 f"mac_summary.csv: {key} total mismatch")
    if obss_enabled:
        station_count = int(obss["stations_per_bss"])
        _require(len(obss.get("bsses", [])) == 4, "resolved_config.json: expected four OBSSs")
        _require(len(flows) == 4 * station_count * 2,
                 "background_flows.csv: unexpected flow count")
        flow_keys: set[tuple[int, int, str]] = set()
        streams: set[int] = set()
        periods_by_flow: dict[tuple[int, int, str], int] = {}
        for row in flows:
            _require(row["run_id"] == run_id, "background_flows.csv: run_id mismatch")
            key = (int(row["bss_id"]), int(row["sta_index"]), row["direction"])
            _require(row["direction"] in {"uplink", "downlink"},
                     "background_flows.csv: invalid direction")
            _require(key not in flow_keys, "background_flows.csv: duplicate flow")
            flow_keys.add(key)
            for stream_key in ("rate_stream", "on_stream", "off_stream"):
                stream = int(row[stream_key])
                _require(stream not in streams, "background_flows.csv: reused RNG stream")
                streams.add(stream)
            periods_by_flow[key] = _integer(
                row, "period_count", "background_flows.csv"
            )
            _integer(row, "bytes_sent", "background_flows.csv")
            _integer(row, "bytes_received", "background_flows.csv")
        observed_periods: dict[tuple[int, int, str], int] = {}
        _require(obss.get("station_manager", "constant") in {
            "minstrel_ht", "ideal", "constant",
        },
                 "resolved_config.json: invalid OBSS station manager")
        for row in periods:
            _require(row["run_id"] == run_id,
                     "background_rate_periods.csv: run_id mismatch")
            key = (int(row["bss_id"]), int(row["sta_index"]), row["direction"])
            _require(key in flow_keys, "background_rate_periods.csv: unknown flow")
            direction = row["direction"]
            prefix = "ul" if direction == "uplink" else "dl"
            minimum = float(obss.get(f"{prefix}_min_rate_mbps", obss["min_rate_mbps"]))
            maximum = float(obss.get(f"{prefix}_max_rate_mbps", obss["max_rate_mbps"]))
            rate = float(row["rate_mbps"])
            _require(minimum <= rate <= maximum,
                     "background_rate_periods.csv: rate outside configured range")
            _require(int(row["end_us"]) >= int(row["start_us"]),
                     "background_rate_periods.csv: negative period")
            observed_periods[key] = observed_periods.get(key, 0) + 1
        _require(observed_periods == periods_by_flow,
                 "background_rate_periods.csv: period counts mismatch")
    return {"run_id": run_id, "frame_count": total, "valid": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--project-commit")
    parser.add_argument("--ns3-commit")
    args = parser.parse_args()
    for directory in args.run_dirs:
        result = validate_run(directory, expected_project_commit=args.project_commit,
                              expected_ns3_commit=args.ns3_commit)
        print(f"VALID {result['run_id']} frames={result['frame_count']}")


if __name__ == "__main__":
    main()
