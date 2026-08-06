#!/usr/bin/env python3
"""Run the frozen environment-generalization analysis pipeline in place."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

import analyze_environment_generalization_lofo as lofo_analysis
import analyze_environment_generalization_policy as policy_analysis
import build_environment_generalization_dataset as dataset_builder
import plot_environment_generalization_analysis as plotting


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_SCHEMA_VERSION = 1
EXPECTED_RUN_COUNT = 384
DEFAULT_CONFIG = ROOT / (
    "experiments/configs/environment_generalization_randomized_collection_v1.yaml"
)
OUTPUT_DATASET = "dataset"
OUTPUT_LOFO = "lofo"
OUTPUT_POLICY = "policy"
OUTPUT_PLOTS = "plots"
OUTPUT_MANIFEST = "analysis_pipeline_manifest.json"
PIPELINE_SOURCES = (
    Path(__file__).resolve(),
    Path(dataset_builder.__file__).resolve(),
    Path(lofo_analysis.__file__).resolve(),
    Path(policy_analysis.__file__).resolve(),
    Path(plotting.__file__).resolve(),
)


class PipelineError(RuntimeError):
    """Raised when the analysis pipeline boundary is incomplete or ambiguous."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise PipelineError(f"cannot hash {path}: {error}") from error
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PipelineError(f"cannot read {path}: {error}") from error
    if not isinstance(value, dict):
        raise PipelineError(f"{path}: expected a JSON object")
    return value


