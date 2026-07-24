# StackSight.org — Complete End-to-End Audit

**Audited site:** https://stacksight.org
**Source reviewed:** `scraper.py` (single-file FastAPI app, v9.7.4, ~3,156 lines)
**Audit date:** 2026-07-24
**Auditors:** Technical SEO, Web Dev, UX, Accessibility, Performance, Security, CRO, Analytics, Marketing

---

## 1. Executive Summary

StackSight is a single-file FastAPI application selling a B2B "hiring intent + tech stack" enrichment API. It is a competent solo-founder MVP: SSRF protection exists, magic-link auth is used, admin is behind password + TOTP, and Stripe webhooks are signature-verified. That baseline is better than most indie SaaS.

However, the audit surfaced **serious problems that undermine trust, conversion, security, and correctness**:

- **The core product is producing visibly wrong data.** The public `/trending` page shows "Twilio" as the company name for `segment.com` and `sendgrid.com`, "Fin" for `intercom.com`, and blank roles for many entries. This is a live credibility killer on an indexable page.
- **Marketing claims contradict the code.** The homepage says "deterministic tech stack detection" and "100% deterministic… we parse actual HTML script tags." In reality tech stack is *inferred by GPT-4o-mini from job titles* (see `EXTRACTION_PROMPT` and `infer_tech_from_roles`). This is a material false-advertising / trust risk.
- **Free-tier limit is inconsistent across the app** (25 vs 10) — homepage/docs say 25, `/docs` rate-limit table shows 10, `PLAN_LIMITS` says 25, but `customer.subscription.deleted` downgrades to `requests_limit = 10`. Users will be confused and support tickets will follow.
- **`/register` is broken** — it returns a blank page. The homepage/nav funnels users to `#signup` (an anchor), but a `/register` URL exists in expectations and 404s to empty content.
- **In-process rate limiting (`_rate_limit` dict)** is per-worker and lost on restart — ineffective under multiple workers/replicas.
- **Money-losing quota logic:** the public `/demo/{domain}` page performs a full live scrape + OpenAI call for **any anonymous visitor** with no rate limit and no auth. This is an unbounded cost and abuse vector (each demo = one GPT-4o-mini call + a Playwright browser launch).
- **No analytics of any kind** — no GA4, no GTM, no Search Console verification tag, no conversion tracking. You are flying blind on the entire funnel.

**Bottom line:** the app *works*, but data quality, honesty of claims, and a few cost/security holes are the highest-leverage fixes. Nail those before spending on traffic.

---

## 2. Scores (0–100)

| Category | Score | One-line rationale |
|---|---:|---|
| **Overall** | **54** | Functional MVP undermined by data-quality, honesty, and cost-control gaps |
| SEO | 62 | Good meta/OG/canonical basics; thin sitemap, no schema, contradictory content |
| Performance | 68 | Inline CSS is fine for size; Playwright + live GPT on demo is the real bottleneck |
| Security | 58 | SSRF guard + TOTP admin good; abuse vectors, verbose logging, in-memory rate limit |
| Accessibility | 45 | No skip links, icon-only signals, missing form/label semantics, contrast risks |
| UX | 60 | Clean dark UI; broken register, anchor-only CTAs, no dashboard preview |
| Conversion | 55 | Strong hero, but false claims + no social proof + friction in signup flow |
| Code Quality | 40 | 3,100-line single file, HTML-in-Python, duplicated DB code, no tests |
| Mobile | 65 | Responsive meta present; tables on /trending and /docs likely overflow |

---

## 3. Critical & High-Severity Findings

### C-1 — Product is emitting wrong company data on a public, indexable page (CRITICAL)
- **Evidence:** `/trending` renders `segment.com → "Twilio"`, `sendgrid.com → "Twilio"`, `intercom.com → "Fin"`, and many rows show `Open Roles: N/A` while still marked "✓ Hiring."
- **Why it matters:** This is the *storefront for the product's accuracy*. A prospect evaluating a data vendor will immediately distrust it. It also gets indexed by Google (page is `index,follow`), creating public evidence of low quality.
- **Root cause:** Cache is keyed by `domain:{domain}` and populated from GPT extraction of whatever careers page was scraped; subsidiaries/redirects (Segment/SendGrid → Twilio careers) leak the parent's name and roles. GPT hallucination/leakage isn't corrected.
- **Fix:**
  1. Store the *requested* domain alongside extracted data and reject/flag when `company_name` doesn't plausibly match the domain.
  2. Filter `/trending` to only show rows where `open_roles > 0` AND `company_name` is non-generic (you already skip `open_roles == 0` — but "N/A hiring" rows still appear, meaning the filter is being bypassed by the fallback DEMO_DATA path or role-count double counting). Audit the render loop.
  3. Add a manual allow-list of curated domains for `/trending` until data quality is trustworthy.
