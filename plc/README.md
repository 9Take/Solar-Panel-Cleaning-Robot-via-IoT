# Reference ladder (Delta IL)

`robot_reference.il` คือ state machine ตัวเดียวกับ `app/sim/ladder.py` เขียนเป็น **IL (Instruction List)** ของ Delta DVP สำหรับ DVP-12SE11T

> ⚠️ ตำแหน่ง M/D ทั้งหมดเป็น **ค่าสมมติ** ตาม `config/plc_tags.yaml` ถ้าจะใช้กับ ladder จริง ต้องเปลี่ยน device ให้ตรงกันทั้ง 3 ที่: ไฟล์ `.il`, tag map, และ `app/sim/ladder.py`
>
> ยังไม่ได้ compile ใน ISPSoft และยังไม่ได้ลองกับ PLC จริง ต้อง compile, ดู device ซ้ำ และทดสอบตอนมอเตอร์ยกล้อลอยก่อนใช้งานจริง

## นำเข้า ISPSoft

1. สร้าง project ใหม่ เลือก PLC รุ่น DVP-12SE
2. สร้าง POU ใหม่ (Program) ภาษา **IL**
3. copy คำสั่งจาก `robot_reference.il` ไปวาง
   - ถ้า ISPSoft ไม่รับ comment ที่ขึ้นต้นด้วย `;` ให้ลบ comment ก่อน:
     `sed 's/;.*//; /^[[:space:]]*$/d' plc/robot_reference.il > robot_plain.il`
4. Compile แล้วเช็ก error/warning
5. ถ้าจะแก้เป็น ladder ให้วาดตาม IL ทีละ rung (1 rung เริ่มที่ `LD`/`LDI`/`LDP`/`LD=`…)

## โครงสร้าง

| Section | ทำอะไร |
|---|---|
| 0 | First scan (`M1002`): ตั้ง state เริ่มต้น (Home ถ้าอยู่ปลายแถว ไม่งั้น Idle), เวลาเดินเริ่มต้น 20 s |
| 1 | ขอบขาขึ้นของปุ่ม X2, limit ทั้งสองฝั่ง, ตำแหน่ง % จากเวลาเดิน |
| 2 | Heartbeat watchdog: `D111` ไม่เปลี่ยน 10 s → ไม่เชื่อค่าแบต |
| 3 | เกณฑ์แบต 25 % (กลับบ้าน), 20 % (alarm 2), 80 % (ออกงานได้) |
| 4 | Alarm ที่หยุดหุ่น: E-stop (1), limit ทั้งสองฝั่ง (4) |
| 5 | Alarm เตือน: heartbeat (5), แบตวิกฤต (2) |
| 6 | รวมคำขอ Start/Stop จาก Pi + ปุ่มหน้าเครื่อง — **Stop ชนะ Start** |
| 7–12 | Home / Idle / Cleaning / ถึงปลายแถว / กลับบ้าน / Returning |
| 13 | Reset alarm (ต้องปลด E-stop ก่อน) |
| 14 | Y0/Y1 + นับเวลาเดินทุก 100 ms (`M1012`) |
| 15 | ล้าง M100–M103 ท้าย scan (Pi ส่งเป็น pulse) |

ทุก section อ่าน state จาก `D212` (state ตอนต้น scan) เลย 1 scan ขยับได้แค่ 1 ขั้น เหมือน `elif` ใน Python

## ทดสอบ

`tests/test_ladder_il.py` รันไฟล์ `.il` ผ่าน `app/sim/il.py` (interpreter เล็กๆ รองรับเฉพาะคำสั่งที่ใช้ในไฟล์นี้) กับ plant จำลอง ใช้ scenario ชุดเดียวกับ `tests/test_sim_behavior.py`

```bash
.venv/bin/python -m pytest tests/test_ladder_il.py
```

Interpreter ไม่ใช่ DVP emulator เต็มรูปแบบ: 1 scan = 10 ms, timer เฉพาะ T0–T199 (ฐาน 100 ms), special M มีแค่ M1000/M1002/M1012 ผลทดสอบจึงยืนยันได้แค่ logic ไม่ได้ยืนยันว่า ISPSoft compile ผ่าน
