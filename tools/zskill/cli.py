"""zskill command line interface."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import registry_ops as ops
from .bundle import repo_root
from .validate import Registry


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="zskill", description="Zeptly Skills Registry tooling")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="validate every bundle, ledger and cross-reference")
    v.add_argument("--strict", action="store_true", help="treat warnings as errors")
    v.add_argument("--json", action="store_true")

    d = sub.add_parser("digest", help="print content/contract/security digests of a bundle")
    d.add_argument("skill")

    r = sub.add_parser("release", help="append an immutable release entry for a canonical skill's current version")
    r.add_argument("skill")
    r.add_argument("--notes")

    p = sub.add_parser("promote", help="move an approved candidate into skills/, set active, and release it")
    p.add_argument("skill")

    i = sub.add_parser("index", help="(re)generate registry/index.json")
    i.add_argument("--check", action="store_true", help="fail if index.json is stale")

    rs = sub.add_parser("resolve", help="resolve a skill + dependency closure to exact versions and digests")
    rs.add_argument("skill")
    rs.add_argument("--range")

    lc = sub.add_parser("ledger-check", help="verify release ledgers are append-only vs a base git ref")
    lc.add_argument("--base", required=True)

    n = sub.add_parser("new", help="scaffold a new candidate skill")
    n.add_argument("path", help="<domain>/<name>")

    a = ap.parse_args(argv)
    root = repo_root()

    if a.cmd == "validate":
        issues = Registry(root).run()
        errs = [x for x in issues if x.level == "error"]
        warns = [x for x in issues if x.level == "warning"]
        if a.json:
            print(json.dumps([x.__dict__ for x in issues], indent=2))
        else:
            for x in issues:
                print(x)
            n_b = len(Registry(root).bundles)
            print(f"\n{n_b} bundle(s) checked: {len(errs)} error(s), {len(warns)} warning(s)")
        return 1 if errs or (a.strict and warns) else 0
    if a.cmd == "digest":
        b = ops.find(Registry(root), a.skill)
        print(json.dumps({"id": b.id, "version": b.version, "digest": b.digest(),
                          "contract_digest": b.contract_digest(), "security_digest": b.security_digest()}, indent=2))
    elif a.cmd == "release":
        print(ops.release(root, a.skill, notes=a.notes))
    elif a.cmd == "promote":
        print(ops.promote(root, a.skill))
    elif a.cmd == "index":
        text = ops.index_text(root)
        target = root / "registry" / "index.json"
        if a.check:
            if not target.exists() or target.read_text(encoding="utf-8") != text:
                print("registry/index.json is stale; run `zskill index`", file=sys.stderr)
                return 1
            print("index up to date")
        else:
            target.write_text(text, encoding="utf-8")
            print(f"wrote {target.relative_to(root)}")
    elif a.cmd == "resolve":
        print(json.dumps(ops.resolve(root, a.skill, a.range), indent=2))
    elif a.cmd == "ledger-check":
        probs = ops.ledger_check(root, a.base)
        for pr in probs:
            print("ERROR", pr)
        print("ledgers append-only" if not probs else f"{len(probs)} ledger violation(s)")
        return 1 if probs else 0
    elif a.cmd == "new":
        dom, _, name = a.path.partition("/")
        print(f"created {ops.scaffold(root, dom, name).relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
