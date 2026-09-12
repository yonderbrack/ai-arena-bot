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
    is_active_day = now.weekday() in [1, 2, 3]
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

        # SKLEJA WSZYSTKIE PONUMEROWANE Z OSTATNICH 100 WIADOMOSCI - 1-20 + 21-40
        numbered = {}  # numer -> linia
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
                # bierzemy pierwsze wystapienie numeru (najnowsza wiadomosc)
                if num not in numbered:
                    numbered[num] = raw.strip()

        if len(numbered) < 5:
            print("Nie znaleziono min 5 ponumerowanych")
            return

        # posortuj po numerze 1,2,3...40
        sorted_nums = sorted(numbered.keys())
        final_list = [numbered[n] for n in sorted_nums]

        # jesli jest luka np 1-20 i 21-40 to mamy 40, jesli tylko 1-20 to 20
        print(f"Znalazlem ponumerowane numery: {sorted_nums[:5]}...{sorted_nums[-5:]} razem {len(final_list)}")
        print(f"Przyklad: {final_list[:2]}")

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
# LINKI DO GŁOSOWANIA — NOWE - ZMIENIA SIĘ RAZEM Z LISTĄ
# Kanał #linki-do-glosowania - ID 1517609248765382776 z Twojego screena
# ============================================================

@tasks.loop(seconds=60)
async def check_glosowanie_link():
    try:
        # ID kanału #linki-do-glosowania - możesz nadpisać ENV GLOSOWANIE_CHANNEL_ID
        cid_raw = os.getenv("GLOSOWANIE_CHANNEL_ID") or os.getenv("LINKI_CHANNEL_ID") or "1517609248765382776"
        if not cid_raw:
            return
        cid = int(cid_raw)
        ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
        if ch is None:
            # print tylko raz żeby nie spamować
            print(f"GLOSOWANIE: nie znaleziono kanału {cid}")
            return

        # Regex na Google Forms / Sheets - z Twojego screena: https://forms.gle/HpmeGTc6tCqZKvqM7
        # Obsługuje forms.gle, docs.google.com/forms, docs.google.com/spreadsheets, forms.google.com
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
                # wyczyść link z końcowych znaków interpunkcyjnych
                raw_link = m.group(0).rstrip(").,!;")
                found_link = raw_link
                found_msg_id = str(msg.id)
                found_msg_date = msg.created_at
                break  # bierzemy najnowszy

        if not found_link:
            print("GLOSOWANIE: nie znaleziono linka w ostatnich 20 wiadomościach")
            return

        # Sprawdź czy link już jest zapisany - żeby nie nadpisywać bez potrzeby
        doc_ref = db.collection("config").document("glosowanie")
        current_doc = doc_ref.get()
        current_link = ""
        if current_doc.exists:
            current_link = current_doc.to_dict().get("glosuj_link", "") or ""

        if current_link == found_link:
            # print(f"GLOSOWANIE: link bez zmian {found_link}")
            return

        # Zapisz do Firebase - tego słucha apka w GlosujTab
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

        # Fallback dla starej apki - config/glosuj
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
# TYPY — AUTOMATYCZNE CZYSZCZENIE STARYCH TYPÓW
#
# NIE DOTYKA:
# - historia
# - Wyniki
# - lista
# - users
# - czlonkowie
# - pin_recovery
#
# CZYŚCI WYŁĄCZNIE:
# - typy
# ============================================================

TYPY_SYSTEM_DOC = "_system_weekly_cleanup"


def get_current_week_start():
    """
    Zwraca początek bieżącego tygodnia:
    poniedziałek 00:00 czasu Europe/Warsaw.
    """
    now = datetime.now(WARSAW)

    monday = (
        now - timedelta(days=now.weekday())
    ).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    return monday


