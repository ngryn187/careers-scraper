# StackSight — Real-time B2B Hiring Intent & Tech Stack API

[![API Status](https://img.shields.io/badge/API-Live-brightgreen)](https://stacksight.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> Know which companies are actively hiring — and what tech they use — before your competitors do.

**[Try it free →](https://stacksight.org/demo/vercel.com)** · **[Get an API key →](https://stacksight.org)** · **[Docs →](https://stacksight.org/docs)**

---

## What is StackSight?

StackSight scrapes company career pages in real-time to surface:

- **Hiring intent** — Is this company actively hiring? How many open roles?
- **Tech stack** — What technologies are they using, inferred from job descriptions
- **Growth signals** — Which departments are expanding right now
- **Sales intelligence** — Find companies at the exact moment they have budget and headcount growth

**Built for:** Sales teams, SDRs, recruiters, VC firms, and developers building prospecting tools.

---

## Why hiring signals?

A company posting 15 new jobs is a company with budget. That's a fundamentally better time to reach out than cold-guessing from a static database.

StackSight gives you a real-time signal — not stale data from a quarterly crawl.

---

## Quick Start

### 1. Get a free API key

Visit [stacksight.org](https://stacksight.org) and sign up. No credit card required. 25 free lookups included.

### 2. Make your first request

```bash
curl -X GET "https://stacksight.org/v1/enrich?domain=stripe.com" \
     -H "X-API-Key: YOUR_API_KEY"
```

### 3. Parse the response

```json
{
  "company_name": "Stripe",
  "domain": "stripe.com",
  "is_hiring": true,
  "open_roles": 42,
  "engineering_roles": ["Backend Engineer", "ML Engineer", "Platform Engineer"],
  "sales_roles": ["Account Executive", "Solutions Engineer"],
  "detected_tech_stack": ["Go", "Ruby", "AWS", "Kubernetes", "Kafka"],
  "hiring_signals": ["Aggressive growth in Sales team"],
  "cached": false,
  "scraped_at": "2026-07-25T12:00:00Z"
}
```

---

## API Reference

**Base URL:** `https://stacksight.org`

**Authentication:** All requests require an `X-API-Key` header.

```
X-API-Key: YOUR_API_KEY
```

### `GET /v1/enrich`

Scrape hiring intent and tech stack for a company domain.

| Parameter | Type   | Required | Description                         |
|-----------|--------|----------|-------------------------------------|
| `domain`  | string | ✅        | Company domain (e.g. `stripe.com`)  |

**Example:**

```bash
curl "https://stacksight.org/v1/enrich?domain=notion.so" \
     -H "X-API-Key: YOUR_KEY"
```

Full API reference at [stacksight.org/docs](https://stacksight.org/docs).

---

## Pricing

| Tier         | Price    | Requests/Month | Rate Limit    |
|--------------|----------|----------------|---------------|
| **Free**     | $0       | 25             | 10 req/min    |
| **Starter**  | $12/mo   | 500            | 60 req/min    |
| **Pro**      | $49/mo   | 5,000          | 300 req/min   |
| **Business** | $199/mo  | 50,000         | 1,000 req/min |

[View pricing →](https://stacksight.org/#pricing)

---

## Use Cases

### 🎯 Sales prospecting
Find companies actively hiring in a role that needs your product. If a company is hiring 5 DevOps engineers, they probably need infrastructure tooling — reach out now, not after they've already bought.

### 📊 Competitive intelligence
Monitor competitor hiring patterns to understand their product roadmap and where they're expanding.

### 🤝 Recruiting
Find fast-growing companies before they post on LinkedIn.

### 🔁 Workflow automation
Plug the API into your CRM, Clay, or prospecting workflow to enrich leads automatically.

---

## Tech Stack

- **FastAPI** — High-performance async Python API
- **Playwright** — Headless browser for dynamic career page scraping
- **OpenAI GPT-4o-mini** — Structured data extraction from job postings
- **Redis** — 7-day response caching for instant repeat lookups
- **PostgreSQL** — Usage tracking and account management
- **Railway** — Cloud deployment

---

## Self-Hosting

```bash
git clone https://github.com/ngryn187/careers-scraper.git
cd careers-scraper
pip install -r requirements.txt

# Required environment variables
export OPENAI_API_KEY=your_key
export DATABASE_URL=postgresql://...
export REDIS_URL=redis://localhost:6379
export STRIPE_SECRET_KEY=your_stripe_key

uvicorn scraper:app --host 0.0.0.0 --port 8000
```

Or with Docker:

```bash
docker build -t stacksight .
docker run -p 8000:8000 -e OPENAI_API_KEY=your_key stacksight
```

---

## vs. Alternatives

| Feature | StackSight | BuiltWith | Wappalyzer |
|---|---|---|---|
| Real-time data | ✅ | ❌ | ❌ |
| Hiring intent signals | ✅ | ❌ | ❌ |
| Free tier | ✅ | ❌ | Limited |
| API access | ✅ | ✅ | ✅ |
| Starts at | $12/mo | $490/mo | $250/mo |

---

## Support

- 📧 [support@stacksight.org](mailto:support@stacksight.org)
- 📖 [stacksight.org/docs](https://stacksight.org/docs)
- 🌐 [stacksight.org](https://stacksight.org)

---

*Built for sales teams, recruiters, and developers who need real-time hiring intelligence.*
