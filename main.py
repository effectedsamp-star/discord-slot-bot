import os
import asyncio
import random
import re
from collections import Counter
from datetime import datetime, timedelta

import discord
from discord import ui, Interaction, app_commands
from discord.ext import commands


# ================= НАСТРОЙКИ =================

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "Не найдена переменная окружения DISCORD_TOKEN."
    )

CHANNEL_ID = 1543007392315474080

ADMIN_IDS = [
    1416863224430596107,
    1076786393658970214,
    1519017264295313548,
    541552528677011457,
    1326336317071818804,
]

NOTIFICATION_BEFORE_MINUTES = 30
FINAL_NOTIFICATION_BEFORE_MINUTES = 15

MIN_CHAT_DELAY_SECONDS = 30 * 60
MAX_CHAT_DELAY_SECONDS = 60 * 60

# ===============================================


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

active_raids = {}
scheduled_raids = {}

last_raid_message_id = None
next_schedule_id = 1

word_counts = Counter()
phrase_counts = Counter()


# ================= ВЫБОРЫ В /slot =================


SERVER_CHOICES = [
    app_commands.Choice(name="04 Chandler", value="04 Chandler"),
    app_commands.Choice(name="12 Glendale", value="12 Glendale"),
    app_commands.Choice(name="15 Payson", value="15 Payson"),
    app_commands.Choice(name="16 Gilbert", value="16 Gilbert"),
    app_commands.Choice(name="23 Holiday", value="23 Holiday"),
]

FACTION_CHOICES = [
    app_commands.Choice(name="RM", value="RM"),
    app_commands.Choice(name="LCN", value="LCN"),
    app_commands.Choice(name="TRB", value="TRB"),
    app_commands.Choice(name="YKZ", value="YKZ"),
    app_commands.Choice(name="WMC", value="WMC"),
]

YES_NO_CHOICES = [
    app_commands.Choice(name="Да", value="yes"),
    app_commands.Choice(name="Нет", value="no"),
]

FORMAT_CHOICES = [
    app_commands.Choice(name="2x2", value="2x2"),
    app_commands.Choice(name="3x3", value="3x3"),
    app_commands.Choice(name="4x4", value="4x4"),
    app_commands.Choice(name="5x5", value="5x5"),
]

TIME_CHOICES = [
    app_commands.Choice(name="16:00", value="16:00"),
    app_commands.Choice(name="16:20", value="16:20"),
    app_commands.Choice(name="16:40", value="16:40"),
    app_commands.Choice(name="17:00", value="17:00"),
    app_commands.Choice(name="17:20", value="17:20"),
    app_commands.Choice(name="17:40", value="17:40"),
    app_commands.Choice(name="18:00", value="18:00"),
    app_commands.Choice(name="18:20", value="18:20"),
    app_commands.Choice(name="18:40", value="18:40"),
    app_commands.Choice(name="19:00", value="19:00"),
    app_commands.Choice(name="19:20", value="19:20"),
    app_commands.Choice(name="19:40", value="19:40"),
    app_commands.Choice(name="20:00", value="20:00"),
    app_commands.Choice(name="20:20", value="20:20"),
    app_commands.Choice(name="20:40", value="20:40"),
    app_commands.Choice(name="21:00", value="21:00"),
    app_commands.Choice(name="21:20", value="21:20"),
    app_commands.Choice(name="21:40", value="21:40"),
]


# ===================================================


def is_admin(user: discord.abc.User) -> bool:
    return user.id in ADMIN_IDS


def parse_format(format_text: str) -> int:
    formats = {
        "2x2": 2,
        "3x3": 3,
        "4x4": 4,
        "5x5": 5,
    }

    cleaned = format_text.lower().replace(" ", "").replace("х", "x")

    if cleaned not in formats:
        raise ValueError(
            "Формат должен быть: 2x2, 3x3, 4x4 или 5x5."
        )

    return formats[cleaned]


