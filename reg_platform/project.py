#!/usr/bin/env python3
"""Build the REG projection v1 from a reg-conformance checkout.

The projection is the only thing the presentation layer (reg-site) reads.
It copies facts out of reg-conformance at one exact commit, recomputes what
can be recomputed (structural admissibility, evidence binding), and records
every disagreement instead of hiding it.

The content of a projection depends on the pinned commit and on
source.lock.json only: two honest builds of the same lock give the same
content_sha256, whatever else the checkout holds (tags, other branches).

Usage:
    python -m reg_platform.project --source ../reg-conformance \
        --expect-commit b943f06... [--expect-tag v0.7.0] \
        --out projection/projection-v1.json
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: pip install pyyaml") from exc

CONTRACT = "reg-projection"
CONTRACT_VERSION = "1.1.0"
ZERO_HASH = "0" * 64
SOURCE_REPO = "https://github.com/skiredj-prog/reg-conformance"

# Guarantee states, in the presentation vocabulary. "partial" is the
# catalog's own term for evidence bound on a loopback-only perimeter.
STATE_DEMONSTRATED = "demonstrated"   # catalog true + every record bound, production-equivalent
STATE_PARTIAL = "partial"             # catalog partial, or bound on loopback-test-only
STATE_NOT_EVALUATED = "not_evaluated" # catalog null, or evidence not bound
STATE_REFUTED = "refuted"             # catalog false
STATE_INADMISSIBLE = "inadmissible"   # structurally rejected

VECTOR_FILES = ("core", "commit", "extended", "proposals")
# YAML list key -> registry. Only "vectors" entries are registered vectors;
# the other two lists hold proposals that the runner does not register.
REGISTRY_KEYS = (("vectors", "vector"), ("candidates", "candidate"), ("non_validated", "non_validated"))
INDEX_PATH = "VECTORS.md"
CLAIMS_PATH = "CLAIMS.md"


def git(source: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(source), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


class Reader:
    """Reads files of the checkout and remembers each one with its sha256,
    so the projection can say exactly what it was built from."""

    def __init__(self, source: Path):
        self.source = source
        self.files: dict[str, dict] = {}

    def raw(self, rel: str) -> bytes:
        data = (self.source / rel).read_bytes()
        self.files[rel] = {"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        return data

    def text(self, rel: str) -> str:
        return self.raw(rel).decode("utf-8")

    def yaml(self, rel: str):
        return yaml.safe_load(self.text(rel)) or {}

    def json(self, rel: str):
        return json.loads(self.text(rel))

    def exists(self, rel: str) -> bool:
        return (self.source / rel).is_file()

    def listing(self) -> list[dict]:
        return [self.files[k] for k in sorted(self.files)]


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
            out.setdefault(hashlib.sha256(p.read_bytes()).hexdigest(), rel)
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


# ---------------------------------------------------------------- markdown
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def md_plain(s: str) -> str:
    """Markdown inline -> plain text: links keep their text, code keeps its
    content. The words are the source's; only the markup goes."""
    s = _LINK.sub(r"\1", s)
    s = s.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", s).strip()


def parse_vector_index(text: str) -> dict:
    """VECTORS.md: the prose before the table, the status definitions it
    gives, and one row per vector id."""
    lines = text.splitlines()
    rows: dict[str, dict] = {}
    header = None
    notes: list[str] = []
    para: list[str] = []
    for line in lines:
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if header is None:
                header = [c.lower() for c in cells]
                continue
            if all(set(c) <= set("-: ") for c in cells):
                continue
            row = dict(zip(header, cells))
            vid = row.get("vector_id")
            if vid:
                rows[vid] = {
                    "status": row.get("status") or None,
                    "family": row.get("family") or None,
                    "rfc": md_plain(row.get("rfc", "")) or None,
                    "normative_property": md_plain(row.get("normative_property", "")) or None,
                    "location": md_plain(row.get("location", "")) or None,
                }
        elif header is None:
            if line.startswith("#"):
                continue
            if line.strip():
                para.append(line.strip())
            elif para:
                notes.append(md_plain(" ".join(para)))
                para = []
    if para and header is None:
        notes.append(md_plain(" ".join(para)))
    definitions = {}
    for m in re.finditer(r"`(IMPLEMENTED|DECLARED|RESERVED)` means ([^.]+)\.", text):
        definitions[m.group(1)] = md_plain(m.group(2)) + "."
    return {"rows": rows, "notes": notes, "status_definitions": definitions}


