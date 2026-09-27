# ขั้นที่ 7 — Schedule (ตั้งเวลาทำความสะอาด)

## หน้าที่

สั่งหุ่นออกทำความสะอาดตามเวลาที่ตั้งไว้ในโหมด Auto ตารางเวลาเก็บใน SQLite แก้ได้จาก CLI ตอนนี้ และจาก UI ในอนาคต

| ไฟล์ | หน้าที่ |
|---|---|
| `app/schedule.py` | ตาราง `schedules`, ฟังก์ชันจัดการ (`add`, `remove`, `set_enabled`, `list_all`), `Scheduler`, CLI |

## ทำงานยังไง

### ตาราง `schedules` (ใน `logs/gateway.db`)

| คอลัมน์ | ความหมาย |
|---|---|
| `at` | เวลา `HH:MM` ตาม `SCHEDULE_TZ` (ค่าเริ่มต้น Asia/Bangkok) |
| `days` | วัน เช่น `mon,wed,fri` (ทุกวัน = ครบ 7 วัน) |
| `cycles` | จำนวนรอบ 1–100 หรือ NULL = ใช้ค่า `cycles_setpoint` ที่อยู่ใน PLC |
| `enabled` | 1 = ใช้งาน |
| `created_ts` | เวลาที่เพิ่ม |
| `last_run` | วันที่รัน/ข้ามล่าสุด (`YYYY-MM-DD`) กันรันซ้ำในวันเดียว |

### ขั้นตอนทุก 5 วิ

```
อ่านตาราง (แก้ตารางแล้วมีผลทันที ไม่ต้อง restart)
แต่ละรายการที่ enabled, วันนี้อยู่ใน days, ถึงเวลาแล้ว, วันนี้ยังไม่ได้รัน:
  1. บันทึก last_run = วันนี้  ← ทำก่อนสั่ง: ถ้า gateway ดับกลางคันจะไม่สั่งซ้ำ
  2. เพิ่มรายการหลังเวลาของวันนี้ไปแล้ว → ไม่ทำอะไร เริ่มพรุ่งนี้
  3. เลยเวลาเกิน 60 วิ (gateway/PLC ล่มตอนถึงเวลา) → ข้าม, event "missed"
  4. อ่าน mode_switch: Manual หรืออ่านไม่ได้ → ข้าม, event "skipped"
  5. ส่ง cycles (ถ้าตั้ง) → ไม่สำเร็จ → ข้าม, event "skipped"
  6. ส่ง start → สำเร็จ: event "started" / ถูกปฏิเสธ: event "skipped" + เหตุผลจาก commander
```

- รันได้วันละครั้งต่อรายการ **ไม่ลองซ้ำ** (ตรงกับกฎข้อ 8 ของขั้นที่ 5)
- เรียก `PlcCommander` ตรงๆ ไม่ผ่านคิว: คิวรันแต่ละคำสั่งพร้อมกัน ถ้าส่ง `cycles` + `start` ผ่านคิว `start` จะโดนปฏิเสธว่า "another command is in progress" การเรียกตรงยังใช้กฎความปลอดภัยครบ
- scheduler ไม่เช็กเองว่าหุ่นอยู่ HOME หรือแบตพอ ให้ commander และ ladder ตัดสิน แล้วเก็บเหตุผลไว้

### Timezone

container ใช้เวลา UTC ถ้าไม่มี `SCHEDULE_TZ` เวลา 08:00 จะเป็น 15:00 เวลาไทย image ติดตั้ง `tzdata` ไว้แล้ว ใส่ชื่อ timezone ผิด gateway จะไม่ยอมเริ่ม

## วิธีใช้

```bash
.venv/bin/python -m app.schedule list
.venv/bin/python -m app.schedule add 08:00 --days mon,wed,fri --cycles 2
.venv/bin/python -m app.schedule add 16:30             # ทุกวัน ใช้จำนวนรอบใน PLC
.venv/bin/python -m app.schedule disable 2
.venv/bin/python -m app.schedule enable 2
.venv/bin/python -m app.schedule remove 2

docker compose exec gateway python -m app.schedule list
```

ตัวอย่างผล:

```
Schedules (Asia/Bangkok, now Mon 02:05):
  #1 08:00 mon,wed,fri, 2 cycle(s)              next Mon 28 Sep 08:00
  #2 17:30 every day, PLC cycles_setpoint       disabled
```

ดูผลการรัน:

```bash
sqlite3 logs/gateway.db "SELECT datetime(ts,'unixepoch','localtime'), new, message FROM events WHERE kind='schedule' ORDER BY id DESC LIMIT 10"
```

อย่าลืม: schedule ทำงานเฉพาะเมื่อสวิตช์ X3 อยู่ที่ **Auto** (OFF)

## วิธีเทส

```bash
.venv/bin/python -m pytest tests/test_schedule.py -v
```

test ใช้นาฬิกาปลอม (ตั้งเวลาเป็นวันจันทร์ 28 ก.ย. 2026 ตามเวลาไทย) ไม่ต้องรอเวลาจริง

| test | ตรวจอะไร |
|---|---|
| ตาราง | เพิ่ม/เรียง/แปลงรูปแบบ, ปฏิเสธเวลา/วัน/รอบผิด, enable/disable/remove, คำนวณเวลาครั้งถัดไป |
| `test_fires_cycles_then_start_once` | ถึงเวลา → cycles แล้ว start ครั้งเดียว |
| `test_runs_again_next_day`, `test_other_weekday_and_disabled_do_not_fire` | วันถัดไปรันใหม่, วันอื่น/ปิดใช้งานไม่รัน |
| `test_missed_run_is_skipped_and_recorded` | เลยเวลา 20 นาที → missed ไม่สั่ง |
| `test_added_after_todays_time_waits_for_tomorrow` | เพิ่มหลังเวลา → รอพรุ่งนี้ |
| `test_manual_mode_*`, `test_plc_offline_*`, `test_refused_start_*`, `test_failed_cycles_*` | ไม่พร้อม → skipped พร้อมเหตุผล และไม่ลองซ้ำ |
| `test_schedule_starts_the_simulated_robot` | กับ mock + หุ่นจำลอง: ตั้งเวลาแล้วหุ่นเข้า CLEANING และ `cycles_setpoint` = 4 |

### ทดสอบด้วยมือกับ mock

1. รัน mock + gateway (ดู [README](README.md))
2. ดูเวลาตอนนี้ แล้วเพิ่มรายการที่อีก 1–2 นาที: `python -m app.schedule add 14:32 --cycles 1`
3. `python -m app.read --watch robot_state cycle_count` → ถึงเวลาจะเห็น `robot_state` เป็น 1
4. เช็ก event ด้วยคำสั่ง `sqlite3` ข้างบน ควรเห็น `started`
5. ลองกรณีข้าม: เพิ่มรายการเวลาถัดไประหว่างหุ่นยังวิ่ง → ได้ `skipped: start rejected (robot is already Cleaning)`

## ข้อควรรู้

- ❓ ลักษณะการใช้งานจริง (กี่รอบ/วัน, เวลาไหนแผงร้อน/มีน้ำค้าง) ยังไม่ได้กำหนด ตอนนี้แค่รองรับให้ตั้งได้
- UI (ขั้นที่ 8) แก้ตารางนี้ผ่านฟังก์ชันใน `app/schedule.py` ได้เลย ไม่ต้องเขียน SQL เอง
