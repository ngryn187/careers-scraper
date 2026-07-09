import asyncio
import json
import os
import secrets
import time
import smtplib
from contextlib import asynccontextmanager
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import openai
import psycopg2
import psycopg2.extras
import psycopg2.pool
import redis as redis_lib
import stripe
from fastapi import FastAPI, HTTPException, Security, Header, Request, Response, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel
from typing import List
import uvicorn
from playwright.async_api import async_playwright, Playwright

# ── Globals ──────────────────────────────────────────────────────────────────
_playwright: Playwright = None
_browser = None
_browser_context = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _playwright, _browser, _browser_context
    print("[STARTUP] Launching Playwright browser singleton...")
    _playwright = await async_playwright().start()
    _browser = await _playwright.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
    )
    _browser_context = await _browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    print("[STARTUP] Browser singleton ready.")
    yield
    print("[SHUTDOWN] Closing Playwright browser...")
    await _browser_context.close()
    await _browser.close()
    await _playwright.stop()

app = FastAPI(title="StackSight API", version="8.15.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config ────────────────────────────────────────────────────────────────────
openai.api_key = os.environ.get("OPENAI_API_KEY", "")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
CRON_SECRET = os.environ.get("CRON_SECRET", "stacksight-cron-2024")

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

ZOHO_EMAIL = os.environ.get("ZOHO_EMAIL", "")
ZOHO_PASSWORD = os.environ.get("ZOHO_PASSWORD", "")

# Plan limits
PLAN_LIMITS = {
    "free": 10,
    "pro": 5000,
    "business": 50000,
}

# ── Rate Limiting ─────────────────────────────────────────────────────────────
_rate_limit: dict = {}
RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW = 60

def check_rate_limit(api_key: str):
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    timestamps = _rate_limit.get(api_key, [])
    timestamps = [t for t in timestamps if t > window_start]
    if len(timestamps) >= RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded: max {RATE_LIMIT_MAX} requests/minute")
    timestamps.append(now)
    _rate_limit[api_key] = timestamps

# ── Redis ─────────────────────────────────────────────────────────────────────
redis_client = redis_lib.from_url(REDIS_URL, decode_responses=True)

# ── PostgreSQL ────────────────────────────────────────────────────────────────
_db_pool = None

def get_db_pool():
    global _db_pool
    if _db_pool is None and DATABASE_URL:
        _db_pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DATABASE_URL)
    return _db_pool

def get_db_conn():
    pool = get_db_pool()
    if pool:
        return pool.getconn()
    return None

def release_db_conn(conn):
    pool = get_db_pool()
    if pool and conn:
        pool.putconn(conn)

def init_db():
    conn = get_db_conn()
    if not conn:
        print("[DB] No DATABASE_URL set, skipping DB init")
        return
    try:
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
        conn.commit()
        cur.close()
        print("[DB] api_keys table ready.")
    except Exception as e:
        print(f"[DB] Init error: {e}")
        conn.rollback()
    finally:
        release_db_conn(conn)

