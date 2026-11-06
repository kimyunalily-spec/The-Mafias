"""
THE MAFIAS — Telegram Mafia game bot
Requires: python-telegram-bot >= 20, < 23

Set the new bot token in an environment variable named BOT_TOKEN.
Example (Windows PowerShell):
    $env:BOT_TOKEN="9137842496:AAHH1oJvvTsiWh7REZ_xwQJz7UybaIilkXQ"
    python The mafias bot.py

Players must open the bot privately and press Start before /startgame.
The bot currently supports one active game at a time.
"""

import os
import random
import asyncio
import logging
from collections import Counter, defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is missing. Set the environment variable to your NEW "
        "BotFather token; do not put the token directly in this file."
    )

NIGHT_SECONDS = 35
DISCUSSION_SECONDS = 20
VOTING_SECONDS = 30
MIN_PLAYERS = 5

ROLE_EMOJI = {
    "Mafia": "🔪",
    "Doctor": "💉",
    "Detective": "🔍",
    "Bodyguard": "🛡️",
    "Tracker": "📡",
    "Vigilante": "🎯",
    "Mayor": "🗳️",
    "Villager": "👤",
}

game = {}


def reset_game(chat_id=None):
    """Create a clean game state."""
    global game
    game = {
        "created": False,
        "started": False,
        "chat_id": chat_id,
        "phase": "lobby",
        "day": 0,
        "players": [],             # [{"id": int, "name": str}]
        "roles": {},               # user_id -> role
        "alive": [],               # player dictionaries
        "dead": [],
        "night_actions": {},       # actor_id -> {"action": str, "target": int}
        "votes": {},               # voter_id -> target_id
        "mayor_revealed": set(),
        "vigilante_shots": set(),  # vigilantes who already used their shot
        "phase_task": None,
    }


reset_game()


def player_by_id(user_id):
    return next((p for p in game["players"] if p["id"] == user_id), None)


def alive_player(user_id):
    return next((p for p in game["alive"] if p["id"] == user_id), None)


def role_of(user_id):
    return game["roles"].get(user_id, "Unknown")


def alive_ids():
    return {p["id"] for p in game["alive"]}


def display_name(user_id):
    player = player_by_id(user_id)
    return player["name"] if player else "Unknown player"


def get_roles(player_count):
    """Role distribution; returns exactly player_count roles."""
    if player_count < MIN_PLAYERS:
        return []

    if player_count <= 5:
        roles = ["Mafia", "Doctor", "Detective", "Villager", "Villager"]
    elif player_count <= 7:
        roles = [
            "Mafia", "Doctor", "Detective", "Bodyguard",
            "Villager", "Villager", "Villager",
        ]
    elif player_count <= 10:
        roles = [
            "Mafia", "Mafia", "Doctor", "Detective", "Bodyguard",
            "Vigilante", "Villager", "Villager", "Villager", "Villager",
        ]
    else:
        roles = [
            "Mafia", "Mafia", "Mafia", "Doctor", "Doctor",
            "Detective", "Bodyguard", "Tracker", "Mayor", "Vigilante",
            "Villager", "Villager", "Villager", "Villager", "Villager",
        ]
    return roles[:player_count]


def target_keyboard(action, actor_id, allow_self=False):
    buttons = []
    for person in game["alive"]:
        if not allow_self and person["id"] == actor_id:
            continue
        buttons.append([
            InlineKeyboardButton(
                person["name"][:60],
                callback_data=f"night:{action}:{person['id']}",
            )
        ])
    return InlineKeyboardMarkup(buttons)


async def safe_send(context, user_id, text, reply_markup=None):
    try:
        await context.bot.send_message(
            chat_id=user_id, text=text, reply_markup=reply_markup
        )
        return True
    except Exception:
        log.exception("Could not DM user %s", user_id)
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎮 Welcome to The Mafias!\n\n"
        "Open this bot in a private chat and press Start so it can DM you "
        "your role during a game.\n\n"
        "Group commands:\n"
        "/create — create a game\n"
        "/join — join the lobby\n"
        "/leave — leave before the game starts\n"
        "/players — list lobby players\n"
        "/startgame — assign roles and begin\n"
        "/reveal — Mayor reveals themselves\n"
        "/roles — full role guide"
    )


