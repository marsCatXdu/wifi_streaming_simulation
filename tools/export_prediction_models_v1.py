#!/usr/bin/env python3
"""Export the version-1 commodity polling predictors as deterministic C++ data."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from prediction.online_replay import read_model_bundle

EXPORT_SCHEMA_VERSION = 1
PIPELINE_ID = "commodity_polling_1ms"
STAGES = ("T0", "T1", "T2", "T4")


def sha256(path: Path) -> str:
    """Return the hexadecimal SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cpp_float(value: float) -> str:
    """Format a finite double as a round-trip-safe C++ literal."""
    value = float(value)
    if not np.isfinite(value):
        raise ValueError(f"model parameter is not finite: {value}")
    text = format(value, ".17g")
    if "." not in text and "e" not in text:
        text += ".0"
    return text


def fitted_transforms(predictor: Any) -> list[tuple[str, int, float, float]]:
    """Flatten the fitted ColumnTransformer into scalar C++ operations."""
    preprocessing = predictor.pipeline.named_steps["preprocess"]
    result: list[tuple[str, int, float, float]] = []
    for name, transformer, columns in preprocessing.transformers_:
        if name == "remainder":
            continue
        columns = [int(index) for index in columns]
        imputer = transformer.named_steps["impute"]
        statistics = np.asarray(imputer.statistics_, dtype=np.float64)
        indicators = [int(index) for index in imputer.indicator_.features_]
        if name == "numeric":
            if len(statistics) != len(columns):
                raise ValueError("unsupported numeric imputer shape")
            result.extend(
                ("IMPUTED_VALUE", column, float(statistic), 0.0)
                for column, statistic in zip(columns, statistics, strict=True)
            )
            result.extend(
                ("MISSING_INDICATOR", columns[index], 0.0, 0.0) for index in indicators
            )
            continue
        if name != "categorical":
            raise ValueError(f"unsupported preprocessing transformer: {name}")
        encoder = transformer.named_steps["encode"]
        imputed_columns: list[tuple[str, int, float]] = [
            ("ONE_HOT_VALUE", column, float(statistic))
            for column, statistic in zip(columns, statistics, strict=True)
        ]
        imputed_columns.extend(
            ("ONE_HOT_MISSING_STATUS", columns[index], 0.0) for index in indicators
        )
        if len(imputed_columns) != len(encoder.categories_):
            raise ValueError("unsupported categorical imputer/encoder shape")
        for (kind, column, replacement), categories in zip(
            imputed_columns, encoder.categories_, strict=True
        ):
            result.extend(
                (kind, column, replacement, float(category)) for category in categories
            )
    model = predictor.pipeline.named_steps["model"]
    if len(result) != int(model.n_features_in_):
        raise ValueError("exported preprocessing width differs from fitted model")
    return result


def predictor_data(predictor: Any) -> dict[str, Any]:
    """Extract one supported sklearn predictor into stable plain data."""
    if predictor.pipeline_id != PIPELINE_ID:
        raise ValueError("attempted to export a non-commodity predictor")
    if predictor.model_name != "histogram_gradient_boosting":
        raise ValueError("commodity predictor is not histogram gradient boosting")
    model = predictor.pipeline.named_steps["model"]
    trees: list[tuple[int, int]] = []
    nodes: list[tuple[float, float, int, int, int, bool, bool]] = []
    for iteration in model._predictors:
        if len(iteration) != 1:
            raise ValueError("only binary histogram-gradient-boosting models are supported")
        tree = iteration[0]
        offset = len(nodes)
        for node in tree.nodes:
            if bool(node["is_categorical"]):
                raise ValueError("categorical histogram splits are not supported")
            nodes.append(
                (
                    float(node["value"]),
                    float(node["num_threshold"]),
                    int(node["feature_idx"]),
                    int(node["left"]),
                    int(node["right"]),
                    bool(node["missing_go_to_left"]),
                    bool(node["is_leaf"]),
                )
            )
        trees.append((offset, len(tree.nodes)))
    calibrator = predictor.calibrator.model
    return {
        "transforms": fitted_transforms(predictor),
        "nodes": nodes,
        "trees": trees,
        "baseline": float(np.asarray(model._baseline_prediction).reshape(-1)[0]),
        "platt_coefficient": float(calibrator.coef_[0, 0]),
        "platt_intercept": float(calibrator.intercept_[0]),
    }


