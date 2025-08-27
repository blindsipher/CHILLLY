import json
from typing import Any

def safe_load(stream: Any) -> Any:
    try:
        data = stream.read() if hasattr(stream, "read") else stream
        if not data:
            return {}
        return json.loads(data)
    except Exception:
        return {}
