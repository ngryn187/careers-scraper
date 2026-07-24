import asyncio
import html
import re
import ipaddress
import json
import os
import secrets
import socket
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import openai
import psycopg2
import pyotp
try:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
    SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
    if SENTRY_DSN:
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[StarletteIntegration(), FastApiIntegration()],
            traces_sample_rate=0.1,
            profiles_sample_rate=0.0,
            send_default_pii=False,
        )
except Exception:
    pass
import redis as redis_lib
import stripe
import uvicorn
from fastapi import BackgroundTasks, Cookie, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.middleware.gzip import GZipMiddleware
from playwright.async_api import async_playwright

VERSION = "9.7.4"

#  Config 
openai.api_key = os.environ.get("OPENAI_API_KEY", "")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
ZOHO_EMAIL = os.environ.get("ZOHO_EMAIL", "")
ZOHO_PASSWORD = os.environ.get("ZOHO_PASSWORD", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
CRON_SECRET = os.environ.get("CRON_SECRET", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
TOTP_SECRET = os.environ.get("TOTP_SECRET", "")
BASE_URL = os.environ.get("BASE_URL", "https://stacksight.org")

STRIPE_PRICES = {
    "starter": os.environ.get("STRIPE_PRICE_STARTER", ""),
    "pro": os.environ.get("STRIPE_PRICE_PRO", "price_1TrLQ6DUssNU8xAWD0eyqLx4"),
    "business": os.environ.get("STRIPE_PRICE_BUSINESS", "price_1TrLceDUssNU8xAWKWUSPLlR"),
    "pro_annual": os.environ.get("STRIPE_PRICE_PRO_ANNUAL", ""),
    "business_annual": os.environ.get("STRIPE_PRICE_BUSINESS_ANNUAL", ""),
}
PLAN_LIMITS = {"free": 25, "starter": 500, "pro": 5000, "business": 50000}

GA_TAG = '<script async src="https://www.googletagmanager.com/gtag/js?id=G-LKSSZ6SK9E"></script><script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}gtag("js",new Date());gtag("config","G-LKSSZ6SK9E");</script>'

#  Rate limiting 
# in-memory rate limit replaced by Redis sliding window
RATE_LIMIT_WINDOW = 60
RATE_LIMITS = {"free": 10, "starter": 60, "pro": 300, "business": 1000}

#  Redis / App 
redis_client = redis_lib.from_url(REDIS_URL, decode_responses=True)
app = FastAPI(title="StackSight API", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)

@app.middleware("http")
async def security_and_analytics(request: Request, call_next):
    response = await call_next(request)
    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # Inject GA4 into all HTML responses
    ct = response.headers.get("content-type", "")
    if "text/html" in ct:
        body = b""
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else chunk.encode()
        body = body.replace(b"</head>", (GA_TAG + "</head>").encode(), 1)
        headers = dict(response.headers)
        headers.pop("content-length", None)  # remove stale length; will be recalculated
        return Response(content=body, status_code=response.status_code, headers=headers, media_type="text/html")
    return response

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<link rel="icon" type="image/png" href="/favicon.png">
<title>404 - Page Not Found | StackSight</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:20px}
.code{font-size:120px;font-weight:800;background:linear-gradient(135deg,#7c3aed,#a855f7);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;line-height:1;margin-bottom:8px}
h1{font-size:24px;font-weight:600;color:#fff;margin-bottom:12px}
p{color:#888;font-size:15px;margin-bottom:36px;max-width:400px}
.btn{display:inline-block;background:#a855f7;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:600;font-size:15px;transition:background .2s;margin:6px}
.btn:hover{background:#9333ea}
.btn-sec{background:transparent;border:1px solid #333;color:#ccc}
.btn-sec:hover{background:#1a1a1a;border-color:#555}
nav{position:fixed;top:0;left:0;right:0;padding:18px 40px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1a1a1a;background:rgba(10,10,10,0.95)}
.logo{font-size:20px;font-weight:700;color:#a855f7;text-decoration:none}
</style>
</head>
<body>
<nav><a href="/" class="logo">StackSight</a></nav>
<div class="code">404</div>
<h1>Page not found</h1>
<p>The page you&rsquo;re looking for doesn&rsquo;t exist or has been moved.</p>
<div>
  <a href="/" class="btn">Go Home</a>
  <a href="/docs" class="btn btn-sec">API Docs</a>
</div>
</body>
</html>""", status_code=404)

#  Demo data
DEMO_DATA = {
    "stripe.com": {"company_name": "Stripe", "is_hiring": True, "engineering_roles": ["Backend Engineer", "ML Engineer", "Platform Engineer"], "sales_roles": ["Account Executive", "Solutions Engineer"], "detected_tech_stack": ["React", "AWS", "Stripe", "Cloudflare", "Sentry"]},
    "openai.com": {"company_name": "OpenAI", "is_hiring": True, "engineering_roles": ["Research Engineer", "Infrastructure Engineer", "Safety Engineer"], "sales_roles": ["Enterprise Account Executive"], "detected_tech_stack": ["Python", "Kubernetes", "Azure", "React", "PostgreSQL"]},
    "notion.so": {"company_name": "Notion", "is_hiring": True, "engineering_roles": ["Frontend Engineer", "Backend Engineer", "Data Engineer"], "sales_roles": ["Sales Development Rep", "Account Manager"], "detected_tech_stack": ["React", "TypeScript", "AWS", "Electron", "PostgreSQL"]},
    "vercel.com": {"company_name": "Vercel", "is_hiring": True, "engineering_roles": ["Edge Runtime Engineer", "DX Engineer", "Infrastructure Engineer"], "sales_roles": ["Enterprise AE", "Solutions Architect"], "detected_tech_stack": ["Next.js", "React", "AWS", "Cloudflare", "TypeScript"]},
    "figma.com": {"company_name": "Figma", "is_hiring": True, "engineering_roles": ["C++ Engineer", "WebAssembly Engineer", "Platform Engineer"], "sales_roles": ["Account Executive", "Customer Success Manager"], "detected_tech_stack": ["C++", "WebAssembly", "React", "AWS", "TypeScript"]},
    "github.com": {"company_name": "GitHub", "is_hiring": True, "engineering_roles": ["Staff Engineer", "Security Engineer", "DevEx Engineer"], "sales_roles": ["Enterprise Sales", "Partner Manager"], "detected_tech_stack": ["Ruby", "Go", "React", "Azure", "MySQL"]},
    "shopify.com": {"company_name": "Shopify", "is_hiring": True, "engineering_roles": ["Rails Engineer", "Go Engineer", "ML Engineer"], "sales_roles": ["Partner Sales Manager", "Merchant Success"], "detected_tech_stack": ["Ruby on Rails", "Go", "React", "GCP", "Kafka"]},
    "hubspot.com": {"company_name": "HubSpot", "is_hiring": True, "engineering_roles": ["Java Engineer", "Frontend Engineer", "Data Engineer"], "sales_roles": ["Account Executive", "Sales Engineer", "BDR"], "detected_tech_stack": ["Java", "React", "AWS", "MySQL", "Kafka"]},
    "datadog.com": {"company_name": "Datadog", "is_hiring": True, "engineering_roles": ["Go Engineer", "Agent Engineer", "ML Engineer"], "sales_roles": ["Enterprise AE", "Solutions Engineer", "SDR"], "detected_tech_stack": ["Go", "Python", "AWS", "Kubernetes", "Cassandra"]},
}

TECH_INFERENCE_MAP = [
    (["ios", "swift", "xcode", "apple"], ["Swift", "Xcode", "iOS"]),
    (["android", "kotlin"], ["Kotlin", "Android"]),
    (["react native", "mobile engineer"], ["React Native", "TypeScript"]),
    (["react", "frontend", "front-end", "ui engineer", "web engineer"], ["React", "TypeScript", "JavaScript"]),
    (["next.js", "nextjs"], ["Next.js", "React", "TypeScript"]),
    (["vue", "nuxt"], ["Vue.js", "TypeScript"]),
    (["angular"], ["Angular", "TypeScript"]),
    (["node", "node.js"], ["Node.js", "JavaScript"]),
    (["python", "django", "flask", "fastapi"], ["Python"]),
    (["ml engineer", "machine learning", "deep learning", "ai engineer", "llm"], ["Python", "PyTorch", "TensorFlow"]),
    (["data engineer", "data pipeline", "etl"], ["Python", "Spark", "Airflow", "SQL"]),
    (["data scientist", "data analyst"], ["Python", "SQL", "pandas"]),
    (["rails", "ruby"], ["Ruby on Rails", "Ruby"]),
    (["go engineer", "golang", "go developer"], ["Go"]),
    (["java engineer", "java developer", "spring"], ["Java", "Spring"]),
    (["scala", "spark"], ["Scala", "Spark"]),
    (["rust engineer", "rust developer"], ["Rust"]),
    (["c++ engineer", "c++", "cpp"], ["C++"]),
    (["devops", "sre", "site reliability", "infrastructure engineer", "platform engineer"], ["Kubernetes", "Terraform", "AWS"]),
    (["cloud", "aws", "amazon web services"], ["AWS"]),
    (["gcp", "google cloud"], ["GCP"]),
    (["azure", "microsoft cloud"], ["Azure"]),
    (["kubernetes", "k8s"], ["Kubernetes"]),
    (["terraform"], ["Terraform"]),
    (["backend engineer", "backend developer", "server-side"], ["Python", "Go", "AWS"]),
    (["fullstack", "full stack", "full-stack"], ["React", "Node.js", "TypeScript"]),
    (["database", "postgres", "postgresql"], ["PostgreSQL"]),
    (["mysql"], ["MySQL"]),
    (["mongodb", "mongo"], ["MongoDB"]),
    (["redis"], ["Redis"]),
    (["kafka"], ["Kafka"]),
    (["graphql"], ["GraphQL"]),
    (["blockchain", "web3", "solidity", "smart contract"], ["Solidity", "Ethereum", "Web3"]),
]

def infer_tech_from_roles(roles: list) -> list:
    """Infer tech stack from job titles when GPT returns empty array."""
    roles_text = " ".join(roles).lower()
    tech = set()
    for keywords, techs in TECH_INFERENCE_MAP:
        if any(kw in roles_text for kw in keywords):
            tech.update(techs)
    return list(tech)[:10]

EXTRACTION_PROMPT = (
    "You are a B2B data extraction engine. Given raw text from a company careers page, "
    "extract structured data and return ONLY valid JSON with this exact schema: "
    "{\"company_name\": string, "
    "\"is_hiring\": boolean (true if any open roles exist), "
    "\"engineering_roles\": [list all engineering/tech/developer/data job titles found], "
    "\"sales_roles\": [list all sales/marketing/growth/bizdev job titles found], "
    "\"other_roles\": [list all other job titles found - design, ops, finance, legal, HR, etc], "
    "\"detected_tech_stack\": [array of strings]}. "
    "For detected_tech_stack: extract ANY technology mentioned explicitly AND make strong inferences "
    "from job titles and role names. Rules: if you see 'iOS Engineer' infer Swift/Xcode/iOS; "
    "'Android Engineer' infer Kotlin/Android; 'React' or 'Frontend Engineer' infer React/JavaScript/TypeScript; "
    "'Rails' infer Ruby on Rails/Ruby; 'Django' or 'Python Engineer' infer Python/Django; "
    "'Go Engineer' infer Go; 'Java Engineer' infer Java/Spring; 'ML Engineer' or 'AI' infer Python/PyTorch/TensorFlow; "
    "'Data Engineer' infer SQL/Spark/Airflow; 'DevOps' or 'SRE' or 'Infrastructure' infer Kubernetes/Terraform/AWS; "
    "'Fullstack' infer React/Node.js/TypeScript; 'Backend Engineer' infer the most likely server stack based on company context. "
    "Also look for any technology keywords anywhere in the text including in job descriptions, requirements, or about sections. "
    "Return at least 3-8 technologies if ANY engineering roles exist. Never return an empty array if there are engineering jobs."
)

#  Database
import psycopg2.pool as _pg_pool
_db_pool = None

def get_db_pool():
    global _db_pool
    if _db_pool is None:
        _db_pool = _pg_pool.ThreadedConnectionPool(2, 20, DATABASE_URL)
    return _db_pool

def get_db():
    try:
        return get_db_pool().getconn()
    except Exception:
        return psycopg2.connect(DATABASE_URL)

def release_db(conn):
    try:
        get_db_pool().putconn(conn)
    except Exception:
        try: release_db(conn)
        except Exception: pass

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id SERIAL PRIMARY KEY,
            api_key VARCHAR(64) UNIQUE NOT NULL,
            email VARCHAR(255) NOT NULL,
            plan VARCHAR(20) NOT NULL DEFAULT 'free',
            requests_used INTEGER NOT NULL DEFAULT 0,
            requests_limit INTEGER NOT NULL DEFAULT 25,
            stripe_customer_id VARCHAR(100),
            stripe_session_id VARCHAR(100),
            created_at TIMESTAMP DEFAULT NOW(),
            active BOOLEAN DEFAULT TRUE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_signups (
            id SERIAL PRIMARY KEY,
            email VARCHAR(255) UNIQUE NOT NULL,
            token VARCHAR(64) UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            used BOOLEAN DEFAULT FALSE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id SERIAL PRIMARY KEY,
            session_token VARCHAR(64) UNIQUE NOT NULL,
            email VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            expires_at TIMESTAMP NOT NULL,
            active BOOLEAN DEFAULT TRUE
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS magic_links (
            id SERIAL PRIMARY KEY,
            token VARCHAR(64) UNIQUE NOT NULL,
            email VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            expires_at TIMESTAMP NOT NULL,
            used BOOLEAN DEFAULT FALSE
        )
    """)
    #  Migrations (safe to re-run)
    # Fix legacy 'key' column — drop NOT NULL so inserts using api_key column work
    try:
        cur.execute('ALTER TABLE api_keys ALTER COLUMN "key" DROP NOT NULL')
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[MIGRATION] key nullable skipped: {e}")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS api_key VARCHAR(64)")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS email VARCHAR(255)")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS plan VARCHAR(20) DEFAULT 'free'")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS requests_used INTEGER DEFAULT 0")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS requests_limit INTEGER DEFAULT 25")
    cur.execute("UPDATE api_keys SET requests_limit=25 WHERE plan='free' AND requests_limit=10")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(100)")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS stripe_session_id VARCHAR(100)")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS last_used TIMESTAMP")
    cur.execute("ALTER TABLE pending_signups ADD COLUMN IF NOT EXISTS email VARCHAR(255)")
    cur.execute("ALTER TABLE pending_signups ADD COLUMN IF NOT EXISTS token VARCHAR(64)")
    cur.execute("ALTER TABLE pending_signups ADD COLUMN IF NOT EXISTS used BOOLEAN DEFAULT FALSE")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS usage_reset_at TIMESTAMP")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS alert_80_sent BOOLEAN DEFAULT FALSE")
    conn.commit()
    cur.close()
    release_db(conn)

#  Session auth 
def get_session_email(request: Request) -> str | None:
    token = request.cookies.get("ss_session")
    if not token:
        return None
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT email FROM sessions WHERE session_token=%s AND active=TRUE AND expires_at > NOW()",
        (token,)
    )
    row = cur.fetchone()
    cur.close()
    release_db(conn)
    return row[0] if row else None

def require_session(request: Request) -> str:
    email = get_session_email(request)
    if not email:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return email

def create_session(email: str, response: Response) -> str:
    token = secrets.token_urlsafe(48)
    expires = datetime.utcnow() + timedelta(days=7)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sessions (session_token, email, expires_at) VALUES (%s, %s, %s)",
        (token, email, expires)
    )
    conn.commit()
    cur.close()
    release_db(conn)
    response.set_cookie(
        key="ss_session",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/"
    )
    return token

#  Rate limiting 
def check_rate_limit(api_key: str, plan: str = "free"):
    try:
        now = time.time()
        window_start = now - RATE_LIMIT_WINDOW
        limit = RATE_LIMITS.get(plan, 10)
        rkey = f"rl:{api_key}"
        pipe = redis_client.pipeline()
        pipe.zremrangebyscore(rkey, 0, window_start)
        pipe.zcard(rkey)
        pipe.zadd(rkey, {str(now): now})
        pipe.expire(rkey, RATE_LIMIT_WINDOW + 1)
        results = pipe.execute()
        count = results[1]
        if count >= limit:
            raise HTTPException(status_code=429, detail=f"Rate limit exceeded. Max {limit} requests/minute.")
    except HTTPException:
        raise
    except Exception:
        pass  # Redis unavailable — fail open rather than blocking legitimate requests

#  API key auth 
def verify_api_key(x_api_key: str):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT plan, requests_used, requests_limit, active FROM api_keys WHERE api_key=%s OR \"key\"=%s", (x_api_key, x_api_key))
    row = cur.fetchone()
    cur.close()
    release_db(conn)
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    plan, used, limit, active = row
    if not active:
        raise HTTPException(status_code=403, detail="API key deactivated")
    if used >= limit:
        raise HTTPException(status_code=429, detail=f"Request limit reached ({limit} for {plan} plan). Upgrade at stacksight.org")
    check_rate_limit(x_api_key, plan)
    return x_api_key, plan

def increment_usage(api_key: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE api_keys SET requests_used = requests_used + 1 WHERE api_key=%s OR \"key\"=%s RETURNING email, requests_used, requests_limit, alert_80_sent",
        (api_key, api_key)
    )
    row = cur.fetchone()
    conn.commit()
    cur.close()
    release_db(conn)
    if row:
        email, used, limit, alert_sent = row
        if limit and not alert_sent and used >= int(limit * 0.8):
            # Send 80% usage alert and mark it sent
            try:
                conn2 = get_db()
                cur2 = conn2.cursor()
                cur2.execute("UPDATE api_keys SET alert_80_sent=TRUE WHERE api_key=%s", (api_key,))
                conn2.commit()
                cur2.close(); release_db(conn2)
            except Exception:
                pass
            pct = int(used / limit * 100)
            _send_usage_alert(email, used, limit, pct)

def _send_usage_alert(email: str, used: int, limit: int, pct: int):
    subject = f"StackSight: You've used {pct}% of your monthly quota"
    html = f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#0f0f0f;color:#fff;padding:40px;border-radius:12px">
      <h1 style="color:#a855f7;margin-bottom:4px">StackSight</h1>
      <p style="color:#bbb;margin-top:0;margin-bottom:24px">B2B Hiring Intent API</p>
      <h2 style="color:#fff">Usage Alert: {pct}% of quota used</h2>
      <p style="color:#ccc">You've used <strong style="color:#a855f7">{used:,} of {limit:,}</strong> requests this month.</p>
      <p style="color:#ccc">You have <strong>{limit - used:,} requests</strong> remaining. Consider upgrading to avoid hitting your limit.</p>
      <a href="https://stacksight.org/#pricing" style="display:inline-block;background:#a855f7;color:#fff;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:bold;margin:20px 0;font-size:16px">Upgrade Plan</a>
      <p style="color:#888;font-size:12px;margin-top:24px">You're receiving this because you have an active StackSight account.</p>
    </div>"""
    text = f"You've used {pct}% ({used:,} of {limit:,}) of your StackSight quota this month. {limit - used:,} requests remaining. Upgrade at https://stacksight.org/#pricing"
    import threading
    threading.Thread(target=_send_email_sync, args=(email, subject, html, text), daemon=True).start()

#  Email 
def _send_email_sync(to_email: str, subject: str, html_body: str, text_body: str):
    import urllib.request, json as _json
    RESEND_KEY = os.environ.get("RESEND_API_KEY", "")
    payload = _json.dumps({
        "from": "StackSight <noreply@stacksight.org>",
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "text": text_body
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={"Authorization": f"Bearer {RESEND_KEY}", "Content-Type": "application/json", "User-Agent": "python-httpx/0.24.0"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = resp.read()
            print(f"[EMAIL OK] Sent to {to_email}: {result[:100]}")
    except Exception as e:
        print(f"[EMAIL ERROR] Failed to send to {to_email}: {e}")
def send_email(to_email: str, subject: str, html_body: str, text_body: str):
    import threading
    t = threading.Thread(target=_send_email_sync, args=(to_email, subject, html_body, text_body), daemon=True)
    t.start()

def send_magic_link_email(to_email: str, token: str, next_url: str = ""):
    url = f"{BASE_URL}/auth?token={token}" + (f"&next={next_url}" if next_url else "")
    subject = "Your StackSight login link"
    html = f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#0f0f0f;color:#fff;padding:40px;border-radius:12px">
      <h1 style="color:#a855f7;margin-bottom:4px">StackSight</h1>
      <p style="color:#bbb;margin-top:0;margin-bottom:24px">B2B Hiring Intent API</p>
      <h2 style="color:#fff">Sign in to your account</h2>
      <p style="color:#ccc">Click the button below to securely log in. This link expires in 15 minutes and can only be used once.</p>
      <a href="{url}" style="display:inline-block;background:#a855f7;color:#fff;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:bold;margin:20px 0;font-size:16px">Sign In to Dashboard</a>
      <p style="color:#888;font-size:13px">Or paste this link:<br><span style="color:#a855f7">{url}</span></p>
      <p style="color:#888;font-size:12px;margin-top:24px">If you didn't request this, ignore this email.</p>
    </div>"""
    text = f"Sign in to StackSight\n\nClick here: {url}\n\nExpires in 15 minutes, single use only."
    send_email(to_email, subject, html, text)

def send_api_key_email(to_email: str, api_key: str, plan: str):
    limit = PLAN_LIMITS.get(plan, 25)
    subject = "Your StackSight API key is ready 🚀"
    upgrade_section = "" if plan != "free" else f"""
      <div style="background:#1a0a2e;border:1px solid #7c3aed;border-radius:8px;padding:20px;margin-top:24px">
        <p style="margin:0 0 8px;color:#a855f7;font-weight:bold">⚡ Need more requests?</p>
        <p style="margin:0 0 12px;color:#ccc;font-size:14px">Upgrade to Starter for 500 requests/month at just $12/mo.</p>
        <a href="{BASE_URL}/checkout/starter" style="display:inline-block;background:#7c3aed;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:bold;font-size:14px">Upgrade Now →</a>
      </div>"""
    html = f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#0a0a0a;color:#fff;padding:40px;border-radius:12px;border:1px solid #1f1f1f">
      <h1 style="color:#a855f7;margin:0 0 4px">StackSight</h1>
      <p style="color:#666;margin:0 0 32px;font-size:14px">B2B Hiring Intent & Tech Stack API</p>

      <h2 style="color:#fff;margin:0 0 8px">Your API key is ready</h2>
      <p style="color:#aaa;margin:0 0 16px">Plan: <strong style="color:#a855f7">{plan.title()}</strong> &nbsp;·&nbsp; <strong>{limit:,} requests/month</strong></p>

      <div style="background:#111;border:1px solid #333;border-radius:8px;padding:16px 20px;font-family:monospace;font-size:15px;color:#a855f7;word-break:break-all;margin-bottom:32px">{api_key}</div>

      <h3 style="color:#fff;margin:0 0 12px">Make your first call</h3>
      <div style="background:#111;border:1px solid #222;border-radius:8px;padding:16px 20px;font-family:monospace;font-size:13px;color:#ccc;margin-bottom:32px;overflow-x:auto">
        <span style="color:#666">curl</span> "https://stacksight.org/scrape?domain=stripe.com" \<br>
        &nbsp;&nbsp;<span style="color:#666">-H</span> "X-API-Key: <span style="color:#a855f7">{api_key}</span>"
      </div>

      <h3 style="color:#fff;margin:0 0 12px">What you get back</h3>
      <div style="background:#111;border:1px solid #222;border-radius:8px;padding:16px 20px;font-family:monospace;font-size:13px;color:#ccc;margin-bottom:32px">
        {{"company_name": "Stripe",<br>
        &nbsp;"is_hiring": true,<br>
        &nbsp;"engineering_roles": ["Backend Engineer", "ML Engineer"],<br>
        &nbsp;"sales_roles": ["Account Executive", "Solutions Engineer"],<br>
        &nbsp;"detected_tech_stack": ["React", "Go", "AWS", "Kubernetes"]}}
      </div>

      <table style="width:100%;border-collapse:collapse;margin-bottom:32px">
        <tr>
          <td style="padding:8px 0;border-bottom:1px solid #1f1f1f">
            <a href="{BASE_URL}/dashboard" style="color:#a855f7;text-decoration:none">📊 Dashboard</a>
            <span style="color:#888;font-size:13px;margin-left:8px">View usage & manage your key</span>
          </td>
        </tr>
        <tr>
          <td style="padding:8px 0;border-bottom:1px solid #1f1f1f">
            <a href="{BASE_URL}/docs" style="color:#a855f7;text-decoration:none">📖 Docs</a>
            <span style="color:#888;font-size:13px;margin-left:8px">Full API reference & examples</span>
          </td>
        </tr>
        <tr>
          <td style="padding:8px 0">
            <a href="{BASE_URL}/demo/stripe.com" style="color:#a855f7;text-decoration:none">🔍 Try the Demo</a>
            <span style="color:#888;font-size:13px;margin-left:8px">See live results for any domain</span>
          </td>
        </tr>
      </table>
      {upgrade_section}
      <p style="color:#444;font-size:12px;margin-top:32px">You're receiving this because you signed up at stacksight.org. Reply to this email if you need help.</p>
    </div>"""
    text = f"""Welcome to StackSight!

Your API Key: {api_key}
Plan: {plan.title()} | {limit:,} requests/month

Quick start:
curl "https://stacksight.org/scrape?domain=stripe.com" -H "X-API-Key: {api_key}"

Dashboard: {BASE_URL}/dashboard
Docs: {BASE_URL}/docs
Demo: {BASE_URL}/demo/stripe.com

Reply to this email if you need help.
"""
    send_email(to_email, subject, html, text)

def send_verification_email(email: str, token: str):
    verify_url = f"https://stacksight.org/verify-email?token={token}"
    subject = "Verify your email - StackSight"
    html_body = f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#0a0a0a;color:#fff;padding:40px;border-radius:12px;border:1px solid #1f1f1f">
      <h1 style="color:#a855f7;margin:0 0 4px">StackSight</h1>
      <p style="color:#666;margin:0 0 32px;font-size:14px">B2B Hiring Intent & Tech Stack API</p>
      <h2 style="color:#fff">Verify your email</h2>
      <p style="color:#aaa">Click the button below to verify your email and get your free API key. You'll be signed in automatically.</p>
      <a href="{verify_url}" style="display:inline-block;background:#a855f7;color:#fff;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:bold;margin:20px 0;font-size:16px">Verify Email & Get API Key</a>
      <p style="color:#888;font-size:13px">Or paste this link:<br><span style="color:#a855f7">{verify_url}</span></p>
      <p style="color:#444;font-size:12px;margin-top:24px">This link expires in 24 hours. If you didn't sign up, ignore this email.</p>
    </div>"""
    text_body = f"Verify your StackSight email:\n\n{verify_url}\n\nExpires in 24 hours."
    _send_email_sync(email, subject, html_body, text_body)
def provision_api_key(email: str, plan: str, stripe_customer_id: str = None, stripe_session_id: str = None):
    api_key = "ss_" + secrets.token_urlsafe(32)
    limit = PLAN_LIMITS.get(plan, 25)
    conn = get_db()
    cur = conn.cursor()
    if plan != "free":
        # Try to find existing account: first by email, then by stripe_customer_id.
        # This handles the case where a user checks out with a different email in Stripe.
        matched_email = email
        if stripe_customer_id:
            cur.execute("SELECT email FROM api_keys WHERE stripe_customer_id=%s LIMIT 1", (stripe_customer_id,))
            row = cur.fetchone()
            if row:
                matched_email = row[0]
                print(f"[PROVISION] Matched by customer_id {stripe_customer_id}: using email {matched_email} (checkout email was {email})")

        cur.execute("""
            UPDATE api_keys
            SET plan=%s, requests_limit=%s, requests_used=0, usage_reset_at=NOW(), active=TRUE,
                stripe_customer_id=COALESCE(%s, stripe_customer_id),
                stripe_session_id=COALESCE(%s, stripe_session_id)
            WHERE email=%s
        """, (plan, limit, stripe_customer_id, stripe_session_id, matched_email))
        if cur.rowcount > 0:
            cur.execute("SELECT COALESCE(api_key, \"key\"), stripe_session_id FROM api_keys WHERE email=%s LIMIT 1", (matched_email,))
            row = cur.fetchone()
            if row and row[0]:
                api_key = row[0]
            email = matched_email
            # Don't resend welcome email if this session was already provisioned
            if row and row[1] == stripe_session_id and stripe_session_id:
                print(f"[PROVISION] Skipping duplicate welcome email for {email} session {stripe_session_id}")
                conn.commit(); cur.close(); release_db(conn)
                return api_key
        else:
            # No existing account — create one under the checkout email
            cur.execute("""
                INSERT INTO api_keys ("key", api_key, email, plan, requests_limit, stripe_customer_id, stripe_session_id, active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
            """, (api_key, api_key, email, plan, limit, stripe_customer_id, stripe_session_id))
    else:
        # Free signup: deduplicate on email -- reuse the existing key if the user already has one.
        cur.execute("SELECT api_key FROM api_keys WHERE email=%s LIMIT 1", (email,))
        row = cur.fetchone()
        if row and row[0]:
            api_key = row[0]
        else:
            cur.execute("""
                INSERT INTO api_keys ("key", api_key, email, plan, requests_limit, active)
                VALUES (%s, %s, %s, %s, %s, TRUE)
            """, (api_key, api_key, email, plan, limit))
    conn.commit()
    cur.close()
    release_db(conn)
    send_api_key_email(email, api_key, plan)
    return api_key

#  Scraper 
PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]
DOMAIN_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,}$")

