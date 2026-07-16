#!/usr/bin/env python3
"""Expand a YAML experiment matrix and run validated ns-3 jobs."""

from __future__ import annotations

import argparse
import copy
import hashlib
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

import yaml

from plot_results import plot
from summarize_runs import discover, summarize, write_outputs
from validate_outputs import validate_run

ROOT = Path(__file__).resolve().parents[1]
NS3_UPSTREAM_COMMIT = "d2add90b452d600cfb4859baed8e9ea633519447"
CLI_KEYS = {
    "duration": "duration", "fps": "fps", "frame_size": "frameSize",
    "payload_size": "payloadSize", "deadline_us": "deadlineUs",
    "fixed_rss_dbm": "fixedRssDbm", "station_distance_m": "stationDistanceM",
    "propagation_model": "propagationModel",
    "path_loss_exponent": "pathLossExponent",
    "reference_loss_2_4_ghz_db": "referenceLoss2GhzDb",
    "reference_loss_5_ghz_db": "referenceLoss5GhzDb",
    "nakagami_distance_1_m": "nakagamiDistance1M",
    "nakagami_distance_2_m": "nakagamiDistance2M",
    "nakagami_m0": "nakagamiM0", "nakagami_m1": "nakagamiM1",
    "nakagami_m2": "nakagamiM2",
    "propagation_stream_base": "propagationStreamBase",
    "emission_mode": "emissionMode",
    "source": "source", "trace_file": "traceFile", "wifi_standard": "wifiStandard",
    "queue_max_packets": "queueMaxPackets", "queue_max_delay_ms": "queueMaxDelayMs",
    "max_ampdu_size": "maxAmpduSize", "max_amsdu_size": "maxAmsduSize",
    "frame_retry_limit": "frameRetryLimit", "txop_limit_us": "txopLimitUs",
    "rts_cts_threshold": "rtsCtsThreshold",
    "fragmentation_threshold": "fragmentationThreshold",
    "guard_interval_ns": "guardIntervalNs", "static_link_0_score": "staticLink0Score",
    "static_link_1_score": "staticLink1Score", "background_traffic": "backgroundTraffic",
    "background_profile": "backgroundProfile",
    "background_direction": "backgroundDirection",
    "background_stations_0": "backgroundStations0",
    "background_stations_1": "backgroundStations1",
    "background_rate_mbps": "backgroundRateMbps",
    "background_packet_size": "backgroundPacketSize",
    "background_standard_0": "backgroundStandard0",
    "background_standard_1": "backgroundStandard1",
    "correlation_mode": "correlationMode", "correlation_trace": "correlationTrace",
    "background_stream_base": "backgroundStreamBase",
    "common_on_mean_ms": "commonOnMeanMs", "common_off_mean_ms": "commonOffMeanMs",
    "local_on_mean_ms": "localOnMeanMs", "local_off_mean_ms": "localOffMeanMs",
    "common_on_duration_ms": "commonOnDurationMs",
    "common_off_duration_ms": "commonOffDurationMs",
    "local_on_duration_ms": "localOnDurationMs",
    "local_off_duration_ms": "localOffDurationMs",
    "random_on_mean_ms": "randomOnMeanMs",
    "random_off_mean_ms": "randomOffMeanMs",
    "obss_profile": "obssProfile",
    "obss_stations_per_bss": "obssStationsPerBss",
    "obss_ul_min_rate_mbps": "obssUlMinRateMbps",
    "obss_ul_max_rate_mbps": "obssUlMaxRateMbps",
    "obss_dl_min_rate_mbps": "obssDlMinRateMbps",
    "obss_dl_max_rate_mbps": "obssDlMaxRateMbps",
    "obss_on_mean_ms": "obssOnMeanMs",
    "obss_off_mean_ms": "obssOffMeanMs",
    "obss_station_manager": "obssStationManager",
    "obss_manager_update_ms": "obssManagerUpdateMs",
    "obss_packet_size": "obssPacketSize",
    "obss_area_min_x_m": "obssAreaMinXM",
    "obss_area_max_x_m": "obssAreaMaxXM",
    "obss_area_min_y_m": "obssAreaMinYM",
    "obss_area_max_y_m": "obssAreaMaxYM",
    "obss_sta_min_distance_m": "obssStaMinDistanceM",
    "obss_sta_max_distance_m": "obssStaMaxDistanceM",
    "obss_placement_stream_base": "obssPlacementStreamBase",
    "obss_application_stream_base": "obssApplicationStreamBase",
    "obss_wifi_stream_base": "obssWifiStreamBase",
}


