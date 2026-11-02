import os
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import ui, app_commands
from discord.ext import commands


# ================= НАСТРОЙКИ =================

TOKEN = os.getenv("DISCORD_TOKEN")

CHANNEL_ID = 1564244322109628537

ADMIN_IDS = {
    1416863224430596107,
}

RAID_GIF_URL = "https://i.ibb.co/C5RHPHP4/10312.gif"

TIMEZONE = ZoneInfo("Europe/Moscow")

FIRST_NOTIFICATION_MINUTES = 30
FINAL_NOTIFICATION_MINUTES = 15

RESERVE_LIMIT = 3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

log = logging.getLogger("dislocation")

if not TOKEN:
    raise RuntimeError(
        "Не найдена переменная окружения DISCORD_TOKEN."
    )

TOKEN = TOKEN.strip()


# ================= БОТ =================

class DislocationBot(commands.Bot):
    async def setup_hook(self):
        synced = await self.tree.sync()

        log.info(
            "Слэш-команды синхронизированы: %s",
            len(synced)
        )


bot = DislocationBot(
    command_prefix="!",
    intents=discord.Intents.default()
)

raids = {}
raid_tasks = {}

next_raid_id = 1


# ================= ВАРИАНТЫ В КОМАНДАХ =================

SERVER_CHOICES = [
    app_commands.Choice(
        name="[13] Kingman",
        value="[13] Kingman"
    ),
    app_commands.Choice(
        name="[16] Gilbert",
        value="[16] Gilbert"
    ),
]

FORMAT_CHOICES = [
    app_commands.Choice(name="2x2", value=2),
    app_commands.Choice(name="3x3", value=3),
    app_commands.Choice(name="4x4", value=4),
    app_commands.Choice(name="5x5", value=5),
]

TIME_CHOICES = [
    app_commands.Choice(
        name=f"{hour:02d}:{minute:02d}",
        value=f"{hour:02d}:{minute:02d}"
    )
    for hour in range(16, 22)
    for minute in (0, 20, 40)
]


# ================= ВРЕМЯ =================

def now():
    return datetime.now(TIMEZONE)


def get_start_time(time_text):
    hours, minutes = map(int, time_text.split(":"))

    current = now()

    start = current.replace(
        hour=hours,
        minute=minutes,
        second=0,
        microsecond=0
    )

    if start <= current:
        start += timedelta(days=1)

    return start


async def wait_until(target):
    while True:
        seconds = (target - now()).total_seconds()

        if seconds <= 0:
            return

        await asyncio.sleep(min(seconds, 60))


# ================= КАНАЛ =================

async def get_target_channel():
    channel = bot.get_channel(CHANNEL_ID)

    if channel is None:
        channel = await bot.fetch_channel(CHANNEL_ID)

    if not isinstance(
        channel,
        (discord.TextChannel, discord.Thread)
    ):
        raise RuntimeError(
            "CHANNEL_ID должен указывать "
            "на текстовый канал или ветку."
        )

    return channel


# ================= ОФОРМЛЕНИЕ =================

def raid_details(raid):
    return (
        f"🌍 Сервер: {raid['server']}\n"
        f"⏰ Время: {raid['start']:%d.%m %H:%M}\n"
        f"🎯 Формат: {raid['slots']}x{raid['slots']}"
    )


