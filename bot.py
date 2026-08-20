import discord, re, json, os, time
import firebase_admin
from firebase_admin import credentials, firestore

TOKEN = os.getenv("DISCORD_TOKEN","")
CHANNEL_ID = 1537019562625597440

if not TOKEN:
    print("ERROR: Brak DISCORD_TOKEN w Variables!")
    while True: time.sleep(60)

# --- FIREBASE - ODPORNA WERSJA ---
fb_json_str = os.getenv("FIREBASE_JSON","").strip()

if not fb_json_str:
    print("ERROR: Brak FIREBASE_JSON w Variables!")
    while True: time.sleep(60)

# Usuń cudzysłowy z zewnątrz
if fb_json_str.startswith("'") and fb_json_str.endswith("'"):
    fb_json_str = fb_json_str[1:-1]
if fb_json_str.startswith('"') and fb_json_str.endswith('"'):
    try:
        inner = json.loads(fb_json_str)
        if isinstance(inner, str):
            fb_json_str = inner
    except:
        pass

# Wyciągnij tylko JSON { ... } - naprawia błąd Extra data
try:
    start = fb_json_str.find('{')
    end = fb_json_str.rfind('}')
    if start != -1 and end != -1:
        fb_json_str = fb_json_str[start:end+1]
    cred_dict = json.loads(fb_json_str)
except Exception as e:
    print(f"FIREBASE JSON ERROR: {e}")
    print(f"Pierwsze 300 znakow: {fb_json_str[:300]}")
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
    print(f"BOT ONLINE jako {client.user} -> nasluchuje kanal {CHANNEL_ID}")

@client.event
async def on_message(message):
    if message.author.bot:
        return
    if message.channel.id != CHANNEL_ID:
        return
    
    # Zapisz do Firebase do kolekcji 'lista'
    try:
        content = message.content.strip()
        if not content:
            return