def get_start_datetime(time_text: str) -> datetime:
    hours_text, minutes_text = time_text.split(":")

    hours = int(hours_text)
    minutes = int(minutes_text)

    now = datetime.now()

    start_time = now.replace(
        hour=hours,
        minute=minutes,
        second=0,
        microsecond=0
    )

    if start_time <= now:
        start_time += timedelta(days=1)

    return start_time


def clean_chat_text(text: str) -> str:
    text = text.lower().strip()

    text = re.sub(
        r"https?://\S+|www\.\S+",
        "",
        text
    )

    text = re.sub(
        r"<@!?\d+>|<@&\d+>|<#\d+>",
        "",
        text
    )

    return re.sub(r"\s+", " ", text).strip()


def build_embed(data: dict) -> discord.Embed:
    main_list = "\n".join(
        f"{number}. <@{user_id}>"
        for number, user_id in enumerate(data["main"], start=1)
    ) or "—"

    reserve_list = "\n".join(
        f"{number}. <@{user_id}>"
        for number, user_id in enumerate(data["reserve"], start=1)
    ) or "—"

    embed = discord.Embed(
        title=f"🏹 Стрела | {data['server']}",
        color=discord.Color.green()
    )

    embed.add_field(
        name="Формат",
        value=data["format"],
        inline=True
    )

    embed.add_field(
        name="Время начала",
        value=data["time"],
        inline=True
    )

    embed.add_field(
        name="Против",
        value=data["faction"],
        inline=True
    )

    embed.add_field(
        name="Оружие",
        value=data["weapon"],
        inline=True
    )

    embed.add_field(
        name="Создал",
        value=f"<@{data['creator_id']}>",
        inline=True
    )

    embed.add_field(
        name=f"Основные слоты ({len(data['main'])}/{data['slots_total']})",
        value=main_list,
        inline=False
    )

    embed.add_field(
        name=f"Резерв ({len(data['reserve'])}/3)",
        value=reserve_list,
        inline=False
    )

    embed.set_footer(
        text="Нажми кнопку, чтобы записаться или покинуть слот."
    )

    return embed


class RaidView(ui.View):
    def __init__(self, data: dict):
        super().__init__(timeout=None)

        self.data = data
        self.message = None
        self.lock = asyncio.Lock()

    async def update_message(self):
        if self.message is not None:
            await self.message.edit(
                embed=build_embed(self.data),
                view=self
            )

    @ui.button(
        label="Взять основной слот",
        style=discord.ButtonStyle.green,
        custom_id="main_slot"
    )
    async def main_button(
        self,
        interaction: Interaction,
        button: ui.Button
    ):
        user_id = interaction.user.id

        async with self.lock:
            if user_id in self.data["main"]:
                await interaction.response.send_message(
                    "Ты уже записан в основные слоты.",
                    ephemeral=True
                )
                return

            if len(self.data["main"]) >= self.data["slots_total"]:
                await interaction.response.send_message(
                    "Основные слоты заполнены. Можешь записаться в резерв.",
                    ephemeral=True
                )
                return

            was_in_reserve = user_id in self.data["reserve"]

            if was_in_reserve:
                self.data["reserve"].remove(user_id)

            self.data["main"].append(user_id)

            slot_number = len(self.data["main"])

            await interaction.response.defer(ephemeral=True)
            await self.update_message()

            if was_in_reserve:
                text = (
                    f"✅ Ты переведён из резерва в основу.\n"
                    f"Твой номер основного слота: **{slot_number}**.\n"
                    f"Покинуть слот можно кнопкой **«Покинуть слот»**."
                )
            else:
                text = (
                    f"✅ Ты успешно взял основной слот №**{slot_number}**.\n"
                    f"Покинуть слот можно кнопкой **«Покинуть слот»**."
                )

            await interaction.followup.send(
                text,
                ephemeral=True
            )

    @ui.button(
        label="Взять резерв",
        style=discord.ButtonStyle.secondary,
        custom_id="reserve_slot"
    )
    async def reserve_button(
        self,
        interaction: Interaction,
        button: ui.Button
    ):
        user_id = interaction.user.id

        async with self.lock:
            if user_id in self.data["reserve"]:
                await interaction.response.send_message(
                    "Ты уже записан в резерв.",
                    ephemeral=True
                )
                return

            if user_id in self.data["main"]:
                await interaction.response.send_message(
                    "Ты уже записан в основные слоты.",
                    ephemeral=True
                )
                return

            if len(self.data["reserve"]) >= 3:
                await interaction.response.send_message(
                    "Резерв заполнен. Максимум: 3 игрока.",
                    ephemeral=True
                )
                return

            self.data["reserve"].append(user_id)

            reserve_number = len(self.data["reserve"])

            await interaction.response.defer(ephemeral=True)
            await self.update_message()

            await interaction.followup.send(
                f"✅ Ты успешно взял резервный слот №**{reserve_number}**.\n"
                f"Покинуть слот можно кнопкой **«Покинуть слот»**.",
                ephemeral=True
            )

    @ui.button(
        label="Покинуть слот",
        style=discord.ButtonStyle.danger,
        custom_id="leave_slot"
    )
    async def leave_button(
        self,
        interaction: Interaction,
        button: ui.Button
    ):
        user_id = interaction.user.id

        async with self.lock:
            if user_id in self.data["main"]:
                self.data["main"].remove(user_id)
                place = "основные слоты"

            elif user_id in self.data["reserve"]:
                self.data["reserve"].remove(user_id)
                place = "резерв"

            else:
                await interaction.response.send_message(
                    "Ты не записан в эту стрелу.",
                    ephemeral=True
                )
                return

            await interaction.response.defer(ephemeral=True)
            await self.update_message()

            await interaction.followup.send(
                f"✅ Ты успешно покинул {place}.",
                ephemeral=True
            )


