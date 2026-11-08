import os, json, base64, hashlib, re, asyncio, threading, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import 【entity-discord¦canonical_name=discord】
from 【entity-discord¦canonical_name=discord】.ext import commands, tasks
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
intents=【entity-discord¦canonical_name=discord】.Intents.default()
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

def _fetch_sync_debug(url):
    html=_fetch_sync(url)
    low=html.lower()
    is_cf="cf-challenge" in low or "attention required" in low or "just a moment" in low
    return html, len(html), is_cf

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

def _parse_edycje(html):
    soup=BeautifulSoup(html,"html.parser")
    out=[]
    seen=set()
    for a in soup.find_all("a",href=True):
        href=a["href"]
        if "/lista/" not in href: continue
        if href.startswith("/"): href=TOP20_BASE_URL+href
        url=href.split("?")[0].split("#")[0].rstrip("/")
        if url in seen: continue
        seen.add(url)
        card=a
        for _ in range(4):
            if card.parent: card=card.parent
        card_text=card.get_text(" ",strip=True) if card else ""
        title=""
        h=card.find(["h2","h3","h4"]) if card else None
        if h: title=h.get_text(" ",strip=True)
        if not title: title=a.get_text(" ",strip=True) or url.split("/")[-1]
        low=card_text.lower()
        if "głosowanie trwa" in low or "trwa głosowanie" in low:
            status="voting"
        elif "zamknięta" in low or "zamknięty" in low:
            status="closed"
        elif "start" in low or "poczekalnia" in low or "zgłoszenia" in low:
            status="upcoming"
        else:
            status="unknown"
        out.append({"url":url,"title":title,"status":status,"card":card_text[:300]})
    return out

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

def _to_int(v):
    try: return int(v or 0)
    except: return 0

def _unescape(s):
    try: return json.loads(f'"{s}"')
    except: return s

def _tracks_from_json_obj(obj,out):
    if isinstance(obj,dict):
        keys={str(k).lower():k for k in obj.keys()}
        tytul_k=next((keys[k] for k in keys if k in ("tytul","tytuł","title","nazwa","name")),None)
        tworca_k=next((keys[k] for k in keys if k in ("tworca","twórca","artist","autor","wykonawca","creator")),None)
        if tytul_k and obj.get(tytul_k):
            out.append({"tytul":str(obj.get(tytul_k,"")).strip(),"tworca":str(obj.get(tworca_k,"")).strip() if tworca_k else "","youtube":str(obj.get("youtube",obj.get("yt",obj.get("youtube_url",""))) or "").strip(),"fav_count":_to_int(obj.get("fav_count",obj.get("favs",obj.get("serca",0)))),"track_id":str(obj.get("track_id",obj.get("id","")) or "").strip(),"no":_to_int(obj.get("no",obj.get("position",obj.get("miejsce",0))))})
            return
        for v in obj.values(): _tracks_from_json_obj(v,out)
    elif isinstance(obj,list):
        for v in obj: _tracks_from_json_obj(v,out)

def _extract_tracks_v2(html):
    tracks=[]
    soup=BeautifulSoup(html,"html.parser")
    script=soup.find("script",id="__NEXT_DATA__")
    if script and script.string:
        try: _tracks_from_json_obj(json.loads(script.string),tracks)
        except Exception as e: print(f"PARSE NEXT_DATA blad: {e}")
    if not tracks:
        for m in re.finditer(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)',html,re.S):
            chunk=m.group(1)
            try:
                decoded=json.loads(f'"{chunk}"')
                if decoded.strip().startswith(("{","[")):
                    _tracks_from_json_obj(json.loads(decoded),tracks)
            except: pass
    if not tracks:
        pairs=re.findall(r'"tytul"\s*:\s*"((?:\\.|[^"\\])*)".{0,400}?"tworca"\s*:\s*"((?:\\.|[^"\\])*)"',html)
        pairs+= [(b,a) for a,b in re.findall(r'"tworca"\s*:\s*"((?:\\.|[^"\\])*)".{0,400}?"tytul"\s*:\s*"((?:\\.|[^"\\])*)"',html)]
        for tytul,tworca in pairs:
            tracks.append({"tytul":_unescape(tytul),"tworca":_unescape(tworca),"youtube":"","fav_count":0,"track_id":"","no":0})
    if not tracks:
        old=_extract_tracks_from_html(html)
        for no,tytul,tworca,yt,fav,tid in old:
            tracks.append({"no":no,"tytul":tytul,"tworca":tworca,"youtube":yt,"fav_count":fav,"track_id":tid})
    seen=set(); clean=[]
    for i,t in enumerate(tracks,1):
        key=(t["tworca"].lower(),t["tytul"].lower())
        if key in seen or not t["tytul"]: continue
        seen.add(key)
        if not t.get("no"): t["no"]=i
        clean.append(t)
    clean.sort(key=lambda x:x["no"])
    return clean

