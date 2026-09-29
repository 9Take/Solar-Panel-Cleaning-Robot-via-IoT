# Flowchart การทำงาน (1 รอบทำความสะอาด)

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

**อ่านแผนภาพ:** ถ้า Pi ปฏิเสธ จะบันทึกเหตุผลลง `events` ไว้ให้ dashboard แสดง. แต่ถ้า Pi ส่งคำสั่งผ่านไปแล้ว ladder ยังตัดสินเองเสมอว่าจะออกเดินหรือไม่ (แบต ≥ 80 %, heartbeat). ปุ่มหน้าเครื่องเข้า ladder ตรง ไม่ผ่าน Pi.

## เหตุแทรกระหว่างเดิน (Cleaning / Returning)

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