async def get_target_channel():
    channel = bot.get_channel(CHANNEL_ID)

    if channel is None:
        try:
            channel = await bot.fetch_channel(CHANNEL_ID)
        except discord.DiscordException:
            return None

    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return None

    return channel


async def publish_raid(number: int):
    global last_raid_message_id

    raid = scheduled_raids.get(number)

    if not raid:
        return

    seconds_to_first_message = (
        raid["publish_time"] - datetime.now()
    ).total_seconds()

    if seconds_to_first_message > 0:
        await asyncio.sleep(seconds_to_first_message)

    raid = scheduled_raids.get(number)

    if not raid:
        return

    channel = await get_target_channel()

    if not channel:
        print(
            f"Стрела #{number} не отправлена. "
            f"Проверь CHANNEL_ID и права бота."
        )

        scheduled_raids.pop(number, None)
        return

    data = raid["data"]

    minutes_left = max(
        0,
        int(
            (
                raid["start_time"] - datetime.now()
            ).total_seconds() / 60
        )
    )

    if minutes_left == 0:
        notification_text = "🏹 Стрела начинается прямо сейчас!"

    elif minutes_left == 1:
        notification_text = "🏹 Через 1 минуту стрела!"

    elif 2 <= minutes_left <= 4:
        notification_text = f"🏹 Через {minutes_left} минуты стрела!"

    else:
        notification_text = f"🏹 Через {minutes_left} минут стрела!"

    try:
        # Первое уведомление за 30 минут
        await channel.send(
            f"@everyone {notification_text}\n"
            f"Сервер: **{data['server']}**\n"
            f"Против: **{data['faction']}**\n"
            f"Оружие: **{data['weapon']}**\n"
            f"Формат: **{data['format']}**\n"
            f"Время начала: **{data['time']}**",

            allowed_mentions=discord.AllowedMentions(
                everyone=True,
                users=False,
                roles=False
            )
        )

        view = RaidView(data)

        message = await channel.send(
            embed=build_embed(data),
            view=view
        )

        view.message = message

        active_raids[message.id] = {
            "data": data,
            "view": view,
            "message": message
        }

        last_raid_message_id = message.id

    except discord.Forbidden:
        print(
            f"Нет прав на первую публикацию стрелы #{number}."
        )

        scheduled_raids.pop(number, None)
        return

    except discord.HTTPException as error:
        print(f"Ошибка Discord: {error}")

        scheduled_raids.pop(number, None)
        return

    # Второе уведомление и актуальный пик за 15 минут
    seconds_to_final_message = (
        raid["final_time"] - datetime.now()
    ).total_seconds()

    if seconds_to_final_message > 0:
        await asyncio.sleep(seconds_to_final_message)

    # Проверяем, что стрелу не отменили
    if number not in scheduled_raids:
        return

    try:
        # Новый Embed содержит актуальный список тех,
        # кто успел взять основной слот или резерв
        final_embed = build_embed(data)

        await channel.send(
            f"@everyone ⏰ Через {FINAL_NOTIFICATION_BEFORE_MINUTES} минут "
            f"начинается стрела!\n"
            f"Сервер: **{data['server']}**\n"
            f"Против: **{data['faction']}**\n"
            f"Оружие: **{data['weapon']}**\n"
            f"Формат: **{data['format']}**\n"
            f"Время начала: **{data['time']}**\n\n"
            f"Ниже показан актуальный пик слотов.",

            embed=final_embed,

            allowed_mentions=discord.AllowedMentions(
                everyone=True,
                users=False,
                roles=False
            )
        )

    except discord.Forbidden:
        print(
            f"Нет прав на итоговое сообщение стрелы #{number}."
        )

    except discord.HTTPException as error:
        print(f"Ошибка итогового сообщения: {error}")

    finally:
        scheduled_raids.pop(number, None)


