"""
Telegram-бот для моніторингу записів ICP.

Команди:
  /start   – реєстрація + введення даних
  /stop    – зупинити моніторинг
  /check   – ручна перевірка прямо зараз
  /status  – статус моніторингу
  /help    – допомога
"""
import logging
from io import BytesIO

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode

import storage
from checker import check_appointments
from config import TELEGRAM_TOKEN, CHECK_INTERVAL

logger = logging.getLogger(__name__)

# ── Стани діалогу реєстрації ──────────────────────────────
WAITING_NIE, WAITING_NAME = range(2)


# ═══════════════════════════════════════════════════════════
# /start
# ═══════════════════════════════════════════════════════════
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user = storage.get_user(update.effective_chat.id)
    if user:
        await update.message.reply_text(
            f"👋 Ти вже зареєстрований!\n\n"
            f"📋 NIE/Паспорт: `{user['nie']}`\n"
            f"👤 Ім'я: `{user['name']}`\n"
            f"🔄 Моніторинг: {'✅ активний' if user['active'] else '⛔ зупинений'}\n\n"
            "Команди:\n"
            "/check – перевірити зараз\n"
            "/stop – зупинити моніторинг\n"
            "/start – змінити дані",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "👋 Вітаю! Цей бот стежить за вільними записами на\n"
        "*POLICÍA – TARJETA CONFLICTO UCRANIA* (Barcelona)\n\n"
        "Для перевірки потрібні твої дані (NIE або номер паспорта).\n\n"
        "📝 Введи свій *NIE або номер паспорта* (наприклад: `X1234567Z` або `AA123456`):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return WAITING_NIE


async def received_nie(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    nie = update.message.text.strip().upper()
    ctx.user_data["nie"] = nie
    await update.message.reply_text(
        f"✅ NIE/Паспорт: `{nie}`\n\n"
        "👤 Тепер введи своє *прізвище та ім'я* (як у документі, наприклад: `IVANOVA OLENA`):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return WAITING_NAME


async def received_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip().upper()
    nie = ctx.user_data["nie"]
    chat_id = update.effective_chat.id

    storage.save_user(chat_id, nie=nie, name=name, active=True)

    await update.message.reply_text(
        f"🎉 Готово! Моніторинг запущено.\n\n"
        f"📋 NIE/Паспорт: `{nie}`\n"
        f"👤 Ім'я: `{name}`\n"
        f"⏱ Перевірка кожні {CHECK_INTERVAL // 60} хв.\n\n"
        "Як тільки з'являться вільні записи — одразу напишу! 🔔\n\n"
        "/check – перевірити прямо зараз\n"
        "/stop – зупинити моніторинг",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Реєстрацію скасовано.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════
# /check – ручна перевірка
# ═══════════════════════════════════════════════════════════
async def cmd_check(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = storage.get_user(chat_id)

    if not user:
        await update.message.reply_text(
            "⚠️ Ти ще не зареєстрований. Введи /start щоб почати."
        )
        return

    msg = await update.message.reply_text("🔍 Перевіряю сайт, зачекай...")

    result = await check_appointments(user["nie"], user["name"])
    await msg.delete()

    await _send_result(ctx, chat_id, result)


# ═══════════════════════════════════════════════════════════
# /stop
# ═══════════════════════════════════════════════════════════
async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = storage.get_user(chat_id)
    if not user:
        await update.message.reply_text("Ти ще не зареєстрований. Введи /start")
        return
    storage.set_active(chat_id, False)
    await update.message.reply_text(
        "⛔ Моніторинг зупинено.\n\nВведи /start щоб відновити."
    )


# ═══════════════════════════════════════════════════════════
# /status
# ═══════════════════════════════════════════════════════════
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = storage.get_user(chat_id)
    if not user:
        await update.message.reply_text("Ти ще не зареєстрований. Введи /start")
        return
    await update.message.reply_text(
        f"📊 *Статус моніторингу*\n\n"
        f"📋 NIE/Паспорт: `{user['nie']}`\n"
        f"👤 Ім'я: `{user['name']}`\n"
        f"🔄 Моніторинг: {'✅ активний' if user['active'] else '⛔ зупинений'}\n"
        f"⏱ Інтервал: {CHECK_INTERVAL // 60} хв.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ═══════════════════════════════════════════════════════════
# /help
# ═══════════════════════════════════════════════════════════
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤖 *Бот моніторингу записів ICP*\n\n"
        "*Команди:*\n"
        "/start – зареєструватись / змінити дані\n"
        "/check – перевірити зараз вручну\n"
        "/stop – зупинити моніторинг\n"
        "/status – переглянути статус\n"
        "/help – ця довідка\n\n"
        "*Як це працює:*\n"
        f"Бот кожні {CHECK_INTERVAL // 60} хв. заходить на сайт ICP, "
        "обирає Barcelona → TARJETA CONFLICTO UCRANIA і перевіряє, "
        "чи є вільні слоти. Якщо є — одразу надсилає сповіщення з посиланням.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ═══════════════════════════════════════════════════════════
# Допоміжна: відправка результату перевірки
# ═══════════════════════════════════════════════════════════
async def _send_result(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, result) -> None:
    # Відправляємо текст
    await ctx.bot.send_message(
        chat_id=chat_id,
        text=result.message,
        parse_mode=ParseMode.MARKDOWN,
    )
    # Якщо є скріншот (для діагностики або підтвердження)
    if result.screenshot:
        await ctx.bot.send_photo(
            chat_id=chat_id,
            photo=BytesIO(result.screenshot),
            caption="📸 Скріншот сторінки",
        )


# ═══════════════════════════════════════════════════════════
# Фонова задача: автоматична перевірка за розкладом
# ═══════════════════════════════════════════════════════════
async def scheduled_check(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Викликається JobQueue кожні CHECK_INTERVAL секунд."""
    users = storage.get_all_active()
    if not users:
        logger.info("Немає активних користувачів, пропускаємо перевірку.")
        return

    logger.info(f"Запускаємо планову перевірку для {len(users)} користувачів...")

    for user in users:
        try:
            result = await check_appointments(user["nie"], user["name"])
            logger.info(f"chat_id={user['chat_id']}: available={result.available}")

            # Надсилаємо повідомлення тільки якщо:
            #   - є вільні слоти (True)
            #   - або незрозумілий стан (None) — для діагностики
            if result.available is True or result.available is None:
                await _send_result(ctx, user["chat_id"], result)

                # Якщо є місця — зупиняємо моніторинг щоб не спамити
                if result.available is True:
                    storage.set_active(user["chat_id"], False)
                    await ctx.bot.send_message(
                        chat_id=user["chat_id"],
                        text=(
                            "⚠️ *Моніторинг зупинено* — запис знайдено!\n"
                            "Після запису введи /start щоб відновити, якщо потрібно."
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )

        except Exception as exc:
            logger.exception(f"Помилка для chat_id={user['chat_id']}: {exc}")


# ═══════════════════════════════════════════════════════════
# Побудова Application
# ═══════════════════════════════════════════════════════════
def build_app() -> Application:
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Реєстраційний діалог
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            WAITING_NIE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, received_nie)],
            WAITING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("check",  cmd_check))
    app.add_handler(CommandHandler("stop",   cmd_stop))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help",   cmd_help))

    # Планова перевірка
    app.job_queue.run_repeating(
        scheduled_check,
        interval=CHECK_INTERVAL,
        first=30,   # перша перевірка через 30 с після старту
    )

    return app
