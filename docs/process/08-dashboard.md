# ขั้นที่ 8 — Dashboard (Streamlit)

## หน้าที่

หน้าเว็บให้ทีม dev ดูสถานะและสั่งหุ่นได้จากมือถือหรือคอมในวง LAN (สำหรับ demo ให้บริษัท) ออก internet ยังไม่ทำในขั้นนี้

| ไฟล์ | หน้าที่ |
|---|---|
| `app/dashboard/data.py` | ดึงข้อมูลจาก SQLite, สถานะสด, ป้ายข้อความ, ส่งคำสั่งผ่านคิว (ไม่มี Streamlit เทสได้ตรงๆ) |
| `app/dashboard/main.py` | หน้า Streamlit: login, สถานะ, แท็บ Control / Schedule / History |
| `app/weather.py` | ดึงอากาศที่แผงจาก Open-Meteo (ฟรี ไม่ต้องใช้ key): อุณหภูมิ, เมฆ, ความชื้น |
| `requirements-dashboard.txt` | streamlit (เฉพาะ image dashboard) |
| `Dockerfile` target `dashboard`, service `dashboard` ใน compose | รันใน Docker port 8501 |

## ทำงานยังไง

```
มือถือ / คอม ──HTTP (LAN)──> dashboard :8501 (Streamlit)
                                  │  SQLite เท่านั้น (ไม่คุย Modbus)
                                  ▼
                           logs/gateway.db  <── gateway (poller, คิวคำสั่ง, schedule)
```

- **สถานะ** รีเฟรชเองทุก `DASHBOARD_REFRESH_S` (2 วิ) เฉพาะส่วนสถานะ ไม่โหลดทั้งหน้า (`st.fragment`)
- **แถบการเชื่อมต่อ**:
  - 🟢 PLC online
  - 🔴 PLC offline (แสดงค่าล่าสุดที่รู้)
  - ⚪ gateway ไม่อัปเดตเกิน 10 วิ (gateway ดับ แถว `latest` จะค้าง `online=1` ตลอด เลยดูจากอายุของข้อมูลแทน)
- **คำสั่ง** เขียนลงตาราง `commands` แล้วรอผลจาก gateway สูงสุด 10 วิ กฎความปลอดภัยของขั้นที่ 5 ใช้ครบเพราะไปทางเดียวกับ CLI ปุ่ม STOP อยู่บนสุดของหน้า (เหนือช่องสถานะ นอกแท็บ) เพราะบนมือถือช่องสถานะเรียงลงมาเป็นแถวเดียว ถ้าอยู่ข้างล่างต้องเลื่อนหา
- **Schedule** ใช้ฟังก์ชันใน `app/schedule.py` ตัวเดียวกับ CLI
- **History** กราฟแบต % และกำลังไฟ W **แยก 2 กราฟ** (หน่วยต่างกัน ไม่ใช้กราฟ 2 แกน) + ตาราง event ล่าสุด 100 รายการ
- **อากาศที่แผง** (ใต้ช่องสถานะ) อุณหภูมิ °C, เมฆ %, ความชื้น % จาก Open-Meteo ที่พิกัด `WEATHER_LAT` / `WEATHER_LON`
  ดึงใหม่ทุก `WEATHER_REFRESH_S` (600 วิ, cache ใน `st.cache_data`) ในแท็บ History มีกราฟอากาศรายชั่วโมงช่วงเวลาเดียวกับกราฟแบต/กำลังไฟ
  ไว้ดูว่าการชาร์จจากโซลาร์เซลล์แปรผันตามอากาศ: เมฆมาก → แสงถึงแผงน้อย, แผงร้อน → แปลงแสงเป็นไฟได้น้อยลง (~-0.4 %/°C เหนือ 25 °C), อากาศชื้น/หมอก → แสงกระเจิง
  - ไม่ตั้งพิกัด → ขึ้น "Weather off" / ดึงไม่ได้ → ขึ้นคำเตือน **ไม่แสดงค่าจำลอง** หน้าอื่นใช้งานได้ปกติ
  - dashboard เรียก Open-Meteo เอง gateway และ PLC ไม่ได้ใช้ค่านี้
- สถานะใช้ไอคอน + ข้อความคู่กันเสมอ ไม่ใช้สีอย่างเดียว

### Login

- ตั้ง `DASHBOARD_PASSWORD` → ต้องใส่รหัสก่อนเห็นอะไรในหน้า
- ว่าง → ไม่มี login และหน้าเว็บขึ้นคำเตือน เพราะใครในวง Wi-Fi เดียวกันก็สั่งหุ่นได้

## วิธีใช้

**ใช้งานจริง** (Pi + PLC จริง, `PLC_HOST` ใน `.env` = IP ของ PLC) — gateway + dashboard:

```bash
docker compose up -d --build
docker compose logs -f dashboard
docker compose down
```

