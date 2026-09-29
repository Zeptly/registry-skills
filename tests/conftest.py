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


def edit_manifest(root, rel, fn):
    p = root / rel / "manifest.yaml"
    m = yaml.safe_load(p.read_text())
    fn(m)
    p.write_text(yaml.safe_dump(m, sort_keys=False))