def emit_model_source(
    predictors: dict[str, Any],
    feature_names: tuple[str, ...],
    model_digest: str,
) -> str:
    """Render the generated C++ model-data translation unit."""
    lines = [
        "/*",
        " * SPDX-License-Identifier: GPL-2.0-only",
        " *",
        " * Generated by tools/export_prediction_models_v1.py.",
        f" * Export schema: {EXPORT_SCHEMA_VERSION}",
        f" * Source model SHA-256: {model_digest}",
        " */",
        "",
        '#include "prediction-model-data-v1.h"',
        "",
        "#include <array>",
        "#include <stdexcept>",
        "",
        "namespace ns3",
        "{",
        "namespace prediction_model_v1",
        "{",
        "namespace",
        "{",
        f"constexpr std::array<std::string_view, {len(feature_names)}> g_featureNames{{{{",
    ]
    lines.extend(f'    "{name}",' for name in feature_names)
    lines.extend(["}};", ""])
    for stage in STAGES:
        data = predictors[stage]
        lower = stage.lower()
        lines.append(
            f"constexpr std::array<Transform, {len(data['transforms'])}> g_{lower}Transforms{{{{"
        )
        lines.extend(
            "    {TransformKind::%s, %d, %s, %s},"
            % (kind, raw, cpp_float(replacement), cpp_float(category))
            for kind, raw, replacement, category in data["transforms"]
        )
        lines.extend(["}};", ""])
        lines.append(f"constexpr std::array<Node, {len(data['nodes'])}> g_{lower}Nodes{{{{")
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
            for value, threshold, feature, left, right, missing_left, leaf in data["nodes"]
        )
        lines.extend(["}};", ""])
        lines.append(f"constexpr std::array<Tree, {len(data['trees'])}> g_{lower}Trees{{{{")
        lines.extend(f"    {{{offset}, {count}}}," for offset, count in data["trees"])
        lines.extend(["}};", ""])
        lines.extend(
            [
                f"const Predictor g_{lower}Predictor{{",
                f"    PredictionStage::{stage},",
                f"    g_{lower}Transforms,",
                f"    g_{lower}Nodes,",
                f"    g_{lower}Trees,",
                f"    {cpp_float(data['baseline'])},",
                f"    {cpp_float(data['platt_coefficient'])},",
                f"    {cpp_float(data['platt_intercept'])},",
                "};",
                "",
            ]
        )
    lines.extend(
        [
            "} // namespace",
            "",
            "std::span<const std::string_view>",
            "GetFeatureNames()",
            "{",
            "    return g_featureNames;",
            "}",
            "",
            "const Predictor&",
            "GetPredictor(PredictionStage stage)",
            "{",
            "    switch (stage)",
            "    {",
        ]
    )
    for stage in STAGES:
        lines.extend(
            [
                f"    case PredictionStage::{stage}:",
                f"        return g_{stage.lower()}Predictor;",
            ]
        )
    lines.extend(
        [
            "    }",
            '    throw std::invalid_argument("unknown prediction stage");',
            "}",
            "",
            "} // namespace prediction_model_v1",
            "} // namespace ns3",
            "",
        ]
    )
    return "\n".join(lines)


def golden_inputs(feature_count: int) -> list[np.ndarray]:
    """Build deterministic vectors that exercise imputation and categorical encoding."""
    zeros = np.zeros(feature_count, dtype=np.float32)
    missing = np.full(feature_count, np.nan, dtype=np.float32)
    ramp = np.asarray(
        [((index % 17) - 8) * (index + 1) / 7 for index in range(feature_count)],
        dtype=np.float32,
    )
    rng = np.random.default_rng(20260727)
    random = rng.normal(100.0, 1500.0, feature_count).astype(np.float32)
    patterned = np.asarray(
        [(index % 5) * 10.0 if index % 9 else np.nan for index in range(feature_count)],
        dtype=np.float32,
    )
    values = [zeros, missing, ramp, random, patterned]
    for index, vector in enumerate(values):
        vector[5] = np.float32([0.0, np.nan, 1.0, -1.0, 0.0][index])
        vector[17] = np.float32([1.0, np.nan, 1.0, -1.0, 0.0][index])
    return values


