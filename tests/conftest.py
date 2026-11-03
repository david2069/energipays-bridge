import asyncio
import types

import pytest

DEVICE = "00000000-0000-4000-8000-000000000001"


def run(coro):
    return asyncio.run(coro)


class FakeClient:
    """Stands in for energipays-client; records calls, answers from `results`."""

    def __init__(self, **results):
        self.calls = []
        self.results = results

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self.results.get(name, {})
        return call


@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture
def app_factory():
    def make(client, read_only=False):
        from fastapi import FastAPI
        from energipays_bridge.api import cloud_stats, devices, metrics
        app = FastAPI()
        for r in (devices.router, cloud_stats.router, metrics.router):
            app.include_router(r)
        app.state.client = client
        app.state.device_id = DEVICE
        app.state.data_server = "https://data-au-1.energipays.com"
        app.state.settings = types.SimpleNamespace(read_only=read_only, timezone="Australia/Perth",
                                                   poll_interval=60)
        app.state.poller = None
        app.state.mqtt_publisher = None
        return app
    return make
