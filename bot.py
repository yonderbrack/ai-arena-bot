import os, json, base64, discord, re
from discord.ext import commands, tasks
import firebase_admin
from firebase_admin import credentials, firestore

b64 = os.getenv("FIREBASE_B64")
cred_dict = json.loads(base64.b64decode(b64).decode('utf-8'))
cred = credentials.Certificate(cred_dict)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@tasks.loop(minutes=1)
async def check_lista():
    try:
        cid = int(os.getenv("CHANNEL_ID") or os.getenv("LISTA_CHANNEL_ID"))
        ch = bot.get_channel(cid) or await bot.fetch_channel(cid)
        all_lines = []
        async for msg in ch.history(limit=200):
            if not msg.content: continue
            # bierzemy kazda linie ktora zaczyna sie od cyfry
            for raw in msg.content.split("\n"):
                raw = raw.strip()
                if not raw: continue
                if re.match(r'^\d+[\.\)]?\s*', raw):
                    all_lines.append(raw)

        uniq = {}
        for line in all_lines:
            m = re.match(r'^\s*(\d+)', line)
            if m:
                uniq[int(m.group(1))] = line

        sorted_list = [uniq[k] for k in sorted(uniq.keys())]

        print(f"Znaleziono {len(all_lines)} linii, unikalnych {len(sorted_list)}: {sorted(uniq.keys())}")

        if len(sorted_list) >= 1:
            db.collection("lista").document("aktualna").set({
                "utwory": sorted_list,
                "count": len(sorted_list),
                "updated_at": firestore.SERVER_TIMESTAMP
            })
            print(f"ZAPISANO {len(sorted_list)} do lista/aktualna")
    except Exception as e:
        print(f"ERROR: {e}")

@bot.event
async def on_ready():
    print(f"READY {bot.user}")
    check_lista.start()

bot.run(os.getenv("DISCORD_TOKEN"))import os, re, time, json, base64, requests, tempfile, subprocess, glob
import firebase_admin
from firebase_admin import credentials, firestore

b64=os.getenv("FIREBASE_B64")
cred_dict=json.loads(base64.b64decode(b64).decode('utf-8'))
cred=credentials.Certificate(cred_dict)
if not firebase_admin._apps: firebase_admin.initialize_app(cred)
db=firestore.client()

YOUTUBE_CHANNEL_ID=os.getenv("YOUTUBE_CHANNEL_ID","UCDgUD2W-MItyPy7QKRTSp_Q")
TEST_VIDEO_ID=os.getenv("TEST_VIDEO_ID")

def get_live_video_id(channel_id):
    try:
        r=requests.get(f"https://www.youtube.com/channel/{channel_id}/live",allow_redirects=True,timeout=10)
        if "hQdefault_live" in r.text or '"isLive":true' in r.text:
            m=re.search(r"watch\?v=([A-Za-z0-9_-]{11})",r.text)
            if m: return m.group(1)
    except: pass
    return None

from faster_whisper import WhisperModel
print("Ladowanie Whisper SMALL - 300MB...")
model=WhisperModel("small",device="cpu",compute_type="int8")
print("Whisper SMALL ready")

def transcribe_audio_file(wav_path):
    try:
        segments,info=model.transcribe(wav_path,language="pl",beam_size=1,vad_filter=True)
        return " ".join([s.text for s in segments]).lower()
    except Exception as e:
        print(f"Transcribe error: {e}")
        return ""

def download_live_chunk_v12(video_id, start_seconds=3000, duration=30):
    """
    V12 FIX 25.08.2026 - YouTube wymaga teraz PO_TOKEN
    - Dockerfile ma nodejs + deno (generuje token automatycznie)
    - Nie iterujemy po 9 klientach, tylko 1 proba z android + web
    - Fallback na Invidious jeśli dalej BOT DETECTED
    """
    tmpdir = tempfile.mkdtemp()
    wav_path = os.path.join(tmpdir, "chunk.wav")
    url = f"https://www.youtube.com/watch?v={video_id}"
    
    # PROBA 1: yt-dlp z auto PO_TOKEN (dziala tylko jak w Docker jest node/deno)
    try:
        print(f"BOT V12 - proba yt-dlp android+web z PO_TOKEN")
        cmd = [
            "yt-dlp", "--quiet",
            "-f", "bestaudio[ext=m4a]/bestaudio/best",
            "--extractor-args", "youtube:player_client=android,web",
            "--get-url", url
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        audio_url = result.stdout.strip().split("\n")[0] if result.stdout else ""
        if audio_url.startswith("http"):
            print(f"AUDIO URL OK via android+web")
            return ffmpeg_extract(audio_url, start_seconds, duration, wav_path)
        else:
            print(f"FAIL android+web: {result.stderr[:200]}")
    except Exception as e:
        print(f"FAIL android+web exception: {e}")

    # PROBA 2: Invidious fallback - omija BOT DETECTED calkowicie
    # Uzywamy publicznego invidious instance
    try:
        print("Proba Invidious fallback...")
        inv_instances = [
            "https://invidious.f5.si",
            "https://inv.nadeko.net",
            "https://y.com.sb"
        ]
        for inv in inv_instances:
            try:
                r = requests.get(f"{inv}/api/v1/videos/{video_id}", timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    # znajdz audio
                    for fmt in data.get("adaptiveFormats", []):
                        if "audio" in fmt.get("type",""):
                            audio_url = fmt.get("url")
                            if audio_url:
                                print(f"AUDIO URL OK via invidious {inv}")
                                return ffmpeg_extract(audio_url, start_seconds, duration, wav_path)
            except: continue
    except Exception as e:
        print(f"Invidious fail: {e}")

    print(f"NO AUDIO URL at all seek {start_seconds} - all blocked")
    return None

def ffmpeg_extract(audio_url, start_seconds, duration, wav_path):
    try:
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_seconds),
            "-i", audio_url,
            "-t", str(duration),
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            wav_path
        ]
        print(f"AUDIO URL OK seek {start_seconds}")
        subprocess.run(cmd, capture_output=True, timeout=30)
        if os.path.exists(wav_path) and os.path.getsize(wav_path) > 2000:
            print(f"DOWNLOADED chunk {start_seconds}-{start_seconds+duration} via ffmpeg size {os.path.getsize(wav_path)}")
            return wav_path
    except Exception as e:
        print(f"ffmpeg error: {e}")
    return None

def main():
    print("BOT V12 ANTI-BOT TV - START 3000s - 1 client + PO_TOKEN + Invidious")
    start_offset = 3000
    while True:
        try:
            video_id = TEST_VIDEO_ID or get_live_video_id(YOUTUBE_CHANNEL_ID)
            if not video_id:
                time.sleep(30)
                continue
            print(f"BOT V12 - miejsce + numer DOWOLNIE start {start_offset}")
            wav = download_live_chunk_v12(video_id, start_seconds=start_offset, duration=30)
            if not wav:
                print(f"NOT FOUND chunk {start_offset}-{start_offset+30} - skip")
                time.sleep(10)
                continue
            text = transcribe_audio_file(wav)
            print(f"Transcribed: {text[:300]}")
            try: os.remove(wav)
            except: pass
            start_offset += 30
            time.sleep(2)
        except Exception as e:
            print(f"MAIN ERROR: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()

