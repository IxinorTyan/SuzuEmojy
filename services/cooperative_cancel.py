"""Cancellation marker shared by short-lived helpers that must finish writes safely."""

from pathlib import Path
from threading import Event


class CancellationMarker(Event):
    def __init__(self, marker):
        super().__init__()
        self.marker = Path(marker) if marker else None

    def is_set(self):
        if not super().is_set() and self.marker is not None:
            try:
                if self.marker.stat().st_size:
                    self.set()
            except OSError:
                pass
        return super().is_set()
