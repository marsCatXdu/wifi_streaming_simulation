#!/usr/bin/env python3
"""Run one whole-paired-unit shard of the adaptive-MCS qualification."""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Sequence

import run_experiments as runner


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / (
    "experiments/configs/environment_generalization_adaptive_mcs_qualification_v1.yaml"
)
MCS_CLI_KEY = "mcsMode"


class AdaptiveMcsRunnerError(RuntimeError):
    """Raised when an adaptive-MCS shard identity differs."""


def adaptive_cli_arguments(config: dict[str, Any], config_dir: Path) -> list[str]:
    """Expand arguments with the campaign-local MCS attribute mapping."""

    previous = runner.CLI_KEYS.get("mcs_mode")
    runner.CLI_KEYS["mcs_mode"] = MCS_CLI_KEY
    try:
        return runner.cli_arguments(config, config_dir)
    finally:
        if previous is None:
            runner.CLI_KEYS.pop("mcs_mode", None)
        else:
            runner.CLI_KEYS["mcs_mode"] = previous


def _unit(spec: dict[str, Any]) -> tuple[str, int, int]:
    scenario = spec.get("scenario")
    scenario_id = scenario.get("scenario_id", "") if isinstance(scenario, dict) else ""
    return (str(scenario_id), int(spec["seed"]), int(spec["run"]))


def select_paired_unit_shard(
    specs: list[dict[str, Any]], shard_index: int, shard_count: int
) -> list[dict[str, Any]]:
    """Select complete scenario/seed/run units by stable round robin."""

    if shard_count < 1 or not 0 <= shard_index < shard_count:
        raise AdaptiveMcsRunnerError("shard index must be in [0, shard count)")
    ordered_units = list(dict.fromkeys(_unit(spec) for spec in specs))
    selected_units = {
        unit
        for ordinal, unit in enumerate(ordered_units)
        if ordinal % shard_count == shard_index
    }
    return [spec for spec in specs if _unit(spec) in selected_units]


def shard_descriptor(
    full_specs: list[dict[str, Any]],
    selected_specs: list[dict[str, Any]],
    shard_index: int,
    shard_count: int,
) -> dict[str, Any]:
    """Build the manifest identity for one distributed shard."""

    return {
        "schema_version": 1,
        "selection": "paired_unit_round_robin_v1",
        "index": shard_index,
        "count": shard_count,
        "full_matrix_run_count": len(full_specs),
        "selected_run_count": len(selected_specs),
    }


def _validate_shard_manifest(path: Path, expected: dict[str, Any]) -> None:
    if not path.exists():
        return
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AdaptiveMcsRunnerError(f"cannot read shard manifest: {error}") from error
    if manifest.get("shard") != expected:
        raise AdaptiveMcsRunnerError(
            "output root belongs to a different distributed shard"
        )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, default=2)
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("workers must be positive")

    document = runner.load_yaml(CONFIG_PATH)
    runtime_contract = runner.validate_runtime_contract(document)
    commit = runner.project_commit()
    full_specs = runner.expand_config(document)
    try:
        specs = select_paired_unit_shard(
            full_specs, args.shard_index, args.shard_count
        )
    except AdaptiveMcsRunnerError as error:
        parser.error(str(error))
    shard = shard_descriptor(
        full_specs, specs, args.shard_index, args.shard_count
    )
    if len(full_specs) != 576 or shard["selected_run_count"] != 576 // args.shard_count:
        raise AdaptiveMcsRunnerError("campaign or shard run count differs")

    runner.CLI_KEYS["mcs_mode"] = MCS_CLI_KEY
    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    for spec in specs:
        spec["run_id"] = runner.derive_run_id(
            spec["config"],
            spec["seed"],
            spec["run"],
            runner.NS3_UPSTREAM_COMMIT,
            commit,
            runtime_contract,
            spec.get("scenario"),
        )
        if spec["run_id"] in seen:
            raise AdaptiveMcsRunnerError("duplicate resolved run in shard")
        seen.add(spec["run_id"])
        completed = output_root / spec["run_id"]
        if completed.exists():
            runner.validate_run(
                completed,
                spec["run_id"],
                commit,
                runner.NS3_UPSTREAM_COMMIT,
            )
            if not args.resume:
                raise FileExistsError(f"completed duplicate rejected: {spec['run_id']}")
            spec["completed"] = True

    experiment = str(document["name"])
    matrix_sha = runner.matrix_sha256(document)
    manifest_path = output_root / "experiment_manifest.json"
    runner.validate_existing_manifest(
        manifest_path,
        experiment,
        matrix_sha,
        commit,
        seen,
        runtime_contract,
    )
    _validate_shard_manifest(manifest_path, shard)
    runner.write_experiment_description(document, specs, output_root)
    if not args.no_build:
        subprocess.run(
            [str(ROOT / "ns3"), "build", "streaming-experiment"],
            cwd=ROOT,
            check=True,
        )
    manifest = runner.build_experiment_manifest(
        experiment,
        matrix_sha,
        CONFIG_PATH,
        commit,
        specs,
        runtime_contract,
    )
    manifest["shard"] = copy.deepcopy(shard)
    runner.atomic_json(manifest_path, manifest)

    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                runner.run_one,
                spec,
                output_root,
                CONFIG_PATH.parent,
                commit,
            ): spec
            for spec in specs
            if not spec.get("completed")
        }
        for future in as_completed(futures):
            try:
                result = future.result()
                manifest["runs"].append(result)
                manifest["runs"].sort(key=lambda item: item["run_id"])
                runner.atomic_json(manifest_path, manifest)
                print(f"COMPLETE {result['run_id']}", flush=True)
            except Exception as error:
                failures.append(str(error))
                print(
                    f"FAILED {futures[future]['run_id']}: {error}",
                    flush=True,
                )
    if failures:
        raise SystemExit("\n".join(failures))


if __name__ == "__main__":
    main()
