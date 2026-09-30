#!/usr/bin/env python3
"""Independent reference implementation of digestAlgorithm `zeptly-jcs-v1` (Zeptly Registry Protocol v0.2).

Standard library only; it does NOT import zskill. Manifests in the vectors are already-parsed JSON, so this verifier
does not depend on any YAML parser. Usage:  python3 reference_zeptly_jcs_v1.py <vectors.json>
"""
import hashlib
import json
import math
import sys

SAFE_INT = 2 ** 53 - 1


class Reject(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ------------------------------------------------------------------ RFC 8785 JCS
def es_number(x: float) -> str:
    """ECMAScript Number::toString, derived from repr() by string manipulation (independent of the zskill code)."""
    if x == 0:
        return "0"
    sign, r = ("-", repr(-x)) if x < 0 else ("", repr(x))
    mant, _, exp = r.partition("e")
    e10 = int(exp) if exp else 0
    ip, _, fp = mant.partition(".")
    digits = (ip + fp).lstrip("0")
    lead = len(ip + fp) - len((ip + fp).lstrip("0"))
    n = len(ip) - lead + e10            # position of the decimal point relative to the first significant digit
    digits = digits.rstrip("0")
    k = len(digits)
    if k <= n <= 21:
        out = digits + "0" * (n - k)
    elif 0 < n <= 21:
        out = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        out = digits[0] + ("." + digits[1:] if k > 1 else "") + "e" + ("+" if e >= 0 else "-") + str(abs(e))
    return sign + out


def jcs(v) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, str):
        if any(0xD800 <= ord(c) <= 0xDFFF for c in v):
            raise Reject("lone-surrogate")
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, int):
        if abs(v) > SAFE_INT:
            raise Reject("unsafe-integer")
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise Reject("non-finite")
        return es_number(v)
    if isinstance(v, list):
        return "[" + ",".join(jcs(x) for x in v) + "]"
    if isinstance(v, dict):
        if not all(isinstance(k, str) for k in v):
            raise Reject("non-string-key")
        for k in v:
            jcs(k)  # rejects lone surrogates before sorting
        return "{" + ",".join(jcs(k) + ":" + jcs(v[k]) for k in sorted(v, key=lambda k: k.encode("utf-16-be"))) + "}"
    raise Reject("unsupported-type")


def sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------------ digest and seal
def projection(m: dict) -> dict:
    meta, sec = m.get("metadata") or {}, m.get("security") or {}
    return {"apiVersion": m.get("apiVersion"), "kind": m.get("kind"),
            "metadata": {"id": meta.get("id"), "registry": meta.get("registry"), "origin": meta.get("origin")},
            "spec": m.get("spec"), "references": m.get("references"), "provenance": m.get("provenance"),
            "security": {"classification": sec.get("classification"), "capabilities": sec.get("capabilities")}}


def artifact_digest(manifest: dict) -> str:
    return sha(jcs(projection(manifest)).encode("utf-8"))


def payload_problem(data: bytes):
    if data.startswith(b"\xef\xbb\xbf"):
        return "payload-bom"
    try:
        t = data.decode("utf-8")
    except UnicodeDecodeError:
        return "file-not-text"
    if "\x00" in t:
        return "payload-nul"
    if "\r" in t:
        return "payload-line-endings"
    return None


def seal_document(manifest: dict, files: dict) -> dict:
    """files: {posix relative path: bytes}. Payload = everything except the root manifest.yaml and provenance/**."""
    seen = {}
    for p in files:
        if p.casefold() in seen and seen[p.casefold()] != p:
            raise Reject("case-collision")
        seen[p.casefold()] = p
    payload = []
    for p in sorted(files, key=lambda s: [ord(c) for c in s]):
        if p.split("/")[0] == "provenance" or p == "manifest.yaml":
            continue
        prob = payload_problem(files[p])
        if prob:
            raise Reject(prob)
        payload.append({"path": p, "sha256": sha(files[p])})
    meta = manifest.get("metadata") or {}
    return {"registry": meta.get("registry"), "id": meta.get("id"), "version": meta.get("version"), "payload": payload}


def directory_seal(manifest: dict, files: dict) -> str:
    return sha(jcs(seal_document(manifest, files)).encode("utf-8"))


# ------------------------------------------------------------------ vector runner
def set_path(obj, path, value):
    for k in path[:-1]:
        obj = obj.setdefault(k, {})
    obj[path[-1]] = value


def del_path(obj, path):
    for k in path[:-1]:
        obj = obj.get(k, {})
    obj.pop(path[-1], None)


def apply_mutation(base_manifest, base_files, mu):
    m = json.loads(json.dumps(base_manifest))
    if mu.get("reverseTopLevelKeys"):
        m = {k: m[k] for k in reversed(list(m))}
    files = dict(base_files)
    for op in mu.get("set", []):
        set_path(m, op["path"], op["value"])
    for op in mu.get("delete", []):
        del_path(m, op["path"])
    for f in mu.get("putFiles", []):
        files[f["path"]] = bytes.fromhex(f["contentHex"])
    for p in mu.get("removeFiles", []):
        files.pop(p, None)
    return m, files


def run(vectors: dict):
    """Yield (name, got, want) triples."""
    for c in vectors["jcs"]:
        name = "jcs/" + c["name"]
        if "reject" in c:
            try:
                jcs(json.loads(c["input"]))
                yield name, "accepted", "reject:" + c["reject"]
            except Reject as r:
                yield name, "reject:" + r.code, "reject:" + c["reject"]
        else:
            yield name, jcs(json.loads(c["input"])), c["expect"]
    for c in vectors["payloadText"]:
        got = payload_problem(bytes.fromhex(c["inputHex"])) or "ok"
        yield "payloadText/" + c["name"], got, c["expect"]
    B = vectors["bundle"]
    base_files = {f["path"]: bytes.fromhex(f["contentHex"]) for f in B["files"]}
    yield "bundle/projection", jcs(projection(B["manifest"])), B["expected"]["projectionJcs"]
    yield "bundle/artifactDigest", artifact_digest(B["manifest"]), B["expected"]["artifactDigest"]
    yield "bundle/sealDocument", jcs(seal_document(B["manifest"], base_files)), B["expected"]["sealDocumentJcs"]
    yield "bundle/directorySeal", directory_seal(B["manifest"], base_files), B["expected"]["directorySeal"]
    for mu in B["mutations"]:
        m, files = apply_mutation(B["manifest"], base_files, mu)
        d = artifact_digest(m)
        try:
            s = directory_seal(m, files)
        except Reject as r:
            yield f"mutation/{mu['name']}/reject", r.code, mu.get("expectReject", "none")
            continue
        yield f"mutation/{mu['name']}/digest", "same" if d == B["expected"]["artifactDigest"] else "different", mu["expectDigest"]
        yield f"mutation/{mu['name']}/seal", "same" if s == B["expected"]["directorySeal"] else "different", mu["expectSeal"]
        yield f"mutation/{mu['name']}/digestValue", d, mu["digest"]
        yield f"mutation/{mu['name']}/sealValue", s, mu["seal"]


def main(path):
    vectors = json.load(open(path, encoding="utf-8"))
    bad = 0
    for name, got, want in run(vectors):
        ok = got == want
        bad += not ok
        print(("ok   " if ok else "FAIL ") + name + ("" if ok else f"\n     got  {got!r}\n     want {want!r}"))
    print("\nALL VECTORS PASS" if not bad else f"\n{bad} FAILURE(S)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "zeptly-jcs-v1.skills-generated.json"))
