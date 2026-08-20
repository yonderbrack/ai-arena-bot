import os
import json
import base64
import discord
from discord.ext import commands
import firebase_admin
from firebase_admin import credentials

# --- FIREBASE ---
firebase_creds_b64 = os.getenv("FIREBASE_B64")
firebase_creds_json = os.getenv("FIREBASE_JSON")
cred = None

if firebase_creds_b64:
    try:
        decoded = base64.b64decode(firebase_creds_b64).decode('utf-8')
        cred_dict = json.loads(decoded)
        cred = credentials.Certificate(cred_dict)
        print("Firebase zaladowany z FIREBASE_B64")
    except Exception as e:
        print(f"Blad FIREBASE_B64: {e}")
elif firebase_creds_json:
    try:
        cred_dict = json.loads(firebase_creds_json)
        cred = credentials.Certificate(cred_dict)
        print("Firebase zaladowany z FIREBASE_JSON")
    except Exception as e:
        print(f"Blad FIREBASE_JSON: {e}")

if not cred and os.path.exists("serviceAccount.json"):
    cred = credentials.Certificate("serviceAccount.json")
    print("Firebase zaladowany z pliku")

if cred:
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
else:
    print("BRAK CREDENTIALS FIREBASE!")

# --- DISCORD BOT ---
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Zalogowano jako {bot.user}")

@bot.command()
async def ping(ctx):
    await ctx.send("Dziala!")

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("BRAK DISCORD_TOKEN!")
else:
    bot.run(TOKEN)
