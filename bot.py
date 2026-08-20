import discord, re, json, os, time
import firebase_admin
from firebase_admin import credentials, firestore

TOKEN = os.getenv("DISCORD_TOKEN","")
CHANNEL_ID = 1537019562625597440

if not TOKEN:
    print("ERROR: Brak DISCORD_TOKEN w Variables!")
    while True: time.sleep(60)

# --- FIREBASE ---
# W Railway Variables dodaj FIREBASE_JSON = cała zawartość pliku serviceAccountKey.json z Firebase
fb_json_str = os.getenv("FIREBASE_JSON","").strip()
# usuń ewentualne cudzysłowy na początku/końcu które dodaje Railway
if fb_json_str.startswith("'") and fb_json_str.endswith("'"):
    fb_json_str = fb_json_str[1:-1]
if fb_json_str.startswith('"') and fb_json_str.endswith('"'):
    # jeśli całość jest w cudzysłowie, spróbuj 2x parsować
    try:
        inner = json.loads(fb_json_str)
        if isinstance(inner, str):
            fb_json_str = inner
    except:
        pass
cred_dict = json.loads(fb_json_str)
cred = credentials.Certificate(cred_dict)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)

db = firestore.client()
print("FIREBASE OK -> kolekcja lista")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

def parse(content):
    songs=[]
    for line in content.splitlines():
        m=re.search(r'(\d+)\.\s*(.*?)\s*-\s*(.*?)\s*(?:https?://\S+)?\s*$', line)
        if not m: continue
        num=int(m.group(1)); wykonawca=m.group(2).strip(); tytul=m.group(3).strip()
        link_match=re.search(r'(https?://\S+)', line)
        link=link_match.group(1) if link_match else ""
        songs.append({"numer":num,"wykonawca":wykonawca,"tytul":tytul,"link":link})
    return songs

async def sync_channel(channel):
    all_songs=[]
    async for msg in channel.history(limit=100):
        if not msg.content: continue
        parsed=parse(msg.content)
        if parsed:
            all_songs.extend(parsed)
            try: await msg.add_reaction("✅")
            except: pass
    uniq={}
    for s in all_songs:
        uniq[s["numer"]]=s
    final=sorted(uniq.values(), key=lambda x:x["numer"])
    
    if final:
        batch = db.batch()
        for s in final:
            doc_ref = db.collection("lista").document(str(s["numer"]))
            batch.set(doc_ref, {
                "numer": s["numer"],
                "wykonawca": s["wykonawca"],
                "tytul": s["tytul"],
                "link": s["link"],
                "timestamp": firestore.SERVER_TIMESTAMP
            })
        batch.commit()
        print(f"ZAPISANO DO FIREBASE lista: {len(final)} utworow")
        for s in final:
            print(f"{s['numer']}. {s['wykonawca']} - {s['tytul']}")
    
    # nadal zapis lokalny dla backupu
    with open("piosenki.json","w",encoding="utf-8") as f:
        json.dump(final,f,ensure_ascii=False,indent=2)
        
    return final

@client.event
async def on_ready():
    print(f"ONLINE {client.user}")
    ch=client.get_channel(CHANNEL_ID)
    if ch:
        await sync_channel(ch)

@client.event
async def on_message(message):
    if message.author==client.user: return
    if message.channel.id!=CHANNEL_ID: return
    await sync_channel(message.channel)

client.run(TOKEN)
