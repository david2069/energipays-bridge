from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EntityDef:
    slug: str
    stat_key: str
    ha_type: str          # "sensor" | "binary_sensor" | "switch" | "select" | "button"
    name: str
    unit: str = ""
    device_class: str = ""
    state_class: str = ""
    icon: str = ""
    # writable entities
    writable: bool = False
    options: list[str] = field(default_factory=list)  # for select
    # diagnostic entities appear under "Diagnostic" in HA device page
    diagnostic: bool = False

    # ── topic helpers ──────────────────────────────────────────────────────

    def unique_id(self, device_id: str) -> str:
        return f"energipays_{device_id}_{self.slug}"

    def state_topic(self, device_id: str) -> str:
        return f"energipays/{device_id}/state/{self.slug}"

    def command_topic(self, device_id: str) -> str:
        return f"energipays/{device_id}/cmd/{self.slug}"

    def discovery_topic(self, device_id: str) -> str:
        return f"homeassistant/{self.ha_type}/energipays_{device_id}_{self.slug}/config"

    # ── value formatting ───────────────────────────────────────────────────

    def format_value(self, raw) -> str | None:
        if raw is None:
            return None
        if self.ha_type in ("binary_sensor", "switch"):
            return "ON" if int(raw) == 1 else "OFF"
        if self.ha_type == "select" and self.options == ["Off", "On"]:
            try:
                return "On" if int(raw) == 1 else "Off"
            except (TypeError, ValueError):
                return None
        try:
            return str(round(float(raw), 3))
        except (TypeError, ValueError):
            return str(raw)

    # ── discovery payload ──────────────────────────────────────────────────

    def discovery_payload(self, device_id: str, device_info: dict,
                          options_override: list[str] | None = None) -> dict:
        uid = self.unique_id(device_id)
        payload: dict = {
            "unique_id": uid,
            "object_id": uid,
            "name": self.name,
            "state_topic": self.state_topic(device_id),
            "device": device_info,
        }
        if self.writable:
            payload["command_topic"] = self.command_topic(device_id)
        if self.ha_type == "switch":
            payload["payload_on"] = "ON"
            payload["payload_off"] = "OFF"
        if self.ha_type == "button":
            payload["payload_press"] = "PRESS"
        if self.ha_type == "select":
            opts = options_override if options_override is not None else self.options
            payload["options"] = opts
        if self.unit:
            payload["unit_of_measurement"] = self.unit
        if self.device_class:
            payload["device_class"] = self.device_class
        if self.state_class:
            payload["state_class"] = self.state_class
        if self.icon:
            payload["icon"] = self.icon
        if self.diagnostic:
            payload["entity_category"] = "diagnostic"
        return payload


def _s(slug, stat_key, name, unit="", device_class="", state_class="measurement", icon="", diagnostic=False) -> EntityDef:
    return EntityDef(slug=slug, stat_key=stat_key, ha_type="sensor", name=name,
                     unit=unit, device_class=device_class, state_class=state_class,
                     icon=icon, diagnostic=diagnostic)


def _b(slug, stat_key, name, device_class="", diagnostic=False) -> EntityDef:
    return EntityDef(slug=slug, stat_key=stat_key, ha_type="binary_sensor", name=name,
                     device_class=device_class, diagnostic=diagnostic)


def _sw(slug, stat_key, name, icon="") -> EntityDef:
    return EntityDef(slug=slug, stat_key=stat_key, ha_type="switch", name=name,
                     writable=True, icon=icon)


def _sel(slug, stat_key, name, options: list[str], icon="") -> EntityDef:
    return EntityDef(slug=slug, stat_key=stat_key, ha_type="select", name=name,
                     writable=True, options=options, icon=icon)


def _btn(slug, name, icon="") -> EntityDef:
    return EntityDef(slug=slug, stat_key="", ha_type="button", name=name,
                     writable=True, icon=icon)


BOOST_DURATION_OPTIONS = ["30 min", "1 hour", "2 hours"]
BOOST_POWER_MAP = {1: "25%", 2: "50%", 3: "75%", 4: "100%"}
BOOST_POWER_OPTIONS = list(BOOST_POWER_MAP.values())
BOOST_POWER_REVERSE = {v: k for k, v in BOOST_POWER_MAP.items()}