def _git(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise PipelineError(f"cannot inspect git {' '.join(args)}") from error


def resolve_run_directories(run_root: Path | str) -> tuple[Path, tuple[Path, ...]]:
    """Resolve exactly the complete manifest's 384 canonical run directories."""

    root = Path(run_root).resolve()
    manifest_path = root / "experiment_manifest.json"
    manifest = _read_json(manifest_path)
    rows = manifest.get("runs")
    if not isinstance(rows, list) or len(rows) != EXPECTED_RUN_COUNT:
        raise PipelineError("collection manifest run count differs")
    directories: list[Path] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise PipelineError(f"collection manifest run {index} differs")
        run_id = row.get("run_id")
        directory = row.get("directory")
        if (
            not isinstance(run_id, str)
            or not run_id
            or run_id in seen
            or row.get("status") != "complete"
            or directory != run_id
        ):
            raise PipelineError(f"collection manifest run {index} is incomplete")
        path = root / run_id
        if not path.is_dir():
            raise PipelineError(f"collection run directory is absent: {run_id}")
        seen.add(run_id)
        directories.append(path)
    if len(seen) != EXPECTED_RUN_COUNT:
        raise PipelineError("collection manifest run identities differ")
    return manifest_path, tuple(directories)


def _run(command: Sequence[str], environment: dict[str, str]) -> None:
    print(json.dumps({"command": list(command)}, sort_keys=True), flush=True)
    try:
        subprocess.run(
            list(command),
            cwd=ROOT,
            env=environment,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise PipelineError(f"pipeline command failed: {command[1]}") from error


def _stage_manifest(path: Path) -> dict[str, Any]:
    document = _read_json(path)
    if document.get("hash_algorithm") != "sha256":
        raise PipelineError(f"stage manifest hash algorithm differs: {path}")
    return {
        "path": str(path),
        "sha256": _sha256(path),
    }


def _validate_artifact_directory(
    directory: Path,
    expected_files: set[str],
) -> dict[str, Any]:
    """Validate one atomic stage directory and every declared artifact hash."""

    if not directory.is_dir() or directory.is_symlink():
        raise PipelineError(f"reusable stage directory differs: {directory}")
    manifest = _read_json(directory / "artifact_manifest.json")
    if (
        set(manifest)
        != {"manifest_schema_version", "hash_algorithm", "artifacts_sha256"}
        or manifest.get("manifest_schema_version") != 1
        or manifest.get("hash_algorithm") != "sha256"
        or set(manifest.get("artifacts_sha256", {})) != expected_files
    ):
        raise PipelineError(f"reusable stage manifest differs: {directory}")
    for name, expected in manifest["artifacts_sha256"].items():
        path = directory / name
        if (
            not path.is_file()
            or path.is_symlink()
            or not isinstance(expected, str)
            or _sha256(path) != expected
        ):
            raise PipelineError(f"reusable stage artifact differs: {path}")
    return manifest


def _validate_reusable_prefix(
    destination: Path,
    config: Path,
    experiment_manifest: Path,
    run_directories: Sequence[Path],
) -> None:
    """Validate the completed dataset and LOFO prefix after a later-stage failure."""

    if not destination.is_dir() or destination.is_symlink():
        raise PipelineError("reusable output root is not a regular directory")
    expected_children = {OUTPUT_DATASET, OUTPUT_LOFO}
    observed_children = {path.name for path in destination.iterdir()}
    if observed_children != expected_children:
        raise PipelineError(
            "reusable output root must contain exactly dataset and LOFO stages"
        )
    dataset_dir = destination / OUTPUT_DATASET
    lofo_dir = destination / OUTPUT_LOFO
    dataset_manifest = _validate_artifact_directory(
        dataset_dir,
        {dataset_builder.OUTPUT_CSV, dataset_builder.OUTPUT_METADATA},
    )
    metadata = _read_json(dataset_dir / dataset_builder.OUTPUT_METADATA)
    identity = metadata.get("experiment_identity", {})
    expected_builder_sources = {
        str(path.relative_to(ROOT)): _sha256(path)
        for path in dataset_builder.BUILDER_SOURCES
    }
    source_runs = metadata.get("source_runs")
    if (
        identity.get("config_file_sha256") != _sha256(config)
        or identity.get("matrix_sha256")
        != dataset_builder.runner.matrix_sha256(
            dataset_builder.runner.load_yaml(config)
        )
        or identity.get("manifest_sha256") != _sha256(experiment_manifest)
        or identity.get("phase") != "randomized_collection"
        or metadata.get("builder_sources_sha256") != expected_builder_sources
        or not isinstance(source_runs, list)
        or len(source_runs) != EXPECTED_RUN_COUNT
        or {row.get("run_id") for row in source_runs}
        != {path.name for path in run_directories}
    ):
        raise PipelineError("reusable dataset provenance differs")

    lofo_manifest = _validate_artifact_directory(
        lofo_dir,
        {lofo_analysis.OUTPUT_PREDICTIONS, lofo_analysis.OUTPUT_METRICS},
    )
    lofo_metrics = _read_json(lofo_dir / lofo_analysis.OUTPUT_METRICS)
    expected_lofo_sources = {
        str(path.relative_to(ROOT)): _sha256(path)
        for path in lofo_analysis.ANALYSIS_SOURCES
    }
    dataset = lofo_metrics.get("dataset", {})
    if (
        lofo_metrics.get("analysis_id") != "environment-generalization-lofo-v1"
        or lofo_metrics.get("analysis_sources_sha256") != expected_lofo_sources
        or dataset.get("artifact_manifest_sha256")
        != _sha256(dataset_dir / dataset_builder.OUTPUT_MANIFEST)
        or dataset.get("dataset_metadata_sha256")
        != dataset_manifest["artifacts_sha256"][dataset_builder.OUTPUT_METADATA]
        or dataset.get("dataset_csv_sha256")
        != dataset_manifest["artifacts_sha256"][dataset_builder.OUTPUT_CSV]
        or lofo_metrics.get("software", {}).get("git_status_porcelain") != ""
        or lofo_manifest.get("hash_algorithm") != "sha256"
    ):
        raise PipelineError("reusable LOFO provenance differs")


def run_pipeline(
    run_root: Path | str,
    output_root: Path | str,
    config_path: Path | str = DEFAULT_CONFIG,
    *,
    resume_completed_prefix: bool = False,
) -> dict[str, Any]:
    """Execute all frozen stages and publish their top-level provenance."""

    destination = Path(output_root).resolve()
    config = Path(config_path).resolve()
    if destination.exists() and not resume_completed_prefix:
        raise PipelineError(f"refusing to overwrite output root: {destination}")
    if not config.is_file():
        raise PipelineError(f"collection config is absent: {config}")
    if _git("status", "--porcelain", "--untracked-files=all"):
        raise PipelineError("analysis requires a clean worktree")
    experiment_manifest, run_directories = resolve_run_directories(run_root)
    if resume_completed_prefix:
        _validate_reusable_prefix(
            destination, config, experiment_manifest, run_directories
        )
    else:
        destination.mkdir(parents=True)
    dataset_dir = destination / OUTPUT_DATASET
    lofo_dir = destination / OUTPUT_LOFO
    policy_dir = destination / OUTPUT_POLICY
    plot_dir = destination / OUTPUT_PLOTS
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT / "tools")
    environment.setdefault(
        "MPLCONFIGDIR", "/tmp/wifi-streaming-environment-matplotlib"
    )
    if not resume_completed_prefix:
        _run(
            [
                sys.executable,
                str(Path(dataset_builder.__file__).resolve()),
                "--config",
                str(config),
                "--experiment-manifest",
                str(experiment_manifest),
                "--output-dir",
                str(dataset_dir),
                *[str(path) for path in run_directories],
            ],
            environment,
        )
        _run(
            [
                sys.executable,
                str(Path(lofo_analysis.__file__).resolve()),
                "--dataset-dir",
                str(dataset_dir),
                "--output-dir",
                str(lofo_dir),
            ],
            environment,
        )
    _run(
        [
            sys.executable,
            str(Path(policy_analysis.__file__).resolve()),
            "--dataset-dir",
            str(dataset_dir),
            "--prediction-dir",
            str(lofo_dir),
            "--output-dir",
            str(policy_dir),
        ],
        environment,
    )
    _run(
        [
            sys.executable,
            str(Path(plotting.__file__).resolve()),
            "--lofo-dir",
            str(lofo_dir),
            "--policy-dir",
            str(policy_dir),
            "--output-dir",
            str(plot_dir),
        ],
        environment,
    )
    manifest = {
        "pipeline_schema_version": PIPELINE_SCHEMA_VERSION,
        "pipeline_id": "environment-generalization-analysis-v1",
        "hash_algorithm": "sha256",
        "project_git_commit": _git("rev-parse", "HEAD"),
        "project_git_status_porcelain": _git(
            "status", "--porcelain", "--untracked-files=all"
        ),
        "software": {
            "python": platform.python_version(),
            "numpy": importlib.metadata.version("numpy"),
            "scikit_learn": importlib.metadata.version("scikit-learn"),
            "matplotlib": importlib.metadata.version("matplotlib"),
        },
        "sources_sha256": {
            str(source.relative_to(ROOT)): _sha256(source)
            for source in PIPELINE_SOURCES
        },
        "inputs_sha256": {
            str(config): _sha256(config),
            str(experiment_manifest): _sha256(experiment_manifest),
        },
        "run_count": len(run_directories),
        "reused_completed_stages": (
            [OUTPUT_DATASET, OUTPUT_LOFO] if resume_completed_prefix else []
        ),
        "stage_manifests": {
            OUTPUT_DATASET: _stage_manifest(
                dataset_dir / dataset_builder.OUTPUT_MANIFEST
            ),
            OUTPUT_LOFO: _stage_manifest(lofo_dir / lofo_analysis.OUTPUT_MANIFEST),
            OUTPUT_POLICY: _stage_manifest(
                policy_dir / policy_analysis.OUTPUT_MANIFEST
            ),
            OUTPUT_PLOTS: _stage_manifest(plot_dir / plotting.OUTPUT_MANIFEST),
        },
    }
    manifest_path = destination / OUTPUT_MANIFEST
    manifest_path.write_text(
        json.dumps(
            manifest,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output_root": str(destination),
                "pipeline_manifest_sha256": _sha256(manifest_path),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="run the complete frozen environment-generalization analysis"
    )
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--resume-completed-prefix", action="store_true")
    args = parser.parse_args(argv)
    run_pipeline(
        args.run_root,
        args.output_root,
        args.config,
        resume_completed_prefix=args.resume_completed_prefix,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
