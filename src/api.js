import { config } from './config.js';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function randomBetween(min, max) {
  return min + Math.random() * (max - min);
}

function superProperties(android = false) {
  if (android) {
    return {
      os: 'Android',
      browser: 'Discord Android',
      device: 'b0q',
      system_locale: 'pt-BR',
      has_client_mods: false,
      client_version: '316.11 - rn',
      release_channel: 'googleRelease',
      design_id: 2,
      browser_user_agent: '',
      browser_version: '',
      os_version: '28',
      client_build_number: 5169,
      client_event_source: null,
      client_app_state: 'active',
    };
  }
  return {
    os: 'Windows',
    browser: 'Discord Client',
    release_channel: 'stable',
    client_version: '1.0.9236',
    os_version: '10.0.19045',
    os_arch: 'x64',
    app_arch: 'x64',
    system_locale: 'pt-BR',
    has_client_mods: false,
    browser_user_agent: config.userAgent,
    browser_version: '37.6.0',
    os_sdk_version: '19045',
    client_build_number: config.clientBuildNumber,
    client_event_source: null,
    client_app_state: 'focused',
  };
}

function encodeProperties(android = false) {
  return Buffer.from(JSON.stringify(superProperties(android)), 'utf8').toString('base64');
}

export class DiscordApiError extends Error {
  constructor(status, payload, route) {
    const message = typeof payload === 'object' && payload?.message ? payload.message : '';
    const fields = typeof payload === 'object' && payload?.errors ? ` ${JSON.stringify(payload.errors)}` : '';
    const detail = message ? `: ${message}${fields}`.slice(0, 400) : '';
    super(`Discord API ${status} em ${route}${detail}`);
    this.name = 'DiscordApiError';
    this.status = status;
    this.payload = payload;
    this.route = route;
  }
}

export class TokenExpiredError extends Error {
  constructor(message = 'Token de usuario invalido ou expirado.') {
    super(message);
    this.name = 'TokenExpiredError';
  }
}

export class DiscordApi {
  constructor(token) {
    this.token = String(token).replace(/^Bot\s+/i, '').trim();
    this.routeUntil = new Map();
    this.globalUntil = 0;
  }

  bucketKey(method, route) {
    return `${method}:${route.replace(/\/\d{5,}/g, '/:id')}`;
  }

  headers(android = false) {
    const headers = {
      Authorization: this.token,
      'Accept-Language': 'pt-BR,en;q=0.9',
      'X-Discord-Locale': 'pt-BR',
      'X-Discord-Timezone': 'America/Sao_Paulo',
      'X-Super-Properties': encodeProperties(android),
      'User-Agent': android ? config.androidUserAgent : config.userAgent,
    };
    if (!android) {
      Object.assign(headers, {
        Origin: 'https://discord.com',
        Referer: 'https://discord.com/channels/@me',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
      });
    }
    return headers;
  }

  async waitForBucket(method, route) {
    const key = this.bucketKey(method, route);
    const now = Date.now();
    const routeWait = (this.routeUntil.get(key) ?? 0) - now;
    if (routeWait > 0) await sleep(routeWait + 120);
    const globalWait = this.globalUntil - now;
    if (globalWait > 0) await sleep(globalWait + 120);
  }

  observeHeaders(response, method, route) {
    const remaining = Number(response.headers.get('x-ratelimit-remaining'));
    const resetAfter = Number(response.headers.get('x-ratelimit-reset-after'));
    if (Number.isFinite(remaining) && remaining <= 0 && Number.isFinite(resetAfter)) {
      this.routeUntil.set(this.bucketKey(method, route), Date.now() + resetAfter * 1000);
    }
  }

  async request(method, route, options = {}) {
    const { body, query, android = false, retries = config.maxRetries } = options;
    const url = new URL(config.apiBase + route);
    if (query) {
      for (const [key, value] of Object.entries(query)) {
        if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
      }
    }

    for (let attempt = 0; attempt <= retries; attempt += 1) {
      await this.waitForBucket(method, route);
      let response;
      try {
        const headers = this.headers(android);
        if (body !== undefined) headers['Content-Type'] = 'application/json';
        response = await fetch(url, {
          method,
          headers,
          body: body === undefined ? undefined : JSON.stringify(body),
        });
      } catch (error) {
        if (attempt >= retries) throw error;
        await sleep(Math.min(15000, 600 * 2 ** attempt) + randomBetween(100, 500));
        continue;
      }

      this.observeHeaders(response, method, route);

      if (response.status === 204) return {};

      const raw = await response.text();
      let payload = {};
      if (raw) {
        try {
          payload = JSON.parse(raw);
        } catch {
          payload = raw;
        }
      }

      if (response.status === 401) {
        throw new TokenExpiredError();
      }

      if (response.status === 429) {
        const retryAfter = Number(
          response.headers.get('retry-after') ?? payload?.retry_after ?? 1,
        );
        const scope = response.headers.get('x-ratelimit-scope');
        if (scope === 'global') {
          this.globalUntil = Date.now() + retryAfter * 1000;
        }
        await sleep((Math.max(0.5, retryAfter) + randomBetween(0.2, 0.8)) * 1000);
        continue;
      }

      if (response.status >= 500) {
        if (attempt >= retries) throw new DiscordApiError(response.status, payload, route);
        await sleep(Math.min(30000, 800 * 2 ** attempt) + randomBetween(100, 600));
        continue;
      }

      if (!response.ok) {
        throw new DiscordApiError(response.status, payload, route);
      }

      return payload;
    }

    throw new DiscordApiError(429, { message: 'limite de requisicoes excedido' }, route);
  }

  get(route, options) {
    return this.request('GET', route, options);
  }

  post(route, options) {
    return this.request('POST', route, options);
  }

  delete(route, options) {
    return this.request('DELETE', route, options);
  }
}
