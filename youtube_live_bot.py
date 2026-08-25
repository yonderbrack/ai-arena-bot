
import os, re, time, json, base64, tempfile, subprocess, glob, random, difflib
import firebase_admin
from firebase_admin import credentials, firestore

b64=os.getenv("FIREBASE_B64")
cred_dict=json.loads(base64.b64decode(b64).decode('utf-8'))
cred=credentials.Certificate(cred_dict)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db=firestore.client()

TEST_VIDEO_ID=os.getenv("TEST_VIDEO_ID", "gAgi3qMpqIU")
START_SEEK = 3000  # SZTYWNO 3000s - FIX NOT FOUND

print(f"BOT V4 FIX NOT FOUND START od {START_SEEK}s - SMALL + SLOWNIK")

from faster_whisper import WhisperModel
print("Ladowanie Whisper SMALL PL - 300MB...")
model=WhisperModel("small",device="cpu",compute_type="int8")
print("Whisper SMALL ready - slucha")

ZNANE = ["Koda Grace","Zapach Pomaranczy","Zakazany Owoc","Jedna rodzina","Szczera do bolu","Czarny Krawat","Poeta Ulicy List do nieba","Wilkor Historia z rozdroz","Tato gdzie jest Mama","Soulforge Rising","DodekLab","Carmenaigrami","Pawlo","Miami69Records","AGA-MUSIC"]

def fix(t):
    m={"siudne":"siódme","indy":"Andy","tobicy":"tablicy","pedrak":"Pędrak","koda grace":"Koda Grace","zapach pomaranczy":"Zapach Pomarańczy","zakazany owoc":"Zakazany Owoc","jedna rodzina":"Jedna rodzina"}
    low=t.lower()
    for k,v in m.items():
        low=low.replace(k,v.lower())
    return low

def best_match(txt):
    best=None; sc=0
    for u in ZNANE:
        s=difflib.SequenceMatcher(None, txt.lower(), u.lower()).ratio()
        if s>sc and s>0.5:
            sc=s; best=u
    return best

def transcribe(wav):
    try:
        clean=tempfile.mktemp()+".clean.wav"
        subprocess.run(["ffmpeg","-y","-i",wav,"-ar","16000","-ac","1","-af","loudnorm=I=-16:TP=-1:LRA=11","-loglevel","quiet",clean],timeout=20)
        use=clean if os.path.exists(clean) else wav
        segs,_=model.transcribe(use,language="pl",beam_size=5,vad_filter=True,initial_prompt="AI Arena FM TOP 15 miejsce numer trzy to Koda Grace miejsce drugie to Zapach Pomaranczy miejsce pierwsze to Poeta Ulicy miejsce czwarte to Zakazany Owoc miejsce piate to Jedna rodzina Andy tablica",hotwords="Koda Grace Zapach Pomaranczy Zakazany Owoc Jedna rodzina Andy tablica")
        txt=" ".join([s.text for s in segs])
        txt=fix(txt)
        try: os.remove(clean)
        except: pass
        return txt
    except Exception as e:
        print(f"whisper err {e}")
        return ""

def download_chunk(vid, seek, dur=30):
    try:
        url=f"https://www.youtube.com/watch?v={vid}"
        tmp=tempfile.mktemp()+".wav"
        end=seek+dur
        # METODA FFMPEG - nie robi NOT FOUND
        cmd_url=["yt-dlp","-f","bestaudio","--extractor-args","youtube:player_client=android","-g","--no-playlist",url]
        r=subprocess.run(cmd_url,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        aurl=r.stdout.decode().strip().split("\n")[0]
        if aurl and "http" in aurl:
            cmd_ff=["ffmpeg","-y","-ss",str(seek),"-i",aurl,"-t",str(dur),"-ar","16000","-ac","1","-c:a","pcm_s16le","-loglevel","quiet",tmp]
            subprocess.run(cmd_ff,timeout=60)
            if os.path.exists(tmp) and os.path.getsize(tmp)>2000:
                print(f"DOWNLOADED chunk {seek}-{end} via ffmpeg")
                return tmp, end
        # FALLBACK sections
        base=tempfile.mktemp()
        out=base+".wav"
        cmd=["yt-dlp","-f","bestaudio","--extractor-args","youtube:player_client=android","--no-playlist","--download-sections",f"*{seek}-{end}","-x","--audio-format","wav","-o",out,url]
        subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90)
        if os.path.exists(out):
            print(f"DOWNLOADED chunk {seek}-{end} via sections")
            return out, end
        f=glob.glob(base+"*")
        if f:
            return f[0], end
    except Exception as e:
        print(f"dl err {seek}: {e}")
    print(f"NOT FOUND chunk {seek}-{seek+dur}")
    time.sleep(3)
    return None, seek+dur

MAPA={"pierwsze":"1","drugie":"2","trzecie":"3","czwarte":"4","piąte":"5","piate":"5","pierwsza":"1","druga":"2","trzecia":"3","czwarta":"4","piąta":"5"}

def extract(text):
    res=[]
    for m in re.finditer(r'miejsc[aeu]|pozycj[ai]', text, re.I):
        win=text[max(0,m.start()-15):m.end()+35]
        num=re.search(r'\b([1-5])\b', win)
        if not num:
            sm=re.search(r'(pierwsze|drugie|trzecie|czwarte|piąte|piate|pierwsza|druga|trzecia|czwarta|piąta)', win, re.I)
            if sm:
                n=MAPA.get(sm.group(1).lower())
                if n:
                    res.append((n, text[m.end():m.end()+150]))
            continue
        res.append((num.group(1), text[m.end():m.end()+150]))
    for m in re.finditer(r'numer\s*([1-5]).*?miejsc[aeu]|([1-5])\s*miejsc[aeu]', text, re.I):
        n=m.group(1) or m.group(2)
        if n:
            res.append((n, text[m.end():m.end()+150]))
    return res

def clean(t):
    t=t.strip().split('.')[0][:120]
    t=re.sub(r'^(to jest|jest to|to)\s+', '', t, flags=re.I)
    bm=best_match(t)
    if bm:
        return bm
    return t.strip()[:80]

def main():
    seek=START_SEEK
    seen=set()
    while True:
        wav, nxt = download_chunk(TEST_VIDEO_ID, seek, 30)
        seek=nxt
        if not wav:
            time.sleep(2)
            continue
        txt=transcribe(wav)
        if txt:
            print(f"🎤 [{seek-30}-{seek}] {txt[:500]}")
            for num, raw in extract(txt):
                print(f"  -> WYKRYTO miejsce={num} raw='{raw[:100]}'")
                title=clean(raw)
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

