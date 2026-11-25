"""Build the P4b-3B correctness registry from the externally pinned R1 archive."""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
from pathlib import Path, PurePosixPath
import sys
import tarfile
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts import replay_rapidflow_apx_p4b as replay
from scripts import run_rapidflow_apx_p4b as p4b

ARCHIVE_SHA = "22b5245d24d47b00fec6f3ab7b0960a6d6a27636759cc9c7c1259a343882cfe9"
ARCHIVE_SIZE = 34_057_918
MANIFEST_SHA = "cdd7ae9e7063649e8945a6e7a086d16e54410b43c313623f935b2bc9309a1898"
REPLAY_REV = "dce3cce8f7c2093caccdb83ee82e471f371f1507"
REGISTRY_PATH = p4b.REPO_ROOT / "configs/rapidflow_apx_p4b_correctness_registry.json"
CANDIDATE_REGISTRY_PATH = p4b.REPO_ROOT / "configs/rapidflow_apx_p4b_r2_1_candidate_registry.json"
CANDIDATE_ARCHIVE_SHA = "8efd7c65856c675773249d19b20165106d92917af56913be3730b5a2de044464"
CANDIDATE_ARCHIVE_SIZE = 33_681_113
CANDIDATE_MANIFEST_SHA = "37d649b697212de0e0779a1d33e1fdc990cee5f6473b8cf2b351c365cd0fda15"
CANDIDATE_REPLAY_REV = "115ce2844d91f6a6588fe0ac50b1179bc3b38012"
CANDIDATE_REGISTRY_SHA = "40a28b4d963d8c652bc49ab231aa2ccb6626e3de9b4badc3b3a13a03620fe0b4"
SCHEMA = "p4b-correctness-registry-v1"
ROOT = {"archive_sha256": ARCHIVE_SHA, "evidence_manifest_sha256": MANIFEST_SHA}
CANDIDATE_ROOT = {
    "bundle_id": "R2_CANDIDATE_BUNDLE",
    "archive_sha256": CANDIDATE_ARCHIVE_SHA,
    "evidence_manifest_sha256": CANDIDATE_MANIFEST_SHA,
    "archive_size_bytes": CANDIDATE_ARCHIVE_SIZE,
    "native_binary_sha256": p4b.NATIVE_SHA256,
    "native_source_commit": p4b.NATIVE_SOURCE_COMMIT,
    "historical_r1_evidence": ROOT,
    "superseded_r2_preview_registry_sha256": "90fc2f3ee1e8c4bcf210603caeb218190a294b168377858cd4be276ebb662118",
}
EXPECTED_COUNTS = {
    "M0_VERIFIED": 160, "DIFFERENTIAL_M2_VERIFIED": 64,
    "CORRECTNESS_NOT_FULLY_CERTIFIED": 16, "C3_BUDGET_UNAVAILABLE": 10,
}

