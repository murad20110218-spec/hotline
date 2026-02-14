#!/usr/bin/env python3
"""
HOTLINE PYGAME — Multiplayer Server
Run in GitHub Codespaces
"""

import asyncio
import json
import time
import os
import math
import random
from typing import Dict

try:
    import websockets
except ImportError:
    os.system("pip install websockets")
    import websockets

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 8765))
TICK_RATE = 20
MAX_PLAYERS = 8


class Player:
    _counter = 0

    def __init__(self, name, x, y):
        Player._counter += 1
        self.id = f"p{Player._counter}"
        self.name = name
        self.x = x
        self.y = y
        self.angle = 0.0
        self.alive = True
        self.kills = 0
        self.deaths = 0
        self.health = 100
        self.weapon = "pistol"
        self.last_update = time.time()

    def to_dict(self):
        return {
            "id": self.id, "name": self.name,
            "x": round(self.x, 1), "y": round(self.y, 1),
            "angle": round(self.angle, 3), "alive": self.alive,
            "kills": self.kills, "deaths": self.deaths,
            "health": self.health, "weapon": self.weapon,
        }


class BulletServer:
    _counter = 0

    def __init__(self, owner_id, x, y, dx, dy, speed=700):
        BulletServer._counter += 1
        self.id = BulletServer._counter
        self.owner_id = owner_id
        self.x = x
        self.y = y
        self.dx = dx
        self.dy = dy
        self.speed = speed
        self.alive = True
        self.life = 3.0

    def update(self, dt):
        self.life -= dt
        if self.life <= 0:
            self.alive = False
            return
        self.x += self.dx * self.speed * dt
        self.y += self.dy * self.speed * dt


