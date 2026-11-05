import os, json, base64, hashlib, re, asyncio, threading, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
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
        self.send_response(200); self.send_header("Content-type","text/plain"); self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args): return
def start_health_server():
    try:
        s=HTTPServer(("0.0.0.0",PORT),HealthHandler)
        threading.Thread(target=s.serve_forever,daemon=True).start()
        print(f"HEALTH {PORT}")
    except Exception as e: print(e)

b64=os.getenv("FIREBASE_B64")
if b64:
    d=json.loads(base64.b64decode(b64).decode())
    if not firebase_admin._apps: firebase_admin.initialize_app(credentials.Certificate(d))
else:
    if not firebase_admin._apps: firebase_admin.initialize_app()
db=firestore.client()
intents=discord.Intents.default()
intents.message_content=True
intents.members=True
bot=commands.Bot(command_prefix="!",intents=intents)
WARSAW=ZoneInfo("Europe/Warsaw")
TOP20_BASE_URL=os.getenv("TOP20_BASE_URL","https://aiarenatop10.pl")
TOP20_EDYCJE_URL=f"{TOP20_BASE_URL}/edycje"

def ensure_users_doc(discord_id,nick):
    try:
        ref=db.collection("users").document(str(discord_id))
        if not ref.get().exists:
            ref.set({"discordId":str(discord_id),"discord_id":str(discord_id),"nick":nick,"discordName":nick,"createdAt":firestore.SERVER_TIMESTAMP,"lastLoginAt":firestore.SERVER_TIMESTAMP},merge=True)
    except: pass

def _fetch_sync(url):
    try:
        import cloudscraper
        scraper=cloudscraper.create_scraper()
        r=scraper.get(url,timeout=20)
        r.raise_for_status()
        return r.text
    except:
        headers={"User-Agent":"Mozilla/5.0 Chrome/124.0","Accept-Language":"pl-PL,pl;q=0.9"}
        r=requests.get(url,headers=headers,timeout=20)
        r.raise_for_status()
        return r.text

def _parse_edycje_for_top10_urls(html):
    urls=[]
    try:
        soup=BeautifulSoup(html,"html.parser")
        for a in soup.find_all("a",href=True):
            href=a["href"]
            if "/lista/" not in href: continue
            txt=(a.get_text(" ",strip=True)+" "+str(a.get("title",""))).upper()
            if "top-10" in href.lower() or "TOP 10" in txt:
                if href.startswith("/"): href=TOP20_BASE_URL+href
                urls.append(href.split("?")[0].split("#")[0].rstrip("/"))
    except: pass
    return list(dict.fromkeys(urls))

def _extract_tracks_from_html(html):
    tracks=[]
    try:
        soup=BeautifulSoup(html,"html.parser")
        articles=soup.find_all("article",class_=lambda c:c and "track" in c)
        if not articles: articles=soup.find_all("article",id=lambda x:x and x.startswith("u-t_"))
        for idx,art in enumerate(articles):
            tytul=art.get("data-tytul") or art.get("data-title") or ""
            tworca=art.get("data-tworca") or art.get("data-artist") or ""
            if not tytul: continue
            no_el=art.find("span",class_=lambda c:c and "track-no" in c)
            try: no=int(no_el.get_text(strip=True)) if no_el else idx+1
            except: no=idx+1
            track_id=art.get("id") or ""
            if track_id.startswith("u-"): track_id=track_id[2:]
            yt=""
            for a in art.find_all("a",href=True):
                if "youtu.be" in a["href"] or "youtube.com" in a["href"]:
                    yt=a["href"].strip(); break
            fav=0
            fav_el=art.select_one("span.fav-count, .fav-count")
            if fav_el:
                m=re.search(r"\d+",fav_el.get_text(" ",strip=True))
                if m: fav=int(m.group())
            tracks.append((no,tytul.strip(),tworca.strip(),yt,fav,track_id.strip()))
    except Exception as e:
        print(f"EXTRACT error {e}")
    tracks.sort(key=lambda x:x[0])
    return tracks

@tasks.loop(seconds=60)
async def check_lista():
    now=datetime.now(WARSAW)
    try:
        # NAJNOWSZE TOP20 NAJPIERW - bez parsowania /edycje
        candidate_urls=[]
        for n in range(35,0,-1):
            candidate_urls.append(f"{TOP20_BASE_URL}/lista/ai-arena-top-20-edycja-{n}")
        # dopisz TOP10 specjalne na koniec jako fallback
        try:
            html=await asyncio.to_thread(_fetch_sync, TOP20_EDYCJE_URL)
            top10=_parse_edycje_for_top10_urls(html)
            candidate_urls.extend(top10)
            print(f"TOP20 FIX: brute 35..1 + {len(top10)} TOP10 special")
        except Exception as e:
            print(f"TOP10 parse blad {e}")

        for url in candidate_urls:
            try:
                print(f"TOP20 WWW: probuje {url}")
                html=await asyncio.to_thread(_fetch_sync, url)
                tracks=_extract_tracks_from_html(html)
                print(f"TOP20 WWW: {url} -> {len(tracks)} utworow")
                if len(tracks)>=1:
                    final_list=[]
                    detailed=[]
                    serca=[]
                    for no,tytul,tworca,yt,fav,tid in tracks:
                        base=f"{no}. {tworca} - {tytul}" if tworca else f"{no}. {tytul}"
                        if yt: base+=f" {yt}"
                        if fav: base+=f" [{fav}]"
                        final_list.append(base.strip())
                        serca.append(int(fav))
                        detailed.append({"no":int(no),"tytul":tytul,"tworca":tworca,"youtube":yt,"fav_count":int(fav),"track_id":tid})
                    db.collection("lista").document("aktualna").set({
                        "utwory":final_list,"tracks":detailed,"serca":serca,"count":len(final_list),
                        "updated_at":firestore.SERVER_TIMESTAMP,"updated_day":now.strftime("%d.%m.%Y %H:%M:%S"),
                        "source_url":url,"edycja":url.split("/")[-1],
                        "typ":"TOP10" if "top-10" in url.lower() else "TOP20",
                    })
                    print(f"ZAPISANO {len(final_list)} z {url}")
                    break
            except Exception as e:
                print(f"BLAD {url}: {e}")
                continue
    except Exception as e:
        print(f"ERROR lista {e}")
        import traceback; traceback.print_exc()

