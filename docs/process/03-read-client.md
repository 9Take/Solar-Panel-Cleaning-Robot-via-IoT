# ขั้นที่ 3 — Read client

## หน้าที่

อ่านค่าจาก PLC ด้วย **ชื่อ tag** ผ่าน Modbus TCP รับมือสายหลุด/PLC ดับได้ public API อ่านอย่างเดียว

| ไฟล์ | หน้าที่ |
|---|---|
| `app/plc_client.py` | `PlcClient`, `plan_reads()`, `PlcOfflineError`, `PlcReadError` |
| `app/read.py` | CLI สำหรับ dev: `python -m app.read` |

## ทำงานยังไง

### รวม request (`plan_reads`)

อ่าน tag ทีละตัวช้าเกินไป เลยรวม tag ที่ address ติดกันเป็น request เดียว:

```
D10 D11 D12 D13            → 1 request (0x100A x4)
D20 D21 D22-23 D24-25      → 1 request (0x1014 x6)
D101  D110 D111            → 2 request (D101 กับ D110–111 ห่างกัน)
```

- **ไม่รวมข้ามช่องว่าง** เพราะ PLC ปฏิเสธทั้ง request ถ้าช่วงนั้นมี address ที่ไม่มีอยู่
- จำกัดขนาด: register ≤ 100 word ต่อ request (ตามเอกสาร DVP), bit ≤ 256
- ชื่อซ้ำถูกตัดออก, แยกตามชนิด (coil / discrete input / holding register)

### Error 2 แบบ

| Exception | เกิดเมื่อ | ความหมาย |
|---|---|---|
| `PlcOfflineError` | ต่อไม่ได้, timeout, สายหลุด | ปัญหาเครือข่าย → ลองใหม่ได้ |
| `PlcReadError` | PLC ตอบกลับเป็น Modbus exception (เช่น 02) | PLC ออนไลน์แต่ tag map ไม่ตรงกับ PLC → ปัญหา config |

### ต่อใหม่แบบ backoff

- พอสายหลุด การเรียกครั้งต่อไปจะลองต่อใหม่ทันที
- ต่อไม่ได้อีก → รอ 1, 2, 4, 8 … วิ (สูงสุด 30 วิ) ก่อนลองครั้งถัดไป ระหว่างรอจะได้ `PlcOfflineError` ทันทีโดยไม่เสียเวลารอ timeout
- ต่อได้แล้ว → เวลารอกลับไปที่ 1 วิ
- มี lock ให้ส่ง request ได้ทีละตัว (poller, commander, feeder ใช้ client ตัวเดียวกัน)

### การเขียน

`_write()` เป็นเมธอดภายใน เรียกได้แค่ 2 ที่: `PlcCommander` (ขั้นที่ 5) กับ `BatteryFeeder` (ขั้นที่ 6) ตัวมันเองยังตรวจอีกชั้น: tag ต้องเป็น `dir: write/rw` และห้ามเป็น X

## วิธีใช้

```bash
.venv/bin/python -m app.read                          # ทุก tag ครั้งเดียว
.venv/bin/python -m app.read robot_state battery_pct  # เฉพาะที่เลือก
.venv/bin/python -m app.read --watch                  # อ่านซ้ำทุก PLC_POLL_INTERVAL_S (Ctrl+C หยุด)

docker compose run --rm gateway python -m app.read --watch
```

ตัวอย่างผล:

```
PLC 127.0.0.1:5020
  robot_state       D10            3
  battery_pct       D110          90 %
  limit_1           X0             1
```

## วิธีเทส

```bash
.venv/bin/python -m pytest tests/test_plc_client.py -v
```

| test | ตรวจอะไร |
|---|---|
| `test_plan_*` | รวม address ติดกัน, ไม่ข้ามช่องว่าง, จำกัดขนาด, ตัดชื่อซ้ำ |
| `test_read_values_by_name`, `test_read_all_uses_one_request_per_block` | อ่านจาก mock ได้ค่าถูก จำนวน request ตรงแผน |
| `test_reads_live_simulation` | อ่านค่าจากหุ่นจำลองที่กำลังขยับ |
| `test_modbus_exception_is_read_error_not_offline` | exception 02 เป็น `PlcReadError` ไม่ใช่ offline |
| `test_offline_then_backoff` | ไม่มี PLC → offline และเว้นระยะก่อนลองใหม่ |
| `test_reconnects_after_plc_restart` | ปิด mock แล้วเปิดใหม่ → client ต่อกลับได้เอง |

ลองมือ (สายหลุด): เปิด mock + `python -m app.read --watch` → ปิด mock → จะเห็น `!! PLC offline` → เปิด mock ใหม่ → ค่ากลับมาเอง

## ข้อควรรู้

- ใช้กับ mock บนเครื่อง: `PLC_HOST=127.0.0.1 PLC_PORT=5020`
- ต่อ PLC จริงครั้งแรก ให้ใช้ `python -m app.read` ก่อน เพราะอ่านอย่างเดียว ไม่ทำให้หุ่นขยับ