async def create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if game["started"]:
        await update.message.reply_text("A game is already running.")
        return
    reset_game(update.effective_chat.id)
    game["created"] = True
    await update.message.reply_text(
        "🎮 New Mafia Game Created!\n\nUse /join to participate. "
        f"At least {MIN_PLAYERS} players are required."
    )


async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not game["created"] or game["started"]:
        await update.message.reply_text("Create a lobby first with /create.")
        return
    user = update.effective_user
    if player_by_id(user.id):
        await update.message.reply_text("⚠️ You already joined.")
        return
    game["players"].append({"id": user.id, "name": user.first_name})
    await update.message.reply_text(
        f"✅ {user.first_name} joined!\nPlayers: {len(game['players'])}"
    )


async def leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if game["started"]:
        await update.message.reply_text("You cannot leave after the game starts.")
        return
    user = update.effective_user
    person = player_by_id(user.id)
    if not person:
        await update.message.reply_text("You aren't in the game.")
        return
    game["players"].remove(person)
    await update.message.reply_text(f"❌ {person['name']} left the game.")


async def players(update: Update, context: ContextTypes.DEFAULT_TYPE):
    roster = game["alive"] if game["started"] else game["players"]
    if not roster:
        await update.message.reply_text("No players to show.")
        return
    heading = "👥 Living players:" if game["started"] else "👥 Lobby players:"
    lines = [f"{i}. {p['name']}" for i, p in enumerate(roster, 1)]
    await update.message.reply_text(heading + "\n\n" + "\n".join(lines))


async def send_role_dms(context):
    """Send every role before starting the first night."""
    failed = []
    for person in game["players"]:
        role = role_of(person["id"])
        message = (
            f"🎭 Your secret role is: {ROLE_EMOJI.get(role, '')} {role}\n\n"
            f"Game: The Mafias\n"
        )
        if role == "Mafia":
            teammates = [
                p["name"] for p in game["players"]
                if p["id"] != person["id"] and role_of(p["id"]) == "Mafia"
            ]
            message += "Your fellow Mafia: " + (
                ", ".join(teammates) if teammates else "None"
            )
        elif role == "Mayor":
            message += "Use /reveal in the group during the day to make "
            message += "your vote count as 2."
        elif role == "Vigilante":
            message += "You have one night shot for the entire game."
        else:
            message += "Keep your identity secret. Use your ability when prompted."
        if not await safe_send(context, person["id"], message):
            failed.append(person["name"])
    return failed


async def startgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not game["created"] or game["started"]:
        await update.message.reply_text("Create a new lobby first with /create.")
        return
    if len(game["players"]) < MIN_PLAYERS:
        await update.message.reply_text(
            f"❌ Need at least {MIN_PLAYERS} players."
        )
        return

    role_list = get_roles(len(game["players"]))
    if len(role_list) != len(game["players"]):
        await update.message.reply_text(
            "This player count does not have a configured role distribution."
        )
        return

    random.shuffle(role_list)
    game["roles"] = {
        person["id"]: role for person, role in zip(game["players"], role_list)
    }

    # Test private delivery before changing the lobby into a running game.
    failed = await send_role_dms(context)
    if failed:
        await update.message.reply_text(
            "❌ I couldn't DM: " + ", ".join(failed) + ".\n"
            "Each player must open the bot privately, press Start, then "
            "the host can run /startgame again."
        )
        game["roles"] = {}
        return

    game["started"] = True
    game["phase"] = "night"
    game["alive"] = list(game["players"])
    game["dead"] = []
    game["day"] = 1
    await update.message.reply_text(
        "🎮 Game Started!\nAll roles have been sent privately."
    )
    await begin_night(context)


async def begin_night(context):
    if not game["started"]:
        return
    game["phase"] = "night"
    game["night_actions"] = {}
    await context.bot.send_message(
        game["chat_id"],
        f"🌙 NIGHT {game['day']}\n\n"
        f"The town falls asleep...\nSpecial roles have {NIGHT_SECONDS} "
        "seconds to act. Check your private messages."
    )
    await send_night_actions(context)
    schedule_phase(context, resolve_night, NIGHT_SECONDS)