class Room:
    def __init__(self, rid, name):
        self.id = rid
        self.name = name
        self.players: Dict[str, Player] = {}
        self.bullets: list = []
        self.chat: list = []
        self.spawns = [
            (100, 100), (900, 100), (100, 700), (900, 700),
            (500, 100), (500, 700), (100, 400), (900, 400),
        ]

    def get_spawn(self):
        used = set()
        for p in self.players.values():
            used.add((int(p.x // 100) * 100, int(p.y // 100) * 100))
        for sp in self.spawns:
            if sp not in used:
                return sp
        return (random.randint(100, 900), random.randint(100, 700))

    def add_player(self, name):
        sx, sy = self.get_spawn()
        p = Player(name, sx, sy)
        self.players[p.id] = p
        return p

    def remove_player(self, pid):
        self.players.pop(pid, None)

    def update(self, dt):
        for b in self.bullets:
            b.update(dt)

        for b in self.bullets:
            if not b.alive:
                continue
            for pid, p in self.players.items():
                if pid == b.owner_id or not p.alive:
                    continue
                if math.hypot(b.x - p.x, b.y - p.y) < 14:
                    b.alive = False
                    p.health -= 34
                    if p.health <= 0:
                        p.alive = False
                        p.deaths += 1
                        if b.owner_id in self.players:
                            self.players[b.owner_id].kills += 1

        self.bullets = [b for b in self.bullets if b.alive]

        # Respawn
        now = time.time()
        for p in self.players.values():
            if not p.alive and now - p.last_update > 3.0:
                sx, sy = self.get_spawn()
                p.x, p.y = sx, sy
                p.alive = True
                p.health = 100

    def state(self):
        return {
            "type": "state",
            "players": {pid: p.to_dict() for pid, p in self.players.items()},
            "bullets": [{"id": b.id, "x": round(b.x, 1), "y": round(b.y, 1),
                         "owner": b.owner_id} for b in self.bullets[-50:]],
        }


class Server:
    def __init__(self):
        self.rooms = {"lobby": Room("lobby", "Main Lobby")}
        self.clients: Dict[str, websockets.WebSocketServerProtocol] = {}
        self.player_rooms: Dict[str, str] = {}

    def get_room(self, rid):
        if rid not in self.rooms:
            self.rooms[rid] = Room(rid, rid)
        return self.rooms[rid]

    async def broadcast(self, room_id, msg):
        room = self.rooms.get(room_id)
        if not room:
            return
        data = json.dumps(msg)
        dead = []
        for pid in room.players:
            ws = self.clients.get(pid)
            if ws:
                try:
                    await ws.send(data)
                except Exception:
                    dead.append(pid)
        for pid in dead:
            await self.disconnect(pid)

    async def send(self, pid, msg):
        ws = self.clients.get(pid)
        if ws:
            try:
                await ws.send(json.dumps(msg))
            except Exception:
                pass

    async def disconnect(self, pid):
        rid = self.player_rooms.pop(pid, None)
        if rid and rid in self.rooms:
            self.rooms[rid].remove_player(pid)
            await self.broadcast(rid, {"type": "player_left", "id": pid})
        self.clients.pop(pid, None)
        print(f"[-] {pid} left ({len(self.clients)} online)")

    async def handle(self, ws, path=None):
        pid = None
        try:
            # Welcome
            temp_id = f"tmp_{id(ws)}"
            await ws.send(json.dumps({
                "type": "welcome",
                "server": "HOTLINE PYGAME SERVER",
                "max_players": MAX_PLAYERS,
            }))

            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                t = msg.get("type")

                if t == "join":
                    name = msg.get("name", "Anon")[:20]
                    rid = msg.get("room", "lobby")
                    room = self.get_room(rid)

                    if len(room.players) >= MAX_PLAYERS:
                        await ws.send(json.dumps({"type": "error", "msg": "Full"}))
                        continue

                    player = room.add_player(name)
                    pid = player.id
                    self.clients[pid] = ws
                    self.player_rooms[pid] = rid

                    await self.send(pid, {
                        "type": "joined", "id": pid, "room": rid,
                        "spawn": [player.x, player.y],
                    })
                    await self.broadcast(rid, {
                        "type": "player_joined", "id": pid, "name": name,
                    })
                    print(f"[+] {name} ({pid}) -> {rid} ({len(self.clients)} online)")

                elif t == "input" and pid:
                    rid = self.player_rooms.get(pid)
                    if rid and rid in self.rooms:
                        p = self.rooms[rid].players.get(pid)
                        if p:
                            p.x = msg.get("x", p.x)
                            p.y = msg.get("y", p.y)
                            p.angle = msg.get("angle", p.angle)
                            p.weapon = msg.get("weapon", p.weapon)
                            p.last_update = time.time()

                elif t == "shoot" and pid:
                    rid = self.player_rooms.get(pid)
                    if rid and rid in self.rooms:
                        room = self.rooms[rid]
                        p = room.players.get(pid)
                        if p and p.alive:
                            b = BulletServer(
                                pid, msg.get("x", p.x), msg.get("y", p.y),
                                msg.get("dx", 0), msg.get("dy", 0),
                                msg.get("speed", 700),
                            )
                            room.bullets.append(b)
                            await self.broadcast(rid, {
                                "type": "bullet", "id": b.id, "owner": pid,
                                "x": round(b.x, 1), "y": round(b.y, 1),
                                "dx": round(b.dx, 3), "dy": round(b.dy, 3),
                                "speed": b.speed,
                            })

                elif t == "chat" and pid:
                    rid = self.player_rooms.get(pid)
                    if rid and rid in self.rooms:
                        p = self.rooms[rid].players.get(pid)
                        if p:
                            await self.broadcast(rid, {
                                "type": "chat", "from": p.name,
                                "text": msg.get("text", "")[:100],
                            })

                elif t == "ping":
                    await ws.send(json.dumps({
                        "type": "pong", "time": msg.get("time", 0),
                    }))

                elif t == "rooms":
                    await ws.send(json.dumps({
                        "type": "rooms_list",
                        "rooms": [{"id": r.id, "name": r.name,
                                    "players": len(r.players), "max": MAX_PLAYERS}
                                   for r in self.rooms.values()],
                    }))

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if pid:
                await self.disconnect(pid)

    async def tick_loop(self):
        while True:
            dt = 1.0 / TICK_RATE
            for rid, room in self.rooms.items():
                if room.players:
                    room.update(dt)
                    await self.broadcast(rid, room.state())
            await asyncio.sleep(dt)

    async def run(self):
        print(f"[SERVER] ws://{HOST}:{PORT}")
        print(f"[SERVER] Tick: {TICK_RATE}/s, Max: {MAX_PLAYERS}")
        srv = await websockets.serve(self.handle, HOST, PORT,
                                      ping_interval=20, ping_timeout=60)
        tick = asyncio.create_task(self.tick_loop())
        print("[SERVER] Ready!")
        await srv.wait_closed()
        tick.cancel()


if __name__ == "__main__":
    asyncio.run(Server().run())
