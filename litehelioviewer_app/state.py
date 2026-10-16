from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import CACHE_DIR


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
    # Which sidebar mode the layer belongs to: "single" (single-frame) or
    # "series" (time-series member). The two modes render independent stacks.
    mode: str = "single"


def _norm_mode(mode: Any) -> str:
    return "series" if mode == "series" else "single"


class LayerStore:
    def __init__(self):
        self.layers: list[Layer] = []

    def add_image(self, name: str, image_path: Path, opacity: float = 1.0, metadata: dict[str, Any] | None = None, mode: str = "single") -> Layer:
        try:
            rel = image_path.relative_to(CACHE_DIR).as_posix()
        except ValueError:
            rel = image_path.name
        layer = Layer(
            id=str(uuid.uuid4()),
            name=name,
            kind="image",
            image_url=f"/data/cache/{rel}",
            opacity=max(0.0, min(1.0, opacity)),
            metadata=metadata or {},
            mode=_norm_mode(mode),
        )
        self.layers.append(layer)
        return layer

    def add_pfss(self, metadata: dict[str, Any] | None = None, mode: str = "single") -> Layer:
        existing = next((layer for layer in self.layers if layer.kind == "pfss" and layer.mode == _norm_mode(mode)), None)
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
            mode=_norm_mode(mode),
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

    def clear(self, mode: str | None = None) -> None:
        if mode is None:
            self.layers.clear()
            return
        norm = _norm_mode(mode)
        self.layers = [layer for layer in self.layers if layer.mode != norm]


store = LayerStore()
