#!/usr/bin/env python3
"""Fit and export the frozen distributional temporal-T2 runtime.

The selected cross-fitted predictor and allocator have already been chosen.
This tool performs only the deployment refit on all opened development groups,
constructs the fixed shadow-price reference, checks an in-sample runtime replay,
and emits checksum-closed portable artifacts.  It never reads confirmation
seeds.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import pickle
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline

import analyze_temporal_t2_distributional_frontier as static_frontier
import analyze_temporal_t2_online_allocator as online
import crossfit_temporal_t2_distributions as crossfit
import export_temporal_t2_value_model_v1 as legacy_export
import fit_temporal_t2_shadow_reference as shadow_reference


EXPORT_SCHEMA_VERSION = 1
RUNTIME_CONTRACT = Path(
    "experiments/model-selection/temporal-t2-shadow-borrow-runtime-v1.json"
)
RUNTIME_CONTRACT_SHA256 = (
    "33b16c62848d0b724d347b791650e805c0fe2611eaf44ac4079b93cb59b5f4fa"
)
RUNTIME_CONTRACT_ID = "temporal-t2-shadow-borrow-runtime-v1"
SELECTED_VARIANT = "primary_secondary_hgb64"
SELECTED_FAMILY = crossfit.SECONDARY_FAMILY
SELECTED_FEATURE_COUNT = 308
SELECTED_VARIANT_ORDINAL = 2
CONTROL_SEED = 20262885
TREATED_SEED = 20262886
MODEL_SPEC = crossfit.MODEL_SPECS[0]
TIME_BIN_COUNT = 12
TIME_BIN_WIDTH_US = 5_000_000
REGIME_COUNT = 3
DIRICHLET_ALPHA = Decimal("0.5")
MAXIMUM_REPAYABLE_CREDIT_US = Decimal("372000")

OUTPUT_MODEL_PICKLE = "temporal_t2_shadow_runtime_models.pkl"
OUTPUT_MODEL_JSON = "temporal_t2_shadow_runtime_model.json"
OUTPUT_REFERENCE_JSON = "temporal_t2_shadow_runtime_reference.json"
OUTPUT_METRICS = "temporal_t2_shadow_runtime_refit_metrics.json"
OUTPUT_MANIFEST = "artifact_manifest.json"
OUTPUT_FILES = (
    OUTPUT_MODEL_PICKLE,
    OUTPUT_MODEL_JSON,
    OUTPUT_REFERENCE_JSON,
    OUTPUT_METRICS,
    OUTPUT_MANIFEST,
)

DEFAULT_DATA_HEADER = Path(
    "contrib/wifi-streaming/model/temporal-t2-distribution-model-data-v1.h"
)
DEFAULT_DATA_SOURCE = Path(
    "contrib/wifi-streaming/model/temporal-t2-distribution-model-data-v1.cc"
)
DEFAULT_GOLDEN_HEADER = Path(
    "contrib/wifi-streaming/test/temporal-t2-distribution-model-goldens-v1.h"
)


class RuntimeExportError(RuntimeError):
    """Raised when the frozen deployment artifact cannot be established."""


@dataclass(frozen=True)
class RuntimeFit:
    """Fitted deployment heads and predictions on the opened dataset."""

    data: crossfit.DistributionDataset
    feature_names: tuple[str, ...]
    control_model: Pipeline
    treated_model: Pipeline
    cdf: np.ndarray
    rewards: np.ndarray
    primary_busy_20ms: np.ndarray


@dataclass(frozen=True)
class RuntimeCurve:
    """One exact P-frame marginal reward-density curve."""

    density_descending: np.ndarray
    training_run_count: int

    def opportunity_cost(
        self, repayable_credit_us: Decimal, p_cost_us: Decimal
    ) -> float:
        """Return the frozen marginal density at the resource state."""

        if repayable_credit_us < 0 or p_cost_us <= 0 or self.training_run_count <= 0:
            raise RuntimeExportError("invalid runtime shadow-price state")
        count = len(self.density_descending)
        if count == 0:
            return math.inf
        target = repayable_credit_us * self.training_run_count
        low = 0
        high = count
        while low < high:
            middle = (low + high) // 2
            if p_cost_us * Decimal(middle + 1) <= target:
                low = middle + 1
            else:
                high = middle
        affordable = low
        if affordable == 0:
            return math.inf
        if affordable == count:
            return 0.0
        return float(self.density_descending[affordable - 1])


@dataclass(frozen=True)
class RuntimeReference:
    """Full-data congestion states and deployment shadow curves."""

    p_cost_text: str
    p_cost_us: Decimal
    decision_times: np.ndarray
    time_bins: np.ndarray
    indices_by_unit: dict[static_frontier.Unit, np.ndarray]
    cutpoints: tuple[tuple[float, float], ...]
    congestion_curves: dict[tuple[int, int], RuntimeCurve]
    global_curves: dict[int, RuntimeCurve]


def repository_root() -> Path:
    """Return the repository containing this exporter."""

    return Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest."""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise RuntimeExportError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    """Read one JSON object."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeExportError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeExportError(f"{path}: expected a JSON object")
    return value


def write_json(path: Path, value: Any) -> None:
    """Write deterministic human-readable JSON."""

    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def canonical_sha256(value: Any) -> str:
    """Hash one value using compact canonical JSON bytes."""

    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _git_value(*arguments: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *arguments],
            cwd=repository_root(),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _expected_paths(contract: dict[str, Any]) -> dict[Path, str]:
    """Resolve every source digest pinned by the runtime contract."""

    source = contract["source_evidence"]
    pairs: list[tuple[str, str]] = []
    for name in (
        "distributional_design_contract",
        "online_design_contract",
        "borrow_repay_design_contract",
    ):
        pairs.append((source[name]["path"], source[name]["sha256"]))
    dataset = source["temporal_dataset"]
    pairs.extend(
        (
            (f"{dataset['path']}/artifact_manifest.json", dataset["artifact_manifest_sha256"]),
            (f"{dataset['path']}/dataset_metadata.json", dataset["dataset_metadata_sha256"]),
            (f"{dataset['path']}/randomized_t2_temporal.csv", dataset["randomized_t2_temporal_sha256"]),
        )
    )
    for name, metric_file in (
        ("crossfit_result", crossfit.OUTPUT_METRICS),
        ("static_frontier_result", static_frontier.OUTPUT_METRICS),
        ("borrow_repay_result", "temporal_t2_shadow_borrow.json"),
    ):
        item = source[name]
        pairs.extend(
            (
                (f"{item['path']}/artifact_manifest.json", item["artifact_manifest_sha256"]),
                (f"{item['path']}/{metric_file}", item["metrics_sha256"]),
            )
        )
    result = {repository_root() / path: digest for path, digest in pairs}
    if len(result) != len(pairs):
        raise RuntimeExportError("runtime contract source paths are duplicate")
    return result


def _verify_manifest_closure(directory: Path) -> dict[str, Any]:
    """Verify every artifact named by one source manifest."""

    manifest = read_json(directory / "artifact_manifest.json")
    artifacts = manifest.get("artifacts_sha256")
    if not isinstance(artifacts, dict) or not artifacts:
        raise RuntimeExportError(f"{directory}: artifact manifest differs")
    for name, digest in artifacts.items():
        if not isinstance(name, str) or not isinstance(digest, str):
            raise RuntimeExportError(f"{directory}: artifact digest schema differs")
        path = directory / name
        if not path.is_file() or path.is_symlink() or sha256_file(path) != digest:
            raise RuntimeExportError(f"source artifact hash differs: {path}")
    return manifest


def validate_contract_and_sources() -> dict[str, Any]:
    """Validate the frozen contract and all source artifacts before fitting."""

    contract_path = repository_root() / RUNTIME_CONTRACT
    if sha256_file(contract_path) != RUNTIME_CONTRACT_SHA256:
        raise RuntimeExportError("runtime contract hash differs")
    contract = read_json(contract_path)
    refit = contract.get("full_data_refit", {})
    accounting = contract.get("shadow_priced_borrow_repay", {})
    boundary = contract.get("evidence_boundary", {})
    if (
        contract.get("schema_version") != EXPORT_SCHEMA_VERSION
        or contract.get("runtime_contract_id") != RUNTIME_CONTRACT_ID
        or contract.get("status")
        != "frozen_before_full_data_refit_and_runtime_implementation"
        or refit.get("selected_variant") != SELECTED_VARIANT
        or refit.get("feature_family") != SELECTED_FAMILY
        or refit.get("raw_feature_count") != SELECTED_FEATURE_COUNT
        or refit.get("control_random_seed") != CONTROL_SEED
        or refit.get("treated_random_seed") != TREATED_SEED
        or refit.get("model_spec") != MODEL_SPEC
        or boundary.get("reserved_confirmation_seed_range") != [1301, 1348]
        or boundary.get("reserved_confirmation_seeds_must_remain_unread") is not True
        or accounting.get("measurement_duration_us") != int(online.MEASUREMENT_DURATION_US)
        or accounting.get("initial_credit_us") != int(online.INITIAL_CREDIT_US)
        or accounting.get("positive_balance_capacity_us") != int(online.CAPACITY_US)
        or accounting.get("causal_refill_rate_us_per_us") != float(online.REFILL_RATE)
        or accounting.get("maximum_generated_credit_us")
        != int(online.MAXIMUM_GENERATED_CREDIT_US)
        or accounting.get("measured_settlement_release") is not False
    ):
        raise RuntimeExportError("runtime contract semantics differ")
    for path, expected in _expected_paths(contract).items():
        if not path.is_file() or path.is_symlink() or sha256_file(path) != expected:
            raise RuntimeExportError(f"pinned source hash differs: {path}")
    source = contract["source_evidence"]
    for name in (
        "crossfit_result",
        "static_frontier_result",
        "borrow_repay_result",
    ):
        _verify_manifest_closure(repository_root() / source[name]["path"])

    static_metrics = read_json(
        repository_root()
        / source["static_frontier_result"]["path"]
        / static_frontier.OUTPUT_METRICS
    )
    selected, _ = shadow_reference.select_variant(static_metrics)
    if selected != SELECTED_VARIANT:
        raise RuntimeExportError("frozen static selection no longer chooses runtime variant")
    return contract


def _model_specification() -> dict[str, Any]:
    """Return a detached copy of the selected model specification."""

    return dict(MODEL_SPEC)


def fit_runtime_models(dataset_dir: Path | str) -> RuntimeFit:
    """Fit both full-data heads and score every opened development row."""

    data = crossfit.load_distribution_dataset(dataset_dir)
    observed_seeds = sorted(set(int(value) for value in data.seeds))
    if observed_seeds != list(range(1101, 1197)) or any(
        value >= 1301 for value in observed_seeds
    ):
        raise RuntimeExportError("runtime refit seed boundary differs")
    matrix = data.family_matrices[SELECTED_FAMILY]
    names = data.family_feature_names[SELECTED_FAMILY]
    if matrix.shape != (73_400, SELECTED_FEATURE_COUNT) or len(names) != SELECTED_FEATURE_COUNT:
        raise RuntimeExportError("runtime refit feature matrix differs")
    models: list[Pipeline] = []
    cdf = np.empty(
        (len(matrix), 2, len(crossfit.THRESHOLDS_US)), dtype=np.float64
    )
    for arm, seed in ((0, CONTROL_SEED), (1, TREATED_SEED)):
        selected = data.treatment == arm
        expected_rows = 66_759 if arm == 0 else 6_641
        labels = data.outcome_bins[selected]
        if int(np.sum(selected)) != expected_rows or sorted(set(labels.tolist())) != list(
            range(crossfit.CLASS_COUNT)
        ):
            raise RuntimeExportError(f"runtime arm {arm} training support differs")
        model = crossfit._model(_model_specification(), seed)
        model.fit(matrix[selected], labels)
        probability = crossfit.aligned_smoothed_probabilities(
            model, matrix, expected_rows
        )
        cdf[:, arm, :] = np.cumsum(probability, axis=1)[
            :, : len(crossfit.THRESHOLDS_US)
        ]
        models.append(model)
        print(f"full-data {SELECTED_VARIANT} arm {arm} complete", flush=True)
    if (
        not np.all(np.isfinite(cdf))
        or np.any((cdf < 0.0) | (cdf > 1.0))
        or np.any(np.diff(cdf, axis=2) < -1e-12)
    ):
        raise RuntimeExportError("runtime CDF predictions are invalid")
    rewards = np.maximum(cdf[:, 1, -1] - cdf[:, 0, -1], 0.0)
    try:
        names.index("x_primary_phy_busy_fraction_20ms")
    except ValueError as error:
        raise RuntimeExportError("runtime congestion feature is absent") from error
    primary_busy = _load_raw_primary_busy(data)
    if not np.all(np.isfinite(primary_busy)) or np.any(
        (primary_busy < 0.0) | (primary_busy > 1.0)
    ):
        raise RuntimeExportError("runtime congestion feature is invalid")
    return RuntimeFit(
        data, names, models[0], models[1], cdf, rewards, primary_busy
    )


def _load_raw_primary_busy(data: crossfit.DistributionDataset) -> np.ndarray:
    """Load the allocator's unquantized causal busy signal in exact row order."""

    values = np.empty(len(data.seeds), dtype=np.float64)
    csv_path = data.path / "randomized_t2_temporal.csv"
    count = 0
    try:
        with csv_path.open(newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            for index, row in enumerate(reader):
                if index >= len(values):
                    raise RuntimeExportError("temporal CSV has extra congestion rows")
                identity = (
                    int(row["seed"]),
                    int(row["run_number"]),
                    int(row["frame_id"]),
                )
                expected = (
                    int(data.seeds[index]),
                    int(data.run_numbers[index]),
                    int(data.frame_ids[index]),
                )
                if identity != expected:
                    raise RuntimeExportError("temporal congestion row identity differs")
                values[index] = float(row["x_primary_phy_busy_fraction_20ms"])
                count = index + 1
            if count != len(values):
                raise RuntimeExportError("temporal CSV congestion coverage differs")
    except (OSError, KeyError, ValueError) as error:
        raise RuntimeExportError(f"cannot load runtime congestion signal: {error}") from error
    return values


def _finite(value: Any, context: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise RuntimeExportError(f"{context}: invalid finite number") from error
    if not math.isfinite(result):
        raise RuntimeExportError(f"{context}: non-finite number")
    return result


def _imputer_payload(imputer: Any, feature_count: int, context: str) -> dict[str, Any]:
    if not isinstance(imputer, SimpleImputer):
        raise RuntimeExportError(f"{context}: unsupported imputer")
    if (
        imputer.strategy != "median"
        or imputer.add_indicator is not True
        or imputer.keep_empty_features is not True
    ):
        raise RuntimeExportError(f"{context}: imputer parameters differ")
    medians = np.asarray(imputer.statistics_, dtype=np.float64)
    indicators = np.asarray(imputer.indicator_.features_)
    if (
        medians.shape != (feature_count,)
        or not np.all(np.isfinite(medians))
        or indicators.ndim != 1
        or not np.issubdtype(indicators.dtype, np.integer)
        or len(set(indicators.tolist())) != len(indicators)
        or np.any(indicators < 0)
        or np.any(indicators >= feature_count)
    ):
        raise RuntimeExportError(f"{context}: fitted imputer shape differs")
    return {
        "medians": medians.tolist(),
        "missing_indicator_raw_features": [int(value) for value in indicators],
    }


def multiclass_hgb_payload(
    pipeline: Any, feature_count: int, training_count: int, context: str
) -> dict[str, Any]:
    """Extract one fitted six-class HGB pipeline into plain values."""

    if not isinstance(pipeline, Pipeline) or list(pipeline.named_steps) != [
        "impute",
        "classifier",
    ]:
        raise RuntimeExportError(f"{context}: classifier pipeline differs")
    imputer = _imputer_payload(pipeline.named_steps["impute"], feature_count, context)
    classifier = pipeline.named_steps["classifier"]
    if not isinstance(classifier, HistGradientBoostingClassifier):
        raise RuntimeExportError(f"{context}: classifier type differs")
    parameters = classifier.get_params(deep=False)
    expected = {
        "loss": "log_loss",
        "learning_rate": MODEL_SPEC["learning_rate"],
        "max_iter": MODEL_SPEC["max_iter"],
        "max_leaf_nodes": MODEL_SPEC["max_leaf_nodes"],
        "max_depth": MODEL_SPEC["max_depth"],
        "min_samples_leaf": MODEL_SPEC["min_samples_leaf"],
        "l2_regularization": MODEL_SPEC["l2_regularization"],
        "max_bins": MODEL_SPEC["max_bins"],
        "early_stopping": MODEL_SPEC["early_stopping"],
    }
    transformed_count = feature_count + len(
        imputer["missing_indicator_raw_features"]
    )
    if (
        any(parameters.get(key) != value for key, value in expected.items())
        or int(classifier.n_features_in_) != transformed_count
        or np.asarray(classifier.classes_).tolist()
        != list(range(crossfit.CLASS_COUNT))
        or len(classifier._predictors) != MODEL_SPEC["max_iter"]
    ):
        raise RuntimeExportError(f"{context}: fitted HGB contract differs")
    nodes: list[list[Any]] = []
    trees: list[list[int]] = []
    for iteration_index, iteration in enumerate(classifier._predictors):
        if len(iteration) != crossfit.CLASS_COUNT:
            raise RuntimeExportError(f"{context}: iteration class count differs")
        for class_index, predictor in enumerate(iteration):
            offset = len(nodes)
            count = len(predictor.nodes)
            if count <= 0 or count > 65535:
                raise RuntimeExportError(f"{context}: invalid tree size")
            for node in predictor.nodes:
                value = _finite(node["value"], f"{context} tree value")
                threshold = _finite(
                    node["num_threshold"], f"{context} tree threshold"
                )
                feature = int(node["feature_idx"])
                left = int(node["left"])
                right = int(node["right"])
                missing_left = bool(node["missing_go_to_left"])
                leaf = bool(node["is_leaf"])
                if bool(node["is_categorical"]):
                    raise RuntimeExportError(f"{context}: categorical split found")
                if not 0 <= feature < transformed_count:
                    raise RuntimeExportError(f"{context}: tree feature escapes input")
                if not leaf and not (0 <= left < count and 0 <= right < count):
                    raise RuntimeExportError(f"{context}: tree child escapes tree")
                if leaf and (left != 0 or right != 0):
                    raise RuntimeExportError(f"{context}: leaf has children")
                nodes.append(
                    [value, threshold, feature, left, right, missing_left, leaf]
                )
            trees.append([offset, count, class_index, iteration_index])
    baseline = np.asarray(
        classifier._baseline_prediction, dtype=np.float64
    ).reshape(-1)
    if baseline.shape != (crossfit.CLASS_COUNT,) or not np.all(np.isfinite(baseline)):
        raise RuntimeExportError(f"{context}: HGB baseline differs")
    return {
        "training_count": training_count,
        "dirichlet_alpha_per_class": float(crossfit.DIRICHLET_ALPHA),
        "imputer": imputer,
        "nodes": nodes,
        "trees": trees,
        "baseline": baseline.tolist(),
    }


def portable_model_payload(fit: RuntimeFit) -> dict[str, Any]:
    """Build the plain, hashable runtime model payload."""

    heads = {
        "control": multiclass_hgb_payload(
            fit.control_model, SELECTED_FEATURE_COUNT, 66_759, "CONTROL head"
        ),
        "full_copy_t2": multiclass_hgb_payload(
            fit.treated_model, SELECTED_FEATURE_COUNT, 6_641, "FULL_COPY_T2 head"
        ),
    }
    return {
        "model_schema_version": EXPORT_SCHEMA_VERSION,
        "runtime_contract_id": RUNTIME_CONTRACT_ID,
        "runtime_contract_sha256": RUNTIME_CONTRACT_SHA256,
        "selected_variant": SELECTED_VARIANT,
        "feature_family": SELECTED_FAMILY,
        "feature_names": list(fit.feature_names),
        "thresholds_us": list(crossfit.THRESHOLDS_US),
        "model_spec": _model_specification(),
        "heads": heads,
    }


def _quantize_features(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.shape != (SELECTED_FEATURE_COUNT,):
        raise RuntimeExportError("portable evaluator feature width differs")
    if np.any(np.isinf(array)):
        raise RuntimeExportError("portable evaluator input contains infinity")
    with np.errstate(over="ignore", invalid="ignore"):
        quantized = array.astype(np.float32)
    if np.any(np.isinf(quantized)):
        raise RuntimeExportError("portable evaluator input overflows float32")
    return quantized.astype(np.float64)


def evaluate_portable_head(head: dict[str, Any], raw: np.ndarray) -> dict[str, Any]:
    """Evaluate one extracted head independently of sklearn internals."""

    values = _quantize_features(raw)
    imputer = head["imputer"]
    medians = np.asarray(imputer["medians"], dtype=np.float64)
    indicators = np.asarray(
        imputer["missing_indicator_raw_features"], dtype=np.int64
    )
    missing = np.isnan(values)
    transformed = np.concatenate(
        (np.where(missing, medians, values), missing[indicators].astype(float))
    )
    logits = np.asarray(head["baseline"], dtype=np.float64).copy()
    nodes = head["nodes"]
    for offset, count, class_index, _iteration in head["trees"]:
        index = 0
        while True:
            if not 0 <= index < count:
                raise RuntimeExportError("portable tree child is invalid")
            value, threshold, feature, left, right, missing_left, leaf = nodes[
                offset + index
            ]
            if leaf:
                logits[class_index] += value
                break
            observed = transformed[feature]
            if math.isnan(observed):
                index = left if missing_left else right
            else:
                index = left if observed <= threshold else right
    shifted = logits - np.max(logits)
    softmax = np.exp(shifted)
    softmax /= np.sum(softmax)
    training_count = int(head["training_count"])
    alpha = float(head["dirichlet_alpha_per_class"])
    smoothed = (training_count * softmax + alpha) / (
        training_count + alpha * crossfit.CLASS_COUNT
    )
    return {
        "logits": logits,
        "softmax_probabilities": softmax,
        "smoothed_probabilities": smoothed,
        "cdf": np.cumsum(smoothed)[: len(crossfit.THRESHOLDS_US)],
    }


def portable_result(payload: dict[str, Any], raw: np.ndarray) -> dict[str, Any]:
    """Evaluate both plain heads and derive separate runtime benefits."""

    control = evaluate_portable_head(payload["heads"]["control"], raw)
    treated = evaluate_portable_head(payload["heads"]["full_copy_t2"], raw)
    deadline = max(float(treated["cdf"][-1] - control["cdf"][-1]), 0.0)
    tail18 = float(treated["cdf"][1] - control["cdf"][1])
    return {
        "control": control,
        "full_copy_t2": treated,
        "deadline_rescue_reward": deadline,
        "tail18_cdf_gain": tail18,
    }


def validate_portable_parity(fit: RuntimeFit, payload: dict[str, Any]) -> float:
    """Check independent extraction against sklearn on deterministic rows."""

    indices = np.unique(
        np.linspace(0, len(fit.data.treatment) - 1, 257, dtype=np.int64)
    )
    matrix = fit.data.family_matrices[SELECTED_FAMILY]
    maximum = 0.0
    for index in indices:
        raw = np.asarray(matrix[index], dtype=np.float64)
        portable = portable_result(payload, raw)
        vector = _quantize_features(raw).reshape(1, -1)
        for arm, model, name in (
            (0, fit.control_model, "control"),
            (1, fit.treated_model, "full_copy_t2"),
        ):
            classifier = model.named_steps["classifier"]
            logits = np.asarray(classifier.decision_function(
                model.named_steps["impute"].transform(vector)
            )).reshape(-1)
            probability = crossfit.aligned_smoothed_probabilities(
                model, vector, 66_759 if arm == 0 else 6_641
            )[0]
            maximum = max(
                maximum,
                float(np.max(np.abs(portable[name]["logits"] - logits))),
                float(
                    np.max(
                        np.abs(
                            portable[name]["smoothed_probabilities"] - probability
                        )
                    )
                ),
            )
    if maximum > 1e-12:
        raise RuntimeExportError("portable HGB extraction differs from sklearn")
    return maximum


def _canonical_p_cost(data: crossfit.DistributionDataset) -> tuple[str, Decimal]:
    values: set[str] = set()
    csv_path = data.path / "randomized_t2_temporal.csv"
    try:
        with csv_path.open(newline="", encoding="utf-8") as source:
            for row in csv.DictReader(source):
                if row["x_f0_frame_type"] == "P_FRAME":
                    values.add(row["action_estimated_airtime_us"])
    except OSError as error:
        raise RuntimeExportError(f"cannot read canonical costs: {error}") from error
    if values != {"1983.760667318285"}:
        raise RuntimeExportError("canonical P-frame reservation differs")
    text = next(iter(values))
    return text, Decimal(text)


def _regime(value: float, cutpoints: tuple[float, float]) -> int:
    return int(np.searchsorted(np.asarray(cutpoints), value, side="right"))


def _indices_by_unit(
    data: crossfit.DistributionDataset,
) -> dict[static_frontier.Unit, np.ndarray]:
    units = tuple(
        sorted(
            {
                (int(data.seeds[index]), int(data.run_numbers[index]))
                for index in range(len(data.seeds))
            }
        )
    )
    return {
        unit: np.flatnonzero(
            (data.seeds == unit[0]) & (data.run_numbers == unit[1])
        )
        for unit in units
    }


def build_runtime_reference(fit: RuntimeFit) -> RuntimeReference:
    """Build the full-data fixed deployment curves from opened groups."""

    data = fit.data
    decision_times, time_bins = online._time_arrays(data)
    indices_by_unit = _indices_by_unit(data)
    units = tuple(sorted(indices_by_unit))
    if len(units) != 96:
        raise RuntimeExportError("deployment reference group count differs")
    states: dict[tuple[static_frontier.Unit, int], float] = {}
    cutpoint_map: dict[int, tuple[float, float]] = {}
    for time_bin in range(TIME_BIN_COUNT):
        bin_stop = (time_bin + 1) * TIME_BIN_WIDTH_US
        values: list[float] = []
        for unit in units:
            indices = indices_by_unit[unit]
            causal = indices[decision_times[indices] < bin_stop]
            if len(causal) == 0:
                raise RuntimeExportError(
                    "deployment group lacks a causal congestion observation"
                )
            state = float(np.mean(fit.primary_busy_20ms[causal]))
            states[(unit, time_bin)] = state
            values.append(state)
        quantiles = np.quantile(values, (1.0 / 3.0, 2.0 / 3.0))
        cutpoint_map[time_bin] = (float(quantiles[0]), float(quantiles[1]))
    p_cost_text, p_cost = _canonical_p_cost(data)
    congestion_curves: dict[tuple[int, int], RuntimeCurve] = {}
    global_curves: dict[int, RuntimeCurve] = {}

    def curve(selected_units: Sequence[static_frontier.Unit], time_bin: int) -> RuntimeCurve:
        if not selected_units:
            raise RuntimeExportError("deployment reference has an empty regime")
        indices = np.concatenate([indices_by_unit[unit] for unit in selected_units])
        eligible = decision_times[indices] >= time_bin * TIME_BIN_WIDTH_US
        eligible &= np.asarray(
            [data.frame_types[int(index)] == "P_FRAME" for index in indices]
        )
        eligible &= fit.rewards[indices] > 0.0
        candidates = indices[eligible]
        density = fit.rewards[candidates] / data.canonical_reservation_us[candidates]
        order = np.lexsort(
            (
                data.run_numbers[candidates],
                data.seeds[candidates],
                data.frame_ids[candidates],
                -density,
            )
        )
        ranked = np.asarray(density[order], dtype=np.float64)
        if len(ranked) and (
            not np.all(np.isfinite(ranked))
            or np.any(ranked <= 0.0)
            or np.any(np.diff(ranked) > 0.0)
        ):
            raise RuntimeExportError("deployment density curve differs")
        return RuntimeCurve(ranked, len(selected_units))

    cutpoints: list[tuple[float, float]] = []
    for time_bin in range(TIME_BIN_COUNT):
        points = cutpoint_map[time_bin]
        cutpoints.append(points)
        global_curves[time_bin] = curve(units, time_bin)
        for regime in range(REGIME_COUNT):
            selected = tuple(
                unit
                for unit in units
                if _regime(states[(unit, time_bin)], points) == regime
            )
            congestion_curves[(time_bin, regime)] = curve(selected, time_bin)
    return RuntimeReference(
        p_cost_text,
        p_cost,
        decision_times,
        time_bins,
        indices_by_unit,
        tuple(cutpoints),
        congestion_curves,
        global_curves,
    )


def reference_payload(reference: RuntimeReference) -> dict[str, Any]:
    """Build the plain deployment reference payload."""

    bins: list[dict[str, Any]] = []
    for time_bin in range(TIME_BIN_COUNT):
        bins.append(
            {
                "time_bin": time_bin,
                "start_us": time_bin * TIME_BIN_WIDTH_US,
                "stop_us": (time_bin + 1) * TIME_BIN_WIDTH_US,
                "congestion_cutpoints": list(reference.cutpoints[time_bin]),
                "global": {
                    "training_run_count": reference.global_curves[
                        time_bin
                    ].training_run_count,
                    "density_descending": reference.global_curves[
                        time_bin
                    ].density_descending.tolist(),
                },
                "congestion_tertile": [
                    {
                        "regime": regime,
                        "training_run_count": reference.congestion_curves[
                            (time_bin, regime)
                        ].training_run_count,
                        "density_descending": reference.congestion_curves[
                            (time_bin, regime)
                        ].density_descending.tolist(),
                    }
                    for regime in range(REGIME_COUNT)
                ],
            }
        )
    return {
        "reference_schema_version": EXPORT_SCHEMA_VERSION,
        "runtime_contract_id": RUNTIME_CONTRACT_ID,
        "runtime_contract_sha256": RUNTIME_CONTRACT_SHA256,
        "selected_variant": SELECTED_VARIANT,
        "frame_gate": "p_frames_only",
        "objective": "deadline_rescue",
        "canonical_p_frame_reservation_us": reference.p_cost_text,
        "time_bin_count": TIME_BIN_COUNT,
        "time_bin_width_us": TIME_BIN_WIDTH_US,
        "bins": bins,
    }


def replay_runtime(
    fit: RuntimeFit, reference: RuntimeReference, regime_mode: str
) -> dict[str, Any]:
    """Run a construction-only in-sample shadow-borrow replay."""

    if regime_mode not in {"global", "congestion_tertile"}:
        raise RuntimeExportError("unsupported runtime replay regime")
    data = fit.data
    total_actions = 0
    total_reservation = Decimal(0)
    captured_misses = 0
    reason_counts = {
        "frame_gate": 0,
        "nonpositive_reward": 0,
        "opportunity_price": 0,
        "horizon_credit": 0,
        "action": 0,
    }
    run_rows: list[dict[str, Any]] = []
    for unit in sorted(reference.indices_by_unit):
        indices = reference.indices_by_unit[unit]
        balance = online.INITIAL_CREDIT_US
        last_time = Decimal(0)
        spend = Decimal(0)
        actions = 0
        minimum_balance = balance
        busy_sum = 0.0
        busy_count = 0
        for index in indices:
            current_time = online.decision_time_us(int(data.frame_ids[index]))
            if current_time < last_time:
                raise RuntimeExportError("runtime replay decisions are not chronological")
            balance = min(
                online.CAPACITY_US,
                balance + online.REFILL_RATE * (current_time - last_time),
            )
            last_time = current_time
            busy_sum += float(fit.primary_busy_20ms[index])
            busy_count += 1
            if data.frame_types[index] != "P_FRAME":
                reason_counts["frame_gate"] += 1
                continue
            reward = float(fit.rewards[index])
            if reward <= 0.0:
                reason_counts["nonpositive_reward"] += 1
                continue
            time_bin = int(reference.time_bins[index])
            regime = _regime(
                busy_sum / busy_count, reference.cutpoints[time_bin]
            )
            curve = (
                reference.global_curves[time_bin]
                if regime_mode == "global"
                else reference.congestion_curves[(time_bin, regime)]
            )
            remaining_refill = online.REFILL_RATE * (
                online.MEASUREMENT_DURATION_US - current_time
            )
            repayable = balance + remaining_refill
            opportunity = curve.opportunity_cost(repayable, reference.p_cost_us)
            density = reward / float(reference.p_cost_us)
            if density < opportunity:
                reason_counts["opportunity_price"] += 1
                continue
            if reference.p_cost_us > repayable:
                reason_counts["horizon_credit"] += 1
                continue
            balance -= reference.p_cost_us
            spend += reference.p_cost_us
            actions += 1
            total_actions += 1
            reason_counts["action"] += 1
            captured_misses += int(data.primary_deadline_miss[index] == 1)
            minimum_balance = min(minimum_balance, balance)
            if balance < -remaining_refill:
                raise RuntimeExportError("runtime replay exceeds repayable credit")
        final_balance = min(
            online.CAPACITY_US,
            balance
            + online.REFILL_RATE * (online.MEASUREMENT_DURATION_US - last_time),
        )
        if final_balance < 0 or spend > online.MAXIMUM_GENERATED_CREDIT_US:
            raise RuntimeExportError("runtime replay does not repay by stop")
        total_reservation += spend
        run_rows.append(
            {
                "seed": unit[0],
                "run_number": unit[1],
                "actions": actions,
                "canonical_reservation_us": float(spend),
                "maximum_debt_us": float(max(Decimal(0), -minimum_balance)),
                "final_balance_us": float(final_balance),
            }
        )
    if sum(reason_counts.values()) != len(data.treatment):
        raise RuntimeExportError("runtime replay row coverage differs")
    return {
        "role": "in_sample_deployment_construction_sanity_not_performance_evidence",
        "regime_mode": regime_mode,
        "actions": total_actions,
        "captured_observed_primary_misses": captured_misses,
        "mean_canonical_reservation_us_per_run": float(
            total_reservation / len(run_rows)
        ),
        "maximum_canonical_reservation_us_per_run": max(
            row["canonical_reservation_us"] for row in run_rows
        ),
        "maximum_debt_us": max(row["maximum_debt_us"] for row in run_rows),
        "minimum_final_balance_us": min(row["final_balance_us"] for row in run_rows),
        "decision_counts": reason_counts,
        "runs": run_rows,
    }


def _head_summary(head: dict[str, Any]) -> dict[str, Any]:
    return {
        "training_count": head["training_count"],
        "transformed_feature_count": SELECTED_FEATURE_COUNT
        + len(head["imputer"]["missing_indicator_raw_features"]),
        "missing_indicator_count": len(
            head["imputer"]["missing_indicator_raw_features"]
        ),
        "tree_count": len(head["trees"]),
        "node_count": len(head["nodes"]),
    }


def _reference_summary(reference: RuntimeReference) -> dict[str, Any]:
    return {
        "p_cost_us": reference.p_cost_text,
        "time_bins": [
            {
                "time_bin": time_bin,
                "cutpoints": list(reference.cutpoints[time_bin]),
                "global_curve_rows": len(
                    reference.global_curves[time_bin].density_descending
                ),
                "regimes": [
                    {
                        "regime": regime,
                        "training_run_count": reference.congestion_curves[
                            (time_bin, regime)
                        ].training_run_count,
                        "curve_rows": len(
                            reference.congestion_curves[
                                (time_bin, regime)
                            ].density_descending
                        ),
                    }
                    for regime in range(REGIME_COUNT)
                ],
            }
            for time_bin in range(TIME_BIN_COUNT)
        ],
    }


def fit_to_directory(
    dataset_dir: Path | str, output_dir: Path | str
) -> tuple[dict[str, Any], RuntimeFit, dict[str, Any], RuntimeReference, dict[str, Any]]:
    """Fit and atomically publish the portable runtime artifact set."""

    contract = validate_contract_and_sources()
    destination = Path(output_dir).resolve()
    if destination.exists():
        raise RuntimeExportError(f"refusing to overwrite output directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent)
    )
    try:
        fit = fit_runtime_models(dataset_dir)
        model = portable_model_payload(fit)
        maximum_parity_error = validate_portable_parity(fit, model)
        reference = build_runtime_reference(fit)
        reference_plain = reference_payload(reference)
        replays = {
            mode: replay_runtime(fit, reference, mode)
            for mode in ("global", "congestion_tertile")
        }
        pickle_bundle = {
            "model_bundle_schema_version": EXPORT_SCHEMA_VERSION,
            "runtime_contract_id": RUNTIME_CONTRACT_ID,
            "runtime_contract_sha256": RUNTIME_CONTRACT_SHA256,
            "feature_names": fit.feature_names,
            "control_model": fit.control_model,
            "treated_model": fit.treated_model,
        }
        with (temporary / OUTPUT_MODEL_PICKLE).open("wb") as output:
            pickle.dump(pickle_bundle, output, protocol=5)
        write_json(temporary / OUTPUT_MODEL_JSON, model)
        write_json(temporary / OUTPUT_REFERENCE_JSON, reference_plain)

        source_paths = _expected_paths(contract)
        metrics = {
            "metrics_schema_version": EXPORT_SCHEMA_VERSION,
            "runtime_contract_id": RUNTIME_CONTRACT_ID,
            "runtime_contract_sha256": RUNTIME_CONTRACT_SHA256,
            "evidence_role": "deployment_refit_and_in_sample_construction_sanity",
            "selected_variant": SELECTED_VARIANT,
            "dataset": {
                "path": str(Path(dataset_dir)),
                "row_count": len(fit.data.treatment),
                "run_group_count": len(_indices_by_unit(fit.data)),
                "control_rows": int(np.sum(fit.data.treatment == 0)),
                "treated_rows": int(np.sum(fit.data.treatment == 1)),
            },
            "model": {
                "feature_count": len(fit.feature_names),
                "control": _head_summary(model["heads"]["control"]),
                "full_copy_t2": _head_summary(model["heads"]["full_copy_t2"]),
                "maximum_portable_sklearn_absolute_error": maximum_parity_error,
                "mean_deadline_rescue_reward": float(np.mean(fit.rewards)),
                "positive_deadline_rescue_rows": int(np.sum(fit.rewards > 0)),
            },
            "reference": _reference_summary(reference),
            "in_sample_replays": replays,
            "interpretation_limits": contract["interpretation_limits"],
            "provenance": {
                "project_git_commit": _git_value("rev-parse", "HEAD"),
                "project_git_status_porcelain": _git_value("status", "--porcelain"),
                "tool": str(Path(__file__).resolve().relative_to(repository_root())),
                "tool_sha256": sha256_file(Path(__file__).resolve()),
                "python": platform.python_version(),
                "numpy": np.__version__,
                "scikit_learn": importlib.metadata.version("scikit-learn"),
            },
            "source_artifacts_sha256": {
                str(path.relative_to(repository_root())): digest
                for path, digest in source_paths.items()
            },
            "component_sha256": {
                "portable_model": canonical_sha256(model),
                "deployment_reference": canonical_sha256(reference_plain),
                "feature_contract": canonical_sha256(
                    {
                        "feature_family": SELECTED_FAMILY,
                        "feature_names": list(fit.feature_names),
                        "adapter": contract["full_data_refit"]["numeric_adapter"],
                    }
                ),
            },
        }
        write_json(temporary / OUTPUT_METRICS, metrics)
        manifest = {
            "manifest_schema_version": EXPORT_SCHEMA_VERSION,
            "hash_algorithm": "sha256",
            "runtime_contract_id": RUNTIME_CONTRACT_ID,
            "runtime_contract_sha256": RUNTIME_CONTRACT_SHA256,
            "artifacts_sha256": {
                name: sha256_file(temporary / name)
                for name in (
                    OUTPUT_MODEL_PICKLE,
                    OUTPUT_MODEL_JSON,
                    OUTPUT_REFERENCE_JSON,
                    OUTPUT_METRICS,
                )
            },
            "source_artifacts_sha256": metrics["source_artifacts_sha256"],
        }
        write_json(temporary / OUTPUT_MANIFEST, manifest)
        os.replace(temporary, destination)
        return metrics, fit, model, reference, reference_plain
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def load_runtime_artifacts(
    artifact_dir: Path | str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load a checksum-closed canonical runtime without unpickling it."""

    validate_contract_and_sources()
    directory = Path(artifact_dir).resolve()
    try:
        names = {path.name for path in directory.iterdir()}
    except OSError as error:
        raise RuntimeExportError(f"cannot list runtime artifact: {error}") from error
    if names != set(OUTPUT_FILES) or any(
        not (directory / name).is_file() or (directory / name).is_symlink()
        for name in names
    ):
        raise RuntimeExportError("runtime artifact directory closure differs")
    manifest = read_json(directory / OUTPUT_MANIFEST)
    artifacts = manifest.get("artifacts_sha256")
    expected_artifacts = {
        OUTPUT_MODEL_PICKLE,
        OUTPUT_MODEL_JSON,
        OUTPUT_REFERENCE_JSON,
        OUTPUT_METRICS,
    }
    if (
        manifest.get("manifest_schema_version") != EXPORT_SCHEMA_VERSION
        or manifest.get("runtime_contract_id") != RUNTIME_CONTRACT_ID
        or manifest.get("runtime_contract_sha256") != RUNTIME_CONTRACT_SHA256
        or not isinstance(artifacts, dict)
        or set(artifacts) != expected_artifacts
    ):
        raise RuntimeExportError("runtime artifact manifest differs")
    for name, digest in artifacts.items():
        if sha256_file(directory / name) != digest:
            raise RuntimeExportError(f"runtime artifact hash differs: {name}")
    model = read_json(directory / OUTPUT_MODEL_JSON)
    reference = read_json(directory / OUTPUT_REFERENCE_JSON)
    metrics = read_json(directory / OUTPUT_METRICS)
    if (
        model.get("runtime_contract_id") != RUNTIME_CONTRACT_ID
        or model.get("runtime_contract_sha256") != RUNTIME_CONTRACT_SHA256
        or model.get("selected_variant") != SELECTED_VARIANT
        or len(model.get("feature_names", ())) != SELECTED_FEATURE_COUNT
        or reference.get("runtime_contract_id") != RUNTIME_CONTRACT_ID
        or reference.get("runtime_contract_sha256") != RUNTIME_CONTRACT_SHA256
        or reference.get("selected_variant") != SELECTED_VARIANT
        or reference.get("time_bin_count") != TIME_BIN_COUNT
        or metrics.get("runtime_contract_id") != RUNTIME_CONTRACT_ID
        or metrics.get("runtime_contract_sha256") != RUNTIME_CONTRACT_SHA256
        or metrics.get("provenance", {}).get("tool_sha256")
        != sha256_file(Path(__file__).resolve())
        or metrics.get("component_sha256", {}).get("portable_model")
        != canonical_sha256(model)
        or metrics.get("component_sha256", {}).get("deployment_reference")
        != canonical_sha256(reference)
    ):
        raise RuntimeExportError("runtime artifact semantic closure differs")
    return model, reference, metrics, manifest


def _provenance_values(
    artifact_dir: Path,
    metrics: dict[str, Any],
    manifest: dict[str, Any],
) -> list[str]:
    artifacts = manifest["artifacts_sha256"]
    components = metrics["component_sha256"]
    return [
        metrics["evidence_role"],
        RUNTIME_CONTRACT_ID,
        RUNTIME_CONTRACT_SHA256,
        SELECTED_VARIANT,
        metrics["provenance"]["project_git_commit"],
        artifacts[OUTPUT_MODEL_PICKLE],
        artifacts[OUTPUT_MODEL_JSON],
        artifacts[OUTPUT_REFERENCE_JSON],
        artifacts[OUTPUT_METRICS],
        sha256_file(artifact_dir / OUTPUT_MANIFEST),
        metrics["provenance"]["tool_sha256"],
        components["portable_model"],
        components["deployment_reference"],
        components["feature_contract"],
    ]


def emit_data_header() -> str:
    """Render the generated internal model-data interface."""

    return "\n".join(
        [
            "/*",
            " * SPDX-License-Identifier: GPL-2.0-only",
            " *",
            " * Generated by tools/export_temporal_t2_shadow_runtime_v1.py.",
            " */",
            "",
            "#ifndef TEMPORAL_T2_DISTRIBUTION_MODEL_DATA_V1_H",
            "#define TEMPORAL_T2_DISTRIBUTION_MODEL_DATA_V1_H",
            "",
            '#include "temporal-t2-distribution-model-evaluator.h"',
            "",
            "#include <array>",
            "#include <cstdint>",
            "#include <span>",
            "#include <string_view>",
            "",
            "namespace ns3",
            "{",
            "namespace temporal_t2_distribution_model_v1",
            "{",
            "",
            "/** One fitted median imputer and appended missing indicators. */",
            "struct Imputer",
            "{",
            "    std::span<const double> medians; ///< Per-feature training medians.",
            "    std::span<const uint16_t> missingIndicatorRawFeatures; ///< Indicator sources.",
            "};",
            "",
            "/** One node in a fitted histogram-gradient-boosting tree. */",
            "struct Node",
            "{",
            "    double value; ///< Leaf raw-score contribution.",
            "    double threshold; ///< Numeric split threshold.",
            "    uint16_t feature; ///< Imputed feature index.",
            "    uint16_t left; ///< Left child relative to the tree root.",
            "    uint16_t right; ///< Right child relative to the tree root.",
            "    bool missingLeft; ///< Whether a missing value follows the left child.",
            "    bool leaf; ///< Whether this node is a leaf.",
            "};",
            "",
            "/** Location and class of one tree in a concatenated node array. */",
            "struct Tree",
            "{",
            "    uint32_t offset; ///< First node in the classifier node array.",
            "    uint16_t count; ///< Number of nodes in the tree.",
            "    uint8_t classIndex; ///< Output class updated by the tree.",
            "};",
            "",
            "/** One fitted six-class HGB completion-distribution head. */",
            "struct MulticlassClassifier",
            "{",
            "    Imputer imputer; ///< Fitted arm-specific imputer.",
            "    std::span<const Node> nodes; ///< Concatenated tree nodes.",
            "    std::span<const Tree> trees; ///< Tree locations and output classes.",
            "    std::array<double, 6> baseline; ///< Initial raw class scores.",
            "    uint32_t trainingCount; ///< Rows used to fit this arm.",
            "    double dirichletAlpha; ///< Per-class smoothing pseudocount.",
            "};",
            "",
            "/** Reachable prefix of one exact marginal reward-density curve. */",
            "struct ShadowCurve",
            "{",
            "    std::span<const double> densityDescending; ///< Sorted positive densities.",
            "    uint16_t trainingRunCount; ///< Contributing opened run groups.",
            "    bool complete; ///< Whether the stored prefix is the complete curve.",
            "};",
            "",
            "/** Congestion state and three deployable curves for one time bin. */",
            "struct ReferenceBin",
            "{",
            "    std::array<double, 2> congestionCutpoints; ///< Frozen tertile boundaries.",
            "    std::array<ShadowCurve, 3> congestionCurves; ///< Regime-specific curves.",
            "};",
            "",
            "/** Generated runtime metadata. */",
            "struct Metadata",
            "{",
            "    TemporalT2DistributionModelProvenance provenance; ///< Source closure.",
            "    std::string_view featureFamily; ///< Selected 308-feature family.",
            "    std::string_view featureAdapter; ///< Numeric input adapter.",
            "    std::string_view modelSpecId; ///< Selected HGB specification.",
            "    std::string_view objective; ///< Primary allocation benefit.",
            "    std::string_view frameGate; ///< Caller-owned frame gate.",
            "    double canonicalPFrameReservationUs; ///< Frozen action reservation.",
            "    double maximumRepayableCreditUs; ///< Maximum reachable resource state.",
            "    uint32_t timeBinWidthUs; ///< Fixed shadow-reference time bin.",
            "};",
            "",
            "/** @return Ordered raw feature names. */",
            "std::span<const std::string_view> GetFeatureNames();",
            "",
            "/** @return Fitted no-action completion head. */",
            "const MulticlassClassifier& GetControlClassifier();",
            "",
            "/** @return Fitted full-copy completion head. */",
            "const MulticlassClassifier& GetFullCopyClassifier();",
            "",
            "/** @return Frozen deployable congestion reference bins. */",
            "std::span<const ReferenceBin> GetReferenceBins();",
            "",
            "/** @return Generated runtime metadata. */",
            "const Metadata& GetMetadata();",
            "",
            "} // namespace temporal_t2_distribution_model_v1",
            "} // namespace ns3",
            "",
            "#endif // TEMPORAL_T2_DISTRIBUTION_MODEL_DATA_V1_H",
            "",
        ]
    )


def _emit_double_array(lines: list[str], name: str, values: Sequence[float]) -> None:
    lines.append(f"constexpr std::array<double, {len(values)}> {name}{{{{")
    lines.extend(f"    {legacy_export.cpp_float(float(value))}," for value in values)
    lines.extend(["}};", ""])


def _emit_uint16_array(lines: list[str], name: str, values: Sequence[int]) -> None:
    lines.append(f"constexpr std::array<uint16_t, {len(values)}> {name}{{{{")
    lines.extend(f"    {int(value)}," for value in values)
    lines.extend(["}};", ""])


def _emit_classifier(lines: list[str], prefix: str, head: dict[str, Any]) -> None:
    imputer = head["imputer"]
    _emit_double_array(lines, f"g_{prefix}Medians", imputer["medians"])
    _emit_uint16_array(
        lines,
        f"g_{prefix}MissingIndicators",
        imputer["missing_indicator_raw_features"],
    )
    lines.extend(
        [
            f"const Imputer g_{prefix}Imputer{{",
            f"    g_{prefix}Medians,",
            f"    g_{prefix}MissingIndicators,",
            "};",
            "",
        ]
    )
    nodes = head["nodes"]
    lines.append(f"constexpr std::array<Node, {len(nodes)}> g_{prefix}Nodes{{{{")
    lines.extend(
        "    {%s, %s, %d, %d, %d, %s, %s},"
        % (
            legacy_export.cpp_float(float(value)),
            legacy_export.cpp_float(float(threshold)),
            feature,
            left,
            right,
            str(bool(missing_left)).lower(),
            str(bool(leaf)).lower(),
        )
        for value, threshold, feature, left, right, missing_left, leaf in nodes
    )
    lines.extend(["}};", ""])
    trees = head["trees"]
    lines.append(f"constexpr std::array<Tree, {len(trees)}> g_{prefix}Trees{{{{")
    lines.extend(
        f"    {{{offset}, {count}, {class_index}}},"
        for offset, count, class_index, _iteration in trees
    )
    lines.extend(["}};", ""])
    baseline = ", ".join(
        legacy_export.cpp_float(float(value)) for value in head["baseline"]
    )
    lines.extend(
        [
            f"const MulticlassClassifier g_{prefix}Classifier{{",
            f"    g_{prefix}Imputer,",
            f"    g_{prefix}Nodes,",
            f"    g_{prefix}Trees,",
            f"    {{{{{baseline}}}}},",
            f"    {head['training_count']},",
            f"    {legacy_export.cpp_float(head['dirichlet_alpha_per_class'])},",
            "};",
            "",
        ]
    )


def _reachable_curve_prefix(
    curve: dict[str, Any], p_cost: Decimal
) -> tuple[list[float], bool]:
    values = curve["density_descending"]
    run_count = int(curve["training_run_count"])
    maximum_affordable = int(
        (MAXIMUM_REPAYABLE_CREDIT_US * run_count) // p_cost
    )
    limit = min(len(values), maximum_affordable + 1)
    return [float(value) for value in values[:limit]], limit == len(values)


def emit_data_source(
    artifact_dir: Path,
    model: dict[str, Any],
    reference: dict[str, Any],
    metrics: dict[str, Any],
    manifest: dict[str, Any],
) -> str:
    """Render deterministic model and reachable shadow-reference data."""

    provenance = _provenance_values(artifact_dir, metrics, manifest)
    p_cost = Decimal(reference["canonical_p_frame_reservation_us"])
    lines = [
        "/*",
        " * SPDX-License-Identifier: GPL-2.0-only",
        " *",
        " * Generated by tools/export_temporal_t2_shadow_runtime_v1.py.",
        f" * Runtime contract SHA-256: {RUNTIME_CONTRACT_SHA256}",
        f" * Portable model SHA-256: {metrics['component_sha256']['portable_model']}",
        f" * Deployment reference SHA-256: {metrics['component_sha256']['deployment_reference']}",
        " */",
        "",
        '#include "temporal-t2-distribution-model-data-v1.h"',
        "",
        "#include <array>",
        "",
        "namespace ns3",
        "{",
        "namespace temporal_t2_distribution_model_v1",
        "{",
        "namespace",
        "{",
        f"constexpr std::array<std::string_view, {len(model['feature_names'])}> g_featureNames{{",
    ]
    lines.extend(
        f"    {legacy_export.cpp_string(name)}," for name in model["feature_names"]
    )
    lines.extend(["}};", ""])
    _emit_classifier(lines, "control", model["heads"]["control"])
    _emit_classifier(lines, "fullCopy", model["heads"]["full_copy_t2"])

    curve_metadata: list[list[tuple[str, int, bool]]] = []
    for bin_row in reference["bins"]:
        time_bin = int(bin_row["time_bin"])
        bin_curves: list[tuple[str, int, bool]] = []
        for regime_row in bin_row["congestion_tertile"]:
            regime = int(regime_row["regime"])
            values, complete = _reachable_curve_prefix(regime_row, p_cost)
            name = f"g_bin{time_bin}Regime{regime}Density"
            _emit_double_array(lines, name, values)
            bin_curves.append(
                (name, int(regime_row["training_run_count"]), complete)
            )
        curve_metadata.append(bin_curves)

    lines.append(f"constexpr std::array<ReferenceBin, {TIME_BIN_COUNT}> g_referenceBins{{{{")
    for bin_row, bin_curves in zip(reference["bins"], curve_metadata, strict=True):
        low, high = bin_row["congestion_cutpoints"]
        lines.extend(
            [
                "    {",
                "        {{%s, %s}},"
                % (
                    legacy_export.cpp_float(float(low)),
                    legacy_export.cpp_float(float(high)),
                ),
                "        {{",
            ]
        )
        for name, run_count, complete in bin_curves:
            lines.append(
                f"            {{{name}, {run_count}, {str(complete).lower()}}},"
            )
        lines.extend(["        }},", "    },"])
    lines.extend(["}};", ""])

    contract = read_json(repository_root() / RUNTIME_CONTRACT)
    adapter = contract["full_data_refit"]["numeric_adapter"]
    lines.extend(
        [
            "constexpr Metadata g_metadata{",
            "    {",
            *(f"        {legacy_export.cpp_string(value)}," for value in provenance),
            "    },",
            f"    {legacy_export.cpp_string(SELECTED_FAMILY)},",
            f"    {legacy_export.cpp_string(adapter)},",
            f"    {legacy_export.cpp_string(MODEL_SPEC['id'])},",
            '    "deadline_rescue",',
            '    "p_frames_only",',
            f"    {legacy_export.cpp_float(float(p_cost))},",
            f"    {legacy_export.cpp_float(float(MAXIMUM_REPAYABLE_CREDIT_US))},",
            f"    {TIME_BIN_WIDTH_US},",
            "};",
            "",
            "} // namespace",
            "",
            "std::span<const std::string_view>",
            "GetFeatureNames()",
            "{",
            "    return g_featureNames;",
            "}",
            "",
            "const MulticlassClassifier&",
            "GetControlClassifier()",
            "{",
            "    return g_controlClassifier;",
            "}",
            "",
            "const MulticlassClassifier&",
            "GetFullCopyClassifier()",
            "{",
            "    return g_fullCopyClassifier;",
            "}",
            "",
            "std::span<const ReferenceBin>",
            "GetReferenceBins()",
            "{",
            "    return g_referenceBins;",
            "}",
            "",
            "const Metadata&",
            "GetMetadata()",
            "{",
            "    return g_metadata;",
            "}",
            "",
            "} // namespace temporal_t2_distribution_model_v1",
            "} // namespace ns3",
            "",
        ]
    )
    return "\n".join(lines)


def _transformed_features(head: dict[str, Any], raw: np.ndarray) -> np.ndarray:
    values = _quantize_features(raw)
    imputer = head["imputer"]
    medians = np.asarray(imputer["medians"], dtype=np.float64)
    indicators = np.asarray(
        imputer["missing_indicator_raw_features"], dtype=np.int64
    )
    missing = np.isnan(values)
    return np.concatenate(
        (np.where(missing, medians, values), missing[indicators].astype(float))
    )


def golden_model_inputs(data: crossfit.DistributionDataset) -> list[tuple[str, np.ndarray]]:
    """Return deterministic synthetic and real 308-feature parity cases."""

    matrix = data.family_matrices[SELECTED_FAMILY]
    zeros = np.zeros(SELECTED_FEATURE_COUNT, dtype=np.float64)
    zeros[67] = 1.0
    missing = np.full(SELECTED_FEATURE_COUNT, np.nan, dtype=np.float64)
    deterministic = np.asarray(
        [
            (((index * 7919) % 1009) - 504) * (1 + index % 11) / 13
            for index in range(SELECTED_FEATURE_COUNT)
        ],
        dtype=np.float64,
    )
    deterministic[66], deterministic[67] = 1.0, 0.0
    missing_probe = np.zeros(SELECTED_FEATURE_COUNT, dtype=np.float64)
    missing_probe[[7, 83, 301]] = np.nan
    missing_probe[67] = 1.0
    cases = [
        ("zeros_p", zeros),
        ("all_missing", missing),
        ("deterministic_i", deterministic),
        ("missing_probe_p", missing_probe),
    ]
    for label, index in (
        ("opened_first", 0),
        ("opened_middle", len(matrix) // 2),
        ("opened_last", len(matrix) - 1),
    ):
        cases.append((label, np.asarray(matrix[index], dtype=np.float64)))
    return cases


def _cpp_number(value: float) -> str:
    if math.isnan(value):
        return "std::numeric_limits<double>::quiet_NaN()"
    if value == math.inf:
        return "std::numeric_limits<double>::infinity()"
    if value == -math.inf:
        return "-std::numeric_limits<double>::infinity()"
    return legacy_export.cpp_float(float(value))


def _emit_values(lines: list[str], values: Iterable[float], indent: str) -> None:
    lines.extend(f"{indent}{_cpp_number(float(value))}," for value in values)


def _reference_curve_from_row(row: dict[str, Any]) -> RuntimeCurve:
    return RuntimeCurve(
        np.asarray(row["density_descending"], dtype=np.float64),
        int(row["training_run_count"]),
    )


def _reference_golden_cases(reference: dict[str, Any]) -> list[dict[str, Any]]:
    p_cost = Decimal(reference["canonical_p_frame_reservation_us"])
    cases: list[dict[str, Any]] = []
    specifications = (
        (0, 0, Decimal("0"), "bin0_low_no_credit"),
        (0, 1, Decimal("12000"), "bin0_mid_start_credit"),
        (0, 2, MAXIMUM_REPAYABLE_CREDIT_US, "bin0_high_full_credit"),
        (5, 0, Decimal("120000"), "bin5_low"),
        (5, 1, Decimal("240000"), "bin5_mid"),
        (5, 2, Decimal("360000"), "bin5_high"),
        (11, 0, Decimal("12000"), "bin11_low"),
        (11, 1, Decimal("180000"), "bin11_mid"),
        (11, 2, MAXIMUM_REPAYABLE_CREDIT_US, "bin11_high_full_credit"),
    )
    for time_bin, regime, credit, label in specifications:
        row = reference["bins"][time_bin]
        low, high = (float(value) for value in row["congestion_cutpoints"])
        if regime == 0:
            signal = float(np.nextafter(low, -math.inf))
        elif regime == 1:
            signal = low
        else:
            signal = high
        curve_row = row["congestion_tertile"][regime]
        curve = _reference_curve_from_row(curve_row)
        cases.append(
            {
                "label": label,
                "time_bin": time_bin,
                "running_busy": signal,
                "repayable_credit_us": float(credit),
                "expected_regime": regime,
                "expected_opportunity_cost": curve.opportunity_cost(credit, p_cost),
            }
        )
    return cases


def _credit_golden_cases(p_cost: Decimal) -> list[dict[str, Any]]:
    specifications = (
        (Decimal("12000"), Decimal("0"), Decimal("2000"), p_cost, "initial_action"),
        (Decimal("-50000"), Decimal("10000000"), Decimal("15000000"), p_cost, "debt_action"),
        (Decimal("350000"), Decimal("10000000"), Decimal("15000000"), p_cost, "positive_cap"),
        (Decimal("-5000"), Decimal("59000000"), Decimal("59900000"), p_cost, "horizon_reject"),
    )
    result: list[dict[str, Any]] = []
    for balance, prior, decision, cost, label in specifications:
        refilled = min(
            online.CAPACITY_US,
            balance + online.REFILL_RATE * (decision - prior),
        )
        remaining = online.REFILL_RATE * (
            online.MEASUREMENT_DURATION_US - decision
        )
        repayable = refilled + remaining
        admitted = cost <= repayable
        after = refilled - cost if admitted else refilled
        final = min(online.CAPACITY_US, after + remaining)
        result.append(
            {
                "label": label,
                "prior_balance_us": float(balance),
                "prior_time_us": float(prior),
                "decision_time_us": float(decision),
                "reservation_us": float(cost),
                "expected_refilled_balance_us": float(refilled),
                "expected_remaining_refill_us": float(remaining),
                "expected_repayable_credit_us": float(repayable),
                "expected_admitted": admitted,
                "expected_balance_after_us": float(after),
                "expected_final_balance_us": float(final),
            }
        )
    return result


def emit_golden_header(
    artifact_dir: Path,
    model: dict[str, Any],
    reference: dict[str, Any],
    metrics: dict[str, Any],
    manifest: dict[str, Any],
    data: crossfit.DistributionDataset,
) -> str:
    """Render exact Python/C++ model, reference, and credit goldens."""

    provenance = _provenance_values(artifact_dir, metrics, manifest)
    model_cases = golden_model_inputs(data)
    reference_cases = _reference_golden_cases(reference)
    credit_cases = _credit_golden_cases(
        Decimal(reference["canonical_p_frame_reservation_us"])
    )
    transformed_count = SELECTED_FEATURE_COUNT + len(
        model["heads"]["control"]["imputer"]["missing_indicator_raw_features"]
    )
    lines = [
        "/*",
        " * SPDX-License-Identifier: GPL-2.0-only",
        " *",
        " * Generated by tools/export_temporal_t2_shadow_runtime_v1.py.",
        " */",
        "",
        "#ifndef TEMPORAL_T2_DISTRIBUTION_MODEL_GOLDENS_V1_H",
        "#define TEMPORAL_T2_DISTRIBUTION_MODEL_GOLDENS_V1_H",
        "",
        '#include "ns3/temporal-t2-distribution-model-evaluator.h"',
        "",
        "#include <array>",
        "#include <limits>",
        "#include <string_view>",
        "",
        "namespace ns3",
        "{",
        "namespace temporal_t2_distribution_model_goldens_v1",
        "{",
        "",
        "inline constexpr TemporalT2DistributionModelProvenance g_provenance{",
        *(f"    {legacy_export.cpp_string(value)}," for value in provenance),
        "};",
        "",
        "/** @ingroup tests One exact model-evaluator parity case. */",
        "struct ModelGoldenCase",
        "{",
        "    std::string_view label; ///< Stable case label.",
        f"    std::array<double, {SELECTED_FEATURE_COUNT}> features; ///< Raw features.",
        f"    std::array<double, {transformed_count}> controlTransformed; ///< CONTROL preprocessing.",
        f"    std::array<double, {transformed_count}> fullCopyTransformed; ///< FULL_COPY_T2 preprocessing.",
        "    TemporalT2DistributionModelResult expected; ///< Exact model result.",
        "};",
        "",
        f"inline const std::array<ModelGoldenCase, {len(model_cases)}> g_modelCases{{{{",
    ]
    for label, raw in model_cases:
        result = portable_result(model, raw)
        control = result["control"]
        treated = result["full_copy_t2"]
        lines.extend([f"    {{{legacy_export.cpp_string(label)},", "     {{"])
        _emit_values(lines, np.asarray(raw, dtype=np.float64), "         ")
        lines.extend(["     }},", "     {{"])
        _emit_values(
            lines, _transformed_features(model["heads"]["control"], raw), "         "
        )
        lines.extend(["     }},", "     {{"])
        _emit_values(
            lines,
            _transformed_features(model["heads"]["full_copy_t2"], raw),
            "         ",
        )
        lines.extend(["     }},", "     {"])
        for values in (
            control["logits"],
            control["smoothed_probabilities"],
            control["cdf"],
            treated["logits"],
            treated["smoothed_probabilities"],
            treated["cdf"],
        ):
            lines.append("         {{")
            _emit_values(lines, values, "             ")
            lines.append("         }},")
        lines.extend(
            [
                f"         {_cpp_number(result['deadline_rescue_reward'])},",
                f"         {_cpp_number(result['tail18_cdf_gain'])},",
                "     },",
                "    },",
            ]
        )
    lines.extend(
        [
            "}};",
            "",
            "/** @ingroup tests One exact shadow-reference query. */",
            "struct ReferenceGoldenCase",
            "{",
            "    std::string_view label; ///< Stable case label.",
            "    uint8_t timeBin; ///< Frozen five-second time bin.",
            "    double runningBusy; ///< Causal running busy mean.",
            "    double repayableCreditUs; ///< Resource state before action.",
            "    uint8_t expectedRegime; ///< Expected congestion tertile.",
            "    double expectedOpportunityCost; ///< Expected marginal density.",
            "};",
            "",
            f"inline const std::array<ReferenceGoldenCase, {len(reference_cases)}> g_referenceCases{{{{",
        ]
    )
    for row in reference_cases:
        lines.append(
            "    {%s, %d, %s, %s, %d, %s},"
            % (
                legacy_export.cpp_string(row["label"]),
                row["time_bin"],
                _cpp_number(row["running_busy"]),
                _cpp_number(row["repayable_credit_us"]),
                row["expected_regime"],
                _cpp_number(row["expected_opportunity_cost"]),
            )
        )
    lines.extend(
        [
            "}};",
            "",
            "/** @ingroup tests One exact borrow/repay accounting transition. */",
            "struct CreditGoldenCase",
            "{",
            "    std::string_view label; ///< Stable case label.",
            "    double priorBalanceUs; ///< Balance before causal refill.",
            "    double priorTimeUs; ///< Previous accounting time.",
            "    double decisionTimeUs; ///< Current decision time.",
            "    double reservationUs; ///< Prospective canonical debit.",
            "    double expectedRefilledBalanceUs; ///< Balance after causal refill.",
            "    double expectedRemainingRefillUs; ///< Deterministic future refill.",
            "    double expectedRepayableCreditUs; ///< Horizon-feasible resource.",
            "    bool expectedAdmitted; ///< Whether the debit is repayable.",
            "    double expectedBalanceAfterUs; ///< Balance after accepted debit.",
            "    double expectedFinalBalanceUs; ///< Balance after stop-time refill.",
            "};",
            "",
            f"inline const std::array<CreditGoldenCase, {len(credit_cases)}> g_creditCases{{{{",
        ]
    )
    for row in credit_cases:
        lines.append(
            "    {%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s},"
            % (
                legacy_export.cpp_string(row["label"]),
                _cpp_number(row["prior_balance_us"]),
                _cpp_number(row["prior_time_us"]),
                _cpp_number(row["decision_time_us"]),
                _cpp_number(row["reservation_us"]),
                _cpp_number(row["expected_refilled_balance_us"]),
                _cpp_number(row["expected_remaining_refill_us"]),
                _cpp_number(row["expected_repayable_credit_us"]),
                str(row["expected_admitted"]).lower(),
                _cpp_number(row["expected_balance_after_us"]),
                _cpp_number(row["expected_final_balance_us"]),
            )
        )
    lines.extend(
        [
            "}};",
            "",
            "} // namespace temporal_t2_distribution_model_goldens_v1",
            "} // namespace ns3",
            "",
            "#endif // TEMPORAL_T2_DISTRIBUTION_MODEL_GOLDENS_V1_H",
            "",
        ]
    )
    return "\n".join(lines)


def write_or_check(path: Path, content: str, check: bool) -> None:
    """Write generated text or fail if a checked copy is stale."""

    if check:
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            raise RuntimeExportError(f"generated file is stale: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    root = repository_root()
    campaign = root / "results/randomized_full_copy_exploration_collection_v1"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-dir", type=Path, default=campaign / "temporal_dataset"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=campaign / "temporal_t2_shadow_borrow_runtime_v1",
    )
    parser.add_argument(
        "--data-header", type=Path, default=root / DEFAULT_DATA_HEADER
    )
    parser.add_argument(
        "--data-source", type=Path, default=root / DEFAULT_DATA_SOURCE
    )
    parser.add_argument(
        "--golden-output", type=Path, default=root / DEFAULT_GOLDEN_HEADER
    )
    parser.add_argument(
        "--artifact-only",
        action="store_true",
        help="fit/verify the portable artifact without generating C++",
    )
    parser.add_argument(
        "--check", action="store_true", help="check tracked generated files"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.check and not args.output_dir.exists():
            raise RuntimeExportError("cannot check without the canonical runtime artifact")
        if args.output_dir.exists():
            model, reference, metrics, manifest = load_runtime_artifacts(
                args.output_dir
            )
        else:
            metrics, _fit, _model, _reference, _plain = fit_to_directory(
                args.dataset_dir, args.output_dir
            )
            model, reference, metrics, manifest = load_runtime_artifacts(
                args.output_dir
            )
        if not args.artifact_only:
            data = crossfit.load_distribution_dataset(args.dataset_dir)
            artifact = args.output_dir.resolve()
            write_or_check(args.data_header, emit_data_header(), args.check)
            write_or_check(
                args.data_source,
                emit_data_source(artifact, model, reference, metrics, manifest),
                args.check,
            )
            write_or_check(
                args.golden_output,
                emit_golden_header(
                    artifact, model, reference, metrics, manifest, data
                ),
                args.check,
            )
    except RuntimeExportError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "runtime_contract_id": metrics["runtime_contract_id"],
                "selected_variant": metrics["selected_variant"],
                "feature_count": metrics["model"]["feature_count"],
                "replays": {
                    name: {
                        "actions": row["actions"],
                        "captured_observed_primary_misses": row[
                            "captured_observed_primary_misses"
                        ],
                        "mean_canonical_reservation_us_per_run": row[
                            "mean_canonical_reservation_us_per_run"
                        ],
                    }
                    for name, row in metrics["in_sample_replays"].items()
                },
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
