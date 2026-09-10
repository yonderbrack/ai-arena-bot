import os
import json
import base64
import hashlib
import re
from datetime import datetime, timezone, timedelta

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
# LISTA UTWORÓW — ISTNIEJĄCY MECHANIZM, BEZ ZMIAN
# ============================================================

@tasks.loop(minutes=5)
async def check_lista():
    now = datetime.now()

    is_active_day = now.weekday() in [1, 2, 3]

    if not is_active_day:
        print(
            f"[{now.strftime('%a %H:%M')}] "
            f"Nie wt/sr/czw - spie do wtorku"
        )
        return

    try:
        print(
            f"[{now}] WT/SR/CZW - sprawdzam liste..."
        )

        cid = int(
            os.getenv("CHANNEL_ID")
            or os.getenv("LISTA_CHANNEL_ID")
        )

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

        sorted_list = [
            uniq[k]
            for k in sorted(uniq.keys())
        ]

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
                f"ZAPISANO {len(sorted_list)} do "
                f"lista/aktualna - WT/SR/CZW"
            )

    except Exception as e:
        print(f"ERROR lista: {e}")


# ============================================================
# CZŁONKOWIE SERWERA — ISTNIEJĄCY MECHANIZM
# ============================================================

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
# ARCHIWUM UTWORÓW — ISTNIEJĄCY MECHANIZM
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
# ODZYSKIWANIE PIN — UTWORZENIE ŻĄDANIA
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
# ODZYSKIWANIE PIN — SPRAWDZENIE ŻĄDANIA Z FIREBASE
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
# ODZYSKIWANIE PIN — OBSŁUGA WIADOMOŚCI PRYWATNYCH
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
    # SPRAWDZENIE CZASU WAŻNOŚCI
    # ========================================================

    expires_at = recovery.get("expiresAt")

    if expires_at is not None:
        try:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(
                    tzinfo=timezone.utc
                )

            if datetime.now(timezone.utc) > expires_at:
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

        except Exception as e:
            print(
                f"RECOVERY: błąd sprawdzania "
                f"wygaśnięcia: {e}"
            )

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
                    "❌ Nie znaleziono Twojego konta w bazie.\n\n"
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
# TEST ODZYSKIWANIA PIN
# ============================================================

@bot.command(name="odzyskaj_test")
async def odzyskaj_test(ctx):

    discord_id = str(ctx.author.id)

    print(
        f"RECOVERY TEST: rozpoczęto dla "
        f"{ctx.author} ({discord_id})"
    )

    # ========================================================
    # SPRAWDZAMY USERS
    # ========================================================

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

    # ========================================================
    # TWORZYMY ŻĄDANIE
    # ========================================================

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
# START
# ============================================================

@bot.event
async def on_ready():

    print(
        f"READY {bot.user} - "
        f"tryb WT/SR/CZW 00:00-23:59"
    )

    if not check_lista.is_running():
        check_lista.start()

    if not check_recovery_requests.is_running():
        check_recovery_requests.start()

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
        "RECOVERY: system odzyskiwania PIN aktywny"
    )


# ============================================================
# BOT
# ============================================================

bot.run(
    os.getenv("DISCORD_TOKEN")
)
