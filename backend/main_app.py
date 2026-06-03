import os, re, csv, io, zipfile
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from jose import JWTError, jwt
from database import init_db, get_db, hash_password, check_password, PH, _execute, _fetchone, _fetchall, row_val, USE_PG
import qrcode

SECRET   = os.getenv("JWT_SECRET", "biomark-secret-change-me")
ALGO     = "HS256"
FACE_API_KEY = os.getenv("FACE_API_KEY", "biomark-face-key-change-me")
SITE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "main_site")
QR_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "qrcodes")

os.makedirs(QR_DIR, exist_ok=True)

app = FastAPI()
# NOTE: In production, restrict origins to your domain
allowed_origins = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(CORSMiddleware, allow_origins=allowed_origins, allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=SITE_DIR), name="static")

@app.on_event("startup")
def startup():
    try:
        init_db()
        print("[OK] Database initialized successfully")
    except Exception as e:
        print(f"[ERROR] Database init failed: {e}")
        import traceback; traceback.print_exc()

# ── helpers ─────────────────────────────────────────────
p = PH  # placeholder shorthand

def make_token(d):
    return jwt.encode({**d,"exp":datetime.now(timezone.utc)+timedelta(hours=8)},SECRET,ALGO)

def get_payload(request:Request):
    a = request.headers.get("Authorization","")
    if not a.startswith("Bearer "): raise HTTPException(401,"Not authenticated")
    try:    return jwt.decode(a[7:],SECRET,algorithms=[ALGO])
    except: raise HTTPException(401,"Token expired")

def teacher_only(request:Request):
    pl = get_payload(request)
    if pl.get("role") != "teacher": raise HTTPException(403,"Teachers only")
    return pl

def admin_only(request:Request):
    pl = teacher_only(request)
    if not pl.get("is_admin"): raise HTTPException(403,"Admin teachers only")
    return pl

def student_only(request:Request):
    pl = get_payload(request)
    if pl.get("role") != "student": raise HTTPException(403,"Students only")
    return pl

def safe(s): return re.sub(r'[^a-zA-Z0-9_\-]','_',s)

def serve(path):
    with open(os.path.join(SITE_DIR,path), encoding="utf-8") as f:
        return HTMLResponse(f.read())

# ── pages ────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def index():   return serve("index.html")

@app.get("/login", response_class=HTMLResponse)
def login():   return serve("login.html")

@app.get("/student", response_class=HTMLResponse)
def student(): return serve("student.html")

@app.get("/teacher", response_class=HTMLResponse)
def teacher(): return serve("teacher.html")

# ══════════════════════════════════════════════════════════
#  AUTH
# ══════════════════════════════════════════════════════════
class TeacherLogin(BaseModel): username:str; password:str
class StudentLogin(BaseModel): regno:str;    password:str
class QRLogin(BaseModel):      token:str
class ChangePw(BaseModel):     old_password:str; new_password:str

@app.post("/api/auth/teacher")
def auth_teacher(r:TeacherLogin):
    db  = get_db()
    try:
        row = _fetchone(db, f"SELECT id,password_hash,fullname,is_admin FROM teachers WHERE username={p}",
                         (r.username.lower(),))
        if not row or not check_password(r.password, row_val(row,"password_hash")):
            raise HTTPException(401,"Invalid username or password")
        
        is_admin = bool(row_val(row, "is_admin"))
        tok = make_token({"sub":str(row_val(row,"id")),"role":"teacher","name":row_val(row,"fullname"), "is_admin": is_admin})
        return {"token":tok,"role":"teacher","name":row_val(row,"fullname"),"is_admin":is_admin}
    finally:
        db.close()

@app.post("/api/auth/student")
def auth_student(r:StudentLogin):
    db  = get_db()
    try:
        row = _fetchone(db, f"SELECT id,name,regno,cls,password_hash FROM students WHERE regno={p}",
                         (r.regno.upper(),))
        if not row or not check_password(r.password, row_val(row,"password_hash")):
            raise HTTPException(401,"Invalid register number or password")
        tok = make_token({"sub":str(row_val(row,"id")),"role":"student","name":row_val(row,"name"),"regno":row_val(row,"regno")})
        return {"token":tok,"role":"student","name":row_val(row,"name"),"regno":row_val(row,"regno"),"cls":row_val(row,"cls")}
    finally:
        db.close()

