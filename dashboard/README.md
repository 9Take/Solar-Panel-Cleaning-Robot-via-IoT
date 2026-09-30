# Dashboard mockup (Streamlit)

หน้าจอติดตามหุ่นยนต์ทำความสะอาดแผงโซลาร์ ทำแยกไว้ในโฟลเดอร์นี้ทั้งหมด **ไม่แก้ไฟล์ของ gateway หรือไฟล์กลางของ repo** (`docker-compose.yml`, `.env.example`, `CLAUDE.md`, `README.md` ที่ root ไม่ถูกแตะ)

- บันทึกงาน (อ่านอันนี้ก่อน): [LOG.md](LOG.md)
- Branch: `feature/dashboard` (ยังไม่ merge เข้า `main`)

## ไฟล์ในโฟลเดอร์นี้

| ไฟล์ | หน้าที่ |
|---|---|
| `app.py` | ตัว dashboard ทั้งหมด (ไฟล์เดียว) |
| `Dockerfile` | image `solarbot-dashboard-mockup` (python:3.12-slim, uid 1000 เหมือน gateway) |
| `docker-compose.yml` | compose แยกของ dashboard (ไม่แตะ compose หลัก) |
| `requirements.txt` | streamlit, pandas, PyYAML, tzdata |
| `.env.example` | ค่าเฉพาะ dashboard (สภาพอากาศ) → copy เป็น `dashboard/.env` |
| `LOG.md` | บันทึกงานสำหรับทีม |
| `CLAUDE.md` | กฎสำหรับ Claude เวลาแก้ dashboard (อยู่ในเครื่องเท่านั้น ไม่ commit ตาม `.gitignore`) |

## วิธีรัน (Docker)

รันจาก root ของ repo:

```bash
cp .env.example .env                        # ค่าของ gateway (ถ้ายังไม่มี)
cp dashboard/.env.example dashboard/.env    # พิกัดสภาพอากาศตั้งไว้ที่ ต.หน้าไม้ แล้ว
docker compose up -d                        # gateway (compose หลัก)
docker compose -f dashboard/docker-compose.yml up -d --build
docker compose -f dashboard/docker-compose.yml logs -f
```

เปิด `http://<ip ของ Pi>:8502` จากมือถือหรือคอมใน LAN เดียวกัน

หยุด: `docker compose -f dashboard/docker-compose.yml down` (gateway ยังรันต่อ)

ไม่มี Docker (ทดสอบบนเครื่องตัวเอง): `pip install -r dashboard/requirements.txt` แล้ว `streamlit run dashboard/app.py`

## Dashboard ต่อกับ gateway ยังไง

```
gateway (python -m app) ──เขียน──> logs/gateway.db <──อ่าน── dashboard
                                        │
                     schedules table <──┴── dashboard แก้ได้แค่ตารางเวลา
```

- อ่าน `latest` / `snapshots` / `events` จาก `HISTORY_DB` แบบอ่านอย่างเดียว **ไม่คุย Modbus เอง**
- แก้ได้อย่างเดียวคือตาราง `schedules` (step 7) gateway ใช้ตารางใหม่ภายใน 5 วินาที
- ไม่มี `logs/gateway.db` → ใช้ข้อมูลจำลอง พร้อมแถบด้านข้างไว้กดจำลองฮาร์ดแวร์
- สภาพอากาศจาก Open-Meteo (ฟรี ไม่ต้องมี key) ไม่ตั้งพิกัดหรือเน็ตล่ม → ใช้ข้อมูลจำลองและบอกเหตุผลบนจอ

## หน้าจอ (ตามที่เจ้าของโปรเจกต์กำหนด)

1. **สภาพอากาศ** อยู่บนสุด + คำแนะนำว่าควรทำความสะอาดไหม (ไม่ควร: ฝนกำลังตก, โอกาสฝนใน 3 ชม. ≥ 60 %, ลม ≥ 30 km/h)
2. **สถานะหุ่นยนต์** 3 แบบ: กำลังทำงาน (Cleaning/Returning) / หยุด (Idle/Alarm) / อยู่ที่ Home
3. **ตำแหน่ง** 3 จุด: ปลาย 1 (X0) / ระหว่างแผง / ปลาย 2 (X1) ปลายที่จอดอยู่คือ Home
4. **Emergency** แถบแดงเมื่อ E-stop ถูกกด + แถบเตือน PLC offline / gateway ไม่อัปเดต
5. **แบตเตอรี่ 48 V** (Tuya %, PZEM V/A/W/Wh), **เหตุการณ์ล่าสุด**, **PLC Tag Monitor**
6. **ตารางเวลาโหมดอัตโนมัติ**: เวลา, วัน, จำนวนรอบ (1–100, ว่าง = ใช้ค่าใน PLC)

- **ไม่มีปุ่มควบคุม** Start/Stop และเลือกโหมดทำที่ตัวเครื่อง
- ยังไม่มีระบบ user/login
