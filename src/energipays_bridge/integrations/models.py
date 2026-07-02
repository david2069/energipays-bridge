"""Pydantic models for integration config and field mappings."""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel


class FieldMapping(BaseModel):
    source: str          # protocol-specific: dot-path / "fc3:40206:uint16:0.1" / entity_id / "topic:path"
    target_metric: str   # one of the standard ext.* metric keys (without the "ext." prefix)
    scale: float = 1.0   # multiply raw value by this (default 1.0 = no scaling)


class IntegrationIn(BaseModel):
    name: str
    type: Literal["battery", "solar"]
    protocol: Literal["rest", "modbus_tcp", "sunspec_tcp", "ha_ws", "mqtt"]
    config: dict         # protocol-specific fields (see registry.py for shape per protocol)
    mappings: list[FieldMapping]
    enabled: bool = True


class IntegrationOut(IntegrationIn):
    id: str
    created_at: float
    status: str = "unknown"   # "live" | "offline" | "disabled" | "unknown"
    last_error: str = ""


class ProbeRequest(BaseModel):
    host: str
    port: int = 502
    unit_id: int = 1
    fc: int = 3           # 3 = holding registers, 4 = input registers
    address: int = 0
    count: int = 10
    type: Literal["uint16", "int16", "uint32", "int32", "float32"] = "uint16"


class SunSpecDiscoverRequest(BaseModel):
    host: str
    port: int = 502
    unit_id: int = 1
    sunspec_start: int = 0   # address to start SunSpec 'SunS' scan; 0=FranklinWH, 40000=standard


class HaEntitiesRequest(BaseModel):
    url: str             # http://homeassistant.local:8123
    token: str
    domain: str = ""     # filter by domain, empty = all
    search: str = ""     # substring filter
    page: int = 1
    page_size: int = 50