# ── Demo Data ─────────────────────────────────────────────────────────────────
DEMO_DATA = {
    "stripe.com": {
        "company_name": "Stripe",
        "is_hiring": True,
        "engineering_roles": ["Backend Engineer", "Infrastructure Engineer", "Security Engineer", "ML Engineer"],
        "sales_roles": ["Account Executive", "Sales Development Representative", "Enterprise Sales"],
        "detected_tech_stack": ["Ruby", "Go", "Java", "React", "AWS", "Kubernetes", "MySQL", "Redis"]
    },
    "figma.com": {
        "company_name": "Figma",
        "is_hiring": True,
        "engineering_roles": ["Software Engineer", "Frontend Engineer", "Platform Engineer"],
        "sales_roles": ["Account Executive", "Customer Success Manager"],
        "detected_tech_stack": ["React", "TypeScript", "WebAssembly", "Rust", "AWS", "Kubernetes"]
    },
    "notion.so": {
        "company_name": "Notion",
        "is_hiring": True,
        "engineering_roles": ["Full Stack Engineer", "iOS Engineer", "Android Engineer"],
        "sales_roles": ["Sales Engineer", "Enterprise Account Executive"],
        "detected_tech_stack": ["React", "Node.js", "TypeScript", "AWS", "PostgreSQL", "Redis"]
    },
    "linear.app": {
        "company_name": "Linear",
        "is_hiring": True,
        "engineering_roles": ["Software Engineer", "Infrastructure Engineer"],
        "sales_roles": [],
        "detected_tech_stack": ["React", "TypeScript", "Node.js", "PostgreSQL", "AWS"]
    },
    "vercel.com": {
        "company_name": "Vercel",
        "is_hiring": True,
        "engineering_roles": ["Software Engineer", "DevOps Engineer", "Runtime Engineer"],
        "sales_roles": ["Enterprise Account Executive", "Solutions Engineer"],
        "detected_tech_stack": ["Next.js", "React", "Node.js", "Go", "AWS", "Cloudflare"]
    },
    "openai.com": {
        "company_name": "OpenAI",
        "is_hiring": True,
        "engineering_roles": ["Research Engineer", "ML Engineer", "Infrastructure Engineer", "Software Engineer"],
        "sales_roles": ["Enterprise Account Executive", "Partnership Manager"],
        "detected_tech_stack": ["Python", "Kubernetes", "Azure", "PyTorch", "CUDA"]
    },
    "shopify.com": {
        "company_name": "Shopify",
        "is_hiring": True,
        "engineering_roles": ["Backend Developer", "Frontend Developer", "Data Engineer"],
        "sales_roles": ["Merchant Success Manager", "Partner Manager"],
        "detected_tech_stack": ["Ruby", "React", "GraphQL", "MySQL", "Kubernetes", "GCP"]
    },
    "hubspot.com": {
        "company_name": "HubSpot",
        "is_hiring": True,
        "engineering_roles": ["Software Engineer", "Platform Engineer", "Data Scientist"],
        "sales_roles": ["Account Executive", "Sales Development Representative", "Channel Account Manager"],
        "detected_tech_stack": ["Java", "React", "AWS", "Kafka", "MySQL", "Elasticsearch"]
    },
    "datadog.com": {
        "company_name": "Datadog",
        "is_hiring": True,
        "engineering_roles": ["Software Engineer", "Site Reliability Engineer", "Security Engineer"],
        "sales_roles": ["Enterprise Account Executive", "Sales Development Representative"],
        "detected_tech_stack": ["Go", "Python", "Kubernetes", "AWS", "GCP", "Azure", "Kafka"]
    },
    "github.com": {
        "company_name": "GitHub",
        "is_hiring": True,
        "engineering_roles": ["Software Engineer", "Staff Engineer", "DevOps Engineer"],
        "sales_roles": ["Enterprise Account Executive", "Customer Success Manager"],
        "detected_tech_stack": ["Ruby", "Go", "React", "MySQL", "Kubernetes", "Azure"]
    },
}

EXTRACTION_PROMPT = (
    "You are a B2B data extraction engine. Given raw text from a company careers page, "
    "extract structured data. Return ONLY valid JSON. If a field cannot be determined, "
    "return an empty array or false. Look for: job titles, technology keywords "
    "(React, AWS, Kubernetes, etc.), and company name. Schema: "
    "{company_name: string, is_hiring: boolean, engineering_roles: [string], "
    "sales_roles: [string], detected_tech_stack: [string]}"
)

async def verify_api_key(x_api_key: str = Header(None)):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    conn = get_db_conn()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM api_keys WHERE api_key = %s AND active = TRUE", (x_api_key,))
        row = cur.fetchone()
        cur.close()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid API key")
        if row["requests_used"] >= row["requests_limit"]:
            raise HTTPException(status_code=429, detail=f"Request limit reached ({row['requests_limit']}/mo). Upgrade at stacksight.org")
        return row
    finally:
        release_db_conn(conn)