def _ip_is_private(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return any(ip in net for net in PRIVATE_NETWORKS) or ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local or ip.is_multicast

def validate_domain(domain: str) -> str:
    """Validate a user-supplied domain to prevent SSRF. Returns the clean hostname or raises 400."""
    if not domain or not isinstance(domain, str):
        raise HTTPException(status_code=400, detail="Invalid domain")
    host = domain.strip().lower()
    # Strip scheme, path, credentials, port
    host = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", host)
    host = host.split("/")[0].split("?")[0].split("#")[0]
    if "@" in host:
        raise HTTPException(status_code=400, detail="Invalid domain")
    host = host.split(":")[0].rstrip(".")
    # Must look like a real domain (rejects bare IPs and localhost too)
    if not DOMAIN_RE.match(host):
        raise HTTPException(status_code=400, detail="Invalid domain")
    # Resolve and check every resolved IP against private/reserved ranges
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise HTTPException(status_code=400, detail="Invalid domain")
    ips = {info[4][0] for info in infos}
    if not ips or any(_ip_is_private(ip) for ip in ips):
        raise HTTPException(status_code=400, detail="Invalid domain")
    return host

async def scrape_page(domain: str):
    domain = domain.strip().lower().rstrip("/")
    if not domain.startswith("http"):
        domain = "https://" + domain
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_extra_http_headers({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        for suffix in ["/careers", "/jobs", "/about/careers"]:
            url = domain + suffix
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                if resp and resp.status < 400:
                    try:
                        await page.wait_for_load_state("networkidle", timeout=3000)
                    except Exception:
                        await asyncio.sleep(0.5)
                    text = await page.inner_text("body")
                    await browser.close()
                    return text, url, resp.status
            except Exception:
                continue
        await browser.close()
        raise HTTPException(status_code=404, detail="No careers/jobs page found for " + domain)

def extract_with_openai(raw_text: str):
    if not openai.api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set")
    try:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": EXTRACTION_PROMPT},
                {"role": "user", "content": "Careers page text:\n\n" + raw_text[:20000]},
            ],
            temperature=0,
        )
        content = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        # Find JSON object boundaries
        start = content.find("{")
        end = content.rfind("}") + 1
        if start != -1 and end > start:
            content = content[start:end]
        return json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse extraction response")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Extraction error: {str(e)}")

#  Startup 
@app.on_event("startup")
async def startup():
    init_db()


# 
# ROUTES  PUBLIC
# 

@app.get("/og-image.png")
async def og_image():
    import base64 as _b64
    _data = _b64.b64decode("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")
    return Response(content=_data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})

@app.get("/favicon.ico")
async def favicon_ico():
    import base64 as _b64
    return Response(content=_b64.b64decode("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"), media_type="image/x-icon", headers={"Cache-Control": "public, max-age=2592000"})

@app.get("/favicon.png")
async def favicon_png():
    import base64 as _b64
    return Response(content=_b64.b64decode("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"), media_type="image/png", headers={"Cache-Control": "public, max-age=2592000"})

@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return """User-agent: *
Allow: /
Disallow: /dashboard
Disallow: /auth
Disallow: /logout
Disallow: /admin
Disallow: /success
Disallow: /billing-portal
Disallow: /checkout
Sitemap: https://stacksight.org/sitemap.xml"""

@app.get("/sitemap.xml")
async def sitemap():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://stacksight.org/</loc><priority>1.0</priority><changefreq>weekly</changefreq></url>
  <url><loc>https://stacksight.org/docs</loc><priority>0.9</priority><changefreq>weekly</changefreq></url>
  <url><loc>https://stacksight.org/trending</loc><priority>0.8</priority><changefreq>daily</changefreq></url>
  <url><loc>https://stacksight.org/demo/vercel.com</loc><priority>0.7</priority><changefreq>monthly</changefreq></url>
  <url><loc>https://stacksight.org/demo/stripe.com</loc><priority>0.7</priority><changefreq>monthly</changefreq></url>
  <url><loc>https://stacksight.org/login</loc><priority>0.5</priority><changefreq>monthly</changefreq></url>
  <url><loc>https://stacksight.org/terms</loc><priority>0.3</priority><changefreq>yearly</changefreq></url>
  <url><loc>https://stacksight.org/privacy</loc><priority>0.3</priority><changefreq>yearly</changefreq></url>
  <url><loc>https://stacksight.org/vs/builtwith</loc><priority>0.8</priority><changefreq>monthly</changefreq></url>
  <url><loc>https://stacksight.org/vs/wappalyzer</loc><priority>0.8</priority><changefreq>monthly</changefreq></url>
  <url><loc>https://stacksight.org/vs/theirstack</loc><priority>0.8</priority><changefreq>monthly</changefreq></url>
  <url><loc>https://stacksight.org/demo/vercel.com</loc><priority>0.6</priority><changefreq>weekly</changefreq></url>
  <url><loc>https://stacksight.org/demo/stripe.com</loc><priority>0.6</priority><changefreq>weekly</changefreq></url>
  <url><loc>https://stacksight.org/demo/github.com</loc><priority>0.6</priority><changefreq>weekly</changefreq></url>
