#!/usr/bin/env python3
"""Fit frozen LOFO completion distributions and OOD fallback diagnostics."""

from __future__ import annotations

import argparse
import csv
import gc
import gzip
import hashlib
import importlib.metadata
import io
import json
import math
import os
import platform
import shutil
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline

import environment_generalization_lofo as lofo


ANALYSIS_SCHEMA_VERSION = 1
OUTPUT_PREDICTIONS = "environment_lofo_predictions.csv.gz"
OUTPUT_METRICS = "environment_lofo_metrics.json"
OUTPUT_MANIFEST = "artifact_manifest.json"
ANALYSIS_SOURCES = (Path(__file__).resolve(), Path(lofo.__file__).resolve())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise lofo.LofoError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _git_value(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=lofo.ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _model(spec: dict[str, Any], seed: int) -> Pipeline:
    required = {
        "id",
        "class",
        "loss",
        "learning_rate",
        "max_iter",
        "max_leaf_nodes",
        "max_depth",
        "min_samples_leaf",
        "l2_regularization",
        "max_bins",
        "early_stopping",
        "random_seed_base",
    }
    if set(spec) != required or spec["class"] != (
        "sklearn.ensemble.HistGradientBoostingClassifier"
    ):
        raise lofo.LofoError("LOFO model specification differs")
    return Pipeline(
        [
            (
                "impute",
                SimpleImputer(
                    strategy="median",
                    add_indicator=True,
                    keep_empty_features=True,
                ),
            ),
            (
                "classifier",
                HistGradientBoostingClassifier(
                    loss=spec["loss"],
                    learning_rate=spec["learning_rate"],
                    max_iter=spec["max_iter"],
                    max_leaf_nodes=spec["max_leaf_nodes"],
                    max_depth=spec["max_depth"],
                    min_samples_leaf=spec["min_samples_leaf"],
                    l2_regularization=spec["l2_regularization"],
                    max_bins=spec["max_bins"],
                    early_stopping=spec["early_stopping"],
                    random_state=seed,
                ),
            ),
        ]
    )


def aligned_smoothed_probabilities(
    model: Pipeline,
    matrix: np.ndarray,
    training_count: int,
    class_count: int,
    alpha: float,
) -> np.ndarray:
    """Return class-aligned probabilities with the frozen rare-class guard."""

    classifier = model.named_steps.get("classifier")
    if classifier is None or training_count <= 0 or class_count < 2 or alpha <= 0:
        raise lofo.LofoError("probability-alignment inputs are invalid")
    raw = np.asarray(model.predict_proba(matrix), dtype=float)
    classes = np.asarray(classifier.classes_, dtype=int)
    if (
        raw.ndim != 2
        or raw.shape != (len(matrix), len(classes))
        or len(set(classes.tolist())) != len(classes)
        or np.any(classes < 0)
        or np.any(classes >= class_count)
        or not np.all(np.isfinite(raw))
    ):
        raise lofo.LofoError("classifier probability schema differs")
    aligned = np.zeros((len(matrix), class_count), dtype=float)
    aligned[:, classes] = raw
    smoothed = (training_count * aligned + alpha) / (
        training_count + alpha * class_count
    )
    if not np.allclose(np.sum(smoothed, axis=1), 1.0, atol=1e-12, rtol=0.0):
        raise lofo.LofoError("smoothed probabilities do not sum to one")
    return smoothed


def _scenario_family_map(data: lofo.LofoDataset) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for scenario, family in zip(data.scenario_ids, data.family_ids):
        previous = mapping.setdefault(scenario, family)
        if previous != family:
            raise lofo.LofoError("scenario belongs to multiple families")
    return mapping


def fit_lofo_predictions(
    data: lofo.LofoDataset,
    *,
    model_spec: dict[str, Any] | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray], list[dict[str, Any]]]:
    """Fit both arms without each held-out family and predict every row once."""

    contract = data.contract
    distribution = contract["completion_distribution"]
    crossfit = contract["cross_fitting"]
    detector_contract = contract["ood_detector"]
    spec = contract["predictor"]["model_spec"] if model_spec is None else model_spec
    thresholds = distribution["thresholds_us"]
    class_count = distribution["class_count"]
    alpha = distribution["dirichlet_alpha_per_class"]
    family_order = crossfit["outer_family_order"]
    families = np.asarray(data.family_ids, dtype=object)
    scenarios = np.asarray(data.scenario_ids, dtype=object)
    if set(families.tolist()) != set(family_order):
        raise lofo.LofoError("dataset family coverage differs from LOFO contract")
    cdf = np.full((len(families), 2, len(thresholds)), np.nan, dtype=np.float32)
    ood_score = np.full(len(families), np.nan, dtype=float)
    ood_threshold = np.full(len(families), np.nan, dtype=float)
    ood_hard = np.zeros(len(families), dtype=np.int8)
    ood_soft = np.zeros(len(families), dtype=np.int8)
    fits: list[dict[str, Any]] = []
    scenario_families = _scenario_family_map(data)
    optional_missing = detector_contract["allowed_missing_context_features"]
    shrinkage = detector_contract["covariance_shrinkage_to_identity"]
    eigenvalue_floor = 1e-9
    for outer_index, held_out_family in enumerate(family_order):
        test = families == held_out_family
        train = ~test
        if not np.any(test) or not np.any(train):
            raise lofo.LofoError(f"{held_out_family}: empty outer split")
        for arm in (0, 1):
            selected = train & (data.treatment == arm)
            labels = data.outcome_bins[selected]
            if len(labels) < 20 or len(set(labels.tolist())) < 2:
                raise lofo.LofoError(
                    f"{held_out_family} arm {arm}: insufficient outcome support"
                )
            seed = spec["random_seed_base"] + outer_index * 10 + arm
            model = _model(spec, seed)
            model.fit(data.model_matrix[selected], labels)
            probabilities = aligned_smoothed_probabilities(
                model,
                data.model_matrix[test],
                int(np.sum(selected)),
                class_count,
                alpha,
            )
            cdf[test, arm, :] = np.cumsum(probabilities, axis=1)[
                :, : len(thresholds)
            ]
            fits.append(
                {
                    "outer_fold": outer_index,
                    "held_out_family": held_out_family,
                    "record_type": "predictor",
                    "arm": arm,
                    "training_rows": int(np.sum(selected)),
                    "prediction_rows": int(np.sum(test)),
                    "observed_training_classes": sorted(set(labels.tolist())),
                    "random_seed": seed,
                }
            )
            del model, probabilities
            gc.collect()
            print(
                f"LOFO {outer_index + 1}/{len(family_order)} {held_out_family} "
                f"arm {arm} complete",
                flush=True,
            )

        training_families = tuple(
            family for family in family_order if family != held_out_family
        )
        training_scenarios = {
            scenario: family
            for scenario, family in scenario_families.items()
            if family in set(training_families)
        }
        assignment = lofo.assign_inner_scenario_folds(
            training_scenarios,
            training_families,
            crossfit["inner_fold_count"],
            crossfit["inner_assignment_salt"],
        )
        threshold, calibration = lofo.calibrate_ood_threshold(
            data.ood_context,
            data.scenario_ids,
            scenario_families,
            training_families,
            assignment,
            data.ood_context_names,
            optional_missing,
            shrinkage,
            eigenvalue_floor,
            detector_contract["threshold_quantile"],
        )
        calibration_mask = np.isfinite(calibration)
        if not np.array_equal(calibration_mask, train):
            raise lofo.LofoError(f"{held_out_family}: OOD calibration coverage differs")
        detector = lofo.fit_ood_model(
            data.ood_context[train],
            data.ood_context_names,
            optional_missing,
            shrinkage,
            eigenvalue_floor,
        )
        scores = detector.scores(data.ood_context[test])
        hard = detector.hard_failures(data.ood_context[test])
        soft = (~hard) & (scores > threshold)
        ood_score[test] = scores
        ood_threshold[test] = threshold
        ood_hard[test] = hard.astype(np.int8)
        ood_soft[test] = soft.astype(np.int8)
        fits.append(
            {
                "outer_fold": outer_index,
                "held_out_family": held_out_family,
                "record_type": "ood",
                "inner_fold_count": crossfit["inner_fold_count"],
                "calibration_rows": int(np.sum(train)),
                "threshold": threshold,
                "held_out_hard_failure_rows": int(np.sum(hard)),
                "held_out_soft_ood_rows": int(np.sum(soft)),
            }
        )
    if (
        not np.all(np.isfinite(cdf))
        or np.any(np.diff(cdf, axis=2) < -1e-6)
        or np.any((cdf < 0) | (cdf > 1))
        or not np.all(np.isfinite(ood_score))
        or not np.all(np.isfinite(ood_threshold))
    ):
        raise lofo.LofoError("LOFO prediction or OOD coverage is invalid")
    ood = {
        "score": ood_score,
        "threshold": ood_threshold,
        "hard_failure": ood_hard,
        "soft_ood": ood_soft,
        "fallback": np.maximum(ood_hard, ood_soft),
    }
    return cdf.astype(float), ood, fits