TRUSTED_BUNDLES = {
    "HISTORICAL_R1_BUNDLE": {
        "bundle_id": "HISTORICAL_R1_BUNDLE",
        "registry_schema": SCHEMA,
        "registry_sha256": "a122454b3cdd5b00c221a3f3d847aea372c7e1f9889c4fb1314017c16d4e2117",
        "reference_archive_sha256": ARCHIVE_SHA,
        "reference_archive_size_bytes": ARCHIVE_SIZE,
        "reference_manifest_sha256": MANIFEST_SHA,
        "native_source_commit": p4b.HISTORICAL_R1_NATIVE_SOURCE_COMMIT,
        "native_binary_sha256": p4b.HISTORICAL_R1_NATIVE_SHA256,
        "native_binary_size_bytes": p4b.HISTORICAL_R1_NATIVE_SIZE_BYTES,
        "replay_revision": REPLAY_REV,
        "reference_prefix_layout": "pre-r09",
        "workload_manifest_sha256": "09c78158d541c22f91b48aa484e3fd5e494910c2885635de1fa79bbed1784869",
        "correctness_policy": "M0_AND_SAME_VARIANT_DIFFERENTIAL",
        "expected_member_count": 1811,
        "trusted_root": ROOT,
        "registry_path": REGISTRY_PATH,
    },
    "R2_CANDIDATE_BUNDLE": {
        "bundle_id": "R2_CANDIDATE_BUNDLE",
        "registry_schema": SCHEMA,
        "registry_sha256": CANDIDATE_REGISTRY_SHA,
        "reference_archive_sha256": CANDIDATE_ARCHIVE_SHA,
        "reference_archive_size_bytes": CANDIDATE_ARCHIVE_SIZE,
        "reference_manifest_sha256": CANDIDATE_MANIFEST_SHA,
        "native_source_commit": p4b.NATIVE_SOURCE_COMMIT,
        "native_binary_sha256": p4b.NATIVE_SHA256,
        "native_binary_size_bytes": p4b.NATIVE_SIZE_BYTES,
        "replay_revision": CANDIDATE_REPLAY_REV,
        "reference_prefix_layout": "r2-ref",
        "workload_manifest_sha256": "09c78158d541c22f91b48aa484e3fd5e494910c2885635de1fa79bbed1784869",
        "correctness_policy": "M0_AND_SAME_VARIANT_DIFFERENTIAL",
        "expected_member_count": 302,
        "trusted_root": CANDIDATE_ROOT,
        "registry_path": CANDIDATE_REGISTRY_PATH,
    },
}

BUNDLE_IDENTITY_FIELDS = (
    "bundle_id",
    "registry_schema",
    "registry_sha256",
    "reference_archive_sha256",
    "reference_archive_size_bytes",
    "reference_manifest_sha256",
    "native_source_commit",
    "native_binary_sha256",
    "native_binary_size_bytes",
    "replay_revision",
    "reference_prefix_layout",
    "workload_manifest_sha256",
    "correctness_policy",
)


def bundle_identity_payload(bundle_spec):
    if not isinstance(bundle_spec, dict):
        raise p4b.LedgerIntegrityError("INVALID_TRUST_BUNDLE_SHAPE")
    missing = [field for field in BUNDLE_IDENTITY_FIELDS if field not in bundle_spec]
    if missing:
        raise p4b.LedgerIntegrityError(f"MISSING_BUNDLE_IDENTITY_FIELD: {missing[0]}")
    payload = {}
    for field in BUNDLE_IDENTITY_FIELDS:
        val = bundle_spec[field]
        if type(val) not in (str, int):
            raise p4b.LedgerIntegrityError(f"INVALID_TRUST_BUNDLE_SHAPE: {field}")
        payload[field] = val
    return payload


def resolve_trusted_bundle(bundle=None, *, default_id="HISTORICAL_R1_BUNDLE"):
    if bundle is None:
        return TRUSTED_BUNDLES[default_id]
    if isinstance(bundle, str):
        if bundle not in TRUSTED_BUNDLES:
            raise p4b.LedgerIntegrityError(f"UNREVIEWED_TRUST_BUNDLE: {bundle}")
        return TRUSTED_BUNDLES[bundle]
    if not isinstance(bundle, dict):
        raise p4b.LedgerIntegrityError("INVALID_TRUST_BUNDLE_SHAPE")
    bundle_id = bundle.get("bundle_id")
    if not isinstance(bundle_id, str):
        raise p4b.LedgerIntegrityError("INVALID_TRUST_BUNDLE_SHAPE: bundle_id")
    if bundle_id not in TRUSTED_BUNDLES:
        raise p4b.LedgerIntegrityError(f"UNREVIEWED_TRUST_BUNDLE: {bundle_id}")
    expected = TRUSTED_BUNDLES[bundle_id]
    if p4b.canonical_json_bytes(bundle_identity_payload(bundle)) != p4b.canonical_json_bytes(bundle_identity_payload(expected)):
        raise p4b.LedgerIntegrityError(f"BUNDLE_IDENTITY_MISMATCH: {bundle_id}")
    return expected