@tasks.loop(seconds=60)
async def check_glosowanie_link():
    try:
        cid=int(os.getenv("GLOSOWANIE_CHANNEL_ID") or "1517609248765382776")
        ch=bot.get_channel(cid) or await bot.fetch_channel(cid)
        if not ch: return
        pat=re.compile(r"https?://(?:forms\.gle|docs\.google\.com/(?:forms|spreadsheets)/|forms\.google\.com|forms\.office\.com|docs\.google\.com/forms)/[^\s\)\]\<\>\"']+",re.I)
        async for msg in ch.history(limit=20):
            m=pat.search(msg.content or "")
            if m:
                found=m.group(0).rstrip(").,!;")
                ref=db.collection("config").document("glosowanie")
                cur=ref.get().to_dict().get("glosuj_link","") if ref.get().exists else ""
                if cur!=found:
                    ref.set({"glosuj_link":found,"source_message_id":str(msg.id),"updated_at":firestore.SERVER_TIMESTAMP},merge=True)
                break
    except: pass

def get_current_week_start():
    now=datetime.now(WARSAW)
    return (now - timedelta(days=now.weekday())).replace(hour=0,minute=0,second=0,microsecond=0)
def cleanup_old_typy(cutoff):
    try:
        cutoff_ms=int(cutoff.timestamp()*1000)
        batch=db.batch(); cnt=0; deleted=0
        for doc in db.collection("typy").stream():
            if doc.id=="_system_weekly_cleanup": continue
            ts=(doc.to_dict() or {}).get("timestamp")
            if ts and int(ts)<cutoff_ms:
                batch.delete(doc.reference); cnt+=1; deleted+=1
                if cnt>=400: batch.commit(); batch=db.batch(); cnt=0
        if cnt>0: batch.commit()
        return deleted
    except: return 0

@tasks.loop(seconds=30)
async def check_typy_cleanup():
    try:
        now=datetime.now(WARSAW)
        week_start=get_current_week_start()
        week_key=week_start.strftime("%Y-%m-%d")
        ref=db.collection("typy").document("_system_weekly_cleanup")
        snap=ref.get()
        data=snap.to_dict() or {} if snap.exists else {}
        if not data.get("initialized"):
            ref.set({"initialized":True,"cleanup_week":week_key,"last_cleanup_at":firestore.SERVER_TIMESTAMP},merge=True)
            return
        if now.weekday()!=0 or now.hour<12 or data.get("cleanup_week")==week_key: return
        d=cleanup_old_typy(week_start)
        ref.set({"cleanup_week":week_key,"last_cleanup_at":firestore.SERVER_TIMESTAMP,"last_deleted_count":d},merge=True)
    except: pass

async def sync_members():
    try:
        guild=bot.get_guild(int(os.getenv("GUILD_ID") or "1515466354113777715"))
        if not guild: return
        batch=db.batch(); cnt=0
        async for member in guild.fetch_members(limit=None):
            if member.bot: continue
            batch.set(db.collection("czlonkowie").document(str(member.id)),{"discord_id":str(member.id),"username":member.name,"display_name":member.display_name,"avatar":str(member.display_avatar.url) if member.display_avatar else ""},merge=True)
            ensure_users_doc(str(member.id),member.display_name)
            cnt+=1
            if cnt>=400: batch.commit(); batch=db.batch(); cnt=0
        if cnt>0: batch.commit()
    except Exception as e: print(f"ERROR czlonkowie {e}")

@tasks.loop(hours=6)
async def sync_members_loop(): await sync_members()
@bot.event
async def on_member_join(member):
    if member.bot: return
    db.collection("czlonkowie").document(str(member.id)).set({"discord_id":str(member.id),"username":member.name,"display_name":member.display_name,"active":True},merge=True)
    ensure_users_doc(str(member.id),member.display_name)
@bot.event
async def on_ready():
    print(f"READY {bot.user}")
    if not check_lista.is_running(): check_lista.start()
    if not check_glosowanie_link.is_running(): check_glosowanie_link.start()
    if not check_typy_cleanup.is_running(): check_typy_cleanup.start()
    if not sync_members_loop.is_running(): sync_members_loop.start()
    await sync_members()

start_health_server()
_token=(os.getenv("DISCORD_TOKEN") or "").strip().strip('"').strip("'")
if not _token:
    print("FATAL DISCORD_TOKEN pusty")
    while True: time.sleep(60)
print(f"DEBUG TOKEN len={len(_token)} kropki={_token.count('.')}")
try: bot.run(_token)
except Exception as e:
    print(f"FATAL {e}")
    import traceback; traceback.print_exc()
    while True: time.sleep(60)

