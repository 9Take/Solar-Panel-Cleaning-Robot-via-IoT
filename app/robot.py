"""Robot status codes shared by the ladder spec, the mock and the gateway.

Values follow docs/robot-operation.md (robot_state / alarm_code registers).
"""

from enum import IntEnum


class State(IntEnum):
    IDLE = 0
    CLEANING = 1
    RETURNING = 2
    HOME = 3
    ALARM = 4


class Alarm(IntEnum):
    NONE = 0
    ESTOP = 1
    BATTERY_CRITICAL = 2
    STUCK = 3            # future: travel timeout
    BOTH_LIMITS = 4
    HEARTBEAT_LOST = 5


# Spec thresholds (docs/robot-operation.md). The ladder enforces them; the Pi only
# uses them to explain why the robot did not do something.
BATTERY_START_MIN_PCT = 80


def describe(enum_cls: type[IntEnum], code) -> str:
    """'Home' for State 3; 'unknown(9)' for codes the spec does not define."""
    try:
        return enum_cls(int(code)).name.replace("_", " ").title()
    except (ValueError, TypeError):
        return f"unknown({code})"
