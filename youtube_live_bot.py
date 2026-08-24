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
        m = re.search(r"watch\?v=([A-Za-z0-9_-]{11})", r.text)
        if m:
            return m.group(1)
        m2 = re.search(r"watch\?v=([A-Za-z0-9_-]+)", r.url)
        if m2:
            return m2.group(1)
    except Exception as e:
        print(f"get_live error: {e}")
    return None

SINGLE_RE = re.compile(r'miejsce\s+([1-5])\s*(?:to|:|jest|-)?\s*([^\n\.]{3,100})', re.I)

def fetch_transcript(vid):
    # obsluga starej i nowej wersji biblioteki
    try:
        # nowa wersja >=1.0
        api = YouTubeTranscriptApi()
        data = api.fetch(vid, languages=['pl','pl-PL'])
        return [d.text for d in data]
    except Exception:
        try:
            # stara wersja
            return [t['text'] for t in YouTubeTranscriptApi.get_transcript(vid, languages=['pl'])]
        except Exception as e:
            # proba przez list_transcripts
            try:
                ytt = YouTubeTranscriptApi.list_transcripts(vid)
                for tr in ytt:
                    if 'pl' in tr.language_code:
                        return [t['text'] for t in tr.fetch()]
                # fallback pierwszy
                return [t['text'] for t in next(iter(ytt)).fetch()]
            except Exception as e2:
                print(f"transcript err: {e} / {e2}")
                return []

def save_single(num, title):
    title = title.strip()[:150]
    if len(title) < 3: return
    db.collection("config").document("top5").set({
        f"miejsce{num}": title,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)
    print(f"✅ ZAPISANO miejsce{num}: {title}")

def main_loop():
    print("BOT LIVE START - fixed API")
    last_vid = None
    seen = set()
    while True:
        try:
            vid = get_live_video_id(YOUTUBE_CHANNEL_ID)
            if not vid:
                print("brak LIVE")
                time.sleep(60)
                continue
            if vid != last_vid:
                print(f"🔴 LIVE {vid}")
                last_vid = vid
                seen.clear()

            texts = fetch_transcript(vid)
            if texts:
                full = " ".join(texts[-150:]).lower()
                print(f"chunk: ...{full[-250:]}")
                for m in SINGLE_RE.finditer(full):
                    num = int(m.group(1))
                    title = m.group(2).strip()
                    key = f"{num}:{title.lower()}"
                    if key not in seen:
                        save_single(num, title)
                        seen.add(key)
            else:
                print("pusty transcript - live moze nie miec napisow PL")

            time.sleep(25)
        except Exception as e:
            print(f"loop err: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main_loop()