def cleanup_old_typy(cutoff_datetime):
    """
    Usuwa WYŁĄCZNIE dokumenty z kolekcji 'typy',
    których timestamp jest starszy niż cutoff_datetime.

    Dokument systemowy _system_weekly_cleanup zostaje.
    """

    try:
        collection = db.collection("typy")

        cutoff_ms = int(
            cutoff_datetime.timestamp() * 1000
        )

        docs = list(collection.stream())

        deleted_count = 0
        skipped_count = 0

        batch = db.batch()
        batch_count = 0

        for doc in docs:

            # Nigdy nie usuwamy dokumentu technicznego
            if doc.id == TYPY_SYSTEM_DOC:
                continue

            data = doc.to_dict() or {}

            timestamp = data.get("timestamp")

            # Jeśli dokument nie ma timestampu,
            # NIE USUWAMY go automatycznie.
            if timestamp is None:
                skipped_count += 1
                continue

            try:
                timestamp_ms = int(timestamp)
            except (TypeError, ValueError):
                skipped_count += 1
                continue

            # Usuwamy tylko wpisy starsze od początku
            # bieżącego tygodnia.
            if timestamp_ms < cutoff_ms:

                batch.delete(doc.reference)
                batch_count += 1
                deleted_count += 1

                # Firestore batch max 500 operacji.
                # Zostawiamy bezpieczny limit 400.
                if batch_count >= 400:
                    batch.commit()

                    batch = db.batch()
                    batch_count = 0

        if batch_count > 0:
            batch.commit()

        print(
            f"TYPY: czyszczenie zakończone. "
            f"Usunięto: {deleted_count}, "
            f"pominięto bez poprawnego timestamp: {skipped_count}"
        )

        return deleted_count

    except Exception as e:

        print(
            f"ERROR TYPY cleanup: "
            f"{type(e).__name__}: {e}"
        )

        import traceback
        traceback.print_exc()

        return 0


@tasks.loop(seconds=30)
async def check_typy_cleanup():

    try:

        now = datetime.now(WARSAW)

        current_week_start = get_current_week_start()

        current_week_key = current_week_start.strftime(
            "%Y-%m-%d"
        )

        system_ref = (
            db.collection("typy")
            .document(TYPY_SYSTEM_DOC)
        )

        system_doc = system_ref.get()

        system_data = {}

        if system_doc.exists:
            system_data = system_doc.to_dict() or {}

        initialized = bool(
            system_data.get("initialized")
        )

        cleanup_week = (
            system_data.get("cleanup_week")
        )

        # ====================================================
        # PIERWSZE URUCHOMIENIE
        #
        # Usuwamy tylko stare wpisy sprzed bieżącego
        # tygodnia.
        #
        # Aktualne typy zostają.
        # ====================================================

        if not initialized:

            print(
                "TYPY: pierwsze uruchomienie systemu "
                "czyszczenia."
            )

            deleted_count = cleanup_old_typy(
                current_week_start
            )

            system_ref.set({
                "initialized": True,
                "initialized_at": firestore.SERVER_TIMESTAMP,
                "cleanup_week": current_week_key,
                "last_cleanup_at": firestore.SERVER_TIMESTAMP,
                "last_deleted_count": deleted_count,
                "description": (
                    "Automatyczne czyszczenie starych typów. "
                    "Dokument techniczny."
                )
            }, merge=True)

            print(
                f"TYPY: pierwsze czyszczenie wykonane. "
                f"Usunięto {deleted_count} starych wpisów."
            )

            return

        # ====================================================
        # COTYGODNIOWE CZYSZCZENIE
        #
        # Tylko poniedziałek od 12:00.
        # ====================================================

        if now.weekday() != 0:
            return

        if now.hour < 12:
            return

        if cleanup_week == current_week_key:
            return

        print(
            f"TYPY: rozpoczęto cotygodniowe czyszczenie "
            f"dla tygodnia {current_week_key}."
        )

        deleted_count = cleanup_old_typy(
            current_week_start
        )

        system_ref.set({
            "cleanup_week": current_week_key,
            "last_cleanup_at": firestore.SERVER_TIMESTAMP,
            "last_deleted_count": deleted_count
        }, merge=True)

        print(
            f"TYPY: cotygodniowe czyszczenie zakończone. "
            f"Usunięto {deleted_count} starych wpisów."
        )

    except Exception as e:

        print(
            f"ERROR typy cleanup checker: "
            f"{type(e).__name__}: {e}"
        )

        import traceback
        traceback.print_exc()


async def sync_members():
    try:
        guild_id = int(
            os.getenv("GUILD_ID")
            or "1515466354113777715"
        )

        guild = bot.get_guild(guild_id)

        if guild is None:
            print(
                f"ERROR członkowie: "
                f"nie znaleziono serwera {guild_id}"
            )
            return

        print(
            f"CZŁONKOWIE: znaleziono serwer: "
            f"{guild.name} ({guild.id})"
        )

        print(
            "CZŁONKOWIE: pobieram listę przez Discord API..."
        )

        members = []

        async for member in guild.fetch_members(limit=None):
            members.append(member)

        print(
            f"CZŁONKOWIE: Discord API zwróciło "
            f"{len(members)} członków"
        )

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

                print(
                    f"CZŁONKOWIE: zapisano partię "
                    f"{batch_count} "
                    f"(łącznie {saved_count})"
                )

                batch = db.batch()
                batch_count = 0

        if batch_count > 0:
            batch.commit()
            saved_count += batch_count

        print(
            f"CZŁONKOWIE: zapisano/zaktualizowano "
            f"{saved_count} rekordów w Firebase "
            f"→ czlonkowie"
        )

    except Exception as e:
        print(
            f"ERROR członkowie: "
            f"{type(e).__name__}: {e}"
        )


