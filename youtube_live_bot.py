import os, re, time, json, base64, requests
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
        if 'isLive' in r.text or 'hqdefault_live' in r.text:
            m = re.search(r"watch\?v=([A-Za-z0-9_-]{11})", r.text)
            if m:
                return m.group(1)
    except Exception as e:
        print(f"get_live error: {e}")
    print("brak LIVE (kanal nie nadaje)")
    return None

def fetch_transcript_new(vid):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi()
        fetched = api.fetch(vid, languages=['pl','pl-PL','en'])
        return [x.text for x in fetched]
    except Exception as e:
        print(f"transcript err new API: {e}")
        return []

def save_single(num, title):
    title = title.strip()[:150]
    if len(title) < 3: return
    db.collection("config").document("top5").set({
        f"miejsce{num}": title,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)
    print(f"✅ ZAPISANO miejsce{num}: {title}")

def main():
    print("BOT LIVE START - v3 fixed API")
    last_vid = None
    seen = set()
    while True:
        try:
            vid = get_live_video_id(YOUTUBE_CHANNEL_ID)
            if not vid:
                time.sleep(60)
                continue
            if vid != last_vid:
                print(f"🔴 LIVE {vid}")
                last_vid = vid
                seen.clear()

            texts = fetch_transcript_new(vid)
            if texts:
                full = " ".join(texts[-150:]).lower()
                print(f"chunk: ...{full[-250:]}")
                for m in re.finditer(r'miejsce\s+([1-5])\s*(?:to|:|jest|-)?\s*([^\n\.]{3,100})', full):
                    num = int(m.group(1))
                    title = m.group(2).strip()
                    key = f"{num}:{title.lower()}"
                    if key not in seen:
                        save_single(num, title)
                        seen.add(key)
            else:
                print("pusty transcript - czekam")
            time.sleep(25)
        except Exception as e:
            print(f"loop err: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()
