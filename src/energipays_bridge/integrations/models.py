"""Pydantic models for integration config and field mappings."""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, model_validator

from .normalise import SIGNED_METRICS


class FieldMapping(BaseModel):
    source: str          # protocol-specific: dot-path / "fc3:40206:uint16:0.1" / entity_id / "topic:path"
    target_metric: str   # one of the standard ext.* metric keys (without the "ext." prefix)
    scale: float = 1.0   # multiply raw value by this (default 1.0 = no scaling)
    enabled: bool = True # when False this mapping is skipped during polling
    # What a positive source value means, for signed metrics (see normalise.SIGNED_METRICS):
    # battery_power_w "discharge"|"charge", grid_power_w "import"|"export". None = the
    # internal convention (battery + discharge, grid + import).
    positive: Optional[str] = None
    # Source value (as text, e.g. "1" or "Idle") → internal state id, e.g. {"1": "emergency_backup"}
    value_map: dict[str, str] = {}

    @model_validator(mode="after")
    def _sign(self) -> "FieldMapping":
        signed = SIGNED_METRICS.get(self.target_metric)
        if signed is None:
            if self.positive is not None:
                raise ValueError(f"'positive' applies only to {sorted(SIGNED_METRICS)}")
            return self
        if self.positive is not None and self.positive not in signed:
            raise ValueError(f"{self.target_metric}: positive must be one of {list(signed)}")
        # Before #15 the only way to flip a sign was a negative scale: keep its meaning
        # as an explicit sign so the UI can show it.
        if self.scale < 0:
            current = self.positive or signed[0]
            self.positive = signed[1] if current == signed[0] else signed[0]
            self.scale = -self.scale
        return self


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
    # Optional: convert the first register as a mapping would (#15)
    target_metric: Optional[str] = None
    scale: float = 1.0
    positive: Optional[str] = None
    value_map: dict[str, str] = {}


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
