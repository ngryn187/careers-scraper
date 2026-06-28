import asyncio
import json
import os

import openai
from fastapi import FastAPI, HTTPException
from playwright.async_api import async_playwright
import uvicorn

app = FastAPI(title="Careers Scraper", version="2.0.0")
openai.api_key = os.environ.get("OPENAI_API_KEY", "")

EXTRACTION_PROMPT = (
    "You are a data extraction assistant. Given raw text from a careers/jobs page, "
    "return a JSON object with: company_is_hiring (bool), total_jobs_found (int or null), "
    "departments (list), locations (list), sample_job_titles (up to 10), "
    "hiring_signals (list). Use null for unknown fields."
)


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
async def scrape(domain: str):
    raw_text, url, status = await scrape_page(domain)
    extracted = extract_with_openai(raw_text)
    return {"scrape_metadata": {"url": url, "status": status, "raw_chars": len(raw_text)}, "extracted_data": extracted}


@app.get("/scrape/raw")
async def scrape_raw(domain: str):
    raw_text, url, status = await scrape_page(domain)
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    return {"url": url, "status": status, "total_lines": len(lines), "preview": lines[:50]}


@app.get("/health")
async def health():
    return {"status": "ok", "openai_key_set": bool(openai.api_key)}


if __name__ == "__main__":
    uvicorn.run("scraper:app", host="0.0.0.0", port=8000, reload=True)
