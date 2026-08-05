#!/usr/bin/env python3
"""Build the scenario-aware temporal T2 generalization dataset.

The historical randomized builders intentionally require one invariant
environment.  This builder preserves their row and temporal-feature logic but
admits only runs from a checksum-bound environment-generalization matrix.  It
streams one validated run at a time, joins scenario identity from the
experiment manifest, and appends only application context known to a deployed
sender.  Scenario and family identities remain non-feature metadata.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

import build_randomized_intervention_dataset as base
import build_randomized_temporal_dataset as temporal
import generate_environment_generalization_v1 as generator
import run_experiments as runner


ROOT = Path(__file__).resolve().parents[1]
DATASET_SCHEMA_VERSION = 1
FEATURE_CONTRACT_ID = "environment_generalization_t2_temporal_action_clean_v1"
HASH_ALGORITHM = "sha256"
OUTPUT_CSV = "environment_randomized_t2_temporal.csv"
OUTPUT_METADATA = "dataset_metadata.json"
OUTPUT_MANIFEST = "artifact_manifest.json"
SCENARIO_COLUMNS = ("scenario_id", "family_id", "parameter_sample")
ENVIRONMENT_FEATURE_COLUMNS = (
    "env_stream_fps",
    "env_interframe_size_bytes",
    "env_gop_length",
    "env_keyframe_size_multiplier",
    "env_deadline_us",
)
NON_FEATURE_COLUMNS = temporal.NON_FEATURE_COLUMNS + SCENARIO_COLUMNS
FEATURE_COLUMNS = temporal.FEATURE_COLUMNS + ENVIRONMENT_FEATURE_COLUMNS
DATASET_COLUMNS = NON_FEATURE_COLUMNS + FEATURE_COLUMNS
POOL_ROLE = "generalization_pool"
BUILDER_SOURCES = (
    Path(__file__).resolve(),
    ROOT / "tools/build_randomized_intervention_dataset.py",
    ROOT / "tools/build_randomized_temporal_dataset.py",
    ROOT / "tools/generate_environment_generalization_v1.py",
    ROOT / "tools/run_experiments.py",
)


class EnvironmentDatasetError(RuntimeError):
    """Raised when scenario-aware dataset provenance or rows differ."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise EnvironmentDatasetError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EnvironmentDatasetError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise EnvironmentDatasetError(f"{path}: expected a JSON object")
    return value