@app.post("/api/auth/student-qr")
def auth_student_qr(r:QRLogin):
    """Login a student by scanning their QR code (contains a signed JWT)."""
    try:
        payload = jwt.decode(r.token, SECRET, algorithms=[ALGO])
    except JWTError:
        raise HTTPException(401, "Invalid or expired QR code")
    if payload.get("type") != "qr_login":
        raise HTTPException(401, "Invalid QR code")
    regno = payload.get("regno", "").upper()
    if not regno:
        raise HTTPException(401, "Invalid QR code")
    db = get_db()
    try:
        row = _fetchone(db, f"SELECT id,name,regno,cls FROM students WHERE regno={p}", (regno,))
        if not row:
            raise HTTPException(401, "Student not found")
        tok = make_token({"sub":str(row_val(row,"id")),"role":"student","name":row_val(row,"name"),"regno":row_val(row,"regno")})
        return {"token":tok,"role":"student","name":row_val(row,"name"),"regno":row_val(row,"regno"),"cls":row_val(row,"cls")}
    finally:
        db.close()

@app.put("/api/auth/change-password")
def change_pw(r:ChangePw, request:Request):
    pl = get_payload(request)
    db = get_db()
    try:
        if pl["role"] == "student":
            row = _fetchone(db, f"SELECT password_hash FROM students WHERE id={p}",(pl["sub"],))
            if not check_password(r.old_password, row_val(row,"password_hash")): raise HTTPException(400,"Wrong password")
            _execute(db, f"UPDATE students SET password_hash={p} WHERE id={p}",(hash_password(r.new_password),pl["sub"]))
        else:
            row = _fetchone(db, f"SELECT password_hash FROM teachers WHERE id={p}",(pl["sub"],))
            if not check_password(r.old_password, row_val(row,"password_hash")): raise HTTPException(400,"Wrong password")
            _execute(db, f"UPDATE teachers SET password_hash={p} WHERE id={p}",(hash_password(r.new_password),pl["sub"]))
        db.commit()
    finally:
        db.close()
    return {"success":True}

# ══════════════════════════════════════════════════════════
#  STUDENT ENDPOINTS
# ══════════════════════════════════════════════════════════
@app.get("/api/student/me")
def student_me(request:Request):
    pl  = student_only(request)
    db  = get_db()
    try:
        s   = _fetchone(db, f"SELECT id,name,regno,cls FROM students WHERE id={p}",(pl["sub"],))
        sid = row_val(s,"id")
        mks = _fetchall(db, f"SELECT subject,marks,grade FROM marks WHERE student_id={p} ORDER BY subject",(sid,))
        att = _fetchone(db, f"SELECT COUNT(*) AS t,SUM(present) AS a FROM attendance WHERE student_id={p}",(sid,))
        sc  = _fetchone(db, f"SELECT scan_count FROM face_scans WHERE student_id={p}",(sid,))
        t   = row_val(att,"t") or 0
        a   = row_val(att,"a") or 0
        return {"name":row_val(s,"name"),"regno":row_val(s,"regno"),"cls":row_val(s,"cls"),
                "marks":[dict(m) for m in mks],
                "attendance":{"total":t,"present":int(a),"absent":t-int(a),
                              "pct":round(int(a)/t*100,1) if t else 0},
                "face_scans":row_val(sc,"scan_count") if sc else 0}
    finally:
        db.close()

