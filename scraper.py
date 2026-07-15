import asyncio
import json
import os
import secrets
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import openai
import psycopg2
import redis as redis_lib
import stripe
import uvicorn
from fastapi import BackgroundTasks, Cookie, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from playwright.async_api import async_playwright

VERSION = "9.7.0"

# ââ Config ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
openai.api_key = os.environ.get("OPENAI_API_KEY", "")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
ZOHO_EMAIL = os.environ.get("ZOHO_EMAIL", "")
ZOHO_PASSWORD = os.environ.get("ZOHO_PASSWORD", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
CRON_SECRET = os.environ.get("CRON_SECRET", "stacksight-cron-2024")
BASE_URL = os.environ.get("BASE_URL", "https://stacksight.org")

STRIPE_PRICES = {
    "pro": os.environ.get("STRIPE_PRICE_PRO", "price_1TrLQ6DUssNU8xAWD0eyqLx4"),
    "business": os.environ.get("STRIPE_PRICE_BUSINESS", "price_1TrLceDUssNU8xAWKWUSPLlR"),
}
PLAN_LIMITS = {"free": 10, "pro": 5000, "business": 50000}

# ââ Rate limiting âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
_rate_limit: dict = {}
RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW = 60

# ââ Redis / App âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
redis_client = redis_lib.from_url(REDIS_URL, decode_responses=True)
app = FastAPI(title="StackSight API", version=VERSION, docs_url=None, redoc_url=None)

# ââ Demo data âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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

EXTRACTION_PROMPT = (
    "You are a B2B data extraction engine. Given raw text from a company careers page, "
    "extract structured data. Return ONLY valid JSON. Schema: "
    "{company_name: string, is_hiring: boolean, engineering_roles: [string], "
    "sales_roles: [string], detected_tech_stack: [string]}"
)

# ââ Database ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
def get_db():
    return psycopg2.connect(DATABASE_URL)

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
            requests_limit INTEGER NOT NULL DEFAULT 10,
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
    # ââ Migrations (safe to re-run) âââââââââââââââââââââââââââââââââââââââââ
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS api_key VARCHAR(64)")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS email VARCHAR(255)")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS plan VARCHAR(20) DEFAULT 'free'")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS requests_used INTEGER DEFAULT 0")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS requests_limit INTEGER DEFAULT 10")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(100)")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS stripe_session_id VARCHAR(100)")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS last_used TIMESTAMP")
    cur.execute("ALTER TABLE pending_signups ADD COLUMN IF NOT EXISTS email VARCHAR(255)")
    cur.execute("ALTER TABLE pending_signups ADD COLUMN IF NOT EXISTS token VARCHAR(64)")
    cur.execute("ALTER TABLE pending_signups ADD COLUMN IF NOT EXISTS used BOOLEAN DEFAULT FALSE")
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
    conn.commit()
    cur.close()
    conn.close()

# ââ Session auth ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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
    conn.close()
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
    conn.close()
    response.set_cookie(
        key="ss_session",
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=60 * 60 * 24 * 7,
        path="/"
    )
    return token

# ââ Rate limiting âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
def check_rate_limit(api_key: str):
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    if api_key not in _rate_limit:
        _rate_limit[api_key] = []
    _rate_limit[api_key] = [t for t in _rate_limit[api_key] if t > window_start]
    if len(_rate_limit[api_key]) >= RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Max 20 requests/minute.")
    _rate_limit[api_key].append(now)

# ââ API key auth ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
def verify_api_key(x_api_key: str):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT plan, requests_used, requests_limit, active FROM api_keys WHERE api_key=%s", (x_api_key,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    plan, used, limit, active = row
    if not active:
        raise HTTPException(status_code=403, detail="API key deactivated")
    if used >= limit:
        raise HTTPException(status_code=429, detail=f"Request limit reached ({limit} for {plan} plan). Upgrade at stacksight.org")
    check_rate_limit(x_api_key)
    return x_api_key, plan