def emit_golden_header(
    predictors: dict[str, Any],
    feature_names: tuple[str, ...],
    model_digest: str,
) -> str:
    """Render sklearn parity vectors for the ns-3 unit test."""
    vectors = golden_inputs(len(feature_names))
    cases: list[tuple[str, np.ndarray, float, float]] = []
    for stage in STAGES:
        predictor = predictors[stage]
        for vector in vectors:
            score, probability = predictor.predict(vector.reshape(1, -1))
            cases.append((stage, vector, float(score[0]), float(probability[0])))
    lines = [
        "/*",
        " * SPDX-License-Identifier: GPL-2.0-only",
        " *",
        " * Generated by tools/export_prediction_models_v1.py.",
        f" * Source model SHA-256: {model_digest}",
        " */",
        "",
        "#ifndef PREDICTION_MODEL_GOLDEN_V1_H",
        "#define PREDICTION_MODEL_GOLDEN_V1_H",
        "",
        '#include "ns3/prediction-model-evaluator.h"',
        "",
        "#include <array>",
        "#include <limits>",
        "",
        "namespace ns3",
        "{",
        "namespace prediction_model_golden_v1",
        "{",
        "",
        "struct GoldenCase",
        "{",
        "    PredictionStage stage;",
        f"    std::array<double, {len(feature_names)}> features;",
        "    double rankingScore;",
        "    double calibratedProbability;",
        "};",
        "",
        f"inline const std::array<GoldenCase, {len(cases)}> g_cases{{{{",
    ]
    for stage, vector, score, probability in cases:
        lines.extend([f"    {{PredictionStage::{stage},", "     {{"])
        for value in vector:
            if np.isnan(value):
                lines.append("         std::numeric_limits<double>::quiet_NaN(),")
            else:
                lines.append(f"         {cpp_float(float(value))},")
        lines.extend(
            [
                "     }},",
                f"     {cpp_float(score)},",
                f"     {cpp_float(probability)}}},",
            ]
        )
    lines.extend(
        [
            "}};",
            "",
            "} // namespace prediction_model_golden_v1",
            "} // namespace ns3",
            "",
            "#endif // PREDICTION_MODEL_GOLDEN_V1_H",
            "",
        ]
    )
    return "\n".join(lines)


def write_or_check(path: Path, content: str, check: bool) -> None:
    """Write generated content, or verify that an existing file is current."""
    if check:
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            raise ValueError(f"generated file is stale: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def main() -> int:
    """Export all and only the four accepted commodity-polling predictors."""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle",
        type=Path,
        default=root / "results/prediction_online_models_v1/model_bundle.pkl",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=root / "results/prediction_online_models_v1/model_bundle_manifest.json",
    )
    parser.add_argument(
        "--model-output",
        type=Path,
        default=root
        / "contrib/wifi-streaming/model/prediction-model-data-v1.cc",
    )
    parser.add_argument(
        "--golden-output",
        type=Path,
        default=root
        / "contrib/wifi-streaming/test/prediction-model-golden-v1.h",
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    model_digest = sha256(args.bundle)
    if manifest.get("model_bundle_schema_version") != 1:
        raise ValueError("unsupported input model bundle schema")
    if manifest.get("model_sha256") != model_digest:
        raise ValueError("model bundle checksum does not match its manifest")
    bundle = read_model_bundle(args.bundle)
    selected = {
        stage: bundle.predictors[(PIPELINE_ID, stage)]
        for stage in STAGES
    }
    if len(selected) != 4:
        raise AssertionError("export must select exactly four predictors")
    feature_names = selected["T0"].feature_names
    if any(predictor.feature_names != feature_names for predictor in selected.values()):
        raise ValueError("commodity predictors do not share one feature contract")
    extracted = {stage: predictor_data(predictor) for stage, predictor in selected.items()}
    write_or_check(
        args.model_output,
        emit_model_source(extracted, feature_names, model_digest),
        args.check,
    )
    write_or_check(
        args.golden_output,
        emit_golden_header(selected, feature_names, model_digest),
        args.check,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
