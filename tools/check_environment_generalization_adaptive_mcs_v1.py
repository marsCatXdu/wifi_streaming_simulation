#!/usr/bin/env python3
"""Verify the adaptive-MCS campaign is a one-variable 576-run ablation."""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Sequence

from run_experiments import (
    expand_config,
    load_yaml,
    validate_runtime_contract,
)
from run_environment_generalization_adaptive_mcs_v1 import (
    adaptive_cli_arguments,
    select_paired_unit_shard,
)


ROOT = Path(__file__).resolve().parents[1]
FIXED_CONFIG = ROOT / (
    "experiments/configs/environment_generalization_closed_loop_qualification_v1.yaml"
)
ADAPTIVE_CONFIG = ROOT / (
    "experiments/configs/environment_generalization_adaptive_mcs_qualification_v1.yaml"
)


class AdaptiveMcsCampaignError(RuntimeError):
    """Raised when the controlled-ablation boundary differs."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AdaptiveMcsCampaignError(message)


def _identity(spec: dict[str, Any]) -> tuple[str, int, int, str, str]:
    return (
        spec["scenario"]["scenario_id"],
        int(spec["seed"]),
        int(spec["run"]),
        spec["config"]["topology"],
        spec["config"]["policy"],
    )


def check() -> dict[str, Any]:
    """Return a compact proof after verifying the exact campaign expansion."""

    fixed_document = load_yaml(FIXED_CONFIG)
    adaptive_document = load_yaml(ADAPTIVE_CONFIG)
    fixed_runtime = validate_runtime_contract(fixed_document)
    adaptive_runtime = validate_runtime_contract(adaptive_document)
    _require(fixed_runtime is not None and adaptive_runtime is not None,
             "both campaigns require runtime contracts")
    _require(
        fixed_runtime["source_artifacts"] == adaptive_runtime["source_artifacts"],
        "runtime source artifacts differ",
    )

    fixed_specs = expand_config(fixed_document)
    adaptive_specs = expand_config(adaptive_document)
    _require(len(fixed_specs) == len(adaptive_specs) == 576,
             "campaign run count differs from 576")
    fixed_by_identity = {_identity(spec): spec for spec in fixed_specs}
    adaptive_by_identity = {_identity(spec): spec for spec in adaptive_specs}
    _require(len(fixed_by_identity) == len(adaptive_by_identity) == 576,
             "campaign identities are not unique")
    _require(fixed_by_identity.keys() == adaptive_by_identity.keys(),
             "scenario, seed, run, topology, or policy identities differ")

    observed_differences: set[str] = set()
    for identity in fixed_by_identity:
        fixed = fixed_by_identity[identity]
        adaptive = adaptive_by_identity[identity]
        _require(fixed["scenario"] == adaptive["scenario"],
                 f"scenario metadata differs for {identity}")
        normalized = copy.deepcopy(adaptive["config"])
        mcs_mode = normalized.get("wifi", {}).pop("mcs_mode", None)
        _require(mcs_mode == "adaptive", f"adaptive MCS is absent for {identity}")
        observed_differences.add("wifi.mcs_mode=fixed(default)->adaptive")
        _require(normalized == fixed["config"],
                 f"non-MCS simulation configuration differs for {identity}")

    shards = [
        select_paired_unit_shard(adaptive_specs, index, 2)
        for index in range(2)
    ]
    shard_identities = [{_identity(spec) for spec in shard} for shard in shards]
    _require([len(shard) for shard in shards] == [288, 288],
             "distributed shard sizes differ")
    _require(shard_identities[0].isdisjoint(shard_identities[1]),
             "distributed shards overlap")
    _require(shard_identities[0] | shard_identities[1] == set(adaptive_by_identity),
             "distributed shards do not cover the campaign")

    paired_units: dict[tuple[str, int, int], int] = {}
    for spec in adaptive_specs:
        unit = (
            spec["scenario"]["scenario_id"],
            int(spec["seed"]),
            int(spec["run"]),
        )
        paired_units[unit] = paired_units.get(unit, 0) + 1
    _require(len(paired_units) == 192 and set(paired_units.values()) == {3},
             "paired-unit arm closure differs")

    return {
        "status": "pass",
        "fixed_runtime_contract_id": fixed_runtime["runtime_contract_id"],
        "adaptive_runtime_contract_id": adaptive_runtime["runtime_contract_id"],
        "simulation_run_count": len(adaptive_specs),
        "paired_unit_count": len(paired_units),
        "arms_per_paired_unit": 3,
        "shard_run_counts": [len(shard) for shard in shards],
        "only_simulation_config_difference": sorted(observed_differences),
    }


def unique_configuration_checks() -> list[dict[str, Any]]:
    """Return one representative of each adaptive scenario/arm configuration."""

    specs = expand_config(load_yaml(ADAPTIVE_CONFIG))
    unique: dict[str, dict[str, Any]] = {}
    for spec in specs:
        key = json.dumps(spec["config"], sort_keys=True, separators=(",", ":"))
        unique.setdefault(key, spec)
    checks = sorted(unique.values(), key=_identity)
    _require(len(checks) == 144, "adaptive unique configuration count differs")
    return checks


def default_executable() -> Path:
    """Resolve the one built default-profile streaming executable."""

    directory = ROOT / "build/contrib/wifi-streaming/examples"
    candidates = [
        path
        for path in sorted(directory.glob("ns3.*-streaming-experiment-default"))
        if path.is_file() and os.access(path, os.X_OK)
    ]
    _require(len(candidates) == 1, "expected one built streaming executable")
    return candidates[0].resolve()


def _run_configuration_check(executable: Path, spec: dict[str, Any]) -> None:
    command = [
        str(executable),
        *adaptive_cli_arguments(spec["config"], ADAPTIVE_CONFIG.parent),
        f"--seed={spec['seed']}",
        f"--run={spec['run']}",
        "--configurationCheckOnly=1",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode:
        detail = "\n".join((completed.stdout + completed.stderr).splitlines()[-20:])
        raise AdaptiveMcsCampaignError(
            f"configuration rejected for {_identity(spec)}:\n{detail}"
        )


def check_configurations(executable: Path, workers: int) -> int:
    """Fail fast through every unique adaptive scenario/arm configuration."""

    _require(workers > 0, "configuration-check workers must be positive")
    checks = unique_configuration_checks()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_run_configuration_check, executable, spec): spec
            for spec in checks
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise
    return len(checks)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration-check", action="store_true")
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--workers", type=int, default=64)
    args = parser.parse_args(argv)
    result = check()
    if args.configuration_check:
        executable = args.executable.resolve() if args.executable else default_executable()
        _require(executable.is_file() and os.access(executable, os.X_OK),
                 "streaming executable is absent or not executable")
        result["checked_unique_configurations"] = check_configurations(
            executable, args.workers
        )
        result["configuration_check_workers"] = args.workers
        result["executable"] = str(executable)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
