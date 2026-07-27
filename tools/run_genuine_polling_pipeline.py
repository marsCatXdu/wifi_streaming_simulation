#!/usr/bin/env python3
"""Run the complete resumable genuine-polling production pipeline."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from run_experiments import NS3_UPSTREAM_COMMIT

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/genuine_polling_v1"
STATE = RESULTS / "pipeline_state.json"
LOGS = RESULTS / "logs"
CONFIGS = RESULTS / "configs"
ANALYSIS = ROOT / "experiments/configs/prediction_analysis.yaml"
LOADS = ROOT / "experiments/configs/prediction_loads.yaml"
REPLAY = ROOT / "experiments/configs/prediction_online_replay.yaml"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, encoding="utf-8"
    ) as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")
        temporary = Path(output.name)
    os.replace(temporary, path)


def load_state() -> dict[str, object]:
    if not STATE.exists():
        return {"schema_version": 1, "completed_phases": []}
    value = json.loads(STATE.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1:
        raise ValueError("unsupported genuine-polling pipeline state")
    return value


def command(phase: str, arguments: list[str]) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / f"{phase}.log"
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n$ {' '.join(arguments)}\n")
        log.flush()
        process = subprocess.run(
            arguments,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
    if process.returncode:
        raise RuntimeError(f"phase {phase} failed; see {log_path.relative_to(ROOT)}")


def run_phase(
    state: dict[str, object], phase: str, action: Callable[[], None]
) -> None:
    completed = state["completed_phases"]
    if not isinstance(completed, list):
        raise ValueError("invalid pipeline state")
    if phase in completed:
        print(f"PIPELINE_SKIP phase={phase}", flush=True)
        return
    print(f"PIPELINE_START phase={phase}", flush=True)
    action()
    completed.append(phase)
    state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    atomic_json(STATE, state)
    print(f"PIPELINE_DONE phase={phase}", flush=True)


def generated_config(source_name: str, output_name: str, workers: int) -> Path:
    source = ROOT / "experiments/configs" / source_name
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{source}: YAML root must be a mapping")
    document["name"] = f"genuine-polling-v1-{output_name}"
    document["output_root"] = str(
        (RESULTS / output_name / "runs").relative_to(ROOT)
    )
    document["workers"] = workers
    prediction = document.get("base", {}).get("prediction")
    if isinstance(prediction, dict) and prediction.get(
        "prediction_telemetry_enabled", False
    ):
        prediction["prediction_polling_interval_us"] = 1000
        prediction["prediction_polling_report_delay_us"] = 1000
    CONFIGS.mkdir(parents=True, exist_ok=True)
    destination = CONFIGS / source_name
    content = yaml.safe_dump(document, sort_keys=False)
    if destination.exists() and destination.read_text(encoding="utf-8") != content:
        raise ValueError(f"generated config changed after pipeline start: {destination}")
    destination.write_text(content, encoding="utf-8")
    return destination


def preserve_failed_attempts(result_root: Path, round_number: int) -> None:
    attempts = sorted(result_root.glob(".*.attempt-*"))
    if not attempts:
        return
    destination = result_root / "failed_attempts" / f"round_{round_number}"
    destination.mkdir(parents=True, exist_ok=True)
    for attempt in attempts:
        target = destination / attempt.name.lstrip(".")
        if target.exists():
            raise FileExistsError(target)
        shutil.move(str(attempt), target)


def run_matrix(phase: str, config: Path, result_root: Path, workers: int) -> None:
    """Run and retry only incomplete matrix entries, retaining every failure log."""
    result_root.mkdir(parents=True, exist_ok=True)
    for round_number in range(1, 4):
        arguments = [
            sys.executable,
            "tools/run_experiments.py",
            str(config),
            "--workers",
            str(workers),
            "--output-root",
            str(result_root),
            "--no-build",
            "--resume",
        ]
        try:
            command(f"{phase}_round_{round_number}", arguments)
            return
        except RuntimeError:
            preserve_failed_attempts(result_root, round_number)
            if round_number == 3:
                raise


def run_closed_matrix(phase: str, config: Path, result_root: Path, workers: int) -> None:
    """Run a closed-loop matrix and reject legacy compiled predictors."""
    run_matrix(phase, config, result_root, workers)
    selective_count = 0
    for config_path in result_root.glob("*/resolved_config.json"):
        value = json.loads(config_path.read_text(encoding="utf-8"))
        if value.get("policy") != "selective_duplication":
            continue
        selective_count += 1
        model_id = value.get("selectiveDuplication", {}).get("model_id")
        if model_id != "commodity_polling_1ms_genuine_v1":
            raise ValueError(f"{config_path}: closed loop used legacy predictor {model_id}")
    if selective_count == 0:
        raise ValueError(f"{result_root}: no selective-duplication runs")


def git_value(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def dependency_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def main() -> int:
    if len(sys.argv) != 1:
        raise SystemExit("run_genuine_polling_pipeline.py accepts no arguments")
    workers = max(1, os.cpu_count() or 1)
    RESULTS.mkdir(parents=True, exist_ok=True)
    state = load_state()
    if "initial_provenance" not in state:
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        state["initial_provenance"] = {
            "project_git_commit": git_value("rev-parse", "HEAD"),
            "ns3_upstream_commit": NS3_UPSTREAM_COMMIT,
            "git_status_sha256": hashlib.sha256(status).hexdigest(),
        }
        atomic_json(STATE, state)
    stage_config = generated_config("prediction_stage_a.yaml", "source_stage_a", workers)
    obss_config = generated_config("prediction_obss.yaml", "source_obss", workers)
    closed_obss_config = generated_config(
        "closed_loop_selective_duplication_obss.yaml", "closed_loop_obss", workers
    )
    closed_combined_config = generated_config(
        "closed_loop_selective_duplication_combined.yaml",
        "closed_loop_combined",
        workers,
    )

    run_phase(
        state,
        "build_source",
        lambda: command("build_source", ["./ns3", "build", "streaming-experiment"]),
    )
    run_phase(
        state,
        "source_stage_a",
        lambda: run_matrix(
            "source_stage_a", stage_config, RESULTS / "source_stage_a/runs", workers
        ),
    )
    run_phase(
        state,
        "source_obss",
        lambda: run_matrix(
            "source_obss", obss_config, RESULTS / "source_obss/runs", workers
        ),
    )

    dataset = RESULTS / "dataset"
    run_phase(
        state,
        "dataset",
        lambda: command(
            "dataset",
            [
                sys.executable,
                "tools/build_prediction_dataset.py",
                str(RESULTS / "source_stage_a/runs"),
                str(RESULTS / "source_obss/runs"),
                "--output-dir",
                str(dataset),
                "--analysis-config",
                str(ANALYSIS),
                "--load-config",
                str(LOADS),
                "--format",
                "parquet",
            ],
        ),
    )
    evaluation = RESULTS / "evaluation"
    run_phase(
        state,
        "evaluation",
        lambda: command(
            "evaluation",
            [
                sys.executable,
                "tools/evaluate_prediction.py",
                "--dataset-dir",
                str(dataset),
                "--output-dir",
                str(evaluation),
                "--analysis-config",
                str(ANALYSIS),
                "--seed",
                "20250308",
            ],
        ),
    )
    models = RESULTS / "models"
    run_phase(
        state,
        "train_predictor",
        lambda: command(
            "train_predictor",
            [
                sys.executable,
                "tools/replay_online_prediction.py",
                "train",
                str(dataset),
                "--analysis-config",
                str(ANALYSIS),
                "--replay-config",
                str(REPLAY),
                "--output-dir",
                str(models),
            ],
        ),
    )

    def export() -> None:
        temporary_model = models / "prediction-model-data-v1.cc"
        temporary_golden = models / "prediction-model-golden-v1.h"
        command(
            "export_predictor",
            [
                sys.executable,
                "tools/export_prediction_models_v1.py",
                "--bundle",
                str(models / "model_bundle.pkl"),
                "--manifest",
                str(models / "model_bundle_manifest.json"),
                "--model-output",
                str(temporary_model),
                "--golden-output",
                str(temporary_golden),
            ],
        )
        os.replace(
            temporary_model,
            ROOT / "contrib/wifi-streaming/model/prediction-model-data-v1.cc",
        )
        os.replace(
            temporary_golden,
            ROOT / "contrib/wifi-streaming/test/prediction-model-golden-v1.h",
        )
        command("export_predictor_build", ["./ns3", "build", "streaming-experiment"])
        command("export_predictor_parity", ["./test.py", "-s", "wifi-streaming"])

    run_phase(state, "export_predictor", export)
    run_phase(
        state,
        "closed_loop_obss",
        lambda: run_closed_matrix(
            "closed_loop_obss",
            closed_obss_config,
            RESULTS / "closed_loop_obss/runs",
            workers,
        ),
    )
    run_phase(
        state,
        "closed_loop_combined",
        lambda: run_closed_matrix(
            "closed_loop_combined",
            closed_combined_config,
            RESULTS / "closed_loop_combined/runs",
            workers,
        ),
    )

    def report() -> None:
        command(
            "report_obss",
            [
                sys.executable,
                "tools/plot_selective_duplication.py",
                str(RESULTS / "closed_loop_obss/runs"),
            ],
        )
        command(
            "report_combined",
            [
                sys.executable,
                "tools/plot_selective_duplication.py",
                str(RESULTS / "closed_loop_combined/runs"),
            ],
        )

    run_phase(state, "reports", report)

    def manifest() -> None:
        tracked_diff = subprocess.run(
            ["git", "diff", "--binary", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        artifacts = [
            dataset / "dataset_manifest.json",
            dataset / "dataset_validation.json",
            models / "model_bundle.pkl",
            models / "model_bundle_manifest.json",
            evaluation / "analysis_manifest.json",
        ]
        missing = [path for path in artifacts if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"pipeline artifacts are missing: {missing}")
        value = {
            "pipeline_schema_version": 1,
            "pipeline_id": "genuine_polling_v1",
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
            "initial_provenance": state["initial_provenance"],
            "project_git_commit": git_value("rev-parse", "HEAD"),
            "ns3_upstream_commit": NS3_UPSTREAM_COMMIT,
            "dirty_state_sha256": hashlib.sha256(tracked_diff).hexdigest(),
            "workers": workers,
            "dependencies": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "pyyaml": dependency_version("PyYAML"),
                "numpy": dependency_version("numpy"),
                "scikit_learn": dependency_version("scikit-learn"),
                "pyarrow": dependency_version("pyarrow"),
            },
            "config_sha256": {
                str(path.relative_to(ROOT)): sha256(path)
                for path in (stage_config, obss_config, closed_obss_config, closed_combined_config)
            },
            "artifacts": {
                str(path.relative_to(ROOT)): sha256(path) for path in artifacts
            },
        }
        atomic_json(RESULTS / "pipeline_manifest.json", value)

    run_phase(state, "manifest", manifest)
    print(f"PIPELINE_COMPLETE manifest={RESULTS / 'pipeline_manifest.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
