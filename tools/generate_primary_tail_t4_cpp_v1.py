#!/usr/bin/env python3
"""Generate the compiled two-head T4 evaluator data and parity vectors."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from export_prediction_models_v1 import predictor_data
from prediction.primary_tail import read_primary_tail_bundle

EXPORT_SCHEMA_VERSION = 2
EXPECTED_FEATURE_COUNT = 101
MAX_TRANSFORM_COUNT = 256
HEAD_NAMES = ("primary_miss", "completed_tail")
HEAD_PREFIXES = {
    "primary_miss": "primaryMiss",
    "completed_tail": "completedTail",
}
IDENTIFIER_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,127}")
ARTIFACT_ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,127}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")

RUNTIME_ADAPTER_SEMANTICS = {
    "model_raw_matrix_dtype": "float32",
    "missing_value": "NaN",
    "category_encodings": {
        "frame_type": {
            "I_FRAME": 0.0,
            "P_FRAME": 1.0,
            "B_FRAME": 2.0,
            "UNKNOWN": -1.0,
        },
        "frequency_band": {
            "2.4GHz": 0.0,
            "5GHz": 1.0,
            "6GHz": 2.0,
            "unknown": -1.0,
            "missing": "NaN",
        },
    },
    "age_conversion": {
        "formula": "(reference_time_ns - event_time_ns) / 1000.0",
        "references": {
            "last_attempt_age_us": "polling_report.capture_time_ns",
            "last_positive_ack_age_us": "polling_report.capture_time_ns",
            "queue_oldest_age_us": "prediction_sample.sample_time_ns",
        },
        "missing_event_time": "NaN",
        "future_event_time": "abort",
    },
    "polling_window_resolution": {
        "input_order": "arbitrary",
        "lookup_key": "window_us",
        "missing_polling_report": "all_F1_NaN",
        "missing_required_window": "abort",
        "required_windows_us": [1000, 5000, 20000],
        "feature_order_us": [1000, 20000, 5000],
    },
    "quantization": {
        "operation": "finite_value_float32_cast",
        "integer_prerounding": "none",
        "timing": "after_complete_feature_assembly",
    },
}


def canonical_file_bytes(value: Any) -> bytes:
    """Encode one canonical JSON file, including its trailing newline."""
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode()


def canonical_sha256(value: Any) -> str:
    """Hash one canonical JSON value without file framing."""
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    """Hash one file without loading it all into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    """Read a JSON object."""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return value


def cpp_float(value: float) -> str:
    """Format one finite double as a round-trip-safe C++ literal."""
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"model parameter is not finite: {value}")
    text = format(value, ".17g")
    if "." not in text and "e" not in text:
        text += ".0"
    return text


def cpp_string(value: str) -> str:
    """Encode one ASCII artifact string as a C++ string literal."""
    if not isinstance(value, str) or not value.isascii():
        raise ValueError("compiled artifact strings must be ASCII")
    return json.dumps(value, ensure_ascii=True)


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")
    return value


def _artifact_id(value: Any) -> str:
    if not isinstance(value, str) or ARTIFACT_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid artifact_id")
    return value


def _sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"invalid {name}")
    return value


def _validate_feature_names(value: Any, name: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) != EXPECTED_FEATURE_COUNT
        or any(
            not isinstance(item, str) or not item or not item.isascii()
            for item in value
        )
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"invalid {name}")
    return tuple(value)