def build_embed(raid):
    main_text = "\n".join(
        f"{index}. <@{user_id}>"
        for index, user_id in enumerate(
            raid["main"],
            start=1
        )
    ) or "Пока свободно"

    reserve_text = "\n".join(
        f"{index}. <@{user_id}>"
        for index, user_id in enumerate(
            raid["reserve"],
            start=1
        )
    ) or "Пока свободно"

    if raid["cancelled"]:
        description = "🚫 Стрела отменена."
        color = discord.Color.dark_grey()

    elif now() >= raid["start"]:
        description = "🏹 Стрела началась. Запись закрыта."
        color = discord.Color.dark_grey()

    else:
        description = (
            "🏹 Сбор состава на стрелу.\n"
            "Занимай свой слот и готовься к выходу."
        )
        color = discord.Color.dark_red()

    embed = discord.Embed(
        title="🩸 DISLOCATION MAFIA",
        description=description,
        color=color
    )

    embed.add_field(
        name="🌍 Сервер",
        value=raid["server"],
        inline=True
    )

    embed.add_field(
        name="⏰ Время",
        value=raid["start"].strftime("%d.%m %H:%M"),
        inline=True
    )

    embed.add_field(
        name="🎯 Формат",
        value=f"{raid['slots']}x{raid['slots']}",
        inline=True
    )

    embed.add_field(
        name=(
            f"🟢 Основа "
            f"({len(raid['main'])}/{raid['slots']})"
        ),
        value=main_text,
        inline=False
    )

    embed.add_field(
        name=(
            f"🟡 Резерв "
            f"({len(raid['reserve'])}/{RESERVE_LIMIT})"
        ),
        value=reserve_text,
        inline=False
    )

    embed.set_footer(
        text=f"DISLOCATION MAFIA • Стрела #{raid['id']}"
    )

    return embed


# ================= КНОПКИ =================

class RaidView(ui.View):
    def __init__(self, raid):
        super().__init__(timeout=None)

        self.raid = raid
        self.lock = asyncio.Lock()

        for child in self.children:
            child.custom_id = (
                f"dislocation:{raid['id']}:{child.custom_id}"
            )

    async def change_slot(self, interaction, action):
        await interaction.response.defer(ephemeral=True)

        async with self.lock:
            raid = self.raid
            user_id = interaction.user.id
            changed = False

            if raid["cancelled"]:
                text = "🚫 Эта стрела отменена."

            elif now() >= raid["start"]:
                text = "⏰ Стрела уже началась. Запись закрыта."

            elif action == "main":
                if user_id in raid["main"]:
                    text = "⚠️ Ты уже записан в основу."

                elif len(raid["main"]) >= raid["slots"]:
                    text = (
                        "🟡 Основные слоты заполнены. "
                        "Можешь записаться в резерв."
                    )

                else:
                    was_in_reserve = user_id in raid["reserve"]

                    if was_in_reserve:
                        raid["reserve"].remove(user_id)

                    raid["main"].append(user_id)
                    changed = True

                    slot_number = len(raid["main"])

                    if was_in_reserve:
                        text = (
                            "✅ Ты переведён из резерва в основу.\n"
                            f"🎯 Твой слот: №{slot_number}."
                        )
                    else:
                        text = (
                            "✅ Ты записан в основу.\n"
                            f"🎯 Твой слот: №{slot_number}."
                        )

            elif action == "reserve":
                if user_id in raid["main"]:
                    text = "⚠️ Ты уже записан в основу."

                elif user_id in raid["reserve"]:
                    text = "⚠️ Ты уже записан в резерв."

                elif len(raid["reserve"]) >= RESERVE_LIMIT:
                    text = "🚫 Резерв заполнен."

                else:
                    raid["reserve"].append(user_id)
                    changed = True

                    text = (
                        "✅ Ты записан в резерв.\n"
                        f"🟡 Твой номер: №{len(raid['reserve'])}."
                    )

            else:
                if user_id in raid["main"]:
                    raid["main"].remove(user_id)
                    changed = True
                    text = "✅ Ты покинул основной слот."

                elif user_id in raid["reserve"]:
                    raid["reserve"].remove(user_id)
                    changed = True
                    text = "✅ Ты покинул резерв."

                else:
                    text = "⚠️ Ты не записан в эту стрелу."

            if changed and interaction.message is not None:
                try:
                    await interaction.message.edit(
                        embed=build_embed(raid),
                        view=self,
                        allowed_mentions=discord.AllowedMentions.none()
                    )

                except discord.HTTPException:
                    log.exception(
                        "Не удалось обновить панель стрелы #%s",
                        raid["id"]
                    )

                    text += (
                        "\n⚠️ Изменение сохранено в памяти, "
                        "но обновить панель не удалось."
                    )

        await interaction.followup.send(
            text,
            ephemeral=True
        )

    @ui.button(
        label="Взять основной слот",
        style=discord.ButtonStyle.success,
        emoji="🟢",
        custom_id="main"
    )
    async def main_button(
        self,
        interaction: discord.Interaction,
        button: ui.Button
    ):
        await self.change_slot(interaction, "main")

    @ui.button(
        label="Взять резерв",
        style=discord.ButtonStyle.secondary,
        emoji="🟡",
        custom_id="reserve"
    )
    async def reserve_button(
        self,
        interaction: discord.Interaction,
        button: ui.Button
    ):
        await self.change_slot(interaction, "reserve")

    @ui.button(
        label="Покинуть слот",
        style=discord.ButtonStyle.danger,
        emoji="🚪",
        custom_id="leave"
    )
    async def leave_button(
        self,
        interaction: discord.Interaction,
        button: ui.Button
    ):
        await self.change_slot(interaction, "leave")

    async def on_error(self, interaction, error, item):
        log.error(
            "Ошибка кнопки стрелы #%s",
            self.raid["id"],
            exc_info=(type(error), error, error.__traceback__)
        )

        text = "❌ Не удалось обработать нажатие. Попробуй ещё раз."

        if interaction.response.is_done():
            await interaction.followup.send(
                text,
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                text,
                ephemeral=True
            )