# ══════════════════════════════════════════════════════════
#  TEACHER ENDPOINTS  (teacher has full admin access)
# ══════════════════════════════════════════════════════════
@app.get("/api/teacher/students")
def t_students(request:Request):
    teacher_only(request)
    db   = get_db()
    try:
        rows = _fetchall(db, """
            SELECT s.id,s.name,s.regno,s.cls,
                   COALESCE(fs.scan_count,0) AS sc,
                   COUNT(a.id) AS td,
                   SUM(CASE WHEN a.present=1 THEN 1 ELSE 0 END) AS ap
            FROM students s
            LEFT JOIN face_scans fs ON fs.student_id=s.id
            LEFT JOIN attendance  a  ON a.student_id=s.id
            GROUP BY s.id,s.name,s.regno,s.cls,fs.scan_count ORDER BY s.name""")
        res=[]
        for r in rows:
            t = row_val(r,"td") or 0
            a = row_val(r,"ap") or 0
            res.append({"id":row_val(r,"id"),"name":row_val(r,"name"),"regno":row_val(r,"regno"),
                        "cls":row_val(r,"cls"),"scan_count":row_val(r,"sc"),
                        "total":t,"present":int(a),
                        "pct":round(int(a)/t*100,1) if t else 0})
        return res
    finally:
        db.close()

class AddStu(BaseModel): name:str;regno:str;cls:str;password:str

@app.post("/api/teacher/students")
def t_add_student(r:AddStu,request:Request):
    admin_only(request)
    db = get_db()
    try:
        if _fetchone(db, f"SELECT id FROM students WHERE regno={p}",(r.regno.upper(),)):
            raise HTTPException(400,"Register number already exists")
        _execute(db, f"INSERT INTO students (name,regno,cls,password_hash) VALUES ({p},{p},{p},{p})",
                 (r.name,r.regno.upper(),r.cls,hash_password(r.password)))
        db.commit()
        return {"success":True}
    finally:
        db.close()

class EditStu(BaseModel): name:str;regno:str;cls:str

@app.put("/api/teacher/students/{sid}")
def t_edit_student(sid:int,r:EditStu,request:Request):
    admin_only(request)
    db = get_db()
    try:
        if _fetchone(db, f"SELECT id FROM students WHERE regno={p} AND id!={p}",(r.regno.upper(),sid)):
            raise HTTPException(400,"Register number taken")
        _execute(db, f"UPDATE students SET name={p},regno={p},cls={p} WHERE id={p}",
                 (r.name,r.regno.upper(),r.cls,sid))
        db.commit()
        return {"success":True}
    finally:
        db.close()

@app.delete("/api/teacher/students/{sid}")
def t_del_student(sid:int,request:Request):
    admin_only(request)
    db = get_db()
    try:
        _execute(db, f"DELETE FROM students WHERE id={p}",(sid,))
        db.commit()
        return {"success":True}
    finally:
        db.close()

class MarkBody(BaseModel):
    student_id:int
    subject:str
    marks:int = Field(ge=0, le=100)
    grade:str

@app.post("/api/teacher/marks")
def t_mark(r:MarkBody,request:Request):
    admin_only(request)
    db = get_db()
    try:
        _execute(db,
            f"INSERT INTO marks (student_id,subject,marks,grade) VALUES ({p},{p},{p},{p}) "
            f"ON CONFLICT(student_id,subject) DO UPDATE SET marks=excluded.marks,grade=excluded.grade",
            (r.student_id,r.subject,r.marks,r.grade))
        db.commit()
        return {"success":True}
    finally:
        db.close()

class AttBody(BaseModel): student_id:int;date:str;present:bool

@app.post("/api/teacher/attendance")
def t_att(r:AttBody,request:Request):
    admin_only(request)
    db = get_db()
    try:
        _execute(db,
            f"INSERT INTO attendance (student_id,date,present) VALUES ({p},{p},{p}) "
            f"ON CONFLICT(student_id,date) DO UPDATE SET present=excluded.present",
            (r.student_id,r.date,1 if r.present else 0))
        db.commit()
        return {"success":True}
    finally:
        db.close()

class AddTeacherBody(BaseModel):
    username: str
    password: str
    fullname: str
    is_admin: bool

