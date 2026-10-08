"""Validate analyzer activity replies before admitting another upload."""

import httpx
import pytest
import respx

from app.main import ANALYZER_URL, _backend_state


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"busy": 0, "operations": []},
        {"busy": False},
        {"busy": False, "operations": [{"job_id": "running"}]},
    ],
)
@respx.mock
async def test_invalid_backend_state_fails_closed(payload):
    respx.get(f"{ANALYZER_URL}/processing").mock(
        return_value=httpx.Response(200, json=payload)
    )
    with pytest.raises((ValueError, RuntimeError)):
        await _backend_state()


@respx.mock
async def test_valid_idle_backend_state():
    payload = {"instance_id": "test", "busy": False, "operations": []}
    respx.get(f"{ANALYZER_URL}/processing").mock(
        return_value=httpx.Response(200, json=payload)
    )
    assert await _backend_state() == payload
