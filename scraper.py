import asyncio
import json
import httpx
import os
import secrets
import time

import openai
import psycopg2
import psycopg2.extrash
import psycopg2.pool
from contextlib import contextmanager, asynccontextmanager
import redis as redis_lib
import stripe
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from fastapi import FastAPI, HTTPException, Security, Header, Request, Response, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from datetime import datetime
from pydantic import BaseModel
from typing import List
import uvicorn
from playwright.async_api import async_playwright, Playwright

# Global Playwright singleton â launched once at startup, reused for all requests
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

app = FastAPI(title="Careers Scraper API", version="8.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

openai.api_key = os.environ.get("OPENAI_API_KEY", "")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
redis_client = redis_lib.from_url(REDIS_URL, decode_responses=True)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")
BASE_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "careers-scraper-production.up.railway.app")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# SMTP Email Config
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "ngrynai@gmail.com")

def send_api_key_email(user_email: str, api_key: str, tier: str = "Free"):
    """Send API key to user via email after signup or Stripe payment."""
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        print(f"SMTP not configured, skipping email to {user_email}")
        return
    try:
        html_body = f"""
<html><body style="font-family:sans-serif;background:#0d1117;color:#c9d1d9;padding:30px">
<h2 style="color:#58a6ff">Your StackSight API Key ({tier} Tier)</h2>
<p>Thanks for signing up! Here is your API key:</p>
<pre style="background:#161b22;padding:15px;border-radius:6px;color:#79c0ff">{api_key}</pre>
<h3>Quick Start</h3>
<pre style="background:#161b22;padding:15px;border-radius:6px;color:#79c0ff">curl -X GET "https://careers-scraper-production.up.railway.app/scrape?domain=stripe.com" \\
-H "x-api-key: {api_key}"</pre>
<p>Read the <a href="https://careers-scraper-production.up.railway.app/docs" style="color:#58a6ff">full API docs</a>.</p>
<p>Thank you for your business!</p>
</body></html>
"""
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"Your StackSight API Key ({tier} Tier)"
        msg['From'] = FROM_EMAIL
        msg['To'] = user_email
        msg.attach(MIMEText(html_body, 'html'))
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, user_email, msg.as_string())
        print(f"API key email sent to {user_email}")
    except Exception as e:
        print(f"Failed to send email to {user_email}: {e}")

EXTRACTION_PROMPT = (
    "You are a B2B data extraction engine. Given raw text from a company careers page, "
    "extract structured data. Return ONLY valid JSON. Schema: "
    "{company_name: string, is_hiring: boolean, engineering_roles: [string], "
    "sales_roles: [string], detected_tech_stack: [string]}"
)

LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>StackSight API - B2B Hiring Intent &amp; Tech Stack Data</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0d1117; color: #c9d1d9; min-height: 100vh; }
.container { max-width: 900px; margin: 0 auto; padding: 60px 20px; }
.badge { display: inline-block; background: #161b22; border: 1px solid #30363d; color: #58a6ff; font-size: 0.75em; padding: 4px 12px; border-radius: 20px; margin-bottom: 20px; }
h1 { color: #58a6ff; font-size: 2.8em; margin-bottom: 12px; line-height: 1.2; }
.subtitle { font-size: 1.2em; color: #8b949e; margin-bottom: 30px; max-width: 640px; }
.cta-group { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 50px; }
.btn { display: inline-block; padding: 12px 24px; border-radius: 6px; font-weight: 700; text-decoration: none; font-size: 1em; cursor: pointer; border: none; }
.btn-primary { background: #238636; color: white; }
.btn-primary:hover { background: #2ea043; }
.btn-secondary { background: #1f6feb; color: white; }
.btn-secondary:hover { background: #388bfd; }
.features { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin: 40px 0; }
.feature { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 24px; }
.feature h3 { color: #e6edf3; font-size: 1em; margin-bottom: 10px; }
.feature p { color: #8b949e; font-size: 0.9em; line-height: 1.5; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 30px; margin-bottom: 24px; }
.card h3 { color: #e6edf3; font-size: 1.1em; margin-bottom: 12px; }
.card p { color: #8b949e; font-size: 0.95em; margin-bottom: 16px; }
input[type="email"] { width: 100%; padding: 12px 16px; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; color: #e6edf3; font-size: 1em; margin-bottom: 12px; outline: none; }
input[type="email"]:focus { border-color: #58a6ff; }
button.gen-btn { background: #238636; color: white; border: none; padding: 12px 24px; border-radius: 6px; font-size: 1em; cursor: pointer; width: 100%; font-weight: 600; }
button.gen-btn:hover { background: #2ea043; }
.key-display { background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 14px; font-family: monospace; font-size: 0.9em; color: #79c0ff; margin-top: 12px; display: none; word-break: break-all; }
pre { background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 20px; overflow-x: auto; margin: 16px 0; }
code { color: #79c0ff; font-family: 'SFMono-Regular', Consolas, monospace; font-size: 0.9em; line-height: 1.6; }
.pricing { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin: 24px 0; }
.price-box { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 24px; text-align: center; }
.price-box.featured { border-color: #58a6ff; }
.price-box h3 { color: #e6edf3; margin-bottom: 8px; }
.price-box .price { font-size: 2em; font-weight: 700; color: #58a6ff; margin: 12px 0; }
.price-box ul { list-style: none; color: #8b949e; font-size: 0.9em; text-align: left; }
.price-box ul li { padding: 5px 0; }
.price-box ul li::before { content: "\2713 "; color: #2ea043; }
.price-box .plan-btn { margin-top: 16px; background: #238636; color: white; border: none; padding: 10px 20px; border-radius: 6px; font-weight: 600; cursor: pointer; width: 100%; }
.price-box.featured .plan-btn { background: #1f6feb; }
.price-box.featured .plan-btn:hover { background: #388bfd; }
.section-title { font-size: 1.5em; color: #e6edf3; margin: 50px 0 20px; }
.endpoint-row { display: flex; align-items: center; gap: 12px; background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 14px 18px; margin-bottom: 10px; }
.method { background: #0d7a3c; color: white; font-size: 0.8em; font-weight: 700; padding: 3px 10px; border-radius: 4px; font-family: monospace; }
.method.post { background: #1f4080; }
.path { color: #79c0ff; font-family: monospace; }
.desc { color: #8b949e; font-size: 0.9em; margin-left: auto; }
footer { border-top: 1px solid #30363d; margin-top: 60px; padding-top: 30px; color: #8b949e; font-size: 0.9em; text-align: center; }
footer a { color: #58a6ff; text-decoration: none; }
@media (max-width: 600px) { .pricing { grid-template-columns: 1fr; } h1 { font-size: 2em; } .features { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="container">
<div class="badge">&#x1F680; Now on RapidAPI &middot; v8.3.0</div>
<h1>StackSight API</h1>
<p class="subtitle">Turn any company domain into actionable B2B sales intelligence. Real-time hiring intent, deterministic tech stack detection, and bulk enrichment &mdash; before your competitors know what hit them.</p>
<div class="cta-group">
<a href="/demo/stripe.com" class="btn btn-secondary">&#x1F50D; Live Demo</a>
<a href="#get-key" class="btn btn-primary">Get Free API Key</a>
</div>

<div class="features">
<div class="feature">
<h3>&#x1F3AF; Real-Time Hiring Intent</h3>
<p>Know exactly which companies are actively hiring engineers, sales reps, or executives. Stop cold-calling companies that are not growing.</p>
</div>
<div class="feature">
<h3>&#x1F9EC; Deterministic Tech Stack</h3>
<p>We parse actual script tags to detect React, AWS, Stripe, and 20+ technologies with 100% accuracy. No guessing.</p>
</div>
<div class="feature">
<h3>&#x1F4E6; Bulk Enrichment API</h3>
<p>Process up to 50 domains in one request. Cached results return in under 50ms. Uncached domains queue in background.</p>
</div>
<div class="feature">
<h3>&#x26A1; Lightning Fast Cache</h3>
<p>All scrapes cached in Redis for 7 days. Top 80 SaaS domains pre-warmed nightly. Most requests return instantly.</p>
</div>
</div>

<div class="card" id="get-key">
<h3>&#x26A1; Get Started Free</h3>
<p>5 requests/month, no credit card required:</p>
<input type="email" id="email" placeholder="you@company.com">
<button class="gen-btn" onclick="generateKey()">Get My Free API Key</button>
<div class="key-display" id="keyResult"></div>
</div>

<h2 class="section-title">Try It Now</h2>
<div class="card">
<h3>Example Request</h3>
<pre><code id="curlExample">curl -X GET "https://careers-scraper-production.up.railway.app/scrape?domain=stripe.com" \
-H "X-API-Key: YOUR_API_KEY"</code></pre>
<h3 style="margin-top:20px;">Example Response</h3>
<pre><code>{
  "source": "cache",
  "data": {
    "company_name": "Stripe",
    "is_hiring": true,
    "engineering_roles": ["Backend Engineer", "ML Engineer", "Platform Engineer"],
    "sales_roles": ["Account Executive", "Solutions Engineer"],
    "detected_tech_stack": ["React", "AWS", "Stripe", "Cloudflare", "Sentry"]
  }
}</code></pre>
</div>

<h2 class="section-title">Endpoints</h2>
<div class="endpoint-row">
<span class="method">GET</span>
<span class="path">/scrape?domain={domain}</span>
<span class="desc">Hiring intent + tech stack (Redis cached)</span>
</div>
<div class="endpoint-row">
<span class="method post">POST</span>
<span class="path">/scrape/bulk</span>
<span class="desc">Bulk enrichment up to 50 domains (Pro)</span>
</div>
<div class="endpoint-row">
<span class="method">GET</span>
<span class="path">/demo/{domain}</span>
<span class="desc">Interactive HTML demo - no key needed</span>
</div>
<div class="endpoint-row">
<span class="method">GET</span>
<span class="path">/me</span>
<span class="desc">Your plan, usage and remaining requests</span>
</div>
<div class="endpoint-row">
<span class="method">GET</span>
<span class="path">/docs</span>
<span class="desc">Interactive Swagger documentation</span>
</div>
<div class="endpoint-row">
<span class="method">GET</span>
<span class="path">/health</span>
<span class="desc">API health and version check</span>
</div>

<h2 class="section-title" id="pricing">Pricing</h2>
<div class="pricing">
<div class="price-box">
<h3>Free</h3>
<div class="price">$0<span style="font-size:0.4em;color:#8b949e">/mo</span></div>
<ul>
<li>50 requests/month</li>
<li>1 request/second</li>
<li>JSON responses</li>
<li>Community support</li>
</ul>
<button class="plan-btn" onclick="document.getElementById('email').focus()">Get Started</button>
</div>
<div class="price-box featured">
<h3>Pro</h3>
<div class="price">$49<span style="font-size:0.4em;color:#8b949e">/mo</span></div>
<ul>
<li>2,500 requests/month</li>
<li>10 requests/second</li>
<li>Bulk API (50 domains)</li>
<li>Redis-cached responses</li>
<li>Priority support</li>
</ul>
<button class="plan-btn" onclick="window.location.href='mailto:ngrynai@gmail.com?subject=StackSight%20Pro%20Subscription'">Subscribe on RapidAPI</button>
</div>
<div class="price-box">
<h3>Business</h3>
<div class="price">$199<span style="font-size:0.4em;color:#8b949e">/mo</span></div>
<ul>
<li>15,000 requests/month</li>
<li>Unlimited rate limit</li>
<li>Webhook support</li>
<li>Dedicated support</li>
</ul>
<button class="plan-btn" onclick="window.open('https://rapidapi.com/search/stacksight','_blank')">Contact Sales</button>
</div>
</div>

<footer>
<p>StackSight API &mdash; <a href="/docs">API Docs</a> &middot; <a href="https://rapidapi.com" target="_blank">RapidAPI</a> &middot; <a href="https://github.com/ngryn187/careers-scraper" target="_blank">GitHub</a> &middot; Built with FastAPI</p>
<p style="margin-top:8px;">Questions? Email <a href="mailto:ngrynai@gmail.com">ngrynai@gmail.com</a></p>
</footer>
</div>
<script>
async function generateKey() {
    const email = document.getElementById('email').value.trim();
    if (!email || !email.includes('@')) { alert('Please enter a valid email address'); return; }
    const btn = event.target;
    btn.textContent = 'Generating...';
    btn.disabled = true;
    try {
        const res = await fetch('/generate-key', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({email: email})
        });
        const data = await res.json();
        if (data.api_key) {
            const box = document.getElementById('keyResult');
            box.style.display = 'block';
            box.innerHTML = '<strong style="color:#2ea043">â Your API Key:</strong><br>' + data.api_key + '<br><br><small style="color:#8b949e">Use header: X-API-Key: ' + data.api_key + '</small>';
            document.getElementById('curlExample').textContent = 'curl -X GET "https://careers-scraper-production.up.railway.app/scrape?domain=stripe.com" \\\\n -H "X-API-Key: ' + data.api_key + '"';
        } else {
            alert(data.detail || 'Error generating key. Please try again.');
        }
    } catch(e) {
        alert('Error: ' + e.message);
    }
    btn.textContent = 'Get My Free API Key';
    btn.disabled = false;
}
</script>
</body>
</html>"""

class FreeKeyRequest(BaseModel):
    email: str

# Connection pool Ã¢ÂÂ initialized once at startup, shared across all requests
postgre_pool = None

def init_pool():
    global postgre_pool
    if DATABASE_URL:
        try:
            postgre_pool = psycopg2.pool.SimpleConnectionPool(1, 10, DATABASE_URL)
            print("Postgres connection pool initialized (min=1, max=10)")
        except Exception as e:
            print(f"Failed to initialize connection pool: {e}")

init_pool()  # Called after function is defined

@contextmanager
def get_db_connection():
    """Borrow a connection from the pool, return it when done."""
    if not postgre_pool:
        raise Exception("Database pool not initialized")
    conn = postgre_pool.getconn()
    try:
        yield conn
    finally:
        postgre_pool.putconn(conn)

def send_usage_alert_email(user_email: str, plan: str, usage: int, limit: int):
    """Sends email when user hits 80% or 100% of monthly quota."""
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        return
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    from_email = os.getenv("FROM_EMAIL", SMTP_USERNAME)
    if usage >= limit:
        subject = f"Action Required: StackSight API Limit Reached ({limit}/{limit})"
        body = (f"You have reached your monthly limit of {limit} requests on the {plan} tier. "
                f"Upgrade to continue: https://careers-scraper-production.up.railway.app")
    else:
        subject = f"Heads Up: StackSight API Usage at 80% ({usage}/{limit})"
        body = (f"You've used {usage} of {limit} requests on the {plan} tier. "
                f"Upgrade to avoid interruptions: https://careers-scraper-production.up.railway.app")
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = from_email
    msg['To'] = user_email
    msg.attach(MIMEText(body, 'plain'))
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(from_email, user_email, msg.as_string())
        print(f"[EMAIL] Usage alert sent to {user_email} ({usage}/{limit})")
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")


def get_db():
    """Legacy: open a single connection."""
    return psycopg2.connect(DATABASE_URL)

def init_db():
    if not DATABASE_URL:
        return
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key VARCHAR(64) PRIMARY KEY,
                email VARCHAR(255) NOT NULL,
                stripe_customer_id VARCHAR(255),
                plan VARCHAR(50) DEFAULT 'free',
                monthly_limit INTEGER DEFAULT 50,
                usage_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS webhook_subscriptions (
                id SERIAL PRIMARY KEY,
                api_key VARCHAR(64) REFERENCES api_keys(key),
                domain VARCHAR(255) NOT NULL,
                webhook_url TEXT NOT NULL,
                last_known_status BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(api_key, domain)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS newsletter_subs (
                email VARCHAR(255) PRIMARY KEY,
                subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"DB init error: {e}")

@app.on_event("startup")
async def startup():
    init_db()

@app.get("/", response_class=HTMLResponse)
async def landing():
    return HTMLResponse(content=LANDING_HTML)

@app.post("/generate-key")
@app.post("/generate-free-key")
async def generate_free_key(body: FreeKeyRequest):
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="Database not configured")
    email = body.email.lower().strip()
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT key, plan FROM api_keys WHERE email = %s LIMIT 1", (email,))
        row = cur.fetchone()
        if row:
            cur.close(); conn.close()
            return {"api_key": row["key"], "plan": row["plan"], "existing": True}
        new_key = "sk_free_" + secrets.token_urlsafe(32)
        cur.execute("INSERT INTO api_keys (key, email, plan, monthly_limit) VALUES (%s, %s, %s, %s)", (new_key, email, "free", 50))
        send_api_key_email(email, new_key, "Free")
        conn.commit()
        cur.close(); conn.close()
        return {"api_key": new_key, "plan": "free", "monthly_limit": 50}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/create-checkout-session")
async def create_checkout_session():
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")
    if not STRIPE_PRICE_ID:
        raise HTTPException(status_code=500, detail="STRIPE_PRICE_ID not set")
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            mode="subscription",
            success_url=f"https://{BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"https://{BASE_URL}/",
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/success", response_class=HTMLResponse)
async def success(session_id: str = ""):
    api_key = None
    email = ""
    if session_id and stripe.api_key:
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            email = session.get("customer_details", {}).get("email", "")
            if email and DATABASE_URL:
                conn = get_db()
                cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                cur.execute("SELECT key FROM api_keys WHERE email = %s LIMIT 1", (email.lower(),))
                row = cur.fetchone()
                if row:
                    api_key = row["key"]
                cur.close(); conn.close()
        except Exception as e:
            print(f"Success page error: {e}")
    html = f"""<!DOCTYPE html><html><head><title>StackSight - Success</title>
<style>body{{font-family:system-ui;max-width:600px;margin:80px auto;padding:0 20px;background:#0f0f0f;color:#e0e0e0}}
.card{{background:#1a1a1a;border:1px solid #333;border-radius:12px;padding:32px}}
h1{{color:#4caf50}}code{{background:#111;padding:12px;border-radius:8px;display:block;word-break:break-all;margin-top:12px}}</style></head>
<body><div class="card"><h1>Payment Successful!</h1>
{"<p>Your API key:</p><code>" + api_key + "</code>" if api_key else "<p>Key being activated...</p>"}
<p style="margin-top:24px"><a href="/docs" style="color:#7c83fc">API docs</a> &nbsp; <a href="/" style="color:#7c83fc">Home</a></p>
</div></body></html>"""
    return HTMLResponse(content=html)

async def verify_api_key(
    request: Request,
    x_api_key: str = Header(None),
    x_rapidapi_proxy_secret: str = Header(None, alias="X-RapidAPI-Proxy-Secret"),
):
    # Allow RapidAPI proxy requests through with their secret
    rapidapi_secret = os.environ.get("RAPIDAPI_PROXY_SECRET", "")
    if rapidapi_secret and x_rapidapi_proxy_secret == rapidapi_secret:
        request.state.rate_limit = "Unlimited (RapidAPI)"
        request.state.rate_remaining = "N/A"
        return "rapidapi_user"
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    if not DATABASE_URL:
        valid_key = os.environ.get("VALID_API_KEY", "")
        if x_api_key != valid_key:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return x_api_key
    try:
        with get_db_connection() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            cur.execute("SELECT * FROM api_keys WHERE key = %s", (x_api_key,))
            row = cur.fetchone()
            cur.close()
            if not row:
                raise HTTPException(status_code=401, detail="Invalid API key")
            monthly_limit = row["monthly_limit"]
            plan = row["plan"]

            current_month = time.strftime("%Y-%m")
            redis_key = f"usage:{x_api_key}:{current_month}"
            current_count = int(redis_client.get(redis_key) or 0)
            if current_count >= monthly_limit:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded. {plan.capitalize()} tier limit: {monthly_limit} requests/month"
                )
            request.state.redis_key = redis_key
            request.state.monthly_limit = monthly_limit
            request.state.rate_limit = monthly_limit
            request.state.rate_remaining = max(0, monthly_limit - current_count)
            request.state.plan = plan
            return x_api_key
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Auth error: {str(e)}")

@app.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        except stripe.error.SignatureVerificationError:
            raise HTTPException(status_code=400, detail="Invalid signature")
    else:
        raise HTTPException(status_code=500, detail="STRIPE_WEBHOOK_SECRET not configured Ã¢ÂÂ rejecting request")
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        email = session.get("customer_details", {}).get("email", "").lower()
        customer_id = session.get("customer", "")
        if not email:
            return {"status": "ok"}
        if DATABASE_URL:
            try:
                conn = get_db()
                cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
                cur.execute("SELECT key FROM api_keys WHERE email = %s LIMIT 1", (email,))
                row = cur.fetchone()
                if row:
                    cur.execute("UPDATE api_keys SET plan='pro', monthly_limit=2500, stripe_customer_id=%s WHERE email=%s", (customer_id, email))
                else:
                    new_key = "sk_live_" + secrets.token_urlsafe(32)
                    cur.execute("INSERT INTO api_keys (key, email, stripe_customer_id, plan, monthly_limit) VALUES (%s,%s,%s,%s,%s)", (new_key, email, customer_id, "pro", 2500))
                    send_api_key_email(email, new_key, "Pro")
                conn.commit()
                cur.close(); conn.close()
            except Exception as e:
                print(f"Webhook error: {e}")
    return {"status": "ok"}

def detect_tech_stack_locally(html_source: str, scripts: list) -> list:
    """Detects tech stack via regex on HTML/Scripts. Free and fast."""
    combined = html_source + " " + " ".join(scripts)
    tech = set()
    # Frontend
    if "react" in combined.lower() or "react-dom" in combined.lower():
        tech.add("React")
    if "vue" in combined.lower() or "vue.js" in combined.lower():
        tech.add("Vue")
    if "angular" in combined.lower():
        tech.add("Angular")
    if "next.js" in combined.lower() or "_next/" in combined.lower():
        tech.add("Next.js")
    # Backend/Infra
    if "aws" in combined.lower() or "amazonaws" in combined.lower():
        tech.add("AWS")
    if "cloudflare" in combined.lower():
        tech.add("Cloudflare")
    if "vercel" in combined.lower():
        tech.add("Vercel")
    if "docker" in combined.lower():
        tech.add("Docker")
    # Analytics/Tools
    if "google-analytics" in combined.lower() or "gtag" in combined.lower():
        tech.add("Google Analytics")
    if "sentry" in combined.lower():
        tech.add("Sentry")
    if "stripe" in combined.lower():
        tech.add("Stripe")
    return list(tech)

async def find_careers_url(page, base_domain: str) -> str:
    """Load homepage and follow links containing careers/jobs/team keywords."""
    keywords = ["career", "careers", "jobs", "job", "team", "work", "hiring", "join"]
    try:
        await page.goto(base_domain, wait_until="domcontentloaded", timeout=15000)
        links = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.href,
                text: a.innerText.toLowerCase().trim()
            }));
        }""")
        for link in links:
            href = link.get('href', '')
            text = link.get('text', '')
            if any(kw in text for kw in keywords) or any(kw in href.lower() for kw in keywords):
                if href.startswith('http') and (base_domain.replace('https://', '').replace('http://', '').split('/')[0] in href or href.startswith('/')):
                    if href.startswith('/'):
                        href = base_domain.rstrip('/') + href
                    return href
    except Exception:
        pass
    # Fallback: guess common paths
    return base_domain.rstrip('/') + "/careers"

async def scrape_page(domain: str):
    domain = domain.strip().lower().rstrip("/")
    if not domain.startswith("http"):
        domain = "https://" + domain
    if not _browser_context:
        raise HTTPException(status_code=503, detail="Browser not initialized")
    page = await _browser_context.new_page()
    try:
        careers_url = await find_careers_url(page, domain)
        resp = await page.goto(careers_url, wait_until="domcontentloaded", timeout=15000)
        if resp and resp.status < 400:
            await asyncio.sleep(2)
            text = await page.inner_text("body")
            scripts = await page.evaluate("""() => Array.from(document.querySelectorAll('script[src]')).map(s => s.src).filter(Boolean)""")
            if scripts:
                text += "\n\nDETECTED SCRIPTS:\n" + "\n".join(scripts[:50])
            return text, careers_url, resp.status
        raise HTTPException(status_code=404, detail="No careers/jobs page found for " + domain)
    except HTTPException:
        raise
    except Exception:
        # Fallback: try common direct paths
    fallback_paths = [
        "/about/careers", "/company/careers", "/company/jobs",
        "/open-positions", "/open-roles", "/join-us", "/join",
        "/hiring", "/work-with-us", "/about/jobs",
    ]
    for path in fallback_paths:
        try:
            r = await page.goto(domain.rstrip("/") + path, wait_until="domcontentloaded", timeout=8000)
            if r and r.status < 400:
                body = await page.inner_text("body")
                if any(k in body.lower() for k in ["apply", "position", "role", "opening", "vacancy"]):
                    return domain.rstrip("/") + path
        except Exception:
            continue
    # Try careers subdomain
    try:
        dom_root = domain.replace("https://", "").replace("http://", "").split("/")[0]
        sub = "https://careers." + dom_root
        r = await page.goto(sub, wait_until="domcontentloaded", timeout=8000)
        if r and r.status < 400:
            return sub
    except Exception:
        pass
    raise HTTPException(status_code=404, detail="No careers/jobs page found for " + domain)
    finally:
        await page.close()

def extract_with_openai(raw_text: str):
    if not openai.api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set")
    try:
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
    except openai.RateLimitError:
        raise HTTPException(status_code=503, detail="OpenAI quota exceeded - service temporarily unavailable")
    except openai.OpenAIError as e:
        raise HTTPException(status_code=502, detail=f"OpenAI error: {str(e)}")

@app.get("/me")
async def me(request: Request, api_key: str = Security(verify_api_key)):
    """Return the current user's plan, monthly limit, and Redis usage count."""
    current_month = time.strftime("%Y-%m")
    redis_key = f"usage:{api_key}:{current_month}"
    current_count = int(redis_client.get(redis_key) or 0)
    monthly_limit = getattr(request.state, "monthly_limit", 0)
    plan = getattr(request.state, "plan", "free")
    rate_remaining = max(0, monthly_limit - current_count)
    return {
        "api_key": api_key[:8] + "...",
        "plan": plan,
        "monthly_limit": monthly_limit,
        "requests_used": current_count,
        "requests_remaining": rate_remaining,
        "billing_period": current_month,
    }

@app.get("/scrape")
async def scrape(domain: str, request: Request, response: Response, background_tasks: BackgroundTasks, api_key: str = Security(verify_api_key)):
    cache_key = f"domain:{domain}"
    cached = redis_client.get(cache_key)
    if cached:
        response.headers["X-RateLimit-Limit"] = str(getattr(request.state, "rate_limit", "N/A"))
        response.headers["X-RateLimit-Remaining"] = str(getattr(request.state, "rate_remaining", "N/A"))
        return {"source": "cache", "data": json.loads(cached)}
    try:
        raw_text, url, status = await scrape_page(domain)
        local_tech_stack = detect_tech_stack_locally(raw_text, [])
        # Early exit: skip OpenAI if page has no job content (saves cost)
        job_keywords = ["job", "career", "position", "role", "hiring", "opening", "apply", "vacancy"]
        if len(raw_text) < 500 or not any(kw in raw_text.lower() for kw in job_keywords):
            print(f"[EARLY EXIT] No job content for {domain}. Skipping OpenAI.")
            return {"source": "live", "domain": domain, "detected_tech_stack": local_tech_stack, "job_listings": [], "note": "No job content found"}
        extracted = extract_with_openai(raw_text)
        extracted["detected_tech_stack"] = local_tech_stack
        # Cache ONLY on success
        if extracted and extracted.get("company_name"):
            redis_client.setex(cache_key, 604800, json.dumps(extracted))
        else:
            print(f"[WARN] Extracted data empty or missing company_name for {domain}. Not caching.")
        # Increment usage only for live scrapes (cache hits are free)
        redis_key = getattr(request.state, "redis_key", None)
        if redis_key:
            new_count = redis_client.incr(redis_key)
            if new_count == 1:
                redis_client.expire(redis_key, 35 * 24 * 3600)
            monthly_limit = getattr(request.state, "monthly_limit", 0)
            request.state.rate_remaining = max(0, monthly_limit - new_count)

            # Email alert at 80% and 100% usage
            if monthly_limit > 0 and (new_count == int(monthly_limit * 0.8) or new_count == monthly_limit):
                with get_db_connection() as conn:
                    _ac = conn.cursor()
                    _ac.execute("SELECT email, plan FROM api_keys WHERE key = %s", (api_key,))
                    _ar = _ac.fetchone()
                    _ac.close()
                if _ar:
                    background_tasks.add_task(send_usage_alert_email, _ar[0], _ar[1], new_count, monthly_limit)
        response.headers["X-RateLimit-Limit"] = str(getattr(request.state, "rate_limit", "N/A"))
        response.headers["X-RateLimit-Remaining"] = str(getattr(request.state, "rate_remaining", "N/A"))
        return {"source": "live", "scrape_metadata": {"url": url, "status": status, "raw_chars": len(raw_text)}, "data": extracted}
    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] Failed to scrape {domain}: {str(e)}")
        return {"source": "error", "domain": domain, "error": f"Scrape failed: {str(e)}"}

@app.get("/demo/{domain}", response_class=HTMLResponse)
async def demo_endpoint(domain: str, request: Request):
    """Public interactive demo Ã¢ÂÂ no API key required. Rate-limited to 5/hour per IP."""
    client_ip = request.client.host if request.client else "unknown"
    demo_limit_key = f"demo_limit:{client_ip}"
    count = redis_client.incr(demo_limit_key)
    if count == 1:
        redis_client.expire(demo_limit_key, 3600)
    if count > 5:
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;max-width:600px;margin:80px auto;padding:20px'>"
            "<h2>Demo limit reached</h2>"
            "<p>You've used 5 free demos this hour. <a href='/'>Get a free API key</a> for full access.</p>"
            "</body></html>",
            status_code=429
        )

    # Check cache first
    cache_key = f"domain:{domain}"
    cached = redis_client.get(cache_key)
    if cached:
        data = json.loads(cached)
        source = "cache"
    else:
        try:
            raw_text, url, status = await scrape_page(domain)
            local_tech_stack = detect_tech_stack_locally(raw_text, [])
            job_keywords = ["job", "career", "position", "role", "hiring", "opening", "apply", "vacancy"]
            if len(raw_text) < 500 or not any(kw in raw_text.lower() for kw in job_keywords):
                data = {
                    "company_name": domain.split('.')[0].capitalize(),
                    "is_hiring": False,
                    "engineering_roles": [],
                    "sales_roles": [],
                    "detected_tech_stack": local_tech_stack,
                }
            else:
                data = extract_with_openai(raw_text)
                data["detected_tech_stack"] = local_tech_stack
                if data.get("company_name"):
                    redis_client.setex(cache_key, 604800, json.dumps(data))
            source = "live"
        except Exception as e:
            return HTMLResponse(
                f"<html><body style='font-family:sans-serif;max-width:600px;margin:80px auto;padding:20px'>"
                f"<h2>Error scraping {domain}</h2><p>{str(e)}</p>"
                f"<p><a href='/'>Back to home</a></p></body></html>",
                status_code=500
            )

    def fmt_list(items):
        if not items:
            return "<li style='color:#888'>None detected</li>"
        return "".join(f"<li>{item}</li>" for item in items)

    hiring_badge = (
        '<span style="background:#2ea043;color:white;padding:4px 10px;border-radius:4px">Actively Hiring</span>'
        if data.get("is_hiring") else
        '<span style="background:#6e7681;color:white;padding:4px 10px;border-radius:4px">Not Hiring / Unknown</span>'
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>StackSight Demo Ã¢ÂÂ {data.get('company_name', domain)}</title>
<style>
body {{ font-family: -apple-system, sans-serif; background: #0d1117; color: #c9d1d9; max-width: 800px; margin: 40px auto; padding: 20px; }}
h1 {{ color: #58a6ff; }} h2 {{ color: #e6edf3; border-bottom: 1px solid #30363d; padding-bottom: 8px; margin: 24px 0 12px; }}
.card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin-bottom: 16px; }}
ul {{ list-style: none; padding: 0; }}
li {{ background: #0d1117; margin: 5px 0; padding: 10px 14px; border-radius: 4px; border: 1px solid #21262d; }}
.meta {{ color: #8b949e; font-size: 0.85em; margin-bottom: 20px; }}
.cta {{ display: inline-block; background: #238636; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: bold; margin-top: 20px; }}
pre {{ background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 16px; overflow-x: auto; }}
code {{ color: #79c0ff; font-family: monospace; font-size: 0.9em; }}
</style>
</head>
<body>
<h1>StackSight Demo: {data.get('company_name', domain)}</h1>
<p class="meta">Domain: <strong>{domain}</strong> &nbsp;|&nbsp; Source: <strong>{source}</strong></p>

<div class="card">
<h2>Hiring Status</h2>
{hiring_badge}
</div>

<div class="card">
<h2>Engineering Roles</h2>
<ul>{fmt_list(data.get('engineering_roles', []))}</ul>
</div>

<div class="card">
<h2>Sales Roles</h2>
<ul>{fmt_list(data.get('sales_roles', []))}</ul>
</div>

<div class="card">
<h2>Detected Tech Stack</h2>
<ul>{fmt_list(data.get('detected_tech_stack', []))}</ul>
</div>

<div class="card">
<h2>Raw JSON</h2>
<pre><code>{json.dumps(data, indent=2)}</code></pre>
</div>

<a href="/" class="cta">Get Your Free API Key Ã¢ÂÂ 50 Requests/Month</a>
</body>
</html>"""
    return HTMLResponse(content=html)

@app.get("/scrape/raw")
async def scrape_raw(domain: str):
    raw_text, url, status = await scrape_page(domain)
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    return {"url": url, "status": status, "total_lines": len(lines), "preview": lines[:50]}

@app.get("/health")
async def health():
    try: redis_client.ping(); redis_ok = True
    except: redis_ok = False
    db_ok = False
    if DATABASE_URL:
        try: conn = get_db(); conn.close(); db_ok = True
        except: pass
    return {"status": "ok", "version": "8.12.0", "openai_key_set": bool(openai.api_key), "redis_connected": redis_ok, "db_connected": db_ok}

@app.get("/admin/stats")
async def admin_stats(admin_password: str = Query(None)):
    """Password-protected admin analytics dashboard."""
    if admin_password != os.getenv("ADMIN_PASSWORD"):
        raise HTTPException(status_code=403, detail="Forbidden")

    stats = {}

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT plan, COUNT(*) FROM api_keys GROUP BY plan")
            plan_counts = dict(cur.fetchall())
            stats["users_by_plan"] = plan_counts
            stats["total_users"] = sum(plan_counts.values())

            mrr = 0
            for plan, count in plan_counts.items():
                if plan == "pro": mrr += count * 49
                elif plan == "business": mrr += count * 199
            stats["estimated_mrr"] = mrr

            try:
                cur.execute("SELECT email, plan, created_at FROM api_keys ORDER BY created_at DESC LIMIT 5")
                recent = cur.fetchall()
                stats["recent_signups"] = [{"email": r[0], "plan": r[1], "created_at": str(r[2])} for r in recent]
            except Exception:
                stats["recent_signups"] = []

    try:
        redis_info = redis_client.info()
        stats["redis"] = {
            "total_commands_processed": redis_info.get("total_commands_processed", 0),
            "keyspace_hits": redis_info.get("keyspace_hits", 0),
            "keyspace_misses": redis_info.get("keyspace_misses", 0),
        }
    except Exception:
        stats["redis"] = "unavailable"

    current_month = time.strftime("%Y-%m")
    usage_keys = redis_client.keys(f"usage:*:{current_month}")
    total_usage = sum(int(redis_client.get(k) or 0) for k in usage_keys)
    stats["total_api_calls_this_month"] = total_usage

    return stats


class BulkDomainPayload(BaseModel):
    domains: List[str]


async def background_scrape(domain: str):
    """Runs the full scrape pipeline for a domain and caches the result."""
    print(f"[BACKGROUND] Starting scrape for {domain}")
    cache_key = f"domain:{domain}"
    try:
        raw_text, url, status = await scrape_page(domain)
        local_tech_stack = detect_tech_stack_locally(raw_text, [])
        job_keywords = ["job", "career", "position", "role", "hiring", "opening", "apply", "vacancy"]
        if len(raw_text) < 500 or not any(kw in raw_text.lower() for kw in job_keywords):
            data = {
                "company_name": domain.split(".")[0].capitalize(),
                "is_hiring": False,
                "engineering_roles": [],
                "sales_roles": [],
                "detected_tech_stack": local_tech_stack,
            }
        else:
            data = extract_with_openai(raw_text)
            data["detected_tech_stack"] = local_tech_stack
        if data.get("company_name"):
            redis_client.setex(cache_key, 604800, json.dumps(data))
            print(f"[BACKGROUND] Cached result for {domain}")
    except Exception as e:
        print(f"[BACKGROUND ERROR] {domain}: {e}")


@app.post("/scrape/bulk")
async def bulk_scrape(
    payload: BulkDomainPayload,
    background_tasks: BackgroundTasks,
    request: Request,
    api_key: str = Security(verify_api_key),
):
    """Bulk enrichment: returns cached data instantly, queues uncached domains in background."""
    plan = getattr(request.state, "plan", "free")
    max_domains = 5 if plan == "free" else 50
    if len(payload.domains) > max_domains:
        raise HTTPException(
            status_code=403,
            detail=f"{'Free' if plan == 'free' else 'Pro'} tier limited to {max_domains} domains per bulk request."
        )

    results = []
    for domain in payload.domains:
        domain = domain.strip().lower().rstrip("/")
        domain_key = domain.replace("https://", "").replace("http://", "").split("/")[0]
        cache_key = f"domain:{domain_key}"
        cached = redis_client.get(cache_key)
        if cached:
            results.append({"domain": domain_key, "status": "success", "source": "cache", "data": json.loads(cached)})
        else:
            background_tasks.add_task(background_scrape, domain_key)
            results.append({
                "domain": domain_key,
                "status": "processing",
                "source": "background",
                "data": None,
                "message": f"Scraping in background. Poll GET /scrape?domain={domain_key} in 30-60s."
            })

    return {
        "results": results,
        "total": len(results),
        "cached": sum(1 for r in results if r["source"] == "cache"),
        "queued": sum(1 for r in results if r["source"] == "background"),
    }



TOP_SAAS_DOMAINS = [
    "salesforce.com", "hubspot.com", "zendesk.com", "intercom.com", "slack.com",
    "notion.so", "airtable.com", "asana.com", "monday.com", "linear.app",
    "stripe.com", "brex.com", "ramp.com", "rippling.com", "gusto.com",
    "workday.com", "greenhouse.io", "lever.co", "ashbyhq.com", "lattice.com",
    "figma.com", "miro.com", "loom.com", "zoom.us", "webex.com",
    "datadog.com", "newrelic.com", "pagerduty.com", "splunk.com", "elastic.co",
    "snowflake.com", "databricks.com", "dbt.com", "fivetran.com", "segment.com",
    "twilio.com", "sendgrid.com", "mailchimp.com", "klaviyo.com", "braze.com",
    "amplitude.com", "mixpanel.com", "heap.io", "fullstory.com", "hotjar.com",
    "cloudflare.com", "fastly.com", "vercel.com", "netlify.com", "heroku.com",
    "mongodb.com", "redis.com", "planetscale.com", "supabase.com", "neon.tech",
    "openai.com", "anthropic.com", "cohere.com", "deepmind.com", "scale.com",
    "confluent.com", "mulesoft.com", "apigee.com", "postman.com", "stoplight.io",
    "okta.com", "auth0.com", "ping.com", "crowdstrike.com", "sentinelone.com",
    "servicenow.com", "freshworks.com", "zoho.com", "pipedrive.com", "close.com",
    "gong.io", "chorus.ai", "outreach.io", "salesloft.com", "apollo.io",
    "zoominfo.com", "clearbit.com", "lusha.com", "hunter.io", "snov.io",
    "bill.com", "expensify.com", "netsuite.com", "sage.com", "quickbooks.com",
    "shopify.com", "bigcommerce.com", "woocommerce.com", "magento.com", "squarespace.com",
]


@app.post("/cron/warm-cache")
async def warm_cache(
    background_tasks: BackgroundTasks,
    secret: str = Query(None),
):
    """Secret-protected endpoint to pre-scrape top SaaS domains and warm Redis cache."""
    cron_secret = os.getenv("CRON_SECRET")
    if not cron_secret or secret != cron_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    queued_count = 0
    already_cached = 0
    for domain in TOP_SAAS_DOMAINS:
        cache_key = f"domain:{domain}"
        if redis_client.get(cache_key):
            already_cached += 1
        else:
            background_tasks.add_task(background_scrape, domain)
            queued_count += 1

    return {
        "status": "success",
        "message": f"Queued {queued_count} domains for cache warming.",
        "total_domains_checked": len(TOP_SAAS_DOMAINS),
        "already_cached": already_cached,
        "queued_for_scrape": queued_count,
    }



class WebhookPayload(BaseModel):
    domain: str
    webhook_url: str


@app.post("/webhooks/subscribe")
async def subscribe_webhook(payload: WebhookPayload, request: Request, api_key: str = Security(verify_api_key)):
    plan = getattr(request.state, "plan", "free")
    if plan == "free":
        raise HTTPException(status_code=403, detail="Webhooks are a Pro/Business tier feature.")
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO webhook_subscriptions (api_key, domain, webhook_url)
            VALUES (%s, %s, %s)
            ON CONFLICT (api_key, domain)
            DO UPDATE SET webhook_url = EXCLUDED.webhook_url
        """, (api_key, payload.domain, payload.webhook_url))
        conn.commit()
        cur.close()
    return {"status": "success", "message": f"Subscribed to changes for {payload.domain}"}


@app.get("/webhooks/subscriptions")
async def get_subscriptions(api_key: str = Security(verify_api_key)):
    with get_db_connection() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT domain, webhook_url, last_known_status FROM webhook_subscriptions WHERE api_key = %s", (api_key,))
        results = cur.fetchall()
        cur.close()
    return {"subscriptions": [{"domain": r["domain"], "webhook_url": r["webhook_url"], "last_known_status": r["last_known_status"]} for r in results]}


@app.post("/cron/check-webhooks")
async def check_webhooks(background_tasks: BackgroundTasks, secret: str = Query(None)):
    if secret != os.getenv("CRON_SECRET", ""):
        raise HTTPException(status_code=403, detail="Forbidden")
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id, api_key, domain, webhook_url, last_known_status FROM webhook_subscriptions")
        subscriptions = cur.fetchall()
        cur.close()
    queued_count = 0
    for sub in subscriptions:
        sub_id, api_key, domain, webhook_url, last_known_status = sub
        cache_key = f"domain:{domain}"
        cached_data = redis_client.get(cache_key)
        if cached_data:
            data = json.loads(cached_data)
            current_status = data.get("is_hiring", False)
            if current_status != last_known_status:
                background_tasks.add_task(
                    dispatch_webhook,
                    sub_id, api_key, domain, webhook_url, current_status, data
                )
                queued_count += 1
    return {"status": "success", "total_subscriptions": len(subscriptions), "webhooks_queued": queued_count}


async def dispatch_webhook(sub_id: int, api_key: str, domain: str, webhook_url: str, new_status: bool, data: dict):
    payload = {
        "event": "hiring_status_changed",
        "domain": domain,
        "is_hiring": new_status,
        "timestamp": datetime.now().isoformat(),
        "data": data,
    }
    headers = {"Content-Type": "application/json", "X-StackSight-Event": "hiring_status_changed"}
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(webhook_url, json=payload, headers=headers, timeout=10.0)
            print(f"[WEBHOOK] Sent to {webhook_url} for {domain}. Status: {res.status_code}")
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE webhook_subscriptions SET last_known_status = %s WHERE id = %s", (new_status, sub_id))
            conn.commit()
            cur.close()
    except Exception as e:
        print(f"[WEBHOOK ERROR] Failed to send to {webhook_url}: {e}")


@app.get("/trending", response_class=HTMLResponse)
async def trending_companies():
    keys = redis_client.keys("domain:*")
    companies = []
    for key in keys:
        try:
            cached_data = redis_client.get(key)
            if cached_data:
                data = json.loads(cached_data)
                domain = key.split(":", 1)[1] if isinstance(key, str) else key.decode().split(":", 1)[1]
                job_count = len(data.get("sample_job_titles", []))
                dept_count = len(data.get("departments", []))
                companies.append({
                    "domain": domain,
                    "name": data.get("company_name", domain.capitalize()),
                    "is_hiring": data.get("is_hiring", False),
                    "jobs": data.get("sample_job_titles", [])[:3],
                    "departments": data.get("departments", []),
                    "tech": data.get("detected_tech_stack", [])[:4],
                    "score": job_count + dept_count,
                })
        except Exception:
            continue
    companies.sort(key=lambda x: x["score"], reverse=True)
    companies = companies[:20]
    rows = ""
    for c in companies:
        badge = '<span style="color:#2ea043;font-weight:bold">● Hiring</span>' if c["is_hiring"] else '<span style="color:#888">○ No Roles</span>'
        jobs = ", ".join(c["jobs"]) if c["jobs"] else "N/A"
        tech = ", ".join(c["tech"]) if c["tech"] else "N/A"
        rows += f'<tr><td><a href="/demo/{c["domain"]}" style="color:#58a6ff">{c["name"]}</a></td><td>{badge}</td><td style="color:#ccc">{jobs}</td><td style="color:#aaa">{tech}</td></tr>'
    count = len(companies)
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="description" content="Live list of companies actively hiring engineers right now. Updated automatically via StackSight API.">
<title>Trending Hiring Companies | StackSight API</title>
<style>
body{{background:#0d1117;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;padding:2rem}}
h1{{color:#f0f6fc;font-size:2rem;margin-bottom:.5rem}}
p.sub{{color:#8b949e;margin-bottom:2rem}}
table{{width:100%;border-collapse:collapse;background:#161b22;border-radius:8px;overflow:hidden}}
th{{background:#21262d;color:#8b949e;padding:.75rem 1rem;text-align:left;font-size:.85rem;text-transform:uppercase;letter-spacing:.05em}}
td{{padding:.75rem 1rem;border-bottom:1px solid #21262d;font-size:.9rem}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:#1c2128}}
.cta{{margin-top:2rem;padding:1rem 1.5rem;background:#1f6feb22;border:1px solid #1f6feb;border-radius:8px;display:inline-block}}
.cta a{{color:#58a6ff;text-decoration:none;font-weight:600}}
</style>
</head>
<body>
<h1>🔥 Companies Actively Hiring Right Now</h1>
<p class="sub">Live data from {count} companies in our index. Updated automatically. Powered by <a href="/" style="color:#58a6ff">StackSight API</a>.</p>
<table>
<thead><tr><th>Company</th><th>Status</th><th>Open Roles (sample)</th><th>Tech Stack</th></tr></thead>
<tbody>{rows}</tbody>
</table>
<div class="cta"><a href="/">Get API Access → Scrape any company's hiring data in 1 line of code</a></div>
</body></html>"""
    return HTMLResponse(content=html)


class EmailPayload(BaseModel):
    email: str


@app.post("/newsletter/subscribe")
async def newsletter_subscribe(payload: EmailPayload):
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO newsletter_subs (email) VALUES (%s)
            ON CONFLICT (email) DO NOTHING
        """, (payload.email,))
        conn.commit()
        cur.close()
    return {"status": "success", "message": "Subscribed! You'll get weekly hiring updates."}


@app.post("/cron/send-newsletter")
async def send_newsletter_cron(secret: str = Query(None)):
    if secret != os.getenv("CRON_SECRET", ""):
        raise HTTPException(status_code=403, detail="Forbidden")
    keys = redis_client.keys("domain:*")
    companies = []
    for key in keys:
        try:
            cached_data = redis_client.get(key)
            if cached_data:
                data = json.loads(cached_data)
                domain = key.split(":", 1)[1] if isinstance(key, str) else key.decode().split(":", 1)[1]
                score = len(data.get("sample_job_titles", [])) + len(data.get("departments", []))
                companies.append({"name": data.get("company_name", domain), "domain": domain, "jobs": data.get("sample_job_titles", [])[:3], "score": score})
        except Exception:
            continue
    companies.sort(key=lambda x: x["score"], reverse=True)
    top_5 = companies[:5]
    rows = ""
    for c in top_5:
        jobs = ", ".join(c["jobs"]) if c["jobs"] else "N/A"
        rows += f'<li><strong>{c["name"]}</strong> is hiring: {jobs}. <a href="https://careers-scraper-production.up.railway.app/demo/{c["domain"]}">View full data →</a></li>'
    html_body = f"""<html><body style="font-family:sans-serif;background:#0d1117;color:#e6edf3;padding:2rem">
<h2 style="color:#f0f6fc">🔥 Weekly Hiring Update from StackSight</h2>
<p>Here are the top companies actively hiring this week:</p>
<ul style="line-height:2">{rows}</ul>
<p>Want to scrape any company in 1 line? <a href="https://careers-scraper-production.up.railway.app" style="color:#58a6ff">Get API access →</a></p>
<p style="color:#666;font-size:.8rem">Unsubscribe: reply to this email with "unsubscribe"</p>
</body></html>"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT email FROM newsletter_subs")
        subs = cur.fetchall()
        cur.close()
    sent_count = 0
    for sub in subs:
        email = sub[0]
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = "Weekly Update: Companies Hiring Right Now"
            msg['From'] = os.getenv("FROM_EMAIL", SMTP_USERNAME)
            msg['To'] = email
            msg.attach(MIMEText(html_body, 'html'))
            with smtplib.SMTP(os.getenv("SMTP_SERVER", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", 587))) as server:
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(os.getenv("FROM_EMAIL", SMTP_USERNAME), email, msg.as_string())
            sent_count += 1
        except Exception as e:
            print(f"[NEWSLETTER ERROR] Failed to send to {email}: {e}")
    return {"status": "success", "emails_sent": sent_count}




@app.get("/badge/{domain}.svg", response_class=Response)
async def hiring_badge(domain: str):
    cache_key = "domain:" + domain
    cached_data = redis_client.get(cache_key)
    is_hiring = False
    if cached_data:
        data = json.loads(cached_data)
        is_hiring = data.get("is_hiring", False)
    label = "Hiring" if is_hiring else "Not Hiring"
    color = "#28a745" if is_hiring else "#dc3545"
    text_len = "370" if is_hiring else "600"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="20" role="img">'
        '<linearGradient id="s" x2="0" y2="100%">'
        '<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
        '<stop offset="1" stop-opacity=".1"/>'
        '</linearGradient>'
        '<clipPath id="r"><rect width="120" height="20" rx="3" fill="#fff"/></clipPath>'
        '<g clip-path="url(#r)">'
        '<rect width="65" height="20" fill="#555"/>'
        '<rect x="65" width="55" height="20" fill="' + color + '"/>'
        '<rect width="120" height="20" fill="url(#s)"/>'
        '</g>'
        '<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,sans-serif" font-size="110">'
        '<text x="325" y="140" transform="scale(.1)" textLength="550">StackSight</text>'
        '<text x="925" y="140" transform="scale(.1)" textLength="' + text_len + '">' + label + '</text>'
        '</g>'
        '</svg>'
    )
    return Response(content=svg, media_type="image/svg+xml", headers={"Cache-Control": "no-cache, max-age=0"})

@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    return """User-agent: *
Allow: /
Disallow: /scrape
Disallow: /me
Disallow: /admin
Sitemap: https://careers-scraper-production.up.railway.app/sitemap.xml
"""


@app.get("/sitemap.xml")
async def sitemap():
    top_domains = [
        "stripe.com", "notion.so", "airbnb.com", "uber.com", "shopify.com",
        "figma.com", "github.com", "gitlab.com", "twilio.com", "sendgrid.com",
        "cloudflare.com", "vercel.com", "netlify.com", "supabase.com", "planetscale.com",
        "linear.app", "slack.com", "zoom.us", "asana.com", "monday.com",
        "hubspot.com", "salesforce.com", "zendesk.com", "intercom.com", "mixpanel.com",
        "datadog.com", "newrelic.com", "pagerduty.com", "amplitude.com", "segment.com",
    ]
    base_url = "https://careers-scraper-production.up.railway.app"
    today = datetime.now().strftime("%Y-%m-%d")
    urls = [f"<url><loc>{base_url}/</loc><lastmod>{today}</lastmod><priority>1.0</priority></url>"]
    for domain in top_domains:
        urls.append(f'<url><loc>{base_url}/demo/{domain}</loc><lastmod>{today}</lastmod><priority>0.8</priority></url>')
    urls_str = "".join(urls)
    xml_content = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + urls_str + "\n</urlset>"
    return PlainTextResponse(content=xml_content, media_type="application/xml")


if __name__ == "__main__":
    uvicorn.run("scraper:app", host="0.0.0.0", port=8000, reload=True)