async def random_chat_loop():
    await bot.wait_until_ready()

    while not bot.is_closed():
        await asyncio.sleep(
            random.randint(
                MIN_CHAT_DELAY_SECONDS,
                MAX_CHAT_DELAY_SECONDS
            )
        )

        channel = await get_target_channel()

        if channel is None:
            continue

        if phrase_counts and random.choice([True, True, False]):
            popular_phrases = [
                phrase
                for phrase, count in phrase_counts.most_common(30)
            ]

            text = random.choice(popular_phrases)

        elif word_counts:
            popular_words = [
                word
                for word, count in word_counts.most_common(30)
            ]

            text = random.choice(popular_words)

        else:
            continue

        await channel.send(
            f"💬 {text}",
            allowed_mentions=discord.AllowedMentions.none()
        )


@bot.event
async def on_message(message: discord.Message):
    if (
        message.guild is not None
        and not message.author.bot
        and message.channel.id == CHANNEL_ID
    ):
        text = clean_chat_text(message.content)

        if text and not text.startswith(("!", "/")):
            words = re.findall(
                r"[a-zа-яё0-9]{3,}",
                text,
                flags=re.IGNORECASE
            )

            word_counts.update(words)

            if 2 <= len(words) <= 10 and len(text) <= 120:
                phrase_counts[text] += 1

    await bot.process_commands(message)


