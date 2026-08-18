
import discord, re, json, os

TOKEN = os.getenv("DISCORD_TOKEN","")
CHANNEL_ID = 1537019562625597440
OUTPUT = "piosenki.json"

if not TOKEN:
    print("ERROR: Brak DISCORD_TOKEN w Variables! Dodaj go w Railway.")
    # nie crashujemy builda, tylko czekamy
    import time
    while True:
        time.sleep(60)

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
        with open(OUTPUT,"w",encoding="utf-8") as f:
            json.dump(final,f,ensure_ascii=False,indent=2)
        print(f"NADPISANO {len(final)} utworow")
        for s in final:
            print(f"{s['numer']}. {s['wykonawca']} - {s['tytul']}")
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
