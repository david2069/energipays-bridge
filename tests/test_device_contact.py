"""PowerDiverter contact: offline when Energipays says so or its data stops changing (#36)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from conftest import DEVICE, FakeClient, run
from energipays_bridge.poller import (DEVICE_STALE_S, DeviceContact, EnergipaysPoller,
                                      last_data_slot)
from energipays_bridge.sample import SampleBus

TZ = "Australia/Sydney"


def test_reported_offline_is_not_connected():
    c = DeviceContact()
    assert c.update({"phasePower": 100, "is_online": 1}, 0) == (True, True)
    assert c.update({"phasePower": 120, "is_online": 0}, 60) == (False, True)


def test_unchanged_grid_and_tank_go_stale():
    c = DeviceContact()
    pts = {"phasePower": -5270, "waterTemperature1": 52, "is_online": 1}
    c.update(dict(pts), 0)
    assert c.update(dict(pts), DEVICE_STALE_S)[0] is True
    assert c.update(dict(pts), DEVICE_STALE_S + 1) == (False, True)
    # a changed reading is contact again, from that poll
    assert c.update({**pts, "phasePower": -5100}, DEVICE_STALE_S + 60) == (True, True)
    assert c.last_data_ts == DEVICE_STALE_S + 60


def test_no_telemetry_at_all_is_not_connected():
    assert DeviceContact().update({"is_online": 1}, 0)[0] is False


def test_last_data_slot_is_latest_nonzero_slot_in_device_time():
    raw = {"analytics": {
        "2026-11-09 13:45:00": {"EIP": 1.2, "SP": 3.0},
        "2026-11-09 14:00:00": {"EIP": 0.4, "SP": 0},
        "2026-11-09 14:15:00": {"EIP": 0, "SP": 0},
    }}
    want = datetime(2026, 11, 9, 14, 0, tzinfo=ZoneInfo(TZ)).timestamp()
    assert last_data_slot(raw, TZ) == want
    assert last_data_slot({"analytics": {"2026-11-09 14:15:00": {"EIP": 0}}}, TZ) is None
    assert last_data_slot({"error": "x"}, TZ) is None


def _poller(client):
    bus = SampleBus()
    got, events = [], []

    async def sub(s):
        got.append(s)
    bus.subscribe(sub)
    p = EnergipaysPoller(client, bus, DEVICE, "https://data-x", tz=TZ)

    async def ev(name, ctx):
        events.append((name, ctx))
    p.on_health_event = ev
    return p, got, events


def test_poll_marks_offline_and_finds_last_data():
    stats = {"analytics": {"2026-11-09 14:00:00": {"EIP": 0.4}}}
    c = FakeClient(device_status={"data": [{"is_online": 0, "status": {"phasePower": -5270}}]},
                   stats=stats, device={})
    p, got, events = _poller(c)
    run(p._poll())
    pts = got[0].points
    assert pts["device_connected"] == 0
    assert pts["device_last_data_ts"] == round(datetime(2026, 11, 9, 14, 0, tzinfo=ZoneInfo(TZ)).timestamp())
    assert events == [("device_offline", {"ts": "14:00 Mon 09 Nov", "since": "14:00 Mon 09 Nov"})]
    assert any(x[0] == "stats" for x in c.calls)
    run(p._poll())                      # still offline: no second alert
    assert len(events) == 1


def test_poll_online_first_time_raises_nothing():
    c = FakeClient(device_status={"data": [{"is_online": 1, "status": {"phasePower": 10}}]}, device={})
    p, got, events = _poller(c)
    run(p._poll())
    assert got[0].points["device_connected"] == 1 and events == []
    assert not any(x[0] == "stats" for x in c.calls)


def test_ha_entities_for_contact():
    from energipays_bridge.publish.entities import ENTITY_BY_SLUG
    assert ENTITY_BY_SLUG["device_connected"].format_value(0) == "OFF"
    assert ENTITY_BY_SLUG["device_connected"].format_value(1) == "ON"
    iso = "2026-11-09T14:00:00+11:00"
    assert ENTITY_BY_SLUG["device_last_data"].format_value(iso) == iso


def test_poll_publishes_last_data_as_iso():
    stats = {"analytics": {"2026-11-09 14:00:00": {"EIP": 0.4}}}
    c = FakeClient(device_status={"data": [{"is_online": 0, "status": {"phasePower": 1}}]},
                   stats=stats, device={})
    p, got, _ = _poller(c)
    run(p._poll())
    assert got[0].points["device_last_data"] == "2026-11-09T14:00:00+11:00"


def test_offline_sample_has_no_readings_but_keeps_settings():
    from energipays_bridge.poller import DEVICE_READINGS
    st = {"data": [{"is_online": 0, "status": {
        "phasePower": 5.27, "waterTemperature1": 76, "solarStreamA": 5.49, "signalWiFi": -42,
        "boostStatus": 0, "boost_power": 4, "temperatureLimit": 85}}]}
    c = FakeClient(device_status=st, stats={}, device={})
    p, got, _ = _poller(c)
    run(p._poll())
    pts = got[0].points
    assert not (set(pts) & DEVICE_READINGS), set(pts) & DEVICE_READINGS
    assert pts["boost_power"] == 4 and pts["temperatureLimit"] == 85 and pts["is_online"] == 0


def test_online_sample_keeps_readings():
    st = {"data": [{"is_online": 1, "status": {"phasePower": 5.27, "waterTemperature1": 76}}]}
    p, got, _ = _poller(FakeClient(device_status=st, device={}))
    run(p._poll())
    assert got[0].points["phasePower"] == 5.27 and got[0].points["waterTemperatureAvg"] == 76


def test_ha_readings_go_unavailable_settings_do_not():
    from energipays_bridge.publish.entities import ENERGIPAYS_ENTITIES
    from energipays_bridge.publish.mqtt_publisher import MqttPublisher
    from energipays_bridge.sample import Sample
    by_key = {e.stat_key: e for e in ENERGIPAYS_ENTITIES}
    grid = by_key["phasePower"].discovery_payload("pd1", {})
    assert grid["availability_topic"] == "energipays/pd1/device_availability"
    assert "availability_topic" not in by_key["dev.firmware_version"].discovery_payload("pd1", {})
    assert "availability_topic" not in by_key["device_connected"].discovery_payload("pd1", {})
    pub = MqttPublisher("localhost")
    pub._connected = True
    run(pub.queue_sample(Sample("pd1", 1, {"device_connected": 0})))
    msgs = []
    while not pub._queue.empty():
        msgs.append(pub._queue.get_nowait())
    assert ("energipays/pd1/device_availability", "offline", True) in msgs