- **Benefit:** Directly protects conversion and brand credibility.

### C-2 — False / misleading accuracy claims (CRITICAL, legal + trust)
- **Evidence (homepage FAQ):** "How accurate is the tech stack detection? Very accurate. We parse actual HTML script tags and headers, not guesses… 100% deterministic." Homepage hero: "deterministic tech stack detection."
- **Reality (code):** Tech stack comes from `extract_with_openai` (GPT-4o-mini, an LLM that *guesses*) plus `infer_tech_from_roles`, which maps *job titles* to tech (e.g., sees "iOS Engineer" → outputs "Swift/Xcode"). This is explicitly the opposite of "parsing script tags" and is not deterministic.
- **Why it matters:** This is a factual misrepresentation of the product to paying customers — a consumer-protection / false-advertising exposure, and a churn driver once customers verify results.
- **Fix:** Either (a) change the copy to the truth ("AI-inferred tech signals from hiring data") or (b) actually implement deterministic detection (fetch homepage HTML, regex for known script/CDN/analytics fingerprints — a Wappalyzer-style ruleset). Given you also market `/vs/wappalyzer` and `/vs/builtwith`, honesty here is essential.
- **Benefit:** Removes legal risk, reduces refund/churn, builds durable trust.

### C-3 — Unauthenticated, unmetered, expensive `/demo/{domain}` (CRITICAL cost/abuse)
- **Evidence:** `demo()` is "public — no login required," runs `scrape_page` (launches a headless Chromium via Playwright) and `extract_with_openai` (paid GPT call) for any domain on a cache miss. No IP rate limiting on this path.
- **Why it matters:** An attacker (or a crawler) can hit `/demo/<random-subdomains>` and force unbounded Playwright launches + OpenAI spend. Each request is real money and CPU. This can drain your OpenAI budget and OOM the box (each Chromium instance is heavy).
- **Fix:**
  1. Add per-IP rate limiting (Redis-backed, e.g. 5 demo scrapes/hour/IP).
  2. Restrict live scraping on `/demo` to a curated allow-list; for anything else, only serve cache or a "sign up to analyze this domain" gate.
  3. Add a global concurrency semaphore around Playwright launches.
- **Benefit:** Protects margins and uptime.

### C-4 — Free-tier limit is inconsistent (HIGH)
- **Evidence:** `PLAN_LIMITS["free"] = 25`; homepage + `/docs` prose say "25"; but `/docs` rate-limit card shows **10**, and `customer.subscription.deleted` sets `requests_limit = 10`. A canceled paying customer is silently downgraded to 10, not 25.
- **Fix:** Single source of truth — reference `PLAN_LIMITS["free"]` everywhere; change the webhook downgrade to `requests_limit = PLAN_LIMITS["free"]`; fix the docs card to 25.
- **Benefit:** Consistency, fewer support tickets, correct entitlements.

### C-5 — In-memory rate limiter is non-functional in production (HIGH)
- **Evidence:** `_rate_limit: dict = {}` module global; `check_rate_limit` reads/writes it. On Railway/uvicorn with >1 worker or after any restart, this state is per-process and volatile.
- **Why it matters:** Advertised per-minute rate limits (300/min Pro, 1000/min Business) are not reliably enforced; a determined caller bypasses them by hitting different workers.
- **Fix:** Move to Redis sliding-window (INCR + EXPIRE, or sorted-set timestamps) keyed by `ratelimit:{api_key}:{minute}`.
- **Benefit:** Real abuse protection; protects OpenAI spend.

### C-6 — `/register` returns a blank page (HIGH, conversion)
- **Evidence:** `GET https://stacksight.org/register` returns empty body; there is no `/register` route in the source. All signup flows use the homepage `#signup` anchor + `POST /signup`.
- **Why it matters:** `/register` is a URL users and external links will guess; a blank page loses signups and looks broken.
- **Fix:** Add `@app.get("/register")` → `RedirectResponse("/#signup", 301)` (mirror the existing `/pricing` redirect pattern).
- **Benefit:** Recovers lost top-of-funnel signups.

