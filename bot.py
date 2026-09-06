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

    # 1=wtorek, 2=sroda, 3=czwartek (0=pon)
    is_active_day = now.weekday() in [1, 2, 3]

    # DO TESTOWANIA:
    # zakomentuj linijke wyzej i odkomentuj ponizej
    # is_active_day = True

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

        print(
            f"Znaleziono {len(all_lines)} linii, "
            f"unikalnych {len(sorted_list)}"
        )

        if len(sorted_list) >= 1:
            db.collection("lista").document("aktualna").set({
                "utwory": sorted_list,
                "count": len(sorted_list),
                "updated_at": firestore.SERVER_TIMESTAMP,
                "updated_day": "wtorek-sroda-czwartek"
            })

            print(
                f"ZAPISANO {len(sorted_list)} do lista/aktualna "
                f"- WT/SR/CZW"
            )

    except Exception as e:
        print(f"ERROR lista: {e}")


# ============================================================
# CZŁONKOWIE SERWERA — NOWA FUNKCJA
# ============================================================

async def sync_members():
    try:
        guild_id = int(os.getenv("GUILD_ID") or "1515466354113777715")
        guild = bot.get_guild(guild_id)

        if guild is None:
            print(f"ERROR członkowie: nie znaleziono serwera {guild_id}")
            return

        print(
            f"CZŁONKOWIE: znaleziono serwer: "
            f"{guild.name} ({guild.id})"
        )

        print("CZŁONKOWIE: pobieram listę przez Discord API...")

        members = []

        # fetch_members() pobiera członków przez HTTP API.
        # Nie używamy guild.chunk(), ponieważ wcześniejsza wersja
        # zawieszała się na tym wywołaniu.
        async for member in guild.fetch_members(limit=None):
            members.append(member)

        print(
            f"CZŁONKOWIE: Discord API zwróciło "
            f"{len(members)} członków"
        )

        collection = db.collection("czlonkowie")

        # Firestore Batch ma limit 500 operacji.
        # Używamy maksymalnie 400 na jedną partię.
        batch = db.batch()
        batch_count = 0
        saved_count = 0

        for member in members:
            doc_ref = collection.document(str(member.id))

            # merge=True:
            # NIE usuwamy istniejących danych.
            # PIN-y i inne pola pozostają bez zmian.
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

                print(
                    f"CZŁONKOWIE: zapisano partię {batch_count} "
                    f"(łącznie {saved_count})"
                )

                batch = db.batch()
                batch_count = 0

        if batch_count > 0:
            batch.commit()
            saved_count += batch_count

        print(
            f"CZŁONKOWIE: zapisano/zaktualizowano "
            f"{saved_count} rekordów w Firebase → czlonkowie"
        )

    except Exception as e:
        print(f"ERROR członkowie: {type(e).__name__}: {e}")


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


# ============================================================
# BOT
# ============================================================

bot.run(os.getenv("DISCORD_TOKEN"))
