"""
FOOD REQUEST DISCORD BOT — AUTO DM ALL MEMBERS

DMs every member in the server on Sundays and Wednesdays, collects
comma-separated grocery requests, and pushes them to the Supplies
Tracker via Apps Script.

v3 changes:
- Control words: "stop" / "pause" / "resume" / "help" are handled as
  commands instead of being logged as grocery items. Opt-outs persist
  in Apps Script, so a redeploy doesn't re-subscribe people.
- Sends discord_user_id so rejection DMs can resolve users (the #0
  discriminator is dead and can't be looked up).
- Manager digest with real data: what came in today, plus everything
  still unpurchased, grouped by category and by person, with ages.
- All scheduling is timezone-aware. Railway runs UTC, so the naive
  datetime.now() was firing the 7pm DMs at noon Pacific.
"""

import asyncio
import os
import random
import re
from datetime import datetime, time as dtime
from threading import Thread
from zoneinfo import ZoneInfo

import discord
import requests
from discord.ext import commands, tasks
from flask import Flask, jsonify, request

# ========== CONFIGURATION ==========
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
APPS_SCRIPT_URL = os.getenv('APPS_SCRIPT_URL', 'YOUR_APPS_SCRIPT_WEB_APP_URL_HERE')
API_SECRET = os.getenv('API_SECRET', 'your_secret_key_here_change_this')
REJECTION_SECRET = os.getenv('REJECTION_SECRET', 'ATH_rejection_2025_secret')

# Reina's Discord User ID (for notifications and the digest)
REINA_USER_ID = int(os.getenv('MANAGER_ID', '194648306188681216'))

# Railway assigns the port. Hardcoding 8080 meant the /notify endpoint
# was unreachable whenever Railway picked something else.
FLASK_PORT = int(os.getenv('PORT', '8080'))

SUPPLIES_TRACKER_URL = "https://docs.google.com/spreadsheets/d/1HEyjrLRnenRwYeOgbvWMsdJJgrcV-GCuCOjD57brKO0/edit"

# Everything scheduled is pinned to this. Railway containers are UTC.
LOCAL_TZ = ZoneInfo("America/Los_Angeles")

REQUEST_DAYS = [6, 2]        # 6 = Sunday, 2 = Wednesday (Monday = 0)
REQUEST_HOUR = 19            # 7pm — collection prompt
DIGEST_HOUR = 21            # 9pm — manager digest, two hours later

DISCORD_LIMIT = 1900         # real cap is 2000; leave headroom

# ========== CONTROL WORDS ==========
# Matched against the WHOLE message, punctuation stripped — not as
# substrings. Otherwise "stop & shop bread" would opt someone out, and
# a request for "help yourself bars" would trigger the help text.
STOP_WORDS = {
    'stop', 'stfu', 'pause', 'unsubscribe', 'opt out', 'optout',
    'leave me alone', 'no thanks', 'no thank you', 'mute', 'quit',
    'remove me', 'take me off', 'unsub',
}
RESUME_WORDS = {
    'start', 'resume', 'opt in', 'optin', 'subscribe', 'unmute',
    'add me back', 'resubscribe', 'resub',
}
HELP_WORDS = {'help', 'commands', 'what', 'wtf', 'huh', 'info'}
# Bare punctuation normalizes to an empty string, so these are matched
# against the raw message instead.
HELP_RAW = {'?', '??', '???', '?!'}
SKIP_WORDS = {'skip', 'nothing', 'none', 'nope', 'no', 'im good', "i'm good", 'all good', 'pass'}


def normalize_control(text):
    """Lowercase, strip punctuation and extra spaces, for exact matching."""
    cleaned = re.sub(r"[^\w\s'’]", " ", text.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


# ========== FLASK WEB SERVER ==========
app = Flask(__name__)


@app.route('/notify', methods=['POST'])
def handle_rejection_notification():
    """Batched status updates pushed from Apps Script."""
    try:
        data = request.get_json()

        if data.get('secret') != REJECTION_SECRET:
            return jsonify({"success": False, "error": "Invalid secret"}), 401

        discord_user = data.get('discord_user')
        approved_items = data.get('approved', [])
        rejected_items = data.get('rejected', [])

        asyncio.run_coroutine_threadsafe(
            send_batched_update_dm(discord_user, approved_items, rejected_items),
            bot.loop
        )

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "online", "bot": "Food Request Bot"})


