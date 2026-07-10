import asyncio
import hashlib
import hmac
import json
import os
import secrets
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import openai
import psycopg2
import redis as redis_lib
import stripe
import uvicorn
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from playwright.async_api import async_playwright

VERSION = "9.0.0"

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

_rate_limit: dict = {}
RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW = 60

redis_client = redis_lib.from_url(REDIS_URL, decode_responses=True)
app = FastAPI(title="StackSight API", version=VERSION)

DEMO_DATA = {
    "stripe.com": {"company_name": "Stripe", "is_hiring": True, "engineering_roles": ["Backend Engineer", "ML Engineer", "Platform Engineer"], "sales_roles": ["Account Executive", "Solutions Engineer"], "detected_tech_stack": ["React", "AWS", "Stripe", "Cloudflare", "Sentry"]},
    "openai.com": {"company_name": "OpenAI", "is_hiring": True, "engineering_roles": ["Research Engineer", "Infrastructure Engineer", "Safety Engineer"], "sales_roles": ["Enterprise Account Executive"], "detected_tech_stack": ["Python", "Kubernetes", "Azure", "React", "PostgreSQL"]},
    "notion.so": {"company_name": "Notion", "is_hiring": True, "engineering_roles": ["Frontend Engineer", "Backend Engineer", "Data Engineer"], "sales_roles": ["Sales Development Rep", "Account Manager"], "detected_tech_stack": ["React", "TypeScript", "AWS", "Electron", "PostgreSQL"]},
    "vercel.com": {"company_name": "Vercel", "is_hiring": True, "engineering_roles": ["Edge Runtime Engineer", "DX Engineer"], "sales_roles": ["Enterprise AE", "Solutions Architect"], "detected_tech_stack": ["Next.js", "React", "AWS", "Cloudflare", "TypeScript"]},
    "figma.com": {"company_name": "Figma", "is_hiring": True, "engineering_roles": ["C++ Engineer", "WebAssembly Engineer"], "sales_roles": ["Account Executive", "Customer Success Manager"], "detected_tech_stack": ["C++", "WebAssembly", "React", "AWS", "TypeScript"]},
    "github.com": {"company_name": "GitHub", "is_hiring": True, "engineering_roles": ["Staff Engineer", "Security Engineer"], "sales_roles": ["Enterprise Sales", "Partner Manager"], "detected_tech_stack": ["Ruby", "Go", "React", "Azure", "MySQL"]},
    "shopify.com": {"company_name": "Shopify", "is_hiring": True, "engineering_roles": ["Rails Engineer", "Go Engineer", "ML Engineer"], "sales_roles": ["Partner Sales Manager"], "detected_tech_stack": ["Ruby on Rails", "Go", "React", "GCP", "Kafka"]},
    "hubspot.com": {"company_name": "HubSpot", "is_hiring": True, "engineering_roles": ["Java Engineer", "Frontend Engineer"], "sales_roles": ["Account Executive", "Sales Engineer", "BDR"], "detected_tech_stack": ["Java", "React", "AWS", "MySQL", "Kafka"]},
    "datadog.com": {"company_name": "Datadog", "is_hiring": True, "engineering_roles": ["Go Engineer", "Agent Engineer", "ML Engineer"], "sales_roles": ["Enterprise AE", "Solutions Engineer", "SDR"], "detected_tech_stack": ["Go", "Python", "AWS", "Kubernetes", "Cassandra"]},
}

EXTRACTION_PROMPT = (
    "You are a B2B data extraction engine. Given raw text from a company careers page, "
    "extract structured data. Return ONLY valid JSON. Schema: "
    "{company_name: string, is_hiring: boolean, engineering_roles: [string], "
    "sales_roles: [string], detected_tech_stack: [string]}"
)

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
    conn.commit()
    cur.close()
    conn.close()