def checked_member(name):
    path = PurePosixPath(name)
    if not name or name != str(path) or path.is_absolute() or ".." in path.parts or name == "." or "\\" in name:
        raise p4b.LedgerIntegrityError(f"UNSAFE_ARCHIVE_PATH: {name}")
    return path


def unpack_references(archive, store, *, bundle=None):
    """Verify every member and copy just replay artifacts, without tar extraction."""
    spec = resolve_trusted_bundle(bundle, default_id="HISTORICAL_R1_BUNDLE")
    prefix = spec["reference_prefix_layout"]
    is_hist = spec["bundle_id"] == "HISTORICAL_R1_BUNDLE"
    with archive.open("rb") as stream:
        if archive.stat().st_size != spec["reference_archive_size_bytes"] or p4b.sha256_bytes(stream.read()) != spec["reference_archive_sha256"]:
            raise p4b.LedgerIntegrityError("R1_ARCHIVE_IDENTITY_MISMATCH" if is_hist else "ARCHIVE_IDENTITY_MISMATCH")
        stream.seek(0)
        observed = {}
        members = None
        with tarfile.open(fileobj=stream, mode="r|gz") as tar:
            for member in tar:
                relative = checked_member(member.name)
                if not member.isfile() or member.name in observed or member.size > 128 * 1024**2:
                    raise p4b.LedgerIntegrityError(f"INVALID_ARCHIVE_MEMBER: {member.name}")
                raw = tar.extractfile(member).read()
                sha = p4b.sha256_bytes(raw)
                observed[member.name] = {"sha256": sha, "size_bytes": len(raw)}
                if member.name == "evidence-manifest.json":
                    if sha != spec["reference_manifest_sha256"]:
                        raise p4b.LedgerIntegrityError("R1_MANIFEST_HASH_MISMATCH" if is_hist else "MANIFEST_HASH_MISMATCH")
                    members = p4b.load_json_bytes(raw)["artifacts"]
                elif relative.parts[0] == prefix:
                    out = store / relative
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with out.open("xb") as file:
                        file.write(raw)
                elif not is_hist:
                    raise p4b.LedgerIntegrityError(f"UNEXPECTED_ARCHIVE_LAYOUT: {member.name}")
        observed.pop("evidence-manifest.json", None)
        if members is None or len(members) != spec["expected_member_count"] or set(members) != set(observed):
            raise p4b.LedgerIntegrityError("ARCHIVE_MEMBER_INVENTORY_MISMATCH")
        for name, expected in members.items():
            if observed[name] != {k: expected[k] for k in ("sha256", "size_bytes")}:
                raise p4b.LedgerIntegrityError(f"ARCHIVE_MEMBER_IDENTITY_MISMATCH: {name}")
    return spec


def read_reference(store, entry):
    member = checked_member(entry["raw_output_relative_path"])
    compressed = p4b.verify_raw_artifact(store / member, entry["compressed_output_sha256"])
    from io import BytesIO
    with gzip.GzipFile(fileobj=BytesIO(compressed)) as source:
        raw = source.read(entry["raw_output_size"] + 1)
    if len(raw) != entry["raw_output_size"] or p4b.sha256_bytes(raw) != entry["raw_output_sha256"]:
        raise p4b.LedgerIntegrityError(f"REFERENCE_RAW_IDENTITY_MISMATCH: {member}")
    return raw