ENERGIPAYS_ENTITIES: list[EntityDef] = [
    # ── Temperature sensors ────────────────────────────────────────────────
    _s("water_temp_avg",  "waterTemperatureAvg",  "Water Temp Avg",    "°C", "temperature"),
    _s("water_temp_top",  "waterTemperature3",    "Water Temp Top T3", "°C", "temperature"),
    _s("water_temp_mid",  "waterTemperature2",    "Water Temp Mid T2", "°C", "temperature"),
    _s("water_temp_bot",  "waterTemperature1",    "Water Temp Bot T1", "°C", "temperature"),
    _s("temperature_limit", "temperatureLimit",   "Water Temp Target", "°C", "temperature",
       icon="mdi:thermometer-high"),

    # ── Grid power ─────────────────────────────────────────────────────────
    # phasePower: negative = importing, positive = exporting
    _s("grid_power",        "phasePower",          "Grid Power",       "kW", "power"),
    _s("grid_import_today", "today.IEct",          "Grid Import Today","kWh","energy",
       state_class="total_increasing"),
    _s("grid_export_today", "today.EEct",          "Grid Export Today","kWh","energy",
       state_class="total_increasing"),
    _s("grid_import_week",  "week.IEct",           "Grid Import Week", "kWh","energy",
       state_class="total_increasing"),
    _s("grid_export_week",  "week.EEct",           "Grid Export Week", "kWh","energy",
       state_class="total_increasing"),

    # ── Phase voltages / power ─────────────────────────────────────────────
    _s("voltage_a",    "voltageA",    "Voltage L1",   "V", "voltage"),
    _s("voltage_b",    "voltageB",    "Voltage L2",   "V", "voltage"),
    _s("voltage_c",    "voltageC",    "Voltage L3",   "V", "voltage"),
    _s("power_a",      "phasePowerA", "Phase Power L1","kW","power"),
    _s("power_b",      "phasePowerB", "Phase Power L2","kW","power"),
    _s("power_c",      "phasePowerC", "Phase Power L3","kW","power"),

    # ── Heater power ──────────────────────────────────────────────────────
    # offPeakStreamHeaterPower: live heater draw (kW, negative = consuming). Present
    # whenever the heater is drawing power from any source (grid, solar, or off-peak).
    _s("heater_power",        "offPeakStreamHeaterPower",  "Heater Power",       "kW", "power"),
    # offPeakStreamExtraPower: Off-Peak (controlled-load) circuit live power (kW).
    # N/A when the off-peak circuit is not installed/active.
    _s("offpeak_power",       "offPeakStreamExtraPower",   "Off-Peak Power",     "kW", "power"),
    # divertedPowerHeater: solar kW actively being diverted to the heater.
    # Zero during grid/boost-only operation — non-zero only when solar diverting.
    _s("diverted_power_heater","divertedPowerHeater",       "Diverted Power Heater", "kW", "power"),

    # ── Solar / diverter ───────────────────────────────────────────────────
    _s("solar_power",         "solarStreamA",    "Solar Power",          "kW", "power"),
    _s("diverted_heat_today", "today.DE_h",      "Diverted Heat Today",  "kWh","energy",
       state_class="total_increasing"),
    _s("diverted_plug_today", "today.DE_e",      "Diverted Plug Today",  "kWh","energy",
       state_class="total_increasing"),
    _s("diverted_heat_week",  "week.DE_h",       "Diverted Heat Week",   "kWh","energy",
       state_class="total_increasing"),

    # ── Device / connectivity (diagnostic) ────────────────────────────────
    _s("wifi_signal",    "signalWiFi",            "WiFi Signal",  "dBm", "signal_strength",
       diagnostic=True),
    _b("lora_enabled",  "dev.is_lora_enabled",   "LoRa Enabled", diagnostic=True),
    _s("firmware",       "dev.firmware_version",  "Firmware",     "", "", state_class="",
       icon="mdi:chip", diagnostic=True),
    _s("serial_number",  "dev.ws_serial_number",  "Serial Number","", "", state_class="",
       icon="mdi:identifier", diagnostic=True),

    # ── Read-only binary sensor ────────────────────────────────────────────
    _b("off_grid", "dev.off_grid", "Off-Grid Mode", diagnostic=True),

    # ── Boost state ────────────────────────────────────────────────────────
    # boost_active: select so HA shows state AND can trigger start/cancel
    _sel("boost_active", "boostStatus", "Boost State", ["Off", "On"], "mdi:rocket-launch"),

    # ── Writable switches ──────────────────────────────────────────────────
    _sw("power_diverter_sw",  "sd.customer",                      "Power Diverter",        "mdi:lightning-bolt"),
    _sw("heater_sw",          "sd.heaterStatus",                  "Immersion Heater",      "mdi:water-boiler"),
    _sw("offpeak_switcher",   "sd.offpeakSwitcherStatus",         "Off-Peak Switcher",     "mdi:clock-time-eight"),
    _sw("weather_switcher",   "sd.weatherSwitcherStatus",         "Weather Boost Switcher","mdi:weather-partly-cloudy"),
    # WCR weather scores (0–100): today's and tomorrow's cloud-cover probability that triggers weather boost
    _s("wcr_today",    "wcr_today_score",    "Weather Score Today",    "%",  None),
    _s("wcr_tomorrow", "wcr_tomorrow_score", "Weather Score Tomorrow", "%",  None),
    _sw("solar_restriction",  "sd.solarRestrictionSwitcherStatus","Solar Restriction",     "mdi:solar-power"),
    _sw("priority_mode",      "sd.priorityModeSwitcherStatus",    "Priority Mode",         "mdi:star"),

    # ── Boost controls ─────────────────────────────────────────────────────
    # boost_duration: local select — chosen duration for next boost trigger
    _sel("boost_duration", "",          "Boost Duration",
         BOOST_DURATION_OPTIONS, "mdi:timer"),
    # boost_power: reflects dev.boost_power (read-only from API; label shown in HA)
    _sel("boost_power_sel", "dev.boost_power", "Boost Power",
         BOOST_POWER_OPTIONS, "mdi:flash"),
    # ── Active rule select — options populated dynamically ─────────────────
    _sel("active_rule", "active_rule_id", "Active Rule", [], "mdi:calendar-clock"),

    # ── Command result feedback (diagnostic) ──────────────────────────────
    # Published by the command handler after every write attempt (success or failure).
    # Blank until first command is issued.
    EntityDef(slug="last_cmd_result", stat_key="", ha_type="sensor",
              name="Last Command Result", icon="mdi:information-outline",
              state_class="", diagnostic=True),
    EntityDef(slug="last_cmd_ts", stat_key="", ha_type="sensor",
              name="Last Command Time", device_class="timestamp",
              state_class="", icon="mdi:clock-outline", diagnostic=True),
]

# Keyed lookup
ENTITY_BY_SLUG: dict[str, EntityDef] = {e.slug: e for e in ENERGIPAYS_ENTITIES}
