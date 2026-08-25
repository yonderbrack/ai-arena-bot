
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
START_SEEK = 3000  # SZTYWNO 3000s - ignoruje Railway TEST_SEEK

print(f"BOT V4 START od {START_SEEK}s - FIX NOT FOUND + SMALL + SLOWNIK")

from faster_whisper import WhisperModel
print("Ladowanie Whisper SMALL PL...")
model=WhisperModel("small",device="cpu",compute_type="int8")
print("Whisper SMALL ready - FIX NOT FOUND")

ZNANE_UTWORY = [
    "Poeta Ulicy List do nieba",
    "Soulforge Rising Broken But Still Fighting",
    "AGA-MUSIC Tato gdzie jest Mama",
    "A.AWE56 SZCZERA DO BOLU",
    "Miami69Records Kto sieje wiatr ten zbiera burze",
    "Wilkor Historia z rozdroz",
    "Poland Czarny Krawat Elegantka",
    "pawlo Jedna rodzina",
    "DodekLab Zakazany Owoc",
    "Carmenaigrami Zapach Pomaranczy",
    "Sound Studio Jestesmy w raju",
    "Koda Grace",
    "Zapach Pomaranczy",
    "Zakazany Owoc",
    "Jedna rodzina",
    "Szczera do bolu",
    "Czarny Krawat"
]

def fix_text(t):
    corr={"siudne":"siódme","siudme":"siódme","indy":"Andy","tobicy":"tablicy","pedrak":"Pędrak","koda grace":"Koda Grace","zapach pomaranczy":"Zapach Pomarańczy","zakazany owoc":"Zakazany Owoc","jedna rodzina":"Jedna rodzina","szczera do bolu":"Szczera do bólu"}
    low=t.lower()
    for k,v in corr.items():
        low=low.replace(k,v.lower())
    return low

def find_best_match(txt):
    best=None; best_score=0
    for u in ZNANE_UTWORY:
        s=difflib.SequenceMatcher(None, txt.lower(), u.lower()).ratio()
        for w in u.lower().split():
            if len(w)>4 and w in txt.lower():
                s+=0.15
        if s>best_score and s>0.45:
            best_score=s; best=u
    return best,best_score

def transcribe_audio_file(wav_path):
    try:
        tmp_clean=tempfile.mktemp()+".clean.wav"
        subprocess.run(["ffmpeg","-y","-i",wav_path,"-ar","16000","-ac","1","-af","loudnorm=I=-16:TP=-1:LRA=11","-loglevel","quiet",tmp_clean],timeout=20)
        use_path=tmp_clean if os.path.exists(tmp_clean) else wav_path
        segments,_=model.transcribe(
            use_path,
            language="pl",
            beam_size=5,
            vad_filter=True,
            initial_prompt="Lista przebojow AI Arena FM TOP 15. Miejsce numer trzy to Koda Grace. Miejsce drugie to Carmenaigrami Zapach Pomaranczy. Na miejscu czwartym jest DodekLab Zakazany Owoc. Miejsce piate to pawlo Jedna rodzina. Andy tablica wynikow.",
            hotwords="Koda Grace Zapach Pomaranczy Zakazany Owoc Jedna rodzina Szczera do bolu Czarny Krawat Koda Grace Andy tablica"
        )
        text=" ".join([s.text for s in segments])
        text=fix_text(text)
        try: os.remove(tmp_clean)
        except: pass
        return text
    except Exception as e:
        print(f"whisper err:{e}")
        return ""

