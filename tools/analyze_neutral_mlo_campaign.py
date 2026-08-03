#!/usr/bin/env python3
"""Analyze a paired adaptive-versus-MLO neutral campaign without writing files."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from scipy.stats import t as student_t


TREATMENTS = {
    "adaptive": ("dual_interface", "adaptive_airtime_duplication"),
    "str_mlo": ("mlo_str", "fixed_link_0"),
    "emlsr_mlo": ("mlo_emlsr", "fixed_link_0"),
}

TREATMENT_LABELS = {
    "adaptive": "Adaptive dual-interface",
    "str_mlo": "STR MLO",
    "emlsr_mlo": "EMLSR MLO",
}

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

BUILD_IDENTITY_FIELDS = (
    "ns3_version",
    "ns3_upstream_commit",
    "project_git_commit",
)

METER_FIELDS = (
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
)


class CampaignError(ValueError):
    """Raised when the input is not one valid paired campaign."""


@dataclass(frozen=True)
class Thresholds:
    """Predeclared practical and controller-integrity thresholds."""

    min_relative_improvement: float = 0.50
    max_airtime_increase: float = 0.20
    max_background_throughput_loss: float = 0.05
    max_budget_excess_us: float = 1.0
    max_budget_debt_us: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "min_relative_improvement",
            "max_airtime_increase",
            "max_background_throughput_loss",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0 or value > 1:
                raise ValueError(f"{name} must be a fraction in [0, 1]")
        for name in ("max_budget_excess_us", "max_budget_debt_us"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be nonnegative and finite")


def _finite_number(value: Any, description: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CampaignError(f"{description} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise CampaignError(f"{description} must be a finite number")
    return result


def _fraction(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0 or result > 1:
        raise argparse.ArgumentTypeError("expected a fraction in [0, 1]")
    return result


def _nonnegative(value: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise argparse.ArgumentTypeError("expected a nonnegative finite number")
    return result


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _pair_key(run: dict[str, Any], config: dict[str, Any]) -> tuple[int, int]:
    try:
        seed = int(run.get("seed", config.get("seed")))
        run_number = int(run.get("run", config.get("run")))
    except (KeyError, TypeError, ValueError) as error:
        raise CampaignError("every treatment row must contain integer seed and run") from error
    for name, value in (("seed", seed), ("run", run_number)):
        if name in config and int(config[name]) != value:
            raise CampaignError(
                f"aggregate {name}={value} disagrees with resolved config {name}={config[name]}"
            )
    return seed, run_number


def _run_directory(run: dict[str, Any], result_root: Path) -> Path:
    serialized = run.get("run_dir")
    if serialized and Path(serialized).is_dir():
        return Path(serialized)
    run_id = run.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise CampaignError("every treatment row must have a nonempty run_id")
    candidate = result_root / run_id
    if not candidate.is_dir():
        raise CampaignError(f"cannot locate run directory for {run_id}: tried {candidate}")
    return candidate


def _resolve_aggregate(path: Path) -> Path:
    path = path.resolve()
    candidates = [path] if path.is_file() else [
        path / "aggregate.json",
        path / "runs" / "aggregate.json",
    ]
    matches = [candidate for candidate in candidates if candidate.is_file()]
    if len(matches) != 1:
        tried = ", ".join(str(candidate) for candidate in candidates)
        raise CampaignError(f"expected exactly one aggregate for {path}; tried {tried}")
    return matches[0]


def _read_config(run: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    path = run_dir / "resolved_config.json"
    if not path.is_file():
        raise CampaignError(f"missing campaign input: {path}")
    config = json.loads(path.read_text(encoding="utf-8"))
    aggregate_config = run.get("config")
    if (
        aggregate_config is not None and
        _canonical_json(aggregate_config) != _canonical_json(config)
    ):
        raise CampaignError(f"{run_dir}: aggregate and resolved configurations disagree")
    if config.get("run_id") != run.get("run_id"):
        raise CampaignError(f"{run_dir}: resolved run_id does not match aggregate")
    return config


def _read_build_identity(run_dir: Path) -> dict[str, str]:
    path = run_dir / "build_info.json"
    if not path.is_file():
        raise CampaignError(f"missing campaign input: {path}")
    build = json.loads(path.read_text(encoding="utf-8"))
    identity: dict[str, str] = {}
    for name in BUILD_IDENTITY_FIELDS:
        value = build.get(name)
        if not isinstance(value, str) or not value:
            raise CampaignError(f"{path}: missing nonempty {name}")
        identity[name] = value
    return identity


def _treatment_name(run: dict[str, Any]) -> str | None:
    identity = (run.get("topology"), run.get("policy"))
    return next((name for name, expected in TREATMENTS.items() if identity == expected), None)


def _band_label(config: dict[str, Any], link_id: int) -> str:
    ranges = config.get("wifi", {}).get("frequency_ranges")
    if not isinstance(ranges, list) or link_id < 0 or link_id >= len(ranges):
        raise CampaignError(f"missing frequency range for link {link_id}")
    raw = str(ranges[link_id]).upper()
    if "2_4_GHZ" in raw:
        return "2.4GHz"
    if "5_GHZ" in raw:
        return "5GHz"
    if "6_GHZ" in raw:
        return "6GHz"
    return f"link_{link_id}:{ranges[link_id]}"


def _sender_airtime(run_dir: Path, config: dict[str, Any]) -> tuple[dict[str, float], float]:
    path = run_dir / "link_intervals.csv"
    if not path.is_file():
        raise CampaignError(f"missing campaign input: {path}")
    by_band: dict[str, float] = {}
    seen_links: set[int] = set()
    with path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    if not rows:
        raise CampaignError(f"{path}: no link rows")
    for row in rows:
        try:
            link_id = int(row["link_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise CampaignError(f"{path}: invalid link_id") from error
        if link_id in seen_links:
            raise CampaignError(f"{path}: duplicate link-{link_id} row")
        seen_links.add(link_id)
        try:
            airtime_us = float(row["phy_tx_time_us"])
        except (KeyError, TypeError, ValueError) as error:
            raise CampaignError(f"{path}: invalid PHY TX airtime") from error
        if not math.isfinite(airtime_us):
            raise CampaignError(f"{path}: invalid PHY TX airtime")
        if airtime_us < 0:
            raise CampaignError(f"{path}: negative PHY TX airtime")
        band = _band_label(config, link_id)
        by_band[band] = by_band.get(band, 0.0) + airtime_us
    if set(seen_links) != {0, 1}:
        raise CampaignError(f"{path}: headline treatments require exactly links 0 and 1")
    return by_band, sum(by_band.values())


def _measurement_duration_us(config: dict[str, Any]) -> float:
    start = _finite_number(config.get("measurement_start_s"), "measurement_start_s")
    stop = _finite_number(config.get("measurement_stop_s"), "measurement_stop_s")
    duration = (stop - start) * 1_000_000.0
    if duration <= 0:
        raise CampaignError("measurement_stop_s must be after measurement_start_s")
    return duration


def _read_meter(run_dir: Path, duration_us: float) -> dict[str, float]:
    path = run_dir / "secondary_airtime_summary.json"
    if not path.is_file():
        raise CampaignError(f"missing adaptive campaign input: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    missing = [name for name in METER_FIELDS if name not in raw]
    if missing:
        raise CampaignError(f"{path}: missing fields {', '.join(missing)}")
    meter = {name: _finite_number(raw[name], f"{path}: {name}") for name in METER_FIELDS}
    if abs(meter["measurement_duration_us"] - duration_us) > 1.0:
        raise CampaignError(f"{path}: meter and run measurement durations disagree")
    measured_fraction = meter["tagged_secondary_tx_airtime_us"] / duration_us
    if abs(measured_fraction - meter["tagged_secondary_tx_airtime_fraction"]) > 1e-9:
        raise CampaignError(f"{path}: tagged secondary airtime fraction is inconsistent")
    for name in METER_FIELDS:
        if meter[name] < 0:
            raise CampaignError(f"{path}: {name} is negative")
    return meter


def _environment(config: dict[str, Any]) -> dict[str, Any]:
    try:
        result = {name: config[name] for name in ENVIRONMENT_KEYS}
    except KeyError as error:
        raise CampaignError(
            f"resolved config is missing environment field {error.args[0]}"
        ) from error
    wifi = config.get("wifi")
    if not isinstance(wifi, dict):
        raise CampaignError("resolved config is missing wifi settings")
    result["shared_target_wifi"] = {
        name: wifi.get(name, "__MISSING__") for name in SHARED_WIFI_KEYS
    }
    return result


def _nominal_config(config: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(config)
    for name in ("run_id", "seed", "run"):
        result.pop(name, None)
    obss = result.get("background", {}).get("obss")
    if isinstance(obss, dict):
        obss.pop("bsses", None)
    return result


def _observation(
    run: dict[str, Any],
    run_dir: Path,
    treatment: str,
    expected_obss_profile: str | None,
) -> dict[str, Any]:
    config = _read_config(run, run_dir)
    if (config.get("topology"), config.get("policy")) != TREATMENTS[treatment]:
        raise CampaignError(f"{run_dir}: resolved treatment identity disagrees with aggregate")
    pair = _pair_key(run, config)
    profile = config.get("background", {}).get("obss", {}).get("profile")
    if expected_obss_profile is not None and profile != expected_obss_profile:
        raise CampaignError(
            f"{run_dir}: expected OBSS profile {expected_obss_profile!r}, found {profile!r}"
        )
    duration_us = _measurement_duration_us(config)
    by_band_us, total_us = _sender_airtime(run_dir, config)
    p99 = run.get("latency_p99_us")
    if p99 is None:
        raise CampaignError(f"{run_dir}: completed-frame P99 is unavailable")
    result = {
        "run_id": run["run_id"],
        "pair": pair,
        "config": config,
        "environment": _environment(config),
        "nominal_config": _nominal_config(config),
        "build_identity": _read_build_identity(run_dir),
        "deadline_miss_ratio": _finite_number(
            run.get("deadline_miss_ratio"), f"{run_dir}: deadline miss ratio"
        ),
        "completed_frame_p99_us": _finite_number(p99, f"{run_dir}: completed-frame P99"),
        "background_throughput_mbps": _finite_number(
            run.get("background_throughput_mbps"), f"{run_dir}: background throughput"
        ),
        "measurement_duration_us": duration_us,
        "sender_airtime_by_band_us": by_band_us,
        "sender_airtime_by_band_fraction": {
            band: value / duration_us for band, value in by_band_us.items()
        },
        "sender_airtime_total_us": total_us,
        "sender_airtime_total_fraction": total_us / duration_us,
    }
    for name in ("deadline_miss_ratio", "completed_frame_p99_us", "background_throughput_mbps"):
        if result[name] < 0:
            raise CampaignError(f"{run_dir}: {name} is negative")
    if treatment == "adaptive":
        meter = _read_meter(run_dir, duration_us)
        adaptive_config = config.get("adaptiveAirtimeDuplication", {})
        secondary_path = int(adaptive_config.get("secondary_path", 0))
        secondary_band = _band_label(config, secondary_path)
        if meter["tagged_secondary_tx_airtime_us"] > by_band_us[secondary_band] + 1.0:
            raise CampaignError(
                f"{run_dir}: tagged secondary airtime exceeds secondary-band PHY TX airtime"
            )
        result["secondary_meter"] = meter
    return result


def confidence_interval(values: list[float]) -> dict[str, Any]:
    """Return a two-sided 95 percent Student-t interval over run-level values."""
    if not values:
        return {
            "n": 0,
            "mean": None,
            "standard_deviation": None,
            "ci95_low": None,
            "ci95_high": None,
        }
    if not all(math.isfinite(value) for value in values):
        raise CampaignError("cannot summarize non-finite values")
    mean = statistics.mean(values)
    if len(values) == 1:
        return {
            "n": 1,
            "mean": mean,
            "standard_deviation": None,
            "ci95_low": None,
            "ci95_high": None,
        }
    deviation = statistics.stdev(values)
    half_width = float(student_t.ppf(0.975, len(values) - 1)) * deviation / math.sqrt(len(values))
    return {
        "n": len(values),
        "mean": mean,
        "standard_deviation": deviation,
        "ci95_low": mean - half_width,
        "ci95_high": mean + half_width,
    }


def _relative_summary(
    adaptive: list[float],
    baseline: list[float],
    transform: Callable[[float, float], float],
) -> dict[str, Any]:
    values = [transform(left, right) for left, right in zip(adaptive, baseline) if right > 0]
    result = confidence_interval(values)
    result["excluded_zero_baseline_units"] = sum(right == 0 for right in baseline)
    mean_baseline = statistics.mean(baseline)
    result["ratio_of_paired_means"] = (
        transform(statistics.mean(adaptive), mean_baseline) if mean_baseline > 0 else None
    )
    return result


def _criterion(
    rule: str,
    observed: float | None,
    threshold: float,
    predicate: Callable[[float, float], bool],
) -> dict[str, Any]:
    return {
        "status": "insufficient_data" if observed is None else (
            "pass" if predicate(observed, threshold) else "fail"
        ),
        "rule": rule,
        "observed": observed,
        "threshold": threshold,
    }


def _composite(criteria: list[dict[str, Any]]) -> str:
    statuses = {criterion["status"] for criterion in criteria}
    if "fail" in statuses:
        return "fail"
    if "insufficient_data" in statuses:
        return "insufficient_data"
    return "pass"


def _treatment_summary(rows: list[dict[str, Any]], bands: list[str]) -> dict[str, Any]:
    return {
        "deadline_miss_ratio": confidence_interval([row["deadline_miss_ratio"] for row in rows]),
        "completed_frame_p99_us": confidence_interval(
            [row["completed_frame_p99_us"] for row in rows]
        ),
        "background_throughput_mbps": confidence_interval(
            [row["background_throughput_mbps"] for row in rows]
        ),
        "sender_phy_tx_airtime": {
            "summed_us": confidence_interval([row["sender_airtime_total_us"] for row in rows]),
            "summed_fraction": confidence_interval(
                [row["sender_airtime_total_fraction"] for row in rows]
            ),
            "per_band_us": {
                band: confidence_interval([row["sender_airtime_by_band_us"][band] for row in rows])
                for band in bands
            },
            "per_band_fraction": {
                band: confidence_interval(
                    [row["sender_airtime_by_band_fraction"][band] for row in rows]
                )
                for band in bands
            },
        },
    }


def _adaptive_diagnostics(
    rows: list[dict[str, Any]], thresholds: Thresholds
) -> tuple[dict[str, Any], dict[str, Any]]:
    meters = [row["secondary_meter"] for row in rows]
    metric_names = (
        "tagged_secondary_tx_airtime_us",
        "tagged_secondary_tx_airtime_fraction",
        "budget_fraction",
        "initial_bucket_capacity_us",
        "finite_run_budget_us",
        "maximum_budget_debt_us",
        "estimated_action_airtime_us",
        "actual_to_estimated_airtime_ratio",
        "forced_reservation_settlements",
        "budget_excess_us",
    )
    diagnostics = {
        name: confidence_interval([meter[name] for meter in meters]) for name in metric_names
    }
    diagnostics["finite_run_budget_fraction"] = confidence_interval([
        meter["finite_run_budget_us"] / row["measurement_duration_us"]
        for meter, row in zip(meters, rows)
    ])
    diagnostics["maximum_observed_budget_debt_us"] = max(
        meter["maximum_budget_debt_us"] for meter in meters
    )
    diagnostics["maximum_observed_budget_excess_us"] = max(
        meter["budget_excess_us"] for meter in meters
    )
    diagnostics["total_forced_reservation_settlements"] = int(sum(
        meter["forced_reservation_settlements"] for meter in meters
    ))
    criteria = {
        "finite_run_budget_conformance": _criterion(
            "maximum observed budget excess <= configured tolerance",
            diagnostics["maximum_observed_budget_excess_us"],
            thresholds.max_budget_excess_us,
            lambda observed, threshold: observed <= threshold,
        )
    }
    if thresholds.max_budget_debt_us is not None:
        criteria["maximum_budget_debt"] = _criterion(
            "maximum observed reservation debt <= configured limit",
            diagnostics["maximum_observed_budget_debt_us"],
            thresholds.max_budget_debt_us,
            lambda observed, threshold: observed <= threshold,
        )
    return diagnostics, criteria


def _comparison(
    adaptive: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
    bands: list[str],
    thresholds: Thresholds,
    controller_criterion: dict[str, Any],
) -> dict[str, Any]:
    adaptive_miss = [row["deadline_miss_ratio"] for row in adaptive]
    baseline_miss = [row["deadline_miss_ratio"] for row in baseline]
    adaptive_p99 = [row["completed_frame_p99_us"] for row in adaptive]
    baseline_p99 = [row["completed_frame_p99_us"] for row in baseline]
    adaptive_airtime = [row["sender_airtime_total_fraction"] for row in adaptive]
    baseline_airtime = [row["sender_airtime_total_fraction"] for row in baseline]
    adaptive_airtime_us = [row["sender_airtime_total_us"] for row in adaptive]
    baseline_airtime_us = [row["sender_airtime_total_us"] for row in baseline]
    adaptive_background = [row["background_throughput_mbps"] for row in adaptive]
    baseline_background = [row["background_throughput_mbps"] for row in baseline]

    miss_difference = confidence_interval([
        left - right for left, right in zip(adaptive_miss, baseline_miss)
    ])
    miss_relative = _relative_summary(
        adaptive_miss, baseline_miss, lambda left, right: (right - left) / right
    )
    p99_difference = confidence_interval([
        left - right for left, right in zip(adaptive_p99, baseline_p99)
    ])
    p99_relative = _relative_summary(
        adaptive_p99, baseline_p99, lambda left, right: (right - left) / right
    )
    airtime_difference = confidence_interval([
        left - right for left, right in zip(adaptive_airtime, baseline_airtime)
    ])
    airtime_us_difference = confidence_interval([
        left - right for left, right in zip(adaptive_airtime_us, baseline_airtime_us)
    ])
    airtime_relative = _relative_summary(
        adaptive_airtime, baseline_airtime, lambda left, right: (left - right) / right
    )
    background_difference = confidence_interval([
        left - right for left, right in zip(adaptive_background, baseline_background)
    ])
    background_relative = _relative_summary(
        adaptive_background,
        baseline_background,
        lambda left, right: (left - right) / right,
    )

    per_band: dict[str, Any] = {}
    for band in bands:
        left = [row["sender_airtime_by_band_fraction"][band] for row in adaptive]
        right = [row["sender_airtime_by_band_fraction"][band] for row in baseline]
        left_us = [row["sender_airtime_by_band_us"][band] for row in adaptive]
        right_us = [row["sender_airtime_by_band_us"][band] for row in baseline]
        per_band[band] = {
            "paired_us_difference_adaptive_minus_mlo": confidence_interval([
                first - second for first, second in zip(left_us, right_us)
            ]),
            "paired_fraction_difference_adaptive_minus_mlo": confidence_interval([
                first - second for first, second in zip(left, right)
            ]),
            "paired_relative_increase": _relative_summary(
                left, right, lambda first, second: (first - second) / second
            ),
        }

    criteria = {
        "deadline_miss_ratio_ci_below_zero": _criterion(
            "upper 95% CI of adaptive-minus-MLO paired difference < 0",
            miss_difference["ci95_high"],
            0.0,
            lambda observed, threshold: observed < threshold,
        ),
        "completed_frame_p99_ci_below_zero": _criterion(
            "upper 95% CI of adaptive-minus-MLO paired difference < 0",
            p99_difference["ci95_high"],
            0.0,
            lambda observed, threshold: observed < threshold,
        ),
        "deadline_miss_ratio_relative_ambition": _criterion(
            "ratio-of-paired-means relative improvement > target",
            miss_relative["ratio_of_paired_means"],
            thresholds.min_relative_improvement,
            lambda observed, threshold: observed > threshold,
        ),
        "completed_frame_p99_relative_ambition": _criterion(
            "ratio-of-paired-means relative improvement > target",
            p99_relative["ratio_of_paired_means"],
            thresholds.min_relative_improvement,
            lambda observed, threshold: observed > threshold,
        ),
        "summed_sender_airtime_cost": _criterion(
            "ratio-of-paired-means relative increase < limit",
            airtime_relative["ratio_of_paired_means"],
            thresholds.max_airtime_increase,
            lambda observed, threshold: observed < threshold,
        ),
        "background_throughput_preservation": _criterion(
            "ratio-of-paired-means relative change >= negative loss limit",
            background_relative["ratio_of_paired_means"],
            -thresholds.max_background_throughput_loss,
            lambda observed, threshold: observed >= threshold,
        ),
        "summed_sender_airtime_cost_confidence_bound": _criterion(
            "upper 95% CI of paired relative increase < limit",
            airtime_relative["ci95_high"],
            thresholds.max_airtime_increase,
            lambda observed, threshold: observed < threshold,
        ),
        "background_throughput_confidence_bound": _criterion(
            "lower 95% CI of paired relative change >= negative loss limit",
            background_relative["ci95_low"],
            -thresholds.max_background_throughput_loss,
            lambda observed, threshold: observed >= threshold,
        ),
    }
    defeat_members = [
        criteria["deadline_miss_ratio_ci_below_zero"],
        criteria["completed_frame_p99_ci_below_zero"],
        criteria["summed_sender_airtime_cost"],
        criteria["background_throughput_preservation"],
        controller_criterion,
    ]
    conservative_members = defeat_members[:2] + [
        criteria["summed_sender_airtime_cost_confidence_bound"],
        criteria["background_throughput_confidence_bound"],
        controller_criterion,
    ]
    ideal_members = defeat_members + [
        criteria["deadline_miss_ratio_relative_ambition"],
        criteria["completed_frame_p99_relative_ambition"],
    ]
    return {
        "deadline_miss_ratio": {
            "paired_difference_adaptive_minus_mlo": miss_difference,
            "paired_relative_improvement": miss_relative,
        },
        "completed_frame_p99_us": {
            "paired_difference_adaptive_minus_mlo": p99_difference,
            "paired_relative_improvement": p99_relative,
        },
        "summed_sender_phy_tx_airtime": {
            "paired_us_difference_adaptive_minus_mlo": airtime_us_difference,
            "paired_fraction_difference_adaptive_minus_mlo": airtime_difference,
            "paired_relative_increase": airtime_relative,
            "per_band": per_band,
        },
        "background_throughput_mbps": {
            "paired_difference_adaptive_minus_mlo": background_difference,
            "paired_relative_change": background_relative,
        },
        "criteria": criteria,
        "defeat_status": _composite(defeat_members),
        "conservative_defeat_status": _composite(conservative_members),
        "ideal_pareto_status": _composite(ideal_members),
    }


def analyze_campaign(
    inputs: Path | list[Path] | tuple[Path, ...],
    thresholds: Thresholds = Thresholds(),
    expected_obss_profile: str | None = "mixed4x4",
) -> dict[str, Any]:
    """Merge, pair, validate, and summarize the three headline treatments."""
    raw_inputs = [inputs] if isinstance(inputs, Path) else list(inputs)
    if not raw_inputs:
        raise CampaignError("at least one result root or aggregate is required")
    aggregate_paths = [_resolve_aggregate(path) for path in raw_inputs]
    indexes: dict[str, dict[tuple[int, int], dict[str, Any]]] = {
        name: {} for name in TREATMENTS
    }
    ignored_run_count = 0
    for aggregate_path in aggregate_paths:
        aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
        runs = aggregate.get("runs")
        if not isinstance(runs, list):
            raise CampaignError(f"{aggregate_path}: aggregate must contain a runs list")
        result_root = aggregate_path.parent
        for run in runs:
            treatment = _treatment_name(run)
            if treatment is None:
                ignored_run_count += 1
                continue
            run_dir = _run_directory(run, result_root)
            observation = _observation(run, run_dir, treatment, expected_obss_profile)
            observation["source_aggregate"] = str(aggregate_path)
            pair = observation["pair"]
            if pair in indexes[treatment]:
                previous = indexes[treatment][pair]["source_aggregate"]
                raise CampaignError(
                    f"duplicate {TREATMENT_LABELS[treatment]} treatment for seed/run {pair}: "
                    f"{previous} and {aggregate_path}"
                )
            indexes[treatment][pair] = observation

    for treatment, index in indexes.items():
        if not index:
            raise CampaignError(f"campaign has no {TREATMENT_LABELS[treatment]} runs")
    pair_sets = {name: set(index) for name, index in indexes.items()}
    all_pairs = set.union(*pair_sets.values())
    if any(pairs != all_pairs for pairs in pair_sets.values()):
        details = []
        for name in TREATMENTS:
            missing = sorted(all_pairs - pair_sets[name])
            if missing:
                details.append(f"{TREATMENT_LABELS[name]} missing {missing}")
        raise CampaignError("incomplete paired treatment matrix: " + "; ".join(details))
    pairs = sorted(all_pairs)

    for pair in pairs:
        environments = {
            name: _canonical_json(indexes[name][pair]["environment"]) for name in TREATMENTS
        }
        if len(set(environments.values())) != 1:
            raise CampaignError(
                f"seed/run {pair} does not share matched environment and target-radio conditions"
            )

    build_identities = {
        _canonical_json(row["build_identity"])
        for index in indexes.values() for row in index.values()
    }
    if len(build_identities) != 1:
        raise CampaignError(
            "merged headline treatments have inconsistent project/ns-3 commits or versions"
        )
    build_identity = json.loads(next(iter(build_identities)))

    nominal_hashes: dict[str, str] = {}
    for name, index in indexes.items():
        hashes = {_sha256_json(row["nominal_config"]) for row in index.values()}
        if len(hashes) != 1:
            raise CampaignError(
                f"{TREATMENT_LABELS[name]} nominal configuration changed within campaign"
            )
        nominal_hashes[name] = next(iter(hashes))

    ordered = {
        name: [indexes[name][pair] for pair in pairs] for name in TREATMENTS
    }
    band_sets = {
        frozenset(row["sender_airtime_by_band_us"])
        for rows in ordered.values() for row in rows
    }
    if len(band_sets) != 1:
        raise CampaignError("headline treatments do not use the same frequency bands")
    bands = sorted(next(iter(band_sets)))
    adaptive_diagnostics, adaptive_criteria = _adaptive_diagnostics(
        ordered["adaptive"], thresholds
    )
    controller_criterion = {
        "status": _composite(list(adaptive_criteria.values()))
    }
    comparisons = {
        baseline: _comparison(
            ordered["adaptive"], ordered[baseline], bands, thresholds, controller_criterion
        )
        for baseline in ("str_mlo", "emlsr_mlo")
    }

    first_environment = ordered["adaptive"][0]["environment"]
    background = first_environment["background"]
    obss = background.get("obss", {})
    return {
        "schema_version": 1,
        "analysis": "neutral_adaptive_vs_str_and_emlsr_mlo",
        "independent_sample_unit": ["seed", "run"],
        "confidence_interval": "two-sided 95% Student-t interval over paired run units",
        "paired_unit_count": len(pairs),
        "paired_units": [{"seed": seed, "run": run_number} for seed, run_number in pairs],
        "source_aggregates": [str(path) for path in aggregate_paths],
        "ignored_nonheadline_run_count": ignored_run_count,
        "campaign_checks": {
            "complete_three_treatment_pairs": True,
            "paired_environment_realizations_match": True,
            "nominal_configuration_is_frozen_per_treatment": True,
            "expected_obss_profile": expected_obss_profile,
            "observed_obss_profile": obss.get("profile"),
            "obss_bss_count": len(obss.get("bsses", [])),
            "obss_stations_per_bss": obss.get("stations_per_bss"),
            "background_correlation_mode": background.get("correlation", {}).get("mode"),
            "propagation_model": first_environment["propagation"].get("model"),
            "measurement_duration_s": (
                first_environment["measurement_stop_s"] -
                first_environment["measurement_start_s"]
            ),
            "build_identity": build_identity,
            "nominal_config_sha256": nominal_hashes,
            "paired_environment_sha256": [
                {
                    "seed": pair[0],
                    "run": pair[1],
                    "sha256": _sha256_json(indexes["adaptive"][pair]["environment"]),
                }
                for pair in pairs
            ],
        },
        "thresholds": {
            "min_relative_improvement_fraction": thresholds.min_relative_improvement,
            "max_summed_sender_airtime_increase_fraction": thresholds.max_airtime_increase,
            "max_background_throughput_loss_fraction": thresholds.max_background_throughput_loss,
            "max_secondary_budget_excess_us": thresholds.max_budget_excess_us,
            "max_secondary_budget_debt_us": thresholds.max_budget_debt_us,
        },
        "treatments": {
            name: {
                "label": TREATMENT_LABELS[name],
                **_treatment_summary(rows, bands),
            }
            for name, rows in ordered.items()
        },
        "comparisons": comparisons,
        "adaptive_secondary_budget_diagnostics": adaptive_diagnostics,
        "adaptive_secondary_budget_criteria": adaptive_criteria,
        "adaptive_controller_integrity_status": controller_criterion["status"],
        "overall_status": {
            "defeats_both_mlo_modes": _composite([
                controller_criterion,
                *[
                    {"status": comparisons[name]["defeat_status"]}
                    for name in ("str_mlo", "emlsr_mlo")
                ],
            ]),
            "conservatively_defeats_both_mlo_modes": _composite([
                controller_criterion,
                *[
                    {"status": comparisons[name]["conservative_defeat_status"]}
                    for name in ("str_mlo", "emlsr_mlo")
                ],
            ]),
            "meets_ideal_pareto_goal_against_both": _composite([
                controller_criterion,
                *[
                    {"status": comparisons[name]["ideal_pareto_status"]}
                    for name in ("str_mlo", "emlsr_mlo")
                ],
            ]),
        },
    }


def _format_interval(item: dict[str, Any], scale: float = 1.0, digits: int = 3) -> str:
    if item["mean"] is None:
        return "n/a"
    mean = item["mean"] * scale
    if item["ci95_low"] is None:
        return f"{mean:.{digits}f} (CI unavailable)"
    low = item["ci95_low"] * scale
    high = item["ci95_high"] * scale
    return f"{mean:.{digits}f} [{low:.{digits}f}, {high:.{digits}f}]"


def _format_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.2f}%"


def render_markdown(report: dict[str, Any]) -> str:
    """Render the compact human report; the JSON form retains all diagnostics."""
    checks = report["campaign_checks"]
    thresholds = report["thresholds"]
    background_loss = 100 * thresholds["max_background_throughput_loss_fraction"]
    lines = [
        "# Neutral adaptive-versus-MLO campaign",
        "",
        f"Paired seed/run units: {report['paired_unit_count']}. "
        f"OBSS profile: {checks['observed_obss_profile']}; "
        f"measurement: {checks['measurement_duration_s']} s per run.",
        "",
        "Thresholds: more than "
        f"{100 * thresholds['min_relative_improvement_fraction']:.1f}% relative improvement, "
        f"less than {100 * thresholds['max_summed_sender_airtime_increase_fraction']:.1f}% "
        "summed sender-airtime increase, and no more than "
        f"{background_loss:.1f}% background-throughput loss.",
        "",
    ]
    for baseline in ("str_mlo", "emlsr_mlo"):
        label = TREATMENT_LABELS[baseline]
        comparison = report["comparisons"][baseline]
        miss = comparison["deadline_miss_ratio"]
        p99 = comparison["completed_frame_p99_us"]
        airtime = comparison["summed_sender_phy_tx_airtime"]
        background = comparison["background_throughput_mbps"]
        miss_delta = _format_interval(
            miss["paired_difference_adaptive_minus_mlo"], 100, 4
        )
        miss_relative = _format_percent(
            miss["paired_relative_improvement"]["ratio_of_paired_means"]
        )
        p99_delta = _format_interval(
            p99["paired_difference_adaptive_minus_mlo"], 0.001, 3
        )
        p99_relative = _format_percent(
            p99["paired_relative_improvement"]["ratio_of_paired_means"]
        )
        airtime_delta = _format_interval(
            airtime["paired_fraction_difference_adaptive_minus_mlo"], 100, 3
        )
        airtime_relative = _format_percent(
            airtime["paired_relative_increase"]["ratio_of_paired_means"]
        )
        background_delta = _format_interval(
            background["paired_difference_adaptive_minus_mlo"], 1, 3
        )
        background_relative = _format_percent(
            background["paired_relative_change"]["ratio_of_paired_means"]
        )
        lines += [
            f"## Adaptive versus {label}",
            "",
            "| Measure | Paired adaptive-minus-MLO effect (95% CI) | "
            "Relative point effect | Criterion |",
            "|---|---:|---:|:---:|",
            "| Deadline miss ratio | "
            f"{miss_delta} pp | {miss_relative} improvement | "
            f"{comparison['criteria']['deadline_miss_ratio_ci_below_zero']['status'].upper()} |",
            "| Completed-frame P99 | "
            f"{p99_delta} ms | {p99_relative} improvement | "
            f"{comparison['criteria']['completed_frame_p99_ci_below_zero']['status'].upper()} |",
            "| Summed target sender PHY TX airtime | "
            f"{airtime_delta} pp | {airtime_relative} increase | "
            f"{comparison['criteria']['summed_sender_airtime_cost']['status'].upper()} |",
            "| Background throughput | "
            f"{background_delta} Mbps | {background_relative} change | "
            f"{comparison['criteria']['background_throughput_preservation']['status'].upper()} |",
            "",
            "Per-band sender-airtime change:",
            "",
            "| Band | Fraction change (percentage points, 95% CI) | Relative point increase |",
            "|---|---:|---:|",
        ]
        for band, values in airtime["per_band"].items():
            band_delta = _format_interval(
                values["paired_fraction_difference_adaptive_minus_mlo"], 100, 3
            )
            band_relative = _format_percent(
                values["paired_relative_increase"]["ratio_of_paired_means"]
            )
            lines.append(
                f"| {band} | {band_delta} | {band_relative} |"
            )
        miss_ambition = comparison["criteria"][
            "deadline_miss_ratio_relative_ambition"
        ]["status"].upper()
        p99_ambition = comparison["criteria"][
            "completed_frame_p99_relative_ambition"
        ]["status"].upper()
        lines += [
            "",
            f"Relative-improvement ambitions: miss ratio **{miss_ambition}**; "
            f"completed-frame P99 **{p99_ambition}**.",
            "",
            f"Defeat status: **{comparison['defeat_status'].upper()}**. "
            f"Conservative cost/background-CI status: "
            f"**{comparison['conservative_defeat_status'].upper()}**. "
            f"Ideal Pareto status: **{comparison['ideal_pareto_status'].upper()}**.",
            "",
        ]

    diagnostics = report["adaptive_secondary_budget_diagnostics"]
    lines += [
        "## Adaptive secondary budget diagnostics",
        "",
        "| Diagnostic | Run-level mean (95% CI) or maximum |",
        "|---|---:|",
        "| Tagged secondary PHY TX fraction | "
        f"{_format_interval(diagnostics['tagged_secondary_tx_airtime_fraction'], 100, 3)}% |",
        "| Finite-run budget fraction | "
        f"{_format_interval(diagnostics['finite_run_budget_fraction'], 100, 3)}% |",
        "| Maximum reservation debt | "
        f"{diagnostics['maximum_observed_budget_debt_us']:.3f} us |",
        "| Maximum finite-run budget excess | "
        f"{diagnostics['maximum_observed_budget_excess_us']:.3f} us |",
        "| Actual / estimated action airtime | "
        f"{_format_interval(diagnostics['actual_to_estimated_airtime_ratio'], 1, 3)} |",
        "| Forced reservation settlements | "
        f"{diagnostics['total_forced_reservation_settlements']} total |",
        "",
        "Adaptive controller integrity **"
        f"{report['adaptive_controller_integrity_status'].upper()}**.",
        "",
        "Overall: defeats both MLO modes **"
        f"{report['overall_status']['defeats_both_mlo_modes'].upper()}**; "
        "ideal Pareto goal against both **"
        f"{report['overall_status']['meets_ideal_pareto_goal_against_both'].upper()}**.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs",
        type=Path,
        nargs="+",
        help="result roots, runs directories, or aggregate.json files to merge",
    )
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--min-relative-improvement", type=_fraction, default=0.50)
    parser.add_argument("--max-airtime-increase", type=_fraction, default=0.20)
    parser.add_argument("--max-background-throughput-loss", type=_fraction, default=0.05)
    parser.add_argument("--max-budget-excess-us", type=_nonnegative, default=1.0)
    parser.add_argument("--max-budget-debt-us", type=_nonnegative)
    parser.add_argument("--expected-obss-profile", default="mixed4x4")
    parser.add_argument(
        "--require-defeat",
        action="store_true",
        help="exit with status 1 unless adaptive defeats both MLO treatments",
    )
    args = parser.parse_args()
    thresholds = Thresholds(
        min_relative_improvement=args.min_relative_improvement,
        max_airtime_increase=args.max_airtime_increase,
        max_background_throughput_loss=args.max_background_throughput_loss,
        max_budget_excess_us=args.max_budget_excess_us,
        max_budget_debt_us=args.max_budget_debt_us,
    )
    try:
        report = analyze_campaign(
            args.inputs,
            thresholds,
            expected_obss_profile=args.expected_obss_profile,
        )
    except (CampaignError, json.JSONDecodeError, OSError) as error:
        parser.error(str(error))
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    if args.require_defeat and report["overall_status"]["defeats_both_mlo_modes"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
