"""Simulated physical world around the PLC.

The plant reads PLC outputs (drive_run, drive_dir) and produces what the
sensors would report: limit switches, front buttons, E-stop, PZEM readings.
The ladder never sees the true position, only the limit switches.

With fake_pi=True the plant also plays the Pi's battery feeder (battery_pct +
pi_heartbeat), so the mock is usable before the real Tuya integration exists.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlantParams:
    travel_s: float = 20.0                  # true end-to-end travel time
    start_position: float = 0.0             # 0 = at end 1 (X0), 1 = at end 2 (X1)
    start_battery_pct: float = 90.0
    drain_pct_per_s: float = 0.05           # battery use while the motor runs
    solar_pct_per_s: float = 0.01           # solar charging, always on
    fake_pi: bool = True
    heartbeat_period_s: float = 2.0
    idle_current_a: float = 0.3
    run_current_a: float = 2.5
    full_voltage_v: float = 54.6            # 48 V Li-ion 13S (placeholder until confirmed)
    empty_voltage_v: float = 39.0


class Plant:
    def __init__(self, params: PlantParams | None = None) -> None:
        self.p = params or PlantParams()
        self.position = self.p.start_position
        self.battery_pct = self.p.start_battery_pct
        self.estop_ok = True
        self.mode_manual = False            # mode switch not installed yet -> Auto
        self._button_scans = 0
        self._heartbeat = 0
        self._heartbeat_timer = 0.0
        self.energy_wh = 0.0

    # --- operator actions (used by tests / manual experiments) ---------------

    def press_button(self) -> None:
        """Front Start/Stop button: held for one step, then released."""
        self._button_scans = 1

    def press_estop(self) -> None:
        self.estop_ok = False

    def release_estop(self) -> None:
        self.estop_ok = True

    # --- one simulation step -------------------------------------------------

    def step(self, plc: dict, dt: float) -> dict:
        # E-stop cuts motor power in hardware, whatever the PLC outputs say.
        running = bool(plc["drive_run"]) and self.estop_ok
        if running:
            delta = dt / self.p.travel_s
            self.position += delta if plc["drive_dir"] else -delta
            self.position = min(1.0, max(0.0, self.position))

        self.battery_pct += self.p.solar_pct_per_s * dt
        if running:
            self.battery_pct -= self.p.drain_pct_per_s * dt
        self.battery_pct = min(100.0, max(0.0, self.battery_pct))

        voltage = self.p.empty_voltage_v + (self.p.full_voltage_v - self.p.empty_voltage_v) * self.battery_pct / 100
        current = self.p.run_current_a if running else self.p.idle_current_a
        power = voltage * current
        self.energy_wh += power * dt / 3600

        button = self._button_scans > 0
        self._button_scans = max(0, self._button_scans - 1)

        inputs = {
            "limit_1": self.position <= 0.0,
            "limit_2": self.position >= 1.0,
            "start_stop_btn": button,
            "mode_switch": self.mode_manual,
            "estop_ok": self.estop_ok,
            "pzem_voltage": round(voltage, 2),
            "pzem_current": round(current, 2),
            "pzem_power": round(power, 1),
            "pzem_energy": int(self.energy_wh),
        }

        if self.p.fake_pi:
            self._heartbeat_timer += dt
            if self._heartbeat_timer >= self.p.heartbeat_period_s:
                self._heartbeat_timer = 0.0
                self._heartbeat = (self._heartbeat + 1) & 0xFFFF
            inputs["battery_pct"] = int(self.battery_pct)
            inputs["pi_heartbeat"] = self._heartbeat
        return inputs
