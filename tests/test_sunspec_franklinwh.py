"""SunSpec discovery on a FranklinWH aGate (#15, plan F2).

Register meanings from franklinwh-modbus docs/FRANKLINWH_MODBUS_EXTENSIONS.md and
API_COOKBOOK.md (M701 W = grid, + import). Fake register bank, no device.
"""
import asyncio

from energipays_bridge.integrations import sunspec_scanner


def bank(manufacturer: str) -> dict[int, int]:
    b = {40000: 0x5375, 40001: 0x6E53, 40002: 1, 40003: 66}
    text = manufacturer.encode().ljust(32, b"\0")
    for i in range(16):
        b[40004 + i] = int.from_bytes(text[2 * i:2 * i + 2], "big")
    b.update({40070: 701, 40071: 153, 40072 + 8: 1500, 40072 + 153: 0xFFFF})
    b.update({15502: 3867, 16000: 617, 15507: 2, 15508: 8, 15509: 15})
    return b


def scan(monkeypatch, regs):
    async def fake(host, port, unit, fc, address, count, reg_type="uint16", timeout=5.0):
        return [regs.get(address + i, 0) for i in range(count)]
    monkeypatch.setattr(sunspec_scanner, "read_registers", fake)
    return asyncio.run(sunspec_scanner.scan_sunspec("192.0.2.10"))


def _suggest(result):
    return {(m["model_id"], s["point"]): s for m in result["models"] for s in m["suggested_mappings"]}


def test_agate_701_w_is_grid_power_and_extension_registers_are_offered(monkeypatch):
    r = scan(monkeypatch, bank("FranklinWH Technologies Co., Ltd"))
    assert r["manufacturer"] == "FranklinWH Technologies Co., Ltd"
    s = _suggest(r)
    assert s[(701, "W")]["target_metric"] == "grid_power_w"
    ext = {k[1]: v for k, v in s.items() if k[0] == "ext"}
    assert {p: (v["target_metric"], v["source"], v["live_value"]) for p, v in ext.items()} == {
        "PVOutputP": ("solar_power_w", "fc3:15502:uint16:1.0", 3867),
        "LoadActiveP": ("home_load_w", "fc3:16000:uint16:1.0", 617),
        "OnGridMode": ("operating_mode", "fc3:15507:uint16:1.0", 2),
        "SelfReserve": ("self_reserve_soc", "fc3:15508:uint16:1.0", 8),
        "TouReserve": ("tou_reserve_soc", "fc3:15509:uint16:1.0", 15),
    }
    assert "V10R01B04D00" in ext["TouReserve"]["note"]


def test_other_vendors_keep_generic_suggestions(monkeypatch):
    r = scan(monkeypatch, bank("Acme Inverters"))
    s = _suggest(r)
    assert s[(701, "W")]["target_metric"] == "solar_power_w"
    assert not any(m["model_id"] == "ext" for m in r["models"])


def test_extension_suggestions_validate_as_mappings():
    from energipays_bridge.integrations.models import FieldMapping
    from energipays_bridge.integrations.normalise import convert
    for address, _, reg_type, metric, _ in sunspec_scanner.FRANKLINWH_EXTENSION:
        FieldMapping(source=f"fc3:{address}:{reg_type}:1.0", target_metric=metric)
    assert convert(FieldMapping(source="x", target_metric="operating_mode"), 3) == "time_of_use"
