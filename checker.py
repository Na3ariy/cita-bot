"""
Головний скрипт для GitHub Actions.

Логіка:
  1. Читає users.json з репозиторію (через GitHub API)
  2. Для кожного активного користувача перевіряє сайт ICP через Playwright
  3. Якщо знайдено слоти — надсилає Telegram-повідомлення
  4. Якщо users.json не існує — чекає поки хтось напише /start боту

Окремо запускається register_bot.py для обробки Telegram /start команд.
"""
import asyncio
import base64
import json
import logging
import os
import sys
from io import BytesIO

import requests
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# ── Конфіг ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GITHUB_TOKEN   = os.environ["GITHUB_TOKEN"]
GITHUB_REPO    = os.environ["GITHUB_REPO"]   # "username/cita-bot"

PROVINCE_VALUE    = "08"  # Barcelona
PROCEDURE_KEYWORDS = ["UCRANIA", "CONFLICTO"]
USERS_FILE        = "users.json"

BASE_URL = (
    "https://icp.administracionelectronica.gob.es/icpplus/index.html"
)

NO_SLOTS_PHRASES = [
    "no hay citas disponibles",
    "no existen citas",
    "no quedan citas",
    "en este momento no hay citas",
    "no se puede realizar",
    "no existe ninguna cita",
]

CALENDAR_SELECTORS = [
    ".celdaFecha",
    "table.calendario",
    "input[name='rdbCita']",
    "td.libre",
]


# ══════════════════════════════════════════════════════════════════════════════
# GitHub API — читання/запис users.json
# ══════════════════════════════════════════════════════════════════════════════

def _gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def load_users() -> tuple[list[dict], str | None]:
    """Повертає (список користувачів, sha файлу для оновлення)."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{USERS_FILE}"
    r = requests.get(url, headers=_gh_headers())
    if r.status_code == 404:
        log.info("users.json не знайдено — немає зареєстрованих користувачів.")
        return [], None
    r.raise_for_status()
    data = r.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return json.loads(content), data["sha"]


def save_users(users: list[dict], sha: str | None) -> None:
    """Зберігає оновлений users.json назад у репозиторій."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{USERS_FILE}"
    content = base64.b64encode(json.dumps(users, ensure_ascii=False, indent=2).encode()).decode()
    payload = {
        "message": "update users [skip ci]",
        "content": content,
    }
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=_gh_headers(), json=payload)
    r.raise_for_status()
    log.info("users.json збережено.")


# ══════════════════════════════════════════════════════════════════════════════
# Telegram
# ══════════════════════════════════════════════════════════════════════════════

def tg_send(chat_id: int, text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }, timeout=10)


def tg_send_photo(chat_id: int, photo_bytes: bytes, caption: str = "") -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    requests.post(url, data={
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": "Markdown",
    }, files={"photo": ("screen.png", BytesIO(photo_bytes), "image/png")}, timeout=20)


def tg_get_updates(offset: int = 0) -> list[dict]:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    r = requests.get(url, params={"offset": offset, "timeout": 5}, timeout=10)
    if r.ok:
        return r.json().get("result", [])
    return []


# ══════════════════════════════════════════════════════════════════════════════
# Playwright — перевірка сайту ICP
# ══════════════════════════════════════════════════════════════════════════════