def _class_probabilities(cdf: np.ndarray) -> np.ndarray:
    if cdf.ndim != 2:
        raise lofo.LofoError("CDF matrix must be two-dimensional")
    probabilities = np.column_stack(
        (cdf[:, 0], np.diff(cdf, axis=1), 1.0 - cdf[:, -1])
    )
    if np.any(probabilities < -1e-6):
        raise lofo.LofoError("CDF implies negative class probability")
    probabilities = np.maximum(probabilities, 0.0)
    probabilities /= np.sum(probabilities, axis=1, keepdims=True)
    return probabilities


def _hierarchical_weights(
    data: lofo.LofoDataset, selected: np.ndarray
) -> np.ndarray:
    """Weight families, scenarios, runs, and then frames equally in order."""

    mask = np.asarray(selected, dtype=bool)
    if mask.shape != data.treatment.shape or not np.any(mask):
        raise lofo.LofoError("hierarchical weighting selection is invalid")
    families = np.asarray(data.family_ids, dtype=object)
    scenarios = np.asarray(data.scenario_ids, dtype=object)
    runs = np.asarray(data.run_ids, dtype=object)
    weights = np.zeros(len(mask), dtype=float)
    selected_rows = np.flatnonzero(mask)
    family_scenarios: defaultdict[str, set[str]] = defaultdict(set)
    scenario_runs: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    run_frames: Counter[tuple[str, str, str]] = Counter()
    for index in selected_rows:
        family = str(families[index])
        scenario = str(scenarios[index])
        run = str(runs[index])
        family_scenarios[family].add(scenario)
        scenario_runs[(family, scenario)].add(run)
        run_frames[(family, scenario, run)] += 1
    family_count = len(family_scenarios)
    for index in selected_rows:
        family = str(families[index])
        scenario = str(scenarios[index])
        run = str(runs[index])
        weights[index] = 1.0 / (
            family_count
            * len(family_scenarios[family])
            * len(scenario_runs[(family, scenario)])
            * run_frames[(family, scenario, run)]
        )
    if not math.isclose(float(np.sum(weights)), 1.0, rel_tol=0.0, abs_tol=1e-12):
        raise lofo.LofoError("hierarchical weights do not sum to one")
    return weights


