"""Publish JSON atomically, including Windows sharing-violation retries."""
import json
import os
import time
import uuid


def write_json(target, data, indent=4):
    temporary = f"{target}.{uuid.uuid4()}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=indent, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(5):
            try:
                os.replace(temporary, target)
                break
            except OSError as error:
                if getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 4:
                    raise
                time.sleep(0.05 * (2 ** attempt))
    finally:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except OSError:
                pass
