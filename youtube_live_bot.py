
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
START_SEEK = int(os.getenv("TEST_SEEK", "3800"))

print(f"BOT START V3 od {START_SEEK}s - LARGE-V3-TURBO + SLOWNIK 37 UTWOROW")

from faster_whisper import WhisperModel
print("Ladowanie Whisper LARGE-V3-TURBO PL... (1.5GB - 2 min)")
try:
    model=WhisperModel("large-v3-turbo",device="cpu",compute_type="int8")
except:
    print("Brak turbo - laduje MEDIUM")
    model=WhisperModel("medium",device="cpu",compute_type="int8")
print("Whisper LARGE ready")

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
    "Andrzej Tablica",
    "Pędrak",
    "Zapach Pomaranczy",
    "Szczera do bolu",
    "Jedna rodzina",
    "Zakazany Owoc",
    "List do nieba",
    "Historia z rozdroz"
]

def fix_text(text):
    corrections = {
        "siudne": "siódme", "siudme": "siódme", "siodme": "siódme",
        "indy": "Andy", "indii": "Andy", "indysie": "Andy",
        "tobicy": "tablicy", "tobica": "tablica", "tabicy": "tablicy",
        "pedrak": "Pędrak", "koda grace": "Koda Grace", "kody grace": "Koda Grace",
        "srodme": "siódme", "siudma": "siódme",
        "wyrble": "trybie", "slyszymy": "słyszymy",
        "ogiecujemy": "obiecujemy", "polsciemy": "pogościmy",
        "wstawimy": "wystawimy", "groupowiczom": "grupowiczom",
        "carmenaigrami": "Carmenaigrami", "zapach pomaranczy": "Zapach Pomarańczy",
        "dodeklab": "DodekLab", "zakazany owoc": "Zakazany Owoc",
        "jedna rodzina": "Jedna rodzina", "wilkor": "Wilkor",
        "czarny krawat": "Czarny Krawat", "szczera do bolu": "Szczera do bólu",
        "tato gdzie jest mama": "Tato gdzie jest Mama"
    }
    low = text.lower()
    for k,v in corrections.items():
        low = low.replace(k, v.lower())
    return low

def find_best_match(trans_text):
    best = None
    best_score = 0
    for utwor in ZNANE_UTWORY:
        score = difflib.SequenceMatcher(None, trans_text.lower(), utwor.lower()).ratio()
        for word in utwor.lower().split():
            if len(word)>4 and word in trans_text.lower():
                score+=0.15
        if score>best_score and score>0.45:
            best_score=score
            best=utwor
    return best, best_score

