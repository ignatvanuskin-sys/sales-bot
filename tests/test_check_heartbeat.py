import json
from datetime import datetime, timezone

import pytest

from scripts import check_heartbeat as heartbeat


@pytest.mark.asyncio
async def test_check_heartbeat_accepts_json_payload(tmp_path, monkeypatch):
    heartbeat_file = tmp_path / "heartbeat"
    heartbeat_file.write_text(
        json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "workers_alive": 2}),
        encoding="utf-8",
    )
    monkeypatch.setattr(heartbeat.settings, "heartbeat_file", str(heartbeat_file))

    assert await heartbeat.check_heartbeat(stale_minutes=30) is True


@pytest.mark.asyncio
async def test_check_heartbeat_keeps_legacy_timestamp_support(tmp_path, monkeypatch):
    heartbeat_file = tmp_path / "heartbeat"
    heartbeat_file.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    monkeypatch.setattr(heartbeat.settings, "heartbeat_file", str(heartbeat_file))

    assert await heartbeat.check_heartbeat(stale_minutes=30) is True


@pytest.mark.asyncio
async def test_check_heartbeat_reports_invalid_json_payload(tmp_path, monkeypatch):
    heartbeat_file = tmp_path / "heartbeat"
    heartbeat_file.write_text(json.dumps({"workers_alive": 0}), encoding="utf-8")
    monkeypatch.setattr(heartbeat.settings, "heartbeat_file", str(heartbeat_file))
    sent = []

    async def fake_send(*args):
        sent.append(args)

    monkeypatch.setattr(heartbeat, "send_bot_message", fake_send)

    assert await heartbeat.check_heartbeat(stale_minutes=30) is False
    assert sent