# ============================================================
# ARCHIWUM UTWORÓW — ISTNIEJĄCY MECHANIZM - BEZ ZMIAN
# ============================================================

async def sync_archiwum():
    try:
        print(
            "ARCHIWUM: rozpoczynam pobieranie "
            "historii kanału..."
        )

        cid = 1518213312234655825

        ch = (
            bot.get_channel(cid)
            or await bot.fetch_channel(cid)
        )

        print(
            f"ARCHIWUM: kanał "
            f"{ch.name} ({ch.id})"
        )

        entries = []
        total_msgs = 0

        async for msg in ch.history(limit=None):
            total_msgs += 1

            if not msg.content:
                continue

            text = msg.content

            text = re.sub(
                r"\[([^\]]+)\]\([^)]+\)",
                r"\1",
                text
            )

            text = re.sub(
                r"https?://\S+",
                "",
                text
            )

            for raw in text.splitlines():
                raw = raw.strip()

                if not raw:
                    continue

                upper = raw.upper()

                if (
                    upper.startswith("TOP 10")
                    or "LISTA PRZEBOJÓW" in upper
                    or raw.startswith("🏆")
                ):
                    continue

                raw_no_num = re.sub(
                    r"^\s*\d+[\.\)]?\s*",
                    "",
                    raw
                ).strip()

                if not raw_no_num:
                    continue

                parts = re.split(
                    r"\s{2,}|\t+",
                    raw_no_num
                )

                if len(parts) >= 2:
                    wykonawca = parts[0].strip()
                    tytul = parts[1].strip()

                    if wykonawca and tytul:
                        entries.append({
                            "wykonawca": wykonawca,
                            "tytul": tytul
                        })

        print(
            f"ARCHIWUM: przeskanowano "
            f"{total_msgs} wiadomości, "
            f"znaleziono {len(entries)} "
            f"wpisów przed dedup"
        )

        unique = {}
        display_entries = []

        for entry in entries:
            wykonawca = entry["wykonawca"].strip()
            tytul = entry["tytul"].strip()

            key = (
                re.sub(
                    r"\s+",
                    " ",
                    wykonawca
                ).lower(),

                re.sub(
                    r"\s+",
                    " ",
                    tytul
                ).lower()
            )

            if key not in unique:
                unique[key] = True

                display_entries.append({
                    "wykonawca": wykonawca,
                    "tytul": tytul
                })

        print(
            f"ARCHIWUM: po usunięciu duplikatów "
            f"{len(display_entries)} utworów"
        )

        if len(display_entries) == 0:
            print(
                "ARCHIWUM: 0 utworów - "
                "nie nadpisuje Firebase!"
            )
            return

        db.collection("archiwum").document(
            "utwory"
        ).set({
            "utwory": display_entries,
            "count": len(display_entries),
            "updated_at": firestore.SERVER_TIMESTAMP
        })

        print(
            f"ARCHIWUM: ZAPISANO "
            f"{len(display_entries)} utworów "
            f"do archiwum/utwory"
        )

    except Exception as e:
        print(
            f"ERROR archiwum: "
            f"{type(e).__name__}: {e}"
        )



# ============================================================
# HALL OF FAME — TYLKO NR 1 — TOP 10/15/20
# 1 RAZ przy pierwszym uruchomieniu wszystkie, potem tylko poniedziałek ostatnie
# ============================================================

HALL_OF_FAME_CHANNEL_ID = 1518213312234655825
HALL_OF_FAME_COLLECTION = "HALL OF FAME"
HALL_OF_FAME_SYSTEM_DOC = "_system_hall_of_fame"


