import os, re, time, json, base64, tempfile, subprocess, glob, difflib
import firebase_admin
from firebase_admin import credentials, firestore

b64=os.getenv("FIREBASE_B64")
cred_dict=json.loads(base64.b64decode(b64).decode('utf-8'))
cred=credentials.Certificate(cred_dict)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db=firestore.client()

TEST_VIDEO_ID=os.getenv("TEST_VIDEO_ID", "gAgi3qMpqIU")
START_SEEK = 3000  # SZTYWNO

print(f"BOT V5 FIX NOT FOUND - START {START_SEEK}s - SMALL")

from faster_whisper import WhisperModel
print("Ladowanie Whisper SMALL...")
model=WhisperModel("small",device="cpu",compute_type="int8")
print("Whisper SMALL ready")

ZNANE=["Koda Grace","Zapach Pomaranczy","Zakazany Owoc","Jedna rodzina","Szczera do bolu","Czarny Krawat","Poeta Ulicy List do nieba","Wilkor Historia z rozdroz","Tato gdzie jest Mama","DodekLab","Carmenaigrami"]

def fix(t):
    return t.lower().replace("koda grace","Koda Grace").replace("zapach pomaranczy","Zapach Pomarańczy").replace("siudne","siódme").replace("indy","Andy").replace("tobicy","tablicy")

def best_match(txt):
    for u in ZNANE:
        if u.lower() in txt.lower() or txt.lower() in u.lower():
            return u
    best=None; sc=0
    for u in ZNANE:
        s=difflib.SequenceMatcher(None, txt.lower(), u.lower()).ratio()
        if s>sc and s>0.5:
            sc=s; best=u
    return best

def transcribe(wav):
    try:
        clean=tempfile.mktemp()+".clean.wav"
        subprocess.run(["ffmpeg","-y","-i",wav,"-ar","16000","-ac","1","-af","loudnorm","-loglevel","quiet",clean],timeout=20)
        use=clean if os.path.exists(clean) else wav
        segs,_=model.transcribe(use,language="pl",beam_size=5,vad_filter=True,initial_prompt="AI Arena FM TOP 15 miejsce numer trzy to Koda Grace miejsce drugie to Zapach Pomaranczy miejsce pierwsze to Poeta Ulicy miejsce czwarte to Zakazany Owoc miejsce piate to Jedna rodzina Andy tablica",hotwords="Koda Grace Zapach Pomaranczy Zakazany Owoc Jedna rodzina")
        txt=" ".join([s.text for s in segs])
        txt=fix(txt)
        try: os.remove(clean)
        except: pass
        return txt
    except Exception as e:
        print(f"whisper err {e}")
        return ""

def download_chunk(vid, seek, dur=30):
    url=f"https://www.youtube.com/watch?v={vid}"
    tmp=tempfile.mktemp()+".wav"
    end=seek+dur
    try:
        # 1. Pobierz audio URL
        cmd=["yt-dlp","-f","bestaudio[ext=m4a]/bestaudio","--extractor-args","youtube:player_client=android","-g",url]
        r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40)
        out=r.stdout.decode().strip()
        err=r.stderr.decode().strip()
        aurl=out.split("\n")[0].strip()
        if not aurl.startswith("http"):
            print(f"NO URL seek {seek} err: {err[:300]} out: {out[:300]}")
            # proba bez android client
            cmd2=["yt-dlp","-f","bestaudio","-g",url]
            r2=subprocess.run(cmd2,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40)
            aurl=r2.stdout.decode().strip().split("\n")[0]
        if aurl.startswith("http"):
            print(f"AUDIO URL OK seek {seek}")
            cmd_ff=["ffmpeg","-y","-ss",str(seek),"-i",aurl,"-t",str(dur),"-ar","16000","-ac","1","-c:a","pcm_s16le","-loglevel","error",tmp]
            res=subprocess.run(cmd_ff,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=70)
            if os.path.exists(tmp) and os.path.getsize(tmp)>3000:
                print(f"DOWNLOADED chunk {seek}-{end} via ffmpeg size {os.path.getsize(tmp)}")
                return tmp, end
            else:
                print(f"FFMPEG FAIL size 0 err: {res.stderr.decode()[:500]}")
        else:
            print(f"NO AUDIO URL at all seek {seek}")
    except Exception as e:
        print(f"dl err {seek}: {e}")
    # fallback - sections
    try:
        base=tempfile.mktemp()
        out2=base+".wav"
        cmd=["yt-dlp","-f","bestaudio","--extractor-args","youtube:player_client=android","--download-sections",f"*{seek}-{end}","-x","--audio-format","wav","-o",out2,url]
        subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90)
        if os.path.exists(out2):
            print(f"DOWNLOADED chunk {seek}-{end} via sections FALLBACK")
            return out2, end
    except: pass
    print(f"NOT FOUND chunk {seek}-{end}")
    time.sleep(5)
    return None, end

MAPA={"pierwsze":"1","drugie":"2","trzecie":"3","czwarte":"4","piąte":"5","piate":"5","pierwsza":"1","druga":"2","trzecia":"3","czwarta":"4","piąta":"5"}

def extract(text):
    res=[]
    for m in re.finditer(r'miejsc[aeu]|pozycj[ai]', text, re.I):
        win=text[max(0,m.start()-20):m.end()+40]
        num_match=re.search(r'\b([1-5])\b', win)
        if num_match:
            res.append((num_match.group(1), text[m.end():m.end()+150]))
            continue
        sm=re.search(r'(pierwsze|drugie|trzecie|czwarte|piąte|piate|pierwsza|druga|trzecia|czwarta|piąta)', win, re.I)
        if sm:
            n=MAPA.get(sm.group(1).lower())
            if n:
                res.append((n, text[m.end():m.end()+150]))
    for m in re.finditer(r'numer\s*([1-5])', text, re.I):
        # numer X miejsce w dowolnej kolejnosci
        if "miejsc" in text[max(0,m.start()-20):m.start()+30].lower():
            res.append((m.group(1), text[m.end():m.end()+150]))
    return res

def clean_title(t):
    t=t.strip().split('.')[0][:120]
    t=re.sub(r'^(to jest|jest to|to)\s+', '', t, flags=re.I)
    bm=best_match(t)
    if bm:
        return bm
    return t.strip()[:80]

def main():
    seek=START_SEEK
    seen=set()
    print(f"BOT V5 - miejsce + numer DOWOLNIE + FIX NOT FOUND start {seek}")
    while True:
        wav, nxt = download_chunk(TEST_VIDEO_ID, seek, 30)
        seek=nxt
        if not wav:
            time.sleep(3)
            continue
        txt=transcribe(wav)
        if txt:
            print(f"🎤 [{seek-30}-{seek}] {txt[:500]}")
            for num, raw in extract(txt):
                print(f"  -> WYKRYTO miejsce={num} raw='{raw[:100]}'")
                title=clean_title(raw)
                print(f"  -> clean='{title}'")
                if len(title)<4: continue
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
        if seek>7200:
            break
        time.sleep(2)

if __name__=="__main__":
    main()