# ================= ПУБЛИКАЦИЯ =================

async def publish_panel(raid, instant=False):
    channel = await get_target_channel()

    banner = discord.Embed(
        color=discord.Color.dark_red()
    )
    banner.set_image(url=RAID_GIF_URL)

    await channel.send(
        embed=banner,
        allowed_mentions=discord.AllowedMentions.none()
    )

    heading = (
        "⚡ Пик слотов открыт прямо сейчас!"
        if instant
        else "🏹 Открыта запись на стрелу!"
    )

    view = RaidView(raid)

    message = await channel.send(
        content=(
            f"@everyone {heading}\n\n"
            "🩸 DISLOCATION MAFIA\n"
            "Занимай слот кнопками ниже."
        ),
        embed=build_embed(raid),
        view=view,
        allowed_mentions=discord.AllowedMentions(
            everyone=True,
            users=False,
            roles=False
        )
    )

    raid["message"] = message
    raid["view"] = view


async def finish_raid(raid):
    await wait_until(raid["start"])

    view = raid["view"]
    message = raid["message"]

    if view is not None:
        async with view.lock:
            for child in view.children:
                child.disabled = True

            if message is not None:
                try:
                    await message.edit(
                        content="🏹 Стрела началась. Запись закрыта.",
                        embed=build_embed(raid),
                        view=view,
                        allowed_mentions=discord.AllowedMentions.none()
                    )

                except discord.HTTPException:
                    log.exception(
                        "Не удалось закрыть панель стрелы #%s",
                        raid["id"]
                    )

        view.stop()


async def raid_worker(raid, instant=False):
    try:
        if not instant:
            await wait_until(raid["publish"])

            if raid["cancelled"]:
                return

            await publish_panel(raid)

        # Не отправляем второе уведомление сразу после первого,
        # если до начала уже осталось меньше 15 минут.
        if now() < raid["final"]:
            await wait_until(raid["final"])

            if raid["cancelled"]:
                return

            try:
                channel = await get_target_channel()
                panel = raid["message"]

                panel_link = (
                    f"\n\n🟢 Запись в слоты: {panel.jump_url}"
                    if panel is not None
                    else ""
                )

                await channel.send(
                    content=(
                        "@everyone ⏰ Через 15 минут начинается стрела!\n\n"
                        "🩸 DISLOCATION MAFIA\n"
                        f"{raid_details(raid)}"
                        f"{panel_link}"
                    ),
                    embed=build_embed(raid),
                    allowed_mentions=discord.AllowedMentions(
                        everyone=True,
                        users=False,
                        roles=False
                    )
                )

            except discord.HTTPException:
                log.exception(
                    "Не удалось отправить напоминание "
                    "о стреле #%s",
                    raid["id"]
                )

        if not raid["cancelled"]:
            await finish_raid(raid)

    except asyncio.CancelledError:
        raise

    except Exception:
        log.exception(
            "Ошибка обработки стрелы #%s",
            raid["id"]
        )

    finally:
        raid_tasks.pop(raid["id"], None)