@tasks.loop(seconds=60)
async def check_lista():
    now=datetime.now(WARSAW)
    debug={"last_run":now.strftime("%d.%m.%Y %H:%M:%S"),"candidates":[],"errors":[]}
    try:
        html_edycje,ln,is_cf=await asyncio.to_thread(_fetch_sync_debug,TOP20_EDYCJE_URL)
        print(f"EDYCJE: len={ln} cf={is_cf}")
        candidates=_parse_edycje(html_edycje)
        print(f"EDYCJE: znaleziono {len(candidates)} linkow /lista/: {[(c['status'],c['title']) for c in candidates]}")
        debug["candidates"]=candidates
        ordered=[c for c in candidates if c["status"]=="voting"]+[c for c in candidates if c["status"]=="closed"]+[c for c in candidates if c["status"] in ("unknown",)]
        for c in ordered:
            url=c["url"]
            try:
                print(f"TOP20 WWW: probuje {c['status']} {c['title']} {url}")
                html,hlen,cf=await asyncio.to_thread(_fetch_sync_debug,url)
                tracks=_extract_tracks_v2(html)
                print(f"TOP20 WWW: {url} -> {len(tracks)} utworow, html_len={hlen} cf={cf}")
                debug["last_tried"]={"url":url,"status":c["status"],"title":c["title"],"tracks":len(tracks),"html_len":hlen,"cf":cf}
                if len(tracks)>=1:
                    final_list=[]; detailed=[]; serca=[]
                    for t in tracks:
                        base=f"{t['no']}. {t['tworca']} - {t['tytul']}" if t["tworca"] else f"{t['no']}. {t['tytul']}"
                        if t["youtube"]: base+=f" {t['youtube']}"
                        if t["fav_count"]: base+=f" [{t['fav_count']}]"
                        final_list.append(base.strip())
                        serca.append(int(t["fav_count"]))
                        detailed.append({"no":int(t["no"]),"tytul":t["tytul"],"tworca":t["tworca"],"youtube":t["youtube"],"fav_count":int(t["fav_count"]),"track_id":t["track_id"]})
                    db.collection("lista").document("aktualna").set({
                        "utwory":final_list,"tracks":detailed,"serca":serca,"count":len(final_list),
                        "updated_at":firestore.SERVER_TIMESTAMP,"updated_day":now.strftime("%d.%m.%Y %H:%M:%S"),
                        "source_url":url,"edycja":c["title"],"typ":"TOP10" if "top 10" in c["title"].lower() else "TOP20",
                        "status":c["status"],
                    })
                    print(f"ZAPISANO {len(final_list)} z {url} [{c['title']}]")
                    debug["saved_from"]=url
                    break
                else:
                    debug["errors"].append(f"{url}: 0 utworow, html_len={hlen} cf={cf}")
            except Exception as e:
                print(f"BLAD {url}: {e}")
                debug["errors"].append(f"{url}: {e}")
                continue
        else:
            print("TOP20: zadna edycja nie dala utworow, NIE nadpisuje aktualna")
    except Exception as e:
        print(f"ERROR lista {e}")
        debug["errors"].append(str(e))
        import traceback; traceback.print_exc()
    finally:
        try: db.collection("lista").document("_debug").set(debug,merge=True)
        except: pass

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
