"""SunSpec model discovery over Modbus TCP.

Scans a device for the SunS marker, walks the model chain, and returns
discovered models with suggested field mappings in fc3:ADDR:TYPE:SCALE format.
"""
from __future__ import annotations

import asyncio
import logging
import struct

from .modbus_poller import read_registers

log = logging.getLogger(__name__)

# Well-known SunSpec base addresses (0-based Modbus register addresses)
SUNSPEC_BASES = [40000, 0]

# SunS magic: ASCII "SunS" = 0x53756E53
SUNSPEC_MAGIC_HI = 0x5375
SUNSPEC_MAGIC_LO = 0x6E53

MODEL_NAMES: dict[int, tuple[str, str | None]] = {
    1:   ("Common",                    None),
    101: ("Single-Phase Inverter",     "solar"),
    102: ("Split-Phase Inverter",      "solar"),
    103: ("Three-Phase Inverter",      "solar"),
    111: ("Single-Phase Inverter (float)", "solar"),
    112: ("Split-Phase Inverter (float)", "solar"),
    113: ("Three-Phase Inverter (float)", "solar"),
    120: ("Nameplate",                 None),
    121: ("Basic Settings",            None),
    122: ("Measurements / Status",     None),
    123: ("Immediate Controls",        None),
    124: ("Basic Storage Controls",    "battery"),
    160: ("Multiple MPPT Extension",   "solar"),
    701: ("DER AC Measurement",        "battery"),
    702: ("DER Capacity",              "battery"),
    703: ("DER Enter Service",         None),
    704: ("DER Controls",              None),
    705: ("DER Volt-Var",              None),
    706: ("DER Volt-Watt",             None),
    707: ("DER Freq-Watt",             None),
    708: ("DER Watt-PF",               None),
    709: ("DER P-V Curve",             None),
    710: ("DER Limit",                 None),
    711: ("DER Ramp Rate",             None),
    712: ("DER Time-of-Use",           None),
    713: ("DER Storage Capacity",      "battery"),
    714: ("DER DC Measurement",        "battery"),
    715: ("DER AC Measurement Control",None),
    802: ("Li-Ion Battery Bank",       "battery"),
    803: ("Li-Ion Battery Module",     "battery"),
    804: ("Li-Ion String",             "battery"),
    805: ("Li-Ion Module",             "battery"),
}

# Points to auto-map per model.
# Each entry: (data_offset, point_id, reg_type, sf_offset_or_None, target_metric)
# sf_offset: offset (relative to data start) of the SunSpec scale-factor register (int16, power of 10)
#            None means use scale=1.0 (float32 models or unitless)
MODEL_AUTO_POINTS: dict[int, list[tuple[int, str, str, int | None, str]]] = {
    # int16 inverter models — W at offset 12, W_SF at 13
    101: [(12, "W", "int16", 13, "solar_power_w")],
    102: [(12, "W", "int16", 13, "solar_power_w")],
    103: [(12, "W", "int16", 13, "solar_power_w")],
    # float32 inverter models — W at offset 12 (2 regs), no SF
    111: [(12, "W", "float32", None, "solar_power_w")],
    112: [(12, "W", "float32", None, "solar_power_w")],
    113: [(12, "W", "float32", None, "solar_power_w")],
    # Model 701 DER AC Measurement (offsets from data_addr, after 2-register ID/L header)
    # Verified against SunSpec 701 register map via FranklinWH /api/models/701/read
    # data_off: ACType=0, St=1, InvSt=2, ConnSt=3, Alrm=4-5, DERMode=6-7, W=8, ...
    #           TmpAmb=33, TmpCab=34, ..., W_SF=114, ..., Tmp_SF=120
    701: [
        (8,   "W",       "int16",  114, "solar_power_w"),     # Active power (W)
        (2,   "InvSt",   "uint16", None, "inverter_state"),   # Inverter state enum
        (3,   "ConnSt",  "uint16", None, "connection_state"), # Grid connection state enum
        (6,   "DERMode", "uint32", None, "grid_status"),      # DER mode bitfield32
        (33,  "TmpAmb",  "int16",  120, "ambient_temp_c"),   # Ambient temperature
        (34,  "TmpCab",  "int16",  120, "cabinet_temp_c"),   # Cabinet temperature
    ],
    # Model 713 DER Storage Capacity
    # data[0]=WHRtg, data[1]=WHAvail, data[2]=SoC, data[3]=SoH, data[5]=WH_SF, data[6]=Pct_SF
    713: [
        (0, "WHRtg",   "uint16", 5, "battery_capacity_kwh", 0.001),  # Wh → kWh
        (1, "WHAvail", "uint16", 5, "energy_available_kwh", 0.001),  # Wh → kWh
        (2, "SoC",     "uint16", 6, "battery_soc"),
        (3, "SoH",     "uint16", 6, "battery_soh"),
    ],
    # Model 714 DER DC Measurement — DCW at data[4], DCW_SF at data[15]
    # Sign: positive = charging (power absorbed), negative = discharging (power injected)
    714: [
        (4, "DCW", "int16", 15, "battery_power_w"),
    ],
}