async def send_night_actions(context):
    for person in list(game["alive"]):
        user_id = person["id"]
        role = role_of(user_id)

        prompts = {
            "Mafia": ("kill", "🔪 Who would you like to eliminate tonight?", False),
            "Doctor": ("save", "💉 Who would you like to protect tonight?", True),
            "Detective": (
                "investigate",
                "🔍 Who would you like to investigate tonight?", False
            ),
            "Bodyguard": ("guard", "🛡️ Who would you like to guard tonight?", False),
            "Tracker": ("track", "📡 Who would you like to track tonight?", False),
        }

        if role in prompts:
            action, prompt, allow_self = prompts[role]
            await safe_send(
                context,
                user_id,
                prompt,
                target_keyboard(action, user_id, allow_self=allow_self),
            )
        elif role == "Vigilante":
            if user_id in game["vigilante_shots"]:
                await safe_send(context, user_id, "🎯 You have already used your shot.")
            else:
                await safe_send(
                    context,
                    user_id,
                    "🎯 Choose a player to shoot tonight, or skip your shot.",
                    InlineKeyboardMarkup([
                        *target_keyboard("shoot", user_id).inline_keyboard,
                        [InlineKeyboardButton("Skip", callback_data="night:skip:0")],
                    ]),
                )


def schedule_phase(context, coroutine, seconds):
    old_task = game.get("phase_task")
    if old_task and not old_task.done():
        old_task.cancel()
    game["phase_task"] = asyncio.create_task(coroutine(context, seconds))


async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data or ""

    if data.startswith("night:"):
        if game["phase"] != "night" or not alive_player(user_id):
            await query.answer("This night action is no longer available.", show_alert=True)
            return
        try:
            _, action, target_text = data.split(":")
            target_id = int(target_text)
        except (ValueError, TypeError):
            return

        role = role_of(user_id)
        allowed = {
            "Mafia": "kill",
            "Doctor": "save",
            "Detective": "investigate",
            "Bodyguard": "guard",
            "Tracker": "track",
            "Vigilante": "shoot",
        }
        if action == "skip" and role == "Vigilante":
            game["night_actions"][user_id] = {"action": "skip", "target": None}
            await query.edit_message_text("You skipped your shot tonight.")
            return
        if allowed.get(role) != action:
            await query.answer("That action does not belong to your role.", show_alert=True)
            return
        if target_id not in alive_ids():
            await query.answer("That player is no longer alive.", show_alert=True)
            return
        if target_id == user_id and action not in ("save",):
            await query.answer("You cannot select yourself.", show_alert=True)
            return
        if action == "shoot" and user_id in game["vigilante_shots"]:
            await query.answer("You have already used your shot.", show_alert=True)
            return

        game["night_actions"][user_id] = {
            "action": action, "target": target_id
        }
        labels = {
            "kill": "Mafia target selected.",
            "save": "Protection selected.",
            "investigate": "Investigation target selected.",
            "guard": "Guard target selected.",
            "track": "Tracking target selected.",
            "shoot": "Shot target selected.",
        }
        await query.edit_message_text("✅ " + labels[action])
        if action == "investigate":
            result = "IS Mafia." if role_of(target_id) == "Mafia" else "is NOT Mafia."
            await safe_send(
                context, user_id,
                f"🔍 Investigation result for {display_name(target_id)}: {result}"
            )
        elif action == "track":
            await safe_send(
                context, user_id,
                f"📡 You are tracking {display_name(target_id)}. "
                "The result will arrive after the night."
            )
        return

    if data.startswith("vote:"):
        if game["phase"] != "voting" or not alive_player(user_id):
            await query.answer("Voting is closed.", show_alert=True)
            return
        try:
            target_id = int(data.split(":", 1)[1])
        except ValueError:
            return
        if target_id not in alive_ids() or target_id == user_id:
            await query.answer("Choose a living player other than yourself.", show_alert=True)
            return
        game["votes"][user_id] = target_id
        await query.edit_message_text(
            f"✅ Your vote for {display_name(target_id)} has been recorded."
        )


