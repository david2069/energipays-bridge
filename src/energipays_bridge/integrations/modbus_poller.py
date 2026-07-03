"""Modbus TCP integration poller — raw asyncio socket, FC3/FC4, no pysunspec."""
from __future__ import annotations

import asyncio
import logging
import struct
from typing import Literal

from ..sample import SampleBus
from .base import IntegrationPoller
from .models import FieldMapping

log = logging.getLogger(__name__)

RegType = Literal["uint16", "int16", "uint32", "int32", "float32"]

# Register count needed per type
_REG_COUNT: dict[str, int] = {
    "uint16": 1, "int16": 1,
    "uint32": 2, "int32": 2, "float32": 2,
}

_TRANSACTION_ID = 0


def _next_tid() -> int:
    global _TRANSACTION_ID
    _TRANSACTION_ID = (_TRANSACTION_ID + 1) & 0xFFFF
    return _TRANSACTION_ID


def _build_request(fc: int, address: int, count: int, unit_id: int = 1) -> bytes:
    tid = _next_tid()
    pdu = struct.pack(">BBH H", fc, 0x00, address, count)[1:]  # skip first byte
    # Actually: function code + start addr + quantity
    pdu = struct.pack(">B H H", fc, address, count)
    mbap = struct.pack(">H H H B", tid, 0, len(pdu) + 1, unit_id)
    return mbap + pdu


def _parse_response(data: bytes, fc: int, address: int, count: int, reg_type: RegType) -> list[int | float]:
    """Parse Modbus TCP response, return list of typed values."""
    # MBAP header = 6 bytes, then PDU: fc(1) + byte_count(1) + data
    if len(data) < 9:
        raise ValueError(f"Response too short: {len(data)} bytes")
    resp_fc = data[7]
    if resp_fc != fc:
        raise ValueError(f"Unexpected function code in response: {resp_fc}")
    byte_count = data[8]
    payload = data[9:9 + byte_count]
    n_regs = byte_count // 2
    raw_regs = struct.unpack(f">{n_regs}H", payload)

    values: list[int | float] = []
    step = _REG_COUNT[reg_type]
    for i in range(0, len(raw_regs), step):
        chunk = raw_regs[i:i + step]
        if len(chunk) < step:
            break
        if reg_type == "uint16":
            values.append(chunk[0])
        elif reg_type == "int16":
            v = chunk[0]
            values.append(v - 65536 if v >= 32768 else v)
        elif reg_type == "uint32":
            values.append((chunk[0] << 16) | chunk[1])
        elif reg_type == "int32":
            v = (chunk[0] << 16) | chunk[1]
            values.append(v - 2**32 if v >= 2**31 else v)
        elif reg_type == "float32":
            packed = struct.pack(">HH", chunk[0], chunk[1])
            values.append(struct.unpack(">f", packed)[0])
    return values


async def read_registers(
    host: str,
    port: int,
    unit_id: int,
    fc: int,
    address: int,
    count: int,
    reg_type: RegType = "uint16",
    timeout: float = 5.0,
) -> list[int | float]:
    request = _build_request(fc, address, count, unit_id)
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout
    )
    try:
        writer.write(request)
        await writer.drain()
        # Read MBAP (6) + PDU header (2) + data
        header = await asyncio.wait_for(reader.readexactly(6), timeout=timeout)
        length = struct.unpack(">H", header[4:6])[0]
        rest = await asyncio.wait_for(reader.readexactly(length), timeout=timeout)
        full = header + rest
        return _parse_response(full, fc, address, count, reg_type)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


def _parse_source(source: str) -> tuple[int, int, RegType, float]:
    """Parse 'fc3:40001:uint16:0.1' → (fc, address, type, scale)."""
    parts = source.split(":")
    if len(parts) != 4:
        raise ValueError(f"Invalid Modbus source format: {source!r} (expected fc3:addr:type:scale)")
    fc_str, addr_str, reg_type, scale_str = parts
    fc = int(fc_str.replace("fc", ""))
    return fc, int(addr_str), reg_type, float(scale_str)  # type: ignore[return-value]


class ModbusPoller(IntegrationPoller):
    def __init__(
        self,
        integration_id: str,
        name: str,
        bus: SampleBus,
        host: str,
        port: int,
        unit_id: int,
        mappings: list[FieldMapping],
        poll_interval: int = 10,
    ) -> None:
        super().__init__(integration_id, name, bus, poll_interval)
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.mappings = mappings

    async def _poll(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for m in self.mappings:
            try:
                fc, address, reg_type, scale = _parse_source(m.source)
            except ValueError as exc:
                log.debug("Skipping invalid mapping source %r: %s", m.source, exc)
                continue
            count = _REG_COUNT[reg_type]
            values = await read_registers(self.host, self.port, self.unit_id, fc, address, count, reg_type)
            if values:
                result[m.target_metric] = values[0] * scale
        # Derive battery_state from battery_power_w sign (M714.DCW convention:
        # positive = charging, negative = discharging; FranklinWH M713.Sta always 0)
        if "battery_power_w" in result and "battery_state" not in result:
            pw = result["battery_power_w"]
            result["battery_state"] = "charging" if pw > 50 else "discharging" if pw < -50 else "idle"
        return result
