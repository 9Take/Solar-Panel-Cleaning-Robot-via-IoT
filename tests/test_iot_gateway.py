import struct
import unittest
from unittest.mock import patch

from iot_gateway import GatewayRegisterMap, IoTGateway, ModbusTCPClient


class FakeSocket:
    def __init__(self, response_chunks):
        self._response = b"".join(response_chunks)
        self.sent = b""

    def sendall(self, data):
        self.sent += data

    def recv(self, size):
        if not self._response:
            return b""
        chunk = self._response[:size]
        self._response = self._response[size:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class ModbusClientTests(unittest.TestCase):
    def test_read_holding_registers(self):
        transaction_id = 1
        response = struct.pack(">HHHB", transaction_id, 0, 5, 1) + struct.pack(
            ">BBH", 3, 2, 0x1234
        )

        fake_socket = FakeSocket([response])
        with patch("socket.create_connection", return_value=fake_socket):
            client = ModbusTCPClient("127.0.0.1", unit_id=1)
            registers = client.read_holding_registers(0, 1)

        self.assertEqual(registers, [0x1234])
        self.assertEqual(
            fake_socket.sent,
            struct.pack(">HHHBBHH", transaction_id, 0, 6, 1, 3, 0, 1),
        )


class GatewayTests(unittest.TestCase):
    def test_read_status_scales_values(self):
        class StubClient:
            def read_holding_registers(self, address, count):
                self.address = address
                self.count = count
                return [2, 87, 325, 41, 0]

        gateway = IoTGateway(StubClient(), GatewayRegisterMap())
        status = gateway.read_status()

        self.assertEqual(status["robot_state"], 2)
        self.assertEqual(status["battery_percent"], 87)
        self.assertEqual(status["panel_voltage_v"], 32.5)
        self.assertEqual(status["panel_current_a"], 4.1)
        self.assertEqual(status["fault_code"], 0)

    def test_send_command_writes_expected_register(self):
        class StubClient:
            def __init__(self):
                self.writes = []

            def write_single_register(self, address, value):
                self.writes.append((address, value))

        stub = StubClient()
        gateway = IoTGateway(stub, GatewayRegisterMap())
        gateway.send_command("return_to_charge", 1)

        self.assertEqual(stub.writes, [(102, 1)])


if __name__ == "__main__":
    unittest.main()
