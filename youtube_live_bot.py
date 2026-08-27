import os, re, time, json, base64
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

b64=os.getenv("FIREBASE_B64")
if not b64:
    print("BRAK FIREBASE_B64 - exit")
    exit(1)
cred_dict=json.loads(base64.b64decode(b64).decode('utf-8'))
cred=credentials.Certificate(cred_dict)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db=firestore.client()

VIDEO_ID=os.getenv("YOUTUBE_CHANNEL_ID") or os.getenv("TEST_VIDEO_ID","gAgi3qMpqIU")

ZNANE=["Koda Grace","Zapach Pomaranczy","Zakazany Owoc","Jedna rodzina"]
MAPA={"pierwsze":"1","drugie":"2","trzecie":"3","czwarte":"4","piąte":"5","1":"1","2":"2","3":"3","4":"4","5":"5"}

def best_match(t):
    t=t.lower()
    for u in ZNANE:
        if u.lower() in t: return u
    # weź pierwsze 3 słowa jako tytuł
    clean = re.sub(r'[^a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ ]','',t).strip()
    return clean[:40]

def get_live_text():
    try:
        # lista wszystkich dostępnych transkryptów - w tym automatyczne z LIVE
        transcript_list = YouTubeTranscriptApi.list_transcripts(VIDEO_ID)
        # próbuj: 1. manual PL, 2. auto PL, 3. auto EN -> tłumacz na PL
        transcript = None
        for lang_try in [('pl','manual'), ('pl','auto'), ('en','auto')]:
            try:
                if lang_try[1]=='manual':
                    transcript = transcript_list.find_transcript(['pl','pl-PL'])
                else:
                    transcript = transcript_list.find_generated_transcript(['pl','pl-PL','en'])
                break
            except: continue

        if not transcript:
            return None

        fetched = transcript.fetch()
        # weź ostatnie 30 linijek = live
        full = " ".join([x['text'] for x in fetched[-30:]])
        return full
    except (TranscriptsDisabled, NoTranscriptFound) as e:
        print(f"Napisy jeszcze nie gotowe / wyłączone: {e}")
        return None
    except Exception as e:
        print(f"transcript err {e}")
        return None

def main():
    seen=set()
    print(f"BOT V14 LIVE TRANSCRIPT - ID {VIDEO_ID} - czeka na niedzielę 20:00")
    while True:
        now = datetime.now()
        # 6 = niedziela (0=pon). Uruchamiaj tylko Nd 19:50-23:30
        is_sunday_show = now.weekday() == 6 and (20 <= now.hour < 24 or (now.hour==19 and now.minute>=50))

        # DO TESTÓW: zakomentuj linijkę wyżej i odkomentuj poniższą żeby testować teraz:
        # is_sunday_show = True

        if not is_sunday_show:
            print(f"[{now}] Nie niedziela 20:00 - śpię 60s...")
            time.sleep(60)
            continue

        text = get_live_text()
        if not text:
            print("Brak tekstu, czekam 15s...")
            time.sleep(15)
            continue

        print(f"TRANSCRIPT: {text[:250]}")

        # szukaj: miejsce 1 / pierwsze miejsce itp + tytuł obok
        for m in re.finditer(r'(?:miejsce\s*([1-5])|([1-5])\s*miejsce|pierwsze|drugie|trzecie|czwarte|piąte)', text, re.I):
            num = m.group(1) or m.group(2)
            if not num:
                low = m.group(0).lower()
                for k,v in MAPA.items():
                    if k in low:
                        num=v
                        break
            if not num: continue

            # weź 100 znaków po dopasowaniu jako tytuł
            after = text[m.end():m.end()+120]
            title = best_match(after)

            key=f"{num}:{title.lower()}"
            if key not in seen and len(title)>3:
                try:
                    db.collection("config").document("top5").set({f"miejsce{num}":title},merge=True)
                    print(f"✅ ZAPISANO {num}: {title}")
                    seen.add(key)
                except Exception as e:
                    print(f"firebase err {e}")

        time.sleep(10)

if __name__=="__main__": main()
