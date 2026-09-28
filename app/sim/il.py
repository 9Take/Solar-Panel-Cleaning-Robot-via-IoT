"""Tiny Delta DVP instruction-list (IL) interpreter for testing plc/*.il.

Runs a ladder written as IL with the same scan(io, dt) interface as
app.sim.ladder.LadderSim, so the IL can be tested against the simulated plant.
Covers only the instructions the reference ladder uses; anything else fails at
load time. Not a full DVP emulator: no special registers besides M1000, M1002
and M1012, timers T0-T199 only (100 ms base), no error flags.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.tags import Direction, TagMap

SCAN_S = 0.01   # one PLC scan; scan(io, dt) runs dt / SCAN_S scans

_CMP = {
    "=": lambda a, b: a == b, "<>": lambda a, b: a != b,
    ">": lambda a, b: a > b, "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
}
_CMP_OP = re.compile(r"(LD|AND|OR)(=|<>|>=|<=|>|<)")
_ARGS = {
    "LD": 1, "LDI": 1, "LDP": 1, "AND": 1, "ANI": 1, "OR": 1, "ORI": 1,
    "ANB": 0, "ORB": 0, "OUT": 1, "SET": 1, "RST": 1, "TMR": 2,
    "MOV": 2, "INC": 1, "INCP": 1, "DEC": 1, "MUL": 3, "DDIV": 3,
    "CMP": 3, "ZRST": 2, "END": 0,
}
_STARTS_BLOCK = {"LD", "LDI", "LDP"}
_OUTPUTS = {"OUT", "SET", "RST", "TMR", "MOV", "INC", "INCP", "DEC", "MUL", "DDIV", "CMP", "ZRST"}

COMMANDS = ("cmd_start", "cmd_stop", "cmd_return", "cmd_reset_alarm")
OUTPUT_TAGS = COMMANDS + (
    "drive_run", "drive_dir", "robot_state", "alarm_code", "cycle_count", "position_est_pct")


def load_il(path: Path) -> list[tuple[str, list[str]]]:
    program = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        words = line.split(";", 1)[0].split()
        if not words:
            continue
        op, args = words[0].upper(), [w.upper() for w in words[1:]]
        expected = 2 if _CMP_OP.fullmatch(op) else _ARGS.get(op)
        if expected is None:
            raise ValueError(f"{path.name}:{lineno}: unsupported instruction {op}")
        if len(args) != expected:
            raise ValueError(f"{path.name}:{lineno}: {op} needs {expected} operand(s), got {len(args)}")
        program.append((op, args))
    return program


def _offset(device: str, k: int) -> str:
    m = re.fullmatch(r"([A-Z]+)(\d+)", device)
    return f"{m[1]}{int(m[2]) + k}"


def _signed16(v: int) -> int:
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


class IlLadder:
    def __init__(self, path: Path, tags: TagMap) -> None:
        self.program = load_il(path)
        self.tags = tags
        self.bits: dict[str, bool] = {}
        self.words: dict[str, int] = {}    # D registers, signed 16-bit
        self.timers: dict[str, float] = {} # elapsed ms
        self.edges: dict[int, bool] = {}   # previous value per LDP / INCP
        self.time_ms = 0.0
        self.first_scan = True

    # --- operands ------------------------------------------------------------

    def bit(self, dev: str) -> bool:
        if dev == "M1000":
            return True
        if dev == "M1002":
            return self.first_scan
        if dev == "M1012":
            return self.time_ms % 100 < 50
        return self.bits.get(dev, False)

    def word(self, dev: str) -> int:
        if dev.startswith("K"):
            return int(dev[1:])
        return self.words.get(dev, 0)

    def set_word(self, dev: str, value: int) -> None:
        self.words[dev] = _signed16(value)

    def dword(self, dev: str) -> int:
        if dev.startswith("K"):
            return int(dev[1:])
        v = (self.word(dev) & 0xFFFF) | (self.word(_offset(dev, 1)) & 0xFFFF) << 16
        return v - (1 << 32) if v & 0x8000_0000 else v

    def set_dword(self, dev: str, value: int) -> None:
        self.set_word(dev, value)
        self.set_word(_offset(dev, 1), value >> 16)

    # --- one scan ------------------------------------------------------------

    def _edge(self, i: int, value: bool) -> bool:
        rising = value and not self.edges.get(i, False)
        self.edges[i] = value
        return rising

    def run_scan(self) -> None:
        stack: list[bool] = []
        prev_op = "END"
        for i, (op, a) in enumerate(self.program):
            if op == "END":
                break
            cmp = _CMP_OP.fullmatch(op)
            base = cmp[1] if cmp else op
            if base in _STARTS_BLOCK or base == "LD":
                if prev_op in _OUTPUTS:
                    stack = []              # new rung
                if cmp:
                    stack.append(_CMP[cmp[2]](self.word(a[0]), self.word(a[1])))
                elif op == "LDP":
                    stack.append(self._edge(i, self.bit(a[0])))
                else:
                    stack.append(self.bit(a[0]) != (op == "LDI"))
            elif cmp:
                v = _CMP[cmp[2]](self.word(a[0]), self.word(a[1]))
                stack[-1] = stack[-1] and v if base == "AND" else stack[-1] or v
            elif op in ("AND", "ANI"):
                stack[-1] = stack[-1] and (self.bit(a[0]) != (op == "ANI"))
            elif op in ("OR", "ORI"):
                stack[-1] = stack[-1] or (self.bit(a[0]) != (op == "ORI"))
            elif op in ("ANB", "ORB"):
                b = stack.pop()
                stack[-1] = stack[-1] and b if op == "ANB" else stack[-1] or b
            else:
                self._output(i, op, a, stack[-1])
            prev_op = op
        self.first_scan = False
        self.time_ms += SCAN_S * 1000

    def _output(self, i: int, op: str, a: list[str], en: bool) -> None:
        if op == "OUT":
            self.bits[a[0]] = en
        elif op == "TMR":
            t = a[0]
            if not 0 <= int(t[1:]) <= 199:
                raise ValueError(f"{t}: only 100 ms timers T0-T199 are simulated")
            self.timers[t] = self.timers.get(t, 0.0) + SCAN_S * 1000 if en else 0.0
            self.bits[t] = en and self.timers[t] >= self.word(a[1]) * 100
        elif op == "INCP":
            if self._edge(i, en):
                self.set_word(a[0], self.word(a[0]) + 1)
        elif not en:
            return
        elif op == "SET":
            self.bits[a[0]] = True
        elif op == "RST":
            if a[0].startswith("D"):
                self.set_word(a[0], 0)
            else:
                self.bits[a[0]] = False
                self.timers.pop(a[0], None)
        elif op == "MOV":
            self.set_word(a[1], self.word(a[0]))
        elif op == "INC":
            self.set_word(a[0], self.word(a[0]) + 1)
        elif op == "DEC":
            self.set_word(a[0], self.word(a[0]) - 1)
        elif op == "MUL":
            self.set_dword(a[2], self.word(a[0]) * self.word(a[1]))
        elif op == "DDIV":
            divisor = self.dword(a[1])
            if divisor:
                q = int(self.dword(a[0]) / divisor)     # truncate toward zero
                self.set_dword(a[2], q)
                self.set_dword(_offset(a[2], 2), self.dword(a[0]) - q * divisor)
        elif op == "CMP":
            s1, s2 = self.word(a[0]), self.word(a[1])
            for k, v in enumerate((s1 > s2, s1 == s2, s1 < s2)):
                self.bits[_offset(a[2], k)] = v
        elif op == "ZRST":
            start, end = (int(d[1:]) for d in a)
            for n in range(start, end + 1):
                self.bits[f"{a[0][0]}{n}"] = False

    # --- LadderSim-compatible interface ---------------------------------------

    def scan(self, io: dict, dt: float) -> dict:
        """Load Pi-written tags and inputs from io, run dt of scans, return outputs."""
        for tag in self.tags:
            if tag.name not in io:
                continue
            if tag.device.startswith("X") or tag.dir in (Direction.WRITE, Direction.RW):
                if tag.addr.is_bit:
                    self.bits[tag.device] = bool(io[tag.name])
                else:
                    self.set_word(tag.device, int(io[tag.name]))
        for _ in range(max(1, round(dt / SCAN_S))):
            self.run_scan()
        out = {}
        for name in OUTPUT_TAGS:
            tag = self.tags[name]
            out[name] = self.bit(tag.device) if tag.addr.is_bit else self.word(tag.device)
        return out
