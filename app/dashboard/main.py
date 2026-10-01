"""Streamlit dashboard: live status, commands, schedule, history.

    streamlit run app/dashboard/main.py        (Docker: compose service `dashboard`, port 8501)

Reads and writes the gateway's SQLite database only. Commands go through the
`commands` queue, so the gateway's safety rules (app.commander) always apply.
"""

from __future__ import annotations

import contextlib
import hmac
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from app import schedule, weather
from app.config import Settings
from app.dashboard import data
from app.history import HistoryStore

SERIES_COLOR = "#2a78d6"     # one series per chart (dataviz palette, categorical slot 1)
HISTORY_RANGES = {"1 hour": 1, "6 hours": 6, "24 hours": 24, "7 days": 24 * 7}

st.set_page_config(page_title="Solar Robot", page_icon="🤖", layout="wide")


@st.cache_resource
def settings() -> Settings:
    return Settings()


@contextlib.contextmanager
def open_store():
    """One SQLite connection per run (connections cannot be shared across Streamlit threads)."""
    store = HistoryStore(settings().history_db)
    try:
        yield store
    finally:
        store.close()


def login_gate() -> None:
    password = settings().dashboard_password.get_secret_value()
    if not password:
        st.warning("DASHBOARD_PASSWORD is not set: anyone on this network can control the robot.", icon="⚠️")
        return
    if st.session_state.get("authed"):
        return
    with st.form("login"):
        entered = st.text_input("Password", type="password")
        if st.form_submit_button("Log in", type="primary", width="stretch"):
            if hmac.compare_digest(entered.encode(), password.encode()):
                st.session_state.authed = True
                st.rerun()
            st.error("Wrong password")
    st.stop()


# --- status ---------------------------------------------------------------------------

@st.fragment(run_every=settings().dashboard_refresh_s)
def status_panel() -> None:
    with open_store() as store:
        status = data.live_status(store)
    icon, text = status.headline
    st.markdown(f"#### {icon} {text}")
    v = status.values or {}

    cols = st.columns(4)
    cols[0].metric("State", data.state_label(v.get("robot_state")) if v else "–", border=True)
    cols[1].metric("Alarm", data.alarm_label(v.get("alarm_code")) if v else "–", border=True)
    cols[2].metric("Battery", data.battery_label(v.get("battery_pct")), border=True)
    cols[3].metric("Mode", ("✋ Manual" if v.get("mode_switch") else "🤖 Auto") if v else "–", border=True)

    cols = st.columns(4)
    cols[0].metric("E-stop", ("🟢 Released" if v.get("estop_ok") else "🛑 PRESSED") if v else "–", border=True)
    cols[1].metric("Cycles", f"{v.get('cycle_count', '–')} / {v.get('cycles_setpoint', '–')}", border=True)
    cols[2].metric("Position", f"{v['position_est_pct']} %" if "position_est_pct" in v else "–", border=True)
    power = v.get("pzem_power")
    cols[3].metric("Power", f"{power:.1f} W" if power is not None else "–",
                   help=f"{v.get('pzem_voltage', 0):.2f} V · {v.get('pzem_current', 0):.2f} A" if v else None,
                   border=True)
    if status.age_s is not None:
        st.caption(f"Updated {status.age_s:.0f}s ago · refreshes every {settings().dashboard_refresh_s:g}s")


# --- weather --------------------------------------------------------------------------

WEATHER_SERIES = [   # (column, title, unit) — one chart each, same time axis as the history charts
    ("cloud_cover", "Cloud cover (%)", "%"),
    ("temperature_2m", "Temperature (°C)", "°C"),
    ("relative_humidity_2m", "Humidity (%)", "%"),
]


@st.cache_data(ttl=settings().weather_refresh_s, show_spinner=False)
def get_weather(lat: float, lon: float, tz: str) -> weather.Weather:
    """Cached per refresh period; failures are not cached, so the next run retries."""
    return weather.fetch(lat, lon, tz)