def _validate_head(value: Any, feature_count: int, name: str) -> dict[str, Any]:
    """Validate one plain exported HGB head."""
    if not isinstance(value, dict) or set(value) != {
        "transforms",
        "nodes",
        "trees",
        "baseline",
        "platt_coefficient",
        "platt_intercept",
    }:
        raise ValueError(f"{name}: invalid exported-head keys")
    transforms = value["transforms"]
    nodes = value["nodes"]
    trees = value["trees"]
    if (
        not isinstance(transforms, list)
        or not transforms
        or len(transforms) > MAX_TRANSFORM_COUNT
        or not isinstance(nodes, list)
        or not nodes
        or not isinstance(trees, list)
        or len(trees) != 120
    ):
        raise ValueError(f"{name}: invalid exported-head shape")
    kinds = {
        "IMPUTED_VALUE",
        "MISSING_INDICATOR",
        "ONE_HOT_VALUE",
        "ONE_HOT_MISSING_STATUS",
    }
    for transform in transforms:
        if (
            not isinstance(transform, list)
            or len(transform) != 4
            or transform[0] not in kinds
            or type(transform[1]) is not int
            or not 0 <= transform[1] < feature_count
            or not all(math.isfinite(float(item)) for item in transform[2:])
        ):
            raise ValueError(f"{name}: invalid preprocessing transform")
    for node in nodes:
        if (
            not isinstance(node, list)
            or len(node) != 7
            or not all(math.isfinite(float(item)) for item in node[:2])
            or any(type(node[index]) is not int for index in (2, 3, 4))
            or not 0 <= node[2] < len(transforms)
            or not 0 <= node[3] <= 65535
            or not 0 <= node[4] <= 65535
            or type(node[5]) is not bool
            or type(node[6]) is not bool
        ):
            raise ValueError(f"{name}: invalid tree node")
    expected_offset = 0
    for tree in trees:
        if (
            not isinstance(tree, list)
            or len(tree) != 2
            or any(type(item) is not int for item in tree)
            or tree[0] != expected_offset
            or not 0 < tree[1] <= 65535
            or tree[0] + tree[1] > len(nodes)
        ):
            raise ValueError(f"{name}: invalid tree location")
        for node in nodes[tree[0] : tree[0] + tree[1]]:
            if not node[6] and (node[3] >= tree[1] or node[4] >= tree[1]):
                raise ValueError(f"{name}: tree child escapes its tree")
        expected_offset += tree[1]
    if expected_offset != len(nodes) or not all(
        math.isfinite(float(value[key]))
        for key in ("baseline", "platt_coefficient", "platt_intercept")
    ):
        raise ValueError(f"{name}: invalid score parameters")
    return value