async def resolve_night(context, seconds):
    await asyncio.sleep(seconds)
    if not game["started"] or game["phase"] != "night":
        return

    actions = game["night_actions"]
    mafia_votes = [
        item["target"] for actor, item in actions.items()
        if role_of(actor) == "Mafia" and item["action"] == "kill"
        and item["target"] is not None
    ]
    kill_target = Counter(mafia_votes).most_common(1)[0][0] if mafia_votes else None

    doctor_targets = {
        item["target"] for actor, item in actions.items()
        if role_of(actor) == "Doctor" and item["action"] == "save"
    }
    guard_targets = {
        actor: item["target"] for actor, item in actions.items()
        if role_of(actor) == "Bodyguard" and item["action"] == "guard"
    }
    tracker_actions = [
        (actor, item["target"]) for actor, item in actions.items()
        if role_of(actor) == "Tracker" and item["action"] == "track"
    ]

    deaths = set()
    if kill_target is not None and kill_target not in doctor_targets:
        guarding = next(
            (guard_id for guard_id, target in guard_targets.items()
             if target == kill_target and alive_player(guard_id)),
            None,
        )
        if guarding is not None:
            deaths.add(guarding)
        else:
            deaths.add(kill_target)

    # A vigilante shot is independent of the Mafia attack.
    for actor, item in actions.items():
        if role_of(actor) == "Vigilante" and item["action"] == "shoot":
            if item["target"] is not None:
                deaths.add(item["target"])
                game["vigilante_shots"].add(actor)

    deaths &= alive_ids()
    for user_id in list(deaths):
        person = alive_player(user_id)
        if person:
            game["alive"].remove(person)
            game["dead"].append(person)

    # Tracker learns the target's recorded night visit, if any.
    for tracker_id, tracked_id in tracker_actions:
        visit = actions.get(tracked_id)
        if not visit or visit["action"] in ("skip",):
            result = "They did not make a recorded visit."
        else:
            result = f"They visited {display_name(visit['target'])}."
        await safe_send(
            context, tracker_id,
            f"📡 Tracking result for {display_name(tracked_id)}:\n{result}"
        )

    if deaths:
        lines = ["☀️ MORNING — DAY " + str(game["day"]), ""]
        for user_id in deaths:
            lines.append(
                f"💀 {display_name(user_id)} was found dead.\n"
                f"Role: {ROLE_EMOJI.get(role_of(user_id), '')} {role_of(user_id)}"
            )
        await context.bot.send_message(game["chat_id"], "\n\n".join(lines))
    else:
        await context.bot.send_message(
            game["chat_id"],
            f"☀️ MORNING — DAY {game['day']}\n\n"
            "No one died during the night."
        )

    if await check_win(context):
        return
    await begin_discussion(context)


async def begin_discussion(context):
    game["phase"] = "discussion"
    await context.bot.send_message(
        game["chat_id"],
        "🗣️ DISCUSSION PHASE\n\n"
        f"Discuss who you suspect. Voting begins in {DISCUSSION_SECONDS} seconds."
    )
    schedule_phase(context, begin_voting_after_delay, DISCUSSION_SECONDS)


async def begin_voting_after_delay(context, seconds):
    await asyncio.sleep(seconds)
    if not game["started"] or game["phase"] != "discussion":
        return
    await begin_voting(context)


async def begin_voting(context):
    game["phase"] = "voting"
    game["votes"] = {}
    buttons = []
    for person in game["alive"]:
        buttons.append([
            InlineKeyboardButton(
                person["name"][:60],
                callback_data=f"vote:{person['id']}",
            )
        ])
    await context.bot.send_message(
        game["chat_id"],
        "🗳️ VOTING PHASE\n\nChoose a living player to eliminate. "
        f"You have {VOTING_SECONDS} seconds.",
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )
    schedule_phase(context, finish_voting, VOTING_SECONDS)


async def finish_voting(context, seconds):
    await asyncio.sleep(seconds)
    if not game["started"] or game["phase"] != "voting":
        return

    counts = defaultdict(int)
    for voter_id, target_id in game["votes"].items():
        if voter_id not in alive_ids() or target_id not in alive_ids():
            continue
        counts[target_id] += 2 if voter_id in game["mayor_revealed"] else 1

    if not counts:
        await context.bot.send_message(
            game["chat_id"], "🗳️ No valid votes were cast. Nobody was eliminated."
        )
    else:
        ranking = sorted(counts.items(), key=lambda pair: pair[1], reverse=True)
        highest = ranking[0][1]
        winners = [target for target, count in ranking if count == highest]
        tally = "\n".join(
            f"{display_name(target)} = {count} vote(s)"
            for target, count in sorted(
                counts.items(), key=lambda pair: pair[1], reverse=True
            )
        )
        if len(winners) > 1:
            await context.bot.send_message(
                game["chat_id"],
                "🗳️ VOTE RESULTS\n\n" + tally +
                "\n\n⚖️ It's a tie. Nobody was eliminated."
            )
        else:
            target_id = winners[0]
            person = alive_player(target_id)
            if person:
                game["alive"].remove(person)
                game["dead"].append(person)
            await context.bot.send_message(
                game["chat_id"],
                "🗳️ VOTE RESULTS\n\n" + tally +
                f"\n\n💀 {display_name(target_id)} has been voted out.\n"
                f"Role: {ROLE_EMOJI.get(role_of(target_id), '')} {role_of(target_id)}"
            )

    if await check_win(context):
        return
    game["day"] += 1
    await begin_night(context)