### C-7 — No analytics / conversion tracking anywhere (HIGH)
- **Evidence:** No GA4, no Google Tag Manager, no Plausible, no Search Console `google-site-verification` meta, no event tracking on "Get Free Key," checkout, or demo. Grep of rendered pages shows zero analytics scripts.
- **Why it matters:** You cannot measure signup conversion, drop-off, channel ROI, or activation. Every growth decision is a guess.
- **Fix:** Add a lightweight, privacy-friendly analytics (Plausible or GA4 via GTM). Track: hero CTA click, signup submit, verify-email success, checkout start, checkout success, demo run. Verify the domain in Google Search Console and submit the sitemap.
- **Benefit:** Turns the whole funnel from opaque to optimizable.

### C-8 — Sensitive data written to logs (HIGH security)
- **Evidence:** Stripe webhook logs `secret prefix: {STRIPE_WEBHOOK_SECRET[:10]}` and full event/email/customer info; provisioning logs matched emails and session IDs; email-send logs response bodies. `verify-email` error path renders raw exception text to the user (`Setup error: {e}`).
- **Why it matters:** Log aggregation often leaks to third parties; printing secret prefixes and PII is an unnecessary exposure. User-facing raw exceptions can leak stack/DB details.
- **Fix:** Remove secret-prefix logging entirely; redact emails in logs; never render raw `{e}` to users — show a generic message and log the detail server-side.
- **Benefit:** Reduces breach blast radius and info leakage.

### C-9 — No security response headers / CSP (HIGH)
- **Evidence:** No middleware setting `Content-Security-Policy`, `Strict-Transport-Security`, `X-Content-Type-Options`, `X-Frame-Options`/`frame-ancestors`, `Referrer-Policy`, or `Permissions-Policy`. (Confirmed by absence in code; the front proxy blocked direct header inspection.)
- **Why it matters:** Missing HSTS allows SSL-strip on first visit; missing frame-ancestors allows clickjacking of login/dashboard; no CSP means any injected inline script executes freely (and the app uses many inline `<script>`/`onclick`).
- **Fix:** Add a FastAPI middleware that sets HSTS (`max-age=63072000; includeSubDomains; preload`), `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY`, and a CSP. Note: current heavy use of inline `onclick`/`<script>` will require refactoring to external scripts + nonces for a strict CSP.
- **Benefit:** Hardens against clickjacking, MITM, and XSS.

### C-10 — Open-redirect-ish `/scrape` self-redirect built from user URL (MEDIUM→HIGH)
- **Evidence:** `scrape_redirect` does `str(request.url).replace("/scrape", "/v1/enrich")`. Naive string replace on the full URL can misfire if `/scrape` appears elsewhere (e.g., a domain/query containing "scrape"), producing malformed or unexpected redirect targets.
- **Fix:** Reconstruct the redirect from a fixed path + validated query params, not string replacement on the raw URL.
- **Benefit:** Removes redirect-manipulation edge cases.

---

## 4. SEO Audit

**Good:** Homepage and `/docs` have solid `title`, `meta-description`, canonical, OG, and Twitter tags; `meta-robots: index,follow`; robots.txt is valid and references the sitemap.

**Issues:**
- **S-1 (High):** `sitemap.xml` lists only 4 URLs (`/`, `/docs`, `/demo/stripe.com`, `/login`). It omits `/trending`, `/demo/vercel.com`, `/terms`, `/privacy`, `/vs/builtwith`, `/vs/wappalyzer`, `/vs/theirstack`. The `/vs/*` comparison pages are your best organic-intent SEO assets and aren't in the sitemap. **Fix:** Add all indexable public pages; include the `/vs/*` pages and a curated set of `/demo/*` pages.
- **S-2 (High):** **No structured data anywhere.** No `Organization`, `SoftwareApplication`, `Product`/`Offer` (pricing), `FAQPage` (you have a full FAQ section — prime rich-result candidate), or `BreadcrumbList`. **Fix:** Add `FAQPage` JSON-LD on the homepage, `SoftwareApplication`+`Offer` for pricing, `Organization` sitewide, and `TechArticle` on `/docs`.
- **S-3 (Medium):** `/login` and `/trending` are missing `meta-description` and canonical. `/login` should arguably be `noindex`. `/trending` should have a description and canonical.
- **S-4 (Medium):** `/demo/{domain}` generates near-unlimited thin/duplicate pages (title `Demo: {domain}`) that are crawlable and could create index bloat / thin-content signals. **Fix:** `noindex` all `/demo/*` except a small curated set, or add canonical to a hub page.
- **S-5 (Medium):** Robots disallows `/dashboard`, `/auth`, `/logout` — good — but not `/admin`, `/checkout`, `/choose`, `/success`, `/billing-portal`, `/verify-email`. These shouldn't be indexed. **Fix:** Add `Disallow` entries (and `noindex` headers) for all authenticated/transactional routes.
- **S-6 (Medium):** Trending page title uses an emoji H1 ("🔥 Companies Actively Hiring Right Now") which is fine, but the **data errors (C-1) actively damage E-E-A-T** on an indexable page.
- **S-7 (Low):** OG image is a single static `/og-image.png`; no per-page OG images for `/docs`, `/trending`, `/vs/*`.
- **S-8 (Low):** No `hreflang` (fine if English-only), no breadcrumb nav.

