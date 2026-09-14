import os
import json
import base64
import hashlib
import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

import firebase_admin
from firebase_admin import credentials, firestore


# ============================================================
# FIREBASE
# ============================================================

b64 = os.getenv("FIREBASE_B64")
if b64:
    cred_dict = json.loads(base64.b64decode(b64).decode("utf-8"))
    cred = credentials.Certificate(cred_dict)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
else:
    # fallback dla lokalnego testu
    if not firebase_admin._apps:
        firebase_admin.initialize_app()

db = firestore.client()


# ============================================================
# DISCORD
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ============================================================
# USTAWIENIA ODZYSKIWANIA PIN
# ============================================================

RECOVERY_TIMEOUT_MINUTES = 10
RECOVERY_COLLECTION = "pin_recovery"

# ============================================================
# FRANC GADA - FRANC/ANDY NAGRYWAJA -> FIREBASE MP3 -> BOT PULSUJE W APCE
# ============================================================
FRANC_GADA_COLLECTION = "MP3"
FRANC_GADA_SYSTEM_DOC = "_system_franc_gada_bot"
FRANC_GADA_ANNOUNCE_CHANNEL_ID = os.getenv("FRANC_GADA_ANNOUNCE_CHANNEL_ID") or os.getenv("FRANC_GADA_CHANNEL_ID") or "1517600000000000000"


# ============================================================
# POMOCNICZE — HASH PIN
# MUSI BYĆ IDENTYCZNY JAK W ANDROIDZIE
# ============================================================

def hash_pin(pin: str) -> str:
    return hashlib.sha256(
        pin.encode("utf-8")
    ).hexdigest()


# ============================================================
# LISTA UTWORÓW — NAPRAWIONE, DZIAŁA JAK KIEDYŚ OD RAZU
# ============================================================

WARSAW = ZoneInfo("Europe/Warsaw")


@tasks.loop(seconds=30)
async def check_lista():
    now = datetime.now(WARSAW)
    is_active_day = now.weekday() in [0, 1, 3, 6]  # PN/WT/CZW/ND - FIX LIVE
    if not is_active_day:
        return
    try:
        cid_raw = os.getenv("CHANNEL_ID") or os.getenv("LISTA_CHANNEL_ID")
        if not cid_raw:
            return
        cid = int(cid_raw)
        ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
        if ch is None:
            return

        numbered = {}
        async for msg in ch.history(limit=100):
            if not msg.content:
                continue
            for raw in msg.content.split("\n"):
                raw = raw.strip()
                if not raw:
                    continue
                m = re.match(r"^\s*(\d+)\s*[\.\)]?\s*(.+)", raw)
                if not m:
                    continue
                if not re.search(r"https?://\S+", raw):
                    continue
                num = int(m.group(1))
                if num < 1 or num > 100:
                    continue
                if num not in numbered:
                    numbered[num] = raw.strip()

        if len(numbered) < 5:
            print("Nie znaleziono min 5 ponumerowanych")
            return

        sorted_nums = sorted(numbered.keys())
        final_list = [numbered[n] for n in sorted_nums]

        print(f"Znalazlem ponumerowane numery: {sorted_nums[:5]}...{sorted_nums[-5:]} razem {len(final_list)}")

        db.collection("lista").document("aktualna").set({
            "utwory": final_list,
            "count": len(final_list),
            "updated_at": firestore.SERVER_TIMESTAMP,
            "updated_day": now.strftime("%A %H:%M:%S")
        })
        print(f"ZAPISANO {len(final_list)} ponumerowanych 1-{sorted_nums[-1]}")
    except Exception as e:
        print(f"ERROR lista: {e}")
        import traceback
        traceback.print_exc()


# ============================================================
# LINKI DO GŁOSOWANIA
# ============================================================