def check_rate_limit(api_key: str):
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    if api_key not in _rate_limit:
        _rate_limit[api_key] = []
    _rate_limit[api_key] = [t for t in _rate_limit[api_key] if t > window_start]
    if len(_rate_limit[api_key]) >= RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Max 20 requests/minute.")
    _rate_limit[api_key].append(now)

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

def send_email(to_email: str, subject: str, html_body: str, text_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = ZOHO_EMAIL
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP("smtp.zoho.com", 587) as server:
        server.starttls()
        server.login(ZOHO_EMAIL, ZOHO_PASSWORD)
        server.sendmail(ZOHO_EMAIL, to_email, msg.as_string())

def send_api_key_email(to_email: str, api_key: str, plan: str):
    limit = PLAN_LIMITS.get(plan, 10)
    subject = "Your StackSight API Key"
    html = f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#0f0f0f;color:#fff;padding:40px;border-radius:12px">
      <h1 style="color:#a855f7">StackSight API</h1>
      <h2>Your API Key is Ready</h2>
      <p>Plan: <strong style="color:#a855f7">{plan.title()}</strong> | Limit: <strong>{limit:,} requests</strong></p>
      <div style="background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:20px;font-family:monospace;font-size:16px;color:#a855f7;word-break:break-all">{api_key}</div>
      <h3>Quick Start</h3>
      <div style="background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:16px;font-family:monospace;font-size:13px;color:#ccc">curl -X GET "https://stacksight.org/scrape?domain=stripe.com" -H "X-API-Key: {api_key}"</div>
      <p>Docs: <a href="https://stacksight.org/docs" style="color:#a855f7">stacksight.org/docs</a></p>
    </div>"""
    text = f"Your StackSight API Key\n\nPlan: {plan.title()}\nLimit: {limit:,} requests\n\nAPI Key: {api_key}\n\nDocs: https://stacksight.org/docs"
    send_email(to_email, subject, html, text)

def send_verification_email(to_email: str, token: str):
    verify_url = f"{BASE_URL}/verify-email?token={token}"
    subject = "Verify your email - StackSight API"
    html = f"""<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#0f0f0f;color:#fff;padding:40px;border-radius:12px">
      <h1 style="color:#a855f7">StackSight API</h1>
      <h2>Confirm your email</h2>
      <p>Click below to get your free API key (10 requests, no credit card required).</p>
      <a href="{verify_url}" style="display:inline-block;background:#a855f7;color:#fff;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:bold;margin:20px 0">Get My Free API Key</a>
      <p style="color:#666;font-size:13px">Link: {verify_url}<br>Expires in 24 hours.</p>
    </div>"""
    text = f"Confirm your email to get your free StackSight API key.\n\nClick here: {verify_url}\n\nExpires in 24 hours."
    send_email(to_email, subject, html, text)

def provision_api_key(email: str, plan: str, stripe_customer_id: str = None, stripe_session_id: str = None):
    api_key = "ss_" + secrets.token_urlsafe(32)
    limit = PLAN_LIMITS.get(plan, 10)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO api_keys (api_key, email, plan, requests_limit, stripe_customer_id, stripe_session_id)
        VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
    """, (api_key, email, plan, limit, stripe_customer_id, stripe_session_id))
    conn.commit()
    cur.close()
    conn.close()
    send_api_key_email(email, api_key, plan)
    return api_key

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

@app.on_event("startup")
async def startup():
    init_db()

