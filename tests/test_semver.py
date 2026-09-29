import pytest
from zskill.semver import Version, satisfies, bump_level, validate_range


def test_ordering():
    assert Version("1.0.0") < Version("1.0.1") < Version("1.1.0") < Version("2.0.0")
    assert Version("1.0.0-rc.1") < Version("1.0.0")


@pytest.mark.parametrize("v,r,ok", [
    ("1.2.3", "^1.0.0", True), ("2.0.0", "^1.0.0", False), ("0.2.5", "^0.2.1", True), ("0.3.0", "^0.2.1", False),
    ("1.2.9", "~1.2.0", True), ("1.3.0", "~1.2.0", False), ("1.5.0", ">=1.2.0 <2.0.0", True),
    ("2.0.0", ">=1.2.0 <2.0.0", False), ("1.0.0", "1.0.0", True), ("1.0.1", "1.0.0", False),
    ("1.1.0-rc.1", "^1.0.0", False), ("1.1.0-rc.1", "1.1.0-rc.1", True),
])
def test_satisfies(v, r, ok):
    assert satisfies(v, r) is ok


def test_bump_level():
    assert bump_level(Version("1.0.0"), Version("2.0.0")) == "major"
    assert bump_level(Version("1.0.0"), Version("1.1.0")) == "minor"
    assert bump_level(Version("1.0.0"), Version("1.0.1")) == "patch"


def test_validate_range():
    assert validate_range("^1.0.0") and not validate_range("latest")