@app.post("/api/admin/teachers")
def admin_add_teacher(r:AddTeacherBody, request:Request):
    admin_only(request)
    db = get_db()
    try:
        if _fetchone(db, f"SELECT id FROM teachers WHERE username={p}",(r.username.lower(),)):
            raise HTTPException(400,"Username already exists")
        admin_val = 1 if r.is_admin else 0
        _execute(db, f"INSERT INTO teachers (username,password_hash,fullname,is_admin) VALUES ({p},{p},{p},{p})",
                 (r.username.lower(),hash_password(r.password),r.fullname,admin_val))
        db.commit()
        return {"success":True}
    finally:
        db.close()

@app.get("/api/teacher/attendance-today")
def t_today(request:Request):
    teacher_only(request)
    today = datetime.now().strftime("%Y-%m-%d")
    db    = get_db()
    try:
        rows = _fetchall(db, f"""
            SELECT s.id,s.name,s.regno,a.present
            FROM students s
            LEFT JOIN attendance a ON a.student_id=s.id AND a.date={p}
            ORDER BY s.name""",(today,))
        return [{"id":row_val(r,"id"),"name":row_val(r,"name"),"regno":row_val(r,"regno"),
                 "present":bool(row_val(r,"present")) if row_val(r,"present") is not None else None,
                 "date":today} for r in rows]
    finally:
        db.close()

@app.get("/api/student/my-attendance")
def student_attendance(request:Request):
    pl = student_only(request)
    db = get_db()
    try:
        out = io.StringIO(); w = csv.writer(out)
        w.writerow(["Date","Present"])
        rows = _fetchall(db, f"SELECT date,present FROM attendance WHERE student_id={p} ORDER BY date",(pl["sub"],))
        for r in rows: w.writerow([row_val(r,"date"),"Yes" if row_val(r,"present") else "No"])
    finally:
        db.close()
    out.seek(0)
    return StreamingResponse(iter([out.getvalue()]),media_type="text/csv",
                             headers={"Content-Disposition":"attachment; filename=my_attendance.csv"})

@app.get("/api/teacher/export/qrcodes")
def t_export_all_qrs(request:Request):
    """Generate a ZIP file containing QR codes for all registered students."""
    teacher_only(request)
    db = get_db()
    try:
        rows = _fetchall(db, "SELECT name, regno FROM students ORDER BY name")
    finally:
        db.close()
    if not rows:
        raise HTTPException(404, "No students found")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for r in rows:
            name  = row_val(r, "name")
            regno = row_val(r, "regno")
            token = make_qr_token(regno)
            img   = qrcode.make(token)
            img_buf = io.BytesIO()
            img.save(img_buf)
            zf.writestr(f"{safe(regno)}_QR.png", img_buf.getvalue())
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition":"attachment; filename=biomark_all_qrcodes.zip"})

@app.get("/api/teacher/export/{type}")
def t_export(type:str,request:Request):
    teacher_only(request)
    db  = get_db(); out = io.StringIO(); w = csv.writer(out)
    try:
        if type=="students":
            w.writerow(["Name","Reg No","Class","Present","Total","Att %"])
            rows=_fetchall(db, "SELECT s.name,s.regno,s.cls,SUM(CASE WHEN a.present=1 THEN 1 ELSE 0 END) AS pres,COUNT(a.id) AS tot,ROUND(100.0*SUM(CASE WHEN a.present=1 THEN 1 ELSE 0 END)/NULLIF(COUNT(a.id),0),1) AS pct FROM students s LEFT JOIN attendance a ON a.student_id=s.id GROUP BY s.id,s.name,s.regno,s.cls ORDER BY s.name")
            for r in rows: w.writerow([row_val(r,"name"),row_val(r,"regno"),row_val(r,"cls"),row_val(r,"pres"),row_val(r,"tot"),row_val(r,"pct")])
        elif type=="marks":
            w.writerow(["Name","Reg No","Subject","Marks","Grade"])
            rows=_fetchall(db, "SELECT s.name,s.regno,m.subject,m.marks,m.grade FROM marks m JOIN students s ON s.id=m.student_id ORDER BY s.name,m.subject")
            for r in rows: w.writerow([row_val(r,"name"),row_val(r,"regno"),row_val(r,"subject"),row_val(r,"marks"),row_val(r,"grade")])
        elif type=="attendance":
            w.writerow(["Name","Reg No","Date","Present"])
            rows=_fetchall(db, "SELECT s.name,s.regno,a.date,a.present FROM attendance a JOIN students s ON s.id=a.student_id ORDER BY s.name,a.date")
            for r in rows: w.writerow([row_val(r,"name"),row_val(r,"regno"),row_val(r,"date"),"Yes" if row_val(r,"present") else "No"])
        else:
            raise HTTPException(400,"Unknown type")
    finally:
        db.close()
    out.seek(0)
    return StreamingResponse(iter([out.getvalue()]),media_type="text/csv",
                             headers={"Content-Disposition":f"attachment; filename=biomark_{type}.csv"})

