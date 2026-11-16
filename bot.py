import os
import json
import base64
import hashlib
import re
import asyncio
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

import firebase_admin
from firebase_admin import credentials, firestore

import requests
from bs4 import BeautifulSoup

PORT = int(os.getenv("PORT", "8000"))

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK - AI Arena Bot running")
    def log_message(self, format, *args):
        return

def start_health_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
        t = threading.Thread(target=server.serve_forever, daemon=False)
        t.start()
        print(f"HEALTH: HTTP server listening on 0.0.0.0:{PORT}")
    except Exception as e:
        print(f"HEALTH error: {e}")

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

intents = discord.Intents.default()
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
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper()
        r = scraper.get(url, timeout=20)
        if r.status_code == 404:
            return ""
        r.raise_for_status()
        return r.text
    except Exception as e:
        if "404" in str(e):
            return ""
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code == 404:
            return ""
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

def _parse_edycje_for_top10_urls(html: str):
    urls = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/lista/" not in href:
                continue
            text = (a.get_text(" ", strip=True) + " " + str(a.get("title",""))).upper()
            parent_text = ""
            if a.parent:
                parent_text = a.parent.get_text(" ", strip=True).upper()[:400]
            combined = text + " " + parent_text
            is_top10_url = "top-10" in href.lower()
            is_top10_text = "TOP 10" in combined or "TOP-10" in combined or "TOP10" in combined
            if is_top10_url or ("/lista/ai-arena-top-20-" in href and is_top10_text):
                if href.startswith("/"):
                    href = TOP20_BASE_URL + href
                clean = href.split("?")[0].split("#")[0].rstrip("/")
                urls.append(clean)
    except Exception as e:
        print(f"EDYCJE TOP10 parse error: {e}")
    return list(dict.fromkeys(urls))

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
                    youtube_url = m.group(0).rstrip("\"'")
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
                mm = re.search(r'class=["\'][^"\']*\bfav-count\b[^"\']*["\'][^>]*>\s*(\d+)\s*<', raw_article, re.IGNORECASE)
                if mm:
                    fav_count = int(mm.group(1))
            if fav_count is None:
                fav_count = 0
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

# === WYNIKI LIVE: kopia pierwszych 5 z listy w kolejnosci listy (1-5) ===
_last_edycja_for_wyniki = None

async def sync_wyniki_live():
    global _last_edycja_for_wyniki
    try:
        doc = db.collection("lista").document("aktualna").get()
        if not doc.exists:
            return
        data = doc.to_dict() or {}
        tracks = data.get("tracks") or []
        if not tracks:
            return
        edycja = data.get("edycja", "")
        source_url = data.get("source_url", "")
        top5 = sorted(tracks, key=lambda x: x.get("no", 999))[:5]
        if _last_edycja_for_wyniki != edycja:
            print(f"WYNIKI: nowa edycja {edycja} - przepisuje Wyniki live")
            _last_edycja_for_wyniki = edycja
        for i, tr in enumerate(top5, start=1):
            wynik = f"{i}. {tr.get('tworca','')} - {tr.get('tytul','')}"
            full = f"{i}. {tr.get('tworca','')} - {tr.get('tytul','')} {tr.get('youtube','')}"
            db.collection("Wyniki").document(str(i)).set({
                "wynik": wynik,
                "wynik_full": full,
                "pelny": full,
                "tworca": tr.get("tworca", ""),
                "tytul": tr.get("tytul", ""),
                "youtube": tr.get("youtube", ""),
                "youtubeUrl": tr.get("youtube", ""),
                "serca": int(tr.get("fav_count", 0)),
                "track_id": tr.get("track_id", ""),
                "source": "live_top5_z_listy",
                "source_url": source_url,
                "edycja": edycja,
                "updated_at": firestore.SERVER_TIMESTAMP,
                "updated_day": datetime.now(WARSAW).strftime("%d.%m.%Y %H:%M:%S"),
            }, merge=True)
    except Exception as e:
        print(f"WYNIKI blad: {e}")

@tasks.loop(seconds=30)
async def check_wyniki_loop():
    await sync_wyniki_live()

