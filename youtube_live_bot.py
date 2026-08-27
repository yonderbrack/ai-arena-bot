import os, re, time, json, base64, difflib
from youtube_transcript_api import YouTubeTranscriptApi
import firebase_admin
from firebase_admin import credentials, firestore

b64=os.getenv("FIREBASE_B64")
cred_dict=json.loads(base64.b64decode(b64).decode('utf-8'))
cred=credentials.Certificate(cred_dict)
if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)
db=firestore.client()

VIDEO_ID=os.getenv("TEST_VIDEO_ID","gAgi3qMpqIU")

ZNANE=["Koda Grace","Zapach Pomaranczy","Zakazany Owoc","Jedna rodzina"]
MAPA={"pierwsze":"1","drugie":"2","trzecie":"3","czwarte":"4","piąte":"5"}

def best_match(t):
    for u in ZNANE:
        if u.lower() in t.lower(): return u
    return t

def main():
    seen=set()
    print(f"BOT V13 TRANSCRIPT - start {VIDEO_ID}")
    while True:
        try:
            # pobiera napisy live bez pobierania audio = zero bana
            transcript = YouTubeTranscriptApi.get_transcript(VIDEO_ID, languages=['pl'])
            full_text = " ".join([x['text'] for x in transcript[-20:]])
            print(f"TRANSCRIPT: {full_text[:200]}")

            # twoja logika extract
            for m in re.finditer(r'miejsc[aeu].*?([1-5])', full_text, re.I):
                num=m.group(1)
                title=best_match(full_text[m.end():m.end()+100])
                key=f"{num}:{title.lower()}"
                if key not in seen and len(title)>3:
                    db.collection("config").document("top5").set({f"miejsce{num}":title},merge=True)
                    print(f"ZAPISANO {num}: {title}")
                    seen.add(key)
        except Exception as e:
            print(f"transcript err {e} - czekam 10s")
        time.sleep(10)

if __name__=="__main__": main()
