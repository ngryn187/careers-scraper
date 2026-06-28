# v3.0.0 - Redis caching + API auth
import asyncio
import json
import os

import openai
import redis as redis_lib
from fastapi import FastAPI, HTTPException, Security, Header
import uvicorn
from playwright.async_api import async_playwright

app = FastAPI(title="Careers Scraper", version="3.0.0")
openai.api_key = os.environ.get("OPENAI_API_KEY", "")

# Redis setup
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
redis_client = redis_lib.from_url(REDIS_URL, decode_responses=True)

EXTRACTION_PROMPT = (
    "You are a B2B data extraction engine. Given raw text from a company careers page, "
    "extract structured data. Return ONLY valid JSON. If a field cannot be determined, "
    "return an empty array or false. Look for: job titles, technology keywords "
    "(React, AWS, Kubernetes, etc.), and company name. Schema: "
    "{company_name: string, is_hiring: boolean, engineering_roles: [string], "
    "sales_roles: [string], detected_tech_stack: [string]}"
)

# API Key Auth
async def verify_api_key(x_api_key: str = Header(None)):
    valid_key = os.environ.get("VALID_API_KEY", "")
    if not valid_key:
        return  # if no key configured, allow all (dev mode)
    if not x_api_key or x_api_key != valid_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
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

    # Check cache first
    cached = redis_client.get(cache_key)
    if cached:
        return {"source": "cache", "data": json.loads(cached)}

    # Live scrape
    raw_text, url, status = await scrape_page(domain)
    extracted = extract_with_openai(raw_text)

    # Save to cache (7 days TTL)
    redis_client.setex(cache_key, 604800, json.dumps(extracted))

    return {
        "source": "live",
        "scrape_metadata": {"url": url, "status": status, "raw_chars": len(raw_text)},
        "data": extracted
    }


@app.get("/scrape/raw")
async def scrape_raw(domain: str):
    raw_text, url, status = await scrape_page(domain)
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    return {"url": url, "status": status, "total_lines": len(lines), "preview": lines[:50]}


@app.get("/health")
async def health():
    try:
        redis_client.ping()
        redis_ok = True
    except Exception:
        redis_ok = False
    return {
        "status": "ok",
        "openai_key_set": bool(openai.api_key),
        "redis_connected": redis_ok
    }


if __name__ == "__main__":
    uvicorn.run("scraper:app", host="0.0.0.0", port=8000, reload=True)
