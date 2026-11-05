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

import 【entity-discord¦canonical_name=discord】
from 【entity-discord¦canonical_name=discord】.ext import commands, tasks

import firebase_admin
from firebase_admin import credentials, firestore

import requests
from bs4 import BeautifulSoup

# === HOSTLESS HEALTH CHECK - musi otworzyc port dla TCP check ===
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
        candidate_urls_top20 = []
        candidate_urls_top10 = []
        try:
            edycje_html = await asyncio.to_thread(_fetch_sync, TOP20_EDYCJE_URL)
            candidate_urls_top20 = _parse_edycje_for_top20_urls(edycje_html)
            candidate_urls_top10 = _parse_edycje_for_top10_urls(edycje_html)
            print(f"TOP20 WWW: znaleziono {len(candidate_urls_top20)} linkow TOP20 + {len(candidate_urls_top10)} linkow TOP10 specjalnych w /edycje -> {candidate_urls_top10}")
        except Exception as e:
            print(f"TOP20 WWW: blad pobierania /edycje: {e}")
        candidate_urls = []
        candidate_urls.extend(candidate_urls_top10)
        candidate_urls.extend(candidate_urls_top20)
        if not candidate_urls:
            for n in range(35, 0, -1):
                candidate_urls.append(f"{TOP20_BASE_URL}/lista/ai-arena-top-20-edycja-{n}")
        else:
            existing_nums = set()
            for u in candidate_urls:
                m = re.search(r"edycja-(\d+)", u)
                if m:
                    existing_nums.add(int(m.group(1)))
            for n in range(35, 0, -1):
                if n not in existing_nums:
                    candidate_urls.append(f"{TOP20_BASE_URL}/lista/ai-arena-top-20-edycja-{n}")
            candidate_urls = list(dict.fromkeys(candidate_urls))
        final_tracks = []
        used_url = None
        # FIX: bylo [:20] - ucinane nowe edycje
        for url in candidate_urls:
            try:
                print(f"TOP20 WWW: probuje {url}")
                html = await asyncio.to_thread(_fetch_sync, url)
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

@bot.event
async def on_ready():
    print(f"READY {bot.user}")
    if not check_lista.is_running():
        check_lista.start()
    if not check_glosowanie_link.is_running():
        check_glosowanie_link.start()
    if not check_typy_cleanup.is_running():
        check_typy_cleanup.start()
    if not sync_members_loop.is_running():
        sync_members_loop.start()
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
