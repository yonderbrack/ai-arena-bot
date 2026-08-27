import os, json, base64, discord, re
from datetime import datetime
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

@tasks.loop(minutes=5)
async def check_lista():
    now = datetime.now()
    # 3 = czwartek (0=pon, 3=czw)
    is_thursday = now.weekday() == 3

    # DO TESTOWANIA: zakomentuj linijke wyzej i odkomentuj ponizej zeby testowac teraz:
    # is_thursday = True

    if not is_thursday:
        print(f"[{now.strftime('%a %H:%M')}] Nie czwartek - spie do czwartku")
        return

    try:
        print(f"[{now}] CZWARTEK - sprawdzam liste...")
        cid = int(os.getenv("CHANNEL_ID") or os.getenv("LISTA_CHANNEL_ID"))
        ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
        all_lines = []
        async for msg in ch.history(limit=200):
            if not msg.content: continue
            for raw in msg.content.split("\n"):
                raw = raw.strip()
                if not raw: continue
                if re.match(r'^\d+[\.\)]?\s*', raw):
                    all_lines.append(raw)
        uniq = {}
        for line in all_lines:
            m = re.match(r'^\s*(\d+)', line)
            if m:
                uniq[int(m.group(1))] = line
        sorted_list = [uniq[k] for k in sorted(uniq.keys())]
        print(f"Znaleziono {len(all_lines)} linii, unikalnych {len(sorted_list)}")
        if len(sorted_list) >= 1:
            db.collection("lista").document("aktualna").set({
                "utwory": sorted_list,
                "count": len(sorted_list),
                "updated_at": firestore.SERVER_TIMESTAMP,
                "updated_day": "czwartek"
            })
            print(f"ZAPISANO {len(sorted_list)} do lista/aktualna - CZWARTEK")
    except Exception as e:
        print(f"ERROR lista: {e}")

@bot.event
async def on_ready():
    print(f"READY {bot.user} - tryb CZWARTEK 00:00-23:59")
    check_lista.start()

bot.run(os.getenv("DISCORD_TOKEN"))
