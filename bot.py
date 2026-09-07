import os, json, base64, discord, re
from datetime import datetime
from discord.ext import commands, tasks
import firebase_admin
from firebase_admin import credentials, firestore

# ============================================================
# FIREBASE
# ============================================================

b64 = os.getenv("FIREBASE_B64")
cred_dict = json.loads(base64.b64decode(b64).decode("utf-8"))
cred = credentials.Certificate(cred_dict)

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ============================================================
# DISCORD
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ============================================================
# LISTA UTWORÓW — ISTNIEJĄCY MECHANIZM, BEZ ZMIAN
# ============================================================

@tasks.loop(minutes=5)
async def check_lista():
    now = datetime.now()
    is_active_day = now.weekday() in [1, 2, 3]
    if not is_active_day:
        print(f"[{now.strftime('%a %H:%M')}] Nie wt/sr/czw - spie do wtorku")
        return
    try:
        print(f"[{now}] WT/SR/CZW - sprawdzam liste...")
        cid = int(os.getenv("CHANNEL_ID") or os.getenv("LISTA_CHANNEL_ID"))
        ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
        all_lines = []
        async for msg in ch.history(limit=200):
            if not msg.content:
                continue
            for raw in msg.content.split("\n"):
                raw = raw.strip()
                if not raw:
                    continue
                if re.match(r"^\d+[\.\)]?\s*", raw):
                    all_lines.append(raw)
        uniq = {}
        for line in all_lines:
            m = re.match(r"^\s*(\d+)", line)
            if m:
                uniq[int(m.group(1))] = line
        sorted_list = [uniq[k] for k in sorted(uniq.keys())]
        print(f"Znaleziono {len(all_lines)} linii, unikalnych {len(sorted_list)}")
        if len(sorted_list) >= 1:
            db.collection("lista").document("aktualna").set({
                "utwory": sorted_list,
                "count": len(sorted_list),
                "updated_at": firestore.SERVER_TIMESTAMP,
                "updated_day": "wtorek-sroda-czwartek"
            })
            print(f"ZAPISANO {len(sorted_list)} do lista/aktualna - WT/SR/CZW")
    except Exception as e:
        print(f"ERROR lista: {e}")

# ============================================================
# CZŁONKOWIE SERWERA
# ============================================================

async def sync_members():
    try:
        guild_id = int(os.getenv("GUILD_ID") or "1515466354113777715")
        guild = bot.get_guild(guild_id)
        if guild is None:
            print(f"ERROR członkowie: nie znaleziono serwera {guild_id}")
            return
        print(f"CZŁONKOWIE: znaleziono serwer: {guild.name} ({guild.id})")
        print("CZŁONKOWIE: pobieram listę przez Discord API...")
        members = []
        async for member in guild.fetch_members(limit=None):
            members.append(member)
        print(f"CZŁONKOWIE: Discord API zwróciło {len(members)} członków")
        collection = db.collection("czlonkowie")
        batch = db.batch()
        batch_count = 0
        saved_count = 0
        for member in members:
            doc_ref = collection.document(str(member.id))
            batch.set(
                doc_ref,
                {
                    "discord_id": str(member.id),
                    "username": member.name,
                    "display_name": member.display_name
                },
                merge=True
            )
            batch_count += 1
            if batch_count >= 400:
                batch.commit()
                saved_count += batch_count
                print(f"CZŁONKOWIE: zapisano partię {batch_count} (łącznie {saved_count})")
                batch = db.batch()
                batch_count = 0
        if batch_count > 0:
            batch.commit()
            saved_count += batch_count
        print(f"CZŁONKOWIE: zapisano/zaktualizowano {saved_count} rekordów w Firebase → czlonkowie")
    except Exception as e:
        print(f"ERROR członkowie: {type(e).__name__}: {e}")

# ============================================================
# ARCHIWUM UTWORÓW — POPRAWIONE POD #archiwum-list
# ============================================================

async def sync_archiwum():
    try:
        print("ARCHIWUM: rozpoczynam pobieranie historii kanału...")
        cid = 1518213312234655825
        ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
        print(f"ARCHIWUM: kanał {ch.name} ({ch.id})")

        entries = []
        total_msgs = 0

        async for msg in ch.history(limit=None):
            total_msgs += 1
            if not msg.content:
                continue

            text = msg.content
            text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
            text = re.sub(r"https?://\S+", "", text)

            for raw in text.splitlines():
                raw = raw.strip()
                if not raw:
                    continue

                # pomijamy nagłówki TOP 10
                upper = raw.upper()
                if upper.startswith("TOP 10") or "LISTA PRZEBOJÓW" in upper or raw.startswith("🏆"):
                    continue

                # usuwamy numer: 1 RAZNAROCK ILE PIJESZ -> RAZNAROCK ILE PIJESZ
                raw_no_num = re.sub(r"^\s*\d+[\.\)]?\s*", "", raw).strip()
                if not raw_no_num:
                    continue

                # format z Discorda: WYKONAWCA TYTUŁ (2+ spacje / tab)
                parts = re.split(r"\s{2,}|\t+", raw_no_num)
                if len(parts) >= 2:
                    wykonawca = parts[0].strip()
                    tytul = parts[1].strip()
                    if wykonawca and tytul:
                        entries.append({"wykonawca": wykonawca, "tytul": tytul})
                else:
                    # jesli ktos wpisal z myslnikiem, nie kasujemy
                    continue

        print(f"ARCHIWUM: przeskanowano {total_msgs} wiadomości, znaleziono {len(entries)} wpisów przed dedup")

        unique = {}
        display_entries = []
        for entry in entries:
            wykonawca = entry["wykonawca"].strip()
            tytul = entry["tytul"].strip()
            key = (
                re.sub(r"\s+", " ", wykonawca).lower(),
                re.sub(r"\s+", " ", tytul).lower()
            )
            if key not in unique:
                unique[key] = True
                display_entries.append({"wykonawca": wykonawca, "tytul": tytul})

        print(f"ARCHIWUM: po usunięciu duplikatów {len(display_entries)} utworów")

        if len(display_entries) == 0:
            print("ARCHIWUM: 0 utworów - nie nadpisuje Firebase!")
            return

        db.collection("archiwum").document("utwory").set({
            "utwory": display_entries,
            "count": len(display_entries),
            "updated_at": firestore.SERVER_TIMESTAMP
        })
        print(f"ARCHIWUM: ZAPISANO {len(display_entries)} utworów do archiwum/utwory")

    except Exception as e:
        print(f"ERROR archiwum: {type(e).__name__}: {e}")

# ============================================================
# START
# ============================================================

@bot.event
async def on_ready():
    print(f"READY {bot.user} - tryb WT/SR/CZW 00:00-23:59")
    if not check_lista.is_running():
        check_lista.start()
    print("TEST: START sync_members()")
    await sync_members()
    print("TEST: KONIEC sync_members()")
    print("TEST: START sync_archiwum()")
    await sync_archiwum()
    print("TEST: KONIEC sync_archiwum()")

# ============================================================
# BOT
# ============================================================

bot.run(os.getenv("DISCORD_TOKEN"))
