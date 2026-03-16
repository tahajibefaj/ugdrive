"""
UG Drive — Unlimited Google Drive
Persistent tokens via Supabase · JWT auth · FastAPI
"""
import os, pickle, io, base64, secrets, time, smtplib
from secrets import token_urlsafe
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import bcrypt
import jwt as PyJWT
from fastapi import FastAPI, Request, HTTPException, UploadFile, File as FFile, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client, Client

from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# ── Config ─────────────────────────────────────────────────────────────────────
PORT        = int(os.getenv("PORT", 8000))
BASE_URL    = os.getenv("BASE_URL", f"http://localhost:{PORT}")
JWT_SECRET  = os.getenv("JWT_SECRET", secrets.token_hex(32))
JWT_DAYS    = 30
COOKIE_NAME = "ugdrive_token"
SECRETS_FILE = Path("client_secrets.json")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")  # use service_role key

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
]

# ── Supabase ───────────────────────────────────────────────────────────────────
def get_sb() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(503, "Supabase not configured. Set SUPABASE_URL and SUPABASE_KEY.")
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# ── JWT helpers ────────────────────────────────────────────────────────────────
def make_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(days=JWT_DAYS),
        "iat": datetime.now(timezone.utc),
    }
    return PyJWT.encode(payload, JWT_SECRET, algorithm="HS256")

