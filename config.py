import os

# ── Telegram ──────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")

# ── ICP Site ──────────────────────────────────────────────
BASE_URL = "https://icp.administracionelectronica.gob.es/icpplus/index.html"
PROVINCE_VALUE = "08"        # Barcelona
PROVINCE_NAME  = "BARCELONA"

# Ключові слова для пошуку процедури у списку (регістр не важливий)
PROCEDURE_KEYWORDS = ["UCRANIA", "CONFLICTO"]

# ── Scheduler ─────────────────────────────────────────────
# Інтервал перевірки у секундах (за замовчуванням 5 хвилин)
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "300"))
