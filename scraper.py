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
import redis as redis_lib
import stripe
import uvicorn
from fastapi import BackgroundTasks, Cookie, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
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
}
PLAN_LIMITS = {"free": 25, "starter": 500, "pro": 5000, "business": 50000}

#  Rate limiting 
_rate_limit: dict = {}
RATE_LIMIT_WINDOW = 60
RATE_LIMITS = {"free": 10, "starter": 60, "pro": 300, "business": 1000}

#  Redis / App 
redis_client = redis_lib.from_url(REDIS_URL, decode_responses=True)
app = FastAPI(title="StackSight API", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)

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

EXTRACTION_PROMPT = (
    "You are a B2B data extraction engine. Given raw text from a company careers page, "
    "extract structured data. Return ONLY valid JSON. Schema: "
    "{company_name: string, is_hiring: boolean, engineering_roles: [string], "
    "sales_roles: [string], detected_tech_stack: [string]}"
)

#  Database 
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
    #  Migrations (safe to re-run) 
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
    cur.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS usage_reset_at TIMESTAMP")
    conn.commit()
    cur.close()
    conn.close()

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
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/"
    )
    return token

#  Rate limiting 
def check_rate_limit(api_key: str, plan: str = "free"):
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    if api_key not in _rate_limit:
        _rate_limit[api_key] = []
    _rate_limit[api_key] = [t for t in _rate_limit[api_key] if t > window_start]
    limit = RATE_LIMITS.get(plan, 10)
    if len(_rate_limit[api_key]) >= limit:
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded. Max {limit} requests/minute.")
    _rate_limit[api_key].append(now)

#  API key auth 
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
    check_rate_limit(x_api_key, plan)
    return x_api_key, plan

