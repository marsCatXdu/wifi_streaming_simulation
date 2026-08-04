#!/usr/bin/env python3
"""Export the frozen primary-only temporal T2 value policy to C++."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import pickle
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import train_temporal_t2_value as trainer


ROOT = TOOLS.parent
CANONICAL_CAMPAIGN = ROOT / "results/randomized_full_copy_exploration_collection_v1"
CANONICAL_ARTIFACT_DIR = (
    CANONICAL_CAMPAIGN / "temporal_t2_primary_only_two_objective_v1"
).resolve()
CANONICAL_DATASET_DIR = (CANONICAL_CAMPAIGN / "temporal_dataset").resolve()
CANONICAL_SELECTION_PATH = (
    ROOT
    / "experiments/model-selection/temporal-t2-primary-only-two-objective-v1.json"
).resolve()


EXPORT_SCHEMA_VERSION = 1
EXPECTED_FEATURE_COUNT = 246
EXPECTED_TREE_COUNT = 64
EXPECTED_FEATURE_FAMILY = "primary_compact_physics_temporal"
EXPECTED_RANKER = "legacy_bad12_value_per_cost"
EXPECTED_FRAME_GATE = "p_frames_only"
EXPECTED_MODEL_SPEC = "hgb64_depth3_7leaf_two_head_ridge_log_cost_v1"
EXPECTED_SELECTION_ID = "calibration_two_objective_50pct_maximin_v1"
EXPECTED_FEATURE_ADAPTER = "finite_numeric_float32_then_float64_one_hot_v1"
EXPECTED_SCORE_ADAPTER = "final_candidate_float32_threshold_ge_v1"
EXPECTED_PYTHON = "3.12.13"
EXPECTED_NUMPY = "1.26.4"
EXPECTED_SKLEARN = "1.4.1.post1"
EXPECTED_TRAINING_GIT_COMMIT = "9b9ee02edc0b289b0ba4187c3f5567087c1d977f"
EXPECTED_SELECTION_SHA256 = (
    "c7f886a4ca1a29b9fbd2e25d19d78f994d7136ecdea4f6a16db77eacacf5ce9f"
)
EXPECTED_SOURCE_MANIFEST_SHA256 = (
    "b3af02b647c7671a631f3d43ebece75781989889358c845335d4003610a8208f"
)
EXPECTED_SOURCE_ARTIFACTS = {
    "temporal_t2_value_models.pkl": (
        "dff01b0f8319320489709c4039d97011f35439aa92adedbe167fe61b9de7bcb8"
    ),
    "temporal_t2_value_policy_candidates.csv": (
        "7cbd5c622838df0a2f752c3bf9f4c54f333f7d280a9240cb80eda19efb1c28bb"
    ),
    "temporal_t2_value_training_metrics.json": (
        "35929f0638b03ec79f2f3967dd947265c3d73b7fa51f487299cc1d96a555a014"
    ),
}
EXPECTED_DATASET_ARTIFACTS = {
    "artifact_manifest.json": (
        "87d630a66f460b46a31245f56da2a8110091c42dc5fd499416e2d82d697d0314"
    ),
    "dataset_metadata.json": (
        "03a3fc35dac1afa126653703855f243a70cb40d93f713002e7ae0b9d7cea20e8"
    ),
    "randomized_t2_temporal.csv": (
        "9376face6806929318c92e74fc2c47da740e187c7b0570910a37acdd1f3be0bc"
    ),
}
PRIMARY_HEAD_KEY = "bad_tail_12000us:primary_need"
TREATED_HEAD_KEY = "bad_tail_12000us:treated_bad"
COST_KEY = "log_cost_given_launch"
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class TemporalT2ExportError(ValueError):
    """Raised when the frozen model cannot be exported unambiguously."""


@dataclass(frozen=True)
class ExportSource:
    """Validated source objects and their immutable file identities."""

    artifact_dir: Path
    dataset_dir: Path
    selection_path: Path
    selection: dict[str, Any]
    artifact_manifest: dict[str, Any]
    metrics: dict[str, Any]
    bundle: dict[str, Any]
    feature_names: tuple[str, ...]
    primary_head: Pipeline
    treated_head: Pipeline
    cost_model: Pipeline
    smearing_factor: float
    file_hashes: dict[str, str]


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of one file."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    """Encode a JSON value canonically, including file framing."""

    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode()


def canonical_sha256(value: Any) -> str:
    """Return the canonical JSON digest of a language-neutral value."""

    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    """Read a JSON object from disk."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TemporalT2ExportError(f"cannot read JSON object {path}: {error}") from error
    if not isinstance(value, dict):
        raise TemporalT2ExportError(f"{path}: JSON root is not an object")
    return value