def load_weather() -> tuple[weather.Weather | None, str | None]:
    """(weather, None), (None, reason it is unavailable) or (None, None) when weather is off."""
    s = settings()
    if s.weather_lat is None or s.weather_lon is None:
        return None, None
    try:
        return get_weather(s.weather_lat, s.weather_lon, s.schedule_tz), None
    except weather.WeatherError as exc:
        return None, str(exc)


@st.fragment(run_every=settings().weather_refresh_s)
def weather_panel() -> None:
    w, error = load_weather()
    if w is None and error is None:
        st.caption("Weather off: set WEATHER_LAT / WEATHER_LON in .env")
        return
    place = f" · {settings().weather_place}" if settings().weather_place else ""
    st.markdown(f"##### 🌤️ Weather at the panels{place}")
    if error:
        st.warning(f"Weather unavailable: {error}", icon="🌐")
        return
    cols = st.columns(3)
    cols[0].metric("Cloud cover", data.weather_label(w.current["cloud_cover"], "%"), border=True,
                   help="More cloud, less sunlight on the panels: solar charging drops")
    cols[1].metric("Temperature", data.weather_label(w.current["temperature_2m"], "°C", 1), border=True,
                   help="Hotter panels convert less light to power "
                        "(typically about -0.4 % per °C of panel temperature above 25 °C)")
    cols[2].metric("Humidity", data.weather_label(w.current["relative_humidity_2m"], "%"), border=True,
                   help="Humid, hazy air scatters some sunlight")
    st.caption(f"Open-Meteo · {w.at:%H:%M} · trends in the History tab")


def weather_charts(since: datetime, until: datetime) -> None:
    w, error = load_weather()
    if w is None:
        if error:
            st.caption(f"Weather unavailable: {error}")
        return
    rows = data.weather_history(w, since, until)
    if not rows:
        return
    st.markdown("**Weather at the panels** · compare with the battery and power charts above")
    df = pd.DataFrame(rows)
    for column, title, unit in WEATHER_SERIES:
        st.markdown(f"<small>{title}</small>", unsafe_allow_html=True)
        st.line_chart(df, x="time", y=column, color=SERIES_COLOR, x_label="", y_label=unit, height=160)


# --- commands -------------------------------------------------------------------------

def run_command(command: str, value=None) -> None:
    with open_store() as store, st.spinner(f"Sending {command}…"):
        row = data.send_command(store, command, value)
    if row["status"] == "done":
        st.success(f"{command}: {row['result']}", icon="✅")
    elif row["status"] == "pending":
        st.error(f"{command}: no answer from the gateway (is it running?). The request will expire unsent.",
                 icon="⏱️")
    else:
        st.error(f"{command} {row['status']}: {row['result']}", icon="❌")


def stop_button() -> None:
    """Above everything else: on a phone the status tiles stack, and STOP must not need scrolling."""
    if st.button("STOP", icon="⏹️", type="primary", width="stretch"):
        run_command("stop")


def control_panel() -> None:
    cols = st.columns(3)
    if cols[0].button("Start", icon="▶️", width="stretch"):
        run_command("start")
    if cols[1].button("Return home", icon="↩️", width="stretch"):
        run_command("return")
    if cols[2].button("Reset alarm", icon="🔄", width="stretch"):
        run_command("reset")
    with st.form("cycles", border=False):
        low, high = schedule.CYCLES_MIN, schedule.CYCLES_MAX
        cycles = st.number_input(f"Cycles per Auto run ({low}–{high})", low, high, 1, step=1)
        if st.form_submit_button("Set cycles", width="stretch"):
            run_command("cycles", int(cycles))
    st.caption("The PLC decides: a command is refused when the robot is not ready "
               "(E-stop, alarm, battery below 80 %, already moving).")


# --- schedule -------------------------------------------------------------------------

