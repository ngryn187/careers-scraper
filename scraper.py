import asyncio
import html
import re
import ipaddress
import json
import os
import secrets
import socket
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import openai
import psycopg2
import pyotp
import redis as redis_lib
import stripe
import uvicorn
from fastapi import BackgroundTasks, Cookie, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from playwright.async_api import async_playwright

VERSION = "9.7.4"

#  Config 
openai.api_key = os.environ.get("OPENAI_API_KEY", "")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
ZOHO_EMAIL = os.environ.get("ZOHO_EMAIL", "")
ZOHO_PASSWORD = os.environ.get("ZOHO_PASSWORD", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379")
CRON_SECRET = os.environ.get("CRON_SECRET", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
TOTP_SECRET = os.environ.get("TOTP_SECRET", "")
BASE_URL = os.environ.get("BASE_URL", "https://stacksight.org")

STRIPE_PRICES = {
    "starter": os.environ.get("STRIPE_PRICE_STARTER", ""),
    "pro": os.environ.get("STRIPE_PRICE_PRO", "price_1TrLQ6DUssNU8xAWD0eyqLx4"),
    "business": os.environ.get("STRIPE_PRICE_BUSINESS", "price_1TrLceDUssNU8xAWKWUSPLlR"),
}
PLAN_LIMITS = {"free": 25, "starter": 500, "pro": 5000, "business": 50000}

#  Rate limiting 
_rate_limit: dict = {}
RATE_LIMIT_WINDOW = 60
RATE_LIMITS = {"free": 10, "starter": 60, "pro": 300, "business": 1000}

#  Redis / App 
redis_client = redis_lib.from_url(REDIS_URL, decode_responses=True)
app = FastAPI(title="StackSight API", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)

#  Demo data 
DEMO_DATA = {
    "stripe.com": {"company_name": "Stripe", "is_hiring": True, "engineering_roles": ["Backend Engineer", "ML Engineer", "Platform Engineer"], "sales_roles": ["Account Executive", "Solutions Engineer"], "detected_tech_stack": ["React", "AWS", "Stripe", "Cloudflare", "Sentry"]},
    "openai.com": {"company_name": "OpenAI", "is_hiring": True, "engineering_roles": ["Research Engineer", "Infrastructure Engineer", "Safety Engineer"], "sales_roles": ["Enterprise Account Executive"], "detected_tech_stack": ["Python", "Kubernetes", "Azure", "React", "PostgreSQL"]},
    "notion.so": {"company_name": "Notion", "is_hiring": True, "engineering_roles": ["Frontend Engin@app.get("/choose/pro",@app.get("/choose/business", response_class=HTMLResponse)
async def choose_business():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Business Plan - Choose Billing | StackSight</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0a0a;color:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;background-image:radial-gradient(ellipse at 50% 0%,rgba(124,58,237,0.12) 0%,transparent 60%)}
.wrap{max-width:520px;width:100%;text-align:center}
.logo{font-size:22px;font-weight:800;color:#7c3aed;text-decoration:none;letter-spacing:-0.5px;display:inline-block;margin-bottom:48px}
.plan-tag{display:inline-block;background:rgba(124,58,237,0.15);color:#a78bfa;font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;padding:4px 14px;border-radius:20px;border:1px solid rgba(124,58,237,0.3);margin-bottom:20px}
h1{font-size:30px;font-weight:700;letter-spacing:-0.5px;margin-bottom:10px}
.sub{color:#6b7280;font-size:15px;margin-bottom:40px}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:28px}
.card{border:1.5px solid #1f1f1f;border-radius:18px;padding:28px 20px;text-decoration:none;color:#fff;display:block;transition:all .2s;position:relative;background:#111}
.card:hover{border-color:#4c1d95;background:#130f1e;transform:translateY(-2px)}
.card.best{border-color:#7c3aed;background:linear-gradient(135deg,#130f1e 0%,#1a1033 100%);box-shadow:0 0 32px rgba(124,58,237,0.2)}
.card.best:hover{box-shadow:0 0 48px rgba(124,58,237,0.3);transform:translateY(-2px)}
.best-badge{position:absolute;top:-13px;left:50%;transform:translateX(-50%);background:linear-gradient(90deg,#7c3aed,#a855f7);color:#fff;font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;padding:4px 14px;border-radius:20px;white-space:nowrap}
.card-label{font-size:11px;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:#6b7280;margin-bottom:14px}
.card.best .card-label{color:#a78bfa}
.price{font-size:40px;font-weight:800;letter-spacing:-1px;line-height:1}
.price span{font-size:15px;font-weight:400;color:#6b7280}
.card.best .price span{color:#a78bfa}
.price-sub{font-size:12px;color:#4b5563;margin-top:8px}
.card.best .price-sub{color:#7c3aed}
.save{display:inline-block;background:rgba(34,197,94,0.12);color:#22c55e;font-size:11px;font-weight:700;padding:3px 10px;border-radius:10px;margin-top:10px;border:1px solid rgba(34,197,94,0.2)}
.cancel{font-size:12px;color:#4b5563;margin-top:10px}
.back{color:#4b5563;font-size:13px;text-decoration:none;transition:color .2s}
.back:hover{color:#9ca3af}
@media(max-width:400px){.cards{grid-template-columns:1fr}.price{font-size:32px}}
</style>
</head>
<body>
<div class="wrap">
  <a href="/" class="logo">StackSight</a>
  <div class="plan-tag">Business Plan</div>
  <h1>Choose your billing</h1>
  <p class="sub">Same features, same API. Pick what works for you.</p>
  <div class="cards">
    <a href="/checkout/business" class="card">
      <div class="card-label">Monthly</div>
      <div class="price">$199<span>/mo</span></div>
      <div class="price-sub">billed monthly</div>
      <div class="cancel">Cancel anytime</div>
    </a>
    <a href="/checkout/business_annual" class="card best">
      <div class="best-badge">BEST VALUE</div>
      <div class="card-label">Annual</div>
      <div class="price">$166<span>/mo</span></div>
      <div class="price-sub">billed $1,992/yr</div>
      <div class="save">Save 20%</div>
    </a>
  </div>
  <a href="/#pricing" class="back">← Back to pricing</a>
</div>
</body></html>""")

@app.get("/choose/business", response_class=HTMLResponse)
async def choose_business():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Choose Billing - StackSight</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0a0a0a;color:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px}}
.container{{max-width:600px;width:100%;text-align:center}}
.logo{{font-size:24px;font-weight:700;color:#7c3aed;margin-bottom:40px;text-decoration:none;display:block}}
h1{{font-size:28px;font-weight:700;margin-bottom:8px}}
p{{color:#aaa;margin-bottom:40px}}
.options{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}}
.option{{border:2px solid #222;border-radius:16px;padding:28px 20px;cursor:pointer;transition:border-color .2s;text-decoration:none;color:#fff;display:block}}
.option:hover{{border-color:#7c3aed}}
.option.recommended{{border-color:#7c3aed;position:relative}}
.badge{{position:absolute;top:-12px;left:50%;transform:translateX(-50%);background:#7c3aed;color:#fff;font-size:11px;font-weight:600;padding:3px 12px;border-radius:20px;white-space:nowrap}}
.option-label{{font-size:13px;color:#aaa;margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em}}
.option-price{{font-size:36px;font-weight:700;margin-bottom:4px}}
.option-price span{{font-size:16px;font-weight:400;color:#aaa}}
.option-sub{{font-size:12px;color:#aaa;margin-top:6px}}
.option-save{{color:#22c55e;font-size:12px;font-weight:600;margin-top:4px}}
.back{{color:#aaa;font-size:14px;text-decoration:none}}
.back:hover{{color:#fff}}
</style>
</head>
<body>
<div class="container">
  <a href="/" class="logo">StackSight</a>
  <h1>Choose your billing</h1>
  <p>Same features, same API. Pick what works for you.</p>
  <div class="options">
    <a href="/checkout/business" class="option">
      <div class="option-label">Monthly</div>
      <div class="option-price">$199<span>/mo</span></div>
      <div class="option-sub">Cancel anytime</div>
    </a>
    <a href="/checkout/business_annual" class="option recommended">
      <div class="badge">BEST VALUE</div>
      <div class="option-label">Annual</div>
      <div class="option-price">$166<span>/mo</span></div>
      <div class="option-sub">billed $1,992/yr</div>
      <div class="option-save">Save 20%</div>
    </a>
  </div>
  <a href="/#pricing" class="back">← Back to pricing</a>
</div>
</body></html>""")

@app.get("/checkout/{plan}")
async def checkout(plan: str):
    if plan not in STRIPE_PRICES:
        raise HTTPException(status_code=400, detail="Invalid plan")
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{"price": STRIPE_PRICES[plan], "quantity": 1}],
        mode="subscription",
        success_url=f"{BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{BASE_URL}/#pricing",
        metadata={"plan": plan},
    )
    return RedirectResponse(session.url)


@app.get("/success", response_class=HTMLResponse)
async def success():
    return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Payment Successful - StackSight</title>
<style>body{font-family:-apple-system,sans-serif;background:#0a0a0a;color:#e5e5e5;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:48px;text-align:center;max-width:500px}
h1{color:#22c55e;margin-bottom:12px}p{color:#b0b0b0;margin-bottom:24px}
a{background:#a855f7;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;font-weight:600}</style></head>
<body><div class="box">
<h1>Payment Successful!</h1>
<p>Your API key is being generated and will arrive in your inbox within a minute.</p>
<a href="/login">Sign In to Dashboard</a>
</div></body></html>""")


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature")
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        email = session.get("customer_details", {}).get("email") or session.get("customer_email")
        plan = session.get("metadata", {}).get("plan", "pro")
        customer_id = session.get("customer")
        session_id = session.get("id")
        if email:
            background_tasks.add_task(provision_api_key, email, plan, customer_id, session_id)

    elif event["type"] == "invoice.payment_succeeded":
        # Subscription renewed -- reset usage for this customer
        invoice = event["data"]["object"]
        customer_id = invoice.get("customer")
        billing_reason = invoice.get("billing_reason", "")
        # Only reset on renewals, not the initial payment (that's handled by checkout.session.completed)
        if customer_id and billing_reason == "subscription_cycle":
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                "UPDATE api_keys SET requests_used = 0, usage_reset_at = NOW() WHERE stripe_customer_id = %s AND active = TRUE",
                (customer_id,)
            )
            conn.commit()
            cur.close(); conn.close()

    elif event["type"] == "customer.subscription.deleted":
        # Subscription cancelled -- downgrade to free
        subscription = event["data"]["object"]
        customer_id = subscription.get("customer")
        if customer_id:
            conn = get_db()
            cur = conn.cursor()
            cur.execute(
                "UPDATE api_keys SET plan = 'free', requests_limit = 10, requests_used = 0 WHERE stripe_customer_id = %s",
                (customer_id,)
            )
            conn.commit()
            cur.close(); conn.close()

    elif event["type"] == "invoice.payment_failed":
        # Payment failed -- leave access for now but could notify user
        # Stripe will retry; subscription.deleted fires if all retries fail
        pass

    return {"status": "ok"}


@app.get("/trending")
async def trending():
    results = []
    for domain, data in DEMO_DATA.items():
        results.append({
            "domain": domain,
            "company_name": data.get("company_name", domain.split(".")[0].title()),
            "is_hiring": data.get("is_hiring", False),
            "engineering_roles": data.get("engineering_roles", []),
            "sales_roles": data.get("sales_roles", []),
            "detected_tech_stack": data.get("detected_tech_stack", []),
            "open_roles": len(data.get("engineering_roles", [])) + len(data.get("sales_roles", []))
        })
    # Sort by open roles descending
    results.sort(key=lambda x: x["open_roles"], reverse=True)
    return {"trending": results, "count": len(results)}


@app.get("/terms", response_class=HTMLResponse)
async def terms():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Terms of Service - StackSight</title>
<meta name="description" content="Terms of Service for the StackSight API - B2B hiring intent signals and tech stack detection.">
<meta name="robots" content="index, follow">
<link rel="canonical" href="https://stacksight.org/terms">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;line-height:1.6}
nav{padding:18px 40px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1a1a1a;position:sticky;top:0;background:rgba(10,10,10,0.95);backdrop-filter:blur(10px);z-index:100}
.logo{font-size:22px;font-weight:700;color:#a855f7;text-decoration:none}
.nav-links a{color:#c0c0c0;text-decoration:none;margin-left:24px;font-size:14px;transition:color .2s}
.nav-links a:hover{color:#fff}
.nav-links .btn-login{background:#1a1a1a;border:1px solid #333;color:#fff;padding:7px 16px;border-radius:7px;font-weight:500}
.container{max-width:760px;margin:0 auto;padding:60px 24px 90px}
h1{font-size:38px;font-weight:800;letter-spacing:-1px;margin-bottom:10px}
h1 span{color:#a855f7}
.updated{color:#888;font-size:14px;margin-bottom:44px}
h2{font-size:20px;font-weight:700;letter-spacing:-0.5px;margin:40px 0 14px;color:#f5f5f5}
p{color:#b0b0b0;margin-bottom:16px;line-height:1.75}
ul{color:#b0b0b0;padding-left:22px;margin-bottom:16px;line-height:1.75}
ul li{margin-bottom:8px}
a{color:#a855f7}
strong{color:#e5e5e5}
code{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:5px;padding:1px 6px;font-size:13px;color:#c084fc}
footer{border-top:1px solid #1a1a1a;padding:32px 20px;text-align:center;color:#666;font-size:13px}
footer a{color:#888;text-decoration:none}
footer a:hover{color:#fff}
@media(max-width:640px){nav{padding:14px 20px}h1{font-size:30px}}
</style>
</head>
<body>
<nav>
  <a href="/" class="logo">StackSight</a>
  <div class="nav-links">
    <a href="/docs">Docs</a>
    <a href="/demo/stripe.com">Demo</a>
    <a href="/#pricing">Pricing</a>
    <a href="/login" class="btn-login" id="nav-auth-btn">Sign In</a>
  </div>
</nav>
<script>
(function(){
  fetch("/usage",{credentials:"include"}).then(r=>{
    if(r.ok){r.json().then(d=>{
      var btn=document.getElementById("nav-auth-btn");
      if(btn){btn.textContent="My Account";btn.href="/dashboard";}
    });}
  }).catch(function(){});
})();
</script>
<div class="container">
<h1>Terms of <span>Service</span></h1>
<p class="updated">Effective date: July 1, 2026 &nbsp;&middot;&nbsp; Last updated: July 18, 2026</p>

<h2>1. Acceptance of Terms</h2>
<p>These Terms of Service ("Terms") are a binding agreement between you and StackSight ("we", "us"), the operator of the API service available at <a href="https://stacksight.org">stacksight.org</a>. By creating an account, requesting an API key, or sending a request to any StackSight API endpoint, you agree to these Terms. If you are using StackSight on behalf of a company, you represent that you have authority to bind that company. If you do not agree to these Terms, do not use the service.</p>

<h2>2. Description of Service</h2>
<p>StackSight is a REST API that provides B2B sales intelligence derived from publicly available information. When you query a company domain, our systems retrieve that company's publicly accessible careers and website pages and return structured data including:</p>
<ul>
<li>Hiring signals -- whether the company appears to be actively hiring, and open engineering and sales roles detected on its public careers page</li>
<li>Tech stack detection -- technologies identified from public job postings and website signals</li>
<li>Bulk domain enrichment for lists of domains, subject to your plan's limits</li>
</ul>
<p>StackSight only processes information that companies have chosen to publish on the public internet. We do not access private systems, authenticated pages, or paywalled content. There is no mobile app and no user-generated content -- the service is API access plus a web dashboard for managing your account.</p>

<h2>3. API Usage & Rate Limits</h2>
<p>Access requires an API key tied to a plan:</p>
<ul>
<li><strong>Free</strong> -- 25 requests, no credit card required</li>
<li><strong>Pro</strong> -- $49/month, 5,000 requests per month</li>
<li><strong>Business</strong> -- $199/month, 50,000 requests per month</li>
</ul>
<p>When you reach your plan's limit, further requests return HTTP <code>429</code> until your quota resets at the start of your next billing period or you upgrade. We do not silently bill overages -- requests beyond your quota are rejected, not charged. We may also apply short-window rate limits (requests per minute) to protect service stability; these are documented in the <a href="/docs">API docs</a>.</p>

<h2>4. Prohibited Uses</h2>
<p>You agree not to:</p>
<ul>
<li>Use StackSight to build, train, or populate a competing hiring-signals or tech-stack-detection service</li>
<li>Resell, redistribute, or publish raw API responses without meaningful transformation or added value (using the data inside your own product, CRM, or workflow is fine)</li>
<li>Use the data for spam, unsolicited bulk messaging, harassment, stalking, or discrimination</li>
<li>Share, sell, or publish your API key, or make requests on behalf of third parties who do not have their own account</li>
<li>Circumvent rate limits or quotas, including by creating multiple free accounts, rotating keys, or spoofing requests</li>
<li>Use the service to violate any law, regulation, or the rights of any person or company, or direct us to target sites in a manner you know violates a target site's terms of service</li>
<li>Probe, scan, or attempt to gain unauthorized access to StackSight's systems or other users' data</li>
</ul>

<h2>5. Payment & Billing</h2>
<p>Paid plans are billed monthly in advance through <strong>Stripe</strong>, our payment processor. We never see or store your card number. Subscriptions renew automatically each month until cancelled. You may cancel at any time from your dashboard or by emailing <a href="mailto:support@stacksight.org">support@stacksight.org</a>; cancellation takes effect at the end of the current billing period and you retain access until then. We do not provide refunds for billing periods that have already started or for unused requests, except where required by law. Prices may change with at least 30 days' notice before the change applies to your subscription.</p>

<h2>6. API Key Security</h2>
<p>Your API key is a credential. You are responsible for keeping it secret and for all requests made with it, whether or not you authorized them. Do not embed your key in client-side code, public repositories, or shared documents. If you believe your key has been compromised, email <a href="mailto:support@stacksight.org">support@stacksight.org</a> immediately and we will revoke and reissue it. Usage incurred before revocation counts against your quota.</p>

<h2>7. Data & Privacy</h2>
<p>Our collection and handling of your personal data (email address, usage data, IP address) is described in our <a href="/privacy">Privacy Policy</a>, which is incorporated into these Terms by reference.</p>

<h2>8. Intellectual Property</h2>
<p>StackSight owns the service, including the platform, software, API design, documentation, branding, and the systems that generate our data. These Terms grant you a limited, non-exclusive, non-transferable license to use the API and its output for your internal business purposes while your account is in good standing. You own the derived works you create from API output -- enriched CRM records, reports, scoring models, and similar transformations are yours. The underlying facts returned by the API (a company's public job postings and technologies) are public information and are not claimed as proprietary by either party.</p>

<h2>9. Disclaimer of Warranties</h2>
<p>THE SERVICE AND ALL DATA ARE PROVIDED "AS IS" AND "AS AVAILABLE", WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT. Data is extracted from public sources that change without notice; we do not warrant that it is accurate, complete, or current, and you should treat it as a signal rather than a source of truth. We do not guarantee uninterrupted or error-free operation, and we may modify or discontinue features with reasonable notice.</p>

<h2>10. Limitation of Liability</h2>
<p>TO THE MAXIMUM EXTENT PERMITTED BY LAW, STACKSIGHT WILL NOT BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, OR FOR LOST PROFITS, REVENUE, DATA, OR BUSINESS OPPORTUNITIES, ARISING FROM OR RELATED TO YOUR USE OF THE SERVICE. OUR TOTAL AGGREGATE LIABILITY FOR ANY CLAIM IS LIMITED TO THE AMOUNT YOU PAID US IN THE TWELVE (12) MONTHS BEFORE THE CLAIM AROSE, OR $100 IF YOU ARE ON THE FREE PLAN. Some jurisdictions do not allow certain limitations, so parts of this section may not apply to you.</p>

<h2>11. Termination</h2>
<p>We may suspend or terminate your account and API access, with or without notice, if you violate these Terms, abuse the service, engage in fraud, or fail to pay. You may stop using the service and cancel at any time. Sections 8 through 10 survive termination. Upon termination we handle your data as described in the <a href="/privacy">Privacy Policy</a>.</p>

<h2>12. Changes to Terms</h2>
<p>We may update these Terms from time to time. For material changes we will notify active paying customers by email at least 14 days before the changes take effect, and we will update the date at the top of this page. Continued use of the service after changes take effect constitutes acceptance.</p>

<h2>13. Contact</h2>
<p>Questions about these Terms? Email <a href="mailto:support@stacksight.org">support@stacksight.org</a>.</p>
</div>
<footer>
  <div style="margin-bottom:14px;font-size:16px;font-weight:700;color:#a855f7;letter-spacing:-0.5px">Stack<span style="color:#e5e5e5">Sight</span></div>
  <div style="margin-bottom:12px">
    <a href="/docs">Docs</a> &nbsp;&nbsp; <a href="/demo/stripe.com">Demo</a> &nbsp;&nbsp; <a href="/#pricing">Pricing</a> &nbsp;&nbsp; <a href="/login">Sign In</a> &nbsp;&nbsp; <a href="mailto:support@stacksight.org">Contact</a>
  </div>
  <div style="margin-bottom:8px">
    <a href="/terms">Terms of Service</a> &nbsp;&nbsp; <a href="/privacy">Privacy Policy</a>
  </div>
  <div>&copy; 2026 StackSight &nbsp;&nbsp; <a href="https://x.com/StackSightOrg">@StackSightOrg</a></div>
</footer>
</body></html>""")


@app.get("/privacy", response_class=HTMLResponse)
async def privacy():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Privacy Policy - StackSight</title>
<meta name="description" content="Privacy Policy for StackSight - what we collect, what we don't, and how we protect your data.">
<meta name="robots" content="index, follow">
<link rel="canonical" href="https://stacksight.org/privacy">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;line-height:1.6}
nav{padding:18px 40px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #1a1a1a;position:sticky;top:0;background:rgba(10,10,10,0.95);backdrop-filter:blur(10px);z-index:100}
.logo{font-size:22px;font-weight:700;color:#a855f7;text-decoration:none}
.nav-links a{color:#c0c0c0;text-decoration:none;margin-left:24px;font-size:14px;transition:color .2s}
.nav-links a:hover{color:#fff}
.nav-links .btn-login{background:#1a1a1a;border:1px solid #333;color:#fff;padding:7px 16px;border-radius:7px;font-weight:500}
.container{max-width:760px;margin:0 auto;padding:60px 24px 90px}
h1{font-size:38px;font-weight:800;letter-spacing:-1px;margin-bottom:10px}
h1 span{color:#a855f7}
.updated{color:#888;font-size:14px;margin-bottom:44px}
h2{font-size:20px;font-weight:700;letter-spacing:-0.5px;margin:40px 0 14px;color:#f5f5f5}
p{color:#b0b0b0;margin-bottom:16px;line-height:1.75}
ul{color:#b0b0b0;padding-left:22px;margin-bottom:16px;line-height:1.75}
ul li{margin-bottom:8px}
a{color:#a855f7}
strong{color:#e5e5e5}
code{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:5px;padding:1px 6px;font-size:13px;color:#c084fc}
footer{border-top:1px solid #1a1a1a;padding:32px 20px;text-align:center;color:#666;font-size:13px}
footer a{color:#888;text-decoration:none}
footer a:hover{color:#fff}
@media(max-width:640px){nav{padding:14px 20px}h1{font-size:30px}}
</style>
</head>
<body>
<nav>
  <a href="/" class="logo">StackSight</a>
  <div class="nav-links">
    <a href="/docs">Docs</a>
    <a href="/demo/stripe.com">Demo</a>
    <a href="/#pricing">Pricing</a>
    <a href="/login" class="btn-login" id="nav-auth-btn">Sign In</a>
  </div>
</nav>
<script>
(function(){
  fetch("/usage",{credentials:"include"}).then(r=>{
    if(r.ok){r.json().then(d=>{
      var btn=document.getElementById("nav-auth-btn");
      if(btn){btn.textContent="My Account";btn.href="/dashboard";}
    });}
  }).catch(function(){});
})();
</script>
<div class="container">
<h1>Privacy <span>Policy</span></h1>
<p class="updated">Effective date: July 1, 2026 &nbsp;&middot;&nbsp; Last updated: July 18, 2026</p>

<p>StackSight is an API service, not an ad-funded product. We collect the minimum data needed to run the service, and this policy explains exactly what that is.</p>

<h2>1. What We Collect</h2>
<ul>
<li><strong>Email address</strong> -- required to create an account. We use passwordless magic link authentication, so your email is your identity. There are no passwords for us to store or leak.</li>
<li><strong>API key</strong> -- generated when you sign up, stored securely on our servers, and used only to authenticate your API requests.</li>
<li><strong>Usage counts</strong> -- the number of API requests you have used in the current billing period, so we can enforce plan quotas and show usage on your dashboard.</li>
<li><strong>IP address</strong> -- logged with requests for rate limiting, abuse prevention, and security investigation.</li>
<li><strong>Payment information</strong> -- handled entirely by <strong>Stripe</strong>. Your card number never touches our servers; we receive only a Stripe customer reference and subscription status.</li>
</ul>

<h2>2. What We Don't Collect</h2>
<ul>
<li>No tracking pixels</li>
<li>No third-party analytics (no Google Analytics, no Meta Pixel, nothing)</li>
<li>No advertising identifiers</li>
<li>No cookies beyond a single session cookie (<code>ss_session</code>) used solely to keep you signed in to the dashboard</li>
</ul>

<h2>3. How We Use Your Data</h2>
<ul>
<li>To deliver the API service and enforce your plan's request limits</li>
<li>To send magic link sign-in emails and essential transactional emails (API key delivery, billing notices)</li>
<li>To process subscription payments through Stripe</li>
<li>To detect and prevent abuse, fraud, and attempts to circumvent rate limits</li>
</ul>
<p>That's the full list. We do not use your data for advertising, profiling, or anything else.</p>

<h2>4. Data Retention</h2>
<ul>
<li><strong>Magic links</strong> expire 15 minutes after they are sent</li>
<li><strong>Session tokens</strong> expire after 7 days</li>
<li><strong>Rate-limit counters</strong> are ephemeral data held in Redis and expire automatically</li>
<li><strong>Account data</strong> (email, API key, usage history, billing records) is kept while your account is active, and for 90 days after account deletion, after which it is permanently purged. We may retain billing records longer where tax or accounting law requires.</li>
</ul>

<h2>5. Third Parties</h2>
<p>We share data only with the infrastructure providers needed to run the service:</p>
<ul>
<li><strong>Stripe</strong> -- payment processing (<a href="https://stripe.com/privacy">Stripe's privacy policy</a>)</li>
<li><strong>SendGrid / SMTP provider</strong> -- delivery of transactional email only (magic links, receipts). We never send marketing blasts through it without your consent.</li>
<li><strong>Railway</strong> -- hosting infrastructure where the application and database run</li>
</ul>
<p>No advertising networks. No data brokers. No one else.</p>

<h2>6. We Do Not Sell Your Data</h2>
<p>We do not sell, rent, or trade your personal information to anyone, for any purpose. The data our API returns about companies is derived from publicly available web pages and does not include our users' personal data.</p>

<h2>7. Your Rights</h2>
<p>You can access, correct, or delete your personal data at any time. To delete your account, email <a href="mailto:support@stacksight.org">support@stacksight.org</a> from your account email address and we will purge your data within 30 days, subject to the retention rules in Section 4. Depending on where you live (e.g. the EU/UK under GDPR, or California under CCPA), you may have additional statutory rights; email us and we will honor them.</p>

<h2>8. Security</h2>
<ul>
<li>API keys are stored securely and used only to authenticate API requests</li>
<li>All traffic is served over HTTPS only</li>
<li>Session cookies are secure and HttpOnly</li>
<li>Rate-limit and abuse-prevention data lives in Redis and expires automatically</li>
</ul>
<p>No system is perfectly secure, but if we ever discover a breach affecting your personal data, we will notify you by email without undue delay.</p>

<h2>9. Changes to This Policy</h2>
<p>If we make material changes to this policy, we will notify account holders by email before the changes take effect and update the date at the top of this page.</p>

<h2>10. Contact</h2>
<p>Privacy questions or requests: <a href="mailto:support@stacksight.org">support@stacksight.org</a></p>
</div>
<footer>
  <div style="margin-bottom:14px;font-size:16px;font-weight:700;color:#a855f7;letter-spacing:-0.5px">Stack<span style="color:#e5e5e5">Sight</span></div>
  <div style="margin-bottom:12px">
    <a href="/docs">Docs</a> &nbsp;&nbsp; <a href="/demo/stripe.com">Demo</a> &nbsp;&nbsp; <a href="/#pricing">Pricing</a> &nbsp;&nbsp; <a href="/login">Sign In</a> &nbsp;&nbsp; <a href="mailto:support@stacksight.org">Contact</a>
  </div>
  <div style="margin-bottom:8px">
    <a href="/terms">Terms of Service</a> &nbsp;&nbsp; <a href="/privacy">Privacy Policy</a>
  </div>
  <div>&copy; 2026 StackSight &nbsp;&nbsp; <a href="https://x.com/StackSightOrg">@StackSightOrg</a></div>
</footer>
</body></html>""")


@app.get("/health")
async def health():
    try:
        redis_client.ping(); redis_ok = True
    except Exception:
        redis_ok = False
    return {"status": "ok", "version": VERSION, "redis": redis_ok}



ADMIN_EMAIL = "ngrynai@gmail.com"

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, pw: str = None, totp: str = None):
    email = get_session_email(request)
    # 404 for anyone not logged in as owner
    if email != ADMIN_EMAIL:
        raise HTTPException(status_code=404)
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin not configured")
    # Require admin password + TOTP as two factors
    _admin_token = request.cookies.get("admin_verified")
    admin_verified = bool(_admin_token and redis_client.get(f"admin_session:{_admin_token}"))
    if not admin_verified:
        ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown").split(",")[0].strip()
        lockout_key = f"admin_lockout:{ip}"
        fail_key = f"admin_fails:{ip}"
        if redis_client.get(lockout_key):
            return HTMLResponse("<!DOCTYPE html><html><body style='font-family:sans-serif;background:#0a0a0a;color:#e5e5e5;display:flex;align-items:center;justify-content:center;height:100vh;margin:0'><div style='text-align:center'><h1 style='color:#333;font-size:48px'>404</h1><p style='color:#888'>Page not found</p></div></body></html>", status_code=404)
        pw_correct = pw == ADMIN_PASSWORD
        totp_ok = totp is not None and TOTP_SECRET and pyotp.TOTP(TOTP_SECRET).verify(totp, valid_window=1)
        if not (pw_correct and totp_ok):
            if pw is not None or totp is not None:
                fails = redis_client.incr(fail_key)
                redis_client.expire(fail_key, 900)
                if fails >= 5:
                    redis_client.setex(lockout_key, 900, "1")
            if pw_correct:
                # Password correct, ask for TOTP
                return HTMLResponse(f"""<!DOCTYPE html>
<html><head><title>Not Found</title></head>
<body style='font-family:sans-serif;background:#0a0a0a;color:#e5e5e5;display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>
<div style='text-align:center'>
  <h1 style='font-size:48px;color:#333'>404</h1>
  <p style='color:#888'>Page not found</p>
  <form method='get' style='margin-top:24px'>
    <input type='hidden' name='pw' value='{pw}'>
    <input name='totp' type='text' placeholder='Authenticator code' autofocus maxlength='6' inputmode='numeric'
      style='background:#111;border:1px solid #333;color:#fff;padding:10px 16px;border-radius:8px;font-size:15px;margin-right:8px;width:160px'>
    <button type='submit'
      style='background:#a855f7;color:#fff;border:none;padding:10px 20px;border-radius:8px;font-size:15px;cursor:pointer'>Verify</button>
  </form>
</div></body></html>""", status_code=404)
            else:
                return HTMLResponse("""<!DOCTYPE html>
<html><head><title>Not Found</title></head>
<body style='font-family:sans-serif;background:#0a0a0a;color:#e5e5e5;display:flex;align-items:center;justify-content:center;height:100vh;margin:0'>
<div style='text-align:center'>
  <h1 style='font-size:48px;color:#333'>404</h1>
  <p style='color:#888'>Page not found</p>
  <form method='get' style='margin-top:24px'>
    <input name='pw' type='password' placeholder='Password' autofocus
      style='background:#111;border:1px solid #333;color:#fff;padding:10px 16px;border-radius:8px;font-size:15px;margin-right:8px'>
    <button type='submit'
      style='background:#a855f7;color:#fff;border:none;padding:10px 20px;border-radius:8px;font-size:15px;cursor:pointer'>Enter</button>
  </form>
</div></body></html>""", status_code=404)
        # Both factors correct -- clear fail counter, set verified cookie
        redis_client.delete(fail_key)
        admin_token = secrets.token_hex(32)
        redis_client.setex(f"admin_session:{admin_token}", 3600, "1")
        response = HTMLResponse("")
        response.set_cookie("admin_verified", admin_token, httponly=True, secure=True, samesite="lax", max_age=3600)
        response.headers["Location"] = "/admin"
        response.status_code = 302
        return response
    conn = get_db()
    cur = conn.cursor()
    # Stats
    cur.execute("SELECT COUNT(*) FROM api_keys")
    total_users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM api_keys WHERE active=TRUE")
    active_keys = cur.fetchone()[0]
    cur.execute("SELECT COALESCE(SUM(requests_used),0) FROM api_keys")
    total_requests = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM sessions WHERE active=TRUE AND expires_at > NOW()")
    active_sessions = cur.fetchone()[0]
    # All users with keys
    cur.execute("""
        SELECT email, api_key, plan, requests_used, requests_limit, active, created_at
        FROM api_keys ORDER BY created_at DESC
    """)
    users = cur.fetchall()
    cur.close()
    conn.close()

    rows = ""
    for u in users:
        em, key, plan, used, limit, act, created = u
        pct = int((used/limit)*100) if limit else 0
        status_color = "#22c55e" if act else "#ef4444"
        status_text = "Active" if act else "Disabled"
        toggle_label = "Disable" if act else "Enable"
        toggle_color = "#ef4444" if act else "#22c55e"
        safe_key = key or ""
        rows += f"""<tr>
            <td>{html.escape(str(em))}</td>
            <td><code style="font-size:11px">{safe_key[:20] + "..." if safe_key else "None"}</code></td>
            <td><span class="badge badge-{plan}">{plan.upper()}</span></td>
            <td>{used} / {limit} <div class="bar"><div class="bar-fill" style="width:{pct}%"></div></div></td>
            <td><span style="color:{status_color}">{status_text}</span></td>
            <td>{str(created)[:10]}</td>
            <td style="white-space:nowrap">
              <button class="btn-toggle" data-email="{html.escape(str(em), quote=True)}" data-active="{str(act).lower()}" style="background:{toggle_color};color:#fff;border:none;padding:4px 10px;border-radius:6px;cursor:pointer;font-size:12px;margin-right:4px">{toggle_label}</button>
              <button class="btn-delete" data-email="{html.escape(str(em), quote=True)}" style="background:#333;color:#ef4444;border:1px solid #ef4444;padding:4px 10px;border-radius:6px;cursor:pointer;font-size:12px">Delete</button>
            </td>
        </tr>"""

    return HTMLResponse(f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>StackSight Admin</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e5e5e5;padding:32px}}
h1{{font-size:28px;font-weight:700;color:#a855f7;margin-bottom:8px}}
.sub{{color:#888;margin-bottom:32px;font-size:14px}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:40px}}
.stat{{background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:20px}}
.stat-value{{font-size:32px;font-weight:700;color:#a855f7}}
.stat-label{{color:#888;font-size:13px;margin-top:4px}}
table{{width:100%;border-collapse:collapse;background:#111;border-radius:12px;overflow:hidden}}
th{{background:#1a1a1a;padding:12px 16px;text-align:left;font-size:13px;color:#888;font-weight:500;border-bottom:1px solid #1f1f1f}}
td{{padding:12px 16px;font-size:14px;border-bottom:1px solid #1a1a1a}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:#141414}}
code{{font-size:12px;color:#a855f7;background:#1a0a2e;padding:2px 6px;border-radius:4px}}
.badge{{padding:3px 8px;border-radius:20px;font-size:11px;font-weight:600}}
.badge-free{{background:#1a1a1a;color:#888}}
.badge-pro{{background:#1a0a2e;color:#a855f7}}
.badge-business{{background:#0a1a2e;color:#3b82f6}}
.bar{{background:#1f1f1f;border-radius:4px;height:4px;margin-top:6px;width:120px}}
.bar-fill{{background:#a855f7;height:4px;border-radius:4px}}
.back{{color:#888;font-size:13px;text-decoration:none;display:inline-block;margin-bottom:24px}}
.back:hover{{color:#fff}}
</style></head>
<body>
<a href="/" class="back">-- Back to site</a>
<h1>Admin Dashboard</h1>
<p class="sub">Logged in as {email}</p>
<div class="stats">
  <div class="stat"><div class="stat-value">{total_users}</div><div class="stat-label">Total Users</div></div>
  <div class="stat"><div class="stat-value">{active_keys}</div><div class="stat-label">Active API Keys</div></div>
  <div class="stat"><div class="stat-value">{total_requests}</div><div class="stat-label">Total API Calls</div></div>
  <div class="stat"><div class="stat-value">{active_sessions}</div><div class="stat-label">Active Sessions</div></div>
</div>
<table>
  <thead><tr><th>Email</th><th>API Key</th><th>Plan</th><th>Usage</th><th>Status</th><th>Joined</th><th>Actions</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
<script>
async function toggleKey(btn) {{
  const email = btn.dataset.email;
  const currentlyActive = btn.dataset.active === 'true';
  btn.disabled = true;
  const r = await fetch('/admin/toggle-key', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{email, active: !currentlyActive}})}});
  if (r.ok) location.reload();
  else {{ alert('Error'); btn.disabled = false; }}
}}
async function deleteUser(btn) {{
  const email = btn.dataset.email;
  if (!confirm('Delete all data for ' + email + '?')) return;
  btn.disabled = true;
  const r = await fetch('/admin/delete-user', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{email}})}});
  if (r.ok) location.reload();
  else {{ alert('Error'); btn.disabled = false; }}
}}
document.querySelectorAll('.btn-toggle').forEach(b => b.addEventListener('click', () => toggleKey(b)));
document.querySelectorAll('.btn-delete').forEach(b => b.addEventListener('click', () => deleteUser(b)));
</script>
</body></html>""")

@app.post("/admin/toggle-key")
async def admin_toggle_key(request: Request):
    if get_session_email(request) != ADMIN_EMAIL:
        raise HTTPException(status_code=404)
    _tok = request.cookies.get("admin_verified")
    if not (_tok and redis_client.get(f"admin_session:{_tok}")):
        raise HTTPException(status_code=403)
    body = await request.json()
    email = body.get("email")
    active = body.get("active", True)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE api_keys SET active=%s WHERE email=%s", (active, email))
    conn.commit()
    cur.close(); conn.close()
    return {"ok": True}

@app.post("/admin/delete-user")
async def admin_delete_user(request: Request):
    if get_session_email(request) != ADMIN_EMAIL:
        raise HTTPException(status_code=404)
    _tok = request.cookies.get("admin_verified")
    if not (_tok and redis_client.get(f"admin_session:{_tok}")):
        raise HTTPException(status_code=403)
    body = await request.json()
    email = body.get("email")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM api_keys WHERE email=%s", (email,))
    cur.execute("DELETE FROM sessions WHERE email=%s", (email,))
    cur.execute("DELETE FROM magic_links WHERE email=%s", (email,))
    conn.commit()
    cur.close(); conn.close()
    return {"ok": True}

@app.post("/admin/reset-usage")
async def admin_reset_usage(request: Request):
    """Reset monthly quotas for paid users. Called by Railway cron / external scheduler."""
    if not CRON_SECRET:
        raise HTTPException(status_code=503, detail="CRON_SECRET not configured")
    auth = request.headers.get("X-Cron-Secret", "")
    if not secrets.compare_digest(auth, CRON_SECRET):
        raise HTTPException(status_code=403, detail="Forbidden")
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE api_keys
        SET requests_used = 0, usage_reset_at = NOW()
        WHERE plan != 'free'
          AND (usage_reset_at IS NULL OR usage_reset_at < NOW() - INTERVAL '30 days')
    """)
    reset_count = cur.rowcount
    conn.commit()
    cur.close(); conn.close()
    return {"ok": True, "keys_reset": reset_count}


@app.post("/admin/create-key")
async def admin_create_key(request: Request):
    if not CRON_SECRET:
        raise HTTPException(status_code=503, detail="CRON_SECRET not configured")
    auth = request.headers.get("X-Cron-Secret", "")
    if not secrets.compare_digest(auth, CRON_SECRET):
        raise HTTPException(status_code=403, detail="Forbidden")
    body = await request.json()
    email = body.get("email")
    plan = body.get("plan", "free")
    if not email:
        raise HTTPException(status_code=400, detail="Email required")
    key = provision_api_key(email, plan)
    return {"api_key": key, "plan": plan, "email": email}


if __name__ == "__main__":
    uvicorn.run("scraper:app", host="0.0.0.0", port=8000, reload=True)
 
