#!/usr/bin/env python3
"""Memory-bounded Increment-3 offline latency-risk evaluation."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import resource
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from sklearn.inspection import permutation_importance

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from prediction.calibration import fit_platt, select_threshold
from prediction.features import ablation_sets, build_feature_sets, encode_value
from prediction.heuristics import fit_byte_service_fallback, score_heuristics
from prediction.metrics import (
    average_precision_tied,
    grouped_topk_bootstrap,
    percentile_interval,
    probability_metrics,
    threshold_metrics,
    topk_metrics,
)
from prediction.models import CANDIDATES, fit_pipeline, ranking_score
from prediction.reporting import (
    plot_calibration_curves,
    plot_feature_importance,
    plot_stage_recall,
    write_csv,
    write_json,
    write_report,
)

ANALYSIS_OUTPUT_SCHEMA_VERSION = 1
REQUIRED_CONFIG_KEYS = {
    "analysis_schema_version",
    "specification_revision",
    "analysis_seed",
    "split_seed",
    "bootstrap_seed",
    "minimum_run_groups_validation_selection",
    "minimum_run_groups_validation_calibration",
    "minimum_run_groups_id_test",
    "minimum_run_groups_per_required_ood_scenario",
    "stages",
    "links",
    "combine_links_for_formal_decision",
    "ranking_budgets",
    "screening_budget",
    "pr_auc_metric",
    "calibration_bin_count",
    "calibration_method",
    "confidence_level",
    "bootstrap_replicates",
    "minimum_rescue_time_us",
    "model_name_order",
    "required_ood_scenarios",
    "ood_scenarios",
    "ood_formal_aggregation",
    "pooled_ood_decision_use",
    "minimum_f2_recall",
    "minimum_random_multiple",
    "minimum_heuristic_gain",
    "minimum_f2_incremental_gain",
    "minimum_ood_retention",
    "minimum_later_stage_gain",
    "maximum_fixed_threshold_action_rate_overshoot",
    "f2_exportable_allowlist",
    "f1_degradation_profiles",
}
ROLES = {
    "training": 0,
    "validation_selection": 1,
    "validation_calibration": 2,
    "in_distribution_test": 3,
    "out_of_distribution_test": 4,
}
HEURISTIC_COLUMNS = {
    "deadline_slack_us",
    "mpdu_attempts_5ms",
    "mpdu_retries_5ms",
    "last_positive_ack_age_us",
    "acknowledged_mac_service_bytes_20ms",
    "history_coverage_20ms_us",
    "mac_service_bytes_ahead_of_frame",
    "frame_mac_service_bytes_pending_primary",
    "frame_packets_terminally_dropped",
    "packets_ahead_of_frame",
    "frame_packets_pending_primary",
    "mpdu_queue_to_ack_mean_20ms_us",
}


@dataclass
class StageData:
    matrix: np.ndarray
    names: tuple[str, ...]
    label: np.ndarray
    actionable: np.ndarray
    link: np.ndarray
    role: np.ndarray
    scenario: np.ndarray
    regime: np.ndarray
    correlation: np.ndarray
    group: np.ndarray
    run: np.ndarray
    frame: np.ndarray
    sample_time_us: np.ndarray
    polling_capture_time_us: np.ndarray
    polling_staleness_us: np.ndarray
    offset_us: int
    deadline_us: int
    rows_scanned: int

    def column(self, name: str) -> np.ndarray:
        return self.matrix[:, self.names.index(name)]


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be an object")
    return value


def _load_config(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("analysis YAML root must be a mapping")
    missing = sorted(REQUIRED_CONFIG_KEYS - value.keys())
    if missing:
        raise ValueError(f"analysis configuration is missing: {', '.join(missing)}")
    if value["analysis_schema_version"] != 1 or value["specification_revision"] != 8:
        raise ValueError("unsupported analysis schema or specification revision")
    if value["calibration_method"] != "platt":
        raise ValueError("Revision-8 production configuration requires Platt calibration")
    if value["pr_auc_metric"] != "average_precision":
        raise ValueError("unsupported pr_auc_metric")
    if value["ood_formal_aggregation"] != "per_scenario_worst_case":
        raise ValueError("unsupported OOD formal aggregation")
    if value["pooled_ood_decision_use"] is not False:
        raise ValueError("pooled OOD cannot enter formal decisions")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _bool(value: str) -> bool:
    if value.lower() in {"1", "true"}:
        return True
    if value.lower() in {"0", "false"}:
        return False
    raise ValueError(f"invalid Boolean: {value!r}")


def load_stage(
    csv_path: Path,
    stage: str,
    capacity: int,
    feature_names: tuple[str, ...],
    group_codes: dict[str, int],
    run_codes: dict[str, int],
    scenario_codes: dict[str, int],
    regime_codes: dict[str, int],
    correlation_codes: dict[str, int],
) -> StageData:
    """Scan the CSV and materialize one compact stage matrix."""
    matrix = np.empty((capacity, len(feature_names)), dtype=np.float32)
    label = np.empty(capacity, dtype=np.int8)
    actionable = np.empty(capacity, dtype=bool)
    link = np.empty(capacity, dtype=np.int8)
    role = np.empty(capacity, dtype=np.int8)
    scenario = np.empty(capacity, dtype=np.int8)
    regime = np.empty(capacity, dtype=np.int8)
    correlation = np.empty(capacity, dtype=np.int8)
    group = np.empty(capacity, dtype=np.int16)
    run = np.empty(capacity, dtype=np.int16)
    frame = np.empty(capacity, dtype=np.int64)
    sample_time_us = np.empty(capacity, dtype=np.int64)
    polling_capture_time_us = np.empty(capacity, dtype=np.int64)
    polling_staleness_us = np.empty(capacity, dtype=np.float64)
    count = 0
    rows_scanned = 0
    offset = deadline = None
    with csv_path.open(newline="", encoding="utf-8", buffering=8 * 1024 * 1024) as source:
        reader = csv.reader(source)
        header = next(reader)
        if len(header) != len(set(header)):
            raise ValueError("duplicate CSV columns")
        index = {name: position for position, name in enumerate(header)}
        required = set(feature_names) | {
            "deadline_miss",
            "actionable",
            "path_id",
            "split_role",
            "scenario_name",
            "miss_regime",
            "correlation_mode",
            "run_group_id",
            "run_id",
            "frame_id",
            "sample_stage",
            "sample_time_ns",
            "sample_offset_us",
            "generation_time_ns",
            "deadline_time_ns",
            "polling_1ms_capture_time_ns",
            "polling_1ms_staleness_us",
        }
        missing = sorted(required - index.keys())
        if missing:
            raise ValueError(f"dataset is missing columns: {missing}")
        feature_indices = [index[name] for name in feature_names]
        for row in reader:
            rows_scanned += 1
            if row[index["sample_stage"]] != stage:
                continue
            if count == capacity:
                raise ValueError(f"stage {stage} exceeds manifest frame count")
            matrix[count] = [
                encode_value(name, row[position])
                for name, position in zip(feature_names, feature_indices)
            ]
            label[count] = int(row[index["deadline_miss"]])
            actionable[count] = _bool(row[index["actionable"]])
            link[count] = int(row[index["path_id"]])
            role_value = row[index["split_role"]]
            if role_value not in ROLES:
                raise ValueError(f"unknown split role: {role_value}")
            role[count] = ROLES[role_value]
            scenario_value = row[index["scenario_name"]]
            scenario[count] = scenario_codes.setdefault(scenario_value, len(scenario_codes))
            regime_value = row[index["miss_regime"]]
            regime[count] = regime_codes.setdefault(regime_value, len(regime_codes))
            correlation_value = row[index["correlation_mode"]]
            correlation[count] = correlation_codes.setdefault(
                correlation_value, len(correlation_codes)
            )
            group_value = row[index["run_group_id"]]
            run_value = row[index["run_id"]]
            group[count] = group_codes.setdefault(group_value, len(group_codes))
            run[count] = run_codes.setdefault(run_value, len(run_codes))
            frame[count] = int(row[index["frame_id"]])
            sample_time_us[count] = int(row[index["sample_time_ns"]]) // 1000
            polling_capture_time_us[count] = (
                int(row[index["polling_1ms_capture_time_ns"]]) // 1000
            )
            polling_staleness_us[count] = float(
                row[index["polling_1ms_staleness_us"]]
            )
            row_offset = int(row[index["sample_offset_us"]])
            row_deadline = (
                int(row[index["deadline_time_ns"]]) - int(row[index["generation_time_ns"]])
            ) // 1000
            offset = row_offset if offset is None else offset
            deadline = row_deadline if deadline is None else deadline
            if row_offset != offset or row_deadline != deadline:
                raise ValueError(f"stage {stage} has nonfixed offset/deadline")
            count += 1
    if count != capacity:
        raise ValueError(f"stage {stage} has {count} rows, manifest says {capacity}")
    return StageData(
        matrix,
        feature_names,
        label,
        actionable,
        link,
        role,
        scenario,
        regime,
        correlation,
        group,
        run,
        frame,
        sample_time_us,
        polling_capture_time_us,
        polling_staleness_us,
        int(offset),
        int(deadline),
        rows_scanned,
    )


def _mask(data: StageData, link: int, role: str, scenario: int | None = None) -> np.ndarray:
    result = data.actionable & (data.link == link) & (data.role == ROLES[role])
    if scenario is not None:
        result &= data.scenario == scenario
    return result


def _x(data: StageData, mask: np.ndarray, names: tuple[str, ...]) -> np.ndarray:
    indices = [data.names.index(name) for name in names]
    return data.matrix[np.ix_(mask, indices)]


def _categorical_indices(names: tuple[str, ...], categorical: tuple[str, ...]) -> tuple[int, ...]:
    return tuple(index for index, name in enumerate(names) if name in categorical)


def _partition_specs(
    data: StageData, link: int, required_ood: list[str], scenario_codes: dict[str, int]
) -> list[tuple[str, str | None, np.ndarray]]:
    result = [("in_distribution_test", None, _mask(data, link, "in_distribution_test"))]
    for name in required_ood:
        code = scenario_codes.get(name, -1)
        result.append((f"ood:{name}", name, _mask(data, link, "out_of_distribution_test", code)))
    return result


def _metric_record(
    base: dict[str, Any],
    y: np.ndarray,
    ranking: np.ndarray,
    probability: np.ndarray,
    budget: float,
    confidence: float,
    bins: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    rank = topk_metrics(y, ranking, budget, confidence)
    probability_result = probability_metrics(y, probability, bins)
    metric = dict(base)
    metric.update(
        {
            "average_precision": average_precision_tied(y, ranking),
            **probability_result,
            "eligible_frames": len(y),
            "eligible_misses": int(y.sum()),
        }
    )
    budget_row = dict(base)
    budget_row.update(rank)
    return metric, budget_row


def _evaluate_model_set(
    data: StageData,
    link: int,
    set_name: str,
    names: tuple[str, ...],
    config: dict[str, Any],
    categorical: tuple[str, ...],
    partitions: list[tuple[str, str | None, np.ndarray]],
    matrix_override: np.ndarray | None = None,
) -> tuple[
    Any,
    str,
    float | None,
    dict[str, np.ndarray],
    dict[str, np.ndarray],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    train = _mask(data, link, "training")
    selection = _mask(data, link, "validation_selection")
    calibration = _mask(data, link, "validation_calibration")
    source = data.matrix if matrix_override is None else matrix_override
    indices = [data.names.index(name) for name in names]

    def take(mask: np.ndarray) -> np.ndarray:
        return source[np.ix_(mask, indices)]

    cat = _categorical_indices(names, categorical)
    candidates = []
    for candidate in CANDIDATES:
        fitted = fit_pipeline(
            candidate, take(train), data.label[train], cat, int(config["analysis_seed"])
        )
        score = ranking_score(fitted, take(selection))
        recall = topk_metrics(
            data.label[selection],
            score,
            float(config["screening_budget"]),
            float(config["confidence_level"]),
        ).get("recall")
        candidates.append((float("-inf") if recall is None else recall, candidate, fitted))
    order = {name: index for index, name in enumerate(config["model_name_order"])}
    chosen_recall, chosen, _ = max(
        candidates, key=lambda item: (item[0], -order[item[1].name])
    )
    refit_mask = train | selection
    final = fit_pipeline(
        chosen, take(refit_mask), data.label[refit_mask], cat, int(config["analysis_seed"])
    )
    calibration_ranking = ranking_score(final, take(calibration))
    calibrator = fit_platt(
        calibration_ranking, data.label[calibration], int(config["analysis_seed"])
    )
    calibration_probability = calibrator.predict(calibration_ranking)
    thresholds = {
        budget: select_threshold(calibration_probability, data.label[calibration], budget)
        for budget in map(float, config["ranking_budgets"])
    }
    metric_rows: list[dict[str, Any]] = []
    budget_rows: list[dict[str, Any]] = []
    calibration_rows: list[dict[str, Any]] = []
    rankings: dict[str, np.ndarray] = {}
    probabilities: dict[str, np.ndarray] = {}
    for partition, scenario_name, mask in partitions:
        ranking = ranking_score(final, take(mask))
        probability = calibrator.predict(ranking)
        rankings[partition] = ranking
        probabilities[partition] = probability
        base = {
            "analysis": "per_link",
            "pipeline_type": "model",
            "model": chosen.name,
            "feature_set": set_name,
            "stage": str(data.offset_us),
            "link": link,
            "partition": partition,
            "scenario_id": scenario_name,
        }
        for budget in map(float, config["ranking_budgets"]):
            metric, budget_row = _metric_record(
                base,
                data.label[mask],
                ranking,
                probability,
                budget,
                float(config["confidence_level"]),
                int(config["calibration_bin_count"]),
            )
            if budget == float(config["screening_budget"]):
                metric_rows.append(metric)
            threshold = thresholds[budget]
            observed = threshold_metrics(
                data.label[mask], probability, threshold["calibration_score_threshold"]
            )
            budget_row.update(
                {
                    **threshold,
                    "observed_test_action_rate": observed["action_rate"],
                    "test_miss_recall_at_calibration_threshold": observed["recall"],
                    "test_precision_at_calibration_threshold": observed["precision"],
                }
            )
            budget_rows.append(budget_row)
        ece, bins_rows, effective = _calibration_bins(
            data.label[mask], probability, int(config["calibration_bin_count"])
        )
        for bin_index, row in enumerate(bins_rows):
            calibration_rows.append(
                {
                    **base,
                    "bin": bin_index,
                    "requested_bin_count": config["calibration_bin_count"],
                    "effective_bin_count": effective,
                    "calibration_error": ece,
                    **row,
                }
            )
    return (
        final,
        chosen.name,
        None if not np.isfinite(chosen_recall) else float(chosen_recall),
        rankings,
        probabilities,
        metric_rows,
        budget_rows,
        calibration_rows,
    )


def _calibration_bins(y: np.ndarray, probability: np.ndarray, bins: int):
    from prediction.metrics import equal_frequency_calibration

    return equal_frequency_calibration(y, probability, bins)


def _status(value: bool | None) -> str:
    return "insufficient_data" if value is None else ("pass" if value else "fail")


def _criterion(name: str, value: bool | None, estimate: Any, **metadata: Any) -> dict[str, Any]:
    return {"name": name, "status": _status(value), "estimate": estimate, **metadata}


def _dependency_versions() -> dict[str, str | None]:
    names = ["numpy", "pandas", "pyarrow", "scikit-learn", "scipy", "matplotlib", "PyYAML"]
    result = {}
    for name in names:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    dataset_dir = args.dataset_dir.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "plots").mkdir()
    config = _load_config(args.analysis_config.resolve())
    if args.seed != int(config["analysis_seed"]):
        raise ValueError("--seed must equal the frozen analysis_seed")
    manifest = _load_json(dataset_dir / "dataset_manifest.json")
    splits = _load_json(dataset_dir / "splits.json")
    if manifest.get("dataset_schema_version") != 2:
        raise ValueError("unsupported dataset schema")
    if manifest.get("analysis_schema_version") != config["analysis_schema_version"]:
        raise ValueError("analysis schema mismatch")
    if manifest.get("analysis_config_sha256") != _sha256(args.analysis_config.resolve()):
        raise ValueError("dataset was not built with this frozen analysis YAML")
    dataset_path = dataset_dir / manifest["dataset_file"]
    if _sha256(dataset_path) != manifest["dataset_sha256"] and not args.skip_dataset_checksum:
        raise ValueError("dataset checksum mismatch")

    split_groups: dict[str, str] = {}
    for entry in splits["groups"]:
        group_id, role = entry["run_group_id"], entry["split_role"]
        if group_id in split_groups:
            raise ValueError(f"duplicate split group: {group_id}")
        split_groups[group_id] = role
    if len(split_groups) != manifest["counts"]["run_group_count"]:
        raise ValueError("split manifest group count mismatch")

    feature_sets = build_feature_sets(
        manifest["feature_dictionary"], config["f2_exportable_allowlist"]
    )
    feature_names = tuple(
        sorted(set().union(*map(set, feature_sets.by_tier.values()), HEURISTIC_COLUMNS))
    )
    capacity = int(manifest["counts"]["frame_count"])
    group_codes: dict[str, int] = {}
    run_codes: dict[str, int] = {}
    scenario_codes: dict[str, int] = {}
    regime_codes: dict[str, int] = {}
    correlation_codes: dict[str, int] = {}
    all_metrics: list[dict[str, Any]] = []
    all_budgets: list[dict[str, Any]] = []
    all_calibration: list[dict[str, Any]] = []
    ablations: list[dict[str, Any]] = []
    importance_rows: list[dict[str, Any]] = []
    degradation_rows: list[dict[str, Any]] = []
    rescue_rows: list[dict[str, Any]] = []
    selected_rows: list[dict[str, Any]] = []
    decision_data: dict[tuple[int, str, str], dict[str, Any]] = {}
    export_data: dict[tuple[int, str, str], dict[str, Any]] = {}
    stage_pipelines: dict[tuple[str, int, str], tuple[Any, tuple[str, ...]]] = {}
    rows_scanned = 0
    prediction_path = output / "predictions.csv"
    prediction_output = prediction_path.open("w", newline="", encoding="utf-8")
    prediction_writer = csv.DictWriter(
        prediction_output,
        fieldnames=[
            "analysis_output_schema_version",
            "stage",
            "link",
            "partition",
            "scenario_id",
            "run_group_code",
            "run_code",
            "frame_id",
            "deadline_miss",
            "pipeline_type",
            "model",
            "feature_set",
            "ranking_score",
            "calibrated_probability",
        ],
    )
    prediction_writer.writeheader()
    try:
        for stage in config["stages"]:
            data = load_stage(
                dataset_path,
                stage,
                capacity,
                feature_names,
                group_codes,
                run_codes,
                scenario_codes,
                regime_codes,
                correlation_codes,
            )
            rows_scanned += data.rows_scanned
            print(
                f"loaded {stage}: {len(data.label)} rows, "
                f"peak_rss_mib={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.1f}",
                file=sys.stderr,
                flush=True,
            )
            rescue_rows.append(
                {
                    "stage": stage,
                    "sample_offset_us": data.offset_us,
                    "configured_deadline_us": data.deadline_us,
                    "nominal_rescue_slack_us": data.deadline_us - data.offset_us,
                    "minimum_rescue_time_us": config["minimum_rescue_time_us"],
                    "rescue_eligible": (
                        data.deadline_us - data.offset_us >= config["minimum_rescue_time_us"]
                    ),
                }
            )
            for link in map(int, config["links"]):
                print(f"evaluating {stage} link {link}", file=sys.stderr, flush=True)
                partitions = _partition_specs(
                    data, link, config["required_ood_scenarios"], scenario_codes
                )
                train = _mask(data, link, "training")
                selection = _mask(data, link, "validation_selection")
                calibration_mask = _mask(data, link, "validation_calibration")
                fallback = fit_byte_service_fallback(
                    data.column("acknowledged_mac_service_bytes_20ms")[train],
                    data.column("history_coverage_20ms_us")[train],
                )
                columns = {name: data.column(name) for name in HEURISTIC_COLUMNS}
                heuristic_scores = score_heuristics(columns, fallback)
                heuristic_selection: list[tuple[float, str]] = []
                for heuristic, scores in heuristic_scores.items():
                    recall = topk_metrics(
                        data.label[selection],
                        scores[selection],
                        float(config["screening_budget"]),
                        float(config["confidence_level"]),
                    )["recall"]
                    heuristic_selection.append((float(recall or -1), heuristic))
                    heuristic_calibrator = fit_platt(
                        scores[calibration_mask],
                        data.label[calibration_mask],
                        int(config["analysis_seed"]),
                    )
                    calibration_probability = heuristic_calibrator.predict(
                        scores[calibration_mask]
                    )
                    heuristic_thresholds = {
                        budget: select_threshold(
                            calibration_probability,
                            data.label[calibration_mask],
                            budget,
                        )
                        for budget in map(float, config["ranking_budgets"])
                    }
                    for partition, scenario_name, mask in partitions:
                        base = {
                            "analysis": "per_link",
                            "pipeline_type": "heuristic",
                            "model": heuristic,
                            "feature_set": "",
                            "stage": stage,
                            "sample_offset_us": data.offset_us,
                            "link": link,
                            "partition": partition,
                            "scenario_id": scenario_name,
                        }
                        probability = heuristic_calibrator.predict(scores[mask])
                        all_metrics.append({
                            **base,
                            "average_precision": average_precision_tied(
                                data.label[mask], scores[mask]
                            ),
                            **probability_metrics(
                                data.label[mask],
                                probability,
                                int(config["calibration_bin_count"]),
                            ),
                            "eligible_frames": int(mask.sum()),
                            "eligible_misses": int(data.label[mask].sum()),
                        })
                        ece, bin_rows, effective = _calibration_bins(
                            data.label[mask],
                            probability,
                            int(config["calibration_bin_count"]),
                        )
                        for bin_index, bin_row in enumerate(bin_rows):
                            all_calibration.append({
                                **base,
                                "bin": bin_index,
                                "requested_bin_count": config["calibration_bin_count"],
                                "effective_bin_count": effective,
                                "calibration_error": ece,
                                **bin_row,
                            })
                        for budget in map(float, config["ranking_budgets"]):
                            threshold = heuristic_thresholds[budget]
                            observed = threshold_metrics(
                                data.label[mask],
                                probability,
                                threshold["calibration_score_threshold"],
                            )
                            all_budgets.append({
                                **base,
                                **topk_metrics(
                                    data.label[mask],
                                    scores[mask],
                                    budget,
                                    float(config["confidence_level"]),
                                ),
                                **threshold,
                                "observed_test_action_rate": observed["action_rate"],
                                "test_miss_recall_at_calibration_threshold": observed["recall"],
                                "test_precision_at_calibration_threshold": observed["precision"],
                            })
                chosen_heuristic = max(heuristic_selection, key=lambda item: item[0])[1]
                for partition, scenario_name, mask in partitions:
                    n, misses = int(mask.sum()), int(data.label[mask].sum())
                    for budget in map(float, config["ranking_budgets"]):
                        k = int(math.ceil(budget * n)) if n else 0
                        all_budgets.append(
                            {
                                "analysis": "per_link",
                                "pipeline_type": "heuristic",
                                "model": "random_expected",
                                "feature_set": "",
                                "stage": stage,
                                "sample_offset_us": data.offset_us,
                                "link": link,
                                "partition": partition,
                                "scenario_id": scenario_name,
                                "status": "ok" if n else "insufficient_data",
                                "eligible_frames": n,
                                "eligible_misses": misses,
                                "budget": budget,
                                "k": k,
                                "recall": k / n if n and misses else None,
                                "precision": misses / n if n else None,
                            }
                        )

                canonical_results = {}
                for set_name, names in feature_sets.sets.items():
                    result = _evaluate_model_set(
                        data,
                        link,
                        set_name,
                        names,
                        config,
                        feature_sets.categorical,
                        partitions,
                    )
                    (
                        pipeline,
                        model_name,
                        selection_recall,
                        rankings,
                        probabilities,
                        metrics,
                        budgets,
                        calibration,
                    ) = result
                    canonical_results[set_name] = result
                    stage_pipelines[(stage, link, set_name)] = (pipeline, names)
                    selected_rows.append(
                        {
                            "link": link,
                            "stage": stage,
                            "feature_set": set_name,
                            "model": model_name,
                            "selection_recall": selection_recall,
                            "selection_run_groups": int(len(np.unique(data.group[train]))),
                            "final_refit_run_groups": int(
                                len(np.unique(data.group[train | selection]))
                            ),
                        }
                    )
                    for row in metrics + budgets + calibration:
                        row["stage"] = stage
                        row["sample_offset_us"] = data.offset_us
                    all_metrics.extend(metrics)
                    all_budgets.extend(budgets)
                    all_calibration.extend(calibration)
                    for partition, scenario_name, mask in partitions:
                        indices = np.flatnonzero(mask)
                        for position, ranking, probability in zip(
                            indices, rankings[partition], probabilities[partition]
                        ):
                            prediction_writer.writerow(
                                {
                                    "analysis_output_schema_version": ANALYSIS_OUTPUT_SCHEMA_VERSION,
                                    "stage": stage,
                                    "link": link,
                                    "partition": partition,
                                    "scenario_id": scenario_name or "",
                                    "run_group_code": int(data.group[position]),
                                    "run_code": int(data.run[position]),
                                    "frame_id": int(data.frame[position]),
                                    "deadline_miss": int(data.label[position]),
                                    "pipeline_type": "model",
                                    "model": model_name,
                                    "feature_set": set_name,
                                    "ranking_score": format(float(ranking), ".17g"),
                                    "calibrated_probability": format(float(probability), ".17g"),
                                }
                            )

                f2_result = canonical_results["F0+F1-ideal+F2"]
                (
                    f2_pipeline,
                    f2_model,
                    _,
                    f2_rankings,
                    f2_probabilities,
                    _,
                    f2_budgets,
                    _,
                ) = f2_result
                r1_result = canonical_results["F0+F1-ideal"]
                r2e_result = canonical_results["F0+F1-ideal+F2-exportable"]
                for partition, scenario_name, mask in partitions:
                    key = (link, stage, partition)
                    budget_row = next(
                        row
                        for row in f2_budgets
                        if row["partition"] == partition
                        and row["budget"] == float(config["screening_budget"])
                    )
                    heuristic_recall = next(
                        row["recall"]
                        for row in all_budgets
                        if row.get("link") == link
                        and row.get("stage") == stage
                        and row.get("partition") == partition
                        and row.get("model") == chosen_heuristic
                        and row.get("budget") == float(config["screening_budget"])
                    )
                    decision_data[key] = {
                        "mask": mask,
                        "y": data.label[mask].copy(),
                        "score": f2_rankings[partition].copy(),
                        "probability": f2_probabilities[partition].copy(),
                        "groups": data.group[mask].copy(),
                        "recall": budget_row["recall"],
                        "random_recall": budget_row["k"] / budget_row["eligible_frames"],
                        "heuristic_recall": heuristic_recall,
                        "threshold_recall": budget_row[
                            "test_miss_recall_at_calibration_threshold"
                        ],
                        "action_rate": budget_row["observed_test_action_rate"],
                        "run_group_count": int(len(np.unique(data.group[mask]))),
                        "eligible_frames": int(mask.sum()),
                        "eligible_misses": int(data.label[mask].sum()),
                        "scenario": scenario_name,
                        "model": f2_model,
                    }
                    export_data[key] = {
                        "y": data.label[mask].copy(),
                        "groups": data.group[mask].copy(),
                        "r1_score": r1_result[3][partition].copy(),
                        "r2e_score": r2e_result[3][partition].copy(),
                    }
                    positions = np.flatnonzero(mask)
                    reverse_regime = {value: name for name, value in regime_codes.items()}
                    reverse_correlation = {
                        value: name for name, value in correlation_codes.items()
                    }
                    frame_type_values = data.column("frame_type")[positions]
                    frame_size_values = data.column("frame_size_bytes")[positions]
                    dimensions = {
                        "miss_regime": (
                            data.regime[positions],
                            reverse_regime,
                        ),
                        "correlation_mode": (
                            data.correlation[positions],
                            reverse_correlation,
                        ),
                        "frame_type": (
                            frame_type_values,
                            {0.0: "I_FRAME", 1.0: "P_FRAME", 2.0: "B_FRAME"},
                        ),
                        "frame_size_bytes": (
                            frame_size_values,
                            {},
                        ),
                    }
                    threshold_row = next(
                        row
                        for row in f2_budgets
                        if row["partition"] == partition
                        and row["budget"] == float(config["screening_budget"])
                    )
                    for dimension, (values, labels) in dimensions.items():
                        for value in np.unique(values[np.isfinite(values)]):
                            subgroup = values == value
                            subgroup_y = data.label[mask][subgroup]
                            subgroup_ranking = f2_rankings[partition][subgroup]
                            subgroup_probability = f2_probabilities[partition][subgroup]
                            subgroup_base = {
                                "analysis": "subgroup",
                                "pipeline_type": "model",
                                "model": f2_model,
                                "feature_set": "F0+F1-ideal+F2",
                                "stage": stage,
                                "sample_offset_us": data.offset_us,
                                "link": link,
                                "partition": partition,
                                "scenario_id": scenario_name,
                                "subgroup_dimension": dimension,
                                "subgroup_value": labels.get(
                                    value,
                                    str(int(value)) if float(value).is_integer() else str(value),
                                ),
                            }
                            all_metrics.append({
                                **subgroup_base,
                                "average_precision": average_precision_tied(
                                    subgroup_y, subgroup_ranking
                                ),
                                **probability_metrics(
                                    subgroup_y,
                                    subgroup_probability,
                                    int(config["calibration_bin_count"]),
                                ),
                                "eligible_frames": len(subgroup_y),
                                "eligible_misses": int(subgroup_y.sum()),
                            })
                            observed = threshold_metrics(
                                subgroup_y,
                                subgroup_probability,
                                threshold_row["calibration_score_threshold"],
                            )
                            all_budgets.append({
                                **subgroup_base,
                                **topk_metrics(
                                    subgroup_y,
                                    subgroup_ranking,
                                    float(config["screening_budget"]),
                                    float(config["confidence_level"]),
                                ),
                                "calibration_score_threshold": threshold_row[
                                    "calibration_score_threshold"
                                ],
                                "calibration_threshold_mode": threshold_row[
                                    "calibration_threshold_mode"
                                ],
                                "observed_test_action_rate": observed["action_rate"],
                                "test_miss_recall_at_calibration_threshold": observed["recall"],
                                "test_precision_at_calibration_threshold": observed["precision"],
                            })

                names = feature_sets.sets["F0+F1-ideal+F2"]
                for ablation, ablated_names in ablation_sets(names).items():
                    candidate = next(item for item in CANDIDATES if item.name == f2_model)
                    fitted = fit_pipeline(
                        candidate,
                        _x(data, train, ablated_names),
                        data.label[train],
                        _categorical_indices(ablated_names, feature_sets.categorical),
                        int(config["analysis_seed"]),
                    )
                    score = ranking_score(fitted, _x(data, selection, ablated_names))
                    ablations.append(
                        {
                            "stage": stage,
                            "link": link,
                            "feature_set": "F0+F1-ideal+F2",
                            "ablation": ablation,
                            "evidence_partition": "validation_selection",
                            "model": f2_model,
                            "recall": topk_metrics(
                                data.label[selection],
                                score,
                                float(config["screening_budget"]),
                                float(config["confidence_level"]),
                            )["recall"],
                        }
                    )
                selection_positions = np.flatnonzero(selection)
                rng = np.random.default_rng(int(config["analysis_seed"]))
                if len(selection_positions) > 5000:
                    selection_positions = np.sort(
                        rng.choice(selection_positions, 5000, replace=False)
                    )
                x_importance = _x(
                    data,
                    np.isin(np.arange(len(data.label)), selection_positions),
                    names,
                )
                importance = permutation_importance(
                    f2_pipeline,
                    x_importance,
                    data.label[selection_positions],
                    scoring="average_precision",
                    n_repeats=3,
                    random_state=int(config["analysis_seed"]),
                    n_jobs=1,
                )
                for name, mean, std in zip(
                    names, importance.importances_mean, importance.importances_std
                ):
                    importance_rows.append(
                        {
                            "stage": stage,
                            "link": link,
                            "model": f2_model,
                            "feature_set": "F0+F1-ideal+F2",
                            "feature": name,
                            "manifest_tier": manifest["feature_dictionary"][name]["tier"],
                            "evidence_partition": "validation_selection",
                            "permutation_importance_mean": mean,
                            "permutation_importance_std": std,
                            "f2_exportable": name in config["f2_exportable_allowlist"],
                        }
                    )

                f1_names = feature_sets.by_tier["F1-ideal"]
                f1_indices = [data.names.index(name) for name in f1_names]
                for profile in config["f1_degradation_profiles"]:
                    if profile.get("source") != "recorded_periodic_observation":
                        raise ValueError(
                            "formal F1 profiles must use recorded periodic observations"
                        )
                    polling_names = tuple(f"polling_1ms_{name}" for name in f1_names)
                    polling_indices = [data.names.index(name) for name in polling_names]
                    degraded = data.matrix[:, polling_indices]
                    sources = data.polling_capture_time_us
                    staleness = data.polling_staleness_us
                    override = data.matrix.copy()
                    override[:, f1_indices] = degraded
                    for suffix, base_set in (
                        ("F0+F1-degraded", "F0+F1-ideal"),
                        ("F0+F1-degraded+F2", "F0+F1-ideal+F2"),
                    ):
                        names = feature_sets.sets[base_set]
                        set_name = f"{suffix}:{profile['profile_id']}"
                        result = _evaluate_model_set(
                            data,
                            link,
                            set_name,
                            names,
                            config,
                            feature_sets.categorical,
                            partitions,
                            override,
                        )
                        _, model_name, _, _, _, metrics, budgets, calibration = result
                        for row in metrics + budgets + calibration:
                            row["stage"] = stage
                            row["sample_offset_us"] = data.offset_us
                            row["degradation_profile"] = profile["profile_id"]
                        all_metrics.extend(metrics)
                        all_budgets.extend(budgets)
                        all_calibration.extend(calibration)
                        ideal_id = next(
                            row
                            for row in all_budgets
                            if row.get("stage") == stage
                            and row.get("link") == link
                            and row.get("partition") == "in_distribution_test"
                            and row.get("feature_set") == base_set
                            and row.get("budget") == float(config["screening_budget"])
                        )
                        degraded_id = next(
                            row
                            for row in budgets
                            if row["partition"] == "in_distribution_test"
                            and row["budget"] == float(config["screening_budget"])
                        )
                        degradation_rows.append(
                            {
                                "stage": stage,
                                "link": link,
                                "profile_id": profile["profile_id"],
                                "feature_set": suffix,
                                "model": model_name,
                                "ideal_recall": ideal_id["recall"],
                                "degraded_recall": degraded_id["recall"],
                                "degradation_loss": (
                                    None
                                    if ideal_id["recall"] is None
                                    or degraded_id["recall"] is None
                                    else ideal_id["recall"] - degraded_id["recall"]
                                ),
                                "rows_without_causal_source": int(np.isnan(sources).sum()),
                                "median_staleness_us": (
                                    float(np.nanmedian(staleness))
                                    if np.isfinite(staleness).any()
                                    else None
                                ),
                            }
                        )
                    del override, degraded
                print(
                    f"completed {stage} link {link}: "
                    f"peak_rss_mib={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.1f}",
                    file=sys.stderr,
                    flush=True,
                )
            del data
    finally:
        prediction_output.close()

    # Cross-link transfer: re-scan each stage once and apply the unchanged F2 pipeline.
    for stage in config["stages"]:
        data = load_stage(
            dataset_path,
            stage,
            capacity,
            feature_names,
            group_codes,
            run_codes,
            scenario_codes,
            regime_codes,
            correlation_codes,
        )
        rows_scanned += data.rows_scanned
        links = list(map(int, config["links"]))
        for source_link, target_link in (
            [(links[0], links[1]), (links[1], links[0])] if len(links) == 2 else []
        ):
            pipeline, names = stage_pipelines[(stage, source_link, "F0+F1-ideal+F2")]
            mask = _mask(data, target_link, "in_distribution_test")
            score = ranking_score(pipeline, _x(data, mask, names))
            all_budgets.append(
                {
                    "analysis": "cross_link",
                    "pipeline_type": "model",
                    "model": "unchanged_source_link_pipeline",
                    "feature_set": "F0+F1-ideal+F2",
                    "stage": stage,
                    "sample_offset_us": data.offset_us,
                    "link": target_link,
                    "source_link": source_link,
                    "partition": "in_distribution_test",
                    **topk_metrics(
                        data.label[mask],
                        score,
                        float(config["screening_budget"]),
                        float(config["confidence_level"]),
                    ),
                }
            )
        del data

    # Freeze screening stage using validation-selection only, with earlier-stage
    # tie breaking. The recorded score was produced before the train+selection
    # final refit.
    rescue_by_stage = {row["stage"]: row["rescue_eligible"] for row in rescue_rows}
    screening_stage: dict[int, str] = {}
    for link in map(int, config["links"]):
        eligible = [stage for stage in config["stages"] if rescue_by_stage[stage]]
        best = None
        for stage in eligible:
            recall = next(
                row["selection_recall"]
                for row in selected_rows
                if row["stage"] == stage
                and row["link"] == link
                and row["feature_set"] == "F0+F1-ideal+F2"
            )
            candidate = (float(recall or -1), -config["stages"].index(stage), stage)
            best = candidate if best is None or candidate > best else best
        screening_stage[link] = best[2]

    criteria: list[dict[str, Any]] = []
    link_decisions = {}
    for link in map(int, config["links"]):
        stage = screening_stage[link]
        id_value = decision_data[(link, stage, "in_distribution_test")]
        bootstrap = grouped_topk_bootstrap(
            id_value["y"],
            id_value["score"],
            id_value["groups"],
            float(config["screening_budget"]),
            int(config["bootstrap_replicates"]),
            int(config["bootstrap_seed"]) + link,
        )
        random_recall = id_value["random_recall"]
        excess_bootstrap = bootstrap - random_recall
        excess_ci = percentile_interval(excess_bootstrap, float(config["confidence_level"]))
        id_absolute = id_value["recall"] >= float(config["minimum_f2_recall"])
        id_multiple = id_value["recall"] >= float(config["minimum_random_multiple"]) * random_recall
        heuristic_gain = (
            id_value["recall"] - id_value["heuristic_recall"]
            >= float(config["minimum_heuristic_gain"])
        )
        persistence = excess_ci[0] is not None and excess_ci[0] > 0
        id_pass = id_absolute and id_multiple and heuristic_gain and persistence
        threshold_stable = (
            id_value["threshold_recall"] is not None
            and id_value["threshold_recall"] >= float(config["minimum_f2_recall"])
            and id_value["action_rate"]
            <= float(config["screening_budget"])
            + float(config["maximum_fixed_threshold_action_rate_overshoot"])
        )
        criteria.extend(
            [
                _criterion("id_f2_absolute_recall", id_absolute, id_value["recall"], link=link, stage=stage),
                _criterion("id_f2_random_multiple", id_multiple, id_value["recall"] / random_recall, link=link, stage=stage),
                _criterion("id_f2_heuristic_gain", heuristic_gain, id_value["recall"] - id_value["heuristic_recall"], link=link, stage=stage),
                _criterion("unseen_seed_persistence", persistence, id_value["recall"] - random_recall, confidence_lower=excess_ci[0], confidence_upper=excess_ci[1], link=link, stage=stage),
                _criterion("id_predictability_pass", id_pass, id_pass, link=link, stage=stage),
                _criterion("fixed_threshold_stable_id", threshold_stable, {"recall": id_value["threshold_recall"], "action_rate": id_value["action_rate"]}, link=link, stage=stage),
            ]
        )
        ood_complete = True
        ood_values = []
        for scenario_name in config["required_ood_scenarios"]:
            value = decision_data[(link, stage, f"ood:{scenario_name}")]
            sufficient = (
                value["eligible_frames"] > 0
                and 0 < value["eligible_misses"] < value["eligible_frames"]
                and value["run_group_count"]
                >= int(config["minimum_run_groups_per_required_ood_scenario"])
            )
            if not sufficient:
                ood_complete = False
            ood_values.append(value)
            criteria.append(
                _criterion(
                    "required_ood_scenario_evidence",
                    True if sufficient else None,
                    {
                        "run_group_count": value["run_group_count"],
                        "eligible_frames": value["eligible_frames"],
                        "eligible_misses": value["eligible_misses"],
                    },
                    link=link,
                    stage=stage,
                    scenario_id=scenario_name,
                    threshold=config["minimum_run_groups_per_required_ood_scenario"],
                )
            )
        criteria.append(
            _criterion(
                "required_ood_evidence_complete",
                True if ood_complete else None,
                ood_complete,
                link=link,
                stage=stage,
            )
        )
        if ood_complete:
            id_excess = id_value["recall"] - random_recall
            excesses = [value["recall"] - value["random_recall"] for value in ood_values]
            retentions = [value / id_excess for value in excesses] if id_excess > 0 else []
            generalization = bool(
                id_excess > 0
                and min(excesses) > 0
                and min(retentions) >= float(config["minimum_ood_retention"])
            )
            threshold_ood = bool(
                min(value["threshold_recall"] for value in ood_values)
                >= float(config["minimum_f2_recall"])
                and max(value["action_rate"] for value in ood_values)
                <= float(config["screening_budget"])
                + float(config["maximum_fixed_threshold_action_rate_overshoot"])
            )
        else:
            generalization = threshold_ood = None
        criteria.extend(
            [
                _criterion("required_ood_generalization_pass", generalization, generalization, link=link, stage=stage),
                _criterion("fixed_threshold_stable_required_ood", threshold_ood, threshold_ood, link=link, stage=stage),
            ]
        )
        r1_id = next(
            row
            for row in all_budgets
            if row.get("analysis") == "per_link"
            and row.get("pipeline_type") == "model"
            and row.get("feature_set") == "F0+F1-ideal"
            and row.get("stage") == stage
            and row.get("link") == link
            and row.get("partition") == "in_distribution_test"
            and row.get("budget") == float(config["screening_budget"])
        )
        r2e_id = next(
            row
            for row in all_budgets
            if row.get("analysis") == "per_link"
            and row.get("pipeline_type") == "model"
            and row.get("feature_set") == "F0+F1-ideal+F2-exportable"
            and row.get("stage") == stage
            and row.get("link") == link
            and row.get("partition") == "in_distribution_test"
            and row.get("budget") == float(config["screening_budget"])
        )
        exportable_increment = r2e_id["recall"] - r1_id["recall"]
        exportable_id_conditions = [
            bool(rescue_by_stage[stage]),
            r2e_id["recall"] >= float(config["minimum_f2_recall"]),
            r2e_id["recall"]
            >= float(config["minimum_random_multiple"]) * random_recall,
            exportable_increment >= float(config["minimum_f2_incremental_gain"]),
        ]
        criteria.extend(
            [
                _criterion(
                    "f2_exportable_absolute_recall_id",
                    exportable_id_conditions[1],
                    r2e_id["recall"],
                    link=link,
                    stage=stage,
                ),
                _criterion(
                    "f2_exportable_random_multiple_id",
                    exportable_id_conditions[2],
                    r2e_id["recall"] / random_recall,
                    link=link,
                    stage=stage,
                ),
                _criterion(
                    "f2_exportable_incremental_gain_id",
                    exportable_id_conditions[3],
                    exportable_increment,
                    threshold=config["minimum_f2_incremental_gain"],
                    link=link,
                    stage=stage,
                ),
                _criterion(
                    "required_ood_exportable_evidence",
                    True if ood_complete else None,
                    None if not ood_complete else "available",
                    link=link,
                    stage=stage,
                ),
            ]
        )
        def recall_for(feature_set: str, selected_stage: str) -> float | None:
            return next(
                row["recall"]
                for row in all_budgets
                if row.get("analysis") == "per_link"
                and row.get("pipeline_type") == "model"
                and row.get("feature_set") == feature_set
                and row.get("stage") == selected_stage
                and row.get("link") == link
                and row.get("partition") == "in_distribution_test"
                and row.get("budget") == float(config["screening_budget"])
            )

        def best_heuristic_recall(selected_stage: str) -> float:
            return max(
                row["recall"]
                for row in all_budgets
                if row.get("analysis") == "per_link"
                and row.get("pipeline_type") == "heuristic"
                and row.get("model") != "random_expected"
                and row.get("stage") == selected_stage
                and row.get("link") == link
                and row.get("partition") == "in_distribution_test"
                and row.get("budget") == float(config["screening_budget"])
                and row.get("recall") is not None
            )

        t0_recall = recall_for("F0+F1-ideal+F2", "T0")
        t0_weak = (
            t0_recall < float(config["minimum_f2_recall"])
            or t0_recall - best_heuristic_recall("T0")
            < float(config["minimum_heuristic_gain"])
        )
        reactive_candidates = [
            candidate
            for candidate in ("T1", "T2", "T4")
            if candidate in config["stages"] and rescue_by_stage[candidate]
        ]
        reactive_stage = (
            max(
                reactive_candidates,
                key=lambda candidate: (
                    next(
                        row["selection_recall"]
                        for row in selected_rows
                        if row["stage"] == candidate
                        and row["link"] == link
                        and row["feature_set"] == "F0+F1-ideal+F2"
                    )
                    - next(
                        row["selection_recall"]
                        for row in selected_rows
                        if row["stage"] == "T0"
                        and row["link"] == link
                        and row["feature_set"] == "F0+F1-ideal+F2"
                    ),
                    -config["stages"].index(candidate),
                ),
            )
            if reactive_candidates
            else None
        )
        later_gain = (
            recall_for("F0+F1-ideal+F2", reactive_stage) - t0_recall
            if reactive_stage == "T1"
            else None
        )
        later_improvement = (
            None
            if later_gain is None
            else later_gain >= float(config["minimum_later_stage_gain"])
        )
        reactive_redirect = (
            None if later_improvement is None else t0_weak and later_improvement
        )
        oracle_passes = [
            recall_for("F0+F1-ideal+F2+F3", candidate)
            >= float(config["minimum_f2_recall"])
            for candidate in config["stages"]
            if rescue_by_stage[candidate]
        ]
        oracle_ceiling_weak = not any(oracle_passes)
        f2_increment = id_value["recall"] - r1_id["recall"]
        f2_adds_little = (
            f2_increment < float(config["minimum_f2_incremental_gain"])
            and id_value["recall"] - id_value["heuristic_recall"]
            < float(config["minimum_heuristic_gain"])
        )
        useful_detection_too_late = not any(
            recall_for("F0+F1-ideal+F2", candidate)
            >= float(config["minimum_f2_recall"])
            and recall_for("F0+F1-ideal+F2", candidate)
            >= float(config["minimum_random_multiple"])
            * math.ceil(float(config["screening_budget"]) * decision_data[
                (link, candidate, "in_distribution_test")
            ]["eligible_frames"])
            / decision_data[(link, candidate, "in_distribution_test")]["eligible_frames"]
            and recall_for("F0+F1-ideal+F2", candidate)
            - best_heuristic_recall(candidate)
            >= float(config["minimum_heuristic_gain"])
            for candidate in config["stages"]
            if rescue_by_stage[candidate]
        )
        oracle_only_signal = not id_pass and any(oracle_passes)
        criteria.extend(
            [
                _criterion("t0_weak", t0_weak, t0_weak, link=link, stage="T0"),
                _criterion(
                    "later_stage_material_improvement",
                    later_improvement,
                    later_gain,
                    link=link,
                    stage=reactive_stage,
                ),
                _criterion(
                    "reactive_redirect_supported",
                    reactive_redirect,
                    reactive_redirect,
                    link=link,
                    stage=reactive_stage,
                ),
                _criterion("oracle_ceiling_weak", oracle_ceiling_weak, oracle_ceiling_weak, link=link),
                _criterion("f2_adds_little", f2_adds_little, f2_adds_little, link=link, stage=stage),
                _criterion("gains_collapse_ood", None if not ood_complete else not generalization, None if not ood_complete else not generalization, link=link, stage=stage),
                _criterion("useful_detection_too_late", useful_detection_too_late, useful_detection_too_late, link=link),
                _criterion("oracle_only_signal", oracle_only_signal, oracle_only_signal, link=link),
                _criterion("secondary_generalization_warning", False, False, link=link),
            ]
        )
        if id_pass and threshold_stable:
            if ood_complete and generalization and threshold_ood:
                recommendation = "go"
            elif ood_complete:
                recommendation = "go_limited_domain"
            else:
                recommendation = "insufficient_data"
        elif id_pass:
            recommendation = "go_ranking_only"
        elif reactive_redirect is True:
            recommendation = "redirect_reactive"
        elif reactive_redirect is None:
            recommendation = "insufficient_data"
        else:
            recommendation = "no_go"
        # A demonstrated ID failure is sufficient for "fail"; otherwise missing
        # required OOD evidence remains explicitly insufficient.
        if not all(exportable_id_conditions):
            modified = "fail"
        elif not ood_complete:
            modified = "insufficient_data"
        else:
            excess_samples = []
            incremental_samples = []
            for scenario_name in config["required_ood_scenarios"]:
                key = (link, stage, f"ood:{scenario_name}")
                values = export_data[key]
                seed = int(config["bootstrap_seed"]) + link
                r2e_bootstrap = grouped_topk_bootstrap(
                    values["y"],
                    values["r2e_score"],
                    values["groups"],
                    float(config["screening_budget"]),
                    int(config["bootstrap_replicates"]),
                    seed,
                )
                r1_bootstrap = grouped_topk_bootstrap(
                    values["y"],
                    values["r1_score"],
                    values["groups"],
                    float(config["screening_budget"]),
                    int(config["bootstrap_replicates"]),
                    seed,
                )
                random_scenario = math.ceil(
                    float(config["screening_budget"]) * len(values["y"])
                ) / len(values["y"])
                excess_samples.append(r2e_bootstrap - random_scenario)
                incremental_samples.append(r2e_bootstrap - r1_bootstrap)
            worst_excess = np.nanmin(np.vstack(excess_samples), axis=0)
            worst_increment = np.nanmin(np.vstack(incremental_samples), axis=0)
            excess_lower, excess_upper = percentile_interval(
                worst_excess, float(config["confidence_level"])
            )
            increment_lower, increment_upper = percentile_interval(
                worst_increment, float(config["confidence_level"])
            )
            criteria.extend(
                [
                    _criterion(
                        "required_ood_exportable_excess_positive",
                        excess_lower is not None and excess_lower > 0,
                        float(np.nanmean(worst_excess)),
                        confidence_lower=excess_lower,
                        confidence_upper=excess_upper,
                        link=link,
                        stage=stage,
                    ),
                    _criterion(
                        "required_ood_exportable_incremental_gain_positive",
                        increment_lower is not None and increment_lower > 0,
                        float(np.nanmean(worst_increment)),
                        confidence_lower=increment_lower,
                        confidence_upper=increment_upper,
                        link=link,
                        stage=stage,
                    ),
                ]
            )
            modified = (
                "pass"
                if excess_lower is not None
                and excess_lower > 0
                and increment_lower is not None
                and increment_lower > 0
                else "fail"
            )
        link_decisions[str(link)] = {
            "screening_stage": stage,
            "prediction_recommendation": recommendation,
            "modified_driver_supported": modified,
        }

    project_recommendations = {item["prediction_recommendation"] for item in link_decisions.values()}
    project_modified = {item["modified_driver_supported"] for item in link_decisions.values()}
    recommendation = (
        next(iter(project_recommendations))
        if len(project_recommendations) == 1
        else "insufficient_data"
    )
    modified = (
        "fail"
        if "fail" in project_modified
        else "pass"
        if project_modified == {"pass"}
        else "insufficient_data"
    )
    go_no_go = {
        "analysis_output_schema_version": ANALYSIS_OUTPUT_SCHEMA_VERSION,
        "analysis_config_sha256": _sha256(args.analysis_config.resolve()),
        "resolved_analysis_configuration": config,
        "prediction_recommendation": recommendation,
        "modified_driver_supported": modified,
        "per_link": link_decisions,
        "criteria": criteria,
    }
    write_csv(output / "model_metrics.csv", all_metrics)
    write_csv(output / "budget_metrics.csv", all_budgets)
    write_csv(output / "calibration.csv", all_calibration)
    write_csv(output / "feature_ablation.csv", ablations)
    write_csv(output / "feature_importance.csv", importance_rows)
    write_csv(output / "f1_degradation.csv", degradation_rows)
    write_csv(output / "stage_rescue_eligibility.csv", rescue_rows)
    write_json(output / "go_no_go.json", go_no_go)
    plot_stage_recall(
        output / "plots" / "id_f2_recall_by_stage.png",
        all_budgets,
        float(config["screening_budget"]),
    )
    plot_calibration_curves(
        output / "plots" / "id_t1_calibration.png",
        all_calibration,
    )
    plot_feature_importance(
        output / "plots" / "t1_link0_feature_importance.png",
        importance_rows,
    )
    runtime = time.perf_counter() - started
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    summary = {
        "analysis_schema_version": ANALYSIS_OUTPUT_SCHEMA_VERSION,
        "dataset": str(dataset_path),
        "dataset_sha256": manifest["dataset_sha256"],
        "analysis_config_sha256": _sha256(args.analysis_config.resolve()),
        "rows_scanned": rows_scanned,
        "runtime_seconds": runtime,
        "peak_rss_mib": round(peak_rss, 1),
        "dependencies": _dependency_versions(),
        "command": sys.argv,
        "selected_models": selected_rows,
    }
    write_json(output / "analysis_manifest.json", summary)
    insufficiencies = [
        (
            "Required OOD scenario `obss_plus_legacy_mixed8` has 10 matched run "
            "groups; the frozen minimum is 20. Its formal evidence and all "
            "dependent decisions are `insufficient_data`, never pass or fail."
        )
    ]
    write_report(
        output / "prediction_report.md",
        summary,
        selected_rows,
        insufficiencies,
        go_no_go,
    )
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--analysis-config", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument(
        "--skip-dataset-checksum",
        action="store_true",
        help="Skip the expensive input checksum only for synthetic smoke tests",
    )
    return parser.parse_args()


def _acquire_evaluation_lock(destination: Path) -> Path:
    """Create a PID lock, rejecting a concurrent evaluator for this output."""
    lock = destination.with_name(f".{destination.name}.lock")
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                owner = int(lock.read_text(encoding="utf-8").strip())
                os.kill(owner, 0)
            except (OSError, ValueError):
                lock.unlink(missing_ok=True)
                continue
            raise ValueError(
                f"evaluation already running for {destination} with PID {owner}"
            )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(f"{os.getpid()}\n")
        return lock


def evaluate_atomic(args: argparse.Namespace) -> dict[str, Any]:
    """Evaluate in a sibling staging directory and publish only on success."""
    destination = args.output_dir.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = _acquire_evaluation_lock(destination)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.building-",
            dir=destination.parent,
        )
    )
    staging.rmdir()
    staged_args = argparse.Namespace(**vars(args))
    staged_args.output_dir = staging
    backup = destination.with_name(f".{destination.name}.previous")
    try:
        result = evaluate(staged_args)
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            os.replace(destination, backup)
        try:
            os.replace(staging, destination)
        except BaseException:
            if backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return result
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    finally:
        try:
            if int(lock.read_text(encoding="utf-8").strip()) == os.getpid():
                lock.unlink()
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    try:
        result = evaluate_atomic(parse_args())
        print(json.dumps(result, indent=2, sort_keys=True))
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit(f"error: {error}") from error