def increment_usage(api_key: str):
    conn = get_db_conn()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute("UPDATE api_keys SET requests_used = requests_used + 1 WHERE api_key = %s", (api_key,))
        conn.commit()
        cur.close()
    except Exception as e:
        print(f"[DB] Usage increment error: {e}")
        conn.rollback()
    finally:
        release_db_conn(conn)

def send_api_key_email(to_email: str, api_key: str, plan: str, requests_limit: int):
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Your StackSight API Key"
        msg["From"] = ZOHO_EMAIL
        msg["To"] = to_email
        plan_label = plan.capitalize()
        text = f"Welcome to StackSight {plan_label}!\n\nYour API key: {api_key}\n\nPlan: {plan_label}\nMonthly limit: {requests_limit:,} requests\n\ncurl -H \"X-API-Key: {api_key}\" https://stacksight.org/analyze/stripe.com\n\nSupport: ngryn@stacksight.org"
        html = f"""<html><body style="font-family:sans-serif;max-width:600px;margin:40px auto;color:#1a1a1a">
  <h1 style="color:#6e40c9">Welcome to StackSight {plan_label}!</h1>
  <p>Your API key:</p>
  <div style="background:#f4f4f8;border:1px solid #ddd;border-radius:8px;padding:16px;font-family:monospace;font-size:16px">{api_key}</div>
  <p><strong>Plan:</strong> {plan_label} &mdash; {requests_limit:,} requests/month</p>
  <pre style="background:#1a1a2e;color:#e0e0ff;padding:16px;border-radius:8px">curl -H "X-API-Key: {api_key}" https://stacksight.org/analyze/stripe.com</pre>
  <p><a href="https://stacksight.org" style="color:#6e40c9">stacksight.org</a> &middot; <a href="mailto:ngryn@stacksight.org" style="color:#6e40c9">Support</a></p>
</body></html>"""
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP("smtp.zoho.com", 587) as server:
            server.starttls()
            server.login(ZOHO_EMAIL, ZOHO_PASSWORD)
            server.sendmail(ZOHO_EMAIL, to_email, msg.as_string())
        print(f"[EMAIL] Sent API key to {to_email}")
    except Exception as e:
        print(f"[EMAIL] Failed: {e}")

