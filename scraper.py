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
import uvicorn
from playwright.async_api import async_playwright

app = FastAPI(title="Careers Scraper", version="4.0.0")
openai.api_key = os.environ.get("OPENAI_API_KEY", "")

# Redis setup
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
redis_client = redis_lib.from_url(REDIS_URL, decode_responses=True)

# Stripe setup
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

# Database
DATABASE_URL = os.environ.get("DATABASE_URL", "")

EXTRACTION_PROMPT = (
    "You are a B2B data extraction engine. Given raw text from a company careers page, "
    "extract structured data. Return ONLY valid JSON. If a field cannot be determined, "
    "return an empty array or false. Look for: job titles, technology keywords "
    "(React, AWS, Kubernetes, etc.), and company name. Schema: "
    "{company_name: string, is_hiring: boolean, engineering_roles: [string], "
    "sales_roles: [string], detected_tech_stack: [string]}"
)


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
        print("DB initialized")
    except Exception as e:
        print(f"DB init error: {e}")


@app.on_event("startup")
async def startup():
    init_db()


async def verify_api_key(x_api_key: str = Header(None)):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    if not DATABASE_URL:
        # Dev fallback: static env var
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


async def scrape_page(domain: str):
    domain = domain.strip().lower().rstrip("/")
    if not domain.startswith("http"):
        domain = "https://" + domain
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
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


@app.get("/scrape")
async def scrape(domain: str, api_key: str = Security(verify_api_key)):
    cache_key = f"domain:{domain}"
    cached = redis_client.get(cache_key)
    if cached:
        return {"source": "cache", "data": json.loads(cached)}
    raw_text, url, status = await scrape_page(domain)
    extracted = extract_with_openai(raw_text)
    redis_client.setex(cache_key, 604800, json.dumps(extracted))
    return {
        "source": "live",
        "scrape_metadata": {"url": url, "status": status, "raw_chars": len(raw_text)},
        "data": extracted,
    }


@app.get("/scrape/raw")
async def scrape_raw(domain: str):
    raw_text, url, status = await scrape_page(domain)
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    return {"url": url, "status": status, "total_lines": len(lines), "preview": lines[:50]}


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
        email = session.get("customer_details", {}).get("email", "unknown@unknown.com")
        customer_id = session.get("customer", "")
        new_key = "sk_live_" + secrets.token_urlsafe(32)

        if DATABASE_URL:
            try:
                conn = get_db()
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO api_keys (key, email, stripe_customer_id, plan, monthly_limit) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (new_key, email, customer_id, "pro", 2500),
                )
                conn.commit()
                cur.close(); conn.close()
                print(f"NEW KEY: {email} -> {new_key}")
            except Exception as e:
                print(f"Key creation error: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        else:
            print(f"NEW KEY (no DB): {email} -> {new_key}")

    return {"status": "ok"}


@app.get("/health")
async def health():
    try:
        redis_client.ping()
        redis_ok = True
    except Exception:
        redis_ok = False

    db_ok = False
    if DATABASE_URL:
        try:
            conn = get_db()
            conn.close()
            db_ok = True
        except Exception:
            pass

    return {
        "status": "ok",
        "openai_key_set": bool(openai.api_key),
        "redis_connected": redis_ok,
        "db_connected": db_ok,
    }


if __name__ == "__main__":
    uvicorn.run("scraper:app", host="0.0.0.0", port=8000, reload=True)
