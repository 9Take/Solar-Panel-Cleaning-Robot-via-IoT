# ขั้นที่ 5 — Commands (start / stop / return / reset / cycles)

## หน้าที่

ส่งคำสั่งให้หุ่นอย่างปลอดภัย Pi แค่ "ขอ" ผ่าน bit คำสั่ง ส่วน ladder เป็นคนตัดสินใจว่าจะทำหรือไม่

| ไฟล์ | หน้าที่ |
|---|---|
| `app/commander.py` | `PlcCommander`: ส่งคำสั่งตามกฎความปลอดภัย 10 ข้อ |
| `app/command_queue.py` | คิวคำสั่งในตาราง `commands` (SQLite) + `CommandQueueWorker` ใน gateway |
| `app/cmd.py` | CLI: `python -m app.cmd ...` (ส่งผ่านคิว ทางเดียวกับ dashboard) |

## ทำงานยังไง

### เส้นทางของคำสั่ง

```
dashboard / CLI ──INSERT──> ตาราง commands (status = pending)
                                 │  worker เช็กทุก 0.2 วิ
                                 ▼
                          PlcCommander.execute()
                                 │  pre-check → pulse bit → รอ ladder ตอบ
                                 ▼
                    status + result เขียนกลับ + event "command"
```

### คำสั่ง

| คำสั่ง | Tag | Pre-check (ปฏิเสธก่อนส่ง) | ถือว่าสำเร็จเมื่อ |
|---|---|---|---|
| `start` | `cmd_start` (M100) | E-stop กด, อยู่ใน ALARM, กำลังวิ่งอยู่แล้ว | state เป็น CLEANING หรือ RETURNING ภายใน 3 วิ |
| `stop` | `cmd_stop` (M101) | **ไม่มี** | มอเตอร์ (`drive_run`) หยุด |
| `return` | `cmd_return` (M102) | อยู่ HOME แล้ว, อยู่ใน ALARM | state เป็น RETURNING หรือ HOME |
| `reset` | `cmd_reset_alarm` (M103) | ไม่มี alarm, E-stop ยังกด | ออกจาก ALARM |
| `cycles N` | `cycles_setpoint` (D101) | N ต้องเป็นจำนวนเต็ม 1–100 | อ่านกลับได้ค่าตรง |

### กฎความปลอดภัย 10 ข้อ (docstring ใน `commander.py`)

1. เขียนได้เฉพาะ tag ใน whitelist และ tag map ต้องบอกว่าเขียนได้
2. การเขียนอยู่ใน commander เท่านั้น ไม่อยู่ใน API ของ `PlcClient`
3. คำสั่งเป็น pulse: เขียน 1 แล้ว ladder ล้างเป็น 0 เพื่อบอกว่ารับแล้ว
4. ladder ไม่ล้างใน 2 วิ → **Pi ล้างเอง** (กันคำสั่งค้าง เช่น PLC อยู่โหมด STOP แล้วถูกเปลี่ยนเป็น RUN ทีหลัง หุ่นจะออกวิ่งเอง)
5. **Stop ไม่เคยถูกบล็อก**: ไม่รอ lock ไม่ debounce ไม่ pre-check และในคิวถูกหยิบก่อนคำสั่งอื่น
6. pre-check แค่อธิบายเหตุผลที่ปฏิเสธ ladder ยังเป็นคนตัดสิน
7. ทำทีละคำสั่ง (lock), คำสั่งเดิมซ้ำภายใน 1 วิ → ignored
8. ไม่ลองซ้ำอัตโนมัติ
9. ทุกผลลัพธ์ถูกบันทึก (audit) ใน `events`
10. `cycles` รับ 1–100 เท่านั้น

### สถานะผลลัพธ์

`done` · `rejected` (pre-check ไม่ผ่าน) · `ignored` (debounce) · `not_acknowledged` (ladder ไม่รับ, Pi ล้าง bit แล้ว) · `not_started` (รับแล้วแต่ไม่ทำ พร้อมเหตุผล เช่น แบตไม่ถึง 80 %) · `error` (PLC offline) · `expired` (รอในคิวเกิน 5 วิ ไม่ถูกส่ง)

คำสั่งที่รอในคิวเกิน 5 วิ (เช่น gateway ดับ) จะหมดอายุ **ไม่ถูกส่ง** กัน start เก่าไปทำงานตอน gateway กลับมา

## วิธีใช้

ต้องรัน gateway (`python -m app`) อยู่ก่อน:

```bash
.venv/bin/python -m app.cmd start
.venv/bin/python -m app.cmd stop
.venv/bin/python -m app.cmd return
.venv/bin/python -m app.cmd reset
.venv/bin/python -m app.cmd cycles 3

docker compose exec gateway python -m app.cmd start
```

CLI รอผลสูงสุด 15 วิ exit code: 0 = done, 1 = ไม่สำเร็จ, 2 = ไม่มีคำตอบ (gateway ไม่ได้รัน)

จาก dashboard:

```python
import sqlite3, time
db = sqlite3.connect("logs/gateway.db")
cid = db.execute("INSERT INTO commands (ts, command, source) VALUES (?, 'start', 'dashboard')",
                 (time.time(),)).lastrowid
db.commit()
# แล้วอ่าน status/result ของแถว cid จนเป็นค่าสุดท้าย
```

## วิธีเทส

```bash
.venv/bin/python -m pytest tests/test_commands.py -v
```

test ใช้ mock PLC + หุ่นจำลองจริงผ่าน Modbus:

| กลุ่ม | ตรวจอะไร |
|---|---|
| pulse / ack | start จาก HOME สำเร็จ, PLC STOP (ladder ไม่รัน) → `not_acknowledged` และ bit ถูกล้าง |
| pre-check | E-stop กด, วิ่งอยู่แล้ว, reset ตอนไม่มี alarm, return ตอนอยู่บ้าน, แบตไม่พอได้ `not_started` พร้อมเหตุผล |
| stop | stop ระหว่างวิ่ง, stop ทำได้แม้คำสั่งอื่นถือ lock, ไม่ debounce, Stop ชนะ Start ใน scan เดียว |
| lock / debounce | คำสั่งที่สองระหว่างทำงานถูกปฏิเสธ, คำสั่งซ้ำถูก ignore |
| guard | `_write` ปฏิเสธ tag อ่านอย่างเดียว, commander ไม่ยอมสร้างถ้า tag map ผิด |
| คิว | ทำงาน + audit, หมดอายุ, stop ถูกหยิบก่อน, แถวค้าง `running` หลัง crash ไม่ถูกทำซ้ำ |

ลองมือ: mock + gateway รันอยู่ → `python -m app.cmd start` → `python -m app.read --watch` ดู `robot_state` เป็น 1 → `python -m app.cmd stop`

## ข้อควรรู้

- ⚠️ คำสั่งกับ PLC จริง = หุ่นขยับจริง ทดสอบกับ mock ก่อนเสมอ และคนต้องอยู่หน้างาน
- ⚠️ ladder จริงต้องมี: ล้าง bit คำสั่งหลังรับ, Stop ชนะ Start, interlock ทั้งหมด Python ไม่ได้ทำแทน