def _weighted_quantile(
    values: np.ndarray, weights: np.ndarray, quantile: float
) -> float:
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    if (
        values.shape != weights.shape
        or not 0 <= quantile <= 1
        or not np.all(np.isfinite(values))
        or not np.all(np.isfinite(weights))
        or np.any(weights < 0)
        or not np.any(weights > 0)
    ):
        raise lofo.LofoError("weighted quantile inputs differ")
    order = np.argsort(values, kind="stable")
    cumulative = np.cumsum(weights[order])
    target = quantile * cumulative[-1]
    index = min(int(np.searchsorted(cumulative, target, side="left")), len(order) - 1)
    return float(values[order[index]])


def _safe_auc(
    labels: np.ndarray,
    scores: np.ndarray,
    weights: np.ndarray | None = None,
) -> float | None:
    labels = np.asarray(labels, dtype=int)
    scores = np.asarray(scores, dtype=float)
    if labels.shape != scores.shape or not np.all(np.isfinite(scores)):
        raise lofo.LofoError("AUC inputs differ")
    if len(set(labels.tolist())) != 2:
        return None
    if weights is not None:
        weights = np.asarray(weights, dtype=float)
        if (
            weights.shape != labels.shape
            or not np.all(np.isfinite(weights))
            or np.any(weights < 0)
        ):
            raise lofo.LofoError("AUC weights differ")
    return float(roc_auc_score(labels, scores, sample_weight=weights))


