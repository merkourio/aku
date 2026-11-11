"""PipeWire graph snapshot (via pw-dump): devices, app streams, ports and links."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field

OWN_PREFIX = "aku-"


@dataclass
class Port:
    id: int
    node_id: int
    direction: str  # "input" | "output"
    channel: str | None
    monitor: bool


@dataclass
class Node:
    id: int
    name: str
    description: str
    media_class: str
    app_name: str | None
    binary: str | None
    media_name: str | None
    state: str | None
    ports: list[Port] = field(default_factory=list)

    @property
    def app_key(self) -> str:
        return self.binary or self.app_name or self.name

    @property
    def app_label(self) -> str:
        if self.app_name and self.binary and self.app_name.lower() != self.binary.lower():
            return f"{self.app_name} ({self.binary})"
        return self.app_name or self.binary or self.name


@dataclass
class Graph:
    nodes: dict[int, Node]
    links: set[tuple[int, int]]  # (output port id, input port id)
    default_sink: str | None
    default_source: str | None

    def by_name(self, name: str) -> Node | None:
        for n in self.nodes.values():
            if n.name == name:
                return n
        return None

    def app_streams(self) -> list[Node]:
        return [
            n
            for n in self.nodes.values()
            if n.media_class == "Stream/Output/Audio" and not n.name.startswith(OWN_PREFIX)
        ]


def parse(dump: list[dict]) -> Graph:
    nodes: dict[int, Node] = {}
    ports: list[Port] = []
    links: set[tuple[int, int]] = set()
    default_sink = default_source = None
    for obj in dump:
        kind = obj.get("type", "")
        info = obj.get("info") or {}
        props = info.get("props") or {}
        if kind.endswith(":Node"):
            nodes[obj["id"]] = Node(
                id=obj["id"],
                name=props.get("node.name") or str(obj["id"]),
                description=props.get("node.description") or props.get("node.nick") or props.get("node.name") or "",
                media_class=props.get("media.class") or "",
                app_name=props.get("application.name"),
                binary=props.get("application.process.binary"),
                media_name=props.get("media.name"),
                state=info.get("state"),
            )
        elif kind.endswith(":Port"):
            ports.append(
                Port(
                    id=obj["id"],
                    node_id=props.get("node.id", -1),
                    direction=info.get("direction", ""),
                    channel=props.get("audio.channel"),
                    monitor=bool(props.get("port.monitor")),
                )
            )
        elif kind.endswith(":Link"):
            links.add((info.get("output-port-id"), info.get("input-port-id")))
        elif kind.endswith(":Metadata"):
            for m in obj.get("metadata") or []:
                value = m.get("value")
                name = value.get("name") if isinstance(value, dict) else None
                if m.get("key") == "default.audio.sink":
                    default_sink = name
                elif m.get("key") == "default.audio.source":
                    default_source = name
    for p in ports:
        if p.node_id in nodes:
            nodes[p.node_id].ports.append(p)
    return Graph(nodes, links, default_sink, default_source)


def snapshot() -> Graph:
    out = subprocess.run(["pw-dump"], capture_output=True, timeout=10, check=True).stdout
    return parse(json.loads(out))


def list_devices(graph: Graph | None = None) -> dict:
    g = graph or snapshot()
    inputs, outputs = [], []
    for n in g.nodes.values():
        if n.name.startswith(OWN_PREFIX):
            continue
        if n.media_class == "Audio/Source":
            inputs.append({"name": n.name, "description": n.description, "default": n.name == g.default_source})
        elif n.media_class == "Audio/Sink":
            outputs.append({"name": n.name, "description": n.description, "default": n.name == g.default_sink})
    apps: dict[str, dict] = {}
    for n in g.app_streams():
        entry = apps.setdefault(n.app_key, {"key": n.app_key, "label": n.app_label, "streams": []})
        entry["streams"].append({"media": n.media_name or "", "state": n.state})
    key = lambda d: (not d["default"], d["description"].lower())
    return {
        "inputs": sorted(inputs, key=key),
        "outputs": sorted(outputs, key=key),
        "apps": sorted(apps.values(), key=lambda a: a["label"].lower()),
    }


def wanted_links(g: Graph, capture_node: str, app_keys: list[str]) -> list[tuple[int, int]]:
    """Links still missing between the chosen apps' output ports and our capture node."""
    cap = g.by_name(capture_node)
    if cap is None:
        return []
    ins = {p.channel: p.id for p in cap.ports if p.direction == "input"}
    if not ins:
        return []
    left = ins.get("FL") or next(iter(ins.values()))
    right = ins.get("FR") or left
    keys = set(app_keys)
    missing = []
    for n in g.app_streams():
        if n.app_key not in keys:
            continue
        outs = [p for p in n.ports if p.direction == "output" and not p.monitor]
        stereo = [p for p in outs if p.channel in ("FL", "FR")]
        for p in outs:
            if p.channel == "FL":
                targets = [left]
            elif p.channel == "FR":
                targets = [right]
            elif stereo:
                continue  # surround extras: front pair is enough
            else:
                targets = [left, right]  # mono or unknown layout
            for t in dict.fromkeys(targets):
                if (p.id, t) not in g.links:
                    missing.append((p.id, t))
    return missing


def link(out_port: int, in_port: int) -> bool:
    r = subprocess.run(["pw-link", str(out_port), str(in_port)], capture_output=True, timeout=5)
    return r.returncode == 0