@tasks.loop(seconds=60)
async def check_glosowanie_link():
    try:
        cid_raw = os.getenv("GLOSOWANIE_CHANNEL_ID") or os.getenv("LINKI_CHANNEL_ID") or "1517609248765382776"
        if not cid_raw:
            return
        cid = int(cid_raw)
        ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
        if ch is None:
            print(f"GLOSOWANIE: nie znaleziono kanału {cid}")
            return

        link_pattern = re.compile(
            r"https?://(?:forms\.gle|docs\.google\.com/(?:forms|spreadsheets)/|forms\.google\.com|forms\.office\.com|docs\.google\.com/forms)/[^\s\)\]\<\>\"']+",
            re.IGNORECASE
        )

        found_link = None
        found_msg_id = None
        found_msg_date = None

        async for msg in ch.history(limit=20):
            if not msg.content:
                continue
            m = link_pattern.search(msg.content)
            if m:
                raw_link = m.group(0).rstrip(").,!;")
                found_link = raw_link
                found_msg_id = str(msg.id)
                found_msg_date = msg.created_at
                break

        if not found_link:
            print("GLOSOWANIE: nie znaleziono linka w ostatnich 20 wiadomościach")
            return

        doc_ref = db.collection("config").document("glosowanie")
        current_doc = doc_ref.get()
        current_link = ""
        if current_doc.exists:
            current_link = current_doc.to_dict().get("glosuj_link", "") or ""

        if current_link == found_link:
            return

        doc_ref.set({
            "glosuj_link": found_link,
            "glosujLink": found_link,
            "link_glosuj": found_link,
            "glosuj_link_updated_at": firestore.SERVER_TIMESTAMP,
            "source_message_id": found_msg_id,
            "source_channel_id": str(cid),
            "source_channel_name": "linki-do-glosowania",
            "updated_at": firestore.SERVER_TIMESTAMP
        }, merge=True)

        try:
            db.collection("config").document("glosuj").set({
                "link": found_link,
                "updated_at": firestore.SERVER_TIMESTAMP
            }, merge=True)
        except Exception:
            pass

        print(f"GLOSOWANIE: ZAPISANO NOWY LINK {found_link} z msg {found_msg_id} ({found_msg_date})")

    except Exception as e:
        print(f"ERROR glosowanie_link: {e}")
        import traceback
        traceback.print_exc()


# ============================================================
# FRANC GADA
# ============================================================