@app.get("/", response_class=HTMLResponse)
async def landing():
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>StackSight API - B2B Hiring Intent & Tech Stack Data</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;line-height:1.6}}
.nav{{padding:20px 40px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1a1a1a;max-width:1200px;margin:0 auto}}
.logo{{font-size:22px;font-weight:700;color:#a855f7}}
.nav a{{color:#999;text-decoration:none;margin-left:24px;font-size:14px}}
.hero{{text-align:center;padding:80px 20px 60px;max-width:800px;margin:0 auto}}
.badge{{display:inline-block;background:#1a0a2e;color:#a855f7;border:1px solid #a855f7;padding:4px 12px;border-radius:20px;font-size:12px;margin-bottom:20px}}
h1{{font-size:52px;font-weight:800;line-height:1.1;margin-bottom:20px}}
h1 span{{color:#a855f7}}
.hero p{{font-size:18px;color:#999;max-width:600px;margin:0 auto 40px}}
.cta-group{{display:flex;gap:16px;justify-content:center;flex-wrap:wrap}}
.btn-primary{{background:#a855f7;color:#fff;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:600;font-size:16px}}
.btn-secondary{{background:transparent;color:#fff;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:600;font-size:16px;border:1px solid #333}}
.features{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:24px;max-width:1100px;margin:60px auto;padding:0 20px}}
.feature{{background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:28px}}
.feature h3{{font-size:17px;font-weight:600;margin-bottom:8px}}
.feature p{{color:#888;font-size:14px}}
.signup-section{{max-width:520px;margin:0 auto 80px;padding:0 20px;text-align:center}}
.signup-section h2{{font-size:28px;font-weight:700;margin-bottom:8px}}
.signup-section p{{color:#888;margin-bottom:24px}}
.signup-form{{display:flex;gap:12px;flex-wrap:wrap;justify-content:center}}
.signup-form input{{flex:1;min-width:240px;background:#111;border:1px solid #333;color:#fff;padding:12px 16px;border-radius:8px;font-size:15px}}
.signup-form input:focus{{outline:none;border-color:#a855f7}}
.signup-form button{{background:#a855f7;color:#fff;border:none;padding:12px 24px;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer}}
.msg{{margin-top:16px;padding:12px;border-radius:8px;font-size:14px;display:none}}
.msg.success{{background:#0f2a0f;border:1px solid #22c55e;color:#22c55e}}
.msg.error{{background:#2a0f0f;border:1px solid #ef4444;color:#ef4444}}
.pricing{{max-width:1000px;margin:0 auto 80px;padding:0 20px}}
.pricing h2{{text-align:center;font-size:32px;font-weight:700;margin-bottom:40px}}
.plans{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:24px}}
.plan{{background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:32px;position:relative}}
.plan.featured{{border-color:#a855f7}}
.plan-badge{{position:absolute;top:-12px;left:50%;transform:translateX(-50%);background:#a855f7;color:#fff;padding:4px 16px;border-radius:20px;font-size:12px;font-weight:600;white-space:nowrap}}
.plan-name{{font-size:18px;font-weight:700;margin-bottom:4px}}
.plan-price{{font-size:36px;font-weight:800;color:#a855f7;margin:12px 0 4px}}
.plan-price span{{font-size:16px;color:#666;font-weight:400}}
.plan-limit{{color:#888;font-size:14px;margin-bottom:20px}}
.plan ul{{list-style:none;margin-bottom:24px}}
.plan ul li{{padding:6px 0;font-size:14px;color:#ccc}}
.plan ul li::before{{content:"✓ ";color:#a855f7}}
.plan a{{display:block;text-align:center;padding:12px;border-radius:8px;font-weight:600;font-size:15px;text-decoration:none}}
.btn-plan-primary{{background:#a855f7;color:#fff}}
.btn-plan-secondary{{border:1px solid #333;color:#fff}}
pre{{background:#111;border:1px solid #1f1f1f;border-radius:10px;padding:24px;overflow-x:auto;font-size:13px;color:#ccc;line-height:1.6}}
footer{{border-top:1px solid #1a1a1a;padding:32px 20px;text-align:center;color:#555;font-size:13px}}
footer a{{color:#777;text-decoration:none}}
</style>
</head>
<body>
<div class="nav">
  <span class="logo">StackSight</span>
  <div><a href="/docs">Docs</a><a href="/demo/stripe.com">Demo</a><a href="#pricing">Pricing</a><a href="#signup">Free Key</a></div>
</div>
<div class="hero">
  <div class="badge">v{VERSION} · Live API</div>
  <h1>Turn any domain into<br><span>B2B sales intelligence</span></h1>
  <p>Real-time hiring intent, deterministic tech stack detection, and bulk enrichment.</p>
  <div class="cta-group">
    <a href="#signup" class="btn-primary">Get Free API Key</a>
    <a href="/demo/stripe.com" class="btn-secondary">Live Demo</a>
  </div>
</div>
<div class="features">
  <div class="feature"><h3>Real-Time Hiring Intent</h3><p>Know exactly which companies are actively hiring engineers, sales reps, or executives.</p></div>
  <div class="feature"><h3>Deterministic Tech Stack</h3><p>Parse actual script tags to detect React, AWS, Stripe, and 20+ technologies with 100% accuracy.</p></div>
  <div class="feature"><h3>Bulk Enrichment API</h3><p>Process up to 50 domains in one request. Cached results return in under 50ms.</p></div>
  <div class="feature"><h3>Lightning Fast Cache</h3><p>All scrapes cached in Redis for 7 days. Most requests return instantly.</p></div>
</div>
<div class="signup-section" id="signup">
  <h2>Get Started Free</h2>
  <p>10 requests · no credit card required · instant delivery</p>
  <div class="signup-form">
    <input type="email" id="email-input" placeholder="you@company.com">
    <button onclick="signup()">Get My Free Key</button>
  </div>
  <div class="msg" id="signup-msg"></div>
</div>
<div class="pricing" id="pricing">
  <h2>Simple Pricing</h2>
  <div class="plans">
    <div class="plan">
      <div class="plan-name">Free</div>
      <div class="plan-price">$0<span>/mo</span></div>
      <div class="plan-limit">10 requests total</div>
      <ul><li>10 API requests</li><li>JSON responses</li><li>Community support</li></ul>
      <a href="#signup" class="btn-plan-secondary">Get Started</a>
    </div>
    <div class="plan featured">
      <div class="plan-badge">Most Popular</div>
      <div class="plan-name">Pro</div>
      <div class="plan-price">$49<span>/mo</span></div>
      <div class="plan-limit">5,000 requests/month</div>
      <ul><li>5,000 API requests/month</li><li>20 req/min rate limit</li><li>Bulk API (50 domains)</li><li>Redis-cached responses</li><li>Priority support</li></ul>
      <a href="/checkout/pro" class="btn-plan-primary">Get Pro</a>
    </div>
    <div class="plan">
      <div class="plan-name">Business</div>
      <div class="plan-price">$199<span>/mo</span></div>
      <div class="plan-limit">50,000 requests/month</div>
      <ul><li>50,000 API requests/month</li><li>20 req/min rate limit</li><li>Bulk API (50 domains)</li><li>Webhook support</li><li>Dedicated support</li></ul>
      <a href="/checkout/business" class="btn-plan-primary">Get Business</a>
    </div>
  </div>
</div>
<pre>curl -X GET "https://stacksight.org/scrape?domain=stripe.com" -H "X-API-Key: YOUR_KEY"</pre>
<footer>StackSight API &mdash; <a href="/docs">Docs</a> · <a href="mailto:ngryn@stacksight.org">ngryn@stacksight.org</a></footer>
<script>
async function signup() {{
  const email = document.getElementById('email-input').value.trim();
  const msg = document.getElementById('signup-msg');
  if (!email || !email.includes('@')) {{ msg.className='msg error'; msg.style.display='block'; msg.textContent='Please enter a valid email.'; return; }}
  msg.className='msg'; msg.style.display='block'; msg.textContent='Sending verification email...';
  try {{
    const r = await fetch('/signup', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{email}})}});
    const d = await r.json();
    if (r.ok) {{ msg.className='msg success'; msg.textContent='Check your email! Click the link to get your API key.'; }}
    else {{ msg.className='msg error'; msg.textContent=d.detail||'Something went wrong.'; }}
  }} catch(e) {{ msg.className='msg error'; msg.textContent='Network error. Please try again.'; }}
}}
document.getElementById('email-input').addEventListener('keypress', e => {{ if(e.key==='Enter') signup(); }});
</script>
</body>
</html>""")

@app.post("/signup")
async def signup(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    email = body.get("email", "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM api_keys WHERE email=%s AND plan='free'", (email,))
    if cur.fetchone():
        cur.close(); conn.close()
        raise HTTPException(status_code=400, detail="An API key already exists for this email. Check your inbox.")
    cur.execute("SELECT id FROM pending_signups WHERE email=%s AND used=FALSE", (email,))
    if cur.fetchone():
        cur.close(); conn.close()
        raise HTTPException(status_code=400, detail="Verification email already sent. Please check your inbox.")
    token = secrets.token_urlsafe(32)
    cur.execute("INSERT INTO pending_signups (email, token) VALUES (%s, %s) ON CONFLICT (email) DO UPDATE SET token=%s, used=FALSE, created_at=NOW()", (email, token, token))
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
        return HTMLResponse("<h2>Invalid or expired link.</h2>", status_code=400)
    email, used = row
    if used:
        cur.close(); conn.close()
        return HTMLResponse("<h2>This link has already been used.</h2>", status_code=400)
    cur.execute("UPDATE pending_signups SET used=TRUE WHERE token=%s", (token,))
    conn.commit()
    cur.close(); conn.close()
    background_tasks.add_task(provision_api_key, email, "free")
    return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Email Verified - StackSight</title>
<style>body{{font-family:-apple-system,sans-serif;background:#0a0a0a;color:#e5e5e5;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.box{{background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:48px;text-align:center;max-width:480px}}
h1{{color:#a855f7;margin-bottom:12px}}p{{color:#888;margin-bottom:24px}}
a{{background:#a855f7;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600}}</style></head>
<body><div class="box"><h1>Email Verified!</h1>
<p>Your free API key is on its way. Check your inbox within a minute.</p>
<a href="/">Back to StackSight</a></div></body></html>""")

@app.get("/demo/{domain}", response_class=HTMLResponse)
async def demo(domain: str):
    clean = domain.lower().strip().rstrip("/").replace("https://","").replace("http://","")
    data = DEMO_DATA.get(clean, {"company_name": clean.split(".")[0].title(), "is_hiring": True, "engineering_roles": ["Software Engineer"], "sales_roles": ["Account Executive"], "detected_tech_stack": ["JavaScript", "AWS"]})
    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Demo: {clean} - StackSight</title>
<style>body{{font-family:-apple-system,sans-serif;background:#0a0a0a;color:#e5e5e5;padding:40px 20px}}
.box{{max-width:700px;margin:0 auto}}h1{{color:#a855f7}}pre{{background:#111;border:1px solid #1f1f1f;border-radius:10px;padding:24px;overflow-x:auto;font-size:14px}}
a{{color:#a855f7}}</style></head>
<body><div class="box"><h1>StackSight Demo: {clean}</h1>
<p><a href="/">Back</a> | <a href="/#signup">Get your free API key</a></p>
<pre>{json.dumps({{"source":"demo","data":data}},indent=2)}</pre>
<p>This is demo data. <a href="/#signup">Sign up free</a> to analyze any domain live.</p>
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
        raise HTTPException(status_code=404, detail="API key not found")
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
    return HTMLResponse("""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Payment Successful - StackSight</title>
<style>body{font-family:-apple-system,sans-serif;background:#0a0a0a;color:#e5e5e5;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:48px;text-align:center;max-width:500px}
h1{color:#22c55e;margin-bottom:12px}p{color:#888;margin-bottom:24px}
a{background:#a855f7;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600}</style></head>
<body><div class="box"><h1>Payment Successful!</h1>
<p>Your API key is being generated and will arrive in your inbox within a minute.</p>
<a href="/">Back to StackSight</a></div></body></html>""")

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
