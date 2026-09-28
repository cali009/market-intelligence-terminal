"""
Asynchronous Low-Latency WebSocket & Streaming Server (Phase 25.2)
US + Canada Market Intelligence Platform

Provides sub-second bidirectional WebSocket broadcasting for tick-level order flow:
1. Real-Time WebSocket Endpoint: ws://0.0.0.0:8001/ws/microstructure
2. Client Subscription Commands: subscribe, unsubscribe, ping/pong heartbeats.
3. Multi-Channel Multiplexing: channels for ticks, book depth (DOM), and microstructure metrics.
4. Client Anti-Fatigue & Throttling: Configurable broadcast frame rate (100ms–500ms bursts).
5. Robust Lifecycle & Exception Handling: Auto-cleanup of disconnected clients and subscription sets.
6. Impersonal Decision-Support Research Compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion).
"""

import asyncio
import json
import logging
from typing import Dict, Set, Any, Optional
import websockets
try:
    from websockets.asyncio.server import ServerConnection as WebSocketConn
except ImportError:
    from websockets.server import WebSocketServerProtocol as WebSocketConn

from src.compliance.linter import linter
from src.engine.microstructure import microstructure_engine, DISCLAIMERS

logger = logging.getLogger(__name__)


class MicrostructureStreamingServer:
    """
    High-throughput asynchronous WebSocket server managing client connections,
    multi-symbol subscriptions, and tick/order book event broadcasting.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 8001, broadcast_interval: float = 0.5):
        self.host = host
        self.port = port
        self.broadcast_interval = broadcast_interval
        self.active_clients: Set[WebSocketConn] = set()
        self.client_subscriptions: Dict[WebSocketConn, Dict[str, Set[str]]] = {}
        self.server = None
        self._is_running = False
        self._broadcast_task: Optional[asyncio.Task] = None

    async def register_client(self, websocket: WebSocketConn):
        """Registers a newly connected client with default subscriptions."""
        self.active_clients.add(websocket)
        self.client_subscriptions[websocket] = {
            "symbols": {"SPY", "XIU"},  # Default dual-market leaders
            "channels": {"ticks", "book", "metrics"},
        }
        # Send initial connection acknowledgment & disclaimers
        ack = {
            "type": "CONNECTION_ACK",
            "protocol": "MICROSTRUCTURE_WS_V1",
            "active_symbols": list(self.client_subscriptions[websocket]["symbols"]),
            "active_channels": list(self.client_subscriptions[websocket]["channels"]),
            "disclaimer": DISCLAIMERS[0],
        }
        await websocket.send(json.dumps(ack))

    async def unregister_client(self, websocket: WebSocketConn):
        """Cleans up disconnected client and subscription state."""
        self.active_clients.discard(websocket)
        self.client_subscriptions.pop(websocket, None)

    async def handle_client_message(self, websocket: WebSocketConn, message_str: str):
        """
        Processes client-side commands: subscribe, unsubscribe, ping.
        """
        try:
            msg = json.loads(message_str)
        except json.JSONDecodeError:
            await websocket.send(json.dumps({"type": "ERROR", "message": "Invalid JSON format."}))
            return

        action = msg.get("action", "").lower()
        sub_state = self.client_subscriptions.get(websocket)
        if not sub_state:
            return

        if action == "subscribe":
            symbols = [s.upper() for s in msg.get("symbols", [])]
            channels = [c.lower() for c in msg.get("channels", [])]
            if symbols:
                sub_state["symbols"].update(symbols)
            if channels:
                sub_state["channels"].update(channels)

            resp = {
                "type": "SUBSCRIBE_ACK",
                "symbols": list(sub_state["symbols"]),
                "channels": list(sub_state["channels"]),
            }
            await websocket.send(json.dumps(resp))

        elif action == "unsubscribe":
            symbols = [s.upper() for s in msg.get("symbols", [])]
            for s in symbols:
                sub_state["symbols"].discard(s)
            resp = {
                "type": "UNSUBSCRIBE_ACK",
                "symbols": list(sub_state["symbols"]),
            }
            await websocket.send(json.dumps(resp))

        elif action == "ping":
            resp = {
                "type": "PONG",
                "timestamp": msg.get("timestamp", ""),
            }
            await websocket.send(json.dumps(resp))

        else:
            await websocket.send(json.dumps({"type": "ERROR", "message": f"Unknown action: {action}"}))

    async def connection_handler(self, websocket: WebSocketConn):
        """Coroutines handling incoming WebSocket messages from a client."""
        await self.register_client(websocket)
        try:
            async for message in websocket:
                await self.handle_client_message(websocket, message)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await self.unregister_client(websocket)

    async def broadcast_loop(self):
        """
        Background periodic loop broadcasting live tick updates and book state
        to all subscribed clients.
        """
        while self._is_running:
            try:
                await asyncio.sleep(self.broadcast_interval)
                if not self.active_clients:
                    continue

                # Collect all symbols currently needed by any client
                needed_symbols: Set[str] = set()
                for sub in self.client_subscriptions.values():
                    needed_symbols.update(sub.get("symbols", set()))

                if not needed_symbols:
                    continue

                # Generate live ticks for active symbols
                symbol_payloads = {}
                for sym in needed_symbols:
                    tick = microstructure_engine.generate_live_tick(sym)
                    dossier = microstructure_engine._cache.get(sym)
                    symbol_payloads[sym] = {
                        "symbol": sym,
                        "tick": tick.to_dict(),
                        "order_book": dossier.order_book.to_dict() if dossier else None,
                        "metrics": dossier.metrics.to_dict() if dossier else None,
                    }

                # Dispatch customized payload per client based on their subscriptions
                dead_clients = []
                for client in list(self.active_clients):
                    sub = self.client_subscriptions.get(client)
                    if not sub:
                        continue

                    client_syms = sub.get("symbols", set())
                    client_channels = sub.get("channels", {"ticks", "book", "metrics"})

                    # Filter payload
                    client_events = []
                    for sym in client_syms:
                        if sym in symbol_payloads:
                            data = symbol_payloads[sym]
                            item = {"symbol": sym}
                            if "ticks" in client_channels and data["tick"]:
                                item["tick"] = data["tick"]
                            if "book" in client_channels and data["order_book"]:
                                item["order_book"] = data["order_book"]
                            if "metrics" in client_channels and data["metrics"]:
                                item["metrics"] = data["metrics"]
                            client_events.append(item)

                    if client_events:
                        msg = json.dumps({"type": "MICROSTRUCTURE_BURST", "events": client_events})
                        try:
                            await client.send(msg)
                        except websockets.exceptions.ConnectionClosed:
                            dead_clients.append(client)

                for dc in dead_clients:
                    await self.unregister_client(dc)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in broadcast loop: {e}")
                await asyncio.sleep(1.0)

    async def start(self):
        """Starts the WebSocket server and background broadcaster."""
        self._is_running = True
        self.server = await websockets.serve(self.connection_handler, self.host, self.port)
        self._broadcast_task = asyncio.create_task(self.broadcast_loop())
        logger.info(f"Microstructure WebSocket Server listening on ws://{self.host}:{self.port}")

    async def stop(self):
        """Stops the WebSocket server and cancels the broadcast task."""
        self._is_running = False
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass

        if self.server:
            self.server.close()
            await self.server.wait_closed()

        for client in list(self.active_clients):
            await client.close()
        self.active_clients.clear()
        self.client_subscriptions.clear()


# Global singleton instance
streaming_server = MicrostructureStreamingServer()
