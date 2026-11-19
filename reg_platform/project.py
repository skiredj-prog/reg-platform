#!/usr/bin/env python3
"""Build the REG projection v1 from a reg-conformance checkout.

The projection is the only thing the presentation layer (reg-site) reads.
It copies facts out of reg-conformance at one exact commit, recomputes what
can be recomputed (structural admissibility, evidence binding), and records
every disagreement instead of hiding it.

Usage:
    python -m reg_platform.project --source ../reg-conformance \
        --expect-commit b943f06... --out projection/projection-v1.json
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: pip install pyyaml") from exc

CONTRACT = "reg-projection"
CONTRACT_VERSION = "1.0.0"
ZERO_HASH = "0" * 64
SOURCE_REPO = "https://github.com/skiredj-prog/reg-conformance"

# Guarantee states, in the presentation vocabulary. "partial" is the
# catalog's own term for evidence bound on a loopback-only perimeter.
STATE_DEMONSTRATED = "demonstrated"   # catalog true + every record bound, production-equivalent
STATE_PARTIAL = "partial"             # catalog partial, or bound on loopback-test-only
STATE_NOT_EVALUATED = "not_evaluated" # catalog null, or evidence not bound
STATE_REFUTED = "refuted"             # catalog false
STATE_INADMISSIBLE = "inadmissible"   # structurally rejected


def git(source: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(source), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def load_yaml(path: Path):
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_json(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def config_id(cfg: dict, axes: list[str]) -> str:
    return "__".join(cfg[a] for a in axes)


def violations_for(cfg: dict, constraints: list[dict]) -> list[dict]:
    out = []
    for c in constraints:
        cond, req = c["condition"], c["requires"]
        if cfg.get(cond["field"]) == cond["value"] and cfg.get(req["field"]) not in req["allowed_values"]:
            out.append({
                "clause_id": c["clause_id"],
                "rule_id": c["rule_id"],
                "description": c["description"],
                "field": req["field"],
                "allowed_values": list(req["allowed_values"]),
            })
    return out


def tracked_file_hashes(source: Path) -> dict[str, str]:
    """sha256 -> path for every file tracked at the source commit."""
    out = {}
    for rel in git(source, "ls-files", "-z").split("\0"):
        if not rel:
            continue
        p = source / rel
        if p.is_file():
            out[hashlib.sha256(p.read_bytes()).hexdigest()] = rel
    return out


def evidence_bound(record: dict, hashes: dict[str, str]) -> tuple[bool, list[str], str | None]:
    """A record is bound only if its evidence can be checked here: the
    results hash must be the sha256 of a file tracked in reg-conformance at
    the source commit, the scope must be set, and the validation flag true.
    A hash that nothing in the repository reproduces is a declaration, not
    evidence."""
    problems = []
    ev = record.get("evidence", {})
    h = ev.get("test_results_hash", "")
    artifact = None
    if not h or h == ZERO_HASH:
        problems.append("test_results_hash is empty or all zeros")
    elif h not in hashes:
        problems.append("no file tracked in reg-conformance hashes to test_results_hash")
    else:
        artifact = hashes[h]
    if record.get("demonstration_scope") not in ("production-equivalent", "loopback-test-only"):
        problems.append("demonstration_scope is not set")
    if record.get("validation", {}).get("effective_guarantee_demonstrated") is not True:
        problems.append("validation.effective_guarantee_demonstrated is not true")
    return (not problems, problems, artifact)


def build(source: Path, expect_commit: str | None) -> dict:
    findings: list[dict] = []

    def finding(level: str, code: str, message: str, **extra):
        findings.append({"level": level, "code": code, "message": message, **extra})

    # --- source identity -------------------------------------------------
    commit = git(source, "rev-parse", "HEAD")
    dirty = git(source, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        finding("error", "SRC-DIRTY", "The reg-conformance checkout has uncommitted changes.")
    if expect_commit and not commit.startswith(expect_commit):
        finding("error", "SRC-COMMIT", f"HEAD is {commit}, expected {expect_commit}.")
    commit_date = git(source, "show", "-s", "--format=%cI", commit)
    try:
        describe = git(source, "describe", "--tags", "--match", "v*", commit)
    except subprocess.CalledProcessError:
        describe = None
    exact_tag = None
    try:
        exact_tag = git(source, "describe", "--tags", "--exact-match", "--match", "v*", commit)
    except subprocess.CalledProcessError:
        pass

    # --- catalogue -------------------------------------------------------
    hyp = load_yaml(source / "catalog/hypotheses-v1.yaml")
    rules = load_yaml(source / "catalog/compatibility-rules-v1.yaml")
    caps = load_yaml(source / "catalog/capabilities-v1.yaml")
    valid = load_json(source / "catalog/valid-configurations-v1.1.0.json")

    axes = list(hyp["axes"].keys())
    axis_defs = []
    for a in axes:
        values = []
        for v in hyp["axes"][a]["values"]:
            cap = caps.get("capabilities", {}).get(a, {}).get(v, {})
            values.append({
                "value": v,
                "capability_id": cap.get("capability_id"),
                "description": cap.get("description"),
                "required_evidence": list(cap.get("required_evidence", [])),
            })
        axis_defs.append({"id": a, "description": hyp["axes"][a].get("description"), "values": values})

    if valid.get("rules_version") != rules.get("version"):
        finding("warning", "CAT-RULES-VERSION",
                f"valid-configurations declares rules_version {valid.get('rules_version')}, "
                f"compatibility-rules-v1.yaml declares {rules.get('version')}.")

    # guarantee records
    records = {}
    hashes = tracked_file_hashes(source)
    for p in sorted((source / "capabilities/v1.0.0").glob("*.json")):
        r = load_json(p)
        bound, problems, artifact = evidence_bound(r, hashes)
        records[r["capability_id"]] = {
            "id": r["capability_id"],
            "name": r["declaration"]["name"],
            "description": r["declaration"]["description"],
            "invariants_required": r["declaration"]["invariants_required"],
            "deployment_perimeter": r["declaration"]["deployment_perimeter"],
            "implementation_version": r["evidence"].get("implementation_version"),
            "test_command": r["evidence"].get("test_command"),
            "evidence_bound": bound,
            "evidence_problems": problems,
            "evidence_artifact": artifact,
            "declared_results_hash": r["evidence"].get("test_results_hash"),
            "demonstration_scope": r.get("demonstration_scope"),
            "status_note": r.get("status_note"),
            "known_limits": r.get("limitations", {}).get("known_limits", []),
            "source_path": f"capabilities/v1.0.0/{p.name}",
        }

    for rec in records.values():
        perim = {k: v for k, v in rec["deployment_perimeter"].items() if k in axes}
        clash = [v for v in violations_for(perim, rules["constraints"]) if v["field"] in perim]
        if clash:
            finding("warning", "REC-PERIMETER",
                    f"Record {rec['id']} declares a perimeter the rules reject "
                    f"({', '.join(v['clause_id'] for v in clash)}); it is attached to no configuration.",
                    record=rec["id"])

    # configurations: recompute admissibility, check catalog claims
    catalog_by_id = {config_id(c["config"], axes): c for c in valid["configurations"]}
    configurations = []
    expected_total = 1
    for a in axis_defs:
        expected_total *= len(a["values"])
    for combo in itertools.product(*[[v["value"] for v in a["values"]] for a in axis_defs]):
        cfg = dict(zip(axes, combo))
        cid = config_id(cfg, axes)
        viol = violations_for(cfg, rules["constraints"])
        admissible = not viol
        cat = catalog_by_id.get(cid)
        if cat is None:
            finding("error", "CAT-MISSING", f"Configuration {cid} is absent from the catalogue.", config=cid)
            continue
        if cat["admissible_structural"] != admissible:
            finding("error", "CAT-ADMISSIBILITY",
                    f"Catalogue says admissible={cat['admissible_structural']} for {cid}; rules give {admissible}.",
                    config=cid)
        refs = list(cat.get("capability_refs") or [])
        for ref in refs:
            if ref not in records:
                finding("error", "CAT-REF", f"{cid} cites unknown guarantee record {ref}.", config=cid)
        claimed = cat.get("effective_guarantee_demonstrated")
        scope = cat.get("demonstration_scope")
        unbound = [r for r in refs if r in records and not records[r]["evidence_bound"]]
        if not admissible:
            state = STATE_INADMISSIBLE
        elif claimed is False:
            state = STATE_REFUTED
        elif claimed is None:
            state = STATE_NOT_EVALUATED
        else:
            # The catalogue claims something. Fail closed unless every cited
            # record carries bound evidence.
            if not refs or unbound:
                finding("error", "GUARD-UNBOUND-CLAIM",
                        f"{cid} is marked {claimed!r} in the catalogue but records {unbound or '(none)'} "
                        "carry no bound evidence. Projected as not_evaluated.", config=cid)
                state = STATE_NOT_EVALUATED
            elif claimed == "partial" or scope == "loopback-test-only" or any(
                    records[r]["demonstration_scope"] == "loopback-test-only" for r in refs):
                state = STATE_PARTIAL
            else:
                state = STATE_DEMONSTRATED
        configurations.append({
            "id": cid,
            "config": cfg,
            "admissible_structural": admissible,
            "violations": viol,
            "capability_refs": refs,
            "catalog_claim": claimed,
            "state": state,
        })
    if len(configurations) != expected_total:
        finding("error", "CAT-COUNT", f"{len(configurations)} configurations projected, {expected_total} expected.")

    # --- vectors ---------------------------------------------------------
    vectors = []
    for name in ("core", "commit", "extended", "proposals"):
        data = load_yaml(source / f"vectors/{name}.yaml")
        for v in data.get("vectors", []) + data.get("candidates", []) + data.get("non_validated", []):
            vectors.append({
                "id": v["id"],
                "file": f"vectors/{name}.yaml",
                "category": v.get("category"),
                "status": v.get("status"),
                "rfc": v.get("rfc"),
                "property": v.get("property"),
                "expected": v.get("expected"),
                # The runner's native mode runs the repository's reference
                # kernel. It is not a result for any product.
                "reference_kernel_result": v.get("native"),
                "note": v.get("note"),
            })
    ids = [v["id"] for v in vectors]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        finding("error", "VEC-DUPLICATE", f"Duplicate vector ids: {', '.join(dupes)}.")
    dist = source / "vectors/dist/vectors.json"
    if dist.exists():
        dist_ids = sorted(x["id"] for x in load_json(dist)["vectors"])
        if dist_ids != sorted(ids):
            finding("warning", "VEC-DIST-STALE", "vectors/dist/vectors.json differs from the YAML sources.")

    invariants = [
        {"id": i["id"], "name": i["name"], "property": i["property"],
         "formerly": i.get("formerly"), "verification": i.get("verification")}
        for i in caps.get("normative_invariants", [])
    ]

    def count(items, key):
        out: dict[str, int] = {}
        for it in items:
            out[str(it[key])] = out.get(str(it[key]), 0) + 1
        return dict(sorted(out.items()))

    summary = {
        "configurations_total": len(configurations),
        "configurations_admissible": sum(c["admissible_structural"] for c in configurations),
        "configurations_by_state": count(configurations, "state"),
        "guarantee_records_total": len(records),
        "guarantee_records_bound": sum(r["evidence_bound"] for r in records.values()),
        "vectors_total": len(vectors),
        "vectors_by_status": count(vectors, "status"),
        "vectors_by_reference_kernel_result": count(vectors, "reference_kernel_result"),
    }

    errors = [f for f in findings if f["level"] == "error"]
    projection = {
        "contract": CONTRACT,
        "contract_version": CONTRACT_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": {
            "repository": SOURCE_REPO,
            "commit": commit,
            "commit_date": commit_date,
            "describe": describe,
            "exact_tag": exact_tag,
            "catalog_version": valid.get("catalog_version"),
            "rules_version": rules.get("version"),
            "capabilities_version": caps.get("version"),
        },
        "claims": {
            # Nothing in this projection lets anyone say a product conforms
            # to REG. These fields exist so the site can check that, not
            # infer it.
            "conformance_claims_allowed": False,
            "subjects": [],
            "reason": "No subject run has been projected. Reference-kernel results are not product results.",
        },
        "guards": {
            "passed": not errors,
            "findings": findings,
        },
        "summary": summary,
        "axes": axis_defs,
        "invariants": invariants,
        "guarantee_records": sorted(records.values(), key=lambda r: r["id"]),
        "configurations": configurations,
        "vectors": vectors,
    }
    projection["content_sha256"] = content_hash(projection)
    return projection


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def content_hash(projection: dict) -> str:
    body = {k: v for k, v in projection.items() if k not in ("content_sha256", "generated_at")}
    return hashlib.sha256(canonical(body)).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", required=True, type=Path)
    ap.add_argument("--expect-commit")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    projection = build(args.source.resolve(), args.expect_commit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(projection, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for f in projection["guards"]["findings"]:
        print(f"[{f['level']}] {f['code']}: {f['message']}", file=sys.stderr)
    s = projection["summary"]
    print(f"projection {projection['content_sha256'][:12]} from {projection['source']['commit'][:12]}: "
          f"{s['configurations_admissible']}/{s['configurations_total']} admissible, "
          f"{s['configurations_by_state']}, {s['vectors_total']} vectors")
    return 0 if projection["guards"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
