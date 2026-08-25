import os, re, time, json, base64, requests, tempfile, subprocess
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
        if 'hqdefault_live' in r.text or '"isLive":true' in r.text:
            m=re.search(r"watch\?v=([A-Za-z0-9_-]{11})",r.text)
            if m: return m.group(1)
    except: pass
    return None

from faster_whisper import WhisperModel
print("Ladowanie Whisper small PL...")
model=WhisperModel("tiny",device="cpu",compute_type="int8")
print("Whisper ready - slucha audio")

def transcribe_audio_file(wav_path):
    try:
        segments,info=model.transcribe(wav_path,language="pl",beam_size=1,vad_filter=True)
        return " ".join([s.text for s in segments]).lower()
    except Exception as e:
        print(f"whisper err:{e}"); return ""

def download_live_chunk(video_id,duration=30):
    try:
        url=f"https://www.youtube.com/watch?v={video_id}"
        direct_url=subprocess.check_output(["yt-dlp","-f","bestaudio","--no-playlist","-g",url],text=True,timeout=20).strip().split('\n')[0]
        tmp_wav=tempfile.mktemp(suffix=".wav")
        seek=os.getenv("TEST_SEEK")
        if TEST_VIDEO_ID and seek:
            cmd=["ffmpeg","-y","-ss",str(seek),"-i",direct_url,"-t",str(duration),"-vn","-ac","1","-ar","16000","-f","wav",tmp_wav]
            os.environ["TEST_SEEK"]=str(int(seek)+duration)
        else:
            cmd=["ffmpeg","-y","-i",direct_url,"-t",str(duration),"-vn","-ac","1","-ar","16000","-f","wav",tmp_wav]
        subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=25)
        return tmp_wav
    except Exception as e:
        print(f"download chunk err:{e}"); return None

PLACE_RE=re.compile(r'miejsce\s+([1-5]|pierwsze|drugie|trzecie|czwarte|piąte|piate)\s*(?:to|:|jest|-)?\s*([^\n]{3,120})',re.I)
MAPA={"pierwsze":"1","drugie":"2","trzecie":"3","czwarte":"4","piąte":"5","piate":"5"}

def main():
    print(f"BOT TEST MODE film={TEST_VIDEO_ID}" if TEST_VIDEO_ID else "BOT LIVE AUDIO START")
    seen=set()
    while True:
        vid=TEST_VIDEO_ID or get_live_video_id(YOUTUBE_CHANNEL_ID)
        if not vid:
            print("brak LIVE - sleep 60s"); time.sleep(60); continue
        print(f"🔴 LIVE {vid} - nasluchuje")
        while True:
            if not TEST_VIDEO_ID and not get_live_video_id(YOUTUBE_CHANNEL_ID): break
            wav=download_live_chunk(vid,30)
            if not wav: time.sleep(5); continue
            text=transcribe_audio_file(wav)
            print(f"🎤 {text}")
            for m in PLACE_RE.finditer(text):
                raw=m.group(1).lower(); num=MAPA.get(raw,raw); title=m.group(2).strip()
                key=f"{num}:{title.lower()}"
                if key not in seen and len(title)>3:
                    db.collection("config").document("top5").set({f"miejsce{num}":title,"updated_at":firestore.SERVER_TIMESTAMP},merge=True)
                    print(f"✅ ZAPISANO miejsce{num}: {title}"); seen.add(key)
            try: os.remove(wav)
            except: pass
            time.sleep(2)

if __name__=="__main__": main()