# ================= СОЗДАНИЕ СТРЕЛЫ =================

async def create_raid(
    interaction,
    server,
    time_str,
    format_choice,
    instant
):
    global next_raid_id

    if interaction.user.id not in ADMIN_IDS:
        await interaction.response.send_message(
            "🚫 Только руководство может создавать стрелы.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        await get_target_channel()

    except Exception:
        log.exception("Не удалось получить канал стрел.")

        await interaction.followup.send(
            "❌ Не удалось получить канал стрел. "
            "Проверь CHANNEL_ID и доступ бота к каналу.",
            ephemeral=True
        )
        return

    current = now()
    start = get_start_time(time_str.value)

    raid_id = next_raid_id
    next_raid_id += 1

    raid = {
        "id": raid_id,
        "server": server.value,
        "slots": format_choice.value,
        "start": start,
        "publish": max(
            current,
            start - timedelta(
                minutes=FIRST_NOTIFICATION_MINUTES
            )
        ),
        "final": start - timedelta(
            minutes=FINAL_NOTIFICATION_MINUTES
        ),
        "main": [],
        "reserve": [],
        "message": None,
        "view": None,
        "cancelled": False,
    }

    raids[raid_id] = raid

    if instant:
        try:
            await publish_panel(raid, instant=True)

        except Exception:
            log.exception(
                "Ошибка мгновенной публикации стрелы #%s",
                raid_id
            )

            raids.pop(raid_id, None)

            await interaction.followup.send(
                "❌ Не удалось опубликовать слоты. "
                "Проверь права бота на отправку сообщений "
                "и встраивание ссылок.",
                ephemeral=True
            )
            return

    raid_tasks[raid_id] = asyncio.create_task(
        raid_worker(raid, instant=instant)
    )

    heading = (
        f"⚡ Пик слотов #{raid_id} открыт!"
        if instant
        else f"✅ Стрела #{raid_id} запланирована!"
    )

    text = (
        f"{heading}\n\n"
        f"{raid_details(raid)}"
    )

    if not instant:
        text += (
            "\n📣 Открытие записи: "
            f"{raid['publish']:%d.%m %H:%M}"
        )

    if start.date() > current.date():
        text += (
            "\n\n📅 Выбранное время уже прошло сегодня. "
            "Начало назначено на завтра."
        )

    await interaction.followup.send(
        text,
        ephemeral=True
    )


# ================= /slot =================

@bot.tree.command(
    name="slot",
    description="🏹 Запланировать стрелу Dislocation Mafia"
)
@app_commands.describe(
    server="Выбери сервер",
    time_str="Выбери время начала",
    format="Выбери формат: 2x2, 3x3, 4x4 или 5x5"
)
@app_commands.choices(
    server=SERVER_CHOICES,
    time_str=TIME_CHOICES,
    format=FORMAT_CHOICES
)
async def slot(
    interaction: discord.Interaction,
    server: app_commands.Choice[str],
    time_str: app_commands.Choice[str],
    format: app_commands.Choice[int]
):
    await create_raid(
        interaction,
        server,
        time_str,
        format,
        instant=False
    )


# ================= /fslot =================

@bot.tree.command(
    name="fslot",
    description="⚡ Открыть пик слотов прямо сейчас"
)
@app_commands.describe(
    server="Выбери сервер",
    time_str="Выбери время начала",
    format="Выбери формат: 2x2, 3x3, 4x4 или 5x5"
)
@app_commands.choices(
    server=SERVER_CHOICES,
    time_str=TIME_CHOICES,
    format=FORMAT_CHOICES
)
async def fslot(
    interaction: discord.Interaction,
    server: app_commands.Choice[str],
    time_str: app_commands.Choice[str],
    format: app_commands.Choice[int]
):
    await create_raid(
        interaction,
        server,
        time_str,
        format,
        instant=True
    )


# ================= /strels =================

@bot.tree.command(
    name="strels",
    description="📋 Показать предстоящие стрелы"
)
async def strels(interaction: discord.Interaction):
    current = now()

    upcoming = [
        raid
        for raid in raids.values()
        if not raid["cancelled"]
        and raid["start"] > current
    ]

    if not upcoming:
        await interaction.response.send_message(
            "📭 Предстоящих стрел пока нет.",
            ephemeral=True
        )
        return

    upcoming.sort(
        key=lambda raid: raid["start"]
    )

    embed = discord.Embed(
        title="🩸 DISLOCATION MAFIA",
        description="📋 Предстоящие стрелы",
        color=discord.Color.dark_red()
    )

    for raid in upcoming[:25]:
        embed.add_field(
            name=f"🏹 Стрела #{raid['id']}",
            value=raid_details(raid),
            inline=False
        )

    if len(upcoming) > 25:
        embed.set_footer(
            text="Показаны ближайшие 25 стрел."
        )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )

