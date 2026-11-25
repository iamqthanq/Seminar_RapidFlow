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
SCHEMA = "p4b-correctness-registry-v1"
ROOT = {"archive_sha256": ARCHIVE_SHA, "evidence_manifest_sha256": MANIFEST_SHA}
EXPECTED_COUNTS = {
    "M0_VERIFIED": 160, "DIFFERENTIAL_M2_VERIFIED": 64,
    "CORRECTNESS_NOT_FULLY_CERTIFIED": 16, "C3_BUDGET_UNAVAILABLE": 10,
}


def checked_member(name):
    path = PurePosixPath(name)
    if not name or name != str(path) or path.is_absolute() or ".." in path.parts or name == "." or "\\" in name:
        raise p4b.LedgerIntegrityError(f"UNSAFE_ARCHIVE_PATH: {name}")
    return path


def unpack_references(archive, store):
    """Verify every member and copy just replay artifacts, without tar extraction."""
    with archive.open("rb") as stream:
        if archive.stat().st_size != ARCHIVE_SIZE or p4b.sha256_bytes(stream.read()) != ARCHIVE_SHA:
            raise p4b.LedgerIntegrityError("R1_ARCHIVE_IDENTITY_MISMATCH")
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
                    if sha != MANIFEST_SHA:
                        raise p4b.LedgerIntegrityError("R1_MANIFEST_HASH_MISMATCH")
                    members = p4b.load_json_bytes(raw)["artifacts"]
                elif relative.parts[0] == "pre-r09":
                    out = store / relative
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with out.open("xb") as file:
                        file.write(raw)
        observed.pop("evidence-manifest.json", None)
        if members is None or len(members) != 1811 or set(members) != set(observed):
            raise p4b.LedgerIntegrityError("ARCHIVE_MEMBER_INVENTORY_MISMATCH")
        for name, expected in members.items():
            if observed[name] != {k: expected[k] for k in ("sha256", "size_bytes")}:
                raise p4b.LedgerIntegrityError(f"ARCHIVE_MEMBER_IDENTITY_MISMATCH: {name}")


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


def validate_registry(registry_path, archive, store, protocol, manifest):
    """Trust the archived provenance, not a digest supplied by the caller's registry."""
    unpack_references(archive, store)
    canonical = p4b.canonical_json_bytes(rebuild_from_store(store, protocol, manifest))
    require_canonical_registry(registry_path.read_bytes(), canonical)
    document = p4b.load_json_bytes(canonical)
    return {(x["workload_id"], x["variant_id"]): x for x in document["entries"]}, p4b.sha256_bytes(canonical)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--archive", required=True, type=Path)
    cli.add_argument("--registry", default=REGISTRY_PATH, type=Path)
    mode = cli.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = cli.parse_args()
    protocol, manifest, _ = p4b.load_frozen_plan()
    with TemporaryDirectory(prefix="p4b-reference-") as tmp:
        store = Path(tmp)
        unpack_references(args.archive, store)
        document = rebuild_from_store(store, protocol, manifest)
        canonical = p4b.canonical_json_bytes(document)
        if args.build:
            with args.registry.open("xb") as stream:
                stream.write(canonical)
        elif args.registry.read_bytes() != canonical:
            raise p4b.LedgerIntegrityError("REGISTRY_DOES_NOT_MATCH_TRUSTED_ARCHIVE")
        print({"registry_sha256": p4b.sha256_bytes(canonical), **document["counts"]})


if __name__ == "__main__":
    main()