def provision_api_key(email: str, plan: str, stripe_customer_id: str, stripe_session_id: str):
    api_key = "ss_" + secrets.token_urlsafe(32)
    limit = PLAN_LIMITS.get(plan, 10)
    conn = get_db_conn()
    if not conn:
        print("[DB] Cannot provision key")
        return None
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO api_keys (api_key, email, plan, requests_limit, stripe_customer_id, stripe_session_id)
            VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (api_key) DO NOTHING
        """, (api_key, email, plan, limit, stripe_customer_id, stripe_session_id))
        conn.commit()
        cur.close()
        print(f"[PROVISION] Key created for {email} ({plan})")
        send_api_key_email(email, api_key, plan, limit)
        return api_key
    except Exception as e:
        print(f"[DB] Provision error: {e}")
        conn.rollback()
        return None
    finally:
        release_db_conn(conn)

async def scrape_page(domain: str):
    domain = domain.strip().lower().rstrip("/")
    if not domain.startswith("http"):
        domain = "https://" + domain
    page = await _browser_context.new_page()
    try:
        for suffix in ["/careers", "/jobs"]:
            url = domain + suffix
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                if resp and resp.status < 400:
                    await asyncio.sleep(2)
                    text = await page.inner_text("body")
                    return text, url, resp.status
            except Exception:
                continue
        raise HTTPException(status_code=404, detail="No careers/jobs page found for " + domain)
    finally:
        await page.close()

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

LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>StackSight - B2B Sales Intelligence API</title>
<meta name="description" content="Turn any company domain into B2B sales intelligence. Detect hiring intent, tech stack, and open roles via a single API call.">
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0d0d1a;color:#e0e0ff;line-height:1.6}
  .hero{text-align:center;padding:80px 20px 60px;background:linear-gradient(135deg,#0d0d1a 0%,#1a0a2e 100%)}
  .badge{display:inline-block;background:#1a1a3e;border:1px solid #6e40c9;color:#a78bfa;padding:6px 16px;border-radius:20px;font-size:13px;margin-bottom:24px}
  h1{font-size:clamp(2rem,5vw,3.5rem);font-weight:800;background:linear-gradient(135deg,#a78bfa,#60a5fa);-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:20px}
  .subtitle{font-size:1.2rem;color:#9ca3af;max-width:600px;margin:0 auto 40px}
  .btn{display:inline-block;padding:14px 28px;border-radius:8px;font-weight:600;font-size:1rem;text-decoration:none;cursor:pointer;border:none;transition:opacity .2s}
  .btn:hover{opacity:.85}
  .btn-primary{background:linear-gradient(135deg,#6e40c9,#4f46e5);color:white}
  .btn-secondary{background:#1a1a3e;color:#a78bfa;border:1px solid #6e40c9}
  .btn-group{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}
  .section{padding:60px 20px;max-width:1100px;margin:0 auto}
  .section-title{text-align:center;font-size:2rem;font-weight:700;margin-bottom:40px;color:#e0e0ff}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:24px}
  .card{background:#111127;border:1px solid #1e1e4e;border-radius:12px;padding:28px}
  .card h3{font-size:1.1rem;margin-bottom:10px;color:#a78bfa}
  .card p{color:#9ca3af;font-size:.95rem}
  .code-block{background:#0a0a1a;border:1px solid #1e1e4e;border-radius:10px;padding:24px;font-family:monospace;font-size:.9rem;overflow-x:auto;margin:20px 0;color:#a5f3fc}
  .pricing{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:24px;max-width:900px;margin:0 auto}
  .plan{background:#111127;border:1px solid #1e1e4e;border-radius:12px;padding:32px;text-align:center}
  .plan.featured{border-color:#6e40c9;background:#130d2e}
  .plan-name{font-size:1.1rem;font-weight:600;color:#a78bfa;margin-bottom:8px}
  .plan-price{font-size:2.5rem;font-weight:800;color:#e0e0ff;margin-bottom:4px}
  .plan-price span{font-size:1rem;color:#9ca3af}
  .plan-features{list-style:none;margin:20px 0 28px;text-align:left}
  .plan-features li{padding:6px 0;color:#9ca3af;font-size:.9rem}
  .plan-features li::before{content:"checkmark ";color:#6e40c9;font-weight:700}
  footer{text-align:center;padding:40px 20px;border-top:1px solid #1e1e4e;color:#4b5563;font-size:.9rem}
  footer a{color:#6e40c9;text-decoration:none}
</style>
</head>
<body>
<div class="hero">
  <div class="badge">Live API - v8.15.0</div>
  <h1>B2B Sales Intelligence API</h1>
  <p class="subtitle">Turn any company domain into sales intelligence in seconds. Detect hiring intent, tech stack, and open roles via a single API call.</p>
  <div class="btn-group">
    <a href="/demo/stripe.com" class="btn btn-primary">Try Demo</a>
    <a href="#pricing" class="btn btn-secondary">View Pricing</a>
    <a href="/trending" class="btn btn-secondary">Trending Companies</a>
  </div>
</div>
<div class="section">
  <h2 class="section-title">What You Get</h2>
  <div class="cards">
    <div class="card"><h3>Hiring Intent</h3><p>Know if a company is actively hiring before you reach out. Filter leads by role type: engineering, sales, marketing.</p></div>
    <div class="card"><h3>Tech Stack Detection</h3><p>Detect React, Next.js, AWS, Cloudflare, Stripe, and 15+ more technologies with high accuracy.</p></div>
    <div class="card"><h3>Single API Call</h3><p>Pass any domain, get structured JSON back. No scraping setup, no maintenance, no headless browser needed.</p></div>
    <div class="card"><h3>Redis Cached</h3><p>Results cached for 7 days so repeated lookups are instant. Demo data pre-loaded for top 10 SaaS companies.</p></div>
  </div>
</div>
<div class="section">
  <h2 class="section-title">Quick Start</h2>
  <div class="code-block">curl -H "X-API-Key: YOUR_KEY" https://stacksight.org/analyze/stripe.com</div>
  <div class="code-block">{"source":"demo","data":{"company_name":"Stripe","is_hiring":true,"engineering_roles":["Backend Engineer","Infrastructure Engineer"],"sales_roles":["Account Executive"],"detected_tech_stack":["Ruby","Go","React","AWS","Redis"]}}</div>
</div>
<div class="section" id="pricing">
  <h2 class="section-title">Pricing</h2>
  <div class="pricing">
    <div class="plan">
      <div class="plan-name">Free</div>
      <div class="plan-price">$0<span>/mo</span></div>
      <ul class="plan-features"><li>10 requests/month</li><li>All endpoints</li><li>Demo data included</li></ul>
      <a href="/docs" class="btn btn-secondary" style="width:100%;text-align:center">Get Started</a>
    </div>
    <div class="plan featured">
      <div class="plan-name">Pro</div>
      <div class="plan-price">$49<span>/mo</span></div>
      <ul class="plan-features"><li>5,000 requests/month</li><li>All endpoints</li><li>Webhook notifications</li><li>Priority support</li></ul>
      <button class="btn btn-primary" style="width:100%" onclick="window.location.href='/checkout/pro'">Subscribe</button>
    </div>
    <div class="plan">
      <div class="plan-name">Business</div>
      <div class="plan-price">$199<span>/mo</span></div>
      <ul class="plan-features"><li>50,000 requests/month</li><li>All endpoints</li><li>Webhook notifications</li><li>Dedicated support</li><li>SLA guarantee</li></ul>
      <button class="btn btn-secondary" style="width:100%" onclick="window.location.href='/checkout/business'">Subscribe</button>
    </div>
  </div>
</div>
<footer><p>2026 StackSight - <a href="/docs">Docs</a> - <a href="/trending">Trending</a> - <a href="mailto:ngryn@stacksight.org">Support</a></p></footer>
</body>
</html>"""

