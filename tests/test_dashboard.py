"""Step 8: dashboard data layer, and the Streamlit page rendered headless (AppTest)."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import command_queue, schedule
from app.dashboard import data
from app.history import HistoryStore
from app.robot import Alarm, State

TZ = ZoneInfo("Asia/Bangkok")
PAGE = str(Path(__file__).resolve().parents[1] / "app" / "dashboard" / "main.py")
VALUES = {"robot_state": 1, "alarm_code": 0, "battery_pct": 87, "mode_switch": False, "estop_ok": True,
          "cycle_count": 1, "cycles_setpoint": 2, "position_est_pct": 40,
          "pzem_power": 120.5, "pzem_voltage": 51.2, "pzem_current": 2.35}


@pytest.fixture
def store(tmp_path):
    s = HistoryStore(tmp_path / "gateway.db")
    yield s
    s.close()


# --- live status -----------------------------------------------------------------------

def test_no_data_yet(store):
    status = data.live_status(store, now=1000)
    assert status.values is None and not status.gateway_alive
    assert "gateway running" in status.headline[1]


def test_fresh_online(store):
    store.set_latest(1000, True, VALUES)
    status = data.live_status(store, now=1003)
    assert status.gateway_alive and status.plc_online
    assert status.headline == ("🟢", "PLC online")


def test_plc_offline_keeps_last_values(store):
    store.set_latest(1000, True, VALUES)
    store.set_latest(1001, False, None)
    status = data.live_status(store, now=1002)
    assert status.values == VALUES
    assert status.headline[0] == "🔴"


def test_stale_latest_means_gateway_stopped(store):
    store.set_latest(1000, True, VALUES)            # online=1 stays forever if the gateway dies
    status = data.live_status(store, now=1000 + data.STALE_S + 1)
    assert not status.gateway_alive
    assert "Gateway not updating" in status.headline[1]


# --- labels ----------------------------------------------------------------------------

def test_labels():
    assert data.state_label(State.CLEANING) == "🧹 Cleaning"
    assert data.state_label(99) == "❔ 99"
    assert data.alarm_label(Alarm.NONE) == "✅ None"
    assert data.alarm_label(Alarm.HEARTBEAT_LOST).startswith("⚠️")
    assert data.battery_label(87) == "🔋 87 %"
    assert data.battery_label(20) == "🪫 20 %"
    assert data.battery_label(None) == "–"


# --- history / events ------------------------------------------------------------------

def test_history_and_events(store):
    store.add_snapshot(1000, {"battery_pct": 90, "pzem_power": 10.0, "robot_state": 3})
    store.add_snapshot(2000, {"battery_pct": 89})
    store.add_event(1500, "change", "robot_state: Home -> Cleaning")
    rows = data.history(store, 1500, ["battery_pct", "pzem_power"], TZ)
    assert rows == [{"time": datetime.fromtimestamp(2000, TZ), "battery_pct": 89, "pzem_power": None}]
    events = data.recent_events(store, TZ)
    assert events[0]["kind"] == "change" and events[0]["message"] == "robot_state: Home -> Cleaning"


# --- commands --------------------------------------------------------------------------

def test_send_command_waits_for_the_gateway(store):
    def gateway_answers(_):                          # stands in for CommandQueueWorker
        store.db.execute("UPDATE commands SET status = 'done', result = 'started cleaning'")
        store.db.commit()
    row = data.send_command(store, "start", sleep=gateway_answers)
    assert (row["status"], row["result"], row["source"]) == ("done", "started cleaning", "dashboard")


def test_send_command_times_out_without_gateway(store):
    t = [0.0]
    def fake_sleep(s):
        t[0] += s
    row = data.send_command(store, "start", wait_s=1.0, sleep=fake_sleep, clock=lambda: t[0])
    assert row["status"] == "pending"                # expires unsent in the gateway's queue


# --- the page, rendered headless -------------------------------------------------------

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


@pytest.fixture
def page_env(tmp_path, monkeypatch):
    db = tmp_path / "gateway.db"
    monkeypatch.setenv("PLC_HOST", "127.0.0.1")
    monkeypatch.setenv("HISTORY_DB", str(db))
    monkeypatch.setenv("DASHBOARD_PASSWORD", "")
    monkeypatch.chdir(tmp_path)                      # no repo .env
    import streamlit as st
    st.cache_resource.clear()
    return db


def run_page():
    at = AppTest.from_file(PAGE, default_timeout=60)   # first run imports pandas/altair (slow disks)
    at.run()
    assert not at.exception, at.exception
    return at


def test_page_renders_live_values(page_env):
    import time
    run_page()                                       # warm-up: the first run is slow (imports)
    s = HistoryStore(page_env)
    s.set_latest(time.time(), True, VALUES)
    schedule.add(s, "08:00", "mon,fri", 2)
    s.close()
    at = run_page()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["State"] == "🧹 Cleaning"
    assert metrics["Battery"] == "🔋 87 %"
    assert metrics["Cycles"] == "1 / 2"
    assert any("PLC online" in md.value for md in at.markdown)
    assert any("mon,fri, 2 cycle(s)" in md.value for md in at.markdown)
    assert any("anyone on this network" in w.value for w in at.warning)


def test_page_without_gateway_data(page_env):
    at = run_page()
    assert any("No data yet" in md.value for md in at.markdown)


def test_stop_button_queues_a_stop(page_env):
    at = run_page()
    stop = next(b for b in at.button if b.label == "STOP")
    stop.click().run(timeout=20)                     # no gateway: waits COMMAND_WAIT_S, then reports
    s = HistoryStore(page_env)
    command_queue.ensure_schema(s)
    rows = s.db.execute("SELECT command, source FROM commands").fetchall()
    s.close()
    assert rows == [("stop", "dashboard")]
    assert any("no answer from the gateway" in e.value for e in at.error)


def test_password_gate(page_env, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "demo123")
    at = run_page()
    assert not at.metric and not [b for b in at.button if b.label == "STOP"]   # nothing behind the gate
    at.text_input[0].input("wrong")
    at.button[0].click().run()
    assert any("Wrong password" in e.value for e in at.error)
    at.text_input[0].input("demo123")
    at.button[0].click().run()
    assert any(b.label == "STOP" for b in at.button)


def test_schedule_toggle_writes_only_on_user_flip(page_env):
    s = HistoryStore(page_env)
    sid = schedule.add(s, "08:00")
    s.close()
    at = run_page()

    other = HistoryStore(page_env)                  # disabled from another browser / the CLI
    schedule.set_enabled(other, sid, False)
    at.run()                                        # stale session must not re-enable it
    assert not schedule.list_all(other)[0].enabled
    assert at.toggle[0].value is False              # and the page shows the DB value

    at.toggle[0].set_value(True).run()              # a real flip in the page is written
    assert schedule.list_all(other)[0].enabled
    other.close()
