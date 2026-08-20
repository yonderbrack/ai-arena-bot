import os, re, time, json, base64
import firebase_admin
from firebase_admin import credentials, firestore
from youtube_transcript_api import YouTubeTranscriptApi
import requests

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
        m = re.search(r"watch\?v=([A-Za-z0-9_-]+)", r.url)
        if m:
            return m.group(1)
    except Exception as e:
        print(e)
    return None

def parse_top5(full_text):
    pat = r"miejsce\s*5.*?[:\.]\s*(.+?)\s*miejsce\s*4.*?[:\.]\s*(.+?)\s*miejsce\s*3.*?[:\.]\s*(.+?)\s*miejsce\s*2.*?[:\.]\s*(.+?)\s*miejsce\s*1.*?[:\.]\s*(.+?)(?:\.|$)"
    m = re.search(pat, full_text, re.IGNORECASE | re.DOTALL)
    if m:
        return [m.group(1).strip(), m.group(2).strip(), m.group(3).strip(), m.group(4).strip(), m.group(5).strip()]
    return None

def save_top5(l):
    data = {"miejsce5":l[0],"miejsce4":l[1],"miejsce3":l[2],"miejsce2":l[3],"miejsce1":l[4]}
    db.collection("config").document("top5").set(data)
    print(f"ZAPISANO {data}")

def main_loop():
    print("BOT LIVE START")
    last=None
    while True:
        try:
            vid = get_live_video_id(YOUTUBE_CHANNEL_ID)
            if not vid:
                time.sleep(60)
                continue
            if vid!=last:
                print(f"LIVE {vid}")
                last=vid
            try:
                tr = YouTubeTranscriptApi.get_transcript(vid, languages=['pl'])
                full=" ".join([t['text'] for t in tr[-100:]])
                top=parse_top5(full)
                if top:
                    save_top5(top)
            except Exception as e:
                print(e)
            time.sleep(15)
        except Exception as e:
            print(e)
            time.sleep(15)

if __name__=="__main__":
    main_loop()