SUCCESS_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Payment Successful - StackSight</title>
<style>body{font-family:sans-serif;background:#0d0d1a;color:#e0e0ff;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}.box{background:#111127;border:1px solid #6e40c9;border-radius:16px;padding:48px;max-width:500px;text-align:center}h1{color:#a78bfa;margin-bottom:16px}p{color:#9ca3af;margin-bottom:12px}a{color:#6e40c9}</style>
</head>
<body><div class="box"><div style="font-size:3rem">🎉</div><h1>Payment Successful!</h1><p>Your API key has been sent to your email address.</p><p>Check your inbox (and spam folder) for an email from <strong>ngryn@stacksight.org</strong>.</p><p style="margin-top:24px"><a href="/">Return to StackSight</a></p></div></body>
</html>"""

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(content=LANDING_HTML)

@app.get("/health")
async def health():
    try:
        redis_client.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {"status": "ok", "version": "8.15.0", "redis": redis_ok, "db": get_db_pool() is not None}

@app.get("/demo/{domain}")
async def demo(domain: str):
    domain_clean = domain.strip().lower().rstrip("/").replace("https://", "").replace("http://", "")
    if domain_clean in DEMO_DATA:
        return {"source": "demo", "data": DEMO_DATA[domain_clean]}
    cache_key = f"domain:{domain_clean}"
    cached = redis_client.get(cache_key)
    if cached:
        return {"source": "cache", "data": json.loads(cached)}
    raw_text, url, status = await scrape_page(domain_clean)
    extracted = extract_with_openai(raw_text)
    redis_client.setex(cache_key, 604800, json.dumps(extracted))
    return {"source": "live", "data": extracted}

@app.get("/analyze/{domain}")
async def analyze(domain: str, key_row=Security(verify_api_key)):
    domain_clean = domain.strip().lower().rstrip("/").replace("https://", "").replace("http://", "")
    api_key = key_row["api_key"]
    check_rate_limit(api_key)
    if domain_clean in DEMO_DATA:
        increment_usage(api_key)
        return {"source": "demo", "data": DEMO_DATA[domain_clean]}
    cache_key = f"domain:{domain_clean}"
    cached = redis_client.get(cache_key)
    if cached:
        increment_usage(api_key)
        return {"source": "cache", "data": json.loads(cached)}
    raw_text, url, status = await scrape_page(domain_clean)
    extracted = extract_with_openai(raw_text)
    redis_client.setex(cache_key, 604800, json.dumps(extracted))
    increment_usage(api_key)
    return {"source": "live", "data": extracted}

@app.get("/checkout/{plan}")
async def checkout(plan: str):
    plan = plan.lower()
    prices = {
        "pro": os.environ.get("STRIPE_PRICE_PRO", "price_1TrLQ6DUssNU8xAWD0eyqLx4"),
        "business": os.environ.get("STRIPE_PRICE_BUSINESS", "price_1TrLceDUssNU8xAWKWUSPLlR"),
    }
    price_id = prices.get(plan)
    if not price_id:
        raise HTTPException(status_code=400, detail=f"Unknown plan '{plan}'")
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url="https://stacksight.org/success",
            cancel_url="https://stacksight.org/#pricing",
            metadata={"plan": plan},
        )
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=session.url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/success", response_class=HTMLResponse)
async def success():
    return HTMLResponse(content=SUCCESS_HTML)

@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        email = session.get("customer_details", {}).get("email") or session.get("customer_email", "")
        plan = session.get("metadata", {}).get("plan", "pro")
        customer_id = session.get("customer", "")
        session_id = session.get("id", "")
        if email:
            background_tasks.add_task(provision_api_key, email, plan, customer_id, session_id)
    return {"status": "ok"}

@app.get("/trending")
async def trending():
    return {"trending": [{"domain": d, **data} for d, data in DEMO_DATA.items()], "count": len(DEMO_DATA)}

@app.get("/usage")
async def usage(x_api_key: str = Header(None)):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    conn = get_db_conn()
    if not conn:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT plan, requests_used, requests_limit, created_at FROM api_keys WHERE api_key = %s AND active = TRUE", (x_api_key,))
        row = cur.fetchone()
        cur.close()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return {"plan": row["plan"], "requests_used": row["requests_used"], "requests_limit": row["requests_limit"], "requests_remaining": row["requests_limit"] - row["requests_used"], "member_since": str(row["created_at"])}
    finally:
        release_db_conn(conn)

@app.post("/admin/create-key")
async def admin_create_key(request: Request, x_cron_secret: str = Header(None)):
    if x_cron_secret != CRON_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    body = await request.json()
    email = body.get("email")
    plan = body.get("plan", "pro")
    if not email:
        raise HTTPException(status_code=400, detail="email required")
    api_key = provision_api_key(email, plan, "manual", "manual")
    if not api_key:
        raise HTTPException(status_code=500, detail="Failed to create key")
    return {"api_key": api_key, "email": email, "plan": plan}

@app.on_event("startup")
async def startup_event():
    init_db()

if __name__ == "__main__":
    uvicorn.run("scraper:app", host="0.0.0.0", port=8000, reload=True)
