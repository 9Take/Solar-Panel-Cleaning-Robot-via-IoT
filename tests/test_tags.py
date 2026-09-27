from pathlib import Path

import pytest
from pydantic import ValidationError

from app.tags import load_tags

REPO_TAGS = Path(__file__).resolve().parents[1] / "config" / "plc_tags.yaml"


def write_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "tags.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_repo_tag_file_loads():
    assert len(load_tags(REPO_TAGS)) >= 1


def test_load_valid_tags(tmp_path):
    tags = load_tags(write_yaml(tmp_path, """
tags:
  start_cmd: {device: M10, dir: write, type: bool}
  battery_v: {device: D100, dir: read, type: int16, scale: 0.1, unit: V}
  energy: {device: D102, dir: read, type: int32}
"""))
    assert tags["start_cmd"].writable
    assert not tags["battery_v"].writable
    assert tags["battery_v"].addr.address == 0x1064
    assert tags["energy"].word_count == 2


def test_unknown_tag_raises(tmp_path):
    tags = load_tags(write_yaml(tmp_path, "tags:\n  a: {device: M1, dir: read, type: bool}\n"))
    with pytest.raises(KeyError, match="Unknown tag"):
        tags["nope"]


@pytest.mark.parametrize(
    "body, match",
    [
        ("x: {device: M1, dir: read, type: int16}", "must have type bool"),
        ("x: {device: D1, dir: read, type: bool}", "cannot be bool"),
        ("x: {device: Y0, dir: write, type: bool}", "never write X/Y"),
        ("x: {device: X0, dir: rw, type: bool}", "never write X/Y"),
        ("x: {device: M1, dir: read, type: bool, typo: 1}", "Extra inputs"),
        ("x: {device: M1, dir: sometimes, type: bool}", "dir"),
    ],
)
def test_invalid_tag_rejected(tmp_path, body, match):
    with pytest.raises((ValidationError, ValueError), match=match):
        load_tags(write_yaml(tmp_path, f"tags:\n  {body}\n"))


def test_overlap_rejected(tmp_path):
    # D100 as int32 uses D100+D101, so D101 collides.
    path = write_yaml(tmp_path, """
tags:
  a: {device: D100, dir: read, type: int32}
  b: {device: D101, dir: read, type: int16}
""")
    with pytest.raises(ValueError, match="overlaps"):
        load_tags(path)