**Content/E-E-A-T:** No author/company "About" page, no team, no case studies, no logos of real customers. For a data-trust product, an About/Trust page is important. Blog is absent — the `/vs/*` pages hint at SEO intent; a comparison-keyword blog ("BuiltWith alternative," "hiring intent data," "tech stack lookup API") is the obvious organic play.

---

## 5. Performance Audit

- **P-1 (High):** **Live scrape uses Playwright (full Chromium) per cache miss.** Launching a browser per request is the single biggest latency + memory cost. The homepage advertises "<100ms cached" — true for cache hits, but cache misses spin up Chromium + a GPT call (multi-second, heavy). **Fix:** For careers-page scraping, try plain `httpx` + HTML parse first; fall back to Playwright only for JS-heavy sites. Pool/reuse a single browser instance instead of `async_playwright()` per call.
- **P-2 (Medium):** `await asyncio.sleep(2)` hard-coded after each page load in `scrape_page` adds 2s to every live scrape unconditionally. **Fix:** Wait on a selector/network-idle with a shorter cap.
- **P-3 (Medium):** All CSS is inline in every HTML response (fine for first paint, but repeated across pages — no shared cached stylesheet). Favicons/OG image are base64-decoded per request rather than served with cache headers. **Fix:** Serve static assets with long `Cache-Control`; consider one shared CSS file.
- **P-4 (Medium):** No evidence of gzip/brotli compression middleware or `Cache-Control` headers on HTML. **Fix:** Add `GZipMiddleware` and cache headers for static/marketing pages.
- **P-5 (Low):** No font strategy issues (uses system font stack — good, zero web-font cost). No render-blocking third-party scripts (because there are none — see analytics gap).
- **Core Web Vitals (heuristic):** LCP likely fine on marketing pages (system fonts, inline CSS, single static hero). CLS risk on `/trending` and `/docs` from wide tables. INP is low (little JS). The demo page's LCP is gated on the live scrape when uncached — very poor for uncached domains.

---

## 6. UX Audit

- **U-1 (High):** **Primary CTA is an anchor (`#signup`) not a route.** From `/docs`, `/login`, `/demo`, links to "Get free key" point to `/#signup` — a full page reload + scroll. Works, but a dedicated, focusable signup page converts better and is linkable.
- **U-2 (High):** `/register` blank page (see C-6) — dead end.
- **U-3 (Medium):** Login is magic-link only. `POST /login` returns 404 "No account found" if the email isn't registered — but the login page's only alternative is "#signup." A user who mistypes gets a hard error rather than a graceful "want to sign up?" path.
- **U-4 (Medium):** After signup the user must go to email → verify → dashboard. There's no in-app confirmation of what happens next beyond a toast. The `/success` (post-payment) page says "key will arrive in your inbox within a minute" — email-dependent activation is fragile; show the key in-app on the dashboard immediately.
- **U-5 (Medium):** Pricing shows "Pro $39/mo billed annually • save 20%" and "Business $166/mo billed annually" — mixing monthly-equivalent-of-annual with the Starter's true monthly ($12/mo) is confusing. A monthly/annual toggle is expected. The `/choose/pro` and `/choose/business` interstitials add a step.
- **U-6 (Medium):** No trust signals: no customer logos, testimonials, request counts, or "used by X teams." The "why choose us" section even concedes competitors have more data — honest, but needs counterbalancing proof.
- **U-7 (Low):** Trending table "Open Roles: N/A" while "✓ Hiring" is contradictory to users, not just crawlers.
- **U-8 (Low):** Demo page lists 70 raw job titles with no grouping/collapse beyond category labels — long scroll, low scan-ability.

---

## 7. Accessibility Audit (WCAG 2.2)

