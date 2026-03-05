"""
Простое JSON-сховище для збереження даних користувачів між перезапусками.
Файл зберігається поруч зі скриптом.
"""
import json
import os
from typing import Optional

STORAGE_FILE = os.path.join(os.path.dirname(__file__), "users.json")


def _load() -> dict:
    if not os.path.exists(STORAGE_FILE):
        return {}
    with open(STORAGE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict) -> None:
    with open(STORAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_user(chat_id: int, nie: str, name: str, active: bool = True) -> None:
    data = _load()
    data[str(chat_id)] = {"nie": nie, "name": name, "active": active}
    _save(data)


def get_user(chat_id: int) -> Optional[dict]:
    data = _load()
    return data.get(str(chat_id))


def set_active(chat_id: int, active: bool) -> None:
    data = _load()
    if str(chat_id) in data:
        data[str(chat_id)]["active"] = active
        _save(data)


def get_all_active() -> list[dict]:
    """Повертає список активних користувачів з їх chat_id."""
    data = _load()
    result = []
    for chat_id, user in data.items():
        if user.get("active"):
            result.append({"chat_id": int(chat_id), **user})
    return result
