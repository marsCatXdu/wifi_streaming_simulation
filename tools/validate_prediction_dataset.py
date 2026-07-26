#!/usr/bin/env python3
"""Validate an Increment-2 labelled prediction dataset and grouped split."""

from __future__ import annotations

import argparse
import heapq
import itertools
import json
import math
import os
import statistics
import struct
import sys
import tempfile
from array import array
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from prediction_dataset import (
    ATTACHED_COLUMNS,
    CONTEXT_COLUMNS,
    DATASET_SCHEMA_VERSION,
    DERIVED_COLUMNS,
    ELIGIBILITY_COLUMNS,
    OUTCOME_COLUMNS,
    SPLIT_ROLES,
    SourceRun,
    csv_header,
    discover_source_runs,
    feature_dictionary,
    iter_csv,
    load_regime,
    make_run_group_id,
    read_csv,
    read_yaml,
    scenario_name,
    selected_link,
    sha256_file,
    type7_quantile,
    validate_analysis_config,
    validate_ood_run_filter,
)
from validate_outputs import (
    PREDICTION_BASE_COLUMNS,
    PREDICTION_FIELD_SUPPORT_BITS,
    ValidationError,
    validate_run,
)


class DatasetValidationError(ValueError):
    """Raised when a labelled dataset violates its frozen contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DatasetValidationError(message)


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DatasetValidationError(f"{path}: invalid JSON: {error}") from error
    _require(isinstance(value, dict), f"{path}: JSON root must be an object")
    return value


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _read_dataset(
    dataset_dir: Path,
    manifest: dict[str, Any],
) -> tuple[list[str], list[dict[str, str]]]:
    path = dataset_dir / manifest["dataset_file"]
    actual_format = manifest["actual_format"]
    if actual_format == "csv":
        return read_csv(path)
    if actual_format != "parquet":
        raise DatasetValidationError(f"unsupported dataset format: {actual_format}")
    try:
        import pyarrow.parquet as pq  # type: ignore[import-not-found]
    except ImportError as error:
        raise DatasetValidationError("pyarrow is required to validate Parquet") from error
    table = pq.read_table(path)
    columns = table.column_names
    rows = [
        {key: _as_text(value) for key, value in row.items()}
        for row in table.to_pylist()
    ]
    return columns, rows


def _dataset_columns(dataset_dir: Path, manifest: dict[str, Any]) -> list[str]:
    path = dataset_dir / manifest["dataset_file"]
    if manifest["actual_format"] == "csv":
        return csv_header(path)
    if manifest["actual_format"] != "parquet":
        raise DatasetValidationError(
            f"unsupported dataset format: {manifest['actual_format']}"
        )
    try:
        import pyarrow.parquet as pq  # type: ignore[import-not-found]
    except ImportError as error:
        raise DatasetValidationError("pyarrow is required to validate Parquet") from error
    return pq.ParquetFile(path).schema_arrow.names


def _iter_dataset(
    dataset_dir: Path,
    manifest: dict[str, Any],
) -> Any:
    path = dataset_dir / manifest["dataset_file"]
    if manifest["actual_format"] == "csv":
        yield from iter_csv(path)
        return
    try:
        import pyarrow.parquet as pq  # type: ignore[import-not-found]
    except ImportError as error:
        raise DatasetValidationError("pyarrow is required to validate Parquet") from error
    for batch in pq.ParquetFile(path).iter_batches(batch_size=8192):
        for row in batch.to_pylist():
            yield {key: _as_text(value) for key, value in row.items()}


def _number(value: str, field: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise DatasetValidationError(f"invalid numeric value for {field}: {value}") from error
    _require(math.isfinite(parsed), f"nonfinite numeric value for {field}")
    return parsed


def _integer(value: str, field: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise DatasetValidationError(f"invalid integer value for {field}: {value}") from error
    _require(parsed >= 0, f"negative integer value for {field}")
    return parsed


def _optional_integer(value: str, field: str) -> int | None:
    return None if value == "" else _integer(value, field)


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-6)


def _source_map(manifest: dict[str, Any]) -> dict[Path, SourceRun]:
    sources = discover_source_runs([Path(path) for path in manifest["source_roots"]])
    return {source.run_dir.resolve(): source for source in sources}


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    mean_left = statistics.mean(left)
    mean_right = statistics.mean(right)
    numerator = sum(
        (x - mean_left) * (y - mean_right) for x, y in zip(left, right)
    )
    denominator = math.sqrt(
        sum((x - mean_left) ** 2 for x in left)
        * sum((y - mean_right) ** 2 for y in right)
    )
    return numerator / denominator if denominator else None


def _distribution_report(
    rows: list[dict[str, str]],
    columns: list[str],
    dictionary: dict[str, dict[str, Any]],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    list[str],
    list[dict[str, Any]],
]:
    missingness: dict[str, Any] = {}
    numeric_quantiles: dict[str, Any] = {}
    constant_features: list[str] = []
    suspicious: list[dict[str, Any]] = []
    labels = [float(_integer(row["deadline_miss"], "deadline_miss")) for row in rows]
    for column in columns:
        values = [row[column] for row in rows]
        observed = [value for value in values if value != ""]
        missingness[column] = {
            "missing_count": len(values) - len(observed),
            "missing_rate": (len(values) - len(observed)) / len(values),
        }
        if not dictionary[column]["model_eligible"]:
            continue
        distinct = set(observed)
        if len(distinct) <= 1:
            constant_features.append(column)
        parsed: list[float] = []
        numeric = True
        for value in values:
            if value == "":
                continue
            try:
                number = float(value)
            except ValueError:
                numeric = False
                break
            if not math.isfinite(number):
                numeric = False
                break
            parsed.append(number)
        if not numeric or not parsed:
            continue
        numeric_quantiles[column] = {
            "count": len(parsed),
            "min": type7_quantile(parsed, 0),
            "p25": type7_quantile(parsed, 0.25),
            "p50": type7_quantile(parsed, 0.5),
            "p75": type7_quantile(parsed, 0.75),
            "max": type7_quantile(parsed, 1),
        }
        paired = [
            (float(row[column]), labels[index])
            for index, row in enumerate(rows)
            if row[column] != ""
        ]
        correlation = _pearson(
            [pair[0] for pair in paired],
            [pair[1] for pair in paired],
        )
        if correlation is not None and abs(correlation) >= 0.995:
            suspicious.append({"feature": column, "label_correlation": correlation})
    return missingness, numeric_quantiles, sorted(constant_features), suspicious


def _stratified_class_balance(
    rows: list[dict[str, str]],
) -> dict[str, dict[str, dict[str, Any]]]:
    strata = {
        "scenario": ("scenario_name",),
        "selected_policy": ("selected_policy",),
        "miss_regime": ("miss_regime",),
        "scenario_policy_regime": (
            "scenario_name",
            "selected_policy",
            "miss_regime",
        ),
    }
    report: dict[str, dict[str, dict[str, Any]]] = {}
    for stratum, columns in strata.items():
        groups: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = "|".join(row[column] for column in columns)
            entry = groups.setdefault(
                key,
                {
                    "sample_count": 0,
                    "frame_labels": {},
                    "run_ids": set(),
                    "run_group_ids": set(),
                },
            )
            entry["sample_count"] += 1
            entry["run_ids"].add(row["run_id"])
            entry["run_group_ids"].add(row["run_group_id"])
            frame_key = (row["run_id"], row["frame_id"])
            label = _integer(row["deadline_miss"], "deadline_miss")
            previous = entry["frame_labels"].setdefault(frame_key, label)
            _require(previous == label, f"inconsistent frame label in {stratum}")
        report[stratum] = {}
        for key, entry in sorted(groups.items()):
            labels = entry["frame_labels"]
            misses = sum(labels.values())
            frames = len(labels)
            report[stratum][key] = {
                "frame_count": frames,
                "miss_count": misses,
                "miss_rate": misses / frames if frames else 0.0,
                "run_count": len(entry["run_ids"]),
                "run_group_count": len(entry["run_group_ids"]),
                "sample_count": entry["sample_count"],
            }
    return report


class _DistributionAccumulator:
    """Bounded-memory distribution statistics with exact values on disk."""

    def __init__(
        self,
        columns: list[str],
        dictionary: dict[str, dict[str, Any]],
        directory: Path,
    ) -> None:
        self.columns = columns
        self.dictionary = dictionary
        self.total = 0
        self.missing = Counter()
        self.first: dict[str, str] = {}
        self.nonconstant: set[str] = set()
        self.numeric = {
            column: bool(dictionary[column]["model_eligible"]) for column in columns
        }
        self.counts = Counter()
        self.paths = {
            column: directory / f"values-{index}.bin"
            for index, column in enumerate(columns)
            if dictionary[column]["model_eligible"]
        }
        self.buffers = {
            column: array("d")
            for column in columns
            if dictionary[column]["model_eligible"]
        }
        self.handles: dict[str, Any] = {}
        self.correlation: dict[str, list[float]] = defaultdict(
            lambda: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        )

    def add(self, row: dict[str, str]) -> None:
        self.total += 1
        label = float(_integer(row["deadline_miss"], "deadline_miss"))
        for column in self.columns:
            value = row[column]
            if value == "":
                self.missing[column] += 1
                continue
            if column not in self.first:
                self.first[column] = value
            elif value != self.first[column]:
                self.nonconstant.add(column)
            if not self.dictionary[column]["model_eligible"] or not self.numeric[column]:
                continue
            try:
                number = float(value)
            except ValueError:
                self.numeric[column] = False
                self._close_column(column, remove=True)
                continue
            if not math.isfinite(number):
                self.numeric[column] = False
                self._close_column(column, remove=True)
                continue
            buffer = self.buffers[column]
            buffer.append(number)
            if len(buffer) >= 8192:
                self._flush_column(column)
            self.counts[column] += 1
            count, mean_x, mean_y, sum_xx, sum_yy, co_moment = self.correlation[
                column
            ]
            count += 1
            dx = number - mean_x
            mean_x += dx / count
            dy = label - mean_y
            mean_y += dy / count
            sum_xx += dx * (number - mean_x)
            sum_yy += dy * (label - mean_y)
            co_moment += dx * (label - mean_y)
            self.correlation[column][:] = [
                count,
                mean_x,
                mean_y,
                sum_xx,
                sum_yy,
                co_moment,
            ]

    def _flush_column(self, column: str) -> None:
        buffer = self.buffers[column]
        if not buffer:
            return
        handle = self.handles.get(column)
        if handle is None:
            handle = self.paths[column].open("ab")
            self.handles[column] = handle
        if sys.byteorder == "little":
            handle.write(buffer.tobytes())
        else:
            little_endian = array("d", buffer)
            little_endian.byteswap()
            handle.write(little_endian.tobytes())
        del buffer[:]

    def _close_column(self, column: str, remove: bool = False) -> None:
        if not remove:
            self._flush_column(column)
        else:
            del self.buffers[column][:]
        handle = self.handles.pop(column, None)
        if handle is not None:
            handle.close()
        if remove:
            self.paths[column].unlink(missing_ok=True)

    def close(self, discard: bool = False) -> None:
        for column in self.buffers:
            self._close_column(column, remove=discard)

    def __del__(self) -> None:
        self.close(discard=True)

    def finish(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], list[str], list[dict[str, Any]]]:
        self.close()
        missingness = {
            column: {
                "missing_count": self.missing[column],
                "missing_rate": self.missing[column] / self.total,
            }
            for column in self.columns
        }
        constants = sorted(
            column
            for column in self.columns
            if self.dictionary[column]["model_eligible"]
            and column not in self.nonconstant
        )
        quantiles: dict[str, Any] = {}
        suspicious: list[dict[str, Any]] = []
        for column in self.columns:
            if not self.numeric.get(column) or not self.counts[column]:
                continue
            values = _external_quantiles(self.paths[column], self.counts[column])
            quantiles[column] = {
                "count": self.counts[column],
                "min": values[0],
                "p25": values[1],
                "p50": values[2],
                "p75": values[3],
                "max": values[4],
            }
            state = self.correlation[column]
            correlation = (
                state[5] / math.sqrt(state[3] * state[4])
                if state[3] and state[4]
                else None
            )
            if correlation is not None and abs(correlation) >= 0.995:
                suspicious.append(
                    {"feature": column, "label_correlation": correlation}
                )
            self.paths[column].unlink(missing_ok=True)
        return missingness, quantiles, constants, suspicious


def _binary_values(path: Path) -> Any:
    with path.open("rb") as source:
        while block := source.read(8 * 131072):
            yield from (value[0] for value in struct.iter_unpack("<d", block))


def _external_quantiles(path: Path, count: int) -> list[float]:
    """Compute type-7 quantiles with bounded in-memory sort chunks."""
    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        values = np.fromfile(path, dtype="<f8", count=count)
        if len(values) != count:
            raise DatasetValidationError(f"truncated numeric values file: {path}")
        values.sort()
        positions = (
            0.0,
            0.25 * (count - 1),
            0.5 * (count - 1),
            0.75 * (count - 1),
            count - 1.0,
        )
        result = []
        for position in positions:
            lower = math.floor(position)
            upper = math.ceil(position)
            if lower == upper:
                result.append(float(values[lower]))
            else:
                result.append(
                    float(
                        values[lower] * (upper - position)
                        + values[upper] * (position - lower)
                    )
                )
        return result

    chunks: list[Path] = []
    with path.open("rb") as source:
        while block := source.read(8 * 131072):
            values = [value[0] for value in struct.iter_unpack("<d", block)]
            values.sort()
            chunk = path.with_name(f"{path.name}.chunk-{len(chunks)}")
            with chunk.open("wb") as output:
                output.write(struct.pack(f"<{len(values)}d", *values))
            chunks.append(chunk)
    positions = [0.0, 0.25 * (count - 1), 0.5 * (count - 1), 0.75 * (count - 1), count - 1.0]
    needed = {math.floor(position) for position in positions} | {
        math.ceil(position) for position in positions
    }
    selected: dict[int, float] = {}
    streams = [_binary_values(chunk) for chunk in chunks]
    for index, value in enumerate(heapq.merge(*streams)):
        if index in needed:
            selected[index] = value
        if index >= max(needed):
            break
    for chunk in chunks:
        chunk.unlink(missing_ok=True)
    result = []
    for position in positions:
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            result.append(selected[lower])
        else:
            result.append(
                selected[lower] * (upper - position)
                + selected[upper] * (position - lower)
            )
    return result


def _validate_dataset_materialized(
    dataset_dir: Path | str,
    analysis_path: Path | str | None = None,
) -> dict[str, Any]:
    dataset_dir = Path(dataset_dir).resolve()
    manifest = _json(dataset_dir / "dataset_manifest.json")
    splits = _json(dataset_dir / "splits.json")
    _require(
        manifest.get("dataset_schema_version") == DATASET_SCHEMA_VERSION,
        "unsupported dataset schema version",
    )
    resolved_analysis_path = (
        Path(analysis_path).resolve()
        if analysis_path is not None
        else Path(manifest["analysis_config"]).resolve()
    )
    analysis = read_yaml(resolved_analysis_path)
    validate_analysis_config(analysis)
    load_path = Path(manifest["load_config"]).resolve()
    loads = read_yaml(load_path)
    _require(
        sha256_file(resolved_analysis_path) == manifest["analysis_config_sha256"],
        "analysis configuration checksum mismatch",
    )
    _require(
        sha256_file(load_path) == manifest["load_config_sha256"],
        "load configuration checksum mismatch",
    )
    _require(
        splits["analysis_config_sha256"] == manifest["analysis_config_sha256"],
        "split and dataset analysis identities differ",
    )
    dataset_path = dataset_dir / manifest["dataset_file"]
    _require(dataset_path.is_file(), "labelled dataset file is missing")
    _require(
        sha256_file(dataset_path) == manifest["dataset_sha256"],
        "labelled dataset checksum mismatch",
    )
    columns, rows = _read_dataset(dataset_dir, manifest)
    _require(bool(rows), "labelled dataset is empty")
    _require(len(columns) == len(set(columns)), "dataset contains duplicate columns")
    required_columns = set(ATTACHED_COLUMNS + DERIVED_COLUMNS) | PREDICTION_BASE_COLUMNS
    _require(required_columns <= set(columns), "dataset is missing required columns")
    expected_dictionary = feature_dictionary(columns)
    _require(
        manifest["feature_dictionary"] == expected_dictionary,
        "feature dictionary does not match dataset columns",
    )
    prohibited = CONTEXT_COLUMNS | OUTCOME_COLUMNS | ELIGIBILITY_COLUMNS
    leaked = sorted(
        column
        for column, entry in expected_dictionary.items()
        if entry["model_eligible"] and column in prohibited
    )
    _require(not leaked, f"model-eligible allowlist leaks prohibited fields: {leaked}")

    split_groups = splits.get("groups")
    _require(isinstance(split_groups, list), "splits.json groups must be a list")
    group_roles: dict[str, str] = {}
    split_run_ids: dict[str, str] = {}
    for group in split_groups:
        group_id = group["run_group_id"]
        role = group["split_role"]
        _require(group_id not in group_roles, f"duplicate split group: {group_id}")
        _require(role in SPLIT_ROLES, f"invalid split role: {role}")
        group_roles[group_id] = role
        for run_id in group["run_ids"]:
            _require(run_id not in split_run_ids, f"run appears in two split groups: {run_id}")
            split_run_ids[run_id] = group_id

    source_by_path = _source_map(manifest)
    included_runs = manifest.get("included_runs")
    _require(isinstance(included_runs, list) and included_runs, "manifest has no runs")
    manifest_runs = {item["run_id"]: item for item in included_runs}
    _require(
        len(manifest_runs) == len(included_runs),
        "manifest contains duplicate included run IDs",
    )
    _require(
        set(manifest_runs) == set(split_run_ids),
        "split run IDs do not equal included run IDs",
    )

    raw_rows_by_key: dict[
        tuple[str, str, str, str, str, str],
        tuple[dict[str, str], dict[str, str], list[str]],
    ] = {}
    run_context: dict[str, dict[str, Any]] = {}
    try:
        for run_id, item in manifest_runs.items():
            run_dir = Path(item["source_directory"]).resolve()
            _require(run_dir in source_by_path, f"source run is absent from source roots: {run_id}")
            source = source_by_path[run_dir]
            validate_run(run_dir)
            config = _json(run_dir / "resolved_config.json")
            build = _json(run_dir / "build_info.json")
            _require(config["run_id"] == run_id, f"source run ID mismatch: {run_id}")
            _require(
                make_run_group_id(source, config, build) == item["run_group_id"],
                f"run-group identity is not reproducible: {run_id}",
            )
            _require(
                scenario_name(config) == item["scenario_name"],
                f"scenario identity is not reproducible: {run_id}",
            )
            validate_ood_run_filter(config, item["scenario_name"], analysis)
            _require(
                selected_link(config["policy"]) == item["selected_path"],
                f"selected fixed path is not reproducible: {run_id}",
            )
            for name, expected_hash in manifest["source_checksums"][run_id].items():
                _require(
                    sha256_file(run_dir / name) == expected_hash,
                    f"source checksum changed: {run_id}/{name}",
                )
            raw_header, samples = read_csv(run_dir / "prediction_samples.csv")
            _require(
                not (set(raw_header) & OUTCOME_COLUMNS),
                f"raw prediction samples contain outcomes: {run_id}",
            )
            _, frames = read_csv(run_dir / "frames.csv")
            frames_by_id = {frame["frame_id"]: frame for frame in frames}
            for sample in samples:
                key = (
                    sample["run_id"],
                    sample["frame_id"],
                    sample["path_id"],
                    sample["copy_id"],
                    sample["sample_stage"],
                    sample["sample_offset_us"],
                )
                _require(key not in raw_rows_by_key, f"duplicate raw sample key: {key}")
                frame = frames_by_id.get(sample["frame_id"])
                _require(frame is not None, f"raw sample has no frame: {key}")
                raw_rows_by_key[key] = (sample, frame, raw_header)
            run_context[run_id] = {
                "config": config,
                "manifest": item,
                "sample_count": len(samples),
                "frame_count": len(frames),
            }
    except ValidationError as error:
        raise DatasetValidationError(f"source run validation failed: {error}") from error

    seen_keys: set[tuple[str, str, str, str, str, str]] = set()
    labels_by_frame: dict[tuple[str, str], tuple[str, str, str, str]] = {}
    rows_by_frame: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    monotone_columns = {
        "packets_submitted",
        "application_socket_packet_bytes_submitted",
        "mpdu_tx_attempts_total",
        "mpdu_positive_acks_total",
        "mpdu_tx_attempt_failures_total",
        "mpdu_retries_total",
        "mpdu_terminal_drops_total",
        "mpdu_retry_limit_drops_total",
        "mpdu_lifetime_drops_total",
        "mpdu_queue_drops_total",
        "ppdu_tx_count_total",
        "frame_packets_mac_enqueued",
        "frame_packets_mac_dequeued",
        "frame_packets_tx_succeeded",
        "frame_mpdu_attempt_failures",
    }
    nonnegative_columns = {
        column
        for column in columns
        if (
            column.endswith("_us")
            or column.endswith("_fraction")
            or column.startswith("history_coverage_")
        )
    }
    for row in rows:
        _require(
            _integer(row["dataset_schema_version"], "dataset_schema_version")
            == DATASET_SCHEMA_VERSION,
            "row dataset schema version mismatch",
        )
        key = (
            row["run_id"],
            row["frame_id"],
            row["path_id"],
            row["copy_id"],
            row["sample_stage"],
            row["sample_offset_us"],
        )
        _require(key not in seen_keys, f"duplicate dataset sample key: {key}")
        seen_keys.add(key)
        _require(key in raw_rows_by_key, f"dataset sample has no raw source: {key}")
        raw, frame, raw_header = raw_rows_by_key[key]
        for column in raw_header:
            _require(
                row[column] == raw[column],
                f"raw sample field changed during join: {key}/{column}",
            )
        run_id = row["run_id"]
        _require(run_id in manifest_runs, f"dataset contains an unknown run: {run_id}")
        run_item = manifest_runs[run_id]
        group_id = row["run_group_id"]
        _require(group_id == run_item["run_group_id"], "row run-group ID mismatch")
        _require(group_roles[group_id] == row["split_role"], "row split role mismatch")
        _require(split_run_ids[run_id] == group_id, "run crosses split groups")
        _require(row["scenario_name"] == run_item["scenario_name"], "scenario mismatch")
        _require(row["selected_policy"] == run_item["selected_policy"], "policy mismatch")
        source_config = run_context[run_id]["config"]
        background = source_config["background"]
        expected_background_profile = background["profile"]
        expected_correlation_mode = (
            "none"
            if expected_background_profile == "none"
            else background["correlation"]["mode"]
        )
        _require(
            row["background_profile"] == expected_background_profile
            and row["correlation_mode"] == expected_correlation_mode,
            "background context differs from resolved configuration",
        )
        _require(
            _integer(row["path_id"], "path_id") == run_item["selected_path"],
            "selected path mismatch",
        )
        _require(
            row["miss_regime"]
            == load_regime(source_config, loads, run_item["selected_path"]),
            "miss-regime metadata differs from frozen load mapping",
        )
        _require(
            _integer(row["run_seed"], "run_seed") == run_item["seed"]
            and _integer(row["run_number"], "run_number") == run_item["run"],
            "run seed or substream mismatch",
        )
        incomplete = _integer(frame["incomplete"], "incomplete")
        expected_complete = 1 - incomplete
        expected_miss = _integer(frame["deadline_miss"], "deadline_miss")
        _require(
            _integer(row["frame_complete"], "frame_complete") == expected_complete,
            "frame_complete label differs from frames.csv",
        )
        _require(
            _integer(row["deadline_miss"], "deadline_miss") == expected_miss,
            "deadline_miss label differs from frames.csv",
        )
        if incomplete:
            _require(
                row["frame_completion_time_ns"] == "" and row["frame_latency_us"] == "",
                "incomplete frame has completion labels",
            )
        else:
            _require(
                _integer(row["frame_completion_time_ns"], "frame_completion_time_ns")
                == _integer(frame["union_completion_us"], "union_completion_us") * 1000,
                "completion timestamp label differs from frames.csv",
            )
            _require(
                _integer(row["frame_latency_us"], "frame_latency_us")
                == _integer(frame["union_latency_us"], "union_latency_us"),
                "latency label differs from frames.csv",
            )
        label_tuple = (
            row["frame_complete"],
            row["frame_completion_time_ns"],
            row["frame_latency_us"],
            row["deadline_miss"],
        )
        frame_key = (run_id, row["frame_id"])
        prior_label = labels_by_frame.setdefault(frame_key, label_tuple)
        _require(prior_label == label_tuple, "labels vary across frame snapshots")

        sample_time = _integer(row["sample_time_ns"], "sample_time_ns")
        for source_field, age_field in (
            ("last_positive_ack_time_ns", "last_positive_ack_age_us"),
            ("last_tx_attempt_time_ns", "last_attempt_age_us"),
            ("mac_queue_oldest_enqueue_time_ns", "queue_oldest_age_us"),
        ):
            source_time = _optional_integer(row[source_field], source_field)
            if source_time is None:
                _require(row[age_field] == "", f"{age_field} exists without a source time")
            else:
                _require(source_time <= sample_time, f"{source_field} exceeds sample time")
                _require(
                    _close(
                        _number(row[age_field], age_field),
                        (sample_time - source_time) / 1000.0,
                    ),
                    f"{age_field} derivation mismatch",
                )
        expected_not_acknowledged = (
            _integer(row["frame_packet_count"], "frame_packet_count")
            - _integer(row["frame_packets_tx_succeeded"], "frame_packets_tx_succeeded")
        )
        _require(
            expected_not_acknowledged >= 0
            and _integer(
                row["frame_packets_not_acknowledged"],
                "frame_packets_not_acknowledged",
            )
            == expected_not_acknowledged,
            "frame_packets_not_acknowledged derivation mismatch",
        )
        for column in nonnegative_columns:
            if row[column] != "":
                _require(_number(row[column], column) >= 0, f"{column} is negative")
        rows_by_frame[
            (run_id, row["frame_id"], row["path_id"], row["copy_id"])
        ].append(row)

    _require(seen_keys == set(raw_rows_by_key), "dataset and raw sample keys differ")
    for frame_rows in rows_by_frame.values():
        frame_rows.sort(
            key=lambda row: (
                _integer(row["sample_time_ns"], "sample_time_ns"),
                _integer(row["sample_offset_us"], "sample_offset_us"),
            )
        )
        for column in monotone_columns:
            values = [_integer(row[column], column) for row in frame_rows]
            _require(
                values == sorted(values),
                f"cumulative field decreases across snapshots: {column}",
            )
        remaining = [
            _integer(row["packets_remaining_to_submit"], "packets_remaining_to_submit")
            for row in frame_rows
        ]
        _require(
            remaining == sorted(remaining, reverse=True),
            "packets_remaining_to_submit increases across snapshots",
        )

    _require(len(rows) == manifest["counts"]["sample_count"], "sample count mismatch")
    _require(len(manifest_runs) == manifest["counts"]["run_count"], "run count mismatch")
    _require(
        len(labels_by_frame) == manifest["counts"]["frame_count"],
        "frame count mismatch",
    )
    miss_count = sum(int(labels[3]) for labels in labels_by_frame.values())
    _require(miss_count == manifest["counts"]["miss_count"], "miss count mismatch")
    _require(len(group_roles) == manifest["counts"]["run_group_count"], "group count mismatch")

    groups_to_policies: dict[str, set[str]] = defaultdict(set)
    for item in manifest_runs.values():
        groups_to_policies[item["run_group_id"]].add(item["selected_policy"])
    for group_id, policies in groups_to_policies.items():
        _require(
            policies <= {"fixed_link_0", "fixed_link_1"} and len(policies) in {1, 2},
            f"run group has ambiguous policy membership: {group_id}",
        )

    for group in split_groups:
        role = group["split_role"]
        scenario = group["scenario_name"]
        if scenario.startswith("obss_"):
            _require(
                role == "out_of_distribution_test",
                f"OBSS group is not held out: {group['run_group_id']}",
            )
        else:
            _require(
                role != "out_of_distribution_test",
                f"Stage-A group was assigned to OOD: {group['run_group_id']}",
            )

    mask_distribution = Counter(row["feature_support_mask"] for row in rows)
    support_rates = {
        column: {
            "non_null_count": sum(row[column] != "" for row in rows),
            "non_null_rate": sum(row[column] != "" for row in rows) / len(rows),
            "support_bit": bit,
        }
        for column, bit in sorted(PREDICTION_FIELD_SUPPORT_BITS.items())
    }
    missingness, quantiles, constants, suspicious = _distribution_report(
        rows,
        columns,
        expected_dictionary,
    )
    split_counts: dict[str, dict[str, int]] = {}
    for role in sorted(SPLIT_ROLES):
        role_rows = [row for row in rows if row["split_role"] == role]
        role_frames = {
            (row["run_id"], row["frame_id"]): int(row["deadline_miss"])
            for row in role_rows
        }
        split_counts[role] = {
            "run_group_count": len({
                row["run_group_id"] for row in role_rows
            }),
            "frame_count": len(role_frames),
            "miss_count": sum(role_frames.values()),
        }
    _require(
        split_counts == manifest["counts_by_split_role"],
        "manifest split counts do not match dataset",
    )
    _require(
        split_counts == splits["counts_by_role"],
        "split manifest counts do not match dataset",
    )
    ood_frames: dict[str, dict[tuple[str, str], int]] = defaultdict(dict)
    ood_groups: dict[str, set[str]] = defaultdict(set)
    ood_runs: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        scenario = row["scenario_name"]
        if not scenario.startswith("obss_"):
            continue
        ood_frames[scenario][(row["run_id"], row["frame_id"])] = int(
            row["deadline_miss"]
        )
        ood_groups[scenario].add(row["run_group_id"])
        ood_runs[scenario].add(row["run_id"])
    ood_counts = {
        scenario: {
            "run_group_count": len(ood_groups[scenario]),
            "run_count": len(ood_runs[scenario]),
            "frame_count": len(scenario_frames),
            "miss_count": sum(scenario_frames.values()),
        }
        for scenario, scenario_frames in sorted(ood_frames.items())
    }
    _require(
        ood_counts == manifest["counts_by_ood_scenario"],
        "manifest OOD scenario counts do not match dataset",
    )
    _require(
        ood_counts == splits["counts_by_ood_scenario"],
        "split OOD scenario counts do not match dataset",
    )

    return {
        "validation_schema_version": 1,
        "status": "PASS",
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "dataset_sha256": manifest["dataset_sha256"],
        "split_sufficiency_status": splits["sufficiency_status"],
        "split_sufficiency_reasons": splits["sufficiency_reasons"],
        "counts": manifest["counts"],
        "counts_by_split_role": split_counts,
        "counts_by_ood_scenario": ood_counts,
        "class_balance": {
            "frame_count": len(labels_by_frame),
            "miss_count": miss_count,
            "miss_rate": miss_count / len(labels_by_frame),
        },
        "stratified_class_balance": _stratified_class_balance(rows),
        "feature_support_mask_distribution": dict(sorted(mask_distribution.items())),
        "field_support_rates": support_rates,
        "missingness": missingness,
        "numeric_quantiles": quantiles,
        "constant_model_features": constants,
        "suspicious_label_correlations": suspicious,
        "checks": {
            "unique_sample_keys": "PASS",
            "exact_frame_join": "PASS",
            "fixed_path_match": "PASS",
            "source_checksums": "PASS",
            "raw_sample_preservation": "PASS",
            "label_provenance": "PASS",
            "constant_labels_across_snapshots": "PASS",
            "causal_derived_ages": "PASS",
            "counter_monotonicity": "PASS",
            "support_mask_source_validation": "PASS",
            "model_allowlist_exclusions": "PASS",
            "run_group_reconstruction": "PASS",
            "split_group_isolation": "PASS",
            "ood_isolation": "PASS",
        },
    }


def validate_dataset(
    dataset_dir: Path | str,
    analysis_path: Path | str | None = None,
) -> dict[str, Any]:
    """Validate a dataset while retaining only one source run in memory."""
    dataset_dir = Path(dataset_dir).resolve()
    manifest = _json(dataset_dir / "dataset_manifest.json")
    splits = _json(dataset_dir / "splits.json")
    _require(
        manifest.get("dataset_schema_version") == DATASET_SCHEMA_VERSION,
        "unsupported dataset schema version",
    )
    resolved_analysis_path = (
        Path(analysis_path).resolve()
        if analysis_path is not None
        else Path(manifest["analysis_config"]).resolve()
    )
    analysis = read_yaml(resolved_analysis_path)
    validate_analysis_config(analysis)
    load_path = Path(manifest["load_config"]).resolve()
    loads = read_yaml(load_path)
    _require(
        sha256_file(resolved_analysis_path) == manifest["analysis_config_sha256"],
        "analysis configuration checksum mismatch",
    )
    _require(
        sha256_file(load_path) == manifest["load_config_sha256"],
        "load configuration checksum mismatch",
    )
    _require(
        splits["analysis_config_sha256"] == manifest["analysis_config_sha256"],
        "split and dataset analysis identities differ",
    )
    dataset_path = dataset_dir / manifest["dataset_file"]
    _require(dataset_path.is_file(), "labelled dataset file is missing")
    _require(
        sha256_file(dataset_path) == manifest["dataset_sha256"],
        "labelled dataset checksum mismatch",
    )
    columns = _dataset_columns(dataset_dir, manifest)
    _require(len(columns) == len(set(columns)), "dataset contains duplicate columns")
    required_columns = set(ATTACHED_COLUMNS + DERIVED_COLUMNS) | PREDICTION_BASE_COLUMNS
    _require(required_columns <= set(columns), "dataset is missing required columns")
    expected_dictionary = feature_dictionary(columns)
    _require(
        manifest["feature_dictionary"] == expected_dictionary,
        "feature dictionary does not match dataset columns",
    )
    prohibited = CONTEXT_COLUMNS | OUTCOME_COLUMNS | ELIGIBILITY_COLUMNS
    leaked = sorted(
        column
        for column, entry in expected_dictionary.items()
        if entry["model_eligible"] and column in prohibited
    )
    _require(not leaked, f"model-eligible allowlist leaks prohibited fields: {leaked}")

    split_groups = splits.get("groups")
    _require(isinstance(split_groups, list), "splits.json groups must be a list")
    group_roles: dict[str, str] = {}
    split_run_ids: dict[str, str] = {}
    for group in split_groups:
        group_id = group["run_group_id"]
        role = group["split_role"]
        _require(group_id not in group_roles, f"duplicate split group: {group_id}")
        _require(role in SPLIT_ROLES, f"invalid split role: {role}")
        group_roles[group_id] = role
        for run_id in group["run_ids"]:
            _require(
                run_id not in split_run_ids,
                f"run appears in two split groups: {run_id}",
            )
            split_run_ids[run_id] = group_id

    included_runs = manifest.get("included_runs")
    _require(isinstance(included_runs, list) and included_runs, "manifest has no runs")
    manifest_runs = {item["run_id"]: item for item in included_runs}
    _require(
        len(manifest_runs) == len(included_runs),
        "manifest contains duplicate included run IDs",
    )
    _require(
        set(manifest_runs) == set(split_run_ids),
        "split run IDs do not equal included run IDs",
    )
    source_by_path = _source_map(manifest)
    run_context: dict[str, dict[str, Any]] = {}
    try:
        for run_id, item in manifest_runs.items():
            run_dir = Path(item["source_directory"]).resolve()
            _require(
                run_dir in source_by_path,
                f"source run is absent from source roots: {run_id}",
            )
            source = source_by_path[run_dir]
            validate_run(run_dir)
            config = _json(run_dir / "resolved_config.json")
            build = _json(run_dir / "build_info.json")
            _require(config["run_id"] == run_id, f"source run ID mismatch: {run_id}")
            _require(
                make_run_group_id(source, config, build) == item["run_group_id"],
                f"run-group identity is not reproducible: {run_id}",
            )
            _require(
                scenario_name(config) == item["scenario_name"],
                f"scenario identity is not reproducible: {run_id}",
            )
            validate_ood_run_filter(config, item["scenario_name"], analysis)
            _require(
                selected_link(config["policy"]) == item["selected_path"],
                f"selected fixed path is not reproducible: {run_id}",
            )
            for name, expected_hash in manifest["source_checksums"][run_id].items():
                _require(
                    sha256_file(run_dir / name) == expected_hash,
                    f"source checksum changed: {run_id}/{name}",
                )
            raw_header = csv_header(run_dir / "prediction_samples.csv")
            _require(
                not (set(raw_header) & OUTCOME_COLUMNS),
                f"raw prediction samples contain outcomes: {run_id}",
            )
            run_context[run_id] = {"config": config, "manifest": item}
    except ValidationError as error:
        raise DatasetValidationError(f"source run validation failed: {error}") from error

    monotone_columns = {
        "packets_submitted",
        "application_socket_packet_bytes_submitted",
        "mpdu_tx_attempts_total",
        "mpdu_positive_acks_total",
        "mpdu_tx_attempt_failures_total",
        "mpdu_retries_total",
        "mpdu_terminal_drops_total",
        "mpdu_retry_limit_drops_total",
        "mpdu_lifetime_drops_total",
        "mpdu_queue_drops_total",
        "ppdu_tx_count_total",
        "frame_packets_mac_enqueued",
        "frame_packets_mac_dequeued",
        "frame_packets_tx_succeeded",
        "frame_mpdu_attempt_failures",
    }
    nonnegative_columns = {
        column
        for column in columns
        if (
            column.endswith("_us")
            or column.endswith("_fraction")
            or column.startswith("history_coverage_")
        )
    }
    mask_distribution: Counter[str] = Counter()
    support_non_null = Counter()
    split_frames: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    split_role_groups: dict[str, set[str]] = defaultdict(set)
    ood_frames: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    ood_groups: dict[str, set[str]] = defaultdict(set)
    ood_runs: dict[str, set[str]] = defaultdict(set)
    strata_columns = {
        "scenario": ("scenario_name",),
        "selected_policy": ("selected_policy",),
        "miss_regime": ("miss_regime",),
        "scenario_policy_regime": (
            "scenario_name",
            "selected_policy",
            "miss_regime",
        ),
    }
    strata: dict[str, dict[str, dict[str, Any]]] = {
        name: {} for name in strata_columns
    }
    sample_count = 0
    frame_count = 0
    miss_count = 0
    processed_runs: set[str] = set()

    with tempfile.TemporaryDirectory(prefix="prediction-validation-") as temporary:
        distribution = _DistributionAccumulator(
            columns, expected_dictionary, Path(temporary)
        )
        rows = _iter_dataset(dataset_dir, manifest)
        for run_id, grouped_rows in itertools.groupby(rows, key=lambda row: row["run_id"]):
            _require(run_id in manifest_runs, f"dataset contains an unknown run: {run_id}")
            _require(
                run_id not in processed_runs,
                f"dataset ordering splits run into multiple blocks: {run_id}",
            )
            processed_runs.add(run_id)
            run_rows = list(grouped_rows)
            run_item = manifest_runs[run_id]
            run_dir = Path(run_item["source_directory"]).resolve()
            raw_header = csv_header(run_dir / "prediction_samples.csv")
            raw_rows_by_key: dict[tuple[str, str, str, str, str], dict[str, str]] = {}
            for raw in iter_csv(run_dir / "prediction_samples.csv"):
                key = (
                    raw["frame_id"],
                    raw["path_id"],
                    raw["copy_id"],
                    raw["sample_stage"],
                    raw["sample_offset_us"],
                )
                _require(key not in raw_rows_by_key, f"duplicate raw sample key: {(run_id, *key)}")
                raw_rows_by_key[key] = raw
            frames = list(iter_csv(run_dir / "frames.csv"))
            frames_by_id = {frame["frame_id"]: frame for frame in frames}
            _require(len(frames_by_id) == len(frames), f"duplicate source frame: {run_id}")
            _require(
                len(raw_rows_by_key) == run_item["sample_count"],
                f"source sample count mismatch: {run_id}",
            )
            _require(
                len(frames) == run_item["frame_count"],
                f"source frame count mismatch: {run_id}",
            )
            seen_keys: set[tuple[str, str, str, str, str]] = set()
            labels_by_frame: dict[str, tuple[str, str, str, str]] = {}
            rows_by_frame: dict[
                tuple[str, str, str], list[dict[str, str]]
            ] = defaultdict(list)
            source_config = run_context[run_id]["config"]
            background = source_config["background"]
            expected_profile = background["profile"]
            expected_correlation = (
                "none"
                if expected_profile == "none"
                else background["correlation"]["mode"]
            )
            for row in run_rows:
                distribution.add(row)
                sample_count += 1
                mask_distribution[row["feature_support_mask"]] += 1
                for column in PREDICTION_FIELD_SUPPORT_BITS:
                    support_non_null[column] += row[column] != ""
                _require(
                    _integer(row["dataset_schema_version"], "dataset_schema_version")
                    == DATASET_SCHEMA_VERSION,
                    "row dataset schema version mismatch",
                )
                key = (
                    row["frame_id"],
                    row["path_id"],
                    row["copy_id"],
                    row["sample_stage"],
                    row["sample_offset_us"],
                )
                _require(key not in seen_keys, f"duplicate dataset sample key: {(run_id, *key)}")
                seen_keys.add(key)
                _require(key in raw_rows_by_key, f"dataset sample has no raw source: {(run_id, *key)}")
                raw = raw_rows_by_key[key]
                for column in raw_header:
                    _require(
                        row[column] == raw[column],
                        f"raw sample field changed during join: {(run_id, *key)}/{column}",
                    )
                frame = frames_by_id.get(row["frame_id"])
                _require(frame is not None, f"raw sample has no frame: {(run_id, *key)}")
                group_id = row["run_group_id"]
                _require(group_id == run_item["run_group_id"], "row run-group ID mismatch")
                _require(group_roles[group_id] == row["split_role"], "row split role mismatch")
                _require(split_run_ids[run_id] == group_id, "run crosses split groups")
                _require(row["scenario_name"] == run_item["scenario_name"], "scenario mismatch")
                _require(row["selected_policy"] == run_item["selected_policy"], "policy mismatch")
                _require(
                    row["background_profile"] == expected_profile
                    and row["correlation_mode"] == expected_correlation,
                    "background context differs from resolved configuration",
                )
                _require(
                    _integer(row["path_id"], "path_id") == run_item["selected_path"],
                    "selected path mismatch",
                )
                _require(
                    row["miss_regime"]
                    == load_regime(source_config, loads, run_item["selected_path"]),
                    "miss-regime metadata differs from frozen load mapping",
                )
                _require(
                    _integer(row["run_seed"], "run_seed") == run_item["seed"]
                    and _integer(row["run_number"], "run_number") == run_item["run"],
                    "run seed or substream mismatch",
                )
                incomplete = _integer(frame["incomplete"], "incomplete")
                expected_miss = _integer(frame["deadline_miss"], "deadline_miss")
                _require(
                    _integer(row["frame_complete"], "frame_complete") == 1 - incomplete,
                    "frame_complete label differs from frames.csv",
                )
                _require(
                    _integer(row["deadline_miss"], "deadline_miss") == expected_miss,
                    "deadline_miss label differs from frames.csv",
                )
                if incomplete:
                    _require(
                        row["frame_completion_time_ns"] == ""
                        and row["frame_latency_us"] == "",
                        "incomplete frame has completion labels",
                    )
                else:
                    _require(
                        _integer(row["frame_completion_time_ns"], "frame_completion_time_ns")
                        == _integer(frame["union_completion_us"], "union_completion_us") * 1000,
                        "completion timestamp label differs from frames.csv",
                    )
                    _require(
                        _integer(row["frame_latency_us"], "frame_latency_us")
                        == _integer(frame["union_latency_us"], "union_latency_us"),
                        "latency label differs from frames.csv",
                    )
                label_tuple = (
                    row["frame_complete"],
                    row["frame_completion_time_ns"],
                    row["frame_latency_us"],
                    row["deadline_miss"],
                )
                previous = labels_by_frame.setdefault(row["frame_id"], label_tuple)
                _require(previous == label_tuple, "labels vary across frame snapshots")
                sample_time = _integer(row["sample_time_ns"], "sample_time_ns")
                for source_field, age_field in (
                    ("last_positive_ack_time_ns", "last_positive_ack_age_us"),
                    ("last_tx_attempt_time_ns", "last_attempt_age_us"),
                    ("mac_queue_oldest_enqueue_time_ns", "queue_oldest_age_us"),
                ):
                    source_time = _optional_integer(row[source_field], source_field)
                    if source_time is None:
                        _require(row[age_field] == "", f"{age_field} exists without a source time")
                    else:
                        _require(source_time <= sample_time, f"{source_field} exceeds sample time")
                        _require(
                            _close(
                                _number(row[age_field], age_field),
                                (sample_time - source_time) / 1000.0,
                            ),
                            f"{age_field} derivation mismatch",
                        )
                expected_not_acknowledged = (
                    _integer(row["frame_packet_count"], "frame_packet_count")
                    - _integer(row["frame_packets_tx_succeeded"], "frame_packets_tx_succeeded")
                )
                _require(
                    expected_not_acknowledged >= 0
                    and _integer(
                        row["frame_packets_not_acknowledged"],
                        "frame_packets_not_acknowledged",
                    )
                    == expected_not_acknowledged,
                    "frame_packets_not_acknowledged derivation mismatch",
                )
                for column in nonnegative_columns:
                    if row[column] != "":
                        _require(_number(row[column], column) >= 0, f"{column} is negative")
                rows_by_frame[
                    (row["frame_id"], row["path_id"], row["copy_id"])
                ].append(row)
            _require(seen_keys == set(raw_rows_by_key), "dataset and raw sample keys differ")
            for frame_rows in rows_by_frame.values():
                frame_rows.sort(
                    key=lambda row: (
                        _integer(row["sample_time_ns"], "sample_time_ns"),
                        _integer(row["sample_offset_us"], "sample_offset_us"),
                    )
                )
                for column in monotone_columns:
                    values = [_integer(row[column], column) for row in frame_rows]
                    _require(
                        values == sorted(values),
                        f"cumulative field decreases across snapshots: {column}",
                    )
                remaining = [
                    _integer(row["packets_remaining_to_submit"], "packets_remaining_to_submit")
                    for row in frame_rows
                ]
                _require(
                    remaining == sorted(remaining, reverse=True),
                    "packets_remaining_to_submit increases across snapshots",
                )
                representative = frame_rows[0]
                label = _integer(representative["deadline_miss"], "deadline_miss")
                frame_count += 1
                miss_count += label
                role = representative["split_role"]
                split_frames[role][0] += 1
                split_frames[role][1] += label
                split_role_groups[role].add(representative["run_group_id"])
                scenario = representative["scenario_name"]
                if scenario.startswith("obss_"):
                    ood_frames[scenario][0] += 1
                    ood_frames[scenario][1] += label
                    ood_groups[scenario].add(representative["run_group_id"])
                    ood_runs[scenario].add(run_id)
                for stratum, stratum_columns in strata_columns.items():
                    stratum_key = "|".join(
                        representative[column] for column in stratum_columns
                    )
                    entry = strata[stratum].setdefault(
                        stratum_key,
                        {
                            "frame_count": 0,
                            "miss_count": 0,
                            "run_ids": set(),
                            "run_group_ids": set(),
                            "sample_count": 0,
                        },
                    )
                    entry["frame_count"] += 1
                    entry["miss_count"] += label
                    entry["run_ids"].add(run_id)
                    entry["run_group_ids"].add(representative["run_group_id"])
            for stratum, stratum_columns in strata_columns.items():
                for row in run_rows:
                    stratum_key = "|".join(row[column] for column in stratum_columns)
                    strata[stratum][stratum_key]["sample_count"] += 1
        _require(processed_runs, "labelled dataset is empty")
        _require(processed_runs == set(manifest_runs), "dataset and manifest runs differ")
        missingness, quantiles, constants, suspicious = distribution.finish()

    _require(sample_count == manifest["counts"]["sample_count"], "sample count mismatch")
    _require(len(manifest_runs) == manifest["counts"]["run_count"], "run count mismatch")
    _require(frame_count == manifest["counts"]["frame_count"], "frame count mismatch")
    _require(miss_count == manifest["counts"]["miss_count"], "miss count mismatch")
    _require(len(group_roles) == manifest["counts"]["run_group_count"], "group count mismatch")
    groups_to_policies: dict[str, set[str]] = defaultdict(set)
    for item in manifest_runs.values():
        groups_to_policies[item["run_group_id"]].add(item["selected_policy"])
    for group_id, policies in groups_to_policies.items():
        _require(
            policies <= {"fixed_link_0", "fixed_link_1"} and len(policies) in {1, 2},
            f"run group has ambiguous policy membership: {group_id}",
        )
    for group in split_groups:
        role = group["split_role"]
        scenario = group["scenario_name"]
        _require(
            (scenario.startswith("obss_") and role == "out_of_distribution_test")
            or (not scenario.startswith("obss_") and role != "out_of_distribution_test"),
            f"scenario split isolation failed: {group['run_group_id']}",
        )
    split_counts = {
        role: {
            "run_group_count": len(split_role_groups[role]),
            "frame_count": split_frames[role][0],
            "miss_count": split_frames[role][1],
        }
        for role in sorted(SPLIT_ROLES)
    }
    _require(
        split_counts == manifest["counts_by_split_role"],
        "manifest split counts do not match dataset",
    )
    _require(
        split_counts == splits["counts_by_role"],
        "split manifest counts do not match dataset",
    )
    ood_counts = {
        scenario: {
            "run_group_count": len(ood_groups[scenario]),
            "run_count": len(ood_runs[scenario]),
            "frame_count": counts[0],
            "miss_count": counts[1],
        }
        for scenario, counts in sorted(ood_frames.items())
    }
    _require(
        ood_counts == manifest["counts_by_ood_scenario"],
        "manifest OOD scenario counts do not match dataset",
    )
    _require(
        ood_counts == splits["counts_by_ood_scenario"],
        "split OOD scenario counts do not match dataset",
    )
    stratified_report: dict[str, dict[str, dict[str, Any]]] = {}
    for stratum, entries in strata.items():
        stratified_report[stratum] = {}
        for key, entry in sorted(entries.items()):
            frames = entry["frame_count"]
            misses = entry["miss_count"]
            stratified_report[stratum][key] = {
                "frame_count": frames,
                "miss_count": misses,
                "miss_rate": misses / frames if frames else 0.0,
                "run_count": len(entry["run_ids"]),
                "run_group_count": len(entry["run_group_ids"]),
                "sample_count": entry["sample_count"],
            }
    support_rates = {
        column: {
            "non_null_count": support_non_null[column],
            "non_null_rate": support_non_null[column] / sample_count,
            "support_bit": bit,
        }
        for column, bit in sorted(PREDICTION_FIELD_SUPPORT_BITS.items())
    }
    return {
        "validation_schema_version": 1,
        "status": "PASS",
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "dataset_sha256": manifest["dataset_sha256"],
        "split_sufficiency_status": splits["sufficiency_status"],
        "split_sufficiency_reasons": splits["sufficiency_reasons"],
        "counts": manifest["counts"],
        "counts_by_split_role": split_counts,
        "counts_by_ood_scenario": ood_counts,
        "class_balance": {
            "frame_count": frame_count,
            "miss_count": miss_count,
            "miss_rate": miss_count / frame_count,
        },
        "stratified_class_balance": stratified_report,
        "feature_support_mask_distribution": dict(sorted(mask_distribution.items())),
        "field_support_rates": support_rates,
        "missingness": missingness,
        "numeric_quantiles": quantiles,
        "constant_model_features": constants,
        "suspicious_label_correlations": suspicious,
        "checks": {
            "unique_sample_keys": "PASS",
            "exact_frame_join": "PASS",
            "fixed_path_match": "PASS",
            "source_checksums": "PASS",
            "raw_sample_preservation": "PASS",
            "label_provenance": "PASS",
            "constant_labels_across_snapshots": "PASS",
            "causal_derived_ages": "PASS",
            "counter_monotonicity": "PASS",
            "support_mask_source_validation": "PASS",
            "model_allowlist_exclusions": "PASS",
            "run_group_reconstruction": "PASS",
            "split_group_isolation": "PASS",
            "ood_isolation": "PASS",
        },
    }


def _atomic_json(path: Path, value: Any) -> None:
    with tempfile.NamedTemporaryFile(
        "w",
        dir=path.parent,
        delete=False,
        encoding="utf-8",
    ) as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")
        temporary = Path(output.name)
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset_dir", type=Path)
    parser.add_argument("--analysis-config", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate_dataset(args.dataset_dir, args.analysis_config)
    output = args.output or args.dataset_dir / "dataset_validation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    _atomic_json(output, report)
    print(
        f"VALID dataset runs={report['counts']['run_count']} "
        f"frames={report['counts']['frame_count']} "
        f"samples={report['counts']['sample_count']} "
        f"split={report['split_sufficiency_status']}"
    )


if __name__ == "__main__":
    main()
