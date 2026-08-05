#!/usr/bin/env python3
"""Fit fold-honest training-score references for the selected T2 predictor.

Ordinary out-of-fold predictions are honest for their own row, but a score
from another fold can have been fit using the eventual evaluation fold.  This
tool reproduces the selected outer-fold models and scores only their 84
training groups, giving the online allocator a leakage-free future-score
reference distribution.
"""

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
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

import numpy as np

import analyze_temporal_t2_distributional_frontier as frontier
import build_randomized_temporal_dataset as temporal_builder
import crossfit_temporal_t2_distributions as crossfit


REFERENCE_SCHEMA_VERSION = 1
REFERENCE_ID = "temporal-t2-shadow-reference-v1"
OUTPUT_PREDICTIONS = "temporal_t2_shadow_reference_predictions.csv.gz"
OUTPUT_METRICS = "temporal_t2_shadow_reference_metrics.json"
OUTPUT_MANIFEST = "artifact_manifest.json"


class ShadowReferenceError(RuntimeError):
    """Raised when the fold-honest shadow reference cannot be produced."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise ShadowReferenceError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ShadowReferenceError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise ShadowReferenceError(f"{path}: expected a JSON object")
    return value


def _verify_static_artifacts(static_dir: Path) -> dict[str, Any]:
    manifest = _read_json(static_dir / frontier.OUTPUT_MANIFEST)
    result = _read_json(static_dir / frontier.OUTPUT_METRICS)
    artifacts = manifest.get("artifacts_sha256")
    expected = {
        frontier.OUTPUT_METRICS,
        frontier.OUTPUT_REPORT,
        frontier.OUTPUT_FIGURE,
    }
    if (
        manifest.get("analysis_id") != frontier.ANALYSIS_ID
        or result.get("analysis_id") != frontier.ANALYSIS_ID
        or not isinstance(artifacts, dict)
        or set(artifacts) != expected
    ):
        raise ShadowReferenceError("static frontier artifact closure differs")
    for name, digest in artifacts.items():
        if not isinstance(digest, str) or _sha256(static_dir / name) != digest:
            raise ShadowReferenceError(f"static frontier artifact hash differs: {name}")
    return result


def _candidate(result: dict[str, Any], variant: str) -> dict[str, Any]:
    matches = [
        row
        for row in result.get("policies", [])
        if row.get("variant") == variant
        and row.get("objective") == "deadline_rescue"
        and row.get("frame_gate") == "p_frames_only"
        and row.get("budget_us_per_run") == 372_000
    ]
    if len(matches) != 1:
        raise ShadowReferenceError(f"cannot resolve static candidate {variant}")
    return matches[0]


def select_variant(result: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Apply the frozen lexicographic predictor-selection priorities."""

    variant_order = frontier._variant_order()
    family_rank = {
        crossfit.PRIMARY_FAMILY: 0,
        crossfit.SECONDARY_FAMILY: 1,
    }
    model_rank = {
        specification["id"]: index
        for index, specification in enumerate(crossfit.MODEL_SPECS)
    }
    ranked: list[tuple[tuple[Any, ...], str, dict[str, Any]]] = []
    for ordinal, variant in enumerate(variant_order):
        row = _candidate(result, variant)
        tail_delta = row["dr_policy_minus_none_completed_late18_ratio"][
            "estimate"
        ]
        key = (
            -int(row["captured_primary_deadline_misses"]),
            float(row["dr_policy_deadline_miss_probability"]),
            int(float(tail_delta) > 0),
            float(tail_delta),
            float(row["mean_canonical_reservation_us_per_run"]),
            family_rank[row["feature_family"]],
            model_rank[row["model_spec_id"]],
            ordinal,
        )
        ranked.append((key, variant, row))
    ranked.sort(key=lambda item: item[0])
    _, selected, record = ranked[0]
    return selected, record


def _variant_definition(variant: str) -> tuple[str, dict[str, Any], int]:
    ordinal = 0
    for family in crossfit.FEATURE_FAMILY_ORDER:
        for spec in crossfit.MODEL_SPECS:
            if crossfit._variant_id(family, spec) == variant:
                return family, spec, ordinal
            ordinal += 1
    raise ShadowReferenceError("selected variant is outside the frozen grid")


def _verify_dataset_join(
    fitted: crossfit.DistributionDataset,
    scored: frontier.FrontierDataset,
) -> None:
    if (
        len(fitted.outcome_bins) != len(scored.outcome_bins)
        or not np.array_equal(fitted.seeds, scored.seeds)
        or not np.array_equal(fitted.run_numbers, scored.run_numbers)
        or not np.array_equal(fitted.frame_ids, scored.frame_ids)
        or not np.array_equal(fitted.folds, scored.folds)
        or not np.array_equal(fitted.treatment, scored.treatment)
        or not np.allclose(
            fitted.propensity, scored.propensity, rtol=0.0, atol=0.0
        )
        or not np.array_equal(fitted.outcome_bins, scored.outcome_bins)
    ):
        raise ShadowReferenceError("fit and scored temporal rows differ")