- **A-1 (High):** **No skip link** on any page. Keyboard users must tab through nav on every page. (2.4.1)
- **A-2 (High):** **Hiring status conveyed by color + icon only** ("✓ Hiring" / "✗ No Roles" in purple/green). Screen readers may read the checkmark but color is the differentiator. Ensure text label is present and not color-dependent. (1.4.1)
- **A-3 (High):** **Form inputs / labels:** the homepage "Try any domain" input and the signup email field need programmatic `<label>` association and visible focus states. The demo/home domain box appears to be an input with placeholder-as-label (placeholder is not a label). (1.3.1, 3.3.2, 4.1.2)
- **A-4 (Medium):** **Focus visibility:** login input uses `outline:none` with a border-color change on focus — border color alone may not meet the 3:1 focus-indicator contrast of WCAG 2.2 (2.4.11/2.4.13). Add a visible focus ring.
- **A-5 (Medium):** **Contrast:** heavy use of `#6b7280`/`#888`/`#4b5563` gray text on `#0a0a0a`/`#111`. `#6b7280` on `#0a0a0a` is ~4.6:1 (passes normal text) but `#4b5563` (~3.3:1) and `#555` fail for body text. Audit all muted grays against 4.5:1. (1.4.3)
- **A-6 (Medium):** **FAQ accordions** ("What is hiring intent data?+") — verify they use `<button aria-expanded>` and are keyboard operable, not click-only `<div>`s.
- **A-7 (Medium):** **Tables** on `/docs` and `/trending` lack `<caption>`/scope; wide tables aren't responsive (horizontal scroll without `overflow` affordance announced). (1.3.1)
- **A-8 (Low):** OG/favicon images fine; ensure any decorative icons have `aria-hidden`. Page language is set (`lang="en"`) — good.
- **A-9 (Low):** `/login` page missing `<meta description>` is SEO, but also the page has no `<h1>` landmark beyond styled text — verify heading semantics.

---

## 8. Security Audit (detail)

**Strengths:** SSRF protection in `validate_domain` (resolves DNS, blocks private/reserved ranges, strips credentials/scheme/port — genuinely good); parameterized SQL throughout (no injection found); magic-link + session cookies are `HttpOnly; Secure; SameSite=Lax`; admin gated by owner email + password + TOTP with Redis-backed lockout (5 fails → 15 min); Stripe webhook signature verified; `secrets.compare_digest` for cron secret.

**Findings:**
- **SEC-1 (High):** No CSRF protection on state-changing POSTs. `/login`, `/signup`, `/admin/*` are JSON POSTs. `SameSite=Lax` mitigates cross-site form posts for session-cookie routes, but `/admin/toggle-key`, `/admin/delete-user`, `/admin/flush-cache` rely on cookie auth (`admin_verified` + `ss_session`) with no CSRF token. A crafted page could attempt these while the owner is logged in (Lax blocks top-level cross-site POST, but not all vectors). **Fix:** Add CSRF tokens or require a custom header + origin check on admin mutations.
- **SEC-2 (High):** **Verbose secret/PII logging** (see C-8).
- **SEC-3 (High):** **No security headers/CSP** (see C-9).
- **SEC-4 (Medium):** `/demo` abuse + unbounded cost (see C-3).
- **SEC-5 (Medium):** In-memory rate limit (see C-5) is also a mild DoS surface — memory grows with unique API keys (`_rate_limit` never garbage-collects stale keys).
- **SEC-6 (Medium):** `admin_reset_usage` and `admin_create_key` are protected only by `X-Cron-Secret`; `admin_create_key` can mint *any* plan for *any* email if the cron secret leaks. Ensure `CRON_SECRET` is long/rotated and never logged.
- **SEC-7 (Medium):** `/health` exposes `version` (VERSION 9.7.4) — minor info disclosure aiding version-specific attacks. Low value; consider gating.
- **SEC-8 (Low):** `verify_api_key` compares keys with a SQL `=` lookup (constant-ish via DB), fine, but the legacy dual-column `api_key OR "key"` matching is fragile and should be consolidated after migration.
- **SEC-9 (Low):** Session tokens are 48-byte urlsafe (strong). Sessions have a 7-day expiry but **no rotation on privilege change** and no server-side "logout all." Acceptable for scale.
- **SEC-10 (Low):** `next`/redirect params are validated to start with `/` (good — prevents open redirect on auth/login). Keep this.
- **Dependencies:** `openai`, `stripe`, `psycopg2`, `playwright`, `pyotp`, `redis`, `fastapi`, `uvicorn` — no versions pinned in this file; ensure a `requirements.txt` with pinned, patched versions and a Dependabot/`pip-audit` process. Playwright/Chromium must be kept patched (browser CVEs).