def run_flask():
    app.run(host='0.0.0.0', port=FLASK_PORT)


# ========== APPS SCRIPT CLIENT ==========

async def _post(payload):
    """POST to Apps Script off the event loop so the gateway keeps beating."""
    def _do():
        response = requests.post(APPS_SCRIPT_URL, json=payload, timeout=20)
        response.raise_for_status()
        return response.json()
    return await asyncio.to_thread(_do)


async def _get(params):
    def _do():
        response = requests.get(APPS_SCRIPT_URL, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    return await asyncio.to_thread(_do)


async def set_opt_out(user_id, opted_out):
    """Persist an opt-out in Apps Script's Script Properties."""
    try:
        return await _post({
            "secret": API_SECRET,
            "action": "optout" if opted_out else "optin",
            "discord_user_id": str(user_id),
        })
    except Exception as e:
        print(f"Failed to set opt-out for {user_id}: {e}")
        return {"success": False, "error": str(e)}


async def get_opt_outs():
    """Set of Discord IDs (strings) that asked not to be DM'd."""
    try:
        result = await _post({"secret": API_SECRET, "action": "optout_list"})
        return set(str(x) for x in result.get("opted_out", []))
    except Exception as e:
        print(f"Failed to fetch opt-outs, assuming none: {e}")
        return set()


async def fetch_summary():
    return await _get({"action": "summary", "secret": API_SECRET})


# ========== DIGEST FORMATTING ==========

def _age_label(entry):
    age = entry.get("age_days")
    if age is None:
        return ""
    if entry.get("stale"):
        return f" ⏳ {age}d"
    if age >= 7:
        return f" ({age}d)"
    return ""


def _urgency_marker(urgency):
    if not urgency:
        return ""
    flag = str(urgency).strip().lower()
    if flag in ("high", "urgent", "asap"):
        return " ‼️"
    if flag in ("low", "whenever"):
        return " (low)"
    return ""


def _render_by_person(by_person):
    lines = []
    for person, items in sorted(by_person.items(), key=lambda kv: (-len(kv[1]), kv[0].lower())):
        lines.append(f"**{person}** ({len(items)})")
        for entry in items:
            lines.append(
                f"• {entry.get('item', '?')}"
                f"{_urgency_marker(entry.get('urgency'))}"
                f"{_age_label(entry)}"
            )
        lines.append("")
    return lines


def _render_by_category(by_category):
    lines = []
    for category, items in sorted(by_category.items(), key=lambda kv: (-len(kv[1]), kv[0].lower())):
        names = ", ".join(entry.get("item", "?") for entry in items)
        lines.append(f"**{category}** ({len(items)}): {names}")
    return lines


def format_digest(data):
    if not data.get("ok"):
        return f"⚠️ couldn't build the digest: {data.get('error', 'unknown error')}"

    today = data.get("today", {})
    open_section = data.get("open", {})
    stale_days = data.get("stale_days", 21)

    lines = [
        "## 🧾 food request digest",
        f"_as of {data.get('generated_at', '?')}_",
        "",
        "### came in today",
    ]

    if today.get("count"):
        lines.append(f"{today['count']} items from {today.get('people', 0)} people")
        lines.append("")
        lines.extend(_render_by_person(today.get("by_person", {})))
    else:
        lines.append("_nothing yet today._")
        lines.append("")

    lines.append("### still unpurchased")
    if open_section.get("count"):
        header = f"{open_section['count']} open items from {open_section.get('people', 0)} people"
        stale_count = open_section.get("stale_count", 0)
        if stale_count:
            header += f" — {stale_count} older than {stale_days} days ⏳"
        lines.append(header)
        lines.append("")
        lines.append("**shopping list by aisle:**")
        lines.extend(_render_by_category(open_section.get("by_category", {})))
        lines.append("")
        lines.append("**by person:**")
        lines.extend(_render_by_person(open_section.get("by_person", {})))
        if stale_count:
            lines.append(f"_⏳ = open longer than {stale_days} days. "
                         f"run sweepStaleRequests() in Apps Script to clear them._")
    else:
        lines.append("_nothing open. the list is actually clear._")

    return "\n".join(lines).strip()


def chunk_message(text, limit=DISCORD_LIMIT):
    """Split on line breaks so a bullet never gets cut in half."""
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current.rstrip())
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            chunks.append(current.rstrip())
            current = line + "\n"
        else:
            current += line + "\n"
    if current.strip():
        chunks.append(current.rstrip())
    return chunks or ["_(empty)_"]