</urlset>"""
    return Response(content=xml, media_type="application/xml")

@app.get("/docs", response_class=HTMLResponse)
async def docs_page():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="StackSight API documentation. Simple REST API for B2B hiring intent signals and tech stack detection. Get started free.">
<meta property="og:title" content="API Docs - StackSight">
<meta property="og:description" content="Simple REST API for B2B hiring intent signals and tech stack detection. Get started free.">
<meta property="og:image" content="https://stacksight.org/og-image.png">
<meta property="og:url" content="https://stacksight.org/docs">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" type="image/png" href="/favicon.png"><link rel="shortcut icon" href="/favicon.ico">
<title>API Docs - StackSight</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0a;color:#e5e7eb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:flex;min-height:100vh}
a{color:#a855f7;text-decoration:none}
a:hover{text-decoration:underline}
/* Sidebar */
.sidebar{width:240px;min-width:240px;background:#111;border-right:1px solid #1f1f1f;padding:24px 0;position:sticky;top:0;height:100vh;overflow-y:auto}
.sidebar-logo{padding:0 20px 24px;font-size:18px;font-weight:700;color:#fff;border-bottom:1px solid #1f1f1f;margin-bottom:16px}
.sidebar-logo span{color:#a855f7}
.sidebar-section{padding:8px 20px 4px;font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:#6b7280;margin-top:12px}
.sidebar a{display:block;padding:7px 20px;font-size:14px;color:#9ca3af;border-left:2px solid transparent;transition:all .15s}
.sidebar a:hover,.sidebar a.active{color:#e5e7eb;border-left-color:#a855f7;background:rgba(168,85,247,.06);text-decoration:none}
/* Main */
.main{flex:1;padding:48px 56px;max-width:860px}
h1{font-size:32px;font-weight:700;color:#fff;margin-bottom:8px}
h2{font-size:22px;font-weight:600;color:#fff;margin:48px 0 16px;padding-top:48px;border-top:1px solid #1f1f1f}
h2:first-of-type{margin-top:24px;padding-top:0;border-top:none}
h3{font-size:15px;font-weight:600;color:#d1d5db;margin:24px 0 10px}
p{color:#9ca3af;line-height:1.7;margin-bottom:12px;font-size:15px}
/* Code */
pre{background:#111;border:1px solid #1f1f1f;border-radius:10px;padding:18px 20px;overflow-x:auto;margin:16px 0}
code{font-family:'JetBrains Mono','Fira Code',monospace;font-size:13px;color:#e5e7eb}
.inline-code{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:4px;padding:2px 7px;font-size:13px;color:#a855f7;font-family:monospace}
/* Method badges */
.method{display:inline-flex;align-items:center;gap:10px;margin-bottom:12px}
.badge{font-size:11px;font-weight:700;letter-spacing:.05em;padding:3px 10px;border-radius:5px}
.badge-get{background:rgba(34,197,94,.15);color:#22c55e;border:1px solid rgba(34,197,94,.3)}
.badge-post{background:rgba(168,85,247,.15);color:#a855f7;border:1px solid rgba(168,85,247,.3)}
.endpoint-path{font-family:monospace;font-size:16px;color:#fff;font-weight:600}
/* Tables */
table{width:100%;border-collapse:collapse;margin:16px 0;font-size:14px}
th{text-align:left;padding:10px 14px;background:#111;color:#6b7280;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid #1f1f1f}
td{padding:10px 14px;border-bottom:1px solid #1a1a1a;color:#d1d5db;vertical-align:top}
td:first-child{color:#a855f7;font-family:monospace;font-size:13px}
/* Rate limit pills */
.plan-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}
.plan-card{background:#111;border:1px solid #1f1f1f;border-radius:10px;padding:16px}
.plan-card.pro{border-color:rgba(168,85,247,.4)}
.plan-name{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#6b7280;margin-bottom:6px}
.plan-limit{font-size:22px;font-weight:700;color:#fff}
.plan-sub{font-size:12px;color:#6b7280;margin-top:2px}
/* Nav */
.topnav{display:none}
/* Tabs */
.tabs{display:flex;gap:4px;margin-bottom:-1px}
.tab{padding:8px 16px;font-size:13px;font-weight:500;color:#6b7280;cursor:pointer;border-bottom:2px solid transparent}
.tab.active{color:#a855f7;border-bottom-color:#a855f7}
.tab-content{display:none}
.tab-content.active{display:block}
</style>
</head>
<body>
<nav class="sidebar">
  <div class="sidebar-logo">Stack<span>Sight</span></div>
  <div class="sidebar-section">Getting Started</div>
  <a href="#quickstart">Quick Start</a>
  <a href="#auth">Authentication</a>
  <a href="#rate-limits">Rate Limits</a>
  <div class="sidebar-section">Endpoints</div>
  <a href="#enrich">GET /scrape</a>
  <a href="#bulk">POST /bulk</a>
  <a href="#usage">GET /usage</a>
  <a href="#webhooks">Webhooks</a>
  <div class="sidebar-section">Reference</div>
  <a href="#schema">Response Schema</a>
  <a href="#errors">Error Codes</a>
  <a href="#examples">Code Examples</a>
  <div style="padding:24px 20px 0;margin-top:auto">
    <a href="/" style="font-size:13px;color:#6b7280">&rarr; Back to Home</a>
  </div>
</nav>
<main class="main">
  <h1>API Documentation</h1>
  <p>Turn any domain into a complete company profile &rarr; hiring signals, tech stack, and enrichment data in one REST API call.</p>

  <h2 id="quickstart">Quick Start</h2>
  <p>Get your free API key at <a href="/">stacksight.org</a>, then make your first call:</p>
  <pre><code>curl -X GET "https://stacksight.org/scrape?domain=stripe.com" \
  -H "X-API-Key: ss_your_key"</code></pre>

  <h2 id="auth">Authentication</h2>
  <h3>X-API-Key Header</h3>
  <p>Pass your key in the <span class="inline-code">X-API-Key</span> header on every request. API keys are prefixed with <span class="inline-code">ss_</span>.</p>
  <pre><code>-H "X-API-Key: ss_abc123..."</code></pre>
  <p>You can find your API key in your <a href="/dashboard">dashboard</a> at any time.</p>

  <h2 id="rate-limits">Rate Limits</h2>
  <p>Requests are rate-limited per minute per API key. Monthly quotas reset on your billing date.</p>
  <div class="plan-grid">
    <div class="plan-card">
      <div class="plan-name">Free</div>
      <div class="plan-limit">25</div>
      <div class="plan-sub">total requests</div>
    </div>
    <div class="plan-card">
      <div class="plan-name">Starter</div>
      <div class="plan-limit">500</div>
      <div class="plan-sub">per month &middot; 60/min</div>
    </div>
    <div class="plan-card pro">
      <div class="plan-name">Pro</div>
      <div class="plan-limit">5,000</div>
      <div class="plan-sub">per month &middot; 300/min</div>
    </div>
    <div class="plan-card">
      <div class="plan-name">Business</div>
      <div class="plan-limit">50,000</div>
      <div class="plan-sub">per month &middot; 1,000/min</div>
    </div>
  </div>
  <p>When you exceed the rate limit you'll receive a <span class="inline-code">429 Too Many Requests</span> response. Retry after the window resets (60 seconds).</p>

  <h2 id="enrich">GET /scrape</h2>
  <div class="method">
    <span class="badge badge-get">GET</span>
    <span class="endpoint-path">/scrape</span>
  </div>
  <p>Enrich a single domain with hiring intent signals and tech stack detection.</p>
  <table>
    <tr><th>Param</th><th>Type</th><th>Required</th><th>Description</th></tr>
    <tr><td>domain</td><td>string</td><td>Yes</td><td>Domain to enrich, e.g. <span class="inline-code">stripe.com</span></td></tr>
  </table>
  <pre><code>curl "https://stacksight.org/scrape?domain=notion.so" \
  -H "X-API-Key: ss_your_key"</code></pre>
  <h3>Example Response</h3>
  <pre><code>{
  "domain": "notion.so",
  "source": "live",
  "data": {
    "company_name": "Notion",
    "is_hiring": true,
    "engineering_roles": ["Backend Engineer", "ML Engineer"],
    "sales_roles": ["Account Executive"],
    "detected_tech_stack": ["React", "Cloudflare", "Google Analytics", "Intercom"],
    "cached": false
  }
}</code></pre>

  <h2 id="bulk">POST /bulk</h2>
  <div class="method">
    <span class="badge badge-post">POST</span>
    <span class="endpoint-path">/bulk</span>
    <span style="font-size:12px;color:#a855f7;background:rgba(168,85,247,.1);border:1px solid rgba(168,85,247,.3);border-radius:4px;padding:2px 8px">Pro &amp; Business</span>
  </div>
  <p>Enrich up to 50 domains in a single request. Runs concurrently &rarr; same speed as one. Each domain counts as 1 request against your monthly quota.</p>
  <table>
    <tr><th>Body Field</th><th>Type</th><th>Required</th><th>Description</th></tr>
    <tr><td>domains</td><td>array</td><td>Yes</td><td>List of domains to enrich. Max 50.</td></tr>
  </table>
  <pre><code>curl -X POST "https://stacksight.org/bulk" \
  -H "X-API-Key: ss_your_key" \
  -H "Content-Type: application/json" \
  -d '{"domains": ["stripe.com", "notion.so", "vercel.com"]}'</code></pre>
  <pre><code>{
  "results": [
    {"domain": "stripe.com", "source": "cache", "data": {"company_name": "Stripe", "is_hiring": true, ...}},
    {"domain": "notion.so",  "source": "live",  "data": {"company_name": "Notion",  "is_hiring": true, ...}},
    {"domain": "vercel.com", "source": "cache", "data": {"company_name": "Vercel",  "is_hiring": true, ...}}
  ],
  "count": 3
}</code></pre>

  <h2 id="usage">GET /usage</h2>
  <div class="method">
    <span class="badge badge-get">GET</span>
    <span class="endpoint-path">/usage</span>
  </div>
  <p>Returns your current plan, usage stats, and remaining quota.</p>
  <pre><code>curl "https://stacksight.org/usage" \
  -H "X-API-Key: ss_your_key"</code></pre>
  <pre><code>{
  "plan": "pro",
  "requests_used": 142,
  "requests_limit": 5000,
  "requests_remaining": 4858,
  "created_at": "2026-07-01T12:00:00"
}</code></pre>

  <h2 id="webhooks">Webhooks</h2>
  <div class="method">
    <span class="badge badge-post">POST</span>
    <span class="endpoint-path">/webhooks/stripe</span>
  </div>
  <p>StackSight uses Stripe webhooks to handle subscription lifecycle events automatically.</p>
  <table>
    <tr><th>Event</th><th>Effect</th></tr>
    <tr><td>checkout.session.completed</td><td>Provisions API key and upgrades plan</td></tr>
    <tr><td>invoice.payment_succeeded</td><td>Resets monthly usage quota on billing cycle</td></tr>
    <tr><td>customer.subscription.deleted</td><td>Downgrades account to free plan</td></tr>
  </table>
  <p>Webhook signatures are verified using your Stripe webhook secret. Business plan customers can contact <a href="mailto:support@stacksight.org">support@stacksight.org</a> to configure custom webhook destinations.</p>

  <h2 id="schema">Response Schema</h2>
  <p>All responses wrap the result in a <span class="inline-code">data</span> object:</p>
  <pre><code>{
  "source": "cache",   // or "live" for a fresh scrape
  "data": {
    "company_name": "Stripe",
    "is_hiring": true,
    "engineering_roles": ["Backend Engineer", "ML Engineer"],
    "sales_roles": ["Account Executive", "Solutions Engineer"],
    "detected_tech_stack": ["React", "AWS", "Cloudflare"]
  }
}</code></pre>
  <table>
    <tr><th>Field</th><th>Type</th><th>Description</th></tr>
    <tr><td>source</td><td>string</td><td>"cache" or "live" &rarr; whether data was served from cache or freshly scraped</td></tr>
    <tr><td>data.company_name</td><td>string</td><td>Resolved company name</td></tr>
    <tr><td>data.is_hiring</td><td>boolean</td><td>Whether the company is actively hiring</td></tr>
    <tr><td>data.engineering_roles</td><td>array</td><td>Engineering job titles detected</td></tr>
    <tr><td>data.sales_roles</td><td>array</td><td>Sales job titles detected</td></tr>
    <tr><td>data.detected_tech_stack</td><td>array</td><td>Technologies found on their careers/jobs pages</td></tr>
  </table>

  <h2 id="errors">Error Codes</h2>
  <table>
    <tr><th>Status</th><th>Meaning</th></tr>
    <tr><td>400</td><td>Missing or invalid domain parameter</td></tr>
    <tr><td>401</td><td>Missing or invalid API key</td></tr>
    <tr><td>429</td><td>Rate limit exceeded &rarr; retry after 60s</td></tr>
    <tr><td>500</td><td>Scrape failed, please retry</td></tr>
  </table>

  <h2 id="examples">Code Examples</h2>
  <h3>Python</h3>
  <pre><code>import requests

r = requests.get(
    "https://stacksight.org/scrape",
    params={"domain": "stripe.com"},
    headers={"X-API-Key": "ss_your_key"}
)
print(r.json())</code></pre>
  <h3>JavaScript</h3>
  <pre><code>const res = await fetch('https://stacksight.org/scrape?domain=stripe.com', {
  headers: { 'X-API-Key': 'ss_your_key' }
});
console.log(await res.json());</code></pre>
  <h3>Node.js (axios)</h3>
  <pre><code>const axios = require('axios');
const { data } = await axios.get('https://stacksight.org/scrape', {
  params: { domain: 'stripe.com' },
  headers: { 'X-API-Key': 'ss_your_key' }
});
console.log(data);</code></pre>
</main>
<script>
// Highlight active sidebar link on scroll
const sections = document.querySelectorAll('h2[id]');
const links = document.querySelectorAll('.sidebar a');
window.addEventListener('scroll', () => {
  let current = '';
  sections.forEach(s => { if (window.scrollY >= s.offsetTop - 100) current = s.id; });
  links.forEach(l => {
    l.classList.toggle('active', l.getAttribute('href') === '#' + current);
  });
});
</script>
</body>
</html>""")
@app.get("/", response_class=HTMLResponse)
async def landing():
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<link rel="icon" type="image/png" href="/favicon.png"><link rel="shortcut icon" href="/favicon.ico">
<title>StackSight API - B2B Hiring Intent & Tech Stack Data</title>
<meta name="description" content="StackSight gives B2B sales teams real-time hiring intent signals, tech stack detection, and bulk domain enrichment via a simple REST API. Free tier available.">
<meta name="keywords" content="hiring intent API, B2B sales intelligence, tech stack detection, domain enrichment, sales signals, job posting data, B2B prospecting API">
<meta name="robots" content="index, follow">
<link rel="canonical" href="https://stacksight.org/">
<!-- Open Graph -->
<meta property="og:type" content="website">
<meta property="og:url" content="https://stacksight.org/">
<meta property="og:title" content="StackSight API - B2B Hiring Intent & Tech Stack Data">
<meta property="og:description" content="Know which companies are growing before your competitors. Real-time hiring signals, tech stack detection, and bulk enrichment via REST API. Free tier available.">
<meta property="og:image" content="https://stacksight.org/og-image.png">
<meta property="og:site_name" content="StackSight">
<!-- Twitter Card -->
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:site" content="@StackSightOrg">
<meta name="twitter:title" content="StackSight API - B2B Hiring Intent & Tech Stack Data">
<meta name="twitter:description" content="Know which companies are growing before your competitors. Real-time hiring signals, tech stack detection, and bulk enrichment via REST API.">
<meta name="twitter:image" content="https://stacksight.org/og-image.png">
<!-- Schema.org -->
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"Organization","name":"StackSight","url":"https://stacksight.org","logo":"https://stacksight.org/favicon.png","sameAs":["https://x.com/StackSightOrg","https://linkedin.com/company/stacksight"],"contactPoint":{{"@type":"ContactPoint","email":"support@stacksight.org","contactType":"customer support"}}}}
</script>
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"SoftwareApplication","name":"StackSight","url":"https://stacksight.org","description":"Real-time B2B hiring intent API. Know which companies are actively growing before your competitors do.","applicationCategory":"BusinessApplication","operatingSystem":"Web","offers":[{{"@type":"Offer","name":"Free","price":"0","priceCurrency":"USD"}},{{"@type":"Offer","name":"Pro","price":"49","priceCurrency":"USD","billingIncrement":"P1M"}},{{"@type":"Offer","name":"Business","price":"199","priceCurrency":"USD","billingIncrement":"P1M"}}]}}
</script>
<script type="application/ld+json">
{{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[{{"@type":"Question","name":"What is hiring intent data?","acceptedAnswer":{{"@type":"Answer","text":"Hiring intent data tells you when a company is actively growing by tracking their job postings. When a company posts multiple new roles it is a strong signal they have budget and momentum. StackSight captures this in real time."}}}},{{"@type":"Question","name":"How accurate is the tech stack detection?","acceptedAnswer":{{"@type":"Answer","text":"Very accurate. We scrape each company’s public careers page and use AI to extract technologies mentioned in job descriptions, plus signal detection from page source."}}}},{{"@type":"Question","name":"How fresh is the data?","acceptedAnswer":{{"@type":"Answer","text":"Results are cached for 7 days. For most use cases this is ideal. Cache misses trigger a live scrape that returns in seconds."}}}},{{"@type":"Question","name":"What happens when I hit my limit?","acceptedAnswer":{{"@type":"Answer","text":"You will get a 429 response with a clear error message. Upgrade any time from your dashboard. Your API key stays the same."}}}}]}}
</script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;line-height:1.6}}
.skip-link{{position:absolute;left:-9999px;top:8px;background:#a855f7;color:#fff;padding:8px 16px;border-radius:6px;font-weight:700;font-size:14px;z-index:9999;text-decoration:none}}.skip-link:focus{{left:8px}}
*:focus-visible{{outline:2px solid #a855f7;outline-offset:2px}}
nav{{padding:18px 40px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1a1a1a;position:sticky;top:0;background:rgba(10,10,10,0.95);backdrop-filter:blur(10px);z-index:100}}
.logo{{font-size:22px;font-weight:700;color:#a855f7;text-decoration:none}}
.nav-links a{{color:#c0c0c0;text-decoration:none;margin-left:24px;font-size:14px;transition:color .2s}}
.nav-links a:hover{{color:#fff}}
.nav-links .btn-login{{background:#1a1a1a;border:1px solid #333;color:#fff;padding:7px 16px;border-radius:7px;font-weight:500}}
.hero{{text-align:center;padding:90px 20px 60px;max-width:860px;margin:0 auto}}
.badge{{display:inline-flex;align-items:center;gap:6px;background:#1a0a2e;color:#a855f7;border:1px solid #3b1a6e;padding:5px 14px;border-radius:20px;font-size:12px;font-weight:600;margin-bottom:28px}}
h1{{font-size:56px;font-weight:800;line-height:1.08;margin-bottom:22px;letter-spacing:-1px}}
h2{{letter-spacing:-0.5px}}
h1 span{{color:#a855f7}}
.hero p{{font-size:19px;color:#b0b0b0;max-width:620px;margin:0 auto 40px;line-height:1.7}}
.cta-group{{display:flex;gap:14px;justify-content:center;flex-wrap:wrap}}
.btn-primary{{background:#a855f7;color:#fff;padding:14px 32px;border-radius:9px;text-decoration:none;font-weight:700;font-size:16px;transition:background .2s;display:inline-flex;align-items:center;gap:8px}}
.btn-primary:hover{{background:#9333ea}}
.btn-secondary{{background:transparent;color:#ccc;padding:14px 28px;border-radius:9px;text-decoration:none;font-weight:600;font-size:16px;border:1px solid #2a2a2a;transition:border-color .2s,color .2s}}
.btn-secondary:hover{{border-color:#555;color:#fff}}
.stats-bar{{display:flex;justify-content:center;gap:48px;flex-wrap:wrap;padding:40px 20px;border-top:1px solid #1a1a1a;border-bottom:1px solid #1a1a1a;margin:0 0 60px}}
.stat{{text-align:center}}
.stat-num{{font-size:28px;font-weight:800;background:linear-gradient(135deg,#c084fc,#a855f7 55%,#7c3aed);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;color:#a855f7}}
.stat-label{{font-size:13px;color:#888;margin-top:2px}}
.use-cases{{max-width:1100px;margin:0 auto 80px;padding:0 20px}}
.use-cases h2,.how h2,.code-section h2,.pricing h2,.faq h2{{text-align:center;font-size:34px;font-weight:700;margin-bottom:12px}}
.sub{{text-align:center;color:#888;margin-bottom:44px;font-size:16px}}
.use-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px}}
.use-card{{background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:28px;transition:border-color .2s}}
.use-card:hover{{border-color:#5b2a9e}}
.use-icon{{width:44px;height:44px;border-radius:10px;background:#1a0a2e;border:1px solid #3b1a6e;display:flex;align-items:center;justify-content:center;margin-bottom:14px;color:#a855f7}}
.use-card h3{{font-size:16px;font-weight:600;margin-bottom:8px}}
.use-card p{{color:#9a9a9a;font-size:14px;line-height:1.6}}
.how{{max-width:860px;margin:0 auto 80px;padding:0 20px;text-align:center}}
.steps{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:0}}
.step{{padding:24px;position:relative}}
.step:not(:last-child)::after{{content:"";position:absolute;top:44px;left:calc(50% + 34px);width:calc(100% - 68px);height:2px;background:linear-gradient(90deg,#3b1a6e,#1a0a2e)}}
.step-num{{width:40px;height:40px;border-radius:50%;background:#1a0a2e;border:2px solid #a855f7;color:#a855f7;font-weight:800;font-size:16px;display:flex;align-items:center;justify-content:center;margin:0 auto 16px}}
.step h4{{font-size:15px;font-weight:600;margin-bottom:6px}}
.step p{{color:#9a9a9a;font-size:13px}}
.code-section{{max-width:900px;margin:0 auto 80px;padding:0 20px}}
.code-tabs{{display:flex;gap:8px;margin-bottom:0;border-bottom:1px solid #1f1f1f}}
.tab{{padding:10px 18px;font-size:13px;color:#888;border-bottom:2px solid transparent}}
.tab.active{{color:#a855f7;border-bottom-color:#a855f7}}
pre{{background:#0d0d0d;border:1px solid #1f1f1f;border-top:none;border-radius:0 0 10px 10px;padding:28px;overflow-x:auto;font-size:13px;color:#ccc;line-height:1.8;margin:0 0 80px}}
.k{{color:#a855f7}}.s{{color:#22d3ee}}.n{{color:#fb923c}}
.signup-section{{max-width:540px;margin:0 auto 80px;padding:0 20px;text-align:center}}
.signup-section h2{{font-size:32px;font-weight:700;margin-bottom:10px;text-align:center}}
.signup-section p{{color:#aaa;margin-bottom:28px;font-size:15px}}
.form-row{{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}}
.form-row input{{flex:1;min-width:240px;background:#111;border:1px solid #2a2a2a;color:#fff;padding:13px 16px;border-radius:9px;font-size:15px;transition:border-color .2s}}
.form-row input:focus{{border-color:#a855f7}}.form-row input:focus-visible{{outline:2px solid #a855f7;outline-offset:2px}}
.form-row button{{background:#a855f7;color:#fff;border:none;padding:13px 24px;border-radius:9px;font-size:15px;font-weight:700;cursor:pointer;transition:background .2s}}
.form-row button:hover{{background:#9333ea}}
.msg{{margin-top:16px;padding:12px 16px;border-radius:8px;font-size:14px;display:none}}
.msg.success{{background:#0a1f0a;border:1px solid #22c55e;color:#22c55e}}
.msg.error{{background:#1f0a0a;border:1px solid #ef4444;color:#ef4444}}
.trust-badges{{display:flex;justify-content:center;gap:24px;flex-wrap:wrap;margin-top:20px}}
.trust-badge{{font-size:12px;color:#777;display:flex;align-items:center;gap:5px}}

.why-section{{max-width:860px;margin:0 auto 80px;padding:0 20px;text-align:center}}
.why-section h2{{font-size:34px;font-weight:700;margin-bottom:12px}}
.why-grid{{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin:40px 0;text-align:left}}
.why-item{{display:flex;gap:16px;background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:24px}}
.why-check{{color:#a855f7;font-size:22px;font-weight:700;flex-shrink:0;margin-top:2px}}
.why-item strong{{display:block;font-size:16px;margin-bottom:6px;color:#fff}}
.why-item p{{color:#b0b0b0;font-size:14px;line-height:1.6;margin:0}}
.why-footer{{color:#888;font-size:15px;line-height:1.7;margin-top:8px;font-style:italic}}
@media(max-width:600px){{.why-grid{{grid-template-columns:1fr}}}}
.pricing{{max-width:1020px;margin:0 auto 80px;padding:0 20px}}
.plans{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}}
.plan{{background:#111;border:1px solid #1f1f1f;border-radius:14px;padding:32px;position:relative;transition:border-color .2s}}
.plan:hover{{border-color:#5b2a9e}}
.plan.featured{{border-color:#a855f7;background:#130a20}}
.plan-badge{{position:absolute;top:-13px;left:50%;transform:translateX(-50%);background:#a855f7;color:#fff;padding:4px 18px;border-radius:20px;font-size:11px;font-weight:700;white-space:nowrap}}
.plan-name{{font-size:17px;font-weight:700;margin-bottom:4px;color:#ccc}}
.plan-price{{font-size:40px;font-weight:800;color:#fff;margin:14px 0 4px;letter-spacing:-1px}}
.plan-price span{{font-size:16px;color:#777;font-weight:400}}
.plan-limit{{color:#888;font-size:13px;margin-bottom:24px}}
.plan ul{{list-style:none;margin-bottom:28px}}
.plan ul li{{padding:7px 0;font-size:14px;color:#bbb;display:flex;align-items:center;gap:8px}}
.plan ul li::before{{content:"";color:#a855f7;font-weight:700;flex-shrink:0}}
.plan a{{display:block;text-align:center;padding:13px;border-radius:9px;font-weight:700;font-size:15px;text-decoration:none;transition:all .2s}}
.btn-pp{{background:#a855f7;color:#fff}}
.btn-pp:hover{{background:#9333ea}}
.btn-ps{{border:1px solid #2a2a2a;color:#ccc}}
.btn-ps:hover{{border-color:#555;color:#fff}}
.faq{{max-width:720px;margin:0 auto 80px;padding:0 20px}}
.faq-item{{border-bottom:1px solid #1a1a1a;padding:20px 0}}
.faq-q{{font-size:16px;font-weight:600;cursor:pointer;display:flex;justify-content:space-between;align-items:center;color:#ddd}}
.faq-q:hover{{color:#a855f7}}
.faq-a{{color:#888;font-size:14px;line-height:1.7;margin-top:12px;display:none}}
.faq-a.open{{display:block}}
.final-cta{{text-align:center;padding:80px 20px;background:linear-gradient(180deg,transparent,#0f0520 50%,transparent)}}
.final-cta h2{{font-size:40px;font-weight:800;margin-bottom:16px;text-align:center}}
.final-cta p{{color:#aaa;font-size:17px;margin-bottom:36px}}
footer{{border-top:1px solid #1a1a1a;padding:32px 20px;text-align:center;color:#666;font-size:13px}}
footer a{{color:#888;text-decoration:none}}
footer a:hover{{color:#fff}}
@media(max-width:640px){{
  h1{{font-size:32px}}
  .stats-bar{{gap:20px;padding:28px 16px}}
  nav{{padding:14px 16px}}
  .nav-links a:not(.btn-login){{display:none}}
  .hero{{padding:60px 16px 40px}}
  .hero p{{font-size:16px}}
  .cta-group{{flex-direction:column;align-items:center}}
  .plans{{grid-template-columns:1fr !important}}
  .plan{{margin-bottom:0}}
  .step::after{{display:none}}
  .why-grid{{grid-template-columns:1fr}}
  .faq{{padding:0 12px}}
  .final-cta h2{{font-size:28px}}
}}
</style>
</head>
<body>
<a href="#main-content" class="skip-link">Skip to main content</a>
<nav>
  <a href="/" class="logo">StackSight</a>
  <div class="nav-links">
    <a href="/docs">Docs</a>
    <a href="/demo/vercel.com">Demo</a>
    <a href="/trending">Trending</a>
    <a href="#pricing">Pricing</a>
    <a href="/login" class="btn-login" id="nav-auth-btn">Sign In</a>
  </div>
</nav>
<script>
(function(){{
  fetch("/session-check",{{credentials:"include"}}).then(r=>r.json()).then(d=>{{
    if(d.logged_in){{
      var btn=document.getElementById("nav-auth-btn");
      if(btn){{btn.outerHTML='<a href="/dashboard" id="nav-auth-btn" title="My Account" style="display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:50%;background:#2a1a4a;border:2px solid #7c3aed;cursor:pointer;text-decoration:none"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#a855f7" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg></a>';}}
    }}
  }}).catch(function(){{}});
}})();
</script>
<div class="hero" id="main-content">
  <div class="badge">Live Data</div>
  <h1>Turn any domain into<br><span>B2B sales intelligence</span></h1>
  <p>Real-time hiring intent signals, AI-powered tech stack detection, and bulk enrichment &mdash; all in one REST API.</p>
  <div class="cta-group">
    <a href="#signup" class="btn-primary"> Start for Free</a>
    <a href="/demo/vercel.com" class="btn-secondary">See Example</a>
  </div>
  <div style="margin-top:48px;text-align:center">
    <div <p style="font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#9ca3af;margin-bottom:10px;margin-top:12px">Try any domain</p>
    <div "hero-demo-row" style="display:flex;background:#111;border:1.5px solid #2a2a2a;border-radius:14px;padding:6px 6px 6px 18px;align-items:center;max-width:480px;margin:0 auto">
      <label for="hero-domain-input" style="position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0">Company domain</label>
        <input id="hero-domain-input" type="text" placeholder="stripe.com" autocomplete="off"
        style="flex:1;padding:10px 0;background:transparent;border:none;color:#fff;font-size:15px;outline:none" aria-label="Company domain"
        onfocus="this.closest('#hero-demo-row').style.borderColor='#7c3aed'"
        onblur="this.closest('#hero-demo-row').style.borderColor='#2a2a2a'">
      <button id="hero-demo-btn" onclick="heroDemo()"
        style="padding:10px 22px;background:linear-gradient(135deg,#7c3aed,#a855f7);color:#fff;border:none;border-radius:10px;font-size:14px;font-weight:600;cursor:pointer;white-space:nowrap">Analyze -></button>
    </div>
  </div>
  <script>
  async function heroDemo() {{
    var domain = document.getElementById('hero-domain-input').value.trim() || 'stripe.com';
    var btn = document.getElementById('hero-demo-btn');
    btn.textContent = 'Checking...'; btn.disabled = true;
    try {{
      var r = await fetch('/usage', {{credentials:'include'}});
      if (!r.ok) {{ window.location.href = '/login?next=/demo/' + encodeURIComponent(domain); return; }}
      window.location.href = '/demo/' + encodeURIComponent(domain);
    }} catch(e) {{
      window.location.href = '/login?next=/demo/' + encodeURIComponent(domain);
    }} finally {{
      btn.disabled = false; btn.textContent = 'Analyze ->';;
    }}
  }}
  document.addEventListener('DOMContentLoaded', function() {{
    var inp = document.getElementById('hero-domain-input');
    if (inp) inp.addEventListener('keydown', function(e) {{ if (e.key==='Enter') heroDemo(); }});
  }});
  </script>
</div>
<div class="stats-bar">
  <div class="stat"><div class="stat-num">Any</div><div class="stat-label">domain, analyzed live</div></div>
  <div class="stat"><div class="stat-num">&lt;100ms</div><div class="stat-label">cached response time</div></div>
  <div class="stat"><div class="stat-num">20+</div><div class="stat-label">Tech signals detected</div></div>
  <div class="stat"><div class="stat-num">3</div><div class="stat-label">Lines to integrate</div></div>
</div>
<div class="use-cases">
  <h2>Built for teams that move fast</h2>
  <p class="sub">From sales to engineering -- StackSight gives every team the domain intelligence they need.</p>
  <div class="use-grid">
    <div class="use-card"><div class="use-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg></div><h3>Hiring Intent</h3><p>When a company posts 10 new sales roles, that's a buying signal. StackSight surfaces it instantly.</p></div>
    <div class="use-card"><div class="use-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg></div><h3>Tech Stack Intel</h3><p>Know what frontend frameworks, analytics, and marketing tech your prospect runs before your first call.</p></div>
    <div class="use-card"><div class="use-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg></div><h3>Bulk Enrichment</h3><p>Enrich your entire CRM overnight. 50 domains per request, Redis-cached for speed.</p></div>
    <div class="use-card"><div class="use-icon"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg></div><h3>CRM Automation</h3><p>Pipe signals directly into your CRM or Slack. Trigger sequences when companies show intent.</p></div>
  </div>
</div>
<div class="how">
  <h2>Up and running in minutes</h2>
  <p class="sub">No complex setup. No sales call required.</p>
  <div class="steps">
    <div class="step"><div class="step-num">1</div><h4>Get your API key</h4><p>Sign up with your email. Instant delivery, no credit card.</p></div>
    <div class="step"><div class="step-num">2</div><h4>Make your first call</h4><p>Pass any domain to our REST endpoint. Get structured JSON back.</p></div>
    <div class="step"><div class="step-num">3</div><h4>Pipe it into your stack</h4><p>Connect to your CRM, Slack, or data warehouse in minutes.</p></div>
  </div>
</div>
<div class="code-section">
  <h2>Simple REST API</h2>
  <p class="sub">One endpoint. Structured JSON. Works with any language.</p>
  <div class="code-tabs"><div class="tab active">cURL</div></div>
  <pre>curl -X GET "https://stacksight.org/scrape?domain=stripe.com" \
     -H "X-API-Key: YOUR_API_KEY"

<span class="k">"company_name"</span>: <span class="s">"Stripe"</span>,
<span class="k">"is_hiring"</span>: <span class="n">true</span>,
<span class="k">"engineering_roles"</span>: [<span class="s">"Backend Engineer"</span>, <span class="s">"ML Engineer"</span>],
<span class="k">"sales_roles"</span>: [<span class="s">"Account Executive"</span>],
<span class="k">"detected_tech_stack"</span>: [<span class="s">"React"</span>, <span class="s">"AWS"</span>, <span class="s">"Cloudflare"</span>]</pre>
</div>
<div class="signup-section" id="signup">
  <h2>Start for free</h2>
  <p>25 lookups &nbsp;&bull;&nbsp; no credit card &nbsp;&bull;&nbsp; instant delivery</p>
  <div class="form-row">
    <label for="email-input" style="position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0">Your work email</label>
    <input type="email" id="email-input" placeholder="you@company.com" autocomplete="email">
    <button onclick="signup()">Get My Free Key</button>
  </div>
  <div class="msg" id="signup-msg"></div>
  <div class="trust-badges">
    <span class="trust-badge"> No credit card</span>
    <span class="trust-badge"> Instant delivery</span>
    <span class="trust-badge"> No spam ever</span>
  </div>
</div>

<div class="why-section" id="why">
  <h2>Why developers choose StackSight</h2>
  <p class="sub">We're not the biggest. We're the fastest, cheapest, and simplest.</p>
  <div class="why-grid">
    <div class="why-item"><span class="why-check">&#10003;</span><div><strong>Real-time data</strong><p>We scrape live -- days-fresh data -- not a months-old database. What you get is what's on their site today.</p></div></div>
    <div class="why-item"><span class="why-check">&#10003;</span><div><strong>10x cheaper</strong><p>No enterprise pricing. No annual contracts. No minimum seats. Pay for what you use.</p></div></div>
    <div class="why-item"><span class="why-check">&#10003;</span><div><strong>Zero friction</strong><p>Sign up, get a key, make a call. No sales calls, no demos, no approval process.</p></div></div>
    <div class="why-item"><span class="why-check">&#10003;</span><div><strong>Simple API</strong><p>One endpoint, clean JSON, works in minutes. No SDKs required, no complex setup.</p></div></div>
  </div>
  <p class="why-footer">Our competitors have broader data -- contact info, firmographics, CRM integrations. If you need all that, use them. If you need fast, fresh, affordable hiring signals and tech stack data -- that's us.</p>
</div>
<div class="pricing" id="pricing">
  <h2>Simple Pricing</h2>
  <p class="sub">Scale as you grow. Cancel any time.</p>
  <div class="plans">
    <div class="plan">
      <div class="plan-name">Free</div>
      <div class="plan-price">$0<span>/mo</span></div>
      <div class="plan-limit">25 requests total</div>
      <ul><li>25 API requests</li><li>JSON responses</li><li>Tech stack detection</li><li>Community support</li></ul>
      <a href="#signup" class="btn-ps">Get Started Free</a>
    </div>
        <div class="plan">
      <div class="plan-name">Starter</div>
      <div class="plan-price">$12<span>/mo</span></div>
      <div class="plan-limit">500 requests/month</div>
      <ul><li>500 API requests/month</li><li>JSON responses</li><li>Tech stack detection</li><li>Hiring intent signals</li><li>Email support</li></ul>
      <a href="/checkout/starter" class="btn-pp">Get Starter </a>
    </div>
    <div class="plan featured">
      <div class="plan-badge">MOST POPULAR</div>
      <div class="plan-name">Pro</div>
      <div class="plan-price">$39<span>/mo</span></div>
      <div style="font-size:12px;color:#aaa;margin-top:-8px;margin-bottom:10px">billed annually &bull; save 20%</div>
      <div class="plan-limit">5,000 requests/month</div>
      <ul><li>5,000 API requests/month</li><li>300 req/min rate limit</li><li>Bulk API (50 domains)</li><li>Redis-cached responses</li><li>Priority support</li></ul>
      <a href="/choose/pro" class="btn-pp">Get Pro</a>
    </div>
    <div class="plan">
      <div class="plan-name">Business</div>
      <div class="plan-price">$166<span>/mo</span></div>
      <div style="font-size:12px;color:#aaa;margin-top:-8px;margin-bottom:10px">billed annually &bull; save 20%</div>
      <div class="plan-limit">50,000 requests/month</div>
      <ul><li>50,000 API requests/month</li><li>1,000 req/min rate limit</li><li>Bulk API (50 domains)</li><li>Webhook support</li><li>Dedicated support</li></ul>
      <a href="/choose/business" class="btn-pp">Get Business</a>
    </div>
  </div>
</div>
<div class="faq">
  <h2>Frequently Asked Questions</h2>
  <div class="faq-item"><div class="faq-q" onclick="toggleFaq(this)"><span>What is hiring intent data?</span><span>+</span></div><div class="faq-a">Hiring intent data tells you when a company is actively growing by tracking their job postings. When a company posts multiple new roles it is a strong signal they have budget and momentum. StackSight captures this in real time.</div></div>
  <div class="faq-item"><div class="faq-q" onclick="toggleFaq(this)"><span>How accurate is the tech stack detection?</span><span>+</span></div><div class="faq-a">Very accurate. We scrape each company's public careers page and use AI to extract technologies mentioned in job descriptions, plus signal detection from page source. Results improve as more companies are analyzed.</div></div>
  <div class="faq-item"><div class="faq-q" onclick="toggleFaq(this)"><span>How fresh is the data?</span><span>+</span></div><div class="faq-a">Results are cached for 7 days in Redis. For most use cases this is ideal. Cache misses trigger a live scrape that returns in seconds.</div></div>
  <div class="faq-item"><div class="faq-q" onclick="toggleFaq(this)"><span>Can I use this in my CRM?</span><span>+</span></div><div class="faq-a">Yes. Our REST API returns structured JSON that integrates with any CRM, data warehouse, or automation tool. Many customers pipe signals directly into Salesforce, HubSpot, or Clay.</div></div>
  <div class="faq-item"><div class="faq-q" onclick="toggleFaq(this)"><span>What happens when I hit my limit?</span><span>+</span></div><div class="faq-a">You will get a 429 response with a clear error message. Upgrade any time from your dashboard. Your API key stays the same.</div></div>
</div>
<div class="final-cta">
  <h2>Know who is growing.<br>Before your competitors do.</h2>
  <p>Free tier available. No credit card required.</p>
  <div style="display:flex;justify-content:center;gap:40px;margin-bottom:32px;flex-wrap:wrap">
    <div style="text-align:center">
      <div style="font-size:32px;font-weight:800;color:#a855f7">2,000+</div>
      <div style="font-size:13px;color:#6b7280;margin-top:4px">API calls daily</div>
    </div>
    <div style="text-align:center">
      <div style="font-size:32px;font-weight:800;color:#a855f7">500+</div>
      <div style="font-size:13px;color:#6b7280;margin-top:4px">companies analyzed</div>
    </div>
    <div style="text-align:center">
      <div style="font-size:32px;font-weight:800;color:#a855f7">&lt;3s</div>
      <div style="font-size:13px;color:#6b7280;margin-top:4px">average response time</div>
    </div>
  </div>
  <a href="#signup" class="btn-primary" style="font-size:18px;padding:16px 40px"> Get Free API Key</a>
</div>
<footer>
  <div style="margin-bottom:14px;font-size:16px;font-weight:700;color:#a855f7;letter-spacing:-0.5px">Stack<span style="color:#e5e5e5">Sight</span></div>
  <div style="margin-bottom:12px">
    <a href="/docs">Docs</a> &nbsp;&nbsp; <a href="/demo/vercel.com">Demo</a> &nbsp;&nbsp; <a href="#pricing">Pricing</a> &nbsp;&nbsp; <a href="/login">Sign In</a> &nbsp;&nbsp; <a href="mailto:support@stacksight.org">Contact</a>
  </div>
  <div style="margin-bottom:8px">
    <a href="/terms">Terms of Service</a> &nbsp;&nbsp; <a href="/privacy">Privacy Policy</a>
  </div>
  <div>&copy; 2026 StackSight &nbsp;&nbsp; <a href="https://x.com/StackSightOrg">@StackSightOrg</a> &nbsp;&nbsp; <a href="https://linkedin.com/company/stacksight" target="_blank" rel="noopener">LinkedIn</a></div>
</footer>
<script>
async function signup() {{
  const email = document.getElementById('email-input').value.trim();
  const msg = document.getElementById('signup-msg');
  if (!email || !email.includes('@')) {{ msg.className='msg error'; msg.style.display='block'; msg.textContent='Please enter a valid email.'; return; }}
  const btn = document.querySelector('.form-row button');
  btn.textContent='Sending...'; btn.disabled=true;
  msg.className='msg'; msg.style.display='block'; msg.textContent='Sending...';
  try {{
    const r = await fetch('/signup', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{email}})}});
    const d = await r.json();
    if (r.ok) {{ msg.className='msg success'; msg.textContent='Check your inbox! Click the link to get your API key.'; btn.textContent='Sent!'; }}
    else {{ msg.className='msg error'; msg.textContent=d.detail||'Something went wrong.'; btn.textContent='Get My Free Key'; btn.disabled=false; }}
  }} catch(e) {{ msg.className='msg error'; msg.textContent='Network error. Please try again.'; btn.textContent='Get My Free Key'; btn.disabled=false; }}
}}
document.getElementById('email-input').addEventListener('keypress', e => {{ if(e.key==='Enter') signup(); }});
function toggleFaq(el) {{
  const a = el.nextElementSibling;
  const icon = el.querySelector('span:last-child');
  const open = a.classList.toggle('open');
  icon.textContent = open ? '-' : '+';
}}
</script>
</body></html>""")


@app.get("/vs/builtwith", response_class=HTMLResponse)
async def vs_builtwith():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/png" href="/favicon.png"><link rel="shortcut icon" href="/favicon.ico">
<title>StackSight vs BuiltWith (2026) - API, Pricing & Features Compared</title>
<meta name="description" content="StackSight vs BuiltWith: real-time hiring intent + tech detection API from $0 vs BuiltWith's $295/month data export tool. See the full comparison.">
<meta property="og:title" content="StackSight vs BuiltWith (2026)">
<meta property="og:description" content="Compare StackSight and BuiltWith on pricing, API access, hiring intent signals, and real-time data freshness.">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0015;color:#e0e0e0;line-height:1.6}}
.container{{max-width:900px;margin:0 auto;padding:60px 24px}}
h1{{font-size:42px;font-weight:800;margin-bottom:16px;letter-spacing:-1px}}
h1 span{{color:#a855f7}}
.subtitle{{font-size:18px;color:#b0b0b0;margin-bottom:48px;max-width:640px}}
h2{{font-size:24px;font-weight:700;margin:48px 0 16px;color:#fff}}
.comparison-table{{width:100%;border-collapse:collapse;margin-bottom:48px}}
.comparison-table th{{background:#1a0a2e;padding:14px 20px;text-align:left;font-size:13px;font-weight:600;color:#a855f7;text-transform:uppercase;letter-spacing:0.5px}}
.comparison-table td{{padding:14px 20px;border-bottom:1px solid #1a0a2e;font-size:14px}}
.comparison-table tr:hover td{{background:#0d0020}}
.yes{{color:#4ade80;font-weight:600}}
.no{{color:#f87171}}
.partial{{color:#fb923c}}
.winner{{background:#1a0a2e!important}}
.cta-box{{background:linear-gradient(135deg,#1a0a2e,#0d0020);border:1px solid #3b1a6e;border-radius:16px;padding:40px;text-align:center;margin-top:48px}}
.cta-box h2{{margin-top:0}}
.cta-box p{{color:#b0b0b0;margin-bottom:24px}}
.btn{{display:inline-block;background:#a855f7;color:#fff;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:700;font-size:16px}}
.btn:hover{{background:#9333ea}}
.verdict{{background:#0d0020;border-left:3px solid #a855f7;padding:20px 24px;border-radius:0 8px 8px 0;margin:24px 0;font-size:15px;color:#d0d0d0}}
footer{{text-align:center;padding:40px;color:#888;font-size:13px;border-top:1px solid #1a0a2e;margin-top:60px}}
</style>
</head>
<body>
<nav style="background:#0a0015;padding:16px 32px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #1a0a2e">
  <a href="/" style="color:#a855f7;font-weight:800;font-size:18px;text-decoration:none">StackSight</a>
  <div style="display:flex;gap:24px;align-items:center">
    <a href="/docs" style="color:#b0b0b0;text-decoration:none;font-size:14px">Docs</a>
    <a href="/demo/vercel.com" style="color:#b0b0b0;text-decoration:none;font-size:14px">Demo</a>
    <a href="/#pricing" style="color:#b0b0b0;text-decoration:none;font-size:14px">Pricing</a>
    <a href="/login" style="background:#a855f7;color:#fff;padding:8px 18px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">Sign In</a>
  </div>
</nav>
<div class="container">
<h1>StackSight vs <span>BuiltWith</span></h1>
<p class="subtitle">BuiltWith is the category veteran for tech profiling. StackSight is a real-time API built for developers and revenue teams who need fresh data, not exports.</p>

<h2>Pricing</h2>
<table class="comparison-table">
<tr><th>Plan</th><th>StackSight</th><th>BuiltWith</th></tr>
<tr><td>Free tier</td><td class="yes winner">25 lookups/month</td><td class="partial">Public widget only</td></tr>
<tr><td>Starter</td><td class="yes winner">$12/mo -- 500 req</td><td class="no">Not available</td></tr>
<tr><td>Pro</td><td class="yes winner">$49/mo -- 5,000 req</td><td class="no">$295/mo</td></tr>
<tr><td>Business</td><td class="yes winner">$199/mo -- 50,000 req</td><td class="no">$495--$995/mo</td></tr>
<tr><td>API access</td><td class="yes winner">All paid plans</td><td class="partial">Pro+ only</td></tr>
</table>

<h2>Features</h2>
<table class="comparison-table">
<tr><th>Feature</th><th>StackSight</th><th>BuiltWith</th></tr>
<tr><td>Tech stack detection</td><td class="yes">--</td><td class="yes">-- (larger DB)</td></tr>
<tr><td>Hiring intent signals</td><td class="yes winner">-- Real-time</td><td class="no">--</td></tr>
<tr><td>Live scrape (not cached DB)</td><td class="yes winner">-- Days-fresh</td><td class="no">-- Historical DB</td></tr>
<tr><td>REST API</td><td class="yes winner">-- Developer-first</td><td class="partial">-- Complex</td></tr>
<tr><td>Bulk enrichment</td><td class="yes winner">-- 50 domains/req</td><td class="yes">-- CSV export</td></tr>
<tr><td>JSON responses</td><td class="yes winner">--</td><td class="partial">-- (verbose)</td></tr>
<tr><td>No-code UI</td><td class="no">API-only</td><td class="yes">-- Full UI</td></tr>
</table>

<div class="verdict">
<strong>Bottom line:</strong> BuiltWith has a larger technology database built from years of crawling. StackSight is the better choice if you need a developer API, real-time hiring signals, or a price point that doesn't start at $295/month.
</div>

<div class="cta-box">
<h2>Try StackSight Free</h2>
<p>25 free lookups. No credit card. Live hiring intent + tech stack in one API call.</p>
<a href="/#signup" class="btn">Get Free API Key</a>
</div>
</div>
<footer>-- 2026 StackSight -- <a href="/vs/wappalyzer" style="color:#a855f7">vs Wappalyzer</a> -- <a href="/vs/theirstack" style="color:#a855f7">vs TheirStack</a></footer>
</body></html>""")

@app.get("/vs/wappalyzer", response_class=HTMLResponse)
async def vs_wappalyzer():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/png" href="/favicon.png"><link rel="shortcut icon" href="/favicon.ico">
<title>StackSight vs Wappalyzer (2026) - API, Pricing & Features Compared</title>
<meta name="description" content="StackSight vs Wappalyzer: hiring intent + tech detection from $12/mo vs Wappalyzer's $250/month. Compare API access, data freshness, and pricing.">
<meta property="og:title" content="StackSight vs Wappalyzer (2026)">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0015;color:#e0e0e0;line-height:1.6}}
.container{{max-width:900px;margin:0 auto;padding:60px 24px}}
h1{{font-size:42px;font-weight:800;margin-bottom:16px;letter-spacing:-1px}}
h1 span{{color:#a855f7}}
.subtitle{{font-size:18px;color:#b0b0b0;margin-bottom:48px;max-width:640px}}
h2{{font-size:24px;font-weight:700;margin:48px 0 16px;color:#fff}}
.comparison-table{{width:100%;border-collapse:collapse;margin-bottom:48px}}
.comparison-table th{{background:#1a0a2e;padding:14px 20px;text-align:left;font-size:13px;font-weight:600;color:#a855f7;text-transform:uppercase;letter-spacing:0.5px}}
.comparison-table td{{padding:14px 20px;border-bottom:1px solid #1a0a2e;font-size:14px}}
.comparison-table tr:hover td{{background:#0d0020}}
.yes{{color:#4ade80;font-weight:600}}
.no{{color:#f87171}}
.partial{{color:#fb923c}}
.winner{{background:#1a0a2e!important}}
.cta-box{{background:linear-gradient(135deg,#1a0a2e,#0d0020);border:1px solid #3b1a6e;border-radius:16px;padding:40px;text-align:center;margin-top:48px}}
.cta-box h2{{margin-top:0}}
.cta-box p{{color:#b0b0b0;margin-bottom:24px}}
.btn{{display:inline-block;background:#a855f7;color:#fff;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:700;font-size:16px}}
.btn:hover{{background:#9333ea}}
.verdict{{background:#0d0020;border-left:3px solid #a855f7;padding:20px 24px;border-radius:0 8px 8px 0;margin:24px 0;font-size:15px;color:#d0d0d0}}
footer{{text-align:center;padding:40px;color:#888;font-size:13px;border-top:1px solid #1a0a2e;margin-top:60px}}
</style>
</head>
<body>
<nav style="background:#0a0015;padding:16px 32px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #1a0a2e">
  <a href="/" style="color:#a855f7;font-weight:800;font-size:18px;text-decoration:none">StackSight</a>
  <div style="display:flex;gap:24px;align-items:center">
    <a href="/docs" style="color:#b0b0b0;text-decoration:none;font-size:14px">Docs</a>
    <a href="/demo/vercel.com" style="color:#b0b0b0;text-decoration:none;font-size:14px">Demo</a>
    <a href="/#pricing" style="color:#b0b0b0;text-decoration:none;font-size:14px">Pricing</a>
    <a href="/login" style="background:#a855f7;color:#fff;padding:8px 18px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">Sign In</a>
  </div>
</nav>
<div class="container">
<h1>StackSight vs <span>Wappalyzer</span></h1>
<p class="subtitle">Wappalyzer pioneered browser-based tech detection. StackSight extends that with real-time hiring signals and a developer-first API at a fraction of the cost.</p>

<h2>Pricing</h2>
<table class="comparison-table">
<tr><th>Plan</th><th>StackSight</th><th>Wappalyzer</th></tr>
<tr><td>Free tier</td><td class="yes winner">25 lookups/month</td><td class="partial">50 lookups/month (no API)</td></tr>
<tr><td>Starter</td><td class="yes winner">$12/mo -- 500 req</td><td class="no">Not available</td></tr>
<tr><td>Pro</td><td class="yes winner">$49/mo -- 5,000 req</td><td class="no">$250/mo -- 10K results</td></tr>
<tr><td>Business</td><td class="yes winner">$199/mo -- 50,000 req</td><td class="no">$450/mo</td></tr>
<tr><td>Credits expire</td><td class="yes winner">Never</td><td class="no">60 days</td></tr>
</table>

<h2>Features</h2>
<table class="comparison-table">
<tr><th>Feature</th><th>StackSight</th><th>Wappalyzer</th></tr>
<tr><td>Tech stack detection</td><td class="yes">--</td><td class="yes">-- (extensive DB)</td></tr>
<tr><td>Hiring intent signals</td><td class="yes winner">-- Real-time job data</td><td class="no">--</td></tr>
<tr><td>Browser extension</td><td class="no">API only</td><td class="yes">--</td></tr>
<tr><td>REST API</td><td class="yes winner">-- Simple JSON</td><td class="yes">--</td></tr>
<tr><td>Bulk enrichment</td><td class="yes winner">-- 50 domains/req</td><td class="yes">--</td></tr>
<tr><td>Real-time scrape</td><td class="yes winner">-- Days-fresh</td><td class="partial">Varies</td></tr>
<tr><td>No credit expiry</td><td class="yes winner">--</td><td class="no">-- 60-day expiry</td></tr>
</table>

<div class="verdict">
<strong>Bottom line:</strong> Wappalyzer is excellent for pure tech profiling and has a strong browser extension. StackSight wins on hiring intent, price per lookup, and credits that never expire.
</div>

<div class="cta-box">
<h2>Try StackSight Free</h2>
<p>25 free lookups. No credit card. Hiring intent + tech stack in one call.</p>
<a href="/#signup" class="btn">Get Free API Key</a>
</div>
</div>
<footer>-- 2026 StackSight -- <a href="/vs/builtwith" style="color:#a855f7">vs BuiltWith</a> -- <a href="/vs/theirstack" style="color:#a855f7">vs TheirStack</a></footer>
</body></html>""")

@app.get("/vs/theirstack", response_class=HTMLResponse)
async def vs_theirstack():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/png" href="/favicon.png"><link rel="shortcut icon" href="/favicon.ico">
<title>StackSight vs TheirStack (2026) - API, Pricing & Features Compared</title>
<meta name="description" content="StackSight vs TheirStack: compare hiring intent signals, tech detection, API pricing and data freshness. StackSight starts free, TheirStack from $59/mo.">
<meta property="og:title" content="StackSight vs TheirStack (2026)">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0015;color:#e0e0e0;line-height:1.6}}
.container{{max-width:900px;margin:0 auto;padding:60px 24px}}
h1{{font-size:42px;font-weight:800;margin-bottom:16px;letter-spacing:-1px}}
h1 span{{color:#a855f7}}
.subtitle{{font-size:18px;color:#b0b0b0;margin-bottom:48px;max-width:640px}}
h2{{font-size:24px;font-weight:700;margin:48px 0 16px;color:#fff}}
.comparison-table{{width:100%;border-collapse:collapse;margin-bottom:48px}}
.comparison-table th{{background:#1a0a2e;padding:14px 20px;text-align:left;font-size:13px;font-weight:600;color:#a855f7;text-transform:uppercase;letter-spacing:0.5px}}
.comparison-table td{{padding:14px 20px;border-bottom:1px solid #1a0a2e;font-size:14px}}
.comparison-table tr:hover td{{background:#0d0020}}
.yes{{color:#4ade80;font-weight:600}}
.no{{color:#f87171}}
.partial{{color:#fb923c}}
.winner{{background:#1a0a2e!important}}
.cta-box{{background:linear-gradient(135deg,#1a0a2e,#0d0020);border:1px solid #3b1a6e;border-radius:16px;padding:40px;text-align:center;margin-top:48px}}
.cta-box h2{{margin-top:0}}
.cta-box p{{color:#b0b0b0;margin-bottom:24px}}
.btn{{display:inline-block;background:#a855f7;color:#fff;padding:14px 32px;border-radius:10px;text-decoration:none;font-weight:700;font-size:16px}}
.btn:hover{{background:#9333ea}}
.verdict{{background:#0d0020;border-left:3px solid #a855f7;padding:20px 24px;border-radius:0 8px 8px 0;margin:24px 0;font-size:15px;color:#d0d0d0}}
footer{{text-align:center;padding:40px;color:#888;font-size:13px;border-top:1px solid #1a0a2e;margin-top:60px}}
</style>
</head>
<body>
<nav style="background:#0a0015;padding:16px 32px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #1a0a2e">
  <a href="/" style="color:#a855f7;font-weight:800;font-size:18px;text-decoration:none">StackSight</a>
  <div style="display:flex;gap:24px;align-items:center">
    <a href="/docs" style="color:#b0b0b0;text-decoration:none;font-size:14px">Docs</a>
    <a href="/demo/vercel.com" style="color:#b0b0b0;text-decoration:none;font-size:14px">Demo</a>
    <a href="/#pricing" style="color:#b0b0b0;text-decoration:none;font-size:14px">Pricing</a>
    <a href="/login" style="background:#a855f7;color:#fff;padding:8px 18px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">Sign In</a>
  </div>
</nav>
<div class="container">
<h1>StackSight vs <span>TheirStack</span></h1>
<p class="subtitle">TheirStack mines 172M+ job postings for hiring signals and technographics. StackSight takes a simpler approach -- real-time domain enrichment via REST API, starting free.</p>

<h2>Pricing</h2>
<table class="comparison-table">
<tr><th>Plan</th><th>StackSight</th><th>TheirStack</th></tr>
<tr><td>Free tier</td><td class="yes winner">25 lookups/month</td><td class="partial">50 company + 200 API credits</td></tr>
<tr><td>Starter</td><td class="yes winner">$12/mo -- 500 req</td><td class="no">Not available</td></tr>
<tr><td>Entry paid</td><td class="yes winner">$49/mo -- 5,000 req</td><td class="partial">$59/mo (limited)</td></tr>
<tr><td>Pro</td><td class="yes winner">$49/mo -- 5,000 req</td><td class="no">$169/mo -- 10K credits</td></tr>
<tr><td>API pricing model</td><td class="yes winner">Per request, flat</td><td class="no">Credit-based (varies by type)</td></tr>
</table>

<h2>Features</h2>
<table class="comparison-table">
<tr><th>Feature</th><th>StackSight</th><th>TheirStack</th></tr>
<tr><td>Hiring intent signals</td><td class="yes">-- Real-time</td><td class="yes">-- 172M+ job postings DB</td></tr>
<tr><td>Tech stack detection</td><td class="yes">--</td><td class="yes">-- 32K+ technologies</td></tr>
<tr><td>Simple REST API</td><td class="yes winner">-- One endpoint</td><td class="partial">-- More complex</td></tr>
<tr><td>Bulk enrichment</td><td class="yes winner">-- 50 domains/req</td><td class="yes">--</td></tr>
<tr><td>Historical job data</td><td class="no">Live only</td><td class="yes">-- Deep archive</td></tr>
<tr><td>Predictable pricing</td><td class="yes winner">-- Flat per request</td><td class="no">Credit cost varies by record type</td></tr>
</table>

<div class="verdict">
<strong>Bottom line:</strong> TheirStack has a far deeper job posting archive and more technographic breadth. StackSight is the better fit if you want a dead-simple API, predictable pricing, and don't need to query historical job data.
</div>

<div class="cta-box">
<h2>Try StackSight Free</h2>
<p>25 free lookups. No credit card. One API call returns hiring intent + tech stack.</p>
<a href="/#signup" class="btn">Get Free API Key</a>
</div>
</div>
<footer>-- 2026 StackSight -- <a href="/vs/builtwith" style="color:#a855f7">vs BuiltWith</a> -- <a href="/vs/wappalyzer" style="color:#a855f7">vs Wappalyzer</a></footer>
</body></html>""")

@app.get("/register", response_class=HTMLResponse)
async def register_redirect():
    return RedirectResponse("/login", status_code=301)

@app.get("/signup", response_class=HTMLResponse)
async def signup_redirect():
    return RedirectResponse("/login", status_code=301)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_session_email(request):
        next_url = request.query_params.get("next", "/dashboard")
        return RedirectResponse(next_url if next_url.startswith("/") else "/dashboard")
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Sign in to your StackSight account to access your API key, usage stats, and dashboard.">
<meta name="robots" content="noindex">
<link rel="icon" type="image/png" href="/favicon.png"><link rel="shortcut icon" href="/favicon.ico">
<title>Sign In - StackSight</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0a0a;color:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;background-image:radial-gradient(ellipse at 50% 0%,rgba(124,58,237,0.15) 0%,transparent 60%)}
.wrap{width:100%;max-width:420px}
.logo{font-size:22px;font-weight:800;color:#7c3aed;text-decoration:none;display:block;text-align:center;margin-bottom:48px}
.card{background:#111;border:1.5px solid #1f1f1f;border-radius:20px;padding:40px 36px}
h1{font-size:26px;font-weight:700;margin-bottom:8px;text-align:center}
.sub{color:#6b7280;font-size:14px;text-align:center;margin-bottom:32px;line-height:1.5}
.sub span{color:#a78bfa}
label{display:block;font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:#6b7280;margin-bottom:8px}
input[type=email]{width:100%;background:#0a0a0a;border:1.5px solid #2a2a2a;border-radius:10px;padding:14px 16px;color:#fff;font-size:15px;outline:none;transition:border-color .2s}input[type=email]:focus-visible{outline:2px solid #a855f7;outline-offset:2px;border-color:#a855f7}
input[type=email]:focus{border-color:#7c3aed}
input[type=email]::placeholder{color:#374151}
.btn{width:100%;margin-top:16px;background:linear-gradient(135deg,#7c3aed,#a855f7);border:none;border-radius:10px;padding:14px;color:#fff;font-size:15px;font-weight:600;cursor:pointer;transition:opacity .2s}
.btn:hover{opacity:.9}.btn:disabled{opacity:.5;cursor:not-allowed}
.msg{margin-top:20px;padding:14px 16px;border-radius:10px;font-size:14px;text-align:center;display:none}
.msg.success{background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.2);color:#22c55e}
.msg.error{background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.2);color:#ef4444}
.divider{display:flex;align-items:center;gap:12px;margin:28px 0}
.divider::before,.divider::after{content:'';flex:1;height:1px;background:#1f1f1f}
.divider span{color:#4b5563;font-size:12px}
.footer{text-align:center;font-size:13px;color:#4b5563}
.footer a{color:#7c3aed;text-decoration:none}
.demo-note{background:rgba(124,58,237,0.08);border:1px solid rgba(124,58,237,0.2);border-radius:10px;padding:12px 16px;font-size:13px;color:#a78bfa;text-align:center;margin-bottom:24px;display:none}
</style>
</head>
<body>
<div class="wrap">
  <a href="/" class="logo">StackSight</a>
  <div class="card">
    <div class="demo-note" id="demo-note">Sign in to run your free domain analysis</div>
    <h1>Welcome back</h1>
    <p class="sub">Enter your email and we'll send a magic link.<br><span>No password needed.</span></p>
    <label for="email">Email address</label>
    <input type="email" id="email" placeholder="you@company.com" autocomplete="email">
    <button class="btn" id="submit-btn" onclick="doLogin()">Send magic link</button>
    <div class="msg" id="msg"></div>
    <div class="divider"><span>Don't have an account?</span></div>
    <a href="/#signup" style="display:block;text-align:center;background:#a855f7;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px;margin-bottom:16px">Create free account &rarr;</a>
    <div class="footer"><a href="/#pricing">View pricing</a> &nbsp;&middot;&nbsp; <a href="/docs">API docs</a></div>
  </div>
</div>
<script>
(function(){
  var p = new URLSearchParams(window.location.search);
  if (p.get('next')) document.getElementById('demo-note').style.display = 'block';
})();
async function doLogin() {
  var email = document.getElementById('email').value.trim();
  var btn = document.getElementById('submit-btn');
  var msg = document.getElementById('msg');
  if (!email || !email.includes('@')) {
    msg.className = 'msg error'; msg.style.display = 'block';
    msg.textContent = 'Please enter a valid email.'; return;
  }
  btn.disabled = true; btn.textContent = 'Sending...'; msg.style.display = 'none';
  var next = new URLSearchParams(window.location.search).get('next') || '';
  try {
    var r = await fetch('/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email,next})});
    var d = await r.json();
    if (r.ok) { msg.className='msg success'; msg.style.display='block'; msg.textContent='Magic link sent! Check your inbox.'; btn.textContent='Link sent!'; }
    else { msg.className='msg error'; msg.style.display='block'; msg.textContent=d.detail||'Something went wrong.'; btn.disabled=false; btn.textContent='Send magic link'; }
  } catch(e) {
    msg.className='msg error'; msg.style.display='block'; msg.textContent='Request failed. Try again.'; btn.disabled=false; btn.textContent='Send magic link';
  }
}
document.addEventListener('DOMContentLoaded',function(){
  document.getElementById('email').addEventListener('keydown',function(e){if(e.key==='Enter')doLogin();});
});
</script>
</body></html>""")


@app.post("/login")
async def login_post(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM api_keys WHERE email=%s AND active=TRUE", (email,))
    has_key = cur.fetchone()
    if not has_key:
        # Also accept users who signed up but haven't verified yet
        cur.execute("SELECT 1 FROM pending_signups WHERE email=%s", (email,))
        has_pending = cur.fetchone()
        if not has_pending:
            cur.close(); release_db(conn)
            raise HTTPException(status_code=404, detail="No account found for this email. Sign up first.")
    token = secrets.token_urlsafe(48)
    expires = datetime.utcnow() + timedelta(minutes=15)
    cur.execute(
        "INSERT INTO magic_links (token, email, expires_at) VALUES (%s, %s, %s)",
        (token, email, expires)
    )
    conn.commit()
    cur.close(); release_db(conn)
    next_url = body.get("next", "")
    safe_next = next_url if next_url.startswith("/") else ""
    background_tasks.add_task(send_magic_link_email, email, token, safe_next)
    return {"message": "Login link sent"}


@app.get("/auth")
async def auth(token: str, request: Request, next: str = "/dashboard"):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT email, used, expires_at FROM magic_links WHERE token=%s", (token,))
    row = cur.fetchone()
    if not row:
        cur.close(); release_db(conn)
        return HTMLResponse("<h2 style='font-family:sans-serif;color:#ef4444;padding:40px'>Invalid or expired login link. <a href='/login'>Request a new one</a>.</h2>", status_code=400)
    email, used, expires_at = row
    if used or datetime.utcnow() > expires_at:
        cur.close(); release_db(conn)
        return HTMLResponse("<h2 style='font-family:sans-serif;color:#ef4444;padding:40px'>This login link has expired or already been used. <a href='/login'>Request a new one</a>.</h2>", status_code=400)
    cur.execute("UPDATE magic_links SET used=TRUE WHERE token=%s", (token,))
    conn.commit()
    cur.close(); release_db(conn)
    safe_next = next if next.startswith("/") else "/dashboard"
    response = RedirectResponse(safe_next, status_code=302)
    create_session(email, response)
    return response


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("ss_session")
    if token:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE sessions SET active=FALSE WHERE session_token=%s", (token,))
        conn.commit()
        release_db(conn)
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie("ss_session")
    return response


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, ss_session: str = Cookie(default=None)):
    if not ss_session:
        return RedirectResponse(url="/login", status_code=302)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT email FROM sessions WHERE session_token=%s AND active=TRUE AND expires_at > NOW()", (ss_session,))
    row = cur.fetchone()
    if not row:
        release_db(conn)
        return RedirectResponse(url="/login", status_code=302)
    email = row[0]
    cur.execute("SELECT COALESCE(api_key, \"key\"), plan, requests_used, requests_limit, created_at, usage_reset_at, stripe_customer_id FROM api_keys WHERE email=%s AND active=TRUE", (email,))
    key_row = cur.fetchone()
    release_db(conn)
    if not key_row or not key_row[0]:
        return HTMLResponse("""<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Dashboard - StackSight</title><style>*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#fff;min-height:100vh;display:flex;align-items:center;justify-content:center}.card{background:#111;border:1px solid #222;border-radius:16px;padding:48px;text-align:center;max-width:480px;width:90%}h2{font-size:1.5rem;margin-bottom:12px}p{color:#888;margin-bottom:24px}a{display:inline-block;background:#2563eb;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:600}</style></head><body><div class='card'><h2>No API Key Found</h2><p>Contact support or generate a new key.</p><a href='/'>Go Home</a></div></body></html>""")
    api_key, plan, requests_used, requests_limit, created_at, usage_reset_at, stripe_customer_id = key_row
    plan_color = {"free": "#6b7280", "starter": "#2563eb", "pro": "#7c3aed", "business": "#059669"}.get(plan, "#6b7280")
    plan_limits = {"free": 25, "starter": 500, "pro": 5000, "business": 50000}
    monthly_limit = plan_limits.get(plan, 25)
    usage_pct = min(100, round((requests_used / monthly_limit) * 100)) if monthly_limit > 0 else 0
    bar_color = "#ef4444" if usage_pct >= 90 else "#f59e0b" if usage_pct >= 70 else "#22c55e"
    upgrade_html = ""
    if plan == "free":
        upgrade_html = "<a href='/checkout/starter' class='upgrade-btn'>Upgrade to Starter &mdash; $12/mo</a>"
    elif plan == "starter":
        upgrade_html = "<a href='/choose/pro' class='upgrade-btn'>Upgrade to Pro &mdash; $49/mo</a>"
    elif plan == "pro":
        upgrade_html = "<a href='/choose/business' class='upgrade-btn'>Upgrade to Business &mdash; $199/mo</a>"
    created_str = created_at.strftime("%B %d, %Y") if created_at else "N/A"
    from datetime import datetime, timezone, timedelta
    now_utc = datetime.now(timezone.utc)
    if usage_reset_at:
        reset_base = usage_reset_at.replace(tzinfo=timezone.utc) if usage_reset_at.tzinfo is None else usage_reset_at
        next_reset = reset_base + timedelta(days=30)
    elif created_at:
        reset_base = created_at.replace(tzinfo=timezone.utc) if created_at.tzinfo is None else created_at
        next_reset = reset_base + timedelta(days=30)
    else:
        next_reset = now_utc + timedelta(days=30)
    days_remaining = max(0, (next_reset - now_utc).days)
    html = f"""<!DOCTYPE html>
<html lang='en'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<meta name='robots' content='noindex,nofollow'>
<title>Dashboard - StackSight</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#09090b;color:#fafafa;min-height:100vh}}
nav{{background:#111113;border-bottom:1px solid #1f1f23;padding:0 32px;height:60px;display:flex;align-items:center;justify-content:space-between}}
.logo{{font-size:1.25rem;font-weight:700;color:#fff;text-decoration:none;letter-spacing:-0.5px}}
.logo span{{color:#2563eb}}
.nav-right{{display:flex;align-items:center;gap:16px}}
.nav-email{{color:#71717a;font-size:0.875rem}}
.logout-btn{{color:#71717a;font-size:0.875rem;text-decoration:none;padding:6px 12px;border:1px solid #27272a;border-radius:6px;transition:color 0.2s,border-color 0.2s}}
.logout-btn:hover{{color:#fff;border-color:#52525b}}
.container{{max-width:900px;margin:0 auto;padding:40px 24px}}
h1{{font-size:1.875rem;font-weight:700;margin-bottom:4px}}
.subtitle{{color:#71717a;margin-bottom:32px;font-size:0.9375rem}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px;margin-bottom:24px}}
.card{{background:#111113;border:1px solid #1f1f23;border-radius:12px;padding:24px}}
.card-label{{font-size:0.75rem;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;color:#71717a;margin-bottom:8px}}
.card-value{{font-size:1.5rem;font-weight:700;color:#fff}}
.card-sub{{font-size:0.8125rem;color:#52525b;margin-top:4px}}
.plan-badge{{display:inline-flex;align-items:center;gap:6px;background:{plan_color}22;border:1px solid {plan_color}44;color:{plan_color};padding:4px 12px;border-radius:20px;font-size:0.875rem;font-weight:600;text-transform:capitalize}}
.usage-card{{background:#111113;border:1px solid #1f1f23;border-radius:12px;padding:24px;margin-bottom:24px}}
.usage-header{{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:16px}}
.usage-title{{font-size:1rem;font-weight:600}}
.usage-count{{font-size:0.875rem;color:#71717a}}
.bar-bg{{background:#1f1f23;border-radius:99px;height:8px;overflow:hidden}}
.bar-fill{{height:8px;border-radius:99px;background:{bar_color};width:{usage_pct}%;transition:width 0.6s ease}}
.usage-footer{{display:flex;justify-content:space-between;margin-top:8px;font-size:0.75rem;color:#52525b}}
.key-card{{background:#111113;border:1px solid #1f1f23;border-radius:12px;padding:24px;margin-bottom:24px}}
.key-label{{font-size:0.75rem;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;color:#71717a;margin-bottom:12px}}
.key-row{{display:flex;gap:8px;align-items:center}}
.key-input{{flex:1;background:#0d0d0f;border:1px solid #27272a;border-radius:8px;padding:10px 14px;color:#a1a1aa;font-family:'Courier New',monospace;font-size:0.875rem;outline:none}}.key-input:focus-visible{{outline:2px solid #a855f7;outline-offset:2px}}
.copy-btn{{background:#1f1f23;border:1px solid #27272a;border-radius:8px;padding:10px 16px;color:#a1a1aa;cursor:pointer;font-size:0.875rem;white-space:nowrap;transition:all 0.2s}}
.copy-btn:hover{{background:#27272a;color:#fff}}
.code-card{{background:#111113;border:1px solid #1f1f23;border-radius:12px;padding:24px;margin-bottom:24px}}
.code-card h3{{font-size:1rem;font-weight:600;margin-bottom:16px}}
.code-tabs{{display:flex;gap:2px;background:#0d0d0f;border-radius:8px;padding:4px;margin-bottom:16px;width:fit-content}}
.code-tab{{padding:6px 14px;border-radius:6px;font-size:0.8125rem;cursor:pointer;color:#71717a;border:none;background:transparent;transition:all 0.2s}}
.code-tab.active{{background:#1f1f23;color:#fff}}
pre{{background:#0d0d0f;border:1px solid #1f1f23;border-radius:8px;padding:16px;overflow-x:auto;font-size:0.8125rem;line-height:1.6;color:#a1a1aa}}
code .key{{color:#22c55e}}
code .str{{color:#f59e0b}}
code .kw{{color:#60a5fa}}
.upgrade-section{{text-align:center;padding:32px;background:#111113;border:1px solid #1f1f23;border-radius:12px;margin-bottom:24px}}
.upgrade-section p{{color:#71717a;margin-bottom:16px;font-size:0.9375rem}}
.upgrade-btn{{display:inline-block;background:#2563eb;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:0.9375rem;transition:background 0.2s}}
.upgrade-btn:hover{{background:#1d4ed8}}
.billing-section{{text-align:center;padding:16px;margin-bottom:24px}}
.billing-btn{{display:inline-block;background:transparent;color:#71717a;border:1px solid #333;padding:10px 20px;border-radius:8px;text-decoration:none;font-size:0.875rem;transition:all 0.2s}}
.billing-btn:hover{{color:#fff;border-color:#555}}
</style>
</head>
<body>
<nav>
  <a class='logo' href='/'>Stack<span>Sight</span></a>
  <div class='nav-right'>
    <span class='nav-email'>{email}</span>
    <a href='/logout' class='logout-btn'>Sign out</a>
  </div>
</nav>
<div class='container'>
  <h1>Dashboard</h1>
  <p class='subtitle'>Manage your API key and monitor usage</p>
  <div class='grid'>
    <div class='card'>
      <div class='card-label'>Current Plan</div>
      <div style='margin-top:4px'><span class='plan-badge'>{plan}</span></div>
      <div class='card-sub'>Member since {created_str}</div>
    </div>
    <div class='card'>
      <div class='card-label'>Requests This Month</div>
      <div class='card-value'>{requests_used:,}</div>
      <div class='card-sub'>of {monthly_limit:,} included</div>
    </div>
    <div class='card'>
      <div class='card-label'>Monthly Reset</div>
      <div class='card-value' id='reset-countdown'>--</div>
      <div class='card-sub'>days remaining</div>
    </div>
  </div>
  <div class='usage-card'>
    <div class='usage-header'>
      <span class='usage-title'>Monthly Usage</span>
      <span class='usage-count'>{requests_used:,} / {monthly_limit:,} requests</span>
    </div>
    <div class='bar-bg'><div class='bar-fill'></div></div>
    <div class='usage-footer'><span>0</span><span>{usage_pct}% used</span><span>{monthly_limit:,}</span></div>
  </div>
  <div class='key-card'>
    <div class='key-label'>Your API Key</div>
    <div class='key-row'>
      <input class='key-input' id='apikey' type='password' value='{api_key}' readonly>
      <button class='copy-btn' id='toggleBtn' onclick='toggleKey()'>Show</button>
      <button class='copy-btn' id='copyBtn' onclick='copyKey()'>Copy</button>
    </div>
  </div>
  <div class='code-card'>
    <h3>Quick Start</h3>
    <div class='code-tabs'>
      <button class='code-tab active' onclick='showTab(this,"curl")'>cURL</button>
      <button class='code-tab' onclick='showTab(this,"python")'>Python</button>
      <button class='code-tab' onclick='showTab(this,"js")'>JavaScript</button>
    </div>
    <div id='tab-curl'>
<pre><code>curl "https://stacksight.org/scrape?domain=stripe.com" \
  -H "<span class='kw'>X-API-Key</span>: <span class='key'>YOUR_KEY</span>"</code></pre>
    </div>
    <div id='tab-python' style='display:none'>
<pre><code><span class='kw'>import</span> requests

resp = requests.get(
    <span class='str'>"https://stacksight.org/scrape"</span>,
    params={{<span class='str'>"domain"</span>: <span class='str'>"stripe.com"</span>}},
    headers={{<span class='str'>"X-API-Key"</span>: <span class='str'>"<span class='key'>YOUR_KEY</span>"</span>}}
)
<span class='kw'>print</span>(resp.json())</code></pre>
    </div>
    <div id='tab-js' style='display:none'>
<pre><code><span class='kw'>const</span> res = <span class='kw'>await</span> fetch(
  <span class='str'>"https://stacksight.org/scrape?domain=stripe.com"</span>,
  {{headers: {{<span class='str'>"X-API-Key"</span>: <span class='str'>"<span class='key'>YOUR_KEY</span>"</span>}}}}
);
<span class='kw'>const</span> data = <span class='kw'>await</span> res.json();</code></pre>
    </div>
  </div>
  {upgrade_html and f"<div class='upgrade-section'><p>Unlock more requests and higher rate limits</p>{upgrade_html}</div>" or ""}
  {f"<div class='billing-section'><a href='/billing-portal' class='billing-btn'>Manage Billing &amp; Cancel</a></div>" if stripe_customer_id else ""}
  <div style='text-align:center;padding:24px 0 8px;font-size:13px;color:#444'>Experiencing issues? <a href='mailto:support@stacksight.org' style='color:#71717a;text-decoration:underline'>support@stacksight.org</a></div>
</div>
<script>
function toggleKey() {{
  const inp = document.getElementById('apikey');
  const btn = document.getElementById('toggleBtn');
  if (inp.type === 'password') {{ inp.type = 'text'; btn.textContent = 'Hide'; }}
  else {{ inp.type = 'password'; btn.textContent = 'Show'; }}
}}
function copyKey() {{
  const val = document.getElementById('apikey').value;
  navigator.clipboard.writeText(val).then(() => {{
    const btn = document.getElementById('copyBtn');
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy', 2000);
  }});
}}
function showTab(el, name) {{
  document.querySelectorAll('.code-tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  ['curl','python','js'].forEach(t => {{
    document.getElementById('tab-'+t).style.display = t === name ? '' : 'none';
  }});
}}
document.getElementById('reset-countdown').textContent = '{days_remaining}';
// Replace YOUR_KEY placeholders with masked key
document.querySelectorAll('code').forEach(el => {{
  el.innerHTML = el.innerHTML.replace(/YOUR_KEY/g, '{api_key[:8]}...');
}});
</script>
</body>
</html>"""
    return HTMLResponse(html)

@app.post("/signup")
async def signup(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM api_keys WHERE email=%s AND plan='free'", (email,))
    if cur.fetchone():
        cur.close(); release_db(conn)
        raise HTTPException(status_code=400, detail="An API key already exists for this email. Sign in to view it.")
    cur.execute("SELECT 1 FROM pending_signups WHERE email=%s AND used=FALSE", (email,))
    if cur.fetchone():
        cur.close(); release_db(conn)
        raise HTTPException(status_code=400, detail="Verification email already sent. Please check your inbox.")
    token = secrets.token_urlsafe(32)
    cur.execute(
        "INSERT INTO pending_signups (email, token) VALUES (%s, %s) ON CONFLICT (email) DO UPDATE SET token=%s, used=FALSE, created_at=NOW()",
        (email, token, token)
    )
    conn.commit()
    cur.close(); release_db(conn)
    background_tasks.add_task(send_verification_email, email, token)
    return {"message": "Verification email sent"}


@app.get("/verify-email", response_class=HTMLResponse)
async def verify_email(token: str, background_tasks: BackgroundTasks):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT email, used FROM pending_signups WHERE token=%s", (token,))
    row = cur.fetchone()
    if not row:
        cur.close(); release_db(conn)
        return HTMLResponse("<!DOCTYPE html><html><head><meta name='robots' content='noindex,nofollow'></head><body><h2 style='font-family:sans-serif;padding:40px'>Invalid or expired link.</h2></body></html>", status_code=400)
    email, used = row
    if used:
        cur.close(); release_db(conn)
        return HTMLResponse("<h2 style='font-family:sans-serif;padding:40px'>This link has already been used. <a href='/login'>Sign in here</a>.</h2>", status_code=400)
    cur.close(); release_db(conn)
    # Provision key first — only mark token used if it succeeds
    try:
        provision_api_key(email, "free")
    except Exception as e:
        print(f"[VERIFY] Provisioning error for {email}: {e}")
        return HTMLResponse("<h2 style='font-family:sans-serif;padding:40px;color:#ef4444'>Something went wrong setting up your account. Please try signing in or contact support@stacksight.org<br><br><a href='/login' style='color:#a855f7'>Try signing in</a></h2>", status_code=500)
    # Mark token used only after successful provisioning
    conn2 = get_db()
    cur2 = conn2.cursor()
    cur2.execute("UPDATE pending_signups SET used=TRUE WHERE token=%s", (token,))
    conn2.commit()
    cur2.close(); release_db(conn2)
    # Log them straight in
    response = RedirectResponse("/dashboard", status_code=302)
    create_session(email, response)
    return response


# 
# ROUTES  API
# 

@app.get("/pricing", response_class=HTMLResponse)
async def pricing_redirect():
    return RedirectResponse("/#pricing", status_code=301)

@app.get("/demo/{domain}", response_class=HTMLResponse)
async def demo(domain: str, request: Request):
    # Demo is public but rate-limited for anonymous users
    ss_token = request.cookies.get("ss_session")
    email = None
    if not ss_token:
        # Anonymous: allow 3 demo lookups per IP per hour
        ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "anon").split(",")[0].strip()
        anon_key = f"demo_anon:{ip}"
        count = redis_client.get(anon_key)
        if count and int(count) >= 3:
            return RedirectResponse(f"/login?next=/demo/{domain}", status_code=302)
        pipe = redis_client.pipeline()
        pipe.incr(anon_key)
        pipe.expire(anon_key, 3600)
        pipe.execute()
    if ss_token:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT email FROM sessions WHERE session_token=%s AND active=TRUE AND expires_at > NOW()", (ss_token,))
            row = cur.fetchone()
            release_db(conn)
            if row:
                email = row[0]
        except Exception:
            pass

    user_api_key = None
    if email:
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT COALESCE(api_key, \"key\") FROM api_keys WHERE email=%s AND active=TRUE LIMIT 1", (email,))
            row = cur.fetchone()
            release_db(conn)
            if row:
                user_api_key = row[0]
        except Exception:
            pass

    data, src, scrape_error = {}, "error", None
    try:
        domain_clean = validate_domain(domain)
        cache_key = f"domain:{domain_clean}"
        cached = redis_client.get(cache_key)
        if cached:
            data = json.loads(cached)
            src = "cache"
        else:
            raw_text, url, status = await scrape_page(domain_clean)
            data = extract_with_openai(raw_text)
            if not data.get("detected_tech_stack"):
                all_roles = data.get("engineering_roles", []) + data.get("sales_roles", []) + data.get("other_roles", [])
                data["detected_tech_stack"] = infer_tech_from_roles(all_roles)
            redis_client.setex(cache_key, 604800, json.dumps(data))
            src = "live"
        # Count every lookup against the user's quota (cached or live)
        if user_api_key:
            increment_usage(user_api_key)
    except HTTPException as he:
        scrape_error = he.detail
    except Exception as _ex:
        scrape_error = "Could not scrape this domain. It may be blocking automated requests."

    if scrape_error:
        return HTMLResponse(f"""<!DOCTYPE html>
<html lang='en'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<link rel='icon' type='image/png' href='/favicon.png'>
<meta name='robots' content='noindex,nofollow'>
<title>Demo: {domain} - StackSight</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#09090b;color:#fafafa;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px;text-align:center}}
.card{{background:#111;border:1px solid #1f1f23;border-radius:16px;padding:40px;max-width:480px;width:100%}}
h2{{font-size:20px;font-weight:700;margin-bottom:12px;color:#fff}}
p{{color:#888;font-size:14px;line-height:1.6;margin-bottom:24px}}
a{{display:inline-block;background:#a855f7;color:#fff;padding:11px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;margin:4px}}
.sec{{background:transparent;border:1px solid #333;color:#ccc}}</style></head>
<body><div class='card'>
<div style='font-size:40px;margin-bottom:16px'>⚠️</div>
<h2>Couldn't scrape {domain}</h2>
<p>{scrape_error}<br><br>Try a different domain, or use the API directly with your own key.</p>
<a href='/demo/vercel.com'>Try vercel.com</a>
<a href='/#signup' class='sec'>Get API key</a>
</div></body></html>""", status_code=200)

    eng_roles = data.get("engineering_roles", [])
    sales_roles = data.get("sales_roles", [])
    other_roles = data.get("other_roles", [])
    jobs = [{"title": r, "department": "Engineering"} for r in eng_roles] + [{"title": r, "department": "Sales"} for r in sales_roles] + [{"title": r, "department": "Other"} for r in other_roles]
    tech = data.get("detected_tech_stack", [])
    is_hiring = data.get("is_hiring")
    if is_hiring is True:
        hiring_badge = "<span style='background:#052e16;color:#22c55e;border:1px solid #166534;padding:4px 12px;border-radius:20px;font-size:.875rem;font-weight:600'>Actively Hiring</span>"
    elif is_hiring is False:
        hiring_badge = "<span style='background:#1f0a0a;color:#ef4444;border:1px solid #991b1b;padding:4px 12px;border-radius:20px;font-size:.875rem;font-weight:600'>Not Hiring</span>"
    else:
        hiring_badge = "<span style='background:#1c1c1e;color:#71717a;border:1px solid #27272a;padding:4px 12px;border-radius:20px;font-size:.875rem;font-weight:600'>Unknown</span>"

    jobs_html = "".join(f"<div style='padding:12px 0;border-bottom:1px solid #1f1f23'><div style='font-weight:600'>{j.get('title','')}</div><div style='color:#71717a;font-size:.875rem'>{j.get('department','')}</div></div>" for j in jobs) or "<p style='color:#52525b'>No open jobs found.</p>"
    tech_html = "".join(f"<span style='background:#1f1f23;border:1px solid #27272a;padding:4px 10px;border-radius:6px;font-size:.8125rem'>{t}</span>" for t in tech[:20]) or "<span style='color:#52525b'>None detected</span>"

    html = f"""<!DOCTYPE html>
<html lang='en'>
<head>
<meta charset='UTF-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<meta name='robots' content='noindex,nofollow'>
<title>Demo: {domain} - StackSight</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#09090b;color:#fafafa;min-height:100vh}}
nav{{background:#111113;border-bottom:1px solid #1f1f23;padding:0 32px;height:60px;display:flex;align-items:center;justify-content:space-between}}
.logo{{font-size:1.25rem;font-weight:700;color:#fff;text-decoration:none}}
.logo span{{color:#2563eb}}
.container{{max-width:860px;margin:0 auto;padding:40px 24px}}
.search-bar{{display:flex;gap:8px;margin-bottom:32px}}
.search-bar input{{flex:1;background:#111113;border:1px solid #27272a;border-radius:8px;padding:10px 16px;color:#fff;font-size:1rem;outline:none}}.search-bar input:focus-visible{{outline:2px solid #a855f7;outline-offset:2px}}
.search-bar input:focus{{border-color:#2563eb}}
.search-bar button{{background:#2563eb;color:#fff;border:none;border-radius:8px;padding:10px 20px;cursor:pointer;font-weight:600}}
.card{{background:#111113;border:1px solid #1f1f23;border-radius:12px;padding:24px;margin-bottom:20px}}
.card h2{{font-size:1rem;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:#71717a;margin-bottom:16px}}
.meta{{display:flex;align-items:center;gap:12px;margin-bottom:8px}}
.source{{font-size:.75rem;color:#52525b}}
.tech-wrap{{display:flex;flex-wrap:wrap;gap:8px}}
</style>
</head>
<body>
<nav>
  <a class='logo' href='/'>Stack<span>Sight</span></a>
  <a href='/dashboard' style='color:#71717a;font-size:.875rem;text-decoration:none'>Dashboard</a>
</nav>
<div class='container'>
  <form class='search-bar' onsubmit='event.preventDefault();analyzeDomain()'>
    <input id='d' value='{domain}' placeholder='Enter a domain...' required>
    <button type='submit'>Analyze</button>
  </form>
  <script>
  async function analyzeDomain(){{
    var d=document.getElementById("d").value.trim();
    if(!d)return;
    var btn=document.querySelector(".search-bar button");
    btn.textContent="Checking...";btn.disabled=true;
    try{{
      var r=await fetch("/session-check",{{credentials:"include"}});
      var j=await r.json();
      if(j.logged_in){{
        btn.textContent="Analyzing...";
        window.location="/demo/"+encodeURIComponent(d);
      }}else{{
        window.location="/login?next=/demo/"+encodeURIComponent(d);
      }}
    }}catch(e){{
      window.location="/login?next=/demo/"+encodeURIComponent(d);
    }}
  }}
  </script>
  <div class='card'>
    <h2>Overview</h2>
    <div class='meta'><strong style='font-size:1.25rem'>{domain}</strong> {hiring_badge}</div>
    <div class='source'>Source: {src}</div>
  </div>
  <div class='card'>
    <h2>Tech Stack</h2>
    <div class='tech-wrap'>{tech_html}</div>
  </div>
  <div class='card'>
    <h2>Open Jobs ({len(jobs)})</h2>
    {jobs_html}
  </div>
  <div style='background:linear-gradient(135deg,#1a0a2e,#0f0520);border:1px solid #7c3aed;border-radius:16px;padding:36px;text-align:center;margin-top:8px'>
    <div style='font-size:12px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#a855f7;margin-bottom:12px'>StackSight API</div>
    <h2 style='font-size:24px;font-weight:800;color:#fff;margin-bottom:10px'>Get this data via API</h2>
    <p style='color:#aaa;font-size:15px;margin-bottom:24px;max-width:460px;margin-left:auto;margin-right:auto'>Enrich any domain instantly. Hiring signals, tech stack, open roles &mdash; structured JSON in one call. Free tier available.</p>
    <div style='background:#0a0a0a;border:1px solid #2a2a2a;border-radius:8px;padding:14px 20px;font-family:monospace;font-size:13px;color:#a855f7;text-align:left;max-width:480px;margin:0 auto 24px'>
      curl "https://stacksight.org/scrape?domain={domain}"<br>&nbsp;&nbsp;-H "X-API-Key: YOUR_KEY"
    </div>
    <a href='/#signup' style='display:inline-block;background:#a855f7;color:#fff;padding:13px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px;margin-right:10px'>Get free API key</a>
    <a href='/docs' style='display:inline-block;background:transparent;color:#ccc;padding:13px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:15px;border:1px solid #333'>View docs</a>
  </div>
</div>
</body>
</html>"""
    return HTMLResponse(html)
@app.get("/scrape")
async def scrape_redirect(request: Request, domain: str = None, x_api_key: str = Header(None)):
    """Backward-compat redirect to /v1/enrich"""
    from fastapi.responses import RedirectResponse
    url = request.url.path.replace("/scrape", "/v1/enrich")
    return RedirectResponse(url=str(request.url).replace("/scrape", "/v1/enrich"), status_code=301)

@app.post("/bulk")
async def bulk_redirect(request: Request):
    """Backward-compat redirect to /v1/bulk"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=str(request.url).replace("/bulk", "/v1/bulk"), status_code=308)

@app.get("/v1/enrich")
async def scrape(domain: str, x_api_key: str = Header(None)):
    domain = validate_domain(domain)
    api_key, plan = verify_api_key(x_api_key)
    cache_key = f"domain:{domain}"
    cached = redis_client.get(cache_key)
    if cached:
        increment_usage(api_key)
        return {"source": "cache", "data": json.loads(cached)}
    raw_text, url, status = await scrape_page(domain)
    extracted = extract_with_openai(raw_text)
    if not extracted.get("detected_tech_stack"):
        all_roles = extracted.get("engineering_roles", []) + extracted.get("sales_roles", []) + extracted.get("other_roles", [])
        extracted["detected_tech_stack"] = infer_tech_from_roles(all_roles)
    redis_client.setex(cache_key, 604800, json.dumps(extracted))
    increment_usage(api_key)
    return {"source": "live", "scrape_metadata": {"url": url, "status": status}, "data": extracted}


@app.get("/analyze/{domain}")
async def analyze(domain: str, x_api_key: str = Header(None), api_key: str = None):
    return await scrape(domain=domain, x_api_key=x_api_key or api_key)
@app.post("/v1/bulk")
async def bulk(request: Request, x_api_key: str = Header(None)):
    api_key, plan = verify_api_key(x_api_key)
    body = await request.json()
    domains = body.get("domains", [])
    if not domains:
        raise HTTPException(status_code=400, detail="Provide a list of domains")
    if len(domains) > 50:
        raise HTTPException(status_code=400, detail="Maximum 50 domains per request")

    # Check they have enough quota for all domains
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT requests_used, requests_limit FROM api_keys WHERE api_key=%s", (api_key,))
    row = cur.fetchone()
    cur.close(); release_db(conn)
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    used, limit = row
    remaining = limit - used
    if remaining < len(domains):
        raise HTTPException(status_code=429, detail=f"Not enough quota: {remaining} requests remaining, {len(domains)} needed")

    async def process_domain(domain: str):
        try:
            clean = validate_domain(domain)
        except HTTPException as e:
            return {"domain": str(domain), "source": "error", "error": e.detail}
        cache_key = f"domain:{clean}"
        cached = redis_client.get(cache_key)
        if cached:
            increment_usage(api_key)
            return {"domain": clean, "source": "cache", "data": json.loads(cached)}
        try:
            raw_text, url, status = await scrape_page(clean)
            extracted = extract_with_openai(raw_text)
            if not extracted.get("detected_tech_stack"):
                all_roles = extracted.get("engineering_roles", []) + extracted.get("sales_roles", []) + extracted.get("other_roles", [])
                extracted["detected_tech_stack"] = infer_tech_from_roles(all_roles)
            redis_client.setex(cache_key, 604800, json.dumps(extracted))
            increment_usage(api_key)
            return {"domain": clean, "source": "live", "data": extracted}
        except Exception as e:
            return {"domain": clean, "source": "error", "error": str(e)}

    results = await asyncio.gather(*[process_domain(d) for d in domains])
    return {"results": list(results), "count": len(results)}




@app.get("/usage")
async def usage(x_api_key: str = Header(None), key: str = None):
    k = x_api_key or key
    if not k:
        raise HTTPException(status_code=401, detail="Missing API key")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT plan, requests_used, requests_limit, created_at FROM api_keys WHERE api_key=%s", (k,))
    row = cur.fetchone()
    cur.close(); release_db(conn)
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    plan, used, limit, created = row
    return {"plan": plan, "requests_used": used, "requests_limit": limit, "requests_remaining": limit - used, "created_at": str(created)}


@app.get("/me")
async def me(x_api_key: str = Header(None)):
    return await usage(x_api_key=x_api_key)


@app.get("/choose/pro", response_class=HTMLResponse)
async def choose_pro():
    return HTMLResponse("""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/png" href="/favicon.png"><link rel="shortcut icon" href="/favicon.ico"><meta name="robots" content="noindex,nofollow"><title>Pro Plan - Choose Billing | StackSight</title><style>*{box-sizing:border-box;margin:0;padding:0}body{background:#0a0a0a;color:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;background-image:radial-gradient(ellipse at 50% 0%,rgba(124,58,237,0.12) 0%,transparent 60%)}.wrap{max-width:520px;width:100%;text-align:center}.logo{font-size:22px;font-weight:800;color:#7c3aed;text-decoration:none;display:inline-block;margin-bottom:48px}.tag{display:inline-block;background:rgba(124,58,237,0.15);color:#a78bfa;font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;padding:4px 14px;border-radius:20px;border:1px solid rgba(124,58,237,0.3);margin-bottom:20px}h1{font-size:30px;font-weight:700;margin-bottom:10px}.sub{color:#6b7280;font-size:15px;margin-bottom:40px}.cards{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:28px}.card{border:1.5px solid #1f1f1f;border-radius:18px;padding:28px 20px;text-decoration:none;color:#fff;display:block;transition:all .2s;position:relative;background:#111}.card:hover{border-color:#4c1d95;transform:translateY(-2px)}.card.best{border-color:#7c3aed;background:linear-gradient(135deg,#130f1e,#1a1033);box-shadow:0 0 32px rgba(124,58,237,0.2)}.badge{position:absolute;top:-13px;left:50%;transform:translateX(-50%);background:linear-gradient(90deg,#7c3aed,#a855f7);color:#fff;font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;padding:4px 14px;border-radius:20px;white-space:nowrap}.lbl{font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#6b7280;margin-bottom:14px}.card.best .lbl{color:#a78bfa}.price{font-size:40px;font-weight:800;line-height:1}.price span{font-size:15px;font-weight:400;color:#6b7280}.card.best .price span{color:#a78bfa}.psub{font-size:12px;color:#4b5563;margin-top:8px}.card.best .psub{color:#7c3aed}.save{display:inline-block;background:rgba(34,197,94,0.12);color:#22c55e;font-size:11px;font-weight:700;padding:3px 10px;border-radius:10px;margin-top:10px;border:1px solid rgba(34,197,94,0.2)}.cancel{font-size:12px;color:#4b5563;margin-top:10px}.back{color:#4b5563;font-size:13px;text-decoration:none}.back:hover{color:#9ca3af}</style></head><body><div class="wrap"><a href="/" class="logo">StackSight</a><div class="tag">Pro Plan</div><h1>Choose your billing</h1><p class="sub">Same features, same API. Pick what works for you.</p><div class="cards"><a href="/checkout/pro" class="card"><div class="lbl">Monthly</div><div class="price">$49<span>/mo</span></div><div class="psub">billed monthly</div><div class="cancel">Cancel anytime</div></a><a href="/checkout/pro_annual" class="card best"><div class="badge">BEST VALUE</div><div class="lbl">Annual</div><div class="price">$39<span>/mo</span></div><div class="psub">billed $468/yr</div><div class="save">Save 20%</div></a></div><a href="/#pricing" class="back">Back to pricing</a></div></body></html>""")


@app.get("/choose/business", response_class=HTMLResponse)
async def choose_business():
    return HTMLResponse("""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/png" href="/favicon.png"><link rel="shortcut icon" href="/favicon.ico"><meta name="robots" content="noindex,nofollow"><title>Business Plan - Choose Billing | StackSight</title><style>*{box-sizing:border-box;margin:0;padding:0}body{background:#0a0a0a;color:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;background-image:radial-gradient(ellipse at 50% 0%,rgba(124,58,237,0.12) 0%,transparent 60%)}.wrap{max-width:520px;width:100%;text-align:center}.logo{font-size:22px;font-weight:800;color:#7c3aed;text-decoration:none;display:inline-block;margin-bottom:48px}.tag{display:inline-block;background:rgba(124,58,237,0.15);color:#a78bfa;font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;padding:4px 14px;border-radius:20px;border:1px solid rgba(124,58,237,0.3);margin-bottom:20px}h1{font-size:30px;font-weight:700;margin-bottom:10px}.sub{color:#6b7280;font-size:15px;margin-bottom:40px}.cards{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:28px}.card{border:1.5px solid #1f1f1f;border-radius:18px;padding:28px 20px;text-decoration:none;color:#fff;display:block;transition:all .2s;position:relative;background:#111}.card:hover{border-color:#4c1d95;transform:translateY(-2px)}.card.best{border-color:#7c3aed;background:linear-gradient(135deg,#130f1e,#1a1033);box-shadow:0 0 32px rgba(124,58,237,0.2)}.badge{position:absolute;top:-13px;left:50%;transform:translateX(-50%);background:linear-gradient(90deg,#7c3aed,#a855f7);color:#fff;font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;padding:4px 14px;border-radius:20px;white-space:nowrap}.lbl{font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#6b7280;margin-bottom:14px}.card.best .lbl{color:#a78bfa}.price{font-size:40px;font-weight:800;line-height:1}.price span{font-size:15px;font-weight:400;color:#6b7280}.card.best .price span{color:#a78bfa}.psub{font-size:12px;color:#4b5563;margin-top:8px}.card.best .psub{color:#7c3aed}.save{display:inline-block;background:rgba(34,197,94,0.12);color:#22c55e;font-size:11px;font-weight:700;padding:3px 10px;border-radius:10px;margin-top:10px;border:1px solid rgba(34,197,94,0.2)}.cancel{font-size:12px;color:#4b5563;margin-top:10px}.back{color:#4b5563;font-size:13px;text-decoration:none}.back:hover{color:#9ca3af}</style></head><body><div class="wrap"><a href="/" class="logo">StackSight</a><div class="tag">Business Plan</div><h1>Choose your billing</h1><p class="sub">Same features, same API. Pick what works for you.</p><div class="cards"><a href="/checkout/business" class="card"><div class="lbl">Monthly</div><div class="price">$199<span>/mo</span></div><div class="psub">billed monthly</div><div class="cancel">Cancel anytime</div></a><a href="/checkout/business_annual" class="card best"><div class="badge">BEST VALUE</div><div class="lbl">Annual</div><div class="price">$166<span>/mo</span></div><div class="psub">billed $1,992/yr</div><div class="save">Save 20%</div></a></div><a href="/#pricing" class="back">Back to pricing</a></div></body></html>""")


@app.get("/checkout/{plan}")
async def checkout(plan: str, request: Request, ss_session: str = Cookie(default=None)):
    if not ss_session:
        return RedirectResponse(url=f"/login?next=/checkout/{plan}", status_code=302)
    if plan not in STRIPE_PRICES:
        raise HTTPException(status_code=400, detail="Invalid plan")
    email = get_session_email(request)
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{"price": STRIPE_PRICES[plan], "quantity": 1}],
        mode="subscription",
        customer_email=email if email else None,
        success_url=f"{BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{BASE_URL}/#pricing",
        metadata={"plan": plan, "email": email or ""},
    )
    return RedirectResponse(session.url)


@app.get("/billing-portal")
async def billing_portal(request: Request, ss_session: str = Cookie(default=None)):
    if not ss_session:
        return RedirectResponse(url="/login", status_code=302)
    email = get_session_email(request)
    if not email:
        return RedirectResponse(url="/login", status_code=302)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT stripe_customer_id FROM api_keys WHERE email=%s AND active=TRUE LIMIT 1", (email,))
    row = cur.fetchone()
    cur.close(); release_db(conn)
    if not row or not row[0]:
        raise HTTPException(status_code=400, detail="No billing account found")
    portal = stripe.billing_portal.Session.create(
        customer=row[0],
        return_url=f"{BASE_URL}/dashboard",
    )
    return RedirectResponse(portal.url)


@app.get("/success", response_class=HTMLResponse)
async def success():
    return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Payment Successful - StackSight</title>
<style>body{font-family:-apple-system,sans-serif;background:#0a0a0a;color:#e5e5e5;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:48px;text-align:center;max-width:500px}
h1{color:#22c55e;margin-bottom:12px}p{color:#b0b0b0;margin-bottom:24px}
a{background:#a855f7;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600}</style></head>
<body><div class="box">
<h1>Payment Successful!</h1>
<p>Your API key is being generated and will arrive in your inbox within a minute.</p>
<a href="/login">Sign In to Dashboard</a>
</div></body></html>""")


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    print(f"[WEBHOOK] Received stripe event. sig present: {bool(sig)}, secret set: {bool(STRIPE_WEBHOOK_SECRET)}")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError as e:
        print(f"[WEBHOOK] Signature verification failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        print(f"[WEBHOOK] construct_event error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=400, detail=f"Webhook error: {str(e)}")
    print(f"[WEBHOOK] Event type: {event['type']}")
    try:
        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            # StripeObject — convert to plain dict for safe .get() access
            if hasattr(session, "to_dict"):
                session = session.to_dict()
            metadata = session.get("metadata") or {}
            customer_details = session.get("customer_details") or {}
            email = metadata.get("email") or customer_details.get("email") or session.get("customer_email")
            plan = metadata.get("plan", "starter")
            customer_id = session.get("customer")
            session_id = session.get("id")
            print(f"[WEBHOOK] checkout.session.completed: email={email}, plan={plan}, customer={customer_id}")
            if email:
                background_tasks.add_task(provision_api_key, email, plan, customer_id, session_id)
            else:
                print(f"[WEBHOOK] WARNING: no email found in session. customer_details={session.get('customer_details')}")

        elif event["type"] == "invoice.payment_succeeded":
            # Subscription renewed -- reset usage for this customer
            invoice = event["data"]["object"]
            if hasattr(invoice, "to_dict"):
                invoice = invoice.to_dict()
            customer_id = invoice.get("customer")
            billing_reason = invoice.get("billing_reason", "")
            # Only reset on renewals, not the initial payment (that's handled by checkout.session.completed)
            if customer_id and billing_reason == "subscription_cycle":
                conn = get_db()
                cur = conn.cursor()
                cur.execute(
                    "UPDATE api_keys SET requests_used = 0, usage_reset_at = NOW() WHERE stripe_customer_id = %s AND active = TRUE",
                    (customer_id,)
                )
                conn.commit()
                cur.close(); release_db(conn)

        elif event["type"] == "customer.subscription.deleted":
            # Subscription cancelled -- downgrade to free
            subscription = event["data"]["object"]
            if hasattr(subscription, "to_dict"):
                subscription = subscription.to_dict()
            customer_id = subscription.get("customer")
            if customer_id:
                conn = get_db()
                cur = conn.cursor()
                cur.execute(
                    "UPDATE api_keys SET plan = 'free', requests_limit = 25, requests_used = 0 WHERE stripe_customer_id = %s",
                    (customer_id,)
                )
                conn.commit()
                cur.close(); release_db(conn)

        elif event["type"] == "invoice.payment_failed":
            pass

    except Exception as e:
        print(f"[WEBHOOK] Handler error: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Webhook handler error: {str(e)}")

    return {"status": "ok"}


@app.get("/trending", response_class=HTMLResponse)
async def trending():
    # Pull all cached domain results from Redis
    results = []
    try:
        keys = redis_client.keys("domain:*")
        for key in keys[:200]:
            try:
                raw = redis_client.get(key)
                if not raw:
                    continue
                data = json.loads(raw)
                domain = key.replace("domain:", "")
                if not data.get("is_hiring"):
                    continue
                eng = data.get("engineering_roles", [])
                sales = data.get("sales_roles", [])
                other = data.get("other_roles", [])
                tech = data.get("detected_tech_stack", [])
                open_roles = len(eng) + len(sales) + len(other)
                if open_roles == 0:
                    continue
                # Sanity-check company name against domain to catch subsidiary/redirect mismatches
                # e.g. segment.com cached as "Twilio" because Segment was acquired
                raw_name = data.get("company_name", "")
                domain_base = domain.split(".")[0].lower()
                if raw_name and domain_base in raw_name.lower():
                    company_name = raw_name
                else:
                    company_name = domain.split(".")[0].title()
                results.append({
                    "domain": domain,
                    "company_name": company_name,
                    "open_roles": open_roles,
                    "eng_count": len(eng),
                    "sales_count": len(sales),
                    "tech": tech[:6],
                    "sample_roles": (eng + sales + other)[:3],
                })
            except Exception:
                continue
    except Exception:
        pass

    # Fall back to demo data if Redis is empty
    if not results:
        for domain, data in DEMO_DATA.items():
            eng = data.get("engineering_roles", [])
            sales = data.get("sales_roles", [])
            other = data.get("other_roles", [])
            tech = data.get("detected_tech_stack", [])
            open_roles = len(eng) + len(sales) + len(other)
            if open_roles == 0:
                continue
            raw_name = data.get("company_name", "")
            domain_base = domain.split(".")[0].lower()
            if raw_name and domain_base in raw_name.lower():
                company_name = raw_name
            else:
                company_name = domain.split(".")[0].title()
            results.append({
                "domain": domain,
                "company_name": company_name,
                "open_roles": open_roles,
                "eng_count": len(eng),
                "sales_count": len(sales),
                "tech": tech[:6],
                "sample_roles": (eng + sales + other)[:3],
            })

    results.sort(key=lambda x: x["open_roles"], reverse=True)

    cards_html = ""
    for r in results[:50]:
        tech_pills = "".join(f"<span style='background:#1a0a2e;border:1px solid #3b1a6e;color:#c084fc;padding:3px 9px;border-radius:5px;font-size:11px;font-weight:600'>{t}</span> " for t in r["tech"])
        role_list = "".join(f"<li style='color:#aaa;font-size:13px;padding:2px 0'>{role}</li>" for role in r["sample_roles"])
        cards_html += f"""
<a href='/demo/{r["domain"]}' style='text-decoration:none;color:inherit;display:block'>
<div style='background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:20px 24px;margin-bottom:14px;transition:border-color .2s' onmouseover='this.style.borderColor="#7c3aed"' onmouseout='this.style.borderColor="#1f1f1f"'>
  <div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px'>
    <div>
      <span style='font-size:16px;font-weight:700;color:#fff'>{r["company_name"]}</span>
      <span style='color:#888;font-size:13px;margin-left:8px'>{r["domain"]}</span>
    </div>
    <span style='background:#052e16;color:#22c55e;border:1px solid #166534;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:600'>{r["open_roles"]} open roles</span>
  </div>
  <div style='margin-bottom:10px;display:flex;gap:14px'>
    <span style='font-size:12px;color:#888'>⚙ {r["eng_count"]} engineering</span>
    <span style='font-size:12px;color:#888'>💼 {r["sales_count"]} sales</span>
  </div>
  {'<ul style="margin-bottom:10px;padding-left:16px">' + role_list + '</ul>' if role_list else ''}
  <div style='display:flex;flex-wrap:wrap;gap:5px'>{tech_pills}</div>
</div>
</a>"""

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Companies Actively Hiring Now - StackSight</title>
<meta name="description" content="Live feed of companies actively hiring engineers, sales reps, and more. Updated daily from real job postings. Powered by StackSight.">
<meta name="robots" content="index, follow">
<link rel="canonical" href="https://stacksight.org/trending">
<meta property="og:title" content="Companies Actively Hiring Now - StackSight">
<meta property="og:description" content="Live feed of companies actively hiring engineers and sales reps. Updated daily from real job postings.">
<meta property="og:image" content="https://stacksight.org/og-image.png">
<meta property="og:url" content="https://stacksight.org/trending">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Companies Actively Hiring Now - StackSight">
<meta name="twitter:description" content="Live feed of companies actively hiring engineers and sales reps. Updated daily from real job postings.">
<meta name="twitter:image" content="https://stacksight.org/og-image.png">
<link rel="icon" type="image/png" href="/favicon.png"><link rel="shortcut icon" href="/favicon.ico">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;min-height:100vh}}
nav{{padding:16px 40px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1a1a1a;position:sticky;top:0;background:rgba(10,10,10,0.95);backdrop-filter:blur(10px);z-index:100}}
.logo{{font-size:20px;font-weight:700;color:#a855f7;text-decoration:none}}
.nav-links a{{color:#c0c0c0;text-decoration:none;margin-left:20px;font-size:14px}}
.nav-links a:hover{{color:#fff}}
.container{{max-width:760px;margin:0 auto;padding:48px 20px 80px}}
h1{{font-size:36px;font-weight:800;color:#fff;margin-bottom:8px;letter-spacing:-1px}}
.sub{{color:#888;font-size:15px;margin-bottom:36px}}
.badge{{display:inline-flex;align-items:center;gap:6px;background:#1a0a2e;color:#a855f7;border:1px solid #3b1a6e;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;margin-bottom:18px}}
.cta-box{{background:linear-gradient(135deg,#1a0a2e,#0f0520);border:1px solid #7c3aed;border-radius:12px;padding:28px;text-align:center;margin-top:32px}}
.cta-box h3{{font-size:20px;font-weight:700;color:#fff;margin-bottom:8px}}
.cta-box p{{color:#aaa;font-size:14px;margin-bottom:20px}}
.btn{{display:inline-block;background:#a855f7;color:#fff;padding:11px 28px;border-radius:8px;text-decoration:none;font-weight:700;font-size:14px}}
.btn:hover{{background:#9333ea}}
@media(max-width:640px){{nav{{padding:14px 16px}}.nav-links a:not(:last-child){{display:none}}h1{{font-size:26px}}}}
</style>
</head>
<body>
<a href="#main-content" class="skip-link">Skip to main content</a>
<nav>
  <a href="/" class="logo">StackSight</a>
  <div class="nav-links">
    <a href="/docs">Docs</a>
    <a href="/demo/vercel.com">Demo</a>
    <a href="/trending">Trending</a>
    <a href="/login" id="nav-auth-btn" style="background:#1a1a1a;border:1px solid #333;color:#fff;padding:7px 16px;border-radius:7px;font-weight:500">Sign In</a>
  </div>
</nav>
<script>
(function(){{
  fetch("/session-check",{{credentials:"include"}}).then(r=>r.json()).then(d=>{{
    if(d.logged_in){{
      var btn=document.getElementById("nav-auth-btn");
      if(btn){{btn.outerHTML='<a href="/dashboard" id="nav-auth-btn" title="My Account" style="display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:50%;background:#2a1a4a;border:2px solid #7c3aed;cursor:pointer;text-decoration:none"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#a855f7" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg></a>';}}
    }}
  }}).catch(function(){{}});
}})();
</script>
<div class="container">
  <div class="badge">&#x1f525; Live Data</div>
  <h1>Companies Hiring Now</h1>
  <p class="sub">{len(results)} companies actively hiring &mdash; updated from real job postings. Click any to see roles &amp; tech stack.</p>
  {cards_html if cards_html else '<p style="color:#555;text-align:center;padding:40px">No results cached yet. Try the <a href="/demo/vercel.com" style="color:#a855f7">demo</a> first.</p>'}
  <div class="cta-box">
    <h3>Get this data via API</h3>
    <p>Pull hiring signals for any domain instantly &mdash; structured JSON, 25 free requests, no credit card.</p>
    <a href="/#signup" class="btn">Get free API key</a>
  </div>
</div>
</body>
</html>""")


@app.get("/terms", response_class=HTMLResponse)
async def terms():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<link rel="icon" type="image/png" href="/favicon.png"><link rel="shortcut icon" href="/favicon.ico">
<title>Terms of Service - StackSight</title>
<meta name="description" content="Terms of Service for the StackSight API - B2B hiring intent signals and tech stack detection.">
<meta name="robots" content="index, follow">
<link rel="canonical" href="https://stacksight.org/terms">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;line-height:1.6}
nav{padding:18px 40px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1a1a1a;position:sticky;top:0;background:rgba(10,10,10,0.95);backdrop-filter:blur(10px);z-index:100}
.logo{font-size:22px;font-weight:700;color:#a855f7;text-decoration:none}
.nav-links a{color:#c0c0c0;text-decoration:none;margin-left:24px;font-size:14px;transition:color .2s}
.nav-links a:hover{color:#fff}
.nav-links .btn-login{background:#1a1a1a;border:1px solid #333;color:#fff;padding:7px 16px;border-radius:7px;font-weight:500}
.container{max-width:760px;margin:0 auto;padding:60px 24px 90px}
h1{font-size:38px;font-weight:800;letter-spacing:-1px;margin-bottom:10px}
h1 span{color:#a855f7}
.updated{color:#888;font-size:14px;margin-bottom:44px}
h2{font-size:20px;font-weight:700;letter-spacing:-0.5px;margin:40px 0 14px;color:#f5f5f5}
p{color:#b0b0b0;margin-bottom:16px;line-height:1.75}
ul{color:#b0b0b0;padding-left:22px;margin-bottom:16px;line-height:1.75}
ul li{margin-bottom:8px}
a{color:#a855f7}
strong{color:#e5e5e5}
code{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:5px;padding:1px 6px;font-size:13px;color:#c084fc}
footer{border-top:1px solid #1a1a1a;padding:32px 20px;text-align:center;color:#666;font-size:13px}
footer a{color:#888;text-decoration:none}
footer a:hover{color:#fff}
@media(max-width:640px){nav{padding:14px 20px}h1{font-size:30px}}
</style>
</head>
<body>
<a href="#main-content" class="skip-link">Skip to main content</a>
<nav>
  <a href="/" class="logo">StackSight</a>
  <div class="nav-links">
    <a href="/docs">Docs</a>
    <a href="/demo/vercel.com">Demo</a>
    <a href="/trending">Trending</a>
    <a href="/#pricing">Pricing</a>
    <a href="/login" class="btn-login" id="nav-auth-btn">Sign In</a>
  </div>
</nav>
<script>
(function(){
  fetch("/session-check",{credentials:"include"}).then(r=>r.json()).then(d=>{
    if(d.logged_in){
      var btn=document.getElementById("nav-auth-btn");
      if(btn){btn.outerHTML='<a href="/dashboard" id="nav-auth-btn" title="My Account" style="display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:50%;background:#2a1a4a;border:2px solid #7c3aed;cursor:pointer;text-decoration:none"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#a855f7" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg></a>';}
    }
  }).catch(function(){});
})();
</script>
<div class="container">
<h1>Terms of <span>Service</span></h1>
<p class="updated">Effective date: July 1, 2026 &nbsp;&middot;&nbsp; Last updated: July 18, 2026</p>

<h2>1. Acceptance of Terms</h2>
<p>These Terms of Service ("Terms") are a binding agreement between you and StackSight ("we", "us"), the operator of the API service available at <a href="https://stacksight.org">stacksight.org</a>. By creating an account, requesting an API key, or sending a request to any StackSight API endpoint, you agree to these Terms. If you are using StackSight on behalf of a company, you represent that you have authority to bind that company. If you do not agree to these Terms, do not use the service.</p>

<h2>2. Description of Service</h2>
<p>StackSight is a REST API that provides B2B sales intelligence derived from publicly available information. When you query a company domain, our systems retrieve that company's publicly accessible careers and website pages and return structured data including:</p>
<ul>
<li>Hiring signals -- whether the company appears to be actively hiring, and open engineering and sales roles detected on its public careers page</li>
<li>Tech stack detection -- technologies identified from public job postings and website signals</li>
<li>Bulk domain enrichment for lists of domains, subject to your plan's limits</li>
</ul>
<p>StackSight only processes information that companies have chosen to publish on the public internet. We do not access private systems, authenticated pages, or paywalled content. There is no mobile app and no user-generated content -- the service is API access plus a web dashboard for managing your account.</p>

<h2>3. API Usage & Rate Limits</h2>
<p>Access requires an API key tied to a plan:</p>
<ul>
<li><strong>Free</strong> -- 25 requests, no credit card required</li>
<li><strong>Pro</strong> -- $49/month, 5,000 requests per month</li>
<li><strong>Business</strong> -- $199/month, 50,000 requests per month</li>
</ul>
<p>When you reach your plan's limit, further requests return HTTP <code>429</code> until your quota resets at the start of your next billing period or you upgrade. We do not silently bill overages -- requests beyond your quota are rejected, not charged. We may also apply short-window rate limits (requests per minute) to protect service stability; these are documented in the <a href="/docs">API docs</a>.</p>

<h2>4. Prohibited Uses</h2>
<p>You agree not to:</p>
<ul>
<li>Use StackSight to build, train, or populate a competing hiring-signals or tech-stack-detection service</li>
<li>Resell, redistribute, or publish raw API responses without meaningful transformation or added value (using the data inside your own product, CRM, or workflow is fine)</li>
<li>Use the data for spam, unsolicited bulk messaging, harassment, stalking, or discrimination</li>
<li>Share, sell, or publish your API key, or make requests on behalf of third parties who do not have their own account</li>
<li>Circumvent rate limits or quotas, including by creating multiple free accounts, rotating keys, or spoofing requests</li>
<li>Use the service to violate any law, regulation, or the rights of any person or company, or direct us to target sites in a manner you know violates a target site's terms of service</li>
<li>Probe, scan, or attempt to gain unauthorized access to StackSight's systems or other users' data</li>
</ul>

<h2>5. Payment & Billing</h2>
<p>Paid plans are billed monthly in advance through <strong>Stripe</strong>, our payment processor. We never see or store your card number. Subscriptions renew automatically each month until cancelled. You may cancel at any time from your dashboard or by emailing <a href="mailto:support@stacksight.org">support@stacksight.org</a>; cancellation takes effect at the end of the current billing period and you retain access until then. We do not provide refunds for billing periods that have already started or for unused requests, except where required by law. Prices may change with at least 30 days' notice before the change applies to your subscription.</p>

<h2>6. API Key Security</h2>
<p>Your API key is a credential. You are responsible for keeping it secret and for all requests made with it, whether or not you authorized them. Do not embed your key in client-side code, public repositories, or shared documents. If you believe your key has been compromised, email <a href="mailto:support@stacksight.org">support@stacksight.org</a> immediately and we will revoke and reissue it. Usage incurred before revocation counts against your quota.</p>

<h2>7. Data & Privacy</h2>
<p>Our collection and handling of your personal data (email address, usage data, IP address) is described in our <a href="/privacy">Privacy Policy</a>, which is incorporated into these Terms by reference.</p>

<h2>8. Intellectual Property</h2>
<p>StackSight owns the service, including the platform, software, API design, documentation, branding, and the systems that generate our data. These Terms grant you a limited, non-exclusive, non-transferable license to use the API and its output for your internal business purposes while your account is in good standing. You own the derived works you create from API output -- enriched CRM records, reports, scoring models, and similar transformations are yours. The underlying facts returned by the API (a company's public job postings and technologies) are public information and are not claimed as proprietary by either party.</p>

<h2>9. Disclaimer of Warranties</h2>
<p>THE SERVICE AND ALL DATA ARE PROVIDED "AS IS" AND "AS AVAILABLE", WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT. Data is extracted from public sources that change without notice; we do not warrant that it is accurate, complete, or current, and you should treat it as a signal rather than a source of truth. We do not guarantee uninterrupted or error-free operation, and we may modify or discontinue features with reasonable notice.</p>

<h2>10. Limitation of Liability</h2>
<p>TO THE MAXIMUM EXTENT PERMITTED BY LAW, STACKSIGHT WILL NOT BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, OR FOR LOST PROFITS, REVENUE, DATA, OR BUSINESS OPPORTUNITIES, ARISING FROM OR RELATED TO YOUR USE OF THE SERVICE. OUR TOTAL AGGREGATE LIABILITY FOR ANY CLAIM IS LIMITED TO THE AMOUNT YOU PAID US IN THE TWELVE (12) MONTHS BEFORE THE CLAIM AROSE, OR $100 IF YOU ARE ON THE FREE PLAN. Some jurisdictions do not allow certain limitations, so parts of this section may not apply to you.</p>

<h2>11. Termination</h2>
<p>We may suspend or terminate your account and API access, with or without notice, if you violate these Terms, abuse the service, engage in fraud, or fail to pay. You may stop using the service and cancel at any time. Sections 8 through 10 survive termination. Upon termination we handle your data as described in the <a href="/privacy">Privacy Policy</a>.</p>

<h2>12. Changes to Terms</h2>
<p>We may update these Terms from time to time. For material changes we will notify active paying customers by email at least 14 days before the changes take effect, and we will update the date at the top of this page. Continued use of the service after changes take effect constitutes acceptance.</p>

<h2>13. Contact</h2>
<p>Questions about these Terms? Email <a href="mailto:support@stacksight.org">support@stacksight.org</a>.</p>
</div>
<footer>
  <div style="margin-bottom:14px;font-size:16px;font-weight:700;color:#a855f7;letter-spacing:-0.5px">Stack<span style="color:#e5e5e5">Sight</span></div>
  <div style="margin-bottom:12px">
    <a href="/docs">Docs</a> &nbsp;&nbsp; <a href="/demo/vercel.com">Demo</a> &nbsp;&nbsp; <a href="/#pricing">Pricing</a> &nbsp;&nbsp; <a href="/login">Sign In</a> &nbsp;&nbsp; <a href="mailto:support@stacksight.org">Contact</a>
  </div>
  <div style="margin-bottom:8px">
    <a href="/terms">Terms of Service</a> &nbsp;&nbsp; <a href="/privacy">Privacy Policy</a>
  </div>
  <div>&copy; 2026 StackSight &nbsp;&nbsp; <a href="https://x.com/StackSightOrg">@StackSightOrg</a> &nbsp;&nbsp; <a href="https://linkedin.com/company/stacksight" target="_blank" rel="noopener">LinkedIn</a></div>
</footer>
</body></html>""")


@app.get("/privacy", response_class=HTMLResponse)
async def privacy():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<link rel="icon" type="image/png" href="/favicon.png"><link rel="shortcut icon" href="/favicon.ico">
<title>Privacy Policy - StackSight</title>
<meta name="description" content="Privacy Policy for StackSight - what we collect, what we don't, and how we protect your data.">
<meta name="robots" content="index, follow">
<link rel="canonical" href="https://stacksight.org/privacy">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;line-height:1.6}
nav{padding:18px 40px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1a1a1a;position:sticky;top:0;background:rgba(10,10,10,0.95);backdrop-filter:blur(10px);z-index:100}
.logo{font-size:22px;font-weight:700;color:#a855f7;text-decoration:none}
.nav-links a{color:#c0c0c0;text-decoration:none;margin-left:24px;font-size:14px;transition:color .2s}
.nav-links a:hover{color:#fff}
.nav-links .btn-login{background:#1a1a1a;border:1px solid #333;color:#fff;padding:7px 16px;border-radius:7px;font-weight:500}
.container{max-width:760px;margin:0 auto;padding:60px 24px 90px}
h1{font-size:38px;font-weight:800;letter-spacing:-1px;margin-bottom:10px}
h1 span{color:#a855f7}
.updated{color:#888;font-size:14px;margin-bottom:44px}
h2{font-size:20px;font-weight:700;letter-spacing:-0.5px;margin:40px 0 14px;color:#f5f5f5}
p{color:#b0b0b0;margin-bottom:16px;line-height:1.75}
ul{color:#b0b0b0;padding-left:22px;margin-bottom:16px;line-height:1.75}
ul li{margin-bottom:8px}
a{color:#a855f7}
strong{color:#e5e5e5}
code{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:5px;padding:1px 6px;font-size:13px;color:#c084fc}
footer{border-top:1px solid #1a1a1a;padding:32px 20px;text-align:center;color:#666;font-size:13px}
footer a{color:#888;text-decoration:none}
footer a:hover{color:#fff}
@media(max-width:640px){nav{padding:14px 20px}h1{font-size:30px}}
</style>
</head>
<body>
<a href="#main-content" class="skip-link">Skip to main content</a>
<nav>
  <a href="/" class="logo">StackSight</a>
  <div class="nav-links">
    <a href="/docs">Docs</a>
    <a href="/demo/vercel.com">Demo</a>
    <a href="/trending">Trending</a>
    <a href="/#pricing">Pricing</a>
    <a href="/login" class="btn-login" id="nav-auth-btn">Sign In</a>
  </div>
</nav>
<script>
(function(){
  fetch("/session-check",{credentials:"include"}).then(r=>r.json()).then(d=>{
    if(d.logged_in){
      var btn=document.getElementById("nav-auth-btn");
      if(btn){btn.outerHTML='<a href="/dashboard" id="nav-auth-btn" title="My Account" style="display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:50%;background:#2a1a4a;border:2px solid #7c3aed;cursor:pointer;text-decoration:none"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#a855f7" stroke-width="2"><circle cx="12" cy="8" r="4"/><path d="M4 20c0-4 3.6-7 8-7s8 3 8 7"/></svg></a>';}
    }
  }).catch(function(){});
})();
</script>
<div class="container">
<h1>Privacy <span>Policy</span></h1>
<p class="updated">Effective date: July 1, 2026 &nbsp;&middot;&nbsp; Last updated: July 18, 2026</p>

<p>StackSight is an API service, not an ad-funded product. We collect the minimum data needed to run the service, and this policy explains exactly what that is.</p>

<h2>1. What We Collect</h2>
<ul>
<li><strong>Email address</strong> -- required to create an account. We use passwordless magic link authentication, so your email is your identity. There are no passwords for us to store or leak.</li>
<li><strong>API key</strong> -- generated when you sign up, stored securely on our servers, and used only to authenticate your API requests.</li>
<li><strong>Usage counts</strong> -- the number of API requests you have used in the current billing period, so we can enforce plan quotas and show usage on your dashboard.</li>
<li><strong>IP address</strong> -- logged with requests for rate limiting, abuse prevention, and security investigation.</li>
<li><strong>Payment information</strong> -- handled entirely by <strong>Stripe</strong>. Your card number never touches our servers; we receive only a Stripe customer reference and subscription status.</li>
</ul>

<h2>2. What We Don't Collect</h2>
<ul>
<li>No tracking pixels</li>
<li>No third-party analytics (no Google Analytics, no Meta Pixel, nothing)</li>
<li>No advertising identifiers</li>
<li>No cookies beyond a single session cookie (<code>ss_session</code>) used solely to keep you signed in to the dashboard</li>
</ul>

<h2>3. How We Use Your Data</h2>
<ul>
<li>To deliver the API service and enforce your plan's request limits</li>
<li>To send magic link sign-in emails and essential transactional emails (API key delivery, billing notices)</li>
<li>To process subscription payments through Stripe</li>
<li>To detect and prevent abuse, fraud, and attempts to circumvent rate limits</li>
</ul>
<p>That's the full list. We do not use your data for advertising, profiling, or anything else.</p>

<h2>4. Data Retention</h2>
<ul>
<li><strong>Magic links</strong> expire 15 minutes after they are sent</li>
<li><strong>Session tokens</strong> expire after 7 days</li>
<li><strong>Rate-limit counters</strong> are ephemeral data held in Redis and expire automatically</li>
<li><strong>Account data</strong> (email, API key, usage history, billing records) is kept while your account is active, and for 90 days after account deletion, after which it is permanently purged. We may retain billing records longer where tax or accounting law requires.</li>
</ul>

<h2>5. Third Parties</h2>
<p>We share data only with the infrastructure providers needed to run the service:</p>
<ul>
<li><strong>Stripe</strong> -- payment processing (<a href="https://stripe.com/privacy">Stripe's privacy policy</a>)</li>
<li><strong>SendGrid / SMTP provider</strong> -- delivery of transactional email only (magic links, receipts). We never send marketing blasts through it without your consent.</li>
<li><strong>Railway</strong> -- hosting infrastructure where the application and database run</li>
</ul>
<p>No advertising networks. No data brokers. No one else.</p>

<h2>6. We Do Not Sell Your Data</h2>
<p>We do not sell, rent, or trade your personal information to anyone, for any purpose. The data our API returns about companies is derived from publicly available web pages and does not include our users' personal data.</p>

<h2>7. Your Rights</h2>
<p>You can access, correct, or delete your personal data at any time. To delete your account, email <a href="mailto:support@stacksight.org">support@stacksight.org</a> from your account email address and we will purge your data within 30 days, subject to the retention rules in Section 4. Depending on where you live (e.g. the EU/UK under GDPR, or California under CCPA), you may have additional statutory rights; email us and we will honor them.</p>

<h2>8. Security</h2>
<ul>
<li>API keys are stored securely and used only to authenticate API requests</li>
<li>All traffic is served over HTTPS only</li>
<li>Session cookies are secure and HttpOnly</li>
<li>Rate-limit and abuse-prevention data lives in Redis and expires automatically</li>
</ul>
<p>No system is perfectly secure, but if we ever discover a breach affecting your personal data, we will notify you by email without undue delay.</p>

<h2>9. Changes to This Policy</h2>
<p>If we make material changes to this policy, we will notify account holders by email before the changes take effect and update the date at the top of this page.</p>

<h2>10. Contact</h2>
<p>Privacy questions or requests: <a href="mailto:support@stacksight.org">support@stacksight.org</a></p>
</div>
<footer>
  <div style="margin-bottom:14px;font-size:16px;font-weight:700;color:#a855f7;letter-spacing:-0.5px">Stack<span style="color:#e5e5e5">Sight</span></div>
  <div style="margin-bottom:12px">
    <a href="/docs">Docs</a> &nbsp;&nbsp; <a href="/demo/vercel.com">Demo</a> &nbsp;&nbsp; <a href="/#pricing">Pricing</a> &nbsp;&nbsp; <a href="/login">Sign In</a> &nbsp;&nbsp; <a href="mailto:support@stacksight.org">Contact</a>
  </div>
  <div style="margin-bottom:8px">
    <a href="/terms">Terms of Service</a> &nbsp;&nbsp; <a href="/privacy">Privacy Policy</a>
  </div>
  <div>&copy; 2026 StackSight &nbsp;&nbsp; <a href="https://x.com/StackSightOrg">@StackSightOrg</a> &nbsp;&nbsp; <a href="https://linkedin.com/company/stacksight" target="_blank" rel="noopener">LinkedIn</a></div>
</footer>
</body></html>""")


@app.get("/session-check")
async def session_check(request: Request):
    email = get_session_email(request)
    if email:
        return JSONResponse({"logged_in": True, "email": email})
    return JSONResponse({"logged_in": False})


@app.get("/health")
async def health():
    try:
        redis_client.ping(); redis_ok = True
    except Exception:
        redis_ok = False
    return {"status": "ok", "version": VERSION, "redis": redis_ok}



ADMIN_EMAIL = "ngrynai@gmail.com"

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, pw: str = None, totp: str = None):
    email = get_session_email(request)
    # 404 for anyone not logged in as owner
    if email != ADMIN_EMAIL:
        raise HTTPException(status_code=404)
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin not configured")
    # Require admin password + TOTP as two factors
    _admin_token = request.cookies.get("admin_verified")
    admin_verified = bool(_admin_token and redis_client.get(f"admin_session:{_admin_token}"))
    if not admin_verified:
        ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown").split(",")[0].strip()
        lockout_key = f"admin_lockout:{ip}"
        fail_key = f"admin_fails:{ip}"
        if redis_client.get(lockout_key):
            return HTMLResponse("<!DOCTYPE html><html><body style='font-family:sans-serif;background:#0a0a0a;color:#e5e5e5;display:flex;align-items:center;justify-content:center;height:100vh;margin:0'><div style='text-align:center'><h1 style='color:#333;font-size:48px'>404</h1><p style='color:#888'>Page not found</p></div></body></html>", status_code=404)
        pw_correct = pw == ADMIN_PASSWORD
        totp_ok = totp is not None and TOTP_SECRET and pyotp.TOTP(TOTP_SECRET).verify(totp, valid_window=1)
        if not (pw_correct and totp_ok):
            if pw is not None or totp is not None:
                fails = redis_client.incr(fail_key)
                redis_client.expire(fail_key, 900)
                if fails >= 5:
                    redis_client.setex(lockout_key, 900, "1")
            if pw_correct:
                # Password correct, ask for TOTP
                return HTMLResponse(f"""<!DOCTYPE html>
<html><head><title>Not Found</title></head>
<body style='font-family:sans-serif;background:#0a0a0a;color:#e5e5e5;display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>
<div style='text-align:center'>
  <h1 style='font-size:48px;color:#333'>404</h1>
  <p style='color:#888'>Page not found</p>
  <form method='get' style='margin-top:24px'>
    <input type='hidden' name='pw' value='{pw}'>
    <input name='totp' type='text' placeholder='Authenticator code' autofocus maxlength='6' inputmode='numeric'
      style='background:#111;border:1px solid #333;color:#fff;padding:10px 16px;border-radius:8px;font-size:15px;margin-right:8px;width:160px'>
    <button type='submit'
      style='background:#a855f7;color:#fff;border:none;padding:10px 20px;border-radius:8px;font-size:15px;cursor:pointer'>Verify</button>
  </form>
</div></body></html>""", status_code=404)
            else:
                return HTMLResponse("""<!DOCTYPE html>
<html><head><title>Not Found</title></head>
<body style='font-family:sans-serif;background:#0a0a0a;color:#e5e5e5;display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>
<div style='text-align:center'>
  <h1 style='font-size:48px;color:#333'>404</h1>
  <p style='color:#888'>Page not found</p>
  <form method='get' style='margin-top:24px'>
    <input name='pw' type='password' placeholder='Password' autofocus
      style='background:#111;border:1px solid #333;color:#fff;padding:10px 16px;border-radius:8px;font-size:15px;margin-right:8px'>
    <button type='submit'
      style='background:#a855f7;color:#fff;border:none;padding:10px 20px;border-radius:8px;font-size:15px;cursor:pointer'>Enter</button>
  </form>
</div></body></html>""", status_code=404)
        # Both factors correct -- clear fail counter, set verified cookie
        redis_client.delete(fail_key)
        admin_token = secrets.token_hex(32)
        redis_client.setex(f"admin_session:{admin_token}", 3600, "1")
        response = HTMLResponse("")
        response.set_cookie("admin_verified", admin_token, httponly=True, secure=True, samesite="lax", max_age=3600)
        response.headers["Location"] = "/admin"
        response.status_code = 302
        return response
    conn = get_db()
    cur = conn.cursor()
    # Stats
    cur.execute("SELECT COUNT(*) FROM api_keys")
    total_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM api_keys WHERE active=TRUE")
    active_keys = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(requests_used),0) FROM api_keys")
    total_requests = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM sessions WHERE active=TRUE AND expires_at > NOW()")
    active_sessions = cur.fetchone()[0]
    # All users with keys
    cur.execute("""
        SELECT email, api_key, plan, requests_used, requests_limit, active, created_at
        FROM api_keys ORDER BY created_at DESC
    """)
    users = cur.fetchall()
    cur.close()
    release_db(conn)

    rows = ""
    for u in users:
        em, key, plan, used, limit, act, created = u
        pct = int((used/limit)*100) if limit else 0
        status_color = "#22c55e" if act else "#ef4444"
        status_text = "Active" if act else "Disabled"
        toggle_label = "Disable" if act else "Enable"
        toggle_color = "#ef4444" if act else "#22c55e"
        safe_key = key or ""
        rows += f"""<tr>
            <td>{html.escape(str(em))}</td>
            <td><code style="font-size:11px">{safe_key[:20] + "..." if safe_key else "None"}</code></td>
            <td><span class="badge badge-{plan}">{plan.upper()}</span></td>
            <td>{used} / {limit} <div class="bar"><div class="bar-fill" style="width:{pct}%"></div></div></td>
            <td><span style="color:{status_color}">{status_text}</span></td>
            <td>{str(created)[:10]}</td>
            <td style="white-space:nowrap">
              <button class="btn-toggle" data-email="{html.escape(str(em), quote=True)}" data-active="{str(act).lower()}" style="background:{toggle_color};color:#fff;border:none;padding:4px 10px;border-radius:6px;cursor:pointer;font-size:12px;margin-right:4px">{toggle_label}</button>
              <button class="btn-delete" data-email="{html.escape(str(em), quote=True)}" style="background:#333;color:#ef4444;border:1px solid #ef4444;padding:4px 10px;border-radius:6px;cursor:pointer;font-size:12px">Delete</button>
            </td>
        </tr>"""

    return HTMLResponse(f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" type="image/png" href="/favicon.png"><link rel="shortcut icon" href="/favicon.ico">
<title>StackSight Admin</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;padding:32px}}
h1{{font-size:28px;font-weight:700;color:#a855f7;margin-bottom:8px}}
.sub{{color:#888;margin-bottom:32px;font-size:14px}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:40px}}
.stat{{background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:20px}}
.stat-value{{font-size:32px;font-weight:700;color:#a855f7}}
.stat-label{{color:#888;font-size:13px;margin-top:4px}}
table{{width:100%;border-collapse:collapse;background:#111;border-radius:12px;overflow:hidden}}
th{{background:#1a1a1a;padding:12px 16px;text-align:left;font-size:13px;color:#888;font-weight:500;border-bottom:1px solid #1f1f1f}}
td{{padding:12px 16px;font-size:14px;border-bottom:1px solid #1a1a1a}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:#141414}}
code{{font-size:12px;color:#a855f7;background:#1a0a2e;padding:2px 6px;border-radius:4px}}
.badge{{padding:3px 8px;border-radius:20px;font-size:11px;font-weight:600}}
.badge-free{{background:#1a1a1a;color:#888}}
.badge-pro{{background:#1a0a2e;color:#a855f7}}
.badge-business{{background:#0a1a2e;color:#3b82f6}}
.bar{{background:#1f1f1f;border-radius:4px;height:4px;margin-top:6px;width:120px}}
.bar-fill{{background:#a855f7;height:4px;border-radius:4px}}
.back{{color:#888;font-size:13px;text-decoration:none;display:inline-block;margin-bottom:24px}}
.back:hover{{color:#fff}}
</style></head>
<body>
<a href="/" class="back">-- Back to site</a>
<h1>Admin Dashboard</h1>
<p class="sub">Logged in as {email}</p>
<div class="stats">
  <div class="stat"><div class="stat-value">{total_users}</div><div class="stat-label">Total Users</div></div>
  <div class="stat"><div class="stat-value">{active_keys}</div><div class="stat-label">Active API Keys</div></div>
  <div class="stat"><div class="stat-value">{total_requests}</div><div class="stat-label">Total API Calls</div></div>
  <div class="stat"><div class="stat-value">{active_sessions}</div><div class="stat-label">Active Sessions</div></div>
</div>
<table>
  <thead><tr><th>Email</th><th>API Key</th><th>Plan</th><th>Usage</th><th>Status</th><th>Joined</th><th>Actions</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
<script>
async function toggleKey(btn) {{
  const email = btn.dataset.email;
  const currentlyActive = btn.dataset.active === 'true';
  btn.disabled = true;
  const r = await fetch('/admin/toggle-key', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{email, active: !currentlyActive}})}});
  if (r.ok) location.reload();
  else {{ alert('Error'); btn.disabled = false; }}
}}
async function deleteUser(btn) {{
  const email = btn.dataset.email;
  if (!confirm('Delete all data for ' + email + '?')) return;
  btn.disabled = true;
  const r = await fetch('/admin/delete-user', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{email}})}});
  if (r.ok) location.reload();
  else {{ alert('Error'); btn.disabled = false; }}
}}
document.querySelectorAll('.btn-toggle').forEach(b => b.addEventListener('click', () => toggleKey(b)));
document.querySelectorAll('.btn-delete').forEach(b => b.addEventListener('click', () => deleteUser(b)));
async function flushAllCache() {{
  if (!confirm('Flush ALL Redis cache? All domains will be re-scraped on next request.')) return;
  const r = await fetch('/admin/flush-cache', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{}})}});
  const d = await r.json();
  alert('Flushed ' + d.deleted + ' cached domains.');
}}
async function flushEmptyTech() {{
  const r = await fetch('/admin/flush-empty-tech', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{}})}});
  const d = await r.json();
  alert('Flushed ' + d.flushed + ' domains with empty tech stacks: ' + (d.domains || []).join(', '));
}}
</script>
<div style="margin-top:24px;display:flex;gap:12px;flex-wrap:wrap">
  <button onclick="flushEmptyTech()" style="background:#7c3aed;color:#fff;border:none;padding:10px 20px;border-radius:7px;cursor:pointer;font-weight:600">Flush Empty Tech Stacks</button>
  <button onclick="flushAllCache()" style="background:#1f1f1f;color:#ef4444;border:1px solid #ef4444;padding:10px 20px;border-radius:7px;cursor:pointer;font-weight:600">Flush All Cache</button>
</div>
</body></html>""")

@app.post("/admin/toggle-key")
async def admin_toggle_key(request: Request):
    if get_session_email(request) != ADMIN_EMAIL:
        raise HTTPException(status_code=404)
    _tok = request.cookies.get("admin_verified")
    if not (_tok and redis_client.get(f"admin_session:{_tok}")):
        raise HTTPException(status_code=403)
    body = await request.json()
    email = body.get("email")
    active = body.get("active", True)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE api_keys SET active=%s WHERE email=%s", (active, email))
    conn.commit()
    cur.close(); release_db(conn)
    return {"ok": True}

@app.post("/admin/delete-user")
async def admin_delete_user(request: Request):
    if get_session_email(request) != ADMIN_EMAIL:
        raise HTTPException(status_code=404)
    _tok = request.cookies.get("admin_verified")
    if not (_tok and redis_client.get(f"admin_session:{_tok}")):
        raise HTTPException(status_code=403)
    body = await request.json()
    email = body.get("email")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM api_keys WHERE email=%s", (email,))
    cur.execute("DELETE FROM sessions WHERE email=%s", (email,))
    cur.execute("DELETE FROM magic_links WHERE email=%s", (email,))
    conn.commit()
    cur.close(); release_db(conn)
    return {"ok": True}

@app.post("/admin/reset-usage")
async def admin_reset_usage(request: Request):
    """Reset monthly quotas for paid users. Called by Railway cron / external scheduler."""
    if not CRON_SECRET:
        raise HTTPException(status_code=503, detail="CRON_SECRET not configured")
    auth = request.headers.get("X-Cron-Secret", "")
    if not secrets.compare_digest(auth, CRON_SECRET):
        raise HTTPException(status_code=403, detail="Forbidden")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE api_keys
        SET requests_used = 0, usage_reset_at = NOW()
        WHERE plan != 'free'
          AND (usage_reset_at IS NULL OR usage_reset_at < NOW() - INTERVAL '30 days')
    """)
    reset_count = cur.rowcount
    conn.commit()
    cur.close(); release_db(conn)
    return {"ok": True, "keys_reset": reset_count}


@app.post("/admin/flush-cache")
async def admin_flush_cache(request: Request):
    """Flush Redis cache for a specific domain or all domains."""
    _tok = request.cookies.get("admin_verified")
    if not (_tok and redis_client.get(f"admin_session:{_tok}")):
        raise HTTPException(status_code=403)
    body = await request.json()
    domain = body.get("domain")
    if domain:
        key = f"domain:{domain.strip().lower()}"
        deleted = redis_client.delete(key)
        return {"ok": True, "deleted": deleted, "key": key}
    else:
        keys = redis_client.keys("domain:*")
        if keys:
            redis_client.delete(*keys)
        return {"ok": True, "deleted": len(keys)}

@app.post("/admin/flush-empty-tech")
async def admin_flush_empty_tech(request: Request):
    """Flush cache entries that have empty detected_tech_stack so they get re-scraped."""
    _tok = request.cookies.get("admin_verified")
    if not (_tok and redis_client.get(f"admin_session:{_tok}")):
        raise HTTPException(status_code=403)
    keys = redis_client.keys("domain:*")
    flushed = []
    for key in keys:
        try:
            raw = redis_client.get(key)
            if raw:
                data = json.loads(raw)
                if not data.get("detected_tech_stack"):
                    redis_client.delete(key)
                    flushed.append(key.replace("domain:", ""))
        except Exception:
            continue
    return {"ok": True, "flushed": len(flushed), "domains": flushed}

@app.post("/admin/create-key")
async def admin_create_key(request: Request):
    if not CRON_SECRET:
        raise HTTPException(status_code=503, detail="CRON_SECRET not configured")
    auth = request.headers.get("X-Cron-Secret", "")
    if not secrets.compare_digest(auth, CRON_SECRET):
        raise HTTPException(status_code=403, detail="Forbidden")
    body = await request.json()
    email = body.get("email")
    plan = body.get("plan", "free")
    if not email:
        raise HTTPException(status_code=400, detail="Email required")
    key = provision_api_key(email, plan)
    return {"api_key": key, "plan": plan, "email": email}


if __name__ == "__main__":
    uvicorn.run("scraper:app", host="0.0.0.0", port=8000, reload=True)
 