async def sync_hall_of_fame_all():
    try:
        print(
            "HALL OF FAME: PIERWSZE URUCHOMIENIE - ściągam WSZYSTKIE nr 1..."
        )

        cid = HALL_OF_FAME_CHANNEL_ID

        ch = (
            bot.get_channel(cid)
            or await bot.fetch_channel(cid)
        )

        if ch is None:
            print(
                f"HOF: nie znaleziono kanału {cid}"
            )
            return 0

        added = 0
        skipped = 0

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

            m_top = re.search(
                r"TOP\s*(\d+)\s*#\s*0*(\d+)",
                msg.content,
                re.IGNORECASE
            )

            if m_top:
                top_size = int(m_top.group(1))
                numer = int(m_top.group(2))
            else:
                m_num = re.search(
                    r"#\s*0*(\d+)",
                    msg.content
                )
                if not m_num:
                    continue
                top_size = 10
                numer = int(m_num.group(1))

            doc_id = f"{numer:03d}"

            doc_ref = db.collection(HALL_OF_FAME_COLLECTION).document(doc_id)

            if doc_ref.get().exists:
                skipped += 1
                continue

            youtubeUrl = ""
            youtubeId = ""

            urls = re.findall(
                r"https?://[^\s\)\]\<\>\"']+",
                found_line
            )

            for u in urls:
                uc = u.rstrip(").,!;")
                if "youtu" in uc.lower():
                    youtubeUrl = uc
                    m_id = re.search(
                        r"(?:v=|youtu\.be/|shorts/)([^&\s\?\))]+)",
                        uc
                    )
                    if m_id:
                        youtubeId = m_id.group(1)
                    break

            clean = re.sub(
                r"\[([^\]]+)\]\([^)]+\)",
                r"\1",
                found_line
            )

            clean = re.sub(
                r"https?://\S+",
                "",
                clean
            )

            clean = re.sub(
                r"^\s*1(?!\d)[\.\).]?\s*",
                "",
                clean
            ).strip()

            wykonawca = ""
            tytul = ""

            if " - " in clean:
                wykonawca, tytul = [x.strip() for x in clean.split(" - ", 1)]
            elif " – " in clean:
                wykonawca, tytul = [x.strip() for x in clean.split(" – ", 1)]
            else:
                parts = re.split(
                    r"\s{2,}|\t+",
                    clean
                )
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

        print(
            f"HALL OF FAME: KONIEC pierwszego ściągania. Dodano: {added}, pominięto: {skipped}"
        )

        return added

    except Exception as e:
        print(
            f"ERROR HOF ALL: {type(e).__name__}: {e}"
        )
        import traceback
        traceback.print_exc()
        return 0


async def sync_hall_of_fame_latest():
    try:
        print(
            "HALL OF FAME: szukam ostatniego miejsca nr 1..."
        )

        cid = HALL_OF_FAME_CHANNEL_ID

        ch = (
            bot.get_channel(cid)
            or await bot.fetch_channel(cid)
        )

        if ch is None:
            print(
                f"HOF: nie znaleziono kanału {cid}"
            )
            return

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

            m_top = re.search(
                r"TOP\s*(\d+)\s*#\s*0*(\d+)",
                msg.content,
                re.IGNORECASE
            )

            if m_top:
                top_size = int(m_top.group(1))
                numer = int(m_top.group(2))
            else:
                m_num = re.search(
                    r"#\s*0*(\d+)",
                    msg.content
                )
                if not m_num:
                    continue
                top_size = 10
                numer = int(m_num.group(1))

            doc_id = f"{numer:03d}"

            doc_ref = db.collection(HALL_OF_FAME_COLLECTION).document(doc_id)

            if doc_ref.get().exists:
                print(
                    f"HALL OF FAME: {doc_id} już istnieje - nie ma nowego"
                )
                return

            youtubeUrl = ""
            youtubeId = ""

            urls = re.findall(
                r"https?://[^\s\)\]\<\>\"']+",
                found_line
            )

            for u in urls:
                uc = u.rstrip(").,!;")
                if "youtu" in uc.lower():
                    youtubeUrl = uc
                    m_id = re.search(
                        r"(?:v=|youtu\.be/|shorts/)([^&\s\?\))]+)",
                        uc
                    )
                    if m_id:
                        youtubeId = m_id.group(1)
                    break

            clean = re.sub(
                r"\[([^\]]+)\]\([^)]+\)",
                r"\1",
                found_line
            )

            clean = re.sub(
                r"https?://\S+",
                "",
                clean
            )

            clean = re.sub(
                r"^\s*1(?!\d)[\.\).]?\s*",
                "",
                clean
            ).strip()

            wykonawca = ""
            tytul = ""

            if " - " in clean:
                wykonawca, tytul = [x.strip() for x in clean.split(" - ", 1)]
            elif " – " in clean:
                wykonawca, tytul = [x.strip() for x in clean.split(" – ", 1)]
            else:
                parts = re.split(
                    r"\s{2,}|\t+",
                    clean
                )
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

            print(
                f"HALL OF FAME: ZAPISANO NOWE {doc_id} (TOP {top_size}): {wykonawca} - {tytul}"
            )
            return

        print(
            "HALL OF FAME: nie znaleziono nowego miejsca nr 1"
        )

    except Exception as e:
        print(
            f"ERROR HOF LATEST: {type(e).__name__}: {e}"
        )
        import traceback
        traceback.print_exc()


