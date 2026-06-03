import os, re, base64
from dotenv import load_dotenv
load_dotenv()  # Loads .env variables locally
import cv2, numpy as np, qrcode
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from database import init_db, get_db, hash_password

# ── Remote sync config ──────────────────────────────────
try:
    import requests as req_lib
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

REMOTE_API_URL = os.getenv("REMOTE_API_URL", "")
FACE_API_KEY   = os.getenv("FACE_API_KEY", "biomark-face-key-change-me")
USE_REMOTE     = bool(REMOTE_API_URL) and REQUESTS_OK

FACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "face_site")

app = FastAPI()
# NOTE: In production, replace "*" with specific allowed origins
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=FACE_DIR), name="static")

os.makedirs("faces",   exist_ok=True)
os.makedirs("qrcodes", exist_ok=True)

cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

@app.on_event("startup")
def startup():
    init_db()
    if USE_REMOTE:
        print(f"  Remote sync ON: {REMOTE_API_URL}")
    else:
        print("  Remote sync OFF (set REMOTE_API_URL to enable)")

def safe(s): return re.sub(r'[^a-zA-Z0-9_\-]','_',s)

@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(FACE_DIR,"register.html"),encoding="utf-8") as f:
        return HTMLResponse(f.read())

# ── Register student (creates DB account) ───────────────
class RegReq(BaseModel):
    name:str; regno:str; cls:str; password:str

@app.post("/api/register")
def register(r:RegReq):
    if not r.name or not r.regno or not r.password:
        raise HTTPException(400,"All fields required")
    if len(r.password) < 6:
        raise HTTPException(400,"Password must be at least 6 characters")
    db = get_db()
    if db.execute("SELECT id FROM students WHERE regno=?",(r.regno.upper(),)).fetchone():
        db.close(); raise HTTPException(400,"Register number already exists")
    db.execute("INSERT INTO students (name,regno,cls,password_hash) VALUES (?,?,?,?)",
               (r.name,r.regno.upper(),r.cls,hash_password(r.password)))
    db.commit(); db.close()

    # Also register on remote server if configured
    remote_msg = ""
    if USE_REMOTE:
        try:
            resp = req_lib.post(
                f"{REMOTE_API_URL.rstrip('/')}/api/face/register-student",
                json={"name":r.name, "regno":r.regno, "cls":r.cls,
                      "password":r.password, "api_key":FACE_API_KEY},
                timeout=10
            )
            if resp.status_code == 200:
                remote_msg = " (synced to remote)"
                print(f"  Remote sync OK: {r.name} ({r.regno})")
            else:
                remote_msg = " (remote sync failed)"
                print(f"  Remote sync FAILED: {resp.text}")
        except Exception as e:
            remote_msg = " (remote unreachable)"
            print(f"  Remote sync error: {e}")

    return {"success":True,"message":f"Account created for {r.name}{remote_msg}"}

# ── Face scan frame upload ───────────────────────────────
class FaceReq(BaseModel):
    name:str; regno:str; image:str

@app.post("/api/face")
def upload_face(r:FaceReq):
    folder = f"faces/{safe(r.regno)}"
    os.makedirs(folder, exist_ok=True)
    try:
        raw   = base64.b64decode(r.image.split(",")[-1])
        frame = cv2.imdecode(np.frombuffer(raw,np.uint8),cv2.IMREAD_COLOR)
        gray  = cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray,1.1,5)
    except Exception as e:
        print(f"⚠ Face upload error: {e}")
        return {"saved":False,"count":0}

    count = len([f for f in os.listdir(folder) if f.endswith(".jpg")])
    if len(faces)==0: return {"saved":False,"count":count}
    if count >= 20:   return {"saved":False,"count":20,"done":True}

    x,y,w,h = faces[0]
    cv2.imwrite(f"{folder}/{count}.jpg", frame[y:y+h,x:x+w])
    count += 1

    # Update face_scans in DB
    db  = get_db()
    row = db.execute("SELECT id FROM students WHERE regno=?",(r.regno.upper(),)).fetchone()
    if row:
        db.execute("INSERT INTO face_scans (student_id,scan_count) VALUES (?,?) "
                   "ON CONFLICT(student_id) DO UPDATE SET scan_count=?",
                   (row["id"],count,count))
        db.commit()
    db.close()

    return {"saved":True,"count":count,"done":count>=20}

# ── QR code ─────────────────────────────────────────────
@app.get("/api/qr")
def get_qr(name:str, regno:str):
    filename = f"{safe(regno)}_QR.png"
    path     = f"qrcodes/{filename}"
    qrcode.make(f"Name: {name}\nReg: {regno}").save(path)
    return FileResponse(path, media_type="image/png",
                        headers={"Content-Disposition":f"attachment; filename={filename}"})
