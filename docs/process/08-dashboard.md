# ขั้นที่ 8 — Dashboard (Streamlit)

## หน้าที่

หน้าเว็บให้ทีม dev ดูสถานะและสั่งหุ่นได้จากมือถือหรือคอมในวง LAN (สำหรับ demo ให้บริษัท) ออก internet ยังไม่ทำในขั้นนี้

| ไฟล์ | หน้าที่ |
|---|---|
| `app/dashboard/data.py` | ดึงข้อมูลจาก SQLite, สถานะสด, ป้ายข้อความ, ส่งคำสั่งผ่านคิว (ไม่มี Streamlit เทสได้ตรงๆ) |
| `app/dashboard/main.py` | หน้า Streamlit: login, สถานะ, แท็บ Control / Schedule / History |
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
- **คำสั่ง** เขียนลงตาราง `commands` แล้วรอผลจาก gateway สูงสุด 10 วิ กฎความปลอดภัยของขั้นที่ 5 ใช้ครบเพราะไปทางเดียวกับ CLI ปุ่ม STOP อยู่บนสุดและใหญ่ที่สุด
- **Schedule** ใช้ฟังก์ชันใน `app/schedule.py` ตัวเดียวกับ CLI
- **History** กราฟแบต % และกำลังไฟ W **แยก 2 กราฟ** (หน่วยต่างกัน ไม่ใช้กราฟ 2 แกน) + ตาราง event ล่าสุด 100 รายการ
- สถานะใช้ไอคอน + ข้อความคู่กันเสมอ ไม่ใช้สีอย่างเดียว

### Login

- ตั้ง `DASHBOARD_PASSWORD` → ต้องใส่รหัสก่อนเห็นอะไรในหน้า
- ว่าง → ไม่มี login และหน้าเว็บขึ้นคำเตือน เพราะใครในวง Wi-Fi เดียวกันก็สั่งหุ่นได้

## วิธีใช้

```bash
# บน Pi (หรือเครื่อง dev ที่มี Docker)
docker compose up -d --build                     # gateway + dashboard
docker compose --profile sim up -d --build       # + mock PLC (ตั้ง PLC_HOST=plc-sim และ SIM_FAKE_PI_BATTERY=true)
docker compose logs -f dashboard
```

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
| หน้าเว็บ (Streamlit `AppTest`, ไม่ต้องเปิด browser) | แสดงค่าจริงจาก DB, ไม่มีข้อมูล, กด STOP แล้วมีแถวในคิว, ตั้งรหัสแล้วไม่เห็นอะไรก่อน login / รหัสผิด / รหัสถูก |

test หน้าเว็บช้ากว่า test อื่น (import pandas/altair ครั้งแรก ~20 วิบนดิสก์ /mnt/d)

### ทดสอบด้วยมือ

1. `docker compose --profile sim up -d --build`
2. เปิด `http://localhost:8501` บนคอม และ `http://<IP เครื่อง>:8501` บนมือถือ
3. ควรเห็น 🟢 PLC online, State 🏠 Home, แบต 🔋 90 %
4. กด Start → ✅ started cleaning, State เปลี่ยนเป็น 🧹 Cleaning ในไม่กี่วิ
5. กด STOP → ✅ stopped
6. `docker compose stop gateway` → ภายใน 10 วิ แถบขึ้น ⚪ Gateway not updating, กดปุ่มจะได้ ⏱️ no answer
7. แท็บ Schedule: เพิ่มรอบอีก 2 นาที แล้วรอดู state เปลี่ยน
8. แท็บ History: หลังรันสักพักจะเห็นกราฟแบตและกำลังไฟ

## ข้อควรรู้

- ⚠️ ห้ามเปิด port 8501 ออก internet ตรงๆ ตอนทำขั้นออก internet ให้ใช้ tunnel (เช่น Cloudflare Tunnel / Tailscale) + login จริง
- ขั้นนี้ทำเป็นเวอร์ชันเทียบกับของเพื่อนในทีม ยังไม่ merge เข้า `main`
- Streamlit รันสคริปต์ใหม่ทั้งหน้าทุกครั้งที่กดปุ่ม เหมาะกับ demo แต่ถ้าอนาคตต้องการ API ให้แอปอื่นเรียก ต้องเพิ่ม REST API แยก
