#!/usr/bin/env python3
"""Export frozen real-ledger temporal-T2 feature-adapter goldens to C++."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import os
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np


TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

CAMPAIGN = ROOT / "results/randomized_full_copy_exploration_collection_v1"
CANONICAL_RUNS_ROOT = (CAMPAIGN / "runs").resolve()
CANONICAL_DATASET_DIR = (CAMPAIGN / "temporal_dataset").resolve()
CANONICAL_ARTIFACT_DIR = (
    CAMPAIGN / "temporal_t2_primary_only_two_objective_v1"
).resolve()
CANONICAL_SELECTION = (
    ROOT / "experiments/model-selection/temporal-t2-primary-only-two-objective-v1.json"
).resolve()
CANONICAL_CONTRACT = (
    ROOT / "experiments/model-selection/temporal-t2-feature-adapter-goldens-v1.json"
).resolve()
CANONICAL_OUTPUT = (
    ROOT / "contrib/wifi-streaming/test/temporal-t2-feature-adapter-golden-v1.h"
).resolve()

EXPECTED_CONTRACT_SHA256 = (
    "d2bd9b1277a84e51d72579573a7b50891445ec08cdfa0f09e049eb89fdad53b0"
)
EXPECTED_FIXTURE_IDS = (
    "threshold_equal_delayed_not_live",
    "nearest_calibration_below_threshold",
    "nearest_calibration_above_threshold",
    "i_frame_above_threshold_gate_probe",
    "first_history_ready_lag8_last_ack_missing",
)
EXPECTED_LAGS = (1, 3, 8)
EXPECTED_FEATURE_COUNT = 246
CANONICAL_NAN_WORD = 0x7FC00000
EXPECTED_ORDERED_FEATURE_NAMES_SHA256 = (
    "a00ebbb9807f99972f2cd009d1b2a20bf0b001cee123ac60d5121b2b1c07209e"
)


class GoldenExportError(ValueError):
    """Raised when the frozen golden closure cannot be reproduced exactly."""


@dataclass(frozen=True)
class Modules:
    """Feature-producing modules loaded only after their hashes are verified."""

    base: Any
    temporal: Any
    trainer: Any
    model_exporter: Any
    validator: Any


@dataclass(frozen=True)
class GoldenCase:
    """One completely reconstructed golden fixture."""

    contract: dict[str, Any]
    temporal_row: dict[str, str]
    current_sample: dict[str, str]
    current_report: dict[str, str]
    lag_samples: tuple[dict[str, str], ...]
    lag_reports: tuple[dict[str, str], ...]
    feature_values: tuple[float, ...]
    feature_words: tuple[int, ...]
    model_result: dict[str, Any]
    source_hashes: dict[str, str]


@dataclass(frozen=True)
class ProtectedOutputPaths:
    """Files and directory trees that generated output must never replace."""

    files: frozenset[Path]
    directories: frozenset[Path]


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a regular file."""

    digest = hashlib.sha256()
    try:
        if not path.is_file() or path.is_symlink():
            raise GoldenExportError(f"not a regular non-symlink file: {path}")
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise GoldenExportError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def verify_file_hash(path: Path, expected: str, context: str) -> str:
    """Require one exact lowercase SHA-256 identity."""

    if (
        not isinstance(expected, str)
        or len(expected) != 64
        or any(character not in "0123456789abcdef" for character in expected)
    ):
        raise GoldenExportError(f"{context}: invalid declared SHA-256")
    actual = sha256_file(path)
    if actual != expected:
        raise GoldenExportError(f"{context}: SHA-256 differs")
    return actual


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GoldenExportError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_ascii_json(path: Path, context: str) -> dict[str, Any]:
    """Read a unique-key ASCII JSON object."""

    try:
        text = path.read_bytes().decode("ascii")
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                GoldenExportError(f"non-finite JSON token: {token}")
            ),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GoldenExportError(f"{context}: invalid strict ASCII JSON: {error}") from error
    if not isinstance(value, dict):
        raise GoldenExportError(f"{context}: JSON root is not an object")
    return value


