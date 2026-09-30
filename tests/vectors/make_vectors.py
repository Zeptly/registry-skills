#!/usr/bin/env python3
"""Regenerates zeptly-jcs-v1.skills-generated.json from the zskill implementation and cross-checks every value against
the independent reference implementation. `tests/test_vectors.py` fails if the committed file differs from this output.

    python3 tests/vectors/make_vectors.py            # rewrite the file
    python3 tests/vectors/make_vectors.py --check    # exit 1 if the committed file is stale
"""
import copy
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "tools"))
import reference_zeptly_jcs_v1 as R  # noqa: E402
from zskill.bundle import Bundle, BundleError, canonical_json  # noqa: E402
from zskill.yamlsubset import load_yaml, load_yaml_text  # noqa: E402
from zskill.errors import CanonicalizationError, YamlError  # noqa: E402

OUT = HERE / "zeptly-jcs-v1.skills-generated.json"
hx = lambda b: b.hex()

JCS = [
    ("key-order", '{"b":[1,2],"a":"x"}', '{"a":"x","b":[1,2]}'),
    ("nested-sort", '{"z":{"b":1,"a":2},"a":[{"d":1,"c":2}]}', '{"a":[{"c":2,"d":1}],"z":{"a":2,"b":1}}'),
    ("utf16-order-not-codepoint", '{"\\uff5e":1,"\\ud800\\udc00":2}', '{"\U00010000":2,"\uff5e":1}'),
    ("utf16-order-bmp-vs-supplementary-mixed", '{"\\ue000":1,"\\ud83d\\ude00":2,"\\u20ac":3}', '{"\u20ac":3,"\U0001F600":2,"\ue000":1}'),
    ("arrays-keep-order", '[3,1,2,{"b":1,"a":2}]', '[3,1,2,{"a":2,"b":1}]'),
    ("literals-and-empties", '{"n":null,"t":true,"f":false,"e":[],"o":{}}', '{"e":[],"f":false,"n":null,"o":{},"t":true}'),
    ("string-escapes", '"\\u20ac\\n\\u000f\\"\\\\/\\u007f\\u2028"', '"\u20ac\\n\\u000f\\"\\\\/\x7f\u2028"'),
    ("control-chars-lowercase-hex", '"\\u001f\\b\\t\\f\\r"', '"\\u001f\\b\\t\\f\\r"'),
    ("unicode-no-normalization-nfc", '"\\u00e9"', '"\u00e9"'),
    ("unicode-no-normalization-nfd", '"e\\u0301"', '"e\u0301"'),
    ("emoji-literal", '"\\ud83d\\ude00"', '"\U0001F600"'),
    ("numbers-decimal-forms", '[0.8,1.0,-0.0,100.0,0.000001,0.00001,0.5,123.456,3]', "[0.8,1,0,100,0.000001,0.00001,0.5,123.456,3]"),
    ("numbers-exponent-forms", '[1e-7,1e21,1e20,1.5e300,5e-324,1.7976931348623157e308,1e-6,123456789012345680000.0]',
     "[1e-7,1e+21,100000000000000000000,1.5e+300,5e-324,1.7976931348623157e+308,0.000001,123456789012345680000]"),
    ("numbers-shortest-roundtrip", '[0.30000000000000004,4.35,2.5e-5]', "[0.30000000000000004,4.35,0.000025]"),
    ("integers-safe-range", '[9007199254740991,-9007199254740991,0]', "[9007199254740991,-9007199254740991,0]"),
]
JCS_REJECT = [
    ("unsafe-integer-positive", "[9007199254740992]", "unsafe-integer"),
    ("unsafe-integer-negative", "[-9007199254740992]", "unsafe-integer"),
    ("non-finite-overflow", "[1e999]", "non-finite"),
    ("lone-surrogate-value", '"\\ud800"', "lone-surrogate"),
    ("lone-surrogate-key", '{"\\udc00":1}', "lone-surrogate"),
]
PAYLOAD_TEXT = [
    ("ok-lf-utf8", "caf\u00e9\nline 2\n".encode(), "ok"), ("ok-empty", b"", "ok"), ("ok-no-trailing-newline", b"abc", "ok"),
    ("reject-crlf", b"a\r\nb\n", "payload-line-endings"), ("reject-lone-cr", b"a\rb", "payload-line-endings"),
    ("reject-bom", b"\xef\xbb\xbfabc\n", "payload-bom"), ("reject-nul", b"a\x00b\n", "payload-nul"),
    ("reject-invalid-utf8", b"caf\xe9\n", "file-not-text"),
]
MANIFEST = {
    "apiVersion": "registry.zeptly.dev/v1alpha1", "kind": "SkillBlueprint",
    "metadata": {"id": "golden.example", "version": "9.9.9", "registry": "skills", "origin": {"type": "native"},
                 "maturity": "canonical", "lifecycle": "revoked"},
    "spec": {"title": "Golden", "threshold": 0.8, "count": 3, "flag": True, "nothing": None, "uni": "\u20ac\U0001F600"},
    "references": [{"registry": "skills", "id": "other", "version": "^1.0.0", "digest": None}],
    "provenance": {"createdAt": "2026-01-01T00:00:00Z", "authors": [], "sourceRefs": [], "transformations": []},
    "security": {"classification": "low", "capabilities": ["cap.a.b"],
                 "approvals": [{"type": "governance", "ref": "bundle:x", "subjectDigest": "sha256:" + "0" * 64,
                                "digestAlgorithm": "zeptly-jcs-v1"}]},
    "attestations": [{"type": "evaluation", "ref": "evidence://x", "subjectDigest": "sha256:" + "0" * 64,
                      "digestAlgorithm": "zeptly-jcs-v1"}],
}
FILES = {
    "SKILL.md": b"---\nname: golden-example\n---\nbody\n",
    "evals/suite.yaml": b"a: 1\n",
    "examples/e.md": "\u00e9\n".encode(),
    "examples/a-b.md": b"a-b\n", "examples/a.b.md": b"a.b\n", "examples/a2.md": b"a2\n", "examples/B.md": b"B\n",
    "examples/manifest.yaml": b"nested manifest.yaml IS payload (only the root metadata file is excluded)\n",
    "evals/.gitkeep": b"",
    "CHANGELOG.md": b"top-level CHANGELOG.md IS payload in v0.2\n",
    "provenance/approval.yaml": b"excluded: mutable governance record\n",
    "provenance/eval-reports/1.0.0.yaml": b"excluded: mutable record\n",
}
MUT = [
    ("version-bump", dict(set=[dict(path=["metadata", "version"], value="0.0.1")])),
    ("maturity-and-lifecycle", dict(set=[dict(path=["metadata", "maturity"], value="candidate"), dict(path=["metadata", "lifecycle"], value="active")])),
    ("attestations-replaced", dict(set=[dict(path=["attestations"], value=[])])),
    ("approvals-removed", dict(delete=[dict(path=["security", "approvals"])])),
    ("unlisted-top-level-field-ignored", dict(set=[dict(path=["x-extra"], value={"a": 1})])),
    ("unlisted-metadata-field-ignored", dict(set=[dict(path=["metadata", "note"], value="n")])),
    ("manifest-key-order", dict(reverseTopLevelKeys=True)),
    ("origin-type", dict(set=[dict(path=["metadata", "origin", "type"], value="upstream-seed")])),
    ("identity-id", dict(set=[dict(path=["metadata", "id"], value="golden.other")])),
    ("spec-title", dict(set=[dict(path=["spec", "title"], value="Different")])),
    ("spec-runtime-approval-requirement", dict(set=[dict(path=["spec", "approvalRequired"], value=True)])),
    ("references-version", dict(set=[dict(path=["references"], value=[{"registry": "skills", "id": "other", "version": "^2.0.0", "digest": None}])])),
    ("provenance-created-at", dict(set=[dict(path=["provenance", "createdAt"], value="2027-01-01T00:00:00Z")])),
    ("classification", dict(set=[dict(path=["security", "classification"], value="moderate")])),
    ("capabilities", dict(set=[dict(path=["security", "capabilities"], value=["cap.a.b", "cap.c.d"])])),
    ("payload-skill-md-not-in-artifact-digest", dict(putFiles=[dict(path="SKILL.md", contentHex=hx(b"---\nname: golden-example\n---\nchanged\n"))])),
    ("provenance-dir-file", dict(putFiles=[dict(path="provenance/approval.yaml", contentHex=hx(b"different: true\n"))])),
    ("top-level-changelog-is-payload", dict(putFiles=[dict(path="CHANGELOG.md", contentHex=hx(b"different\n"))])),
    ("nested-manifest-yaml-is-payload", dict(putFiles=[dict(path="examples/manifest.yaml", contentHex=hx(b"different\n"))])),
    ("gitkeep-added", dict(putFiles=[dict(path="examples/.gitkeep", contentHex="")])),
    ("payload-file-removed", dict(removeFiles=["examples/B.md"])),
    ("reject-crlf-payload", dict(putFiles=[dict(path="examples/e.md", contentHex=hx(b"a\r\nb\n"))], expectReject="payload-line-endings")),
    ("reject-bom-payload", dict(putFiles=[dict(path="examples/e.md", contentHex=hx(b"\xef\xbb\xbfx\n"))], expectReject="payload-bom")),
    ("reject-case-collision", dict(putFiles=[dict(path="examples/E.md", contentHex=hx(b"x\n"))], expectReject="case-collision")),
]
PARSER = [  # (name, yaml text, expected JSON value | {"reject": code})
    ("plain-types", "a: 1\nb: [true, null, 0.85, 1.5, x]\nc: -3\n", {"a": 1, "b": [True, None, 0.85, 1.5, "x"], "c": -3}),
    ("yes-no-on-off-are-strings", "a: [yes, no, on, off, y, n, Yes, NO]\n", {"a": ["yes", "no", "on", "off", "y", "n", "Yes", "NO"]}),
    ("true-false-forms", "a: [true, True, TRUE, false, False, FALSE]\n", {"a": [True, True, True, False, False, False]}),
    ("timestamps-stay-strings", "a: 2026-09-29\nb: 2026-09-29T00:00:00Z\n", {"a": "2026-09-29", "b": "2026-09-29T00:00:00Z"}),
    ("version-like-strings", "a: 1.0.0\nb: 1.0.0-rc.1\nc: v1\n", {"a": "1.0.0", "b": "1.0.0-rc.1", "c": "v1"}),
    ("json-exponent-is-float", "a: 1e3\nb: 1.5E-2\n", {"a": 1000.0, "b": 0.015}),
    ("quoted-numbers-are-strings", "a: '010'\nb: \"0x1F\"\nc: '1'\n", {"a": "010", "b": "0x1F", "c": "1"}),
    ("explicit-str-tag", "a: !!str 123\n", {"a": "123"}),
    ("explicit-int-tag", "a: !!int 12\n", {"a": 12}),
    ("empty-document", "", None),
    ("comments-ignored", "# c\na: 1 # trailing\n", {"a": 1}),
    ("flow-and-block", "a: {b: [1, 2], c: d}\ne:\n  - f\n", {"a": {"b": [1, 2], "c": "d"}, "e": ["f"]}),
    ("safe-integer-max", "a: 9007199254740991\nb: -9007199254740991\n", {"a": 9007199254740991, "b": -9007199254740991}),
    ("reject-duplicate-key", "a: 1\na: 2\n", {"reject": "duplicate-key"}),
    ("reject-duplicate-key-nested", "a:\n  b: 1\n  b: 2\n", {"reject": "duplicate-key"}),
    ("reject-anchor", "a: &x 1\n", {"reject": "anchor"}),
    ("reject-alias", "a: *x\n", {"reject": "alias"}),
    ("reject-merge-key", "a: {<<: {b: 1}}\n", {"reject": "merge-key"}),
    ("reject-non-string-key-int", "1: a\n", {"reject": "non-string-key"}),
    ("reject-non-string-key-bool", "true: a\n", {"reject": "non-string-key"}),
    ("reject-non-string-key-null", "~: a\n", {"reject": "non-string-key"}),
    ("reject-multiple-documents", "a: 1\n---\nb: 2\n", {"reject": "multiple-documents"}),
    ("reject-unsupported-tag-binary", "a: !!binary abc\n", {"reject": "unsupported-tag"}),
    ("reject-unsupported-tag-python", "a: !!python/object:os.system x\n", {"reject": "unsupported-tag"}),
    ("reject-unsupported-tag-custom", "a: !custom x\n", {"reject": "unsupported-tag"}),
    ("reject-unsafe-integer", "a: 9007199254740992\n", {"reject": "unsafe-integer"}),
    ("reject-unsafe-integer-negative", "a: -9007199254740992\n", {"reject": "unsafe-integer"}),
    ("reject-non-finite-inf", "a: .inf\n", {"reject": "ambiguous-scalar"}),
    ("reject-non-finite-nan", "a: .nan\n", {"reject": "ambiguous-scalar"}),
    ("reject-non-finite-overflow", "a: 1e999\n", {"reject": "non-finite"}),
    ("reject-leading-zero-int", "a: 010\n", {"reject": "ambiguous-scalar"}),
    ("reject-plus-int", "a: +1\n", {"reject": "ambiguous-scalar"}),
    ("reject-hex", "a: 0x1F\n", {"reject": "ambiguous-scalar"}),
    ("reject-octal", "a: 0o17\n", {"reject": "ambiguous-scalar"}),
    ("reject-bare-fraction", "a: .5\n", {"reject": "ambiguous-scalar"}),
    ("reject-trailing-dot", "a: 1.\n", {"reject": "ambiguous-scalar"}),
    ("reject-explicit-bool-yes", "a: !!bool yes\n", {"reject": "invalid-scalar"}),
    ("reject-bom", "\ufeffa: 1\n", {"reject": "bom"}),
    ("reject-nul-escape", 'a: "x\\0y"\n', {"reject": "nul"}),
    ("reject-lone-surrogate-escape", 'a: "\\uD800"\n', {"reject": "lone-surrogate"}),
    ("reject-syntax", "a: [1\n", {"reject": "syntax"}),
]
PARSER_BYTES = [  # (name, raw bytes, reject code) -- rejected before parsing
    ("reject-invalid-utf8", b"a: caf\xe9\n", "invalid-utf8"),
    ("reject-bom-bytes", b"\xef\xbb\xbfa: 1\n", "bom"),
    ("reject-nul-byte", b"a: 1\x00\n", "nul"),
]


