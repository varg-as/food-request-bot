"""
FOOD REQUEST DISCORD BOT - AUTO DM ALL MEMBERS

This bot DMs ALL members in your server on Sundays and Wednesdays.
No need to manually add user IDs!

FEATURES:
- Automatic DMs to everyone in the server (non-bots)
- Simple comma-separated input
- Auto-populates Google Sheet via Apps Script
"""

import discord
from discord.ext import commands, tasks
import requests
import json
from datetime import datetime, time
import asyncio
import os
import random
from threading import Thread
from flask import Flask, request, jsonify

# ========== CONFIGURATION ==========
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
APPS_SCRIPT_URL = os.getenv('APPS_SCRIPT_URL', 'YOUR_APPS_SCRIPT_WEB_APP_URL_HERE')
API_SECRET = os.getenv('API_SECRET', 'your_secret_key_here_change_this')

# Reina's Discord User ID (for notifications)
REINA_USER_ID = 194648306188681216  # Replace with your actual Discord user ID

# Rejection notification secret
REJECTION_SECRET = "ATH_rejection_2025_secret"

# Google Sheet URL
SUPPLIES_TRACKER_URL = "https://docs.google.com/spreadsheets/d/1HEyjrLRnenRwYeOgbvWMsdJJgrcV-GCuCOjD57brKO0/edit"

# Schedule: Sunday and Wednesday at 7 PM
REQUEST_DAYS = [6, 2]  # 6 = Sunday, 2 = Wednesday (0 = Monday)
REQUEST_TIME = time(19, 0)  # 7:00 PM (use 24-hour format)

# Summary schedule: Monday 9 AM and Wednesday 9 PM
SUMMARY_SCHEDULE = [
    (0, 9),   # Monday at 9 AM
    (2, 21),  # Wednesday at 9 PM (21:00)
]

# ========== FLASK WEB SERVER ==========
app = Flask(__name__)

@app.route('/notify', methods=['POST'])
def handle_rejection_notification():
    """Handle batched status update notifications from Google Sheets"""
    try:
        data = request.get_json()
        
        # Verify secret
        if data.get('secret') != REJECTION_SECRET:
            return jsonify({"success": False, "error": "Invalid secret"}), 401
        
        discord_user = data.get('discord_user')  # e.g., "username#1234"
        approved_items = data.get('approved', [])  # List of approved items
        rejected_items = data.get('rejected', [])  # List of {item, reason} objects
        
        # Send the batched notification via Discord bot
        asyncio.run_coroutine_threadsafe(
            send_batched_update_dm(discord_user, approved_items, rejected_items),
            bot.loop
        )
        
        return jsonify({"success": True})
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "online", "bot": "Food Request Bot"})

def run_flask():
    """Run Flask in a separate thread"""
    app.run(host='0.0.0.0', port=8080)

async def send_batched_update_dm(discord_handle, approved_items, rejected_items):
    """Send batched status update DM to user"""
    try:
        # Find user by username#discriminator
        username, discriminator = discord_handle.split('#')
        
        # Search through all guild members
        user = None
        for guild in bot.guilds:
            for member in guild.members:
                if member.name == username and member.discriminator == discriminator:
                    user = member
                    break
            if user:
                break
        
        if not user:
            print(f"❌ Could not find user: {discord_handle}")
            return
        
        # Build message
        message_parts = ["📊 **Your Request Update**\n"]
        message_parts.append("hey! reina reviewed your requests. here's what happened:\n")
        
        # Approved items
        if approved_items and len(approved_items) > 0:
            message_parts.append("\n✅ **APPROVED/PURCHASED:**")
            for item in approved_items:
                message_parts.append(f"• {item}")
        
        # Rejected items
        if rejected_items and len(rejected_items) > 0:
            message_parts.append("\n❌ **NOT APPROVED:**")
            for rejection in rejected_items:
                item = rejection.get('item', 'Unknown item')
                reason = rejection.get('reason', 'No reason provided')
                message_parts.append(f"• {item} - {reason}")
        
        # No changes
        if (not approved_items or len(approved_items) == 0) and (not rejected_items or len(rejected_items) == 0):
            message_parts.append("\nno status changes for your items yet!")
        
        message_parts.append(f"\nif you have questions, talk to reina or check the [supplies tracker]({SUPPLIES_TRACKER_URL})!")
        
        message = "\n".join(message_parts)
        
        # Send DM
        await user.send(message)
        print(f"✅ Sent batched update to {discord_handle}: {len(approved_items)} approved, {len(rejected_items)} rejected")
        
    except Exception as e:
        print(f"❌ Failed to send batched update DM: {e}")

