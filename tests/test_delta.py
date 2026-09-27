import pytest

from app.delta import Area, to_modbus


@pytest.mark.parametrize(
    "device, area, address",
    [
        ("X0", Area.DISCRETE_INPUT, 0x0400),
        ("X10", Area.DISCRETE_INPUT, 0x0408),   # octal 10 = 8
        ("X377", Area.DISCRETE_INPUT, 0x04FF),
        ("Y0", Area.COIL, 0x0500),
        ("Y17", Area.COIL, 0x050F),
        ("M0", Area.COIL, 0x0800),
        ("M1535", Area.COIL, 0x0DFF),
        ("M1536", Area.COIL, 0xB000),
        ("M4095", Area.COIL, 0xB9FF),
        ("D0", Area.HOLDING_REGISTER, 0x1000),
        ("D4095", Area.HOLDING_REGISTER, 0x1FFF),
        ("D4096", Area.HOLDING_REGISTER, 0x9000),
        ("D9999", Area.HOLDING_REGISTER, 0xA70F),
        ("d200", Area.HOLDING_REGISTER, 0x10C8),  # case-insensitive
    ],
)
def test_to_modbus(device, area, address):
    result = to_modbus(device)
    assert result.area is area
    assert result.address == address


@pytest.mark.parametrize("device", ["X8", "Y19", "X400", "M4096", "D10000", "S0", "T5", "", "M"])
def test_to_modbus_rejects_invalid(device):
    with pytest.raises(ValueError):
        to_modbus(device)