@bot.tree.command(
    name="slot",
    description="Запланировать новую стрелу"
)
@app_commands.describe(
    server="Выбери сервер",
    faction="Выбери фракцию противника",
    deagle="Разрешён ли Deagle?",
    shotgun="Разрешён ли Shotgun?",
    rifla="Разрешена ли Rifla?",
    format="Выбери формат",
    time_str="Выбери время начала стрелы"
)
@app_commands.choices(
    server=SERVER_CHOICES,
    faction=FACTION_CHOICES,
    deagle=YES_NO_CHOICES,
    shotgun=YES_NO_CHOICES,
    rifla=YES_NO_CHOICES,
    format=FORMAT_CHOICES,
    time_str=TIME_CHOICES
)
async def slot(
    interaction: Interaction,
    server: app_commands.Choice[str],
    faction: app_commands.Choice[str],
    deagle: app_commands.Choice[str],
    shotgun: app_commands.Choice[str],
    rifla: app_commands.Choice[str],
    format: app_commands.Choice[str],
    time_str: app_commands.Choice[str]
):
    global next_schedule_id

    if interaction.guild is not None:
        await interaction.response.send_message(
            "Используй команду /slot в личных сообщениях с ботом.",
            ephemeral=True
        )
        return

    if not is_admin(interaction.user):
        await interaction.response.send_message(
            "У тебя нет прав для создания стрел.",
            ephemeral=True
        )
        return

    weapons = []

    if deagle.value == "yes":
        weapons.append("Deagle")

    if shotgun.value == "yes":
        weapons.append("Shotgun")

    if rifla.value == "yes":
        weapons.append("Rifla")

    if not weapons:
        await interaction.response.send_message(
            "Ошибка: нужно выбрать хотя бы одно оружие.",
            ephemeral=True
        )
        return

    try:
        slots_total = parse_format(format.value)
        start_time = get_start_datetime(time_str.value)

    except ValueError as error:
        await interaction.response.send_message(
            f"Ошибка: {error}",
            ephemeral=True
        )
        return

    now = datetime.now()

    # Первое сообщение за 30 минут
    publish_time = start_time - timedelta(
        minutes=NOTIFICATION_BEFORE_MINUTES
    )

    if publish_time <= now:
        publish_time = now

    # Второе сообщение и итоговые слоты за 15 минут
    final_time = start_time - timedelta(
        minutes=FINAL_NOTIFICATION_BEFORE_MINUTES
    )

    if final_time <= now:
        final_time = now

    schedule_id = next_schedule_id
    next_schedule_id += 1

    data = {
        "server": server.value,
        "faction": faction.value,
        "weapon": ", ".join(weapons),
        "format": format.value,
        "slots_total": slots_total,
        "time": time_str.value,
        "creator_id": interaction.user.id,
        "main": [],
        "reserve": []
    }

    scheduled_raids[schedule_id] = {
        "data": data,
        "start_time": start_time,
        "publish_time": publish_time,
        "final_time": final_time,
        "task": None
    }

    task = asyncio.create_task(
        publish_raid(schedule_id)
    )

    scheduled_raids[schedule_id]["task"] = task

    await interaction.response.send_message(
        f"✅ Стрела #{schedule_id} запланирована.\n\n"
        f"Сервер: **{server.value}**\n"
        f"Против: **{faction.value}**\n"
        f"Оружие: **{', '.join(weapons)}**\n"
        f"Формат: **{format.value}**\n"
        f"Начало: **{start_time:%d.%m.%Y %H:%M}**\n"
        f"Уведомление и открытие слотов: "
        f"**{publish_time:%d.%m.%Y %H:%M}**\n"
        f"Итоговый пик слотов: "
        f"**{final_time:%d.%m.%Y %H:%M}**",
        ephemeral=True
    )


@bot.tree.command(
    name="strels",
    description="Показать все запланированные стрелы"
)
async def strels(interaction: Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Команду /strels нельзя использовать в личных сообщениях.",
            ephemeral=True
        )
        return

    if not scheduled_raids:
        await interaction.response.send_message(
            "📋 Сейчас нет запланированных стрел.",
            ephemeral=False
        )
        return

    lines = []

    for schedule_id, raid in sorted(scheduled_raids.items()):
        data = raid["data"]

        lines.append(
            f"**#{schedule_id}** — "
            f"**{data['server']}** | "
            f"против **{data['faction']}** | "
            f"оружие: **{data['weapon']}** | "
            f"**{data['format']}** | "
            f"начало: **{raid['start_time']:%d.%m %H:%M}** | "
            f"слоты: **{raid['publish_time']:%d.%m %H:%M}** | "
            f"итог: **{raid['final_time']:%d.%m %H:%M}**"
        )

    await interaction.response.send_message(
        "📋 **Запланированные стрелы:**\n\n" + "\n".join(lines),
        ephemeral=False
    )


