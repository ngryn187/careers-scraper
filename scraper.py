import asyncio
import json
import os
import secrets

import openai
import psycopg2
import psycopg2.extras
import redis as redis_lib
import stripe
from fastapi import FastAPI, HTTPException, Security, Header, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn
from playwright.async_api import async_playwright

app = FastAPI(title="Careers Scraper API", version="7.0.0")
openai.api_key = os.environ.get("OPENAI_API_KEY", "")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
redis_client = redis_lib.from_url(REDIS_URL, decode_responses=True)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")
BASE_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "careers-scraper-production.up.railway.app")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

EXTRACTION_PROMPT = (
    "You are a B2B data extraction engine. Given raw text from a company careers page, "
    "extract structured data. Return ONLY valid JSON. Schema: "
    "{company_name: string, is_hiring: boolean, engineering_roles: [string], "
    "sales_roles: [string], detected_tech_stack: [string]}"
)

LANDING_HTML = """<!DOCTYPE html>
<html>
<head>
<title>HireSignal API - B2B Hiring Intent Data</title>
<style>
body{font-family:system-ui,sans-serif;max-width:700px;margin:60px auto;padding:0 20px;background:#0f0f0f;color:#e0e0e0}
h1{font-size:2rem;margin-bottom:8px}
p{color:#999;margin-bottom:32px}
.card{background:#1a1a1a;border:1px solid #333;border-radius:12px;padding:28px;margin-bottom:20px}
.badge{display:inline-block;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600;margin-bottom:12px}
.free-badge{background:#1a3a1a;color:#4caf50;border:1px solid #4caf50}
.pro-badge{background:#1a1a3a;color:#7c83fc;border:1px solid #7c83fc}
h2{margin:0 0 8px;font-size:1.2rem}
.sub{color:#777;font-size:14px;margin-bottom:20px}
input{width:100%;padding:10px 14px;background:#111;border:1px solid #444;border-radius:8px;color:#fff;font-size:15px;box-sizing:border-box;margin-bottom:12px}
button{padding:10px 20px;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;width:100%}
.btn-free{background:#4caf50;color:#000}
.btn-pro{background:#7c83fc;color:#000}
.result{margin-top:16px;padding:14px;background:#111;border-radius:8px;border:1px solid #333;font-family:monospace;font-size:13px;word-break:break-all;display:none}
</style>
</head>
<body>
<h1>HireSignal API</h1>
<p>Real-time B2B hiring intent data.</p>
<div class="card">
  <span class="badge free-badge">FREE</span>
  <h2>Free Tier</h2>
  <p class="sub">50 requests/month - no credit card required</p>
  <input type="email" id="email" placeholder="your@email.com" />
  <button class="btn-free" onclick="getFreeKey()">Get Free API Key</button>
  <div class="result" id="free-result"></div>
</div>
<div class="card">
  <span class="badge pro-badge">PRO</span>
  <h2>Pro Tier - $49/month</h2>
  <p class="sub">2,500 requests/month</p>
  <button class="btn-pro" onclick="goProCheckout()">Upgrade to Pro ($49/mo)</button>
</div>
<div class="card" style="border-color:#222">
  <h2 style="margin-bottom:8px">Quick Start</h2>
  <p style="color:#777;font-size:13px;margin-bottom:12px">After getting your key:</p>
  <div style="position:relative;background:#111;border:1px solid #333;border-radius:8px;padding:14px">
    <code id="curl-box" style="font-size:12px;color:#c0c0c0;display:block;white-space:pre-wrap">curl "https://careers-scraper-production.up.railway.app/scrape?domain=stripe.com" -H "x-api-key: YOUR_KEY"</code>
    <button onclick="copyCurl()" style="position:absolute;top:8px;right:8px;padding:3px 10px;background:#333;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:11px;width:auto">Copy</button>
  </div>
  <p style="margin-top:12px;font-size:13px"><a href="/docs" target="_blank" style="color:#7c83fc">API docs (Swagger)</a></p>
</div>
<script>
async function getFreeKey() {
  const email = document.getElementById('email').value;
  if (!email) { alert('Enter your email'); return; }
  const res = await fetch('/generate-free-key', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email})});
  const data = await res.json();
  const el = document.getElementById('free-result');
  el.style.display = 'block';
  if (data.api_key) {
    el.innerHTML = '<b>Your API Key:</b><br>' + data.api_key;
    window._lastKey = data.api_key;
    var cb = document.getElementById('curl-box');
    if (cb) cb.textContent = 'curl "https://careers-scraper-production.up.railway.app/scrape?domain=stripe.com" -H "x-api-key: ' + data.api_key + '"';
  } else { el.innerHTML = 'Error: ' + (data.detail || JSON.stringify(data)); }
}
function copyCurl() {
  var k = window._lastKey || 'YOUR_KEY';
  navigator.clipboard.writeText('curl "https://careers-scraper-production.up.railway.app/scrape?domain=stripe.com" -H "x-api-key: ' + k + '"').then(function(){ alert('Copied!'); });
}
async function goProCheckout() {
  const res = await fetch('/create-checkout-session', {method:'POST'});
  const data = await res.json();
  if (data.url) { window.location.href = data.url; }
  else { alert('Error: ' + (data.detail || JSON.stringify(data))); }
}
</script>
</body>
</html>"""

