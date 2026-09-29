"""Minimal semver + range support (no third-party dependency).

Supported ranges: exact ("1.2.3"), caret ("^1.2.3"), tilde ("~1.2.3"),
and space-separated comparators (">=1.2.0 <2.0.0"). Pre-release versions only
satisfy a range that names exactly that version.
"""
from __future__ import annotations

import re
from functools import total_ordering

_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?$")


@total_ordering
class Version:
    def __init__(self, text: str):
        m = _RE.match(text)
        if not m:
            raise ValueError(f"invalid semver: {text!r}")
        self.major, self.minor, self.patch = (int(m.group(i)) for i in (1, 2, 3))
        self.pre = m.group(4)
        self.text = text

    def _key(self):
        # A release sorts after any of its pre-releases.
        pre = (1,) if self.pre is None else (0, *[(0, int(p)) if p.isdigit() else (1, p) for p in self.pre.split(".")])
        return (self.major, self.minor, self.patch, pre)

    def __eq__(self, o):
        return isinstance(o, Version) and self._key() == o._key()

    def __lt__(self, o):
        return self._key() < o._key()

    def __hash__(self):
        return hash(self._key())

    def __repr__(self):
        return f"Version({self.text})"


def bump_level(old: Version, new: Version) -> str:
    """Return 'major' | 'minor' | 'patch' | 'none' describing new relative to old."""
    if new.major != old.major:
        return "major"
    if new.minor != old.minor:
        return "minor"
    if new.patch != old.patch or new.pre != old.pre:
        return "patch"
    return "none"


def satisfies(version: str, rng: str) -> bool:
    v = Version(version)
    rng = rng.strip()
    if rng.startswith("^"):
        lo = Version(rng[1:])
        if lo.major > 0:
            hi = Version(f"{lo.major + 1}.0.0")
        elif lo.minor > 0:
            hi = Version(f"0.{lo.minor + 1}.0")
        else:
            hi = Version(f"0.0.{lo.patch + 1}")
        return _plain(v, lo, hi, lo)
    if rng.startswith("~"):
        lo = Version(rng[1:])
        return _plain(v, lo, Version(f"{lo.major}.{lo.minor + 1}.0"), lo)
    if rng and rng[0] in "<>=":
        ok = True
        for part in rng.split():
            m = re.match(r"^(>=|<=|>|<|=)(.+)$", part)
            if not m:
                raise ValueError(f"invalid range: {rng!r}")
            op, ref = m.group(1), Version(m.group(2))
            ok &= {">=": v >= ref, "<=": v <= ref, ">": v > ref, "<": v < ref, "=": v == ref}[op]
        return ok and v.pre is None
    return v == Version(rng)


def _plain(v: Version, lo: Version, hi: Version, anchor: Version) -> bool:
    if v.pre is not None and v != anchor:
        return False
    return lo <= v < hi


def validate_range(rng: str) -> bool:
    try:
        satisfies("0.0.0", rng)
        return True
    except ValueError:
        return False
