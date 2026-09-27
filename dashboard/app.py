"""Solar Panel Cleaning Robot - monitoring dashboard (mockup).

Run:  streamlit run app.py

Everything here is simulated. Two functions get replaced when the gateway is ready:
  get_weather()  -> weather API
  read_plc()     -> tag values from the IoT gateway (Modbus TCP to the PLC)
                    + Tuya Cloud (battery %, solar)

Behavior follows docs/robot-operation.md in the gateway repo
(branch feature/step2c-sim-behavior). Tag names come from config/plc_tags.yaml.
"""
import os
import random
from datetime import datetime, timedelta
from datetime import time as dtime

import pandas as pd
import streamlit as st
import yaml

st.set_page_config(page_title="Solar Robot Dashboard", page_icon="☀️", layout="wide")
st.markdown("<style>[data-testid='stMetricValue']{font-size:1.5rem}</style>",
            unsafe_allow_html=True)

HERE = os.path.dirname(os.path.abspath(__file__))
TAGS_FILE = os.path.join(HERE, os.environ.get("PLC_TAGS_FILE", "config/plc_tags.yaml"))

# robot_state (D10) and alarm_code (D11), per robot-operation.md §4 and §6
IDLE, CLEANING, RETURNING, HOME, ALARM = range(5)
ALARM_TEXT = {
    0: "ไม่มี",
    1: "E-stop ถูกกด",
    2: "แบตเตอรี่วิกฤต (< 20 %)",
    3: "เดินติด / timeout",
    4: "Limit ทั้งสองฝั่ง ON พร้อมกัน",
    5: "Pi heartbeat หาย (ไม่รู้ค่าแบต)",
}
# Ladder thresholds (LadderParams in app/sim/ladder.py)
BATT_START_MIN, BATT_LOW, BATT_CRITICAL = 80, 25, 20
TRAVEL_S, END_PAUSE_S = 20, 2


# ============================================================
# Simulated data sources
# ============================================================
def get_weather():
    """Current weather + hourly forecast (simulated)."""
    now = datetime.now()
    rain = [5, 5, 10, 20, 45, 70, 60, 30]
    temp = [32, 33, 34, 34, 33, 30, 29, 28]
    hourly = [{"เวลา": (now + timedelta(hours=h)).strftime("%H:00"),
               "โอกาสฝน (%)": rain[h], "อุณหภูมิ (°C)": temp[h]} for h in range(8)]
    return {
        "condition": "มีเมฆบางส่วน",
        "temp_c": 32.4,
        "humidity": 62,
        "wind_kmh": 12,
        "rain_chance": 10,
        "irradiance": 780,  # W/m²
        "dust": 58,         # PM10 µg/m³
        "hourly": hourly,
    }


def new_robot():
    return {
        "state": CLEANING, "alarm": 0,
        "pos": 0.3,              # 0 = end 1 (X0), 1 = end 2 (X1)
        "dir_to_end2": True,
        "start_at_end1": True,
        "pause": 0.0,
        "cycles": 0, "cycles_setpoint": 2,
        "battery": 86.0, "energy_wh": 1520.0,
    }