async def sync_hall_of_fame_initial():
    try:
        sys_ref = db.collection(HALL_OF_FAME_COLLECTION).document(HALL_OF_FAME_SYSTEM_DOC)
        sys_doc = sys_ref.get()

        if sys_doc.exists:
            data = sys_doc.to_dict() or {}
            if data.get("initialized"):
                print(
                    "HALL OF FAME: już zainicjalizowane - pomijam ściąganie wszystkich przy restarcie"
                )
                return

        print(
            "HALL OF FAME: pierwsze uruchomienie w historii - ściągam WSZYSTKIE"
        )

        await sync_hall_of_fame_all()

        sys_ref.set({
            "initialized": True,
            "initialized_at": firestore.SERVER_TIMESTAMP,
            "description": "HALL OF FAME - tylko nr 1, TOP 10/15/20 - inicjalizacja raz"
        }, merge=True)

    except Exception as e:
        print(
            f"ERROR HOF INIT: {type(e).__name__}: {e}"
        )
        import traceback
        traceback.print_exc()


@tasks.loop(minutes=30)
async def check_hall_of_fame():
    try:
        now = datetime.now(WARSAW)

        if now.weekday() != 0:
            return

        if now.hour < 12:
            return

        week_key = now.strftime("%Y-%W")

        sys_ref = db.collection(HALL_OF_FAME_COLLECTION).document(HALL_OF_FAME_SYSTEM_DOC)

        sys_doc = sys_ref.get()

        data = {}
        if sys_doc.exists:
            data = sys_doc.to_dict() or {}

        if data.get("last_week") == week_key:
            return

        print(
            f"HALL OF FAME: poniedziałkowe sprawdzenie {week_key}"
        )

        await sync_hall_of_fame_latest()

        sys_ref.set({
            "last_week": week_key,
            "last_check": firestore.SERVER_TIMESTAMP
        }, merge=True)

    except Exception as e:
        print(
            f"ERROR HOF checker: {type(e).__name__}: {e}"
        )
        import traceback
        traceback.print_exc()



# ============================================================
# ODZYSKIWANIE PIN — UTWORZENIE ŻĄDANIA - BEZ ZMIAN
# ============================================================

async def create_recovery_request(
    discord_id: int,
    nick: str,
    source: str = "app"
):
    try:
        discord_id_str = str(discord_id)

        now = datetime.now(timezone.utc)
        expires = now + timedelta(
            minutes=RECOVERY_TIMEOUT_MINUTES
        )

        recovery_ref = (
            db.collection(RECOVERY_COLLECTION)
            .document(discord_id_str)
        )

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
                print(
                    f"RECOVERY: nie można pobrać "
                    f"użytkownika Discord {discord_id}: {e}"
                )
                return False

        try:
            await user.send(
                "🔐 **AI ARENA FM — ODZYSKIWANIE PIN-U**\n\n"
                f"Została zgłoszona prośba o utworzenie "
                f"nowego PIN-u dla konta **{nick}**.\n\n"
                "Jeśli to Ty chcesz utworzyć nowy PIN, "
                "odpowiedz tutaj:\n\n"
                "**TAK**\n\n"
                "Jeśli nie zgłaszałeś tej prośby, "
                "zignoruj tę wiadomość."
            )

            print(
                f"RECOVERY: wysłano DM do "
                f"{user} ({discord_id})"
            )

            return True

        except discord.Forbidden:
            print(
                f"RECOVERY: użytkownik "
                f"{discord_id} ma zablokowane DM."
            )

            recovery_ref.set({
                "status": "DM_FAILED"
            }, merge=True)

            return False

        except Exception as e:
            print(
                f"RECOVERY: błąd wysyłania DM: {e}"
            )

            recovery_ref.set({
                "status": "DM_FAILED"
            }, merge=True)

            return False

    except Exception as e:
        print(
            f"ERROR recovery request: "
            f"{type(e).__name__}: {e}"
        )

        return False


# ============================================================
# ODZYSKIWANIE PIN — SPRAWDZENIE ŻĄDANIA Z FIREBASE - BEZ ZMIAN
# ============================================================

