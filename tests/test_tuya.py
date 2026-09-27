"""Step 6: Tuya Cloud client (signing, token cache, errors) with a fake HTTP transport."""

import json

import pytest

from app.tuya import TuyaCloud, TuyaError, sign, string_to_sign

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SECRET = "top-secret-value"
TOKEN = "tok-123"


# --- signing: known answers from Tuya's "Sign requests for cloud authorization" doc ----

DOC = dict(secret="4OHBOnWOqaEC1mWXOpVL3yV50s0qGSRC", client_id="1KAD46OrT9HafiKdsXeg",
           t_ms="1588925778000", nonce="5138cc3a9033d69856923fd07b491173")
DOC_HEADERS = "area_id:29a33e8796834b1efa6\ncall_id:8afdb70ab2ed11eb85290242ac130003\n"


def test_sign_token_request_matches_tuya_doc():
    to_sign = f"GET\n{EMPTY_SHA256}\n{DOC_HEADERS}\n/v1.0/token?grant_type=1"
    assert sign(**DOC, to_sign=to_sign) == "9E48A3E93B302EEECC803C7241985D0A34EB944F40FB573C7B5C2A82158AF13E"


def test_sign_business_request_matches_tuya_doc():
    to_sign = f"GET\n{EMPTY_SHA256}\n{DOC_HEADERS}\n/v2.0/apps/schema/users?page_no=1&page_size=50"
    assert (sign(**DOC, to_sign=to_sign, access_token="3f4eda2bdec17232f67c0b188af3eec1")
            == "AE4481C692AA80B25F3A7E12C3A5FD9BBF6251539DD78E565A1A72A508A88784")


def test_string_to_sign_sorts_query_and_hashes_empty_body():
    assert string_to_sign("get", "/v1/x?b=2&a=1") == f"GET\n{EMPTY_SHA256}\n\n/v1/x?a=1&b=2"


# --- client with a fake transport ------------------------------------------------------

class FakeTuya:
    """Answers token and status requests; replies can be scripted per path."""

    def __init__(self, status=None):
        self.status = status if status is not None else [{"code": "battery_percentage", "value": 87}]
        self.requests = []           # (method, url, headers)
        self.scripted = {}           # path prefix -> list of replies to use first
        self.tokens_issued = 0

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers))
        path = url.removeprefix("https://tuya.test")
        for prefix, replies in self.scripted.items():
            if path.startswith(prefix) and replies:
                return replies.pop(0)
        if path.startswith("/v1.0/token"):
            self.tokens_issued += 1
            return 200, json.dumps({"success": True, "result": {
                "access_token": f"{TOKEN}-{self.tokens_issued}", "expire_time": 7200}}).encode()
        return 200, json.dumps({"success": True, "result": self.status}).encode()

    def paths(self):
        return [url.removeprefix("https://tuya.test").split("?")[0] for _, url, _ in self.requests]


def make(transport, clock=lambda: 1000.0):
    return TuyaCloud("https://tuya.test/", "client-1", SECRET, transport=transport,
                     clock=clock, nonce=lambda: "n0")


def test_device_status_returns_dp_dict_and_signs_headers():
    fake = FakeTuya([{"code": "battery_percentage", "value": 87}, {"code": "pv_volt", "value": 181}])
    assert make(fake).device_status("dev1") == {"battery_percentage": 87, "pv_volt": 181}
    assert fake.paths() == ["/v1.0/token", "/v1.0/iot-03/devices/dev1/status"]
    _, _, token_headers = fake.requests[0]
    _, _, status_headers = fake.requests[1]
    assert "access_token" not in token_headers
    assert status_headers["access_token"] == f"{TOKEN}-1"
    assert status_headers["client_id"] == "client-1"
    assert status_headers["sign_method"] == "HMAC-SHA256"
    assert status_headers["t"] == "1000000"
    expected = sign(SECRET, "client-1", "1000000", "n0",
                    string_to_sign("GET", "/v1.0/iot-03/devices/dev1/status"), f"{TOKEN}-1")
    assert status_headers["sign"] == expected


def test_token_is_cached_then_refreshed_before_expiry():
    now = [1000.0]
    fake = FakeTuya()
    cloud = make(fake, clock=lambda: now[0])
    cloud.device_status("dev1")
    now[0] += 7000                   # still > 60 s before expiry (7200 s)
    cloud.device_status("dev1")
    assert fake.tokens_issued == 1
    now[0] += 150                    # inside the refresh margin
    cloud.device_status("dev1")
    assert fake.tokens_issued == 2


def test_invalid_token_is_refreshed_once():
    fake = FakeTuya()
    cloud = make(fake)
    cloud.device_status("dev1")
    fake.scripted["/v1.0/iot-03"] = [(200, b'{"success": false, "code": 1010, "msg": "token invalid"}')]
    assert cloud.device_status("dev1") == {"battery_percentage": 87}
    assert fake.tokens_issued == 2


def test_invalid_token_twice_gives_up():
    fake = FakeTuya()
    bad = (200, b'{"success": false, "code": 1010, "msg": "token invalid"}')
    fake.scripted["/v1.0/iot-03"] = [bad, bad]
    with pytest.raises(TuyaError):
        make(fake).device_status("dev1")
    assert fake.paths().count("/v1.0/iot-03/devices/dev1/status") == 2   # no endless retry


def test_api_error_is_reported_without_secrets():
    fake = FakeTuya()
    fake.scripted["/v1.0/iot-03"] = [(200, b'{"success": false, "code": 2008, "msg": "device offline"}')]
    with pytest.raises(TuyaError, match="2008: device offline") as info:
        make(fake).device_status("dev1")
    assert SECRET not in str(info.value) and TOKEN not in str(info.value)


def test_non_json_reply_and_bad_token_reply():
    fake = FakeTuya()
    fake.scripted["/v1.0/token"] = [(502, b"<html>Bad gateway</html>")]
    with pytest.raises(TuyaError, match="HTTP 502"):
        make(fake).device_status("dev1")
    fake.scripted["/v1.0/token"] = [(200, b'{"success": true, "result": {}}')]
    with pytest.raises(TuyaError, match="unexpected token response"):
        make(fake).device_status("dev1")


def test_network_error_from_urllib_becomes_tuya_error():
    cloud = TuyaCloud("http://127.0.0.1:9", "client-1", SECRET, timeout_s=1)
    with pytest.raises(TuyaError, match="cannot reach Tuya Cloud") as info:
        cloud.device_status("dev1")
    assert SECRET not in str(info.value)