def _sha(value: Any, context: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise TemporalT2ExportError(f"{context}: invalid SHA-256")
    return value


def _finite(value: Any, context: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise TemporalT2ExportError(f"{context}: non-numeric value") from error
    if not math.isfinite(number):
        raise TemporalT2ExportError(f"{context}: non-finite value")
    return number


def _exact_keys(value: Any, keys: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise TemporalT2ExportError(f"{context}: object schema differs")
    return value


def _validate_selection(selection: dict[str, Any], dataset_dir: Path) -> None:
    """Validate the pre-fit freeze and every source hash it declares."""

    _exact_keys(
        selection,
        {
            "selection_schema_version",
            "selection_id",
            "status",
            "training",
            "dataset",
            "feature_contract",
            "model_contract",
            "primary_policy",
            "selection",
            "engineering_test",
            "population_mapping",
            "runtime_safety_guard",
            "confirmation",
        },
        "frozen selection",
    )
    if (
        selection.get("selection_schema_version") != 1
        or selection.get("selection_id")
        != "temporal-t2-primary-only-two-objective-v1"
        or selection.get("status") != "frozen_before_canonical_model_fit"
    ):
        raise TemporalT2ExportError("frozen selection identity differs")
    training = selection.get("training")
    dataset = selection.get("dataset")
    feature_contract = selection.get("feature_contract")
    model_contract = selection.get("model_contract")
    primary_policy = selection.get("primary_policy")
    candidate_selection = selection.get("selection")
    if not all(
        isinstance(value, dict)
        for value in (
            training,
            dataset,
            feature_contract,
            model_contract,
            primary_policy,
            candidate_selection,
        )
    ):
        raise TemporalT2ExportError("frozen selection contract is incomplete")
    if (
        training.get("tool") != "tools/train_temporal_t2_value.py"
        or training.get("tool_sha256")
        != sha256_file(Path(trainer.__file__).resolve())
        or training.get("model_spec_id") != EXPECTED_MODEL_SPEC
        or training.get("random_seed") != trainer.RANDOM_SEED
        or training.get("canonical_fit", {}).get("status") != "pending"
        or training.get("canonical_fit", {}).get(
            "must_use_exact_frozen_tool_and_dataset"
        )
        is not True
        or training.get("canonical_fit", {}).get("must_refuse_output_overwrite")
        is not True
    ):
        raise TemporalT2ExportError("frozen training contract differs")
    dependency_hashes = training.get("local_dependency_source_sha256")
    if not isinstance(dependency_hashes, dict):
        raise TemporalT2ExportError("frozen dependency hashes are absent")
    for name, declared in dependency_hashes.items():
        path = TOOLS / name
        if _sha(declared, f"selection dependency {name}") != sha256_file(path):
            raise TemporalT2ExportError(f"frozen dependency hash differs: {name}")
    expected_dataset_hashes = {
        "artifact_manifest_sha256": sha256_file(dataset_dir / "artifact_manifest.json"),
        "dataset_metadata_sha256": sha256_file(dataset_dir / "dataset_metadata.json"),
        "randomized_t2_temporal_sha256": sha256_file(
            dataset_dir / "randomized_t2_temporal.csv"
        ),
    }
    if any(dataset.get(key) != value for key, value in expected_dataset_hashes.items()):
        raise TemporalT2ExportError("frozen temporal dataset hash differs")
    if (
        dataset.get("feature_contract_id") != trainer.temporal_builder.FEATURE_CONTRACT_ID
        or dataset.get("row_count") != 73400
        or dataset.get("run_group_count") != 96
        or feature_contract.get("scope") != "strictly_primary_only"
        or feature_contract.get("secondary_feature_count") != 0
        or feature_contract.get("receiver_prior_outcomes_allowed") is not False
        or feature_contract.get("input_adapter_id") != EXPECTED_FEATURE_ADAPTER
        or feature_contract.get("score_adapter_id") != EXPECTED_SCORE_ADAPTER
        or feature_contract.get("exact_frame_lags") != [1, 3, 8]
    ):
        raise TemporalT2ExportError("frozen feature or dataset contract differs")
    if (
        model_contract.get("cost_model")
        != "Ridge on log1p measured secondary airtime given treated launch, with Duan smearing"
        or model_contract.get("predicted_cost_floor_us") != 1.0
        or model_contract.get("predicted_cost_cap_us") != trainer.PREDICTED_COST_CAP_US
        or primary_policy.get("stage") != "T2"
        or primary_policy.get("sample_offset_us") != 2000
        or primary_policy.get("sequential_policy") != "T2_only"
        or primary_policy.get("threshold_comparator")
        != "float32(score) >= float32(threshold)"
        or primary_policy.get("threshold_must_be_positive") is not True
        or candidate_selection.get("trainer_selection_id") != EXPECTED_SELECTION_ID
        or candidate_selection.get("candidate_count") != 192
        or candidate_selection.get("rankers_in_order") != list(trainer.RANKER_ORDER)
        or candidate_selection.get("frame_gates_in_order") != list(trainer.FRAME_GATES)
    ):
        raise TemporalT2ExportError("frozen policy or selection contract differs")


def _validate_manifest(artifact_dir: Path) -> tuple[dict[str, Any], dict[str, str]]:
    manifest_path = artifact_dir / trainer.OUTPUT_MANIFEST
    manifest = read_object(manifest_path)
    _exact_keys(
        manifest,
        {
            "manifest_schema_version",
            "hash_algorithm",
            "artifacts_sha256",
            "selected_policy_contract",
        },
        "training artifact manifest",
    )
    if manifest["manifest_schema_version"] != 1 or manifest["hash_algorithm"] != "sha256":
        raise TemporalT2ExportError("training artifact manifest identity differs")
    expected_names = {
        trainer.OUTPUT_MODEL,
        trainer.OUTPUT_CANDIDATES,
        trainer.OUTPUT_METRICS,
    }
    try:
        actual_names = {path.name for path in artifact_dir.iterdir()}
    except OSError as error:
        raise TemporalT2ExportError(f"cannot list canonical artifact: {error}") from error
    if actual_names != expected_names | {trainer.OUTPUT_MANIFEST}:
        raise TemporalT2ExportError("canonical artifact directory closure differs")
    if any(
        not (artifact_dir / name).is_file() or (artifact_dir / name).is_symlink()
        for name in actual_names
    ):
        raise TemporalT2ExportError("canonical artifact contains a non-regular file")
    declared = manifest["artifacts_sha256"]
    if not isinstance(declared, dict) or set(declared) != expected_names:
        raise TemporalT2ExportError("training artifact manifest closure differs")
    hashes = {"source_artifact_manifest": sha256_file(manifest_path)}
    if hashes["source_artifact_manifest"] != EXPECTED_SOURCE_MANIFEST_SHA256:
        raise TemporalT2ExportError("canonical source manifest identity differs")
    for name in sorted(expected_names):
        actual = sha256_file(artifact_dir / name)
        if _sha(declared[name], f"artifact {name}") != actual:
            raise TemporalT2ExportError(f"training artifact hash differs: {name}")
        if EXPECTED_SOURCE_ARTIFACTS.get(name) != actual:
            raise TemporalT2ExportError(f"canonical source artifact hash differs: {name}")
        hashes[name] = actual
    return manifest, hashes


def _validate_metrics(
    metrics: dict[str, Any],
    selection: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    _exact_keys(
        metrics,
        {
            "training_schema_version",
            "evidence_status",
            "decisive_confirmation",
            "provenance",
            "dataset_dir",
            "dataset_artifacts_sha256",
            "input_v1_artifacts_sha256",
            "split",
            "test_role_used_during_selection",
            "test_threshold_source",
            "model_spec_id",
            "selection",
            "estimands",
            "conditional_ratio_caveat",
            "feature_families",
            "selected_calibration_policy",
            "engineering_test_policy",
            "random_seed",
            "software",
        },
        "training metrics",
    )
    if (
        metrics.get("training_schema_version") != trainer.TRAINING_SCHEMA_VERSION
        or metrics.get("model_spec_id") != EXPECTED_MODEL_SPEC
        or metrics.get("random_seed") != trainer.RANDOM_SEED
        or metrics.get("test_role_used_during_selection") is not False
        or metrics.get("evidence_status")
        != "previously_opened_run_group_test_engineering_evidence_not_confirmation"
    ):
        raise TemporalT2ExportError("training metrics identity differs")
    provenance = metrics.get("provenance")
    _exact_keys(
        provenance,
        {
            "repository_git_commit",
            "repository_worktree_clean",
            "repository_worktree_status_sha256",
            "trainer_source_path",
            "trainer_source_sha256",
            "local_dependency_source_sha256",
        },
        "training provenance",
    )
    if (
        provenance.get("trainer_source_path") != "tools/train_temporal_t2_value.py"
        or provenance.get("trainer_source_sha256")
        != selection["training"]["tool_sha256"]
        or provenance.get("repository_worktree_clean") is not True
        or provenance.get("repository_worktree_status_sha256")
        != hashlib.sha256(b"").hexdigest()
        or provenance.get("repository_git_commit") != EXPECTED_TRAINING_GIT_COMMIT
        or provenance.get("local_dependency_source_sha256")
        != selection["training"]["local_dependency_source_sha256"]
    ):
        raise TemporalT2ExportError("training provenance differs from the freeze")
    if metrics.get("software") != {
        "python": EXPECTED_PYTHON,
        "numpy": EXPECTED_NUMPY,
        "scikit_learn": EXPECTED_SKLEARN,
    }:
        raise TemporalT2ExportError("canonical training software differs")
    if metrics.get("dataset_artifacts_sha256") != {
        "dataset_metadata.json": selection["dataset"]["dataset_metadata_sha256"],
        "randomized_t2_temporal.csv": selection["dataset"][
            "randomized_t2_temporal_sha256"
        ],
    }:
        raise TemporalT2ExportError("training metrics dataset identity differs")
    if metrics.get("input_v1_artifacts_sha256") != selection["dataset"].get(
        "input_v1_artifacts_sha256"
    ):
        raise TemporalT2ExportError("training metrics input-v1 identity differs")
    metric_selection = metrics.get("selection")
    if (
        not isinstance(metric_selection, dict)
        or metric_selection.get("selection_id") != EXPECTED_SELECTION_ID
        or metric_selection.get("role") != "calibration"
        or metric_selection.get("candidate_count") != 192
        or metric_selection.get("feature_family_order")
        != list(trainer.FEATURE_FAMILY_ORDER)
        or metric_selection.get("ranker_order") != list(trainer.RANKER_ORDER)
        or metric_selection.get("frame_gate_order") != list(trainer.FRAME_GATES)
        or metric_selection.get("requested_action_fractions")
        != list(trainer.REQUESTED_ACTION_FRACTIONS)
        or metrics.get("test_threshold_source")
        != "single frozen calibration float32 threshold"
    ):
        raise TemporalT2ExportError("training metrics selection contract differs")
    split = metrics.get("split")
    if (
        not isinstance(split, dict)
        or split.get("algorithm") != selection["dataset"]["split"]["algorithm"]
        or split.get("unit") != selection["dataset"]["split"]["unit"]
        or split.get("counts") != {"train": 64, "calibration": 16, "test": 16}
    ):
        raise TemporalT2ExportError("training metrics split contract differs")
    selected = metrics.get("selected_calibration_policy")
    engineering = metrics.get("engineering_test_policy")
    contract = manifest.get("selected_policy_contract")
    if not all(isinstance(value, dict) for value in (selected, engineering, contract)):
        raise TemporalT2ExportError("selected policy evidence is absent")
    _exact_keys(
        contract,
        {
            "feature_family",
            "ordered_feature_names",
            "ranker",
            "frame_gate",
            "score_threshold_float32",
            "feature_adapter_id",
            "score_adapter_id",
        },
        "manifest selected policy",
    )
    threshold = _finite(selected.get("score_threshold"), "selected threshold")
    if threshold <= 0 or float(np.float32(threshold)) != threshold:
        raise TemporalT2ExportError("selected threshold is not positive float32")
    expected = {
        "feature_family": EXPECTED_FEATURE_FAMILY,
        "ranker": EXPECTED_RANKER,
        "frame_gate": EXPECTED_FRAME_GATE,
    }
    if any(selected.get(key) != value for key, value in expected.items()):
        raise TemporalT2ExportError("selected calibration policy differs")
    if (
        selected.get("requested_action_fraction") != 0.15
        or selected.get("candidate_ordinal") != 188
        or selected.get("admissible") != 1
        or engineering.get("frozen_feature_family") != EXPECTED_FEATURE_FAMILY
        or engineering.get("frozen_ranker") != EXPECTED_RANKER
        or engineering.get("frozen_frame_gate") != EXPECTED_FRAME_GATE
        or engineering.get("frozen_float32_score_threshold") != threshold
        or contract.get("feature_family") != EXPECTED_FEATURE_FAMILY
        or contract.get("ranker") != EXPECTED_RANKER
        or contract.get("frame_gate") != EXPECTED_FRAME_GATE
        or contract.get("score_threshold_float32") != threshold
        or contract.get("feature_adapter_id") != EXPECTED_FEATURE_ADAPTER
        or contract.get("score_adapter_id") != EXPECTED_SCORE_ADAPTER
    ):
        raise TemporalT2ExportError("selected policy contracts disagree")


def _validate_feature_names(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise TemporalT2ExportError("selected feature names are absent")
    names = tuple(value)
    if (
        len(names) != EXPECTED_FEATURE_COUNT
        or len(set(names)) != len(names)
        or any(
            not isinstance(name, str)
            or not name
            or not name.isascii()
            or "secondary" in name
            for name in names
        )
    ):
        raise TemporalT2ExportError("selected feature contract is invalid")
    return names


def _imputer_data(imputer: Any, feature_count: int, context: str) -> dict[str, Any]:
    if not isinstance(imputer, SimpleImputer):
        raise TemporalT2ExportError(f"{context}: unsupported imputer")
    if (
        imputer.strategy != "median"
        or imputer.add_indicator is not True
        or imputer.keep_empty_features is not True
    ):
        raise TemporalT2ExportError(f"{context}: imputer contract differs")
    statistics = np.asarray(imputer.statistics_, dtype=np.float64)
    indicators = np.asarray(imputer.indicator_.features_)
    if (
        statistics.shape != (feature_count,)
        or not np.all(np.isfinite(statistics))
        or indicators.ndim != 1
        or not np.issubdtype(indicators.dtype, np.integer)
        or len(set(indicators.tolist())) != len(indicators)
        or np.any(indicators < 0)
        or np.any(indicators >= feature_count)
    ):
        raise TemporalT2ExportError(f"{context}: fitted imputer shape differs")
    return {
        "medians": statistics.tolist(),
        "missing_indicator_raw_features": [int(value) for value in indicators],
    }


def _hgb_data(pipeline: Any, feature_count: int, context: str) -> dict[str, Any]:
    if not isinstance(pipeline, Pipeline) or list(pipeline.named_steps) != [
        "impute",
        "classifier",
    ]:
        raise TemporalT2ExportError(f"{context}: classifier pipeline differs")
    imputer = _imputer_data(pipeline.named_steps["impute"], feature_count, context)
    model = pipeline.named_steps["classifier"]
    if not isinstance(model, HistGradientBoostingClassifier):
        raise TemporalT2ExportError(f"{context}: classifier type differs")
    expected_parameters = {
        "loss": "log_loss",
        "learning_rate": 0.05,
        "max_iter": 64,
        "max_leaf_nodes": 7,
        "max_depth": 3,
        "min_samples_leaf": 20,
        "l2_regularization": 1.0,
        "max_bins": 63,
        "early_stopping": False,
    }
    parameters = model.get_params(deep=False)
    if any(parameters.get(key) != value for key, value in expected_parameters.items()):
        raise TemporalT2ExportError(f"{context}: HGB hyperparameters differ")
    transformed_count = feature_count + len(imputer["missing_indicator_raw_features"])
    if (
        int(model.n_features_in_) != transformed_count
        or np.asarray(model.classes_).tolist() != [0, 1]
        or len(model._predictors) != EXPECTED_TREE_COUNT
    ):
        raise TemporalT2ExportError(f"{context}: fitted HGB shape differs")
    nodes: list[list[Any]] = []
    trees: list[list[int]] = []
    for iteration_index, iteration in enumerate(model._predictors):
        if len(iteration) != 1:
            raise TemporalT2ExportError(f"{context}: HGB is not binary")
        tree = iteration[0]
        offset = len(nodes)
        count = len(tree.nodes)
        if count <= 0 or count > 65535:
            raise TemporalT2ExportError(f"{context}: invalid tree size")
        for node_index, node in enumerate(tree.nodes):
            value = _finite(node["value"], f"{context} tree value")
            threshold = _finite(node["num_threshold"], f"{context} threshold")
            feature = int(node["feature_idx"])
            left = int(node["left"])
            right = int(node["right"])
            missing_left = bool(node["missing_go_to_left"])
            leaf = bool(node["is_leaf"])
            if bool(node["is_categorical"]):
                raise TemporalT2ExportError(f"{context}: categorical HGB split")
            if not 0 <= feature < transformed_count:
                raise TemporalT2ExportError(f"{context}: tree feature escapes input")
            if not leaf and not (0 <= left < count and 0 <= right < count):
                raise TemporalT2ExportError(f"{context}: tree child escapes tree")
            if leaf and (left != 0 or right != 0):
                raise TemporalT2ExportError(f"{context}: leaf has children")
            nodes.append(
                [value, threshold, feature, left, right, missing_left, leaf]
            )
        trees.append([offset, count])
    baseline = np.asarray(model._baseline_prediction, dtype=np.float64).reshape(-1)
    if baseline.shape != (1,) or not np.isfinite(baseline[0]):
        raise TemporalT2ExportError(f"{context}: HGB baseline differs")
    return {
        "imputer": imputer,
        "nodes": nodes,
        "trees": trees,
        "baseline": float(baseline[0]),
    }


def _ridge_data(pipeline: Any, feature_count: int) -> dict[str, Any]:
    if not isinstance(pipeline, Pipeline) or list(pipeline.named_steps) != [
        "impute",
        "scale",
        "regressor",
    ]:
        raise TemporalT2ExportError("cost-model pipeline differs")
    imputer = _imputer_data(pipeline.named_steps["impute"], feature_count, "cost model")
    scaler = pipeline.named_steps["scale"]
    ridge = pipeline.named_steps["regressor"]
    if not isinstance(scaler, StandardScaler) or not isinstance(ridge, Ridge):
        raise TemporalT2ExportError("cost-model fitted classes differ")
    if (
        scaler.with_mean is not True
        or scaler.with_std is not True
        or ridge.alpha != 10.0
        or ridge.fit_intercept is not True
    ):
        raise TemporalT2ExportError("cost-model hyperparameters differ")
    transformed_count = feature_count + len(imputer["missing_indicator_raw_features"])
    means = np.asarray(scaler.mean_, dtype=np.float64)
    scales = np.asarray(scaler.scale_, dtype=np.float64)
    coefficients = np.asarray(ridge.coef_, dtype=np.float64).reshape(-1)
    intercept = np.asarray(ridge.intercept_, dtype=np.float64).reshape(-1)
    if (
        int(scaler.n_features_in_) != transformed_count
        or int(ridge.n_features_in_) != transformed_count
        or means.shape != (transformed_count,)
        or scales.shape != (transformed_count,)
        or coefficients.shape != (transformed_count,)
        or intercept.shape != (1,)
        or not np.all(np.isfinite(means))
        or not np.all(np.isfinite(scales))
        or not np.all(scales > 0)
        or not np.all(np.isfinite(coefficients))
        or not np.isfinite(intercept[0])
    ):
        raise TemporalT2ExportError("cost-model fitted shape differs")
    return {
        "imputer": imputer,
        "means": means.tolist(),
        "scales": scales.tolist(),
        "coefficients": coefficients.tolist(),
        "intercept": float(intercept[0]),
    }


def _validate_source(
    artifact_dir: Path | str,
    selection_path: Path | str,
    dataset_dir: Path | str,
    *,
    require_canonical_paths: bool,
) -> ExportSource:
    """Validate the complete canonical source before extracting fitted data."""

    artifact = Path(artifact_dir).resolve()
    selection_file = Path(selection_path).resolve()
    dataset = Path(dataset_dir).resolve()
    if require_canonical_paths and (
        artifact != CANONICAL_ARTIFACT_DIR
        or selection_file != CANONICAL_SELECTION_PATH
        or dataset != CANONICAL_DATASET_DIR
    ):
        raise TemporalT2ExportError(
            "source paths differ from the repository canonical closure"
        )
    if sha256_file(selection_file) != EXPECTED_SELECTION_SHA256:
        raise TemporalT2ExportError("frozen selection file hash differs")
    try:
        dataset_names = {path.name for path in dataset.iterdir()}
    except OSError as error:
        raise TemporalT2ExportError(f"cannot list temporal dataset: {error}") from error
    if dataset_names != {
        "artifact_manifest.json",
        "dataset_metadata.json",
        "randomized_t2_temporal.csv",
    } or any(
        not (dataset / name).is_file() or (dataset / name).is_symlink()
        for name in dataset_names
    ):
        raise TemporalT2ExportError("temporal dataset directory closure differs")
    for name, digest in EXPECTED_DATASET_ARTIFACTS.items():
        if sha256_file(dataset / name) != digest:
            raise TemporalT2ExportError(f"canonical temporal dataset hash differs: {name}")
    selection = read_object(selection_file)
    _validate_selection(selection, dataset)
    manifest, hashes = _validate_manifest(artifact)
    metrics = read_object(artifact / trainer.OUTPUT_METRICS)
    _validate_metrics(metrics, selection, manifest)

    # The local canonical artifact is explicitly trusted by the caller. Verify
    # all hashes and JSON contracts before invoking pickle's object loader.
    try:
        with (artifact / trainer.OUTPUT_MODEL).open("rb") as source_file:
            bundle = pickle.load(source_file)
    except (OSError, pickle.UnpicklingError) as error:
        raise TemporalT2ExportError(f"cannot load canonical model bundle: {error}") from error
    if not isinstance(bundle, dict):
        raise TemporalT2ExportError("model bundle root differs")
    _exact_keys(
        bundle,
        {
            "model_bundle_schema_version",
            "training_schema_version",
            "feature_contract_id",
            "model_spec_id",
            "selection_id",
            "preprocessing",
            "feature_families",
            "compact_primary_physics",
            "rankers",
            "evaluation_nuisances",
            "selected_policy",
        },
        "model bundle",
    )
    if (
        bundle.get("model_bundle_schema_version") != trainer.MODEL_BUNDLE_SCHEMA_VERSION
        or bundle.get("training_schema_version") != trainer.TRAINING_SCHEMA_VERSION
        or bundle.get("feature_contract_id")
        != trainer.temporal_builder.FEATURE_CONTRACT_ID
        or bundle.get("model_spec_id") != EXPECTED_MODEL_SPEC
        or bundle.get("selection_id") != EXPECTED_SELECTION_ID
    ):
        raise TemporalT2ExportError("model bundle identity differs")
    preprocessing = bundle.get("preprocessing")
    selected = bundle.get("selected_policy")
    families = bundle.get("feature_families")
    if not all(isinstance(value, dict) for value in (preprocessing, selected, families)):
        raise TemporalT2ExportError("model bundle contract is incomplete")
    _exact_keys(
        preprocessing,
        {
            "feature_adapter_id",
            "numeric_input",
            "derived_input",
            "categorical",
            "missing",
            "hgb",
            "ridge_cost",
            "score_adapter_id",
            "score_comparator",
        },
        "model preprocessing",
    )
    _exact_keys(
        selected,
        {
            "feature_family",
            "ordered_feature_names",
            "ranker",
            "frame_gate",
            "score_threshold",
            "requested_action_fraction",
            "selection_role",
            "runtime_gates",
        },
        "bundle selected policy",
    )
    if (
        preprocessing.get("feature_adapter_id") != EXPECTED_FEATURE_ADAPTER
        or preprocessing.get("score_adapter_id") != EXPECTED_SCORE_ADAPTER
        or preprocessing.get("score_comparator")
        != "float32 score >= frozen float32 calibration threshold"
        or preprocessing.get("ridge_cost", {}).get("predicted_cost_cap_us")
        != trainer.PREDICTED_COST_CAP_US
    ):
        raise TemporalT2ExportError("model preprocessing contract differs")
    threshold = metrics["selected_calibration_policy"]["score_threshold"]
    if (
        selected.get("feature_family") != EXPECTED_FEATURE_FAMILY
        or selected.get("ranker") != EXPECTED_RANKER
        or selected.get("frame_gate") != EXPECTED_FRAME_GATE
        or selected.get("score_threshold") != threshold
        or selected.get("requested_action_fraction") != 0.15
        or selected.get("selection_role") != "calibration"
        or selected.get("runtime_gates", {}).get(
            "exact_lag1_lag3_lag8_history_required"
        )
        is not True
    ):
        raise TemporalT2ExportError("model selected policy differs")
    names = _validate_feature_names(selected.get("ordered_feature_names"))
    manifest_names = _validate_feature_names(
        manifest["selected_policy_contract"].get("ordered_feature_names")
    )
    metric_names = _validate_feature_names(
        metrics["feature_families"][EXPECTED_FEATURE_FAMILY].get(
            "ordered_feature_names"
        )
    )
    if names != manifest_names or names != metric_names:
        raise TemporalT2ExportError("selected feature orders disagree")
    family = families.get(EXPECTED_FEATURE_FAMILY)
    if set(families) != set(trainer.FEATURE_FAMILY_ORDER):
        raise TemporalT2ExportError("bundle feature-family set differs")
    _exact_keys(
        family,
        {
            "ordered_feature_names",
            "feature_count",
            "contains_secondary_feature",
            "requires_exact_lags",
            "fit_counts",
            "heads",
        },
        "selected feature family",
    )
    if (
        not isinstance(family, dict)
        or tuple(family.get("ordered_feature_names", ())) != names
        or family.get("feature_count") != EXPECTED_FEATURE_COUNT
        or family.get("contains_secondary_feature") is not False
        or family.get("requires_exact_lags") != [1, 3, 8]
    ):
        raise TemporalT2ExportError("selected feature family differs")
    heads = family.get("heads")
    if not isinstance(heads, dict):
        raise TemporalT2ExportError("selected fitted heads are absent")
    required_heads = {
        PRIMARY_HEAD_KEY,
        TREATED_HEAD_KEY,
        COST_KEY,
        "log_cost_smearing_factor",
    }
    expected_heads = {
        f"{target}:{suffix}"
        for target in trainer.VALUE_TARGETS
        for suffix in ("primary_need", "treated_bad")
    } | {
        COST_KEY,
        "log_cost_smearing_factor",
        "training_risk_normalizers",
    }
    if set(heads) != expected_heads or not required_heads <= set(heads):
        raise TemporalT2ExportError("selected fitted head set differs")
    smearing = _finite(heads["log_cost_smearing_factor"], "Duan smearing factor")
    if smearing <= 0:
        raise TemporalT2ExportError("Duan smearing factor is not positive")
    if (
        sys.version.split()[0] != EXPECTED_PYTHON
        or importlib.metadata.version("numpy") != EXPECTED_NUMPY
        or importlib.metadata.version("scikit-learn") != EXPECTED_SKLEARN
    ):
        raise TemporalT2ExportError("export software version differs from contract")

    primary_data = _hgb_data(heads[PRIMARY_HEAD_KEY], len(names), "primary bad12")
    treated_data = _hgb_data(heads[TREATED_HEAD_KEY], len(names), "treated bad12")
    cost_data = _ridge_data(heads[COST_KEY], len(names))
    if (
        treated_data["imputer"] != cost_data["imputer"]
        or primary_data["imputer"]["missing_indicator_raw_features"]
        != treated_data["imputer"]["missing_indicator_raw_features"]
    ):
        raise TemporalT2ExportError("selected imputers cannot share the runtime adapter")
    hashes.update(
        {
            "selection": sha256_file(selection_file),
            "dataset_manifest": sha256_file(dataset / "artifact_manifest.json"),
            "dataset_metadata": sha256_file(dataset / "dataset_metadata.json"),
            "dataset_csv": sha256_file(dataset / "randomized_t2_temporal.csv"),
            "trainer": sha256_file(Path(trainer.__file__).resolve()),
            "exporter": sha256_file(Path(__file__).resolve()),
        }
    )
    return ExportSource(
        artifact,
        dataset,
        selection_file,
        selection,
        manifest,
        metrics,
        bundle,
        names,
        heads[PRIMARY_HEAD_KEY],
        heads[TREATED_HEAD_KEY],
        heads[COST_KEY],
        smearing,
        hashes,
    )


def validate_source(
    artifact_dir: Path | str,
    selection_path: Path | str,
    dataset_dir: Path | str,
) -> ExportSource:
    """Validate only the frozen source at its repository canonical paths."""

    return _validate_source(
        artifact_dir,
        selection_path,
        dataset_dir,
        require_canonical_paths=True,
    )


def _validate_copied_source_for_testing(
    artifact_dir: Path | str,
    selection_path: Path | str,
    dataset_dir: Path | str,
) -> ExportSource:
    """Validate copied fixtures while retaining all content checks in unit tests."""

    return _validate_source(
        artifact_dir,
        selection_path,
        dataset_dir,
        require_canonical_paths=False,
    )


def export_payload(source: ExportSource) -> dict[str, Any]:
    """Extract only the three fitted components used by the selected policy."""

    primary = _hgb_data(source.primary_head, len(source.feature_names), "primary bad12")
    treated = _hgb_data(source.treated_head, len(source.feature_names), "treated bad12")
    cost = _ridge_data(source.cost_model, len(source.feature_names))
    if treated["imputer"] != cost["imputer"]:
        raise TemporalT2ExportError("cost and treated imputers changed during export")
    threshold = source.bundle["selected_policy"]["score_threshold"]
    payload = {
        "export_schema_version": EXPORT_SCHEMA_VERSION,
        "feature_names": list(source.feature_names),
        "primary_bad12": primary,
        "treated_bad12": treated,
        "cost": {
            key: value for key, value in cost.items() if key != "imputer"
        },
        "smearing_factor": source.smearing_factor,
        "predicted_cost_cap_us": trainer.PREDICTED_COST_CAP_US,
        "selected_policy": {
            "feature_family": EXPECTED_FEATURE_FAMILY,
            "ranker": EXPECTED_RANKER,
            "frame_gate": EXPECTED_FRAME_GATE,
            "score_threshold_float32": threshold,
            "feature_adapter_id": EXPECTED_FEATURE_ADAPTER,
            "score_adapter_id": EXPECTED_SCORE_ADAPTER,
            "threshold_comparator": "float32_score_greater_than_or_equal",
        },
    }
    return payload


def cpp_float(value: float) -> str:
    """Format one finite double as a round-trip-safe C++ literal."""

    number = _finite(value, "C++ floating literal")
    text = format(number, ".17g")
    if "." not in text and "e" not in text:
        text += ".0"
    return text


def cpp_float32(value: float) -> str:
    """Format one exact float32 as a C++ float literal."""

    quantized = np.float32(_finite(value, "C++ float literal"))
    if float(quantized) != float(value):
        raise TemporalT2ExportError("C++ float literal is not exactly float32")
    text = format(float(quantized), ".9g")
    if "." not in text and "e" not in text:
        text += ".0"
    return f"{text}F"


def cpp_string(value: str) -> str:
    """Format one ASCII string as a C++ literal."""

    if not isinstance(value, str) or not value.isascii():
        raise TemporalT2ExportError("generated string is not ASCII")
    return json.dumps(value, ensure_ascii=True)


def _component_digests(
    source: ExportSource, payload: dict[str, Any]
) -> dict[str, str]:
    selected_contract = payload["selected_policy"]
    feature_contract = {
        "feature_names": payload["feature_names"],
        "feature_count": len(payload["feature_names"]),
        "feature_adapter_id": selected_contract["feature_adapter_id"],
        "exact_frame_lags": [1, 3, 8],
        "primary_only": True,
    }
    cost_contract = {
        "cost": payload["cost"],
        "shared_imputer": payload["treated_bad12"]["imputer"],
        "smearing_factor": payload["smearing_factor"],
        "predicted_cost_cap_us": payload["predicted_cost_cap_us"],
    }
    return {
        "plain_model": canonical_sha256(payload),
        "feature_contract": canonical_sha256(feature_contract),
        "selected_policy": canonical_sha256(selected_contract),
        "primary_head": canonical_sha256(payload["primary_bad12"]),
        "treated_head": canonical_sha256(payload["treated_bad12"]),
        "cost_model": canonical_sha256(cost_contract),
        "source_model": source.file_hashes[trainer.OUTPUT_MODEL],
        "source_metrics": source.file_hashes[trainer.OUTPUT_METRICS],
        "source_manifest": source.file_hashes["source_artifact_manifest"],
        "frozen_selection": source.file_hashes["selection"],
        "dataset_manifest": source.file_hashes["dataset_manifest"],
        "dataset_metadata": source.file_hashes["dataset_metadata"],
        "dataset_csv": source.file_hashes["dataset_csv"],
        "trainer": source.file_hashes["trainer"],
        "exporter": source.file_hashes["exporter"],
    }


def _emit_hgb_arrays(
    lines: list[str], prefix: str, head: dict[str, Any], imputer_name: str
) -> None:
    nodes = head["nodes"]
    trees = head["trees"]
    lines.append(f"constexpr std::array<Node, {len(nodes)}> g_{prefix}Nodes{{{{")
    lines.extend(
        "    {%s, %s, %d, %d, %d, %s, %s},"
        % (
            cpp_float(value),
            cpp_float(threshold),
            feature,
            left,
            right,
            str(missing_left).lower(),
            str(leaf).lower(),
        )
        for value, threshold, feature, left, right, missing_left, leaf in nodes
    )
    lines.extend(["}};", ""])
    lines.append(f"constexpr std::array<Tree, {len(trees)}> g_{prefix}Trees{{{{")
    lines.extend(f"    {{{offset}, {count}}}," for offset, count in trees)
    lines.extend(["}};", ""])
    lines.extend(
        [
            f"const HgbClassifier g_{prefix}Classifier{{",
            f"    {imputer_name},",
            f"    g_{prefix}Nodes,",
            f"    g_{prefix}Trees,",
            f"    {cpp_float(head['baseline'])},",
            "};",
            "",
        ]
    )


def _provenance_values(
    source: ExportSource, digests: dict[str, str]
) -> list[str]:
    """Return provenance fields in the public C++ aggregate order."""

    return [
        source.metrics["evidence_status"],
        source.bundle["feature_contract_id"],
        source.bundle["model_spec_id"],
        source.bundle["selection_id"],
        source.metrics["provenance"]["repository_git_commit"],
        digests["source_model"],
        digests["source_metrics"],
        digests["source_manifest"],
        digests["frozen_selection"],
        digests["dataset_manifest"],
        digests["dataset_metadata"],
        digests["dataset_csv"],
        digests["trainer"],
        digests["exporter"],
        digests["plain_model"],
        digests["feature_contract"],
        digests["selected_policy"],
        digests["primary_head"],
        digests["treated_head"],
        digests["cost_model"],
    ]


def emit_model_source(
    source: ExportSource, payload: dict[str, Any], digests: dict[str, str]
) -> str:
    """Render deterministic compiled model data."""

    primary_imputer = payload["primary_bad12"]["imputer"]
    treated_imputer = payload["treated_bad12"]["imputer"]
    indicators = treated_imputer["missing_indicator_raw_features"]
    cost = payload["cost"]
    policy = payload["selected_policy"]
    provenance_values = _provenance_values(source, digests)
    lines = [
        "/*",
        " * SPDX-License-Identifier: GPL-2.0-only",
        " *",
        " * Generated by tools/export_temporal_t2_value_model_v1.py.",
        f" * Export schema: {EXPORT_SCHEMA_VERSION}",
        f" * Source model SHA-256: {digests['source_model']}",
        f" * Plain model SHA-256: {digests['plain_model']}",
        " */",
        "",
        '#include "temporal-t2-value-model-data-v1.h"',
        "",
        "#include <array>",
        "",
        "namespace ns3",
        "{",
        "namespace temporal_t2_value_model_v1",
        "{",
        "namespace",
        "{",
        f"constexpr std::array<std::string_view, {len(payload['feature_names'])}> "
        "g_featureNames{{",
    ]
    lines.extend(f"    {cpp_string(name)}," for name in payload["feature_names"])
    lines.extend(["}};", ""])
    lines.append(
        f"constexpr std::array<uint16_t, {len(indicators)}> g_missingIndicators{{{{"
    )
    lines.extend(f"    {value}," for value in indicators)
    lines.extend(["}};", ""])
    for name, imputer in (
        ("primary", primary_imputer),
        ("treated", treated_imputer),
    ):
        medians = imputer["medians"]
        lines.append(f"constexpr std::array<double, {len(medians)}> g_{name}Medians{{{{")
        lines.extend(f"    {cpp_float(value)}," for value in medians)
        lines.extend(["}};", ""])
        lines.extend(
            [
                f"const Imputer g_{name}Imputer{{",
                f"    g_{name}Medians,",
                "    g_missingIndicators,",
                "};",
                "",
            ]
        )
    _emit_hgb_arrays(
        lines, "primaryBad12", payload["primary_bad12"], "g_primaryImputer"
    )
    _emit_hgb_arrays(
        lines, "treatedBad12", payload["treated_bad12"], "g_treatedImputer"
    )
    for field, cpp_name in (
        ("means", "costMeans"),
        ("scales", "costScales"),
        ("coefficients", "costCoefficients"),
    ):
        values = cost[field]
        lines.append(f"constexpr std::array<double, {len(values)}> g_{cpp_name}{{{{")
        lines.extend(f"    {cpp_float(value)}," for value in values)
        lines.extend(["}};", ""])
    lines.extend(
        [
            "const RidgeCostModel g_costModel{",
            "    g_treatedImputer,",
            "    g_costMeans,",
            "    g_costScales,",
            "    g_costCoefficients,",
            f"    {cpp_float(cost['intercept'])},",
            f"    {cpp_float(math.log(payload['smearing_factor']))},",
            f"    {cpp_float(math.log1p(payload['predicted_cost_cap_us']))},",
            "};",
            "",
            "constexpr Metadata g_metadata{",
            "    {",
            *(f"        {cpp_string(value)}," for value in provenance_values),
            "    },",
            f"    {cpp_string(policy['feature_family'])},",
            f"    {cpp_string(policy['ranker'])},",
            f"    {cpp_string(policy['frame_gate'])},",
            f"    {cpp_string(policy['feature_adapter_id'])},",
            f"    {cpp_string(policy['score_adapter_id'])},",
            f"    {cpp_float32(policy['score_threshold_float32'])},",
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
            "const Metadata&",
            "GetMetadata()",
            "{",
            "    return g_metadata;",
            "}",
            "",
            "const HgbClassifier&",
            "GetPrimaryBad12Classifier()",
            "{",
            "    return g_primaryBad12Classifier;",
            "}",
            "",
            "const HgbClassifier&",
            "GetTreatedBad12Classifier()",
            "{",
            "    return g_treatedBad12Classifier;",
            "}",
            "",
            "const RidgeCostModel&",
            "GetCostModel()",
            "{",
            "    return g_costModel;",
            "}",
            "",
            "} // namespace temporal_t2_value_model_v1",
            "} // namespace ns3",
            "",
        ]
    )
    return "\n".join(lines)


def _quantized_vector(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.shape != (EXPECTED_FEATURE_COUNT,):
        raise TemporalT2ExportError("golden input width differs")
    finite = np.isfinite(array)
    if np.any(~finite & ~np.isnan(array)):
        raise TemporalT2ExportError("golden input contains infinity")
    with np.errstate(over="ignore", invalid="ignore"):
        quantized = array.astype(np.float32)
    if np.any(np.isinf(quantized)):
        raise TemporalT2ExportError("golden input overflows float32")
    return quantized.astype(np.float64)


def sklearn_result(source: ExportSource, values: np.ndarray) -> dict[str, Any]:
    """Evaluate one golden vector through the exact selected sklearn pipelines."""

    vector = _quantized_vector(values).reshape(1, -1)
    primary_logit = float(source.primary_head.decision_function(vector)[0])
    primary_probability = float(source.primary_head.predict_proba(vector)[0, 1])
    treated_logit = float(source.treated_head.decision_function(vector)[0])
    treated_probability = float(source.treated_head.predict_proba(vector)[0, 1])
    predicted_log = float(source.cost_model.predict(vector)[0])
    adjusted_log = float(
        np.clip(
            predicted_log + math.log(source.smearing_factor),
            0.0,
            math.log1p(trainer.PREDICTED_COST_CAP_US),
        )
    )
    predicted_cost = max(math.expm1(adjusted_log), 1.0)
    value = max(primary_probability - treated_probability, 0.0)
    score = np.float32(value / predicted_cost)
    threshold = np.float32(source.bundle["selected_policy"]["score_threshold"])
    return {
        "primary_bad12_logit": primary_logit,
        "primary_bad12_probability": primary_probability,
        "treated_bad12_logit": treated_logit,
        "treated_bad12_probability": treated_probability,
        "predicted_log_airtime": predicted_log,
        "predicted_secondary_airtime_us": predicted_cost,
        "nonnegative_bad12_value": value,
        "value_per_cost_score": float(score),
        "passes_score_threshold": bool(score >= threshold),
    }


def golden_inputs(source: ExportSource) -> list[tuple[str, np.ndarray]]:
    """Build synthetic and real threshold-boundary parity vectors."""

    count = len(source.feature_names)
    zeros = np.zeros(count, dtype=np.float64)
    zeros[67] = 1.0
    missing = np.full(count, np.nan, dtype=np.float64)
    ramp = np.asarray(
        [((index % 17) - 8) * (index + 1) / 7 for index in range(count)],
        dtype=np.float64,
    )
    ramp[66], ramp[67] = 0.0, 1.0
    deterministic = np.asarray(
        [(((index * 7919) % 1009) - 504) * (1 + index % 11) / 13 for index in range(count)],
        dtype=np.float64,
    )
    deterministic[66], deterministic[67] = 1.0, 0.0
    missing_probe = np.zeros(count, dtype=np.float64)
    indicators = source.treated_head.named_steps["impute"].indicator_.features_
    missing_probe[np.asarray(indicators, dtype=int)] = np.nan
    missing_probe[67] = 1.0
    extremes = np.asarray(
        [(-1.0 if index % 2 else 1.0) * 10.0 ** (index % 7) for index in range(count)],
        dtype=np.float64,
    )
    extremes[66], extremes[67] = 0.0, 1.0
    cases: list[tuple[str, np.ndarray]] = [
        ("zeros_p", zeros),
        ("all_missing", missing),
        ("ramp_p", ramp),
        ("deterministic_i", deterministic),
        ("missing_indicator_probe_p", missing_probe),
        ("extremes_p", extremes),
    ]

    dataset = trainer.load_temporal_dataset(source.dataset_dir)
    family = dataset.stage_for_family(EXPECTED_FEATURE_FAMILY)
    threshold = np.float32(source.bundle["selected_policy"]["score_threshold"])
    selected_heads = source.bundle["feature_families"][EXPECTED_FEATURE_FAMILY]["heads"]
    chosen: list[tuple[str, int]] = []
    for role in ("calibration", "test"):
        indices = family.indices(role)
        scores = trainer._candidate_scores(
            selected_heads, family.matrix[indices]
        )[EXPECTED_RANKER].astype(np.float32)
        gate = trainer._frame_gate(dataset.data, indices, EXPECTED_FRAME_GATE)
        positions = np.flatnonzero(gate)
        below = positions[scores[positions] < threshold]
        above = positions[scores[positions] >= threshold]
        if len(below) == 0 or len(above) == 0:
            raise TemporalT2ExportError(f"{role}: threshold boundary has no support")
        chosen.append((f"{role}_nearest_below", int(below[np.argmax(scores[below])])))
        chosen.append((f"{role}_nearest_at_or_above", int(above[np.argmin(scores[above])])))
        if role == "calibration" and not np.any(scores[positions] == threshold):
            raise TemporalT2ExportError("calibration has no exact frozen-threshold row")
    test_indices = family.indices("test")
    test_gate = trainer._frame_gate(dataset.data, test_indices, EXPECTED_FRAME_GATE)
    i_positions = np.flatnonzero(~test_gate)
    if len(i_positions) == 0:
        raise TemporalT2ExportError("test split has no I-frame gate probe")
    chosen.append(("test_i_frame_gate_probe", int(i_positions[0])))
    for label, local in chosen:
        role = "calibration" if label.startswith("calibration") else "test"
        indices = family.indices(role)
        cases.append((label, family.matrix[indices[local]].copy()))
    return cases


def emit_golden_header(
    source: ExportSource,
    digests: dict[str, str],
    cases: Sequence[tuple[str, np.ndarray]],
) -> str:
    """Render exact sklearn parity cases for the ns-3 unit test."""

    provenance_values = _provenance_values(source, digests)
    lines = [
        "/*",
        " * SPDX-License-Identifier: GPL-2.0-only",
        " *",
        " * Generated by tools/export_temporal_t2_value_model_v1.py.",
        f" * Source model SHA-256: {digests['source_model']}",
        f" * Plain model SHA-256: {digests['plain_model']}",
        " */",
        "",
        "#ifndef TEMPORAL_T2_VALUE_MODEL_GOLDEN_V1_H",
        "#define TEMPORAL_T2_VALUE_MODEL_GOLDEN_V1_H",
        "",
        '#include "ns3/temporal-t2-value-model-evaluator.h"',
        "",
        "#include <array>",
        "#include <limits>",
        "#include <string_view>",
        "",
        "namespace ns3",
        "{",
        "namespace temporal_t2_value_model_golden_v1",
        "{",
        "",
        "inline constexpr TemporalT2ValueModelProvenance g_provenance{",
        *(f"    {cpp_string(value)}," for value in provenance_values),
        "};",
        "",
        "/**",
        " * @ingroup tests",
        " * One deterministic sklearn/C++ parity case.",
        " */",
        "struct GoldenCase",
        "{",
        "    std::string_view label; ///< Human-readable parity-case label.",
        f"    std::array<double, {EXPECTED_FEATURE_COUNT}> features; ///< Raw features.",
        "    TemporalT2ValueModelResult expected; ///< Exact sklearn output.",
        "};",
        "",
        f"inline const std::array<GoldenCase, {len(cases)}> g_cases{{{{",
    ]
    for label, raw in cases:
        result = sklearn_result(source, raw)
        lines.extend([f"    {{{cpp_string(label)},", "     {{"])
        for value in np.asarray(raw, dtype=np.float64):
            if np.isnan(value):
                lines.append("         std::numeric_limits<double>::quiet_NaN(),")
            else:
                lines.append(f"         {cpp_float(float(value))},")
        lines.extend(
            [
                "     }},",
                "     {",
                f"         {cpp_float(result['primary_bad12_logit'])},",
                f"         {cpp_float(result['primary_bad12_probability'])},",
                f"         {cpp_float(result['treated_bad12_logit'])},",
                f"         {cpp_float(result['treated_bad12_probability'])},",
                f"         {cpp_float(result['predicted_log_airtime'])},",
                f"         {cpp_float(result['predicted_secondary_airtime_us'])},",
                f"         {cpp_float(result['nonnegative_bad12_value'])},",
                f"         {cpp_float32(result['value_per_cost_score'])},",
                f"         {str(result['passes_score_threshold']).lower()},",
                "     },",
                "    },",
            ]
        )
    lines.extend(
        [
            "}};",
            "",
            "} // namespace temporal_t2_value_model_golden_v1",
            "} // namespace ns3",
            "",
            "#endif // TEMPORAL_T2_VALUE_MODEL_GOLDEN_V1_H",
            "",
        ]
    )
    return "\n".join(lines)


def write_or_check(path: Path, content: str, check: bool) -> None:
    """Write generated text or fail when the checked copy is stale."""

    if check:
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            raise TemporalT2ExportError(f"generated file is stale: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    root = Path(__file__).resolve().parents[1]
    campaign = root / "results/randomized_full_copy_exploration_collection_v1"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=campaign / "temporal_t2_primary_only_two_objective_v1",
    )
    parser.add_argument(
        "--selection",
        type=Path,
        default=root
        / "experiments/model-selection/temporal-t2-primary-only-two-objective-v1.json",
    )
    parser.add_argument(
        "--dataset-dir", type=Path, default=campaign / "temporal_dataset"
    )
    parser.add_argument(
        "--model-output",
        type=Path,
        default=root
        / "contrib/wifi-streaming/model/temporal-t2-value-model-data-v1.cc",
    )
    parser.add_argument(
        "--golden-output",
        type=Path,
        default=root
        / "contrib/wifi-streaming/test/temporal-t2-value-model-golden-v1.h",
    )
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate, extract, and generate the frozen compiled policy."""

    args = parse_args(argv)
    source = validate_source(args.artifact_dir, args.selection, args.dataset_dir)
    payload = export_payload(source)
    digests = _component_digests(source, payload)
    cases = golden_inputs(source)
    write_or_check(
        args.model_output,
        emit_model_source(source, payload, digests),
        args.check,
    )
    write_or_check(
        args.golden_output,
        emit_golden_header(source, digests, cases),
        args.check,
    )
    print(
        json.dumps(
            {
                "feature_count": len(source.feature_names),
                "golden_case_count": len(cases),
                "source_model_sha256": digests["source_model"],
                "plain_model_sha256": digests["plain_model"],
                "score_threshold_float32": source.bundle["selected_policy"][
                    "score_threshold"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, TemporalT2ExportError) as error:
        raise SystemExit(f"error: {error}") from error
