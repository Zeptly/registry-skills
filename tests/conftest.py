import shutil
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def reg(tmp_path):
    """A disposable copy of the registry that tests may corrupt."""
    for d in ("schemas", "vocab", "skills", "candidates", "registry"):
        shutil.copytree(REPO / d, tmp_path / d)
    return tmp_path


def codes(root, level="error"):
    from zskill.validate import validate
    return sorted({i.code for i in validate(root) if i.level == level})


def load(root, rel):
    return yaml.safe_load((root / rel).read_text())


def save(root, rel, data):
    (root / rel).write_text(yaml.safe_dump(data, sort_keys=False))


def edit_manifest(root, rel, fn):
    m = load(root, rel + "/manifest.yaml")
    fn(m)
    save(root, rel + "/manifest.yaml", m)


def rebind(root, rel, release=True):
    """After changing bundle content: re-issue the governance attestation/approval for the new digest
    (what a maintainer would do), and optionally release."""
    from zskill import registry_ops as ops
    from zskill.validate import Registry
    b = next(x for x in Registry(root).bundles if x.rel == rel)
    dig = b.digest()
    seal = b.directory_seal()
    ap = load(root, rel + "/provenance/approval.yaml")
    ap["subject"].update(version=b.version, digest=dig, digestAlgorithm="zeptly-jcs-v1", directorySeal=seal)
    save(root, rel + "/provenance/approval.yaml", ap)
    edit_manifest(root, rel, lambda m: m["security"]["approvals"][0].update(
        subjectDigest=dig, digestAlgorithm="zeptly-jcs-v1", subjectSeal=seal))
    if release and b.tier == "skills":
        ops.release(root, b.id)


ALG = "zeptly-jcs-v1"


def att(root, rel, ref="evidence://x/1", **over):
    """A well-formed evaluation attestation bound to the bundle's current digest, seal and suite."""
    from zskill.validate import Registry
    b = next(x for x in Registry(root).bundles if x.rel == rel)
    suite = "sha256:" + __import__("hashlib").sha256((b.path / b.spec["evaluation"]["suite"]).read_bytes()).hexdigest()
    a = {"type": "evaluation", "ref": ref, "subjectDigest": b.digest(), "digestAlgorithm": ALG,
         "subjectSeal": b.directory_seal(), "suite": {"id": b.spec["evaluation"]["suite"], "version": b.version, "digest": suite},
         "result": "pass"}
    a.update(over)
    return a