def _is_reject(x):
    return isinstance(x, dict) and set(x) == {"reject"}


def zskill_seal(manifest, files):
    d = Path(tempfile.mkdtemp()) / "skills" / "x" / "golden.example"
    for p, b in files.items():
        q = d / p
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_bytes(b)
    b = Bundle(root=d.parents[2], path=d, tier="skills", manifest=manifest)
    return b.directory_seal(), b.digest(), b


def both(manifest, files):
    ref_seal, ref_digest = R.directory_seal(manifest, files), R.artifact_digest(manifest)
    zs, zd, _ = zskill_seal(manifest, files)
    assert (ref_seal, ref_digest) == (zs, zd), ((ref_seal, ref_digest), (zs, zd))
    return ref_seal, ref_digest


def build():
    base_files = dict(FILES)
    seal0, dig0 = both(MANIFEST, base_files)
    out = []
    for name, mu in MUT:
        m, files = R.apply_mutation(MANIFEST, base_files, mu)
        entry = dict(mu)
        entry["name"] = name
        if "expectReject" in mu:
            for fn in (lambda: R.directory_seal(m, files),):
                try:
                    fn()
                    raise SystemExit(f"reference accepted {name}")
                except R.Reject as r:
                    assert r.code == mu["expectReject"], (name, r.code)
            try:
                zskill_seal(m, files)
                raise SystemExit(f"zskill accepted {name}")
            except BundleError as e:
                # path-level case collisions need both files on disk; the BundleError code must match
                assert e.code == mu["expectReject"], (name, e.code)
        else:
            s, d = both(m, files)
            entry.update(expectDigest="same" if d == dig0 else "different", expectSeal="same" if s == seal0 else "different",
                         digest=d, seal=s)
        out.append(entry)
    V = {
        "contract": "zeptly-registry-protocol-v0.2 / digestAlgorithm zeptly-jcs-v1",
        "digestAlgorithm": "zeptly-jcs-v1",
        "status": "Skills-registry generated vectors. The shared protocol vector distribution was not supplied to this registry; "
                  "these vectors cover the section 5 categories of the v0.2 amendment and must be reconciled with the shared set.",
        "interpretations": [
            "seal payload[] entries are objects {path, sha256} with sha256 written as 'sha256:<hex>'",
            "payload = every file except the root manifest.yaml and provenance/**; CHANGELOG.md, .gitkeep and nested manifest.yaml are payload",
            "the seal includes version; the artifact digest does not",
            "payload text rejects NUL in addition to BOM, CR and invalid UTF-8",
        ],
        "jcs": [dict(name=n, input=i, expect=e) for n, i, e in JCS] + [dict(name=n, input=i, reject=c) for n, i, c in JCS_REJECT],
        "payloadText": [dict(name=n, inputHex=hx(b), expect=e) for n, b, e in PAYLOAD_TEXT],
        "parser": [dict(name=n, yaml=y, expect=e) for n, y, e in PARSER] + [dict(name=n, inputHex=hx(b), expect={"reject": c}) for n, b, c in PARSER_BYTES],
        "bundle": {
            "manifest": MANIFEST,
            "files": [dict(path=p, contentHex=hx(b)) for p, b in sorted(FILES.items())],
            "expected": {"projectionJcs": R.jcs(R.projection(MANIFEST)),
                         "artifactDigest": dig0,
                         "sealDocumentJcs": R.jcs(R.seal_document(MANIFEST, FILES)),
                         "directorySeal": seal0},
            "mutations": out,
        },
    }
    # cross-check JCS vectors against zskill
    for c in V["jcs"]:
        if "reject" in c:
            try:
                canonical_json(json.loads(c["input"]))
                raise SystemExit("zskill accepted " + c["name"])
            except CanonicalizationError as e:
                assert e.code == c["reject"], (c["name"], e.code)
        else:
            assert canonical_json(json.loads(c["input"])).decode() == c["expect"], c["name"]
    for c in V["payloadText"]:
        from zskill.bundle import payload_text_problem
        got = (payload_text_problem(bytes.fromhex(c["inputHex"])) or ("ok",))[0]
        assert got == c["expect"], (c["name"], got)
    for c in V["parser"]:
        try:
            if "inputHex" in c:
                tmp = Path(tempfile.mkdtemp()) / "vector.yaml"
                tmp.write_bytes(bytes.fromhex(c["inputHex"]))
                got = load_yaml(tmp)
            else:
                got = load_yaml_text(c["yaml"], "vector.yaml")
            assert not _is_reject(c["expect"]), (c["name"], "accepted")
            assert got == c["expect"], (c["name"], got)
        except YamlError as e:
            assert _is_reject(c["expect"]) and e.code == c["expect"]["reject"], (c["name"], e.code)
    return json.dumps(V, indent=1, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    text = build()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("vectors are stale", file=sys.stderr)
            sys.exit(1)
        print("vectors up to date")
    else:
        OUT.write_text(text, encoding="utf-8")
        print("wrote", OUT.name)