---

## 9. Conversion Rate Optimization

- **CRO-1 (High):** **Remove/replace the false "100% deterministic" claim** (C-2). Trust is your conversion foundation for a data product; a claim customers can trivially disprove kills repeat purchase.
- **CRO-2 (High):** **Add social proof** — even "N companies analyzed" or a live counter, a few logos, or a founder note. The page currently asks for trust with zero third-party validation.
- **CRO-3 (High):** **Reduce activation friction.** Current: signup → email → verify → dashboard → find key. Every email hop loses users. Consider showing the key immediately in-app after email verify (already done) but also surface it on `/success` for paid, and add a "resend email" affordance.
- **CRO-4 (Medium):** **Pricing clarity** — add a monthly/annual toggle; make Starter→Pro upgrade value obvious (bulk API, rate limits). The "Free = 25 total (not monthly)" is a real conversion lever: 25 *total* is very tight and pushes upgrade — but make it explicit and consider a monthly free reset to drive habit + upgrade intent.
- **CRO-5 (Medium):** **Interactive demo on the homepage.** The "Try any domain → Analyze" box is the strongest converter you have; make it work inline (with the C-3 rate-limit guard) and show a real result → then gate "get full JSON" behind signup.
- **CRO-6 (Medium):** CTAs are anchor-based; add a persistent top-right "Get free key" button that routes to a real signup page.
- **A/B test ideas:** (1) "AI-inferred tech signals" vs current claim on trust/conversion; (2) inline working demo vs static code sample; (3) free tier "25 total" vs "25/month"; (4) monthly/annual toggle default; (5) adding 3 customer logos.

---

## 10. Content Audit

- Homepage copy is tight and benefit-led — good.
- **Contradictions** (free limit 25 vs 10; "deterministic" vs AI) must be resolved.
- **Thin/auto pages:** unlimited `/demo/*` are thin and should be `noindex` except curated.
- **Missing pages:** About/Trust, Blog, Changelog, Status page, real customer stories.
- **Keyword opportunity:** the `/vs/builtwith`, `/vs/wappalyzer`, `/vs/theirstack` pages target high-intent comparison queries — get them into the sitemap, add schema, and expand them into full comparison articles.
- **FAQ** is good and should be marked up as `FAQPage` schema.
- No keyword cannibalization observed (small site).

---

## 11. Analytics Audit

- **No GA4, no GTM, no Search Console verification, no server-side event logging for funnel steps.** This is the single biggest measurement gap.
- **Fix:** (1) Add GA4 (or Plausible for privacy) via a single script or GTM; (2) verify domain in GSC + submit sitemap; (3) fire events: `hero_cta_click`, `demo_run`, `signup_submit`, `email_verified`, `checkout_start`, `checkout_success`, `plan_upgrade`; (4) log these server-side too (you already have DB — add a lightweight `events` table) so you have first-party truth independent of client blockers.

---

## 12. Marketing Audit