@tasks.loop(seconds=5)
async def check_recovery_requests():
    try:
        collection = db.collection(
            RECOVERY_COLLECTION
        )

        docs = (
            collection
            .where(
                "status",
                "==",
                "PENDING"
            )
            .stream()
        )

        for doc in docs:
            data = doc.to_dict()

            discord_id = str(
                data.get("discordId")
                or doc.id
            )

            nick = str(
                data.get("nick")
                or ""
            ).strip()

            if not discord_id or not nick:
                print(
                    "RECOVERY: błędne żądanie "
                    f"{doc.id}"
                )

                doc.reference.set({
                    "status": "ERROR",
                    "error": "Brak discordId lub nick"
                }, merge=True)

                continue

            success = await create_recovery_request(
                int(discord_id),
                nick,
                source="app"
            )

            if success:
                doc.reference.set({
                    "status": "WAITING_CONFIRMATION"
                }, merge=True)

    except Exception as e:
        print(
            f"ERROR recovery checker: "
            f"{type(e).__name__}: {e}"
        )


# ============================================================
# ODZYSKIWANIE PIN — OBSŁUGA WIADOMOŚCI PRYWATNYCH - FIX PĘTLI
# ============================================================

@bot.event
async def on_message(message):

    # Bot nie obsługuje własnych wiadomości
    if message.author.bot:
        return

    # Wiadomości na serwerze -> normalne komendy
    if message.guild is not None:
        await bot.process_commands(message)
        return

    # ========================================================
    # DM - BOT AUTOMATYCZNIE WIE, KTO PISZE
    # ========================================================

    discord_id = str(message.author.id)
    content = message.content.strip()
    content_lower = content.lower()

    recovery_ref = (
        db.collection(RECOVERY_COLLECTION)
        .document(discord_id)
    )

    recovery_doc = recovery_ref.get()

    # --------------------------------------------------------
    # BRAK AKTYWNEGO ŻĄDANIA:
    # "odzyskaj PIN" rozpoczyna odzyskiwanie
    # --------------------------------------------------------

    if not recovery_doc.exists:
        if (
            "odzyskaj pin" in content_lower
            or "odzyskaj_pin" in content_lower
            or content_lower in ("odzyskaj", "!odzyskaj", "!odzyskaj_pin")
            or "nie pamiętam pin" in content_lower
            or "nie pamietam pin" in content_lower
        ):
            user_ref = (
                db.collection("users")
                .document(discord_id)
            )

            user_doc = user_ref.get()

            if not user_doc.exists:
                await message.author.send(
                    "❌ Nie znalazłem konta AI Arena FM "
                    "przypisanego do tego konta Discord."
                )
                print(
                    f"RECOVERY: brak users/{discord_id}"
                )
                return

            user_data = user_doc.to_dict()

            stored_discord_id = str(
                user_data.get("discordId")
                or user_data.get("discord_id")
                or ""
            )

            nick = str(
                user_data.get("nick")
                or ""
            ).strip()

            # Jeżeli konto istnieje, ale nie ma jeszcze Discord ID,
            # nie pozwalamy przypadkowo przejąć konta.
            if stored_discord_id != discord_id:
                await message.author.send(
                    "❌ To konto AI Arena FM nie jest jeszcze "
                    "przypisane do tego konta Discord. "
                    "Skontaktuj się z organizatorem."
                )
                print(
                    f"RECOVERY SECURITY: discordId mismatch "
                    f"users/{discord_id}"
                )
                return

            if not nick:
                await message.author.send(
                    "❌ Konto nie posiada nicku. "
                    "Skontaktuj się z organizatorem."
                )
                return

            now = datetime.now(timezone.utc)
            expires = now + timedelta(
                minutes=RECOVERY_TIMEOUT_MINUTES
            )

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
                f"Znalazłem konto **{nick}** przypisane do tego "
                "konta Discord.\n\n"
                "Jeśli to Ty chcesz zmienić PIN, odpowiedz:\n\n"
                "**TAK**\n\n"
                "Jeśli nie prosiłeś o zmianę PIN-u, zignoruj "
                "tę wiadomość."
            )

            print(
                f"RECOVERY: rozpoczęto przez DM dla "
                f"{message.author} ({discord_id})"
            )
            return

        await message.author.send(
            "👋 Cześć!\n\n"
            "Jeśli chcesz odzyskać PIN do AI Arena FM, "
            "napisz:\n\n"
            "**odzyskaj PIN**"
        )
        return

    recovery = recovery_doc.to_dict()
    status = recovery.get("status")

    # ========================================================
    # SPRAWDZENIE CZASU WAŻNOŚCI - FIX PĘTLI EXPIRED
    # ========================================================

    expires_at = recovery.get("expiresAt")
    is_expired = False

    if expires_at is not None:
        try:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(
                    tzinfo=timezone.utc
                )

            if datetime.now(timezone.utc) > expires_at:
                is_expired = True

        except Exception as e:
            print(
                f"RECOVERY: błąd sprawdzania "
                f"wygaśnięcia: {e}"
            )

    # Jeśli wygasło lub jest w stanie końcowym - pozwól zrestartować przez "odzyskaj PIN"
    if is_expired or status in ("EXPIRED", "DONE", "ERROR", "DM_FAILED"):
        if (
            "odzyskaj pin" in content_lower
            or "odzyskaj_pin" in content_lower
            or content_lower in ("odzyskaj", "!odzyskaj", "!odzyskaj_pin")
            or "nie pamiętam pin" in content_lower
            or "nie pamietam pin" in content_lower
        ):
            try:
                recovery_ref.delete()
            except Exception:
                pass

            # utwórz nowy request od zera
            user_ref = (
                db.collection("users")
                .document(discord_id)
            )
            user_doc = user_ref.get()

            if not user_doc.exists:
                await message.author.send(
                    "❌ Nie znalazłem konta AI Arena FM "
                    "przypisanego do tego konta Discord."
                )
                return

            user_data = user_doc.to_dict()
            stored_discord_id = str(
                user_data.get("discordId")
                or user_data.get("discord_id")
                or ""
            )
            nick = str(
                user_data.get("nick")
                or ""
            ).strip()

            if stored_discord_id != discord_id:
                await message.author.send(
                    "❌ To konto AI Arena FM nie jest jeszcze "
                    "przypisane do tego konta Discord. "
                    "Skontaktuj się z organizatorem."
                )
                return

            if not nick:
                await message.author.send(
                    "❌ Konto nie posiada nicku. "
                    "Skontaktuj się z organizatorem."
                )
                return

            now = datetime.now(timezone.utc)
            expires = now + timedelta(
                minutes=RECOVERY_TIMEOUT_MINUTES
            )

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
                f"Znalazłem konto **{nick}** przypisane do tego "
                "konta Discord.\n\n"
                "Jeśli to Ty chcesz zmienić PIN, odpowiedz:\n\n"
                "**TAK**\n\n"
                "Jeśli nie prosiłeś o zmianę PIN-u, zignoruj "
                "tę wiadomość."
            )

            print(
                f"RECOVERY: RESTART po {status}/expired dla "
                f"{message.author} ({discord_id})"
            )
            return
        else:
            if is_expired:
                recovery_ref.set({
                    "status": "EXPIRED",
                    "expiredAt": firestore.SERVER_TIMESTAMP
                }, merge=True)

                await message.author.send(
                    "⏰ Żądanie odzyskania PIN-u wygasło.\n\n"
                    "Napisz ponownie **odzyskaj PIN**, "
                    "aby rozpocząć procedurę."
                )
                return

    # ========================================================
    # POTWIERDZENIE — TAK
    # ========================================================

    if status == "WAITING_CONFIRMATION":

        answer = content.upper()

        if answer == "TAK":
            recovery_ref.set({
                "status": "WAITING_NEW_PIN",
                "confirmedAt": firestore.SERVER_TIMESTAMP
            }, merge=True)

            await message.author.send(
                "✅ **Potwierdzone.**\n\n"
                "Podaj teraz **nowy PIN**.\n"
                "PIN musi zawierać od **4 do 6 cyfr**."
            )
            return

        await message.author.send(
            "Aby potwierdzić zmianę PIN-u, odpowiedz dokładnie:\n\n"
            "**TAK**"
        )
        return

    # ========================================================
    # NOWY PIN
    # ========================================================

    if status == "WAITING_NEW_PIN":

        new_pin = content

        if not re.fullmatch(r"\d{4,6}", new_pin):
            await message.author.send(
                "❌ Nieprawidłowy PIN.\n\n"
                "PIN musi zawierać wyłącznie od **4 do 6 cyfr**.\n"
                "Spróbuj ponownie."
            )
            return

        try:
            user_ref = (
                db.collection("users")
                .document(discord_id)
            )

            user_doc = user_ref.get()

            if not user_doc.exists:
                recovery_ref.set({
                    "status": "ERROR",
                    "error": "Nie znaleziono konta users"
                }, merge=True)

                await message.author.send(
                    "❌ Nie znalazłem Twojego konta w bazie.\n\n"
                    "Skontaktuj się z organizatorem."
                )
                return

            user_data = user_doc.to_dict()

            stored_discord_id = str(
                user_data.get("discordId")
                or user_data.get("discord_id")
                or ""
            )

            if stored_discord_id != discord_id:
                recovery_ref.set({
                    "status": "ERROR",
                    "error": "Niezgodność discordId"
                }, merge=True)

                await message.author.send(
                    "❌ Nie udało się zweryfikować Twojego konta.\n\n"
                    "Skontaktuj się z organizatorem."
                )
                return

            new_hash = hash_pin(new_pin)

            # Zachowujemy nazwę pola używaną przez aplikację.
            user_ref.set({
                "pinHash": new_hash,
                "updatedAt": firestore.SERVER_TIMESTAMP
            }, merge=True)

            recovery_ref.set({
                "status": "DONE",
                "completedAt": firestore.SERVER_TIMESTAMP
            }, merge=True)

            await message.author.send(
                "✅ **PIN został zmieniony.**\n\n"
                "Możesz teraz zalogować się w aplikacji "
                "AI Arena FM przy użyciu nowego PIN-u."
            )

            print(
                f"RECOVERY: PIN zmieniony dla "
                f"discordId={discord_id}"
            )
            return

        except Exception as e:
            print(
                f"ERROR ustawiania nowego PIN: "
                f"{type(e).__name__}: {e}"
            )

            await message.author.send(
                "❌ Wystąpił błąd podczas zmiany PIN-u.\n\n"
                "Spróbuj ponownie później."
            )
            return

    # Po zakończonym/nieznanym stanie
    await message.author.send(
        "Jeśli chcesz rozpocząć odzyskiwanie PIN-u, "
        "napisz **odzyskaj PIN**."
    )