def step_robot(r, sim, dt=1.0):
    """One simulated ladder scan (simplified version of LadderSim.scan)."""
    at_end1, at_end2 = r["pos"] <= 0.0, r["pos"] >= 1.0
    reset = sim.pop("reset", False)

    # E-stop overrides everything (NC contact on X4: 0 = pressed)
    if sim["estop"]:
        r["state"], r["alarm"] = ALARM, 1
    elif r["state"] == ALARM and r["alarm"] == 1 and reset:
        r["state"] = HOME if (at_end1 or at_end2) else IDLE
        r["alarm"] = 0

    # Front Start/Stop button (X2) or scheduled cmd_start from the Pi
    if sim.pop("start", False) and r["state"] in (HOME, IDLE):
        if r["state"] == IDLE:
            r["state"] = RETURNING
            r["dir_to_end2"] = r["pos"] >= 0.5
        elif r["battery"] >= BATT_START_MIN:
            r["state"], r["cycles"] = CLEANING, 0
            r["start_at_end1"] = at_end1
            r["dir_to_end2"] = at_end1
    if sim.pop("stop", False) and r["state"] in (CLEANING, RETURNING):
        r["state"] = IDLE

    moving = r["state"] in (CLEANING, RETURNING)
    if moving and r["pause"] > 0:
        r["pause"] -= dt
    elif moving:
        r["pos"] += (dt / TRAVEL_S) * (1 if r["dir_to_end2"] else -1)
        r["pos"] = min(1.0, max(0.0, r["pos"]))
        arrived = r["pos"] >= 1.0 if r["dir_to_end2"] else r["pos"] <= 0.0
        if arrived and r["state"] == RETURNING:
            r["state"] = HOME
        elif arrived:
            back_at_start = (r["pos"] <= 0.0) == r["start_at_end1"]
            if back_at_start:
                r["cycles"] += 1
            auto_done = sim["mode"] == "AUTO" and r["cycles"] >= r["cycles_setpoint"]
            if back_at_start and auto_done:
                r["state"] = HOME
            else:
                r["dir_to_end2"] = not r["dir_to_end2"]
                r["pause"] = END_PAUSE_S
        if r["state"] == CLEANING and r["battery"] < BATT_LOW:
            r["state"] = RETURNING
            r["dir_to_end2"] = r["pos"] >= 0.5

    # Battery: solar charges all the time, driving drains it (sped up for the demo)
    moving = r["state"] in (CLEANING, RETURNING) and r["pause"] <= 0
    r["battery"] += (-0.15 if moving else 0) + 0.03
    r["battery"] = min(100.0, max(0.0, r["battery"]))
    if r["state"] != ALARM:
        r["alarm"] = 2 if r["battery"] < BATT_CRITICAL else 0


def read_plc(sim):
    """Tag name -> engineering value, as the gateway would return it (simulated)."""
    r = st.session_state.robot
    step_robot(r, sim)
    moving = r["state"] in (CLEANING, RETURNING) and r["pause"] <= 0

    solar_w = 95 + random.uniform(-4, 4)                     # Tuya MPPT
    volt = 42.0 + r["battery"] * 0.126                       # 48 V Li-ion (13S)
    amp = abs(solar_w / volt - (3.6 if moving else 0.4))     # PZEM-017 has no sign
    r["energy_wh"] += amp * volt / 3600

    return {
        # PLC status (D registers)
        "robot_state": r["state"],
        "alarm_code": r["alarm"],
        "cycle_count": r["cycles"],
        "position_est_pct": round(r["pos"] * 100),
        "cycles_setpoint": r["cycles_setpoint"],
        # PLC inputs / outputs
        "limit_1": r["pos"] <= 0.0,
        "limit_2": r["pos"] >= 1.0,
        "start_stop_btn": False,
        "mode_switch": sim["mode"] == "MANUAL",   # OFF = Auto (assumed polarity)
        "estop_ok": not sim["estop"],
        "drive_run": moving,
        "drive_dir": r["dir_to_end2"],
        # Pi -> PLC
        "battery_pct": round(r["battery"]),
        "pi_heartbeat": int(datetime.now().timestamp() / 2) % 65536,
        # PZEM-017 via PLC
        "pzem_voltage": volt,
        "pzem_current": amp,
        "pzem_power": volt * amp,
        "pzem_energy": r["energy_wh"],
        # Tuya Cloud (not a PLC tag)
        "solar_w": solar_w,
    }