def validate_artifacts(
    export_path: Path,
    export_manifest: dict[str, Any],
    source_manifest: dict[str, Any],
    bundle_path: Path,
) -> tuple[dict[str, Any], Any, dict[str, str], dict[str, str]]:
    """Cross-check the plain export, training manifest, and source bundle."""
    payload = read_object(export_path)
    export_digest = sha256_file(export_path)
    if (
        export_manifest.get("primary_tail_export_schema_version")
        != EXPORT_SCHEMA_VERSION
        or export_manifest.get("format") != "canonical_json_plain_hgb_v1"
        or export_manifest.get("export_file") != export_path.name
        or export_manifest.get("export_sha256") != export_digest
        or hashlib.sha256(canonical_file_bytes(payload)).hexdigest() != export_digest
        or export_manifest.get("feature_count") != EXPECTED_FEATURE_COUNT
        or export_manifest.get("heads") != list(HEAD_NAMES)
    ):
        raise ValueError("plain export differs from its manifest")
    if payload.get("primary_tail_export_schema_version") != EXPORT_SCHEMA_VERSION:
        raise ValueError("unsupported primary-tail export schema")

    artifact_id = _artifact_id(payload.get("artifact_id"))
    model_id = _identifier(payload.get("model_id"), "model_id")
    pipeline_id = _identifier(payload.get("pipeline_id"), "pipeline_id")
    if (
        artifact_id != source_manifest.get("artifact_id")
        or model_id != source_manifest.get("model_id")
        or model_id != export_manifest.get("model_id")
        or artifact_id != export_manifest.get("artifact_id")
        or pipeline_id != "exportable_driver_polling_1ms"
        or payload.get("stage") != "T4"
        or payload.get("primary_link") != 1
    ):
        raise ValueError("compiled export has the wrong model identity")
    source_digest = _sha256(payload.get("source_model_sha256"), "source model SHA-256")
    if (
        source_digest != source_manifest.get("model_sha256")
        or source_digest != export_manifest.get("source_model_sha256")
        or source_digest != sha256_file(bundle_path)
    ):
        raise ValueError("compiled export has the wrong source-model digest")
    dataset_digests = {}
    for key in (
        "dataset_sha256",
        "dataset_manifest_sha256",
        "dataset_validation_sha256",
    ):
        dataset_digests[key] = _sha256(payload.get(key), key.replace("_", " "))
        if dataset_digests[key] != source_manifest.get(key):
            raise ValueError(f"compiled export has the wrong {key}")

    feature_names = _validate_feature_names(
        payload.get("feature_names"), "feature names"
    )
    physical_names = _validate_feature_names(
        payload.get("physical_feature_names"), "physical feature names"
    )
    targets = payload.get("target_ids")
    if not isinstance(targets, dict) or set(targets) != set(HEAD_NAMES):
        raise ValueError("compiled export has the wrong target head set")
    for name in HEAD_NAMES:
        if (
            _identifier(targets[name], f"{name} target ID")
            != source_manifest.get("heads", {}).get(name, {}).get("target_id")
        ):
            raise ValueError(f"compiled export has the wrong {name} target")
    heads = payload.get("heads")
    if not isinstance(heads, dict) or set(heads) != set(HEAD_NAMES):
        raise ValueError("compiled export has the wrong model head set")
    for name in HEAD_NAMES:
        _validate_head(heads[name], len(feature_names), name)

    combiner = payload.get("combiner")
    expected_combiner = {
        "output_name": "admission_score",
        "score_kind": "weighted_head_probability_admission_score",
        "combiner": "weighted_arithmetic_mean",
        "primary_miss_weight": 1.0,
        "completed_tail_weight": 0.2,
        "normalization": 1.2,
    }
    if combiner != expected_combiner:
        raise ValueError("compiled export has the wrong admission-score combiner")
    if (
        type(payload.get("tail_threshold_us")) is not int
        or payload["tail_threshold_us"] <= 0
    ):
        raise ValueError("compiled export has an invalid tail threshold")

    target_provenance_digest = _sha256(
        export_manifest.get("target_provenance_sha256"),
        "target provenance SHA-256",
    )
    target_provenance = source_manifest.get("target_provenance")
    if (
        not isinstance(target_provenance, dict)
        or target_provenance_digest
        != source_manifest.get("target_provenance_sha256")
        or target_provenance_digest != canonical_sha256(target_provenance)
    ):
        raise ValueError("compiled export has the wrong target-provenance digest")
    if payload.get("evidence_status") != source_manifest.get("evidence_status"):
        raise ValueError("compiled export has the wrong evidence status")

    bundle = read_primary_tail_bundle(bundle_path)
    if (
        bundle.artifact_id != artifact_id
        or bundle.model_id != model_id
        or bundle.pipeline_id != pipeline_id
        or bundle.stage != "T4"
        or set(bundle.heads) != set(HEAD_NAMES)
        or tuple(bundle.heads["primary_miss"].feature_names) != feature_names
        or bundle.target_ids != targets
    ):
        raise ValueError("source bundle differs from the compiled export")
    miss_predictor = bundle.heads["primary_miss"]
    tail_predictor = bundle.heads["completed_tail"]
    if (
        miss_predictor.feature_set != "F0+F1-degraded+F2-exportable"
        or miss_predictor.feature_set != tail_predictor.feature_set
        or miss_predictor.f1_feature_names != tail_predictor.f1_feature_names
        or miss_predictor.degradation_profile != tail_predictor.degradation_profile
        or not isinstance(miss_predictor.degradation_profile, dict)
        or miss_predictor.degradation_profile.get("profile_id") != "polling_1ms"
    ):
        raise ValueError("source bundle has the wrong exportable feature contract")
    for name in HEAD_NAMES:
        extracted = predictor_data(
            bundle.heads[name], expected_pipeline_id=bundle.pipeline_id
        )
        if canonical_sha256(extracted) != canonical_sha256(heads[name]):
            raise ValueError(f"plain {name} model data differs from the source bundle")

    feature_contract = {
        "feature_names": list(feature_names),
        "physical_feature_names": list(physical_names),
        "f1_feature_names": list(miss_predictor.f1_feature_names),
        "feature_set": miss_predictor.feature_set,
        "degradation_profile": miss_predictor.degradation_profile,
        "pipeline_id": pipeline_id,
        "stage": "T4",
        "runtime_adapter_semantics": RUNTIME_ADAPTER_SEMANTICS,
    }
    digests = {
        "export": export_digest,
        "target_provenance": target_provenance_digest,
        "feature_contract": canonical_sha256(feature_contract),
        "combiner": canonical_sha256(combiner),
        "primary_miss_model": canonical_sha256(heads["primary_miss"]),
        "completed_tail_model": canonical_sha256(heads["completed_tail"]),
    }
    feature_identity = {
        "feature_set": miss_predictor.feature_set,
        "degradation_profile": miss_predictor.degradation_profile["profile_id"],
    }
    return payload, bundle, digests, feature_identity