# ============================================================
# TEST ODZYSKIWANIA PIN - BEZ ZMIAN
# ============================================================

@bot.command(name="odzyskaj_test")
async def odzyskaj_test(ctx):

    discord_id = str(ctx.author.id)

    print(
        f"RECOVERY TEST: rozpoczęto dla "
        f"{ctx.author} ({discord_id})"
    )

    user_ref = (
        db.collection("users")
        .document(discord_id)
    )

    user_doc = user_ref.get()

    if not user_doc.exists:

        await ctx.send(
            "❌ Nie znaleziono Twojego konta "
            f"w users/{discord_id}."
        )

        print(
            f"RECOVERY TEST ERROR: "
            f"brak users/{discord_id}"
        )

        return

    user_data = user_doc.to_dict()

    stored_discord_id = str(
        user_data.get("discordId")
        or ""
    )

    nick = str(
        user_data.get("nick")
        or ""
    ).strip()

    if stored_discord_id != discord_id:

        await ctx.send(
            "❌ Niezgodność discordId w Firebase."
        )

        print(
            f"RECOVERY TEST SECURITY ERROR: "
            f"{discord_id}"
        )

        return

    if not nick:

        await ctx.send(
            "❌ Konto nie posiada pola nick."
        )

        return

    success = await create_recovery_request(
        int(discord_id),
        nick,
        source="test"
    )

    if success:

        await ctx.send(
            "✅ Test uruchomiony.\n\n"
            "Sprawdź teraz **prywatne wiadomości Discord**."
        )

    else:

        await ctx.send(
            "❌ Nie udało się wysłać "
            "wiadomości prywatnej.\n\n"
            "Sprawdź, czy bot może wysyłać Ci DM."
        )