# ========== BOT SETUP ==========
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.dm_messages = True
intents.guilds = True

bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print('='*50)
    print('🤖 Food Request Bot v2.0 online')
    print(f'Bot: {bot.user}')
    print(f'Connected to {len(bot.guilds)} server(s)')
    for guild in bot.guilds:
        print(f'  - {guild.name} ({guild.member_count} members)')
    print(f'Scheduled DMs: Sundays & Wednesdays at {REQUEST_TIME}')
    print('Current vibe: cautiously optimistic')
    print('Powered by: caffeine and spite')
    print('='*50)
    send_request_prompts.start()

@bot.event
async def on_member_join(member):
    """Send welcome message to new members"""
    if member.bot:
        return
    
    welcome_msg = f"""
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

- ur local kitchen manager bot 💚
(powered by: chemistry homework procrastination)
    """
    
    try:
        await member.send(welcome_msg)
        print(f"✅ Sent welcome message to new member: {member.name}")
    except:
        print(f"❌ Couldn't send welcome message to {member.name}")

@tasks.loop(hours=1)
async def send_request_prompts():
    """Check if it's time to send DM prompts OR summaries"""
    now = datetime.now()
    
    # Check if it's time for DM prompts
    if now.weekday() in REQUEST_DAYS and now.hour == REQUEST_TIME.hour:
        print(f"Sending food request prompts at {now}")
        await send_dms_to_all_members()
    
    # Check if it's time for summary
    for day, hour in SUMMARY_SCHEDULE:
        if now.weekday() == day and now.hour == hour:
            print(f"Sending biweekly summary at {now}")
            await send_summary_to_reina()
            break

async def send_summary_to_reina():
    """Send a summary of recent requests to Reina"""
    try:
        # Get Reina's DM
        reina = await bot.fetch_user(REINA_USER_ID)
        
        # Count how many requests were submitted (you could track this in a global variable)
        # For now, just send a simple summary
        now = datetime.now()
        day_name = now.strftime("%A")
        time_of_day = "morning" if now.hour < 12 else "night"
        
        summary = f"""
📊 **Biweekly Food Request Summary** - {day_name} {time_of_day}

check out what people requested: [supplies tracker]({SUPPLIES_TRACKER_URL})

don't forget to review and order soon! 🛒

tip: sort by "requested" status to see what's new 💚
        """
        
        await reina.send(summary)
        print(f"✅ Sent summary to Reina")
        
    except Exception as e:
        print(f"❌ Failed to send summary: {e}")

async def send_dms_to_all_members():
    """Send DM to ALL members in the server (excluding bots) - SHORT VERSION"""
    message = f"""
🍌 **Food Request Time!** 🍌

hey! time to submit ur grocery requests for the co-op order.

reply with items separated by commas:
`grapes, kale, oat milk, bread`

i'll add them to reina's tracker automatically as **medium priority**.

for high priority items or house supplies, add them manually: [supplies tracker]({SUPPLIES_TRACKER_URL})

orders go out soon so reply asap ‼️

_(type `!info` for more details or `!test` to check if i'm working)_
    """
    
    # Get the first guild (your server)
    if not bot.guilds:
        print("Bot is not in any servers!")
        return
    
    guild = bot.guilds[0]
    print(f"Sending bi-weekly DMs to members of '{guild.name}'...")
    
    sent = 0
    failed = 0
    
    for member in guild.members:
        # Skip bots
        if member.bot:
            continue
        
        try:
            await member.send(message)
            print(f"  ✅ Sent to {member.name}")
            sent += 1
            await asyncio.sleep(1)  # Rate limit: 1 second between DMs
        except discord.Forbidden:
            print(f"  ❌ Can't DM {member.name} (DMs disabled)")
            failed += 1
        except Exception as e:
            print(f"  ❌ Failed to DM {member.name}: {e}")
            failed += 1
    
    print(f"\nDM Summary: {sent} sent, {failed} failed")

