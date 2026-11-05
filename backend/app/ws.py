"""WebSocket 广播：把最新判定推给所有在线客户端（前端/采集端）。"""
from __future__ import annotations

import json
from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)

    def register(self, websocket: WebSocket) -> None:
        """连接已经 accept 过时使用（FastAPI 路由里通常如此）。"""
        self._clients.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def broadcast(self, payload: dict[str, Any]) -> int:
        """发送给所有客户端；发送失败的连接直接摘除。返回成功数。"""
        if not self._clients:
            return 0
        text = json.dumps(payload, ensure_ascii=False)
        dead: list[WebSocket] = []
        sent = 0
        for client in list(self._clients):
            try:
                await client.send_text(text)
                sent += 1
            except Exception:  # noqa: BLE001
                dead.append(client)
        for client in dead:
            self._clients.discard(client)
        return sent
