#!/usr/bin/env python3
"""Prometheus exporter for per-player Minecraft stats and online players.

Reads <data>/<level>/players/stats/*.json (written at autosave) and asks RCON
`minecraft:list` (vanilla; Essentials overrides plain `list`). Stdlib only.
"""

import glob
import json
import os
import socket
import struct
from http.server import BaseHTTPRequestHandler, HTTPServer

CUSTOM_STATS = (
    "play_time",
    "deaths",
    "mob_kills",
    "player_kills",
    "damage_dealt",
    "damage_taken",
    "jump",
    "fish_caught",
    "traded_with_villager",
    "sleep_in_bed",
)
TOTAL_STATS = ("mined", "crafted", "used")


def _read(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("RCON connection closed")
        buf += chunk
    return buf


def rcon(command, password, host="127.0.0.1", port=25575):
    with socket.create_connection((host, port), timeout=3) as s:

        def send(req_id, kind, body):
            payload = struct.pack("<ii", req_id, kind) + body.encode() + b"\x00\x00"
            s.sendall(struct.pack("<i", len(payload)) + payload)

        def recv():
            (length,) = struct.unpack("<i", _read(s, 4))
            data = _read(s, length)
            return struct.unpack("<i", data[:4])[0], data[8:-2].decode(
                "utf-8", "replace"
            )

        send(1, 3, password)
        if recv()[0] == -1:
            raise PermissionError("RCON auth failed")
        send(2, 2, command)
        return recv()[1]


def parse_online(text):
    _, sep, names = text.partition(":")
    return {n.strip() for n in names.split(",") if n.strip()} if sep else set()


def _names(data_dir):
    try:
        with open(os.path.join(data_dir, "usercache.json")) as f:
            return {e["uuid"]: e["name"] for e in json.load(f)}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def render(data_dir, level, online):
    names = _names(data_dir)
    lines = [
        "# TYPE mc_player_stat gauge",
        "# TYPE mc_player_total gauge",
        "# TYPE mc_player_distance_cm gauge",
    ]
    players = set()
    for path in sorted(
        glob.glob(os.path.join(data_dir, level, "players", "stats", "*.json"))
    ):
        uuid = os.path.basename(path)[:-5]
        try:
            with open(path) as f:
                stats = json.load(f)["stats"]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue  # mid-write during autosave
        player = names.get(uuid, uuid)
        players.add(player)
        custom = {
            k.removeprefix("minecraft:"): v
            for k, v in stats.get("minecraft:custom", {}).items()
        }
        for stat in CUSTOM_STATS:
            if stat in custom:
                lines.append(
                    f'mc_player_stat{{player="{player}",stat="{stat}"}} {custom[stat]}'
                )
        for stat in TOTAL_STATS:
            total = sum(stats.get(f"minecraft:{stat}", {}).values())
            lines.append(f'mc_player_total{{player="{player}",stat="{stat}"}} {total}')
        distance = sum(v for k, v in custom.items() if k.endswith("_one_cm"))
        lines.append(f'mc_player_distance_cm{{player="{player}"}} {distance}')
    lines.append("# TYPE mc_rcon_up gauge")
    lines.append(f"mc_rcon_up {0 if online is None else 1}")
    if online is not None:
        lines.append("# TYPE mc_player_online gauge")
        for player in sorted(players | online):
            lines.append(
                f'mc_player_online{{player="{player}"}} {int(player in online)}'
            )
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        try:
            online = parse_online(
                rcon(
                    "minecraft:list",
                    os.environ["RCON_PASSWORD"],
                    port=int(os.environ.get("RCON_PORT", "25575")),
                )
            )
        except (OSError, PermissionError):
            online = None  # server starting/stopping
        body = render(
            os.environ.get("MC_DATA_DIR", "/data"),
            os.environ.get("LEVEL", "world"),
            online,
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(
        ("", int(os.environ.get("LISTEN_PORT", "9941"))), Handler
    ).serve_forever()
