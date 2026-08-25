import os, re, time, json, base64, requests, tempfile, subprocess, glob
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
print("Ladowanie Whisper tiny PL...")
model=WhisperModel("tiny",device="cpu",compute_type="int8")
print("Whisper ready - slucha audio")

def transcribe_audio_file(wav_path):
    try:
        segments,info=model.transcribe(wav_path,language="pl",beam_size=1,vad_filter=True)
        return " ".join([s.text for s in segments]).lower()
    except Exception as e:
        print(f"Transcribe error: {e}")
        return ""

# ================== FIX V11 - TYLKO ANDROID, BEZ PETLI 8 KLIENTOW ==================
def download_live_chunk(video_id, start_seconds=3000, duration=30):
    """
    STARY KOD ROBIL:
      for client in ['tv','tv_embedded','web_safari','web_embedded','android','ios','mweb','web']:
          ...
    To powodowalo 7x FAIL BOT DETECTED i krecenie w kolko.

    NOWY KOD: tylko android + default, 1 proba, koniec petli.
    """
    tmpdir = tempfile.mkdtemp()
    wav_path = os.path.join(tmpdir, "chunk.wav")
    
    # JEDNA komenda yt-dlp z android - omija blokady
    # Uzywamy yt-dlp direct + ffmpeg seek
    url = f"https://www.youtube.com/watch?v={video_id}"
    
    # Opcje ktore dzialaja 25.08.2026
    ydl_cmd = [
        "yt-dlp",
        "--quiet",
        "--no-warnings",
        "-f", "bestaudio[ext=m4a]/bestaudio/best",
        "--extractor-args", "youtube:player_client=android",
        "--no-playlist",
        "-o", "-",
        url
    ]
    
    # Fallback jesli android nie zadziala
    ydl_cmd_fallback = [
        "yt-dlp",
        "--quiet",
        "--no-warnings",
        "-f", "bestaudio/best",
        "-o", "-",
        url
    ]

    audio_url = None
    for cmd in [ydl_cmd, ydl_cmd_fallback]:
        try:
            print(f"Trying yt-dlp with {cmd[5]} client...")
            # Najpierw pobierz URL audio bez sciagania
            result = subprocess.run(
                ["yt-dlp", "--quiet", "--no-warnings", "-f", "bestaudio", 
                 "--extractor-args", "youtube:player_client=android",
                 "--get-url", url],
                capture_output=True, text=True, timeout=20
            )
            if result.stdout.strip().startswith("http"):
                audio_url = result.stdout.strip().split("\n")[0]
                print(f"AUDIO URL OK via android")
                break
        except Exception as e:
            print(f"FAIL get-url: {e}")
            continue
    
    if not audio_url:
        # Ostatnia deska - default client
        try:
            result = subprocess.run(
                ["yt-dlp", "--quiet", "--get-url", "-f", "bestaudio", url],
                capture_output=True, text=True, timeout=20
            )
            if result.stdout.strip().startswith("http"):
                audio_url = result.stdout.strip().split("\n")[0]
                print(f"AUDIO URL OK via default")
        except Exception as e:
            print(f"FAIL default: {e}")
            return None

    if not audio_url:
        print(f"FAIL all clients for {video_id}")
        return None

    # Pobierz chunk przez ffmpeg z seekiem
    try:
        # ffmpeg -ss START -i AUDIO_URL -t DURATION -ar 16000 -ac 1 wav
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_seconds),
            "-i", audio_url,
            "-t", str(duration),
            "-ar", "16000",
            "-ac", "1",
            "-c:a", "pcm_s16le",
            wav_path
        ]
        print(f"AUDIO URL OK seek {start_seconds}")
        subprocess.run(ffmpeg_cmd, capture_output=True, timeout=30)
        if os.path.exists(wav_path) and os.path.getsize(wav_path) > 1000:
            print(f"DOWNLOADED chunk {start_seconds}-{start_seconds+duration} via ffmpeg size {os.path.getsize(wav_path)}")
            return wav_path
        else:
            print("ffmpeg produced empty file")
            return None
    except Exception as e:
        print(f"ffmpeg error: {e}")
        return None

PLACE_RE = re.compile(r"miejsce\s+(\d+).*?numer\s+(\d+)", re.IGNORECASE)
MAPA = {
    "jeden": "1", "dwa": "2", "trzy": "3", "cztery": "4", "pięć": "5",
    "piec": "5", "sześć": "6", "szesc": "6", "siedem": "7", "osiem": "8",
    "dziewięć": "9", "dziewiec": "9", "dziesięć": "10", "dziesiec": "10"
}

def main():
    print("BOT V11 ANTI-BOT TV - START 3000s - 1 client android (fix loop)")
    start_offset = 3000
    
    while True:
        try:
            video_id = TEST_VIDEO_ID or get_live_video_id(YOUTUBE_CHANNEL_ID)
            if not video_id:
                print("No live video, waiting 30s...")
                time.sleep(30)
                continue

            print(f"BOT V10 ANTI-BOT TV - miejsce + numer DOWOLNIE start {start_offset}")
            wav = download_live_chunk(video_id, start_seconds=start_offset, duration=30)
            if not wav:
                print("download failed, wait 10s")
                time.sleep(10)
                continue

            text = transcribe_audio_file(wav)
            print(f"Transcribed: {text[:200]}")
            
            # Tu twoja logika miejsca + numer
            # ...
            
            # Sprzatanie
            try:
                os.remove(wav)
            except: pass

            start_offset += 30
            time.sleep(2)

        except Exception as e:
            print(f"MAIN LOOP ERROR: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()

