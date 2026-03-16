# UG Drive — Complete Update & Deploy Guide

This guide covers:
A) Setting up Supabase (new — needed for persistent storage)
B) Updating your GitHub repo with the new code
C) Updating Render.com environment variables
D) Optional: connecting Railway as a backup

---

## PART A — Set up Supabase (free, one time)

Supabase stores your UG Drive account and Google tokens so they survive server restarts.

### A1. Create Supabase account
1. Go to supabase.com → click "Start your project" → sign up free (GitHub login works)
2. Click "New project"
3. Organization: your name
4. Project name: `ugdrive`
5. Database password: create a strong password and save it somewhere
6. Region: pick closest to you
7. Click "Create new project" → wait about 60 seconds

### A2. Create the database tables
1. In your Supabase project → click "SQL Editor" in the left menu
2. Click "New query"
3. Open the file `supabase_schema.sql` from the zip you downloaded
4. Copy all the text inside it
5. Paste it into the SQL editor
6. Click "Run" (or press Ctrl+Enter)
7. You should see "Success. No rows returned"

### A3. Get your Supabase keys
1. In Supabase → left menu → click "Project Settings" (gear icon)
2. Click "API"
3. Copy "Project URL" — save it (looks like: https://xxxxxxxxxxxx.supabase.co)
4. Under "Project API keys" → copy the `service_role` key (NOT the anon key)
   - Click "Reveal" to see it
   - This is your SUPABASE_KEY — treat it like a password

---

## PART B — Update your GitHub repo

You need to replace all the old code files with the new ones.

### B1. Update each file in GitHub
For each file in the zip, do the following:

1. Go to your GitHub repo (github.com/YOUR-USERNAME/drivepool or ugdrive)
2. Click the file you want to update
3. Click the pencil ✏️ edit button (top right of the file view)
4. Select all the existing text (Ctrl+A) and delete it
5. Paste in the new file contents
6. Scroll down → click "Commit changes"
7. Repeat for each file

### Files to update or create:

**UPDATE these existing files:**
- `main.py` — replace entirely
- `startup.py` — replace entirely
- `requirements.txt` — replace entirely
- `render.yaml` — replace entirely

**CREATE these new files** (click "Add file" → "Create new file" in GitHub):
- `supabase_schema.sql` — paste the SQL schema (you already ran this, but keep it for reference)
- `frontend/login.html` — paste the new login page
- `frontend/index.html` — paste the new landing page
- `frontend/dashboard.html` — paste the new dashboard

### B2. Rename your repo (optional)
If you want to rename from "drivepool" to "ugdrive":
- GitHub repo → Settings → Repository name → change to `ugdrive` → Rename

---

## PART C — Update Render.com

### C1. Add new environment variables
Go to render.com → your service → Environment tab → add these new variables:

| Key | Value | Notes |
|---|---|---|
| SUPABASE_URL | https://xxxx.supabase.co | From Supabase Project Settings → API |
| SUPABASE_KEY | eyJhbGc... (long string) | service_role key from Supabase |
| JWT_SECRET | any random 32+ character string | Example: `MySecretKey2026!DrivePool#Random` |

Variables you already have (keep them, just verify):
| Key | Value |
|---|---|
| BASE_URL | https://your-app.onrender.com |
| GOOGLE_SECRETS_JSON | (your Google credentials JSON) |
| PORT | 10000 |

### C2. Trigger a redeploy
After saving env vars:
- Render dashboard → your service → click "Manual Deploy" → "Deploy latest commit"
- Wait 2-3 minutes
- Check the build log for any errors

### C3. Update your app name (optional)
If you want the URL to say "ugdrive" instead of "drivepool":
- Render dashboard → your service → Settings → Name → change to `ugdrive`
- This changes your URL to: https://ugdrive.onrender.com
- Update BASE_URL env var to the new URL
- Update Google Cloud Console redirect URI to: https://ugdrive.onrender.com/auth/google/callback

---

## PART D — Update Google Cloud Console redirect URI

The auth callback URL changed from `/auth/callback` to `/auth/google/callback`.

1. Go to console.cloud.google.com → APIs & Services → Credentials
2. Click your OAuth client
3. Under Authorized redirect URIs:
   - DELETE: https://your-old-url.onrender.com/auth/callback
   - ADD: https://your-url.onrender.com/auth/google/callback
4. Click Save

---

## PART E — Set up UptimeRobot (keep server awake)

1. Go to uptimerobot.com → sign up free
2. + Add New Monitor
3. Monitor type: HTTP(s)
4. URL: https://your-url.onrender.com/api/ping
5. Interval: 5 minutes
6. Create Monitor

This pings your server every 5 minutes so Render never sleeps.

---

## PART F — First login after update

1. Visit your Render URL
2. Wait for wake-up (~30 seconds on first visit)
3. You'll see the new UG Drive landing page
4. Click "Get Started" → Create a new account with email + password
5. You'll land on the dashboard
6. Click "+ Connect Drive" → connect your Google accounts
7. Click 🔄 Sync to load your files

**Important:** Your old DrivePool data (connected accounts) won't carry over.
You need to reconnect your Google accounts once. After that they're stored in
Supabase and will persist permanently — no more losing connections on redeploy.

---

## PART G — Optional: Connect Railway as backup/test

1. Go to railway.app → sign up with GitHub (no credit card)
2. New Project → Deploy from GitHub → select your ugdrive repo
3. Add the same environment variables as Render (all of them)
4. Add one more: `RAILWAY_STATIC_URL` → leave empty (Railway sets it)
5. Update BASE_URL to your Railway URL
6. Add Railway URL to Google Console authorized redirect URIs

You can run both Render and Railway at the same time for testing — they both connect
to the same Supabase database, so accounts and files stay in sync.

---

## Troubleshooting

**"Cannot import supabase" error on Render**
→ Make sure requirements.txt has `supabase==2.4.2` and redeploy

**"SUPABASE_URL not configured" error**
→ Check your Render env vars — SUPABASE_URL must be set

**Google accounts not showing after login**
→ You need to reconnect them once. They now persist in Supabase after reconnecting.

**"redirect_uri_mismatch" from Google**
→ Update Google Console to use `/auth/google/callback` (not `/auth/callback`)

**Build fails with bcrypt error on Python 3.14**
→ Add `bcrypt==4.1.3` explicitly to requirements.txt (already included)
