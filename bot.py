import os
import json
import base64
import discord
from discord.ext import commands, tasks
import firebase_admin
from firebase_admin import credentials, firestore

# --- FIREBASE - TWOJ KOD ZOSTAJE ---
firebase_creds_b64 = os.getenv("FIREBASE_B64")
firebase_creds_json = os.getenv("FIREBASE_JSON")
cred = None
if firebase_creds_b64:
    decoded = base64.b64decode(firebase_creds_b64).decode('utf-8')
    cred_dict = json.loads(decoded)
    cred = credentials.Certificate(cred_dict)
elif firebase_creds_json:
    cred_dict = json.loads(firebase_creds_json)
    cred = credentials.Certificate(cred_dict)
elif os.path.exists("serviceAccount.json"):
    cred = credentials.Certificate("serviceAccount.json")

if cred and not firebase_admin._apps:
    firebase_admin.initialize_app(cred)

db = firestore.client()  # TO BYLO BRAKUJACE

# --- DISCORD ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

CHANNEL_ID = int(os.getenv("CHANNEL_ID") or os.getenv("LISTA_CHANNEL_ID") or 0)

@tasks.loop(minutes=5)
async def check_lista():
    if CHANNEL_ID == 0: return
    try:
        channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
        async for msg in channel.history(limit=20):
            lines = [l.strip() for l in msg.content.split("\n") if l.strip() and "-" in l]
            if len(lines) >= 10:
                db.collection("lista").document("aktualna").set({
                    "utwory": lines,
                    "count": len(lines)
                })
                print(f"Zapisano {len(lines)} do lista")
                return
    except Exception as e:
        print(f"check_lista blad: {e}")

@bot.event
async def on_ready():
    print(f"Zalogowano jako {bot.user}")
    check_lista.start()

@bot.command()
async def ping(ctx):
    await ctx.send("Dziala!")

bot.run(os.getenv("DISCORD_TOKEN"))
