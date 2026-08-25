import os, re, time, json, base64, requests, tempfile, subprocess, glob
import firebase_admin
from firebase_admin import credentials, firestore

b64=os.getenv("FIREBASE_B64")
cred_dict=json.loads(base64.b64decode(b64).decode('utf-8'))
cred=credentials.Certificate(cred_dict)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db=firestore.client()

TEST_VIDEO_ID=os.getenv("TEST_VIDEO_ID", "gAgi3qMpqIU")
START_SEEK = int(os.getenv("TEST_SEEK", "1800")) # 30 minuta

print(f"BOT START od {START_SEEK}s (30 min) do 7200s (120 min) film={TEST_VIDEO_ID}")

# --- WHISPER ---
from faster_whisper import WhisperModel
print("Ladowanie Whisper tiny PL...")
model=WhisperModel("tiny",device="cpu",compute_type="int8")
print("Whisper ready")

def transcribe_audio_file(wav_path):
    try:
        segments,_=model.transcribe(wav_path,language="pl",beam_size=1,vad_filter=True)
        text=" ".join([s.text for s in segments]).lower()
        return text
    except Exception as e:
        print(f"whisper err:{e}")
        return ""

def download_live_chunk(video_id, seek, duration=30):
    try:
        url=f"https://www.youtube.com/watch?v={video_id}"
        tmp_base=tempfile.mktemp()
        tmp_wav=tmp_base+".wav"
        end=seek+duration
        # OBEJŚCIE BOTA YOUTUBE - android client
        cmd=["yt-dlp","-f","bestaudio",
             "--extractor-args","youtube:player_client=android",
             "--no-playlist","--sleep-requests","1","--retries","20",
             "--download-sections",f"*{seek}-{end}",
             "-x","--audio-format","wav",
             "--force-keyframes-at-cuts",
             "-o",tmp_wav, url]
        subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90)
        # yt-dlp czasem tworzy plik z innym rozszerzeniem
        if os.path.exists(tmp_wav):
            print(f"DOWNLOADED chunk {seek}-{end}")
            return tmp_wav, end
        found=glob.glob(tmp_base+"*")
        if found:
            print(f"DOWNLOADED chunk {seek}-{end} -> {found[0]}")
            return found[0], end
        print(f"NOT FOUND chunk {seek}-{end}")
        return None, end
    except Exception as e:
        print(f"download chunk err:{e}")
        return None, seek+duration

# Łapie wszystkie warianty: miejsce 5 to, na miejscu 5, piąte miejsce, miejsce numer 5, miejsce piąte
PLACE_RE = re.compile(r'(?:miejsce\s*(?:numer)?\s*([1-5])|([1-5])\s*miejsce|miejsce\s*(pierwsze|drugie|trzecie|czwarte|piąte|piate))\s*(?:to|:|jest|to jest|-)?\s*([^\n.,]{3,120})', re.I)
MAPA={"pierwsze":"1","drugie":"2","trzecie":"3","czwarte":"4","piąte":"5","piate":"5"}

def main():
    seek = START_SEEK
    seen=set()
    while True:
        wav, next_seek = download_live_chunk(TEST_VIDEO_ID, seek, 30)
        seek = next_seek
        if not wav:
            time.sleep(2)
            continue
        text=transcribe_audio_file(wav)
        if text:
            print(f"🎤 [{seek-30}-{seek}] {text[:250]}")
            for m in PLACE_RE.finditer(text):
                # grupa 1 = cyfra, grupa 2 = cyfra z "1 miejsce", grupa 3 = słownie
                raw_num = m.group(1) or m.group(2) or m.group(3)
                if not raw_num:
                    continue
                raw_num = raw_num.lower()
                num = MAPA.get(raw_num, raw_num)
                title = m.group(4).strip()
                # odfiltruj śmieci
                if len(title) < 4: continue
                if "punkty różnicy" in title: continue
                if "lista cała" in title: continue
                key=f"{num}:{title.lower()}"
                if key not in seen:
                    try:
                        db.collection("config").document("top5").set({f"miejsce{num}":title,"updated_at":firestore.SERVER_TIMESTAMP},merge=True)
                        print(f"✅ ZAPISANO miejsce{num}: {title}")
                        seen.add(key)
                    except Exception as e:
                        print(f"firebase err {e}")
        try: os.remove(wav)
        except: pass
        # jeśli dolecieliśmy do końca filmu (> 90 min), zakończ
        if seek > 7200:
            print("KONIEC filmu - osiągnięto 7200s (120 min)")
            break
        time.sleep(3)

if __name__=="__main__":
    main()