def _exact_keys(value: Any, keys: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise GoldenExportError(f"{context}: object schema differs")
    return value


def _integer(value: Any, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise GoldenExportError(f"{context}: expected integer")
    return value


def _validate_endpoint_contracts(endpoints: Any, context: str) -> None:
    """Require the frozen current/lag1/lag3/lag8 endpoint sequence."""

    if (
        not isinstance(endpoints, list)
        or not all(isinstance(endpoint, dict) for endpoint in endpoints)
        or [endpoint.get("role") for endpoint in endpoints]
        != ["current", "lag1", "lag3", "lag8"]
        or [endpoint.get("lag_frames") for endpoint in endpoints] != [0, 1, 3, 8]
    ):
        raise GoldenExportError(f"{context}: endpoint order differs")


def _load_contract(path: Path, *, require_canonical_path: bool) -> dict[str, Any]:
    resolved = path.resolve()
    if require_canonical_path and resolved != CANONICAL_CONTRACT:
        raise GoldenExportError("golden contract path differs from canonical path")
    verify_file_hash(resolved, EXPECTED_CONTRACT_SHA256, "golden contract")
    contract = strict_ascii_json(resolved, "golden contract")
    _exact_keys(
        contract,
        {
            "feature_adapter_golden_schema_version",
            "feature_adapter_golden_id",
            "status",
            "implementation_state",
            "immutability",
            "source_closure",
            "raw_run_source_closure",
            "raw_row_key_contract",
            "feature_word_contract",
            "fixture_selection_contract",
            "fixtures",
            "generator_contract",
            "python_generator_test_contract",
            "cpp_feature_adapter_test_contract",
            "stateful_history_test_contract",
            "integration_test_contract",
        },
        "golden contract",
    )
    if (
        contract["feature_adapter_golden_schema_version"] != 1
        or contract["feature_adapter_golden_id"]
        != "temporal-t2-feature-adapter-goldens-v1"
        or contract["status"] != "frozen_before_feature_adapter_implementation"
    ):
        raise GoldenExportError("golden contract identity or status differs")
    fixtures = contract["fixtures"]
    if (
        not isinstance(fixtures, list)
        or tuple(item.get("fixture_id") for item in fixtures if isinstance(item, dict))
        != EXPECTED_FIXTURE_IDS
        or contract.get("fixture_selection_contract", {}).get("count") != len(fixtures)
    ):
        raise GoldenExportError("golden fixture count or order differs")
    for index, fixture in enumerate(fixtures):
        _exact_keys(
            fixture,
            {
                "fixture_id",
                "purpose",
                "identity",
                "source_assignment",
                "endpoints_in_exact_order",
                "expected_feature_vector",
                "expected_model_result",
                "sentinel_assertions",
            },
            f"fixture {index}",
        )
        endpoints = fixture["endpoints_in_exact_order"]
        _validate_endpoint_contracts(endpoints, f"fixture {index}")
        nan_indices = fixture["expected_feature_vector"].get("nan_indices")
        if not isinstance(nan_indices, list) or len(nan_indices) > 16:
            raise GoldenExportError(f"fixture {index}: NaN sentinel count exceeds 16")
    return contract


def _canonical_relative(path_value: Any, context: str) -> Path:
    if not isinstance(path_value, str) or not path_value or Path(path_value).is_absolute():
        raise GoldenExportError(f"{context}: path is not repository-relative")
    path = (ROOT / path_value).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as error:
        raise GoldenExportError(f"{context}: path escapes repository") from error
    return path


def _protected_output_paths(
    contract: dict[str, Any],
    contract_path: Path,
    runs_root: Path,
    dataset_dir: Path,
    artifact_dir: Path,
    selection_path: Path,
) -> ProtectedOutputPaths:
    """Build the complete immutable input set protected from output writes."""

    closure = contract["source_closure"]
    files = {
        contract_path.resolve(),
        selection_path.resolve(),
        _canonical_relative(
            closure["frozen_selection"]["path"], "frozen selection path"
        ),
        _canonical_relative(
            closure["runtime_contract"]["path"], "runtime contract path"
        ),
    }
    files.update(
        _canonical_relative(path, f"feature source {path}")
        for path in closure["feature_producing_source_sha256"]
    )
    input_v1_dir = (dataset_dir.resolve().parent / "dataset").resolve()
    declared_dataset_dir = _canonical_relative(
        closure["canonical_temporal_dataset"]["directory"],
        "temporal dataset directory",
    )
    declared_input_v1_dir = (declared_dataset_dir.parent / "dataset").resolve()
    declared_artifact_dir = _canonical_relative(
        closure["canonical_model"]["directory"], "model directory"
    )
    directories = {
        runs_root.resolve(),
        dataset_dir.resolve(),
        artifact_dir.resolve(),
        input_v1_dir,
        declared_dataset_dir,
        declared_input_v1_dir,
        declared_artifact_dir,
    }
    files.update(
        dataset_dir.resolve() / name
        for name in closure["canonical_temporal_dataset"]["artifacts_sha256"]
    )
    files.update(
        declared_dataset_dir / name
        for name in closure["canonical_temporal_dataset"]["artifacts_sha256"]
    )
    files.update(
        input_v1_dir / name
        for name in closure["canonical_temporal_dataset"][
            "input_v1_artifacts_sha256"
        ]
    )
    files.update(
        declared_input_v1_dir / name
        for name in closure["canonical_temporal_dataset"][
            "input_v1_artifacts_sha256"
        ]
    )
    files.add(
        _canonical_relative(
            closure["canonical_model"]["manifest"]["path"],
            "model manifest path",
        )
    )
    files.update(
        artifact_dir.resolve() / name
        for name in closure["canonical_model"]["artifacts_sha256"]
    )
    files.update(
        declared_artifact_dir / name
        for name in closure["canonical_model"]["artifacts_sha256"]
    )
    for run in contract["raw_run_source_closure"]:
        run_dir = (runs_root.resolve() / run["run_id"]).resolve()
        declared_run_dir = _canonical_relative(
            run["run_directory"], f"raw run {run['run_id']} directory"
        )
        directories.add(run_dir)
        directories.add(declared_run_dir)
        files.update(run_dir / name for name in run["files_sha256"])
        files.update(declared_run_dir / name for name in run["files_sha256"])
    return ProtectedOutputPaths(
        frozenset(path.resolve() for path in files),
        frozenset(path.resolve() for path in directories),
    )


def _validate_output_path(path: Path, protected: ProtectedOutputPaths) -> Path:
    """Resolve and require a non-symlink output outside protected inputs."""

    if path.is_symlink():
        raise GoldenExportError(f"refusing symlink output: {path}")
    resolved = path.resolve()
    if resolved in protected.files:
        raise GoldenExportError(f"output collides with protected input: {resolved}")
    for directory in protected.directories:
        try:
            resolved.relative_to(directory)
        except ValueError:
            continue
        raise GoldenExportError(f"output is inside protected input directory: {directory}")
    if resolved.exists() and not resolved.is_file():
        raise GoldenExportError(f"output is not a regular file path: {resolved}")
    return resolved


def _verify_declared_artifacts(
    contract: dict[str, Any],
    runs_root: Path,
    dataset_dir: Path,
    artifact_dir: Path,
    selection_path: Path,
) -> None:
    """Hash all contract-enforced sources before parsing dependent content."""

    closure = contract["source_closure"]
    runtime = closure["runtime_contract"]
    selection = closure["frozen_selection"]
    model = closure["canonical_model"]
    dataset = closure["canonical_temporal_dataset"]
    if (
        runs_root.resolve() != CANONICAL_RUNS_ROOT
        or dataset_dir.resolve() != CANONICAL_DATASET_DIR
        or artifact_dir.resolve() != CANONICAL_ARTIFACT_DIR
        or selection_path.resolve() != CANONICAL_SELECTION
    ):
        raise GoldenExportError("source paths differ from canonical closure")
    verify_file_hash(
        _canonical_relative(runtime["path"], "runtime contract path"),
        runtime["sha256"],
        "runtime contract",
    )
    if _canonical_relative(selection["path"], "selection path") != selection_path.resolve():
        raise GoldenExportError("selection path declaration differs")
    verify_file_hash(selection_path.resolve(), selection["sha256"], "selection")
    manifest = model["manifest"]
    verify_file_hash(
        _canonical_relative(manifest["path"], "model manifest path"),
        manifest["sha256"],
        "model manifest",
    )
    declared_model_dir = _canonical_relative(model["directory"], "model directory")
    if declared_model_dir != artifact_dir.resolve():
        raise GoldenExportError("model directory declaration differs")
    for name, digest in model["artifacts_sha256"].items():
        verify_file_hash(artifact_dir / name, digest, f"model artifact {name}")
    declared_dataset_dir = _canonical_relative(dataset["directory"], "dataset directory")
    if declared_dataset_dir != dataset_dir.resolve():
        raise GoldenExportError("dataset directory declaration differs")
    for name, digest in dataset["artifacts_sha256"].items():
        verify_file_hash(dataset_dir / name, digest, f"temporal dataset {name}")
    input_dir = dataset_dir.parent / "dataset"
    for name, digest in dataset["input_v1_artifacts_sha256"].items():
        verify_file_hash(input_dir / name, digest, f"input v1 dataset {name}")
    producing = closure["feature_producing_source_sha256"]
    expected_sources = {
        "tools/build_randomized_intervention_dataset.py",
        "tools/build_randomized_temporal_dataset.py",
        "tools/train_randomized_value.py",
        "tools/train_temporal_t2_value.py",
    }
    if not isinstance(producing, dict) or set(producing) != expected_sources:
        raise GoldenExportError("feature-producing source closure differs")
    for relative, digest in producing.items():
        verify_file_hash(_canonical_relative(relative, relative), digest, relative)


def _load_modules() -> Modules:
    """Import dependent code after its frozen source hashes have passed."""

    return Modules(
        importlib.import_module("build_randomized_intervention_dataset"),
        importlib.import_module("build_randomized_temporal_dataset"),
        importlib.import_module("train_temporal_t2_value"),
        importlib.import_module("export_temporal_t2_value_model_v1"),
        importlib.import_module("validate_outputs"),
    )


def _metadata_run_map(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = metadata.get("source_runs")
    if not isinstance(rows, list):
        raise GoldenExportError("temporal metadata source_runs is absent")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("run_id"), str):
            raise GoldenExportError("temporal metadata has invalid source run")
        run_id = row["run_id"]
        if run_id in result:
            raise GoldenExportError("temporal metadata duplicates a run_id")
        hashes = row.get("files_sha256")
        if not isinstance(hashes, dict) or not all(
            isinstance(name, str) and isinstance(digest, str)
            for name, digest in hashes.items()
        ):
            raise GoldenExportError(f"metadata run {run_id}: invalid hash dictionary")
        result[run_id] = row
    return result


def _load_runs(
    contract: dict[str, Any],
    metadata: dict[str, Any],
    runs_root: Path,
    modules: Modules,
) -> dict[str, Any]:
    metadata_runs = _metadata_run_map(metadata)
    verified: list[tuple[dict[str, Any], dict[str, Any], Path]] = []
    for frozen in contract["raw_run_source_closure"]:
        _exact_keys(
            frozen,
            {"run_id", "seed", "run_number", "run_directory", "files_sha256"},
            "raw run closure",
        )
        run_id = frozen["run_id"]
        if run_id not in metadata_runs or any(
            item[0]["run_id"] == run_id for item in verified
        ):
            raise GoldenExportError(f"raw run closure differs for {run_id}")
        metadata_row = metadata_runs[run_id]
        if (
            metadata_row.get("seed") != frozen["seed"]
            or metadata_row.get("run_number") != frozen["run_number"]
        ):
            raise GoldenExportError(f"raw run identity differs for {run_id}")
        run_dir = (runs_root / run_id).resolve()
        if _canonical_relative(frozen["run_directory"], "raw run directory") != run_dir:
            raise GoldenExportError(f"raw run path differs for {run_id}")
        complete_hashes = metadata_row["files_sha256"]
        expected_names = set(modules.base.SOURCE_FILES) | set(modules.temporal.EXTRA_RAW_FILES)
        if set(complete_hashes) != expected_names:
            raise GoldenExportError(f"raw run complete file closure differs for {run_id}")
        for name, digest in complete_hashes.items():
            verify_file_hash(run_dir / name, digest, f"raw run {run_id} {name}")
        frozen_hashes = frozen["files_sha256"]
        if not isinstance(frozen_hashes, dict) or any(
            complete_hashes.get(name) != digest for name, digest in frozen_hashes.items()
        ):
            raise GoldenExportError(f"frozen raw hash subset differs for {run_id}")
        verified.append((frozen, metadata_row, run_dir))

    # No validator or builder may parse a ledger until all forty raw files pass.
    loaded: dict[str, Any] = {}
    if modules.base.validate_run is not modules.validator.validate_run:
        raise GoldenExportError("base builder is not bound to the current validator")
    for frozen, metadata_row, run_dir in verified:
        run_id = frozen["run_id"]
        complete_hashes = metadata_row["files_sha256"]
        # _load_run unconditionally invokes the current bound validator before reads.
        run = modules.base._load_run(run_dir)
        builder_hashes = {
            **run.source_hashes,
            **{
                name: sha256_file(run_dir / name)
                for name in modules.temporal.EXTRA_RAW_FILES
            },
        }
        if builder_hashes != complete_hashes:
            raise GoldenExportError(f"base builder source hashes differ for {run_id}")
        loaded[run_id] = run
    return loaded


def _row_key(row: dict[str, str], context: str) -> tuple[int, str, int, int]:
    try:
        return (
            int(row["frame_id"]),
            row["sample_stage"],
            int(row["path_id"]),
            int(row["copy_id"]),
        )
    except (KeyError, ValueError) as error:
        raise GoldenExportError(f"{context}: invalid row key") from error


def _index_exact_rows(
    rows: Sequence[dict[str, str]], context: str
) -> dict[tuple[int, str, int, int], dict[str, str]]:
    """Index raw rows while rejecting duplicate endpoint identities."""

    result: dict[tuple[int, str, int, int], dict[str, str]] = {}
    for row in rows:
        key = _row_key(row, context)
        if key in result:
            raise GoldenExportError(f"{context}: duplicate raw endpoint {key}")
        result[key] = row
    return result


def _exact_endpoint(
    rows: dict[tuple[int, str, int, int], dict[str, str]],
    endpoint: dict[str, Any],
    context: str,
) -> dict[str, str]:
    key_contract = endpoint["row_key"]
    key = (
        _integer(key_contract.get("frame_id"), context),
        key_contract.get("sample_stage"),
        _integer(key_contract.get("path_id"), context),
        _integer(key_contract.get("copy_id"), context),
    )
    if key not in rows:
        raise GoldenExportError(f"{context}: exact endpoint is missing")
    return rows[key]


def _load_temporal_rows(
    csv_path: Path, temporal: Any, fixtures: list[dict[str, Any]]
) -> dict[tuple[str, int], dict[str, str]]:
    targets = {
        (fixture["identity"]["run_id"], fixture["identity"]["frame_id"]): fixture
        for fixture in fixtures
    }
    found: dict[tuple[str, int], dict[str, str]] = {}
    with csv_path.open(newline="", encoding="ascii") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None or tuple(reader.fieldnames) != tuple(
            temporal.DATASET_COLUMNS
        ):
            raise GoldenExportError("temporal CSV schema/order differs")
        for row_index, row in enumerate(reader):
            if None in row or any(value is None for value in row.values()):
                raise GoldenExportError("temporal CSV has malformed row")
            try:
                key = (row["run_id"], int(row["frame_id"]))
            except ValueError as error:
                raise GoldenExportError("temporal CSV frame identity is invalid") from error
            if key not in targets:
                continue
            if key in found:
                raise GoldenExportError(f"temporal CSV duplicates fixture key {key}")
            identity = targets[key]["identity"]
            if (
                row_index != identity["temporal_csv_zero_based_row_index"]
                or row_index + 2 != identity["temporal_csv_line_number_including_header"]
            ):
                raise GoldenExportError(f"temporal CSV location differs for {key}")
            found[key] = row
    if set(found) != set(targets):
        raise GoldenExportError("temporal CSV fixture join is incomplete")
    return found


def _require_exact_temporal_row(
    regenerated: dict[str, str],
    frozen: dict[str, str],
    expected_columns: Sequence[str],
    context: str,
) -> None:
    """Require exact key order and element-for-element string equality."""

    if tuple(regenerated) != tuple(expected_columns) or regenerated != frozen:
        differing = [
            key
            for key in set(regenerated) | set(frozen)
            if regenerated.get(key) != frozen.get(key)
        ]
        raise GoldenExportError(
            f"{context}: rebuilt temporal row differs: {', '.join(sorted(differing)[:8])}"
        )


def _numeric(raw: str, context: str) -> float:
    if raw == "":
        return math.nan
    try:
        value = float(raw)
    except ValueError as error:
        raise GoldenExportError(f"{context}: invalid numeric value") from error
    if not math.isfinite(value):
        raise GoldenExportError(f"{context}: non-finite numeric value")
    with np.errstate(over="ignore", invalid="ignore"):
        quantized = np.float32(value)
    if np.isinf(quantized):
        raise GoldenExportError(f"{context}: float32 overflow")
    return float(quantized)


def _feature_vector(row: dict[str, str], names: tuple[str, ...], modules: Modules) -> np.ndarray:
    if len(names) != EXPECTED_FEATURE_COUNT:
        raise GoldenExportError("ordered feature count differs")
    base_names = names[:68]
    base_values: list[float] = []
    for name in base_names:
        if name == "x_f0_frame_type=I_FRAME":
            value = float(row["x_f0_frame_type"] == "I_FRAME")
        elif name == "x_f0_frame_type=P_FRAME":
            value = float(row["x_f0_frame_type"] == "P_FRAME")
        else:
            value = _numeric(row[name], name)
        base_values.append(value)
    compact = modules.trainer._compact_primary_physics(
        np.asarray(base_values, dtype=np.float64).reshape(1, -1), base_names
    )[0]
    if tuple(names[68:75]) != tuple(modules.trainer.COMPACT_PRIMARY_PHYSICS_NAMES):
        raise GoldenExportError("compact primary feature order differs")
    if tuple(names[75:]) != tuple(modules.trainer.PRIMARY_TEMPORAL_COLUMNS):
        raise GoldenExportError("selected primary temporal feature order differs")
    temporal_values = [_numeric(row[name], name) for name in names[75:]]
    vector = np.asarray([*base_values, *compact.tolist(), *temporal_values], dtype=np.float64)
    if vector.shape != (EXPECTED_FEATURE_COUNT,):
        raise GoldenExportError("feature vector width differs")
    with np.errstate(over="ignore", invalid="ignore"):
        vector = vector.astype(np.float32)
    if np.any(np.isinf(vector)):
        raise GoldenExportError("feature vector contains float32 infinity")
    return vector.astype(np.float64)


def _feature_words(values: np.ndarray) -> tuple[int, ...]:
    words: list[int] = []
    for value in values:
        number = float(value)
        if not math.isnan(number) and not math.isfinite(number):
            raise GoldenExportError("feature word input contains infinity")
        with np.errstate(over="ignore", invalid="ignore"):
            quantized = np.float32(number)
        if np.isinf(quantized):
            raise GoldenExportError("feature word input overflows float32")
        word = struct.unpack("<I", struct.pack("<f", quantized))[0]
        if math.isnan(number):
            word = CANONICAL_NAN_WORD
        words.append(word)
    return tuple(words)


def _words_digest(words: Sequence[int]) -> str:
    payload = b"".join(struct.pack("<I", word) for word in words)
    if len(payload) != 984:
        raise GoldenExportError("canonical feature payload size differs")
    return hashlib.sha256(payload).hexdigest()


def _float32_word(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", np.float32(value)))[0]


def _require_feature_name_contract(
    names: Sequence[str], contract: dict[str, Any]
) -> None:
    """Require the frozen ordered feature-name count, endpoints, and digest."""

    frozen = contract["source_closure"]["ordered_feature_names"]
    payload = json.dumps(
        list(names), ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")
    if (
        len(names) != frozen["count"]
        or not names
        or names[0] != frozen["first"]
        or names[-1] != frozen["last"]
        or hashlib.sha256(payload).hexdigest() != frozen["sha256"]
    ):
        raise GoldenExportError("ordered feature-name contract differs")


def _assert_fixture_result(
    fixture: dict[str, Any], values: np.ndarray, words: tuple[int, ...], result: dict[str, Any]
) -> None:
    expected_vector = fixture["expected_feature_vector"]
    nan_indices = [index for index, value in enumerate(values) if math.isnan(float(value))]
    if (
        nan_indices != expected_vector["nan_indices"]
        or len(nan_indices) != expected_vector["nan_count"]
        or _words_digest(words) != expected_vector["canonical_float32_words_sha256"]
    ):
        raise GoldenExportError(f"{fixture['fixture_id']}: feature vector differs")
    expected = fixture["expected_model_result"]
    mapping = {
        "primary_bad12_logit": "primary_bad12_logit",
        "primary_bad12_probability": "primary_bad12_probability",
        "treated_bad12_logit": "treated_bad12_logit",
        "treated_bad12_probability": "treated_bad12_probability",
        "predicted_log_airtime_raw_ridge": "predicted_log_airtime",
        "predicted_secondary_airtime_us": "predicted_secondary_airtime_us",
        "nonnegative_bad12_value": "nonnegative_bad12_value",
        "value_per_cost_score_float32": "value_per_cost_score",
        "passes_score_threshold": "passes_score_threshold",
    }
    if any(result[target] != expected[source] for source, target in mapping.items()):
        raise GoldenExportError(f"{fixture['fixture_id']}: sklearn diagnostics differ")
    expected_word = int(expected["value_per_cost_score_float32_bits_hex"], 16)
    if _float32_word(result["value_per_cost_score"]) != expected_word:
        raise GoldenExportError(f"{fixture['fixture_id']}: score word differs")
    p_gate = fixture["identity"]["frame_type"] == "P_FRAME"
    if (
        expected["passes_p_frame_gate"] != p_gate
        or expected["passes_score_and_p_frame_gates"]
        != (result["passes_score_threshold"] and p_gate)
    ):
        raise GoldenExportError(f"{fixture['fixture_id']}: caller-owned gate differs")


def build_goldens(
    contract: dict[str, Any],
    runs_root: Path,
    dataset_dir: Path,
    artifact_dir: Path,
    selection_path: Path,
    *,
    require_canonical_paths: bool = True,
) -> tuple[Any, tuple[GoldenCase, ...]]:
    """Validate the complete frozen closure and reconstruct all five fixtures."""

    if require_canonical_paths:
        _verify_declared_artifacts(
            contract, runs_root, dataset_dir, artifact_dir, selection_path
        )
    else:
        # Tests may call this only after separately verifying an equivalent closure.
        _verify_declared_artifacts(
            contract, runs_root, dataset_dir, artifact_dir, selection_path
        )
    modules = _load_modules()
    metadata = strict_ascii_json(dataset_dir / "dataset_metadata.json", "temporal metadata")
    runs = _load_runs(contract, metadata, runs_root, modules)
    # The pickle is loaded only after all declared artifacts and raw files pass.
    model_source = modules.model_exporter.validate_source(
        artifact_dir, selection_path, dataset_dir
    )
    _require_feature_name_contract(model_source.feature_names, contract)
    fixtures = contract["fixtures"]
    temporal_rows = _load_temporal_rows(
        dataset_dir / "randomized_t2_temporal.csv", modules.temporal, fixtures
    )
    results: list[GoldenCase] = []
    for fixture in fixtures:
        identity = fixture["identity"]
        run = runs[identity["run_id"]]
        frame_id = identity["frame_id"]
        source = f"{identity['run_id']} frame {frame_id}"
        if (
            run.seed != identity["seed"]
            or run.run_number != identity["run_number"]
            or run.frames[frame_id]["frame_type"] != identity["frame_type"]
        ):
            raise GoldenExportError(f"{source}: fixture identity differs")
        endpoints = modules.temporal._build_endpoints(run)
        sample_rows = _index_exact_rows(tuple(run.samples.values()), source + " samples")
        polling_rows = _index_exact_rows(tuple(run.polling.values()), source + " polling")
        endpoint_contracts = fixture["endpoints_in_exact_order"]
        exact = []
        for endpoint_contract in endpoint_contracts:
            endpoint_frame = endpoint_contract["row_key"]["frame_id"]
            if endpoint_frame not in endpoints:
                raise GoldenExportError(f"{source}: temporal endpoint is missing")
            endpoint = endpoints[endpoint_frame]
            sample = _exact_endpoint(sample_rows, endpoint_contract, source + " sample")
            poll = _exact_endpoint(polling_rows, endpoint_contract, source + " poll")
            if (
                int(sample["sample_time_ns"]) != endpoint_contract["sample_time_ns"]
                or int(poll["capture_time_ns"])
                != endpoint_contract["poll_capture_time_ns"]
                or int(poll["available_time_ns"])
                != endpoint_contract["poll_available_time_ns"]
                or sample["frame_type"] != endpoint_contract["sample_frame_type"]
                or (sample["actionable"] == "1")
                != endpoint_contract["sample_actionable"]
                or endpoint.primary_sample is not sample
                or endpoint.primary_poll is not poll
            ):
                raise GoldenExportError(f"{source}: endpoint provenance differs")
            exact.append(endpoint)
        current, lag1, lag3, lag8 = exact
        current_capture = int(current.primary_poll["capture_time_ns"])
        for endpoint_contract, endpoint in zip(
            endpoint_contracts, exact, strict=True
        ):
            sample_time = int(endpoint.primary_sample["sample_time_ns"])
            capture_time = int(endpoint.primary_poll["capture_time_ns"])
            if (
                sample_time - capture_time
                != endpoint_contract["sample_minus_capture_ns"]
                or current_capture - capture_time
                != endpoint_contract["current_capture_minus_capture_ns"]
            ):
                raise GoldenExportError(f"{source}: endpoint timing delta differs")
        assignment = fixture["source_assignment"]
        arm_probabilities = run.config["randomizedIntervention"]["arm_probabilities"]
        treatment_probability = float(arm_probabilities["FULL_COPY_T2"]) / (
            float(arm_probabilities["FULL_COPY_T2"])
            + float(arm_probabilities["CONTROL"])
        )
        v1 = modules.base._dataset_row(
            run, frame_id, "T2", identity["split_role"], treatment_probability
        )
        regenerated = modules.temporal._temporal_row(
            v1,
            current,
            {1: lag1, 3: lag3, 8: lag8},
            source,
        )
        frozen = temporal_rows[(identity["run_id"], frame_id)]
        _require_exact_temporal_row(
            regenerated, frozen, modules.temporal.DATASET_COLUMNS, source
        )
        if (
            frozen["assigned_arm"] != assignment["assigned_arm"]
            or (frozen["treatment"] == "1") != assignment["treatment"]
            or (frozen["attempted"] == "1") != assignment["attempted"]
            or (frozen["launched"] == "1") != assignment["launched"]
        ):
            raise GoldenExportError(f"{source}: assignment provenance differs")
        values = _feature_vector(regenerated, model_source.feature_names, modules)
        words = _feature_words(values)
        model_result = modules.model_exporter.sklearn_result(model_source, values)
        _assert_fixture_result(fixture, values, words, model_result)
        metadata_hashes = _metadata_run_map(metadata)[identity["run_id"]]["files_sha256"]
        results.append(
            GoldenCase(
                fixture,
                frozen,
                current.primary_sample,
                current.primary_poll,
                (lag1.primary_sample, lag3.primary_sample, lag8.primary_sample),
                (lag1.primary_poll, lag3.primary_poll, lag8.primary_poll),
                tuple(float(value) for value in values),
                words,
                model_result,
                dict(metadata_hashes),
            )
        )
    return model_source, tuple(results)


def _cpp_string(value: str) -> str:
    if not value.isascii():
        raise GoldenExportError("generated string is not ASCII")
    return json.dumps(value, ensure_ascii=True)


def _cpp_double(raw: str) -> str:
    value = float(raw)
    if not math.isfinite(value):
        raise GoldenExportError("non-finite C++ double")
    text = format(value, ".17g")
    if "." not in text and "e" not in text:
        text += ".0"
    return text


def _optional(raw: str, converter: Any) -> str:
    return "std::nullopt" if raw == "" else converter(raw)


def _cpp_bool(raw: str) -> str:
    if raw not in {"0", "1"}:
        raise GoldenExportError(f"invalid boolean field {raw!r}")
    return "true" if raw == "1" else "false"


def _emit_rolling(row: dict[str, str], window: str, indent: str) -> list[str]:
    suffix = f"_{window}"
    us = {"1ms": 1000, "5ms": 5000, "20ms": 20000}[window]
    values = [
        f"{us}ULL",
        f"{row['mpdu_attempts' + suffix]}ULL",
        f"{row['mpdu_positive_acks' + suffix]}ULL",
        f"{row['mpdu_attempt_failures' + suffix]}ULL",
        f"{row['mpdu_retries' + suffix]}ULL",
        _optional(row["mpdu_retry_ratio" + suffix], _cpp_double),
        f"{row['acknowledged_mac_service_bytes' + suffix]}ULL",
        _optional(row[f"mpdu_queue_to_ack_mean_{window}_us"], _cpp_double),
        _optional(row[f"mpdu_queue_to_ack_p95_{window}_us"], _cpp_double),
        _optional(row[f"mpdu_first_attempt_to_ack_mean_{window}_us"], _cpp_double),
        _optional(row[f"mpdu_first_attempt_to_ack_p95_{window}_us"], _cpp_double),
        _cpp_double(row[f"phy_tx_time_{window}_us"]),
        _cpp_double(row[f"phy_rx_time_{window}_us"]),
        _cpp_double(row[f"phy_busy_time_{window}_us"]),
        _cpp_double(row[f"phy_idle_time_{window}_us"]),
        _cpp_double(row[f"phy_other_time_{window}_us"]),
        _optional(row["phy_tx_fraction" + suffix], _cpp_double),
        _optional(row["phy_rx_fraction" + suffix], _cpp_double),
        _optional(row["phy_busy_fraction" + suffix], _cpp_double),
        _optional(row["phy_idle_fraction" + suffix], _cpp_double),
        _optional(row["phy_other_fraction" + suffix], _cpp_double),
        _cpp_double(row[f"history_coverage_{window}_us"]),
    ]
    return [
        indent + "PredictionRollingSample{",
        *(indent + "    " + value + "," for value in values),
        indent + "}",
    ]


def _emit_report(row: dict[str, str], function_name: str) -> list[str]:
    lines = [
        f"inline PredictionPollingReport {function_name}()",
        "{",
        "    PredictionPollingReport value;",
    ]
    assignments = (
        ("captureTimeNs", f"{row['capture_time_ns']}ULL"),
        ("availableTimeNs", f"{row['available_time_ns']}ULL"),
        (
            "latestFeatureEventTimeNs",
            _optional(row["latest_feature_event_time_ns"], lambda x: f"{x}ULL"),
        ),
        ("latestFeatureEventSequence", f"{row['latest_feature_event_sequence']}ULL"),
        ("mpduTxAttemptsTotal", _optional(row["mpdu_tx_attempts_total"], lambda x: f"{x}ULL")),
        ("mpduPositiveAcksTotal", _optional(row["mpdu_positive_acks_total"], lambda x: f"{x}ULL")),
        (
            "mpduTxAttemptFailuresTotal",
            _optional(row["mpdu_tx_attempt_failures_total"], lambda x: f"{x}ULL"),
        ),
        ("mpduRetriesTotal", _optional(row["mpdu_retries_total"], lambda x: f"{x}ULL")),
        (
            "mpduTerminalDropsTotal",
            _optional(row["mpdu_terminal_drops_total"], lambda x: f"{x}ULL"),
        ),
        (
            "mpduRetryLimitDropsTotal",
            _optional(row["mpdu_retry_limit_drops_total"], lambda x: f"{x}ULL"),
        ),
        (
            "mpduLifetimeDropsTotal",
            _optional(row["mpdu_lifetime_drops_total"], lambda x: f"{x}ULL"),
        ),
        ("mpduQueueDropsTotal", _optional(row["mpdu_queue_drops_total"], lambda x: f"{x}ULL")),
        ("ppduTxCountTotal", _optional(row["ppdu_tx_count_total"], lambda x: f"{x}ULL")),
        ("lastTxAttemptTimeNs", _optional(row["last_tx_attempt_time_ns"], lambda x: f"{x}ULL")),
        ("lastPositiveAckTimeNs", _optional(row["last_positive_ack_time_ns"], lambda x: f"{x}ULL")),
        ("currentMcs", _optional(row["current_mcs"], lambda x: f"{x}U")),
        ("currentNss", _optional(row["current_nss"], lambda x: f"{x}U")),
        ("currentChannelWidthMhz", _optional(row["current_channel_width_mhz"], lambda x: f"{x}U")),
        (
            "currentGuardIntervalNs",
            _optional(row["current_guard_interval_ns"], lambda x: f"{x}ULL"),
        ),
        (
            "frequencyBand",
            _optional(
                row["frequency_band"],
                lambda x: f"std::string{{{_cpp_string(x)}}}",
            ),
        ),
        ("centerFrequencyMhz", _optional(row["center_frequency_mhz"], _cpp_double)),
        ("currentAckSignalDbm", _optional(row["current_ack_signal_dbm"], _cpp_double)),
    )
    lines.extend(f"    value.{name} = {value};" for name, value in assignments)
    lines.append("    value.rolling = {")
    for window in ("1ms", "5ms", "20ms"):
        rolling = _emit_rolling(row, window, "        ")
        rolling[-1] += ","
        lines.extend(rolling)
    lines.extend(
        [
            "    };",
            f"    value.featureSupportMask = {_cpp_string(row['feature_support_mask'])};",
            "    return value;",
            "}",
            "",
        ]
    )
    return lines


def _emit_sample(row: dict[str, str], report_function: str, function_name: str) -> list[str]:
    lines = [f"inline PredictionSample {function_name}()", "{", "    PredictionSample value;"]
    plain = (
        ("telemetrySchemaVersion", f"{row['telemetry_schema_version']}U"),
        ("runId", _cpp_string(row["run_id"])),
        ("key.frameId", f"{row['frame_id']}ULL"),
        ("key.pathId", f"{row['path_id']}U"),
        ("key.copyId", f"{row['copy_id']}U"),
        ("sampleStage", _cpp_string(row["sample_stage"])),
        ("sampleOffsetUs", f"{row['sample_offset_us']}ULL"),
        ("sampleTimeNs", f"{row['sample_time_ns']}ULL"),
        (
            "latestFeatureEventTimeNs",
            _optional(row["latest_feature_event_time_ns"], lambda x: f"{x}ULL"),
        ),
        ("latestFeatureEventSequence", f"{row['latest_feature_event_sequence']}ULL"),
        ("generationTimeNs", f"{row['generation_time_ns']}ULL"),
        ("deadlineTimeNs", f"{row['deadline_time_ns']}ULL"),
        ("frameAgeUs", f"{row['frame_age_us']}ULL"),
        ("deadlineSlackUs", f"{row['deadline_slack_us']}ULL"),
        ("senderMacComplete", _cpp_bool(row["sender_mac_complete"])),
        ("actionable", _cpp_bool(row["actionable"])),
        ("frameSizeBytes", f"{row['frame_size_bytes']}U"),
        ("framePacketCount", f"{row['frame_packet_count']}U"),
        ("frameType", f"FrameType::{row['frame_type']}"),
        ("packetsSubmitted", f"{row['packets_submitted']}U"),
        (
            "applicationSocketPacketBytesSubmitted",
            f"{row['application_socket_packet_bytes_submitted']}ULL",
        ),
        ("packetsRemainingToSubmit", f"{row['packets_remaining_to_submit']}U"),
    )
    lines.extend(f"    value.{name} = {value};" for name, value in plain)
    optional_int_fields = (
        ("mpduTxAttemptsTotal", "mpdu_tx_attempts_total", "ULL"),
        ("mpduPositiveAcksTotal", "mpdu_positive_acks_total", "ULL"),
        ("mpduTxAttemptFailuresTotal", "mpdu_tx_attempt_failures_total", "ULL"),
        ("mpduRetriesTotal", "mpdu_retries_total", "ULL"),
        ("mpduTerminalDropsTotal", "mpdu_terminal_drops_total", "ULL"),
        ("mpduRetryLimitDropsTotal", "mpdu_retry_limit_drops_total", "ULL"),
        ("mpduLifetimeDropsTotal", "mpdu_lifetime_drops_total", "ULL"),
        ("mpduQueueDropsTotal", "mpdu_queue_drops_total", "ULL"),
        ("ppduTxCountTotal", "ppdu_tx_count_total", "ULL"),
        ("lastTxAttemptTimeNs", "last_tx_attempt_time_ns", "ULL"),
        ("lastPositiveAckTimeNs", "last_positive_ack_time_ns", "ULL"),
        ("currentMcs", "current_mcs", "U"),
        ("currentNss", "current_nss", "U"),
        ("currentChannelWidthMhz", "current_channel_width_mhz", "U"),
        ("currentGuardIntervalNs", "current_guard_interval_ns", "ULL"),
        ("framePacketsMacEnqueued", "frame_packets_mac_enqueued", "U"),
        ("framePacketsMacDequeued", "frame_packets_mac_dequeued", "U"),
        ("framePacketsTxSucceeded", "frame_packets_tx_succeeded", "U"),
        ("frameMpduAttemptFailures", "frame_mpdu_attempt_failures", "U"),
        ("framePacketsTerminallyDropped", "frame_packets_terminally_dropped", "U"),
        ("framePacketsCurrentlyQueued", "frame_packets_currently_queued", "U"),
        ("frameMacServiceBytesCurrentlyQueued", "frame_mac_service_bytes_currently_queued", "ULL"),
        ("macQueuePackets", "mac_queue_packets", "U"),
        ("macQueueServiceBytes", "mac_queue_service_bytes", "ULL"),
        ("macQueueOldestEnqueueTimeNs", "mac_queue_oldest_enqueue_time_ns", "ULL"),
        ("packetsAheadOfFrame", "packets_ahead_of_frame", "U"),
        ("macServiceBytesAheadOfFrame", "mac_service_bytes_ahead_of_frame", "ULL"),
        ("framePacketsPendingPrimary", "frame_packets_pending_primary", "U"),
        ("frameMacServiceBytesNotAcknowledged", "frame_mac_service_bytes_not_acknowledged", "ULL"),
        ("frameMacServiceBytesPendingPrimary", "frame_mac_service_bytes_pending_primary", "ULL"),
        ("currentCw", "current_cw", "U"),
        ("remainingBackoffSlots", "remaining_backoff_slots", "U"),
    )
    for member, field, suffix in optional_int_fields:
        lines.append(
            f"    value.{member} = {_optional(row[field], lambda x, s=suffix: f'{x}{s}')};"
        )
    for member, field in (
        ("centerFrequencyMhz", "center_frequency_mhz"),
        ("currentAckSignalDbm", "current_ack_signal_dbm"),
        ("navRemainingUs", "nav_remaining_us"),
    ):
        lines.append(f"    value.{member} = {_optional(row[field], _cpp_double)};")
    for member, field in (
        ("frequencyBand", "frequency_band"),
        ("currentPhyState", "current_phy_state"),
        ("channelAccessStatus", "channel_access_status"),
        ("expectedAccessReasonWithinSlack", "expected_access_reason_within_slack"),
    ):
        lines.append(
            f"    value.{member} = "
            f"{_optional(row[field], lambda x: f'std::string{{{_cpp_string(x)}}}')};"
        )
    lines.append(
        f"    value.mediumBusyNow = {_optional(row['medium_busy_now'], _cpp_bool)};"
    )
    lines.append("    value.rolling = {")
    for window in ("1ms", "5ms", "20ms"):
        rolling = _emit_rolling(row, window, "        ")
        rolling[-1] += ","
        lines.extend(rolling)
    lines.extend(
        [
            "    };",
            f"    value.featureSupportMask = {_cpp_string(row['feature_support_mask'])};",
            f"    value.pollingReport = {report_function}();",
            "    return value;",
            "}",
            "",
        ]
    )
    return lines


def emit_header(model_source: Any, cases: Sequence[GoldenCase]) -> str:
    """Emit a standalone deterministic C++ golden header."""

    lines = [
        "/*",
        " * SPDX-License-Identifier: GPL-2.0-only",
        " *",
        " * Generated by tools/export_temporal_t2_feature_adapter_goldens_v1.py.",
        " * Do not edit.",
        " */",
        "",
        "#ifndef TEMPORAL_T2_FEATURE_ADAPTER_GOLDEN_V1_H",
        "#define TEMPORAL_T2_FEATURE_ADAPTER_GOLDEN_V1_H",
        "",
        '#include "ns3/prediction-telemetry-collector.h"',
        '#include "ns3/temporal-t2-value-model-evaluator.h"',
        "",
        "#include <array>",
        "#include <cstddef>",
        "#include <cstdint>",
        "#include <optional>",
        "#include <span>",
        "#include <string>",
        "#include <string_view>",
        "",
        "namespace ns3",
        "{",
        "namespace temporal_t2_feature_adapter_golden_v1",
        "{",
        "",
        "inline constexpr std::string_view CONTRACT_SHA256 =",
        f"    {_cpp_string(EXPECTED_CONTRACT_SHA256)};",
        "inline constexpr std::string_view ORDERED_FEATURE_NAMES_SHA256 =",
        f"    {_cpp_string(EXPECTED_ORDERED_FEATURE_NAMES_SHA256)};",
        "inline constexpr std::string_view SOURCE_MODEL_SHA256 =",
        f"    {_cpp_string(model_source.file_hashes['temporal_t2_value_models.pkl'])};",
        "inline constexpr std::string_view SOURCE_SELECTION_SHA256 =",
        f"    {_cpp_string(model_source.file_hashes['selection'])};",
        "inline constexpr std::string_view SOURCE_DATASET_SHA256 =",
        f"    {_cpp_string(model_source.file_hashes['dataset_csv'])};",
        "",
        "/** Immutable provenance for one exact lag report. */",
        "struct TemporalT2FeatureAdapterGoldenLag",
        "{",
        "    uint64_t lagFrames; ///< Exact lag distance in application frames.",
        "    uint64_t sourceFrameId; ///< Source application-frame identifier.",
        "    uint8_t sourcePathId; ///< Source primary path identifier.",
        "    uint8_t sourceCopyId; ///< Source primary copy identifier.",
        "    std::string_view sourceStage; ///< Source prediction stage.",
        "    FrameType sourceFrameType; ///< Source application-frame type.",
        "    bool sourceSampleActionable; ///< Source sample actionability.",
        "    uint64_t sourceSampleTimeNs; ///< Source sample time in nanoseconds.",
        "    uint64_t pollCaptureTimeNs; ///< Report capture time in nanoseconds.",
        "    uint64_t pollAvailableTimeNs; ///< Report availability time in nanoseconds.",
        "    PredictionPollingReport report; ///< Owned exact-lag delayed report.",
        "};",
        "",
        "/** One isolated direct-adapter fixture; it does not prove history storage. */",
        "struct TemporalT2FeatureAdapterGoldenCase",
        "{",
        "    std::string_view fixtureId; ///< Frozen fixture identifier.",
        "    uint32_t seed; ///< ns-3 run seed.",
        "    uint32_t runNumber; ///< ns-3 run number.",
        "    uint64_t temporalCsvRowIndex; ///< Zero-based temporal CSV row index.",
        "    uint64_t temporalCsvLineNumber; ///< One-based CSV line including header.",
        "    std::string_view splitRole; ///< Frozen dataset split role.",
        "    std::string_view assignedArm; ///< Randomized source assignment.",
        "    bool treatment; ///< Whether the source row was treated.",
        "    bool attempted; ///< Whether intervention execution was attempted.",
        "    bool launched; ///< Whether the intervention was launched.",
        "    PredictionSample currentSample; ///< Complete owned current sample.",
        "    std::array<TemporalT2FeatureAdapterGoldenLag, 3> lags; ///< Exact lag reports.",
        "    std::array<uint32_t, 246> expectedFeatureWords; ///< Canonical feature words.",
        "    std::array<uint16_t, 16> expectedNanIndices; ///< Padded expected NaN indices.",
        "    std::size_t expectedNanCount; ///< Number of valid expected NaN indices.",
        "    TemporalT2ValueModelResult expectedModelResult; ///< Expected model diagnostics.",
        "    bool expectedPFrameGate; ///< Expected caller-owned P-frame gate.",
        "    bool expectedScoreAndPFrameGates; ///< Expected combined gate result.",
        "    std::string_view featureWordsSha256; ///< Canonical feature-word digest.",
        "    std::string_view resolvedConfigSha256; ///< Source resolved-config digest.",
        "    std::string_view predictionSamplesSha256; ///< Source live-sample digest.",
        "    std::string_view predictionPollingSamplesSha256; ///< Source polling digest.",
        "};",
        "",
    ]
    for index, case in enumerate(cases):
        current_report_fn = f"MakeCase{index}CurrentReport"
        lines.extend(_emit_report(case.current_report, current_report_fn))
        lag_report_functions = []
        for lag_index, report in enumerate(case.lag_reports):
            function = f"MakeCase{index}Lag{EXPECTED_LAGS[lag_index]}Report"
            lag_report_functions.append(function)
            lines.extend(_emit_report(report, function))
        sample_fn = f"MakeCase{index}CurrentSample"
        lines.extend(_emit_sample(case.current_sample, current_report_fn, sample_fn))
        fixture = case.contract
        identity = fixture["identity"]
        assignment = fixture["source_assignment"]
        expected = fixture["expected_model_result"]
        lines.extend(
            [
                f"inline TemporalT2FeatureAdapterGoldenCase MakeCase{index}()",
                "{",
                "    TemporalT2FeatureAdapterGoldenCase value;",
                f"    value.fixtureId = {_cpp_string(fixture['fixture_id'])};",
                f"    value.seed = {identity['seed']}U;",
                f"    value.runNumber = {identity['run_number']}U;",
                "    value.temporalCsvRowIndex = "
                f"{identity['temporal_csv_zero_based_row_index']}ULL;",
                "    value.temporalCsvLineNumber = "
                f"{identity['temporal_csv_line_number_including_header']}ULL;",
                f"    value.splitRole = {_cpp_string(identity['split_role'])};",
                f"    value.assignedArm = {_cpp_string(assignment['assigned_arm'])};",
                f"    value.treatment = {str(assignment['treatment']).lower()};",
                f"    value.attempted = {str(assignment['attempted']).lower()};",
                f"    value.launched = {str(assignment['launched']).lower()};",
                f"    value.currentSample = {sample_fn}();",
                "    value.lags = {{",
            ]
        )
        for lag_index, (sample, report, function) in enumerate(
            zip(case.lag_samples, case.lag_reports, lag_report_functions, strict=True)
        ):
            lag = EXPECTED_LAGS[lag_index]
            lines.extend(
                [
                    "        TemporalT2FeatureAdapterGoldenLag{",
                    f"            {lag}ULL,",
                    f"            {sample['frame_id']}ULL,",
                    f"            {sample['path_id']}U,",
                    f"            {sample['copy_id']}U,",
                    f"            {_cpp_string(sample['sample_stage'])},",
                    f"            FrameType::{sample['frame_type']},",
                    f"            {_cpp_bool(sample['actionable'])},",
                    f"            {sample['sample_time_ns']}ULL,",
                    f"            {report['capture_time_ns']}ULL,",
                    f"            {report['available_time_ns']}ULL,",
                    f"            {function}(),",
                    "        },",
                ]
            )
        lines.extend(["    }};", "    value.expectedFeatureWords = {{"])
        for offset in range(0, len(case.feature_words), 6):
            chunk = case.feature_words[offset : offset + 6]
            lines.append("        " + ", ".join(f"0x{word:08x}U" for word in chunk) + ",")
        nan_indices = list(fixture["expected_feature_vector"]["nan_indices"])
        padded = nan_indices + [0] * (16 - len(nan_indices))
        lines.extend(
            [
                "    }};",
                "    value.expectedNanIndices = {{",
                "        " + ", ".join(f"{value}U" for value in padded[:8]) + ",",
                "        " + ", ".join(f"{value}U" for value in padded[8:]) + ",",
                "    }};",
                f"    value.expectedNanCount = {len(nan_indices)}U;",
                "    value.expectedModelResult = TemporalT2ValueModelResult{",
                f"        {_cpp_double(str(expected['primary_bad12_logit']))},",
                f"        {_cpp_double(str(expected['primary_bad12_probability']))},",
                f"        {_cpp_double(str(expected['treated_bad12_logit']))},",
                f"        {_cpp_double(str(expected['treated_bad12_probability']))},",
                f"        {_cpp_double(str(expected['predicted_log_airtime_raw_ridge']))},",
                f"        {_cpp_double(str(expected['predicted_secondary_airtime_us']))},",
                f"        {_cpp_double(str(expected['nonnegative_bad12_value']))},",
                f"        {format(expected['value_per_cost_score_float32'], '.9g')}F,",
                f"        {str(expected['passes_score_threshold']).lower()},",
                "    };",
                f"    value.expectedPFrameGate = {str(expected['passes_p_frame_gate']).lower()};",
                "    value.expectedScoreAndPFrameGates = "
                f"{str(expected['passes_score_and_p_frame_gates']).lower()};",
                "    value.featureWordsSha256 =",
                "        "
                + _cpp_string(
                    fixture["expected_feature_vector"][
                        "canonical_float32_words_sha256"
                    ]
                )
                + ";",
                "    value.resolvedConfigSha256 =",
                f"        {_cpp_string(case.source_hashes['resolved_config.json'])};",
                "    value.predictionSamplesSha256 =",
                f"        {_cpp_string(case.source_hashes['prediction_samples.csv'])};",
                "    value.predictionPollingSamplesSha256 =",
                f"        {_cpp_string(case.source_hashes['prediction_polling_samples.csv'])};",
                "    return value;",
                "}",
                "",
            ]
        )
    lines.extend(
        [
            "/** Return the five immutable direct-adapter fixtures. */",
            "inline std::span<const TemporalT2FeatureAdapterGoldenCase> GetCases()",
            "{",
            "    static const std::array<TemporalT2FeatureAdapterGoldenCase, 5> cases{{",
            *[f"        MakeCase{index}()," for index in range(len(cases))],
            "    }};",
            "    return cases;",
            "}",
            "",
            "} // namespace temporal_t2_feature_adapter_golden_v1",
            "} // namespace ns3",
            "",
            "#endif // TEMPORAL_T2_FEATURE_ADAPTER_GOLDEN_V1_H",
            "",
        ]
    )
    return "\n".join(lines)


def write_or_check(path: Path, content: str, check: bool) -> None:
    """Write deterministic output or reject a stale checked-in copy."""

    if path.is_symlink():
        raise GoldenExportError(f"refusing symlink output: {path}")
    if check:
        try:
            current = path.read_text(encoding="utf-8")
        except OSError as error:
            raise GoldenExportError(f"generated output is absent: {path}") from error
        if current != content:
            raise GoldenExportError(f"generated output is stale: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as destination:
            destination.write(content)
            destination.flush()
            os.fsync(destination.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-root", type=Path, default=CANONICAL_RUNS_ROOT)
    parser.add_argument("--dataset-dir", type=Path, default=CANONICAL_DATASET_DIR)
    parser.add_argument("--artifact-dir", type=Path, default=CANONICAL_ARTIFACT_DIR)
    parser.add_argument("--selection", type=Path, default=CANONICAL_SELECTION)
    parser.add_argument("--contract", type=Path, default=CANONICAL_CONTRACT)
    parser.add_argument("--output", type=Path, default=CANONICAL_OUTPUT)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    contract = _load_contract(args.contract, require_canonical_path=True)
    protected = _protected_output_paths(
        contract,
        args.contract,
        args.runs_root,
        args.dataset_dir,
        args.artifact_dir,
        args.selection,
    )
    output_path = _validate_output_path(args.output, protected)
    model_source, cases = build_goldens(
        contract,
        args.runs_root,
        args.dataset_dir,
        args.artifact_dir,
        args.selection,
    )
    content = emit_header(model_source, cases)
    write_or_check(output_path, content, args.check)
    print(
        json.dumps(
            {
                "contract_sha256": EXPECTED_CONTRACT_SHA256,
                "fixture_count": len(cases),
                "output": str(output_path),
                "output_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GoldenExportError as error:
        raise SystemExit(f"error: {error}") from error
