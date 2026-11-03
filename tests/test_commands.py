"""REST + MQTT commands: rule activation (#2), boost periods (#3), field names (#6),
real outcomes (#8)."""
import pytest
from fastapi.testclient import TestClient

from conftest import DEVICE, FakeClient, run
from energipays_bridge.publish.mqtt_publisher import MqttPublisher


# ── REST ─────────────────────────────────────────────────────────────────────

def test_ui_rule_activation_uses_activate_rule(app_factory):
    c = FakeClient()
    r = TestClient(app_factory(c)).post("/api/device/set", json={"rule_id": "r1", "rule_type": "offpeak"})
    assert r.status_code == 200
    assert c.calls == [("activate_rule", (DEVICE, "r1", "offpeak"), {})]


def test_ui_rule_off_peak_spelling_is_normalised(app_factory):
    c = FakeClient()
    TestClient(app_factory(c)).post("/api/device/set", json={"rule_id": "r1", "rule_type": "off_peak"})
    assert c.calls[0][1][2] == "offpeak"


def test_ui_rule_cloud_error_is_502(app_factory):
    c = FakeClient(activate_rule={"error": "500 Server Error", "status": 500})
    r = TestClient(app_factory(c)).post("/api/device/set", json={"rule_id": "r1"})
    assert r.status_code == 502


def test_ui_rule_grouped_device_is_409(app_factory):
    c = FakeClient(activate_rule={"skipped": "device is in a group"})
    r = TestClient(app_factory(c)).post("/api/device/set", json={"rule_id": "r1"})
    assert r.status_code == 409


def test_create_rule_uses_offpeak_and_reports_errors(app_factory):
    c = FakeClient(create_rule={"error": "422", "status": 422})
    r = TestClient(app_factory(c)).post("/api/rules", json={"name": "n", "type": "off_peak"})
    assert c.calls[0][1] == ("n", "offpeak")
    assert r.status_code == 502


def test_unknown_rule_type_is_400(app_factory):
    r = TestClient(app_factory(FakeClient())).post("/api/rules", json={"name": "n", "type": "x"})
    assert r.status_code == 400


@pytest.mark.parametrize("period", [2, 3, 4])
def test_boost_accepts_the_sites_periods(app_factory, period):
    c = FakeClient(boost_device=[])
    r = TestClient(app_factory(c)).post("/api/boost", json={"period": period})
    assert r.status_code == 200
    assert c.calls[0][2]["period"] == period


@pytest.mark.parametrize("period", [0, 1, 5])
def test_boost_refuses_other_periods_without_a_call(app_factory, period):
    c = FakeClient()
    assert TestClient(app_factory(c)).post("/api/boost", json={"period": period}).status_code == 400
    assert c.calls == []


def test_boost_clear_rule_uses_activate_rule(app_factory):
    c = FakeClient(boost_device=[])
    TestClient(app_factory(c)).post("/api/boost", json={"period": 2, "clear_rule": True})
    assert c.calls[0] == ("activate_rule", (DEVICE, "0", "command"), {})


def test_boost_error_is_502(app_factory):
    c = FakeClient(boost_device={"error": "x", "status": 500})
    assert TestClient(app_factory(c)).post("/api/boost", json={"period": 2}).status_code == 502


def test_read_only_blocks_before_any_call(app_factory):
    c = FakeClient()
    r = TestClient(app_factory(c, read_only=True)).post("/api/device/set", json={"rule_id": "r1"})
    assert r.status_code == 403 and c.calls == []


# ── MQTT ─────────────────────────────────────────────────────────────────────

def _pub(client):
    p = MqttPublisher("192.0.2.1")
    p._device_id = DEVICE
    p._ep_client = client
    p._data_server = "https://data-x"
    p._rule_name_to_id = {"Solar": "r1"}
    results = []

    async def capture(msg):
        results.append(msg)
    p._publish_cmd_result = capture
    sent = []

    async def enqueue(topic, payload, retain=False):
        sent.append((topic, payload))
    p._enqueue = enqueue
    return p, results, sent


def _cmd(p, slug, payload):
    run(p._handle_command(f"energipays/{DEVICE}/cmd/{slug}", payload))


def test_mqtt_active_rule_uses_activate_rule_not_forced_fields():
    c = FakeClient()
    p, results, _ = _pub(c)
    _cmd(p, "active_rule", "Solar")
    assert c.calls == [("activate_rule", (DEVICE, "r1", "command"), {})]
    assert results == ["Active rule → 'Solar': OK"]


def test_mqtt_failure_is_reported_failed_and_state_not_echoed():
    c = FakeClient(set_device_status={"error": "500", "status": 500})
    p, results, sent = _pub(c)
    _cmd(p, "power_diverter_sw", "ON")
    assert results and "FAILED" in results[0] and "OK" not in results[0]
    assert sent == []


def test_mqtt_power_diverter_writes_status():
    c = FakeClient()
    p, _, _ = _pub(c)
    _cmd(p, "power_diverter_sw", "OFF")
    assert c.calls == [("set_device_status", (DEVICE,), {"status": 0})]


@pytest.mark.parametrize("duration, period", [("1 hour", 2), ("2 hours", 3), ("3 hours", 4)])
def test_mqtt_boost_uses_the_sites_periods(duration, period):
    c = FakeClient()
    p, _, _ = _pub(c)
    _cmd(p, "boost_duration", duration)
    _cmd(p, "boost_start", "PRESS")
    assert c.calls[-1] == ("boost_device", (DEVICE, "https://data-x", period), {})


def test_mqtt_30_min_is_not_an_option():
    c = FakeClient()
    p, _, _ = _pub(c)
    _cmd(p, "boost_duration", "30 min")
    _cmd(p, "boost_start", "PRESS")
    assert c.calls[-1][1][2] == 2   # stays on the 1 hour default; never period 1


def test_mqtt_queued_is_reported_queued():
    c = FakeClient(boost_device={"queued": True, "status": 202})
    p, results, _ = _pub(c)
    _cmd(p, "boost_start", "PRESS")
    assert "QUEUED" in results[0]


def test_mqtt_set_device_moves_the_target():
    c = FakeClient()
    p, _, _ = _pub(c)
    p.set_device("00000000-0000-4000-8000-000000000002", "https://data-y")
    run(p._handle_command("energipays/00000000-0000-4000-8000-000000000002/cmd/heater_sw", "ON"))
    assert c.calls[-1][1][0] == "00000000-0000-4000-8000-000000000002"
    run(p._handle_command(f"energipays/{DEVICE}/cmd/heater_sw", "ON"))
    assert len(c.calls) == 1   # old device's topic is ignored