def rebuild_from_store(
    store,
    protocol,
    manifest,
    *,
    prefix_dir="pre-r09",
    binary_sha256=p4b.HISTORICAL_R1_NATIVE_SHA256,
    native_source_commit=p4b.HISTORICAL_R1_NATIVE_SOURCE_COMMIT,
    replay_rev=REPLAY_REV,
    trusted_root=None,
    require_exact_historical_counts=True,
):
    """Re-derive certification against actual archived/replay bytes and an independently rebuilt M0."""
    rows = {}
    for line in (store / f"{prefix_dir}/replay.jsonl").read_bytes().splitlines():
        row = p4b.load_json_bytes(line)
        key = (row["workload_id"], row["variant_id"])
        if key in rows:
            raise p4b.LedgerIntegrityError(f"DUPLICATE_REFERENCE: {key}")
        rows[key] = row
    expected = {(w["workload_id"], v) for w in manifest["workloads"] for v in p4b.ALL_VARIANTS}
    if set(rows) != expected or len(rows) != 250:
        raise p4b.LedgerIntegrityError("REFERENCE_INVENTORY_MISMATCH")

    entries = []
    for workload in manifest["workloads"]:
        wid = workload["workload_id"]
        fixture = p4b.load_json_bytes((p4b.REPO_ROOT / workload["fixture_path"]).read_bytes())
        oracle, graphs, codecs = replay.oracle_and_snapshots(fixture, workload)
        oracle_path = f"{prefix_dir}/{wid}/oracle.json"
        oracle_bytes = (store / oracle_path).read_bytes()
        if oracle_bytes != p4b.canonical_json_bytes(oracle):
            raise p4b.LedgerIntegrityError(f"ORACLE_RECOMPUTATION_MISMATCH: {wid}")
        bare = None
        for variant in p4b.DIAG_VARIANTS:
            row = rows[wid, variant]
            role = p4b.ALL_VARIANTS[variant]
            relative = f"{wid}/{variant}.json.gz"
            fields = {
                "attempt_id": f"PRE-R09/{wid}/{variant}/1", "raw_output": relative,
                "fixture_sha256": workload["fixture_sha256"], "binary_sha256": binary_sha256,
                "native_source_commit": native_source_commit, "replay_source_commit": replay_rev,
                "protocol_sha256": p4b.PROTOCOL_SHA256, "manifest_sha256": protocol["manifest_sha256"],
                "schedule_sha256": protocol["schedule_sha256"], "oracle_sha256": p4b.sha256_bytes(oracle_bytes),
                "oracle_availability": oracle["availability"], "evidence_kind": "REAL_CORRECTNESS",
                "execution_purpose": "correctness", "process_signal": None, "discrepancy": None,
            }
            for key, value in fields.items():
                if type(row.get(key)) is not type(value) or row[key] != value:
                    raise p4b.LedgerIntegrityError(f"REFERENCE_ROW_IDENTITY: {wid}/{variant}/{key}")
            entry = {
                "schema": SCHEMA, "workload_id": wid, "case_id": workload["case_id"],
                "workload_semantic_sha256": workload["workload_dict_sha256"],
                "variant_id": variant, "engine": role["engine"],
                "requested_nlf_mode": role["nlf_mode"], "requested_neighbor_mode": role["neighbor_mode"],
                **{k: row[k] for k in ("fixture_sha256", "binary_sha256", "native_source_commit",
                   "replay_source_commit", "protocol_sha256", "manifest_sha256", "schedule_sha256",
                   "oracle_sha256", "oracle_availability", "compressed_output_sha256")},
                "raw_output_relative_path": f"{prefix_dir}/" + relative,
                "raw_output_sha256": row["output_sha256"], "raw_output_size": row["output_size_bytes"],
                "oracle_relative_path": oracle_path,
            }
            native = p4b.load_json_bytes(read_reference(store, entry))
            result, prefix = replay.validate_replay(
                {**row, "output_data": native}, variant, fixture, graphs, codecs, oracle, bare)
            if variant == "BARE":
                bare = native
            if result != row["result"] or prefix != row["verified_prefix_steps"] or native["status"] != row["native_status"]:
                raise p4b.LedgerIntegrityError(f"REFERENCE_CERTIFICATION_MISMATCH: {wid}/{variant}")
            entry.update(
                certification_scope=result, native_exit_code=row["exit_code"], native_status=native["status"],
                effective_nlf_mode=native["effective_nlf_mode"], effective_neighbor_mode=native["effective_neighbor_mode"],
                lifecycle=native["execution"], expected_terminal_category=native["error_category"],
                eligibility_policy="P4B3B0_REVIEWED_C1_C3_DISC07_V1",
            )
            entries.append(entry)
    counts = dict(Counter(r["certification_scope"] for r in entries))
    if require_exact_historical_counts and counts != EXPECTED_COUNTS:
        raise p4b.LedgerIntegrityError("REFERENCE_TIER_INVENTORY_MISMATCH")
    return {"schema": SCHEMA, "trusted_evidence": trusted_root or ROOT, "counts": counts, "entries": entries}