def increment_usage(api_key: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE api_keys SET requests_used = requests_used + 1 WHERE api_key=%s", (api_key,))
    conn.commit()
    cur.close()
    conn.close()

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
    if plan != "free":
        # Paid provisioning: upgrade the existing row for this email if one exists.
        cur.execute("""
            UPDATE api_keys
            SET plan=%s, requests_limit=%s, requests_used=0, usage_reset_at=NOW(), active=TRUE,
                stripe_customer_id=COALESCE(%s, stripe_customer_id),
                stripe_session_id=COALESCE(%s, stripe_session_id)
            WHERE email=%s
        """, (plan, limit, stripe_customer_id, stripe_session_id, email))
        if cur.rowcount > 0:
            cur.execute("SELECT api_key FROM api_keys WHERE email=%s LIMIT 1", (email,))
            row = cur.fetchone()
            if row and row[0]:
                api_key = row[0]
        else:
            cur.execute("""
                INSERT INTO api_keys (api_key, email, plan, requests_limit, stripe_customer_id, stripe_session_id, active)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE)
            """, (api_key, email, plan, limit, stripe_customer_id, stripe_session_id))
    else:
        # Free signup: deduplicate on email -- reuse the existing key if the user already has one.
        cur.execute("SELECT api_key FROM api_keys WHERE email=%s LIMIT 1", (email,))
        row = cur.fetchone()
        if row and row[0]:
            api_key = row[0]
        else:
            cur.execute("""
                INSERT INTO api_keys (api_key, email, plan, requests_limit, active)
                VALUES (%s, %s, %s, %s, TRUE)
            """, (api_key, email, plan, limit))
    conn.commit()
    cur.close()
    conn.close()
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

#  Startup 
@app.on_event("startup")
async def startup():
    init_db()


# 
# ROUTES  PUBLIC
# 

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
<meta name="description" content="StackSight API documentation. Integrate real-time hiring intent signals and tech stack detection into your B2B workflows. Free tier available.">
<meta name="robots" content="index,follow">
<meta property="og:title" content="StackSight API Documentation">
<meta property="og:description" content="Full reference for the StackSight API -- hiring intent signals, tech stack detection, and domain enrichment endpoints.">
<meta property="og:url" content="https://stacksight.org/docs">
<meta property="og:type" content="website">
<link rel="canonical" href="https://stacksight.org/docs">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;line-height:1.6;display:flex;min-height:100vh}
nav{padding:18px 40px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1a1a1a;position:fixed;top:0;left:0;right:0;background:rgba(10,10,10,0.95);backdrop-filter:blur(10px);z-index:100}
.logo{font-size:20px;font-weight:700;color:#a855f7;text-decoration:none}
.nav-links a{color:#c0c0c0;text-decoration:none;margin-left:24px;font-size:14px}.nav-links a:hover{color:#fff}
.nav-links .btn-login{background:#1a1a1a;border:1px solid #333;color:#fff;padding:7px 16px;border-radius:7px}
.sidebar{width:240px;flex-shrink:0;position:fixed;top:61px;left:0;bottom:0;overflow-y:auto;border-right:1px solid #1a1a1a;padding:24px 0}
.sidebar-section{padding:8px 20px;font-size:11px;font-weight:700;color:#666;text-transform:uppercase;letter-spacing:.8px;margin-top:16px}
.sidebar a{display:block;padding:8px 20px;font-size:13px;color:#aaa;text-decoration:none;border-left:2px solid transparent}
.sidebar a:hover{color:#ccc;background:#111}.sidebar a.active{color:#a855f7;border-left-color:#a855f7;background:#0f0518}
.main{margin-left:240px;margin-top:61px;flex:1;padding:48px 60px;max-width:900px}
h1{font-size:36px;font-weight:800;margin-bottom:8px}
h2{font-size:22px;font-weight:700;margin:48px 0 16px;padding-top:48px;border-top:1px solid #1a1a1a}
p{color:#b0b0b0;margin-bottom:16px;font-size:15px}
.endpoint{background:#0d0d0d;border:1px solid #1f1f1f;border-radius:12px;margin-bottom:24px;overflow:hidden}
.endpoint-header{display:flex;align-items:center;gap:12px;padding:16px 20px;background:#111;border-bottom:1px solid #1f1f1f}
.method{font-size:12px;font-weight:700;padding:3px 10px;border-radius:5px}
.get{background:#0a2a1a;color:#22c55e;border:1px solid #166534}
.post{background:#1a1a0a;color:#eab308;border:1px solid #713f12}
.path{font-family:monospace;font-size:15px;color:#e5e5e5;font-weight:600}
.endpoint-body{padding:20px}
table{width:100%;border-collapse:collapse;margin-bottom:16px;font-size:14px}
th{text-align:left;padding:10px 12px;background:#111;color:#777;font-size:12px;text-transform:uppercase;border-bottom:1px solid #1f1f1f}
td{padding:10px 12px;border-bottom:1px solid #0f0f0f;color:#bbb}
td:first-child{font-family:monospace;color:#a855f7;font-size:13px}
pre{background:#050505;border:1px solid #1a1a1a;border-radius:8px;padding:20px;padding-top:44px;overflow-x:auto;font-size:13px;color:#ccc;line-height:1.7;margin:12px 0;position:relative}
pre::before{content:"";position:absolute;top:0;left:0;right:0;height:30px;background:#0d0d0d;border-bottom:1px solid #1a1a1a;border-radius:7px 7px 0 0}
pre::after{content:"";position:absolute;top:11px;left:14px;width:8px;height:8px;border-radius:50%;background:#ef4444;box-shadow:14px 0 0 #eab308,28px 0 0 #22c55e}
.auth-box{background:#0f0518;border:1px solid #3b1a6e;border-radius:10px;padding:20px;margin-bottom:24px}
.auth-box h4{color:#a855f7;font-size:14px;font-weight:600;margin-bottom:8px}
.limits{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:24px}
.limit-card{background:#111;border:1px solid #1f1f1f;border-radius:8px;padding:16px;text-align:center}
.limit-name{font-size:12px;color:#777;margin-bottom:4px}
.limit-val{font-size:20px;font-weight:700;color:#a855f7}

.hero-demo{{margin-top:32px;max-width:520px;margin-left:auto;margin-right:auto}}
.hero-demo-label{{font-size:13px;color:#888;margin-bottom:10px}}
.hero-demo-input-row{{display:flex;gap:8px}}
.hero-demo-input-row input{{flex:1;padding:12px 16px;background:#1a0a2e;border:1px solid #3b1a6e;border-radius:8px;color:#fff;font-size:15px;outline:none}}
.hero-demo-input-row input:focus{{border-color:#a855f7}}
.hero-demo-input-row button{{padding:12px 20px;background:#a855f7;color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;white-space:nowrap}}
.hero-demo-input-row button:hover{{background:#9333ea}}
#hero-demo-result{{margin-top:10px;font-size:13px;color:#f87171;padding:8px;background:#1a0a2e;border-radius:6px}}
</style>
</head>
<body>
<nav><a href="/" class="logo">StackSight</a><div class="nav-links"><a href="/">Home</a><a href="/demo/stripe.com">Demo</a><a href="/#pricing">Pricing</a><a href="/login" class="btn-login" id="nav-auth-btn">Sign In</a></div></nav>
<script>
(function(){{
  fetch('/usage',{{'credentials':'include'}}).then(r=>{{
    if(r.ok){{r.json().then(d=>{{
      var btn=document.getElementById('nav-auth-btn');
      if(btn){{btn.textContent='My Account';btn.href='/dashboard';}}
    }});}}  
  }}).catch(function(){{}});
}})();
</script>
<div class="sidebar">
  <div class="sidebar-section">Getting Started</div>
  <a href="#quickstart" class="active">Quick Start</a><a href="#auth">Authentication</a><a href="#limits">Rate Limits</a>
  <div class="sidebar-section">Endpoints</div>
  <a href="#enrich">GET /v1/enrich</a><a href="#bulk">POST /v1/bulk</a><a href="#usage">GET /usage</a><a href="#webhooks">Webhooks</a>
  <div class="sidebar-section">Reference</div>
  <a href="#response">Response Schema</a><a href="#errors">Error Codes</a><a href="#sdks">Code Examples</a>
</div>
<div class="main">
  <h1>API Documentation</h1>
  <p style="font-size:17px;color:#bbb;margin-bottom:32px">Turn a domain into a complete company profile -- hiring signals, tech stack, and enrichment data in one API call.</p>
  <h2 id="quickstart" style="border-top:none;margin-top:0;padding-top:0">Quick Start</h2>
  <p>Get your free API key at <a href="/#signup" style="color:#a855f7">stacksight.org</a>, then:</p>
  <pre>curl -X GET "https://stacksight.org/v1/enrich?domain=stripe.com" -H "X-API-Key: ss_your_key"</pre>
  <h2 id="auth">Authentication</h2>
  <div class="auth-box"><h4>X-API-Key Header</h4><p style="color:#888;margin:0">Pass your key in the <code style="color:#a855f7">X-API-Key</code> header. Keys look like <code style="color:#22d3ee">ss_...</code></p></div>
  <h2 id="limits">Rate Limits</h2>
  <div class="limits">
    <div class="limit-card"><div class="limit-name">Free</div><div class="limit-val">10</div><div style="font-size:11px;color:#666">total requests</div></div>
    <div class="limit-card"><div class="limit-name">Pro</div><div class="limit-val">5,000</div><div style="font-size:11px;color:#666">per month</div></div>
    <div class="limit-card"><div class="limit-name">Business</div><div class="limit-val">50,000</div><div style="font-size:11px;color:#666">per month</div></div>
  </div>
  <h2 id="enrich">GET /v1/enrich</h2>
  <div class="endpoint">
    <div class="endpoint-header"><span class="method get">GET</span><span class="path">/v1/enrich</span></div>
    <div class="endpoint-body">
      <table><tr><th>Param</th><th>Type</th><th>Required</th><th>Description</th></tr>
      <tr><td>domain</td><td>string</td><td style="color:#ef4444;font-size:11px;font-weight:700">required</td><td>Domain to enrich, e.g. stripe.com</td></tr></table>
      <pre>curl "https://stacksight.org/v1/enrich?domain=notion.so" -H "X-API-Key: ss_your_key"</pre>
      <h4>Example Response</h4>
      <pre>{
  "domain": "notion.so",
  "source": "live",
  "data": {
    "company_name": "Notion",
    "is_hiring": true,
    "engineering_roles": 12,
    "sales_roles": 4,
    "detected_tech_stack": ["React", "Cloudflare", "Google Analytics", "Intercom"],
    "cached": false
  }
}</pre>
    </div>
  </div>
  <h2 id="bulk">POST /v1/bulk</h2>
  <div class="endpoint">
    <div class="endpoint-header"><span class="method post">POST</span><span class="path">/v1/bulk</span><span style="font-size:11px;background:#1a1a0a;color:#eab308;border:1px solid #713f12;padding:2px 8px;border-radius:4px;margin-left:8px">Pro &amp; Business</span></div>
    <div class="endpoint-body">
      <p style="color:#888;font-size:14px;margin-bottom:12px">Enrich up to 50 domains in a single request. Runs concurrently -- same speed as one. Each domain counts as 1 request against your quota.</p>
      <table><tr><th>Body field</th><th>Type</th><th>Required</th><th>Description</th></tr>
      <tr><td>domains</td><td>array</td><td style="color:#ef4444;font-size:11px;font-weight:700">required</td><td>List of domains to enrich. Max 50.</td></tr></table>
      <pre>curl -X POST "https://stacksight.org/bulk" \
  -H "X-API-Key: ss_your_key" \
  -H "Content-Type: application/json" \
  -d '{"domains": ["stripe.com", "notion.so", "vercel.com"]}'</pre>
      <pre>{
  "results": [
    {"domain": "stripe.com", "source": "cache", "data": {"company_name": "Stripe", "is_hiring": true, ...}},
    {"domain": "notion.so",  "source": "live",  "data": {"company_name": "Notion", "is_hiring": true, ...}},
    {"domain": "vercel.com", "source": "cache", "data": {"company_name": "Vercel", "is_hiring": true, ...}}
  ],
  "count": 3
}</pre>
    </div>
  </div>
  <h2 id="usage">GET /usage</h2>
  <div class="endpoint">
    <div class="endpoint-header"><span class="method get">GET</span><span class="path">/usage</span></div>
    <div class="endpoint-body">
      <p style="color:#888;font-size:14px;margin-bottom:12px">Returns your current plan and usage stats.</p>
      <pre>curl "https://stacksight.org/usage" -H "X-API-Key: ss_your_key"</pre>
      <pre>{
  "plan": "pro",
  "requests_used": 142,
  "requests_limit": 5000,
  "requests_remaining": 4858,
  "created_at": "2026-07-01T12:00:00"
}</pre>
    </div>
  </div>
  <h2 id="response">Response Schema</h2>
  <p>All responses wrap the result in a <code style="color:#a855f7">data</code> object:</p>
  <pre>{
  "source": "cache",  // or "live" for fresh scrape
  "data": {
    "company_name": "Stripe",
    "is_hiring": true,
    "engineering_roles": ["Backend Engineer", "ML Engineer"],
    "sales_roles": ["Account Executive", "Solutions Engineer"],
    "detected_tech_stack": ["React", "AWS", "Cloudflare"]
  }
}</pre>
  <table>
    <tr><th>Field</th><th>Type</th><th>Description</th></tr>
    <tr><td>source</td><td>string</td><td>"cache" or "live" -- whether data was cached or freshly scraped</td></tr>
    <tr><td>data.company_name</td><td>string</td><td>Resolved company name</td></tr>
    <tr><td>data.is_hiring</td><td>boolean</td><td>Whether the company is actively hiring</td></tr>
    <tr><td>data.engineering_roles</td><td>array</td><td>Engineering job titles detected</td></tr>
    <tr><td>data.sales_roles</td><td>array</td><td>Sales job titles detected</td></tr>
    <tr><td>data.detected_tech_stack</td><td>array</td><td>Technologies found on careers/jobs pages</td></tr>
  </table>
    <h2 id="webhooks">Webhooks</h2>
  <div class="endpoint">
    <div class="endpoint-header"><span class="method post">POST</span><span class="path">/webhooks/stripe</span></div>
    <div class="endpoint-body">
      <p>StackSight uses webhooks to handle subscription lifecycle events automatically.</p>
      <table>
        <tr><th>Event</th><th>Effect</th></tr>
        <tr><td><code>checkout.session.completed</code></td><td>Provisions API key and upgrades plan</td></tr>
        <tr><td><code>invoice.payment_succeeded</code></td><td>Resets monthly usage quota on billing cycle</td></tr>
        <tr><td><code>customer.subscription.deleted</code></td><td>Downgrades account to free plan</td></tr>
      </table>
      <p>Webhook signatures are verified using your Stripe webhook secret. Business plan customers can contact <a href="mailto:support@stacksight.org">support@stacksight.org</a> to configure custom webhook destinations.</p>
    </div>
  </div>
<h2 id="errors">Error Codes</h2>
  <table>
    <tr><th>Status</th><th>Meaning</th></tr>
    <tr><td style="color:#fb923c">400</td><td>Missing or invalid domain</td></tr>
    <tr><td style="color:#fb923c">401</td><td>Missing or invalid API key</td></tr>
    <tr><td style="color:#fb923c">429</td><td>Rate limit exceeded</td></tr>
    <tr><td style="color:#fb923c">500</td><td>Scrape failed, please retry</td></tr>
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
.form-row input:focus{{outline:none;border-color:#a855f7}}
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
@media(max-width:640px){{h1{{font-size:36px}}.stats-bar{{gap:28px}}nav{{padding:14px 20px}}.step::after{{display:none}}}}
</style>
</head>
<body>
<nav>
  <a href="/" class="logo">StackSight</a>
  <div class="nav-links">
    <a href="/docs">Docs</a>
    <a href="/demo/stripe.com">Demo</a>
    <a href="#pricing">Pricing</a>
    <a href="/login" class="btn-login" id="nav-auth-btn">Sign In</a>
  </div>
</nav>
<script>
(function(){{
  fetch("/usage",{{credentials:"include"}}).then(r=>{{
    if(r.ok){{r.json().then(d=>{{
      var btn=document.getElementById("nav-auth-btn");
      if(btn){{btn.textContent="My Account";btn.href="/dashboard";}}
    }});}}  
  }}).catch(function(){{}});
}})();
</script>
<div class="hero">
  <div class="badge">Live Data</div>
  <h1>Turn any domain into<br><span>B2B sales intelligence</span></h1>
  <p>Real-time hiring intent signals, deterministic tech stack detection, and bulk enrichment  all in one REST API.</p>
  <div class="cta-group">
    <a href="#signup" class="btn-primary"> Start for Free</a>
    <a href="/demo/stripe.com" class="btn-secondary">See Example</a>
  </div>
  <div class="hero-demo">
    <div class="hero-demo-label">Try any domain -- free signup required</div>
    <div class="hero-demo-input-row">
      <input id="hero-domain-input" type="text" placeholder="stripe.com" autocomplete="off" />
      <button id="hero-demo-btn" onclick="heroDemo()">Analyze &rarr;</button>
    </div>
    <div id="hero-demo-result" style="display:none"></div>
  </div>
  <script>
  async function heroDemo() {{
    const domain = document.getElementById('hero-domain-input').value.trim() || 'stripe.com';
    const btn = document.getElementById('hero-demo-btn');
    const result = document.getElementById('hero-demo-result');
    btn.textContent = 'Checking...';
    btn.disabled = true;
    try {{
      const check = await fetch('/usage', {{credentials: 'include'}});
      if (!check.ok) {{
        window.location.href = '/login?next=/demo/' + encodeURIComponent(domain);
        return;
      }}
      btn.textContent = 'Analyzing...';
      window.location.href = '/demo/' + encodeURIComponent(domain);
    }} catch(e) {{
      window.location.href = '/login?next=/demo/' + encodeURIComponent(domain);
    }} finally {{
      btn.disabled = false;
      btn.textContent = 'Analyze ->';
    }}
  }}; }});
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
  <p>10 lookups  no credit card  instant delivery</p>
  <div class="form-row">
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
      <ul><li>5,000 API requests/month</li><li>20 req/min rate limit</li><li>Bulk API (50 domains)</li><li>Redis-cached responses</li><li>Priority support</li></ul>
      <a href="/choose/pro" class="btn-pp">Get Pro</a>
    </div>
    <div class="plan">
      <div class="plan-name">Business</div>
      <div class="plan-price">$166<span>/mo</span></div>
      <div style="font-size:12px;color:#aaa;margin-top:-8px;margin-bottom:10px">billed annually &bull; save 20%</div>
      <div class="plan-limit">50,000 requests/month</div>
      <ul><li>50,000 API requests/month</li><li>20 req/min rate limit</li><li>Bulk API (50 domains)</li><li>Webhook support</li><li>Dedicated support</li></ul>
      <a href="/choose/business" class="btn-pp">Get Business</a>
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
  <a href="#signup" class="btn-primary" style="font-size:18px;padding:16px 40px"> Get Free API Key</a>
</div>
<footer>
  <div style="margin-bottom:14px;font-size:16px;font-weight:700;color:#a855f7;letter-spacing:-0.5px">Stack<span style="color:#e5e5e5">Sight</span></div>
  <div style="margin-bottom:12px">
    <a href="/docs">Docs</a> &nbsp;&nbsp; <a href="/demo/stripe.com">Demo</a> &nbsp;&nbsp; <a href="#pricing">Pricing</a> &nbsp;&nbsp; <a href="/login">Sign In</a> &nbsp;&nbsp; <a href="mailto:support@stacksight.org">Contact</a>
  </div>
  <div style="margin-bottom:8px">
    <a href="/terms">Terms of Service</a> &nbsp;&nbsp; <a href="/privacy">Privacy Policy</a>
  </div>
  <div>&copy; 2026 StackSight &nbsp;&nbsp; <a href="https://x.com/StackSightOrg">@StackSightOrg</a></div>
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
    const r = await fetch('/signup', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{email, next: new URLSearchParams(window.location.search).get('next') || ''}})}})  ;
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
footer{{text-align:center;padding:40px;color:#555;font-size:13px;border-top:1px solid #1a0a2e;margin-top:60px}}
</style>
</head>
<body>
<nav style="background:#0a0015;padding:16px 32px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #1a0a2e">
  <a href="/" style="color:#a855f7;font-weight:800;font-size:18px;text-decoration:none">StackSight</a>
  <div style="display:flex;gap:24px;align-items:center">
    <a href="/docs" style="color:#b0b0b0;text-decoration:none;font-size:14px">Docs</a>
    <a href="/demo/stripe.com" style="color:#b0b0b0;text-decoration:none;font-size:14px">Demo</a>
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
footer{{text-align:center;padding:40px;color:#555;font-size:13px;border-top:1px solid #1a0a2e;margin-top:60px}}
</style>
</head>
<body>
<nav style="background:#0a0015;padding:16px 32px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #1a0a2e">
  <a href="/" style="color:#a855f7;font-weight:800;font-size:18px;text-decoration:none">StackSight</a>
  <div style="display:flex;gap:24px;align-items:center">
    <a href="/docs" style="color:#b0b0b0;text-decoration:none;font-size:14px">Docs</a>
    <a href="/demo/stripe.com" style="color:#b0b0b0;text-decoration:none;font-size:14px">Demo</a>
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
footer{{text-align:center;padding:40px;color:#555;font-size:13px;border-top:1px solid #1a0a2e;margin-top:60px}}
</style>
</head>
<body>
<nav style="background:#0a0015;padding:16px 32px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #1a0a2e">
  <a href="/" style="color:#a855f7;font-weight:800;font-size:18px;text-decoration:none">StackSight</a>
  <div style="display:flex;gap:24px;align-items:center">
    <a href="/docs" style="color:#b0b0b0;text-decoration:none;font-size:14px">Docs</a>
    <a href="/demo/stripe.com" style="color:#b0b0b0;text-decoration:none;font-size:14px">Demo</a>
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

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_session_email(request):
        return RedirectResponse(next if next.startswith("/") else "/dashboard")
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
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
input[type=email]{width:100%;background:#0a0a0a;border:1.5px solid #2a2a2a;border-radius:10px;padding:14px 16px;color:#fff;font-size:15px;outline:none;transition:border-color .2s}
input[type=email]:focus{border-color:#7c3aed}
input[type=email]::placeholder{color:#374151}
.btn{width:100%;margin-top:16px;background:linear-gradient(135deg,#7c3aed,#a855f7);border:none;border-radius:10px;padding:14px;color:#fff;font-size:15px;font-weight:600;cursor:pointer;transition:opacity .2s;letter-spacing:.01em}
.btn:hover{opacity:.9}
.btn:disabled{opacity:.5;cursor:not-allowed}
.msg{margin-top:20px;padding:14px 16px;border-radius:10px;font-size:14px;text-align:center;display:none}
.msg.success{background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.2);color:#22c55e}
.msg.error{background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.2);color:#ef4444}
.divider{display:flex;align-items:center;gap:12px;margin:28px 0}
.divider::before,.divider::after{content:'';flex:1;height:1px;background:#1f1f1f}
.divider span{color:#4b5563;font-size:12px;white-space:nowrap}
.footer{text-align:center;margin-top:24px;font-size:13px;color:#4b5563}
.footer a{color:#7c3aed;text-decoration:none}
.footer a:hover{color:#a78bfa}
.demo-note{background:rgba(124,58,237,0.08);border:1px solid rgba(124,58,237,0.2);border-radius:10px;padding:12px 16px;font-size:13px;color:#a78bfa;text-align:center;margin-bottom:24px;display:none}
</style>
</head>
<body>
<div class="wrap">
  <a href="/" class="logo">StackSight</a>
  <div class="card">
    <div class="demo-note" id="demo-note">Sign in to run your free domain analysis</div>
    <h1>Welcome back</h1>
    <p class="sub">Enter your email and we'll send you a magic link.<br><span>No password needed.</span></p>
    <div>
      <label for="email">Email address</label>
      <input type="email" id="email" placeholder="you@company.com" autocomplete="email">
      <button class="btn" id="submit-btn" onclick="doLogin()">Send magic link</button>
      <div class="msg" id="msg"></div>
    </div>
    <div class="divider"><span>New to StackSight?</span></div>
    <div class="footer">
      Get <strong style="color:#fff">25 free lookups</strong> instantly &mdash; no credit card.<br><br>
      <a href="/#pricing">View pricing</a> &nbsp;&middot;&nbsp; <a href="/docs">API docs</a>
    </div>
  </div>
</div>
<script>
(function() {
  const p = new URLSearchParams(window.location.search);
  if (p.get('next')) {
    const note = document.getElementById('demo-note');
    note.style.display = 'block';
  }
})();

async function doLogin() {
  const email = document.getElementById('email').value.trim();
  const btn = document.getElementById('submit-btn');
  const msg = document.getElementById('msg');
  if (!email || !email.includes('@')) {
    msg.className = 'msg error'; msg.style.display = 'block';
    msg.textContent = 'Please enter a valid email address.'; return;
  }
  btn.disabled = true; btn.textContent = 'Sending...';
  msg.style.display = 'none';
  const next = new URLSearchParams(window.location.search).get('next') || '';
  try {
    const r = await fetch('/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email, next})});
    const d = await r.json();
    if (r.ok) {
      msg.className = 'msg success'; msg.style.display = 'block';
      msg.textContent = 'Magic link sent! Check your inbox (and spam folder).';
      btn.textContent = 'Link sent';
    } else {
      msg.className = 'msg error'; msg.style.display = 'block';
      msg.textContent = d.detail || 'Something went wrong. Try again.';
      btn.disabled = false; btn.textContent = 'Send magic link';
    }
  } catch(e) {
    msg.className = 'msg error'; msg.style.display = 'block';
    msg.textContent = 'Request failed. Please try again.';
    btn.disabled = false; btn.textContent = 'Send magic link';
  }
}

document.getElementById('email').addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });
</script>
</body>
</html>""")


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
        cur = conn.cursor()
        cur.execute("UPDATE sessions SET active=FALSE WHERE session_token=%s", (token,))
        conn.commit()
        conn.close()
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
        conn.close()
        return RedirectResponse(url="/login", status_code=302)
    email = row[0]
    cur.execute("SELECT api_key, plan, requests_used, requests_limit, created_at FROM api_keys WHERE email=%s AND active=TRUE", (email,))
    key_row = cur.fetchone()
    conn.close()
    if not key_row or not key_row[0]:
        return HTMLResponse("""<!DOCTYPE html><html><head><title>StackSight</title>
<style>body{font-family:sans-serif;background:#0a0a0a;color:#e5e5e5;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.box{background:#111;border:1px solid #222;border-radius:12px;padding:48px;text-align:center;max-width:400px}
h2{color:#a855f7;margin-bottom:12px}p{color:#b0b0b0;margin-bottom:24px}
.btn{display:inline-block;background:#a855f7;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600}</style></head>
<body><div class="box"><h2>No API Key Found</h2>
<p>Your account doesn't have an active API key yet. Please sign up to get one.</p>
<a href="/#signup" class="btn">Get Your Free API Key</a></div></body></html>""")
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
.subtitle{{color:#b0b0b0;margin-bottom:40px;font-size:15px}}
.card{{background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:28px;margin-bottom:20px}}
.card h2{{font-size:14px;font-weight:600;color:#aaa;text-transform:uppercase;letter-spacing:.05em;margin-bottom:20px;padding-bottom:12px;border-bottom:1px solid #1f1f1f}}
.field + .field{{border-top:1px solid #161616;padding-top:18px}}
.field{{margin-bottom:20px}}
.field label{{display:block;font-size:13px;color:#aaa;margin-bottom:6px}}
.field .value{{font-size:15px;color:#e5e5e5;font-family:monospace;background:#0d0d0d;border:1px solid #222;border-radius:8px;padding:10px 14px;display:flex;align-items:center;justify-content:space-between;gap:12px}}
.field .value span{{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.copy-btn{{background:#6366f1;border:none;color:#fff;padding:5px 12px;border-radius:6px;cursor:pointer;font-size:12px;white-space:nowrap;flex-shrink:0}}
.copy-btn:hover{{background:#4f46e5}}
.badge{{display:inline-block;background:#1a1a2e;color:#6366f1;border:1px solid #2d2d5e;padding:4px 12px;border-radius:20px;font-size:13px;font-weight:600;text-transform:capitalize}}
.usage-bar{{background:#1a1a1a;border-radius:6px;height:8px;margin-top:8px;overflow:hidden}}
.usage-fill{{background:#6366f1;height:100%;border-radius:6px;transition:width .3s}}
.usage-label{{display:flex;justify-content:space-between;font-size:13px;color:#aaa;margin-top:6px}}
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
    return HTMLResponse(content=html)
# ROUTES  FREE SIGNUP
# 

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
h1{color:#a855f7;margin-bottom:12px}p{color:#b0b0b0;margin-bottom:24px}
.btn{display:inline-block;background:#a855f7;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;margin:8px}
.btn2{display:inline-block;background:transparent;border:1px solid #333;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600;margin:8px}</style></head>
<body><div class="box">
<h1>Email Verified!</h1>
<p>Your free API key is on its way. Check your inbox - it should arrive within a minute.</p>
<a href="/login" class="btn">Sign In to Dashboard</a>
<a href="/" class="btn2">Back to Home</a>
</div></body></html>""")


# 
# ROUTES  API
# 

@app.get("/demo/{domain}", response_class=HTMLResponse)
async def demo(domain: str):
    clean = domain.lower().strip().rstrip("/").replace("https://", "").replace("http://", "")
    esc = html.escape(clean)
    from urllib.parse import quote as _urlquote
    url_clean = _urlquote(clean, safe="")
    data = DEMO_DATA.get(clean, {"company_name": clean.split(".")[0].title(), "is_hiring": True, "engineering_roles": ["Software Engineer"], "sales_roles": ["Account Executive"], "detected_tech_stack": ["JavaScript", "AWS"]})
    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Demo: {esc} - StackSight</title>
<meta name="description" content="See real-time hiring data and tech stack for {esc} via the StackSight API. Free demo -- no signup required.">
<meta name="robots" content="index,follow">
<meta property="og:title" content="StackSight Demo: {esc}">
<meta property="og:description" content="Live hiring intent and tech stack data for {esc}. Powered by StackSight.">
<meta property="og:url" content="https://stacksight.org/demo/{url_clean}">
<meta property="og:type" content="website">
<link rel="canonical" href="https://stacksight.org/demo/{url_clean}">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;line-height:1.6}}
nav{{padding:18px 40px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1a1a1a;position:fixed;top:0;left:0;right:0;background:rgba(10,10,10,0.95);backdrop-filter:blur(10px);z-index:100}}
.logo{{font-size:20px;font-weight:700;color:#a855f7;text-decoration:none}}
.nav-links a{{color:#c0c0c0;text-decoration:none;margin-left:24px;font-size:14px}}.nav-links a:hover{{color:#fff}}
.nav-links .btn-login{{background:#1a1a1a;border:1px solid #333;color:#fff;padding:7px 16px;border-radius:7px}}
.wrap{{max-width:900px;margin:0 auto;padding:110px 20px 60px}}
.hero{{text-align:center;margin-bottom:40px}}
h1{{font-size:36px;font-weight:800;margin-bottom:10px}}
h1 span{{color:#a855f7}}
.sub{{color:#999;font-size:16px}}
.sub a{{color:#a855f7;text-decoration:none}}
.card{{background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:24px}}
.company-card{{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px;margin-bottom:16px}}
.company-name{{font-size:26px;font-weight:800}}
.company-domain{{color:#777;font-size:14px;font-family:monospace}}
.badge-hiring{{padding:6px 14px;border-radius:20px;font-size:13px;font-weight:600}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-bottom:16px}}
.card h3{{font-size:13px;font-weight:700;color:#777;text-transform:uppercase;letter-spacing:.8px;margin-bottom:14px}}
.role-list{{list-style:none}}
.role-list li{{padding:6px 0;color:#b0b0b0;border-bottom:1px solid #0f0f0f;font-size:14px}}
.role-list li:last-child{{border-bottom:none}}
.pill{{background:#1a0a2e;color:#a855f7;border:1px solid #3b1a6e;padding:4px 12px;border-radius:20px;font-size:13px;display:inline-block;margin:3px}}
.raw-label{{font-size:13px;font-weight:700;color:#777;text-transform:uppercase;letter-spacing:.8px;margin:24px 0 8px}}
pre{{background:#050505;border:1px solid #1a1a1a;border-radius:8px;padding:20px;padding-top:44px;overflow-x:auto;font-size:13px;color:#ccc;line-height:1.7;margin:12px 0;position:relative}}
pre::before{{content:"";position:absolute;top:0;left:0;right:0;height:30px;background:#0d0d0d;border-bottom:1px solid #1a1a1a;border-radius:7px 7px 0 0}}
pre::after{{content:"";position:absolute;top:11px;left:14px;width:8px;height:8px;border-radius:50%;background:#ef4444;box-shadow:14px 0 0 #eab308,28px 0 0 #22c55e}}
.cta{{text-align:center;padding:48px 20px 20px}}
.cta h2{{font-size:28px;font-weight:800;margin-bottom:20px}}
.btn-primary{{display:inline-block;background:#a855f7;color:#fff;padding:13px 28px;border-radius:8px;text-decoration:none;font-weight:700;margin:6px}}
.btn-primary:hover{{background:#9333ea}}
.btn-secondary{{display:inline-block;border:1px solid #333;color:#e5e5e5;padding:13px 28px;border-radius:8px;text-decoration:none;font-weight:700;margin:6px}}
.btn-secondary:hover{{border-color:#555}}
footer{{border-top:1px solid #1a1a1a;padding:32px 20px;text-align:center;color:#666;font-size:13px}}
footer a{{color:#888;text-decoration:none}}
footer a:hover{{color:#fff}}
@media(max-width:640px){{h1{{font-size:28px}}nav{{padding:14px 20px}}}}
</style></head>
<body>
<nav><a href="/" class="logo">StackSight</a><div class="nav-links"><a href="/">Home</a><a href="/docs">Docs</a><a href="/#pricing">Pricing</a><a href="/login" class="btn-login" id="nav-auth-btn">Sign In</a></div></nav>
<script>
(function(){{
  fetch('/usage',{{'credentials':'include'}}).then(r=>{{
    if(r.ok){{r.json().then(d=>{{
      var btn=document.getElementById('nav-auth-btn');
      if(btn){{btn.textContent='My Account';btn.href='/dashboard';}}
    }});}}
  }}).catch(function(){{}});
}})();
</script>
<div class="wrap">
  <div class="hero">
    <h1>Live Demo: <span>{esc}</span></h1>
    <p class="sub">This is cached demo data. <a href="/#signup">Sign up free</a> to analyze any domain live.</p>
  </div>
  <div class="card company-card">
    <div>
      <div class="company-name">{html.escape(str(data.get('company_name', clean)))}</div>
      <div class="company-domain">{esc}</div>
    </div>
    <div class="badge-hiring" style="color:{"#22c55e" if data.get('is_hiring') else "#ef4444"};background:{"#0a1f0a" if data.get('is_hiring') else "#1f0a0a"};border:1px solid {"#22c55e" if data.get('is_hiring') else "#ef4444"}">{"-- Hiring" if data.get('is_hiring') else "-- Not Hiring"}</div>
  </div>
  <div class="grid">
    <div class="card">
      <h3>Engineering Roles</h3>
      <ul class="role-list">{"".join(f'<li>{r}</li>' for r in data.get("engineering_roles", [])) or '<li>None detected</li>'}</ul>
    </div>
    <div class="card">
      <h3>Sales Roles</h3>
      <ul class="role-list">{"".join(f'<li>{r}</li>' for r in data.get("sales_roles", [])) or '<li>None detected</li>'}</ul>
    </div>
    <div class="card">
      <h3>Tech Stack</h3>
      <div>{"".join(f'<span class="pill">{t}</span>' for t in data.get("detected_tech_stack", [])) or '<span class="pill">Unknown</span>'}</div>
    </div>
  </div>
  <div class="raw-label">Raw API Response</div>
  <pre>{json.dumps({"source": "demo", "data": data}, indent=2)}</pre>
  <div class="cta">
    <h2>Ready to integrate?</h2>
    <a href="/#signup" class="btn-primary">Get Free API Key</a>
    <a href="/docs" class="btn-secondary">View API Docs</a>
  </div>
</div>
<footer>
  <div style="margin-bottom:14px;font-size:16px;font-weight:700;color:#a855f7;letter-spacing:-0.5px">Stack<span style="color:#e5e5e5">Sight</span></div>
  <div style="margin-bottom:12px">
    <a href="/docs">Docs</a> &nbsp;&nbsp; <a href="/demo/stripe.com">Demo</a> &nbsp;&nbsp; <a href="/#pricing">Pricing</a> &nbsp;&nbsp; <a href="/login">Sign In</a> &nbsp;&nbsp; <a href="mailto:support@stacksight.org">Contact</a>
  </div>
  <div style="margin-bottom:8px">
    <a href="/terms">Terms of Service</a> &nbsp;&nbsp; <a href="/privacy">Privacy Policy</a>
  </div>
  <div>&copy; 2026 StackSight &nbsp;&nbsp; <a href="https://x.com/StackSightOrg">@StackSightOrg</a></div>
</footer>
</body></html>""")


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
    cur.close(); conn.close()
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
    cur.close(); conn.close()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid API key")
    plan, used, limit, created = row
    return {"plan": plan, "requests_used": used, "requests_limit": limit, "requests_remaining": limit - used, "created_at": str(created)}


@app.get("/me")
async def me(x_api_key: str = Header(None)):
    return await usage(x_api_key=x_api_key)


@app.get("/choose/pro", response_class=HTMLResponse)
async def choose_pro():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pro Plan - Choose Billing | StackSight</title>
<style>*{box-sizing:border-box;margin:0;padding:0}body{background:#0a0a0a;color:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;background-image:radial-gradient(ellipse at 50% 0%,rgba(124,58,237,0.12) 0%,transparent 60%)}.wrap{max-width:520px;width:100%;text-align:center}.logo{font-size:22px;font-weight:800;color:#7c3aed;text-decoration:none;display:inline-block;margin-bottom:48px}.tag{display:inline-block;background:rgba(124,58,237,0.15);color:#a78bfa;font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;padding:4px 14px;border-radius:20px;border:1px solid rgba(124,58,237,0.3);margin-bottom:20px}h1{font-size:30px;font-weight:700;margin-bottom:10px}.sub{color:#6b7280;font-size:15px;margin-bottom:40px}.cards{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:28px}.card{border:1.5px solid #1f1f1f;border-radius:18px;padding:28px 20px;text-decoration:none;color:#fff;display:block;transition:all .2s;position:relative;background:#111}.card:hover{border-color:#4c1d95;transform:translateY(-2px)}.card.best{border-color:#7c3aed;background:linear-gradient(135deg,#130f1e,#1a1033);box-shadow:0 0 32px rgba(124,58,237,0.2)}.card.best:hover{box-shadow:0 0 48px rgba(124,58,237,0.3)}.badge{position:absolute;top:-13px;left:50%;transform:translateX(-50%);background:linear-gradient(90deg,#7c3aed,#a855f7);color:#fff;font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;padding:4px 14px;border-radius:20px;white-space:nowrap}.lbl{font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#6b7280;margin-bottom:14px}.card.best .lbl{color:#a78bfa}.price{font-size:40px;font-weight:800;line-height:1}.price span{font-size:15px;font-weight:400;color:#6b7280}.card.best .price span{color:#a78bfa}.psub{font-size:12px;color:#4b5563;margin-top:8px}.card.best .psub{color:#7c3aed}.save{display:inline-block;background:rgba(34,197,94,0.12);color:#22c55e;font-size:11px;font-weight:700;padding:3px 10px;border-radius:10px;margin-top:10px;border:1px solid rgba(34,197,94,0.2)}.cancel{font-size:12px;color:#4b5563;margin-top:10px}.back{color:#4b5563;font-size:13px;text-decoration:none}.back:hover{color:#9ca3af}@media(max-width:420px){.cards{grid-template-columns:1fr}}</style>
</head>
<body>
<div class="wrap">
  <a href="/" class="logo">StackSight</a>
  <div class="tag">Pro Plan</div>
  <h1>Choose your billing</h1>
  <p class="sub">Same features, same API. Pick what works for you.</p>
  <div class="cards">
    <a href="/checkout/pro" class="card">
      <div class="lbl">Monthly</div>
      <div class="price">$49<span>/mo</span></div>
      <div class="psub">billed monthly</div>
      <div class="cancel">Cancel anytime</div>
    </a>
    <a href="/checkout/pro_annual" class="card best">
      <div class="badge">BEST VALUE</div>
      <div class="lbl">Annual</div>
      <div class="price">$39<span>/mo</span></div>
      <div class="psub">billed $468/yr</div>
      <div class="save">Save 20%</div>
    </a>
  </div>
  <a href="/#pricing" class="back">Back to pricing</a>
</div>
</body></html>""")


@app.get("/choose/business", response_class=HTMLResponse)
async def choose_business():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Business Plan - Choose Billing | StackSight</title>
<style>*{box-sizing:border-box;margin:0;padding:0}body{background:#0a0a0a;color:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;background-image:radial-gradient(ellipse at 50% 0%,rgba(124,58,237,0.12) 0%,transparent 60%)}.wrap{max-width:520px;width:100%;text-align:center}.logo{font-size:22px;font-weight:800;color:#7c3aed;text-decoration:none;display:inline-block;margin-bottom:48px}.tag{display:inline-block;background:rgba(124,58,237,0.15);color:#a78bfa;font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;padding:4px 14px;border-radius:20px;border:1px solid rgba(124,58,237,0.3);margin-bottom:20px}h1{font-size:30px;font-weight:700;margin-bottom:10px}.sub{color:#6b7280;font-size:15px;margin-bottom:40px}.cards{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:28px}.card{border:1.5px solid #1f1f1f;border-radius:18px;padding:28px 20px;text-decoration:none;color:#fff;display:block;transition:all .2s;position:relative;background:#111}.card:hover{border-color:#4c1d95;transform:translateY(-2px)}.card.best{border-color:#7c3aed;background:linear-gradient(135deg,#130f1e,#1a1033);box-shadow:0 0 32px rgba(124,58,237,0.2)}.card.best:hover{box-shadow:0 0 48px rgba(124,58,237,0.3)}.badge{position:absolute;top:-13px;left:50%;transform:translateX(-50%);background:linear-gradient(90deg,#7c3aed,#a855f7);color:#fff;font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;padding:4px 14px;border-radius:20px;white-space:nowrap}.lbl{font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#6b7280;margin-bottom:14px}.card.best .lbl{color:#a78bfa}.price{font-size:40px;font-weight:800;line-height:1}.price span{font-size:15px;font-weight:400;color:#6b7280}.card.best .price span{color:#a78bfa}.psub{font-size:12px;color:#4b5563;margin-top:8px}.card.best .psub{color:#7c3aed}.save{display:inline-block;background:rgba(34,197,94,0.12);color:#22c55e;font-size:11px;font-weight:700;padding:3px 10px;border-radius:10px;margin-top:10px;border:1px solid rgba(34,197,94,0.2)}.cancel{font-size:12px;color:#4b5563;margin-top:10px}.back{color:#4b5563;font-size:13px;text-decoration:none}.back:hover{color:#9ca3af}@media(max-width:420px){.cards{grid-template-columns:1fr}}</style>
</head>
<body>
<div class="wrap">
  <a href="/" class="logo">StackSight</a>
  <div class="tag">Business Plan</div>
  <h1>Choose your billing</h1>
  <p class="sub">Same features, same API. Pick what works for you.</p>
  <div class="cards">
    <a href="/checkout/business" class="card">
      <div class="lbl">Monthly</div>
      <div class="price">$199<span>/mo</span></div>
      <div class="psub">billed monthly</div>
      <div class="cancel">Cancel anytime</div>
    </a>
    <a href="/checkout/business_annual" class="card best">
      <div class="badge">BEST VALUE</div>
      <div class="lbl">Annual</div>
      <div class="price">$166<span>/mo</span></div>
      <div class="psub">billed $1,992/yr</div>
      <div class="save">Save 20%</div>
    </a>
  </div>
  <a href="/#pricing" class="back">Back to pricing</a>
</div>
</body></html>""")


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

    elif event["type"] == "invoice.payment_succeeded":
        # Subscription renewed -- reset usage for this customer
        invoice = event["data"]["object"]
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
            cur.close(); conn.close()

    elif event["type"] == "customer.subscription.deleted":
        # Subscription cancelled -- downgrade to free
        subscription = event["data"]["object"]
        customer_id = subscription.get("customer")
        if customer_id:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                "UPDATE api_keys SET plan = 'free', requests_limit = 10, requests_used = 0 WHERE stripe_customer_id = %s",
                (customer_id,)
            )
            conn.commit()
            cur.close(); conn.close()

    elif event["type"] == "invoice.payment_failed":
        # Payment failed -- leave access for now but could notify user
        # Stripe will retry; subscription.deleted fires if all retries fail
        pass

    return {"status": "ok"}


@app.get("/trending")
async def trending():
    results = []
    for domain, data in DEMO_DATA.items():
        results.append({
            "domain": domain,
            "company_name": data.get("company_name", domain.split(".")[0].title()),
            "is_hiring": data.get("is_hiring", False),
            "engineering_roles": data.get("engineering_roles", []),
            "sales_roles": data.get("sales_roles", []),
            "detected_tech_stack": data.get("detected_tech_stack", []),
            "open_roles": len(data.get("engineering_roles", [])) + len(data.get("sales_roles", []))
        })
    # Sort by open roles descending
    results.sort(key=lambda x: x["open_roles"], reverse=True)
    return {"trending": results, "count": len(results)}


@app.get("/terms", response_class=HTMLResponse)
async def terms():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
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
<nav>
  <a href="/" class="logo">StackSight</a>
  <div class="nav-links">
    <a href="/docs">Docs</a>
    <a href="/demo/stripe.com">Demo</a>
    <a href="/#pricing">Pricing</a>
    <a href="/login" class="btn-login" id="nav-auth-btn">Sign In</a>
  </div>
</nav>
<script>
(function(){
  fetch("/usage",{credentials:"include"}).then(r=>{
    if(r.ok){r.json().then(d=>{
      var btn=document.getElementById("nav-auth-btn");
      if(btn){btn.textContent="My Account";btn.href="/dashboard";}
    });}
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
    <a href="/docs">Docs</a> &nbsp;&nbsp; <a href="/demo/stripe.com">Demo</a> &nbsp;&nbsp; <a href="/#pricing">Pricing</a> &nbsp;&nbsp; <a href="/login">Sign In</a> &nbsp;&nbsp; <a href="mailto:support@stacksight.org">Contact</a>
  </div>
  <div style="margin-bottom:8px">
    <a href="/terms">Terms of Service</a> &nbsp;&nbsp; <a href="/privacy">Privacy Policy</a>
  </div>
  <div>&copy; 2026 StackSight &nbsp;&nbsp; <a href="https://x.com/StackSightOrg">@StackSightOrg</a></div>
</footer>
</body></html>""")


@app.get("/privacy", response_class=HTMLResponse)
async def privacy():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
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
<nav>
  <a href="/" class="logo">StackSight</a>
  <div class="nav-links">
    <a href="/docs">Docs</a>
    <a href="/demo/stripe.com">Demo</a>
    <a href="/#pricing">Pricing</a>
    <a href="/login" class="btn-login" id="nav-auth-btn">Sign In</a>
  </div>
</nav>
<script>
(function(){
  fetch("/usage",{credentials:"include"}).then(r=>{
    if(r.ok){r.json().then(d=>{
      var btn=document.getElementById("nav-auth-btn");
      if(btn){btn.textContent="My Account";btn.href="/dashboard";}
    });}
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
    <a href="/docs">Docs</a> &nbsp;&nbsp; <a href="/demo/stripe.com">Demo</a> &nbsp;&nbsp; <a href="/#pricing">Pricing</a> &nbsp;&nbsp; <a href="/login">Sign In</a> &nbsp;&nbsp; <a href="mailto:support@stacksight.org">Contact</a>
  </div>
  <div style="margin-bottom:8px">
    <a href="/terms">Terms of Service</a> &nbsp;&nbsp; <a href="/privacy">Privacy Policy</a>
  </div>
  <div>&copy; 2026 StackSight &nbsp;&nbsp; <a href="https://x.com/StackSightOrg">@StackSightOrg</a></div>
</footer>
</body></html>""")


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
    conn.close()

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
</script>
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
    cur.close(); conn.close()
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
    cur.close(); conn.close()
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
    cur.close(); conn.close()
    return {"ok": True, "keys_reset": reset_count}


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
 
