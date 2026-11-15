import os
import re
import random
import time
import aiosqlite
from dotenv import load_dotenv
import fluxer

# ================== НАСТРОЙКИ ==================

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("Не найден BOT_TOKEN в файле .env!")

API_URL = "https://msg.my-shield.dev/api"
DB = "settings.db"

# Твой ник для команды !stop (регистр важен!)
OWNER_NAME = "KriperBoom"

# ================== СОВМЕСТИМЫЙ СЛОЙ ==================
# fluxer.py 0.4.x: ctx — это само сообщение (ctx.message НЕ существует!).

def get_any(obj, *names, default=None):
    """Первый существующий атрибут объекта."""
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return default

async def reply(ctx, text):
    """Ответить в чат: сначала reply, потом send."""
    try:
        await ctx.reply(text)
    except Exception as e1:
        try:
            await ctx.send(text)
        except Exception as e2:
            print(f"[ERR] reply не сработал: {e1}; send тоже: {e2}")

async def safe_call(fn, *args, **kwargs):
    """Вызвать await-метод, вернув результат или None при ошибке."""
    if not callable(fn):
        return None
    try:
        return await fn(*args, **kwargs)
    except Exception as e:
        print(f"[WARN] Вызов {getattr(fn, '__name__', fn)} не удался: {e}")
        return None

async def safe_delete(obj):
    """Безопасно удалить сообщение."""
    if not obj:
        return
    fn = getattr(obj, "delete", None)
    if callable(fn):
        await safe_call(fn)

def fmt_date(value, pattern="%d.%m.%Y"):
    if value is None:
        return "неизвестно"
    try:
        return value.strftime(pattern)
    except Exception:
        return str(value)

def parse_role_id(text):
    """Вытащить ID роли из упоминания <@&123>, числа или текста с цифрами."""
    if not text:
        return None
    m = re.search(r"\d{5,}", str(text))
    return m.group(0) if m else None

# ================== БАЗА ДАННЫХ ==================

