# คู่มือแต่ละ process (ขั้นที่ 1–8)

เอกสารชุดนี้อธิบายโค้ดฝั่ง Raspberry Pi ทีละขั้นตาม roadmap ทุกไฟล์แบ่งหัวข้อเหมือนกัน: **หน้าที่ → ทำงานยังไง → วิธีใช้ → วิธีเทส → ข้อควรรู้**

| ขั้น | เอกสาร | โค้ดหลัก | test |
|---|---|---|---|
| 1 | [Config + tag map](01-config-tags.md) | `app/config.py`, `app/tags.py`, `app/delta.py`, `app/codec.py` | `test_config`, `test_tags`, `test_delta`, `test_codec` |
| 2 | [Mock PLC](02-mock-plc.md) | `app/sim/` | `test_sim_datastore`, `test_sim_server`, `test_sim_behavior` |
| 3 | [Read client](03-read-client.md) | `app/plc_client.py`, `app/read.py` | `test_plc_client` |
| 4 | [Polling + history](04-polling-history.md) | `app/poller.py`, `app/history.py`, `app/__main__.py` | `test_history_poller` |
| 5 | [Commands](05-commands.md) | `app/commander.py`, `app/command_queue.py`, `app/cmd.py` | `test_commands` |
| 6 | [Battery (Tuya) + heartbeat](06-battery-heartbeat.md) | `app/tuya.py`, `app/battery.py`, `app/sim/battery_demo.py` | `test_tuya`, `test_battery` |
| 7 | [Schedule](07-schedule.md) | `app/schedule.py` | `test_schedule` |
| 8 | [Dashboard (Streamlit)](08-dashboard.md) | `app/dashboard/` | `test_dashboard` |

## ภาพรวมการไหลของข้อมูล

```
                    ┌──────────────────── gateway (python -m app) ─────────────────────┐
 .env ──> Settings  │                                                                    │
 plc_tags.yaml ──>  │  Poller ──read──┐                                                  │
   TagMap           │                 │                                                  │
                    │  CommandQueue ──┼──> PlcCommander ──_write──┐                      │
                    │  Scheduler ─────┘                           │                      │
                    │  BatteryFeeder ──(battery_pct, heartbeat)───┼──> PlcClient ──Modbus TCP──> PLC / mock
                    │        ▲                                    │                      │
                    │        └── Tuya Cloud (HTTPS)               │                      │
                    │                                                                    │
                    │  ทุกตัวเขียนผลลง SQLite  logs/gateway.db                              │
                    └────────────────────────────────────────────────────────────────────┘
 dashboard (Streamlit :8501) / CLI ──> อ่าน/เขียน SQLite เท่านั้น (ไม่คุย Modbus เอง)
```

## เตรียมเครื่องสำหรับเทส

test ทั้งหมดรันบนเครื่อง dev ด้วย venv (image Docker ไม่มี pytest):

```bash
.venv/bin/python -m pytest -q                 # ทั้งหมด (230 test, ~25 วินาที)
.venv/bin/python -m pytest tests/test_schedule.py -v   # เฉพาะไฟล์
.venv/bin/python -m pytest -k heartbeat -v    # เฉพาะชื่อที่มีคำนี้
```

test ไม่ต้องใช้ PLC จริง ไม่ต้องมี `.env` และไม่ต้องต่อเน็ต test ที่ต้องมี PLC จะเปิด mock PLC บน `127.0.0.1` port สุ่มเอง

## รันทั้งระบบกับ mock PLC (ลองมือ)

```bash
# Docker: ไม่ต้องแก้ .env
docker compose -f docker-compose.yml -f docker-compose.sim.yml up -d --build

# ไม่ใช้ Docker: 2 terminal
SIM_PORT=5020 .venv/bin/python -m app.sim
PLC_HOST=127.0.0.1 PLC_PORT=5020 .venv/bin/python -m app
```

ค่าที่ส่งผ่านตัวแปร environment จะทับค่าใน `.env`
