import os, json, base64, discord
import os, json, base64, discord
from discord.ext import commands, tasks
import firebase_admin
from firebase_admin import credentials, firestore

b64 = os.getenv("FIREBASE_B64")
cred = credentials.Certificate(json.loads(base64.b64decode(b64).decode()))
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@tasks.loop(minutes=1)
async def check_lista():
    try:
        cid = int(os.getenv("CHANNEL_ID") or os.getenv("LISTA_CHANNEL_ID"))
        ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
        async for msg in ch.history(limit=20):
            if "-" in msg.content and len(msg.content.split("\n")) >= 10:
                lines = [l.strip() for l in msg.content.split("\n") if l.strip()]
                db.collection("lista").document("aktualna").set({"utwory": lines, "count": len(lines)})
                print(f"ZAPISANO {len(lines)} do lista/aktualna")
                return
    except Exception as e:
        print(f"ERROR: {e}")

@bot.event
async def on_ready():
    print(f"READY {bot.user}")
    check_lista.start()

bot.run(os.getenv("DISCORD_TOKEN"))