@bot.event
async def on_message(message):
    # Ignore bot's own messages
    if message.author == bot.user:
        return
    
    # Only process DMs
    if isinstance(message.channel, discord.DMChannel):
        # Accept requests from anyone who DMs the bot
        await process_food_request(message)
    
    await bot.process_commands(message)

async def process_food_request(message):
    """Process a food request from a DM"""
    content = message.content.strip()
    
    # Skip if it's a command
    if content.startswith('!'):
        return
    
    # EASTER EGGS - check before processing
    content_lower = content.lower()
    
    # Drug jokes
    drug_keywords = ['weed', 'edibles', 'molly', 'acid', 'shrooms', 'adderall', 'vyvanse', 
                     'xanax', 'cocaine', 'coke', 'drugs', 'marijuana', 'thc', 'cbd oil']
    if any(keyword in content_lower for keyword in drug_keywords):
        responses = [
            "bestie this is a GROCERY bot 😭\n\n(also ur on a berkeley co-op discord, we can see this)",
            "ma'am this is a wendy's\n\n(jk but like... wrong bot)",
            "i'm telling reina\n\n(jk i'm not a narc) (but maybe don't put this in writing)",
            "the FBI has entered the chat\n\n(jk they dgaf about berkeley students)",
            "added to cart ✅\n\n(jk i literally cannot do that) (this is a grocery bot) (go touch grass)"
        ]
        await message.reply(random.choice(responses))
        return
    
    # Grass joke
    if 'grass' in content_lower and len(content.split(',')) == 1:
        await message.reply("bestie that's called salad 🥗\n\n(or are u telling me to go outside? valid tbh)")
        return
    
    # Good vibes
    if 'good vibes' in content_lower or 'vibes' in content_lower:
        await message.reply("added to cart ✨\n\n(jk but i respect the energy) (unfortunately i can only add physical items)")
        return
    
    # Dominos/pizza delivery
    if 'dominos' in content_lower or 'pizza hut' in content_lower or 'papa johns' in content_lower:
        await message.reply("i tried to add a dominos integration\n\nreina said no 💔\n\n(she's right tho we have a food budget)")
        return
    
    # Someone being a menace
    if 'deez nuts' in content_lower or 'ligma' in content_lower:
        await message.reply("so funny 😐\n\nnow give me actual groceries or perish")
        return
    
    # Parse items (comma-separated)
    items = [item.strip() for item in content.split(',')]
    items = [item for item in items if item]  # Remove empty strings
    
    # Too many items
    if len(items) > 20:
        await message.reply("okay gordon ramsay calm down 👨‍🍳\n\n(jk adding all of it but damn)")
    
    if not items:
        await message.reply("❌ bestie i literally cannot read this. try again but like... with actual items?\n\nexample: `grapes, kale, bread`\n\n(i'm just a bot i can't do critical thinking 😭)")
        return
    
    # Send to Google Sheets via Apps Script
    try:
        discord_handle = f"{message.author.name}#{message.author.discriminator}"
        
        response = requests.post(APPS_SCRIPT_URL, json={
            "secret": API_SECRET,
            "discord_user": discord_handle,
            "items": items
        }, timeout=10)
        
        result = response.json()
        
        if result.get("success"):
            items_list = "\n".join([f"• {item}" for item in items])
            await message.reply(f"✅ **bet, added to the list:**\n{items_list}\n\nreina will see this and hopefully remember to order it 🙏\n\nthanks bestie 💚")
            
            # Notify Reina
            try:
                reina = await bot.fetch_user(REINA_USER_ID)
                notification = f"🔔 **New food request from {message.author.name}:**\n{items_list}"
                await reina.send(notification)
            except Exception as e:
                print(f"Failed to notify Reina: {e}")
        else:
            error = result.get("error", "Unknown error")
            await message.reply(f"❌ something broke (not my fault) (probably reina's code) (jk love u reina)\n\ntry again in a sec or yell at reina on discord\n\nerror for the nerds: {error}")
            
    except Exception as e:
        print(f"Error submitting to Google Sheets: {e}")
        await message.reply(f"❌ something broke (not my fault) (probably reina's code) (jk love u reina)\n\ntry again in a sec or yell at reina on discord\n\nerror for the nerds: {str(e)}")

