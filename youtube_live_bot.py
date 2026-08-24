import os, re, time, json, base64, requests
import firebase_admin
from firebase_admin import credentials, firestore
from youtube_transcript_api import YouTubeTranscriptApi

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
        # youtube.com/watch?v=XXXX
        m = re.search(r"watch\?v=([A-Za-z0-9_-]{11})", r.text) or re.search(r"watch\?v=([A-Za-z0-9_-]+)", r.url)
        if m:
            return m.group(1)
    except Exception as e:
        print(f"get_live error: {e}")
    return None

# wykrywa pojedyncze miejsca - dziala jak mowisz "miejsce 5 to..."
SINGLE_RE = re.compile(r'miejsce\s+([1-5])\s*(?:to|:|jest|-\s*|)\s*([^\n\.]+?)(?:\s+miejsce|\s*$|\.)', re.I)

def save_single(place_num, title):
    title = title.strip()[:120]
    if len(title) < 3:
        return
    field = f"miejsce{place_num}"
    # zapisuje do tego samego dokumentu co apka czyta
    db.collection("config").document("top5").set({
        field: title,
        "updated_at": firestore.SERVER_TIMESTAMP,
        f"updated_{place_num}": firestore.SERVER_TIMESTAMP
    }, merge=True)
    print(f"✅ ZAPISANO {field}: {title}")

def main_loop():
    print("BOT LIVE START - nasluchuje co niedziele 20-22 ale sprawdza caly czas")
    last_vid = None
    seen = {} # zeby nie spamowac tym samym
    while True:
        try:
            vid = get_live_video_id(YOUTUBE_CHANNEL_ID)
            if not vid:
                print(f"{time.strftime('%H:%M:%S')} - brak LIVE, sleep 60s")
                time.sleep(60)
                continue

            if vid!= last_vid:
                print(f"🔴 WYKRYTO LIVE {vid}")
                last_vid = vid
                seen.clear()

            try:
                # bierzemy transkrypcje PL
                transcript = YouTubeTranscriptApi.get_transcript(vid, languages=['pl', 'pl-PL'])
                full_text = " ".join([t['text'] for t in transcript[-150:]]).lower()
                # print dla debug
                print(f"chunk: {full_text[-300:]}")

                for m in SINGLE_RE.finditer(full_text):
                    num = int(m.group(1))
                    title = m.group(2).strip()
                    key = f"{num}:{title.lower()}"
                    if key not in seen:
                        save_single(num, title)
                        seen[key] = True

            except Exception as e:
                # na live transcript czasem nie ma jeszcze - to normalne
                print(f"transcript err: {e}")

            time.sleep(20)
        except Exception as e:
            print(f"loop err: {e}")
            time.sleep(30)

if __name__=="__main__":
    main_loop()
