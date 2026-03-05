"""
Перевіряє наявність запису на сайті ICP для:
  POLICÍA – TARJETA CONFLICTO UCRANIA  (Barcelona)

Повертає dict:
  available : True | False | None (None = незрозуміло, треба перевірити вручну)
  message   : str
  screenshot: bytes | None
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

from config import BASE_URL, PROVINCE_VALUE, PROCEDURE_KEYWORDS

logger = logging.getLogger(__name__)

# Фрази на сайті, що означають «записів немає»
NO_SLOTS_PHRASES = [
    "no hay citas disponibles",
    "no existen citas",
    "no quedan citas",
    "en este momento no hay citas",
    "no se puede realizar",
    "no existe ninguna cita",
]

# CSS-ознаки того, що календар з датами з'явився
CALENDAR_SELECTORS = [
    ".celdaFecha",
    "table.calendario",
    "[class*='calendar']",
    "input[name='rdbCita']",   # radio-кнопки з часом
    "td.libre",                 # вільна клітинка календаря
]


@dataclass
class CheckResult:
    available: Optional[bool]   # True=є місця, False=немає, None=незрозуміло
    message: str
    screenshot: Optional[bytes] = field(default=None, repr=False)


async def _safe_click(page: Page, selector: str) -> bool:
    """Клікає на перший знайдений елемент, повертає True якщо успішно."""
    try:
        el = page.locator(selector).first
        await el.wait_for(state="visible", timeout=8000)
        await el.click()
        return True
    except Exception:
        return False


async def check_appointments(nie: str, name: str) -> CheckResult:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="es-ES",
        )
        page = await context.new_page()

        try:
            # ── Крок 1: головна сторінка ─────────────────────────
            logger.info("Відкриваємо сайт ICP...")
            await page.goto(BASE_URL, wait_until="networkidle", timeout=30_000)

            # ── Крок 2: вибір провінції Barcelona ────────────────
            logger.info("Вибираємо провінцію Barcelona...")
            province_sel = page.locator("select").first
            await province_sel.select_option(value=PROVINCE_VALUE)

            # Натискаємо «Aceptar» після вибору провінції
            if not await _safe_click(page, "input[value='Aceptar']"):
                await _safe_click(page, "input[type='submit']")
            await page.wait_for_load_state("networkidle", timeout=15_000)

            # ── Крок 3: вибір процедури ───────────────────────────
            logger.info("Шукаємо процедуру UCRANIA...")
            tramite_select = page.locator("select[name='tramite'], select[id='tramite']").first

            options = await tramite_select.locator("option").all()
            target_value = None
            found_text = ""
            for opt in options:
                text = (await opt.inner_text()).upper()
                if all(kw in text for kw in PROCEDURE_KEYWORDS):
                    target_value = await opt.get_attribute("value")
                    found_text = text.strip()
                    break

            if not target_value:
                logger.warning("Процедура UCRANIA не знайдена у списку!")
                screenshot = await page.screenshot(full_page=True)
                return CheckResult(
                    available=None,
                    message=(
                        "⚠️ Процедура 'TARJETA CONFLICTO UCRANIA' не знайдена у списку.\n"
                        "Можливо сайт змінився або тимчасово недоступний."
                    ),
                    screenshot=screenshot,
                )

            logger.info(f"Знайдено процедуру: {found_text}")
            await tramite_select.select_option(value=target_value)

            if not await _safe_click(page, "input[value='Aceptar']"):
                await _safe_click(page, "input[type='submit']")
            await page.wait_for_load_state("networkidle", timeout=15_000)

            # ── Рання перевірка: може вже видно «немає записів» ──
            content = (await page.content()).lower()
            for phrase in NO_SLOTS_PHRASES:
                if phrase in content:
                    logger.info(f"Рання відмова: '{phrase}'")
                    return CheckResult(
                        available=False,
                        message="😔 Вільних записів наразі немає. Бот продовжує моніторити.",
                    )

            # ── Крок 4: заповнення особистих даних ───────────────
            logger.info("Заповнюємо особисті дані...")

            # NIE / номер паспорта
            nie_locator = page.locator(
                "input[id*='Citado'], input[name*='Citado'], "
                "input[id*='nie'], input[id*='NIE'], "
                "input[id*='docId'], input[name*='docId']"
            ).first
            try:
                await nie_locator.wait_for(state="visible", timeout=8_000)
                await nie_locator.fill(nie)
            except PWTimeout:
                logger.warning("Поле для NIE не знайдено, пробуємо продовжити...")

            # Ім'я
            name_locator = page.locator(
                "input[id*='nombre'], input[name*='nombre'], "
                "input[id*='Name'], input[name*='Name'], "
                "input[id*='des'], input[name*='des']"
            ).first
            try:
                await name_locator.wait_for(state="visible", timeout=5_000)
                await name_locator.fill(name)
            except PWTimeout:
                logger.warning("Поле для імені не знайдено, пробуємо продовжити...")

            # Натискаємо «Solicitar Cita» або «Aceptar»
            clicked = await _safe_click(page, "input[value*='Solicitar']")
            if not clicked:
                clicked = await _safe_click(page, "input[value='Aceptar']")
            if not clicked:
                await _safe_click(page, "input[type='submit']")

            await page.wait_for_load_state("networkidle", timeout=20_000)

            # ── Крок 5: аналіз результату ─────────────────────────
            content = (await page.content()).lower()

            for phrase in NO_SLOTS_PHRASES:
                if phrase in content:
                    logger.info("Записів немає (після заповнення даних).")
                    return CheckResult(
                        available=False,
                        message="😔 Вільних записів наразі немає. Бот продовжує моніторити.",
                    )

            # Шукаємо ознаки календаря / вільних слотів
            for sel in CALENDAR_SELECTORS:
                try:
                    el = page.locator(sel).first
                    await el.wait_for(state="visible", timeout=3_000)
                    screenshot = await page.screenshot(full_page=True)
                    logger.info(f"🎉 Знайдено вільні слоти! Селектор: {sel}")
                    return CheckResult(
                        available=True,
                        message=(
                            "🎉 *Є вільні записи!*\n\n"
                            f"Зайди на сайт ЗАРАЗ:\n{BASE_URL}\n\n"
                            "Провінція: Barcelona\n"
                            "Процедура: TARJETA CONFLICTO UCRANIA"
                        ),
                        screenshot=screenshot,
                    )
                except PWTimeout:
                    continue

            # Незрозумілий результат — зберігаємо скріншот для діагностики
            screenshot = await page.screenshot(full_page=True)
            logger.warning("Незрозумілий результат сторінки.")
            return CheckResult(
                available=None,
                message="⚠️ Не вдалося однозначно визначити наявність записів. Скріншот додано.",
                screenshot=screenshot,
            )

        except Exception as exc:
            logger.exception(f"Помилка під час перевірки: {exc}")
            try:
                screenshot = await page.screenshot(full_page=True)
            except Exception:
                screenshot = None
            return CheckResult(
                available=False,
                message=f"❌ Помилка перевірки: {exc}",
                screenshot=screenshot,
            )
        finally:
            await browser.close()


# ── Ручний тест ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    nie_arg  = sys.argv[1] if len(sys.argv) > 1 else "X1234567Z"
    name_arg = sys.argv[2] if len(sys.argv) > 2 else "TEST"
    result = asyncio.run(check_appointments(nie_arg, name_arg))
    print(f"\nРезультат: available={result.available}")
    print(f"Повідомлення: {result.message}")
    if result.screenshot:
        with open("debug_screenshot.png", "wb") as f:
            f.write(result.screenshot)
        print("Скріншот збережено: debug_screenshot.png")
