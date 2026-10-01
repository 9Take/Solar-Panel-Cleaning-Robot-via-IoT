# CLAUDE.md

## Project

Bachelor's senior project (Robotics Engineering, KMUTNB):
**"Development of an IoT-Based Control, Monitoring, and Self-Charging System for a Solar Panel Cleaning Robot"**

The owner is a student. Explanations may be in Thai; code, identifiers, commits, and comments stay in English.

## Status

- **Done:** Hardware + PLC ladder logic (the robot runs standalone on the PLC).
- **Now:** IoT layer — a Raspberry Pi that reads status from the PLC and sends commands to it.
- **Later:** User-facing monitoring/control UI. Stack is undecided; it must stay simple because the first goal is a demo for the sponsoring company. Do not pick a UI/cloud stack without asking.

## Roadmap (one step at a time — stop for owner review after each)

Keep the progress tables in `README.md` (Thai + English sections) in sync with this list.

- [x] **1. Config + tag map** — `.env` settings, `plc_tags.yaml` loader, Delta device → Modbus address conversion (pure code, no network)
- [x] **2. Mock PLC** — `app/sim`
  - [x] 2a. Datastore from tag map; only tag-map addresses exist (`StrictSimCore`), others → exception 02
  - [x] 2b. TCP server `MockPlcServer` (`python -m app.sim`, compose file `docker-compose.sim.yml`, host port 5020) + network tests
  - [x] 2c. Simulated ladder behavior per `docs/robot-operation.md` using **assumed** M/D tags (owner's choice); replace with real ladder tags later
    - `app/codec.py` (shared with the client), `app/sim/ladder.py` (reference state machine), `app/sim/plant.py` (physics, buttons, E-stop, PZEM, fake Pi battery), `app/sim/runner.py`
- [x] **3. Read client** — `app/plc_client.py` (`PlcClient`, `plan_reads`): read tags by name (read-only), contiguous-block reads never spanning gaps, `PlcOfflineError` + reconnect with backoff, `PlcReadError` for Modbus exceptions. Dev CLI: `python -m app.read [names] [--watch]`
- [x] **4. Polling + logging** — `python -m app` service: `app/poller.py` polls every `PLC_POLL_INTERVAL_S`, logs state/alarm/E-stop/mode/online changes; `app/history.py` SQLite `logs/gateway.db` (WAL) with `latest` (live row), `snapshots` (JSON, every 10 s, 30 days), `events` (kept). Dashboard reads the DB, never Modbus. Status code names in `app/robot.py`
- [x] **5. Commands** — `app/commander.py` `PlcCommander` (10 safety rules in its docstring: whitelist, pulse + 2 s ack else Pi clears the bit, Stop never blocked and wins over Start, pre-checks explain refusals, lock + debounce, no retries, cycles 1-100). Writes only via `PlcClient._write` (read-only public API). Dashboard/CLI send via SQLite `commands` queue (`app/command_queue.py`, 5 s expiry, stop first, audited in `events`). CLI: `python -m app.cmd start|stop|return|reset|cycles N`
- [x] **6. Battery (Tuya) + heartbeat** — `app/tuya.py` (stdlib Tuya Cloud client, HMAC-SHA256 signing verified against Tuya doc examples; `python -m app.tuya` lists DPs), `app/battery.py` `BatteryFeeder`: Tuya poll task (`TUYA_POLL_INTERVAL_S`) + feed task writing `battery_pct` then `pi_heartbeat+1` every `PI_HEARTBEAT_INTERVAL_S`. `battery_to_feed`: no/stale (> `BATTERY_MAX_AGE_S`)/invalid reading → None → heartbeat held (ladder alarm 5); valid → `int()` (round down). Feed off if any TUYA_* key empty. PZEM values already read by the poller
- [x] **7. Schedule** — `app/schedule.py`: `schedules` table in `logs/gateway.db` (HH:MM, days, optional cycles, enabled, last_run), `Scheduler` checks every 5 s in `SCHEDULE_TZ` (tzdata in image). Due → Auto mode check, then `cycles` + `start` directly via `PlcCommander` (not the queue: the queue runs jobs concurrently and the commander lock would reject the second). Once per day, no retries; missed > 60 s → `missed` event; not ready → `skipped` event with the commander's reason. CLI: `python -m app.schedule list|add|remove|enable|disable`
- [ ] **8. UI / API** — owner picked **Streamlit** for now (LAN demo; dev team views + commands; phone + PC). Merged into `main` (owner's pick; teammate's `feature/dashboard` was the comparison). `app/dashboard/data.py` (SQLite only) + `main.py`; Dockerfile target `dashboard`, compose service `dashboard` :8501; optional `DASHBOARD_PASSWORD`. Internet access later (tunnel + real auth)

## Hardware

| Part | Detail |
|---|---|
| PLC | Delta **DVP-12SE11T** (DVP-SE series, built-in Ethernet) |
| Gateway | Raspberry Pi 4/5, Raspberry Pi OS |
| Link | Ethernet, **Modbus TCP**, PLC is the server (port 502), Pi is the client |

## Architecture

```
[UI (TBD)] <--> [Raspberry Pi] <--Modbus TCP--> [DVP-12SE11T] <--RS485--> [PZEM-017 DC meter]
                      |                               |
                      +--HTTPS--> [Tuya Cloud] <--WiFi-- [Tuya MPPT solar charger]
                                                      +--> MD30C --> DC drive motor (+ geared brush)
```

Full behavior spec: `docs/robot-operation.md`.

- Data sources on the Pi: **PLC** (Modbus TCP: state, limits, E-stop, motor, PZEM values in D registers) and **Tuya Cloud API** (battery %, solar). Router + AP in the control cabinet provides LAN + internet. No MQTT.
- The **PLC owns all motion logic, interlocks, and safety.** The Pi requests actions (command bits); ladder decides. Never move safety logic into Python.
- Battery decisions stay in the ladder: the Pi only **feeds** `battery_pct` (from Tuya) and an incrementing `pi_heartbeat` into D registers; the ladder decides return-to-home / may-start (≥ 80 %) and treats a stale heartbeat as unknown battery.
- E-stop cuts motor power in hardware (MD30C supply); its NC contact on X4 only reports status. PLC/Pi stay powered to report the alarm.
- No encoder: position is known only at the two ends (X0, X1); the ladder estimates position from travel time.
- Mode (Manual/Auto) is selected only by the front switch X3; the Pi reads it, never writes it. Auto = Pi schedule sends `cmd_start`, runs `cycles_setpoint` cycles. Manual = runs until Stop.
- Scope of data: commands (start/stop/return/reset), status (state, alarm, cycles, limits, mode, E-stop, motor), power (PZEM via PLC), battery/solar (Tuya), schedule + history log on the Pi.

## Tech Stack (Pi side)

- Python 3
- `pymodbus` >=3.15,<4 for Modbus TCP (plain Modbus; no vendor SDK required). Mock uses the new `SimData`/`SimDevice` API; `ModbusDeviceContext`/`*DataBlock` are deprecated — don't use them. `StrictSimCore` subclasses the internal `SimCore`, so re-run tests after any pymodbus upgrade.
- Delta 32-bit values: low word in D(n), high word in D(n+1) — opposite of pymodbus `DataType.INT32`. Store/convert 32-bit tags as 2 raw registers.
- `pydantic-settings` for `.env` config, `PyYAML` for the tag map
- Keep dependencies minimal; bound versions in `requirements.txt`

## Docker (everything on the Pi runs in containers)

- Nothing is installed on the Pi host except Docker + Compose plugin. Pi must run a **64-bit OS** (image targets `linux/arm64`).
- Files: multi-stage `Dockerfile` (python:3.12-slim, non-root user; targets `gateway` = default/last, `dashboard` = + Streamlit), `docker-compose.yml` (real use), `docker-compose.sim.yml` (test add-on), `.dockerignore`.
- Entry point: `python -m app` → package `app/`.
- Services:
  - `gateway`: the Pi ↔ PLC service. `restart: unless-stopped`.
  - `dashboard`: Streamlit UI on :8501, SQLite only.
  - `plc-sim` (only in `docker-compose.sim.yml`): mock Modbus TCP server (`python -m app.sim`). The sim file also overrides the gateway's `PLC_HOST=plc-sim` and sets `HISTORY_DB=logs/sim/gateway.db` for gateway + dashboard, so `.env` always holds the real PLC and sim history never mixes with real history. Never name it `docker-compose.override.yml` (compose would load it by default).
- Runtime config: `.env` via `env_file` (never baked into the image — `.dockerignore` excludes it); `config/` mounted read-only; `logs/` mounted as a volume.
- Default bridge network is enough to reach the PLC on the LAN. Use `network_mode: host` only if a real need appears.
- New services (UI, DB) go in the same `docker-compose.yml`.

Commands:
```bash
docker compose up -d --build          # run on the Pi
docker compose logs -f gateway        # follow logs
docker compose -f docker-compose.yml -f docker-compose.sim.yml up -d --build   # dev with mock PLC
docker compose -f docker-compose.yml -f docker-compose.sim.yml down
docker compose down
```

## Delta DVP Modbus Addressing

Delta DVP devices map to Modbus addresses as below (hex, 0-based as used by pymodbus). Verified against Delta's "DVP Series PLC Communication Protocol" device table. Delta docs also list 1-based decimal addresses (e.g. M0 = 002049) — never use those directly.

| Device | Range | Modbus addr | Function codes |
|---|---|---|---|
| X (input) | X0–X377 (octal) | 0x0400– | FC02 read |
| Y (output) | Y0–Y377 (octal) | 0x0500– | FC01 read (avoid writing Y directly) |
| M (aux relay) | M0–M1535 | 0x0800– | FC01 read, FC05/FC15 write |
| M (aux relay) | M1536–M4095 | 0xB000– | FC01 read, FC05/FC15 write |
| D (data reg) | D0–D4095 | 0x1000– | FC03 read, FC06/FC16 write |
| D (data reg) | D4096–D9999 | 0x9000– | FC03 read, FC06/FC16 write |

X/Y numbers are **octal** on DVP (X10 = 8th input). Convert carefully. The DVP-12SE11T itself has X0–X7 and Y0–Y3 (transistor NPN); higher X/Y only exist with extension modules.

Factory network default: IP 192.168.1.5, mask 255.255.255.0 (change via DCISoft or CR#88–91, module K108).

## Configuration & Secrets

No fixed parameter lives in code. Two places only:

| File | Committed | Holds |
|---|---|---|
| `.env` | **No** (gitignored) | PLC IP/port/unit ID, timeouts, poll interval, API keys/tokens, log settings |
| `.env.example` | Yes | Every `.env` key with a dummy value + comment |
| `config/plc_tags.yaml` | Yes | PLC I/O tag map (device address, direction, type, scale) |

- In Docker, `.env` reaches the container through `env_file`; it is never copied into the image.
- Load `.env` with `pydantic-settings` (typed + validated); load the tag map with PyYAML. Fail fast on missing/invalid values.
- Never hard-code IPs, ports, unit IDs, PLC addresses, or keys in Python.
- Never commit `.env` or real credentials. Adding a new key → also add it to `.env.example`.
- Never print secrets (API keys/tokens) in logs or error messages.

## Robot Behavior Spec

`docs/robot-operation.md` (Thai) describes movement, states, sensors, I/O, alarms and the draft tag list. Items marked ❓ are unconfirmed assumptions — never treat them as facts; ask the owner. Keep the mock (2c) and tag map consistent with this file.

## PLC Tag Map

Single source of truth: `config/plc_tags.yaml`. Code refers to tags by name, never by raw address. **Fill in from the ladder program — do not guess addresses.**

## Rules for Claude

- **Never write to a PLC address that is not in the tag map.** Unknown address = ask.
- Writes to a live PLC move real hardware. Default to read-only scripts; any write must be explicit and documented.
- Commands should be **momentary pulses or request bits** the ladder latches/clears — not direct Y output forcing.
- Handle connection loss: timeouts, reconnect with backoff, and a clear "PLC offline" state. A dead link must never leave the robot in an unsafe state (a PLC-side watchdog/heartbeat is recommended).
- All fixed parameters follow **Configuration & Secrets** above.
- Provide a simulator/mock Modbus server path so code can be tested without the real PLC (`plc-sim` compose service).
- Every runnable piece must work via `docker compose`; don't add steps that require installing packages on the Pi host.

## Open Questions

- PLC IP address, Modbus unit/slave ID, whether Modbus TCP server is enabled on the PLC
- Full tag map (M/D devices used in the ladder for commands, status, alarms, battery)
- Battery/charging measurement: which D registers, scaling/units
- UI/monitoring stack and whether remote (internet) access is needed for the demo
