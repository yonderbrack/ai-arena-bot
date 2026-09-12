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
    """
    ARCHIWUM — zapisuje WYŁĄCZNIE zwycięzcę (miejsce 1)
    z każdego notowania.

    Format archiwum:
        TOP10 #123
        1. Wykonawca - Tytuł

    Obsługiwane są również:
        TOP 10 #123
        TOP15 #123
        TOP 20 #123
        1 Wykonawca - Tytuł
        1. Wykonawca    Tytuł

    Każde notowanie zmienia się raz w tygodniu, dlatego
    skan archiwum jest wykonywany tylko w poniedziałek.
    """
    try:
        print("ARCHIWUM: rozpoczynam skanowanie historii kanału...")

        cid = 1518213312234655825
        ch = bot.get_channel(cid) or await bot.fetch_channel(cid)

        if ch is None:
            print(f"ARCHIWUM: nie znaleziono kanału {cid}")
            return False

        print(f"ARCHIWUM: kanał {ch.name} ({ch.id})")

        # Pobieramy wszystkie wiadomości, bo archiwum może być duże.
        messages = []
        total_msgs = 0

        async for msg in ch.history(limit=None, oldest_first=True):
            total_msgs += 1
            if msg.content:
                messages.append(msg.content)

        print(f"ARCHIWUM: przeskanowano {total_msgs} wiadomości")

        # ------------------------------------------------------------
        # WYKRYWANIE NAGŁÓWKA NOTOWANIA
        #
        # TOP10 #123
        # TOP 10 #123
        # TOP15 #123
        # TOP 20 #123
        # ------------------------------------------------------------
        top_pattern = re.compile(
            r"^\s*TOP\s*(10|15|20)\s*#\s*(\d+)\b",
            re.IGNORECASE
        )

        # Miejsce 1:
        # 1
        # 1.
        # 1)
        # 1. Wykonawca - Tytuł
        first_pattern = re.compile(
            r"^\s*1\s*[\.\)]?\s*(.*)$"
        )

        found = {}
        current_chart = None

        for content in messages:
            # Usuwamy markdownowe linki, ale zachowujemy tekst.
            content = re.sub(
                r"\[([^\]]+)\]\([^)]+\)",
                r"\1",
                content
            )

            for raw in content.splitlines():
                raw = raw.strip()

                if not raw:
                    continue

                # Szukamy początku nowego notowania.
                top_match = top_pattern.match(raw)
                if top_match:
                    top_size = int(top_match.group(1))
                    chart_number = int(top_match.group(2))

                    current_chart = {
                        "numer": chart_number,
                        "top": top_size,
                        "winner": None
                    }

                    found[chart_number] = current_chart

                    print(
                        f"ARCHIWUM: znaleziono TOP{top_size} "
                        f"#{chart_number}"
                    )
                    continue

                # Jeżeli nie jesteśmy wewnątrz notowania — ignorujemy.
                if current_chart is None:
                    continue

                # Jeżeli zwycięzca został już znaleziony dla tego notowania,
                # nic więcej z tego notowania nie pobieramy.
                if current_chart["winner"]:
                    continue

                first_match = first_pattern.match(raw)
                if not first_match:
                    continue

                song = first_match.group(1).strip()

                # Jeżeli linia "1." jest pusta, sprawdzimy następną linię.
                # Wtedy dopuszczamy np.:
                # 1.
                # Wykonawca - Tytuł
                if song:
                    current_chart["winner"] = song
                else:
                    # Tymczasowo oznaczamy oczekiwanie na następną linię.
                    current_chart["waiting_song_line"] = True

                    # Nie kończymy tutaj — kolejna linia może być utworem.
                    continue

                print(
                    f"ARCHIWUM: #{current_chart['numer']} "
                    f"→ 1. {song}"
                )

        # ------------------------------------------------------------
        # DRUGIE PRZEJŚCIE dla przypadków:
        #
        # TOP10 #123
        # 1.
        # Wykonawca - Tytuł
        #
        # Robimy to osobno, żeby nie komplikować głównego parsera.
        # ------------------------------------------------------------
        found = {}
        current_chart = None
        waiting_for_song = False

        for content in messages:
            content = re.sub(
                r"\[([^\]]+)\]\([^)]+\)",
                r"\1",
                content
            )

            lines = content.splitlines()

            for raw in lines:
                raw = raw.strip()

                if not raw:
                    continue

                top_match = top_pattern.match(raw)

                if top_match:
                    top_size = int(top_match.group(1))
                    chart_number = int(top_match.group(2))

                    current_chart = {
                        "numer": chart_number,
                        "top": top_size,
                        "winner": None
                    }

                    found[chart_number] = current_chart
                    waiting_for_song = False
                    continue

                if current_chart is None:
                    continue

                if current_chart["winner"]:
                    continue

                first_match = first_pattern.match(raw)

                if first_match:
                    song = first_match.group(1).strip()

                    if song:
                        current_chart["winner"] = song
                        waiting_for_song = False
                    else:
                        waiting_for_song = True

                    continue

                if waiting_for_song:
                    # Nie bierzemy kolejnego numeru ani kolejnego nagłówka.
                    if re.match(r"^\s*\d+\s*[\.\)]?", raw):
                        waiting_for_song = False
                        continue

                    # To jest tekst piosenki po "1."
                    current_chart["winner"] = raw
                    waiting_for_song = False

        # ------------------------------------------------------------
        # BUDOWA WYNIKU
        # Tylko miejsce 1 z każdego znalezionego notowania.
        # ------------------------------------------------------------
        display_entries = []

        for chart_number in sorted(found.keys()):
            item = found[chart_number]
            winner = item.get("winner")

            if not winner:
                print(
                    f"ARCHIWUM: UWAGA — TOP{item['top']} "
                    f"#{chart_number} — nie znaleziono miejsca 1"
                )
                continue

            # Rozbijamy "Wykonawca - Tytuł".
            # Jeżeli format jest kolumnowy, próbujemy również 2+ spacje/tab.
            wykonawca = ""
            tytul = ""

            if " - " in winner:
                wykonawca, tytul = winner.split(" - ", 1)
            else:
                parts = re.split(r"\s{2,}|\t+", winner)

                if len(parts) >= 2:
                    wykonawca = parts[0].strip()
                    tytul = parts[1].strip()
                else:
                    # Nie zgadujemy wykonawcy.
                    # Całość zachowujemy jako tytuł/tekst zwycięzcy.
                    tytul = winner.strip()

            entry = {
                "notowanie": chart_number,
                "miejsce": 1,
                "top": item["top"],
                "wykonawca": wykonawca.strip(),
                "tytul": tytul.strip(),
                "tekst": winner.strip()
            }

            display_entries.append(entry)

        print(
            f"ARCHIWUM: znaleziono {len(display_entries)} "
            f"zwycięzców (tylko miejsce 1)"
        )

        if not display_entries:
            print(
                "ARCHIWUM: 0 zwycięzców — "
                "nie nadpisuję Firebase!"
            )
            return False

        db.collection("archiwum").document("utwory").set({
            "utwory": display_entries,
            "count": len(display_entries),
            "updated_at": firestore.SERVER_TIMESTAMP
        })

        print(
            f"ARCHIWUM: ZAPISANO {len(display_entries)} zwycięzców "
            f"do archiwum/utwory"
        )

        return True

    except Exception as e:
        print(
            f"ERROR archiwum: "
            f"{type(e).__name__}: {e}"
        )
        import traceback
        traceback.print_exc()
        return False


