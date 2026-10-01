# เรื่องที่ยังไม่ตัดสินใจ (open decisions)

> อัปเดตล่าสุด: 2026-10-01
> แต่ละข้อมี: **สถานะตอนนี้ → ทางเลือก → ต้องการข้อมูลอะไรก่อนตัดสินใจ**
> ตัดสินใจแล้วให้ย้ายไปหัวข้อ "ตัดสินใจแล้ว" ด้านล่าง พร้อมวันที่และเหตุผล

## 1. ค่า battery % จาก Tuya MPPT

**สถานะ (ทดสอบ 2026-10-01):**
- Tuya API ใช้ได้ (project อยู่ที่ data center Singapore, endpoint `https://openapi-sg.iotbing.com`)
- เครื่อง "MPPT Solar Charge Controller" (category `MPPT`) ตอบ `[]` จาก `/v1.0/iot-03/devices/{id}/status`
  ซึ่งเป็น path ที่ `app/tuya.py` ใช้อยู่ เพราะรุ่นนี้ไม่มี standard instruction set
  → นี่คือสาเหตุที่วันเทสหน้างาน "Tuya ไม่ส่งข้อมูล"
- ค่าจริงอ่านได้จาก `/v2.0/cloud/thing/{id}/shadow/properties` (37 DP)
- **ไม่มี DP ที่เป็น battery %** ตัวที่น่าจะเกี่ยว:
  - `phase_b` (DP 104–106 เป็น `raw`): bytes `01 e4 00 00 02 08` → `0x01E4 = 484` น่าจะเป็น 48.4 V (ยังเดา)
  - `undervol_value` = 420 (42.0 V), `overvol_value` = 540 (54.0 V): แรงดันตัด/เต็มที่ตั้งใน MPPT
  - `fault` (bitmap): Standby, Bat_Over, Bat_Under, Pv_Over, Load_Short, Load_Over, Charge, DisCharge

**ทางเลือก:**
- **A. Pi แปลงแรงดัน → %** (เช่น 42.0 V = 0 %, 54.0 V = 100 %) แล้วส่ง `battery_pct` เหมือนเดิม
  ladder ไม่ต้องแก้ แต่แบต Li มี curve ไม่เป็นเส้นตรง % ช่วงกลางจะคลาด
- **B. ส่งแรงดันเข้า PLC แทน %** แล้วให้ ladder ตั้งเกณฑ์เป็นโวลต์
  ตรงกับหลัก "ladder ตัดสินใจเรื่องแบต" มากกว่า แต่ต้องเพิ่ม tag ใหม่และแก้ ladder

**ต้องการก่อนตัดสินใจ:**
- ตอน MPPT online หน้าจอแสดงแรงดันแบตเท่าไร (เทียบกับ `phase_b` ว่าเป็น 48.4 V จริงไหม)
- แอป Tuya / Smart Life มีหน้าแสดง % แบตไหม (ถ้ามี แปลว่า % คำนวณที่แอป ไม่ได้มาจากเครื่อง)

**งานที่ตามมา (ไม่ว่าเลือกทางไหน):** แก้ `app/tuya.py` ให้อ่านจาก v2.0 shadow และ decode `raw` DP

## 2. เกณฑ์แบตใน Python ไม่ตรงกับ PLC

**สถานะ:** เกณฑ์ออกทำงาน / กลับจุดพักอยู่ใน PLC (ตัดสินใจแล้ว ดูด้านล่าง)
ตอนเทส PLC ตั้งเกณฑ์ออกทำงานเป็น 40 % แต่ใน Python ยังมี `BATTERY_START_MIN_PCT = 80`
(`app/robot.py`) ใช้ใน:
- pre-check ของ `app/commander.py` ก่อนส่ง Start (ไว้อธิบายเหตุผลที่ไม่ออก)
- sim (`app/sim/ladder.py`) และข้อความใน dashboard

ถ้าแบตจริงอยู่ระหว่างเกณฑ์ PLC กับ 80 % **Pi จะปฏิเสธ Start เอง** ทั้งที่ PLC ยอมให้ออก

**ทางเลือก:**
- **A.** ladder เก็บเกณฑ์ใน D register แล้ว Pi อ่านอย่างเดียว (ต้องได้เลข D จาก ladder จริง)
- **B.** แบบ A แต่ให้ Pi เขียนได้ (แก้จาก dashboard ได้ แต่ Pi จะลดเกณฑ์ความปลอดภัยได้)
- **C.** ย้าย 80 ไปไว้ใน `.env` แล้วคอยแก้ให้ตรงกับ ladder เอง
- **D.** ตัด pre-check เรื่อง % ออกจาก Python ให้ PLC ตัดสินอย่างเดียว

## 3. branch `test/fake-tuya-battery`

**สถานะ:** push แล้ว **ห้าม merge** เพิ่ม `BATTERY_FAKE_PCT` ให้ gateway ส่ง % คงที่ + heartbeat
แทน Tuya ใช้เทสกับ PLC จริงบน bench ตอน Tuya ยังใช้ไม่ได้

**ต้องตัดสินใจ:** พอข้อ 1 เสร็จแล้ว จะลบ branch นี้ทิ้ง หรือเก็บไว้เป็นเครื่องมือเทส
(ถ้าเก็บ ต้องมีคำเตือนชัดเจน เพราะทำให้ PLC เชื่อค่าแบตปลอม)

## 4. tag map จริงจาก ladder

**สถานะ:** address M/D ใน `config/plc_tags.yaml` ยังเป็นค่าสมมติ (❓ ใน `docs/robot-operation.md`)
ladder จริงบน PLC อาจไม่ตรงกับ `plc/robot_reference.il` (branch `feature/ladder-il`)

**ต้องการ:** export / screenshot ladder จริง เพื่อเทียบ address ทีละ tag

## 5. Remote access (นอก LAN)

**สถานะ:** dashboard ใช้ใน LAN (Wi-Fi / hotspot) เท่านั้น มีแค่ `DASHBOARD_PASSWORD`
**ทางเลือก:** tunnel (เช่น Cloudflare Tunnel / Tailscale) + auth จริง หรือยังไม่ทำในรอบเดโม

---

## ตัดสินใจแล้ว

| วันที่ | เรื่อง | ผล | เหตุผล |
|---|---|---|---|
| 2026-09-28 | UI stack | Streamlit (LAN demo) | ง่าย ทำเร็ว ใช้ได้ทั้งมือถือและ PC |
| 2026-10-01 | dashboard ที่ใช้ | `feature/step8-streamlit-ui` merge เข้า main (PR #9) | review แล้วไม่มีปัญหา |
| 2026-10-01 | เกณฑ์แบตออกทำงาน / กลับจุดพัก | อยู่ใน PLC ตามเดิม | ladder เป็นคนตัดสินใจเรื่องแบต |
