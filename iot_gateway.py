from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
from dataclasses import dataclass
from typing import Dict, List


class ModbusError(RuntimeError):
    pass


class ModbusTCPClient:
    def __init__(self, host: str, port: int = 502, unit_id: int = 1, timeout: float = 3.0) -> None:
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self._transaction_id = 0

    def _next_transaction_id(self) -> int:
        self._transaction_id = (self._transaction_id + 1) & 0xFFFF
        return self._transaction_id

    @staticmethod
    def _read_exact(sock: socket.socket, size: int) -> bytes:
        chunks: List[bytes] = []
        received = 0
        while received < size:
            chunk = sock.recv(size - received)
            if not chunk:
                raise ModbusError("Connection closed while reading Modbus response")
            chunks.append(chunk)
            received += len(chunk)
        return b"".join(chunks)

    def _request(self, function_code: int, payload: bytes) -> bytes:
        transaction_id = self._next_transaction_id()
        protocol_id = 0
        length = 1 + 1 + len(payload)  # unit + function + payload
        header = struct.pack(">HHHB", transaction_id, protocol_id, length, self.unit_id)
        packet = header + bytes([function_code]) + payload

        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.sendall(packet)
            response_header = self._read_exact(sock, 7)
            r_transaction_id, r_protocol_id, r_length, r_unit_id = struct.unpack(">HHHB", response_header)

            if r_transaction_id != transaction_id:
                raise ModbusError("Mismatched Modbus transaction ID")
            if r_protocol_id != 0:
                raise ModbusError("Invalid Modbus protocol ID")
            if r_unit_id != self.unit_id:
                raise ModbusError("Mismatched Modbus unit ID")
            if r_length < 2:
                raise ModbusError("Malformed Modbus response length")

            pdu = self._read_exact(sock, r_length - 1)

        response_function = pdu[0]
        if response_function == (function_code | 0x80):
            exception_code = pdu[1] if len(pdu) > 1 else 0
            raise ModbusError(f"Modbus exception response: {exception_code}")
        if response_function != function_code:
            raise ModbusError("Unexpected Modbus function code in response")

        return pdu[1:]

    def read_holding_registers(self, address: int, count: int) -> List[int]:
        payload = struct.pack(">HH", address, count)
        response_payload = self._request(3, payload)
        if not response_payload:
            raise ModbusError("Empty read response payload")

        byte_count = response_payload[0]
        expected_bytes = count * 2
        data = response_payload[1:]
        if byte_count != expected_bytes or len(data) != expected_bytes:
            raise ModbusError("Unexpected register byte count in response")

        return [
            struct.unpack(">H", data[index : index + 2])[0]
            for index in range(0, expected_bytes, 2)
        ]

    def write_single_register(self, address: int, value: int) -> None:
        payload = struct.pack(">HH", address, value)
        response_payload = self._request(6, payload)
        if len(response_payload) != 4:
            raise ModbusError("Malformed write response payload")

        written_address, written_value = struct.unpack(">HH", response_payload)
        if written_address != address or written_value != value:
            raise ModbusError("Write response does not match request")


@dataclass(frozen=True)
class GatewayRegisterMap:
    robot_state_addr: int = 0
    battery_percent_addr: int = 1
    panel_voltage_addr: int = 2
    panel_current_addr: int = 3
    fault_code_addr: int = 4

    start_cleaning_addr: int = 100
    stop_cleaning_addr: int = 101
    return_to_charge_addr: int = 102
    manual_mode_addr: int = 103


class IoTGateway:
    def __init__(self, client: ModbusTCPClient, register_map: GatewayRegisterMap | None = None) -> None:
        self.client = client
        self.register_map = register_map or GatewayRegisterMap()

    def read_status(self) -> Dict[str, float | int]:
        start_addr = self.register_map.robot_state_addr
        registers = self.client.read_holding_registers(start_addr, 5)

        return {
            "robot_state": registers[0],
            "battery_percent": registers[1],
            "panel_voltage_v": registers[2] / 10.0,
            "panel_current_a": registers[3] / 10.0,
            "fault_code": registers[4],
        }

    def send_command(self, command: str, value: int = 1) -> None:
        command_registers = {
            "start_cleaning": self.register_map.start_cleaning_addr,
            "stop_cleaning": self.register_map.stop_cleaning_addr,
            "return_to_charge": self.register_map.return_to_charge_addr,
            "manual_mode": self.register_map.manual_mode_addr,
        }
        if command not in command_registers:
            valid = ", ".join(sorted(command_registers.keys()))
            raise KeyError(f"Unknown command '{command}'. Valid commands: {valid}")
        if not 0 <= value <= 0xFFFF:
            raise ValueError("Command value must fit in an unsigned 16-bit register")

        self.client.write_single_register(command_registers[command], value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="IoT gateway for Delta PLC via Modbus TCP")
    parser.add_argument("--host", required=True, help="PLC Modbus TCP host/IP")
    parser.add_argument("--port", type=int, default=502, help="PLC Modbus TCP port")
    parser.add_argument("--unit-id", type=int, default=1, help="Modbus unit ID")
    parser.add_argument("--timeout", type=float, default=3.0, help="Socket timeout in seconds")

    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("status", help="Read robot status registers")

    command_parser = subparsers.add_parser("command", help="Send command register write")
    command_parser.add_argument(
        "name",
        choices=["start_cleaning", "stop_cleaning", "return_to_charge", "manual_mode"],
        help="Command to send",
    )
    command_parser.add_argument(
        "--value", type=int, default=1, help="16-bit value to write for the command"
    )

    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    client = ModbusTCPClient(
        host=args.host,
        port=args.port,
        unit_id=args.unit_id,
        timeout=args.timeout,
    )
    gateway = IoTGateway(client)

    try:
        if args.action == "status":
            print(json.dumps(gateway.read_status(), ensure_ascii=False))
        elif args.action == "command":
            gateway.send_command(args.name, args.value)
            print(json.dumps({"ok": True, "command": args.name, "value": args.value}))
        else:
            parser.error(f"Unsupported action: {args.action}")
    except (ModbusError, OSError, KeyError, ValueError) as exc:
        print(f"Gateway error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
