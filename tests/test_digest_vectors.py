"""Golden vectors for canonical JSON (RFC 8785 / JCS), the line-ending policy, the directory seal and the artifact digest.

Expected values are derived here independently (hashlib over hand-written literals), then pinned as hex constants,
so an accidental change to the algorithm fails loudly. Any other registry claiming the shared algorithm must
reproduce these bytes exactly.
"""
import hashlib

import pytest

from zskill.bundle import Bundle, canonical_json, manifest_projection, normalize_text

sha = lambda b: hashlib.sha256(b).hexdigest()

JCS_VECTORS = [
    ({"b": [1, 2], "a": "x"}, '{"a":"x","b":[1,2]}'),
    # RFC 8785 sorts by UTF-16 code units: U+10000 (D800 DC00) sorts BEFORE U+FF5E, unlike code-point order
    ({"～": 1, "\U00010000": 2}, '{"\U00010000":2,"～":1}'),
    ([0.8, 1.0, -0.0, 100.0, 0.000001, 0.00001, 1.5e15, 3], "[0.8,1,0,100,0.000001,0.00001,1500000000000000,3]"),
    ("€\n\x0f\"\\/\x7f", '"€\\n\\u000f\\"\\\\/\x7f"'),
    ({"n": None, "t": True, "f": False, "e": [], "o": {}}, '{"e":[],"f":false,"n":null,"o":{},"t":true}'),
]


@pytest.mark.parametrize("obj,expected", JCS_VECTORS)
def test_jcs_vectors(obj, expected):
    assert canonical_json(obj) == expected.encode("utf-8")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 2 ** 60, 1e-7, 1e21, {1: "non-string key"}, {"x": {1, 2}}])
def test_jcs_rejects_unrepresentable_values(bad):
    with pytest.raises((ValueError, TypeError)):
        canonical_json(bad)


def test_line_ending_policy_vectors():
    assert normalize_text(b"a\r\nb\rc\nd") == b"a\nb\nc\nd"
    assert normalize_text(b"\r\n\r\n") == b"\n\n"
    assert normalize_text(b"no endings") == b"no endings"
    assert normalize_text("é\r\n".encode()) == "é\n".encode()  # bytes-level: no re-encoding, no BOM handling


MANIFEST = {
    "apiVersion": "registry.zeptly.dev/v1alpha1", "kind": "SkillBlueprint",
    "metadata": {"id": "golden.example", "version": "9.9.9", "registry": "skills", "origin": {"type": "native"},
                 "maturity": "canonical", "lifecycle": "revoked"},
    "spec": {"title": "Golden", "threshold": 0.8, "count": 3, "flag": True, "nothing": None, "uni": "€\U0001F600"},
    "references": [{"registry": "skills", "id": "other", "version": "^1.0.0", "digest": None}],
    "provenance": {"createdAt": "2026-01-01T00:00:00Z", "authors": [], "sourceRefs": [], "transformations": []},
    "security": {"classification": "low", "capabilities": ["cap.a.b"],
                 "approvals": [{"type": "governance", "ref": "bundle:x", "subjectDigest": "sha256:" + "0" * 64}]},
    "attestations": [{"type": "evaluation", "ref": "evidence://x", "subjectDigest": "sha256:" + "0" * 64}],
}
PROJECTION_JCS = (
    '{"apiVersion":"registry.zeptly.dev/v1alpha1","kind":"SkillBlueprint",'
    '"metadata":{"id":"golden.example","origin":{"type":"native"},"registry":"skills"},'
    '"provenance":{"authors":[],"createdAt":"2026-01-01T00:00:00Z","sourceRefs":[],"transformations":[]},'
    '"references":[{"digest":null,"id":"other","registry":"skills","version":"^1.0.0"}],'
    '"security":{"capabilities":["cap.a.b"],"classification":"low"},'
    '"spec":{"count":3,"flag":true,"nothing":null,"threshold":0.8,"title":"Golden","uni":"€\U0001F600"}}'
)
FILES = {  # payload files (LF form); written to disk with mixed line endings below
    "SKILL.md": b"---\nname: golden-example\n---\nbody\n",
    "evals/suite.yaml": b"a: 1\n",
    "examples/e.md": "é\n".encode(),
}
# Pinned golden values (derived independently in test_golden_values_match_independent_derivation)
GOLDEN_SEAL = "sha256:80329458503475064a0c4d679ec5f83b204ac2b5fdc3e0ec2eeba960fd36c1f8"
GOLDEN_DIGEST = "sha256:011e280ea6d7fd2ea0a5def90aca79dd9b422b77f6117f18890b6f037707a777"


def build(tmp_path, eol=b"\n"):
    d = tmp_path / "skills" / "x" / "golden.example"
    for rel, data in {**FILES, "provenance/approval.yaml": b"ignored: true\n", "CHANGELOG.md": b"ignored\n",
                      "examples/.gitkeep": b""}.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data.replace(b"\n", eol))
    return Bundle(root=tmp_path, path=d, tier="skills", manifest=MANIFEST)


def test_projection_is_explicit_include_list():
    assert canonical_json(manifest_projection(MANIFEST)).decode() == PROJECTION_JCS
    p = manifest_projection(MANIFEST)
    assert "version" not in p["metadata"] and "maturity" not in p["metadata"] and "lifecycle" not in p["metadata"]
    assert "attestations" not in p and "approvals" not in p["security"]


def test_golden_values_match_independent_derivation(tmp_path):
    b = build(tmp_path)
    lines = "".join(f"{rel}\0{sha(data)}\n" for rel, data in sorted(FILES.items()))
    seal = "sha256:" + sha(lines.encode())
    doc = '{"directorySeal":"' + seal + '","manifest":' + PROJECTION_JCS + "}"
    digest = "sha256:" + sha(doc.encode("utf-8"))
    assert b.directory_seal() == seal
    assert b.digest() == digest
    assert seal == GOLDEN_SEAL, f"pin GOLDEN_SEAL = {seal!r}"
    assert digest == GOLDEN_DIGEST, f"pin GOLDEN_DIGEST = {digest!r}"


@pytest.mark.parametrize("eol", [b"\n", b"\r\n", b"\r"])
def test_golden_digest_stable_across_line_endings(tmp_path, eol):
    b = build(tmp_path, eol)
    assert b.directory_seal() == GOLDEN_SEAL and b.digest() == GOLDEN_DIGEST


def test_golden_digest_ignores_version_maturity_lifecycle_attestations_approvals(tmp_path):
    import copy
    m = copy.deepcopy(MANIFEST)
    m["metadata"].update(version="0.0.1", maturity="candidate", lifecycle="active")
    m["attestations"] = []
    m["security"]["approvals"] = []
    b = build(tmp_path)
    b.manifest = m
    assert b.digest() == GOLDEN_DIGEST
