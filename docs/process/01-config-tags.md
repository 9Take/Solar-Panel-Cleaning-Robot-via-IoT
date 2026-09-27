# ขั้นที่ 1 — Config + tag map

## หน้าที่

รวมค่าคงที่ทั้งหมดไว้นอกโค้ด และแปลง "ชื่อ tag" เป็นตำแหน่ง Modbus ของ PLC Delta ได้ถูกต้อง ขั้นนี้เป็นโค้ดล้วน ไม่มี network

| ไฟล์ | หน้าที่ |
|---|---|
| `.env` / `.env.example` | ค่าที่เปลี่ยนตามเครื่อง: IP PLC, port, timeout, key ของ Tuya ฯลฯ (`.env` ไม่ commit) |
| `app/config.py` | `Settings` อ่าน `.env` ด้วย pydantic-settings ตรวจชนิดและช่วงค่า ผิดแล้วหยุดทันที (fail fast) |
| `config/plc_tags.yaml` | tag map: ชื่อ tag → device ใน PLC (M100, D10, X0 …) ทิศทาง ชนิด scale หน่วย |
| `app/tags.py` | โหลดและตรวจ tag map (`load_tags`, `TagMap`, `Tag`) |
| `app/delta.py` | `to_modbus()` แปลงชื่อ device ของ Delta → address Modbus |
| `app/codec.py` | แปลงค่า tag ↔ register ดิบ (ใช้ร่วมกันทั้ง mock และ client) |

## ทำงานยังไง

### 1. ชื่อ device → address Modbus (`app/delta.py`)

| Device | ช่วง | Address เริ่ม | ตัวอย่าง |
|---|---|---|---|
| X (input) | X0–X377 **ฐาน 8** | 0x0400 | X10 = input ตัวที่ 8 → 0x0408 |
| Y (output) | Y0–Y377 ฐาน 8 | 0x0500 | Y1 → 0x0501 |
| M | M0–M1535 | 0x0800 | M100 → 0x0864 |
| M | M1536–M4095 | 0xB000 | M1536 → 0xB000 |
| D | D0–D4095 | 0x1000 | D110 → 0x106E |
| D | D4096–D9999 | 0x9000 | D4096 → 0x9000 |

- เลข X/Y เป็นเลขฐาน 8: `X8`, `X9` ผิด (ไม่มีเลข 8, 9 ในฐาน 8)
- address เป็นแบบ 0-based ตามที่ pymodbus ใช้ ห้ามเอาเลข 1-based ในเอกสาร Delta (เช่น M0 = 002049) มาใช้ตรงๆ

### 2. ตรวจ tag map ตอนโหลด (`app/tags.py`)

ทุก tag ถูกตรวจตอนเริ่มโปรแกรม ผิดข้อใดข้อหนึ่งโปรแกรมไม่ยอมเริ่ม:

- device ต้องแปลงได้ (X/Y/M/D อยู่ในช่วง)
- device แบบ bit (X, Y, M) ต้องเป็น `type: bool` ส่วน D ห้ามเป็น bool
- **X และ Y ต้องเป็น `dir: read` เท่านั้น** Pi ไม่เขียน input และไม่สั่ง output ตรงๆ ใช้ M/D เป็น "คำขอ" ให้ ladder ตัดสินใจ
- tag สองตัวห้ามทับ register เดียวกัน (เช่น uint32 ที่ D24 ใช้ D24+D25 แล้ว ห้ามมี tag อื่นที่ D25)
- field เกินห้ามมี (`extra="forbid"`) กันพิมพ์ชื่อ field ผิดแล้วไม่รู้ตัว
- เรียกชื่อ tag ที่ไม่มี → `KeyError: Unknown tag ...; add it to the tag map first`

### 3. แปลงค่า (`app/codec.py`)

- ค่าจริง = ค่าดิบ × `scale` เช่น `pzem_voltage` scale 0.01 → register 4850 = 48.50 V
- ค่า 32 บิตของ Delta: **word ต่ำอยู่ D(n), word สูงอยู่ D(n+1)** (กลับกับ `DataType.INT32` ของ pymodbus) เลยแปลงเองแบบ 2 register
- ค่าเกินช่วงของชนิด (เช่น uint16 > 65535) หรือจำนวน register ไม่ตรง → error ไม่ตัดค่าเงียบๆ

## วิธีใช้

```bash
cp .env.example .env          # แล้วแก้ค่าให้ตรงเครื่อง
```

แก้ tag map: เพิ่ม tag ใน `config/plc_tags.yaml`

```yaml
  my_tag: {device: D120, dir: read, type: uint16, scale: 0.1, unit: V, desc: "..."}
```

แล้วเช็กว่าโหลดผ่าน:

```bash
.venv/bin/python -c "from app.tags import load_tags; t = load_tags('config/plc_tags.yaml'); print(len(t.tags), t['my_tag'].addr)"
```

เพิ่ม key ใหม่ใน `.env` ต้องเพิ่มใน `.env.example` (พร้อม comment) และใน `Settings` ด้วยเสมอ

## วิธีเทส

```bash
.venv/bin/python -m pytest tests/test_config.py tests/test_tags.py tests/test_delta.py tests/test_codec.py -v
```

| test | ตรวจอะไร |
|---|---|
| `test_config` | อ่านค่าจาก env ได้, ไม่มี `PLC_HOST` → error, port ผิดช่วง → error |
| `test_delta` | ตาราง address ทุกช่วง, เลขฐาน 8, เลขเกินช่วง/ชื่อผิดถูกปฏิเสธ |
| `test_tags` | tag map ของ repo โหลดได้, tag ผิดกฎถูกปฏิเสธ, register ทับกันถูกปฏิเสธ |
| `test_codec` | แปลงไป-กลับครบทุกชนิด, scale, uint32 แบบ Delta, ค่าเกินช่วง |

## ข้อควรรู้

- ⚠️ **ตำแหน่ง M/D ใน `plc_tags.yaml` ตอนนี้เป็นค่าสมมติ** (มีป้าย `ASSUMED`) ต้องแก้ให้ตรงกับ ladder จริงก่อนต่อ PLC จริง ส่วน X0–X4, Y0–Y1 ยืนยันแล้ว
- โค้ดอ้างถึง tag ด้วยชื่อเท่านั้น ห้ามใส่ address หรือ IP ลงในโค้ด Python
