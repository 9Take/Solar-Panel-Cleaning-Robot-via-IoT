# ขั้นที่ 4 — Polling + history (SQLite)

## หน้าที่

gateway service (`python -m app`) อ่าน PLC ทุกช่วงเวลา บันทึกค่าล่าสุด ประวัติ และเหตุการณ์ลง SQLite ให้ dashboard อ่านจากฐานข้อมูลโดยไม่ต้องคุย Modbus เอง

| ไฟล์ | หน้าที่ |
|---|---|
| `app/__main__.py` | จุดเริ่ม service: สร้างทุกส่วนแล้วรันเป็น task พร้อมกัน จนได้ SIGINT/SIGTERM |
| `app/poller.py` | `Poller`: อ่านทุก tag, หาค่าที่เปลี่ยน, เก็บ snapshot, ลบข้อมูลเก่า |
| `app/history.py` | `HistoryStore`: ตาราง SQLite + ฟังก์ชันอ่าน |
| `app/robot.py` | ชื่อของ state / alarm code |

## ทำงานยังไง

```
ทุก PLC_POLL_INTERVAL_S (1 วิ):   อ่านทุก tag → อัปเดตตาราง latest
                                  tag ที่เฝ้าดูเปลี่ยน → log + เพิ่มแถวใน events
ทุก SNAPSHOT_INTERVAL_S (10 วิ):  เก็บค่าทุก tag ลง snapshots
ทุกชั่วโมง:                       ลบ snapshots เก่ากว่า HISTORY_RETENTION_DAYS (30 วัน)
```

### ตารางใน `logs/gateway.db`

| ตาราง | เก็บอะไร | อายุ |
|---|---|---|
| `latest` | 1 แถว: ค่าล่าสุด + `online` (ใช้แสดงสดบน dashboard) | อัปเดตทับทุกรอบ |
| `snapshots` | ค่าทุก tag เป็น JSON ทุก 10 วิ (ใช้ทำกราฟ) | 30 วัน |
| `events` | เหตุการณ์: state/alarm/E-stop/mode เปลี่ยน, online/offline, read error, คำสั่ง, battery feed, schedule | เก็บตลอด |

ค่า tag เก็บเป็น JSON (`{"robot_state": 3, "battery_pct": 90, ...}`) เพิ่ม/ลบ tag ได้โดยไม่ต้องแก้ schema เวลา `ts` เป็น Unix time (วินาที)

### tag ที่เฝ้าดู (เกิด event เมื่อเปลี่ยน)

`robot_state`, `alarm_code`, `estop_ok`, `mode_switch` ถ้าค่าใหม่เป็นปัญหา (alarm, E-stop กด, state ALARM) จะ log เป็น WARNING

### กรณีผิดปกติ

- **PLC offline** → `latest.online = 0` (ค่าเก่ายังอยู่), event `offline` ครั้งเดียว พอกลับมาได้ event `online`
- **PLC ตอบ exception** (tag map ไม่ตรงกับ PLC) → event `read_error` ครั้งเดียว ไม่ใช่ทุกวินาที
- SQLite ใช้ **WAL mode** ให้ dashboard อ่านได้ระหว่างที่ gateway เขียน

## วิธีใช้

```bash
docker compose up -d --build          # บน Pi
docker compose logs -f gateway        # ดู log

.venv/bin/python -m app               # รันตรง (ต้องมี .env)
```

อ่านข้อมูล (ตัวอย่างสำหรับ dashboard):

```sql
SELECT ts, online, data FROM latest;
SELECT ts, json_extract(data, '$.battery_pct') FROM snapshots WHERE ts > strftime('%s','now') - 3600;
SELECT datetime(ts, 'unixepoch', 'localtime'), kind, message FROM events ORDER BY id DESC LIMIT 20;
```

```bash
sqlite3 logs/gateway.db "SELECT datetime(ts,'unixepoch','localtime'), kind, message FROM events ORDER BY id DESC LIMIT 20"
```

## วิธีเทส

```bash
.venv/bin/python -m pytest tests/test_history_poller.py -v
```

| test | ตรวจอะไร |
|---|---|
| `test_store_round_trip`, `test_store_prune_and_wal` | เขียน/อ่านทุกตาราง, ลบ snapshot เก่า, WAL เปิดอยู่ |
| `test_first_poll_records_initial_state` | รอบแรกบันทึกสถานะเริ่มต้น |
| `test_changes_become_events_only_when_they_change` | ค่าเดิมไม่สร้าง event ซ้ำ |
| `test_snapshot_interval`, `test_old_snapshots_pruned` | ระยะ snapshot และการลบ |
| `test_offline_and_back_online`, `test_offline_at_startup_is_recorded` | offline/online เป็น event |
| `test_read_error_recorded_once` | read error ไม่ท่วม log |
| `test_poller_against_simulated_robot` | poller กับหุ่นจำลองจริงผ่าน Modbus |

ลองมือ: รัน mock + gateway สักครู่ แล้วสั่งคำสั่ง `sqlite3` ข้างบน จะเห็น event `robot_state: ...` ลองปิด mock จะเห็น `PLC offline`

## ข้อควรรู้

- dashboard **อ่าน SQLite เท่านั้น** ไม่ต่อ Modbus เอง ทำให้มี client ที่คุยกับ PLC แค่ตัวเดียว
- ไฟล์ DB อยู่ใน `logs/` ที่ mount เป็น volume ลบ container แล้วข้อมูลไม่หาย