async def send_digest_to_manager():
    try:
        reina = await bot.fetch_user(REINA_USER_ID)
    except discord.HTTPException as e:
        print(f"Couldn't resolve manager {REINA_USER_ID}: {e}")
        return

    try:
        data = await fetch_summary()
    except Exception as e:
        print(f"Digest fetch failed: {e}")
        await reina.send(f"⚠️ digest fetch failed: `{e}`")
        return

    for chunk in chunk_message(format_digest(data)):
        await reina.send(chunk)
    print("Sent digest to manager")


# ========== BOT SETUP ==========
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.dm_messages = True
intents.guilds = True

bot = commands.Bot(command_prefix='!', intents=intents)

# user_id -> {items, duplicates, timestamp}
pending_confirmations = {}


async def send_batched_update_dm(discord_handle, approved_items, rejected_items):
    """Send a batched status update DM."""
    try:
        user = None
        handle = str(discord_handle or "").strip()

        if handle.isdigit():
            user = await bot.fetch_user(int(handle))
        else:
            # Legacy rows store a handle. Discriminators are gone, so match
            # on username alone and ignore any trailing #0.
            username = handle.split('#')[0].lower()
            for guild in bot.guilds:
                for member in guild.members:
                    if member.name.lower() == username:
                        user = member
                        break
                if user:
                    break

        if not user:
            print(f"❌ Could not find user: {discord_handle}")
            return

        parts = ["📊 **Your Request Update**\n",
                 "hey! reina reviewed your requests. here's what happened:\n"]

        if approved_items:
            parts.append("\n✅ **APPROVED/PURCHASED:**")
            for item in approved_items:
                parts.append(f"• {item}")

        if rejected_items:
            parts.append("\n❌ **NOT APPROVED:**")
            for rejection in rejected_items:
                item = rejection.get('item', 'Unknown item')
                reason = rejection.get('reason', 'No reason provided')
                parts.append(f"• {item} - {reason}")

        if not approved_items and not rejected_items:
            parts.append("\nno status changes for your items yet!")

        parts.append(f"\nif you have questions, talk to reina or check the "
                     f"[supplies tracker]({SUPPLIES_TRACKER_URL})!")

        await user.send("\n".join(parts))
        print(f"✅ Sent batched update to {discord_handle}: "
              f"{len(approved_items)} approved, {len(rejected_items)} rejected")

    except Exception as e:
        print(f"❌ Failed to send batched update DM: {e}")


@bot.event
async def on_ready():
    print('=' * 50)
    print('🤖 Food Request Bot v3.0 online')
    print(f'Bot: {bot.user}')
    print(f'Connected to {len(bot.guilds)} server(s)')
    for guild in bot.guilds:
        print(f'  - {guild.name} ({guild.member_count} members)')
    print(f'Timezone: {LOCAL_TZ}')
    print(f'Prompts: Sun & Wed at {REQUEST_HOUR}:00 local')
    print(f'Digest:  Sun & Wed at {DIGEST_HOUR}:00 local')
    print('Current vibe: cautiously optimistic')
    print('Powered by: caffeine and spite')
    print('=' * 50)
    if not scheduled_jobs.is_running():
        scheduled_jobs.start()


# Two fixed times, both timezone-aware. The old version polled hourly on
# a naive datetime, which under Railway's UTC clock meant the "7pm" DM
# went out at noon Pacific.
@tasks.loop(time=[
    dtime(hour=REQUEST_HOUR, minute=0, tzinfo=LOCAL_TZ),
    dtime(hour=DIGEST_HOUR, minute=0, tzinfo=LOCAL_TZ),
])
async def scheduled_jobs():
    now = datetime.now(LOCAL_TZ)
    if now.weekday() not in REQUEST_DAYS:
        return

    if now.hour == REQUEST_HOUR:
        print(f"Sending food request prompts at {now}")
        await send_dms_to_all_members()
    elif now.hour == DIGEST_HOUR:
        print(f"Sending manager digest at {now}")
        await send_digest_to_manager()


