#!/usr/bin/env python3
"""Export the frozen two-head T4 artifact as deterministic plain model data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from export_prediction_models_v1 import predictor_data
from prediction.primary_tail import read_primary_tail_bundle
from train_primary_risk_t0 import (
    physical_feature_names,
    predictor_fingerprint,
    provenance_sha256,
    sha256_file,
)

EXPORT_SCHEMA_VERSION = 2
HEAD_NAMES = ("primary_miss", "completed_tail")


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: JSON root must be an object")
    return value


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode()


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as output:
        output.write(value)
        temporary = Path(output.name)
    os.replace(temporary, path)


def validate_source_manifest(
    bundle_path: Path, manifest: dict[str, Any]
) -> tuple[Any, str]:
    """Validate the bundle and its complete training-manifest identity."""
    digest = sha256_file(bundle_path)
    if manifest.get("model_sha256") != digest:
        raise ValueError("primary-tail bundle checksum differs from its manifest")
    bundle = read_primary_tail_bundle(bundle_path)
    if (
        manifest.get("model_id") != bundle.model_id
        or manifest.get("artifact_id") != bundle.artifact_id
        or manifest.get("evidence_status") != bundle.evidence_status
    ):
        raise ValueError("primary-tail bundle identity differs from its manifest")
    provenance = manifest.get("target_provenance")
    if (
        not isinstance(provenance, dict)
        or manifest.get("target_provenance_sha256") != provenance_sha256(provenance)
    ):
        raise ValueError("primary-tail target provenance checksum is invalid")
    for name in HEAD_NAMES:
        declared = manifest.get("heads", {}).get(name, {})
        if (
            declared.get("target_id") != bundle.target_ids[name]
            or declared.get("serialized_ranker_sha256")
            != predictor_fingerprint(bundle.heads[name].pipeline)
            or declared.get("serialized_frozen_predictor_sha256")
            != predictor_fingerprint(bundle.heads[name])
        ):
            raise ValueError(f"primary-tail {name} identity differs from its manifest")
    return bundle, digest


def export_payload(bundle: Any, source_digest: str) -> dict[str, Any]:
    """Return stable language-neutral model data for a future C++ generator."""
    miss = bundle.heads["primary_miss"]
    physical = list(physical_feature_names(miss))
    result = {
        "primary_tail_export_schema_version": EXPORT_SCHEMA_VERSION,
        "source_model_sha256": source_digest,
        "dataset_sha256": bundle.dataset_sha256,
        "dataset_manifest_sha256": bundle.dataset_manifest_sha256,
        "dataset_validation_sha256": bundle.dataset_validation_sha256,
        "artifact_id": bundle.artifact_id,
        "model_id": bundle.model_id,
        "evidence_status": bundle.evidence_status,
        "pipeline_id": bundle.pipeline_id,
        "stage": bundle.stage,
        "primary_link": bundle.primary_link,
        "feature_names": list(miss.feature_names),
        "physical_feature_names": physical,
        "target_ids": bundle.target_ids,
        "tail_threshold_us": bundle.tail_threshold_us,
        "heads": {},
        "combiner": {
            "output_name": bundle.score_name,
            "score_kind": bundle.score_kind,
            "combiner": bundle.combiner,
            "primary_miss_weight": bundle.miss_weight,
            "completed_tail_weight": bundle.tail_weight,
            "normalization": bundle.score_normalization,
        },
    }
    for name in HEAD_NAMES:
        predictor = bundle.heads[name]
        if list(physical_feature_names(predictor)) != physical:
            raise ValueError("primary-tail export heads have different physical features")
        result["heads"][name] = predictor_data(
            predictor, expected_pipeline_id=bundle.pipeline_id
        )
    return result


def export(args: argparse.Namespace) -> dict[str, Any]:
    """Validate and atomically publish deterministic plain model data."""
    bundle_path = args.bundle.resolve()
    source_manifest = _json(args.manifest.resolve())
    bundle, source_digest = validate_source_manifest(bundle_path, source_manifest)
    payload = export_payload(bundle, source_digest)
    encoded = _canonical_bytes(payload)
    payload_digest = hashlib.sha256(encoded).hexdigest()
    output_path = args.output.resolve()
    export_manifest = {
        "primary_tail_export_schema_version": EXPORT_SCHEMA_VERSION,
        "model_id": bundle.model_id,
        "artifact_id": bundle.artifact_id,
        "source_model_sha256": source_digest,
        "target_provenance_sha256": source_manifest["target_provenance_sha256"],
        "export_file": output_path.name,
        "export_sha256": payload_digest,
        "feature_count": len(bundle.heads["primary_miss"].feature_names),
        "heads": list(HEAD_NAMES),
        "format": "canonical_json_plain_hgb_v1",
        "export_tool": "tools/export_primary_tail_t4_v1.py",
        "export_tool_sha256": sha256_file(Path(__file__).resolve()),
    }
    _atomic_bytes(output_path, encoded)
    _atomic_bytes(
        output_path.with_name(f"{output_path.stem}_manifest.json"),
        _canonical_bytes(export_manifest),
    )
    return export_manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    try:
        result = export(parse_args())
    except (OSError, ValueError) as error:
        raise SystemExit(f"error: {error}") from error
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
