#!/usr/bin/env python3
"""Fail-fast check every unique held-out qualification configuration."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Sequence

import generate_environment_generalization_qualification_v1 as generator
import run_experiments


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / (
    "experiments/configs/"
    "environment_generalization_closed_loop_qualification_v1.yaml"
)
EXPECTED_UNIQUE_CONFIGURATIONS = 144


class ConfigurationCheckError(RuntimeError):
    """Raised when the matrix or executable rejects a frozen configuration."""


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def build_checks() -> list[dict[str, Any]]:
    """Return one check for each unique scenario/arm configuration."""

    contract = generator.load_contract()
    document = generator.build_document(contract)
    specs = generator.validate_document(document, contract)
    unique: dict[str, dict[str, Any]] = {}
    for spec in specs:
        key = _canonical(spec["config"])
        previous = unique.setdefault(
            key,
            {
                "config": spec["config"],
                "scenario": spec["scenario"],
                "seed": spec["seed"],
                "run": spec["run"],
            },
        )
        if _canonical(previous["scenario"]) != _canonical(spec["scenario"]):
            raise ConfigurationCheckError(
                "one resolved configuration is shared by different scenarios"
            )
    checks = sorted(
        unique.values(),
        key=lambda item: (
            item["scenario"]["family_id"],
            item["scenario"]["scenario_id"],
            item["config"]["topology"],
            item["config"]["policy"],
        ),
    )
    if len(checks) != EXPECTED_UNIQUE_CONFIGURATIONS:
        raise ConfigurationCheckError(
            f"expected {EXPECTED_UNIQUE_CONFIGURATIONS} unique configurations, "
            f"observed {len(checks)}"
        )
    return checks


def default_executable() -> Path:
    """Resolve the one built default-profile streaming executable."""

    directory = ROOT / "build/contrib/wifi-streaming/examples"
    candidates = sorted(directory.glob("ns3.*-streaming-experiment-default"))
    candidates = [
        path for path in candidates if path.is_file() and os.access(path, os.X_OK)
    ]
    if len(candidates) != 1:
        raise ConfigurationCheckError(
            "expected exactly one built default streaming-experiment executable"
        )
    return candidates[0].resolve()


def _run_check(executable: Path, check: dict[str, Any]) -> None:
    arguments = run_experiments.cli_arguments(check["config"], CONFIG_PATH.parent)
    command = [
        str(executable),
        *arguments,
        f"--seed={check['seed']}",
        f"--run={check['run']}",
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
        detail = "\n".join(
            (completed.stdout + completed.stderr).splitlines()[-20:]
        )
        scenario = check["scenario"]["scenario_id"]
        policy = check["config"]["policy"]
        raise ConfigurationCheckError(
            f"configuration rejected for {scenario}/{policy}:\n{detail}"
        )


def check_configurations(executable: Path, workers: int) -> int:
    """Check the exact unique configuration set in parallel."""

    if workers <= 0:
        raise ConfigurationCheckError("workers must be positive")
    checks = build_checks()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_run_check, executable, check): check for check in checks
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise
    return len(checks)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--workers", type=int, default=64)
    args = parser.parse_args(argv)
    executable = (
        args.executable.resolve() if args.executable is not None else default_executable()
    )
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ConfigurationCheckError(f"executable is absent or not executable: {executable}")
    checked = check_configurations(executable, args.workers)
    print(
        json.dumps(
            {
                "checked_unique_configurations": checked,
                "executable": str(executable),
                "workers": args.workers,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