@scheduled_jobs.before_loop
async def _wait_ready():
    await bot.wait_until_ready()


WELCOME_MSG = f"""
🍌 FOOD REQUEST SZNNNN 🍌

hey! welcome to the server. i'm reina's food request bot.

**the deal:**
i'm here to collect everyone's food requests for our bi-weekly co-op orders. reina coded me at 3am fueled by pure spite and adderall.

**how to use me:**
i'll DM u every sunday & wednesday at 7pm. just reply with what u want separated by commas. that's literally it. i'm not complicated.

examples:
`grapes, kale, oat milk`
`those purple carrots, good bread, not the mid bread`
`anything chocolate, i'm going through it`

**important notes:**
• everything submitted through me is marked as **medium priority**
• for **high priority** items, add them manually to the [supplies tracker]({SUPPLIES_TRACKER_URL})
• house supplies (toilet paper, soap, etc) count too! don't wait till we're on our last roll :)

i'll add ur stuff to reina's spreadsheet and she'll try to order it. no mames guey.

**commands:**
• `!test` - check if i'm working
• `!request` - get the full food request prompt
• `!info` - see detailed instructions
• reply `stop` anytime and i'll stop DMing u

- ur local kitchen manager bot 💚
(powered by: chemistry homework procrastination)
"""


@bot.event
async def on_member_join(member):
    if member.bot:
        return
    try:
        await member.send(WELCOME_MSG)
        print(f"✅ Sent welcome message to new member: {member.name}")
    except Exception:
        print(f"❌ Couldn't send welcome message to {member.name}")


async def send_dms_to_all_members():
    """DM every non-bot member who hasn't opted out."""
    message = f"""
🍌 **Food Request Time!** 🍌

hey! time to submit ur grocery requests for the co-op order.

reply with items separated by commas:
`grapes, kale, oat milk, bread`

i'll add them to reina's tracker automatically as **medium priority**.

for high priority items or house supplies, add them manually: [supplies tracker]({SUPPLIES_TRACKER_URL})

orders go out soon so reply asap ‼️

_(`!info` for details, `!test` to check i'm working, or reply `stop` to opt out)_
"""

    if not bot.guilds:
        print("Bot is not in any servers!")
        return

    opted_out = await get_opt_outs()
    guild = bot.guilds[0]
    print(f"Sending bi-weekly DMs to members of '{guild.name}' "
          f"({len(opted_out)} opted out)...")

    sent = failed = skipped = 0

    for member in guild.members:
        if member.bot:
            continue
        if str(member.id) in opted_out:
            skipped += 1
            continue

        try:
            await member.send(message)
            print(f"  ✅ Sent to {member.name}")
            sent += 1
            await asyncio.sleep(1)  # rate limit
        except discord.Forbidden:
            print(f"  ❌ Can't DM {member.name} (DMs disabled)")
            failed += 1
        except Exception as e:
            print(f"  ❌ Failed to DM {member.name}: {e}")
            failed += 1

    print(f"\nDM Summary: {sent} sent, {failed} failed, {skipped} opted out")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if isinstance(message.channel, discord.DMChannel):
        await process_food_request(message)

    await bot.process_commands(message)


async def handle_control_word(message, normalized, raw=""):
    """
    Returns True if the message was a control word and has been handled.

    This runs BEFORE parsing, which is the whole point: someone replying
    "Stop" used to end up in the tracker as a grocery item.
    """
    if raw.strip() in HELP_RAW:
        normalized = 'help'

    if normalized in STOP_WORDS:
        await set_opt_out(message.author.id, True)
        await message.reply(
            "got it, i'll stop DMing u 🫡\n\n"
            "u can still send me items whenever u want, i just won't bug u.\n"
            "reply `resume` if u change ur mind."
        )
        print(f"Opted out: {message.author.name} ({message.author.id})")
        return True

    if normalized in RESUME_WORDS:
        await set_opt_out(message.author.id, False)
        await message.reply("ur back on the list 💚 see u sunday")
        print(f"Opted back in: {message.author.name} ({message.author.id})")
        return True

    if normalized in SKIP_WORDS:
        await message.reply(
            "no worries! nothing added.\n\n"
            "(if u want me to stop asking entirely, reply `stop`)"
        )
        return True

    if normalized in HELP_WORDS:
        await message.reply(
            "**how this works:**\n"
            "reply with groceries separated by commas: `grapes, kale, oat milk`\n\n"
            "**other stuff u can say:**\n"
            "• `stop` — i stop DMing u\n"
            "• `resume` — back on the list\n"
            "• `skip` — nothing this round\n"
            "• `!info` — the full manual\n"
            "• `!test` — check i'm alive"
        )
        return True

    return False


