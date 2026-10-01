# Operation Flowchart

- [ภาษาไทย](#ภาษาไทย)
- [English](#english)

---

## ภาษาไทย

### 1 รอบทำความสะอาด

ตั้งแต่สั่ง Start จนหุ่นกลับจุดพัก. แบ่ง 3 ช่วงตามว่า **ใครตัดสิน**: ผู้สั่ง → Pi ตรวจก่อนส่ง → ladder ใน PLC ตัดสินและสั่งมอเตอร์.
State ทั้งหมดดู `docs/robot-operation.md` §4. ค่าที่มี ❓ ยังไม่ยืนยันกับ ladder จริง (ตอนนี้อ้างตาม mock `app/sim/ladder.py`).

```mermaid
flowchart TB
    subgraph trig["1 · สั่งงาน"]
        direction LR
        ui(["Dashboard: กด Start"])
        sch(["ถึงเวลาตามตาราง"])
        btn(["ปุ่มหน้าเครื่อง X2"])
    end

    subgraph pi["2 · Raspberry Pi: ตรวจก่อนส่ง (ไม่ใช่ตัวตัดสินสุดท้าย)"]
        q["commands queue<br/>หมดอายุ 5 s"]
        late{"สายเกิน 60 s?"}
        mode{"สวิตช์ X3 = Auto?"}
        cyc["เขียน cycles_setpoint"]
        pre{"E-stop กด / Alarm /<br/>กำลังเดินอยู่?"}
        pulse["cmd_start = 1<br/>รอ ladder ล้าง bit"]
        ack{"ack ภายใน 2 s?"}
        clr["Pi ล้าง bit เอง"]
        log[("events log<br/>missed · skipped · rejected<br/>not_acknowledged")]
    end

    subgraph plc["3 · PLC ladder: ตัดสิน + เดิน"]
        where{"state ตอนนี้?"}
        ret["Returning<br/>กลับจุดพักใกล้สุด"]
        park["Home: จอดรอ Start ใหม่"]
        stay["ไม่ออกเดิน อยู่ Home<br/>Pi บันทึก not_started + เหตุผล"]
        may{"แบต ≥ 80 %<br/>และ heartbeat ปกติ?"}
        go["Cleaning<br/>Y1 ตั้งทิศออกจากจุดพัก<br/>Y0 เดิน (แปรงหมุนตาม)"]
        far["ถึง limit ฝั่งตรงข้าม<br/>หยุด 2 s แล้วกลับทิศ"]
        near["ถึง limit ฝั่งเริ่ม<br/>cycle_count + 1"]
        done{"Auto: ครบ cycles_setpoint?<br/>Manual: ไม่ครบเสมอ"}
        home(["Home: จอดที่ปลายแถว<br/>ชาร์จ solar ต่อ"])
    end

    ui --> q --> pre
    sch --> late
    late -- "ใช่ (Pi/PLC ล่มช่วงนั้น)" --> log
    late -- ไม่ --> mode
    mode -- "Manual → skipped" --> log
    mode -- Auto --> cyc --> pre
    pre -- "ใช่ → rejected" --> log
    pre -- ไม่ --> pulse --> ack
    ack -- ไม่ --> clr --> log
    ack -- ใช่ --> where
    btn -- "ตรงเข้า ladder" --> where

    where -- "Idle (กลางแผง)" --> ret --> park
    where -- Home --> may
    may -- ไม่ --> stay
    may -- ใช่ --> go --> far --> near --> done
    done -- "ยังไม่ครบ" --> go
    done -- ครบ --> home
```

**อ่านแผนภาพ:** ถ้า Pi ปฏิเสธ จะบันทึกเหตุผลลง `events` ไว้ให้ dashboard แสดง. แต่ถ้า Pi ส่งคำสั่งผ่านไปแล้ว ladder ยังตัดสินเองเสมอว่าจะออกเดินหรือไม่ (แบต ≥ เกณฑ์ใน PLC, heartbeat). เกณฑ์ตาม spec คือ 80 % แต่ตอนเทสตั้งใน PLC เป็น 40 % ส่วน pre-check ฝั่ง Pi ยังใช้ 80 % ดู [open-decisions.md](open-decisions.md) ข้อ 2. ปุ่มหน้าเครื่องเข้า ladder ตรง ไม่ผ่าน Pi.

### เหตุแทรกระหว่างเดิน (Cleaning / Returning)

แยกจากแผนภาพบน เพราะเกิดได้ทุกจุดของรอบ ถ้าวาดรวมจะมีเส้นจากทุกกล่อง.

```mermaid
flowchart LR
    moving(["กำลังเดิน<br/>Cleaning / Returning"])
    moving -- "Stop (dashboard / ปุ่ม X2)<br/>ไม่ถูกบล็อก, ชนะ Start" --> idle["Idle: หยุดกลางแผง"]
    moving -- "E-stop: ตัดไฟมอเตอร์ทาง hardware" --> alarm["Alarm 1<br/>รอปลด E-stop + Reset"]
    moving -- "cmd_return / แบตต่ำ (เกณฑ์ ❓)" --> ret["Returning → Home"]
    moving -- "heartbeat หาย ❓" --> finish["ทำรอบนี้ให้จบ → Home<br/>ไม่เริ่มรอบใหม่"]
```

- Stop, cmd_return และแบตต่ำ ใช้ได้กับ Cleaning. ตอน Returning ใช้ได้แค่ Stop กับ E-stop.
- E-stop ไม่ต้องรอ PLC หรือ Pi: สายตัดไฟเข้า MD30C ตรง. X4 แค่รายงานสถานะให้ ladder เข้า Alarm.

---

## English

### One Cleaning Run

From a Start request until the robot is back at a parking end. Split into 3 parts by **who decides**: requester → Pi pre-checks → the PLC ladder decides and drives the motor.
For all states see `docs/robot-operation.md` §4. Items marked ❓ are not yet confirmed against the real ladder (they currently follow the mock `app/sim/ladder.py`).

```mermaid
flowchart TB
    subgraph trig["1 · Request"]
        direction LR
        ui(["Dashboard: Start"])
        sch(["Schedule is due"])
        btn(["Front button X2"])
    end

    subgraph pi["2 · Raspberry Pi: pre-checks (not the final decision)"]
        q["commands queue<br/>5 s expiry"]
        late{"More than 60 s late?"}
        mode{"Switch X3 = Auto?"}
        cyc["write cycles_setpoint"]
        pre{"E-stop pressed / alarm /<br/>already moving?"}
        pulse["cmd_start = 1<br/>wait for the ladder to clear it"]
        ack{"ack within 2 s?"}
        clr["Pi clears the bit itself"]
        log[("events log<br/>missed · skipped · rejected<br/>not_acknowledged")]
    end

    subgraph plc["3 · PLC ladder: decides + moves"]
        where{"current state?"}
        ret["Returning<br/>to the nearest end"]
        park["Home: parked, wait for a new Start"]
        stay["Does not start, stays Home<br/>Pi logs not_started + reason"]
        may{"battery ≥ 80 %<br/>and heartbeat OK?"}
        go["Cleaning<br/>Y1 sets direction away from home<br/>Y0 runs (brush turns with it)"]
        far["Reaches the far limit<br/>pause 2 s, reverse"]
        near["Reaches the start limit<br/>cycle_count + 1"]
        done{"Auto: cycles_setpoint reached?<br/>Manual: never"}
        home(["Home: parked at the row end<br/>solar keeps charging"])
    end

    ui --> q --> pre
    sch --> late
    late -- "yes (Pi/PLC was down)" --> log
    late -- no --> mode
    mode -- "Manual → skipped" --> log
    mode -- Auto --> cyc --> pre
    pre -- "yes → rejected" --> log
    pre -- no --> pulse --> ack
    ack -- no --> clr --> log
    ack -- yes --> where
    btn -- "straight to the ladder" --> where

    where -- "Idle (mid-panel)" --> ret --> park
    where -- Home --> may
    may -- no --> stay
    may -- yes --> go --> far --> near --> done
    done -- "not yet" --> go
    done -- reached --> home
```

**Reading the diagram:** When the Pi refuses, it logs the reason in `events` for the dashboard to show. Once the Pi has sent the command, the ladder still decides on its own whether to move (battery ≥ the PLC threshold, heartbeat). The spec says 80 %; for testing the PLC is set to 40 %, while the Pi pre-check still uses 80 %; see [open-decisions.md](open-decisions.md) item 2. The front button goes straight to the ladder, not through the Pi.

### Interrupts While Moving (Cleaning / Returning)

Kept out of the chart above because they can happen at any point of the run; drawing them there would add an edge from every box.

```mermaid
flowchart LR
    moving(["Moving<br/>Cleaning / Returning"])
    moving -- "Stop (dashboard / button X2)<br/>never blocked, wins over Start" --> idle["Idle: stopped mid-panel"]
    moving -- "E-stop: motor power cut in hardware" --> alarm["Alarm 1<br/>release E-stop + Reset"]
    moving -- "cmd_return / low battery (threshold ❓)" --> ret["Returning → Home"]
    moving -- "heartbeat lost ❓" --> finish["Finish this cycle → Home<br/>no new cycle"]
```

- Stop, cmd_return and low battery apply while Cleaning. While Returning, only Stop and E-stop apply.
- E-stop does not wait for the PLC or the Pi: its contact cuts the MD30C supply directly. X4 only reports the status so the ladder enters Alarm.