@bot.tree.command(
    name="dstrel",
    description="Отменить запланированную стрелу"
)
@app_commands.describe(
    number="Номер стрелы из команды /strels"
)
async def dstrel(
    interaction: Interaction,
    number: int
):
    if interaction.guild is not None:
        await interaction.response.send_message(
            "Используй /dstrel в личных сообщениях с ботом.",
            ephemeral=True
        )
        return

    if not is_admin(interaction.user):
        await interaction.response.send_message(
            "У тебя нет прав для отмены стрел.",
            ephemeral=True
        )
        return

    raid = scheduled_raids.get(number)

    if raid is None:
        await interaction.response.send_message(
            "Стрела не найдена или уже опубликована.",
            ephemeral=True
        )
        return

    task = raid["task"]

    if task is not None and not task.done():
        task.cancel()

    scheduled_raids.pop(number, None)

    await interaction.response.send_message(
        f"❌ Стрела #{number} отменена.",
        ephemeral=True
    )


async def get_current_raid(interaction: Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Эту команду нельзя использовать в личных сообщениях.",
            ephemeral=True
        )
        return None

    if not is_admin(interaction.user):
        await interaction.response.send_message(
            "У тебя нет прав для использования этой команды.",
            ephemeral=True
        )
        return None

    raid = active_raids.get(last_raid_message_id)

    if raid is None:
        await interaction.response.send_message(
            "Сейчас нет активной стрелы.",
            ephemeral=True
        )

    return raid


@bot.tree.command(
    name="dslot",
    description="Удалить игрока из активной стрелы"
)
@app_commands.describe(
    user="Игрок, которого нужно удалить"
)
async def dslot(
    interaction: Interaction,
    user: discord.Member
):
    raid = await get_current_raid(interaction)

    if raid is None:
        return

    data = raid["data"]
    view = raid["view"]

    async with view.lock:
        if user.id in data["main"]:
            data["main"].remove(user.id)

        elif user.id in data["reserve"]:
            data["reserve"].remove(user.id)

        else:
            await interaction.response.send_message(
                "Этот игрок не записан в слоты.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        await view.update_message()

    await interaction.followup.send(
        f"{user.mention} удалён из слотов.",
        ephemeral=True
    )


@bot.tree.command(
    name="addslot",
    description="Добавить игрока в основные слоты"
)
@app_commands.describe(
    user="Игрок, которого нужно добавить"
)
async def addslot(
    interaction: Interaction,
    user: discord.Member
):
    raid = await get_current_raid(interaction)

    if raid is None:
        return

    data = raid["data"]
    view = raid["view"]

    async with view.lock:
        if user.id in data["main"]:
            await interaction.response.send_message(
                "Этот игрок уже находится в основных слотах.",
                ephemeral=True
            )
            return

        if len(data["main"]) >= data["slots_total"]:
            await interaction.response.send_message(
                "Основные слоты заполнены.",
                ephemeral=True
            )
            return

        if user.id in data["reserve"]:
            data["reserve"].remove(user.id)

        data["main"].append(user.id)

        await interaction.response.defer(ephemeral=True)
        await view.update_message()

    await interaction.followup.send(
        f"{user.mention} добавлен в основные слоты.",
        ephemeral=True
    )


@bot.event
async def on_ready():
    print(f"Бот готов: {bot.user}")

    await bot.change_presence(
        activity=discord.Game(
            name="Пикни слот на стрелу, чудище!"
        )
    )

    synced = await bot.tree.sync()

    print(
        f"Синхронизировано slash-команд: {len(synced)}"
    )

    if not hasattr(bot, "random_chat_task"):
        bot.random_chat_task = asyncio.create_task(
            random_chat_loop()
        )


@bot.event
async def on_app_command_error(
    interaction: Interaction,
    error: app_commands.AppCommandError
):
    print(f"Ошибка slash-команды: {error}")

    if interaction.response.is_done():
        await interaction.followup.send(
            "При выполнении команды произошла ошибка.",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "При выполнении команды произошла ошибка.",
            ephemeral=True
        )


if __name__ == "__main__":
    bot.run(TOKEN)