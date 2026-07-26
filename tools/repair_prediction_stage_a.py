#!/usr/bin/env python3
"""Run bounded, resumable Stage-A repair rounds until all pairs are complete."""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

import yaml

from prepare_prediction_stage_a_round2 import (
    POLICIES,
    Condition,
    cleanup_failed_attempts,
    completed_policy_seeds,
    condition_templates,
    configured_seeds,
    plan_fresh_seeds,
    semantic_condition,
)
from run_experiments import expand_config, load_yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "experiments/configs"
ORIGINAL_CONFIG = CONFIG_DIR / "prediction_stage_a.yaml"
ORIGINAL_RESULTS = ROOT / "results/prediction_stage_a"
REPAIR_PREFIX = "prediction_stage_a_repair_v1"
TARGET_GROUPS = 10
MAX_ROUNDS = 10
MAX_NO_PROGRESS_ROUNDS = 2
ROUND_PATTERN = re.compile(rf"^{REPAIR_PREFIX}_round([1-9][0-9]*)\.yaml$")
ROUND_MARKER = ".stage_a_repair_round.json"


def emit(event: str, **values: object) -> None:
    """Print one stable, immediately visible progress sentinel."""
    fields = " ".join(f"{key}={value}" for key, value in values.items())
    print(f"STAGE_A_REPAIR_{event}" + (f" {fields}" if fields else ""), flush=True)


def repair_config_paths(config_dir: Path = CONFIG_DIR) -> list[Path]:
    """Return original and all existing repair matrix files."""
    paths = [config_dir / ORIGINAL_CONFIG.name]
    paths.extend(
        path
        for path in config_dir.glob(f"{REPAIR_PREFIX}*.yaml")
        if path.is_file()
    )
    return sorted(set(paths), key=lambda path: path.name)


def repair_result_roots(
    results_dir: Path = ROOT / "results",
) -> list[Path]:
    """Return original and all direct Stage-A repair result roots."""
    roots = [results_dir / ORIGINAL_RESULTS.name]
    if results_dir.is_dir():
        roots.extend(
            child
            for child in results_dir.iterdir()
            if child.is_dir() and child.name.startswith(REPAIR_PREFIX)
        )
    return sorted(set(roots), key=lambda path: path.name)


