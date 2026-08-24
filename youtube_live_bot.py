import os, re, time, json, base64, requests, tempfile, subprocess
import firebase_admin
from firebase_admin import credentials, firestore

b64 = os.getenv("FIREBASE_B64")
cred_dict = json.loads(base64.b64decode(b64).decode('utf-8'))
cred = credentials.Certificate(cred_dict)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db = firestore.client()

YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID", "UCDgUD2W-MItyPy7QKRTSp_Q")

def get_live_video_id(channel_id):
    try:
        r = requests.get(f"https://www.youtube.com/channel/{channel_id}/live", allow_redirects=True, timeout=10)
        if 'hqdefault_live' in r.text or '"isLive":true' in r.text:
            m = re.search(r"watch\?v=([A-Za-z0-9_-]{11})", r.text)
            if m:
                return m.group(1)
    except Exception as e:
        print(f"live check err: {e}")
    return None

from faster_whisper import WhisperModel
print("Ladowanie Whisper small PL...")
model = WhisperModel("small", device="cpu", compute_type="int8")
print("Whisper ready - slucha audio")

def transcribe_audio_file(wav_path):
    try:
        segments, info = model.transcribe(wav_path, language="pl", beam_size=1, vad_filter=True)
        text = " ".join([s.text for s in segments])
        return text.lower()
    except Exception as e:
        print(f"whisper err: {e}")
        return ""

def download_live_chunk(video_id, duration=15):
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        direct_url = subprocess.check_output(["yt-dlp", "-f", "bestaudio", "--no-playlist", "-g", url], text=True, timeout=15).strip().split('\n')[0]
        tmp_wav = tempfile.mktemp(suffix=".wav")
        subprocess.run(["ffmpeg", "-y", "-i", direct_url, "-t", str(duration), "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", tmp_wav], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        return tmp_wav
    except Exception as e:
        print(f"download chunk err: {e}")
        return None

PLACE_RE = re.compile(r'miejsce\s+([1-5])\s*(?:to|:|jest|-)?\s*([^\n]{3,120})', re.I)

def main():
    print("BOT LIVE AUDIO START")
    seen=set()
    while True:
        vid = get_live_video_id(YOUTUBE_CHANNEL_ID)
        if not vid:
            print("brak LIVE - sleep 60s")
            time.sleep(60)
            continue
        print(f"🔴 LIVE {vid} - nasluchuje")
        while True:
            if not get_live_video_id(YOUTUBE_CHANNEL_ID):
                print("LIVE koniec")
                break
            wav = download_live_chunk(vid, 15)
            if not wav:
                time.sleep(5)
                continue
            text = transcribe_audio_file(wav)
            print(f"🎤 {text}")
            for m in PLACE_RE.finditer(text):
                num = int(m.group(1))
                title = m.group(2).strip()
                key = f"{num}:{title.lower()}"
                if key not in seen and len(title)>3:
                    db.collection("config").document("top5").set({f"miejsce{num}": title, "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
                    print(f"✅ ZAPISANO miejsce{num}: {title}")
                    seen.add(key)
            try: os.remove(wav)
            except: pass
            time.sleep(2)

if __name__ == "__main__":
    main()
