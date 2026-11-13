"""Battery entities from the battery integration's ext.* metrics (#20, phase H1)."""
import json

from conftest import run
from energipays_bridge.publish.entities import ENTITY_BY_SLUG
from energipays_bridge.publish.mqtt_publisher import MqttPublisher
from energipays_bridge.sample import Sample

PD = "pd1"


def _pub():
    p = MqttPublisher("localhost")
    p._connected = True
    return p


def _drain(p):
    out = []
    while not p._queue.empty():
        out.append(p._queue.get_nowait())
    return out


def test_on_grid_states():
    e = ENTITY_BY_SLUG["battery_on_grid"]
    assert e.format_value("on_grid") == "ON"
    assert e.format_value("off_grid") == "OFF"
    assert e.format_value("simulated") is None


def test_ext_sample_first_does_not_become_the_device():
    p = _pub()
    run(p.queue_sample(Sample("ext", 1, {"ext.battery_grid_status": "on_grid"})))
    assert p._device_id is None and p._latest_points == {}
    assert _drain(p) == []                    # nothing until the HA device is known
    run(p.queue_sample(Sample(PD, 2, {"dev.name": "Sunamp", "phasePower": 1})))
    msgs = _drain(p)
    disc = [m for m in msgs if m[0] == f"homeassistant/binary_sensor/energipays_{PD}_battery_on_grid/config"]
    assert len(disc) == 1 and json.loads(disc[0][1])["device"]["identifiers"] == [f"energipays_{PD}"]
    assert (f"energipays/{PD}/state/battery_on_grid", "ON", True) in msgs
    assert p._device_id == PD and "ext.battery_grid_status" not in p._latest_points


def test_ext_sample_updates_state_without_rediscovery():
    p = _pub()
    run(p.queue_sample(Sample(PD, 1, {"phasePower": 1})))
    _drain(p)
    run(p.queue_sample(Sample("ext", 2, {"ext.battery_grid_status": "on_grid"})))
    run(p.queue_sample(Sample("ext", 3, {"ext.battery_grid_status": "off_grid"})))
    msgs = _drain(p)
    assert sum(1 for m in msgs if m[0].endswith("battery_on_grid/config")) == 1
    assert msgs[-1] == (f"energipays/{PD}/state/battery_on_grid", "OFF", True)
    assert p._latest_points == {"phasePower": 1}


def test_no_battery_entity_without_its_metric():
    p = _pub()
    run(p.queue_sample(Sample(PD, 1, {"phasePower": 1})))
    run(p.queue_sample(Sample("ext", 2, {"ext.battery_soc": 50})))
    assert not any("battery_on_grid" in m[0] for m in _drain(p))