# ========== MANUAL COMMANDS ==========

@bot.command(name='request')
async def manual_request(ctx):
    """Allow anyone to manually trigger request prompt - FULL VERSION"""
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

- ur local kitchen manager bot 💚
(powered by: chemistry homework procrastination)
    """)

@bot.command(name='test')
async def test_command(ctx):
    """Test if bot is working (DM only)"""
    if isinstance(ctx.channel, discord.DMChannel):
        await ctx.send("✅ yup i'm working! try sending: `grapes, kale` and i'll add it to the list")

@bot.command(name='info')
async def help_command(ctx):
    """Show help message"""
    help_msg = """
📱 **reina's food request bot - user manual**

**what i do:**
collect ur food requests for the bi-weekly co-op order and add them to reina's tracker automatically

**how to use:**
1. i'll dm u every sun/wed at 7pm
2. reply with items: `grapes, kale, oat milk`
3. that's literally it

**commands:**
• `!test` - check if i'm working
• `!request` - manually trigger the food request prompt
• `!info` - ur reading it rn bestie

**created by:** reina (sophomore, chem major, stressed)
**powered by:** coffee, chaos, and stackoverflow
**bug reports:** dm reina and she'll fix it (eventually) (maybe)

no i cannot order dominos. i tried. she said no. 💔
    """
    await ctx.send(help_msg)

@bot.command(name='testdm')
async def test_dm_all(ctx):
    """Manually trigger DMs to all members"""
    # Check if user is Reina
    if ctx.author.id != REINA_USER_ID:
        await ctx.send("❌ Only Reina can use this command!")
        return
    
    await ctx.send("Sending test DMs to all members...")
    await send_dms_to_all_members()
    await ctx.send("Done!")

@bot.command(name='welcome')
async def send_welcome_to_all(ctx):
    """Send welcome message to ALL members"""
    # Check if user is Reina
    if ctx.author.id != REINA_USER_ID:
        await ctx.send("❌ Only Reina can use this command!")
        return
    
    await ctx.send("Sending welcome messages to all members... this might take a minute")
    
    guild = ctx.guild
    if not guild:
        await ctx.send("❌ This command only works in a server!")
        return
    
    welcome_msg = f"""
🍌 FOOD REQUEST SZNNNN 🍌

hey! i'm reina's food request bot.

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

- ur local kitchen manager bot 💚
(powered by: chemistry homework procrastination)
    """
    
    sent = 0
    failed = 0
    
    for member in guild.members:
        if member.bot:
            continue
        
        try:
            await member.send(welcome_msg)
            print(f"  ✅ Sent welcome to {member.name}")
            sent += 1
            await asyncio.sleep(1)
        except:
            print(f"  ❌ Failed to send to {member.name}")
            failed += 1
    
    await ctx.send(f"✅ Done! Sent: {sent}, Failed: {failed}")

# ========== RUN BOT ==========
if __name__ == "__main__":
    print("Starting Food Request Bot...")
    print("This bot will DM ALL server members on schedule.")
    print("Make sure you've configured:")
    print("1. DISCORD_BOT_TOKEN")
    print("2. APPS_SCRIPT_URL")
    print("3. API_SECRET")
    
    # Start Flask server in background thread
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("Flask server started on port 8080")
    
    # Start Discord bot
    bot.run(DISCORD_BOT_TOKEN)
    bot.run(DISCORD_BOT_TOKEN)