def parse_claims(text: str) -> dict:
    """CLAIMS.md, kept verbatim: which sections the file says are in force,
    and for each section its quoted templates, list items and steps.
    `blocks` keeps every paragraph, quote, list and table in file order, each
    tagged with the bold part it sits under, so a reader can render the
    section as written."""
    parts = re.split(r"^## +(.+)$", text, flags=re.M)
    intro, rest = parts[0], parts[1:]
    in_force = None
    m = re.search(r"in force:\s*(.+?)\.\s+The rest", intro, re.S)
    if m:
        in_force = [re.sub(r"\s+", " ", t).strip() for t in re.findall(r'"([^"]+)"', m.group(1))] or None
    title = None
    tm = re.search(r"^# +(.+)$", intro, flags=re.M)
    if tm:
        title = tm.group(1).strip()
    status_line = None
    if m:
        sm = re.search(r"([^.\n]*in force:.+?\.\s+The rest[^.]*\.)", intro, re.S)
        status_line = md_plain(sm.group(1)) if sm else None
    sections = []
    for i in range(0, len(rest), 2):
        name, body = rest[i].strip(), rest[i + 1]
        quotes, bullets, steps, paras, tables = [], [], [], [], []
        blocks: list[dict] = []
        part: str | None = None
        q: list[str] = []
        table: list[list[str]] = []
        item: list[str] | None = None
        kind = None
        buf: list[str] = []

        def flush():
            nonlocal item, kind, buf, table, q, part
            if item:
                text = md_plain(" ".join(item))
                (bullets if kind == "bullet" else steps).append(text)
                ordered = kind == "step"
                last = blocks[-1] if blocks else None
                if last and last["type"] == "list" and last["ordered"] == ordered:
                    last["items"].append(text)
                else:
                    blocks.append({"type": "list", "ordered": ordered, "items": [text], "part": part})
            item, kind = None, None
            if buf:
                raw = " ".join(buf)
                paras.append(md_plain(raw))
                lm = re.match(r"^\*\*([^*]+)\*\*\s*(.*)$", raw)
                if lm:
                    part = re.sub(r"[.:]$", "", lm.group(1).strip())
                    blocks.append({"type": "paragraph", "lead": md_plain(lm.group(1)),
                                   "text": md_plain(lm.group(2)), "part": part})
                else:
                    blocks.append({"type": "paragraph", "lead": None, "text": md_plain(raw), "part": part})
                buf = []
            if table:
                tables.append({"header": table[0], "rows": table[1:]})
                blocks.append({"type": "table", "header": table[0], "rows": table[1:], "part": part})
                table = []
            if q:
                text = md_plain(" ".join(x for x in q if x))
                quotes.append(text)
                blocks.append({"type": "quote", "text": text, "part": part})
                q = []

        for line in body.splitlines() + [""]:
            s = line.rstrip()
            if s.startswith(">"):
                if not q:
                    flush()
                q.append(s.lstrip(">").strip())
                continue
            if s.startswith("|"):
                if not table:
                    flush()
                cells = [md_plain(c) for c in s.strip().strip("|").split("|")]
                if not all(set(c) <= set("-: ") for c in cells):
                    table.append(cells)
                continue
            bm = re.match(r"^- +(.*)$", s)
            nm = re.match(r"^\d+\. +(.*)$", s)
            if bm or nm:
                flush()
                kind = "bullet" if bm else "step"
                item = [(bm or nm).group(1)]
            elif s.startswith("  ") and item is not None:
                item.append(s.strip())
            elif not s.strip():
                flush()
            else:
                if item is not None or table or q:
                    flush()
                buf.append(s.strip())
        # Bold lead-ins ("**What counts as an admission.**") name the parts
        # of a section; CLAIMS.md may put one part in force and not the rest.
        parts = [re.sub(r"[.:]$", "", m.group(1).strip())
                 for m in re.finditer(r"^\*\*([^*]+)\*\*", body, flags=re.M)]
        if in_force is None:
            status, parts_in_force = "unknown", []
        elif name in in_force:
            status, parts_in_force = "in_force", []
        else:
            parts_in_force = [x for x in parts if x in in_force]
            status = "partly_in_force" if parts_in_force else "draft"
        sections.append({
            "title": name,
            "status": status,
            "parts": parts,
            "parts_in_force": parts_in_force,
            "paragraphs": paras,
            "quotes": quotes,
            "bullets": bullets,
            "steps": steps,
            "tables": tables,
            "blocks": blocks,
        })
    return {"title": title, "status_line": status_line, "in_force_sections": in_force, "sections": sections}