@tasks.loop(seconds=60)
async def check_lista():
    now = datetime.now(WARSAW)
    try:
        candidate_urls_top20 = []
        candidate_urls_top10 = []
        edycje_html = ""
        try:
            edycje_html = await asyncio.to_thread(_fetch_sync, TOP20_EDYCJE_URL)
            candidate_urls_top20 = _parse_edycje_for_top20_urls(edycje_html)
            candidate_urls_top10 = _parse_edycje_for_top10_urls(edycje_html)
            print(f"TOP20 WWW: znaleziono {len(candidate_urls_top20)} linkow TOP20 + {len(candidate_urls_top10)} linkow TOP10 specjalnych")
        except Exception as e:
            print(f"TOP20 WWW: blad pobierania /edycje: {e}")
        # NAJNOWSZA Z /edycje - kolejnosc jak na stronie, najnowsza pierwsza
        # Brute 25..1 tylko jako fallback, jak /edycje puste
        candidate_urls = []
        try:
            if edycje_html:
                soup_ed = BeautifulSoup(edycje_html, "html.parser")
                for a in soup_ed.find_all("a", href=True):
                    href = a["href"]
                    if "/lista/" not in href:
                        continue
                    if href.startswith("/"):
                        href = TOP20_BASE_URL + href
                    clean = href.split("?")[0].split("#")[0].rstrip("/")
                    if clean not in candidate_urls:
                        candidate_urls.append(clean)
        except Exception as e:
            print(f"TOP20 WWW: blad parsowania kolejnosci /edycje: {e}")
        if not candidate_urls:
            for n in range(25, 0, -1):
                candidate_urls.append(f"{TOP20_BASE_URL}/lista/ai-arena-top-20-edycja-{n}")
            candidate_urls.extend(candidate_urls_top10)
        candidate_urls = list(dict.fromkeys(candidate_urls))
        final_tracks = []
        used_url = None
        for url in candidate_urls:
            try:
                print(f"TOP20 WWW: probuje {url}")
                html = await asyncio.to_thread(_fetch_sync, url)
                if not html:
                    continue
                tracks = _extract_tracks_from_html(html)
                print(f"TOP20 WWW: {url} -> znaleziono {len(tracks)} utworow")
                if len(tracks) >= 1:
                    final_tracks = tracks
                    used_url = url
                    break
            except Exception as e:
                print(f"TOP20 WWW: blad {url}: {e}")
                continue
        if not final_tracks:
            return
        final_list = []
        detailed_tracks = []
        serca = []
        for item in final_tracks:
            if len(item) == 6:
                no, tytul, tworca, yt, fav, tid = item
            else:
                no, tytul, tworca, yt = item
                fav = 0
                tid = ""
            base = f"{no}. {tworca} - {tytul}" if tworca else f"{no}. {tytul}"
            if yt:
                base = f"{base} {yt}"
            if fav:
                base = f"{base} [{fav}]"
            final_list.append(base.strip())
            serca.append(int(fav))
            detailed_tracks.append({
                "no": int(no),
                "tytul": tytul,
                "tworca": tworca,
                "youtube": yt,
                "fav_count": int(fav),
                "track_id": tid,
            })
        lista_ref = db.collection("lista").document("aktualna")
        lista_ref.set({
            "utwory": final_list,
            "tracks": detailed_tracks,
            "serca": serca,
            "count": len(final_list),
            "updated_at": firestore.SERVER_TIMESTAMP,
            "updated_day": now.strftime("%d.%m.%Y %H:%M:%S"),
            "source_url": used_url,
            "edycja": used_url.split("/")[-1] if used_url else "unknown",
            "typ": "TOP10" if "top-10" in (used_url or "").lower() else "TOP20",
        })
        print(f"TOP20 WWW: ZAPISANO {len(final_list)} utworow do lista/aktualna z {used_url}")
        await sync_wyniki_live()
    except Exception as e:
        print(f"ERROR lista WWW: {e}")
        import traceback
        traceback.print_exc()

