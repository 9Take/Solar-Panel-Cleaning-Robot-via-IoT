# Dashboard log (branch `feature/dashboard`)

บันทึกงาน dashboard สำหรับเพื่อนในทีม: ทำอะไรไปแล้ว ทำไม ทดสอบยังไง และเรื่องที่ยังค้าง
**รายการใหม่อยู่บนสุด** ทุกครั้งที่แก้ dashboard ให้เพิ่มรายการที่นี่ใน commit เดียวกัน

วิธีรันและภาพรวม: [README.md](README.md)

## ปัญหาที่รู้แล้ว

1. **บน Mac (Docker Desktop) gateway หยุดเขียน DB หลังรันได้ ~1 วินาที** ถ้า `logs/` เป็น bind mount
   SQLite โหมด WAL ใช้ร่วมกันผ่าน file sharing ของ Docker Desktop ไม่ได้ (gateway ไม่ขึ้น error แต่ข้อมูลค้าง)
   ทดสอบแล้ว: ย้าย DB ไปไว้ใน container หรือใช้ named volume แล้วทำงานปกติ
   **บน Pi (Linux) ไม่น่ามีปัญหานี้** แต่ยังไม่ได้ทดสอบบน Pi จริง
2. **`docker compose --profile sim up --build` ของ compose หลักพัง** (`image "solarbot-gateway:latest": already exists`)
   `gateway` กับ `plc-sim` build image ชื่อเดียวกันพร้อมกัน เป็นปัญหาเดิมบน main ไม่เกี่ยวกับ dashboard
   ทางเลี่ยง: `docker compose build gateway` ก่อน แล้วค่อย `docker compose --profile sim up -d`
3. **Repo ยังไม่มี `.gitignore` ที่ root** → ระวัง `git add .` แล้ว `.env`, `dashboard/.env`, `logs/`, `__pycache__/` ติดขึ้นไป
4. **มี dashboard อีกตัวใน `feature/step8-streamlit-ui`** (service `dashboard`, port 8501, มีปุ่ม STOP)
   ของเราใช้ชื่อ `dashboard-mockup` port 8502 และอยู่ใน compose แยก จึงรันพร้อมกันได้ แต่ทีมต้องเลือกว่าจะใช้ตัวไหนเป็นหลัก
   ข้อตกลงของเจ้าของโปรเจกต์: dashboard **ไม่มีปุ่มควบคุม**

## ยังค้าง / ต้องถาม

- พิกัดจริงของแผงสำหรับ `WEATHER_LAT` / `WEATHER_LON` (ตอนทดสอบใช้ มจพ. บางซื่อ 13.8196, 100.5139)
- กำลังไฟ solar (W) ยังไม่มี: step 6 อ่านจาก Tuya แค่ % แบต
- Address M/D ใน tag map ยังเป็นค่าสมมติ รอ ladder จริง
- E-stop ตัดไฟตรงไหน: ผังสายไฟของเจ้าของโปรเจกต์วางสวิตช์ระหว่าง MD30C กับมอเตอร์ แต่ `docs/robot-operation.md` §2.3 บอกตัดไฟเข้า MD30C
- ข้อมูลฮาร์ดแวร์จากผังสายไฟ (ยังไม่ได้ใส่ในเอกสารหลัก): แบต Li-ion 48 V 10.2 Ah + ฟิวส์ 10 A, step-down 48 → 24 V เลี้ยง PLC และ MD30C, Y0/Y1 → PC817 → PWM/DIR ของ MD30C

---

## 2026-09-29 · แยก dashboard ออกจากไฟล์หลักทั้งหมด

**ทำไม:** ให้ branch นี้ไม่กระทบไฟล์ของ main ตอน merge จะได้เพิ่มแค่โฟลเดอร์ `dashboard/`

**ทำอะไร**
- คืนไฟล์กลาง `CLAUDE.md`, `README.md`, `docker-compose.yml`, `.env.example` ให้เหมือน main ทุกตัวอักษร
- ย้ายของ dashboard เข้า `dashboard/`:
  - compose แยก `dashboard/docker-compose.yml` (project `solarbot-dashboard-mockup`)
  - ค่าสภาพอากาศย้ายจาก `.env.example` หลักไป `dashboard/.env.example` (อ่านจาก `dashboard/.env`)
  - บันทึกงานย้ายจาก `docs/dashboard-log.md` มาเป็น `dashboard/LOG.md`
  - เนื้อหา dashboard ที่เคยเพิ่มใน `CLAUDE.md` หลัก ย้ายไป `dashboard/README.md` (และ `dashboard/CLAUDE.md` ซึ่งอยู่ในเครื่องเท่านั้น ไม่ commit ตาม `.gitignore` ของเจ้าของโปรเจกต์)
- `app.py` อ่าน `dashboard/.env` ก่อน แล้วค่อย `.env` ของ repo