# Word-boundary matching, so "citric acid" and "coke zero" don't trigger.
DRUG_PATTERN = re.compile(
    r"\b(weed|edibles|shrooms|adderall|vyvanse|xanax|cocaine|"
    r"marijuana|thc)\b|\bmolly\b(?!['\u2019]s)",
    re.IGNORECASE,
)

DRUG_RESPONSES = [
    "bestie this is a GROCERY bot 😭\n\n(also ur on a berkeley co-op discord, we can see this)",
    "ma'am this is a wendy's\n\n(jk but like... wrong bot)",
    "i'm telling reina\n\n(jk i'm not a narc) (but maybe don't put this in writing)",
    "the FBI has entered the chat\n\n(jk they dgaf about berkeley students)",
    "added to cart ✅\n\n(jk i literally cannot do that) (this is a grocery bot) (go touch grass)",
]


async def process_food_request(message):
    content = message.content.strip()

    if not content:
        return
    if content.startswith('!'):
        return

    # ---- Pending duplicate confirmation ----
    if message.author.id in pending_confirmations:
        pending = pending_confirmations[message.author.id]
        if (datetime.now() - pending['timestamp']).total_seconds() < 300:
            if content.lower() in ('yes', 'y', 'yeah', 'yep', 'yes please'):
                await add_items_to_sheet(message, pending['items'], force=True)
                del pending_confirmations[message.author.id]
                return
            else:
                await message.reply("okay, cancelled! you can send new items anytime 💚")
                del pending_confirmations[message.author.id]
                return
        else:
            del pending_confirmations[message.author.id]

    # ---- Control words, before anything else ----
    normalized = normalize_control(content)
    if await handle_control_word(message, normalized, raw=content):
        return

    content_lower = content.lower()

    # ---- Easter eggs ----
    if DRUG_PATTERN.search(content_lower):
        await message.reply(random.choice(DRUG_RESPONSES))
        return

    if re.search(r"\bgrass\b", content_lower) and len(content.split(',')) == 1:
        await message.reply("bestie that's called salad 🥗\n\n(or are u telling me to go outside? valid tbh)")
        return

    if 'good vibes' in content_lower or re.fullmatch(r"vibes?", normalized or ""):
        await message.reply("added to cart ✨\n\n(jk but i respect the energy) (unfortunately i can only add physical items)")
        return

    if any(chain in content_lower for chain in ('dominos', "domino's", 'pizza hut', 'papa johns')):
        await message.reply("i tried to add a dominos integration\n\nreina said no 💔\n\n(she's right tho we have a food budget)")
        return

    if 'deez nuts' in content_lower or 'ligma' in content_lower:
        await message.reply("so funny 😐\n\nnow give me actual groceries or perish")
        return

    # ---- Parse items ----
    items = [item.strip() for item in content.split(',')]
    items = [item for item in items if item]

    if not items:
        await message.reply(
            "❌ bestie i literally cannot read this. try again but like... with actual items?\n\n"
            "example: `grapes, kale, bread`\n\n"
            "(reply `help` if ur stuck, or `stop` if u want me to leave u alone)"
        )
        return

    if len(items) > 20:
        await message.reply("okay gordon ramsay calm down 👨‍🍳\n\n(jk adding all of it but damn)")

    await add_items_to_sheet(message, items, force=False)