@tasks.loop(seconds=60)
async def check_glosowanie_link():
    try:
        cid_raw = os.getenv("GLOSOWANIE_CHANNEL_ID") or os.getenv("LINKI_CHANNEL_ID") or "1517609248765382776"
        try:
            cid = int(cid_raw)
        except:
            return
        try:
            ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
        except:
            return
        if ch is None:
            return
        link_pattern = re.compile(r"https?://(?:forms\.gle|docs\.google\.com/(?:forms|spreadsheets)/|forms\.google\.com|forms\.office\.com|docs\.google\.com/forms)/[^\s\)\]\<\>\"']+", re.IGNORECASE)
        found_link = None
        found_msg_id = None
        async for msg in ch.history(limit=20):
            if not msg.content:
                continue
            m = link_pattern.search(msg.content)
            if m:
                found_link = m.group(0).rstrip(").,!;")
                found_msg_id = str(msg.id)
                break
        if not found_link:
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
            "source_message_id": found_msg_id,
            "source_channel_id": str(cid),
            "updated_at": firestore.SERVER_TIMESTAMP
        }, merge=True)
    except Exception as e:
        print(f"ERROR glosowanie_link: {e}")

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
        batch = db.batch()
        batch_count = 0
        for doc in docs:
            if doc.id == TYPY_SYSTEM_DOC:
                continue
            data = doc.to_dict() or {}
            timestamp = data.get("timestamp")
            if timestamp is None:
                continue
            try:
                timestamp_ms = int(timestamp)
            except:
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
        print(f"TYPY: czyszczenie zakończone. Usunięto: {deleted_count}")
        return deleted_count
    except Exception as e:
        print(f"ERROR TYPY cleanup: {e}")
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
            deleted_count = cleanup_old_typy(current_week_start)
            system_ref.set({
                "initialized": True,
                "cleanup_week": current_week_key,
                "last_cleanup_at": firestore.SERVER_TIMESTAMP,
                "last_deleted_count": deleted_count
            }, merge=True)
            return
        if now.weekday() != 0 or now.hour < 12 or cleanup_week == current_week_key:
            return
        deleted_count = cleanup_old_typy(current_week_start)
        system_ref.set({
            "cleanup_week": current_week_key,
            "last_cleanup_at": firestore.SERVER_TIMESTAMP,
            "last_deleted_count": deleted_count
        }, merge=True)
    except Exception as e:
        print(f"ERROR typy cleanup: {e}")

async def sync_members():
    try:
        guild_id = int(os.getenv("GUILD_ID") or "1515466354113777715")
        guild = bot.get_guild(guild_id)
        if guild is None:
            return
        members = []
        async for member in guild.fetch_members(limit=None):
            members.append(member)
        collection = db.collection("czlonkowie")
        batch = db.batch()
        batch_count = 0
        for member in members:
            if member.bot:
                continue
            doc_ref = collection.document(str(member.id))
            batch.set(doc_ref, {
                "discord_id": str(member.id),
                "username": member.name,
                "display_name": member.display_name,
                "avatar": str(member.display_avatar.url) if member.display_avatar else "",
            }, merge=True)
            ensure_users_doc(str(member.id), member.display_name)
            batch_count += 1
            if batch_count >= 400:
                batch.commit()
                batch = db.batch()
                batch_count = 0
        if batch_count > 0:
            batch.commit()
    except Exception as e:
        print(f"ERROR czlonkowie: {e}")

@tasks.loop(hours=6)
async def sync_members_loop():
    await sync_members()

@bot.event
async def on_member_join(member):
    try:
        if member.bot:
            return
        db.collection("czlonkowie").document(str(member.id)).set({
            "discord_id": str(member.id),
            "username": member.name,
            "display_name": member.display_name,
            "avatar": str(member.display_avatar.url) if member.display_avatar else "",
            "active": True,
        }, merge=True)
        ensure_users_doc(str(member.id), member.display_name)
    except Exception as e:
        print(f"ERROR on_member_join: {e}")