def vector_lines(text: str) -> dict[str, int]:
    out = {}
    for n, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^\s*-\s+id:\s*[\"']?([^\"'\s#]+)", line)
        if m:
            out.setdefault(m.group(1), n)
    return out


def replay_report(path: Path | None, registered: list[dict], finding) -> dict:
    """Compare the registry's reference-kernel results with a native run of
    the runner at the same commit. The projection does not take the
    registry's word for it: any difference is an error."""
    if path is None:
        for v in registered:
            v["reference_kernel_replay"] = None
        return {"performed": False}
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("interface_mode") != "native":
        finding("error", "VEC-REPLAY-MODE", f"The replay report was not produced in native mode ({report.get('interface_mode')!r}).")
    run = {r["vector_id"]: r["status"] for r in report.get("results", [])}
    same = 0
    for v in registered:
        got = run.get(v["id"])
        v["reference_kernel_replay"] = got
        if v["id"] not in run:
            finding("error", "VEC-REPLAY-MISSING", f"The native run has no result for {v['id']}.", vector=v["id"])
        elif got != v["reference_kernel_result"]:
            finding("error", "VEC-REPLAY-DRIFT",
                    f"{v['id']}: the registry says {v['reference_kernel_result']}, the native run gives {got}.", vector=v["id"])
        else:
            same += 1
    extra = sorted(set(run) - {v["id"] for v in registered})
    if extra:
        finding("error", "VEC-REPLAY-EXTRA", f"The native run reports ids the registry does not hold: {', '.join(extra)}.")
    return {
        "performed": True,
        "suite": report.get("suite"),
        "interface_mode": report.get("interface_mode"),
        "evidence_level": report.get("evidence_level"),
        "results_total": len(run),
        "matching_registry": same,
        "summary_by_basis": report.get("summary_by_basis"),
    }