def apply_transform(transform: list[Any], features: np.ndarray) -> float:
    """Apply one exported transform in Python for export self-validation."""
    kind, raw_index, replacement, category = transform
    raw = float(features[raw_index])
    missing = math.isnan(raw)
    if kind == "IMPUTED_VALUE":
        return float(replacement) if missing else raw
    if kind == "MISSING_INDICATOR":
        return 1.0 if missing else 0.0
    if kind == "ONE_HOT_VALUE":
        return 1.0 if (float(replacement) if missing else raw) == category else 0.0
    if kind == "ONE_HOT_MISSING_STATUS":
        return 1.0 if (1.0 if missing else 0.0) == category else 0.0
    raise ValueError(f"unknown transform kind: {kind}")


def evaluate_exported_head(
    head: dict[str, Any], features: np.ndarray
) -> tuple[float, float]:
    """Evaluate one plain exported head independently of sklearn."""
    transformed = np.asarray(
        [apply_transform(transform, features) for transform in head["transforms"]],
        dtype=np.float64,
    )
    score = float(head["baseline"])
    for offset, count in head["trees"]:
        index = 0
        while True:
            if index >= count:
                raise ValueError("exported tree has an invalid child index")
            value, threshold, feature, left, right, missing_left, leaf = head["nodes"][
                offset + index
            ]
            if leaf:
                score += float(value)
                break
            observed = float(transformed[feature])
            index = (
                (left if missing_left else right)
                if math.isnan(observed)
                else (left if observed <= threshold else right)
            )
    argument = float(head["platt_coefficient"]) * score + float(head["platt_intercept"])
    if argument >= 0:
        probability = 1.0 / (1.0 + math.exp(-argument))
    else:
        exponential = math.exp(argument)
        probability = exponential / (1.0 + exponential)
    return score, probability


