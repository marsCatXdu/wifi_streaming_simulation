#!/usr/bin/env python3
"""Recover one validated attempt and run the seven interrupted matrix entries."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SIMULATION_COMMIT = "47e19962420bb7623784bc91b0c0d40fbf462b35"
VALIDATOR_COMMIT = "5ca913ab40f8d6fa06188d80a86b7489f221eb05"
NS3_COMMIT = "d2add90b452d600cfb4859baed8e9ea633519447"
PROMOTED_RUN_ID = "88b7da31f843daf76e77"
EXPECTED_ORIGINAL_MISSING_RUN_IDS = {
    "475d547fb22c4f5f560d",
    "496b758bc693619e8105",
    "65d1ce87c6a341742545",
    "74f06c3d90823411be02",
    PROMOTED_RUN_ID,
    "e99686e4fff2028e0d68",
    "ecde3c40d1f8cf58fc83",
    "edeeb26b753ca8b5c4fd",
}
FAILURE_EVIDENCE_ARCHIVE_SHA256 = (
    "bb4c067ada47afc3ee2067f1a2fc70179ebfb7cd1231d8720e3aed5300ba9c00"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root).as_posix()
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"  ")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def git_commit(root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()


def git_tracked_status(root: Path) -> str:
    return subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root,
        text=True,
    ).strip()


def load_frozen_runner(frozen_checkout: Path, validator_checkout: Path) -> tuple[Any, Any]:
    validation_tools = validator_checkout / "tools"
    frozen_tools = frozen_checkout / "tools"
    sys.path.insert(0, str(validation_tools))
    import validate_outputs as validator

    sys.modules["validate_outputs"] = validator
    sys.path.insert(0, str(frozen_tools))
    runner_path = frozen_tools / "run_experiments.py"
    spec = importlib.util.spec_from_file_location("frozen_run_experiments", runner_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load frozen runner: {runner_path}")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    if runner.validate_run is not validator.validate_run:
        raise RuntimeError("frozen runner did not bind the corrected validator")
    # The validator verifies the absolute origins of its feature-building
    # modules.  Restore its committed tools directory ahead of the frozen
    # runner directory before any lazy replay imports occur.
    sys.path.remove(str(validation_tools))
    sys.path.insert(0, str(validation_tools))
    return runner, validator


def manifest_item(
    runner: Any, spec: dict[str, Any], config_dir: Path, output_dir: Path
) -> dict[str, Any]:
    arguments = runner.cli_arguments(spec["config"], config_dir)
    command = [
        str(runner.ROOT / "ns3"),
        "run",
        "streaming-experiment",
        "--no-build",
        "--",
        *arguments,
        f"--seed={spec['seed']}",
        f"--run={spec['run']}",
        f"--runId={spec['run_id']}",
        f"--outputDir={output_dir}",
        f"--projectGitCommit={SIMULATION_COMMIT}",
    ]
    item = {
        "run_id": spec["run_id"],
        "status": "complete",
        "seed": spec["seed"],
        "run": spec["run"],
        "directory": spec["run_id"],
        "config": copy.deepcopy(spec["config"]),
        "command": command,
    }
    if "scenario" in spec:
        item["scenario"] = copy.deepcopy(spec["scenario"])
    return item


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-checkout", required=True, type=Path)
    parser.add_argument("--validator-checkout", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--promoted-attempt", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=7)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()

    frozen_checkout = args.frozen_checkout.resolve()
    validator_checkout = args.validator_checkout.resolve()
    config_path = args.config.resolve()
    run_root = args.run_root.resolve()
    attempt = args.promoted_attempt.resolve()
    if git_commit(frozen_checkout) != SIMULATION_COMMIT:
        raise RuntimeError("frozen simulation checkout commit differs")
    if git_commit(validator_checkout) != VALIDATOR_COMMIT:
        raise RuntimeError("validator checkout commit differs")
    if git_tracked_status(frozen_checkout):
        raise RuntimeError("frozen simulation checkout has tracked changes")
    if git_tracked_status(validator_checkout):
        raise RuntimeError("validator checkout has tracked changes")

    runner, validator = load_frozen_runner(frozen_checkout, validator_checkout)
    if runner.project_commit() != SIMULATION_COMMIT:
        raise RuntimeError("frozen runner project identity differs")
    if runner.NS3_UPSTREAM_COMMIT != NS3_COMMIT:
        raise RuntimeError("frozen runner ns-3 identity differs")

    document = runner.load_yaml(config_path)
    runtime_contract = runner.validate_runtime_contract(document)
    specs = runner.expand_config(document)
    spec_by_id: dict[str, dict[str, Any]] = {}
    for spec in specs:
        run_id = runner.derive_run_id(
            spec["config"],
            spec["seed"],
            spec["run"],
            NS3_COMMIT,
            SIMULATION_COMMIT,
            runtime_contract,
            spec.get("scenario"),
        )
        spec["run_id"] = run_id
        if run_id in spec_by_id:
            raise RuntimeError(f"duplicate matrix run ID: {run_id}")
        spec_by_id[run_id] = spec
    expected_ids = set(spec_by_id)
    if len(expected_ids) != 576:
        raise RuntimeError(f"expected 576 matrix entries, found {len(expected_ids)}")

    manifest_path = run_root / "experiment_manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest_before_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = json.loads(manifest_bytes)
    runner.validate_existing_manifest(
        manifest_path,
        str(document.get("name", config_path.stem)),
        runner.matrix_sha256(document),
        SIMULATION_COMMIT,
        expected_ids,
        runtime_contract,
    )
    recorded_ids = {item["run_id"] for item in manifest["runs"]}
    canonical_ids = {run_id for run_id in expected_ids if (run_root / run_id).is_dir()}
    if recorded_ids != canonical_ids:
        raise RuntimeError("manifest records and canonical directories differ before recovery")
    missing_ids = expected_ids - canonical_ids
    if not missing_ids <= EXPECTED_ORIGINAL_MISSING_RUN_IDS:
        raise RuntimeError(f"unexpected missing run IDs: {sorted(missing_ids)}")
    if args.preflight_only:
        if missing_ids != EXPECTED_ORIGINAL_MISSING_RUN_IDS:
            raise RuntimeError(
                f"preflight expected the original eight missing IDs, found {sorted(missing_ids)}"
            )
        validator.validate_run(
            attempt,
            expected_project_commit=SIMULATION_COMMIT,
            expected_ns3_commit=NS3_COMMIT,
        )
        print(
            json.dumps(
                {
                    "status": "preflight_passed",
                    "manifest_sha256": manifest_before_sha256,
                    "canonical_run_count": len(canonical_ids),
                    "missing_runs": [
                        {
                            "run_id": run_id,
                            "seed": spec_by_id[run_id]["seed"],
                            "policy": spec_by_id[run_id]["config"]["policy"],
                            "scenario_id": spec_by_id[run_id]["scenario"][
                                "scenario_id"
                            ],
                            "completed_attempt": run_id == PROMOTED_RUN_ID,
                        }
                        for run_id in sorted(missing_ids)
                    ],
                },
                indent=2,
                sort_keys=True,
            ),
            flush=True,
        )
        return

    backup_name = f"experiment_manifest.{len(recorded_ids)}-runs.{manifest_before_sha256[:12]}.json"
    backup_path = run_root / backup_name
    if not backup_path.exists():
        shutil.copy2(manifest_path, backup_path)

    promoted_attempt_sha256: str | None = None
    promoted_now = False
    final_promoted = run_root / PROMOTED_RUN_ID
    if PROMOTED_RUN_ID in missing_ids:
        if attempt.name.split(".attempt-", 1)[0] != f".{PROMOTED_RUN_ID}":
            raise RuntimeError("promoted attempt path does not match the frozen run ID")
        if not attempt.is_dir() or final_promoted.exists():
            raise RuntimeError("promoted attempt/final directory state differs")
        promoted_attempt_sha256 = sha256_tree(attempt)
        validator.validate_run(
            attempt,
            expected_project_commit=SIMULATION_COMMIT,
            expected_ns3_commit=NS3_COMMIT,
        )
        os.replace(attempt, final_promoted)
        manifest["runs"].append(
            manifest_item(
                runner,
                spec_by_id[PROMOTED_RUN_ID],
                config_path.parent,
                attempt,
            )
        )
        manifest["runs"].sort(key=lambda item: item["run_id"])
        runner.atomic_json(manifest_path, manifest)
        promoted_now = True
        print(f"PROMOTED {PROMOTED_RUN_ID}", flush=True)
    elif PROMOTED_RUN_ID in canonical_ids:
        validator.validate_run(
            final_promoted,
            expected_project_commit=SIMULATION_COMMIT,
            expected_ns3_commit=NS3_COMMIT,
        )
    else:
        raise RuntimeError("promoted run is neither missing nor canonical")

    canonical_ids = {run_id for run_id in expected_ids if (run_root / run_id).is_dir()}
    remaining_ids = sorted(expected_ids - canonical_ids)
    if not set(remaining_ids) <= EXPECTED_ORIGINAL_MISSING_RUN_IDS - {PROMOTED_RUN_ID}:
        raise RuntimeError(f"unexpected remaining run IDs: {remaining_ids}")

    completed_now: list[str] = []
    failures: list[str] = []
    if remaining_ids:
        worker_count = min(args.workers, len(remaining_ids))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    runner.run_one,
                    spec_by_id[run_id],
                    run_root,
                    config_path.parent,
                    SIMULATION_COMMIT,
                ): run_id
                for run_id in remaining_ids
            }
            for future in as_completed(futures):
                run_id = futures[future]
                try:
                    result = future.result()
                    manifest["runs"].append(result)
                    manifest["runs"].sort(key=lambda item: item["run_id"])
                    runner.atomic_json(manifest_path, manifest)
                    completed_now.append(run_id)
                    print(f"COMPLETE {run_id}", flush=True)
                except Exception as error:
                    failures.append(f"{run_id}: {error}")
                    print(f"FAILED {run_id}: {error}", file=sys.stderr, flush=True)

    final_canonical_ids = {
        run_id for run_id in expected_ids if (run_root / run_id).is_dir()
    }
    final_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    final_recorded_ids = {item["run_id"] for item in final_manifest["runs"]}
    closed = final_canonical_ids == expected_ids and final_recorded_ids == expected_ids
    recovery = {
        "schema_version": 1,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "status": "complete" if not failures and closed else "failed",
        "simulation_project_commit": SIMULATION_COMMIT,
        "validator_commit": VALIDATOR_COMMIT,
        "ns3_upstream_commit": NS3_COMMIT,
        "original_missing_run_ids": sorted(EXPECTED_ORIGINAL_MISSING_RUN_IDS),
        "promoted_run_ids": [PROMOTED_RUN_ID] if promoted_now else [],
        "promoted_attempt_tree_sha256": promoted_attempt_sha256,
        "simulations_rerun": len(completed_now),
        "rerun_completed_run_ids": sorted(completed_now),
        "failures": failures,
        "completed_run_count": len(final_canonical_ids),
        "manifest_run_count": len(final_recorded_ids),
        "manifest_backup": backup_name,
        "manifest_before_sha256": manifest_before_sha256,
        "manifest_after_sha256": sha256_file(manifest_path),
        "failure_evidence_archive_sha256": FAILURE_EVIDENCE_ARCHIVE_SHA256,
        "recovery_script_sha256": sha256_file(Path(__file__).resolve()),
    }
    runner.atomic_json(run_root / "attempt_recovery_5ca913a.json", recovery)
    if failures:
        raise SystemExit("\n".join(failures))
    if not closed:
        raise RuntimeError("recovery finished without exact 576-run closure")
    print(
        f"CLOSED runs={len(expected_ids)} manifest={recovery['manifest_after_sha256']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
