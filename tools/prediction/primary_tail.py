"""Frozen two-head T4 predictor artifact for primary-copy tail risk."""

from __future__ import annotations

import math
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from prediction.online_replay import FrozenPredictor

PRIMARY_TAIL_BUNDLE_SCHEMA_VERSION = 2
PRIMARY_TAIL_HEADS = ("primary_miss", "completed_tail")


@dataclass
class PrimaryTailT4Bundle:
    """Trusted local artifact for the action-neutral two-head T4 model."""

    schema_version: int
    artifact_id: str
    model_id: str
    dataset_sha256: str
    dataset_manifest_sha256: str
    dataset_validation_sha256: str
    training_config_sha256: str
    primary_link: int
    pipeline_id: str
    stage: str
    heads: dict[str, FrozenPredictor]
    target_ids: dict[str, str]
    tail_threshold_us: int
    miss_weight: float
    tail_weight: float
    score_normalization: float
    score_name: str
    score_kind: str
    combiner: str
    evidence_status: str

    def predict(self, matrix: np.ndarray) -> dict[str, np.ndarray]:
        """Return both calibrated probabilities and the combined admission score."""
        miss = self.heads["primary_miss"].predict(matrix)[1]
        tail = self.heads["completed_tail"].predict(matrix)[1]
        combined = (
            self.miss_weight * miss + self.tail_weight * tail
        ) / self.score_normalization
        return {
            "primary_miss_calibrated_probability": miss,
            "completed_tail_calibrated_probability": tail,
            "admission_score": combined,
        }


def validate_primary_tail_bundle(bundle: PrimaryTailT4Bundle) -> None:
    """Validate the immutable runtime-facing contract of one bundle."""
    if not isinstance(bundle, PrimaryTailT4Bundle):
        raise ValueError("primary-tail bundle has the wrong Python type")
    if bundle.schema_version != PRIMARY_TAIL_BUNDLE_SCHEMA_VERSION:
        raise ValueError("unsupported primary-tail bundle schema")
    if bundle.primary_link != 1 or bundle.stage != "T4":
        raise ValueError("primary-tail bundle must target path 1 at T4")
    if any(
        len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest)
        for digest in (
            bundle.dataset_sha256,
            bundle.dataset_manifest_sha256,
            bundle.dataset_validation_sha256,
            bundle.training_config_sha256,
        )
    ):
        raise ValueError("primary-tail bundle has an invalid provenance digest")
    if tuple(sorted(bundle.heads)) != tuple(sorted(PRIMARY_TAIL_HEADS)):
        raise ValueError("primary-tail bundle has the wrong head set")
    if set(bundle.target_ids) != set(PRIMARY_TAIL_HEADS):
        raise ValueError("primary-tail bundle has the wrong target-ID set")
    miss = bundle.heads["primary_miss"]
    tail = bundle.heads["completed_tail"]
    for head in (miss, tail):
        if head.pipeline_id != bundle.pipeline_id or head.stage != bundle.stage:
            raise ValueError("primary-tail head identity differs from its bundle")
    if (
        miss.feature_names != tail.feature_names
        or miss.f1_feature_names != tail.f1_feature_names
        or miss.feature_set != tail.feature_set
        or miss.degradation_profile != tail.degradation_profile
    ):
        raise ValueError("primary-tail heads have different feature contracts")
    if bundle.tail_threshold_us <= 0:
        raise ValueError("primary-tail latency threshold must be positive")
    weights = (bundle.miss_weight, bundle.tail_weight, bundle.score_normalization)
    if not all(math.isfinite(value) and value > 0 for value in weights):
        raise ValueError("primary-tail combiner weights must be finite and positive")
    if not math.isclose(
        bundle.score_normalization,
        bundle.miss_weight + bundle.tail_weight,
        rel_tol=0,
        abs_tol=1e-15,
    ):
        raise ValueError("primary-tail score normalization differs from its weights")
    if (
        bundle.score_name != "admission_score"
        or bundle.score_kind != "weighted_head_probability_admission_score"
        or bundle.combiner != "weighted_arithmetic_mean"
    ):
        raise ValueError("primary-tail admission-score contract is invalid")


def write_primary_tail_bundle(path: Path, bundle: PrimaryTailT4Bundle) -> None:
    """Serialize a validated trusted-local primary-tail artifact."""
    validate_primary_tail_bundle(bundle)
    with path.open("wb") as output:
        pickle.dump(bundle, output, protocol=pickle.HIGHEST_PROTOCOL)


def read_primary_tail_bundle(path: Path) -> PrimaryTailT4Bundle:
    """Read and validate a trusted-local primary-tail artifact."""
    with path.open("rb") as source:
        value = pickle.load(source)
    validate_primary_tail_bundle(value)
    return value