async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                guild_id INTEGER,
                key TEXT,
                value TEXT,
                PRIMARY KEY (guild_id, key)
            )
        """)
        await db.commit()

async def get_cfg(guild_id, key, default=None):
    async with aiosqlite.connect(DB) as db:
        cursor = await db.execute(
            "SELECT value FROM settings WHERE guild_id=? AND key=?",
            (guild_id, key)
        )
        row = await cursor.fetchone()
        return row[0] if row else default

async def set_cfg(guild_id, key, value):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (guild_id, key, value) VALUES (?, ?, ?)",
            (guild_id, key, value)
        )
        await db.commit()

async def del_cfg(guild_id, key):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "DELETE FROM settings WHERE guild_id=? AND key=?",
            (guild_id, key)
        )
        await db.commit()

# ================== БОТ ==================

def create_bot():
    intents = fluxer.Intents.default()
    for attr in ("members", "message_content"):
        try:
            setattr(intents, attr, True)
        except AttributeError:
            pass
    kwargs = {
        "command_prefix": "!",
        "intents": intents,
    }
    if API_URL:
        kwargs["api_url"] = API_URL
    return fluxer.Bot(**kwargs)

bot = create_bot()

startup_time = time.time()

@bot.event
async def on_command_error(ctx, error):
    cmd = getattr(ctx, "command", None)
    print(f"[ERR] Команда '{getattr(cmd, 'name', '?')}': "
          f"{type(error).__name__}: {error}")
    try:
        await ctx.send(f"Ошибка в команде: {type(error).__name__}: {error}")
    except Exception:
        pass

@bot.event
async def on_ready():
    print(f"[OK] Бот готов: {bot.user.username}")
    print(f"[GUILD] В сообществах: {len(getattr(bot, 'guilds', []) or [])}")
    await init_db()
    print("[DB] База настроек готова")

# ---------- Роли и приветствия ----------

async def find_role_by_id(guild, role_id):
    """Найти роль в сообществе по ID (если список ролей доступен)."""
    roles = get_any(guild, "roles", default=[]) or []
    for r in roles:
        if str(getattr(r, "id", "")) == str(role_id):
            return r
    return None

async def give_role_by_id(member, guild, role_id):
    """Выдать роль по ID: перебираем известные варианты API."""
    # 1) Находим объект роли и пробуем member.add_roles(role)
    roles = get_any(guild, "roles", default=[]) or []
    role_obj = None
    for r in roles:
        if str(getattr(r, "id", "")) == str(role_id):
            role_obj = r
            break

    if role_obj is not None:
        fn = get_any(member, "add_roles", "add_role")
        if callable(fn):
            ok = await safe_call(fn, role_obj)
            if ok is not None:
                return True

    # 2) Методы на стороне guild: (member, роль) и (member_id, role_id)
    member_id = getattr(member, "id", None)
    for name in ("add_member_role", "add_role", "add_roles"):
        fn = get_any(guild, name)
        if callable(fn):
            ok = await safe_call(fn, member, role_obj or int(role_id))
            if ok is not None:
                return True
            if member_id is not None:
                ok = await safe_call(fn, member_id, int(role_id))
                if ok is not None:
                    return True

    print(f"[WARN] Не нашёл способ выдать роль {role_id} "
          f"в этой версии библиотеки")
    return False

@bot.event
async def on_member_join(member):
    guild = get_any(member, "guild")
    if guild is None:
        return

    # Приветствие
    channel_id = await get_cfg(guild.id, "welcome_channel")
    if channel_id:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except Exception as e:
            print(f"[WARN] Не удалось получить канал {channel_id}: {e}")
            channel = None
        if channel is not None:
            intro = (
                f"👋 Привет, {member.mention}! Я **{bot.user.username}** — "
                "самый первый бот этого мессенджера\n"
                "Напиши `!help`, чтобы увидеть полный список команд!"
            )
            await safe_call(channel.send, intro)
            text = await get_cfg(guild.id, "welcome_text")
            if text:
                await safe_call(channel.send, text.replace("{user}", member.mention))

    # Автовыдача роли
    role_id = await get_cfg(guild.id, "autorole")
    if role_id:
        ok = await give_role_by_id(member, guild, role_id)
        if ok:
            print(f"[ROLE] Выдал роль {role_id} участнику {member}")

# ================== КОМАНДЫ ==================

@bot.command()
async def help(ctx, section=None):
    """Справка: обзор или подробности по разделу."""

    if section is None:
        await reply(ctx,
            "🤖 **Команды бота** — напиши `!help <раздел>`, чтобы узнать подробности:\n\n"
            "💬 **Общие** — `!help общие`\n"
            "  `!ping` `!say` `!roll` `!coin` `!choose` `!poll` `!uptime`\n\n"
            "🎮 **Развлечения** — `!help игры`\n"
            "  `!menu` — меню на реакциях\n\n"
            "📊 **Инфо** — `!help инфо`\n"
            "  `!serverinfo` `!roles`\n\n"
            "⚙️ **Настройки** — `!help настройки`\n"
            "  приветствия, автовыдача ролей, `!cfg`\n\n"
            "🛡️ **Админ** — `!help админ`\n"
            "  `!stop`"
        )
        return

    s = section.lower()

    if s in ("общие", "общее", "основные"):
        await reply(ctx,
            "💬 **Общие команды**\n\n"
            "🏓 `!ping` — проверить, жив ли бот\n"
            "📣 `!say <текст>` — бот повторит твой текст и удалит команду\n"
            "🎲 `!roll [число]` — случайное число от 1 до N (по умолчанию 100)\n"
            "🪙 `!coin` — подбросить монетку\n"
            "🎯 `!choose <вариант1> <вариант2> ...` — бот выберет за тебя\n"
            "🗳️ `!poll <вопрос>` — голосование с реакциями 👍/👎\n"
            "⏱️ `!uptime` — сколько бот уже работает"
        )

    elif s in ("игры", "развлечения", "меню"):
        await reply(ctx,
            "🎮 **Развлечения**\n\n"
            "📋 `!menu` — меню на реакциях:\n"
            "🎲 бросить кубик • 🪙 подбросить монетку • 📊 инфо о сервере\n\n"
            "Нажми на реакцию под сообщением меню — бот выполнит действие. "
            "Кликать может только тот, кто вызвал меню."
        )

    elif s in ("инфо", "информация"):
        await reply(ctx,
            "📊 **Информация**\n\n"
            "🏠 `!serverinfo` — участники, каналы, роли и дата создания сообщества\n"
            "🎭 `!roles` — какие роли бот видит на сервере"
        )

    elif s in ("настройки", "сетап", "setup"):
        await reply(ctx,
            "⚙️ **Настройки сообщества**\n\n"
            "📨 `!setup welcome_channel` — включить приветствия в текущем канале\n"
            "✏️ `!setup welcome_text <текст>` — свой текст приветствия, `{user}` — упоминание новичка\n"
            "🔕 `!setup welcome_off` — выключить приветствия\n"
            "🎭 `!autorole @роль` — каждый новый участник получит эту роль\n"
            "🚫 `!autorole null` — выключить автовыдачу (также `off`)\n"
            "📋 `!cfg` — показать все настройки сообщества"
        )

    elif s in ("админ", "admin"):
        await reply(ctx,
            "🛡️ **Админ-команды**\n\n"
            "⛔ `!stop` — остановить бота (только для владельца)"
        )

    else:
        await reply(ctx,
            f"🤔 Раздел `{section}` не нашёл. Доступные: `общие`, `игры`, "
            f"`инфо`, `настройки`, `админ`"
        )

@bot.command()
async def ping(ctx):
    lat = get_any(bot, "latency", default=None)
    try:
        ms = int(float(lat) * 1000)
    except (TypeError, ValueError):
        ms = None
    await reply(ctx, f"🏓 Pong! Задержка: {ms} мс" if ms is not None else "🏓 Pong!")

@bot.command()
async def uptime(ctx):
    secs = int(time.time() - startup_time)
    h, m = secs // 3600, (secs % 3600) // 60
    await reply(ctx, f"⏱️ Работаю уже {h} ч {m} мин")

@bot.command()
async def say(ctx, *, text=None):
    if not text:
        await reply(ctx, "📣 Что сказать? Например: `!say Привет`")
        return
    # Сначала отправляем, потом удаляем команду
    await reply(ctx, text)
    await safe_delete(ctx)

@bot.command()
async def roll(ctx, n: int = 100):
    if n < 1:
        n = 100
    await reply(ctx, f"🎲 Выпало: **{random.randint(1, n)}** (из {n})")

@bot.command()
async def coin(ctx):
    await reply(ctx, f"🪙 Монетка: **{random.choice(['Орёл', 'Решка'])}**!")

@bot.command()
async def choose(ctx, *variants):
    if len(variants) < 2:
        await reply(ctx, "🎯 Напиши хотя бы два варианта: `!choose пицца суши роллы`")
        return
    await reply(ctx, f"🎯 Я выбираю: **{random.choice(variants)}**")

@bot.command()
async def poll(ctx, *, question=None):
    if not question:
        await reply(ctx, "🗳️ Напиши вопрос: `!poll Идём в кино?`")
        return
    author = get_any(ctx, "author", default=None)
    author_name = getattr(author, "name", None) or str(author or "неизвестный")
    msg = await safe_call(
        ctx.send,
        f"🗳️ **{question}**\n\n"
        f"опрос запустил(а): **{author_name}**\n\n"
        f"👍 Да | 👎 Нет"
    )
    if msg is None:
        return
    for emoji in ("👍", "👎"):
        fn = get_any(msg, "add_reaction")
        ok = await safe_call(fn, emoji)
        if ok is None:
            print(f"[WARN] Реакция {emoji} не поставилась — продолжаю со следующей")

@bot.command()
async def serverinfo(ctx):
    g = ctx.guild
    await reply(ctx,
        f"🏠 **Сообщество: {g.name}**\n"
        f"🆔 ID: `{getattr(g, 'id', '?')}`\n"
        f"👥 Участников: {get_any(g, 'member_count', default='?')}\n"
        f"📁 Каналов: {len(get_any(g, 'channels', default=[]) or [])}\n"
        f"🎭 Ролей: {len(get_any(g, 'roles', default=[]) or [])}\n"
        f"📅 Создано: {fmt_date(get_any(g, 'created_at'))}"
    )

@bot.command()
async def roles(ctx):
    """Диагностика: какие роли видит бот."""
    roles_list = get_any(ctx.guild, "roles", default=None)
    if not roles_list:
        await reply(ctx,
            "🎭 Список ролей пуст — эта версия библиотеки не отдаёт роли "
            "из кэша. Но `!autorole` всё равно работает по ID!")
        return
    names = [getattr(r, "name", "?") for r in roles_list]
    await reply(ctx, "🎭 Роли на сервере: " + ", ".join(names[:20]))

# ---------- Настройки ----------

@bot.command()
async def setup(ctx, key=None, *, value=None):
    if key == "welcome_channel":
        await set_cfg(ctx.guild.id, "welcome_channel", str(ctx.channel.id))
        mention = getattr(ctx.channel, "mention", None) or f"<#{ctx.channel.id}>"
        await reply(ctx, f"📨 Приветствия теперь будут в канале {mention}")
    elif key == "welcome_text":
        if not value:
            await reply(ctx, "✏️ Напиши текст: `!setup welcome_text Привет, {user}!`")
            return
        await set_cfg(ctx.guild.id, "welcome_text", value)
        await reply(ctx, "✅ Текст приветствия обновлён")
    elif key == "welcome_off":
        await del_cfg(ctx.guild.id, "welcome_channel")
        await reply(ctx, "🔕 Приветствия выключены в этом сообществе")
    else:
        await reply(ctx, "⚙️ Напиши `!help настройки`, чтобы увидеть все настройки")

OFF_WORDS = ("off", "null", "none", "выкл", "0", "отмена")

@bot.command()
async def autorole(ctx, *, role_arg=None):
    """Автовыдача роли новичкам: !autorole @роль | !autorole null — выключить"""
    # --- Выключение ---
    if role_arg is None or role_arg.lower().strip() in OFF_WORDS:
        await del_cfg(ctx.guild.id, "autorole")
        await reply(ctx,
            "🚫 Автовыдача ролей выключена. Новые участники роль получать не будут.\n"
            "Включить снова: `!autorole @роль`")
        return

    # --- Вытаскиваем ID из упоминания, числа или имени ---
    role_id = None
    role_name = None

    # 1) ID прямо из текста: <@&123>, <@123> или "123"
    role_id = parse_role_id(role_arg)

    # 2) Упоминание роли в сообщении (если библиотека их отдаёт)
    if role_id is None:
        role_mentions = get_any(ctx, "role_mentions", default=[]) or []
        if role_mentions:
            rid = getattr(role_mentions[0], "id", None)
            role_id = str(rid) if rid is not None else None

    # 3) Ищем по имени среди ролей сообщества (если список не пустой)
    if role_id is None:
        roles_list = get_any(ctx.guild, "roles", default=[]) or []
        for r in roles_list:
            if getattr(r, "name", "").lower() == role_arg.lstrip("@").lower():
                role_id = str(getattr(r, "id"))
                role_name = getattr(r, "name")
                break

    if role_id is None:
        await reply(ctx,
            f"🤔 Не смог вытащить роль из `{role_arg}`.\n"
            "Попробуй именно упоминанием: набери `@Учасник сервера` "
            "и выбери роль из всплывашки, потом отправь сообщение.")
        return

    # Проверим, что роль реально существует (если список доступен)
    role_obj = await find_role_by_id(ctx.guild, role_id)
    if role_obj is not None:
        role_name = getattr(role_obj, "name", role_name)

    await set_cfg(ctx.guild.id, "autorole", role_id)

    display = role_name or f"ID {role_id}"
    await reply(ctx,
        f"🎉 Готово! Теперь все новые участники этого сообщества "
        f"автоматически получают роль **{display}**!\n"
        f"Выключить: `!autorole null`")

@bot.command()
async def cfg(ctx):
    autorole_id = await get_cfg(ctx.guild.id, "autorole")
    welcome_channel_id = await get_cfg(ctx.guild.id, "welcome_channel")
    welcome_text = await get_cfg(ctx.guild.id, "welcome_text")

    if not welcome_channel_id and not welcome_text and not autorole_id:
        await reply(ctx, "📋 Настройки для этого сообщества ещё не заданы. Используй `!setup` и `!autorole`.")
        return

    lines = ["📋 **Настройки этого сообщества:**"]

    if autorole_id:
        role = await find_role_by_id(ctx.guild, autorole_id)
        name = getattr(role, "name", f"ID {autorole_id}") if role is not None else f"ID {autorole_id}"
        lines.append(f"- 🎭 Авто-роль: **{name}** (выключить: `!autorole null`)")

    if welcome_channel_id:
        try:
            channel = await bot.fetch_channel(int(welcome_channel_id))
            mention = getattr(channel, "mention", None) or f"<#{welcome_channel_id}>"
            lines.append(f"- 📨 Канал приветствий: {mention}")
        except Exception as e:
            print(f"[WARN] Не удалось получить канал {welcome_channel_id}: {e}")
            lines.append(f"- 📨 Канал приветствий: ID {welcome_channel_id} (не найден)")

    if welcome_text:
        display_text = welcome_text[:200] + ("..." if len(welcome_text) > 200 else "")
        lines.append(f"- ✏️ Текст: `{display_text}`")

    await reply(ctx, "\n".join(lines))

# ---------- Команда остановки (только для владельца) ----------

@bot.command()
async def stop(ctx):
    author_name = getattr(ctx.author, "name", None) or str(ctx.author)
    if author_name != OWNER_NAME:
        await reply(ctx, "⛔ У тебя нет права останавливать бота.")
        return
    await reply(ctx, "🛑 Бот завершает работу...")
    fn = get_any(bot, "close", "logout", "disconnect")
    if callable(fn):
        await safe_call(fn)

# ================== МЕНЮ НА РЕАКЦИЯХ ==================

MENU_EMOJIS = {
    "🎲": "roll",
    "🪙": "coin",
    "📊": "info",
}

menu_messages = {}  # id сообщения меню -> id вызвавшего

@bot.command()
async def menu(ctx):
    """Меню с кнопками-реакциями."""
    text = (
        "📋 **Меню бота** — выбери реакцию:\n\n"
        "🎲 — бросить кубик\n"
        "🪙 — подбросить монетку\n"
        "📊 — инфо о сервере"
    )
    msg = await safe_call(ctx.send, text)
    if msg is None:
        return

    added = 0
    for emoji in MENU_EMOJIS:
        fn = get_any(msg, "add_reaction")
        ok = await safe_call(fn, emoji)
        if ok is None:
            print(f"[WARN] Реакция {emoji} не поставилась — продолжаю")
            continue
        added += 1

    if added == 0:
        await reply(ctx, "😕 Реакции не поддерживаются — меню недоступно")
        return

    msg_id = getattr(msg, "id", None)
    if msg_id is not None:
        menu_messages[msg_id] = getattr(ctx.author, "id", None)

@bot.event
async def on_reaction_add(reaction, user):
    try:
        msg = get_any(reaction, "message")
        if msg is None:
            return

        msg_id = getattr(msg, "id", None)
        if msg_id not in menu_messages:
            return

        if getattr(user, "bot", False):
            return

        allowed_id = menu_messages[msg_id]
        user_id = getattr(user, "id", None)
        if allowed_id is not None and user_id != allowed_id:
            return

        emoji = get_any(reaction, "emoji")
        emoji = getattr(emoji, "name", emoji)

        action = MENU_EMOJIS.get(emoji)
        if action == "roll":
            await safe_call(msg.channel.send,
                            f"🎲 Выпало: **{random.randint(1, 100)}** (из 100)")
        elif action == "coin":
            await safe_call(msg.channel.send,
                            f"🪙 Монетка: **{random.choice(['Орёл', 'Решка'])}**!")
        elif action == "info":
            g = get_any(msg, "guild")
            if g is None:
                return
            await safe_call(msg.channel.send,
                f"📊 **{getattr(g, 'name', '?')}**\n"
                f"👥 Участников: {get_any(g, 'member_count', default='?')}")
    except Exception as e:
        print(f"[WARN] on_reaction_add: {e}")

# ================== ЗАПУСК ==================

# При обычном запуске (start_bot.bat) бот стартует сам.
# setup.py импортирует этот файл и запускает бота сам — поэтому тут защита,
# чтобы бот не запустился дважды.
if __name__ == "__main__":
    bot.run(TOKEN)
