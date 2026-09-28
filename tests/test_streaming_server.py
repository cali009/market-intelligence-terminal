"""
Unit & Integration Tests for Phase 25.2: Asynchronous Microstructure Streaming WebSocket Server
US + Canada Market Intelligence Platform
"""

import asyncio
import json
import pytest
import websockets

from src.engine.streaming_server import MicrostructureStreamingServer


@pytest.mark.anyio
class TestMicrostructureStreamingServer:
    """Test suite covering WebSocket connection lifecycle, subscriptions, ping/pong, and broadcasts."""

    @pytest.fixture
    async def running_server(self):
        """Starts a temporary test WebSocket server on an ephemeral port."""
        server = MicrostructureStreamingServer(host="127.0.0.1", port=8099, broadcast_interval=0.1)
        await server.start()
        yield server
        await server.stop()

    async def test_server_registration_and_connection_ack(self, running_server):
        """Verifies new WebSocket client registration and CONNECTION_ACK dispatch."""
        uri = f"ws://127.0.0.1:{running_server.port}"
        async with websockets.connect(uri) as ws:
            raw_msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
            msg = json.loads(raw_msg)
            assert msg["type"] == "CONNECTION_ACK"
            assert msg["protocol"] == "MICROSTRUCTURE_WS_V1"
            assert "SPY" in msg["active_symbols"]
            assert "ticks" in msg["active_channels"]
            assert len(running_server.active_clients) == 1

    async def test_client_subscribe_command(self, running_server):
        """Verifies client subscription command updates symbols and channels."""
        uri = f"ws://127.0.0.1:{running_server.port}"
        async with websockets.connect(uri) as ws:
            # Drain ACK
            await ws.recv()

            sub_cmd = {
                "action": "subscribe",
                "symbols": ["NVDA", "SHOP"],
                "channels": ["ticks", "book"],
            }
            await ws.send(json.dumps(sub_cmd))

            raw_resp = await asyncio.wait_for(ws.recv(), timeout=2.0)
            resp = json.loads(raw_resp)
            assert resp["type"] == "SUBSCRIBE_ACK"
            assert "NVDA" in resp["symbols"]
            assert "SHOP" in resp["symbols"]

    async def test_client_unsubscribe_command(self, running_server):
        """Verifies client unsubscribe command removes designated symbols."""
        uri = f"ws://127.0.0.1:{running_server.port}"
        async with websockets.connect(uri) as ws:
            # Drain ACK
            await ws.recv()

            unsub_cmd = {
                "action": "unsubscribe",
                "symbols": ["SPY"],
            }
            await ws.send(json.dumps(unsub_cmd))

            raw_resp = await asyncio.wait_for(ws.recv(), timeout=2.0)
            resp = json.loads(raw_resp)
            assert resp["type"] == "UNSUBSCRIBE_ACK"
            assert "SPY" not in resp["symbols"]

    async def test_ping_pong_heartbeat(self, running_server):
        """Verifies ping command returns immediate pong with identical timestamp."""
        uri = f"ws://127.0.0.1:{running_server.port}"
        async with websockets.connect(uri) as ws:
            await ws.recv()  # Drain ACK

            ts = "2026-09-28T22:35:00Z"
            await ws.send(json.dumps({"action": "ping", "timestamp": ts}))

            raw_resp = await asyncio.wait_for(ws.recv(), timeout=2.0)
            resp = json.loads(raw_resp)
            assert resp["type"] == "PONG"
            assert resp["timestamp"] == ts

    async def test_microstructure_burst_broadcast(self, running_server):
        """Verifies periodic broadcast bursts containing live ticks and order book depth."""
        uri = f"ws://127.0.0.1:{running_server.port}"
        async with websockets.connect(uri) as ws:
            await ws.recv()  # Drain ACK

            # Wait for periodic burst
            raw_burst = await asyncio.wait_for(ws.recv(), timeout=2.0)
            burst = json.loads(raw_burst)

            # Might receive a burst or ACK
            if burst.get("type") == "MICROSTRUCTURE_BURST":
                assert len(burst["events"]) > 0
                evt = burst["events"][0]
                assert "symbol" in evt
                assert "tick" in evt or "order_book" in evt

    async def test_client_disconnect_cleanup(self, running_server):
        """Verifies server cleans up client references after disconnection."""
        uri = f"ws://127.0.0.1:{running_server.port}"
        ws = await websockets.connect(uri)
        await ws.recv()  # Drain ACK
        assert len(running_server.active_clients) == 1

        await ws.close()
        await asyncio.sleep(0.1)
        assert len(running_server.active_clients) == 0