# ══════════════════════════════════════════════════════════
#  QR CODE (moved here so teacher dashboard works online)
# ══════════════════════════════════════════════════════════
def make_qr_token(regno:str) -> str:
    """Create a long-lived JWT that encodes the student's regno for QR login."""
    return jwt.encode(
        {"type":"qr_login","regno":regno.upper(),
         "exp":datetime.now(timezone.utc)+timedelta(days=365)},
        SECRET, ALGO)

@app.get("/api/qr")
def get_qr(name:str, regno:str):
    filename = f"{safe(regno)}_QR.png"
    path     = os.path.join(QR_DIR, filename)
    # Encode a signed login token instead of plain text
    qr_token = make_qr_token(regno)
    qrcode.make(qr_token).save(path)
    return FileResponse(path, media_type="image/png",
                        headers={"Content-Disposition":f"attachment; filename={filename}"})

# ══════════════════════════════════════════════════════════
#  REMOTE FACE ATTENDANCE API (called by local face.py)
# ══════════════════════════════════════════════════════════
class FaceAttReq(BaseModel):
    regno: str
    api_key: str

@app.post("/api/face/mark-attendance")
def face_mark_attendance(r: FaceAttReq):
    """Called by the local face recognition script to mark attendance remotely."""
    if r.api_key != FACE_API_KEY:
        raise HTTPException(403, "Invalid API key")

    today = datetime.now().strftime("%Y-%m-%d")
    db = get_db()
    try:
        student = _fetchone(db, f"SELECT id,name FROM students WHERE regno={p}", (r.regno.upper(),))
        if not student:
            raise HTTPException(404, "Student not found")

        sid = row_val(student, "id")
        _execute(db,
            f"INSERT INTO attendance (student_id,date,present) VALUES ({p},{p},1) "
            f"ON CONFLICT(student_id,date) DO UPDATE SET present=1",
            (sid, today))
        db.commit()
        return {"success": True, "name": row_val(student, "name"), "regno": r.regno.upper(), "date": today}
    finally:
        db.close()

# ══════════════════════════════════════════════════════════
#  REMOTE STUDENT REGISTRATION API (called by local face_app.py)
# ══════════════════════════════════════════════════════════
class RemoteRegReq(BaseModel):
    name: str; regno: str; cls: str; password: str; api_key: str

@app.post("/api/face/register-student")
def face_register_student(r: RemoteRegReq):
    """Called by the local face registration to create account on remote DB."""
    if r.api_key != FACE_API_KEY:
        raise HTTPException(403, "Invalid API key")
    if not r.name or not r.regno or not r.password:
        raise HTTPException(400, "All fields required")
    if len(r.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    db = get_db()
    try:
        if _fetchone(db, f"SELECT id FROM students WHERE regno={p}", (r.regno.upper(),)):
            raise HTTPException(400, "Register number already exists")
        _execute(db, f"INSERT INTO students (name,regno,cls,password_hash) VALUES ({p},{p},{p},{p})",
                 (r.name, r.regno.upper(), r.cls, hash_password(r.password)))
        db.commit()
        return {"success": True, "message": f"Account created for {r.name}"}
    finally:
        db.close()