def toggle_schedule(schedule_id: int, key: str) -> None:
    with open_store() as store:
        schedule.set_enabled(store, schedule_id, st.session_state[key])


def schedule_panel(tz: ZoneInfo) -> None:
    now = datetime.now(tz)
    st.caption(f"Runs in Auto mode only · time zone {settings().schedule_tz} · now {now:%a %H:%M}")
    with open_store() as store:
        entries = schedule.list_all(store)
        for entry in entries:
            nxt = schedule.next_due(entry, tz, now)
            cols = st.columns([5, 2, 1], vertical_alignment="center")
            cols[0].markdown(f"**{entry.at}** · {entry.summary}  \n"
                             f"<small>{'next ' + format(nxt, '%a %d %b %H:%M') if nxt else 'disabled'}</small>",
                             unsafe_allow_html=True)
            # Written only from the callback (a real user flip). The widget keeps its own
            # session state, so comparing its value with the DB would write back stale
            # values when the schedule was changed from another browser or the CLI.
            key = f"en{entry.id}"
            st.session_state[key] = entry.enabled          # always show the DB value
            cols[1].toggle("On", key=key, on_change=toggle_schedule, args=(entry.id, key))
            if cols[2].button("🗑️", key=f"rm{entry.id}", help="Delete"):
                schedule.remove(store, entry.id)
                st.rerun()
        if not entries:
            st.info("No scheduled runs yet.")

        with st.form("add_schedule", clear_on_submit=True):
            st.markdown("**Add a run**")
            at = st.time_input("Time", value=None, step=timedelta(minutes=5))
            days = st.multiselect("Days", schedule.DAYS, default=list(schedule.DAYS))
            use_cycles = st.checkbox("Set cycles for this run (otherwise keep the PLC setpoint)")
            cycles = st.number_input("Cycles", schedule.CYCLES_MIN, schedule.CYCLES_MAX, 1, step=1)
            if st.form_submit_button("Add", type="primary", width="stretch"):
                try:
                    if at is None:
                        raise ValueError("pick a time")
                    if not days:
                        raise ValueError("pick at least one day")
                    schedule.add(store, at.strftime("%H:%M"), ",".join(days),
                                 int(cycles) if use_cycles else None)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))


# --- history --------------------------------------------------------------------------

def history_panel(tz: ZoneInfo) -> None:
    span = st.segmented_control("Range", list(HISTORY_RANGES), default="6 hours")
    since = time.time() - HISTORY_RANGES[span or "6 hours"] * 3600
    until = time.time()
    with open_store() as store:
        rows = data.history(store, since, ["battery_pct", "pzem_power"], tz)
        events = data.recent_events(store, tz)
    if rows:
        df = pd.DataFrame(rows)
        st.markdown("**Battery (%)**")
        st.line_chart(df, x="time", y="battery_pct", color=SERIES_COLOR, x_label="", y_label="%", height=220)
        st.markdown("**Power (W)**")
        st.line_chart(df, x="time", y="pzem_power", color=SERIES_COLOR, x_label="", y_label="W", height=220)
    else:
        st.info("No history in this range yet.")
    weather_charts(datetime.fromtimestamp(since, tz), datetime.fromtimestamp(until, tz))
    st.markdown("**Events**")
    st.dataframe(pd.DataFrame(events, columns=["time", "kind", "message"]), hide_index=True, height=320)


# --- page -----------------------------------------------------------------------------

def main() -> None:
    st.title("🤖 Solar Panel Cleaning Robot")
    login_gate()
    tz = ZoneInfo(settings().schedule_tz)
    stop_button()
    status_panel()
    weather_panel()
    tab_control, tab_schedule, tab_history = st.tabs(["Control", "Schedule", "History"])
    with tab_control:
        control_panel()
    with tab_schedule:
        schedule_panel(tz)
    with tab_history:
        history_panel(tz)


main()