ARCHIWUM_SYSTEM_DOC = "_system_weekly_archive_sync"


@tasks.loop(seconds=30)
async def check_archiwum_weekly():
    """
    Uruchamia synchronizację archiwum tylko raz w tygodniu:
    PONIEDZIAŁEK.

    Po poprawnym skanowaniu zapisuje numer tygodnia.
    Dzięki temu bot może sprawdzać warunek co 30 sekund,
    ale samo archiwum zostanie pobrane tylko raz.
    """
    try:
        now = datetime.now(WARSAW)

        week_key = now.strftime("%Y-%m-%d")

        system_ref = (
            db.collection("archiwum")
            .document(ARCHIWUM_SYSTEM_DOC)
        )

        system_doc = system_ref.get()
        system_data = system_doc.to_dict() if system_doc.exists else {}

        last_sync_week = system_data.get("last_sync_week")

        # ------------------------------------------------------------
        # PIERWSZE URUCHOMIENIE:
        # wykonaj od razu, niezależnie od dnia tygodnia.
        # Dzięki temu można sprawdzić archiwum natychmiast po wdrożeniu.
        # ------------------------------------------------------------
        if not system_data.get("initialized"):
            print(
                "ARCHIWUM: pierwsze uruchomienie — "
                "wykonuję testowy pełny skan TERAZ."
            )

            success = await sync_archiwum()

            if not success:
                print(
                    "ARCHIWUM: pierwszy skan nieudany — "
                    "spróbuję ponownie za 30 sekund."
                )
                return

            system_ref.set({
                "initialized": True,
                "initialized_at": firestore.SERVER_TIMESTAMP,
                "last_sync_week": week_key,
                "last_sync_at": firestore.SERVER_TIMESTAMP
            }, merge=True)

            print(
                "ARCHIWUM: pierwszy skan zakończony pomyślnie."
            )
            return

        # ------------------------------------------------------------
        # NORMALNA PRACA:
        # kolejne skany tylko w poniedziałek i tylko raz w tygodniu.
        # ------------------------------------------------------------
        if now.weekday() != 0:
            return

        if last_sync_week == week_key:
            return

        print(
            f"ARCHIWUM: poniedziałkowa synchronizacja "
            f"dla {week_key}"
        )

        success = await sync_archiwum()

        if not success:
            print(
                "ARCHIWUM: synchronizacja nieudana — "
                "spróbuję ponownie za 30 sekund."
            )
            return

        system_ref.set({
            "last_sync_week": week_key,
            "last_sync_at": firestore.SERVER_TIMESTAMP
        }, merge=True)

        print(
            "ARCHIWUM: poniedziałkowa synchronizacja "
            "zakończona i oznaczona jako wykonana."
        )

    except Exception as e:
        print(
            f"ERROR archiwum weekly checker: "
            f"{type(e).__name__}: {e}"
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

    print(
        "TEST: START sync_members()"
    )

    await sync_members()

    print(
        "TEST: KONIEC sync_members()"
    )

    if not check_archiwum_weekly.is_running():
        check_archiwum_weekly.start()

    print(
        "ARCHIWUM: tygodniowy system aktywny "
        "— skanowanie tylko w poniedziałek"
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


# ============================================================
# BOT
# ============================================================

bot.run(
    os.getenv("DISCORD_TOKEN")
)