# ================= /cancel =================

@bot.tree.command(
    name="cancel",
    description="🚫 Отменить стрелу"
)
@app_commands.describe(
    raid_id="Номер стрелы"
)
async def cancel(
    interaction: discord.Interaction,
    raid_id: int
):
    if interaction.user.id not in ADMIN_IDS:
        await interaction.response.send_message(
            "🚫 Только руководство может отменять стрелы.",
            ephemeral=True
        )
        return

    raid = raids.get(raid_id)

    if raid is None or raid["cancelled"]:
        await interaction.response.send_message(
            "❌ Стрела не найдена или уже отменена.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    raid["cancelled"] = True

    task = raid_tasks.pop(raid_id, None)

    if task is not None:
        task.cancel()

    text = f"✅ Стрела #{raid_id} отменена."

    view = raid["view"]
    message = raid["message"]

    if view is not None:
        async with view.lock:
            for child in view.children:
                child.disabled = True

            if message is not None:
                try:
                    await message.edit(
                        content="🚫 Стрела отменена.",
                        embed=build_embed(raid),
                        view=view,
                        allowed_mentions=discord.AllowedMentions.none()
                    )

                except discord.HTTPException:
                    log.exception(
                        "Не удалось обновить отменённую стрелу #%s",
                        raid_id
                    )

                    text += (
                        "\n⚠️ Не удалось обновить "
                        "сообщение в канале."
                    )

        view.stop()

    await interaction.followup.send(
        text,
        ephemeral=True
    )


# ================= ОШИБКИ КОМАНД =================

@bot.tree.error
async def on_app_command_error(interaction, error):
    original = getattr(error, "original", error)

    log.error(
        "Ошибка слэш-команды",
        exc_info=(
            type(original),
            original,
            original.__traceback__
        )
    )

    text = (
        "❌ Произошла ошибка при выполнении команды. "
        "Подробности записаны в логах бота."
    )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                text,
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                text,
                ephemeral=True
            )

    except discord.HTTPException:
        log.exception(
            "Не удалось отправить сообщение об ошибке."
        )


# ================= ЗАПУСК =================

@bot.event
async def on_ready():
    log.info(
        "Бот подключён: %s | Время Москвы: %s",
        bot.user,
        now().strftime("%d.%m.%Y %H:%M:%S")
    )


if __name__ == "__main__":
    bot.run(TOKEN)
