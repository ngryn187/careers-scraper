import requests


class StackSightError(Exception):
    pass


class StackSight:
    BASE_URL = "https://careers-scraper-production.up.railway.app"

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("API Key is required.")
        self.api_key = api_key
        self.headers = {"x-api-key": self.api_key}

    def scrape(self, domain: str) -> dict:
        """Scrape a single domain for hiring intent and tech stack."""
        res = requests.get(f"{self.BASE_URL}/scrape", params={"domain": domain}, headers=self.headers)
        if res.status_code != 200:
            raise StackSightError(f"API Error {res.status_code}: {res.text}")
        return res.json()

    def bulk_scrape(self, domains: list) -> dict:
        """Scrape up to 50 domains at once."""
        res = requests.post(f"{self.BASE_URL}/scrape/bulk", json={"domains": domains}, headers=self.headers)
        if res.status_code != 200:
            raise StackSightError(f"API Error {res.status_code}: {res.text}")
        return res.json()

    def get_usage(self) -> dict:
        """Check current API usage and limits."""
        res = requests.get(f"{self.BASE_URL}/me", headers=self.headers)
        if res.status_code != 200:
            raise StackSightError(f"API Error {res.status_code}: {res.text}")
        return res.json()

    def subscribe_webhook(self, domain: str, webhook_url: str) -> dict:
        """Subscribe to hiring status change alerts for a domain."""
        res = requests.post(
            f"{self.BASE_URL}/webhooks/subscribe",
            json={"domain": domain, "webhook_url": webhook_url},
            headers=self.headers
        )
        if res.status_code != 200:
            raise StackSightError(f"API Error {res.status_code}: {res.text}")
        return res.json()

    def get_subscriptions(self) -> dict:
        """List all active webhook subscriptions."""
        res = requests.get(f"{self.BASE_URL}/webhooks/subscriptions", headers=self.headers)
        if res.status_code != 200:
            raise StackSightError(f"API Error {res.status_code}: {res.text}")
        return res.json()