# Type widths in registers
_REG_COUNT = {"uint16": 1, "int16": 1, "uint32": 2, "int32": 2, "float32": 2}


async def scan_sunspec(
    host: str,
    port: int = 502,
    unit_id: int = 1,
    sunspec_start: int | None = None,
) -> dict:
    """
    Discover SunSpec models on a Modbus TCP device.

    Returns:
        {
          "ok": bool,
          "base_address": int,
          "models": [
            {
              "model_id": int,
              "name": str,
              "category": "solar"|"battery"|null,
              "data_address": int,       # absolute 0-based address of data start
              "length": int,             # data register count
              "suggested_mappings": [    # only for models with known auto-points
                {"target_metric": str, "source": "fc3:ADDR:TYPE:SCALE",
                 "point": str, "live_value": float|None}
              ]
            }
          ],
          "error": str|None
        }
    """
    base = await _find_base(host, port, unit_id, sunspec_start)
    if base is None:
        return {"ok": False, "base_address": None, "models": [], "error": "SunSpec marker not found. Device may not support SunSpec, or base address differs."}

    models = []
    offset = base + 2   # skip the 2-register "SunS" magic

    while True:
        try:
            hdr = await read_registers(host, port, unit_id, 3, offset, 2, "uint16")
        except Exception as exc:
            log.warning("SunSpec scan error at offset %d: %s", offset, exc)
            break
        if not hdr or len(hdr) < 2:
            break
        model_id, length = hdr[0], hdr[1]
        if model_id == 0xFFFF:
            break

        name, category = MODEL_NAMES.get(model_id, (f"Model {model_id}", None))
        data_addr = offset + 2  # first data register

        # Try to build suggested mappings for known models
        # Tuple format: (data_off, point_id, reg_type, sf_off_or_None, target_metric[, unit_scale])
        suggested = []
        for entry in MODEL_AUTO_POINTS.get(model_id, []):
            data_off, point_id, reg_type, sf_off, target_metric = entry[:5]
            unit_scale = entry[5] if len(entry) > 5 else 1.0
            abs_addr = data_addr + data_off
            scale = unit_scale
            live_value = None

            if sf_off is not None:
                sf_abs = data_addr + sf_off
                try:
                    sf_regs = await read_registers(host, port, unit_id, 3, sf_abs, 1, "int16")
                    if sf_regs:
                        sf_val = sf_regs[0]
                        scale = (10.0 ** sf_val) * unit_scale
                except Exception:
                    scale = unit_scale

            try:
                count = _REG_COUNT[reg_type]
                raw_regs = await read_registers(host, port, unit_id, 3, abs_addr, count, reg_type)
                if raw_regs:
                    live_value = round(raw_regs[0] * scale, 4)
            except Exception:
                pass

            suggested.append({
                "target_metric": target_metric,
                "source": f"fc3:{abs_addr}:{reg_type}:{scale}",
                "point": point_id,
                "live_value": live_value,
            })

        models.append({
            "model_id": model_id,
            "name": name,
            "category": category,
            "data_address": data_addr,
            "length": length,
            "suggested_mappings": suggested,
        })

        offset += 2 + length  # skip header + data

    return {
        "ok": True,
        "base_address": base,
        "models": models,
        "error": None,
    }


async def _find_base(host: str, port: int, unit_id: int, preferred_start: int | None = None) -> int | None:
    bases = ([preferred_start] + SUNSPEC_BASES) if preferred_start is not None else SUNSPEC_BASES
    for base in bases:
        try:
            regs = await read_registers(host, port, unit_id, 3, base, 2, "uint16")
            if regs and regs[0] == SUNSPEC_MAGIC_HI and regs[1] == SUNSPEC_MAGIC_LO:
                return base
        except Exception:
            continue
    return None