- **Branding:** Consistent purple (#a855f7/#7c3aed) dark theme across pages, emails, and 404 — good cohesion.
- **Email capture:** Only via signup; no newsletter/lead magnet. A "free hiring-intent report for your top 10 target accounts" lead magnet would capture top-of-funnel.
- **Social:** Only an X/Twitter link (@StackSightOrg). No LinkedIn (odd for a B2B sales tool — LinkedIn is where the buyers are). No og per-page images.
- **Local SEO / listings:** N/A for a pure API product, but Product Hunt, G2, Capterra, and API directories (RapidAPI, APIs.guru) are the equivalent "listings" you should pursue.
- **Review strategy:** none; add G2/Product Hunt.
- **Competitive positioning:** honest "we're smaller but faster/cheaper" framing is defensible — but the false accuracy claim undercuts it. Lean into "real-time, fresh, cheap, simple" and drop "deterministic."

---

## 13. Code Quality & Infrastructure

- **CQ-1 (High):** **3,156-line single file mixing routing, DB, HTML templates, email, Stripe, and admin.** Unmaintainable and error-prone (this is how the 25-vs-10 and "deterministic" inconsistencies happen). **Fix:** Split into modules (`routes/`, `db.py`, `templates/` with Jinja2, `payments.py`, `scraper.py`), move HTML to template files.
- **CQ-2 (High):** **No tests.** For an app handling payments, auth, and SSRF, add unit tests for `validate_domain`, quota logic, webhook handling, and rate limiting.
- **CQ-3 (Medium):** **Repeated DB boilerplate** (`get_db()` → cursor → commit → close) in ~40 handlers with no connection pooling. Use a pool (`psycopg2.pool` or `asyncpg`) and a context manager/dependency. Under load, opening a fresh Postgres connection per request will exhaust connections.
- **CQ-4 (Medium):** **Legacy dual-column `api_key`/`"key"`** and inline `ALTER TABLE ... IF NOT EXISTS` migrations run on every startup — brittle; move to a real migration tool (Alembic).
- **CQ-5 (Medium):** `startup()` runs `init_db()` synchronously with schema mutations — risky on multi-replica deploys (race on ALTERs). Gate migrations behind a single job.
- **CQ-6 (Low):** Duplicated tech-inference logic between the GPT prompt and `TECH_INFERENCE_MAP` — pick one source of truth.
- **Infra:** DB (Postgres) + Redis + Playwright on what appears to be Railway (front proxy present; `X-Proxy-Error: blocked-by-allowlist` seen). Ensure: automated Postgres backups, uptime monitoring + `/health` checks, an alert on OpenAI spend, and a Redis persistence/eviction policy (trending + cache depend on it; if Redis is `noeviction` and fills, writes fail; if `allkeys-lru`, cache silently evicts). **Add a status page and error monitoring (Sentry).**
- **HTTP/2/3, CDN, SSL:** Behind a proxy that terminates TLS (good); confirm HSTS + HTTP/2 at the edge and put static assets (og-image, favicons) on a CDN with long cache TTLs.

---

## 14. Top 10 Critical Issues (ranked)

1. **Wrong company data on public `/trending`** (Twilio/Fin mislabels) — C-1.
2. **False "100% deterministic tech detection" claim** vs AI-guessed reality — C-2.
3. **Unauthenticated, unmetered, expensive `/demo` scraping** (cost/abuse) — C-3.
4. **No analytics/conversion tracking at all** — C-7.
5. **In-memory rate limiter ineffective in prod** — C-5.
6. **Free-tier limit inconsistency (25 vs 10)**, incl. wrong downgrade value — C-4.
7. **No security headers / CSP / HSTS** — C-9.
8. **Secret-prefix + PII logging; raw exceptions shown to users** — C-8.
9. **`/register` blank page loses signups** — C-6.
10. **No connection pooling — Postgres connection exhaustion under load** — CQ-3.

---

## 15. Top 25 Quick Wins (low effort, high impact)

1. Add `@app.get("/register")` → redirect to `/#signup`.
2. Fix `/docs` rate-limit card: 10 → 25.
3. Change webhook downgrade `requests_limit = 10` → `PLAN_LIMITS["free"]` (25).
4. Rewrite FAQ "100% deterministic" → truthful "AI-inferred from hiring signals."
5. Remove `STRIPE_WEBHOOK_SECRET[:10]` from logs.
6. Stop rendering raw `{e}` to users in `verify-email` error path.
7. Add security-headers middleware (HSTS, nosniff, frame-options, referrer-policy).
8. Add `FAQPage` JSON-LD to homepage (rich results).
9. Add `Organization` + `SoftwareApplication`/`Offer` JSON-LD.
10. Expand sitemap.xml to include `/trending`, `/vs/*`, `/terms`, `/privacy`.
11. Add `noindex` + robots `Disallow` for `/admin`, `/checkout`, `/choose`, `/success`, `/billing-portal`, `/verify-email`.
12. Add `noindex` to `/demo/*` (except curated) and `/login`.
13. Add GA4 or Plausible + verify Google Search Console.
14. Add a skip-to-content link on all pages.
15. Add visible focus rings (replace `outline:none`-only styling).
16. Add `<label>`s to the domain input and signup email field.
17. Fix `/trending` to hide "N/A roles / ✓ Hiring" contradictions.
18. Add per-IP Redis rate limit to `/demo`.
19. Add `GZipMiddleware`.
20. Add `Cache-Control` headers to favicon/og-image responses.
21. Reduce hard-coded `asyncio.sleep(2)` to a selector/network-idle wait.
22. Add meta description + canonical to `/trending` and `/login`.
23. Add a LinkedIn link in the footer (B2B audience).
24. Add contrast fixes for `#4b5563`/`#555` body text.
25. Pin dependency versions and run `pip-audit`.

---

## 16. 30-Day Improvement Plan

**Week 1 — Trust & correctness:** Fix all copy contradictions (deterministic claim, 25/10 limit). Fix `/trending` data filtering and add domain↔company-name validation. Add `/register` redirect. Ship security-headers middleware + remove secret/PII logging.

**Week 2 — Cost & abuse control:** Move rate limiting to Redis (sliding window). Add per-IP `/demo` limits + Playwright concurrency semaphore + httpx-first scraping fallback. Add OpenAI spend alerting.

**Week 3 — Measurement:** Install GA4/Plausible + GTM, verify GSC, submit expanded sitemap. Add server-side `events` table and instrument the funnel. Add Sentry.

**Week 4 — Conversion:** Add social proof block + real inline homepage demo (gated). Build a proper `/signup` page. Add FAQ + Organization + SoftwareApplication schema. Add connection pooling.

---

## 17. 90-Day Improvement Plan

- **Refactor the monolith** into modules + Jinja2 templates; add a test suite (validate_domain, quota, webhook, rate limit) and CI.
- **Implement genuinely deterministic tech detection** (Wappalyzer-style fingerprint ruleset on homepage HTML) so the marketing claim can become true and `/vs/*` pages are credible.
- **Adopt Alembic migrations**; retire the dual `api_key`/`"key"` column.
- **Build the SEO content engine:** expand `/vs/*` into full comparison articles + a blog targeting "hiring intent data," "tech stack lookup API," "BuiltWith alternative," each with schema and internal linking.
- **Add About/Trust, Status, and Changelog pages.**
- **CRO experiments** via the new analytics: pricing toggle, inline demo, social proof, free-tier framing.
- **List on Product Hunt, G2, RapidAPI**; add review capture.

---

## 18. Long-Term Roadmap

- Real deterministic enrichment pipeline (job-board APIs + HTML fingerprinting) reducing OpenAI dependence and cost per lookup.
- Webhooks/outbound notifications for hiring-intent changes (a genuine "signal" product, not just lookups) — the differentiated, sticky feature.
- CRM-native integrations (HubSpot, Salesforce, Clay) as marketplace listings.
- SOC-2 lite trust posture (security page, DPA, subprocessors) to unlock larger accounts.
- Usage-based/overage billing and team seats.
- Data-quality monitoring dashboard (accuracy sampling) to back accuracy claims with evidence.

---

## 19. Prioritized Checklist (Impact vs Effort)

| # | Action | Impact | Effort | Priority |
|---|---|---|---|---|
| 1 | Fix `/trending` wrong-company data + name validation | Critical | Med | **Do now** |
| 2 | Replace false "deterministic/100%" claims | Critical | Low | **Do now** |
| 3 | Rate-limit + gate `/demo` scraping (cost/abuse) | Critical | Med | **Do now** |
| 4 | Resolve free-tier 25-vs-10 everywhere | High | Low | **Do now** |
| 5 | `/register` redirect | High | Trivial | **Do now** |
| 6 | Remove secret/PII logging + generic error pages | High | Low | **Do now** |
| 7 | Security headers + HSTS + basic CSP | High | Low-Med | **Do now** |
| 8 | Install analytics + GSC + expanded sitemap | High | Low | Week 1 |
| 9 | Redis-backed rate limiting | High | Med | Week 1-2 |
| 10 | DB connection pooling | High | Med | Week 1-2 |
| 11 | FAQ/Org/SoftwareApplication schema | Med-High | Low | Week 1-2 |
| 12 | noindex transactional/demo/login routes | Med | Low | Week 1 |
| 13 | Accessibility: skip link, labels, focus, contrast | Med | Med | Week 2-3 |
| 14 | Social proof + inline demo + real signup page | High | Med | Week 3-4 |
| 15 | httpx-first scraping + browser pooling + drop sleep(2) | Med-High | Med | Week 2-3 |
| 16 | Refactor monolith + tests + Alembic | High (long-term) | High | 90-day |
| 17 | Real deterministic tech detection | High | High | 90-day |
| 18 | SEO content engine (`/vs/*` + blog) | High | High | 90-day |
| 19 | CSRF tokens on admin mutations | Med | Low-Med | Month 2 |
| 20 | Status page + Sentry + backups verification | Med | Low | Month 1-2 |

---

*End of audit. The three fastest, highest-leverage moves: (1) fix the wrong data on `/trending`, (2) tell the truth about how tech detection works, and (3) put a rate limit + gate on the public `/demo` scraper before it costs you money or uptime. Everything else compounds once those trust and cost foundations are solid.*
