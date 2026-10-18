import asyncio
import html
import logging
import os
import re
import sqlite3
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# =====================================================================
# НАСТРОЙКИ БОТА ДЛЯ АНКЕТ НА СЕРВЕР
# На Render задайте BOT_TOKEN и ADMIN_ID в разделе Environment Variables.
# Шаблон анкеты и тексты ответов ниже можно изменить под ваш сервер.
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ОТ_BOTFATHER")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # Ваш цифровой Telegram ID

WELCOME_TEXT = (
    "👋 <b>Добро пожаловать в бота подачи заявок на сервер!</b>\n\n"
    "Чтобы подать анкету, скопируйте шаблон ниже, заполните все пункты "
    "и отправьте одним сообщением в этот чат:\n\n"
    "<code>1. Ваш игровой никнейм:\n"
    "2. Ваш возраст:\n"
    "3. Ваш опыт игры:\n"
    "4. Чем планируете заниматься на сервере?</code>\n\n"
    "✉️ <i>После отправки администратор рассмотрит анкету, и ответ придет сюда.</i>"
)

APPROVE_TEXT = (
    "🎉 <b>Поздравляем! Ваша анкета на сервер одобрена!</b>\n\n"
    "Добро пожаловать! Если у вас появятся вопросы, вы можете написать их прямо сюда."
)

REJECT_TEXT = (
    "😔 <b>К сожалению, ваша анкета на сервер отклонена.</b>\n\n"
    "Вы можете уточнить детали или отправить исправленную анкету позже."
)

DB_PATH = os.getenv("DB_PATH", "bot_messages.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def init_db() -> None:
    """Создает локальную таблицу SQLite для связи ID сообщений администратора и игроков."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS message_links (
                admin_message_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL
            )
            """
        )
        conn.commit()


def save_message_link(admin_message_id: int, user_id: int) -> None:
    """Сохраняет привязку сообщения у администратора к ID игрока."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO message_links (admin_message_id, user_id) VALUES (?, ?)",
            (admin_message_id, user_id),
        )
        conn.commit()


def get_user_id_by_admin_msg(admin_message_id: int) -> int | None:
    """Находит ID игрока по ID сообщения, на которое ответил администратор."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            "SELECT user_id FROM message_links WHERE admin_message_id = ?",
            (admin_message_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else None


def extract_user_id_from_text(text: str | None) -> int | None:
    """Извлекает ID игрока из метки #id123456789 (работает даже после перезапуска сервера)."""
    if not text:
        return None
    match = re.search(r"#id(\d+)", text)
    return int(match.group(1)) if match else None


def format_sender_header(message: Message) -> str:
    """Формирует шапку анкеты с данными об отправителе."""
    user = message.from_user
    if not user:
        return "👤 <b>От кого:</b> Неизвестный пользователь"

    full_name = html.escape(user.full_name or "Без имени")
    username_part = f" (@{html.escape(user.username)})" if user.username else " (без @username)"
    user_id = user.id

    return (
        f"📋 <b>Новая анкета / сообщение!</b>\n"
        f"👤 <b>От кого:</b> {full_name}{username_part}\n"
        f"🆔 <b>ID игрока:</b> <code>{user_id}</code> (#id{user_id})"
    )


def get_review_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Кнопки быстрого принятия или отклонения анкеты для администратора."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Принять",
                    callback_data=f"approve:{user_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"reject:{user_id}",
                ),
            ]
        ]
    )


dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Приветствие для игроков и панель управления для администратора."""
    if message.from_user and message.from_user.id == ADMIN_ID:
        await message.answer(
            "👋 <b>Панель администратора анкет активна!</b>\n\n"
            "• Все новые анкеты игроков будут приходить в этот чат.\n"
            "• Нажимайте кнопки <b>«✅ Принять»</b> или <b>«❌ Отклонить»</b> под анкетой.\n"
            "• Чтобы задать игроку вопрос или написать свой ответ, просто сделайте "
            "<b>Reply (Ответить)</b> на карточку его анкеты."
        )
    else:
        await message.answer(WELCOME_TEXT)


@dp.message(Command("id"))
async def cmd_id(message: Message) -> None:
    """Показывает цифровой Telegram ID пользователя."""
    if message.from_user:
        await message.answer(
            f"🆔 Ваш цифровой Telegram ID: <code>{message.from_user.id}</code>"
        )


@dp.callback_query(F.data.startswith(("approve:", "reject:")))
async def handle_application_decision(callback: CallbackQuery, bot: Bot) -> None:
    """Обрабатывает нажатие кнопок 'Принять' и 'Отклонить' администратором."""
    if not callback.from_user or callback.from_user.id != ADMIN_ID:
        await callback.answer("Недостаточно прав.", show_alert=True)
        return

    action, user_id_str = (callback.data or "").split(":", maxsplit=1)
    target_user_id = int(user_id_str)

    if action == "approve":
        user_notification = APPROVE_TEXT
        status_line = "\n\n✅ <b>Статус: АНКЕТА ОДОБРЕНА</b>"
    else:
        user_notification = REJECT_TEXT
        status_line = "\n\n❌ <b>Статус: АНКЕТА ОТКЛОНЕНА</b>"

    try:
        await bot.send_message(chat_id=target_user_id, text=user_notification)
        if callback.message and callback.message.text:
            await callback.message.edit_text(
                text=f"{html.escape(callback.message.text)}{status_line}",
                reply_markup=None,
            )
        await callback.answer("Решение отправлено игроку!")
    except Exception as e:
        logger.error("Ошибка при отправке решения игроку %s: %s", target_user_id, e)
        await callback.answer(
            "Не удалось отправить сообщение (возможно, игрок заблокировал бота).",
            show_alert=True,
        )


@dp.message(Command("reply"))
async def cmd_reply(message: Message, bot: Bot) -> None:
    """Позволяет администратору написать игроку по ID: /reply 123456789 Текст."""
    if not message.from_user or message.from_user.id != ADMIN_ID:
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer(
            "⚠️ Используйте формат:\n<code>/reply ID_игрока Текст сообщения</code>"
        )
        return

    target_user_id = int(parts[1])
    reply_text = parts[2]

    try:
        await bot.send_message(
            chat_id=target_user_id,
            text=f"✉️ <b>Сообщение от администрации сервера:</b>\n\n{html.escape(reply_text)}",
        )
        await message.answer(f"✅ Сообщение отправлено игроку <code>{target_user_id}</code>.")
    except Exception as e:
        logger.error("Ошибка при отправке ответа игроку %s: %s", target_user_id, e)
        await message.answer(
            f"❌ Не удалось отправить сообщение игроку <code>{target_user_id}</code>."
        )


@dp.message(F.from_user.id == ADMIN_ID)
async def handle_admin_message(message: Message, bot: Bot) -> None:
    """Обрабатывает ответы администратора через функцию 'Ответить' (Reply)."""
    if not message.reply_to_message:
        await message.answer(
            "ℹ️ Чтобы написать игроку, нажмите <b>«Ответить» (Reply)</b> "
            "на карточку его анкеты и введите ваш ответ."
        )
        return

    replied = message.reply_to_message
    target_user_id = (
        extract_user_id_from_text(replied.text)
        or extract_user_id_from_text(replied.caption)
        or get_user_id_by_admin_msg(replied.message_id)
    )

    if not target_user_id:
        await message.answer(
            "⚠️ Не удалось определить получателя. Отвечайте (Reply) именно на карточку анкеты."
        )
        return

    try:
        if message.text:
            await bot.send_message(
                chat_id=target_user_id,
                text=f"✉️ <b>Сообщение от администрации сервера:</b>\n\n{html.escape(message.text)}",
            )
        else:
            await bot.copy_message(
                chat_id=target_user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
        await message.answer(f"✅ Ответ доставлен игроку (ID: <code>{target_user_id}</code>).")
    except Exception as e:
        logger.error("Ошибка при доставке ответа игроку %s: %s", target_user_id, e)
        await message.answer(
            f"❌ Ошибка при отправке: возможно, игрок заблокировал бота.\n"
            f"Детали: <code>{html.escape(str(e))}</code>"
        )


@dp.message()
async def handle_user_application(message: Message, bot: Bot) -> None:
    """Принимает анкету или сообщение от игрока и пересылает администратору."""
    if not message.from_user:
        return

    if ADMIN_ID == 0:
        await message.answer(
            "⚙️ Бот еще настраивается. Если вы администратор сервера, укажите ваш "
            f"<code>ADMIN_ID = {message.from_user.id}</code> в настройках на Render."
        )
        return

    header = format_sender_header(message)
    user_id = message.from_user.id
    keyboard = get_review_keyboard(user_id)

    try:
        if message.text:
            card_text = (
                f"{header}\n\n"
                f"📝 <b>Текст анкеты / сообщения:</b>\n{html.escape(message.text)}\n\n"
                f"↩️ <i>Нажмите кнопку ниже или ответьте (Reply) на это сообщение.</i>"
            )
            sent_msg = await bot.send_message(
                chat_id=ADMIN_ID,
                text=card_text,
                reply_markup=keyboard,
            )
            save_message_link(sent_msg.message_id, user_id)
        else:
            caption_content = (
                f"\n\n📝 <b>Подпись:</b>\n{html.escape(message.caption)}"
                if message.caption
                else ""
            )
            info_text = (
                f"{header}{caption_content}\n\n"
                f"📎 <i>Медиафайл прикреплен ниже. Вы можете принять/отклонить заявку кнопками "
                f"или ответить (Reply) на сообщение.</i>"
            )
            info_msg = await bot.send_message(
                chat_id=ADMIN_ID,
                text=info_text,
                reply_markup=keyboard,
            )
            save_message_link(info_msg.message_id, user_id)

            copied_msg = await bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            save_message_link(copied_msg.message_id, user_id)

        await message.answer(
            "✅ <b>Ваша анкета отправлена на рассмотрение!</b>\n"
            "Как только администратор проверит ее, вы получите уведомление в этом чате."
        )
    except Exception as e:
        logger.error("Ошибка при пересылке анкеты администратору: %s", e)
        await message.answer(
            "⚠️ Произошла ошибка при отправке анкеты. Пожалуйста, попробуйте чуть позже."
        )


async def health_check(request: web.Request) -> web.Response:
    """Веб-эндпоинт для Render и UptimeRobot, поддерживающий работу 24/7."""
    return web.Response(text="Server Application Bot is running 24/7!")


async def start_health_server() -> web.AppRunner | None:
    """Запускает фоновый HTTP-сервер, если задана переменная окружения PORT."""
    port_str = os.getenv("PORT")
    if not port_str:
        return None

    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=int(port_str))
    await site.start()
    logger.info("HTTP health-check сервер запущен на порту %s", port_str)
    return runner


async def main() -> None:
    init_db()
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    runner = await start_health_server()
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Бот для анкет запущен в режиме long-polling...")
        await dp.start_polling(bot)
    finally:
        if runner:
            await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
