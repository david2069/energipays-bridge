"""Integration secrets are encrypted at rest and never returned (#17)."""
import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from energipays_bridge.integrations import secret_store
from energipays_bridge.integrations.registry import IntegrationRegistry
from energipays_bridge.sample import SampleBus
from energipays_bridge.store.db import init_db

KEY = b"k" * 32
TOKEN = "eyJhbGciOiJIUzI1NiJ9.fake-long-lived-token"
HA_URL = "http://192.0.2.20:8123"


def run(coro):
    return asyncio.run(coro)


def ha_row(token=TOKEN, enabled=False):
    return {"name": "ha", "type": "battery", "protocol": "ha_ws",
            "config": {"url": HA_URL, "token": token}, "mappings": [], "enabled": enabled}


async def _reg(tmp_path, key=KEY):
    db = await init_db(tmp_path / "t.db")
    return IntegrationRegistry(db, SampleBus(), key)


async def _stored_config(reg, row_id):
    cur = await reg.db.execute("SELECT config FROM integrations WHERE id=?", (row_id,))
    return json.loads((await cur.fetchone())[0])


def test_secret_encrypted_at_rest_and_blank_in_api(tmp_path):
    async def go():
        reg = await _reg(tmp_path)
        row = await reg.create(ha_row())
        stored = await _stored_config(reg, row["id"])
        listed = await reg.list_all()
        got = await reg.get(row["id"])
        inner = await reg.get_with_secrets(row["id"])
        await reg.db.close()
        return row, stored, listed, got, inner
    row, stored, listed, got, inner = run(go())
    assert stored["token"].startswith("enc:v1:") and TOKEN not in json.dumps(stored)
    for out in (row, listed[0], got):
        assert out["config"]["token"] == "" and out["secrets_set"] == {"token": True}
        assert TOKEN not in json.dumps(out)
    assert inner["config"]["token"] == TOKEN


def test_update_blank_keeps_null_clears_value_replaces(tmp_path):
    async def go():
        reg = await _reg(tmp_path)
        row = await reg.create(ha_row())
        rid = row["id"]
        await reg.update(rid, ha_row(token=""))
        kept = (await reg.get_with_secrets(rid))["config"]["token"]
        await reg.update(rid, ha_row(token="new-token-value"))
        replaced = (await reg.get_with_secrets(rid))["config"]["token"]
        await reg.update(rid, ha_row(token=None))
        cleared = (await reg.get_with_secrets(rid))["config"]["token"]
        flag = (await reg.get(rid))["secrets_set"]
        await reg.db.close()
        return kept, replaced, cleared, flag
    kept, replaced, cleared, flag = run(go())
    assert (kept, replaced, cleared) == (TOKEN, "new-token-value", "")
    assert flag == {"token": False}


def test_plaintext_rows_from_before_are_encrypted_on_start(tmp_path):
    async def go():
        reg = await _reg(tmp_path)
        await reg.db.execute(
            "INSERT INTO integrations (id, name, type, protocol, config, mappings, enabled, created_at)"
            " VALUES ('old', 'old', 'battery', 'mqtt', ?, '[]', 0, 0)",
            (json.dumps({"host": "192.0.2.5", "password": "mqtt-secret"}),))
        await reg.db.commit()
        await reg.start_all()
        stored = await _stored_config(reg, "old")
        inner = await reg.get_with_secrets("old")
        await reg.db.close()
        return stored, inner
    stored, inner = run(go())
    assert stored["password"].startswith("enc:v1:") and stored["host"] == "192.0.2.5"
    assert inner["config"]["password"] == "mqtt-secret"


def test_wrong_machine_key_loses_secret_without_crashing(tmp_path):
    async def go():
        reg = await _reg(tmp_path)
        row = await reg.create(ha_row())
        other = IntegrationRegistry(reg.db, SampleBus(), b"x" * 32)
        inner = await other.get_with_secrets(row["id"])
        await reg.db.close()
        return inner
    assert run(go())["config"]["token"] == ""


def test_poll_errors_never_show_the_secret(tmp_path):
    async def go():
        reg = await _reg(tmp_path)
        row = await reg.create(ha_row())

        class P:
            status = "offline"
            last_error = f"401 for {HA_URL}?token={TOKEN}"
        reg._pollers[row["id"]] = P()
        out = await reg.get(row["id"])
        await reg.db.close()
        return out
    out = run(go())
    assert TOKEN not in out["last_error"] and "***" in out["last_error"]


def test_scrub_own_uses_poller_attributes():
    class Poller:
        password = "hunter22"
    assert secret_store.scrub_own(Poller(), "auth failed for hunter22") == "auth failed for ***"


# ── HA entity browser: saved token only for the saved URL ───────────────────

@pytest.fixture
def api(tmp_path, monkeypatch):
    from energipays_bridge.api import integrations
    app = FastAPI()
    app.include_router(integrations.router)
    sent = {}

    async def fake_get(self, url, headers=None, **k):
        sent["url"], sent["auth"] = url, (headers or {}).get("Authorization")
        return httpx.Response(200, json=[], request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    with TestClient(app) as client:
        # Open the DB on the client's own event loop
        reg = client.portal.call(_reg, tmp_path)
        row = client.portal.call(reg.create, ha_row())
        app.state.integration_registry = reg
        yield client, row["id"], sent
        client.portal.call(reg.db.close)


def test_entity_browser_uses_saved_token_for_saved_url(api):
    client, rid, sent = api
    r = client.post("/api/integrations/ha-entities", json={"url": HA_URL, "integration_id": rid})
    assert r.status_code == 200 and sent["auth"] == f"Bearer {TOKEN}"


def test_entity_browser_refuses_saved_token_for_another_url(api):
    client, rid, sent = api
    r = client.post("/api/integrations/ha-entities",
                    json={"url": "http://198.51.100.9:8123", "integration_id": rid})
    assert r.status_code == 400 and not sent
