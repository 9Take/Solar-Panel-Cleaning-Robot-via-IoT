# ขั้นที่ 6 — Battery (Tuya) + heartbeat

## หน้าที่

อ่านแบต % จาก Tuya MPPT ผ่าน Tuya Cloud แล้วเขียนลง PLC (`battery_pct`, D110) พร้อม `pi_heartbeat` (D111) ladder เป็นคนตัดสินใจเรื่องแบตทั้งหมด Pi แค่ส่งตัวเลขให้

| ไฟล์ | หน้าที่ |
|---|---|
| `app/tuya.py` | `TuyaCloud`: client ของ Tuya OpenAPI (ใช้แค่ standard library) + CLI `python -m app.tuya` |
| `app/battery.py` | `BatteryFeeder` + policy `battery_to_feed()` |
| `app/sim/battery_demo.py` | สคริปต์ทดสอบด้วยมือ ไม่ต้องมีบัญชี Tuya |

## ทำงานยังไง

```
task 1, ทุก TUYA_POLL_INTERVAL_S (60 วิ):
    Tuya Cloud → ค่า DP TUYA_BATTERY_DP × TUYA_BATTERY_SCALE → reading (ค่า, เวลาที่อ่าน)
    อ่านไม่ได้ → เก็บ reading เดิมไว้ + event tuya_error (ครั้งเดียว)

task 2, ทุก PI_HEARTBEAT_INTERVAL_S (2 วิ):
    battery_to_feed(reading) ─┬─ int  → เขียน battery_pct แล้วเขียน pi_heartbeat + 1
                              └─ None → ไม่เขียนอะไร (heartbeat หยุด)
```

แยก task เพราะ request ไป Tuya ช้าได้ถึง 10 วิ (timeout) ถ้าอยู่ loop เดียวกัน heartbeat จะดีเลย์จน ladder ขึ้น alarm ทั้งที่แบตปกติ

### heartbeat = "ค่าแบตยังเชื่อได้"

ladder ดูว่า `pi_heartbeat` เปลี่ยนไหม ถ้าค้างเกิน 10 วิ → **alarm 5**: ไม่รับ start ถ้ากำลังทำความสะอาดจะทำรอบนั้นให้จบแล้วกลับจุดพัก

การเขียนเรียงลำดับ **ค่าแบตก่อน แล้วค่อย heartbeat** heartbeat ที่เปลี่ยนแปลว่ายืนยันค่าที่อยู่ใน D110 ตอนนั้น

### policy `battery_to_feed()`

| กรณี | ผล |
|---|---|
| ยังไม่เคยอ่านได้ | `None` → heartbeat หยุด |
| ค่าเก่ากว่า `BATTERY_MAX_AGE_S` (300 วิ) | `None` |
| ค่าผิด: ติดลบ, > 100, NaN, inf | `None` (ไม่บีบค่าให้ดูปกติ) |
| 0–100 | `int()` ปัดลง เช่น 79.9 → 79 ไม่ถึงเกณฑ์ออกงาน 80 % ก่อนเวลาจริง |

### Tuya Cloud

- เซ็นทุก request ด้วย HMAC-SHA256 ตามเอกสาร Tuya (test ใช้ตัวอย่างในเอกสารเป็นเฉลย)
- token ถูกเก็บไว้ใช้ซ้ำ ต่ออายุก่อนหมด 60 วิ ถ้า Tuya บอกว่า token ใช้ไม่ได้ (1010/1011) จะขอใหม่ 1 ครั้ง
- secret และ token ไม่ออกไปใน error หรือ log
- key `TUYA_*` ตัวใดว่าง → feed ปิด และ gateway log เตือน

## วิธีใช้

### ตั้งค่า Tuya (ครั้งแรก)

