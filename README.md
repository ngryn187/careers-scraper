# StackSight API — Real-time B2B Hiring Intent & Tech Stack Detection

> Know which companies are hiring engineers and what tech they use — before your competitors do.

[![API Status](https://img.shields.io/badge/API-Live-brightgreen)](https://careers-scraper-production.up.railway.app)
[![RapidAPI](https://img.shields.io/badge/RapidAPI-Listed-blue)](https://rapidapi.com/search/stacksight)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## What is StackSight?

StackSight is a B2B data API that scrapes company career pages in real-time to detect:

- **Hiring intent** — Is the company actively hiring? Which roles?
- **Tech stack** — What technologies does the company use based on job postings?
- **Growth signals** — Which departments are expanding?
- **Sales intelligence** — Find companies at the exact moment they need your product

**Perfect for:** Sales teams, recruiters, VC firms, market researchers, and developer tools.

---

## Quick Start

### 1. Get a Free API Key

Visit [https://careers-scraper-production.up.railway.app](https://careers-scraper-production.up.railway.app) and enter your email to get 50 free requests instantly.

Or subscribe on [RapidAPI](https://rapidapi.com/search/stacksight) for higher limits.

### 2. Make Your First Request

```bash
curl -X GET "https://careers-scraper-production.up.railway.app/scrape?domain=stripe.com" \
     -H "X-API-Key: YOUR_API_KEY"
```

### 3. Parse the Response

```json
{
  "company_name": "Stripe",
  "is_hiring": true,
  "engineering_roles": ["Backend Engineer", "ML Engineer", "Platform Engineer"],
  "sales_roles": ["Account Executive", "Solutions Engineer"],
  "detected_tech_stack": ["Go", "Ruby", "AWS", "Kubernetes", "Kafka"],
  "departments": ["Engineering", "Sales", "Design"],
  "hiring_signals": ["Aggressive growth in Sales team"],
  "sample_job_titles": ["Solutions Engineer", "Senior Product Designer"]
}
```

---

## API Reference

### Base URL

```
https://careers-scraper-production.up.railway.app
```

### Authentication

All requests require an `X-API-Key` header:

```
X-API-Key: sk_your_api_key_here
```

### Endpoints

#### `GET /scrape`

Scrape hiring intent and tech stack for a company domain.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `domain` | string | ✅ | Company domain (e.g., `stripe.com`) |

**Example:**

```bash
curl "https://careers-scraper-production.up.railway.app/scrape?domain=notion.so" \
     -H "X-API-Key: YOUR_KEY"
```

#### `GET /docs`

Interactive Swagger UI documentation.

#### `GET /health`

Health check endpoint.

---

## Pricing

| Tier | Price | Requests/Month | Rate Limit |
|------|-------|----------------|------------|
| **Free** | $0 | 50 | 1 req/sec |
| **Pro** | $49/mo | 2,500 | 10 req/sec |
| **Business** | $199/mo | 15,000 | Unlimited |

[Subscribe on RapidAPI →](https://rapidapi.com/search/stacksight)

---

## Use Cases

### 🎯 Sales Prospecting
Identify companies actively hiring in a department that needs your product. If a company is hiring 5 DevOps engineers, they probably need infrastructure tooling.

### 🔍 Competitive Intelligence
Monitor competitor hiring patterns to understand their product roadmap and expansion plans.

### 🤝 Recruiting
Find companies that are scaling fast and reach out before they post on LinkedIn.

### 📊 Market Research
Aggregate hiring signals across thousands of companies to spot industry trends.

---

## Tech Stack

- **FastAPI** — High-performance async Python API
- **Playwright** — Headless browser for dynamic career page scraping
- **OpenAI GPT-4o-mini** — Structured data extraction from raw job postings
- **Redis** — Response caching for instant repeat lookups
- **Railway** — Auto-scaling cloud deployment

---

## Self-Hosting

```bash
git clone https://github.com/ngryn187/careers-scraper.git
cd careers-scraper
pip install -r requirements.txt

# Set environment variables
export OPENAI_API_KEY=your_key
export REDIS_URL=redis://localhost:6379

# Run
uvicorn scraper:app --host 0.0.0.0 --port 8000
```

Or deploy with Docker:

```bash
docker build -t stacksight .
docker run -p 8000:8000 -e OPENAI_API_KEY=your_key stacksight
```

---

## Support

- 📧 Email: [ngrynai@gmail.com](mailto:ngrynai@gmail.com)
- 📖 Docs: [https://careers-scraper-production.up.railway.app/docs](https://careers-scraper-production.up.railway.app/docs)
- 🚀 RapidAPI: [https://rapidapi.com/search/stacksight](https://rapidapi.com/search/stacksight)

---

*Built with ❤️ for sales teams, recruiters, and developers who need real-time hiring intelligence.*