def project_commit(root: Path = ROOT) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def derive_run_id(resolved: dict[str, Any], seed: int, run: int,
                  ns3_commit: str, project_git_commit: str) -> str:
    identity = {
        "config": resolved, "seed": seed, "run": run,
        "ns3_commit": ns3_commit, "project_commit": project_git_commit,
    }
    return hashlib.sha256(canonical_json(identity).encode()).hexdigest()[:20]


def set_dotted(target: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    if not all(parts):
        raise ValueError(f"invalid sweep key: {dotted!r}")
    node = target
    for part in parts[:-1]:
        existing = node.setdefault(part, {})
        if not isinstance(existing, dict):
            raise ValueError(f"sweep key traverses scalar: {dotted}")
        node = existing
    node[parts[-1]] = value


def _entries(values: Any, kind: str) -> list[dict[str, Any]]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{kind} must be a nonempty list")
    result = []
    for item in values:
        entry = {"name": item} if isinstance(item, str) else copy.deepcopy(item)
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ValueError(f"invalid {kind} entry: {item!r}")
        result.append(entry)
    return result


def expand_config(document: dict[str, Any]) -> list[dict[str, Any]]:
    base = document.get("base", {})
    if not isinstance(base, dict):
        raise ValueError("base must be a mapping")
    seeds = document.get("seeds", [1])
    runs = document.get("runs", [1])
    if not all(isinstance(value, int) and value > 0 for value in [*seeds, *runs]):
        raise ValueError("seeds and runs must contain positive integers")
    topologies = _entries(document.get("topologies", ["single_link"]), "topologies")
    policies = _entries(document.get("policies", ["fixed_link_0"]), "policies")
    sweep = document.get("sweep", {})
    if not isinstance(sweep, dict) or not all(isinstance(v, list) and v for v in sweep.values()):
        raise ValueError("sweep must map dotted keys to nonempty lists")
    sweep_keys = sorted(sweep)
    sweep_products: Iterable[tuple[Any, ...]] = itertools.product(
        *(sweep[key] for key in sweep_keys)
    ) if sweep_keys else [()]
    expanded = []
    for topology, policy, seed, run, values in itertools.product(
        topologies, policies, seeds, runs, list(sweep_products)
    ):
        compatible = policy.get("topologies")
        if compatible is not None and topology["name"] not in compatible:
            continue
        compatible_policies = topology.get("policies")
        if compatible_policies is not None and policy["name"] not in compatible_policies:
            continue
        resolved = copy.deepcopy(base)
        resolved["topology"] = topology["name"]
        resolved["policy"] = policy["name"]
        for overlay in (topology.get("config", {}), policy.get("config", {})):
            if not isinstance(overlay, dict):
                raise ValueError("topology/policy config must be a mapping")
            for key, value in overlay.items():
                set_dotted(resolved, key, value)
        for key, value in zip(sweep_keys, values):
            set_dotted(resolved, key, value)
        expanded.append({"config": resolved, "seed": seed, "run": run})
    if not expanded:
        raise ValueError("matrix expansion produced no compatible runs")
    return expanded


def _flatten(value: dict[str, Any], prefix: str = "") -> Iterable[tuple[str, Any]]:
    for key in sorted(value):
        dotted = f"{prefix}.{key}" if prefix else key
        if isinstance(value[key], dict):
            yield from _flatten(value[key], dotted)
        else:
            yield dotted, value[key]


def cli_arguments(config: dict[str, Any], config_dir: Path) -> list[str]:
    arguments = []
    for dotted, value in _flatten(config):
        if dotted in {"topology", "policy"}:
            cli_key = dotted
        else:
            leaf = dotted.rsplit(".", 1)[-1]
            if leaf not in CLI_KEYS:
                raise ValueError(f"no C++ CLI translation for {dotted}")
            cli_key = CLI_KEYS[leaf]
        if cli_key in {"traceFile", "correlationTrace"} and value:
            path = Path(str(value))
            value = str((config_dir / path).resolve()) if not path.is_absolute() else str(path)
        if isinstance(value, bool):
            value = "1" if value else "0"
        arguments.append(f"--{cli_key}={value}")
    return arguments


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")
        temporary = Path(output.name)
    os.replace(temporary, path)


def load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("experiment YAML root must be a mapping")
    return value


def run_one(spec: dict[str, Any], output_root: Path, config_dir: Path,
            project_git_commit: str) -> dict[str, Any]:
    run_id = spec["run_id"]
    final = output_root / run_id
    if final.exists():
        validate_run(final, run_id, project_git_commit, NS3_UPSTREAM_COMMIT)
        raise FileExistsError(f"completed duplicate rejected: {run_id}")
    for stale in output_root.glob(f".{run_id}.attempt-*"):
        shutil.rmtree(stale)
    attempt = output_root / f".{run_id}.attempt-{os.getpid()}"
    log_path = output_root / f".{run_id}.stdout-{os.getpid()}.tmp"
    arguments = cli_arguments(spec["config"], config_dir)
    arguments += [
        f"--seed={spec['seed']}", f"--run={spec['run']}", f"--runId={run_id}",
        f"--outputDir={attempt}", f"--projectGitCommit={project_git_commit}",
    ]
    command = [str(ROOT / "ns3"), "run", "streaming-experiment", "--no-build", "--", *arguments]
    try:
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.run(command, cwd=ROOT, stdout=log,
                                     stderr=subprocess.STDOUT, text=True)
        if attempt.is_dir():
            shutil.move(str(log_path), attempt / "stdout.log")
        if process.returncode:
            raise RuntimeError(f"run {run_id} failed ({process.returncode}); see {attempt}/stdout.log")
        validate_run(attempt, run_id, project_git_commit, NS3_UPSTREAM_COMMIT)
        os.replace(attempt, final)
    finally:
        if log_path.exists():
            log_path.unlink()
    return {
        "run_id": run_id, "status": "complete", "seed": spec["seed"], "run": spec["run"],
        "directory": str(final.relative_to(output_root)), "config": spec["config"],
        "command": command,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--no-analysis", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="keep validated completed runs and execute missing runs")
    args = parser.parse_args()
    document = load_yaml(args.config.resolve())
    workers = args.workers or int(document.get("workers", 1))
    if workers < 1:
        parser.error("workers must be positive")
    output_root = (args.output_root or Path(document.get("output_root", "results"))).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    commit = project_commit()
    specs = expand_config(document)
    seen = set()
    for spec in specs:
        spec["run_id"] = derive_run_id(spec["config"], spec["seed"], spec["run"],
                                       NS3_UPSTREAM_COMMIT, commit)
        if spec["run_id"] in seen:
            raise ValueError(f"duplicate resolved run in matrix: {spec['run_id']}")
        seen.add(spec["run_id"])
        completed = output_root / spec["run_id"]
        if completed.exists():
            validate_run(completed, spec["run_id"], commit, NS3_UPSTREAM_COMMIT)
            if not args.resume:
                raise FileExistsError(f"completed duplicate rejected: {spec['run_id']}")
            spec["completed"] = True
    if not args.no_build:
        subprocess.run([str(ROOT / "ns3"), "build", "streaming-experiment"],
                       cwd=ROOT, check=True)
    manifest = {
        "schema_version": 1, "experiment": document.get("name", args.config.stem),
        "config_file": str(args.config.resolve()), "project_commit": commit,
        "ns3_upstream_commit": NS3_UPSTREAM_COMMIT,
        "runs": [
            {
                "run_id": spec["run_id"],
                "status": "complete",
                "seed": spec["seed"],
                "run": spec["run"],
                "directory": spec["run_id"],
                "config": spec["config"],
                "command": None,
            }
            for spec in specs
            if spec.get("completed")
        ],
    }
    manifest_path = output_root / "experiment_manifest.json"
    atomic_json(manifest_path, manifest)
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(run_one, spec, output_root, args.config.resolve().parent, commit): spec
            for spec in specs
            if not spec.get("completed")
        }
        for future in as_completed(futures):
            try:
                result = future.result()
                manifest["runs"].append(result)
                manifest["runs"].sort(key=lambda item: item["run_id"])
                atomic_json(manifest_path, manifest)
                print(f"COMPLETE {result['run_id']}")
            except Exception as error:
                failures.append(str(error))
                print(f"FAILED {futures[future]['run_id']}: {error}", file=sys.stderr)
    if failures:
        raise SystemExit("\n".join(failures))
    if not args.no_analysis:
        aggregate = summarize(discover(output_root))
        aggregate_json = output_root / "aggregate.json"
        aggregate_csv = output_root / "aggregate.csv"
        write_outputs(aggregate, aggregate_json, aggregate_csv)
        plot(aggregate, output_root / "plots")
        print(f"ANALYSIS {aggregate_json} {aggregate_csv} {output_root / 'plots'}")
    print(f"MANIFEST {manifest_path}")


if __name__ == "__main__":
    main()