# === HALL OF FAME: zamiana WSZYSTKICH danych, niedziela 23:30 ===
# Zasada:
# - Zrodlo: /edycje + /edycje?strona=2 + /edycje?strona=3, tylko div#listaEdycji, tylko a.edition-foto
# - Numer = numer z URL (edycja-23 -> 23). Specjalna bez numeru dostaje max+1.
#   Bez filtra naglowka 8000 - link z #listaEdycji to juz zamknieta.
# - numerNotowania zapisywane jako LICZBA (int), nie string - tego wymaga orderBy w telefonie.
# - Przy starcie bot raz przebudowuje wszystko (zamiana teraz). Potem tylko niedziela 23:30-23:59.
def _hof_youtube_id(url: str) -> str:
    if not url:
        return ""
    m = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{6,})", url)
    return m.group(1) if m else ""

def _hof_fetch_closed_edition_urls_sync():
    # Zwraca liste (url, tytul) w kolejnosci ze strony: najnowsza pierwsza.
    out = []
    seen = set()
    page_urls = [
        TOP20_EDYCJE_URL,
        f"{TOP20_EDYCJE_URL}?strona=2",
        f"{TOP20_EDYCJE_URL}?strona=3",
    ]
    for page_url in page_urls:
        try:
            html = _fetch_sync(page_url)
        except Exception as e:
            print(f"HALL OF FAME: blad pobrania {page_url}: {e}")
            continue
        if not html:
            continue
        try:
            soup = BeautifulSoup(html, "html.parser")
            lista = soup.find("div", id="listaEdycji")
            container = lista if lista is not None else soup
            links = container.find_all("a", class_=lambda c: c and "edition-foto" in c, href=True)
            if not links:
                # Fallback: wszystkie linki /lista/ w sekcji Zamkniete
                links = container.find_all("a", href=True)
            for a in links:
                href = a.get("href") or ""
                if "/lista/" not in href:
                    continue
                if href.startswith("/"):
                    full = TOP20_BASE_URL + href
                elif href.startswith("http"):
                    full = href
                else:
                    full = TOP20_BASE_URL + "/" + href.lstrip("/")
                clean = full.split("?")[0].split("#")[0].rstrip("/")
                if clean in seen:
                    continue
                seen.add(clean)
                # Tytul z article/h3 obok linku - do wykrycia edycji specjalnej
                title = ""
                try:
                    art = a.find_parent("article")
                    if art is not None:
                        h3 = art.find("h3")
                        if h3 is not None:
                            title = h3.get_text(" ", strip=True)
                except Exception:
                    title = ""
                out.append((clean, title))
        except Exception as e:
            print(f"HALL OF FAME: blad parsowania {page_url}: {e}")
            continue
    print(f"HALL OF FAME: znaleziono {len(out)} linkow edycji z /edycje 1-3")
    return out

def _hof_is_closed_html(html: str) -> bool:
    # Link jest z div#listaEdycji = z definicji zamkniety. Nie odrzucamy po naglowku.
    # Stary check na pierwszych 8000 znakach kasowal wiekszosc edycji.
    return True if html else False

def _hof_num_from_url(url: str):
    m = re.search(r"edycja-(\d+)", url, re.IGNORECASE)
    return int(m.group(1)) if m else None

_hof_last_sunday_run = ""
_hof_initial_done = False

