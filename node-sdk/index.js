'use strict';
const https = require('https');
const http = require('http');

class StackSightError extends Error {
  constructor(message, statusCode) {
    super(message);
    this.name = 'StackSightError';
    this.statusCode = statusCode;
  }
}

class StackSight {
  constructor(apiKey) {
    if (!apiKey) throw new Error('API key is required.');
    this.apiKey = apiKey;
    this.baseUrl = 'https://careers-scraper-production.up.railway.app';
  }

  _request(method, path, body = null) {
    return new Promise((resolve, reject) => {
      const url = new URL(this.baseUrl + path);
      const isHttps = url.protocol === 'https:';
      const options = {
        hostname: url.hostname,
        path: url.pathname + url.search,
        method,
        headers: { 'x-api-key': this.apiKey, 'Content-Type': 'application/json' },
      };
      if (body) {
        const bodyStr = JSON.stringify(body);
        options.headers['Content-Length'] = Buffer.byteLength(bodyStr);
      }
      const req = (isHttps ? https : http).request(options, (res) => {
        let data = '';
        res.on('data', chunk => data += chunk);
        res.on('end', () => {
          try {
            const parsed = JSON.parse(data);
            if (res.statusCode >= 400) reject(new StackSightError(parsed.detail || data, res.statusCode));
            else resolve(parsed);
          } catch (e) { reject(new StackSightError('Failed to parse response', res.statusCode)); }
        });
      });
      req.on('error', reject);
      if (body) req.write(JSON.stringify(body));
      req.end();
    });
  }

  scrape(domain) {
    return this._request('GET', '/scrape?domain=' + encodeURIComponent(domain));
  }

  bulkScrape(domains) {
    return this._request('POST', '/scrape/bulk', { domains });
  }

  getUsage() {
    return this._request('GET', '/me');
  }

  subscribeWebhook(domain, webhookUrl) {
    return this._request('POST', '/webhooks/subscribe', { domain, webhook_url: webhookUrl });
  }

  getSubscriptions() {
    return this._request('GET', '/webhooks/subscriptions');
  }
}

module.exports = { StackSight, StackSightError };
