"""Settings the owner changes on /admin, kept in a JSON file next to the database (or $DEALS_SETTINGS).

  talabat_hubs   the talabat mart hubs to follow: [{"id": 776372, "aid": 8032, "name": "El Obour Industrial Area"}]
                 Each hub is tracked separately (its own prices and stock). Default: Maadi Old.
"""

import json
import os
import tempfile

DEFAULT_HUBS = [{"id": 619848, "aid": 9838, "name": "Maadi Old"}]


def path_for(db_path: str) -> str:
    return os.environ.get("DEALS_SETTINGS") or os.path.join(os.path.dirname(os.path.abspath(db_path)), "settings.json")


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    hubs = [h for h in data.get("talabat_hubs") or [] if isinstance(h, dict) and str(h.get("id", "")).isdigit()]
    data["talabat_hubs"] = [{"id": int(h["id"]), "aid": int(h.get("aid") or 9838), "name": str(h.get("name") or h["id"])}
                            for h in hubs] or DEFAULT_HUBS
    return data


def save(path: str, data: dict):
    """Written whole and swapped in, so a reader never sees half a file."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".settings-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def mtime(path: str):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None
