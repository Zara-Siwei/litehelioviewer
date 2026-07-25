from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Layer:
    id: str
    name: str
    kind: str
    image_url: str | None = None
    opacity: float = 1.0
    visible: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class LayerStore:
    def __init__(self):
        self.layers: list[Layer] = []

    def add_image(self, name: str, image_path: Path, opacity: float = 1.0, metadata: dict[str, Any] | None = None) -> Layer:
        layer = Layer(
            id=str(uuid.uuid4()),
            name=name,
            kind="image",
            image_url=f"/data/cache/{image_path.name}",
            opacity=max(0.0, min(1.0, opacity)),
            metadata=metadata or {},
        )
        self.layers.append(layer)
        return layer

    def add_pfss(self, metadata: dict[str, Any] | None = None) -> Layer:
        existing = next((layer for layer in self.layers if layer.kind == "pfss"), None)
        if existing:
            existing.visible = True
            if metadata:
                existing.metadata = metadata
                nearest = metadata.get("nearest_date") or metadata.get("date_obs")
                existing.name = f"PFSS Model @ {nearest}" if nearest else "PFSS Model"
            return existing
        nearest = metadata.get("nearest_date") if metadata else None
        layer = Layer(
            id=str(uuid.uuid4()),
            name=f"PFSS Model @ {nearest}" if nearest else "PFSS Model",
            kind="pfss",
            opacity=0.9,
            metadata=metadata or {},
        )
        self.layers.append(layer)
        return layer

    def to_list(self) -> list[dict[str, Any]]:
        return [layer.__dict__ for layer in self.layers]

    def get(self, layer_id: str) -> Layer:
        for layer in self.layers:
            if layer.id == layer_id:
                return layer
        raise KeyError(layer_id)

    def remove(self, layer_id: str) -> None:
        self.layers = [layer for layer in self.layers if layer.id != layer_id]

    def clear(self) -> None:
        self.layers.clear()


store = LayerStore()