**ทดสอบ** (Mac, Docker Desktop)
- `git diff origin/main` ของ branch นี้ → มีแค่ไฟล์ใน `dashboard/` (7 ไฟล์) ไฟล์อื่นเหมือน main ทุกตัว
- `docker compose --profile sim up -d` (compose หลัก ไม่แก้) + `docker compose -f dashboard/docker-compose.yml up -d --build` → ขึ้นครบ 3 container
- dashboard อ่านค่าจากทั้ง `dashboard/.env` (สภาพอากาศ) และ `.env` หลัก (`SCHEDULE_TZ`) ได้ถูกต้อง
- ข้อมูลจาก gateway สด (อายุ < 1 วินาที), สภาพอากาศจาก Open-Meteo, เวลาเป็นเวลาไทย
- บน Mac ต้องใช้ override ให้ `logs` เป็น volume ร่วม (ปัญหาข้อ 1) ถ้าจะใช้ override กับ compose ของ dashboard ต้องใส่ `-f` ทั้งสองไฟล์ เพราะ override ไม่ถูกโหลดอัตโนมัติเมื่อใช้ `-f`

## 2026-09-29 · รันผ่าน Docker + แก้เวลาใน container

- เพิ่ม `dashboard/Dockerfile` (python:3.12-slim, user uid 1000 เหมือน gateway เพื่อเขียนไฟล์ SQLite ร่วมกันได้)
- เวลาทุกจุดในหน้าจอ (เหตุการณ์, อัปเดตล่าสุด, กราฟ) ใช้ `SCHEDULE_TZ` เพราะ container เป็น UTC ทำให้เวลาเพี้ยน 7 ชั่วโมง
- ใส่ `tzdata` ใน `dashboard/requirements.txt` ให้ตรงกับ gateway
- ทดสอบบน Mac, Docker Desktop, arm64 (เหมือน Pi): `plc-sim` + `gateway` + dashboard ขึ้นครบ เห็นข้อมูลจริงจาก gateway และสภาพอากาศจาก Open-Meteo เจอปัญหาข้อ 1–2 ด้านบน ใช้ทางเลี่ยงแล้วอายุข้อมูล < 1 วินาที

## 2026-09-28 · สภาพอากาศจริงจาก Open-Meteo (`b0e8e74`)

- สภาพอากาศปัจจุบัน, โอกาสฝน 8 ชม., PM10 จาก Open-Meteo (ฟรี ไม่ต้องมี key) ทุก `WEATHER_REFRESH_S` (600 วินาที)
- ไม่ตั้งพิกัด หรือเน็ตล่ม → ใช้ข้อมูลจำลองและบอกเหตุผลบนจอ
- คำแนะนำ "ไม่ควรทำความสะอาด" เมื่อ: ฝนกำลังตก, โอกาสฝนใน 3 ชม. ≥ 60 %, หรือลม ≥ 30 km/h
- กราฟโอกาสฝนแกน 0–100 % (เดิม Streamlit ปรับแกนเองจนสัดส่วนผิด)

## 2026-09-28 · ตารางเวลาใช้ตาราง `schedules` ของ gateway (`f566613`)

- อ่าน/เขียนตาราง `schedules` ใน `gateway.db` (step 7) โดยตรง gateway ใช้ตารางใหม่ภายใน 5 วินาที
- คอลัมน์: เปิดใช้, เวลา, วัน (`จ,พ,ศ` หรือ `ทุกวัน` → เก็บเป็น `mon,wed,fri`), จำนวนรอบ 1–100 (ว่าง = ใช้ `cycles_setpoint` ใน PLC), รันล่าสุด
- ตรวจค่าก่อนบันทึก กรอกผิดจะกดบันทึกไม่ได้
- **ต้องรู้:** schema และการตรวจค่าของ `schedules` ถูก copy ไว้ใน `dashboard/app.py` (เพื่อไม่ต้องลง pymodbus) ถ้าแก้ `app/schedule.py` ต้องแก้ตรงนี้ด้วย
- ทดสอบ: ตั้งเวลาจาก dashboard → `python -m app.schedule list` เห็น → ถึงเวลา gateway สั่งหุ่น Home → Cleaning และบันทึก `last_run`

## 2026-09-28 · อ่านข้อมูลจริงจาก gateway DB (`6089987`)

- อ่าน `latest` / `snapshots` / `events` จาก `HISTORY_DB` แบบอ่านอย่างเดียว ไม่คุย Modbus เอง (ตามกฎใน `CLAUDE.md` หลัก)
- แถบแดงเมื่อ PLC offline และเมื่อ gateway ไม่อัปเดตเกิน 5 วินาที
- เพิ่มตารางเหตุการณ์ล่าสุด, กราฟแรงดันจาก snapshot
- tag map อ่านจาก `config/plc_tags.yaml` ของ repo
- ตัวจำลอง: Stop ชนะ Start ตามสเปก

## 2026-09-27 · Mockup แรก (`ebe791e`)

- หน้าจอตามที่เจ้าของโปรเจกต์กำหนด (ดู README.md หัวข้อ "หน้าจอ")
- ใช้ Streamlit เพราะเจ้าของโปรเจกต์ต้องการ Python ที่ง่ายที่สุด
- ไม่มีปุ่มควบคุม ไม่มีระบบ user/login