1. สร้าง Cloud project ที่ https://platform.tuya.com เลือก Data Center **ตรงกับภูมิภาคของบัญชีแอป Smart Life**
2. เปิด API service **IoT Core** แล้ว Devices → Link App Account → ใช้แอปสแกน QR
3. ใส่ใน `.env`: `TUYA_ACCESS_ID`, `TUYA_ACCESS_SECRET`, `TUYA_API_ENDPOINT`, `TUYA_DEVICE_ID`
4. หาชื่อ DP ของแบต:

   ```bash
   docker compose run --rm gateway python -m app.tuya
   ```

   ```
   battery_percentage  87
   pv_volt             181
   ```

5. ใส่ `TUYA_BATTERY_DP=...` (และ `TUYA_BATTERY_SCALE=0.1` ถ้าค่าเป็น 0–1000)
6. ถ้าใช้ mock PLC ตั้ง `SIM_FAKE_PI_BATTERY=false` ไม่อย่างนั้น mock กับ Pi จะเขียน register เดียวกัน
7. `docker compose up -d` แล้วดู log จะเห็น `Battery feed on: ...` และ `Battery feed running: 87% to the PLC`

ค่าอื่น: `TUYA_POLL_INTERVAL_S` (≥ 10, ระวังโควตา API), `TUYA_TIMEOUT_S`, `BATTERY_MAX_AGE_S`, `PI_HEARTBEAT_INTERVAL_S`

## วิธีเทส

```bash
.venv/bin/python -m pytest tests/test_tuya.py tests/test_battery.py -v
```

| test | ตรวจอะไร |
|---|---|
| `test_sign_*_matches_tuya_doc` | ลายเซ็นตรงกับตัวอย่างในเอกสาร Tuya ทั้งแบบขอ token และแบบเรียก API |
| token | ใช้ token ซ้ำ, ต่ออายุก่อนหมด, token เสียขอใหม่ครั้งเดียว, เสียซ้ำ → error ไม่วนไม่จบ |
| error | error จาก API / ตอบไม่ใช่ JSON / ต่อไม่ได้ → `TuyaError` ที่ไม่มี secret |
| `test_battery_to_feed` | policy 11 กรณี (ขอบ 0/100, 79.9, อายุพอดี/เกิน, ค่าผิด) |
| feeder | ลำดับการเขียน, heartbeat วนที่ 16 บิต, PLC offline ไม่นับ heartbeat, Tuya ล่มบันทึก event ครั้งเดียว |
| `test_ladder_trusts_battery_while_fed_and_alarms_when_held` | กับ mock + ladder: ส่งอยู่ → ไม่มี alarm, หยุดส่ง → alarm 5 |

### ทดสอบด้วยมือ (ไม่ต้องมี Tuya)

```bash
docker compose run --rm gateway python -m app.sim.battery_demo
.venv/bin/python -m app.sim.battery_demo
```

ใช้ Tuya ปลอมตาม timeline ~30 วิ (เวลาย่อ: heartbeat 0.5 วิ, timeout 3 วิ) แล้วสรุป PASS/FAIL:

| ช่วง | Tuya ส่ง | ที่ควรเห็น |
|---|---|---|
| 1 | 88 % | heartbeat เพิ่มขึ้นเรื่อยๆ, ไม่มี alarm |
| 2 | ล่ม | heartbeat ค้าง → `Heartbeat Lost` (ค่าแบตใน PLC ยังเป็น 88 แต่ ladder ไม่เชื่อแล้ว) |
| 3 | 86 % | heartbeat เดินต่อ, alarm หายเอง |
| 4 | -1 | ไม่เขียน -1 ลง PLC, heartbeat ค้าง → `Heartbeat Lost` |
| 5 | 15 % | เขียนค่าได้, ladder ขึ้น `Battery Critical` |

exit code 1 ถ้ามีช่วงไหน FAIL

## ข้อควรรู้

- ❓ DP code ของ MPPT ยังไม่รู้ (ขึ้นกับรุ่น) ได้จาก `python -m app.tuya` แล้วใส่ใน `docs/robot-operation.md` §7.4
- ❓ ladder จริงต้องมี watchdog ของ heartbeat (timeout 10 วิ ตามค่าใน mock) และค่าแรกหลังเปิดเครื่องต้องใช้เป็นแค่จุดอ้างอิง