async def check_win(context):
    if not game["started"]:
        return True
    mafia_alive = sum(1 for p in game["alive"] if role_of(p["id"]) == "Mafia")
    town_alive = len(game["alive"]) - mafia_alive

    if mafia_alive == 0:
        await end_game(context, "🏆 TOWN WINS")
        return True
    # Mafia wins when they reach parity with or outnumber all non-Mafia players.
    if mafia_alive >= town_alive:
        await end_game(context, "🏆 MAFIA WINS")
        return True
    return False


async def end_game(context, result):
    game["phase"] = "ended"
    game["started"] = False
    task = game.get("phase_task")
    if task and not task.done() and task is not asyncio.current_task():
        task.cancel()

    lines = [result, "", "🎭 FINAL ROLE REVEAL", ""]
    for person in game["players"]:
        status = "💀 Dead" if person in game["dead"] else "💚 Alive"
        role = role_of(person["id"])
        lines.append(
            f"{person['name']} — {ROLE_EMOJI.get(role, '')} {role} ({status})"
        )
    await context.bot.send_message(game["chat_id"], "\n".join(lines))
    await context.bot.send_message(
        game["chat_id"],
        "Game over! Use /create to start another game."
    )


async def reveal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not game["started"] or game["phase"] not in ("discussion", "voting"):
        await update.message.reply_text(
            "The Mayor can reveal themselves during the day."
        )
        return
    if not alive_player(user_id) or role_of(user_id) != "Mayor":
        await update.message.reply_text("Only the living Mayor can use /reveal.")
        return
    if user_id in game["mayor_revealed"]:
        await update.message.reply_text("You have already revealed yourself.")
        return
    game["mayor_revealed"].add(user_id)
    await update.message.reply_text(
        f"🗳️ {display_name(user_id)} has revealed themselves as the MAYOR!\n"
        "Their vote now counts as 2."
    )


async def roles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎭 THE MAFIAS — ROLE GUIDE\n\n"
        "🔪 MAFIA: Vote secretly for a player to eliminate each night.\n\n"
        "💉 DOCTOR: Protect one living player each night. A successful "
        "protection prevents the Mafia attack.\n\n"
        "🔍 DETECTIVE: Investigate one player each night; learn privately "
        "whether they are Mafia.\n\n"
        "🛡️ BODYGUARD: Guard another player. If the Mafia attacks them, "
        "the Bodyguard takes the attack instead.\n\n"
        "📡 TRACKER: Learn the recorded night visit of one player.\n\n"
        "🎯 VIGILANTE: Has one shot for the entire game and may shoot "
        "one player at night.\n\n"
        "🗳️ MAYOR: Use /reveal during the day; your vote then counts twice.\n\n"
        "👤 VILLAGER: No night power. Discuss and vote to find the Mafia.\n\n"
        "The dead player's role is announced when they die. All roles are "
        "revealed again when the game ends."
    )


async def roleinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await roles(update, context)


async def cancelgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not game["created"] and not game["started"]:
        await update.message.reply_text("There is no active game.")
        return
    task = game.get("phase_task")
    if task and not task.done():
        task.cancel()
    chat_id = update.effective_chat.id
    reset_game(chat_id)
    await update.message.reply_text("🛑 Game cancelled. Use /create to start again.")


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("create", create))
    app.add_handler(CommandHandler("join", join))
    app.add_handler(CommandHandler("leave", leave))
    app.add_handler(CommandHandler("players", players))
    app.add_handler(CommandHandler("startgame", startgame))
    app.add_handler(CommandHandler("reveal", reveal))
    app.add_handler(CommandHandler("roles", roles))
    app.add_handler(CommandHandler("roleinfo", roleinfo))
    app.add_handler(CommandHandler("cancelgame", cancelgame))
    app.add_handler(CallbackQueryHandler(button_click))

    log.info("🎮 The Mafias bot is starting")
    app.run_polling()


if __name__ == "__main__":
    main()