class FreeKeyRequest(BaseModel):
    email: str

def get_db():
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
    html = f"""<!DOCTYPE html><html><head><title>HireSignal - Success</title>
<style>body{{font-family:system-ui;max-width:600px;margin:80px auto;padding:0 20px;background:#0f0f0f;color:#e0e0e0}}
.card{{background:#1a1a1a;border:1px solid #333;border-radius:12px;padding:32px}}
h1{{color:#4caf50}}code{{background:#111;padding:12px;border-radius:8px;display:block;word-break:break-all;margin-top:12px}}</style></head>
<body><div class="card"><h1>Payment Successful!</h1>
{"<p>Your API key:</p><code>" + api_key + "</code>" if api_key else "<p>Key being activated...</p>"}
<p style="margin-top:24px"><a href="/docs" style="color:#7c83fc">API docs</a> &nbsp; <a href="/" style="color:#7c83fc">Home</a></p>
</div></body></html>"""
    return HTMLResponse(content=html)

async def verify_api_key(x_api_key: str = Header(None)):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    if not DATABASE_URL:
        valid_key = os.environ.get("VALID_API_KEY", "")
        if valid_key and x_api_key != valid_key:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return x_api_key
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM api_keys WHERE key = %s", (x_api_key,))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            raise HTTPException(status_code=401, detail="Invalid API key")
        if row["usage_count"] >= row["monthly_limit"]:
            cur.close(); conn.close()
            raise HTTPException(status_code=429, detail="Monthly limit exceeded")
        cur.execute("UPDATE api_keys SET usage_count = usage_count + 1 WHERE key = %s", (x_api_key,))
        conn.commit()
        cur.close(); conn.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Auth error: {e}")
    return x_api_key

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
        event = json.loads(payload)
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
                conn.commit()
                cur.close(); conn.close()
            except Exception as e:
                print(f"Webhook error: {e}")
    return {"status": "ok"}

async def scrape_page(domain: str):
    domain = domain.strip().lower().rstrip("/")
    if not domain.startswith("http"):
        domain = "https://" + domain
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_extra_http_headers({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        for suffix in ["/careers", "/jobs"]:
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

@app.get("/scrape")
async def scrape(domain: str, api_key: str = Security(verify_api_key)):
    cache_key = f"domain:{domain}"
    cached = redis_client.get(cache_key)
    if cached:
        return {"source": "cache", "data": json.loads(cached)}
    try:
        raw_text, url, status = await scrape_page(domain)
        extracted = extract_with_openai(raw_text)
        redis_client.setex(cache_key, 604800, json.dumps(extracted))
        return {"source": "live", "scrape_metadata": {"url": url, "status": status, "raw_chars": len(raw_text)}, "data": extracted}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scrape error: {str(e)}")

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
    return {"status": "ok", "version": "7.0.0", "openai_key_set": bool(openai.api_key), "redis_connected": redis_ok, "db_connected": db_ok}

if __name__ == "__main__":
    uvicorn.run("scraper:app", host="0.0.0.0", port=8000, reload=True)
