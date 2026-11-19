"""Guards of the projection: each test builds a throwaway reg-conformance
copy, breaks one thing, and checks that the projection refuses it."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from reg_platform.project import build, content_hash

SOURCE = Path(os.environ.get("REG_CONFORMANCE", Path(__file__).resolve().parents[2] / "reg-conformance"))
PATHS = ["catalog", "capabilities", "vectors"]
FILES = ["VECTORS.md", "CLAIMS.md"]


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


class Fixture:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp())
        for p in PATHS:
            shutil.copytree(SOURCE / p, self.tmp / p)
        for f in FILES:
            if (SOURCE / f).is_file():
                shutil.copy2(SOURCE / f, self.tmp / f)
        git(self.tmp, "init", "-q")
        git(self.tmp, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
        self.commit("base")

    def commit(self, msg):
        git(self.tmp, "add", "-A")
        git(self.tmp, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", msg)

    def edit_json(self, rel, fn):
        p = self.tmp / rel
        d = json.loads(p.read_text())
        fn(d)
        p.write_text(json.dumps(d, indent=2, ensure_ascii=False))

    def codes(self):
        return {f["code"] for f in build(self.tmp, None)["guards"]["findings"] if f["level"] == "error"}

    def close(self):
        shutil.rmtree(self.tmp)


@unittest.skipUnless((SOURCE / "catalog").exists(), "reg-conformance checkout not found")
class GuardTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def tearDown(self):
        self.f.close()

    def first_admissible(self, d):
        return next(c for c in d["configurations"] if c["admissible_structural"] and c["capability_refs"])

    def test_current_main_passes_with_no_claims(self):
        p = build(self.f.tmp, None)
        self.assertTrue(p["guards"]["passed"], p["guards"]["findings"])
        self.assertFalse(p["claims"]["conformance_claims_allowed"])
        self.assertEqual(p["claims"]["subjects"], [])
        self.assertEqual(p["content_sha256"], content_hash(p))

    def test_claim_without_evidence_is_refused(self):
        def claim(d):
            self.first_admissible(d)["effective_guarantee_demonstrated"] = True
        self.f.edit_json("catalog/valid-configurations-v1.1.0.json", claim)
        self.f.commit("claim")
        p = build(self.f.tmp, None)
        self.assertIn("GUARD-UNBOUND-CLAIM", {x["code"] for x in p["guards"]["findings"]})
        self.assertFalse(p["guards"]["passed"])
        self.assertNotIn("demonstrated", p["summary"]["configurations_by_state"])

    def test_hash_without_artifact_is_not_bound(self):
        def fake(d):
            d["evidence"]["test_results_hash"] = "ab" * 32
            d["demonstration_scope"] = "production-equivalent"
            d["validation"]["effective_guarantee_demonstrated"] = True
        self.f.edit_json("capabilities/v1.0.0/cap-budget-inproc.json", fake)
        self.f.commit("fake hash")
        rec = next(r for r in build(self.f.tmp, None)["guarantee_records"] if r["id"] == "cap-budget-inproc")
        self.assertFalse(rec["evidence_bound"])

    def test_bound_evidence_yields_demonstrated(self):
        log = self.f.tmp / "evidence" / "inproc.log"
        log.parent.mkdir()
        log.write_text("46 passed\n")
        h = hashlib.sha256(log.read_bytes()).hexdigest()

        def bind(d):
            d["evidence"]["test_results_hash"] = h
            d["demonstration_scope"] = "production-equivalent"
            d["validation"]["effective_guarantee_demonstrated"] = True
        self.f.edit_json("capabilities/v1.0.0/cap-budget-inproc.json", bind)

        def claim(d):
            for c in d["configurations"]:
                if c.get("capability_refs") == ["cap-budget-inproc"]:
                    c["effective_guarantee_demonstrated"] = True
        self.f.edit_json("catalog/valid-configurations-v1.1.0.json", claim)
        self.f.commit("bind")
        p = build(self.f.tmp, None)
        self.assertTrue(p["guards"]["passed"], p["guards"]["findings"])
        self.assertGreater(p["summary"]["configurations_by_state"].get("demonstrated", 0), 0)

    def test_loopback_scope_yields_partial(self):
        log = self.f.tmp / "evidence" / "inproc.log"
        log.parent.mkdir()
        log.write_text("loopback\n")
        h = hashlib.sha256(log.read_bytes()).hexdigest()

        def bind(d):
            d["evidence"]["test_results_hash"] = h
            d["demonstration_scope"] = "loopback-test-only"
            d["validation"]["effective_guarantee_demonstrated"] = True
        self.f.edit_json("capabilities/v1.0.0/cap-budget-inproc.json", bind)

        def claim(d):
            for c in d["configurations"]:
                if c.get("capability_refs") == ["cap-budget-inproc"]:
                    c["effective_guarantee_demonstrated"] = "partial"
        self.f.edit_json("catalog/valid-configurations-v1.1.0.json", claim)
        self.f.commit("partial")
        p = build(self.f.tmp, None)
        self.assertGreater(p["summary"]["configurations_by_state"].get("partial", 0), 0)

    def test_admissibility_disagreement_is_refused(self):
        def flip(d):
            c = next(c for c in d["configurations"] if not c["admissible_structural"])
            c["admissible_structural"] = True
        self.f.edit_json("catalog/valid-configurations-v1.1.0.json", flip)
        self.f.commit("flip")
        self.assertIn("CAT-ADMISSIBILITY", self.f.codes())

    def test_dirty_checkout_is_refused(self):
        (self.f.tmp / "catalog" / "hypotheses-v1.yaml").write_text(
            (self.f.tmp / "catalog" / "hypotheses-v1.yaml").read_text() + "\n# edit\n")
        self.assertIn("SRC-DIRTY", self.f.codes())

    def test_wrong_commit_is_refused(self):
        p = build(self.f.tmp, "deadbeef")
        self.assertIn("SRC-COMMIT", {x["code"] for x in p["guards"]["findings"]})

    # --- contract 1.1 -----------------------------------------------------

    def test_content_does_not_depend_on_the_tags_of_the_clone(self):
        before = build(self.f.tmp, None)["content_sha256"]
        git(self.f.tmp, "tag", "v9.9.9")
        git(self.f.tmp, "tag", "-a", "-m", "annotated", "v9.9.8", "HEAD")
        self.assertEqual(build(self.f.tmp, None)["content_sha256"], before)

    def test_lock_tag_must_point_to_the_commit(self):
        p = build(self.f.tmp, None, "v1.0.0")
        self.assertIn("SRC-TAG", {x["code"] for x in p["guards"]["findings"]})
        self.assertIsNone(p["source"]["exact_tag"])
        git(self.f.tmp, "tag", "v1.0.0")
        p = build(self.f.tmp, None, "v1.0.0")
        self.assertTrue(p["guards"]["passed"], p["guards"]["findings"])
        self.assertEqual(p["source"]["exact_tag"], "v1.0.0")

    def test_candidates_are_not_counted_as_vectors(self):
        import yaml
        reg = cand = 0
        for name in ("core", "commit", "extended", "proposals"):
            d = yaml.safe_load((self.f.tmp / "vectors" / f"{name}.yaml").read_text()) or {}
            reg += len(d.get("vectors") or [])
            cand += len(d.get("candidates") or []) + len(d.get("non_validated") or [])
        s = build(self.f.tmp, None)["summary"]
        self.assertEqual(s["vectors_registered"], reg)
        self.assertEqual(s["candidates_total"], cand)
        self.assertEqual(s["vectors_total"], reg + cand)

    @unittest.skipUnless((SOURCE / "VECTORS.md").is_file(), "VECTORS.md not in this checkout")
    def test_index_status_comes_from_vectors_md(self):
        p = build(self.f.tmp, None)
        c1 = next(v for v in p["vectors"] if v["id"] == "C1")
        self.assertEqual(c1["index"]["status"], "IMPLEMENTED")
        idx = self.f.tmp / "VECTORS.md"
        idx.write_text("\n".join(l for l in idx.read_text().splitlines() if not l.startswith("|C1|")) + "\n")
        self.f.commit("drop C1 row")
        p = build(self.f.tmp, None)
        self.assertIsNone(next(v for v in p["vectors"] if v["id"] == "C1")["index"])
        self.assertIn("VEC-INDEX-MISSING", {x["code"] for x in p["guards"]["findings"]})
        self.assertTrue(p["guards"]["passed"])

    def test_claims_policy_is_read_verbatim(self):
        (self.f.tmp / "CLAIMS.md").write_text(
            "# What a result lets you say\n\nTwo sections are in force: \"Quoting\" and \"Part b\", "
            "ratified on 1 May. The rest is not yet in force.\n\n## Quoting\n\n> Assessed against `<v>`.\n\n"
            "## Mixed\n\n**Part a.** Text.\n\n**Part b.** More.\n\n## Later\n\n- That a run\n  is a score.\n")
        self.f.commit("claims")
        cp = build(self.f.tmp, None)["claims_policy"]
        self.assertTrue(cp["present"])
        by = {s["title"]: s for s in cp["sections"]}
        self.assertEqual(by["Quoting"]["status"], "in_force")
        self.assertEqual(by["Quoting"]["quotes"], ["Assessed against <v>."])
        self.assertEqual(by["Mixed"]["status"], "partly_in_force")
        self.assertEqual(by["Mixed"]["parts_in_force"], ["Part b"])
        self.assertEqual(by["Later"]["status"], "draft")
        self.assertEqual(by["Later"]["bullets"], ["That a run is a score."])

    def test_every_file_read_is_listed_with_its_hash(self):
        p = build(self.f.tmp, None)
        paths = {f["path"] for f in p["source"]["files"]}
        self.assertIn("catalog/valid-configurations-v1.1.0.json", paths)
        self.assertIn("vectors/core.yaml", paths)
        for f in p["source"]["files"]:
            self.assertEqual(hashlib.sha256((self.f.tmp / f["path"]).read_bytes()).hexdigest(), f["sha256"])


if __name__ == "__main__":
    unittest.main()