def decode_token(token: str) -> Optional[dict]:
    try:
        return PyJWT.decode(token, JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return None

def get_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        return None
    return decode_token(token)

def require_user(request: Request) -> dict:
    user = get_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user

# ── Password helpers ───────────────────────────────────────────────────────────
def hash_pw(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def check_pw(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False

def generate_reset_code() -> str:
    return f"{int.from_bytes(os.urandom(3), 'big') % 1_000_000:06d}"

def send_reset_code_email(to_email: str, code: str):
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    smtp_from = os.getenv("SMTP_FROM", smtp_user or "no-reply@ugdrive.local")

    if not smtp_host:
        print(f"[UGDRIVE][RESET_CODE][DEV_FALLBACK] {to_email} code={code}")
        return

    msg = EmailMessage()
    msg["Subject"] = "UG Drive password reset code"
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg.set_content(
        f"Your UG Drive verification code is: {code}\n\n"
        "This code expires in 10 minutes."
    )

    try:
        print(f"[UGDRIVE][SMTP] Connecting to SMTP server... host={smtp_host} port={smtp_port}")
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as smtp:
            print("[UGDRIVE][SMTP] Starting TLS...")
            smtp.starttls()
            print("[UGDRIVE][SMTP] TLS started successfully")
            if smtp_user:
                print(f"[UGDRIVE][SMTP] Logging into SMTP... user={smtp_user}")
                smtp.login(smtp_user, smtp_pass)
                print("[UGDRIVE][SMTP] SMTP login successful")
            else:
                print("[UGDRIVE][SMTP] SMTP_USER is empty, skipping login")
            print(f"[UGDRIVE][SMTP] Sending reset email... to={to_email}")
            smtp.send_message(msg)
            print(f"[UGDRIVE][SMTP] Reset email sent successfully to={to_email}")
    except Exception as e:
        print(f"[UGDRIVE][SMTP] SMTP ERROR: {e}")
        raise

def store_reset_code(sb: Client, user_id: str, code: str):
    token = f"CODE:{code}:{token_urlsafe(8)}"
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    sb.table("reset_tokens").insert({
        "user_id": user_id,
        "token": token,
        "expires_at": expires.isoformat(),
        "used": False,
    }).execute()

# ── Google helpers ─────────────────────────────────────────────────────────────
def load_creds(token_b64: str) -> Credentials:
    return pickle.loads(base64.b64decode(token_b64))

def save_creds(creds: Credentials) -> str:
    return base64.b64encode(pickle.dumps(creds)).decode()

def get_drive_svc(account_id: int, user_id: str):
    sb = get_sb()
    r = sb.table("google_accounts").select("*").eq("id", account_id).eq("user_id", user_id).execute()
    if not r.data:
        raise HTTPException(404, "Google account not found")
    acc = r.data[0]
    creds = load_creds(acc["token_b64"])
    if creds.expired and creds.refresh_token:
        creds.refresh(GRequest())
        sb.table("google_accounts").update({"token_b64": save_creds(creds)}).eq("id", account_id).execute()
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def make_flow(state=None):
    flow = Flow.from_client_secrets_file(
        str(SECRETS_FILE), scopes=SCOPES,
        redirect_uri=f"{BASE_URL}/auth/google/callback"
    )
    if state:
        flow.state = state
    return flow

def human(b: int) -> str:
    if not b:
        return "0 B"
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if abs(b) < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"

def pct(used, total):
    return round(used / total * 100, 1) if total else 0

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="UG Drive")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_active_transfers: set = set()

def html(name: str) -> str:
    return open(f"frontend/{name}").read()

# ── Public pages ───────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return html("index.html")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_user(request):
        return RedirectResponse("/dashboard")
    return html("login.html")

@app.get("/api/auth/check")
async def auth_check(request: Request):
    user = get_user(request)
    if user:
        sb = get_sb()
        r = sb.table("users").select("id,name,email").eq("id", user["sub"]).execute()
        if r.data:
            return {"logged_in": True, "user": r.data[0]}
    return {"logged_in": False}

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not get_user(request):
        return RedirectResponse("/login")
    return html("dashboard.html")


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    if not get_user(request):
        return RedirectResponse("/login")
    return html("profile.html")

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if not get_user(request):
        return RedirectResponse("/login")
    return html("settings.html")

@app.get("/api/ping")
async def ping():
    return {"ok": True, "ts": int(time.time())}

# ── Auth API ───────────────────────────────────────────────────────────────────
@app.post("/api/auth/register")
async def register(request: Request):
    body = await request.json()
    email    = (body.get("email") or "").lower().strip()
    password = body.get("password") or ""
    name     = (body.get("name") or "").strip()

    if not email or "@" not in email:
        raise HTTPException(400, "Valid email required")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    sb = get_sb()
    existing = sb.table("users").select("id").eq("email", email).execute()
    if existing.data:
        raise HTTPException(400, "An account with this email already exists")

    result = sb.table("users").insert({
        "email": email,
        "password_hash": hash_pw(password),
        "name": name or email.split("@")[0],
    }).execute()
    user = result.data[0]
    token = make_token(user["id"], email)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax",
                    max_age=60 * 60 * 24 * JWT_DAYS, secure=BASE_URL.startswith("https"))
    return resp

@app.post("/api/auth/login")
async def login(request: Request):
    body = await request.json()
    email    = (body.get("email") or "").lower().strip()
    password = body.get("password") or ""

    sb = get_sb()
    r = sb.table("users").select("*").eq("email", email).execute()
    if not r.data or not check_pw(password, r.data[0]["password_hash"]):
        raise HTTPException(401, "Incorrect email or password")

    user  = r.data[0]
    token = make_token(user["id"], email)
    resp  = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax",
                    max_age=60 * 60 * 24 * JWT_DAYS, secure=BASE_URL.startswith("https"))
    return resp