# ============================================================
# START - DODANY check_glosowanie_link + check_typy_cleanup
# ============================================================

@bot.event
async def on_ready():

    print(
        f"READY {bot.user} - "
        f"tryb WT/SR/CZW 00:00-23:59 (Warszawa)"
    )

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

    print(
        "TEST: START sync_members()"
    )

    await sync_members()

    print(
        "TEST: KONIEC sync_members()"
    )

    print(
        "TEST: START sync_archiwum()"
    )

    await sync_archiwum()

    print(
        "TEST: KONIEC sync_archiwum()"
    )

    print(
        "TEST: START sync_hall_of_fame_initial()"
    )

    await sync_hall_of_fame_initial()

    print(
        "TEST: KONIEC sync_hall_of_fame_initial()"
    )

    print(
        "RECOVERY: system odzyskiwania PIN aktywny"
    )

    print(
        "GLOSOWANIE: system linków do głosowania aktywny - kanał 1517609248765382776"
    )

    print(
        "TYPY: automatyczne czyszczenie starych typów aktywne"
    )

    print(
        "HALL OF FAME: aktywny - raz wszystkie przy starcie, potem tylko poniedziałek ostatnie"
    )


# ============================================================
# BOT
# ============================================================

bot.run(
    os.getenv("DISCORD_TOKEN")
)