async def add_items_to_sheet(message, items, force=False):
    """Push items to the tracker. Sends the numeric ID so status DMs work."""
    try:
        payload = {
            "secret": API_SECRET,
            # No more "#0" — discriminators are dead. The ID is what
            # actually lets Apps Script address a DM back to this person.
            "discord_user": message.author.name,
            "discord_user_id": str(message.author.id),
            "items": items,
        }
        if force:
            payload["force"] = True

        result = await _post(payload)

        if result.get("success"):
            added = result.get("items", [])
            merged = result.get("merged", [])

            reply = []
            if added:
                reply.append("✅ **bet, added to the list:**")
                reply.extend(f"• {item}" for item in added)
            if merged:
                # The sheet already had these open, so we noted the extra
                # request on the existing row instead of duplicating it.
                reply.append("\n📌 **already on the list, noted u want it too:**")
                reply.extend(f"• {m.get('item')}" for m in merged)
            if not added and not merged:
                reply.append("hm, nothing got added. try again?")
            else:
                reply.append("\nreina will see this and hopefully remember to order it 🙏\n\nthanks bestie 💚")

            await message.reply("\n".join(reply))

            # Per-request ping to Reina, only for genuinely new items.
            if added:
                try:
                    reina = await bot.fetch_user(REINA_USER_ID)
                    items_list = "\n".join(f"• {item}" for item in added)
                    await reina.send(
                        f"🔔 **New food request from {message.author.name}:**\n{items_list}"
                    )
                except Exception as e:
                    print(f"Failed to notify Reina: {e}")

        elif result.get("error") == "duplicate_items" and not force:
            duplicates = result.get("duplicates", [])

            warning = ["⚠️ **heads up** - some items look like repeats:\n"]
            clean_items = []

            for item in items:
                dup = next(
                    (d for d in duplicates if d['item'].lower() == item.lower()),
                    None
                )
                if dup:
                    warning.append(f"• **{item}** - {dup['reason']}")
                else:
                    clean_items.append(item)

            warning.append("\ndo you still want to add them?")
            warning.append('• reply **"yes"** to add anyway')
            warning.append('• reply anything else to cancel')

            if clean_items:
                warning.append(f"\n_(these are fine: {', '.join(clean_items)})_")

            await message.reply("\n".join(warning))

            pending_confirmations[message.author.id] = {
                'items': items,
                'duplicates': duplicates,
                'timestamp': datetime.now(),
            }

        else:
            error = result.get("error", "Unknown error")
            await message.reply(
                "❌ something broke (not my fault) (probably reina's code) (jk love u reina)\n\n"
                "try again in a sec or yell at reina on discord\n\n"
                f"error for the nerds: {error}"
            )

    except Exception as e:
        print(f"Error submitting to Google Sheets: {e}")
        await message.reply(
            "❌ something broke (not my fault) (probably reina's code) (jk love u reina)\n\n"
            "try again in a sec or yell at reina on discord\n\n"
            f"error for the nerds: {e}"
        )


# ========== COMMANDS ==========

@bot.command(name='request')
async def manual_request(ctx):
    await ctx.author.send(f"""
🍌 FOOD REQUEST SZNNNN 🍌

bestie wake up it's time to tell me what groceries u want

**the deal:**
i'm reina's bot (she coded me at 3am fueled by pure spite and adderall) and i collect everyone's food requests for our bi-weekly co-op order

**how to use me:**
literally just reply with what u want separated by commas. that's it. i'm not complicated.

examples:
`grapes, kale, oat milk`
`those purple carrots, good bread, not the mid bread`
`anything chocolate, i'm going through it`

**important notes:**
• everything submitted through me is marked as **medium priority**
• for **high priority** items, add them manually to the [supplies tracker]({SUPPLIES_TRACKER_URL})
• house supplies (toilet paper, soap, etc) count too! don't wait till we're on our last roll :)

i'll add ur stuff to reina's spreadsheet and she'll try to order it. no mames guey.

orders go out irregularly so reply soon or ur eating air ‼️

**commands u can use:**
• `!test` - check if i'm working
• `!request` - get this message again
• `!info` - see the full manual
• reply `stop` - i'll stop DMing u

- ur local kitchen manager bot 💚
(powered by: chemistry homework procrastination)
""")


@bot.command(name='test')
async def test_command(ctx):
    if isinstance(ctx.channel, discord.DMChannel):
        await ctx.send("✅ yup i'm working! try sending: `grapes, kale` and i'll add it to the list")


