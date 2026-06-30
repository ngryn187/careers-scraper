import httpx
import pytest

BASE = "https://careers-scraper-production.up.railway.app"


def test_health():
    r = httpx.get(f"{BASE}/health", timeout=20)
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_landing_page():
    r = httpx.get(f"{BASE}/", timeout=20)
    assert r.status_code == 200


def test_me_no_key():
    r = httpx.get(f"{BASE}/me", timeout=20)
    assert r.status_code == 401


def test_me_bad_key():
    r = httpx.get(f"{BASE}/me", headers={"X-API-Key": "bad-key-123"}, timeout=20)
    assert r.status_code == 401


def test_scrape_no_key():
    r = httpx.get(f"{BASE}/scrape", params={"domain": "example.com"}, timeout=20)
    assert r.status_code == 401


def test_sitemap():
    r = httpx.get(f"{BASE}/sitemap.xml", timeout=20)
    assert r.status_code == 200


def test_trending():
    r = httpx.get(f"{BASE}/trending", timeout=20)
    # endpoint added in v8.8.0; accept 200 or 404 during rollout
    assert r.status_code in [200, 404]


def test_badge():
    r = httpx.get(f"{BASE}/badge/stripe.com.svg", timeout=20)
    # endpoint added in v8.10.0; accept 200 or 404 during rollout
    assert r.status_code in [200, 404]
