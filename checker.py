"""
GitHub Actions checker — тільки перевірка сайту ICP.
Реєстрацію/команди обробляє Fly.io бот в реальному часі.
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

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GITHUB_TOKEN   = os.environ["GITHUB_TOKEN"]
GITHUB_REPO    = os.environ["GITHUB_REPO"]
USERS_FILE     = "users.json"

BASE_URL = "https://icp.administracionelectronica.gob.es/icpplus/index.html"
PROVINCE_VALUE     = "08"  # Barcelona
PROCEDURE_KEYWORDS = ["UCRANIA", "CONFLICTO"]

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
# GitHub API
# ══════════════════════════════════════════════════════════════════════════════

def _gh_headers():
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


def load_users() -> tuple[list[dict], str | None]:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{USERS_FILE}"
    r = requests.get(url, headers=_gh_headers(), timeout=10)
    if r.status_code == 404:
        return [], None
    r.raise_for_status()
    data = r.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return json.loads(content), data["sha"]


def save_users(users: list[dict], sha: str | None) -> None:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{USERS_FILE}"
    content = base64.b64encode(
        json.dumps(users, ensure_ascii=False, indent=2).encode()
    ).decode()
    payload = {"message": "update users [skip ci]", "content": content}
    if sha:
        payload["sha"] = sha
    r = requests.put(url, headers=_gh_headers(), json=payload, timeout=10)
    r.raise_for_status()
    log.info("users.json збережено.")


# ══════════════════════════════════════════════════════════════════════════════
# Telegram
# ══════════════════════════════════════════════════════════════════════════════

def tg_send(chat_id: int, text: str) -> None:
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=10,
    )


def tg_send_photo(chat_id: int, photo_bytes: bytes, caption: str = "") -> None:
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
        data={"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"},
        files={"photo": ("screen.png", BytesIO(photo_bytes), "image/png")},
        timeout=20,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Playwright — перевірка ICP
# ══════════════════════════════════════════════════════════════════════════════

async def check_appointments(nie: str, name: str) -> dict:
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
            log.info("Відкриваємо ICP...")
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60_000)
            # Чекаємо поки з'явиться select з провінціями
            await page.locator("select").first.wait_for(state="visible", timeout=20_000)

            log.info("Обираємо Barcelona...")
            await page.locator("select").first.select_option(value=PROVINCE_VALUE)
            if not await safe_click("input[value='Aceptar']"):
                await safe_click("input[type='submit']")
            await page.wait_for_load_state("domcontentloaded", timeout=30_000)

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
                shot = await page.screenshot(full_page=True)
                return {
                    "available": None,
                    "message": (
                        "⚠️ Процедура *TARJETA CONFLICTO UCRANIA* не знайдена у списку.\n"
                        f"Перевір вручну: {BASE_URL}"
                    ),
                    "screenshot": shot,
                }

            await tramite.select_option(value=target_value)
            if not await safe_click("input[value='Aceptar']"):
                await safe_click("input[type='submit']")
            await page.wait_for_load_state("domcontentloaded", timeout=30_000)

            content = (await page.content()).lower()
            for phrase in NO_SLOTS_PHRASES:
                if phrase in content:
                    return {"available": False, "message": "", "screenshot": None}

            # Дані користувача
            for sel in ["input[id*='Citado']", "input[id*='nie']", "input[id*='docId']"]:
                try:
                    el = page.locator(sel).first
                    await el.wait_for(state="visible", timeout=4_000)
                    await el.fill(nie)
                    break
                except PWTimeout:
                    continue

            for sel in ["input[id*='nombre']", "input[id*='Name']"]:
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

            content = (await page.content()).lower()
            for phrase in NO_SLOTS_PHRASES:
                if phrase in content:
                    return {"available": False, "message": "", "screenshot": None}

            for sel in CALENDAR_SELECTORS:
                try:
                    await page.locator(sel).first.wait_for(state="visible", timeout=3_000)
                    shot = await page.screenshot(full_page=True)
                    log.info(f"🎉 Вільні слоти знайдено! ({sel})")
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

            shot = await page.screenshot(full_page=True)
            return {
                "available": None,
                "message": "⚠️ Не вдалось визначити наявність записів.",
                "screenshot": shot,
            }

        except Exception as e:
            log.exception(f"Помилка: {e}")
            try:
                shot = await page.screenshot(full_page=True)
            except Exception:
                shot = None
            return {"available": None, "message": f"❌ Помилка: {e}", "screenshot": shot}
        finally:
            await browser.close()


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    log.info("=== GitHub Actions: перевірка ICP ===")

    users, sha = load_users()
    active = [u for u in users if u.get("active") and u.get("nie") and u.get("name")]
    log.info(f"Активних користувачів: {len(active)}")

    if not active:
        log.info("Немає активних — завершуємо.")
        return

    changed = False
    for user in active:
        log.info(f"Перевіряємо NIE={user['nie']} chat_id={user['chat_id']}")
        result = await check_appointments(user["nie"], user["name"])

        if result["available"] is True:
            tg_send(user["chat_id"], result["message"])
            if result["screenshot"]:
                tg_send_photo(user["chat_id"], result["screenshot"],
                              "📸 Скріншот — заходь негайно!")
            # Зупиняємо щоб не спамити
            for u in users:
                if u["chat_id"] == user["chat_id"]:
                    u["active"] = False
                    changed = True
            tg_send(user["chat_id"],
                "⚠️ *Моніторинг зупинено* після знаходження записів.\n"
                "Після запису введи /start щоб відновити."
            )

        elif result["available"] is None:
            tg_send(user["chat_id"], result["message"])
            if result["screenshot"]:
                tg_send_photo(user["chat_id"], result["screenshot"])

        else:
            log.info(f"chat_id={user['chat_id']}: записів немає.")

    if changed:
        save_users(users, sha)

    log.info("=== Завершено ===")


if __name__ == "__main__":
    asyncio.run(main())