def _write_json(path: Path, value: Any) -> None:
    try:
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise EnvironmentDatasetError(f"cannot write {path}: {error}") from error


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _nonnegative_integer(value: Any, name: str, *, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise EnvironmentDatasetError(f"{name} must be an integer")
    if value < int(positive):
        qualifier = "positive" if positive else "nonnegative"
        raise EnvironmentDatasetError(f"{name} must be {qualifier}")
    return value


def _finite_positive(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise EnvironmentDatasetError(f"{name} must be numeric")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise EnvironmentDatasetError(f"{name} must be finite and positive")
    return number


def static_environment_features(config: dict[str, Any]) -> dict[str, str]:
    """Return the sender-known application context for one resolved run."""

    stream = config.get("stream")
    if not isinstance(stream, dict):
        raise EnvironmentDatasetError("resolved run lacks stream context")
    fps = _nonnegative_integer(stream.get("fps"), "stream.fps", positive=True)
    frame_size = _nonnegative_integer(
        stream.get("frame_size_bytes"), "stream.frame_size_bytes", positive=True
    )
    gop_length = _nonnegative_integer(
        stream.get("gop_length"), "stream.gop_length", positive=True
    )
    multiplier = _finite_positive(
        stream.get("keyframe_size_multiplier"), "stream.keyframe_size_multiplier"
    )
    deadline_us = _nonnegative_integer(
        stream.get("deadline_us"), "stream.deadline_us", positive=True
    )
    return {
        "env_stream_fps": str(fps),
        "env_interframe_size_bytes": str(frame_size),
        "env_gop_length": str(gop_length),
        "env_keyframe_size_multiplier": format(multiplier, ".17g"),
        "env_deadline_us": str(deadline_us),
    }


def _validated_contract(
    document: dict[str, Any], config_path: Path
) -> tuple[dict[str, Any], Path, str, str]:
    declaration = document.get("generalization_contract")
    required = {"id", "path", "sha256", "phase"}
    if not isinstance(declaration, dict) or set(declaration) != required:
        raise EnvironmentDatasetError(
            "generalization_contract must contain exactly id, path, sha256, and phase"
        )
    contract_id = declaration.get("id")
    phase = declaration.get("phase")
    declared_hash = declaration.get("sha256")
    relative = declaration.get("path")
    if (
        not isinstance(contract_id, str)
        or not contract_id
        or phase not in {"randomized_collection", "preflight"}
        or not isinstance(declared_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", declared_hash) is None
        or not isinstance(relative, str)
        or not relative
    ):
        raise EnvironmentDatasetError("generalization contract declaration is invalid")
    contract_path = (ROOT / relative).resolve()
    try:
        contract_path.relative_to(ROOT)
    except ValueError as error:
        raise EnvironmentDatasetError("generalization contract escapes the repository") from error
    actual_hash = _sha256(contract_path)
    if actual_hash != declared_hash:
        raise EnvironmentDatasetError(
            f"generalization contract hash drift: {actual_hash} != {declared_hash}"
        )
    contract = _read_json(contract_path)
    try:
        generator.validate_contract(contract, contract_path, ROOT)
    except generator.GeneralizationContractError as error:
        raise EnvironmentDatasetError(f"invalid generalization contract: {error}") from error
    if contract.get("contract_id") != contract_id:
        raise EnvironmentDatasetError("generalization contract id differs")

    base_path = ROOT / contract["randomized_collection"]["base_config_path"]
    base_document = runner.load_yaml(base_path)
    expected_scenarios = generator.generate_phase_scenarios(contract, phase, base_document)
    if _canonical(document.get("scenario_instances")) != _canonical(expected_scenarios):
        raise EnvironmentDatasetError(
            f"{config_path}: scenarios differ from the hash-verified {phase} catalog"
        )
    return contract, contract_path, actual_hash, phase


def _validated_manifest(
    config_path: Path, manifest_path: Path
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, Any],
    dict[str, Any],
    Path,
    str,
    str,
]:
    document = runner.load_yaml(config_path)
    contract, contract_path, contract_hash, phase = _validated_contract(
        document, config_path
    )
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != runner.MANIFEST_SCHEMA_VERSION:
        raise EnvironmentDatasetError("experiment manifest schema differs")
    if manifest.get("scenario_schema_version") != 1:
        raise EnvironmentDatasetError("experiment manifest lacks scenario schema v1")
    if manifest.get("experiment") != document.get("name"):
        raise EnvironmentDatasetError("experiment manifest name differs")
    if manifest.get("matrix_sha256") != runner.matrix_sha256(document):
        raise EnvironmentDatasetError("experiment manifest matrix hash differs")
    project_commit = manifest.get("project_commit")
    ns3_commit = manifest.get("ns3_upstream_commit")
    if (
        not isinstance(project_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", project_commit) is None
        or not isinstance(ns3_commit, str)
        or re.fullmatch(r"[0-9a-f]{40}", ns3_commit) is None
    ):
        raise EnvironmentDatasetError("experiment manifest build identity is invalid")

    expected: dict[str, dict[str, Any]] = {}
    for spec in runner.expand_config(document):
        run_id = runner.derive_run_id(
            spec["config"],
            spec["seed"],
            spec["run"],
            ns3_commit,
            project_commit,
            scenario=spec.get("scenario"),
        )
        if run_id in expected:
            raise EnvironmentDatasetError("resolved matrix contains duplicate run ids")
        expected[run_id] = spec

    manifest_runs = manifest.get("runs")
    if not isinstance(manifest_runs, list) or len(manifest_runs) != len(expected):
        raise EnvironmentDatasetError("experiment manifest is incomplete")
    recorded: dict[str, dict[str, Any]] = {}
    for item in manifest_runs:
        if not isinstance(item, dict):
            raise EnvironmentDatasetError("experiment manifest run is invalid")
        run_id = item.get("run_id")
        if not isinstance(run_id, str) or run_id in recorded or run_id not in expected:
            raise EnvironmentDatasetError("experiment manifest run id differs")
        spec = expected[run_id]
        if (
            item.get("status") != "complete"
            or item.get("directory") != run_id
            or item.get("seed") != spec["seed"]
            or item.get("run") != spec["run"]
            or _canonical(item.get("config")) != _canonical(spec["config"])
            or _canonical(item.get("scenario")) != _canonical(spec.get("scenario"))
        ):
            raise EnvironmentDatasetError(f"manifest identity differs for run {run_id}")
        recorded[run_id] = item
    if set(recorded) != set(expected):
        raise EnvironmentDatasetError("experiment manifest run set differs")

    expected_scenarios = {
        spec["scenario"]["scenario_id"]: spec["scenario"] for spec in expected.values()
    }
    manifest_scenarios = manifest.get("scenario_instances")
    if (
        not isinstance(manifest_scenarios, list)
        or _canonical(manifest_scenarios)
        != _canonical([expected_scenarios[key] for key in sorted(expected_scenarios)])
    ):
        raise EnvironmentDatasetError("experiment manifest scenario catalog differs")
    return recorded, expected, manifest, contract, contract_path, contract_hash, phase


def _run_paths(
    roots: Sequence[Path | str], expected_run_ids: set[str]
) -> dict[str, Path]:
    try:
        discovered = base._discover_run_dirs(roots)
    except base.DatasetError as error:
        raise EnvironmentDatasetError(str(error)) from error
    result: dict[str, Path] = {}
    for path in discovered:
        run_id = path.name
        if run_id not in expected_run_ids or run_id in result:
            raise EnvironmentDatasetError(f"unexpected or duplicate raw run directory: {path}")
        result[run_id] = path
    if set(result) != expected_run_ids:
        missing = sorted(expected_run_ids - set(result))
        raise EnvironmentDatasetError(
            f"raw run set is incomplete: {', '.join(missing[:8])}"
        )
    return result


def _empty_filters() -> dict[str, Any]:
    return {
        "candidate_t2_rows": 0,
        "excluded_lag8_warmup": 0,
        "excluded_secondary_direct_tx_dirty": 0,
        "excluded_secondary_active_reservation": 0,
        "excluded_any_secondary_action_dirty": 0,
        "included_rows": 0,
        "dirty_endpoint_counts": {
            "current": {"direct_tx": 0, "active_reservation": 0},
            "lag1": {"direct_tx": 0, "active_reservation": 0},
            "lag3": {"direct_tx": 0, "active_reservation": 0},
            "lag8": {"direct_tx": 0, "active_reservation": 0},
        },
    }


def _generalization_row(
    temporal_row: dict[str, str], scenario: dict[str, Any], environment: dict[str, str]
) -> dict[str, str]:
    row = {column: temporal_row[column] for column in temporal.NON_FEATURE_COLUMNS}
    row.update(
        {
            "scenario_id": scenario["scenario_id"],
            "family_id": scenario["family_id"],
            "parameter_sample": str(scenario["parameter_sample"]),
        }
    )
    row.update({column: temporal_row[column] for column in temporal.FEATURE_COLUMNS})
    row.update(environment)
    if tuple(row) != DATASET_COLUMNS:
        raise EnvironmentDatasetError("internal generalization row schema differs")
    return row


def _accumulate_exclusions(total: Counter[str], current: dict[str, Any]) -> None:
    for key, value in current.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise EnvironmentDatasetError(f"invalid base exclusion count: {key}")
        total[key] += value


def build_dataset(
    config_path: Path | str,
    experiment_manifest: Path | str,
    run_dirs: Sequence[Path | str],
    output_dir: Path | str,
) -> dict[str, Any]:
    """Validate and atomically publish one scenario-aware T2 dataset."""

    config = Path(config_path).resolve()
    manifest_path = Path(experiment_manifest).resolve()
    (
        recorded,
        expected,
        experiment,
        contract,
        contract_path,
        contract_hash,
        phase,
    ) = _validated_manifest(config, manifest_path)
    paths = _run_paths(run_dirs, set(expected))

    observable = contract["model_evaluation"]["observable_environment_features"]
    if not isinstance(observable, list) or len(observable) != len(set(observable)):
        raise EnvironmentDatasetError("observable environment feature list is invalid")
    if set(ENVIRONMENT_FEATURE_COLUMNS) - set(observable):
        raise EnvironmentDatasetError("static sender context is absent from the model contract")
    if not set(observable) <= set(FEATURE_COLUMNS):
        raise EnvironmentDatasetError("model contract names unavailable dataset features")
    if {"scenario_id", "family_id"} & set(FEATURE_COLUMNS):
        raise EnvironmentDatasetError("latent scenario identity leaked into model features")

    output = Path(output_dir).resolve()
    if output.exists():
        raise EnvironmentDatasetError(f"refusing to overwrite existing output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    filters = _empty_filters()
    base_exclusions: Counter[str] = Counter()
    source_runs: list[dict[str, Any]] = []
    scenario_run_counts: Counter[str] = Counter()
    scenario_row_counts: Counter[str] = Counter()
    family_run_counts: Counter[str] = Counter()
    family_row_counts: Counter[str] = Counter()
    reference_build: dict[str, str] | None = None
    try:
        with (temporary / OUTPUT_CSV).open("w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(
                destination,
                fieldnames=DATASET_COLUMNS,
                lineterminator="\n",
                extrasaction="raise",
            )
            writer.writeheader()
            for run_id in sorted(expected, key=lambda key: (expected[key]["seed"], key)):
                path = paths[run_id]
                try:
                    run = base._load_run(path)
                except (base.DatasetError, temporal.TemporalDatasetError) as error:
                    raise EnvironmentDatasetError(str(error)) from error
                spec = expected[run_id]
                item = recorded[run_id]
                if (
                    run.run_id != run_id
                    or run.seed != spec["seed"]
                    or run.run_number != spec["run"]
                ):
                    raise EnvironmentDatasetError(f"raw identity differs for run {run_id}")
                if (
                    run.build_identity["project_git_commit"]
                    != experiment["project_commit"]
                    or run.build_identity["ns3_upstream_commit"]
                    != experiment["ns3_upstream_commit"]
                ):
                    raise EnvironmentDatasetError(f"raw build identity differs for run {run_id}")
                if reference_build is None:
                    reference_build = run.build_identity
                elif run.build_identity != reference_build:
                    raise EnvironmentDatasetError(f"build identity differs for run {run_id}")

                scenario = item["scenario"]
                scenario_id = scenario["scenario_id"]
                family_id = scenario["family_id"]
                environment = static_environment_features(run.config)
                try:
                    context = temporal._build_context(run)
                    t2_rows, _, exclusions = base._build_rows(
                        [run], {(run.seed, run.run_number): POOL_ROLE}
                    )
                except (base.DatasetError, temporal.TemporalDatasetError) as error:
                    raise EnvironmentDatasetError(str(error)) from error
                _accumulate_exclusions(base_exclusions, exclusions)
                scenario_run_counts[scenario_id] += 1
                family_run_counts[family_id] += 1
                included_this_run = 0
                for v1_row in t2_rows:
                    filters["candidate_t2_rows"] += 1
                    frame_id = int(v1_row["frame_id"])
                    if frame_id < max(temporal.LAGS):
                        filters["excluded_lag8_warmup"] += 1
                        continue
                    current = context.endpoints.get(frame_id)
                    lagged = {
                        lag: context.endpoints.get(frame_id - lag) for lag in temporal.LAGS
                    }
                    if current is None or any(value is None for value in lagged.values()):
                        raise EnvironmentDatasetError(
                            f"{path}: frame {frame_id} lacks an exact temporal endpoint"
                        )
                    endpoint_map = {
                        "current": current,
                        **{f"lag{lag}": lagged[lag] for lag in temporal.LAGS},
                    }
                    dirty_tx = False
                    dirty_reservation = False
                    for name, endpoint in endpoint_map.items():
                        assert endpoint is not None
                        tx_dirty, reservation_dirty = temporal._endpoint_dirty(
                            context, endpoint.capture_time_ns
                        )
                        filters["dirty_endpoint_counts"][name]["direct_tx"] += int(tx_dirty)
                        filters["dirty_endpoint_counts"][name][
                            "active_reservation"
                        ] += int(reservation_dirty)
                        dirty_tx = dirty_tx or tx_dirty
                        dirty_reservation = dirty_reservation or reservation_dirty
                    filters["excluded_secondary_direct_tx_dirty"] += int(dirty_tx)
                    filters["excluded_secondary_active_reservation"] += int(
                        dirty_reservation
                    )
                    if dirty_tx or dirty_reservation:
                        filters["excluded_any_secondary_action_dirty"] += 1
                        continue
                    try:
                        temporal_row = temporal._temporal_row(
                            v1_row,
                            current,
                            {
                                lag: endpoint
                                for lag, endpoint in lagged.items()
                                if endpoint is not None
                            },
                            f"{path}: frame {frame_id}",
                        )
                    except temporal.TemporalDatasetError as error:
                        raise EnvironmentDatasetError(str(error)) from error
                    writer.writerow(_generalization_row(temporal_row, scenario, environment))
                    filters["included_rows"] += 1
                    included_this_run += 1
                scenario_row_counts[scenario_id] += included_this_run
                family_row_counts[family_id] += included_this_run
                source_runs.append(
                    {
                        "path": str(path),
                        "run_id": run.run_id,
                        "seed": run.seed,
                        "run_number": run.run_number,
                        "scenario_id": scenario_id,
                        "family_id": family_id,
                        "parameter_sample": scenario["parameter_sample"],
                        "files_sha256": context.source_hashes,
                    }
                )
        if filters["included_rows"] <= 0:
            raise EnvironmentDatasetError("action-clean filter retained no rows")
        expected_families = set(contract["sampling"]["family_order"])
        if set(family_run_counts) != expected_families:
            raise EnvironmentDatasetError("dataset does not cover every frozen scenario family")

        metadata: dict[str, Any] = {
            "dataset_schema_version": DATASET_SCHEMA_VERSION,
            "feature_contract_id": FEATURE_CONTRACT_ID,
            "comparison": {
                "file": OUTPUT_CSV,
                "analysis_stage": "T2",
                "arms": ["CONTROL", "FULL_COPY_T2"],
                "estimand": "binary_assignment_ITT_on_action_clean_temporal_population",
                "row_count": filters["included_rows"],
            },
            "validation": {
                "authoritative_validator": "validate_outputs.validate_run",
                "every_run_validated_before_source_reads": True,
                "experiment_run_ids_rederived_from_frozen_matrix": True,
                "scenario_identity_joined_from_checksum_bound_manifest": True,
                "one_run_loaded_at_a_time": True,
            },
            "experiment_identity": {
                "config_path": str(config),
                "config_file_sha256": _sha256(config),
                "matrix_sha256": runner.matrix_sha256(runner.load_yaml(config)),
                "manifest_path": str(manifest_path),
                "manifest_sha256": _sha256(manifest_path),
                "project_commit": experiment["project_commit"],
                "ns3_upstream_commit": experiment["ns3_upstream_commit"],
                "phase": phase,
            },
            "generalization_contract": {
                "id": contract["contract_id"],
                "path": str(contract_path),
                "sha256": contract_hash,
            },
            "builder_sources_sha256": {
                str(path.relative_to(ROOT)): _sha256(path) for path in BUILDER_SOURCES
            },
            "scenario_contract": {
                "identity_columns": list(SCENARIO_COLUMNS),
                "identity_columns_are_non_features": True,
                "outer_split": "leave_one_scenario_family_out",
                "outer_split_unit": "family_id",
                "inner_split_unit": "scenario_id",
                "seed_replicates_must_not_cross_inner_splits": True,
                "scenario_run_counts": dict(sorted(scenario_run_counts.items())),
                "scenario_row_counts": dict(sorted(scenario_row_counts.items())),
                "family_run_counts": dict(sorted(family_run_counts.items())),
                "family_row_counts": dict(sorted(family_row_counts.items())),
            },
            "feature_contract": {
                "feature_columns": list(FEATURE_COLUMNS),
                "base_temporal_feature_columns": list(temporal.FEATURE_COLUMNS),
                "sender_known_environment_feature_columns": list(
                    ENVIRONMENT_FEATURE_COLUMNS
                ),
                "observable_environment_features": observable,
                "categorical_features": ["x_f0_frame_type"],
                "forbidden_model_inputs": [
                    "scenario_id",
                    "family_id",
                    "parameter_sample",
                    "seed",
                    "run_id",
                    "run_number",
                    "frame_id",
                ],
                "latent_simulator_parameters_are_features": False,
                "environment_feature_availability": "known_at_or_before_stream_start",
            },
            "non_feature_columns": list(NON_FEATURE_COLUMNS),
            "split": {
                "algorithm": "lofo_at_fit_time_grouped_inner_scenario_v1",
                "stored_split_role": POOL_ROLE,
                "outer_unit": "family_id",
                "inner_unit": "scenario_id",
            },
            "filter_counts": filters,
            "base_exclusion_counts": dict(sorted(base_exclusions.items())),
            "build_identity": reference_build,
            "source_runs": source_runs,
        }
        _write_json(temporary / OUTPUT_METADATA, metadata)
        manifest = {
            "manifest_schema_version": 1,
            "hash_algorithm": HASH_ALGORITHM,
            "artifacts_sha256": {
                OUTPUT_CSV: _sha256(temporary / OUTPUT_CSV),
                OUTPUT_METADATA: _sha256(temporary / OUTPUT_METADATA),
            },
        }
        _write_json(temporary / OUTPUT_MANIFEST, manifest)
        os.replace(temporary, output)
        return metadata
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="build a validated environment-generalization temporal T2 dataset"
    )
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--experiment-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    metadata = build_dataset(
        args.config,
        args.experiment_manifest,
        args.run_dirs,
        args.output_dir,
    )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "run_count": len(metadata["source_runs"]),
                "row_count": metadata["comparison"]["row_count"],
                "family_run_counts": metadata["scenario_contract"][
                    "family_run_counts"
                ],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
