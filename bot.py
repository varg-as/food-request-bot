"""
FOOD REQUEST DISCORD BOT

This bot DMs house members on Sundays and Wednesdays to collect food requests.
Members can simply reply with: "grapes, kale, lettuce, cabbage"

SETUP:
1. Install: pip install discord.py requests schedule
2. Create Discord bot at https://discord.com/developers/applications
3. Get bot token and add to this file
4. Deploy Apps Script web app and add URL here
5. Run: python food_request_bot.py

FEATURES:
- Automatic DMs on Sunday/Wednesday at specified time
- Simple comma-separated input
- Auto-populates Google Sheet via Apps Script
- Confirms what was added
"""

import discord
from discord.ext import commands, tasks
import requests
import json
from datetime import datetime, time
import asyncio

# ========== CONFIGURATION ==========
# IMPORTANT: Store these in environment variables or Replit Secrets, NOT in code!
import os

DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
APPS_SCRIPT_URL = os.getenv('APPS_SCRIPT_URL', 'YOUR_APPS_SCRIPT_WEB_APP_URL_HERE')
API_SECRET = os.getenv('API_SECRET', 'your_secret_key_here_change_this')

# User IDs to DM (get these by enabling Developer Mode in Discord and right-clicking users)
HOUSE_MEMBERS = [
    480977878025240576,
516878349407354880,
170388662062678016,
564913037312524291,
287369433809420289,
904167477615927367,
1400192088263491695,
1369705334112911503,
674773831419953176,
761490254737309705,
    # Add more user IDs here
]

# Schedule: Sunday and Wednesday at 7 PM
REQUEST_DAYS = [6, 2]  # 6 = Sunday, 2 = Wednesday (0 = Monday)
REQUEST_TIME = time(19, 0)  # 7:00 PM (use 24-hour format)

# ========== BOT SETUP ==========
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.dm_messages = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Store pending requests
pending_requests = {}

@bot.event
async def on_ready():
    print(f'{bot.user} is now running!')
    print(f'Bot ID: {bot.user.id}')
    print(f'Scheduled for: Sundays and Wednesdays at {REQUEST_TIME}')
    send_request_prompts.start()

@tasks.loop(hours=1)
async def send_request_prompts():
    """Check if it's time to send DM prompts"""
    now = datetime.now()
    
    # Check if today is a request day and if it's the right time
    if now.weekday() in REQUEST_DAYS and now.hour == REQUEST_TIME.hour:
        print(f"Sending food request prompts at {now}")
        await send_dms_to_members()

async def send_dms_to_members():
    """Send DM to all house members asking for food requests"""
    message = """
🥗 **Food Request Time!** 🥗

It's time to submit your food requests for this week's order.

**How to respond:**
Just reply with items separated by commas, like:
`grapes, kale, lettuce, cabbage`

Or just single items:
`grapes`

Your requests will be automatically added to the Kitchen Manager's tracker!
    """
    
    for user_id in HOUSE_MEMBERS:
        try:
            user = await bot.fetch_user(user_id)
            await user.send(message)
            print(f"Sent DM to {user.name}")
        except Exception as e:
            print(f"Failed to send DM to user {user_id}: {e}")

@bot.event
async def on_message(message):
    # Ignore bot's own messages
    if message.author == bot.user:
        return
    
    # Only process DMs
    if isinstance(message.channel, discord.DMChannel):
        # Check if message is from a house member
        if message.author.id in HOUSE_MEMBERS:
            await process_food_request(message)
    
    await bot.process_commands(message)

async def process_food_request(message):
    """Process a food request from a DM"""
    content = message.content.strip()
    
    # Skip if it's a command
    if content.startswith('!'):
        return
    
    # Parse items (comma-separated)
    items = [item.strip() for item in content.split(',')]
    items = [item for item in items if item]  # Remove empty strings
    
    if not items:
        await message.reply("❌ I couldn't parse any items from your message. Try: `grapes, kale, lettuce`")
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
            await message.reply(f"✅ **Added to request tracker:**\n{items_list}\n\nThank you!")
        else:
            error = result.get("error", "Unknown error")
            await message.reply(f"❌ Failed to add requests: {error}\nPlease contact the Kitchen Manager.")
            
    except Exception as e:
        print(f"Error submitting to Google Sheets: {e}")
        await message.reply(f"❌ Error submitting your request. Please try again or contact the Kitchen Manager.")

# ========== MANUAL COMMANDS ==========

@bot.command(name='request')
async def manual_request(ctx):
    """Allow members to manually trigger request prompt"""
    if ctx.author.id not in HOUSE_MEMBERS:
        return
    
    await ctx.author.send("""
🥗 **Submit Food Requests** 🥗

Reply with items separated by commas:
`grapes, kale, lettuce, cabbage`
    """)

@bot.command(name='test')
async def test_command(ctx):
    """Test if bot is working (DM only)"""
    if isinstance(ctx.channel, discord.DMChannel):
        await ctx.send("✅ Bot is working! Try sending: `grapes, kale`")

# ========== RUN BOT ==========
if __name__ == "__main__":
    print("Starting Food Request Bot...")
    print("Make sure you've configured:")
    print("1. DISCORD_BOT_TOKEN")
    print("2. APPS_SCRIPT_URL")
    print("3. API_SECRET")
    print("4. HOUSE_MEMBERS list")
    bot.run(DISCORD_BOT_TOKEN)
