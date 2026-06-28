import asyncio
import json
import os
import secrets

import openai
import psycopg2
import psycopg2.extras
import redis as redis_lib
import stripe
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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
        <pre style="background:#161b22;padding:15px;border-radius:6px;color:#79c0ff">curl -X GET "https://careers-scraper-production.up.railway.app/scrape?domain=stripe.com" \
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
    <title>StackSight API - Hiring Intent & Tech Stack Data</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0d1117; color: #c9d1d9; min-height: 100vh; }
        .container { max-width: 860px; margin: 0 auto; padding: 60px 20px; }
        .badge { display: inline-block; background: #161b22; border: 1px solid #30363d; color: #58a6ff; font-size: 0.75em; padding: 4px 12px; border-radius: 20px; margin-bottom: 20px; }
        h1 { color: #58a6ff; font-size: 2.8em; margin-bottom: 12px; line-height: 1.2; }
        .subtitle { font-size: 1.2em; color: #8b949e; margin-bottom: 50px; max-width: 600px; }
        .card { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 30px; margin-bottom: 24px; }
        .card h3 { color: #e6edf3; font-size: 1.1em; margin-bottom: 12px; }
        .card p { color: #8b949e; font-size: 0.95em; margin-bottom: 16px; }
        input[type="email"] { width: 100%; padding: 12px 16px; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; color: #e6edf3; font-size: 1em; margin-bottom: 12px; outline: none; }
        input[type="email"]:focus { border-color: #58a6ff; }
        button { background: #238636; color: white; border: none; padding: 12px 24px; border-radius: 6px; font-size: 1em; cursor: pointer; width: 100%; font-weight: 600; }
        button:hover { background: #2ea043; }
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
        .price-box ul li::before { content: "\u2713 "; color: #2ea043; }
        .price-box button { margin-top: 16px; background: #238636; }
        .price-box.featured button { background: #1f6feb; }
        .price-box.featured button:hover { background: #388bfd; }
        .section-title { font-size: 1.5em; color: #e6edf3; margin: 50px 0 20px; }
        .endpoint-row { display: flex; align-items: center; gap: 12px; background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 14px 18px; margin-bottom: 10px; }
        .method { background: #0d7a3c; color: white; font-size: 0.8em; font-weight: 700; padding: 3px 10px; border-radius: 4px; font-family: monospace; }
        .path { color: #79c0ff; font-family: monospace; }
        .desc { color: #8b949e; font-size: 0.9em; margin-left: auto; }
        footer { border-top: 1px solid #30363d; margin-top: 60px; padding-top: 30px; color: #8b949e; font-size: 0.9em; text-align: center; }
        footer a { color: #58a6ff; text-decoration: none; }
        @media (max-width: 600px) { .pricing { grid-template-columns: 1fr; } h1 { font-size: 2em; } }
    </style>
</head>
<body>
<div class="container">
    <div class="badge">&#x1F680; Now on RapidAPI</div>
    <h1>StackSight API</h1>
    <p class="subtitle">Real-time B2B hiring intent and tech stack detection for any company domain. Know which companies are growing, hiring engineers, and which tools they use &mdash; before your competitors do.</p>

    <div class="card">
        <h3>&#x26A1; Quick Start &mdash; Free Tier</h3>
        <p>Get 50 free API requests instantly. No credit card required.</p>
        <input type="email" id="email" placeholder="you@company.com">
        <button onclick="generateKey()">Get My Free API Key</button>
        <div class="key-display" id="keyResult"></div>
    </div>

    <h2 class="section-title">Try It Now</h2>
    <div class="card">
        <h3>Example Request</h3>
        <pre><code id="curlExample">curl -X GET "https://careers-scraper-production.up.railway.app/scrape?domain=stripe.com" \
     -H "X-API-Key: YOUR_API_KEY"</code></pre>
        <h3 style="margin-top:20px;">Example Response</h3>
        <pre><code>{
  "company_name": "Stripe",
  "is_hiring": true,
  "engineering_roles": ["Backend Engineer", "ML Engineer", "Platform Engineer"],
  "sales_roles": ["Account Executive", "Solutions Engineer"],
  "detected_tech_stack": ["Go", "Ruby", "AWS", "Kubernetes", "Kafka"]
}</code></pre>
    </div>

    <h2 class="section-title">Endpoints</h2>
    <div class="endpoint-row">
        <span class="method">GET</span>
        <span class="path">/scrape?domain={domain}</span>
        <span class="desc">Scrape hiring intent + tech stack for a domain</span>
    </div>
    <div class="endpoint-row">
        <span class="method">GET</span>
        <span class="path">/docs</span>
        <span class="desc">Interactive Swagger documentation</span>
    </div>
    <div class="endpoint-row">
        <span class="method">GET</span>
        <span class="path">/health</span>
        <span class="desc">API health check</span>
    </div>

    <h2 class="section-title">Pricing</h2>
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
            <button onclick="document.getElementById('email').focus()">Get Started</button>
        </div>
        <div class="price-box featured">
            <h3>Pro</h3>
            <div class="price">$49<span style="font-size:0.4em;color:#8b949e">/mo</span></div>
            <ul>
                <li>2,500 requests/month</li>
                <li>10 requests/second</li>
                <li>Redis-cached responses</li>
                <li>Priority support</li>
            </ul>
            <button onclick="window.open('https://rapidapi.com/search/stacksight','_blank')">Subscribe on RapidAPI</button>
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
            <button onclick="window.open('https://rapidapi.com/search/stacksight','_blank')">Contact Sales</button>
        </div>
    </div>

    <footer>
        <p>StackSight API &mdash; <a href="/docs">Documentation</a> &middot; <a href="https://rapidapi.com" target="_blank">RapidAPI</a> &middot; Built with FastAPI</p>
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
            box.innerHTML = '<strong style="color:#2ea043">\u2713 Your API Key:</strong><br>' + data.api_key + '<br><br><small style="color:#8b949e">Use header: X-API-Key: ' + data.api_key + '</small>';
            document.getElementById('curlExample').textContent = 'curl -X GET "https://careers-scraper-production.up.railway.app/scrape?domain=stripe.com" \\\n     -H "X-API-Key: ' + data.api_key + '"';
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
    html = f"""<!DOCTYPE html><html><head><title>HireSignal - Success</title>
<style>body{{font-family:system-ui;max-width:600px;margin:80px auto;padding:0 20px;background:#0f0f0f;color:#e0e0e0}}
.card{{background:#1a1a1a;border:1px solid #333;border-radius:12px;padding:32px}}
h1{{color:#4caf50}}code{{background:#111;padding:12px;border-radius:8px;display:block;word-break:break-all;margin-top:12px}}</style></head>
<body><div class="card"><h1>Payment Successful!</h1>
{"<p>Your API key:</p><code>" + api_key + "</code>" if api_key else "<p>Key being activated...</p>"}
<p style="margin-top:24px"><a href="/docs" style="color:#7c83fc">API docs</a> &nbsp; <a href="/" style="color:#7c83fc">Home</a></p>
</div></body></html>"""
    return HTMLResponse(content=html)

async def verify_api_key(
    x_api_key: str = Header(None),
    x_rapidapi_proxy_secret: str = Header(None, alias="X-RapidAPI-Proxy-Secret"),
):
    # Allow RapidAPI proxy requests through with their secret
    rapidapi_secret = os.environ.get("RAPIDAPI_PROXY_SECRET", "")
    if rapidapi_secret and x_rapidapi_proxy_secret == rapidapi_secret:
        return "rapidapi_user"
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    if not DATABASE_URL:
        valid_key = os.environ.get("VALID_API_KEY", "")
        if x_api_key != valid_key:
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
        monthly_limit = row["monthly_limit"]
        plan = row["plan"]
        cur.close(); conn.close()

        # Redis atomic rate limiting — prevents concurrent request abuse
        import time
        current_month = time.strftime("%Y-%m")
        redis_key = f"usage:{x_api_key}:{current_month}"
        current_count = redis_client.incr(redis_key)
        if current_count == 1:
            # Set TTL to ~35 days on first use so key auto-expires after the month
            redis_client.expire(redis_key, 35 * 24 * 3600)
        if current_count > monthly_limit:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. {plan.capitalize()} tier limit: {monthly_limit} requests/month"
            )
        return x_api_key
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Auth error: {str(e)}")


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
                    send_api_key_email(email, new_key, "Pro")
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
        # Dynamic careers URL discovery via homepage link following
        careers_url = await find_careers_url(page, domain)
        try:
            resp = await page.goto(careers_url, wait_until="domcontentloaded", timeout=15000)
            if resp and resp.status < 400:
                await asyncio.sleep(2)
                text = await page.inner_text("body")
                # Extract script tags for better tech stack detection
                scripts = await page.evaluate("""() => Array.from(document.querySelectorAll('script[src]')).map(s => s.src).filter(Boolean)""")
                if scripts:
                    text += "\n\nDETECTED SCRIPTS:\n" + "\n".join(scripts[:50])
                await browser.close()
                return text, careers_url, resp.status
        except Exception:
            pass
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