# ============================================================
# Tag map
# ============================================================
@st.cache_data
def load_tags(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["tags"]


def modbus_address(device):
    """Delta DVP device -> 0-based Modbus address (see CLAUDE.md)."""
    kind, num = device[0], device[1:]
    if kind in "XY":
        return (0x0400 if kind == "X" else 0x0500) + int(num, 8)  # X/Y are octal
    n = int(num)
    if kind == "M":
        return 0x0800 + n if n < 1536 else 0xB000 + n - 1536
    return 0x1000 + n if n < 4096 else 0x9000 + n - 4096


# ============================================================
# Session defaults
# ============================================================
if "robot" not in st.session_state:
    st.session_state.robot = new_robot()
    st.session_state.volt_hist = []
    st.session_state.sim_events = {}
    st.session_state.schedule = pd.DataFrame([
        {"เปิดใช้": True,  "เวลา": dtime(8, 0),  "จำนวนรอบ": 2},
        {"เปิดใช้": True,  "เวลา": dtime(12, 0), "จำนวนรอบ": 1},
        {"เปิดใช้": False, "เวลา": dtime(16, 0), "จำนวนรอบ": 1},
    ])


# ============================================================
# Sidebar: hardware simulator (testing only)
# ============================================================
ev = st.session_state.sim_events
with st.sidebar:
    st.header("🧪 จำลองฮาร์ดแวร์")
    st.caption("ใช้ทดสอบหน้าจอเท่านั้น ของจริงสั่งงานที่ตัวเครื่อง")
    mode = st.radio("สวิตช์ Mode หน้าเครื่อง (X3)", ["AUTO", "MANUAL"], horizontal=True)
    estop = st.toggle("กด E-stop (X4)")
    c = st.columns(2)
    if c[0].button("▶ Start (X2)", width="stretch"):
        ev["start"] = True
    if c[1].button("■ Stop (X2)", width="stretch"):
        ev["stop"] = True
    if st.button("Reset alarm", width="stretch", disabled=estop):
        ev["reset"] = True
    if st.button("⏰ ถึงเวลาตามตาราง", width="stretch"):
        sched = st.session_state.schedule
        on = sched[sched["เปิดใช้"] == True].dropna()  # noqa: E712
        if len(on):
            st.session_state.robot["cycles_setpoint"] = int(on.iloc[0]["จำนวนรอบ"])
        ev["start"] = True
    st.caption("ปุ่ม “ถึงเวลาตามตาราง” = Pi เขียน cycles_setpoint แล้วส่ง cmd_start")

sim = ev
sim["mode"], sim["estop"] = mode, estop


# ============================================================
# Top: weather
# ============================================================
st.title("☀️ Solar Panel Cleaning Robot")
st.caption("ข้อมูลจำลอง (Mockup) · ตาม docs/robot-operation.md")

w = get_weather()
with st.container(border=True):
    st.subheader(f"🌤️ สภาพอากาศ · {w['condition']}")
    c = st.columns(6)
    c[0].metric("อุณหภูมิ", f"{w['temp_c']} °C")
    c[1].metric("ความชื้น", f"{w['humidity']} %")
    c[2].metric("ลม", f"{w['wind_kmh']} km/h")
    c[3].metric("โอกาสฝน", f"{w['rain_chance']} %")
    c[4].metric("แสงแดด", f"{w['irradiance']} W/m²")
    c[5].metric("ฝุ่น PM10", f"{w['dust']} µg/m³")

    left, right = st.columns([2, 1])
    with left:
        st.caption("โอกาสฝน 8 ชั่วโมงข้างหน้า")
        st.bar_chart(pd.DataFrame(w["hourly"]).set_index("เวลา")["โอกาสฝน (%)"],
                     height=160, sort=False)
    with right:
        st.caption("คำแนะนำ")
        rain_soon = max(h["โอกาสฝน (%)"] for h in w["hourly"][:3])
        if rain_soon >= 60 or w["wind_kmh"] >= 30:
            st.error("ไม่ควรทำความสะอาดตอนนี้ (ฝน/ลมแรง)")
        elif w["dust"] >= 50:
            st.success("เหมาะทำความสะอาด ฝุ่นค่อนข้างสูง")
        else:
            st.success("เหมาะทำความสะอาด")
        st.caption(f"ฝนสูงสุดใน 3 ชม.: {rain_soon}%")


# ============================================================
# Below: system data (refreshes every second)
# ============================================================
def badge(text, color):
    return (f"<span style='background:{color};color:white;padding:4px 14px;"
            f"border-radius:999px;font-weight:600'>{text}</span>")


def position_bar(at_end1, at_end2, is_home, moving_to_end2):
    """Three known positions: end 1 (X0) | between panels | end 2 (X1)."""
    middle = not (at_end1 or at_end2)
    if moving_to_end2 is None:
        middle_label = "ระหว่างแผง"
    else:
        middle_label = "ระหว่างแผง →" if moving_to_end2 else "← ระหว่างแผง"
    cells = [
        (at_end1, ("🏠 Home · " if at_end1 and is_home else "") + "ปลาย 1 (X0)"),
        (middle, middle_label),
        (at_end2, ("🏠 Home · " if at_end2 and is_home else "") + "ปลาย 2 (X1)"),
    ]
    html = ""
    for on, label in cells:
        html += (f"<div style='flex:1;text-align:center;padding:14px 6px;border-radius:8px;"
                 f"background:{'#F2B233' if on else 'rgba(128,128,128,.12)'};"
                 f"color:{'#1a1300' if on else 'inherit'};font-weight:{600 if on else 400}'>"
                 f"{'🤖 ' if on else ''}{label}</div>")
    return f"<div style='display:flex;gap:6px'>{html}</div>"


@st.fragment(run_every="1s")
def live_section():
    plc = read_plc(sim)
    state, alarm = plc["robot_state"], plc["alarm_code"]

    # --- Emergency ---
    if not plc["estop_ok"]:
        st.error("🛑 **EMERGENCY STOP** · E-stop ถูกกดที่ตัวเครื่อง ไฟมอเตอร์ถูกตัดทางฮาร์ดแวร์ "
                 "· ปลด E-stop แล้วกด Reset ที่ตัวเครื่อง")
    elif alarm:
        st.warning(f"⚠️ Alarm {alarm}: {ALARM_TEXT[alarm]}")

    col_robot, col_batt = st.columns([3, 2])

    # --- Robot status ---
    with col_robot, st.container(border=True):
        st.subheader("🤖 สถานะหุ่นยนต์")
        text, color = {
            CLEANING: ("กำลังทำงาน", "#1F8A4C"),
            RETURNING: ("กำลังทำงาน", "#1F8A4C"),
            IDLE: ("หยุด", "#B86E00"),
            HOME: ("อยู่ที่ Home", "#1E5F74"),
            ALARM: ("หยุด (Alarm)", "#C0392B"),
        }[state]
        detail = {
            CLEANING: f"ทำความสะอาด · รอบ {plc['cycle_count'] + 1}"
                      + (f"/{plc['cycles_setpoint']}" if not plc["mode_switch"] else ""),
            RETURNING: "กำลังกลับจุดพักที่ใกล้ที่สุด",
            IDLE: "หยุดกลางแผง (กด Stop)",
            HOME: "จอดที่ปลายแถว พร้อมรับคำสั่ง",
            ALARM: ALARM_TEXT[alarm],
        }[state]
        st.markdown(f"{badge(text, color)} &nbsp; {detail}", unsafe_allow_html=True)
        st.write("")
        st.caption("ตำแหน่งหุ่นยนต์ (รู้จริงเฉพาะปลายแถว 2 จุด)")
        st.markdown(position_bar(plc["limit_1"], plc["limit_2"], state == HOME,
                                 plc["drive_dir"] if plc["drive_run"] else None),
                    unsafe_allow_html=True)
        st.write("")
        c = st.columns(3)
        c[0].metric("โหมด (สวิตช์ X3)", "MANUAL" if plc["mode_switch"] else "AUTO")
        c[1].metric("รอบที่ทำเสร็จ", plc["cycle_count"])
        c[2].metric("E-stop", "ปกติ" if plc["estop_ok"] else "กดอยู่")
        st.caption("Start / Stop / เลือกโหมด ทำที่ตัวเครื่อง · Dashboard อ่านอย่างเดียว")

    # --- Battery & power ---
    with col_batt, st.container(border=True):
        st.subheader("🔋 แบตเตอรี่ 48 V")
        pct = plc["battery_pct"]
        c = st.columns(3)
        c[0].metric("ประจุ (Tuya)", f"{pct} %")
        c[1].metric("แรงดัน (PZEM)", f"{plc['pzem_voltage']:.1f} V")
        c[2].metric("กระแส (PZEM)", f"{plc['pzem_current']:.2f} A")
        st.progress(pct / 100)
        if pct >= BATT_START_MIN:
            st.caption(f"✅ พร้อมออกทำงาน (≥ {BATT_START_MIN} %)")
        elif pct >= BATT_LOW:
            st.caption(f"⏳ ต่ำกว่า {BATT_START_MIN} % · ยังไม่ออกรอบใหม่")
        else:
            st.caption(f"⚠️ ต่ำกว่า {BATT_LOW} % · กลับจุดพัก")
        c = st.columns(3)
        c[0].metric("Solar (Tuya)", f"{plc['solar_w']:.0f} W")
        c[1].metric("กำลัง (PZEM)", f"{plc['pzem_power']:.0f} W")
        c[2].metric("พลังงานสะสม", f"{plc['pzem_energy'] / 1000:.2f} kWh")
        hist = st.session_state.volt_hist
        hist.append(plc["pzem_voltage"])
        del hist[:-60]
        st.line_chart(pd.DataFrame({"แรงดัน (V)": hist}), height=120)

    # --- Tag monitor ---
    with st.container(border=True):
        st.subheader("📟 PLC Tag Monitor")
        st.caption("จาก config/plc_tags.yaml · M/D เป็นค่าสมมติ (ASSUMED) รอยืนยันจาก ladder "
                   "· X/Y ยืนยันแล้ว")
        rows = []
        for name, t in load_tags(TAGS_FILE).items():
            raw = plc.get(name)
            if t["type"] == "bool":
                raw = int(bool(raw))
                value = "ON" if raw else "OFF"
            else:
                value = f"{raw:.2f}".rstrip("0").rstrip(".") + " " + t.get("unit", "")
                raw = round(raw / t.get("scale", 1))
            rows.append({
                "Tag": name, "Device": t["device"],
                "Modbus": f"0x{modbus_address(t['device']):04X}",
                "Dir": t["dir"], "Type": t["type"], "Raw": raw, "ค่า": value.strip(),
                "ยืนยัน": "✅" if t["device"][0] in "XY" else "❓",
                "ความหมาย": t.get("desc", ""),
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     height=(len(rows) + 1) * 35 + 3)


live_section()


# ============================================================
# Auto mode: schedule + cycles
# ============================================================
with st.container(border=True):
    st.subheader("🗓️ โหมดอัตโนมัติ · ตารางเวลาทำความสะอาด")
    st.caption("ถึงเวลา Pi จะเขียน cycles_setpoint (D101) แล้วส่ง cmd_start (M100) "
               f"· ทำงานเฉพาะเมื่อสวิตช์ X3 อยู่ที่ AUTO และแบต ≥ {BATT_START_MIN} %")
    if mode == "MANUAL":
        st.warning("สวิตช์หน้าเครื่องอยู่ที่ MANUAL · ตารางเวลาจะไม่ทำงาน")

    edited = st.data_editor(
        st.session_state.schedule,
        num_rows="dynamic",
        hide_index=True,
        width="stretch",
        column_config={
            "เปิดใช้": st.column_config.CheckboxColumn(default=True),
            "เวลา": st.column_config.TimeColumn(format="HH:mm", step=300, required=True),
            "จำนวนรอบ": st.column_config.NumberColumn(min_value=1, max_value=10, step=1,
                                                        default=1, required=True),
        },
        key="schedule_editor",
    )

    active = edited[edited["เปิดใช้"] == True].dropna().sort_values("เวลา")  # noqa: E712
    now = datetime.now().time()
    upcoming = active[active["เวลา"] > now]
    nxt = upcoming.iloc[0] if len(upcoming) else (active.iloc[0] if len(active) else None)

    c = st.columns(3)
    c[0].metric("รอบถัดไป", nxt["เวลา"].strftime("%H:%M") if nxt is not None else "—")
    c[1].metric("จำนวนรอบครั้งถัดไป", int(nxt["จำนวนรอบ"]) if nxt is not None else "—")
    c[2].metric("รวมต่อวัน", f"{int(active['จำนวนรอบ'].sum())} รอบ")

    if st.button("💾 บันทึกตารางเวลา", type="primary"):
        st.session_state.schedule = edited
        st.success("บันทึกแล้ว (จำลอง) · ของจริงเก็บตารางไว้ที่ Pi")
