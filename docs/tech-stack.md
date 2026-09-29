# Tech Stack

- [ภาษาไทย](#ภาษาไทย)
- [English](#english)

---

## ภาษาไทย

### แผนภาพระบบ

```mermaid
flowchart TB
    user(["ผู้ใช้<br/>มือถือ / PC ใน LAN"])

    subgraph cab["ตู้ควบคุม"]
        direction TB
        subgraph pi["Raspberry Pi 4/5 · Raspberry Pi OS 64-bit · Docker Compose"]
            direction LR
            subgraph dash["container: dashboard"]
                st["Streamlit<br/>app/dashboard"]
            end
            db[("SQLite (WAL)<br/>logs/gateway.db<br/>latest · snapshots · events<br/>commands · schedules")]
            subgraph gw["container: gateway · python -m app (asyncio)"]
                direction TB
                poller["poller.py<br/>+ history.py"]
                queue["command_queue.py"]
                sched["schedule.py"]
                cmdr["commander.py<br/>10 safety rules"]
                batt["battery.py<br/>+ tuya.py"]
                client["plc_client.py<br/>pymodbus"]
            end
        end

        subgraph ctrl["ชุด PLC (ทำงานเองได้โดยไม่ต้องมี Pi)"]
            direction TB
            plc["Delta DVP-12SE11T<br/>ladder = motion + safety"]
            pzem["PZEM-017<br/>DC meter"]
            md30["MD30C<br/>motor driver"]
            estop["E-stop<br/>ตัดไฟมอเตอร์ (hardware)"]
        end

        mppt["Tuya MPPT<br/>solar charger"]
    end

    motor["DC motor<br/>ขับเคลื่อน + แปรง"]

    tuya["Tuya Cloud API"]

    user -- "HTTP :8501" --> st
    st -- "อ่านสถานะ / ประวัติ" --> db
    st -- "เขียนคำสั่ง + ตารางเวลา" --> db

    poller -- "เขียนทุก poll" --> db
    queue -- "ดึงคำสั่ง (หมดอายุ 5 s)" --> db
    sched -- "อ่านตาราง, เช็กทุก 5 s" --> db
    queue --> cmdr
    sched --> cmdr
    poller --> client
    cmdr -- "pulse bit + รอ ack 2 s" --> client
    batt -- "battery_pct + pi_heartbeat" --> client

    client -- "Modbus TCP :502" --> plc
    batt -- "ดึง % แบต · HTTPS (HMAC-SHA256)" --> tuya
    mppt -- "ส่งค่าแบต/solar · WiFi" --> tuya

    plc -- "RS485 Modbus RTU" --> pzem
    plc -- "Y0 PWM · Y1 DIR" --> md30
    estop -. "NC contact → X4 (รายงานสถานะ)" .-> plc
    estop -- "ตัดไฟ" --> md30
    md30 -- "ไฟมอเตอร์" --> motor
```

**อ่านแผนภาพ:** Dashboard ไม่คุย Modbus เอง ทุกอย่างผ่าน SQLite. มีแค่ container `gateway` ที่ต่อ PLC และคำสั่งทุกตัวต้องผ่าน `commander.py` ก่อนถึง PLC. ส่วน ladder ใน PLC ตัดสินเรื่องการเคลื่อนที่และความปลอดภัยทั้งหมด. Pi แค่ขอคำสั่งกับส่งค่าแบตให้. ค่าแบตมาจาก Tuya MPPT ในตู้ ส่งขึ้น Tuya Cloud ทาง WiFi แล้ว Pi ดึงจาก cloud อีกที (Pi ไม่ต่อ MPPT ตรง).

### Stack แยกตามชั้น

| ชั้น | เทคโนโลยี | หน้าที่ |
|---|---|---|
| Hardware control | Delta DVP-12SE11T, ladder logic | motion, interlock, safety, ตัดสินใจกลับบ้านเมื่อแบตต่ำ |
| Field bus | RS485 Modbus RTU | PLC ↔ PZEM-017 (V, A, W) |
| Plant link | Ethernet, Modbus TCP (PLC = server :502) | Pi ↔ PLC |
| Gateway runtime | Python 3.12, asyncio | poll, คำสั่ง, ส่งค่าแบต + heartbeat, ตารางเวลา |
| Library | `pymodbus` 3.x, `pydantic-settings`, `PyYAML`, `tzdata` | Modbus, config `.env`, tag map, timezone |
| Storage | SQLite (WAL) `logs/gateway.db` | สถานะล่าสุด, ประวัติ, events, คิวคำสั่ง, ตารางเวลา |
| UI | Streamlit (`requirements-dashboard.txt`) :8501 | ดูสถานะ, ส่งคำสั่ง, ตั้งเวลา, กราฟ (step 8: ยังเทียบเวอร์ชันกับเพื่อนในทีม) |
| Cloud | Tuya Cloud API (HTTPS, stdlib client) | % แบต, ข้อมูล solar จาก MPPT |
| Deploy | Docker + Compose, image `linux/arm64`, `python:3.12-slim` | `gateway`, `dashboard`; `plc-sim` สำหรับทดสอบ |
| Config | `.env` (secrets, ไม่ commit), `config/plc_tags.yaml` | ไม่มี IP/address/key ใน code |
| Test | mock PLC `app/sim` (pymodbus server + ladder จำลอง) | ทดสอบได้โดยไม่มี PLC จริง |

---

## English

### System Diagram

```mermaid
flowchart TB
    user(["User<br/>phone / PC on the LAN"])

    subgraph cab["Control cabinet"]
        direction TB
        subgraph pi["Raspberry Pi 4/5 · Raspberry Pi OS 64-bit · Docker Compose"]
            direction LR
            subgraph dash["container: dashboard"]
                st["Streamlit<br/>app/dashboard"]
            end
            db[("SQLite (WAL)<br/>logs/gateway.db<br/>latest · snapshots · events<br/>commands · schedules")]
            subgraph gw["container: gateway · python -m app (asyncio)"]
                direction TB
                poller["poller.py<br/>+ history.py"]
                queue["command_queue.py"]
                sched["schedule.py"]
                cmdr["commander.py<br/>10 safety rules"]
                batt["battery.py<br/>+ tuya.py"]
                client["plc_client.py<br/>pymodbus"]
            end
        end

        subgraph ctrl["PLC set (runs on its own without the Pi)"]
            direction TB
            plc["Delta DVP-12SE11T<br/>ladder = motion + safety"]
            pzem["PZEM-017<br/>DC meter"]
            md30["MD30C<br/>motor driver"]
            estop["E-stop<br/>cuts motor power (hardware)"]
        end

        mppt["Tuya MPPT<br/>solar charger"]
    end

    motor["DC motor<br/>drive + brush"]

    tuya["Tuya Cloud API"]

    user -- "HTTP :8501" --> st
    st -- "reads status / history" --> db
    st -- "writes commands + schedules" --> db

    poller -- "writes every poll" --> db
    queue -- "takes commands (5 s expiry)" --> db
    sched -- "reads schedules, checks every 5 s" --> db
    queue --> cmdr
    sched --> cmdr
    poller --> client
    cmdr -- "pulse bit + wait ack 2 s" --> client
    batt -- "battery_pct + pi_heartbeat" --> client

    client -- "Modbus TCP :502" --> plc
    batt -- "reads battery % · HTTPS (HMAC-SHA256)" --> tuya
    mppt -- "sends battery/solar · WiFi" --> tuya

    plc -- "RS485 Modbus RTU" --> pzem
    plc -- "Y0 PWM · Y1 DIR" --> md30
    estop -. "NC contact → X4 (status only)" .-> plc
    estop -- "cuts power" --> md30
    md30 -- "motor power" --> motor
```

**Reading the diagram:** The dashboard never talks Modbus; everything goes through SQLite. Only the `gateway` container connects to the PLC, and every command passes through `commander.py` first. The ladder in the PLC decides all motion and safety; the Pi only requests commands and feeds the battery value. The battery value comes from the Tuya MPPT in the cabinet, which reports to Tuya Cloud over WiFi; the Pi reads it from the cloud (no direct Pi ↔ MPPT link).

### Stack by Layer

| Layer | Technology | Role |
|---|---|---|
| Hardware control | Delta DVP-12SE11T, ladder logic | motion, interlocks, safety, return home on low battery |
| Field bus | RS485 Modbus RTU | PLC ↔ PZEM-017 (V, A, W) |
| Plant link | Ethernet, Modbus TCP (PLC = server :502) | Pi ↔ PLC |
| Gateway runtime | Python 3.12, asyncio | polling, commands, battery feed + heartbeat, schedule |
| Libraries | `pymodbus` 3.x, `pydantic-settings`, `PyYAML`, `tzdata` | Modbus, `.env` config, tag map, time zone |
| Storage | SQLite (WAL) `logs/gateway.db` | live status, history, events, command queue, schedules |
| UI | Streamlit (`requirements-dashboard.txt`) :8501 | status, commands, schedules, charts (step 8: still being compared with a teammate's version) |
| Cloud | Tuya Cloud API (HTTPS, stdlib client) | battery %, solar data from the MPPT |
| Deploy | Docker + Compose, `linux/arm64` image, `python:3.12-slim` | `gateway`, `dashboard`; `plc-sim` for testing |
| Config | `.env` (secrets, not committed), `config/plc_tags.yaml` | no IPs/addresses/keys in code |
| Test | mock PLC `app/sim` (pymodbus server + simulated ladder) | test without the real PLC |
