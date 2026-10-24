import os
import json
import base64
import hashlib
import re
import asyncio
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import 【entity-discord¦canonical_name=discord】
from 【entity-discord¦canonical_name=discord】.ext import commands, tasks

import firebase_admin
from firebase_admin import credentials, firestore

import requests
from bs4 import BeautifulSoup

b64 = os.getenv("FIREBASE_B64")
if b64:
    cred_dict = json.loads(base64.b64decode(b64).decode("utf-8"))
    cred = credentials.Certificate(cred_dict)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
else:
    if not firebase_admin._apps:
        firebase_admin.initialize_app()

db = firestore.client()

intents = 【entity-discord¦canonical_name=discord】.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

RECOVERY_TIMEOUT_MINUTES = 10
RECOVERY_COLLECTION = "pin_recovery"
RECOVERY_DEBOUNCE_SECONDS = 300

TOP20_BASE_URL = os.getenv("TOP20_BASE_URL", "https://aiarenatop10.pl")
TOP20_EDYCJE_URL = os.getenv("TOP20_EDYCJE_URL", f"{TOP20_BASE_URL}/edycje")
TOP20_DEFAULT_URL = os.getenv("TOP20_LISTA_URL", f"{TOP20_BASE_URL}/lista/ai-arena-top-20-edycja-20")

def hash_pin(pin: str) -> str:
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()

# === DODANE - AUTO ZAPIS USERA ===
def ensure_users_doc(discord_id: str, nick: str):
    try:
        did = str(discord_id)
        ref = db.collection("users").document(did)
        snap = ref.get()
        if not snap.exists:
            ref.set({
                "discordId": did,
                "discord_id": did,
                "nick": nick,
                "discordName": nick,
                "createdAt": firestore.SERVER_TIMESTAMP,
                "lastLoginAt": firestore.SERVER_TIMESTAMP,
            }, merge=True)
            print(f"USERS: auto-create users/{did} ({nick})")
            return True
    except Exception as e:
        print(f"USERS auto-create error {discord_id}: {e}")
    return False

WARSAW = ZoneInfo("Europe/Warsaw")

def get_full_text_from_msg(msg):
    parts = []
    if msg.content:
        parts.append(msg.content)
    for emb in msg.embeds:
        if emb.title:
            parts.append(emb.title)
        if emb.description:
            parts.append(emb.description)
        for f in emb.fields:
            if f.value:
                parts.append(f.value)
    return "\n".join(parts)

