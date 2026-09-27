# Solar-Panel-Cleaning-Robot-via-IoT

IoT Gateway for the Bachelor's senior project (Robotics Engineering, KMUTNB):
**Development of an IoT-Based Control, Monitoring, and Self-Charging System for a Solar Panel Cleaning Robot**.

The Delta DVP-12SE11T PLC handles direct robot control. This repository provides a Raspberry Pi gateway that communicates with the PLC over **Modbus TCP** for status monitoring and command dispatch.

## Features

- Read robot status registers from PLC over Modbus TCP
- Send command writes to PLC over Modbus TCP
- Lightweight Python implementation (standard library only)

## Usage

From repository root:

```bash
python iot_gateway.py --host <PLC_IP> status
```

Send a command:

```bash
python iot_gateway.py --host <PLC_IP> command start_cleaning
python iot_gateway.py --host <PLC_IP> command return_to_charge --value 1
```

Optional connection arguments:

- `--port` (default `502`)
- `--unit-id` (default `1`)
- `--timeout` (default `3.0` seconds)

## Register mapping (default)

Status (holding registers):

- `0`: robot state
- `1`: battery percent
- `2`: panel voltage (`raw / 10` V)
- `3`: panel current (`raw / 10` A)
- `4`: fault code

Commands (single-register writes):

- `100`: `start_cleaning`
- `101`: `stop_cleaning`
- `102`: `return_to_charge`
- `103`: `manual_mode`

## Tests

```bash
python -m unittest discover -s tests -p "test_*.py"
```