def build(source: Path, expect_commit: str | None, expect_tag: str | None = None,
          native_report: Path | None = None) -> dict:
    findings: list[dict] = []

    def finding(level: str, code: str, message: str, **extra):
        findings.append({"level": level, "code": code, "message": message, **extra})

    R = Reader(source)

    # --- source identity -------------------------------------------------
    commit = git(source, "rev-parse", "HEAD")
    dirty = git(source, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        finding("error", "SRC-DIRTY", "The reg-conformance checkout has uncommitted changes.")
    if expect_commit and not commit.startswith(expect_commit):
        finding("error", "SRC-COMMIT", f"HEAD is {commit}, expected {expect_commit}.")
    commit_date = git(source, "show", "-s", "--format=%cI", commit)
    # The tag comes from the lock, never from whatever tags the clone holds,
    # so the content does not depend on the state of the clone.
    tag = None
    if expect_tag:
        try:
            tagged = git(source, "rev-parse", "--verify", "-q", f"refs/tags/{expect_tag}^{{commit}}")
        except subprocess.CalledProcessError:
            tagged = ""
        if tagged != commit:
            finding("error", "SRC-TAG",
                    f"source.lock.json names tag {expect_tag}, which "
                    f"{'points to ' + tagged if tagged else 'does not exist in the checkout'}, not {commit}.")
        else:
            tag = expect_tag

    # --- catalogue -------------------------------------------------------
    hyp = R.yaml("catalog/hypotheses-v1.yaml")
    rules = R.yaml("catalog/compatibility-rules-v1.yaml")
    caps = R.yaml("catalog/capabilities-v1.yaml")
    valid = R.json("catalog/valid-configurations-v1.1.0.json")

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

    rule_defs = [{
        "clause_id": c["clause_id"],
        "rule_id": c["rule_id"],
        "description": c["description"],
        "condition": {"field": c["condition"]["field"], "value": c["condition"]["value"]},
        "requires": {"field": c["requires"]["field"], "allowed_values": list(c["requires"]["allowed_values"])},
    } for c in rules["constraints"]]

    # guarantee records
    records = {}
    hashes = tracked_file_hashes(source)
    for p in sorted((source / "capabilities/v1.0.0").glob("*.json")):
        rel = f"capabilities/v1.0.0/{p.name}"
        r = R.json(rel)
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
            "source_path": rel,
            "source_sha256": R.files[rel]["sha256"],
        }

    for rec in records.values():
        perim = {k: v for k, v in rec["deployment_perimeter"].items() if k in axes}
        clash = [v for v in violations_for(perim, rules["constraints"]) if v["field"] in perim]
        rec["perimeter_rejected_by"] = [v["clause_id"] for v in clash]
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
    index = None
    if R.exists(INDEX_PATH):
        index = parse_vector_index(R.text(INDEX_PATH))
    else:
        finding("warning", "VEC-INDEX-ABSENT", f"{INDEX_PATH} is absent; vectors carry no index status.")

    vectors = []
    for name in VECTOR_FILES:
        rel = f"vectors/{name}.yaml"
        text = R.text(rel)
        data = yaml.safe_load(text) or {}
        lines = vector_lines(text)
        for key, registry in REGISTRY_KEYS:
            for v in data.get(key, []) or []:
                row = index["rows"].get(v["id"]) if index and registry == "vector" else None
                vectors.append({
                    "id": v["id"],
                    "registry": registry,
                    "file": rel,
                    "line": lines.get(v["id"]),
                    "category": v.get("category"),
                    "status": v.get("status"),
                    "rfc": v.get("rfc"),
                    "property": v.get("property"),
                    "input": v.get("input"),
                    "intended": v.get("intended"),
                    "expected": v.get("expected"),
                    "observed": v.get("observed"),
                    "reference": v.get("reference"),
                    "superseded_by": list(v.get("superseded_by") or []),
                    # The runner's native mode runs the repository's reference
                    # kernel. It is not a result for any product.
                    "reference_kernel_result": v.get("native"),
                    "note": v.get("note"),
                    "index": row,
                })
    ids = [v["id"] for v in vectors]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        finding("error", "VEC-DUPLICATE", f"Duplicate vector ids: {', '.join(dupes)}.")
    registered = [v for v in vectors if v["registry"] == "vector"]
    for v in vectors:
        v.setdefault("reference_kernel_replay", None)
    replay = replay_report(native_report, registered, finding)
    # Rows the index lists with no registered vector: properties the RFCs
    # state that no vector of the runner exercises. Shown, not hidden.
    registered_ids = {v["id"] for v in registered}
    index_only = [{"id": k, **row} for k, row in (index["rows"].items() if index else []) if k not in registered_ids]
    if index is not None:
        missing = [v["id"] for v in registered if v["index"] is None]
        if missing:
            finding("warning", "VEC-INDEX-MISSING",
                    f"Registered vectors without a row in {INDEX_PATH}: {', '.join(missing)}.")
    dist_rel = "vectors/dist/vectors.json"
    if R.exists(dist_rel):
        dist_ids = sorted(x["id"] for x in R.json(dist_rel)["vectors"])
        if dist_ids != sorted(v["id"] for v in registered) and dist_ids != sorted(ids):
            finding("warning", "VEC-DIST-STALE", "vectors/dist/vectors.json differs from the YAML sources.")

    invariants = [
        {"id": i["id"], "name": i["name"], "property": i["property"],
         "formerly": i.get("formerly"), "verification": i.get("verification"),
         "scope": i.get("scope"), "applies_to": list(i.get("applies_to") or []),
         "contract_ref": i.get("contract_ref")}
        for i in caps.get("normative_invariants", [])
    ]

    # --- claim policy ----------------------------------------------------
    claims_policy = {"present": False, "path": CLAIMS_PATH, "sha256": None}
    if R.exists(CLAIMS_PATH):
        parsed = parse_claims(R.text(CLAIMS_PATH))
        claims_policy = {"present": True, "path": CLAIMS_PATH, "sha256": R.files[CLAIMS_PATH]["sha256"], **parsed}
        if parsed["in_force_sections"] is None:
            finding("warning", "CLAIMS-STATUS",
                    f"{CLAIMS_PATH} does not say which sections are in force in a form this projection reads; "
                    "every section is shown with an unknown status.")

    def count(items, key):
        out: dict[str, int] = {}
        for it in items:
            k = key(it) if callable(key) else it[key]
            out[str(k)] = out.get(str(k), 0) + 1
        return dict(sorted(out.items()))

    def index_status(v):
        return (v["index"] or {}).get("status") or "UNINDEXED"

    passes = [v for v in registered if v["reference_kernel_result"] == "PASS"]
    summary = {
        "configurations_total": len(configurations),
        "configurations_admissible": sum(c["admissible_structural"] for c in configurations),
        "configurations_by_state": count(configurations, "state"),
        "guarantee_records_total": len(records),
        "guarantee_records_bound": sum(r["evidence_bound"] for r in records.values()),
        "vectors_total": len(vectors),
        "vectors_registered": len(registered),
        "candidates_total": len(vectors) - len(registered),
        "vectors_superseded": sum(1 for v in registered if v["superseded_by"]),
        "vectors_by_status": count(vectors, "status"),
        "vectors_by_index_status": count(registered, index_status),
        "vectors_by_reference_kernel_result": count(registered, "reference_kernel_result"),
        "index_rows_without_vector": len(index_only),
        "index_rows_without_vector_by_status": count(index_only, lambda r: r.get("status") or "UNINDEXED"),
        "reference_kernel_pass": {
            "total": len(passes),
            "by_index_status": count(passes, index_status),
            "superseded": sum(1 for v in passes if v["superseded_by"]),
        },
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
            "exact_tag": tag,
            "catalog_version": valid.get("catalog_version"),
            "rules_version": rules.get("version"),
            "capabilities_version": caps.get("version"),
            "files": R.listing(),
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
        "rules": rule_defs,
        "invariants": invariants,
        "guarantee_records": sorted(records.values(), key=lambda r: r["id"]),
        "configurations": configurations,
        "vector_index": {
            "present": index is not None,
            "path": INDEX_PATH,
            "notes": index["notes"] if index else [],
            "status_definitions": index["status_definitions"] if index else {},
            "rows_without_vector": index_only,
        },
        "vectors": vectors,
        "reference_kernel_replay": replay,
        "claims_policy": claims_policy,
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
    ap.add_argument("--expect-tag", default="")
    ap.add_argument("--native-report", type=Path,
                    help="report of `runner.py --mode native` at the same commit; its results must match the registry")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    projection = build(args.source.resolve(), args.expect_commit, args.expect_tag or None, args.native_report)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(projection, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for f in projection["guards"]["findings"]:
        print(f"[{f['level']}] {f['code']}: {f['message']}", file=sys.stderr)
    s = projection["summary"]
    print(f"projection {projection['content_sha256'][:12]} from {projection['source']['commit'][:12]}"
          f"{' (' + projection['source']['exact_tag'] + ')' if projection['source']['exact_tag'] else ''}: "
          f"{s['configurations_admissible']}/{s['configurations_total']} admissible, "
          f"{s['configurations_by_state']}, {s['vectors_registered']} vectors + {s['candidates_total']} candidates")
    return 0 if projection["guards"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