@app.post("/api/auth/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(COOKIE_NAME)
    return resp

@app.get("/api/auth/me")
async def me(request: Request):
    user = require_user(request)
    sb = get_sb()
    r = sb.table("users").select("id,email,name,avatar,created_at").eq("id", user["sub"]).execute()
    if not r.data:
        raise HTTPException(404, "User not found")
    return r.data[0]

# ── Google OAuth ───────────────────────────────────────────────────────────────
@app.get("/auth/google/start")
async def google_start(request: Request):
    require_user(request)
    if not SECRETS_FILE.exists():
        raise HTTPException(503, "client_secrets.json not found")
    flow = make_flow()
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    # store state temporarily in a cookie
    resp = RedirectResponse(url)
    resp.set_cookie("oauth_state", state, httponly=True, samesite="lax", max_age=600)
    return resp

@app.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    if error:
        msg = "not_test_user" if error == "access_denied" else error
        return RedirectResponse(f"/dashboard?oauth_error={msg}")
    if not code or not state:
        return RedirectResponse("/dashboard?oauth_error=missing_params")
    user = get_user(request)
    if not user:
        return RedirectResponse("/login")
    flow = make_flow(state=state)
    flow.fetch_token(code=code)
    creds = flow.credentials

    # get google user info
    info  = build("oauth2", "v2", credentials=creds, cache_discovery=False).userinfo().get().execute()
    g_email  = info["email"]
    g_name   = info.get("name", g_email)
    g_avatar = info.get("picture", "")

    # get drive quota
    about = build("drive", "v3", credentials=creds, cache_discovery=False).about().get(
        fields="storageQuota").execute().get("storageQuota", {})
    total = int(about.get("limit", 0))
    used  = int(about.get("usage",  0))

    sb = get_sb()
    sb.table("google_accounts").upsert({
        "user_id":    user["sub"],
        "email":      g_email,
        "name":       g_name,
        "avatar":     g_avatar,
        "token_b64":  save_creds(creds),
        "total_bytes": total,
        "used_bytes":  used,
        "synced_at":  datetime.utcnow().isoformat(),
    }, on_conflict="user_id,email").execute()

    resp = RedirectResponse("/dashboard")
    resp.delete_cookie("oauth_state")
    return resp

# ── Google Accounts API ────────────────────────────────────────────────────────
@app.get("/api/accounts")
async def list_accounts(request: Request):
    user = require_user(request)
    sb   = get_sb()
    r = sb.table("google_accounts").select(
        "id,email,name,avatar,total_bytes,used_bytes,synced_at"
    ).eq("user_id", user["sub"]).execute()
    return r.data or []

@app.delete("/api/accounts/{aid}")
async def remove_account(aid: int, request: Request):
    user = require_user(request)
    sb   = get_sb()
    # verify ownership
    r = sb.table("google_accounts").select("id").eq("id", aid).eq("user_id", user["sub"]).execute()
    if not r.data:
        raise HTTPException(404, "Account not found")
    sb.table("google_accounts").delete().eq("id", aid).execute()
    return {"ok": True}

# ── Stats ──────────────────────────────────────────────────────────────────────
@app.get("/api/stats")
async def stats(request: Request):
    user = require_user(request)
    sb   = get_sb()
    accs = sb.table("google_accounts").select(
        "id,email,name,avatar,total_bytes,used_bytes"
    ).eq("user_id", user["sub"]).execute().data or []

    total = sum(a["total_bytes"] for a in accs)
    used  = sum(a["used_bytes"]  for a in accs)

    # file counts from Supabase
    fc  = sb.table("file_cache").select("gid", count="exact").eq("user_id", user["sub"]).eq("trashed", False).neq("mime","application/vnd.google-apps.folder").execute()
    fol = sb.table("file_cache").select("gid", count="exact").eq("user_id", user["sub"]).eq("trashed", False).eq("mime","application/vnd.google-apps.folder").execute()
    tr  = sb.table("file_cache").select("gid", count="exact").eq("user_id", user["sub"]).eq("trashed", True).execute()

    return {
        "accounts":     len(accs),
        "total_bytes":  total,  "total_human":  human(total),
        "used_bytes":   used,   "used_human":   human(used),
        "free_bytes":   total - used, "free_human": human(total - used),
        "pct_used":     pct(used, total),
        "files":        fc.count  or 0,
        "folders":      fol.count or 0,
        "trashed":      tr.count  or 0,
        "account_list": accs,
    }

# ── Sync (pull file list from Drive into Supabase) ─────────────────────────────
@app.post("/api/sync")
async def sync_all(request: Request):
    user = require_user(request)
    sb   = get_sb()
    accs = sb.table("google_accounts").select("*").eq("user_id", user["sub"]).execute().data or []
    summary = []
    for acc in accs:
        try:
            creds = load_creds(acc["token_b64"])
            if creds.expired and creds.refresh_token:
                creds.refresh(GRequest())
                sb.table("google_accounts").update({"token_b64": save_creds(creds)}).eq("id", acc["id"]).execute()
            drv = build("drive", "v3", credentials=creds, cache_discovery=False)

            # refresh quota
            about = drv.about().get(fields="storageQuota").execute().get("storageQuota", {})
            total = int(about.get("limit", 0)); u = int(about.get("usage", 0))
            sb.table("google_accounts").update({
                "total_bytes": total, "used_bytes": u,
                "synced_at": datetime.utcnow().isoformat()
            }).eq("id", acc["id"]).execute()

            # delete old cache for this account
            sb.table("file_cache").delete().eq("account_id", acc["id"]).execute()

            token_page, n, batch = None, 0, []
            while True:
                res = drv.files().list(
                    pageSize=1000, pageToken=token_page,
                    fields="nextPageToken,files(id,name,mimeType,size,parents,createdTime,modifiedTime,trashed,webViewLink)",
                ).execute()
                for f in res.get("files", []):
                    parent = (f.get("parents") or [None])[0]
                    batch.append({
                        "gid":         f["id"],
                        "account_id":  acc["id"],
                        "user_id":     user["sub"],
                        "name":        f.get("name", ""),
                        "mime":        f.get("mimeType", ""),
                        "size":        int(f.get("size", 0)),
                        "parent_gid":  parent,
                        "created_at":  f.get("createdTime"),
                        "modified_at": f.get("modifiedTime"),
                        "trashed":     bool(f.get("trashed")),
                        "view_link":   f.get("webViewLink", ""),
                    })
                    n += 1
                    if len(batch) >= 200:
                        sb.table("file_cache").upsert(batch, on_conflict="gid,account_id").execute()
                        batch = []
                token_page = res.get("nextPageToken")
                if not token_page:
                    break
            if batch:
                sb.table("file_cache").upsert(batch, on_conflict="gid,account_id").execute()
            summary.append({"email": acc["email"], "files": n, "ok": True})
        except Exception as e:
            summary.append({"email": acc["email"], "error": str(e), "ok": False})
    return {"synced": summary}

# ── Files API ──────────────────────────────────────────────────────────────────
@app.get("/api/files")
async def list_files(
    request: Request,
    account_id: Optional[int] = None,
    trashed: bool = False,
    q: Optional[str] = None,
    parent_gid: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
):
    user = require_user(request)
    sb   = get_sb()
    query = sb.table("file_cache").select(
        "gid,account_id,name,mime,size,parent_gid,created_at,modified_at,trashed,view_link,"
        "google_accounts(email,name,avatar)"
    ).eq("user_id", user["sub"]).eq("trashed", trashed)

    if account_id:
        query = query.eq("account_id", account_id)
    if parent_gid:
        query = query.eq("parent_gid", parent_gid)
    if q:
        query = query.ilike("name", f"%{q}%")

    query = query.order("mime", desc=True).order("name").range(offset, offset + limit - 1)
    r = query.execute()

    # flatten nested google_accounts
    rows = []
    for row in (r.data or []):
        ga = row.pop("google_accounts", {}) or {}
        row["email"]     = ga.get("email", "")
        row["acct_name"] = ga.get("name", "")
        row["avatar"]    = ga.get("avatar", "")
        rows.append(row)
    return rows

@app.post("/api/upload")
async def upload(
    request: Request,
    file: UploadFile = FFile(...),
    account_id: Optional[int] = Form(None),
    transfer_id: Optional[str] = Form(None),
):
    user = require_user(request)
    if transfer_id:
        _active_transfers.add(transfer_id)
    try:
        sb = get_sb()
        if account_id:
            r = sb.table("google_accounts").select("*").eq("id", account_id).eq("user_id", user["sub"]).execute()
        else:
            r = sb.table("google_accounts").select("*").eq("user_id", user["sub"]).execute()

        accs = r.data or []
        if not accs:
            raise HTTPException(400, "No Google accounts connected")
        best = max(accs, key=lambda a: a["total_bytes"] - a["used_bytes"])

        content = await file.read()
        meta    = {"name": file.filename}
        creds   = load_creds(best["token_b64"])
        drv     = build("drive", "v3", credentials=creds, cache_discovery=False)
        media   = MediaIoBaseUpload(io.BytesIO(content),
                                    mimetype=file.content_type or "application/octet-stream",
                                    resumable=len(content) > 5_000_000)
        result  = drv.files().create(body=meta, media_body=media,
                                     fields="id,name,mimeType,size,createdTime,modifiedTime,webViewLink"
                                     ).execute()
        # cache
        sb.table("file_cache").upsert({
            "gid":         result["id"],
            "account_id":  best["id"],
            "user_id":     user["sub"],
            "name":        result.get("name", ""),
            "mime":        result.get("mimeType", ""),
            "size":        int(result.get("size", 0)),
            "created_at":  result.get("createdTime"),
            "modified_at": result.get("modifiedTime"),
            "trashed":     False,
            "view_link":   result.get("webViewLink", ""),
        }, on_conflict="gid,account_id").execute()
        sb.table("google_accounts").update({
            "used_bytes": best["used_bytes"] + int(result.get("size", 0))
        }).eq("id", best["id"]).execute()

        return {**result, "routed_to": best["email"]}
    finally:
        if transfer_id:
            _active_transfers.discard(transfer_id)

@app.get("/api/files/{gid}/thumbnail")
async def get_thumbnail(gid: str, request: Request):
    """Stream image file content for preview panel thumbnails."""
    user = require_user(request)
    sb   = get_sb()
    r    = sb.table("file_cache").select("*").eq("gid", gid).eq("user_id", user["sub"]).limit(1).execute()
    if not r.data:
        raise HTTPException(404, "File not found")
    row = r.data[0]
    if not (row.get("mime", "").startswith("image/")):
        raise HTTPException(400, "Not an image file")
    buf = io.BytesIO()
    drv = get_drive_svc(row["account_id"], user["sub"])
    dl  = MediaIoBaseDownload(buf, drv.files().get_media(fileId=gid), chunksize=5*1024*1024)
    done = False
    while not done:
        _, done = dl.next_chunk()
    buf.seek(0)
    return StreamingResponse(buf, media_type=row.get("mime", "image/jpeg"),
                             headers={"Cache-Control": "private, max-age=3600"})

@app.get("/api/files/{gid}/download")
async def download_file(gid: str, request: Request, transfer_id: Optional[str] = None):
    user = require_user(request)
    if transfer_id:
        _active_transfers.add(transfer_id)
    sb = get_sb()
    r  = sb.table("file_cache").select("*").eq("gid", gid).eq("user_id", user["sub"]).limit(1).execute()
    if not r.data:
        raise HTTPException(404, "File not found")
    row = r.data[0]
    try:
        buf = io.BytesIO()
        drv = get_drive_svc(row["account_id"], user["sub"])
        dl  = MediaIoBaseDownload(buf, drv.files().get_media(fileId=gid), chunksize=10*1024*1024)
        done = False
        while not done:
            _, done = dl.next_chunk()
        buf.seek(0)
        return StreamingResponse(buf,
            media_type=row["mime"] or "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{row["name"]}"'})
    finally:
        if transfer_id:
            _active_transfers.discard(transfer_id)

@app.delete("/api/files/{gid}")
async def delete_file(gid: str, request: Request, permanent: bool = False):
    user = require_user(request)
    sb   = get_sb()
    r    = sb.table("file_cache").select("*").eq("gid", gid).eq("user_id", user["sub"]).limit(1).execute()
    if not r.data:
        raise HTTPException(404, "File not found")
    row = r.data[0]
    drv = get_drive_svc(row["account_id"], user["sub"])
    if permanent:
        drv.files().delete(fileId=gid).execute()
        sb.table("file_cache").delete().eq("gid", gid).eq("user_id", user["sub"]).execute()
    else:
        drv.files().update(fileId=gid, body={"trashed": True}).execute()
        sb.table("file_cache").update({"trashed": True}).eq("gid", gid).eq("user_id", user["sub"]).execute()
    return {"ok": True}

@app.post("/api/files/{gid}/restore")
async def restore_file(gid: str, request: Request):
    user = require_user(request)
    sb   = get_sb()
    r    = sb.table("file_cache").select("*").eq("gid", gid).eq("user_id", user["sub"]).limit(1).execute()
    if not r.data:
        raise HTTPException(404, "File not found")
    row = r.data[0]
    get_drive_svc(row["account_id"], user["sub"]).files().update(fileId=gid, body={"trashed": False}).execute()
    sb.table("file_cache").update({"trashed": False}).eq("gid", gid).eq("user_id", user["sub"]).execute()
    return {"ok": True}

@app.patch("/api/files/{gid}")
async def rename_file(gid: str, request: Request):
    user = require_user(request)
    body = await request.json()
    new_name = (body.get("name") or "").strip()
    if not new_name:
        raise HTTPException(400, "Name required")
    sb = get_sb()
    r = sb.table("file_cache").select("*").eq("gid", gid).eq("user_id", user["sub"]).limit(1).execute()
    if not r.data:
        raise HTTPException(404, "File not found")
    row = r.data[0]
    drv = get_drive_svc(row["account_id"], user["sub"])
    drv.files().update(fileId=gid, body={"name": new_name}).execute()
    sb.table("file_cache").update({"name": new_name}).eq("gid", gid).eq("user_id", user["sub"]).execute()
    return {"ok": True, "name": new_name}

@app.post("/api/files/{gid}/move")
async def move_file(gid: str, request: Request):
    user = require_user(request)
    body = await request.json()
    new_parent_gid = body.get("parent_gid")  # folder gid or null for root
    sb = get_sb()
    r = sb.table("file_cache").select("*").eq("gid", gid).eq("user_id", user["sub"]).limit(1).execute()
    if not r.data:
        raise HTTPException(404, "File not found")
    row = r.data[0]
    drv = get_drive_svc(row["account_id"], user["sub"])
    file_meta = drv.files().get(fileId=gid, fields="parents").execute()
    current_parents = ",".join(file_meta.get("parents") or [])
    add_parent = new_parent_gid if new_parent_gid else "root"
    drv.files().update(
        fileId=gid, body={},
        removeParents=current_parents,
        addParents=add_parent,
        fields="id"
    ).execute()
    sb.table("file_cache").update({"parent_gid": new_parent_gid}).eq("gid", gid).eq("user_id", user["sub"]).execute()
    return {"ok": True}

@app.post("/api/mkdir")
async def mkdir(request: Request, name: str = Form(...), account_id: int = Form(...)):
    user = require_user(request)
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    drv  = get_drive_svc(account_id, user["sub"])
    res  = drv.files().create(body=meta, fields="id,name,mimeType,createdTime,modifiedTime").execute()
    sb = get_sb()
    sb.table("file_cache").upsert({
        "gid": res["id"], "account_id": account_id, "user_id": user["sub"],
        "name": name, "mime": "application/vnd.google-apps.folder",
        "size": 0, "trashed": False, "view_link": "",
        "created_at": res.get("createdTime"), "modified_at": res.get("modifiedTime"),
    }, on_conflict="gid,account_id").execute()
    return dict(res)

@app.post("/api/keepalive")
async def keepalive(transfer_id: str = Form(...)):
    _active_transfers.add(transfer_id)
    return {"ok": True}

@app.delete("/api/keepalive/{tid}")
async def end_transfer(tid: str):
    _active_transfers.discard(tid)
    return {"ok": True}

# ── /api/auth/me ───────────────────────────────────────────────────────────────
@app.post("/api/auth/change-password")
async def change_password(request: Request):
    user = require_user(request)
    body = await request.json()
    cur  = body.get("current_password", "")
    new_ = body.get("new_password", "")
    if len(new_) < 8:
        raise HTTPException(400, "New password must be at least 8 characters")
    sb = get_sb()
    r  = sb.table("users").select("*").eq("id", user["sub"]).execute()
    if not r.data:
        raise HTTPException(404, "User not found")
    if not check_pw(cur, r.data[0]["password_hash"]):
        raise HTTPException(401, "Current password is incorrect")
    sb.table("users").update({"password_hash": hash_pw(new_)}).eq("id", user["sub"]).execute()
    return {"ok": True}

# ── Password reset (email + verification code) ────────────────────────────────
@app.post("/auth/request-password-reset")
@app.post("/api/auth/request-password-reset")
@app.post("/api/auth/forgot-password")
async def request_password_reset(request: Request):
    body = await request.json()
    email = (body.get("email") or "").lower().strip()
    if not email:
        raise HTTPException(400, "Email required")

    sb = get_sb()
    r = sb.table("users").select("id,email").eq("email", email).execute()

    # Always return generic success so caller cannot enumerate valid emails.
    if r.data:
        user_id = r.data[0]["id"]
        code = generate_reset_code()
        store_reset_code(sb, user_id, code)
        try:
            send_reset_code_email(email, code)
        except Exception:
            # Keep generic response to avoid leaking account existence.
            pass

    return {
        "ok": True,
        "detail": "If the email exists, a verification code has been sent."
    }

@app.post("/auth/reset-password")
@app.post("/api/auth/reset-password")
async def reset_password(request: Request):
    body = await request.json()
    sb = get_sb()

    # Backward compatibility: old token-link flow
    token = (body.get("token") or "").strip()
    if token:
        password = body.get("password", "")
        if len(password) < 8:
            raise HTTPException(400, "Password must be at least 8 characters")
        r = sb.table("reset_tokens").select("*").eq("token", token).execute()
        if not r.data:
            raise HTTPException(400, "Invalid or expired reset request")
        rec = r.data[0]
        if rec["used"]:
            raise HTTPException(400, "Invalid or expired reset request")
        expires = datetime.fromisoformat(rec["expires_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) > expires:
            raise HTTPException(400, "Invalid or expired reset request")
        sb.table("users").update({"password_hash": hash_pw(password)}).eq("id", rec["user_id"]).execute()
        sb.table("reset_tokens").update({"used": True}).eq("token", token).execute()
        return {"ok": True}

    # New flow: email + code + new_password
    email = (body.get("email") or "").lower().strip()
    code = (body.get("code") or "").strip()
    new_password = body.get("new_password") or body.get("password") or ""

    if not email or not code:
        raise HTTPException(400, "Email and code are required")
    if len(new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    ur = sb.table("users").select("id").eq("email", email).execute()
    if not ur.data:
        raise HTTPException(400, "Invalid or expired reset request")
    user_id = ur.data[0]["id"]

    rr = sb.table("reset_tokens").select("*").eq("user_id", user_id).eq("used", False).execute()
    matched = None
    now = datetime.now(timezone.utc)
    for rec in (rr.data or []):
        tok = rec.get("token", "")
        if not tok.startswith("CODE:"):
            continue
        parts = tok.split(":")
        if len(parts) < 3:
            continue
        rec_code = parts[1]
        if rec_code != code:
            continue
        exp = datetime.fromisoformat(rec["expires_at"].replace("Z", "+00:00"))
        if now > exp:
            continue
        matched = rec
        break

    if not matched:
        raise HTTPException(400, "Invalid or expired reset request")

    sb.table("users").update({"password_hash": hash_pw(new_password)}).eq("id", user_id).execute()
    sb.table("reset_tokens").update({"used": True}).eq("id", matched["id"]).execute()
    return {"ok": True}

# ── Reset password page ────────────────────────────────────────────────────────
@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page():
    return html("reset_password.html")


if __name__ == "__main__":
    import uvicorn
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