def increment_usage(api_key: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE api_keys SET requests_used = requests_used + 1 WHERE api_key=%s", (api_key,))
    conn.commit()
    cur.close()
    conn.close()

# ââ Email âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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

def send_magic_link_email(to_email: str, token: str):
    url = f"{BASE_URL}/auth?token={token}"
    subject = "Your StackSight login link"
    html = f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#0f0f0f;color:#fff;padding:40px;border-radius:12px">
      <h1 style="color:#a855f7;margin-bottom:4px">StackSight</h1>
      <p style="color:#999;margin-top:0;margin-bottom:24px">B2B Hiring Intent API</p>
      <h2 style="color:#fff">Sign in to your account</h2>
      <p style="color:#ccc">Click the button below to securely log in. This link expires in 15 minutes and can only be used once.</p>
      <a href="{url}" style="display:inline-block;background:#a855f7;color:#fff;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:bold;margin:20px 0;font-size:16px">Sign In to Dashboard</a>
      <p style="color:#666;font-size:13px">Or paste this link:<br><span style="color:#a855f7">{url}</span></p>
      <p style="color:#666;font-size:12px;margin-top:24px">If you didn't request this, ignore this email.</p>
    </div>"""
    text = f"Sign in to StackSight\n\nClick here: {url}\n\nExpires in 15 minutes, single use only."
    send_email(to_email, subject, html, text)

def send_api_key_email(to_email: str, api_key: str, plan: str):
    limit = PLAN_LIMITS.get(plan, 10)
    subject = "Your StackSight API Key"
    html = f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#0f0f0f;color:#fff;padding:40px;border-radius:12px">
      <h1 style="color:#a855f7">StackSight API</h1>
      <h2>Your API Key is Ready</h2>
      <p>Plan: <strong style="color:#a855f7">{plan.title()}</strong> | Limit: <strong>{limit:,} requests</strong></p>
      <div style="background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:20px;font-family:monospace;font-size:16px;color:#a855f7;word-break:break-all">{api_key}</div>
      <p style="margin-top:20px">View your dashboard: <a href="{BASE_URL}/dashboard" style="color:#a855f7">{BASE_URL}/dashboard</a></p>
      <p>Docs: <a href="{BASE_URL}/docs" style="color:#a855f7">{BASE_URL}/docs</a></p>
    </div>"""
    text = f"Your StackSight API Key\n\nPlan: {plan.title()}\nLimit: {limit:,} requests\n\nAPI Key: {api_key}\n\nDashboard: {BASE_URL}/dashboard"
    send_email(to_email, subject, html, text)

def send_verification_email(email: str, token: str):
    verify_url = f"https://stacksight.org/verify-email?token={token}"
    subject = "Verify your email - StackSight"
    html_body = f"""<html><body>
<p>Hi,</p>
<p>Thanks for signing up for StackSight! Click the link below to verify your email and get your free API key:</p>
<p><a href="{verify_url}">{verify_url}</a></p>
<p>This link expires in 24 hours.</p>
<p>- The StackSight Team</p>
</body></html>"""
    text_body = f"Verify your email: {verify_url}"
    _send_email_sync(email, subject, html_body, text_body)
def provision_api_key(email: str, plan: str, stripe_customer_id: str = None, stripe_session_id: str = None):
    api_key = "ss_" + secrets.token_urlsafe(32)
    limit = PLAN_LIMITS.get(plan, 10)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO api_keys (key, api_key, email, plan, requests_limit, stripe_customer_id, stripe_session_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
    """, (api_key, api_key, email, plan, limit, stripe_customer_id, stripe_session_id))
    conn.commit()
    cur.close()
    conn.close()
    send_api_key_email(email, api_key, plan)
    return api_key

# ââ Scraper âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
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
                    await asyncio.sleep(2)
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
    response = openai.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACTION_PROMPT},
            {"role": "user", "content": "Careers page text:\n\n" + raw_text[:10000]},
        ],
        temperature=0,
    )
    return json.loads(response.choices[0].message.content)

# ââ Startup âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
@app.on_event("startup")
async def startup():
    init_db()

# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# ROUTES â PUBLIC
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return """User-agent: *
Allow: /
Disallow: /dashboard
Disallow: /auth
Disallow: /logout
Sitemap: https://stacksight.org/sitemap.xml"""

@app.get("/sitemap.xml")
async def sitemap():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://stacksight.org/</loc><priority>1.0</priority><changefreq>weekly</changefreq></url>
  <url><loc>https://stacksight.org/docs</loc><priority>0.9</priority><changefreq>weekly</changefreq></url>
  <url><loc>https://stacksight.org/demo/stripe.com</loc><priority>0.7</priority><changefreq>monthly</changefreq></url>
  <url><loc>https://stacksight.org/login</loc><priority>0.5</priority><changefreq>monthly</changefreq></url>
</urlset>"""
    return Response(content=xml, media_type="application/xml")

@app.get("/docs", response_class=HTMLResponse)
async def docs_page():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>API Docs - StackSight</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;line-height:1.6;display:flex;min-height:100vh}
nav{padding:18px 40px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1a1a1a;position:fixed;top:0;left:0;right:0;background:rgba(10,10,10,0.95);backdrop-filter:blur(10px);z-index:100}
.logo{font-size:20px;font-weight:700;color:#a855f7;text-decoration:none}
.nav-links a{color:#999;text-decoration:none;margin-left:24px;font-size:14px}.nav-links a:hover{color:#fff}
.nav-links .btn-login{background:#1a1a1a;border:1px solid #333;color:#fff;padding:7px 16px;border-radius:7px}
.sidebar{width:240px;flex-shrink:0;position:fixed;top:61px;left:0;bottom:0;overflow-y:auto;border-right:1px solid #1a1a1a;padding:24px 0}
.sidebar-section{padding:8px 20px;font-size:11px;font-weight:700;color:#444;text-transform:uppercase;letter-spacing:.8px;margin-top:16px}
.sidebar a{display:block;padding:8px 20px;font-size:13px;color:#777;text-decoration:none;border-left:2px solid transparent}
.sidebar a:hover{color:#ccc;background:#111}.sidebar a.active{color:#a855f7;border-left-color:#a855f7;background:#0f0518}
.main{margin-left:240px;margin-top:61px;flex:1;padding:48px 60px;max-width:900px}
h1{font-size:36px;font-weight:800;margin-bottom:8px}
h2{font-size:22px;font-weight:700;margin:48px 0 16px;padding-top:48px;border-top:1px solid #1a1a1a}
p{color:#888;margin-bottom:16px;font-size:15px}
.endpoint{background:#0d0d0d;border:1px solid #1f1f1f;border-radius:12px;margin-bottom:24px;overflow:hidden}
.endpoint-header{display:flex;align-items:center;gap:12px;padding:16px 20px;background:#111;border-bottom:1px solid #1f1f1f}
.method{font-size:12px;font-weight:700;padding:3px 10px;border-radius:5px}
.get{background:#0a2a1a;color:#22c55e;border:1px solid #166534}
.post{background:#1a1a0a;color:#eab308;border:1px solid #713f12}
.path{font-family:monospace;font-size:15px;color:#e5e5e5;font-weight:600}
.endpoint-body{padding:20px}
table{width:100%;border-collapse:collapse;margin-bottom:16px;font-size:14px}
th{text-align:left;padding:10px 12px;background:#111;color:#555;font-size:12px;text-transform:uppercase;border-bottom:1px solid #1f1f1f}
td{padding:10px 12px;border-bottom:1px solid #0f0f0f;color:#aaa}
td:first-child{font-family:monospace;color:#a855f7;font-size:13px}
pre{background:#050505;border:1px solid #1a1a1a;border-radius:8px;padding:20px;overflow-x:auto;font-size:13px;color:#ccc;line-height:1.7;margin:12px 0}
.auth-box{background:#0f0518;border:1px solid #3b1a6e;border-radius:10px;padding:20px;margin-bottom:24px}
.auth-box h4{color:#a855f7;font-size:14px;font-weight:600;margin-bottom:8px}
.limits{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px}
.limit-card{background:#111;border:1px solid #1f1f1f;border-radius:8px;padding:16px;text-align:center}
.limit-name{font-size:12px;color:#555;margin-bottom:4px}
.limit-val{font-size:20px;font-weight:700;color:#a855f7}
</style>
</head>
<body>
<nav><a href="/" class="logo">StackSight</a><div class="nav-links"><a href="/">Home</a><a href="/demo/stripe.com">Demo</a><a href="/#pricing">Pricing</a><a href="/login" class="btn-login" id="nav-auth-btn">Sign In</a></div></nav>
<script>
(function(){
  fetch('/usage',{credentials:'include'}).then(r=>{
    if(r.ok){r.json().then(d=>{
      var btn=document.getElementById('nav-auth-btn');
      if(btn){btn.textContent='My Account';btn.href='/dashboard';}
    });}
  }).catch(function(){});
})();
</script>
<div class="sidebar">
  <div class="sidebar-section">Getting Started</div>
  <a href="#quickstart" class="active">Quick Start</a><a href="#auth">Authentication</a><a href="#limits">Rate Limits</a>
  <div class="sidebar-section">Endpoints</div>
  <a href="#scrape">GET /scrape</a><a href="#bulk">POST /bulk</a><a href="#status">GET /status</a>
  <div class="sidebar-section">Reference</div>
  <a href="#response">Response Schema</a><a href="#errors">Error Codes</a><a href="#sdks">Code Examples</a>
</div>
<div class="main">
  <h1>API Documentation</h1>
  <p style="font-size:17px;color:#999;margin-bottom:32px">Real-time hiring intent signals, tech stack detection, and bulk domain enrichment.</p>
  <h2 id="quickstart" style="border-top:none;margin-top:0;padding-top:0">Quick Start</h2>
  <p>Get your free API key at <a href="/#signup" style="color:#a855f7">stacksight.org</a>, then:</p>
  <pre>curl -X GET "https://stacksight.org/scrape?domain=stripe.com" -H "X-API-Key: ss_your_key"</pre>
  <h2 id="auth">Authentication</h2>
  <div class="auth-box"><h4>X-API-Key Header</h4><p style="color:#666;margin:0">Pass your key in the <code style="color:#a855f7">X-API-Key</code> header. Keys look like <code style="color:#22d3ee">ss_...</code></p></div>
  <h2 id="limits">Rate Limits</h2>
  <div class="limits">
    <div class="limit-card"><div class="limit-name">Free</div><div class="limit-val">10</div><div style="font-size:11px;color:#444">total requests</div></div>
    <div class="limit-card"><div class="limit-name">Pro</div><div class="limit-val">5,000</div><div style="font-size:11px;color:#444">per month</div></div>
    <div class="limit-card"><div class="limit-name">Business</div><div class="limit-val">50,000</div><div style="font-size:11px;color:#444">per month</div></div>
  </div>
  <h2 id="scrape">GET /scrape</h2>
  <div class="endpoint">
    <div class="endpoint-header"><span class="method get">GET</span><span class="path">/scrape</span></div>
    <div class="endpoint-body">
      <table><tr><th>Param</th><th>Type</th><th>Required</th><th>Description</th></tr>
      <tr><td>domain</td><td>string</td><td style="color:#ef4444;font-size:11px;font-weight:700">required</td><td>Domain to enrich, e.g. stripe.com</td></tr></table>
      <pre>curl "https://stacksight.org/scrape?domain=notion.so" -H "X-API-Key: ss_your_key"</pre>
    </div>
  </div>
  <h2 id="bulk">POST /bulk</h2>
  <div class="endpoint">
    <div class="endpoint-header"><span class="method post">POST</span><span class="path">/bulk</span></div>
    <div class="endpoint-body">
      <pre>curl -X POST "https://stacksight.org/bulk" -H "X-API-Key: ss_your_key" -H "Content-Type: application/json" -d '{"domains":["stripe.com","notion.so"]}'</pre>
    </div>
  </div>
  <h2 id="status">GET /status</h2>
  <div class="endpoint">
    <div class="endpoint-header"><span class="method get">GET</span><span class="path">/status</span></div>
    <div class="endpoint-body"><pre>curl "https://stacksight.org/status" -H "X-API-Key: ss_your_key"</pre></div>
  </div>
  <h2 id="response">Response Schema</h2>
  <table>
    <tr><th>Field</th><th>Type</th><th>Description</th></tr>
    <tr><td>domain</td><td>string</td><td>Queried domain</td></tr>
    <tr><td>active_jobs</td><td>integer</td><td>Active job postings found</td></tr>
    <tr><td>growth_30d</td><td>string</td><td>Job count change over 30 days</td></tr>
    <tr><td>tech_stack</td><td>array</td><td>Technologies detected</td></tr>
    <tr><td>hiring_signal</td><td>string</td><td>high / medium / low</td></tr>
    <tr><td>cached</td><td>boolean</td><td>Whether result is from cache</td></tr>
  </table>
  <h2 id="errors">Error Codes</h2>
  <table>
    <tr><th>Status</th><th>Meaning</th></tr>
    <tr><td style="color:#fb923c">400</td><td>Missing or invalid domain</td></tr>
    <tr><td style="color:#fb923c">401</td><td>Missing or invalid API key</td></tr>
    <tr><td style="color:#fb923c">429</td><td>Rate limit exceeded</td></tr>
    <tr><td style="color:#fb923c">500</td><td>Scrape failed â retry</td></tr>
  </table>
  <h2 id="sdks">Code Examples</h2>
  <pre># Python
import requests
r = requests.get("https://stacksight.org/scrape", params={"domain":"stripe.com"}, headers={"X-API-Key":"ss_your_key"})
print(r.json())</pre>
  <pre>// JavaScript
const res = await fetch('https://stacksight.org/scrape?domain=stripe.com', { headers: {'X-API-Key':'ss_your_key'} });
console.log(await res.json());</pre>
</div>
<script>
const links = document.querySelectorAll('.sidebar a');
window.addEventListener('scroll',()=>{
  let cur='';
  document.querySelectorAll('[id]').forEach(s=>{if(window.scrollY>=s.offsetTop-100)cur=s.id});
  links.forEach(l=>l.classList.toggle('active',l.getAttribute('href')==='#'+cur));
});
</script>
</body></html>""")

@app.get("/", response_class=HTMLResponse)
async def landing():
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
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
{{"@context":"https://schema.org","@type":"SoftwareApplication","name":"StackSight","url":"https://stacksight.org","description":"Real-time B2B hiring intent API. Know which companies are actively growing before your competitors do.","applicationCategory":"BusinessApplication","operatingSystem":"Web","offers":[{{"@type":"Offer","name":"Free","price":"0","priceCurrency":"USD"}},{{"@type":"Offer","name":"Pro","price":"49","priceCurrency":"USD","billingIncrement":"P1M"}},{{"@type":"Offer","name":"Business","price":"199","priceCurrency":"USD","billingIncrement":"P1M"}}]}}
</script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;line-height:1.6}}
nav{{padding:18px 40px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1a1a1a;position:sticky;top:0;background:rgba(10,10,10,0.95);backdrop-filter:blur(10px);z-index:100}}
.logo{{font-size:22px;font-weight:700;color:#a855f7;text-decoration:none}}
.nav-links a{{color:#999;text-decoration:none;margin-left:24px;font-size:14px;transition:color .2s}}
.nav-links a:hover{{color:#fff}}
.nav-links .btn-login{{background:#1a1a1a;border:1px solid #333;color:#fff;padding:7px 16px;border-radius:7px;font-weight:500}}
.hero{{text-align:center;padding:90px 20px 60px;max-width:860px;margin:0 auto}}
.badge{{display:inline-flex;align-items:center;gap:6px;background:#1a0a2e;color:#a855f7;border:1px solid #3b1a6e;padding:5px 14px;border-radius:20px;font-size:12px;font-weight:600;margin-bottom:28px}}
h1{{font-size:56px;font-weight:800;line-height:1.08;margin-bottom:22px;letter-spacing:-1px}}
h1 span{{color:#a855f7}}
.hero p{{font-size:19px;color:#888;max-width:620px;margin:0 auto 40px;line-height:1.7}}
.cta-group{{display:flex;gap:14px;justify-content:center;flex-wrap:wrap}}
.btn-primary{{background:#a855f7;color:#fff;padding:14px 32px;border-radius:9px;text-decoration:none;font-weight:700;font-size:16px;transition:background .2s;display:inline-flex;align-items:center;gap:8px}}
.btn-primary:hover{{background:#9333ea}}
.btn-secondary{{background:transparent;color:#ccc;padding:14px 28px;border-radius:9px;text-decoration:none;font-weight:600;font-size:16px;border:1px solid #2a2a2a;transition:border-color .2s,color .2s}}
.btn-secondary:hover{{border-color:#555;color:#fff}}
.stats-bar{{display:flex;justify-content:center;gap:48px;flex-wrap:wrap;padding:40px 20px;border-top:1px solid #1a1a1a;border-bottom:1px solid #1a1a1a;margin:0 0 60px}}
.stat{{text-align:center}}
.stat-num{{font-size:28px;font-weight:800;color:#a855f7}}
.stat-label{{font-size:13px;color:#666;margin-top:2px}}
.use-cases{{max-width:1100px;margin:0 auto 80px;padding:0 20px}}
.use-cases h2,.how h2,.code-section h2,.pricing h2,.faq h2{{text-align:center;font-size:34px;font-weight:700;margin-bottom:12px}}
.sub{{text-align:center;color:#666;margin-bottom:44px;font-size:16px}}
.use-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px}}
.use-card{{background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:28px;transition:border-color .2s}}
.use-card:hover{{border-color:#3b1a6e}}
.use-icon{{font-size:28px;margin-bottom:14px}}
.use-card h3{{font-size:16px;font-weight:600;margin-bottom:8px}}
.use-card p{{color:#666;font-size:14px;line-height:1.6}}
.how{{max-width:860px;margin:0 auto 80px;padding:0 20px;text-align:center}}
.steps{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:0}}
.step{{padding:24px}}
.step-num{{width:40px;height:40px;border-radius:50%;background:#1a0a2e;border:2px solid #a855f7;color:#a855f7;font-weight:800;font-size:16px;display:flex;align-items:center;justify-content:center;margin:0 auto 16px}}
.step h4{{font-size:15px;font-weight:600;margin-bottom:6px}}
.step p{{color:#666;font-size:13px}}
.code-section{{max-width:900px;margin:0 auto 80px;padding:0 20px}}
.code-tabs{{display:flex;gap:8px;margin-bottom:0;border-bottom:1px solid #1f1f1f}}
.tab{{padding:10px 18px;font-size:13px;color:#666;border-bottom:2px solid transparent}}
.tab.active{{color:#a855f7;border-bottom-color:#a855f7}}
pre{{background:#0d0d0d;border:1px solid #1f1f1f;border-top:none;border-radius:0 0 10px 10px;padding:28px;overflow-x:auto;font-size:13px;color:#ccc;line-height:1.8;margin:0 0 80px}}
.k{{color:#a855f7}}.s{{color:#22d3ee}}.n{{color:#fb923c}}
.signup-section{{max-width:540px;margin:0 auto 80px;padding:0 20px;text-align:center}}
.signup-section h2{{font-size:32px;font-weight:700;margin-bottom:10px;text-align:center}}
.signup-section p{{color:#777;margin-bottom:28px;font-size:15px}}
.form-row{{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}}
.form-row input{{flex:1;min-width:240px;background:#111;border:1px solid #2a2a2a;color:#fff;padding:13px 16px;border-radius:9px;font-size:15px;transition:border-color .2s}}
.form-row input:focus{{outline:none;border-color:#a855f7}}
.form-row button{{background:#a855f7;color:#fff;border:none;padding:13px 24px;border-radius:9px;font-size:15px;font-weight:700;cursor:pointer;transition:background .2s}}
.form-row button:hover{{background:#9333ea}}
.msg{{margin-top:16px;padding:12px 16px;border-radius:8px;font-size:14px;display:none}}
.msg.success{{background:#0a1f0a;border:1px solid #22c55e;color:#22c55e}}
.msg.error{{background:#1f0a0a;border:1px solid #ef4444;color:#ef4444}}
.trust-badges{{display:flex;justify-content:center;gap:24px;flex-wrap:wrap;margin-top:20px}}
.trust-badge{{font-size:12px;color:#555;display:flex;align-items:center;gap:5px}}
.pricing{{max-width:1020px;margin:0 auto 80px;padding:0 20px}}
.plans{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px}}
.plan{{background:#111;border:1px solid #1f1f1f;border-radius:14px;padding:32px;position:relative;transition:border-color .2s}}
.plan:hover{{border-color:#3b1a6e}}
.plan.featured{{border-color:#a855f7;background:#130a20}}
.plan-badge{{position:absolute;top:-13px;left:50%;transform:translateX(-50%);background:#a855f7;color:#fff;padding:4px 18px;border-radius:20px;font-size:11px;font-weight:700;white-space:nowrap}}
.plan-name{{font-size:17px;font-weight:700;margin-bottom:4px;color:#ccc}}
.plan-price{{font-size:40px;font-weight:800;color:#fff;margin:14px 0 4px;letter-spacing:-1px}}
.plan-price span{{font-size:16px;color:#555;font-weight:400}}
.plan-limit{{color:#666;font-size:13px;margin-bottom:24px}}
.plan ul{{list-style:none;margin-bottom:28px}}
.plan ul li{{padding:7px 0;font-size:14px;color:#aaa;display:flex;align-items:center;gap:8px}}
.plan ul li::before{{content:"â";color:#a855f7;font-weight:700;flex-shrink:0}}
.plan a{{display:block;text-align:center;padding:13px;border-radius:9px;font-weight:700;font-size:15px;text-decoration:none;transition:all .2s}}
.btn-pp{{background:#a855f7;color:#fff}}
.btn-pp:hover{{background:#9333ea}}
.btn-ps{{border:1px solid #2a2a2a;color:#ccc}}
.btn-ps:hover{{border-color:#555;color:#fff}}
.faq{{max-width:720px;margin:0 auto 80px;padding:0 20px}}
.faq-item{{border-bottom:1px solid #1a1a1a;padding:20px 0}}
.faq-q{{font-size:16px;font-weight:600;cursor:pointer;display:flex;justify-content:space-between;align-items:center;color:#ddd}}
.faq-q:hover{{color:#a855f7}}
.faq-a{{color:#666;font-size:14px;line-height:1.7;margin-top:12px;display:none}}
.faq-a.open{{display:block}}
.final-cta{{text-align:center;padding:80px 20px;background:linear-gradient(180deg,transparent,#0f0520 50%,transparent)}}
.final-cta h2{{font-size:40px;font-weight:800;margin-bottom:16px;text-align:center}}
.final-cta p{{color:#777;font-size:17px;margin-bottom:36px}}
footer{{border-top:1px solid #1a1a1a;padding:32px 20px;text-align:center;color:#444;font-size:13px}}
footer a{{color:#666;text-decoration:none}}
footer a:hover{{color:#fff}}
@media(max-width:640px){{h1{{font-size:36px}}.stats-bar{{gap:28px}}nav{{padding:14px 20px}}}}
</style>
</head>
<body>
<nav>
  <a href="/" class="logo">StackSight</a>
  <div class="nav-links">
    <a href="/docs">Docs</a>
    <a href="/demo/stripe.com">Demo</a>
    <a href="#pricing">Pricing</a>
    <a href="/login" class="btn-login">Sign In</a>
  </div>
</nav>
<div class="hero">
  <div class="badge">ð v{VERSION} &nbsp;Â·&nbsp; Live API</div>
  <h1>Turn any domain into<br><span>B2B sales intelligence</span></h1>
  <p>Real-time hiring intent signals, deterministic tech stack detection, and bulk enrichment â all in one REST API.</p>
  <div class="cta-group">
    <a href="#signup" class="btn-primary">ð Get Free API Key</a>
    <a href="/demo/stripe.com" class="btn-secondary">Live Demo â</a>
  </div>
</div>
<div class="stats-bar">
  <div class="stat"><div class="stat-num">50M+</div><div class="stat-label">Job postings tracked</div></div>
  <div class="stat"><div class="stat-num">50ms</div><div class="stat-label">Avg response (cached)</div></div>
  <div class="stat"><div class="stat-num">20+</div><div class="stat-label">Tech signals detected</div></div>
  <div class="stat"><div class="stat-num">3</div><div class="stat-label">Lines to integrate</div></div>
</div>
<div class="use-cases">
  <h2>Built for revenue teams</h2>
  <p class="sub">Know who's ready to buy before they raise their hand.</p>
  <div class="use-grid">
    <div class="use-card"><div class="use-icon">ð¯</div><h3>Hiring Intent</h3><p>When a company posts 10 new sales roles, that's a buying signal. StackSight surfaces it instantly.</p></div>
    <div class="use-card"><div class="use-icon">ð§¬</div><h3>Tech Stack Intel</h3><p>Know if a prospect runs Salesforce, HubSpot, or your competitor before your first call.</p></div>
    <div class="use-card"><div class="use-icon">ð¦</div><h3>Bulk Enrichment</h3><p>Enrich your entire CRM overnight. 50 domains per request, Redis-cached for speed.</p></div>
    <div class="use-card"><div class="use-icon">â¡</div><h3>CRM Automation</h3><p>Pipe signals directly into your CRM or Slack. Trigger sequences when companies show intent.</p></div>
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

<span class="k">"domain"</span>: <span class="s">"stripe.com"</span>,
<span class="k">"active_jobs"</span>: <span class="n">142</span>,
<span class="k">"growth_30d"</span>: <span class="s">"+18%"</span>,
<span class="k">"tech_stack"</span>: [<span class="s">"React"</span>, <span class="s">"AWS"</span>, <span class="s">"Stripe"</span>],
<span class="k">"hiring_signal"</span>: <span class="s">"high"</span></pre>
</div>
<div class="signup-section" id="signup">
  <h2>Start for free</h2>
  <p>10 lookups Â· no credit card Â· instant delivery</p>
  <div class="form-row">
    <input type="email" id="email-input" placeholder="you@company.com" autocomplete="email">
    <button onclick="signup()">Get My Free Key</button>
  </div>
  <div class="msg" id="signup-msg"></div>
  <div class="trust-badges">
    <span class="trust-badge">ð No credit card</span>
    <span class="trust-badge">â¡ Instant delivery</span>
    <span class="trust-badge">ð« No spam ever</span>
  </div>
</div>
<div class="pricing" id="pricing">
  <h2>Simple Pricing</h2>
  <p class="sub">Scale as you grow. Cancel any time.</p>
  <div class="plans">
    <div class="plan">
      <div class="plan-name">Free</div>
      <div class="plan-price">$0<span>/mo</span></div>
      <div class="plan-limit">10 requests total</div>
      <ul><li>10 API requests</li><li>JSON responses</li><li>Tech stack detection</li><li>Community support</li></ul>
      <a href="#signup" class="btn-ps">Get Started Free</a>
    </div>
    <div class="plan featured">
      <div class="plan-badge">MOST POPULAR</div>
      <div class="plan-name">Pro</div>
      <div class="plan-price">$49<span>/mo</span></div>
      <div class="plan-limit">5,000 requests/month</div>
      <ul><li>5,000 API requests/month</li><li>20 req/min rate limit</li><li>Bulk API (50 domains)</li><li>Redis-cached responses</li><li>Priority support</li></ul>
      <a href="/checkout/pro" class="btn-pp">Get Pro â</a>
    </div>
    <div class="plan">
      <div class="plan-name">Business</div>
      <div class="plan-price">$199<span>/mo</span></div>
      <div class="plan-limit">50,000 requests/month</div>
      <ul><li>50,000 API requests/month</li><li>20 req/min rate limit</li><li>Bulk API (50 domains)</li><li>Webhook support</li><li>Dedicated support</li></ul>
      <a href="/checkout/business" class="btn-pp">Get Business â</a>
    </div>
  </div>
</div>
<div class="faq">
  <h2>Frequently Asked Questions</h2>
  <div class="faq-item"><div class="faq-q" onclick="toggleFaq(this)"><span>What is hiring intent data?</span><span>+</span></div><div class="faq-a">Hiring intent data tells you when a company is actively growing by tracking their job postings. When a company posts multiple new roles it is a strong signal they have budget and momentum. StackSight captures this in real time.</div></div>
  <div class="faq-item"><div class="faq-q" onclick="toggleFaq(this)"><span>How accurate is the tech stack detection?</span><span>+</span></div><div class="faq-a">Very accurate. We parse actual HTML script tags and headers, not guesses. If a company uses React, we see the React bundle in their page source. 100% deterministic.</div></div>
  <div class="faq-item"><div class="faq-q" onclick="toggleFaq(this)"><span>How fresh is the data?</span><span>+</span></div><div class="faq-a">Results are cached for 7 days in Redis. For most use cases this is ideal. Cache misses trigger a live scrape that returns in seconds.</div></div>
  <div class="faq-item"><div class="faq-q" onclick="toggleFaq(this)"><span>Can I use this in my CRM?</span><span>+</span></div><div class="faq-a">Yes. Our REST API returns structured JSON that integrates with any CRM, data warehouse, or automation tool. Many customers pipe signals directly into Salesforce, HubSpot, or Clay.</div></div>
  <div class="faq-item"><div class="faq-q" onclick="toggleFaq(this)"><span>What happens when I hit my limit?</span><span>+</span></div><div class="faq-a">You will get a 429 response with a clear error message. Upgrade any time from your dashboard. Your API key stays the same.</div></div>
</div>
<div class="final-cta">
  <h2>Know who is growing.<br>Before your competitors do.</h2>
  <p>Free tier available. No credit card required.</p>
  <a href="#signup" class="btn-primary" style="font-size:18px;padding:16px 40px">ð Get Free API Key</a>
</div>
<footer>
  <div style="margin-bottom:12px">
    <a href="/docs">Docs</a> &nbsp;Â·&nbsp; <a href="/demo/stripe.com">Demo</a> &nbsp;Â·&nbsp; <a href="#pricing">Pricing</a> &nbsp;Â·&nbsp; <a href="/login">Sign In</a> &nbsp;Â·&nbsp; <a href="mailto:ngryn@stacksight.org">Contact</a>
  </div>
  <div>Â© 2025 StackSight Â· <a href="https://x.com/StackSightOrg">@StackSightOrg</a></div>
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


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_session_email(request):
        return RedirectResponse("/dashboard")
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Sign In - StackSight</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#111;border:1px solid #1f1f1f;border-radius:16px;padding:48px 40px;width:100%;max-width:420px;margin:20px}
.logo{font-size:24px;font-weight:700;color:#a855f7;text-align:center;margin-bottom:8px}
.subtitle{text-align:center;color:#666;font-size:14px;margin-bottom:32px}
h2{font-size:22px;font-weight:700;text-align:center;margin-bottom:8px}
p.desc{color:#888;font-size:14px;text-align:center;margin-bottom:24px}
input{width:100%;background:#0a0a0a;border:1px solid #333;color:#fff;padding:12px 16px;border-radius:8px;font-size:15px;margin-bottom:12px}
input:focus{outline:none;border-color:#a855f7}
button{width:100%;background:#a855f7;color:#fff;border:none;padding:13px;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer}
button:hover{background:#9333ea}
.msg{margin-top:16px;padding:12px;border-radius:8px;font-size:14px;display:none;text-align:center}
.msg.success{background:#0f2a0f;border:1px solid #22c55e;color:#22c55e}
.msg.error{background:#2a0f0f;border:1px solid #ef4444;color:#ef4444}
.back{display:block;text-align:center;margin-top:20px;color:#666;font-size:13px;text-decoration:none}
.back:hover{color:#fff}
</style></head>
<body>
<div class="card">
  <div class="logo">StackSight</div>
  <div class="subtitle">B2B Hiring Intent API</div>
  <h2>Sign In</h2>
  <p class="desc">Enter your email and we'll send you a secure login link - no password needed.</p>
  <input type="email" id="email" placeholder="you@company.com" autofocus>
  <button onclick="sendLink()">Send Login Link</button>
  <div class="msg" id="msg"></div>
  <a href="/" class="back">Back to StackSight</a>
</div>
<script>
async function sendLink() {
  const email = document.getElementById('email').value.trim();
  const msg = document.getElementById('msg');
  if (!email || !email.includes('@')) { msg.className='msg error'; msg.style.display='block'; msg.textContent='Please enter a valid email.'; return; }
  msg.className='msg'; msg.style.display='block'; msg.textContent='Sending login link...';
  try {
    const r = await fetch('/login', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});
    const d = await r.json();
    if (r.ok) { msg.className='msg success'; msg.textContent='Check your email for a login link!'; }
    else { msg.className='msg error'; msg.textContent=d.detail||'Something went wrong.'; }
  } catch(e) { msg.className='msg error'; msg.textContent='Network error. Please try again.'; }
}
document.getElementById('email').addEventListener('keypress', e => { if(e.key==='Enter') sendLink(); });
</script>
</body></html>""")


@app.post("/signup")
async def signup_post(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT api_key FROM api_keys WHERE email=%s AND plan='free' LIMIT 1", (email,))
    existing = cur.fetchone()
    if existing:
        return JSONResponse({"ok": True, "msg": "Check your inbox â we sent your API key again."})
    token = secrets.token_urlsafe(32)
    cur.execute("""
        INSERT INTO pending_signups (email, token)
        VALUES (%s, %s)
        ON CONFLICT (email) DO UPDATE SET token=EXCLUDED.token, created_at=NOW(), used=FALSE
    """, (email, token))
    conn.commit()
    cur.close()
    conn.close()
    background_tasks.add_task(send_verification_email, email, token)
    return JSONResponse({"ok": True, "msg": "Check your inbox to get your free API key!"})

@app.get("/verify-email")
async def verify_email(token: str, background_tasks: BackgroundTasks):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT email, used, created_at FROM pending_signups WHERE token=%s LIMIT 1", (token,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return HTMLResponse("<h2>Invalid or expired link.</h2>", status_code=400)
    email, used, created_at = row
    if used:
        cur.close(); conn.close()
        return HTMLResponse("<h2>This link has already been used. Check your inbox for your API key.</h2>")
    from datetime import timezone
    age = (datetime.now(timezone.utc) - created_at.replace(tzinfo=timezone.utc)).total_seconds()
    if age > 86400:
        cur.close(); conn.close()
        return HTMLResponse("<h2>This link has expired. Please sign up again at stacksight.org.</h2>", status_code=400)
    cur.execute("UPDATE pending_signups SET used=TRUE WHERE token=%s", (token,))
    conn.commit()
    cur.close(); conn.close()
    api_key = provision_api_key(email, "free")
    background_tasks.add_task(send_api_key_email, email, api_key, "free")
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Welcome to StackSight!</title>
<style>body{{font-family:-apple-system,sans-serif;background:#0a0a0a;color:#e5e5e5;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.card{{background:#111;border:1px solid #222;border-radius:16px;padding:48px;max-width:480px;text-align:center}}
h1{{color:#a855f7;margin-bottom:8px}}p{{color:#999}}
.key{{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:16px;font-family:monospace;font-size:14px;color:#a855f7;word-break:break-all;margin:24px 0}}
.btn{{display:inline-block;background:#a855f7;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:700;margin-top:8px}}</style>
</head><body><div class="card">
<h1>You are in!</h1>
<p>Your free StackSight API key:</p>
<div class="key">{{api_key}}</div>
<p style="font-size:13px;color:#666">We also emailed this to <strong style="color:#ccc">{{email}}</strong></p>
<a href="/docs" class="btn">View API Docs</a>
<a href="/dashboard" class="btn" style="background:#222;margin-left:8px">Dashboard</a>
</div></body></html>""")

@app.post("/login")
async def login_post(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM api_keys WHERE email=%s AND active=TRUE", (email,))
    if not cur.fetchone():
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="No account found for this email. Sign up first.")
    token = secrets.token_urlsafe(48)
    expires = datetime.utcnow() + timedelta(minutes=15)
    cur.execute(
        "INSERT INTO magic_links (token, email, expires_at) VALUES (%s, %s, %s)",
        (token, email, expires)
    )
    conn.commit()
    cur.close(); conn.close()
    background_tasks.add_task(send_magic_link_email, email, token)
    return {"message": "Login link sent"}


@app.get("/auth")
async def auth(token: str, request: Request):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT email, used, expires_at FROM magic_links WHERE token=%s", (token,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return HTMLResponse("<h2 style='font-family:sans-serif;color:#ef4444;padding:40px'>Invalid or expired login link.</h2>", status_code=400)
    email, used, expires_at = row
    if used or datetime.utcnow() > expires_at:
        cur.close(); conn.close()
        return HTMLResponse("<h2 style='font-family:sans-serif;color:#ef4444;padding:40px'>This login link has expired or already been used. <a href='/login'>Request a new one</a>.</h2>", status_code=400)
    cur.execute("UPDATE magic_links SET used=TRUE WHERE token=%s", (token,))
    conn.commit()
    cur.close(); conn.close()
    response = RedirectResponse("/dashboard", status_code=302)
    create_session(email, response)
    return response


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("ss_session")
    if token:
        conn = get_db()
        @app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, session_token: str = Cookie(default=None)):
    if not session_token:
        return RedirectResponse(url="/login", status_code=302)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT email FROM sessions WHERE session_token=%s AND active=TRUE AND expires_at > NOW()", (session_token,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return RedirectResponse(url="/login", status_code=302)
    email = row[0]
    cur.execute("SELECT api_key, plan, requests_used, requests_limit, created_at FROM api_keys WHERE email=%s AND active=TRUE", (email,))
    key_row = cur.fetchone()
    conn.close()
    if not key_row:
        return RedirectResponse(url="/", status_code=302)
    api_key, plan, used, limit_val, created_at = key_row
    masked = api_key[:8] + ("*" * 24) + api_key[-4:]
    pct = round((used / limit_val) * 100) if limit_val else 0
    created_str = created_at.strftime("%B %d, %Y") if created_at else "N/A"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>My Account - StackSight</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;min-height:100vh}}
nav{{display:flex;align-items:center;justify-content:space-between;padding:16px 32px;border-bottom:1px solid #1f1f1f;background:#0d0d0d}}
.logo{{font-size:20px;font-weight:700;color:#fff;text-decoration:none}}.logo span{{color:#6366f1}}
.nav-links{{display:flex;gap:12px;align-items:center}}
.btn-logout{{background:#1a1a1a;border:1px solid #333;color:#fff;padding:7px 16px;border-radius:7px;text-decoration:none;font-size:14px}}
.btn-logout:hover{{border-color:#555}}
.container{{max-width:800px;margin:48px auto;padding:0 24px}}
h1{{font-size:28px;font-weight:700;margin-bottom:8px}}
.subtitle{{color:#888;margin-bottom:40px;font-size:15px}}
.card{{background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:28px;margin-bottom:20px}}
.card h2{{font-size:14px;font-weight:600;color:#888;text-transform:uppercase;letter-spacing:.05em;margin-bottom:20px}}
.field{{margin-bottom:20px}}
.field label{{display:block;font-size:13px;color:#888;margin-bottom:6px}}
.field .value{{font-size:15px;color:#e5e5e5;font-family:monospace;background:#0d0d0d;border:1px solid #222;border-radius:8px;padding:10px 14px;display:flex;align-items:center;justify-content:space-between;gap:12px}}
.field .value span{{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.copy-btn{{background:#6366f1;border:none;color:#fff;padding:5px 12px;border-radius:6px;cursor:pointer;font-size:12px;white-space:nowrap;flex-shrink:0}}
.copy-btn:hover{{background:#4f46e5}}
.badge{{display:inline-block;background:#1a1a2e;color:#6366f1;border:1px solid #2d2d5e;padding:4px 12px;border-radius:20px;font-size:13px;font-weight:600;text-transform:capitalize}}
.usage-bar{{background:#1a1a1a;border-radius:6px;height:8px;margin-top:8px;overflow:hidden}}
.usage-fill{{background:#6366f1;height:100%;border-radius:6px;transition:width .3s}}
.usage-label{{display:flex;justify-content:space-between;font-size:13px;color:#888;margin-top:6px}}
.upgrade-btn{{display:inline-block;background:#6366f1;color:#fff;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;margin-top:16px}}
.upgrade-btn:hover{{background:#4f46e5}}
</style>
</head>
<body>
<nav>
  <a href="/" class="logo">Stack<span>Sight</span></a>
  <div class="nav-links">
    <a href="/dashboard" style="color:#6366f1;text-decoration:none;font-size:14px;font-weight:500">My Account</a>
    <a href="/logout" class="btn-logout">Sign Out</a>
  </div>
</nav>
<div class="container">
  <h1>My Account</h1>
  <p class="subtitle">Signed in as {email}</p>

  <div class="card">
    <h2>API Key</h2>
    <div class="field">
      <label>Your API Key</label>
      <div class="value">
        <span id="api-key-display">{masked}</span>
        <button class="copy-btn" onclick="copyKey('{api_key}')">Copy</button>
      </div>
    </div>
    <div class="field">
      <label>Member Since</label>
      <div style="font-size:15px;color:#e5e5e5">{created_str}</div>
    </div>
  </div>

  <div class="card">
    <h2>Plan & Usage</h2>
    <div class="field">
      <label>Current Plan</label>
      <span class="badge">{plan}</span>
    </div>
    <div class="field">
      <label>API Requests This Month</label>
      <div class="usage-bar"><div class="usage-fill" style="width:{pct}%"></div></div>
      <div class="usage-label"><span>{used} used</span><span>{limit_val} limit</span></div>
    </div>
    {"" if plan != "free" else '<a href="/checkout/pro" class="upgrade-btn">Upgrade to Pro</a>'}
  </div>

  <div class="card">
    <h2>Quick Start</h2>
    <div class="field">
      <label>Example API Call</label>
      <div class="value" style="font-size:13px">
        <span>curl https://stacksight.org/scrape?domain=stripe.com -H "X-API-Key: YOUR_KEY"</span>
      </div>
    </div>
  </div>
</div>
<script>
function copyKey(key) {{
  navigator.clipboard.writeText(key).then(function(){{
    var btn = event.target;
    btn.textContent = 'Copied!';
    setTimeout(function(){{btn.textContent='Copy';}}, 2000);
  }});
}}
</script>
</body></html>"""
    return HTMLResponse(content=html)ââââââââââââ
# ROUTES â FREE SIGNUP
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

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
        cur.close(); conn.close()
        raise HTTPException(status_code=400, detail="An API key already exists for this email. Sign in to view it.")
    cur.execute("SELECT 1 FROM pending_signups WHERE email=%s AND used=FALSE", (email,))
    if cur.fetchone():
        cur.close(); conn.close()
        raise HTTPException(status_code=400, detail="Verification email already sent. Please check your inbox.")
    token = secrets.token_urlsafe(32)
    cur.execute(
        "INSERT INTO pending_signups (email, token) VALUES (%s, %s) ON CONFLICT (email) DO UPDATE SET token=%s, used=FALSE, created_at=NOW()",
        (email, token, token)
    )
    conn.commit()
    cur.close(); conn.close()
    background_tasks.add_task(send_verification_email, email, token)
    return {"message": "Verification email sent"}


@app.get("/verify-email", response_class=HTMLResponse)
async def verify_email(token: str, background_tasks: BackgroundTasks):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT email, used FROM pending_signups WHERE token=%s", (token,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close()
        return HTMLResponse("<h2 style='font-family:sans-serif;padding:40px'>Invalid or expired link.</h2>", status_code=400)
    email, used = row
    if used:
        cur.close(); conn.close()
        return HTMLResponse("<h2 style='font-family:sans-serif;padding:40px'>This link has already been used. <a href='/login'>Sign in here</a>.</h2>", status_code=400)
    cur.execute("UPDATE pending_signups SET used=TRUE WHERE token=%s", (token,))
    conn.commit()
    cur.close(); conn.close()
    background_tasks.add_task(provision_api_key, email, "free")
    return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Email Verified - StackSight</title>
<style>body{font-family:-apple-system,sans-serif;background:#0a0a0a;color:#e5e5e5;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:48px;text-align:center;max-width:480px}
h1{color:#a855f7;margin-bottom:12px}p{color:#888;margin-bottom:24px}
.btn{display:inline-block;background:#a855f7;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;margin:8px}
.btn2{display:inline-block;background:transparent;border:1px solid #333;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;margin:8px}</style></head>
<body><div class="box">
<h1>Email Verified!</h1>
<p>Your free API key is on its way. Check your inbox - it should arrive within a minute.</p>
<a href="/login" class="btn">Sign In to Dashboard</a>
<a href="/" class="btn2">Back to Home</a>
</div></body></html>""")


# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# ROUTES â API
# âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@app.get("/demo/{domain}", response_class=HTMLResponse)
async def demo(domain: str):
    clean = domain.lower().strip().rstrip("/").replace("https://", "").replace("http://", "")
    data = DEMO_DATA.get(clean, {"company_name": clean.split(".")[0].title(), "is_hiring": True, "engineering_roles": ["Software Engineer"], "sales_roles": ["Account Executive"], "detected_tech_stack": ["JavaScript", "AWS"]})
    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Demo: {clean} - StackSight</title>
<style>body{{font-family:-apple-system,sans-serif;background:#0a0a0a;color:#e5e5e5;padding:40px 20px}}
.box{{max-width:700px;margin:0 auto}}h1{{color:#a855f7;margin-bottom:16px}}
pre{{background:#111;border:1px solid #1f1f1f;border-radius:10px;padding:24px;overflow-x:auto;font-size:14px}}
a{{color:#a855f7}}</style></head>
<body><div class="box"><h1>StackSight Demo: {clean}</h1>
<p style="margin-bottom:20px"><a href="/">Home</a> | <a href="/#signup">Get free API key</a> | <a href="/login">Sign in</a></p>
<pre>{json.dumps({{"source":"demo","data":data}},indent=2)}</pre>
<p style="margin-top:16px;color:#666">This is cached demo data. <a href="/#signup">Sign up free</a> to analyze any domain live.</p>
</div></body></html>""")


@app.get("/scrape")
async def scrape(domain: str, x_api_key: str = Header(None)):
    api_key, plan = verify_api_key(x_api_key)
    cache_key = f"domain:{domain}"
    cached = redis_client.get(cache_key)
    if cached:
        increment_usage(api_key)
        return {"source": "cache", "data": json.loads(cached)}
    raw_text, url, status = await scrape_page(domain)
    extracted = extract_with_openai(raw_text)
    redis_client.setex(cache_key, 604800, json.dumps(extracted))
    increment_usage(api_key)
    return {"source": "live", "scrape_metadata": {"url": url, "status": status}, "data": extracted}


@app.get("/analyze/{domain}")
async def analyze(domain: str, x_api_key: str = Header(None)):
    return await scrape(domain=domain, x_api_key=x_api_key)


@app.get("/usage")
async def usage(x_api_key: str = Header(None), key: str = None):
    k = x_api_key or key
    if not k:
        raise HTTPException(status_code=401, detail="Missing API key")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT plan, requests_used, requests_limit, created_at FROM api_keys WHERE api_key=%s", (k,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    plan, used, limit, created = row
    return {"plan": plan, "requests_used": used, "requests_limit": limit, "requests_remaining": limit - used, "created_at": str(created)}


@app.get("/me")
async def me(x_api_key: str = Header(None)):
    return await usage(x_api_key=x_api_key)


@app.get("/checkout/{plan}")
async def checkout(plan: str):
    if plan not in STRIPE_PRICES:
        raise HTTPException(status_code=400, detail="Invalid plan")
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{"price": STRIPE_PRICES[plan], "quantity": 1}],
        mode="subscription",
        success_url=f"{BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{BASE_URL}/#pricing",
        metadata={"plan": plan},
    )
    return RedirectResponse(session.url)


@app.get("/success", response_class=HTMLResponse)
async def success():
    return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Payment Successful - StackSight</title>
<style>body{font-family:-apple-system,sans-serif;background:#0a0a0a;color:#e5e5e5;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:48px;text-align:center;max-width:500px}
h1{color:#22c55e;margin-bottom:12px}p{color:#888;margin-bottom:24px}
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
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature")
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        email = session.get("customer_details", {}).get("email") or session.get("customer_email")
        plan = session.get("metadata", {}).get("plan", "pro")
        customer_id = session.get("customer")
        session_id = session.get("id")
        if email:
            background_tasks.add_task(provision_api_key, email, plan, customer_id, session_id)
    return {"status": "ok"}


@app.get("/trending")
async def trending():
    return {"domains": list(DEMO_DATA.keys())}


@app.get("/health")
async def health():
    try:
        redis_client.ping(); redis_ok = True
    except Exception:
        redis_ok = False
    return {"status": "ok", "version": VERSION, "redis": redis_ok}


@app.post("/admin/create-key")
async def admin_create_key(request: Request):
    auth = request.headers.get("X-Cron-Secret", "")
    if auth != CRON_SECRET:
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
 