def attempted_seeds(result_roots: Iterable[Path]) -> dict[Condition, set[int]]:
    """Collect seeds from completed and direct in-progress attempt directories."""
    used: dict[Condition, set[int]] = defaultdict(set)
    for root in result_roots:
        if not root.is_dir():
            continue
        for run_dir in root.iterdir():
            if not run_dir.is_dir():
                continue
            try:
                config = json.loads(
                    (run_dir / "resolved_config.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError, TypeError):
                continue
            condition = semantic_condition(config)
            seed = config.get("seed")
            if condition is not None and isinstance(seed, int) and seed > 0:
                used[condition].add(seed)
    return used


def all_used_seeds(
    config_paths: Iterable[Path], result_roots: Iterable[Path]
) -> dict[Condition, set[int]]:
    """Combine configured and attempted seeds for fresh allocation."""
    combined = configured_seeds(config_paths)
    for condition, seeds in attempted_seeds(result_roots).items():
        combined[condition].update(seeds)
    return combined


def matched_and_missing(
    templates: dict[Condition, dict[str, Any]],
    completed: dict[Condition, dict[tuple[int, int, str], set[str]]],
    target_groups: int,
) -> tuple[int, int]:
    """Count commit-consistent matched groups and target deficits."""
    matched_total = 0
    missing_total = 0
    for condition in templates:
        matched = sum(
            policies == POLICIES
            for policies in completed.get(condition, {}).values()
        )
        matched_total += min(matched, target_groups)
        missing_total += max(0, target_groups - matched)
    return matched_total, missing_total


def round_document(
    templates: dict[Condition, dict[str, Any]],
    plan: dict[Condition, list[int]],
    round_number: int,
) -> dict[str, Any]:
    """Build one round with both fixed-link policies for every fresh seed."""
    policies: list[dict[str, Any]] = []
    for condition in sorted(plan):
        for policy in sorted(POLICIES):
            policies.append(
                {
                    "name": policy,
                    "seeds": list(plan[condition]),
                    "config": copy.deepcopy(templates[condition]),
                }
            )
    return {
        "extends": ORIGINAL_CONFIG.name,
        "name": f"prediction-stage-a-repair-v1-round{round_number}",
        "output_root": f"results/{REPAIR_PREFIX}_round{round_number}",
        "workers": 10,
        "policies": policies,
    }


def write_round_config(
    path: Path,
    templates: dict[Condition, dict[str, Any]],
    plan: dict[Condition, list[int]],
    round_number: int,
) -> int:
    """Atomically write and verify a duplicate-free repair matrix."""
    document = round_document(templates, plan, round_number)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, encoding="utf-8"
    ) as output:
        yaml.safe_dump(document, output, sort_keys=False)
        temporary = Path(output.name)
    os.replace(temporary, path)

    specs = expand_config(load_yaml(path))
    identities = {
        (
            json.dumps(spec["config"], sort_keys=True, separators=(",", ":")),
            spec["seed"],
            spec["run"],
        )
        for spec in specs
    }
    expected = 2 * sum(len(seeds) for seeds in plan.values())
    if len(identities) != len(specs):
        raise ValueError(f"{path}: duplicate expanded specs")
    if len(specs) != expected:
        raise ValueError(f"{path}: generated {len(specs)} runs, expected {expected}")
    return len(specs)


def round_details(
    config_path: Path, results_dir: Path = ROOT / "results"
) -> tuple[int, Path, int]:
    """Return round number, absolute result root, and expanded run count."""
    match = ROUND_PATTERN.fullmatch(config_path.name)
    if match is None:
        raise ValueError(f"invalid repair round config name: {config_path}")
    document = load_yaml(config_path)
    output_root = Path(document["output_root"])
    if not output_root.is_absolute():
        output_root = results_dir / output_root.name
    return int(match.group(1)), output_root, len(expand_config(document))


def pending_round(
    config_paths: Iterable[Path], results_dir: Path = ROOT / "results"
) -> tuple[int, Path, Path, int] | None:
    """Find the earliest generated round without a completion marker."""
    pending: list[tuple[int, Path, Path, int]] = []
    for path in config_paths:
        if ROUND_PATTERN.fullmatch(path.name) is None:
            continue
        number, result_root, run_count = round_details(path, results_dir)
        if not (result_root / ROUND_MARKER).is_file():
            pending.append((number, path, result_root, run_count))
    return min(pending, default=None, key=lambda item: item[0])


def next_round_number(config_paths: Iterable[Path]) -> int:
    """Allocate a distinct round number after every existing repair matrix."""
    numbers = [
        int(match.group(1))
        for path in config_paths
        if (match := ROUND_PATTERN.fullmatch(path.name)) is not None
    ]
    return max(numbers, default=1) + 1


def write_marker(path: Path, value: dict[str, Any]) -> None:
    """Atomically record that a launched round returned."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, delete=False, encoding="utf-8"
    ) as output:
        json.dump(value, output, sort_keys=True)
        output.write("\n")
        temporary = Path(output.name)
    os.replace(temporary, path)


def cleanup_repair_roots(roots: Iterable[Path]) -> int:
    """Remove only direct hidden attempt directories from repair roots."""
    return sum(
        cleanup_failed_attempts(root)
        for root in roots
        if root.name.startswith(REPAIR_PREFIX)
    )


def completed_rounds_and_streak(
    config_paths: Iterable[Path], results_dir: Path
) -> tuple[int, int]:
    """Count marked rounds and their trailing no-progress streak."""
    records: list[tuple[int, dict[str, Any]]] = []
    for config_path in config_paths:
        if ROUND_PATTERN.fullmatch(config_path.name) is None:
            continue
        number, result_root, _ = round_details(config_path, results_dir)
        try:
            record = json.loads(
                (result_root / ROUND_MARKER).read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError):
            continue
        records.append((number, record))
    streak = 0
    for _, record in sorted(records, reverse=True):
        if record.get("matched_after", 0) > record.get("matched_before", 0):
            break
        streak += 1
    return len(records), streak


def default_runner(config_path: Path) -> int:
    """Launch one matrix without rebuilding or running analysis."""
    command = [
        sys.executable,
        str(ROOT / "tools/run_experiments.py"),
        str(config_path),
        "--no-build",
        "--no-analysis",
        "--resume",
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def run_repair_loop(
    *,
    config_dir: Path = CONFIG_DIR,
    results_dir: Path = ROOT / "results",
    target_groups: int = TARGET_GROUPS,
    max_rounds: int = MAX_ROUNDS,
    max_no_progress_rounds: int = MAX_NO_PROGRESS_ROUNDS,
    runner: Callable[[Path], int] = default_runner,
    validator: Callable[[Path], dict[str, Any]] | None = None,
) -> int:
    """Run repair rounds and return zero only when all targets are satisfied."""
    original_config = config_dir / ORIGINAL_CONFIG.name
    templates = condition_templates(original_config)
    if len(templates) != 16:
        raise ValueError(f"expected 16 Stage-A conditions, found {len(templates)}")

    configs = repair_config_paths(config_dir)
    completed_rounds, no_progress = completed_rounds_and_streak(
        configs, results_dir
    )
    while True:
        configs = repair_config_paths(config_dir)
        roots = repair_result_roots(results_dir)
        collector_args: dict[str, Any] = {}
        if validator is not None:
            collector_args["validator"] = validator
        completed = completed_policy_seeds(roots, **collector_args)
        matched, missing = matched_and_missing(templates, completed, target_groups)
        emit(
            "PROGRESS",
            round=completed_rounds,
            matched=matched,
            missing=missing,
        )
        if missing == 0:
            emit(
                "FINAL",
                status="success",
                matched=matched,
                missing=0,
                rounds=completed_rounds,
            )
            return 0
        if no_progress >= max_no_progress_rounds:
            emit(
                "FINAL",
                status="no_progress",
                matched=matched,
                missing=missing,
                rounds=completed_rounds,
            )
            return 3
        if completed_rounds >= max_rounds:
            emit(
                "FINAL",
                status="exhausted",
                matched=matched,
                missing=missing,
                rounds=completed_rounds,
            )
            return 2

        pending = pending_round(configs, results_dir)
        if pending is None:
            number = next_round_number(configs)
            used = all_used_seeds(configs, roots)
            plan, planned_missing = plan_fresh_seeds(
                templates,
                completed,
                used,
                target_groups=target_groups,
            )
            if planned_missing != missing or not plan:
                raise RuntimeError("repair plan does not match the observed deficit")
            config_path = config_dir / f"{REPAIR_PREFIX}_round{number}.yaml"
            result_root = results_dir / f"{REPAIR_PREFIX}_round{number}"
            run_count = write_round_config(config_path, templates, plan, number)
        else:
            number, config_path, result_root, run_count = pending

        cleanup_count = cleanup_repair_roots(roots + [result_root])
        emit("ROUND_START", round=number, matched=matched, missing=missing)
        emit("ROUND_CLEANUP", round=number, count=cleanup_count)
        emit("ROUND_GENERATED", round=number, runs=run_count)
        return_code = runner(config_path)
        completed_rounds += 1

        refreshed_roots = repair_result_roots(results_dir)
        refreshed = completed_policy_seeds(refreshed_roots, **collector_args)
        new_matched, new_missing = matched_and_missing(
            templates, refreshed, target_groups
        )
        write_marker(
            result_root / ROUND_MARKER,
            {
                "round": number,
                "runner_return_code": return_code,
                "matched_before": matched,
                "matched_after": new_matched,
                "missing_after": new_missing,
            },
        )
        emit(
            "ROUND_FINISH",
            round=number,
            exit_code=return_code,
            matched=new_matched,
            missing=new_missing,
        )

        no_progress = 0 if new_matched > matched else no_progress + 1
        if new_missing == 0:
            emit(
                "FINAL",
                status="success",
                matched=new_matched,
                missing=0,
                rounds=completed_rounds,
            )
            return 0
        if no_progress >= max_no_progress_rounds:
            emit(
                "FINAL",
                status="no_progress",
                matched=new_matched,
                missing=new_missing,
                rounds=completed_rounds,
            )
            return 3


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-groups", type=int, default=TARGET_GROUPS)
    parser.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    parser.add_argument(
        "--max-no-progress-rounds",
        type=int,
        default=MAX_NO_PROGRESS_ROUNDS,
    )
    args = parser.parse_args()
    for name in ("target_groups", "max_rounds", "max_no_progress_rounds"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be positive")
    raise SystemExit(
        run_repair_loop(
            target_groups=args.target_groups,
            max_rounds=args.max_rounds,
            max_no_progress_rounds=args.max_no_progress_rounds,
        )
    )


if __name__ == "__main__":
    main()
