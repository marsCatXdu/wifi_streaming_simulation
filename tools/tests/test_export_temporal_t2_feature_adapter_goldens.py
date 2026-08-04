#!/usr/bin/env python3
"""Tests for the frozen temporal-T2 feature-adapter golden exporter."""

from __future__ import annotations

import copy
import contextlib
import hashlib
import io
import math
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import export_temporal_t2_feature_adapter_goldens_v1 as exporter


ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN = ROOT / "results/randomized_full_copy_exploration_collection_v1"
RUNS = CAMPAIGN / "runs"
DATASET = CAMPAIGN / "temporal_dataset"
ARTIFACT = CAMPAIGN / "temporal_t2_primary_only_two_objective_v1"
SELECTION = ROOT / "experiments/model-selection/temporal-t2-primary-only-two-objective-v1.json"
CONTRACT = ROOT / "experiments/model-selection/temporal-t2-feature-adapter-goldens-v1.json"
OUTPUT = ROOT / "contrib/wifi-streaming/test/temporal-t2-feature-adapter-golden-v1.h"


class TemporalT2FeatureAdapterGoldenExporterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = exporter._load_contract(CONTRACT, require_canonical_path=True)
        cls.source, cls.cases = exporter.build_goldens(
            cls.contract, RUNS, DATASET, ARTIFACT, SELECTION
        )
        cls.header = exporter.emit_header(cls.source, cls.cases)
        cls.protected = exporter._protected_output_paths(
            cls.contract, CONTRACT, RUNS, DATASET, ARTIFACT, SELECTION
        )

    def _strict_json(self, payload: bytes) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "probe.json"
            path.write_bytes(payload)
            exporter.strict_ascii_json(path, "probe")

    def test_contract_and_fixture_identity_are_frozen(self) -> None:
        self.assertEqual(exporter.sha256_file(CONTRACT), exporter.EXPECTED_CONTRACT_SHA256)
        self.assertEqual(
            tuple(case.contract["fixture_id"] for case in self.cases),
            exporter.EXPECTED_FIXTURE_IDS,
        )
        self.assertEqual(len(self.source.feature_names), 246)
        self.assertEqual(
            hashlib.sha256(
                __import__("json")
                .dumps(
                    list(self.source.feature_names),
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
                .encode("ascii")
            ).hexdigest(),
            exporter.EXPECTED_ORDERED_FEATURE_NAMES_SHA256,
        )

    def test_reconstructs_all_frozen_words_diagnostics_and_provenance(self) -> None:
        expected_digests = (
            "1d89dba70d65f862d09b472d5342ebb780c520d76ee114c00c6960c91a59f9ed",
            "c7bc7a317b9add14c79dd0fdb3da753243a4ba9f4c4150cb83dcb06292f9f848",
            "0b78b476fcd8ebd8ec0a3530ee8840ff796875abaf12ac0d766ed7dc67dbe08b",
            "4d0f798b5984903d02970594881679d07c89a9d4307f58364ab04457f6ee9ae6",
            "0c748008d7a6697f1d3275a909a46d8e76ff74de544ab8ca1a7e01e6352efa1d",
        )
        expected_score_words = (0x38BBC0E5, 0x38BB9051, 0x38BBDD35, 0x38C45E1F, 0x3904F0E5)
        for case, digest, score_word in zip(
            self.cases, expected_digests, expected_score_words, strict=True
        ):
            self.assertEqual(len(case.feature_words), 246)
            self.assertEqual(exporter._words_digest(case.feature_words), digest)
            self.assertEqual(
                exporter._float32_word(case.model_result["value_per_cost_score"]),
                score_word,
            )
            self.assertEqual(len(case.current_sample), 126)
            self.assertEqual(len(case.current_report), 95)
            self.assertEqual(len(case.lag_samples), 3)
            self.assertEqual(len(case.lag_reports), 3)
            self.assertEqual(
                tuple(int(row["frame_id"]) for row in case.lag_samples),
                tuple(int(case.current_sample["frame_id"]) - lag for lag in (1, 3, 8)),
            )
            for report in (case.current_report, *case.lag_reports):
                self.assertEqual(report["report_available"], "1")
                self.assertEqual(report["feature_support_mask"], "0x3ffffffffdffff")
                for window in ("1ms", "5ms", "20ms"):
                    self.assertIn(f"history_coverage_{window}_us", report)

    def test_live_sample_and_delayed_report_remain_separate(self) -> None:
        case = self.cases[0]
        sentinels = case.contract["sentinel_assertions"]
        self.assertTrue(any(item["kind"] == "delayed_rolling_not_live" for item in sentinels))
        for item in sentinels:
            if item["kind"] == "delayed_rolling_not_live":
                self.assertNotEqual(item["live_sample_value"], item["delayed_report_value"])
                self.assertEqual(
                    case.current_sample["mpdu_attempts_1ms"],
                    str(item["live_sample_value"]),
                )
                self.assertEqual(
                    case.current_report["mpdu_attempts_1ms"],
                    str(item["delayed_report_value"]),
                )
            if item["kind"] == "delayed_last_ack_not_live":
                self.assertNotEqual(
                    case.current_sample["last_positive_ack_time_ns"],
                    case.current_report["last_positive_ack_time_ns"],
                )

    def test_generated_header_is_deterministic_current_and_checkable(self) -> None:
        self.assertEqual(self.header, exporter.emit_header(self.source, self.cases))
        self.assertEqual(OUTPUT.read_text(encoding="utf-8"), self.header)
        self.assertEqual(
            [
                (line_number, len(line))
                for line_number, line in enumerate(self.header.splitlines(), start=1)
                if len(line) > 100
            ],
            [],
        )
        for struct_name in (
            "TemporalT2FeatureAdapterGoldenLag",
            "TemporalT2FeatureAdapterGoldenCase",
        ):
            declaration = f"struct {struct_name}\n{{\n"
            body = self.header.split(declaration, maxsplit=1)[1].split("\n};", maxsplit=1)[0]
            members = [line for line in body.splitlines() if line.strip()]
            self.assertGreater(len(members), 0)
            self.assertTrue(all("; ///< " in line for line in members))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "golden.h"
            exporter.write_or_check(path, self.header, False)
            exporter.write_or_check(path, self.header, True)
            path.write_text(self.header + "// stale\n", encoding="utf-8")
            with self.assertRaisesRegex(exporter.GoldenExportError, "stale"):
                exporter.write_or_check(path, self.header, True)

    def test_output_rejects_protected_collisions_and_symlinks(self) -> None:
        protected_examples = (
            CONTRACT,
            SELECTION,
            ROOT / "experiments/model-selection/paired-value-duplication-t2-runtime-v1.json",
            DATASET / "randomized_t2_temporal.csv",
            CAMPAIGN / "dataset/randomized_t2.csv",
            ARTIFACT / "temporal_t2_value_models.pkl",
            RUNS / "8bbdd058ca0ab3bf0082" / "frames.csv",
            ROOT / "tools/build_randomized_temporal_dataset.py",
        )
        for path in protected_examples:
            with self.subTest(path=path), self.assertRaisesRegex(
                exporter.GoldenExportError, "protected input"
            ):
                exporter._validate_output_path(path, self.protected)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            safe = root / "safe.h"
            self.assertEqual(
                exporter._validate_output_path(safe, self.protected), safe.resolve()
            )
            target = root / "target.h"
            target.write_text("target", encoding="utf-8")
            symlink = root / "output.h"
            symlink.symlink_to(target)
            with self.assertRaisesRegex(exporter.GoldenExportError, "symlink output"):
                exporter._validate_output_path(symlink, self.protected)
            with self.assertRaisesRegex(exporter.GoldenExportError, "symlink output"):
                exporter.write_or_check(symlink, self.header, False)
            with self.assertRaisesRegex(exporter.GoldenExportError, "symlink output"):
                exporter.write_or_check(symlink, self.header, True)
            protected_parent = root / "dataset-link"
            protected_parent.symlink_to(DATASET, target_is_directory=True)
            with self.assertRaisesRegex(exporter.GoldenExportError, "protected input"):
                exporter._validate_output_path(
                    protected_parent / "new-output.h", self.protected
                )

    def test_output_publication_is_same_directory_and_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "golden.h"
            output.write_text("old", encoding="utf-8")
            with mock.patch.object(
                exporter.os, "replace", wraps=os.replace
            ) as replace:
                exporter.write_or_check(output, self.header, False)
            replace.assert_called_once()
            temporary_path, published_path = map(Path, replace.call_args.args)
            self.assertEqual(temporary_path.parent, output.parent)
            self.assertEqual(published_path, output)
            self.assertEqual(output.read_text(encoding="utf-8"), self.header)
            self.assertEqual(list(root.glob(".golden.h.tmp-*")), [])

            output.write_text("preserved", encoding="utf-8")
            with mock.patch.object(
                exporter.os, "replace", side_effect=OSError("probe")
            ), self.assertRaisesRegex(OSError, "probe"):
                exporter.write_or_check(output, self.header, False)
            self.assertEqual(output.read_text(encoding="utf-8"), "preserved")
            self.assertEqual(list(root.glob(".golden.h.tmp-*")), [])

    def test_cli_preserves_arbitrary_noncolliding_output_and_checks_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "nested/golden.h"
            with mock.patch.object(
                exporter, "_load_contract", return_value=self.contract
            ), mock.patch.object(
                exporter, "build_goldens", return_value=(self.source, self.cases)
            ), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(exporter.main(["--output", str(output)]), 0)
                self.assertEqual(output.read_text(encoding="utf-8"), self.header)
                self.assertEqual(
                    exporter.main(["--output", str(output), "--check"]), 0
                )

    def test_strict_json_rejects_duplicate_non_ascii_and_nonfinite(self) -> None:
        for payload, pattern in (
            (b'{"outer":{"key":1,"key":2}}', "duplicate JSON key"),
            ('{"key":"caf\u00e9"}'.encode("utf-8"), "strict ASCII JSON"),
            (b'{"key":NaN}', "non-finite JSON token"),
            (b'{"key":Infinity}', "non-finite JSON token"),
        ):
            with self.subTest(payload=payload), self.assertRaisesRegex(
                exporter.GoldenExportError, pattern
            ):
                self._strict_json(payload)

    def test_contract_tamper_is_rejected_before_json_parse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "contract.json"
            path.write_bytes(CONTRACT.read_bytes() + b" ")
            with mock.patch.object(exporter, "strict_ascii_json") as parser:
                with self.assertRaisesRegex(exporter.GoldenExportError, "SHA-256 differs"):
                    exporter._load_contract(path, require_canonical_path=False)
                parser.assert_not_called()

    def test_dependency_hash_failure_precedes_module_import_and_pickle(self) -> None:
        original_hash = exporter.sha256_file

        def altered_hash(path: Path) -> str:
            if Path(path).name == "temporal_t2_value_models.pkl":
                return "0" * 64
            return original_hash(Path(path))

        with mock.patch.object(
            exporter, "sha256_file", side_effect=altered_hash
        ), mock.patch.object(exporter, "_load_modules") as load_modules:
            with self.assertRaisesRegex(exporter.GoldenExportError, "SHA-256 differs"):
                exporter.build_goldens(
                    self.contract, RUNS, DATASET, ARTIFACT, SELECTION
                )
            load_modules.assert_not_called()

    def test_all_raw_hashes_pass_before_current_validation(self) -> None:
        modules = exporter._load_modules()
        metadata = exporter.strict_ascii_json(DATASET / "dataset_metadata.json", "metadata")
        original_hash = exporter.sha256_file

        def altered_hash(path: Path) -> str:
            path = Path(path)
            if path.name == "frames.csv" and path.parent.name == "c7fc90fdfa295932dd2e":
                return "0" * 64
            return original_hash(path)

        with mock.patch.object(
            exporter, "sha256_file", side_effect=altered_hash
        ), mock.patch.object(modules.validator, "validate_run") as validate:
            with self.assertRaisesRegex(exporter.GoldenExportError, "SHA-256 differs"):
                exporter._load_runs(self.contract, metadata, RUNS, modules)
            validate.assert_not_called()

    def test_validator_hash_drift_is_ignored_and_current_validator_runs(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["source_closure"]["historical_validation_source_provenance"][
            "historical_sha256"
        ] = "0" * 64
        exporter._verify_declared_artifacts(
            changed, RUNS, DATASET, ARTIFACT, SELECTION
        )

        modules = exporter._load_modules()
        metadata = exporter.strict_ascii_json(DATASET / "dataset_metadata.json", "metadata")
        current_validator = mock.Mock(wraps=modules.validator.validate_run)
        with mock.patch.object(
            modules.validator, "validate_run", current_validator
        ), mock.patch.object(modules.base, "validate_run", current_validator):
            runs = exporter._load_runs(changed, metadata, RUNS, modules)
        self.assertEqual(set(runs), {row["run_id"] for row in changed["raw_run_source_closure"]})
        self.assertEqual(current_validator.call_count, 4)

    def test_exact_endpoint_join_rejects_missing_or_substituted_key(self) -> None:
        endpoint = self.cases[0].contract["endpoints_in_exact_order"][1]
        key = (
            endpoint["row_key"]["frame_id"],
            "T2",
            1,
            0,
        )
        row = self.cases[0].lag_samples[0]
        self.assertIs(exporter._exact_endpoint({key: row}, endpoint, "probe"), row)
        with self.assertRaisesRegex(exporter.GoldenExportError, "exact endpoint is missing"):
            exporter._exact_endpoint({(key[0] + 1, *key[1:]): row}, endpoint, "probe")

    def test_exact_endpoint_index_rejects_duplicate_raw_key(self) -> None:
        row = self.cases[0].lag_samples[0]
        with self.assertRaisesRegex(exporter.GoldenExportError, "duplicate raw endpoint"):
            exporter._index_exact_rows((row, dict(row)), "probe")

    def test_rejects_reversed_endpoint_contract(self) -> None:
        endpoints = copy.deepcopy(
            self.cases[0].contract["endpoints_in_exact_order"]
        )
        endpoints[1], endpoints[2] = endpoints[2], endpoints[1]
        with self.assertRaisesRegex(exporter.GoldenExportError, "endpoint order differs"):
            exporter._validate_endpoint_contracts(endpoints, "probe")

    def test_rejects_exact_temporal_row_and_model_drift(self) -> None:
        case = self.cases[0]
        changed_row = dict(case.temporal_row)
        changed_row["x_f0_frame_age_us"] = "2001"
        with self.assertRaisesRegex(exporter.GoldenExportError, "rebuilt temporal row differs"):
            exporter._require_exact_temporal_row(
                changed_row,
                case.temporal_row,
                tuple(case.temporal_row),
                "probe",
            )
        changed_result = dict(case.model_result)
        changed_result["primary_bad12_logit"] += 1e-12
        with self.assertRaisesRegex(exporter.GoldenExportError, "diagnostics differ"):
            exporter._assert_fixture_result(
                case.contract,
                np.asarray(case.feature_values),
                case.feature_words,
                changed_result,
            )
        changed_words = (*case.feature_words[:-1], case.feature_words[-1] ^ 1)
        with self.assertRaisesRegex(exporter.GoldenExportError, "feature vector differs"):
            exporter._assert_fixture_result(
                case.contract,
                np.asarray(case.feature_values),
                changed_words,
                case.model_result,
            )

    def test_rejects_isolated_feature_nan_threshold_and_gate_drift(self) -> None:
        case = self.cases[0]
        names = list(self.source.feature_names)
        names[12] += "_drift"
        with self.assertRaisesRegex(exporter.GoldenExportError, "feature-name contract"):
            exporter._require_feature_name_contract(names, self.contract)

        nan_drift = copy.deepcopy(case.contract)
        nan_drift["expected_feature_vector"]["nan_indices"][0] += 1
        with self.assertRaisesRegex(exporter.GoldenExportError, "feature vector differs"):
            exporter._assert_fixture_result(
                nan_drift,
                np.asarray(case.feature_values),
                case.feature_words,
                case.model_result,
            )

        threshold_drift = copy.deepcopy(case.contract)
        threshold_drift["expected_model_result"]["passes_score_threshold"] = False
        with self.assertRaisesRegex(exporter.GoldenExportError, "diagnostics differ"):
            exporter._assert_fixture_result(
                threshold_drift,
                np.asarray(case.feature_values),
                case.feature_words,
                case.model_result,
            )

        gate_drift = copy.deepcopy(case.contract)
        gate_drift["expected_model_result"]["passes_p_frame_gate"] = False
        with self.assertRaisesRegex(exporter.GoldenExportError, "caller-owned gate differs"):
            exporter._assert_fixture_result(
                gate_drift,
                np.asarray(case.feature_values),
                case.feature_words,
                case.model_result,
            )

    def test_float_word_codec_preserves_signed_zero_and_normalizes_nan(self) -> None:
        payload_nan = struct.unpack("<f", struct.pack("<I", 0x7FA12345))[0]
        words = exporter._feature_words(
            np.asarray([0.0, -0.0, np.float32(2**-149), payload_nan], dtype=np.float64)
        )
        self.assertEqual(words, (0x00000000, 0x80000000, 0x00000001, 0x7FC00000))
        for bad in (math.inf, -math.inf, 1e100):
            with self.subTest(bad=bad), self.assertRaises(exporter.GoldenExportError):
                exporter._feature_words(np.asarray([bad], dtype=np.float64))


if __name__ == "__main__":
    unittest.main()