**ทดสอบกับ mock PLC** — ไม่ต้องแก้ `.env` ไฟล์ `docker-compose.sim.yml` เพิ่ม `plc-sim`, ชี้ gateway ไปที่ mock และเก็บประวัติแยกที่ `logs/sim/gateway.db` (ไม่ปนกับของจริง):

```bash
docker compose -f docker-compose.yml -f docker-compose.sim.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.sim.yml down     # ปิดต้องใส่ -f ทั้งสองไฟล์เหมือนกัน
```

⚠️ ใช้งานจริงแล้วปุ่มบน dashboard สั่งหุ่นขยับจริง ต้องมีคนอยู่หน้างาน และควรตั้ง `DASHBOARD_PASSWORD`

เปิด `http://<IP ของ Pi>:8501` จากมือถือหรือคอมที่อยู่ในวง LAN เดียวกัน (หา IP ด้วย `hostname -I` บน Pi)

ตั้งใน `.env`:

```
DASHBOARD_PASSWORD=ตั้งรหัสสำหรับ demo
DASHBOARD_REFRESH_S=2
```

รันตรงไม่ใช้ Docker (dev):

```bash
.venv/bin/python -m streamlit run app/dashboard/main.py
```

## วิธีเทส

```bash
.venv/bin/python -m pytest tests/test_dashboard.py -v      # ต้องมี streamlit ใน venv
```

| test | ตรวจอะไร |
|---|---|
| live status | ยังไม่มีข้อมูล, online, PLC offline ค่าเก่ายังอยู่, gateway ค้าง → ⚪ |
| labels | ไอคอน/ข้อความของ state, alarm, แบต |
| history / events | ช่วงเวลา, tag ที่ไม่มีค่าเป็น None, event เรียงล่าสุดก่อน |
| `send_command` | รอผลจาก gateway, ไม่มี gateway → คืน `pending` |
| weather (`test_weather.py`) | URL ขอครบ 3 ค่า, แปลงเวลาเป็นเวลาท้องถิ่น, ค่าที่ขาดเป็น None, error ของ Open-Meteo / ไม่ใช่ JSON / รูปแบบผิด, กรองช่วงเวลา, พิกัดว่าง = ปิด, พิกัดเกินช่วง = error, หน้าเว็บแสดงค่า / ดึงไม่ได้ / ปิด |
| หน้าเว็บ (Streamlit `AppTest`, ไม่ต้องเปิด browser) | แสดงค่าจริงจาก DB, ไม่มีข้อมูล, กด STOP แล้วมีแถวในคิว, ตั้งรหัสแล้วไม่เห็นอะไรก่อน login / รหัสผิด / รหัสถูก |

test หน้าเว็บช้ากว่า test อื่น (import pandas/altair ครั้งแรก ~20 วิบนดิสก์ /mnt/d)

### ทดสอบด้วยมือ

1. `docker compose -f docker-compose.yml -f docker-compose.sim.yml up -d --build`
2. เปิด `http://localhost:8501` บนคอม และ `http://<IP เครื่อง>:8501` บนมือถือ
3. ควรเห็น 🟢 PLC online, State 🏠 Home, แบต 🔋 90 %
4. กด Start → ✅ started cleaning, State เปลี่ยนเป็น 🧹 Cleaning ในไม่กี่วิ
5. กด STOP → ✅ stopped
6. `docker compose -f docker-compose.yml -f docker-compose.sim.yml stop gateway` → ภายใน 10 วิ แถบขึ้น ⚪ Gateway not updating, กดปุ่มจะได้ ⏱️ no answer
7. แท็บ Schedule: เพิ่มรอบอีก 2 นาที แล้วรอดู state เปลี่ยน
8. แท็บ History: หลังรันสักพักจะเห็นกราฟแบตและกำลังไฟ ด้านล่างเป็นกราฟเมฆ / อุณหภูมิ / ความชื้น
9. ใต้ช่องสถานะเห็นอากาศปัจจุบันที่ site ถ้าลบ `WEATHER_LAT` ออกจาก `.env` แล้ว recreate dashboard จะขึ้น "Weather off"

## ข้อควรรู้

- ⚠️ ห้ามเปิด port 8501 ออก internet ตรงๆ ตอนทำขั้นออก internet ให้ใช้ tunnel (เช่น Cloudflare Tunnel / Tailscale) + login จริง
- ขั้นนี้ทำเป็นเวอร์ชันเทียบกับของเพื่อนในทีม เลือกแล้วและ merge เข้า `main` (PR #9)
- ค่าอากาศเป็นของพื้นที่ (grid ~ไม่กี่ กม.) ไม่ใช่วัดที่แผงจริง ใช้ดูแนวโน้ม ไม่ใช่ค่าแม่นยำ
- Streamlit รันสคริปต์ใหม่ทั้งหน้าทุกครั้งที่กดปุ่ม เหมาะกับ demo แต่ถ้าอนาคตต้องการ API ให้แอปอื่นเรียก ต้องเพิ่ม REST API แยก
