"""Startup: writes GOOGLE_SECRETS_JSON env var to disk, then starts server."""
import os, json, sys
from pathlib import Path

def setup():
    raw = os.getenv("GOOGLE_SECRETS_JSON", "")
    if raw.strip():
        try:
            Path("client_secrets.json").write_text(json.dumps(json.loads(raw)))
            print("✅ client_secrets.json written from env var")
        except Exception as e:
            print(f"❌ Could not parse GOOGLE_SECRETS_JSON: {e}")
            sys.exit(1)
    elif Path("client_secrets.json").exists():
        print("✅ client_secrets.json found on disk")
    else:
        print("⚠️  No client_secrets.json — Google OAuth will fail")

    for var in ["SUPABASE_URL", "SUPABASE_KEY", "BASE_URL", "JWT_SECRET"]:
        if not os.getenv(var):
            print(f"⚠️  {var} is not set")

if __name__ == "__main__":
    setup()
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