async def _hof_rebuild_all():
    # Pobiera wszystkie zamkniete edycje, bierze #1 z kazdej, kasuje cala kolekcje i zapisuje od nowa.
    # Zwraca True gdy cos zmieniono, False gdy bez zmian.
    try:
        edition_links = await asyncio.to_thread(_hof_fetch_closed_edition_urls_sync)
        if not edition_links:
            print("HALL OF FAME: brak linkow edycji - nic nie zmieniam")
            return False
        # Numer = numer z URL (edycja-23 -> 23), nie pozycja na liscie. Dzieki temu 002 zostaje 002 nawet jak jedna edycja odpadnie.
        chronological = list(reversed(edition_links))
        collected = []  # (numer, url, title, tworca, tytul, youtube_url)
        for (url, title) in chronological:
            try:
                html = await asyncio.to_thread(_fetch_sync, url)
                if not html:
                    print(f"HALL OF FAME: pusta strona {url} - pomijam")
                    continue
                if not _hof_is_closed_html(html):
                    print(f"HALL OF FAME: pomijam {url} - nie zamknieta")
                    continue
                tracks = _extract_tracks_from_html(html)
                if not tracks:
                    print(f"HALL OF FAME: brak utworow {url} - pomijam")
                    continue
                top = None
                for t in tracks:
                    # _extract zwraca tuple (no, tytul, tworca, yt, fav, tid)
                    if t[0] == 1:
                        top = t
                        break
                if top is None:
                    top = tracks[0]
                no, tytul, tworca, yt, fav, tid = top if len(top) == 6 else (top[0], top[1], top[2], top[3], 0, "")
                tworca = (tworca or "").strip()
                tytul = (tytul or "").strip()
                if not tworca or not tytul:
                    print(f"HALL OF FAME: puste #1 {url} - pomijam")
                    continue
                ed_num = _hof_num_from_url(url)
                is_special_no_num = ed_num is None
                if is_special_no_num:
                    # Specjalna bez numeru w URL - dostanie numer po posortowaniu, na razie -1
                    ed_num = -1
                collected.append((ed_num, url, title, tworca, tytul, (yt or "").strip()))
                print(f"HALL OF FAME: {ed_num:03d} {url} -> {tworca} - {tytul} (specjalna={('specjalna' in (title or '').lower())})")
            except Exception as e:
                print(f"HALL OF FAME: blad edycji {url}: {e}")
                continue
        if not collected:
            print("HALL OF FAME: nic nie zebrano - nic nie zmieniam")
            return False
        # BLOKADA: jak zebrano podejrzanie malo, nie kasuj nic. Chroni przed wyczyszczeniem calej kolekcji przy bledzie pobierania.
        if len(collected) < 10:
            print(f"HALL OF FAME: BLOKADA - zebrano tylko {len(collected)} edycji, nie kasuje. Sprawdz logi /edycje.")
            return False
        # Specjalna bez numeru: nadaj jej numer = max + 1, zeby miala wlasny calkowity numer
        if any(c[0] == -1 for c in collected):
            mx = max([c[0] for c in collected if c[0] != -1], default=0)
            fixed = []
            nxt = mx + 1
            for numer, url, title, tworca, tytul, yt in collected:
                if numer == -1:
                    fixed.append((nxt, url, title, tworca, tytul, yt))
                    nxt += 1
                else:
                    fixed.append((numer, url, title, tworca, tytul, yt))
            collected = fixed
        collected = sorted(collected, key=lambda x: x[0])
        # Porownanie z tym co jest - jak identyczne, nie kasuj
        try:
            existing = {}
            for d in db.collection("HALL OF FAME").stream():
                data = d.to_dict() or {}
                existing[d.id] = (str(data.get("wykonawca") or ""), str(data.get("tytul") or ""), data.get("numerNotowania"))
        except Exception as e:
            print(f"HALL OF FAME: blad odczytu istniejacych: {e}")
            existing = {}
        new_map = {}
        for numer, url, title, tworca, tytul, yt in collected:
            doc_id = f"{numer:03d}"
            new_map[doc_id] = (tworca, tytul, numer)
        if existing == new_map:
            print(f"HALL OF FAME: bez zmian ({len(new_map)} wpisow) - nie kasuje")
            return False
        # ZAMIANA WSZYSTKICH DANYCH: najpierw kasuj wszystko, potem zapisz od nowa
        print(f"HALL OF FAME: zamieniam wszystkie dane: stare={len(existing)} nowe={len(new_map)}")
        try:
            docs = list(db.collection("HALL OF FAME").stream())
            batch = db.batch()
            n = 0
            for d in docs:
                batch.delete(d.reference)
                n += 1
                if n >= 400:
                    batch.commit()
                    batch = db.batch()
                    n = 0
            if n > 0:
                batch.commit()
            print(f"HALL OF FAME: usunieto {len(docs)} starych dokumentow")
        except Exception as e:
            print(f"HALL OF FAME: blad kasowania: {e}")
            return False
        # Zapis od nowa, numerNotowania jako int
        batch = db.batch()
        n = 0
        for numer, url, title, tworca, tytul, yt in collected:
            doc_id = f"{numer:03d}"
            youtube_id = _hof_youtube_id(yt)
            youtube_url = yt
            if youtube_url and not youtube_url.startswith("http"):
                youtube_url = f"https://www.youtube.com/watch?v={youtube_id}" if youtube_id else ""
            if not youtube_url and youtube_id:
                youtube_url = f"https://www.youtube.com/watch?v={youtube_id}"
            is_special = "specjalna" in (title or "").lower()
            if is_special:
                notowanie = title if title else "Edycja specjalna"
                edycja_str = url.split("/lista/")[-1] if "/lista/" in url else f"edycja-{numer}"
            else:
                notowanie = f"TOP 10 #{doc_id}"
                edycja_str = url.split("/lista/")[-1] if "/lista/" in url else f"edycja-{numer}"
            ref = db.collection("HALL OF FAME").document(doc_id)
            batch.set(ref, {
                "edycja": edycja_str,
                "notowanie": notowanie,
                "numerNotowania": int(numer),
                "source": "bot",
                "source_url": url,
                "tytul": tytul,
                "wykonawca": tworca,
                "youtubeId": youtube_id,
                "youtubeUrl": youtube_url,
                "updated_at": firestore.SERVER_TIMESTAMP,
            })
            n += 1
            if n >= 400:
                batch.commit()
                batch = db.batch()
                n = 0
        if n > 0:
            batch.commit()
        print(f"HALL OF FAME: zapisano od nowa {len(collected)} wpisow")
        return True
    except Exception as e:
        print(f"ERROR hall_of_fame rebuild: {e}")
        import traceback
        traceback.print_exc()
        return False