def doubly_robust_cdf_components(
    outcome_bins: np.ndarray,
    treatment: np.ndarray,
    propensity: np.ndarray,
    cdf: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Return known-propensity DR potential CDF components for both arms."""

    threshold_count = cdf.shape[2]
    if (
        cdf.shape[:2] != (len(outcome_bins), 2)
        or treatment.shape != outcome_bins.shape
        or propensity.shape != outcome_bins.shape
        or np.any((propensity <= 0) | (propensity >= 1))
    ):
        raise lofo.LofoError("DR component inputs differ")
    observed = np.column_stack(
        [outcome_bins <= index for index in range(threshold_count)]
    ).astype(float)
    phi0 = cdf[:, 0, :] + (
        ((1 - treatment) / (1 - propensity))[:, None]
        * (observed - cdf[:, 0, :])
    )
    phi1 = cdf[:, 1, :] + (
        (treatment / propensity)[:, None] * (observed - cdf[:, 1, :])
    )
    if not np.all(np.isfinite(phi0)) or not np.all(np.isfinite(phi1)):
        raise lofo.LofoError("DR components are non-finite")
    return phi0, phi1


def _observed_arm_metrics(
    data: lofo.LofoDataset,
    cdf: np.ndarray,
    indices: np.ndarray,
    arm: int,
) -> dict[str, Any]:
    selected = indices & (data.treatment == arm)
    weights = _hierarchical_weights(data, selected)[selected]
    labels = data.outcome_bins[selected]
    predicted_cdf = cdf[selected, arm, :]
    probabilities = _class_probabilities(predicted_cdf)
    chosen = np.maximum(probabilities[np.arange(len(labels)), labels], 1e-15)
    one_hot = np.eye(probabilities.shape[1], dtype=float)[labels]
    thresholds = data.contract["completion_distribution"]["thresholds_us"]
    return {
        "row_count": int(np.sum(selected)),
        "outcome_bin_counts": [
            int(np.sum(labels == value)) for value in range(probabilities.shape[1])
        ],
        "multiclass_log_loss": float(-np.sum(weights * np.log(chosen))),
        "multiclass_brier_score": float(
            np.sum(weights * np.sum((probabilities - one_hot) ** 2, axis=1))
        ),
        "thresholds": {
            str(threshold): {
                "observed_completion_probability": float(
                    np.sum(weights * (labels <= threshold_index))
                ),
                "mean_predicted_completion_probability": float(
                    np.sum(weights * predicted_cdf[:, threshold_index])
                ),
                "cdf_brier_score": float(
                    np.sum(
                        weights
                        * (
                            predicted_cdf[:, threshold_index]
                            - (labels <= threshold_index)
                        )
                        ** 2
                    )
                ),
            }
            for threshold_index, threshold in enumerate(thresholds)
        },
    }


def summarize_predictions(
    data: lofo.LofoDataset,
    cdf: np.ndarray,
    ood: dict[str, np.ndarray],
    fits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return diagnostic family metrics and LOFO DR effect estimates."""

    phi0, phi1 = doubly_robust_cdf_components(
        data.outcome_bins, data.treatment, data.propensity, cdf
    )
    rows = np.arange(len(data.outcome_bins))
    deadline_index = data.deadline_threshold_indices.astype(int)
    tail_index = data.contract["completion_distribution"]["thresholds_us"].index(
        18_000
    )
    control_deadline = cdf[rows, 0, deadline_index]
    treated_deadline = cdf[rows, 1, deadline_index]
    predicted_rescue = np.maximum(treated_deadline - control_deadline, 0.0)
    predicted_tail = np.maximum(cdf[:, 1, tail_index] - cdf[:, 0, tail_index], 0.0)
    dr_deadline_gain = phi1[rows, deadline_index] - phi0[rows, deadline_index]
    dr_tail_gain = phi1[:, tail_index] - phi0[:, tail_index]
    families = np.asarray(data.family_ids, dtype=object)
    family_metrics: dict[str, Any] = {}
    for family in data.contract["cross_fitting"]["outer_family_order"]:
        selected = families == family
        treated = selected & (data.treatment == 1)
        selected_weights = _hierarchical_weights(data, selected)
        treated_weights = _hierarchical_weights(data, treated)
        family_metrics[family] = {
            "row_count": int(np.sum(selected)),
            "scenario_count": len(
                set(np.asarray(data.scenario_ids, dtype=object)[selected].tolist())
            ),
            "observed_arm_prediction": {
                str(arm): _observed_arm_metrics(data, cdf, selected, arm)
                for arm in (0, 1)
            },
            "control_deadline_miss_auc": _safe_auc(
                data.primary_deadline_miss[selected],
                1.0 - control_deadline[selected],
                selected_weights[selected],
            ),
            "treated_deadline_rescue_auc": _safe_auc(
                (
                    (data.primary_deadline_miss[treated] == 1)
                    & (data.deadline_miss[treated] == 0)
                ),
                predicted_rescue[treated],
                treated_weights[treated],
            ),
            "mean_predicted_deadline_rescue": float(
                np.sum(selected_weights[selected] * predicted_rescue[selected])
            ),
            "mean_predicted_tail18_acceleration": float(
                np.sum(selected_weights[selected] * predicted_tail[selected])
            ),
            "dr_average_deadline_cdf_gain": float(
                np.sum(selected_weights[selected] * dr_deadline_gain[selected])
            ),
            "dr_average_tail18_cdf_gain": float(
                np.sum(selected_weights[selected] * dr_tail_gain[selected])
            ),
            "ood": {
                "threshold": float(np.unique(ood["threshold"][selected]).item()),
                "hard_failure_fraction": float(
                    np.sum(
                        selected_weights[selected]
                        * ood["hard_failure"][selected]
                    )
                ),
                "soft_ood_fraction": float(
                    np.sum(
                        selected_weights[selected] * ood["soft_ood"][selected]
                    )
                ),
                "fallback_fraction": float(
                    np.sum(selected_weights[selected] * ood["fallback"][selected])
                ),
                "score_median": _weighted_quantile(
                    ood["score"][selected], selected_weights[selected], 0.5
                ),
                "score_p99": _weighted_quantile(
                    ood["score"][selected], selected_weights[selected], 0.99
                ),
            },
        }
    equal_family_deadline_gain = float(
        np.mean(
            [value["dr_average_deadline_cdf_gain"] for value in family_metrics.values()]
        )
    )
    equal_family_tail_gain = float(
        np.mean(
            [value["dr_average_tail18_cdf_gain"] for value in family_metrics.values()]
        )
    )
    overall_weights = _hierarchical_weights(
        data, np.ones(len(data.treatment), dtype=bool)
    )
    overall_treated = data.treatment == 1
    overall_treated_weights = _hierarchical_weights(data, overall_treated)
    return {
        "weighting": "equal_family_then_equal_scenario_then_equal_replicate",
        "family_metrics": family_metrics,
        "equal_family_dr_average_deadline_cdf_gain": equal_family_deadline_gain,
        "equal_family_dr_average_tail18_cdf_gain": equal_family_tail_gain,
        "overall_control_deadline_miss_auc": _safe_auc(
            data.primary_deadline_miss, 1.0 - control_deadline, overall_weights
        ),
        "overall_treated_deadline_rescue_auc": _safe_auc(
            (data.primary_deadline_miss[data.treatment == 1] == 1)
            & (data.deadline_miss[data.treatment == 1] == 0),
            predicted_rescue[data.treatment == 1],
            overall_treated_weights[overall_treated],
        ),
        "fit_records": fits,
    }


def _prediction_header(thresholds: Sequence[int]) -> list[str]:
    fields = [
        "analysis_schema_version",
        "seed",
        "run_number",
        "run_id",
        "frame_id",
        "scenario_id",
        "family_id",
        "parameter_sample",
        "treatment",
        "treatment_probability",
        "outcome_bin",
        "outcome_deadline_miss",
        "outcome_primary_deadline_miss",
        "deadline_us",
        "canonical_reservation_us",
        "ood_score",
        "ood_threshold",
        "ood_hard_failure",
        "ood_soft",
        "ood_fallback",
        "predicted_deadline_rescue_probability",
        "predicted_tail18_acceleration_probability",
    ]
    for arm in (0, 1):
        fields.extend(f"arm{arm}_cdf_{threshold}us" for threshold in thresholds)
    return fields


def _write_predictions(
    path: Path,
    data: lofo.LofoDataset,
    cdf: np.ndarray,
    ood: dict[str, np.ndarray],
) -> None:
    thresholds = data.contract["completion_distribution"]["thresholds_us"]
    deadline_indices = data.deadline_threshold_indices.astype(int)
    rows = np.arange(len(data.outcome_bins))
    rescue = np.maximum(
        cdf[rows, 1, deadline_indices] - cdf[rows, 0, deadline_indices], 0.0
    )
    tail_index = thresholds.index(18_000)
    tail = np.maximum(cdf[:, 1, tail_index] - cdf[:, 0, tail_index], 0.0)
    with path.open("wb") as binary:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=binary, mtime=0
        ) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                writer = csv.DictWriter(
                    text,
                    fieldnames=_prediction_header(thresholds),
                    lineterminator="\n",
                )
                writer.writeheader()
                for index in range(len(data.outcome_bins)):
                    row: dict[str, Any] = {
                        "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
                        "seed": int(data.seeds[index]),
                        "run_number": int(data.run_numbers[index]),
                        "run_id": data.run_ids[index],
                        "frame_id": int(data.frame_ids[index]),
                        "scenario_id": data.scenario_ids[index],
                        "family_id": data.family_ids[index],
                        "parameter_sample": int(data.parameter_samples[index]),
                        "treatment": int(data.treatment[index]),
                        "treatment_probability": format(data.propensity[index], ".17g"),
                        "outcome_bin": int(data.outcome_bins[index]),
                        "outcome_deadline_miss": int(data.deadline_miss[index]),
                        "outcome_primary_deadline_miss": int(
                            data.primary_deadline_miss[index]
                        ),
                        "deadline_us": int(data.deadline_us[index]),
                        "canonical_reservation_us": format(
                            data.canonical_reservation_us[index], ".17g"
                        ),
                        "ood_score": format(ood["score"][index], ".17g"),
                        "ood_threshold": format(ood["threshold"][index], ".17g"),
                        "ood_hard_failure": int(ood["hard_failure"][index]),
                        "ood_soft": int(ood["soft_ood"][index]),
                        "ood_fallback": int(ood["fallback"][index]),
                        "predicted_deadline_rescue_probability": format(
                            rescue[index], ".17g"
                        ),
                        "predicted_tail18_acceleration_probability": format(
                            tail[index], ".17g"
                        ),
                    }
                    for arm in (0, 1):
                        for threshold_index, threshold in enumerate(thresholds):
                            row[f"arm{arm}_cdf_{threshold}us"] = format(
                                cdf[index, arm, threshold_index], ".17g"
                            )
                    writer.writerow(row)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def analyze(
    dataset_dir: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Run the frozen LOFO fit and atomically publish checksum-closed outputs."""

    destination = Path(output_dir).resolve()
    if destination.exists():
        raise lofo.LofoError(f"refusing to overwrite output directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.tmp-", dir=destination.parent)
    )
    try:
        data = lofo.load_dataset(dataset_dir)
        cdf, ood, fits = fit_lofo_predictions(data)
        summary = summarize_predictions(data, cdf, ood, fits)
        prediction_path = temporary / OUTPUT_PREDICTIONS
        metrics_path = temporary / OUTPUT_METRICS
        _write_predictions(prediction_path, data, cdf, ood)
        metrics: dict[str, Any] = {
            "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
            "analysis_id": data.contract["analysis_id"],
            "evidence_role": "predeclared_randomized_collection_lofo_diagnostic",
            "design_contract": {
                "path": str(lofo.CONTRACT_PATH),
                "sha256": lofo.CONTRACT_SHA256,
            },
            "analysis_sources_sha256": {
                str(source.relative_to(lofo.ROOT)): _sha256(source)
                for source in ANALYSIS_SOURCES
            },
            "dataset": {
                "path": str(data.path),
                "artifact_manifest_sha256": _sha256(
                    data.path / "artifact_manifest.json"
                ),
                "dataset_metadata_sha256": _sha256(
                    data.path / "dataset_metadata.json"
                ),
                "dataset_csv_sha256": _sha256(
                    data.path / "environment_randomized_t2_temporal.csv"
                ),
                "row_count": len(data.run_ids),
                "run_count": len(set(data.run_ids)),
                "scenario_count": len(set(data.scenario_ids)),
                "family_count": len(set(data.family_ids)),
            },
            "predictor": {
                "feature_family": data.contract["predictor"]["feature_family"],
                "encoded_feature_count": len(data.model_feature_names),
                "model_spec": data.contract["predictor"]["model_spec"],
                "completion_distribution": data.contract[
                    "completion_distribution"
                ],
            },
            "diagnostics": summary,
            "software": {
                "python": platform.python_version(),
                "numpy": importlib.metadata.version("numpy"),
                "scikit_learn": importlib.metadata.version("scikit-learn"),
                "git_commit": _git_value("rev-parse", "HEAD"),
                "git_status_porcelain": _git_value(
                    "status", "--porcelain", "--untracked-files=all"
                ),
            },
        }
        _write_json(metrics_path, metrics)
        manifest = {
            "manifest_schema_version": 1,
            "hash_algorithm": "sha256",
            "artifacts_sha256": {
                OUTPUT_PREDICTIONS: _sha256(prediction_path),
                OUTPUT_METRICS: _sha256(metrics_path),
            },
        }
        _write_json(temporary / OUTPUT_MANIFEST, manifest)
        os.replace(temporary, destination)
        return metrics
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="fit the frozen environment-generalization LOFO distributions"
    )
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    metrics = analyze(args.dataset_dir, args.output_dir)
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "row_count": metrics["dataset"]["row_count"],
                "scenario_count": metrics["dataset"]["scenario_count"],
                "equal_family_dr_average_deadline_cdf_gain": metrics[
                    "diagnostics"
                ]["equal_family_dr_average_deadline_cdf_gain"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
