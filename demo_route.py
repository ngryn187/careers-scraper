from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx, os

BASE_URL = os.environ.get("BASE_URL", "https://stacksight.org")

def get_session_email(request: Request):
    token = request.cookies.get("session")
    if not token:
        return None
    # Import from main app
    from scraper import SESSIONS
    return SESSIONS.get(token)

async def demo_page(domain: str, request: Request):
    email = get_session_email(request)
    if not email:
        return RedirectResponse(f"/login?next=/demo/{domain}")

    # Call the live scrape endpoint internally
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{BASE_URL}/scrape", params={"domain": domain}, cookies=dict(request.cookies))
        if r.status_code == 200:
            result = r.json()
            data = result.get("data", {})
            source = result.get("source", "live")
        else:
            data = {}
            source = "error"
    except Exception as e:
        data = {}
        source = "error"

    company = data.get("company_name", domain)
    is_hiring = data.get("is_hiring", None)
    eng_roles = data.get("engineering_roles", [])
    sales_roles = data.get("sales_roles", [])
    tech_stack = data.get("detected_tech_stack", [])

    hiring_badge = ""
    if is_hiring is True:
        hiring_badge = '<span style="background:rgba(34,197,94,.15);color:#22c55e;border:1px solid rgba(34,197,94,.3);border-radius:6px;padding:4px 12px;font-size:13px;font-weight:600">&#10003; Actively Hiring</span>'
    elif is_hiring is False:
        hiring_badge = '<span style="background:rgba(239,68,68,.15);color:#ef4444;border:1px solid rgba(239,68,68,.3);border-radius:6px;padding:4px 12px;font-size:13px;font-weight:600">&#10007; Not Hiring</span>'
    else:
        hiring_badge = '<span style="background:rgba(156,163,175,.1);color:#9ca3af;border:1px solid #2a2a2a;border-radius:6px;padding:4px 12px;font-size:13px;font-weight:600">Unknown</span>'

    source_badge = "live" if source == "live" else "cached"
    eng_html = "".join(f'<li>{r}</li>' for r in eng_roles) if eng_roles else "<li style='color:#6b7280'>None detected</li>"
    sales_html = "".join(f'<li>{r}</li>' for r in sales_roles) if sales_roles else "<li style='color:#6b7280'>None detected</li>"
    tech_html = "".join(f'<span class="tag">{t}</span>' for t in tech_stack) if tech_stack else '<span style="color:#6b7280">None detected</span>'

    import json
    raw_json = json.dumps({"source": source, "data": data}, indent=2)

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Demo: {domain} - StackSight</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0a0a0a;color:#e5e7eb;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh}}
nav{{display:flex;align-items:center;justify-content:space-between;padding:16px 40px;border-bottom:1px solid #1a1a1a}}
.logo{{font-size:18px;font-weight:700;color:#fff;text-decoration:none}}.logo span{{color:#a855f7}}
.nav-links{{display:flex;gap:24px;align-items:center}}
.nav-links a{{color:#9ca3af;text-decoration:none;font-size:14px}}.nav-links a:hover{{color:#fff}}
.main{{max-width:860px;margin:0 auto;padding:48px 24px}}
.header{{margin-bottom:32px}}
.header h1{{font-size:28px;font-weight:700;color:#fff;margin-bottom:8px}}
.meta{{display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
.source-pill{{font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;padding:3px 10px;border-radius:20px;background:rgba(168,85,247,.1);color:#a855f7;border:1px solid rgba(168,85,247,.3)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px}}
.card{{background:#111;border:1px solid #1f1f1f;border-radius:12px;padding:24px}}
.card h3{{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#6b7280;margin-bottom:16px}}
.card ul{{list-style:none;display:flex;flex-direction:column;gap:8px}}
.card li{{color:#e5e7eb;font-size:14px;padding-left:12px;border-left:2px solid #a855f7}}
.tags{{display:flex;flex-wrap:wrap;gap:8px}}
.tag{{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:6px;padding:4px 12px;font-size:13px;color:#d1d5db}}
.full-width{{grid-column:1/-1}}
pre{{background:#0d0d0d;border:1px solid #1f1f1f;border-radius:10px;padding:20px;overflow-x:auto;font-family:'JetBrains Mono',monospace;font-size:13px;color:#a3e635;line-height:1.6}}
.cta{{margin-top:32px;background:linear-gradient(135deg,rgba(124,58,237,.15),rgba(168,85,247,.1));border:1px solid rgba(168,85,247,.3);border-radius:12px;padding:24px;display:flex;align-items:center;justify-content:space-between;gap:16px}}
.cta p{{color:#9ca3af;font-size:14px}}.cta strong{{color:#fff}}
.btn{{padding:10px 24px;border-radius:8px;font-size:14px;font-weight:600;text-decoration:none;cursor:pointer;border:none}}
.btn-primary{{background:linear-gradient(135deg,#7c3aed,#a855f7);color:#fff}}
.btn-secondary{{background:#1a1a1a;color:#e5e7eb;border:1px solid #2a2a2a;margin-right:8px}}
.try-row{{display:flex;align-items:center;gap:12px;margin-bottom:32px}}
.try-row input{{flex:1;background:#111;border:1px solid #2a2a2a;border-radius:8px;padding:10px 16px;color:#fff;font-size:14px;outline:none;max-width:320px}}
.try-row input:focus{{border-color:#a855f7}}
</style>
</head>
<body>
<nav>
  <a href="/" class="logo">Stack<span>Sight</span></a>
  <div class="nav-links">
    <a href="/docs">Docs</a>
    <a href="/demo/stripe.com">Demo</a>
    <a href="/#pricing">Pricing</a>
    <a href="/dashboard">Dashboard</a>
  </div>
</nav>
<main class="main">
  <div class="try-row">
    <input id="domain-input" type="text" placeholder="Try another domain..." value="{domain}">
    <button class="btn btn-primary" onclick="tryDomain()">Analyze -></button>
  </div>
  <div class="header">
    <h1>{company}</h1>
    <div class="meta">
      <span style="color:#6b7280;font-size:14px">{domain}</span>
      {hiring_badge}
      <span class="source-pill">{source_badge}</span>
    </div>
  </div>
  <div class="grid">
    <div class="card">
      <h3>Engineering Roles</h3>
      <ul>{eng_html}</ul>
    </div>
    <div class="card">
      <h3>Sales Roles</h3>
      <ul>{sales_html}</ul>
    </div>
    <div class="card full-width">
      <h3>Tech Stack</h3>
      <div class="tags">{tech_html}</div>
    </div>
    <div class="card full-width">
      <h3>Raw API Response</h3>
      <pre>{raw_json}</pre>
    </div>
  </div>
  <div class="cta">
    <p><strong>Ready to integrate?</strong><br>Get your API key and start pulling signals into your stack.</p>
    <div>
      <a href="/docs" class="btn btn-secondary">View Docs</a>
      <a href="/dashboard" class="btn btn-primary">Get API Key</a>
    </div>
  </div>
</main>
<script>
function tryDomain() {{
  const d = document.getElementById('domain-input').value.trim();
  if (d) window.location.href = '/demo/' + encodeURIComponent(d);
}}
document.getElementById('domain-input').addEventListener('keydown', e => {{
  if (e.key === 'Enter') tryDomain();
}});
</script>
</body>
</html>""")