@tasks.loop(minutes=1)
async def check_hall_of_fame_loop():
    global _hof_last_sunday_run, _hof_initial_done
    try:
        now = datetime.now(WARSAW)
        # 1) Raz po starcie - zamiana teraz, zeby nie czekac do niedzieli
        if not _hof_initial_done:
            _hof_initial_done = True
            print("HALL OF FAME: start - przebudowa teraz (zamiana wszystkich danych)")
            await _hof_rebuild_all()
            return
        # 2) Potem tylko niedziela 23:30-23:59, raz dziennie
        if now.weekday() != 6:
            return
        if not (now.hour == 23 and now.minute >= 30):
            return
        today = now.strftime("%Y-%m-%d")
        if _hof_last_sunday_run == today:
            return
        _hof_last_sunday_run = today
        print(f"HALL OF FAME: niedziela 23:30 - sprawdzam nowa zamknieta edycje ({today})")
        await _hof_rebuild_all()
    except Exception as e:
        print(f"ERROR hall_of_fame loop: {e}")

@bot.event
async def on_ready():
    print(f"READY {bot.user}")
    if not check_lista.is_running():
        check_lista.start()
    if not check_wyniki_loop.is_running():
        check_wyniki_loop.start()
    if not check_glosowanie_link.is_running():
        check_glosowanie_link.start()
    if not check_typy_cleanup.is_running():
        check_typy_cleanup.start()
    if not sync_members_loop.is_running():
        sync_members_loop.start()
    if not check_hall_of_fame_loop.is_running():
        check_hall_of_fame_loop.start()
    await sync_members()

start_health_server()

_raw_token = os.getenv("DISCORD_TOKEN") or ""
_token = _raw_token.strip().strip('"').strip("'").strip()
if not _token:
    print("FATAL: DISCORD_TOKEN pusty - ustaw w Hostless Environment variables")
    while True:
        time.sleep(60)

print(f"DEBUG TOKEN len={len(_token)} kropki={_token.count('.')}")

try:
    bot.run(_token)
except Exception as e:
    print(f"FATAL LoginFailure: {e}")
    import traceback
    traceback.print_exc()
    while True:
        time.sleep(60)

