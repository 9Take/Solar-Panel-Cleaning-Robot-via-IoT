"""PLC tag map: load and validate config/plc_tags.yaml."""

from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.delta import Area, ModbusAddress, to_modbus


class Direction(str, Enum):
    READ = "read"
    WRITE = "write"
    RW = "rw"


class TagType(str, Enum):
    BOOL = "bool"
    INT16 = "int16"
    UINT16 = "uint16"
    INT32 = "int32"
    UINT32 = "uint32"
    FLOAT32 = "float32"


_WORD_COUNT = {
    TagType.INT16: 1, TagType.UINT16: 1,
    TagType.INT32: 2, TagType.UINT32: 2, TagType.FLOAT32: 2,
}


class Tag(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    device: str
    dir: Direction
    type: TagType
    scale: float = 1.0
    unit: str = ""
    desc: str = ""
    addr: ModbusAddress = Field(default=None, exclude=True)  # filled by validator

    @model_validator(mode="before")
    @classmethod
    def _resolve_address(cls, data: dict) -> dict:
        data = dict(data)
        data["addr"] = to_modbus(str(data.get("device", "")))
        return data

    @model_validator(mode="after")
    def _check_consistency(self) -> "Tag":
        if self.addr.is_bit and self.type is not TagType.BOOL:
            raise ValueError(f"{self.name}: bit device {self.device} must have type bool")
        if not self.addr.is_bit and self.type is TagType.BOOL:
            raise ValueError(f"{self.name}: register device {self.device} cannot be bool")
        # Pi never writes X (inputs) or Y (outputs directly); commands go through M/D request bits.
        if self.device.upper().startswith(("X", "Y")) and self.dir is not Direction.READ:
            raise ValueError(f"{self.name}: {self.device} must be dir: read (never write X/Y)")
        return self

    @property
    def writable(self) -> bool:
        return self.dir in (Direction.WRITE, Direction.RW)

    @property
    def word_count(self) -> int:
        return 1 if self.addr.is_bit else _WORD_COUNT[self.type]


class TagMap(BaseModel):
    tags: dict[str, Tag]

    def __getitem__(self, name: str) -> Tag:
        try:
            return self.tags[name]
        except KeyError:
            raise KeyError(f"Unknown tag {name!r}; add it to the tag map first") from None

    def __iter__(self):
        return iter(self.tags.values())

    def __len__(self) -> int:
        return len(self.tags)


def _check_overlaps(tags: list[Tag]) -> None:
    used: dict[tuple[Area, int], str] = {}
    for tag in tags:
        for offset in range(tag.word_count):
            key = (tag.addr.area, tag.addr.address + offset)
            if key in used:
                raise ValueError(f"Tag {tag.name!r} overlaps tag {used[key]!r} at {tag.device}")
            used[key] = tag.name


def load_tags(path: Path) -> TagMap:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = raw.get("tags") or {}
    if not isinstance(entries, dict):
        raise ValueError(f"{path}: 'tags' must be a mapping of name -> tag")
    tags = [Tag(name=name, **(fields or {})) for name, fields in entries.items()]
    _check_overlaps(tags)
    return TagMap(tags={t.name: t for t in tags})