@tasks.loop(seconds=15)
async def check_franc_gada():
    try:
        coll = db.collection(FRANC_GADA_COLLECTION)
        docs = coll.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(1).stream()
        latest = None
        for d in docs:
            latest = d
            break

        if latest is None:
            return

        data = latest.to_dict() or {}
        ts = data.get("timestamp") or 0
        try:
            ts = int(ts)
        except:
            ts = 0

        if ts == 0:
            return

        sys_ref = db.collection(FRANC_GADA_COLLECTION).document(FRANC_GADA_SYSTEM_DOC)
        sys_doc = sys_ref.get()
        last_announced_ts = 0
        if sys_doc.exists:
            last_announced_ts = int((sys_doc.to_dict() or {}).get("last_announced_ts") or 0)

        if ts <= last_announced_ts:
            return

        author = data.get("author") or "FRANC"
        duration = data.get("durationSec") or 0

        print(f"FRANC GADA: NOWA WIADOMOSC {latest.id} od {author} ts={ts} duration={duration}s")

        try:
            cid_raw = FRANC_GADA_ANNOUNCE_CHANNEL_ID
            if cid_raw and cid_raw != "1517600000000000000":
                cid = int(cid_raw)
                ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
                if ch is not None:
                    embed = discord.Embed(
                        title="🎤 FRANC GADA! NOWA WIADOMOSC!",
                        description=f"**{author}** nagral nowa wiadomosc glosowa w apce AI Arena FM!",
                        color=0xFFD700
                    )
                    embed.add_field(name="Autor", value=author, inline=True)
                    embed.add_field(name="Czas", value=f"{duration}s" if duration else "—", inline=True)
                    embed.add_field(name="Co zrobic?", value="Otworz apke AI Arena FM -> kliknij pulsujace FRANC GADA zeby odtworzyc!", inline=False)
                    embed.set_footer(text=f"ID: {latest.id} • {datetime.now(WARSAW).strftime('%d.%m.%Y %H:%M')}")
                    await ch.send(content="@everyone 🎙 FRANC GADA! Sprawdz apke!", embed=embed)
        except Exception as e:
            print(f"FRANC GADA: blad wysylki Discord: {e}")

        sys_ref.set({
            "last_announced_ts": ts,
            "last_announced_id": latest.id,
            "last_announced_at": firestore.SERVER_TIMESTAMP,
            "last_author": author
        }, merge=True)

    except Exception as e:
        print(f"ERROR FRANC GADA: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


# ============================================================
# TYPY — CZYSZCZENIE
# ============================================================

TYPY_SYSTEM_DOC = "_system_weekly_cleanup"

def get_current_week_start():
    now = datetime.now(WARSAW)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday

def cleanup_old_typy(cutoff_datetime):
    try:
        collection = db.collection("typy")
        cutoff_ms = int(cutoff_datetime.timestamp() * 1000)
        docs = list(collection.stream())
        deleted_count = 0
        skipped_count = 0
        batch = db.batch()
        batch_count = 0
        for doc in docs:
            if doc.id == TYPY_SYSTEM_DOC:
                continue
            data = doc.to_dict() or {}
            timestamp = data.get("timestamp")
            if timestamp is None:
                skipped_count += 1
                continue
            try:
                timestamp_ms = int(timestamp)
            except (TypeError, ValueError):
                skipped_count += 1
                continue
            if timestamp_ms < cutoff_ms:
                batch.delete(doc.reference)
                batch_count += 1
                deleted_count += 1
                if batch_count >= 400:
                    batch.commit()
                    batch = db.batch()
                    batch_count = 0
        if batch_count > 0:
            batch.commit()
        print(f"TYPY: czyszczenie zakończone. Usunięto: {deleted_count}, pominięto: {skipped_count}")
        return deleted_count
    except Exception as e:
        print(f"ERROR TYPY cleanup: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 0

@tasks.loop(seconds=30)
async def check_typy_cleanup():
    try:
        now = datetime.now(WARSAW)
        current_week_start = get_current_week_start()
        current_week_key = current_week_start.strftime("%Y-%m-%d")
        system_ref = db.collection("typy").document(TYPY_SYSTEM_DOC)
        system_doc = system_ref.get()
        system_data = {}
        if system_doc.exists:
            system_data = system_doc.to_dict() or {}
        initialized = bool(system_data.get("initialized"))
        cleanup_week = system_data.get("cleanup_week")
        if not initialized:
            print("TYPY: pierwsze uruchomienie systemu czyszczenia.")
            deleted_count = cleanup_old_typy(current_week_start)
            system_ref.set({
                "initialized": True,
                "initialized_at": firestore.SERVER_TIMESTAMP,
                "cleanup_week": current_week_key,
                "last_cleanup_at": firestore.SERVER_TIMESTAMP,
                "last_deleted_count": deleted_count,
                "description": "Automatyczne czyszczenie starych typów."
            }, merge=True)
            return
        if now.weekday() != 0 or now.hour < 12 or cleanup_week == current_week_key:
            return
        print(f"TYPY: rozpoczęto cotygodniowe czyszczenie dla tygodnia {current_week_key}.")
        deleted_count = cleanup_old_typy(current_week_start)
        system_ref.set({
            "cleanup_week": current_week_key,
            "last_cleanup_at": firestore.SERVER_TIMESTAMP,
            "last_deleted_count": deleted_count
        }, merge=True)
    except Exception as e:
        print(f"ERROR typy cleanup checker: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


# ============================================================
# CZŁONKOWIE - NAPRAWIONE - TERAZ DZIAŁA NA ŻYWO + BACKUP CO 6H
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
            # IGNORUJ BOTY
            if member.bot:
                continue
            doc_ref = collection.document(str(member.id))
            batch.set(
                doc_ref,
                {
                    "discord_id": str(member.id),
                    "username": member.name,
                    "username_lower": member.name.lower(),
                    "display_name": member.display_name,
                    "display_name_lower": member.display_name.lower(),
                    # dla kompatybilności z apką która szuka po "nick"
                    "nick": member.display_name,
                    "nick_lower": member.display_name.lower(),
                    "discordName": member.display_name,
                    "avatar": str(member.display_avatar.url) if member.display_avatar else "",
                    "joined_at": firestore.SERVER_TIMESTAMP,
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
        import traceback
        traceback.print_exc()

# BACKUP LOOP - pełny sync co 6h, a nie co chwilę
@tasks.loop(hours=6)
async def sync_members_loop():
    print("CZŁONKOWIE: backup sync co 6h - start")
    await sync_members()


@bot.event
async def on_member_join(member):
    try:
        if member.bot:
            return
        print(f"CZŁONKOWIE: JOIN {member.display_name} ({member.id})")
        db.collection("czlonkowie").document(str(member.id)).set({
            "discord_id": str(member.id),
            "username": member.name,
            "username_lower": member.name.lower(),
            "display_name": member.display_name,
            "display_name_lower": member.display_name.lower(),
            "nick": member.display_name,
            "nick_lower": member.display_name.lower(),
            "discordName": member.display_name,
            "avatar": str(member.display_avatar.url) if member.display_avatar else "",
            "joined_at": firestore.SERVER_TIMESTAMP,
            "joined_at_iso": datetime.now(timezone.utc).isoformat(),
            "active": True,
        }, merge=True)
    except Exception as e:
        print(f"ERROR on_member_join: {e}")


@bot.event
async def on_member_update(before, after):
    try:
        if after.bot:
            return
        if before.display_name != after.display_name or before.name != after.name:
            print(f"CZŁONKOWIE: UPDATE nick {before.display_name} -> {after.display_name} ({after.id})")
            db.collection("czlonkowie").document(str(after.id)).set({
                "username": after.name,
                "username_lower": after.name.lower(),
                "display_name": after.display_name,
                "display_name_lower": after.display_name.lower(),
                "nick": after.display_name,
                "nick_lower": after.display_name.lower(),
                "discordName": after.display_name,
                "updated_at": firestore.SERVER_TIMESTAMP,
            }, merge=True)
    except Exception as e:
        print(f"ERROR on_member_update: {e}")

@bot.event
async def on_member_remove(member):
    try:
        print(f"CZŁONKOWIE: LEAVE {member.display_name} ({member.id}) - oznaczam jako inactive")
        db.collection("czlonkowie").document(str(member.id)).set({
            "active": False,
            "left_at": firestore.SERVER_TIMESTAMP,
        }, merge=True)
    except Exception as e:
        print(f"ERROR on_member_remove: {e}")

# Komenda manualna do wymuszenia syncu
@bot.command(name="sync")
@commands.has_permissions(administrator=True)
async def sync_cmd(ctx):
    await ctx.send("🔄 Rozpoczynam pełną synchronizację członków...")
    await sync_members()
    await ctx.send("✅ Synchronizacja zakończona!")

@bot.command(name="czlonkowie")
@commands.has_permissions(administrator=True)
async def czlonkowie_count(ctx):
    try:
        col = db.collection("czlonkowie")
        docs = list(col.stream())
        await ctx.send(f"📊 W bazie jest {len(docs)} dokumentów w `czlonkowie`")
    except Exception as e:
        await ctx.send(f"❌ Błąd: {e}")


# ============================================================
# ARCHIWUM UTWORÓW
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
                upper = raw.upper()
                if upper.startswith("TOP 10") or "LISTA PRZEBOJÓW" in upper or raw.startswith("🏆"):
                    continue
                raw_no_num = re.sub(r"^\s*\d+[\.\)]?\s*", "", raw).strip()
                if not raw_no_num:
                    continue
                parts = re.split(r"\s{2,}|\t+", raw_no_num)
                if len(parts) >= 2:
                    wykonawca = parts[0].strip()
                    tytul = parts[1].strip()
                    if wykonawca and tytul:
                        entries.append({"wykonawca": wykonawca, "tytul": tytul})
        print(f"ARCHIWUM: przeskanowano {total_msgs} wiadomości, znaleziono {len(entries)} wpisów przed dedup")
        unique = {}
        display_entries = []
        for entry in entries:
            wykonawca = entry["wykonawca"].strip()
            tytul = entry["tytul"].strip()
            key = (re.sub(r"\s+", " ", wykonawca).lower(), re.sub(r"\s+", " ", tytul).lower())
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
# HALL OF FAME
# ============================================================

HALL_OF_FAME_CHANNEL_ID = 1518213312234655825
HALL_OF_FAME_COLLECTION = "HALL OF FAME"
HALL_OF_FAME_SYSTEM_DOC = "_system_hall_of_fame"

async def sync_hall_of_fame_all():
    try:
        print("HALL OF FAME: PIERWSZE URUCHOMIENIE - ściągam WSZYSTKIE nr 1 + uzupełniam YouTube...")
        cid = HALL_OF_FAME_CHANNEL_ID
        ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
        if ch is None:
            print(f"HOF: nie znaleziono kanału {cid}")
            return 0
        added = 0
        skipped = 0
        updated = 0
        async for msg in ch.history(limit=None):
            if not msg.content:
                continue
            found_line = None
            for raw in msg.content.splitlines():
                if re.match(r"^\s*1(?!\d)[\.\).]?\s+.+", raw):
                    found_line = raw.strip()
                    break
            if not found_line:
                continue
            m_top = re.search(r"TOP\s*(\d+)\s*#\s*0*(\d+)", msg.content, re.IGNORECASE)
            if m_top:
                top_size = int(m_top.group(1))
                numer = int(m_top.group(2))
            else:
                m_num = re.search(r"#\s*0*(\d+)", msg.content)
                if not m_num:
                    continue
                top_size = 10
                numer = int(m_num.group(1))
            doc_id = f"{numer:03d}"
            doc_ref = db.collection(HALL_OF_FAME_COLLECTION).document(doc_id)
            doc_snap = doc_ref.get()
            youtubeUrl = ""
            youtubeId = ""
            urls_all = re.findall(r"https?://[^\s\)\]\<\>\"']+", msg.content)
            for u in urls_all:
                uc = u.rstrip(").,!;")
                if "youtu" in uc.lower():
                    youtubeUrl = uc
                    m_id = re.search(r"(?:v=|youtu\.be/|shorts/)([^&\s\?\))]+)", uc)
                    if m_id:
                        youtubeId = m_id.group(1)
                    break
            if not youtubeUrl:
                urls_line = re.findall(r"https?://[^\s\)\]\<\>\"']+", found_line)
                for u in urls_line:
                    uc = u.rstrip(").,!;")
                    if "youtu" in uc.lower():
                        youtubeUrl = uc
                        m_id = re.search(r"(?:v=|youtu\.be/|shorts/)([^&\s\?\))]+)", uc)
                        if m_id:
                            youtubeId = m_id.group(1)
                        break
            if doc_snap.exists:
                existing = doc_snap.to_dict() or {}
                if existing.get("youtubeId") and existing.get("youtubeUrl"):
                    skipped += 1
                    continue
                else:
                    if youtubeUrl or youtubeId:
                        doc_ref.set({"youtubeId": youtubeId, "youtubeUrl": youtubeUrl, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
                        updated += 1
                        print(f"HALL OF FAME: UZUPEŁNIONO YT dla {doc_id}: {youtubeId}")
                    else:
                        skipped += 1
                    continue
            clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", found_line)
            clean = re.sub(r"https?://\S+", "", clean)
            clean = re.sub(r"^\s*1(?!\d)[\.\).]?\s*", "", clean).strip()
            wykonawca = ""
            tytul = ""
            if " - " in clean:
                wykonawca, tytul = [x.strip() for x in clean.split(" - ", 1)]
            elif " – " in clean:
                wykonawca, tytul = [x.strip() for x in clean.split(" – ", 1)]
            else:
                parts = re.split(r"\s{2,}|\t+", clean)
                if len(parts) >= 2:
                    wykonawca = parts[0].strip()
                    tytul = parts[1].strip()
                else:
                    tytul = clean
            if not tytul:
                continue
            doc_ref.set({
                "notowanie": f"TOP {top_size} #{doc_id}",
                "numerNotowania": numer,
                "sourceChannelId": str(cid),
                "sourceMessageId": str(msg.id),
                "tytul": tytul,
                "wykonawca": wykonawca,
                "youtubeId": youtubeId,
                "youtubeUrl": youtubeUrl,
                "updated_at": firestore.SERVER_TIMESTAMP
            })
            added += 1
        print(f"HALL OF FAME: KONIEC. Dodano: {added}, uzupełniono YT: {updated}, pominięto: {skipped}")
        return added
    except Exception as e:
        print(f"ERROR HOF ALL: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 0

async def sync_hall_of_fame_latest():
    try:
        print("HALL OF FAME: szukam ostatniego miejsca nr 1...")
        cid = HALL_OF_FAME_CHANNEL_ID
        ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
        if ch is None:
            print(f"HOF: nie znaleziono kanału {cid}")
            return False
        async for msg in ch.history(limit=150):
            if not msg.content:
                continue
            found_line = None
            for raw in msg.content.splitlines():
                if re.match(r"^\s*1(?!\d)[\.\).]?\s+.+", raw):
                    found_line = raw.strip()
                    break
            if not found_line:
                continue
            m_top = re.search(r"TOP\s*(\d+)\s*#\s*0*(\d+)", msg.content, re.IGNORECASE)
            if m_top:
                top_size = int(m_top.group(1))
                numer = int(m_top.group(2))
            else:
                m_num = re.search(r"#\s*0*(\d+)", msg.content)
                if not m_num:
                    continue
                top_size = 10
                numer = int(m_num.group(1))
            doc_id = f"{numer:03d}"
            doc_ref = db.collection(HALL_OF_FAME_COLLECTION).document(doc_id)
            doc_snap = doc_ref.get()
            youtubeUrl = ""
            youtubeId = ""
            urls_all = re.findall(r"https?://[^\s\)\]\<\>\"']+", msg.content)
            for u in urls_all:
                uc = u.rstrip(").,!;")
                if "youtu" in uc.lower():
                    youtubeUrl = uc
                    m_id = re.search(r"(?:v=|youtu\.be/|shorts/)([^&\s\?\))]+)", uc)
                    if m_id:
                        youtubeId = m_id.group(1)
                    break
            if doc_snap.exists:
                existing = doc_snap.to_dict() or {}
                if existing.get("youtubeId"):
                    print(f"HALL OF FAME: {doc_id} już istnieje - nie ma nowego")
                    return False
                else:
                    if youtubeUrl:
                        doc_snap.reference.set({"youtubeId": youtubeId, "youtubeUrl": youtubeUrl, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
                        print(f"HALL OF FAME: UZUPEŁNIONO YT dla {doc_id}")
                        return True
                    return False
            clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", found_line)
            clean = re.sub(r"https?://\S+", "", clean)
            clean = re.sub(r"^\s*1(?!\d)[\.\).]?\s*", "", clean).strip()
            wykonawca = ""
            tytul = ""
            if " - " in clean:
                wykonawca, tytul = [x.strip() for x in clean.split(" - ", 1)]
            elif " – " in clean:
                wykonawca, tytul = [x.strip() for x in clean.split(" – ", 1)]
            else:
                parts = re.split(r"\s{2,}|\t+", clean)
                if len(parts) >= 2:
                    wykonawca = parts[0].strip()
                    tytul = parts[1].strip()
                else:
                    tytul = clean
            if not tytul:
                continue
            doc_ref.set({
                "notowanie": f"TOP {top_size} #{doc_id}",
                "numerNotowania": numer,
                "sourceChannelId": str(cid),
                "sourceMessageId": str(msg.id),
                "tytul": tytul,
                "wykonawca": wykonawca,
                "youtubeId": youtubeId,
                "youtubeUrl": youtubeUrl,
                "updated_at": firestore.SERVER_TIMESTAMP
            })
            print(f"HALL OF FAME: ZAPISANO NOWE {doc_id} (TOP {top_size}): {wykonawca} - {tytul}")
            return True
        print("HALL OF FAME: nie znaleziono nowego miejsca nr 1")
        return False
    except Exception as e:
        print(f"ERROR HOF LATEST: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

async def sync_hall_of_fame_initial():
    try:
        sys_ref = db.collection(HALL_OF_FAME_COLLECTION).document(HALL_OF_FAME_SYSTEM_DOC)
        sys_doc = sys_ref.get()
        if sys_doc.exists:
            data = sys_doc.to_dict() or {}
            if data.get("initialized"):
                print("HALL OF FAME: już zainicjalizowane - pomijam ściąganie wszystkich przy restarcie")
                return
        print("HALL OF FAME: pierwsze uruchomienie w historii - ściągam WSZYSTKIE")
        await sync_hall_of_fame_all()
        sys_ref.set({
            "initialized": True,
            "initialized_at": firestore.SERVER_TIMESTAMP,
            "description": "HALL OF FAME - tylko nr 1, TOP 10/15/20 - inicjalizacja raz"
        }, merge=True)
    except Exception as e:
        print(f"ERROR HOF INIT: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

@tasks.loop(minutes=30)
async def check_hall_of_fame():
    try:
        now = datetime.now(WARSAW)
        if now.weekday() != 0 or now.hour < 6:
            return
        week_key = now.strftime("%Y-%W")
        sys_ref = db.collection(HALL_OF_FAME_COLLECTION).document(HALL_OF_FAME_SYSTEM_DOC)
        sys_doc = sys_ref.get()
        data = {}
        if sys_doc.exists:
            data = sys_doc.to_dict() or {}
        if data.get("last_week") == week_key:
            return
        print(f"HALL OF FAME: poniedziałkowe sprawdzenie {week_key} (od 06:00)")
        success = await sync_hall_of_fame_latest()
        if success:
            sys_ref.set({"last_week": week_key, "last_check": firestore.SERVER_TIMESTAMP}, merge=True)
            print(f"HALL OF FAME: sprawdzenie zakończone sukcesem — tydzień {week_key} oznaczony jako wykonany")
        else:
            print("HALL OF FAME: nie znaleziono nowego #1 — ponowię sprawdzenie za 30 minut")
    except Exception as e:
        print(f"ERROR HOF checker: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


# ============================================================
# ODZYSKIWANIE PIN
# ============================================================

async def create_recovery_request(discord_id: int, nick: str, source: str = "app"):
    try:
        discord_id_str = str(discord_id)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(minutes=RECOVERY_TIMEOUT_MINUTES)
        recovery_ref = db.collection(RECOVERY_COLLECTION).document(discord_id_str)
        recovery_ref.set({
            "discordId": discord_id_str,
            "nick": nick,
            "status": "WAITING_CONFIRMATION",
            "source": source,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "expiresAt": expires
        })
        user = bot.get_user(discord_id)
        if user is None:
            try:
                user = await bot.fetch_user(discord_id)
            except Exception as e:
                print(f"RECOVERY: nie można pobrać użytkownika Discord {discord_id}: {e}")
                return False
        try:
            await user.send(
                "🔐 **AI ARENA FM — ODZYSKIWANIE PIN-U**\n\n"
                f"Została zgłoszona prośba o utworzenie nowego PIN-u dla konta **{nick}**.\n\n"
                "Jeśli to Ty chcesz utworzyć nowy PIN, odpowiedz tutaj:\n\n**TAK**\n\n"
                "Jeśli nie zgłaszałeś tej prośby, zignoruj tę wiadomość."
            )
            print(f"RECOVERY: wysłano DM do {user} ({discord_id})")
            return True
        except discord.Forbidden:
            print(f"RECOVERY: użytkownik {discord_id} ma zablokowane DM.")
            recovery_ref.set({"status": "DM_FAILED"}, merge=True)
            return False
        except Exception as e:
            print(f"RECOVERY: błąd wysyłania DM: {e}")
            recovery_ref.set({"status": "DM_FAILED"}, merge=True)
            return False
    except Exception as e:
        print(f"ERROR recovery request: {type(e).__name__}: {e}")
        return False

@tasks.loop(seconds=5)
async def check_recovery_requests():
    try:
        collection = db.collection(RECOVERY_COLLECTION)
        docs = collection.where("status", "==", "PENDING").stream()
        for doc in docs:
            data = doc.to_dict()
            discord_id = str(data.get("discordId") or doc.id)
            nick = str(data.get("nick") or "").strip()
            if not discord_id or not nick:
                print(f"RECOVERY: błędne żądanie {doc.id}")
                doc.reference.set({"status": "ERROR", "error": "Brak discordId lub nick"}, merge=True)
                continue
            success = await create_recovery_request(int(discord_id), nick, source="app")
            if success:
                doc.reference.set({"status": "WAITING_CONFIRMATION"}, merge=True)
    except Exception as e:
        print(f"ERROR recovery checker: {type(e).__name__}: {e}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.guild is not None:
        await bot.process_commands(message)
        return
    discord_id = str(message.author.id)
    content = message.content.strip()
    content_lower = content.lower()
    recovery_ref = db.collection(RECOVERY_COLLECTION).document(discord_id)
    recovery_doc = recovery_ref.get()
    if not recovery_doc.exists:
        if ("odzyskaj pin" in content_lower or "odzyskaj_pin" in content_lower or content_lower in ("odzyskaj", "!odzyskaj", "!odzyskaj_pin") or "nie pamiętam pin" in content_lower or "nie pamietam pin" in content_lower):
            user_ref = db.collection("users").document(discord_id)
            user_doc = user_ref.get()
            if not user_doc.exists:
                await message.author.send("❌ Nie znalazłem konta AI Arena FM przypisanego do tego konta Discord.")
                return
            user_data = user_doc.to_dict()
            stored_discord_id = str(user_data.get("discordId") or user_data.get("discord_id") or "")
            nick = str(user_data.get("nick") or "").strip()
            if stored_discord_id != discord_id:
                await message.author.send("❌ To konto AI Arena FM nie jest jeszcze przypisane do tego konta Discord.")
                return
            if not nick:
                await message.author.send("❌ Konto nie posiada nicku.")
                return
            now = datetime.now(timezone.utc)
            expires = now + timedelta(minutes=RECOVERY_TIMEOUT_MINUTES)
            recovery_ref.set({
                "discordId": discord_id,
                "nick": nick,
                "status": "WAITING_CONFIRMATION",
                "source": "discord_dm",
                "createdAt": firestore.SERVER_TIMESTAMP,
                "expiresAt": expires
            })
            await message.author.send(
                "🔐 **AI ARENA FM — ODZYSKIWANIE PIN-U**\n\n"
                f"Znalazłem konto **{nick}** przypisane do tego konta Discord.\n\n"
                "Jeśli to Ty chcesz zmienić PIN, odpowiedz:\n\n**TAK**\n"
            )
            return
        await message.author.send("👋 Cześć!\nJeśli chcesz odzyskać PIN do AI Arena FM, napisz:\n\n**odzyskaj PIN**")
        return
    recovery = recovery_doc.to_dict()
    status = recovery.get("status")
    expires_at = recovery.get("expiresAt")
    is_expired = False
    if expires_at is not None:
        try:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_at:
                is_expired = True
        except Exception as e:
            print(f"RECOVERY: błąd sprawdzania wygaśnięcia: {e}")
    if is_expired or status in ("EXPIRED", "DONE", "ERROR", "DM_FAILED"):
        if ("odzyskaj pin" in content_lower or "odzyskaj_pin" in content_lower or content_lower in ("odzyskaj", "!odzyskaj", "!odzyskaj_pin") or "nie pamiętam pin" in content_lower or "nie pamietam pin" in content_lower):
            try:
                recovery_ref.delete()
            except Exception:
                pass
            user_ref = db.collection("users").document(discord_id)
            user_doc = user_ref.get()
            if not user_doc.exists:
                await message.author.send("❌ Nie znalazłem konta AI Arena FM przypisanego do tego konta Discord.")
                return
            user_data = user_doc.to_dict()
            stored_discord_id = str(user_data.get("discordId") or user_data.get("discord_id") or "")
            nick = str(user_data.get("nick") or "").strip()
            if stored_discord_id != discord_id:
                await message.author.send("❌ To konto AI Arena FM nie jest jeszcze przypisane do tego konta Discord.")
                return
            if not nick:
                await message.author.send("❌ Konto nie posiada nicku.")
                return
            now = datetime.now(timezone.utc)
            expires = now + timedelta(minutes=RECOVERY_TIMEOUT_MINUTES)
            recovery_ref.set({
                "discordId": discord_id,
                "nick": nick,
                "status": "WAITING_CONFIRMATION",
                "source": "discord_dm",
                "createdAt": firestore.SERVER_TIMESTAMP,
                "expiresAt": expires
            })
            await message.author.send(
                "🔐 **AI ARENA FM — ODZYSKIWANIE PIN-U**\n\n"
                f"Znalazłem konto **{nick}** przypisane do tego konta Discord.\n\n"
                "Jeśli to Ty chcesz zmienić PIN, odpowiedz:\n\n**TAK**\n"
            )
            return
        else:
            if is_expired:
                recovery_ref.set({"status": "EXPIRED", "expiredAt": firestore.SERVER_TIMESTAMP}, merge=True)
                await message.author.send("⏰ Żądanie odzyskania PIN-u wygasło.\nNapisz ponownie **odzyskaj PIN**, aby rozpocząć procedurę.")
                return
    if status == "WAITING_CONFIRMATION":
        if content.upper() == "TAK":
            recovery_ref.set({"status": "WAITING_NEW_PIN", "confirmedAt": firestore.SERVER_TIMESTAMP}, merge=True)
            await message.author.send("✅ **Potwierdzone.**\n\nPodaj teraz **nowy PIN**.\nPIN musi zawierać od **4 do 6 cyfr**.")
            return
        await message.author.send("Aby potwierdzić zmianę PIN-u, odpowiedz dokładnie:\n\n**TAK**")
        return
    if status == "WAITING_NEW_PIN":
        new_pin = content
        if not re.fullmatch(r"\d{4,6}", new_pin):
            await message.author.send("❌ Nieprawidłowy PIN.\nPIN musi zawierać wyłącznie od **4 do 6 cyfr**.\nSpróbuj ponownie.")
            return
        try:
            user_ref = db.collection("users").document(discord_id)
            user_doc = user_ref.get()
            if not user_doc.exists:
                recovery_ref.set({"status": "ERROR", "error": "Nie znaleziono konta users"}, merge=True)
                await message.author.send("❌ Nie znalazłem Twojego konta w bazie.\nSkontaktuj się z organizatorem.")
                return
            user_data = user_doc.to_dict()
            stored_discord_id = str(user_data.get("discordId") or user_data.get("discord_id") or "")
            if stored_discord_id != discord_id:
                recovery_ref.set({"status": "ERROR", "error": "Niezgodność discordId"}, merge=True)
                await message.author.send("❌ Nie udało się zweryfikować Twojego konta.")
                return
            new_hash = hash_pin(new_pin)
            user_ref.set({"pinHash": new_hash, "updatedAt": firestore.SERVER_TIMESTAMP}, merge=True)
            recovery_ref.set({"status": "DONE", "completedAt": firestore.SERVER_TIMESTAMP}, merge=True)
            await message.author.send("✅ **PIN został zmieniony.**\nMożesz teraz zalogować się w aplikacji AI Arena FM przy użyciu nowego PIN-u.")
            return
        except Exception as e:
            print(f"ERROR ustawiania nowego PIN: {type(e).__name__}: {e}")
            await message.author.send("❌ Wystąpił błąd podczas zmiany PIN-u.\nSpróbuj ponownie później.")
            return
    await message.author.send("Jeśli chcesz rozpocząć odzyskiwanie PIN-u, napisz **odzyskaj PIN**.")

@bot.command(name="odzyskaj_test")
async def odzyskaj_test(ctx):
    discord_id = str(ctx.author.id)
    user_ref = db.collection("users").document(discord_id)
    user_doc = user_ref.get()
    if not user_doc.exists:
        await ctx.send(f"❌ Nie znaleziono Twojego konta w users/{discord_id}.")
        return
    user_data = user_doc.to_dict()
    stored_discord_id = str(user_data.get("discordId") or "")
    nick = str(user_data.get("nick") or "").strip()
    if stored_discord_id != discord_id:
        await ctx.send("❌ Niezgodność discordId w Firebase.")
        return
    if not nick:
        await ctx.send("❌ Konto nie posiada pola nick.")
        return
    success = await create_recovery_request(int(discord_id), nick, source="test")
    if success:
        await ctx.send("✅ Test uruchomiony.\nSprawdź teraz **prywatne wiadomości Discord**.")
    else:
        await ctx.send("❌ Nie udało się wysłać wiadomości prywatnej.\nSprawdź, czy bot może wysyłać Ci DM.")


# ============================================================
# START
# ============================================================

@bot.event
async def on_ready():
    print(f"READY {bot.user} - tryb WT/SR/CZW 00:00-23:59 (Warszawa)")
    if not check_lista.is_running():
        check_lista.start()
    if not check_glosowanie_link.is_running():
        check_glosowanie_link.start()
    if not check_typy_cleanup.is_running():
        check_typy_cleanup.start()
    if not check_recovery_requests.is_running():
        check_recovery_requests.start()
    if not check_hall_of_fame.is_running():
        check_hall_of_fame.start()
    if not check_franc_gada.is_running():
        check_franc_gada.start()
    if not sync_members_loop.is_running():
        sync_members_loop.start()

    print("TEST: START sync_members() - pełny sync przy starcie")
    await sync_members()
    print("TEST: KONIEC sync_members()")

    print("TEST: START sync_archiwum()")
    await sync_archiwum()
    print("TEST: KONIEC sync_archiwum()")

    print("TEST: START sync_hall_of_fame_initial()")
    await sync_hall_of_fame_initial()
    print("TEST: KONIEC sync_hall_of_fame_initial()")

    print("RECOVERY: system odzyskiwania PIN aktywny")
    print("CZŁONKOWIE: system na żywo (on_member_join/update) + backup co 6h aktywny")

bot.run(os.getenv("DISCORD_TOKEN"))
