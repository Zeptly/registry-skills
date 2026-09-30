"""zskill command line interface.

Exit status (Protocol v0.2 section 9):
  0  success
  1  a valid request that cannot be satisfied (for example an unresolved reference in a lock)
  2  malformed input or validation errors (invalid bundles, stale derived data, append-only violations, bad schemas)

Failures print `error[<code>]: <message>` on stderr (file, path and line are part of the message); no tracebacks.
"""
from __future__ import annotations

import argparse
import json
import sys

import yaml

from . import registry_ops as ops
from .bundle import RegistryError, repo_root
from .validate import Registry


def main(argv=None) -> int:
    try:
        return _main(argv)
    except RegistryError as e:
        print(f"error[{e.code}]: {e}", file=sys.stderr)
        return e.exit_code
    except (OSError, UnicodeError, yaml.YAMLError) as e:
        print(f"error[io]: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="zskill", description="Zeptly Skills Registry tooling")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="validate every bundle, ledger and cross-reference (exit 2 on errors)")
    v.add_argument("--strict", action="store_true", help="treat warnings as errors")
    v.add_argument("--json", action="store_true")

    d = sub.add_parser("digest", help="print digests and directory seal of a bundle")
    d.add_argument("skill")

    r = sub.add_parser("release", help="append an immutable release entry for a canonical skill's current version")
    r.add_argument("skill")
    r.add_argument("--notes")

    p = sub.add_parser("promote", help="move an approved candidate into skills/, set canonical, and release it")
    p.add_argument("skill")

    i = sub.add_parser("index", help="(re)generate the deterministic indexes (production and synthetic are separate files)")
    i.add_argument("--check", action="store_true", help="fail (exit 2) if an index file is stale")

    lc2 = sub.add_parser("lifecycle", help="append a lifecycle event (active|deprecated|revoked) for a version")
    lc2.add_argument("skill")
    lc2.add_argument("version")
    lc2.add_argument("state", choices=["active", "deprecated", "revoked"])
    lc2.add_argument("--reason", required=True)
    lc2.add_argument("--replaced-by", help="<id>@<version>")
    lc2.add_argument("--sunset", help="YYYY-MM-DD")

    rs = sub.add_parser("resolve", help="produce a RuntimeLock for a skill from explicit indexes (exit 1 if incomplete)")
    rs.add_argument("skill")
    rs.add_argument("--domain", choices=["production", "synthetic"], default="production")
    rs.add_argument("--peer-index", action="append", default=[], metavar="FILE",
                    help="explicit peer registry index (repeatable); nothing is fetched over the network")
    rs.add_argument("--allow-candidates", action="store_true", help="explicit opt-in: candidates may be locked and resolved")

    lc = sub.add_parser("ledger-check", help="verify release ledgers/lifecycle overlays are append-only vs a base git ref")
    lc.add_argument("--base", required=True)

    n = sub.add_parser("new", help="scaffold a new candidate skill")
    n.add_argument("path", help="<domain>/<id>")
    n.add_argument("--synthetic", action="store_true", help="create under synthetic/ with the reserved example. id prefix")

    a = ap.parse_args(argv)
    root = repo_root()

    if a.cmd == "validate":
        reg = Registry(root)
        issues = reg.run()
        errs = [x for x in issues if x.level == "error"]
        warns = [x for x in issues if x.level == "warning"]
        if a.json:
            print(json.dumps([x.__dict__ for x in issues], indent=2))
        else:
            for x in issues:
                print(x)
            print(f"\n{len(reg.bundles)} bundle(s) checked: {len(errs)} error(s), {len(warns)} warning(s)")
        return 2 if errs or (a.strict and warns) else 0
    if a.cmd == "digest":
        b = ops.find(Registry(root), a.skill)
        print(json.dumps({"id": b.id, "version": b.version, "digestAlgorithm": "zeptly-jcs-v1", "digest": b.digest(),
                          "directorySeal": b.directory_seal(), "contract_digest": b.contract_digest(),
                          "security_digest": b.security_digest()}, indent=2))
    elif a.cmd == "release":
        print(ops.release(root, a.skill, notes=a.notes))
    elif a.cmd == "promote":
        print(ops.promote(root, a.skill))
    elif a.cmd == "index":
        rc = 0
        for dom in ("production", "synthetic"):
            target = ops.index_path(root, dom)
            text = ops.index_text(root, dom)            # raises RegistryError on invalid bundles / schema mismatch
            empty = not json.loads(text)["entries"]
            if a.check:
                if empty and not target.exists():
                    continue
                if not target.exists() or target.read_text(encoding="utf-8") != text:
                    print(f"error[index-stale]: {target.relative_to(root)} is stale; run `zskill index`", file=sys.stderr)
                    rc = 2
            elif empty and dom == "synthetic":
                if target.exists():
                    target.unlink()
            else:
                target.write_text(text, encoding="utf-8")
                print(f"wrote {target.relative_to(root)}")
        if a.check and rc == 0:
            print("indexes up to date")
        return rc
    elif a.cmd == "lifecycle":
        print(ops.set_lifecycle(root, a.skill, a.version, a.state, a.reason, a.replaced_by, a.sunset))
    elif a.cmd == "resolve":
        lock = ops.resolve(root, a.skill, domain=a.domain, peer_indexes=a.peer_index, allow_candidates=a.allow_candidates)
        print(json.dumps(lock, indent=2))
        if not lock["complete"]:
            for e in lock["entries"]:
                if e["status"] == "unresolved":
                    u, q = e["unresolved"], e["requested"]
                    print(f"error[{u['code']}]: {q['registry']}:{q['id']}@{q['version']}: {u['message']}", file=sys.stderr)
            return 1
    elif a.cmd == "ledger-check":
        probs = ops.ledger_check(root, a.base)
        for pr in probs:
            print("ERROR", pr)
        print("ledgers append-only" if not probs else f"{len(probs)} ledger violation(s)")
        return 2 if probs else 0
    elif a.cmd == "new":
        dom, _, name = a.path.partition("/")
        print(f"created {ops.scaffold(root, dom, name, 'synthetic' if a.synthetic else 'candidates').relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