async def check_appointments(nie: str, name: str) -> dict:
    """
    Повертає dict:
      available : True | False | None
      message   : str
      screenshot: bytes | None
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="es-ES",
        )
        page = await ctx.new_page()

        async def safe_click(selector: str) -> bool:
            try:
                el = page.locator(selector).first
                await el.wait_for(state="visible", timeout=8000)
                await el.click()
                return True
            except Exception:
                return False

        try:
            # Крок 1: головна сторінка
            log.info("Відкриваємо ICP...")
            await page.goto(BASE_URL, wait_until="networkidle", timeout=30_000)

            # Крок 2: вибір провінції
            log.info("Обираємо Barcelona...")
            await page.locator("select").first.select_option(value=PROVINCE_VALUE)
            if not await safe_click("input[value='Aceptar']"):
                await safe_click("input[type='submit']")
            await page.wait_for_load_state("networkidle", timeout=15_000)

            # Крок 3: пошук процедури UCRANIA
            log.info("Шукаємо процедуру UCRANIA...")
            tramite = page.locator("select[name='tramite'], select[id='tramite']").first
            options = await tramite.locator("option").all()
            target_value = None
            for opt in options:
                text = (await opt.inner_text()).upper()
                if all(kw in text for kw in PROCEDURE_KEYWORDS):
                    target_value = await opt.get_attribute("value")
                    log.info(f"Знайдено: {text.strip()}")
                    break

            if not target_value:
                log.warning("Процедура UCRANIA не знайдена!")
                shot = await page.screenshot(full_page=True)
                return {
                    "available": None,
                    "message": (
                        "⚠️ Процедура *TARJETA CONFLICTO UCRANIA* не знайдена у списку.\n"
                        "Можливо сайт змінився. Перевір вручну:\n"
                        f"{BASE_URL}"
                    ),
                    "screenshot": shot,
                }

            await tramite.select_option(value=target_value)
            if not await safe_click("input[value='Aceptar']"):
                await safe_click("input[type='submit']")
            await page.wait_for_load_state("networkidle", timeout=15_000)

            # Рання перевірка «немає записів»
            content = (await page.content()).lower()
            for phrase in NO_SLOTS_PHRASES:
                if phrase in content:
                    log.info(f"Немає записів: '{phrase}'")
                    return {"available": False, "message": "", "screenshot": None}

            # Крок 4: дані користувача
            log.info("Заповнюємо дані...")
            for sel in [
                "input[id*='Citado'], input[name*='Citado']",
                "input[id*='nie'], input[id*='NIE']",
                "input[id*='docId'], input[name*='docId']",
            ]:
                try:
                    el = page.locator(sel).first
                    await el.wait_for(state="visible", timeout=4_000)
                    await el.fill(nie)
                    break
                except PWTimeout:
                    continue

            for sel in [
                "input[id*='nombre'], input[name*='nombre']",
                "input[id*='Name'], input[name*='Name']",
            ]:
                try:
                    el = page.locator(sel).first
                    await el.wait_for(state="visible", timeout=3_000)
                    await el.fill(name)
                    break
                except PWTimeout:
                    continue

            if not await safe_click("input[value*='Solicitar']"):
                if not await safe_click("input[value='Aceptar']"):
                    await safe_click("input[type='submit']")

            await page.wait_for_load_state("networkidle", timeout=20_000)

            # Крок 5: аналіз результату
            content = (await page.content()).lower()
            for phrase in NO_SLOTS_PHRASES:
                if phrase in content:
                    return {"available": False, "message": "", "screenshot": None}

            for sel in CALENDAR_SELECTORS:
                try:
                    await page.locator(sel).first.wait_for(state="visible", timeout=3_000)
                    shot = await page.screenshot(full_page=True)
                    log.info(f"🎉 Знайдено вільні слоти! ({sel})")
                    return {
                        "available": True,
                        "message": (
                            "🎉 *Є ВІЛЬНІ ЗАПИСИ!*\n\n"
                            f"Заходь на сайт ЗАРАЗ:\n{BASE_URL}\n\n"
                            "Провінція: *Barcelona*\n"
                            "Процедура: *TARJETA CONFLICTO UCRANIA*\n\n"
                            "⚡ Слоти розбирають дуже швидко!"
                        ),
                        "screenshot": shot,
                    }
                except PWTimeout:
                    continue

            # Незрозумілий стан
            shot = await page.screenshot(full_page=True)
            return {
                "available": None,
                "message": "⚠️ Не вдалось визначити наявність записів. Скріншот для діагностики:",
                "screenshot": shot,
            }

        except Exception as e:
            log.exception(f"Помилка: {e}")
            try:
                shot = await page.screenshot(full_page=True)
            except Exception:
                shot = None
            return {"available": None, "message": f"❌ Помилка перевірки: {e}", "screenshot": shot}

        finally:
            await browser.close()


# ══════════════════════════════════════════════════════════════════════════════
# Обробка Telegram /start та /stop команд
# ══════════════════════════════════════════════════════════════════════════════

def process_telegram_updates(users: list[dict]) -> tuple[list[dict], bool]:
    """
    Читає нові Telegram-повідомлення, обробляє /start та /stop.
    Повертає (оновлений список користувачів, чи були зміни).
    """
    # Знаходимо максимальний offset серед збережених
    saved_offsets = [u.get("last_update_id", 0) for u in users]
    offset = max(saved_offsets) + 1 if saved_offsets else 0

    updates = tg_get_updates(offset)
    if not updates:
        return users, False

    changed = False
    # Словник chat_id → індекс у списку
    user_map = {u["chat_id"]: i for i, u in enumerate(users)}

    for upd in updates:
        upd_id = upd.get("update_id", 0)
        msg = upd.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        text = msg.get("text", "").strip()

        if not chat_id or not text:
            continue

        log.info(f"Повідомлення від {chat_id}: {text!r}")

        if text.startswith("/start"):
            if chat_id in user_map:
                # Реактивація
                users[user_map[chat_id]]["active"] = True
                users[user_map[chat_id]]["last_update_id"] = upd_id
                tg_send(chat_id,
                    "✅ Моніторинг відновлено!\n\n"
                    f"NIE: `{users[user_map[chat_id]]['nie']}`\n"
                    f"Ім'я: `{users[user_map[chat_id]]['name']}`\n\n"
                    "Надішли /stop щоб зупинити."
                )
            else:
                # Новий користувач — запитуємо дані
                users.append({
                    "chat_id": chat_id,
                    "nie": None,
                    "name": None,
                    "active": False,
                    "state": "waiting_nie",
                    "last_update_id": upd_id,
                })
                user_map[chat_id] = len(users) - 1
                tg_send(chat_id,
                    "👋 Вітаю! Цей бот моніторить вільні записи для:\n"
                    "*POLICÍA – TARJETA CONFLICTO UCRANIA* (Barcelona)\n\n"
                    "📝 Введи свій *NIE або номер паспорта*\n"
                    "(наприклад: `X1234567Z` або `AA123456`)"
                )
            changed = True

        elif text.startswith("/stop"):
            if chat_id in user_map:
                users[user_map[chat_id]]["active"] = False
                users[user_map[chat_id]]["last_update_id"] = upd_id
                tg_send(chat_id, "⛔ Моніторинг зупинено.\n\nНадішли /start щоб відновити.")
                changed = True

        elif text.startswith("/status"):
            if chat_id in user_map:
                u = users[user_map[chat_id]]
                status = "✅ активний" if u.get("active") else "⛔ зупинений"
                tg_send(chat_id,
                    f"📊 *Статус*\n\n"
                    f"NIE: `{u.get('nie', 'не вказано')}`\n"
                    f"Ім'я: `{u.get('name', 'не вказано')}`\n"
                    f"Моніторинг: {status}\n"
                    f"Інтервал: ~5 хв"
                )
            else:
                tg_send(chat_id, "Ти ще не зареєстрований. Надішли /start")

        else:
            # Очікуємо дані (NIE або ім'я)
            if chat_id in user_map:
                u = users[user_map[chat_id]]
                state = u.get("state")

                if state == "waiting_nie":
                    u["nie"] = text.upper()
                    u["state"] = "waiting_name"
                    u["last_update_id"] = upd_id
                    tg_send(chat_id,
                        f"✅ NIE/Паспорт: `{u['nie']}`\n\n"
                        "👤 Тепер введи *прізвище та ім'я* великими літерами\n"
                        "(як у документі, наприклад: `IVANOVA OLENA`)"
                    )
                    changed = True

                elif state == "waiting_name":
                    u["name"] = text.upper()
                    u["state"] = None
                    u["active"] = True
                    u["last_update_id"] = upd_id
                    tg_send(chat_id,
                        f"🎉 *Готово! Моніторинг запущено.*\n\n"
                        f"NIE/Паспорт: `{u['nie']}`\n"
                        f"Ім'я: `{u['name']}`\n\n"
                        "Перевірка кожні ~5 хвилин.\n"
                        "Як тільки з'являться записи — одразу напишу! 🔔\n\n"
                        "Команди: /stop · /status"
                    )
                    changed = True

    return users, changed


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    log.info("=== Cita Monitor запущено ===")

    # 1. Завантажуємо список користувачів
    users, sha = load_users()
    log.info(f"Завантажено користувачів: {len(users)}")

    # 2. Обробляємо нові Telegram-повідомлення (/start, /stop, введення даних)
    users, changed = process_telegram_updates(users)

    # 3. Для кожного активного користувача — перевіряємо сайт
    active_users = [u for u in users if u.get("active") and u.get("nie") and u.get("name")]
    log.info(f"Активних для перевірки: {len(active_users)}")

    for user in active_users:
        log.info(f"Перевіряємо для chat_id={user['chat_id']} NIE={user['nie']}")
        result = await check_appointments(user["nie"], user["name"])

        if result["available"] is True:
            tg_send(user["chat_id"], result["message"])
            if result["screenshot"]:
                tg_send_photo(user["chat_id"], result["screenshot"],
                              "📸 Скріншот — заходь на сайт негайно!")
            # Зупиняємо моніторинг для цього користувача щоб не спамити
            for u in users:
                if u["chat_id"] == user["chat_id"]:
                    u["active"] = False
                    changed = True
            tg_send(user["chat_id"],
                "⚠️ Моніторинг зупинено після знаходження записів.\n"
                "Після запису надішли /start щоб відновити."
            )

        elif result["available"] is None:
            # Незрозумілий стан — сповіщаємо для діагностики
            tg_send(user["chat_id"], result["message"])
            if result["screenshot"]:
                tg_send_photo(user["chat_id"], result["screenshot"])

        else:
            log.info(f"chat_id={user['chat_id']}: записів немає.")

    # 4. Зберігаємо оновлений users.json якщо були зміни
    if changed or active_users:
        save_users(users, sha)

    log.info("=== Завершено ===")


if __name__ == "__main__":
    asyncio.run(main())