def _fetch_sync(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    r = requests.get(url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.text

def _parse_edycje_for_top20_urls(html: str):
    urls = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/lista/ai-arena-top-20-edycja-" in href:
                if href.startswith("/"):
                    href = TOP20_BASE_URL + href
                m = re.search(r"edycja-(\d+)", href, re.IGNORECASE)
                if m:
                    num = int(m.group(1))
                    urls.append((num, href.split("?")[0].split("#")[0]))
    except Exception as e:
        print(f"EDYCJE parse error: {e}")
    uniq = {}
    for num, u in urls:
        if u not in uniq or num > uniq[u]:
            uniq[u] = num
    sorted_list = sorted(uniq.items(), key=lambda x: x[1], reverse=True)
    return [u for u, _ in sorted_list]

def _extract_tracks_from_html(html: str):
    tracks = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        articles = soup.find_all("article", class_=lambda c: c and "track" in c)
        if not articles:
            articles = soup.find_all("article", id=lambda x: x and x.startswith("u-t_"))
        for idx, art in enumerate(articles):
            tytul = art.get("data-tytul") or art.get("data-title") or ""
            tworca = art.get("data-tworca") or art.get("data-artist") or ""
            if not tytul:
                continue
            no_el = art.find("span", class_=lambda c: c and "track-no" in c)
            try:
                no = int(no_el.get_text(strip=True)) if no_el else idx + 1
            except:
                no = idx + 1
            track_id = ""
            raw_id = art.get("id") or ""
            if raw_id:
                track_id = raw_id[2:] if raw_id.startswith("u-") else raw_id
            if not track_id:
                btn = art.find("button", class_=lambda c: c and "fav" in c)
                if btn:
                    track_id = btn.get("data-track") or btn.get("value") or ""
            youtube_url = ""
            for a in art.find_all("a", href=True):
                href = a["href"]
                if "youtu.be" in href or "youtube.com" in href:
                    youtube_url = href.strip()
                    break
            if not youtube_url:
                for ifr in art.find_all("iframe", src=True):
                    src = ifr["src"]
                    if "youtube" in src or "youtu.be" in src:
                        youtube_url = src.strip()
                        break
            if not youtube_url:
                inner = str(art)
                m = re.search(r'https?://(?:www\.)?(?:youtu\.be/[^\s"<>]+|youtube\.com/watch\?v=[^\s"<>]+)', inner)
                if m:
                    youtube_url = m.group(0).rstrip('"\'')
            fav_count = None
            heart_terms = ("fav", "heart", "serc", "like", "love")
            count_attrs = ("data-fav-count", "data-hearts", "data-serca", "data-likes", "data-count")

            for el in [art] + art.find_all(True):
                if fav_count is not None:
                    break
                attrs = el.attrs or {}
                classes = attrs.get("class", [])
                if isinstance(classes, str):
                    classes = classes.split()
                ident = " ".join([str(attrs.get("id", "")), *[str(c) for c in classes]]).lower()
                relevant = any(term in ident for term in heart_terms)
                for attr in count_attrs:
                    val = attrs.get(attr)
                    if val is not None and (relevant or attr != "data-count"):
                        mm = re.search(r"\d+", str(val))
                        if mm:
                            fav_count = int(mm.group())
                            break
                if fav_count is not None:
                    break

            if fav_count is None:
                for el in art.find_all(True):
                    attrs = el.attrs or {}
                    classes = attrs.get("class", [])
                    if isinstance(classes, str):
                        classes = classes.split()
                    ident = " ".join([str(attrs.get("id", "")), *[str(c) for c in classes]]).lower()
                    if not any(term in ident for term in heart_terms):
                        continue
                    txt = el.get_text(" ", strip=True)
                    mm = re.search(r"\d+", txt)
                    if mm:
                        fav_count = int(mm.group())
                        break

            if fav_count is None:
                fav_el = art.select_one("span.fav-count, .fav-count")
                if fav_el is not None:
                    fav_text = fav_el.get_text(" ", strip=True)
                    mm = re.search(r"\d+", fav_text)
                    if mm:
                        fav_count = int(mm.group())

            if fav_count is None:
                article_text = art.get_text(" ", strip=True)
                mm = re.search(r"[♥❤♡]\s*(\d+)", article_text)
                if mm:
                    fav_count = int(mm.group(1))

            if fav_count is None:
                raw_article = str(art)
                mm = re.search(
                    r'class=["\'][^"\']*\bfav-count\b[^"\']*["\'][^>]*>\s*(\d+)\s*<',
                    raw_article,
                    re.IGNORECASE,
                )
                if mm:
                    fav_count = int(mm.group(1))

            if fav_count is None:
                fav_count = 0
                print(f"TOP20 WWW: UWAGA — nie znaleziono liczby serduszek dla: {tytul.strip()} (ID={track_id.strip()})")
            tracks.append((no, tytul.strip(), tworca.strip(), youtube_url.strip(), fav_count, track_id.strip()))
    except Exception as e:
        print(f"EXTRACT BS4 error: {e}")
    if len(tracks) < 3:
        try:
            art_blocks = re.findall(r'<article[^>]+id="([^"]+)"[^>]*data-tytul="([^"]+)"[^>]*data-tworca="([^"]+)"[^>]*>(.*?)</article>', html, re.DOTALL | re.IGNORECASE)
            if art_blocks:
                tracks = []
                for i, (art_id, tytul, tworca, inner) in enumerate(art_blocks):
                    youtube_url = ""
                    m = re.search(r'https?://(?:www\.)?(?:youtu\.be/[^\s"<>]+|youtube\.com/watch\?v=[^\s"<>]+)', inner)
                    if m:
                        youtube_url = m.group(0)
                    m_no = re.search(r'class="[^"]*track-no[^"]*"[^>]*>(\d+)<', inner)
                    no = int(m_no.group(1)) if m_no else i + 1
                    m_fav = re.search(r'class="[^"]*fav-count[^"]*"[^>]*>(\d+)<', inner)
                    fav_count = int(m_fav.group(1)) if m_fav else 0
                    track_id = art_id[2:] if art_id.startswith("u-") else art_id
                    tracks.append((no, tytul.strip(), tworca.strip(), youtube_url.strip(), fav_count, track_id.strip()))
        except Exception as e:
            print(f"EXTRACT REGEX error: {e}")
    try:
        tracks.sort(key=lambda x: x[0])
    except:
        pass
    return tracks

@tasks.loop(seconds=60)
async def check_lista():
    now = datetime.now(WARSAW)
    try:
        candidate_urls = []
        try:
            edycje_html = await asyncio.to_thread(_fetch_sync, TOP20_EDYCJE_URL)
            candidate_urls = _parse_edycje_for_top20_urls(edycje_html)
            print(f"TOP20 WWW: znaleziono {len(candidate_urls)} linkow TOP20 w /edycje")
        except Exception as e:
            print(f"TOP20 WWW: blad pobierania /edycje: {e}")
        if not candidate_urls:
            print("TOP20 WWW: /edycje nie zwrocilo linkow - generuje zakres 30..1")
            for n in range(30, 0, -1):
                candidate_urls.append(f"{TOP20_BASE_URL}/lista/ai-arena-top-20-edycja-{n}")
        else:
            existing_nums = set()
            for u in candidate_urls:
                m = re.search(r"edycja-(\d+)", u)
                if m:
                    existing_nums.add(int(m.group(1)))
            for n in range(30, 0, -1):
                if n not in existing_nums:
                    candidate_urls.append(f"{TOP20_BASE_URL}/lista/ai-arena-top-20-edycja-{n}")
            def get_num(u):
                m = re.search(r"edycja-(\d+)", u)
                return int(m.group(1)) if m else 0
            candidate_urls = sorted(set(candidate_urls), key=get_num, reverse=True)

        final_tracks = []
        used_url = None
        for url in candidate_urls[:15]:
            try:
                print(f"TOP20 WWW: probuje {url}")
                html = await asyncio.to_thread(_fetch_sync, url)
                if "Lista utworów będzie widoczna po rozpoczęciu głosowania" in html or "będzie widoczna po rozpoczęciu głosowania" in html:
                    print(f"TOP20 WWW: {url} - lista ukryta (moderacja), skip")
                    continue
                tracks = _extract_tracks_from_html(html)
                print(f"TOP20 WWW: {url} -> znaleziono {len(tracks)} utworow")
                if len(tracks) >= 5:
                    final_tracks = tracks
                    used_url = url
                    break
            except requests.exceptions.HTTPError as he:
                if "404" in str(he):
                    print(f"TOP20 WWW: {url} - brak edycji (404), skip")
                    continue
                print(f"TOP20 WWW: blad {url}: {he}")
                continue
            except Exception as e:
                if "404" in str(e) or "Not Found" in str(e):
                    print(f"TOP20 WWW: {url} - brak edycji (404), skip")
                    continue
                print(f"TOP20 WWW: blad {url}: {e}")
                continue

        if not final_tracks:
            print(f"TOP20 WWW: nie znaleziono zadnej listy z utworami - pomijam zapis")
            return

        final_list = []
        detailed_tracks = []
        serca = []
        for item in final_tracks:
            if len(item) == 6:
                no, tytul, tworca, yt, fav, tid = item
            elif len(item) == 4:
                no, tytul, tworca, yt = item
                fav = 0
                tid = ""
            else:
                continue
            if tworca:
                base = f"{no}. {tytul} - {tworca}"
            else:
                base = f"{no}. {tytul}"
            if yt:
                base = f"{base} {yt}"
            if fav:
                base = f"{base} [{fav}❤]"
            final_list.append(base.strip())
            serca.append(int(fav))
            detailed_tracks.append({
                "no": int(no),
                "tytul": tytul,
                "tworca": tworca,
                "wykonawca": tworca,
                "youtube": yt,
                "youtubeUrl": yt,
                "fav_count": int(fav),
                "serca": int(fav),
                "serduszka": int(fav),
                "hearts": int(fav),
                "track_id": tid,
                "trackId": tid,
                "id": tid
            })

        print(f"TOP20 WWW: Znalazlem {len(final_list)} utworow z {used_url} - serca: {serca[:5]}")

        lista_ref = db.collection("lista").document("aktualna")
        prev_snap = lista_ref.get()
        prev_data = prev_snap.to_dict() if prev_snap.exists else None

        lista_ref.set({
            "utwory": final_list,
            "tracks": detailed_tracks,
            "utwory_szczegoly": detailed_tracks,
            "serca": serca,
            "fav_counts": serca,
            "hearts": serca,
            "serduszka": serca,
            "serca_map": {d["track_id"]: d["serca"] for d in detailed_tracks if d["track_id"]},
            "count": len(final_list),
            "updated_at": firestore.SERVER_TIMESTAMP,
            "updated_day": now.strftime("%d.%m.%Y %H:%M:%S"),
            "source_url": used_url,
            "source": "aiarenatop10.pl",
            "edycja": used_url.split("/")[-1] if used_url else "unknown",
            "last_hearts_update": firestore.SERVER_TIMESTAMP
        })

        print(f"TOP20 WWW: ZAPISANO {len(final_list)} utworow do lista/aktualna z {used_url} + serca")

        is_live_window = (now.weekday() == 6 and now.hour >= 20)

        if not is_live_window:
            print(f"LIVE WINNERS: SKIP - nie niedziela po 20:00 (teraz {now.strftime('%A %H:%M:%S')} Europe/Warsaw) - Wyniki czekaja na LIVE")
        else:
            try:
                top5 = final_tracks[:5]
                if not top5:
                    print("LIVE WINNERS: LIVE window ale brak top5 - skip")
                else:
                    print(f"LIVE WINNERS: LIVE window aktywny - sprawdzam TOP5 pojedynczo z {used_url}")
                    for idx, item in enumerate(top5):
                        if len(item) == 6:
                            no, tytul, tworca, yt, fav, tid = item
                        else:
                            no, tytul, tworca, yt = item
                            fav = 0
                            tid = ""
                        miejsce = idx + 1
                        doc_ref = db.collection("Wyniki").document(str(miejsce))
                        existing_snap = doc_ref.get()
                        existing_data = existing_snap.to_dict() if existing_snap.exists else None

                        needs_update = False
                        reason = ""
                        if not existing_data:
                            needs_update = True
                            reason = f"LIVE {miejsce} - pierwsze wstawienie w niedziele po 20:00"
                        else:
                            if existing_data.get("tytul") != tytul or existing_data.get("tworca") != tworca or existing_data.get("track_id") != tid:
                                needs_update = True
                                reason = f"LIVE {miejsce} - wskoczyl nowy: {tworca} - {tytul} (bylo {existing_data.get('tworca')} - {existing_data.get('tytul')})"
                            else:
                                if int(existing_data.get("serca") or 0) != int(fav):
                                    needs_update = True
                                    reason = f"LIVE {miejsce} - aktualizacja serc {existing_data.get('serca')} -> {fav}"

                        if needs_update:
                            wynik_text = f"{tworca} - {tytul}" if tworca else tytul
                            doc_ref.set({
                                "miejsce": miejsce,
                                "wynik": wynik_text,
                                "tytul": tytul,
                                "tworca": tworca,
                                "youtube": yt,
                                "youtubeUrl": yt,
                                "pelny": f"{miejsce}. {tytul} - {tworca} {yt}".strip(),
                                "wynik_full": final_list[idx] if idx < len(final_list) else wynik_text,
                                "serca": int(fav),
                                "fav_count": int(fav),
                                "hearts": int(fav),
                                "track_id": tid,
                                "updated_at": firestore.SERVER_TIMESTAMP,
                                "updated_day": now.strftime("%d.%m.%Y %H:%M:%S"),
                                "source": "live_top5_z_listy_niedziela_20",
                                "source_url": used_url,
                                "edycja": used_url.split("/")[-1] if used_url else "unknown",
                                "reason": reason
                            }, merge=True)
                            print(f"LIVE WINNERS: Wyniki/{miejsce} = {wynik_text} [{fav} serca] ({reason})")
                        else:
                            print(f"LIVE WINNERS: Wyniki/{miejsce} bez zmian - skip")

                    print("LIVE WINNERS: koniec sprawdzania TOP5 w LIVE window")

            except Exception as e:
                print(f"LIVE WINNERS ERROR: {e}")
                import traceback
                traceback.print_exc()

    except Exception as e:
        print(f"ERROR lista WWW: {e}")
        import traceback
        traceback.print_exc()

@tasks.loop(seconds=60)
async def check_glosowanie_link():
    try:
        cid_raw = os.getenv("GLOSOWANIE_CHANNEL_ID") or os.getenv("LINKI_CHANNEL_ID") or "1517609248765382776"
        if not cid_raw:
            return
        try:
            cid = int(cid_raw)
        except:
            print(f"GLOSOWANIE: zly ID kanalu {cid_raw}")
            return
        try:
            ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
        except discord.errors.NotFound:
            print(f"GLOSOWANIE: kanal {cid} nie istnieje (404) - sprawdz ID lub czy bot jest na serwerze")
            return
        except Exception as e:
            print(f"GLOSOWANIE: blad pobierania kanalu {cid}: {e}")
            return
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
                    "nick": member.display_name,
                    "nick_lower": member.display_name.lower(),
                    "discordName": member.display_name,
                    "avatar": str(member.display_avatar.url) if member.display_avatar else "",
                    "joined_at": firestore.SERVER_TIMESTAMP,
                },
                merge=True
            )
            # DODANE - zapis do users
            ensure_users_doc(str(member.id), member.display_name)
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
        print(f"CZŁONKOWIE: zapisano/zaktualizowano {saved_count} rekordów w Firebase → czlonkowie + users")
    except Exception as e:
        print(f"ERROR członkowie: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

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
        # DODANE - zapis do users przy wejsciu
        ensure_users_doc(str(member.id), member.display_name)
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

@bot.command(name="sync")
@commands.has_permissions(administrator=True)
async def sync_cmd(ctx):
    await ctx.send("🔄 Rozpoczynam pełną synchronizację członków...")
    await sync_members()
    await ctx.send("✅ Synchronizacja zakończona! Sprawdz `users` i `czlonkowie`")

@bot.command(name="czlonkowie")
@commands.has_permissions(administrator=True)
async def czlonkowie_count(ctx):
    try:
        col = db.collection("czlonkowie")
        docs = list(col.stream())
        await ctx.send(f"📊 W bazie jest {len(docs)} dokumentów w `czlonkowie`")
    except Exception as e:
        await ctx.send(f"❌ Błąd: {e}")

@bot.command(name="dodaj_usera")
@commands.has_permissions(administrator=True)
async def dodaj_usera(ctx, member: discord.Member):
    ok = ensure_users_doc(str(member.id), member.display_name)
    if ok:
        await ctx.send(f"✅ Utworzono `users/{member.id}` dla {member.mention}")
    else:
        await ctx.send(f"ℹ Już istnieje `users/{member.id}`")

@bot.command(name="lista_test")
@commands.has_permissions(administrator=True)
async def lista_test(ctx):
    await ctx.send("🔄 Wymuszam check listy z WWW + logika Wyniki + serca...")
    await check_lista()
    await ctx.send("✅ Sprawdź logi")

@bot.command(name="hof_test")
@commands.has_permissions(administrator=True)
async def hof_test(ctx):
    await ctx.send("🔄 Wymuszam HOF z Wyniki/1 (niedziela 23:00)...")
    ok = await sync_hall_of_fame_from_lista()
    if ok:
        await ctx.send("✅ HOF wstawiony - sprawdz HALL OF FAME")
    else:
        await ctx.send("ℹ HOF już istnieje lub brak Wyniki/1 - sprawdź logi")

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

HALL_OF_FAME_COLLECTION = "HALL OF FAME"
HALL_OF_FAME_SYSTEM_DOC = "_system_hall_of_fame"

async def sync_hall_of_fame_from_lista():
    try:
        w1_ref = db.collection("Wyniki").document("1")
        w1_snap = w1_ref.get()
        if not w1_snap.exists:
            print("HOF Z WYNIKI/1: brak Wyniki/1 - skip")
            return False
        wd = w1_snap.to_dict() or {}
        tytul = wd.get("tytul") or ""
        wykonawca = wd.get("tworca") or wd.get("wykonawca") or ""
        youtubeUrl = wd.get("youtubeUrl") or wd.get("youtube") or ""
        edycja_str = wd.get("edycja") or ""
        source_url = wd.get("source_url") or ""

        m = re.search(r"edycja-(\d+)", edycja_str, re.IGNORECASE) or re.search(r"edycja-(\d+)", source_url, re.IGNORECASE)
        if not m:
            lista_snap = db.collection("lista").document("aktualna").get()
            if lista_snap.exists:
                ld = lista_snap.to_dict() or {}
                fallback_ed = ld.get("edycja") or ld.get("source_url") or ""
                m = re.search(r"edycja-(\d+)", fallback_ed, re.IGNORECASE)
                if m and not edycja_str:
                    edycja_str = fallback_ed

        if not m:
            print(f"HOF Z WYNIKI/1: nie moge wyciagnac numeru edycji z {edycja_str} / {source_url}")
            return False

        numer = int(m.group(1))
        doc_id = f"{numer:03d}"
        doc_ref = db.collection(HALL_OF_FAME_COLLECTION).document(doc_id)
        if doc_ref.get().exists:
            print(f"HOF Z WYNIKI/1: {doc_id} juz istnieje - skip")
            return False

        if not tytul:
            print("HOF Z WYNIKI/1: brak tytulu w Wyniki/1 - skip")
            return False

        youtubeId = ""
        if youtubeUrl:
            mm = re.search(r"(?:v=|youtu\.be/|shorts/)([^&\s\?\))]+)", youtubeUrl)
            if mm:
                youtubeId = mm.group(1)

        doc_ref.set({
            "notowanie": f"TOP 20 #{doc_id}",
            "numerNotowania": numer,
            "tytul": tytul,
            "wykonawca": wykonawca,
            "youtubeId": youtubeId,
            "youtubeUrl": youtubeUrl,
            "source": "Wyniki/1 niedziela 23:00",
            "source_url": source_url,
            "edycja": edycja_str,
            "updated_at": firestore.SERVER_TIMESTAMP
        })
        print(f"HOF Z WYNIKI/1: ZAPISANO {doc_id}: {wykonawca} - {tytul}")
        return True
    except Exception as e:
        print(f"ERROR HOF Z WYNIKI/1: {e}")
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
                print("HALL OF FAME: juz zainicjalizowane - bez wstawiania")
                return
        print("HALL OF FAME: pierwsze uruchomienie - tylko inicjalizacja, bez wstawiania HOF")
        sys_ref.set({
            "initialized": True,
            "initialized_at": firestore.SERVER_TIMESTAMP,
            "description": "HOF - z Wyniki/1 niedziela 23:00"
        }, merge=True)
    except Exception as e:
        print(f"ERROR HOF INIT: {e}")
        import traceback
        traceback.print_exc()

@tasks.loop(minutes=5)
async def check_hall_of_fame():
    try:
        now = datetime.now(WARSAW)
        if now.weekday() != 6 or now.hour != 23:
            return
        day_key = now.strftime("%Y-%m-%d")
        sys_ref = db.collection(HALL_OF_FAME_COLLECTION).document(HALL_OF_FAME_SYSTEM_DOC)
        sys_doc = sys_ref.get()
        data = {}
        if sys_doc.exists:
            data = sys_doc.to_dict() or {}
        if data.get("last_hof_day") == day_key:
            return
        print(f"HALL OF FAME: niedziela 23:00 {day_key} - proba wstawienia z Wyniki/1")
        success = await sync_hall_of_fame_from_lista()
        if success:
            sys_ref.set({"last_hof_day": day_key, "last_check": firestore.SERVER_TIMESTAMP}, merge=True)
            print(f"HALL OF FAME: wstawiono - dzien {day_key} oznaczony")
        else:
            print("HALL OF FAME: brak nowego wpisu - ponowie za 5 min")
    except Exception as e:
        print(f"ERROR HOF checker: {e}")
        import traceback
        traceback.print_exc()

def _parse_last_sent(last_sent):
    if last_sent is None:
        return None
    try:
        if isinstance(last_sent, (int, float)):
            if last_sent > 1e12:
                return datetime.fromtimestamp(last_sent/1000, tz=timezone.utc)
            else:
                return datetime.fromtimestamp(last_sent, tz=timezone.utc)
        dt = last_sent
        if hasattr(dt, 'to_datetime'):
            dt = dt.to_datetime()
        if hasattr(dt, 'tzinfo'):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        return None
    except Exception:
        return None

async def create_recovery_request(discord_id: int, nick: str, source: str = "app"):
    try:
        discord_id_str = str(discord_id)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(minutes=RECOVERY_TIMEOUT_MINUTES)
        recovery_ref = db.collection(RECOVERY_COLLECTION).document(discord_id_str)
        try:
            existing = recovery_ref.get()
            if existing.exists:
                edata = existing.to_dict() or {}
                last_sent = edata.get("lastDmSentAt") or edata.get("lastDmSentAtMs") or edata.get("sentAt") or edata.get("lastSent")
                last_dt = _parse_last_sent(last_sent)
                if last_dt:
                    diff = (now - last_dt).total_seconds()
                    if diff < RECOVERY_DEBOUNCE_SECONDS:
                        print(f"RECOVERY: ANTY-SPAM pomijam {discord_id} - DM wyslano {int(diff)}s temu")
                        recovery_ref.set({"status": "WAITING_CONFIRMATION"}, merge=True)
                        return True
        except Exception as e:
            print(f"RECOVERY: blad anty-spam check: {e}")
        recovery_ref.set({
            "discordId": discord_id_str,
            "nick": nick,
            "status": "WAITING_CONFIRMATION",
            "source": source,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "expiresAt": expires,
            "lastDmSentAt": firestore.SERVER_TIMESTAMP,
            "lastDmSentAtMs": int(now.timestamp() * 1000)
        }, merge=True)
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
        import traceback
        traceback.print_exc()
        return False

@tasks.loop(seconds=30)
async def check_recovery_requests():
    try:
        collection = db.collection(RECOVERY_COLLECTION)
        docs_pending = list(collection.where("status", "==", "PENDING").stream())
        docs_sending = []
        try:
            old_sending = collection.where("status", "==", "SENDING").stream()
            for d in old_sending:
                dd = d.to_dict() or {}
                proc = dd.get("processingAt")
                last_dt = _parse_last_sent(proc)
                if last_dt:
                    if (datetime.now(timezone.utc) - last_dt).total_seconds() > 180:
                        docs_sending.append(d)
                else:
                    docs_sending.append(d)
        except Exception:
            pass
        docs = docs_pending + docs_sending
        if not docs:
            return
        print(f"RECOVERY: znaleziono {len(docs)} zadan PENDING/SENDING")
        for doc in docs:
            data = doc.to_dict() or {}
            discord_id = str(data.get("discordId") or doc.id)
            nick = str(data.get("nick") or "").strip()
            if not discord_id or not nick:
                print(f"RECOVERY: błędne żądanie {doc.id}")
                doc.reference.set({"status": "ERROR", "error": "Brak discordId lub nick"}, merge=True)
                continue
            last_sent = data.get("lastDmSentAt") or data.get("lastDmSentAtMs") or data.get("sentAt")
            last_dt = _parse_last_sent(last_sent)
            if last_dt:
                diff = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if diff < RECOVERY_DEBOUNCE_SECONDS:
                    print(f"RECOVERY: SKIP PENDING {discord_id} - anty-spam {int(diff)}s")
                    doc.reference.set({"status": "WAITING_CONFIRMATION"}, merge=True)
                    continue
            doc.reference.set({
                "status": "SENDING",
                "processingAt": firestore.SERVER_TIMESTAMP,
                "processingAtMs": int(datetime.now(timezone.utc).timestamp()*1000)
            }, merge=True)
            await asyncio.sleep(1)
            try:
                discord_int = int(discord_id)
            except:
                print(f"RECOVERY: bledny discordId {discord_id}")
                continue
            await create_recovery_request(discord_int, nick, source="app")
    except Exception as e:
        print(f"ERROR recovery checker: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

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
                "expiresAt": expires,
                "lastDmSentAt": firestore.SERVER_TIMESTAMP,
                "lastDmSentAtMs": int(now.timestamp()*1000)
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
            exp_dt = _parse_last_sent(expires_at)
            if exp_dt and datetime.now(timezone.utc) > exp_dt:
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
                "expiresAt": expires,
                "lastDmSentAt": firestore.SERVER_TIMESTAMP,
                "lastDmSentAtMs": int(now.timestamp()*1000)
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

@bot.event
async def on_ready():
    print(f"READY {bot.user} - WWW TOP20 + YT + LIVE TOP5 + HOF NIEDZIELA 23:00 Z WYNIKI/1")
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
    if not sync_members_loop.is_running():
        sync_members_loop.start()

    print("TEST: START sync_members()")
    await sync_members()
    print("TEST: KONIEC sync_members()")

    print("TEST: START sync_archiwum()")
    await sync_archiwum()
    print("TEST: KONIEC sync_archiwum()")

    print("TEST: START sync_hall_of_fame_initial()")
    await sync_hall_of_fame_initial()
    print("TEST: KONIEC sync_hall_of_fame_initial()")

    print("READY: HOF tylko niedziela 23:00 z Wyniki/1")

bot.run(os.getenv("DISCORD_TOKEN"))
