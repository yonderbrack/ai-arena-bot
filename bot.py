import discord, json, os, time
import firebase_admin
from firebase_admin import credentials, firestore

TOKEN = os.getenv("DISCORD_TOKEN","")
CHANNEL_ID = 1537019562625597440

if not TOKEN:
    print("ERROR: Brak DISCORD_TOKEN!")
    while True: time.sleep(60)

fb_json_str = os.getenv("FIREBASE_JSON","").strip()
if not fb_json_str:
    print("ERROR: Brak FIREBASE_JSON!")
    while True: time.sleep(60)

# Usuń zewnętrzne cudzysłowy
if (fb_json_str.startswith("'") and fb_json_str.endswith("'")) or (fb_json_str.startswith('"') and fb_json_str.endswith('"')):
    fb_json_str = fb_json_str[1:-1]

# Naprawa: weź tylko PIERWSZY obiekt JSON, ignoruj resztę (Extra data fix)
try:
    fb_json_str = fb_json_str.strip()
    decoder = json.JSONDecoder()
    cred_dict, _ = decoder.raw_decode(fb_json_str)
except Exception:
    # fallback - wytnij od pierwszego { do pierwszego zamykającego pasującego
    try:
        start = fb_json_str.find('{')
        # znajdź gdzie kończy się pierwszy obiekt licząc klamry
        depth = 0
        end = -1
        for i in range(start, len(fb_json_str)):
            if fb_json_str[i] == '{': depth += 1
            elif fb_json_str[i] == '}': depth -= 1
            if depth == 0 and start!= -1:
                end = i
                break
        if start!= -1 and end!= -1:
            cred_dict = json.loads(fb_json_str[start:end+1])
        else:
            raise ValueError("Nie znaleziono JSON")
    except Exception as ex:
        print(f"FIREBASE JSON ERROR: {ex}")
        print(f"Pierwsze 500: {fb_json_str[:500]}")
        while True: time.sleep(60)

cred = credentials.Certificate(cred_dict)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)

db = firestore.client()
print("FIREBASE OK -> kolekcja lista")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

@client.event
async def on_ready():
    print(f"BOT ONLINE {client.user} -> kanal {CHANNEL_ID}")

@client.event
async def on_message(message):
    if message.author.bot: return
    if message.channel.id!= CHANNEL_ID: return
    if not message.content.strip(): return
    try:
        content = message.content.strip()
        db.collection("lista").document(str(message.id)).set({
            "content": content,
            "author": str(message.author),
            "author_id": str(message.author.id),
            "timestamp": message.created_at,
            "channel_id": str(message.channel.id),
            "message_id": str(message.id)
        })
        print(f"Zapisano: {content[:100]}")
    except Exception as ex:
        print(f"BLAD zapisu: {ex}")

client.run(TOKEN)
