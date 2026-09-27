# ขั้นที่ 2 — Mock PLC

## หน้าที่

PLC ปลอมที่พูด Modbus TCP เหมือน DVP-12SE11T และจำลองหุ่นให้ขยับจริง ใช้พัฒนาและเทสโค้ดฝั่ง Pi ได้โดยไม่ต้องมี PLC หรือหุ่นจริง

| ไฟล์ | หน้าที่ |
|---|---|
| `app/sim/datastore.py` | `StrictSimCore`: หน่วยความจำ PLC ที่มี **เฉพาะ address ใน tag map** |
| `app/sim/server.py` | `MockPlcServer`: Modbus TCP server |
| `app/sim/ladder.py` | `LadderSim`: จำลอง ladder (state machine) ตาม `docs/robot-operation.md` |
| `app/sim/plant.py` | `Plant`: โลกจริงรอบ PLC (มอเตอร์, limit switch, ปุ่ม, E-stop, PZEM, แบต) |
| `app/sim/runner.py` | `SimRunner`: ต่อ plant + ladder เข้ากับ datastore ทีละ tick |
| `app/sim/__main__.py` | `python -m app.sim` |

## ทำงานยังไง

### 2a. Datastore แบบเข้มงวด

- address ที่ไม่มีใน tag map → ตอบ **Modbus exception 02 (Illegal Data Address)** เหมือน PLC จริงที่ไม่มี device นั้น
- ถ้าอ่านเป็นช่วงแล้วช่วงนั้นคร่อม address ที่ไม่มี → ปฏิเสธทั้ง request ทำให้เจอบั๊ก "อ่านข้ามช่องว่าง" ตั้งแต่ใน mock
- function code ตาม Delta: FC01/05/15 → Y, M · FC02 → X · FC03/06/16 → D

### 2b. TCP server

- ฟัง port 502 ใน Docker (host เข้าผ่าน `localhost:5020`) หรือ port ตาม `SIM_PORT`
- unit ID ต้องตรงกับ `PLC_UNIT_ID` ไม่ตรง → exception 0x0B (gateway target no response)
- FC04 (input register) ไม่มีใน DVP → exception 01 (illegal function)

### 2c. จำลองพฤติกรรมหุ่น

ทุก tick (`SIM_TICK_S`, ค่าเริ่มต้น 0.2 วิ):

```
plant อ่าน output PLC (drive_run, drive_dir) → ขยับตำแหน่ง → เขียน input (X0–X4), PZEM, แบต
ladder อ่านหน่วยความจำทั้งหมด → scan 1 รอบ → เขียน Y, robot_state, alarm_code, cycle_count, ล้าง bit คำสั่ง
```

state ของ ladder (`robot_state`):

```
            start (แบต ≥ 80 %, heartbeat ปกติ)
   HOME(3) ─────────────────────────────> CLEANING(1) ──ครบรอบ (Auto)──> HOME
     ▲                                     │   │
     │ ถึงปลาย                    stop     │   │ แบต < 25 % / cmd_return
     │                                     ▼   ▼
 RETURNING(2) <──── start/return ──── IDLE(0)  RETURNING(2)

 E-stop กด หรือ limit ทั้งสองฝั่ง ON → ALARM(4) ต้องปลดแล้ว reset
```

- **1 รอบ** = ออกจากปลายที่เริ่ม → ไปอีกปลาย → กลับมาปลายเดิม
- Auto (X3 OFF): ทำ `cycles_setpoint` รอบแล้วเข้า HOME · Manual: วนไปจนกว่าจะกด Stop
- ไม่มี encoder: ladder เรียนรู้เวลาเดินจากปลายถึงปลายเพื่อประมาณ `position_est_pct`
- `alarm_code` 2 (แบตวิกฤต) และ 5 (heartbeat หาย) แค่แจ้งเตือน หุ่นไม่หยุด
- Stop ชนะ Start ถ้ามาใน scan เดียวกัน
- `SIM_FAKE_PI_BATTERY=true` → plant เขียน `battery_pct` + `pi_heartbeat` แทน Pi (ใช้ตอนยังไม่มี Tuya)

## วิธีใช้

```bash
# Docker (ตั้ง PLC_HOST=plc-sim ใน .env ให้ gateway มาต่อ)
docker compose --profile sim up --build

# ไม่ใช้ Docker
SIM_PORT=5020 .venv/bin/python -m app.sim
```

ปรับใน `.env`: `SIM_BEHAVIOR` (false = datastore เปล่า ไม่จำลองหุ่น), `SIM_TICK_S`, `SIM_TRAVEL_S` (เวลาเดินปลายถึงปลาย), `SIM_FAKE_PI_BATTERY`

ดูค่าด้วยโปรแกรม Modbus (เช่น Modbus Poll) ที่ `localhost:5020` unit 1 หรือใช้ `python -m app.read --watch` (ขั้นที่ 3)

## วิธีเทส

```bash
.venv/bin/python -m pytest tests/test_sim_datastore.py tests/test_sim_server.py tests/test_sim_behavior.py -v
```

| test | ตรวจอะไร |
|---|---|
| `test_sim_datastore` | address ใน tag map อ่าน/เขียนได้, address อื่นได้ exception 02, unit ID ผิด |
| `test_sim_server` | client จริงต่อผ่าน TCP อ่าน/เขียนได้, ค่าที่ฝั่ง server เปลี่ยนเห็นจาก client, error ผ่าน TCP |
| `test_sim_behavior` | ครบทุกกรณีใน spec: เปิดเครื่องที่ปลาย = HOME, Auto ครบรอบแล้วกลับ, Manual วนจนกด stop, แบตต่ำกลับบ้าน, แบต < 80 % ไม่ออก, E-stop, limit สองฝั่ง, heartbeat หาย, ปุ่มหน้าตู้, Stop ชนะ Start, เรียนรู้เวลาเดิน |

ลองมือ: เปิด mock แล้ว `python -m app.read --watch` ในอีก terminal สั่ง `python -m app.cmd start` (ต้องรัน gateway ด้วย ดูขั้นที่ 5) จะเห็น `robot_state`, `limit_1/2`, `position_est_pct` เปลี่ยน

## ข้อควรรู้

- ⚠️ ladder ใน mock เป็น **แบบอ้างอิง** จากข้อสมมติใน `docs/robot-operation.md` (ข้อที่มี ❓) ไม่ใช่ ladder จริง ต้องเทียบกับ ladder จริงอีกครั้ง
- `StrictSimCore` สืบทอดจาก class ภายในของ pymodbus อัปเกรด pymodbus แล้วต้องรัน test ใหม่ทุกครั้ง