def fit_reference_predictions(
    fitted: crossfit.DistributionDataset,
    scored: frontier.FrontierDataset,
    variant: str,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Return training-row CDFs for each reproduced outer-fold model."""

    family, spec, variant_ordinal = _variant_definition(variant)
    matrix = fitted.family_matrices[family]
    reference = np.full(
        (
            crossfit.FOLD_COUNT,
            len(fitted.outcome_bins),
            2,
            len(crossfit.THRESHOLDS_US),
        ),
        np.nan,
        dtype=float,
    )
    fits: list[dict[str, Any]] = []
    maximum_oof_difference = 0.0
    for fold in range(crossfit.FOLD_COUNT):
        evaluation = fitted.folds == fold
        training = ~evaluation
        for arm in (0, 1):
            selected = training & (fitted.treatment == arm)
            labels = fitted.outcome_bins[selected]
            seed = (
                crossfit.RANDOM_SEED
                + variant_ordinal * 1000
                + fold * 10
                + arm
            )
            model = crossfit._model(spec, seed)
            model.fit(matrix[selected], labels)
            training_probabilities = crossfit.aligned_smoothed_probabilities(
                model, matrix[training], int(np.sum(selected))
            )
            reference[fold, training, arm, :] = np.cumsum(
                training_probabilities, axis=1
            )[:, : len(crossfit.THRESHOLDS_US)]
            evaluation_probabilities = crossfit.aligned_smoothed_probabilities(
                model, matrix[evaluation], int(np.sum(selected))
            )
            reproduced = np.cumsum(evaluation_probabilities, axis=1)[
                :, : len(crossfit.THRESHOLDS_US)
            ]
            difference = float(
                np.max(
                    np.abs(
                        reproduced
                        - scored.cdf_by_variant[variant][evaluation, arm, :]
                    )
                )
            )
            maximum_oof_difference = max(maximum_oof_difference, difference)
            fits.append(
                {
                    "fold": fold,
                    "arm": arm,
                    "training_rows": int(np.sum(selected)),
                    "reference_rows": int(np.sum(training)),
                    "evaluation_rows_reproduced": int(np.sum(evaluation)),
                    "observed_training_classes": sorted(set(labels.tolist())),
                    "random_seed": seed,
                    "maximum_oof_absolute_difference": difference,
                }
            )
            del model, training_probabilities, evaluation_probabilities, reproduced
            gc.collect()
            print(
                f"{variant} reference fold {fold + 1}/{crossfit.FOLD_COUNT} "
                f"arm {arm} complete",
                flush=True,
            )
    if maximum_oof_difference > 1e-12:
        raise ShadowReferenceError(
            "reproduced outer models differ from cross-fitted predictions"
        )
    expected_finite = np.broadcast_to(
        fitted.folds[None, :, None, None]
        != np.arange(crossfit.FOLD_COUNT)[:, None, None, None],
        reference.shape,
    )
    if not np.array_equal(np.isfinite(reference), expected_finite):
        raise ShadowReferenceError("shadow reference fold mask differs")
    if np.any(np.diff(np.nan_to_num(reference), axis=3) < -1e-12):
        raise ShadowReferenceError("shadow reference CDF is not monotone")
    return reference, fits


def _header(variant: str) -> list[str]:
    result = [
        "reference_schema_version",
        "evaluation_fold",
        "seed",
        "run_number",
        "frame_id",
    ]
    for arm in (0, 1):
        for threshold in crossfit.THRESHOLDS_US:
            result.append(f"{variant}__arm{arm}_cdf_{threshold}us")
    return result


def _write_predictions(
    path: Path,
    data: crossfit.DistributionDataset,
    variant: str,
    reference: np.ndarray,
) -> int:
    count = 0
    with path.open("wb") as binary:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=binary, mtime=0
        ) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                writer = csv.DictWriter(
                    text, fieldnames=_header(variant), lineterminator="\n"
                )
                writer.writeheader()
                for fold in range(crossfit.FOLD_COUNT):
                    for index in np.flatnonzero(data.folds != fold):
                        row: dict[str, Any] = {
                            "reference_schema_version": REFERENCE_SCHEMA_VERSION,
                            "evaluation_fold": fold,
                            "seed": int(data.seeds[index]),
                            "run_number": int(data.run_numbers[index]),
                            "frame_id": int(data.frame_ids[index]),
                        }
                        for arm in (0, 1):
                            for threshold_index, threshold in enumerate(
                                crossfit.THRESHOLDS_US
                            ):
                                row[
                                    f"{variant}__arm{arm}_cdf_{threshold}us"
                                ] = format(
                                    reference[
                                        fold, index, arm, threshold_index
                                    ],
                                    ".17g",
                                )
                        writer.writerow(row)
                        count += 1
    return count


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _git_value(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def fit_to_directory(
    temporal_dir: Path | str,
    distribution_dir: Path | str,
    static_dir: Path | str,
    output_dir: Path | str,
) -> dict[str, Any]:
    """Select the frozen winner, reproduce its folds, and write references."""

    destination = Path(output_dir).resolve()
    if destination.exists():
        raise ShadowReferenceError(
            f"refusing to overwrite output directory: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        static_path = Path(static_dir).resolve()
        static_result = _verify_static_artifacts(static_path)
        selected_variant, selection_record = select_variant(static_result)
        scored = frontier.load_frontier_dataset(
            distribution_dir, temporal_dir
        )
        if static_result.get("source", {}).get(
            "crossfit_predictions_sha256"
        ) != _sha256(scored.distribution_dir / crossfit.OUTPUT_PREDICTIONS):
            raise ShadowReferenceError("static selection and predictions differ")
        fitted = crossfit.load_distribution_dataset(temporal_dir)
        _verify_dataset_join(fitted, scored)
        reference, fits = fit_reference_predictions(
            fitted, scored, selected_variant
        )
        prediction_path = temporary / OUTPUT_PREDICTIONS
        reference_rows = _write_predictions(
            prediction_path, fitted, selected_variant, reference
        )
        expected_rows = (crossfit.FOLD_COUNT - 1) * len(fitted.outcome_bins)
        if reference_rows != expected_rows:
            raise ShadowReferenceError("shadow reference row count differs")
        result = {
            "reference_schema_version": REFERENCE_SCHEMA_VERSION,
            "reference_id": REFERENCE_ID,
            "evidence_role": "fold_honest_online_allocator_training_reference",
            "reference_score_role": (
                "in-sample scores from each deployed outer-fold model on its "
                "own 84 training groups; evaluation-fold rows and outcomes are "
                "excluded"
            ),
            "selected_variant": selected_variant,
            "selection_rule": (
                "frozen lexicographic direct capture, DR miss, tail nonregression, "
                "canonical reservation, feature scope, model size"
            ),
            "selection_record": selection_record,
            "fold_count": crossfit.FOLD_COUNT,
            "training_groups_per_fold": 84,
            "reference_row_count": reference_rows,
            "fit_records": fits,
            "maximum_reproduced_oof_absolute_difference": max(
                row["maximum_oof_absolute_difference"] for row in fits
            ),
            "source": {
                "temporal_csv_sha256": _sha256(
                    fitted.path / temporal_builder.OUTPUT_CSV
                ),
                "crossfit_manifest_sha256": _sha256(
                    scored.distribution_dir / crossfit.OUTPUT_MANIFEST
                ),
                "crossfit_predictions_sha256": _sha256(
                    scored.distribution_dir / crossfit.OUTPUT_PREDICTIONS
                ),
                "crossfit_metrics_sha256": _sha256(
                    scored.distribution_dir / crossfit.OUTPUT_METRICS
                ),
                "static_metrics_sha256": _sha256(
                    static_path / frontier.OUTPUT_METRICS
                ),
                "static_manifest_sha256": _sha256(
                    static_path / frontier.OUTPUT_MANIFEST
                ),
            },
            "provenance": {
                "project_git_commit": _git_value("rev-parse", "HEAD"),
                "project_git_status_porcelain": _git_value(
                    "status", "--porcelain"
                ),
                "tool": str(
                    Path(__file__).resolve().relative_to(
                        Path(__file__).resolve().parents[1]
                    )
                ),
                "tool_sha256": _sha256(Path(__file__).resolve()),
                "python": platform.python_version(),
                "numpy": np.__version__,
                "scikit_learn": importlib.metadata.version("scikit-learn"),
            },
        }
        metrics_path = temporary / OUTPUT_METRICS
        _write_json(metrics_path, result)
        manifest = {
            "manifest_schema_version": 1,
            "hash_algorithm": "sha256",
            "reference_id": REFERENCE_ID,
            "artifacts_sha256": {
                OUTPUT_PREDICTIONS: _sha256(prediction_path),
                OUTPUT_METRICS: _sha256(metrics_path),
            },
            "source_artifacts_sha256": result["source"],
        }
        _write_json(temporary / OUTPUT_MANIFEST, manifest)
        os.replace(temporary, destination)
        return result
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temporal-dir", type=Path, required=True)
    parser.add_argument("--distribution-dir", type=Path, required=True)
    parser.add_argument("--static-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = fit_to_directory(
            args.temporal_dir,
            args.distribution_dir,
            args.static_dir,
            args.output_dir,
        )
    except (
        ShadowReferenceError,
        frontier.FrontierError,
        crossfit.DistributionError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "reference_id": result["reference_id"],
                "selected_variant": result["selected_variant"],
                "reference_row_count": result["reference_row_count"],
                "maximum_reproduced_oof_absolute_difference": result[
                    "maximum_reproduced_oof_absolute_difference"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