def download_live_chunk(video_id, seek, duration=30):
    # METODA 1: yt-dlp -g + ffmpeg -ss (NIE ROBI NOT FOUND)
    try:
        url=f"https://www.youtube.com/watch?v={video_id}"
        tmp_wav=tempfile.mktemp()+".wav"
        end=seek+duration
        # pobierz link audio
        cmd_url=["yt-dlp","-f","bestaudio","--extractor-args","youtube:player_client=android","-g","--no-playlist",url]
        r=subprocess.run(cmd_url,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        audio_url=r.stdout.decode().strip().split("\n")[0]
        if not audio_url or "http" not in audio_url:
            print(f"NO AUDIO URL for {seek}")
            raise Exception("no url")
        # wytnij chunk ffmpegiem
        cmd_ff=["ffmpeg","-y","-ss",str(seek),"-i",audio_url,"-t",str(duration),"-ar","16000","-ac","1","-c:a","pcm_s16le","-loglevel","quiet",tmp_wav]
        subprocess.run(cmd_ff,timeout=60)
        if os.path.exists(tmp_wav) and os.path.getsize(tmp_wav)>1000:
            print(f"DOWNLOADED chunk {seek}-{end} via ffmpeg")
            return tmp_wav, end
        print(f"FFMPEG empty {seek}")
    except Exception as e:
        print(f"ffmpeg method err {seek}: {e}")
    # METODA 2 FALLBACK: download-sections
    try:
        tmp_base=tempfile.mktemp()
        tmp_wav2=tmp_base+".wav"
        cmd=["yt-dlp","-f","bestaudio","--extractor-args","youtube:player_client=android","--no-playlist","--download-sections",f"*{seek}-{seek+duration}","-x","--audio-format","wav","-o",tmp_wav2, url]
        subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90)
        if os.path.exists(tmp_wav2):
            print(f"DOWNLOADED chunk {seek}-{seek+duration} via sections")
            return tmp_wav2, seek+duration
        found=glob.glob(tmp_base+"*")
        if found:
            return found[0], seek+duration
    except Exception as e:
        print(f"sections err {e}")
    print(f"NOT FOUND chunk {seek}-{seek+duration} - probuje dalej")
    time.sleep(5)
    return None, seek+duration

MAPA={"pierwsze":"1","drugie":"2","trzecie":"3","czwarte":"4","piąte":"5","piate":"5","pierwsza":"1","druga":"2","trzecia":"3","czwarta":"4","piąta":"5"}

def extract_place(text):
    res=[]
    # wszystkie warianty: miejsce, miejscU, miejscA, pozycja, numer - w dowolnej kolejnosci
    patterns = [
        r'miejsc[aeu]\s*(?:numer\s*)?([1-5])',
        r'miejsc[aeu].*?numer\s*([1-5])',
        r'numer\s*([1-5]).*?miejsc[aeu]',
        r'pozycj[ai]\s*(?:numer\s*)?([1-5])',
        r'\b([1-5])\s*miejsc[aeu]',
        r'miejsc[aeu]\s*(pierwsze|drugie|trzecie|czwarte|piąte|piate)',
        r'(pierwsze|drugie|trzecie|czwarte|piąte|piate)\s*miejsc[aeu]'
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.I):
            raw=m.group(1)
            if not raw: continue
            num=MAPA.get(raw.lower(), raw)
            title_win=text[m.end():m.end()+150]
            res.append((num, title_win))
    return res

def clean_title(t):
    t=t.strip().split('.')[0][:120]
    t=re.sub(r'^(to jest|jest to|to|jest)\s+', '', t, flags=re.I)
    best,score=find_best_match(t)
    if best and score>0.5:
        return best
    t=re.sub(r'\s+to swoim.*$', '', t, re.I)
    return t.strip()[:80]

def main():
    seek=START_SEEK
    seen=set()
    print("BOT V4 - miejsce + numer dowolna kolejnosc + FIX NOT FOUND")
    while True:
        wav, next_seek = download_live_chunk(TEST_VIDEO_ID, seek, 30)
        seek=next_seek
        if not wav:
            time.sleep(3)
            continue
        text=transcribe_audio_file(wav)
        if text:
            print(f"🎤 [{seek-30}-{seek}] {text[:500]}")
            for num, raw in extract_place(text):
                print(f"  -> WYKRYTO miejsce={num} raw='{raw[:100]}'")
                title=clean_title(raw)
                print(f"  -> clean='{title}'")
                if len(title)<4: continue
                if "punkty" in title.lower(): continue
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
            print("KONIEC")
            break
        time.sleep(2)

if __name__=="__main__":
    main()

