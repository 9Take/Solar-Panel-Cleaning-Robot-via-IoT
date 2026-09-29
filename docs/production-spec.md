# Production Spec (differences from the demo)

- [ภาษาไทย](#ภาษาไทย)
- [English](#english)

---

## ภาษาไทย

> **สถานะ: ร่าง ยังไม่ implement.** ตอนนี้พัฒนาตาม **demo** (`docs/robot-operation.md` บน `main`). ไฟล์นี้เก็บเฉพาะส่วนที่งานติดตั้งจริงต่างจาก demo. ส่วนที่ไม่ได้เขียนไว้ที่นี่ให้ถือตาม `docs/robot-operation.md`.
>
> ❓ = ยังไม่ยืนยัน (convention เดียวกับ `docs/robot-operation.md`)

### 1. ใครสั่ง Start / Stop ได้

**Demo (ตอนนี้):** dashboard กด Start ได้ทั้ง Manual และ Auto (§3, §7.1 `cmd_start` ใช้ได้ทั้งสองโหมด). เดโม่สั่งรันได้ทันทีโดยไม่ต้องรอตารางเวลา.

**งานจริง ❓:**

| คำสั่ง | Manual | Auto | เหตุผล |
|---|---|---|---|
| Start | ปุ่มหน้าเครื่อง X2 เท่านั้น | ตารางเวลาเท่านั้น | Manual = มีคนอยู่หน้าเครื่อง ห้ามหุ่นออกเดินจากคำสั่งระยะไกลโดยคนหน้าเครื่องไม่รู้ตัว. Auto = รันตามตารางอย่างเดียว ความหมายโหมดชัด |
| Stop | dashboard + X2 | dashboard + X2 | ปุ่มความปลอดภัย ห้ามจำกัดทุกโหมด (`commander.py` rule 5) |
| Return | dashboard | dashboard | สั่งกลับจุดพักจากระยะไกลได้ ไม่ทำให้หุ่นออกทำงานใหม่ |
| Reset alarm | dashboard ❓ | dashboard ❓ | ต้องปลด E-stop ที่เครื่องก่อนอยู่แล้ว (`commander.py` เช็ก `estop_ok`). ❓ งานจริงให้ reset ได้เฉพาะที่หน้าเครื่องหรือไม่ |

**บังคับใช้ที่ไหน ❓:**
- ข้อ "Manual ห้าม Start ระยะไกล" เป็นเรื่อง **safety** → ต้องอยู่ใน **ladder**: Manual (X3) ไม่รับ `cmd_start` รับแค่ X2. Pi ตรวจซ้ำได้แต่ห้ามเป็นด่านเดียว (CLAUDE.md: safety อยู่ใน PLC)
- ข้อ "Auto Start จากตารางเท่านั้น" เป็น **นโยบายการใช้งาน** → บังคับที่ Pi ได้: dashboard ซ่อน/ปิดปุ่ม Start, `commander.py` ปฏิเสธ `start` ที่ไม่ได้มาจาก scheduler พร้อมเหตุผล
- ❓ ladder ต้องแยกได้ว่า `cmd_start` มาจากตารางหรือจากคน หรือไม่ (ถ้าต้องแยก ต้องมี tag ใหม่ใน `config/plc_tags.yaml`)

### 2. หัวข้ออื่นที่อาจต่าง (ยังไม่ได้คุย)

- ❓ `DASHBOARD_PASSWORD` บังคับตั้งค่าในงานจริง (demo ปล่อยว่างได้)
- ❓ เข้า dashboard จากนอก LAN (remote access) หรือไม่

---

## English

> **Status: draft, not implemented.** Development follows the **demo** spec (`docs/robot-operation.md` on `main`). This file lists only where a real installation differs from the demo. Anything not covered here follows `docs/robot-operation.md`.
>
> ❓ = not confirmed (same convention as `docs/robot-operation.md`)

### 1. Who May Start / Stop

**Demo (now):** the dashboard can Start in both Manual and Auto (§3, §7.1 `cmd_start` works in both modes). The demo can run on demand without waiting for a schedule.

**Production ❓:**

| Command | Manual | Auto | Why |
|---|---|---|---|
| Start | front button X2 only | schedule only | Manual = someone is at the machine; a remote command must not start the robot without them knowing. Auto = runs from the schedule only, so the mode means one thing |
| Stop | dashboard + X2 | dashboard + X2 | safety control, never restricted in any mode (`commander.py` rule 5) |
| Return | dashboard | dashboard | sends the robot home remotely; never starts new work |
| Reset alarm | dashboard ❓ | dashboard ❓ | the E-stop must already be released at the machine (`commander.py` checks `estop_ok`). ❓ should production allow reset only at the machine? |

**Where it is enforced ❓:**
- "No remote Start in Manual" is a **safety** rule → it belongs in the **ladder**: in Manual (X3) ignore `cmd_start`, accept only X2. The Pi may check it too but must not be the only guard (CLAUDE.md: safety lives in the PLC).
- "Auto starts only from the schedule" is an **operating policy** → the Pi can enforce it: the dashboard hides/disables Start, and `commander.py` refuses a `start` that did not come from the scheduler, with a reason.
- ❓ Does the ladder need to tell a scheduled `cmd_start` from a human one? If so, a new tag is needed in `config/plc_tags.yaml`.

### 2. Other Possible Differences (not discussed yet)

- ❓ `DASHBOARD_PASSWORD` required in production (may be empty in the demo)
- ❓ Access to the dashboard from outside the LAN (remote access)
