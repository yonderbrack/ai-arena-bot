import os, json, base64, discord, re
from discord.ext import commands, tasks
import firebase_admin
from firebase_admin import credentials, firestore

b64 = os.getenv("FIREBASE_B64")
cred_dict = json.loads(base64.b64decode(b64).decode('utf-8'))
cred = credentials.Certificate(cred_dict)
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
        all_lines = []
        async for msg in ch.history(limit=100):
            if not msg.content or "-" not in msg.content:
                continue
            lines = [l.strip() for l in msg.content.split("\n") if l.strip() and "-" in l]
            all_lines.extend(lines)
        uniq = {}
        for line in all_lines:
            m = re.match(r'^\s*(\d+)', line)
            if m:
                uniq[int(m.group(1))] = line
        sorted_list = [uniq[k] for k in sorted(uniq.keys())]
        if len(sorted_list) >= 5:
            db.collection("lista").document("aktualna").set({
                "utwory": sorted_list,
                "count": len(sorted_list),
                "updated_at": firestore.SERVER_TIMESTAMP
            })
            print(f"ZAPISANO {len(sorted_list)} do lista/aktualna")
        else:
            print(f"Za malo znaleziono: {len(sorted_list)}")
    except Exception as e:
        print(f"ERROR: {e}")

@bot.event
async def on_ready():
    print(f"READY {bot.user}")
    check_lista.start()

bot.run(os.getenv("DISCORD_TOKEN"))

