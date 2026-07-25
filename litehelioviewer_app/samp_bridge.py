from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class SampBridge:
    """Small optional SAMP receiver for FITS and JHV load-request messages."""

    def __init__(self, on_fits, on_request, name: str = "litehelioviewer"):
        self.on_fits = on_fits
        self.on_request = on_request
        self.name = name
        self.client = None
        self.thread: threading.Thread | None = None
        self.running = False
        self.last_error = ""

    def start(self) -> bool:
        if self.running:
            return True
        try:
            from astropy.samp import SAMPIntegratedClient

            self.client = SAMPIntegratedClient(name=self.name, description="LiteHelioviewer SAMP bridge")
            self.client.connect()
            self.client.bind_receive_notification("image.load.fits", self._receive_fits)
            self.client.bind_receive_notification("jhv.load.request", self._receive_request)
            self.running = True
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.running = False
            return False

    def stop(self) -> None:
        if self.client:
            try:
                self.client.disconnect()
            except Exception:
                pass
        self.running = False

    def _receive_fits(self, private_key, sender_id, mtype, params, extra):
        url = params.get("url", "")
        if url:
            self.on_fits(Path(str(url)))

    def _receive_request(self, private_key, sender_id, mtype, params, extra):
        value = params.get("value") or ""
        if value:
            self.on_request(json.loads(str(value)))


bridge: SampBridge | None = None