@bot.command(name='info')
async def help_command(ctx):
    await ctx.send("""
📱 **reina's food request bot - user manual**

**what i do:**
collect ur food requests for the bi-weekly co-op order and add them to reina's tracker automatically

**how to use:**
1. i'll dm u every sun/wed at 7pm
2. reply with items: `grapes, kale, oat milk`
3. that's literally it

**stuff u can reply:**
• `stop` - i stop DMing u (u can still send items)
• `resume` - back on the DM list
• `skip` - nothing this round
• `help` - quick version of this

**commands:**
• `!test` - check if i'm working
• `!request` - manually trigger the food request prompt
• `!info` - ur reading it rn bestie

**created by:** reina (chem major, stressed)
**powered by:** coffee, chaos, and stackoverflow
**bug reports:** dm reina and she'll fix it (eventually) (maybe)

no i cannot order dominos. i tried. she said no. 💔
""")


@bot.command(name='stop')
async def stop_command(ctx):
    await set_opt_out(ctx.author.id, True)
    await ctx.send("got it, i'll stop DMing u 🫡 reply `resume` to come back")


@bot.command(name='resume')
async def resume_command(ctx):
    await set_opt_out(ctx.author.id, False)
    await ctx.send("ur back on the list 💚")


@bot.command(name='digest')
async def digest_command(ctx):
    """Manager digest, on demand."""
    if ctx.author.id != REINA_USER_ID:
        return  # silent — nobody else needs to know this exists
    await ctx.send("pulling it now...")
    await send_digest_to_manager()


@bot.command(name='optouts')
async def optouts_command(ctx):
    """Who has opted out."""
    if ctx.author.id != REINA_USER_ID:
        return

    opted_out = await get_opt_outs()
    if not opted_out:
        await ctx.send("nobody's opted out.")
        return

    names = []
    for raw_id in opted_out:
        try:
            user = await bot.fetch_user(int(raw_id))
            names.append(f"• {user.name}")
        except Exception:
            names.append(f"• (unknown: {raw_id})")

    await ctx.send(f"**{len(opted_out)} opted out:**\n" + "\n".join(names))


@bot.command(name='testdm')
async def test_dm_all(ctx):
    if ctx.author.id != REINA_USER_ID:
        await ctx.send("❌ Only Reina can use this command!")
        return
    await ctx.send("Sending test DMs to all members...")
    await send_dms_to_all_members()
    await ctx.send("Done!")


@bot.command(name='welcome')
async def send_welcome_to_all(ctx):
    if ctx.author.id != REINA_USER_ID:
        await ctx.send("❌ Only Reina can use this command!")
        return

    guild = ctx.guild
    if not guild:
        await ctx.send("❌ This command only works in a server!")
        return

    await ctx.send("Sending welcome messages to all members... this might take a minute")

    opted_out = await get_opt_outs()
    sent = failed = skipped = 0

    for member in guild.members:
        if member.bot:
            continue
        if str(member.id) in opted_out:
            skipped += 1
            continue
        try:
            await member.send(WELCOME_MSG)
            print(f"  ✅ Sent welcome to {member.name}")
            sent += 1
            await asyncio.sleep(1)
        except Exception:
            print(f"  ❌ Failed to send to {member.name}")
            failed += 1

    await ctx.send(f"✅ Done! Sent: {sent}, Failed: {failed}, Skipped (opted out): {skipped}")


@bot.command(name='testrequest')
async def test_request(ctx, *, items: str):
    """Test the request path as if it were a DM (Reina only).
    Usage: !testrequest grapes, kale, oat milk"""
    if ctx.author.id != REINA_USER_ID:
        await ctx.send("❌ Only Reina can use this command!")
        return

    class FakeMessage:
        def __init__(self, author, content):
            self.author = author
            self.content = content

        async def reply(self, content):
            await ctx.send(f"**Bot would reply:**\n{content}")

    fake_msg = FakeMessage(ctx.author, items)
    parsed = [i.strip() for i in items.split(',') if i.strip()]
    await add_items_to_sheet(fake_msg, parsed, force=False)


# ========== RUN BOT ==========
if __name__ == "__main__":
    print("Starting Food Request Bot...")
    print(f"Flask will listen on port {FLASK_PORT}")

    missing = [name for name, value in (
        ('DISCORD_BOT_TOKEN', DISCORD_BOT_TOKEN),
        ('APPS_SCRIPT_URL', APPS_SCRIPT_URL),
        ('API_SECRET', API_SECRET),
    ) if not value or value.startswith('YOUR_') or value.startswith('your_')]

    if missing:
        print(f"⚠️  Missing or placeholder env vars: {', '.join(missing)}")

    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    bot.run(DISCORD_BOT_TOKEN)