def golden_inputs(payload: dict[str, Any]) -> list[np.ndarray]:
    """Build deterministic inputs that cover missing and categorical transforms."""
    count = len(payload["feature_names"])
    zeros = np.zeros(count, dtype=np.float64)
    missing = np.full(count, np.nan, dtype=np.float64)
    ramp = np.asarray(
        [((index % 17) - 8) * (index + 1) / 7 for index in range(count)],
        dtype=np.float64,
    )
    deterministic = np.asarray(
        [
            (((index * 7919) % 1009) - 504) * (1 + index % 11) / 13
            for index in range(count)
        ],
        dtype=np.float64,
    )
    patterned = np.asarray(
        [(index % 5) * 10.0 if index % 9 else np.nan for index in range(count)],
        dtype=np.float64,
    )
    extremes = np.asarray(
        [(-1.0 if index % 2 else 1.0) * 10.0 ** (index % 7) for index in range(count)],
        dtype=np.float64,
    )
    category_probe = np.asarray(
        [((index * 31) % 23) - 11 for index in range(count)], dtype=np.float64
    )
    categories: dict[int, list[float]] = {}
    for head in payload["heads"].values():
        for kind, raw, _, category in head["transforms"]:
            if kind in {"ONE_HOT_VALUE", "ONE_HOT_MISSING_STATUS"}:
                categories.setdefault(int(raw), []).append(float(category))
    for raw, values in categories.items():
        unique = sorted(set(values))
        zeros[raw] = unique[0]
        ramp[raw] = unique[-1]
        deterministic[raw] = unique[len(unique) // 2]
        patterned[raw] = -987654.0
        extremes[raw] = unique[0]
        category_probe[raw] = unique[-1]
    return [zeros, missing, ramp, deterministic, patterned, extremes, category_probe]


def golden_results(
    payload: dict[str, Any], bundle: Any
) -> list[tuple[np.ndarray, dict[str, float]]]:
    """Verify plain-tree/sklearn parity and return sklearn golden results."""
    result = []
    combiner = payload["combiner"]
    for vector in golden_inputs(payload):
        expected: dict[str, float] = {}
        for name in HEAD_NAMES:
            score, probability = bundle.heads[name].predict(vector.reshape(1, -1))
            plain_score, plain_probability = evaluate_exported_head(
                payload["heads"][name], vector
            )
            if not np.isclose(float(score[0]), plain_score, rtol=0, atol=1e-11):
                raise ValueError(f"{name} plain-tree score differs from sklearn")
            if not np.isclose(
                float(probability[0]), plain_probability, rtol=0, atol=1e-12
            ):
                raise ValueError(f"{name} plain-tree calibration differs from sklearn")
            expected[f"{name}_score"] = float(score[0])
            expected[f"{name}_probability"] = float(probability[0])
        admission = (
            combiner["primary_miss_weight"] * expected["primary_miss_probability"]
            + combiner["completed_tail_weight"]
            * expected["completed_tail_probability"]
        ) / combiner["normalization"]
        expected["admission_score"] = min(max(float(admission), 0.0), 1.0)
        result.append((vector, expected))
    return result


def emit_head_arrays(lines: list[str], name: str, head: dict[str, Any]) -> None:
    """Append one head's fitted arrays and Predictor object."""
    prefix = HEAD_PREFIXES[name]
    transforms = head["transforms"]
    nodes = head["nodes"]
    trees = head["trees"]
    lines.append(
        f"constexpr std::array<Transform, {len(transforms)}> "
        f"g_{prefix}Transforms{{{{"
    )
    lines.extend(
        "    {TransformKind::%s, %d, %s, %s},"
        % (kind, raw, cpp_float(replacement), cpp_float(category))
        for kind, raw, replacement, category in transforms
    )
    lines.extend(["}};", ""])
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
            f"const Predictor g_{prefix}Predictor{{",
            f"    g_{prefix}Transforms,",
            f"    g_{prefix}Nodes,",
            f"    g_{prefix}Trees,",
            f"    {cpp_float(head['baseline'])},",
            f"    {cpp_float(head['platt_coefficient'])},",
            f"    {cpp_float(head['platt_intercept'])},",
            "};",
            "",
        ]
    )


def emit_model_source(
    payload: dict[str, Any],
    digests: dict[str, str],
    feature_identity: dict[str, str],
) -> str:
    """Render the generated C++ model-data translation unit."""
    provenance_values = [
        payload["artifact_id"],
        payload["model_id"],
        payload["evidence_status"],
        payload["pipeline_id"],
        payload["stage"],
        feature_identity["feature_set"],
        feature_identity["degradation_profile"],
        payload["target_ids"]["primary_miss"],
        payload["target_ids"]["completed_tail"],
        payload["source_model_sha256"],
        payload["dataset_sha256"],
        payload["dataset_manifest_sha256"],
        payload["dataset_validation_sha256"],
        digests["export"],
        digests["target_provenance"],
        digests["feature_contract"],
        digests["combiner"],
        digests["primary_miss_model"],
        digests["completed_tail_model"],
    ]
    combiner = payload["combiner"]
    lines = [
        "/*",
        " * SPDX-License-Identifier: GPL-2.0-only",
        " *",
        " * Generated by tools/generate_primary_tail_t4_cpp_v1.py.",
        f" * Export schema: {EXPORT_SCHEMA_VERSION}",
        f" * Model ID: {payload['model_id']}",
        f" * Source model SHA-256: {payload['source_model_sha256']}",
        f" * Export SHA-256: {digests['export']}",
        " */",
        "",
        '#include "primary-tail-t4-model-data-v1.h"',
        "",
        "#include <array>",
        "",
        "namespace ns3",
        "{",
        "namespace primary_tail_t4_model_v1",
        "{",
        "namespace",
        "{",
        f"constexpr std::array<std::string_view, {len(payload['feature_names'])}> "
        "g_featureNames{{",
    ]
    lines.extend(f"    {cpp_string(name)}," for name in payload["feature_names"])
    lines.extend(["}};", ""])
    lines.append(
        "constexpr std::array<std::string_view, "
        f"{len(payload['physical_feature_names'])}> "
        "g_physicalFeatureNames{{"
    )
    lines.extend(
        f"    {cpp_string(name)}," for name in payload["physical_feature_names"]
    )
    lines.extend(["}};", ""])
    for name in HEAD_NAMES:
        emit_head_arrays(lines, name, payload["heads"][name])
    lines.extend(
        [
            "constexpr Metadata g_metadata{",
            "    {",
            *(f"        {cpp_string(value)}," for value in provenance_values),
            "    },",
            f"    {payload['tail_threshold_us']},",
            f"    {cpp_string(combiner['output_name'])},",
            f"    {cpp_string(combiner['score_kind'])},",
            f"    {cpp_string(combiner['combiner'])},",
            f"    {cpp_float(combiner['primary_miss_weight'])},",
            f"    {cpp_float(combiner['completed_tail_weight'])},",
            f"    {cpp_float(combiner['normalization'])},",
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
            "std::span<const std::string_view>",
            "GetPhysicalFeatureNames()",
            "{",
            "    return g_physicalFeatureNames;",
            "}",
            "",
            "const Metadata&",
            "GetMetadata()",
            "{",
            "    return g_metadata;",
            "}",
            "",
            "const Predictor&",
            "GetPrimaryMissPredictor()",
            "{",
            "    return g_primaryMissPredictor;",
            "}",
            "",
            "const Predictor&",
            "GetCompletedTailPredictor()",
            "{",
            "    return g_completedTailPredictor;",
            "}",
            "",
            "} // namespace primary_tail_t4_model_v1",
            "} // namespace ns3",
            "",
        ]
    )
    return "\n".join(lines)


