#!/usr/bin/env python3
"""Tests for deterministic primary-tail C++ model generation."""

from __future__ import annotations

import hashlib
import json
import math
import sys
import unittest
from pathlib import Path

import numpy as np

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from generate_primary_tail_t4_cpp_v1 import (
    RUNTIME_ADAPTER_SEMANTICS,
    apply_transform,
    canonical_file_bytes,
    canonical_sha256,
    cpp_float,
    evaluate_exported_head,
)


class PrimaryTailT4CppGenerationTest(unittest.TestCase):
    """Exercise canonical hashing and the independent plain-tree evaluator."""

    def test_canonical_value_digest_excludes_file_newline(self) -> None:
        value = {"z": [2, 1], "a": "ASCII"}
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
        self.assertEqual(canonical_sha256(value), hashlib.sha256(encoded).hexdigest())
        self.assertEqual(canonical_file_bytes(value), encoded + b"\n")

    def test_runtime_adapter_semantics_are_frozen(self) -> None:
        self.assertEqual(
            canonical_sha256(RUNTIME_ADAPTER_SEMANTICS),
            "44b1120aaee77ef6c5f911cef2c85a8adb50b4001d5e3d733175230a014fc815",
        )
        self.assertEqual(
            RUNTIME_ADAPTER_SEMANTICS["quantization"]["integer_prerounding"],
            "none",
        )
        self.assertEqual(
            RUNTIME_ADAPTER_SEMANTICS["polling_window_resolution"]["feature_order_us"],
            [1000, 20000, 5000],
        )

    def test_transform_missing_and_one_hot_contract(self) -> None:
        features = np.asarray([np.nan, 3.0])
        self.assertEqual(apply_transform(["IMPUTED_VALUE", 0, 2.0, 0.0], features), 2.0)
        self.assertEqual(
            apply_transform(["MISSING_INDICATOR", 0, 0.0, 0.0], features),
            1.0,
        )
        self.assertEqual(apply_transform(["ONE_HOT_VALUE", 1, 0.0, 3.0], features), 1.0)
        self.assertEqual(
            apply_transform(["ONE_HOT_MISSING_STATUS", 0, 0.0, 1.0], features),
            1.0,
        )

    def test_plain_tree_and_platt_evaluation(self) -> None:
        head = {
            "transforms": [["IMPUTED_VALUE", 0, 1.0, 0.0]],
            "nodes": [
                [0.0, 0.5, 0, 1, 2, False, False],
                [-1.0, 0.0, 0, 0, 0, False, True],
                [2.0, 0.0, 0, 0, 0, False, True],
            ],
            "trees": [[0, 3]],
            "baseline": 0.25,
            "platt_coefficient": 2.0,
            "platt_intercept": -0.5,
        }
        score, probability = evaluate_exported_head(
            head, np.asarray([np.nan], dtype=np.float64)
        )
        self.assertEqual(score, 2.25)
        self.assertAlmostEqual(probability, 1.0 / (1.0 + math.exp(-4.0)))

    def test_cpp_float_rejects_nonfinite_model_data(self) -> None:
        with self.assertRaises(ValueError):
            cpp_float(math.inf)


if __name__ == "__main__":
    unittest.main()