def require_canonical_registry(candidate, reconstructed):
    if candidate != reconstructed:
        raise p4b.LedgerIntegrityError("REGISTRY_DOES_NOT_MATCH_TRUSTED_ARCHIVE")


def validate_registry(registry_path, archive, store, protocol, manifest, *, bundle=None):
    """Trust the archived provenance, not a digest supplied by the caller's registry."""
    spec = unpack_references(archive, store, bundle=bundle)
    canonical = p4b.canonical_json_bytes(
        rebuild_from_store(
            store,
            protocol,
            manifest,
            prefix_dir=spec["reference_prefix_layout"],
            binary_sha256=spec["native_binary_sha256"],
            native_source_commit=spec["native_source_commit"],
            replay_rev=spec["replay_revision"],
            trusted_root=spec["trusted_root"],
        )
    )
    require_canonical_registry(registry_path.read_bytes(), canonical)
    reg_sha = p4b.sha256_bytes(canonical)
    if reg_sha != spec["registry_sha256"]:
        raise p4b.LedgerIntegrityError("BUNDLE_REGISTRY_HASH_MISMATCH")
    document = p4b.load_json_bytes(canonical)
    entries = document["entries"]
    if any(
        row["binary_sha256"] != spec["native_binary_sha256"]
        or row["native_source_commit"] != spec["native_source_commit"]
        for row in entries
    ):
        raise p4b.LedgerIntegrityError("BUNDLE_BINARY_PROVENANCE_MISMATCH")
    return {(x["workload_id"], x["variant_id"]): x for x in entries}, reg_sha


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--archive", required=True, type=Path)
    cli.add_argument("--registry", default=CANDIDATE_REGISTRY_PATH, type=Path)
    cli.add_argument("--bundle", default="R2_CANDIDATE_BUNDLE", choices=sorted(TRUSTED_BUNDLES))
    mode = cli.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = cli.parse_args()
    spec = resolve_trusted_bundle(args.bundle)
    protocol, manifest, _ = p4b.load_frozen_plan()
    with TemporaryDirectory(prefix="p4b-reference-") as tmp:
        store = Path(tmp)
        unpack_references(args.archive, store, bundle=spec)
        document = rebuild_from_store(
            store,
            protocol,
            manifest,
            prefix_dir=spec["reference_prefix_layout"],
            binary_sha256=spec["native_binary_sha256"],
            native_source_commit=spec["native_source_commit"],
            replay_rev=spec["replay_revision"],
            trusted_root=spec["trusted_root"],
        )
        canonical = p4b.canonical_json_bytes(document)
        if args.build:
            with args.registry.open("xb") as stream:
                stream.write(canonical)
        elif args.registry.read_bytes() != canonical:
            raise p4b.LedgerIntegrityError("REGISTRY_DOES_NOT_MATCH_TRUSTED_ARCHIVE")
        print({"bundle_id": spec["bundle_id"], "registry_sha256": p4b.sha256_bytes(canonical), **document["counts"]})


if __name__ == "__main__":
    main()