def emit_golden_header(
    payload: dict[str, Any],
    digests: dict[str, str],
    feature_identity: dict[str, str],
    cases: list[tuple[np.ndarray, dict[str, float]]],
) -> str:
    """Render deterministic sklearn parity vectors and identity values."""
    provenance_values = [
        payload["artifact_id"],
        payload["model_id"],
        payload["evidence_status"],
        payload["pipeline_id"],
        payload["stage"],
        feature_identity["feature_set"],
        feature_identity["degradation_profile"],
        payload["target_ids"]["primary_miss"],
        payload["target_ids"]["completed_tail"],
        payload["source_model_sha256"],
        payload["dataset_sha256"],
        payload["dataset_manifest_sha256"],
        payload["dataset_validation_sha256"],
        digests["export"],
        digests["target_provenance"],
        digests["feature_contract"],
        digests["combiner"],
        digests["primary_miss_model"],
        digests["completed_tail_model"],
    ]
    combiner = payload["combiner"]
    count = len(payload["feature_names"])
    lines = [
        "/*",
        " * SPDX-License-Identifier: GPL-2.0-only",
        " *",
        " * Generated by tools/generate_primary_tail_t4_cpp_v1.py.",
        f" * Model ID: {payload['model_id']}",
        f" * Source model SHA-256: {payload['source_model_sha256']}",
        f" * Export SHA-256: {digests['export']}",
        " */",
        "",
        "#ifndef PRIMARY_TAIL_T4_MODEL_GOLDEN_V1_H",
        "#define PRIMARY_TAIL_T4_MODEL_GOLDEN_V1_H",
        "",
        '#include "ns3/primary-tail-t4-model-evaluator.h"',
        "",
        "#include <array>",
        "#include <limits>",
        "#include <string_view>",
        "",
        "namespace ns3",
        "{",
        "namespace primary_tail_t4_model_golden_v1",
        "{",
        "",
        "inline constexpr PrimaryTailT4ModelProvenance g_provenance{",
        *(f"    {cpp_string(value)}," for value in provenance_values),
        "};",
        "",
        f"inline constexpr std::array<std::string_view, {count}> g_featureNames{{{{",
        *(f"    {cpp_string(name)}," for name in payload["feature_names"]),
        "}};",
        "",
        f"inline constexpr std::array<std::string_view, {count}> "
        "g_physicalFeatureNames{{",
        *(f"    {cpp_string(name)}," for name in payload["physical_feature_names"]),
        "}};",
        "",
        "inline constexpr uint32_t g_tailThresholdUs{"
        f"{payload['tail_threshold_us']}}};",
        "inline constexpr std::string_view g_scoreName{"
        f"{cpp_string(combiner['output_name'])}}};",
        "inline constexpr std::string_view g_scoreKind{"
        f"{cpp_string(combiner['score_kind'])}}};",
        "inline constexpr std::string_view g_combiner{"
        f"{cpp_string(combiner['combiner'])}}};",
        "inline constexpr double g_primaryMissWeight{"
        f"{cpp_float(combiner['primary_miss_weight'])}}};",
        "inline constexpr double g_completedTailWeight{"
        f"{cpp_float(combiner['completed_tail_weight'])}}};",
        "inline constexpr double g_scoreNormalization{"
        f"{cpp_float(combiner['normalization'])}}};",
        "",
        "struct GoldenCase",
        "{",
        f"    std::array<double, {count}> features; ///< Raw logical features.",
        "    double primaryMissRankingScore;       ///< sklearn miss-head score.",
        "    double primaryMissProbability; ///< sklearn calibrated miss probability.",
        "    double completedTailRankingScore;     ///< sklearn tail-head score.",
        "    double completedTailProbability; ///< sklearn tail probability.",
        "    double admissionScore;                 ///< Frozen combined policy score.",
        "};",
        "",
        f"inline const std::array<GoldenCase, {len(cases)}> g_cases{{{{",
    ]
    for vector, expected in cases:
        lines.extend(["    {", "        {{"])
        for value in vector:
            if np.isnan(value):
                lines.append("            std::numeric_limits<double>::quiet_NaN(),")
            else:
                lines.append(f"            {cpp_float(float(value))},")
        lines.extend(
            [
                "        }},",
                f"        {cpp_float(expected['primary_miss_score'])},",
                f"        {cpp_float(expected['primary_miss_probability'])},",
                f"        {cpp_float(expected['completed_tail_score'])},",
                f"        {cpp_float(expected['completed_tail_probability'])},",
                f"        {cpp_float(expected['admission_score'])},",
                "    },",
            ]
        )
    lines.extend(
        [
            "}};",
            "",
            "} // namespace primary_tail_t4_model_golden_v1",
            "} // namespace ns3",
            "",
            "#endif // PRIMARY_TAIL_T4_MODEL_GOLDEN_V1_H",
            "",
        ]
    )
    return "\n".join(lines)


