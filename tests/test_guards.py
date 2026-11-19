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


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


class Fixture:
    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp())
        for p in PATHS:
            shutil.copytree(SOURCE / p, self.tmp / p)
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


if __name__ == "__main__":
    unittest.main()