def transcribe_audio_file(wav_path):
    try:
        tmp_clean = tempfile.mktemp()+".clean.wav"
        subprocess.run(
            ["ffmpeg","-y","-i",wav_path,"-ar","16000","-ac","1","-af","loudnorm=I=-16:TP=-1:LRA=11","-loglevel","quiet",tmp_clean],
            timeout=20
        )
        use_path = tmp_clean if os.path.exists(tmp_clean) else wav_path
        segments,_=model.transcribe(
            use_path,
            language="pl",
            beam_size=10,
            best_of=5,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            initial_prompt="Lista przebojow AI Arena FM TOP 15. Miejsce numer trzy to Koda Grace. Miejsce drugie to Carmenaigrami Zapach Pomaranczy. Miejsce pierwsze to Poeta Ulicy List do nieba. Na miejscu czwartym jest DodekLab Zakazany Owoc. Na miejscu piatym jest pawlo Jedna rodzina. Andy, tablica wynikow, Pedrak, Wilkor Historia z rozdroz, Miami69Records Kto sieje wiatr, AGA-MUSIC Tato gdzie jest Mama, A.AWE56 Szczera do bolu, Soulforge Rising, Poland Czarny Krawat, Sound Studio Jestesmy w raju.",
            hotwords="Koda Grace Zapach Pomaranczy Zakazany Owoc Jedna rodzina Historia z rozdroz Szczera do bolu Czarny Krawat Poeta Ulicy List do nieba Soulforge AGA-MUSIC Wilkor DodekLab Carmenaigrami Andy tablica Pedrak siódme miejsce"
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
    try:
        url=f"https://www.youtube.com/watch?v={video_id}"
        tmp_base=tempfile.mktemp()
        tmp_wav=tmp_base+".wav"
        end=seek+duration
        cmd=["yt-dlp","-f","bestaudio",
             "--extractor-args","youtube:player_client=android",
             "--no-playlist","--sleep-requests","3","--sleep-interval","5","--max-sleep-interval","10",
             "--retries","10","--extractor-retries","10",
             "--download-sections",f"*{seek}-{end}",
             "-x","--audio-format","wav",
             "--force-keyframes-at-cuts",
             "-o",tmp_wav, url]
        result = subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
        out = (result.stdout.decode()+result.stderr.decode()).lower()
        if "429" in out:
            print("429 - spie 30s")
            time.sleep(30)
            return None, seek+duration
        if os.path.exists(tmp_wav):
            print(f"DOWNLOADED chunk {seek}-{end}")
            return tmp_wav, end
        found=glob.glob(tmp_base+"*")
        if found:
            print(f"DOWNLOADED chunk {seek}-{end} -> {found[0]}")
            return found[0], end
        print(f"NOT FOUND chunk {seek}-{end} - {out[:300]}")
        time.sleep(10)
        return None, end
    except Exception as e:
        print(f"download chunk err:{e}")
        time.sleep(10)
        return None, seek+duration

MAPA_SLOWNIE={"pierwsze":"1","drugie":"2","trzecie":"3","czwarte":"4","piąte":"5","piate":"5","pierwsza":"1","druga":"2","trzecia":"3","czwarta":"4","piąta":"5"}

def extract_place_and_title(text):
    results=[]
    for m in re.finditer(r'miejsc[aeu]|pozycj[ai]', text, re.I):
        start = max(0, m.start()-20)
        end = min(len(text), m.end()+40)
        window = text[start:end]
        num_match = re.search(r'\b([1-5])\b', window)
        if not num_match:
            slow_match = re.search(r'(pierwsze|drugie|trzecie|czwarte|piąte|piate|pierwsza|druga|trzecia|czwarta|piąta)', window, re.I)
            if slow_match:
                num = MAPA_SLOWNIE.get(slow_match.group(1).lower(),"")
                if num:
                    title_window = text[m.end():m.end()+120]
                    results.append((num, title_window))
            continue
        num = num_match.group(1)
        title_window = text[m.end():m.end()+150]
        results.append((num, title_window))
    for m in re.finditer(r'numer\s*([1-5]).*?miejsc[aeu]|([1-5])\s*miejsc[aeu]', text, re.I):
        num = m.group(1) or m.group(2)
        if num:
            title_window = text[m.end():m.end()+150]
            results.append((num, title_window))
    return results

def clean_title(t):
    t=t.strip()
    t=re.sub(r'^(to jest|jest to|to|jest|a i a|a na miejscu \d+|renie.*?)\s+', '', t, flags=re.I)
    t=re.sub(r'^[:\-.,\s]+', '', t)
    t=t.split('.')[0].split('!')[0]
    t=t[:120]
    best, score = find_best_match(t)
    if best and score>0.55:
        return best
    t=re.sub(r'\s+to swoim.*$', '', t, flags=re.I)
    t=re.sub(r'\s+utwór.*$', '', t, flags=re.I)
    return t.strip()[:80]

def main():
    seek = START_SEEK
    seen=set()
    print("BOT V3 - lapie miejsce + numer w dowolnej kolejnosci")
    while True:
        wav, next_seek = download_live_chunk(TEST_VIDEO_ID, seek, 30)
        seek = next_seek
        if not wav:
            time.sleep(5)
            continue
        text=transcribe_audio_file(wav)
        if text:
            print(f"🎤 [{seek-30}-{seek}] {text[:500]}")
            places = extract_place_and_title(text)
            for num, title_raw in places:
                print(f"  -> WYKRYTO miejsce={num} surowy: '{title_raw[:100]}'")
                title = clean_title(title_raw)
                print(f"  -> po clean + fuzzy: '{title}'")
                if len(title) < 4: 
                    print("     ODRZUCONO - za krotkie")
                    continue
                if "punkty" in title.lower(): continue
                if "lista ca" in title.lower(): continue
                if "edycja" in title.lower() and len(title)<15: continue
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
        if seek > 7200:
            print("KONIEC")
            break
        time.sleep(4)

if __name__=="__main__":
    main()