def write_or_check(path: Path, content: str, check: bool) -> None:
    """Write generated text, or check that the tracked copy is current."""
    if check:
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            raise ValueError(f"generated file is stale: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def parse_args() -> argparse.Namespace:
    """Parse generator arguments."""
    root = Path(__file__).resolve().parents[1]
    models = root / "results/primary_tail_t4_corrected_v1/models"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export", type=Path, default=models / "primary_tail_t4_export.json"
    )
    parser.add_argument(
        "--export-manifest",
        type=Path,
        default=models / "primary_tail_t4_export_manifest.json",
    )
    parser.add_argument(
        "--source-manifest",
        type=Path,
        default=models / "primary_tail_t4_manifest.json",
    )
    parser.add_argument(
        "--bundle", type=Path, default=models / "primary_tail_t4_bundle.pkl"
    )
    parser.add_argument(
        "--model-output",
        type=Path,
        default=root / "contrib/wifi-streaming/model/primary-tail-t4-model-data-v1.cc",
    )
    parser.add_argument(
        "--golden-output",
        type=Path,
        default=root / "contrib/wifi-streaming/test/primary-tail-t4-model-golden-v1.h",
    )
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> int:
    """Generate both deterministic C++ artifacts."""
    args = parse_args()
    export_manifest = read_object(args.export_manifest)
    source_manifest = read_object(args.source_manifest)
    payload, bundle, digests, feature_identity = validate_artifacts(
        args.export,
        export_manifest,
        source_manifest,
        args.bundle,
    )
    cases = golden_results(payload, bundle)
    write_or_check(
        args.model_output,
        emit_model_source(payload, digests, feature_identity),
        args.check,
    )
    write_or_check(
        args.golden_output,
        emit_golden_header(payload, digests, feature_identity, cases),
        args.check,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        raise SystemExit(f"error: {error}") from error
