# StackSight API

> Real-time hiring intent signals, tech stack detection, and bulk domain enrichment via a simple REST API.

**[stacksight.org](https://stacksight.org)**

---

## What It Does

StackSight scrapes company careers pages and extracts structured B2B sales intelligence:

- **Hiring intent** — is the company actively hiring?
- **Open roles** — engineering, sales, and other job titles
- **Tech stack** — languages, frameworks, and tools detected from job descriptions
- **Cached results** — 7-day Redis cache for fast repeated lookups

---

## Quick Start

```bash
curl "https://stacksight.org/scrape?domain=stripe.com" \
  -H "X-API-Key: YOUR_API_KEY"
```

**Response:**
```json
{
  "company_name": "Stripe",
  "is_hiring": true,
  "engineering_roles": ["Backend Engineer", "iOS Engineer", "ML Engineer"],
  "sales_roles": ["Account Executive", "Solutions Engineer"],
  "other_roles": ["Product Designer", "Finance Manager"],
  "detected_tech_stack": ["Ruby", "Go", "Java", "React", "AWS"]
}
```

---

## API Reference

### `GET /scrape`

Scrape and extract structured data for a domain.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `domain` | string | Yes | Domain to analyze (e.g. `stripe.com`) |

**Headers:**
- `X-API-Key` — your API key (required)

---

### `GET /health`

Health check endpoint.

---

### `GET /docs`

Interactive Swagger UI documentation.

---

## Pricing

| Tier | Price | Requests/Month | Rate Limit |
|------|-------|----------------|------------|
| Free | $0 | 25 | 10 req/sec |
| Starter | $19/mo | 500 | 60 req/sec |
| Pro | $49/mo | 5,000 | 300 req/sec |
| Business | $199/mo | 50,000 | 1,000 req/sec |

[Get started free →](https://stacksight.org)

---

## Use Cases

- **Sales prospecting** — find companies hiring in your ICP and reach out at the right moment
- **Competitor monitoring** — track which companies are scaling engineering or sales teams
- **CRM enrichment** — enrich leads with real-time hiring and tech stack data
- **Market research** — identify companies adopting specific technologies

---

## Tech Stack

- **FastAPI** — Python web framework
- **Playwright** — headless browser scraping
- **GPT-4o-mini** — structured data extraction
- **Redis** — 7-day result caching
- **PostgreSQL** — user and API key storage
- **Railway** — hosting and deployment
