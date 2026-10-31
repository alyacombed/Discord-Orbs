#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import json
import time
import asyncio
import random
import re
import uuid
import base64
import hashlib
import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import discord
from discord import app_commands, ui
from discord.ext import commands
import aiohttp
from urllib.parse import urlparse

DEVELOPER_ID = 1517281352280703168
OWNER_ID = int(os.environ.get("OWNER_ID", "0") or 0)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "monsters_orbs_data.json")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

def log(level: str, msg: str, *args):
    lvl = getattr(logging, level.upper(), logging.INFO)
    logger.log(lvl, msg, *args)

# ==============================
# [PROXY] Sistema de Proxy Rotation Avançado
# ==============================
class ProxyManager:
    def __init__(self):
        self.proxies: List[Dict] = []  # [{"ip": "...", "port": ..., "country": "...", "url": "..."}]
        self.last_fetch = 0
        self.valid_proxies = []
        self.lock = asyncio.Lock()
        self.proxy_sources = [
            "https://proxylist.geonode.com/api/proxy-list?limit=20&protocols=http&anonymityLevel=elite&sort_by=lastChecked&sort_type=desc",
            "https://www.proxy-list.download/api/v1/get?type=http&anon=elite",
        ]
        # Fallback: lista estática pequena (substitua conforme necessário)
        self.fallback_proxies = [
            {"ip": "103.152.112.162", "port": 80, "country": "ID"},
            {"ip": "103.152.112.163", "port": 80, "country": "ID"},
            {"ip": "45.79.253.119", "port": 3128, "country": "US"},
            {"ip": "198.11.175.198", "port": 80, "country": "CA"},
        ]

    async def fetch_proxies(self) -> List[Dict]:
        """Busca proxies de fontes públicas."""
        proxies = []
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for source in self.proxy_sources:
                try:
                    async with session.get(source) as resp:
                        if "geonode" in source and resp.status == 200:
                            data = await resp.json()
                            for p in data.get("data", []):
                                ip = p.get("ip")
                                port = p.get("port")
                                country = p.get("country", "XX")
                                if ip and port:
                                    url = f"http://{ip}:{port}"
                                    proxies.append({"ip": ip, "port": port, "country": country, "url": url})
                        elif "proxy-list.download" in source and resp.status == 200:
                            text = await resp.text()
                            lines = text.strip().split("\n")
                            for line in lines[:10]:
                                if ":" in line:
                                    ip, port = line.split(":")
                                    proxies.append({"ip": ip, "port": int(port), "country": "XX", "url": f"http://{ip}:{port}"})
                except Exception as e:
                    log("warning", f"Falha ao buscar proxies de {source}: {e}")
                    continue
        return proxies or self.fallback_proxies

    async def validate_proxy(self, proxy: Dict) -> bool:
        """Valida se o proxy funciona e é anônimo E compatível com Discord."""
        # Teste 1: httpbin (básico)
        test_url = "https://httpbin.org/ip"
        timeout = aiohttp.ClientTimeout(total=8)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(test_url, proxy=proxy["url"], ssl=False) as resp:
                    if resp.status != 200:
                        return False
                    data = await resp.json()
                    origin_ip = data.get("origin", "")
                    if proxy["ip"] in origin_ip:
                        return False
        except Exception:
            return False
        # Teste 2: Discord (realista)
        discord_test_url = "https://discord.com/api/v9/experiments"
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(discord_test_url, proxy=proxy["url"], ssl=False) as resp:
                    # Aceita 401/403 como sucesso (significa que chegou no Discord)
                    if resp.status in (200, 401, 403):
                        return True
            return False
        except Exception:
            return False

    async def refresh_proxies(self):
        """Atualiza a lista de proxies válidos."""
        async with self.lock:
            now = time.time()
            if now - self.last_fetch < 600:  # Cache de 10 min
                return
            raw_proxies = await self.fetch_proxies()
            valid = []
            tasks = [self.validate_proxy(p) for p in raw_proxies]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, ok in enumerate(results):
                if ok is True:
                    valid.append(raw_proxies[i])
            self.valid_proxies = valid
            self.last_fetch = now
            log("info", f"[PROXY] {len(self.valid_proxies)} proxies válidos carregados.")

    async def get_proxy_for_region(self, preferred_countries: List[str] = None) -> Optional[str]:
        """Retorna URL de proxy válido, preferindo países específicos."""
        await self.refresh_proxies()
        candidates = self.valid_proxies[:]
        if preferred_countries:
            preferred = [p for p in candidates if p["country"] in preferred_countries]
            if preferred:
                return random.choice(preferred)["url"]
        if candidates:
            return random.choice(candidates)["url"]
        return None

    async def rotate(self) -> Optional[str]:
        """Roda entre proxies válidos."""
        return await self.get_proxy_for_region()

# Instância global do gerenciador de proxies
proxy_manager = ProxyManager()

# ==============================
# [PROXY] Registro Global de Quests Concluídas (Anti-Duplicação)
# ==============================
GLOBAL_COMPLETED_QUESTS: Dict[str, set] = {}  # user_id -> set(quest_id)

def mark_quest_completed(user_id: str, quest_id: str):
    if user_id not in GLOBAL_COMPLETED_QUESTS:
        GLOBAL_COMPLETED_QUESTS[user_id] = set()
    GLOBAL_COMPLETED_QUESTS[user_id].add(quest_id)

def is_quest_already_completed(user_id: str, quest_id: str) -> bool:
    return quest_id in GLOBAL_COMPLETED_QUESTS.get(user_id, set())

# ==============================
# Configuração Original do Bot
# ==============================
class BotConfig:
    def __init__(self):
        self.data = {
            "build_number": 539951,
            "build_number_fetched_at": None,
            "auto_claim_rewards": True,
            "video_speed": 7,
            "video_interval": 1,
            "video_max_future": 10,
            "heartbeat_interval": 20,
            "heartbeat_jitter": 3.0,
            "max_retries": 5,
            "retry_base_delay": 1.5,
            "rate_limit_buffer": 1.5,
            "request_retry_jitter": 0.5,
            "session_timeout_total": 60,
            "session_timeout_connect": 10,
            "max_concurrent_users": 28,
            "quest_processing_delay": 2,
            "session_cleanup_delay": 5,
            "dm_batch_size": 5,
            "dm_batch_delay": 2,
            "quest_cleanup_interval": 300,
            "behavioral_enabled": True,
            "behavioral_mean_delay": 45,
            "behavioral_std_dev": 8,
            "behavioral_min_delay": 20,
            "behavioral_max_delay": 90,
            "stream_simulation_enabled": True,
            "stream_heartbeat_interval": 25,
            "new_quest_check_interval": 1800,
            "auto_claim_retry_on_captcha": False,
            "webhook_url": None,
            "notification_channel_id": None,
            "stealth_mode": True,
            "parallel_max_concurrent": 5,
            "parallel_stagger_min": 3.0,
            "parallel_stagger_max": 8.0,
            # [PROXY] Configurações adicionais
            "use_proxies": True,
            "proxy_rotation_interval": 300,  # 5 min
            "proxy_country_preferences": ["US", "CA", "GB", "DE", "BR"],
        }

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value):
        self.data[key] = value

class StealthParallelEngine:
    def __init__(self):
        self.active_tasks = {}
        self._hour_seed = datetime.now(timezone.utc).hour

    def staggered_delay(self, index, total):
        base_offset = index * random.uniform(3.0, 8.0)
        jitter = random.uniform(-1.0, 2.0)
        return max(0.5, base_offset + jitter)

    def heartbeat_delay(self, quest_id, task_type):
        profiles = {
            "WATCH_VIDEO": {"mean": 7.5, "std": 1.2, "min": 5.0, "max": 12.0},
            "WATCH_VIDEO_ON_MOBILE": {"mean": 7.5, "std": 1.2, "min": 5.0, "max": 12.0},
            "PLAY_ON_DESKTOP": {"mean": 45.0, "std": 10.0, "min": 25.0, "max": 80.0},
            "PLAY_ON_DESKTOP_V2": {"mean": 45.0, "std": 10.0, "min": 25.0, "max": 80.0},
            "PLAY_ON_XBOX": {"mean": 45.0, "std": 10.0, "min": 25.0, "max": 80.0},
            "PLAY_ON_PLAYSTATION": {"mean": 45.0, "std": 10.0, "min": 25.0, "max": 80.0},
            "STREAM_ON_DESKTOP": {"mean": 35.0, "std": 8.0, "min": 20.0, "max": 60.0},
            "PLAY_ACTIVITY": {"mean": 25.0, "std": 5.0, "min": 15.0, "max": 40.0},
            "ACHIEVEMENT_IN_ACTIVITY": {"mean": 30.0, "std": 6.0, "min": 18.0, "max": 50.0},
        }
        p = profiles.get(task_type, profiles["PLAY_ON_DESKTOP"])
        sigma = math.sqrt(math.log(1 + (p["std"] / p["mean"]) ** 2))
        mu = math.log(p["mean"]) - sigma ** 2 / 2
        delay = random.lognormvariate(mu, sigma)
        return max(p["min"], min(p["max"], delay))

    def video_timestamp_jitter(self, base_timestamp):
        return base_timestamp + random.uniform(0.000001, 0.999999)

    def should_pause(self):
        if random.random() < 0.05:
            return random.uniform(60, 180)
        return 0

bot_config = BotConfig()
GLOBAL_QUEST_SEMAPHORE = asyncio.Semaphore(bot_config.get("max_concurrent_users", 45))

class Constants:
    ALLOWED_GUILD_ID = 1516478347239227532  # Servidor original
    CREATOR_ID = 1517281352280703168
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) discord/1.0.9236 Chrome/138.0.7204.251 "
        "Electron/37.6.0 Safari/537.36"
    )
    API_BASE = "https://discord.com/api/v9"

    @classmethod
    def get_properties(cls, build_number: int = None):
        bn = build_number or bot_config.get("build_number", 539951)
        return {
            "os": "Windows",
            "browser": "Discord Client",
            "release_channel": "stable",
            "client_version": "1.0.9236",
            "os_version": "10.0.19045",
            "os_arch": "x64",
            "app_arch": "x64",
            "system_locale": "pt-BR",
            "has_client_mods": False,
            "client_launch_id": str(uuid.uuid4()),
            "browser_user_agent": cls.USER_AGENT,
            "browser_version": "37.6.0",
            "os_sdk_version": "19045",
            "client_build_number": bn,
            "native_build_number": 81687,
            "client_event_source": None,
            "launch_signature": str(uuid.uuid4()),
            "client_heartbeat_session_id": str(uuid.uuid4()),
            "client_app_state": "focused",
        }

    ANDROID_USER_AGENT = "Discord-Android/316011;RNA"

    @classmethod
    def get_android_properties(cls):
        return {
            "os": "Android",
            "browser": "Discord Android",
            "device": "b0q",
            "system_locale": "pt-BR",
            "has_client_mods": False,
            "client_version": "316.11 - rn",
            "release_channel": "googleRelease",
            "device_vendor_id": str(uuid.uuid4()),
            "design_id": 2,
            "browser_user_agent": "",
            "browser_version": "",
            "os_version": "28",
            "client_build_number": 5169,
            "client_event_source": None,
            "client_launch_id": str(uuid.uuid4()),
            "launch_signature": "1771754995045142953",
            "client_app_state": "active",
            "client_heartbeat_session_id": str(uuid.uuid4()),
        }

# Definindo a enumeração fora da classe Constants
class QuestContentType(Enum):
    QUEST_HOME_DESKTOP = 11
    QUEST_HOME_MOBILE = 12

# Atualizando SUPPORTED_TASKS fora da classe Constants
SUPPORTED_TASKS = [
    "WATCH_VIDEO",
    "WATCH_VIDEO_ON_MOBILE",
    "PLAY_ON_DESKTOP",
    "PLAY_ON_DESKTOP_V2",
    "PLAY_ON_XBOX",
    "PLAY_ON_PLAYSTATION",
    "PLAY_ACTIVITY",
    "ACHIEVEMENT_IN_ACTIVITY",
    "STREAM_ON_DESKTOP",
]

def b64encode_str(data: str) -> str:
    return base64.b64encode(data.encode()).decode()

def get_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]

def is_valid_url(string: str) -> bool:
    try:
        result = urlparse(string)
        return all([result.scheme, result.netloc])
    except Exception:
        return False

def format_time(seconds: int) -> str:
    if seconds < 0:
        seconds = 0
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)

def behavioral_delay(mean: float = None, std: float = None, min_d: float = None, max_d: float = None) -> float:
    if not bot_config.get("behavioral_enabled", True):
        return bot_config.get("heartbeat_interval", 20)
    mean = mean or bot_config.get("behavioral_mean_delay", 45)
    std = std or bot_config.get("behavioral_std_dev", 8)
    min_d = min_d or bot_config.get("behavioral_min_delay", 20)
    max_d = max_d or bot_config.get("behavioral_max_delay", 90)
    sigma = math.sqrt(math.log(1 + (std / mean) ** 2))
    mu = math.log(mean) - sigma ** 2 / 2
    delay = random.lognormvariate(mu, sigma)
    return max(min_d, min(max_d, delay))

def make_progress_bar(percent: int, length: int = 20) -> str:
    filled = "█" * (percent * length // 100)
    empty = "░" * (length - len(filled))
    return f"[{filled}{empty}] {percent}%"

class RateLimitBucket:
    def __init__(self):
        self.limit = 1
        self.remaining = 1
        self.reset_after = 0.0
        self.reset_timestamp = 0.0
        self.bucket_id = ""
        self.scope = ""

    def update_from_headers(self, headers):
        try:
            self.limit = int(headers.get("X-RateLimit-Limit", 1))
            self.remaining = int(headers.get("X-RateLimit-Remaining", 1))
            self.reset_after = float(headers.get("X-RateLimit-Reset-After", 0))
            self.bucket_id = headers.get("X-RateLimit-Bucket", "")
            self.scope = headers.get("X-RateLimit-Scope", "")
            self.reset_timestamp = time.time() + self.reset_after
        except Exception as e:
            log("warning", f"Erro ao atualizar bucket de rate limit: {e}")

    def is_exhausted(self) -> bool:
        return self.remaining <= 0 and time.time() < self.reset_timestamp

    def wait_time(self) -> float:
        if self.is_exhausted():
            return max(0, self.reset_timestamp - time.time()) + bot_config.get("rate_limit_buffer", 1.5)
        return 0

class DiscordSelfClient:
    def __init__(self, token: str, user_id: str = None):
        self.token = token.replace("Bot ", "").strip()
        self.user_id = user_id  # [PROXY] usado para anti-duplicação
        self.session = None
        self.build_number = bot_config.get("build_number", 539951)
        self.headers = self._make_headers()
        self.rate_buckets: Dict[str, RateLimitBucket] = {}
        self.global_rate_limit_until = 0.0
        self._closed = False
        self.timeout = aiohttp.ClientTimeout(
            total=bot_config.get("session_timeout_total", 60),
            connect=bot_config.get("session_timeout_connect", 10)
        )
        self.current_proxy = None
        self.use_proxies = bot_config.get("use_proxies", True)

    def _make_headers(self, is_android: bool = False) -> Dict[str, str]:
        props = Constants.get_android_properties() if is_android else Constants.get_properties(self.build_number)
        headers = {
            "Authorization": self.token,
            "accept-language": "pt-BR,en;q=0.9",
            "x-debug-options": "bugReporterEnabled",
            "x-discord-locale": "pt-BR",
            "x-discord-timezone": "America/Sao_Paulo",
            "User-Agent": Constants.ANDROID_USER_AGENT if is_android else Constants.USER_AGENT,
            "x-super-properties": b64encode_str(json.dumps(props, separators=(',', ':'))),
        }
        if not is_android:
            headers.update({
                "origin": "https://discord.com",
                "referer": "https://discord.com/channels/@me",
                "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
            })
        return headers

    async def __aenter__(self):
        # [PROXY] Define proxy se habilitado
        if self.use_proxies:
            self.current_proxy = await proxy_manager.rotate()
            if self.current_proxy:
                log("info", f"[PROXY] Usando proxy: {self.current_proxy}")
            else:
                self.current_proxy = None
        connector = aiohttp.TCPConnector(ssl=False) if self.current_proxy else None
        self.session = aiohttp.ClientSession(
            headers=self.headers,
            timeout=self.timeout,
            connector=connector
        )
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        if self.session and not self.session.closed:
            self._closed = True
            await self.session.close()
            self.session = None

    def _get_bucket_key(self, method: str, endpoint: str) -> str:
        normalized = re.sub(r'/\d+', '/:id', endpoint)
        return f"{method}:{normalized}"

    async def _wait_for_rate_limit(self, bucket_key: str):
        if time.time() < self.global_rate_limit_until:
            wait = self.global_rate_limit_until - time.time()
            await asyncio.sleep(wait)
        bucket = self.rate_buckets.get(bucket_key)
        if bucket:
            wait = bucket.wait_time()
            if wait > 0:
                await asyncio.sleep(wait)

    async def _update_rate_limit(self, resp, bucket_key: str):
        bucket = self.rate_buckets.setdefault(bucket_key, RateLimitBucket())
        bucket.update_from_headers(resp.headers)
        if resp.status == 429:
            scope = resp.headers.get("X-RateLimit-Scope", "")
            retry_after = float(resp.headers.get("Retry-After", 1))
            if scope == "global":
                self.global_rate_limit_until = time.time() + retry_after
                await asyncio.sleep(retry_after + 1)
                await self.close()
                connector = aiohttp.TCPConnector(ssl=False) if self.current_proxy else None
                self.session = aiohttp.ClientSession(headers=self.headers, timeout=self.timeout, connector=connector)

    async def request(self, method: str, endpoint: str, **kwargs) -> Any:
        url = f"{Constants.API_BASE}{endpoint}"
        is_android = kwargs.pop("is_android", False)
        max_retries = bot_config.get("max_retries", 5)
        headers = dict(self.headers)
        if is_android:
            headers = self._make_headers(is_android=True)
        custom_headers = kwargs.pop("headers", {})
        headers.update(custom_headers)
        bucket_key = self._get_bucket_key(method, endpoint)
        last_error = None
        for attempt in range(max_retries + 1):
            await self._wait_for_rate_limit(bucket_key)
            try:
                if not self.session or self._closed:
                    if self.session and not self.session.closed:
                        await self.session.close()
                    connector = aiohttp.TCPConnector(ssl=False) if self.current_proxy else None
                    self.session = aiohttp.ClientSession(headers=headers, timeout=self.timeout, connector=connector)
                # [PROXY] Passa o proxy para a requisição
                proxy_kw = {"proxy": self.current_proxy} if self.current_proxy else {}
                async with self.session.request(method, url, headers=headers, **kwargs, **proxy_kw) as resp:
                    await self._update_rate_limit(resp, bucket_key)
                    if resp.status == 204:
                        return {}
                    try:
                        data = await resp.json()
                    except Exception:
                        data = await resp.text()
                    if resp.status == 401:
                        raise TokenExpiredError("O token fornecido está expirado ou é inválido!")
                    if resp.status == 429:
                        retry_after = float(resp.headers.get("Retry-After", 1))
                        jitter = random.uniform(0, bot_config.get("request_retry_jitter", 0.5))
                        await asyncio.sleep(retry_after + jitter)
                        continue
                    if resp.status >= 500:
                        delay = bot_config.get("retry_base_delay", 1.5) * (2 ** attempt)
                        jitter = random.uniform(0, delay * 0.1)
                        await asyncio.sleep(delay + jitter)
                        continue
                    if resp.status >= 400:
                        raise DiscordAPIError(resp.status, data, endpoint)
                    return data
            except TokenExpiredError:
                raise
            except aiohttp.ClientError as e:
                last_error = e
                if attempt < max_retries:
                    delay = bot_config.get("retry_base_delay", 1.5) * (2 ** attempt)
                    jitter = random.uniform(0, delay * 0.1)
                    await asyncio.sleep(delay + jitter)
                else:
                    raise DiscordAPIError(0, {"message": str(last_error)}, endpoint)
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                else:
                    raise DiscordAPIError(0, {"message": str(last_error)}, endpoint)
        raise DiscordAPIError(429, {"message": "Max retries exceeded"}, endpoint)

    async def get(self, endpoint: str, **kwargs) -> Any:
        return await self.request("GET", endpoint, **kwargs)

    async def post(self, endpoint: str, **kwargs) -> Any:
        return await self.request("POST", endpoint, **kwargs)

    async def delete(self, endpoint: str, **kwargs) -> Any:
        return await self.request("DELETE", endpoint, **kwargs)

    async def update_build_number(self, force: bool = False):
        cached = bot_config.get("build_number", 539951)
        fetched_at = bot_config.get("build_number_fetched_at")
        if not force and fetched_at:
            try:
                last_fetch = datetime.fromisoformat(fetched_at)
                if (datetime.now(timezone.utc) - last_fetch).total_seconds() < 21600:
                    self.build_number = cached
                    return
            except Exception:
                pass
        try:
            if not self.session or self._closed:
                if self.session and not self.session.closed:
                    await self.session.close()
                connector = aiohttp.TCPConnector(ssl=False) if self.current_proxy else None
                self.session = aiohttp.ClientSession(headers=self.headers, timeout=self.timeout, connector=connector)
            proxy_kw = {"proxy": self.current_proxy} if self.current_proxy else {}
            async with self.session.get("https://discord.com/app", headers={"User-Agent": Constants.USER_AGENT}, **proxy_kw) as resp:
                html = await resp.text()
                scripts = re.findall(r'/assets/web\.([a-f0-9]+)\.js', html)
                for script in scripts:
                    try:
                        async with self.session.get(f"https://discord.com/assets/web.{script}.js", headers={"User-Agent": Constants.USER_AGENT}, **proxy_kw) as js_resp:
                            js = await js_resp.text()
                            match = re.search(r'"buildNumber"\s*:\s*"(\d{5,7})"', js)
                            if match:
                                self.build_number = int(match.group(1))
                                bot_config.set("build_number", self.build_number)
                                bot_config.set("build_number_fetched_at", datetime.now(timezone.utc).isoformat())
                                return
                            match = re.search(r'buildNumber["\s:]+["\s]*(\d{5,7})', js)
                            if match:
                                self.build_number = int(match.group(1))
                                bot_config.set("build_number", self.build_number)
                                bot_config.set("build_number_fetched_at", datetime.now(timezone.utc).isoformat())
                                return
                    except Exception as e:
                        log("warning", f"Erro buscando build number no script {script}: {e}")
                        continue
            self.build_number = cached
            log("warning", f"Build number não encontrado, usando cache: {cached}")
        except Exception as e:
            log("error", f"Erro fatal ao atualizar build number: {e}")
            self.build_number = cached

class DiscordAPIError(Exception):
    def __init__(self, status: int, data: Any, endpoint: str = ""):
        self.status = status
        self.data = data
        self.endpoint = endpoint
        msg = f"Discord API Error {status} em {endpoint}"
        if isinstance(data, dict):
            if "message" in data:
                msg += f": {data['message']}"
            if "code" in data:
                msg += f" (code: {data['code']})"
            if "retry_after" in data:
                msg += f" [retry_after: {data['retry_after']}s]"
        super().__init__(msg)

class TokenExpiredError(Exception):
    pass

@dataclass
class Quest:
    id: str
    config: Dict[str, Any]
    user_status: Optional[Dict[str, Any]] = None
    targeted_content: int = 0
    preview: bool = False
    traffic_metadata_raw: Optional[str] = None
    traffic_metadata_sealed: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict) -> "Quest":
        return cls(
            id=data.get("id", ""),
            config=data.get("config", {}),
            user_status=data.get("user_status"),
            targeted_content=data.get("targeted_content", 0),
            preview=data.get("preview", False),
            traffic_metadata_raw=data.get("traffic_metadata_raw"),
            traffic_metadata_sealed=data.get("traffic_metadata_sealed"),
        )

    @property
    def config_version(self) -> int:
        return self.config.get("config_version", 2)

    @property
    def starts_at(self) -> Optional[datetime]:
        ts = self.config.get("starts_at")
        if ts:
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                pass
        return None

    @property
    def expires_at(self) -> Optional[datetime]:
        ts = self.config.get("expires_at")
        if ts:
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                pass
        return None

    @property
    def application(self) -> Dict[str, Any]:
        return self.config.get("application", {})

    @property
    def application_id(self) -> str:
        return self.application.get("id", "")

    @property
    def application_name(self) -> str:
        return self.application.get("name", "Jogo Desconhecido")

    @property
    def messages(self) -> Dict[str, str]:
        return self.config.get("messages", {})

    @property
    def quest_name(self) -> str:
        return self.messages.get("quest_name", "Quest Desconhecida")

    @property
    def rewards_config(self) -> Dict[str, Any]:
        return self.config.get("rewards_config", {})

    @property
    def reward_amount(self) -> int:
        rewards = self.rewards_config
        if rewards:
            return rewards.get("amount", 0)
        return 0

    @property
    def reward_type(self) -> str:
        rewards = self.rewards_config
        if rewards:
            return rewards.get("type", "unknown")
        return "unknown"

    @property
    def assets(self) -> Dict[str, str]:
        return self.config.get("assets", {})

    @property
    def hero_image(self) -> str:
        return self.assets.get("hero", "")

    @property
    def task_config(self) -> Dict[str, Any]:
        if self.config_version == 1:
            tc = self.config.get("task_config", {})
            return tc.get("tasks", {})
        else:
            tc = self.config.get("task_config_v2", {})
            return tc.get("tasks", {})

    def get_task_type(self) -> Optional[str]:
        tasks = self.task_config
        for task_name in SUPPORTED_TASKS:
            if tasks.get(task_name):
                return task_name
        for key in tasks.keys():
            if tasks.get(key):
                return key
        return None

    def get_task_target(self) -> int:
        task_type = self.get_task_type()
        if not task_type:
            return 0
        task = self.task_config.get(task_type, {})
        return task.get("target", 0)

    @property
    def progress(self) -> Dict[str, Any]:
        if self.user_status:
            return self.user_status.get("progress", {})
        return {}

    def get_progress_value(self, task_type: str) -> int:
        prog = self.progress.get(task_type, {})
        return prog.get("value", 0)

    def is_expired(self, reference: datetime = None) -> bool:
        if reference is None:
            reference = datetime.now(timezone.utc)
        expires = self.expires_at
        if expires:
            return reference > expires
        return False

    def is_started(self) -> bool:
        starts = self.starts_at
        if starts:
            return datetime.now(timezone.utc) >= starts
        return True

    def is_completed(self) -> bool:
        if not self.user_status:
            return False
        return bool(self.user_status.get("completed_at"))

    def is_enrolled(self) -> bool:
        if not self.user_status:
            return False
        return bool(self.user_status.get("enrolled_at"))

    def has_claimed(self) -> bool:
        if not self.user_status:
            return False
        return bool(self.user_status.get("claimed_at"))

    def is_claimable(self) -> bool:
        return self.is_completed() and not self.has_claimed()

    def is_valid_to_do(self) -> bool:
        if self.is_completed():
            return False
        if self.is_expired():
            return False
        if not self.is_started():
            return False
        if self.preview:
            return False
        if not self.get_task_type():
            return False
        return True

    def update_user_status(self, new_status: Dict):
        self.user_status = new_status

    def to_embed_field(self) -> str:
        task_type = self.get_task_type() or "Desconhecido"
        progress = self.get_progress_value(task_type)
        target = self.get_task_target()
        percent = min(100, int((progress / target) * 100)) if target > 0 else 0
        orb_text = f" 💎 {self.reward_amount} Orbs" if self.reward_amount > 0 else ""
        return f"**{self.quest_name}**{orb_text}\n`{task_type}` | {make_progress_bar(percent)} | {format_time(progress)}/{format_time(target)}"

class WebhookManager:
    def __init__(self):
        self.webhook_url: Optional[str] = None

    def set_webhook(self, url: str):
        if is_valid_url(url):
            self.webhook_url = url
            bot_config.set("webhook_url", url)
            return True
        return False

    async def send(self, embed: discord.Embed, content: str = None):
        if not self.webhook_url:
            return False
        try:
            async with aiohttp.ClientSession() as session:
                payload = {"embeds": [embed.to_dict()]}
                if content:
                    payload["content"] = content
                async with session.post(self.webhook_url, json=payload) as resp:
                    return resp.status < 400
        except Exception as e:
            log("error", f"Erro ao enviar webhook: {e}")
            return False

webhook_manager = WebhookManager()

class BalanceTracker:
    """Rastreia o saldo de Orbs ganhos e o historico de missoes de forma persistente."""
    MAX_HISTORY = 100

    def __init__(self, path: str):
        self.path = path
        self.data: Dict[str, Any] = {"total_orbs": 0, "missions_completed": 0, "history": []}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data["total_orbs"] = int(loaded.get("total_orbs", 0))
                self.data["missions_completed"] = int(loaded.get("missions_completed", 0))
                hist = loaded.get("history", [])
                if isinstance(hist, list):
                    self.data["history"] = hist[-self.MAX_HISTORY:]
        except FileNotFoundError:
            pass
        except Exception as e:
            log("warning", f"[SALDO] Falha ao carregar dados: {e}")

    def save(self):
        try:
            self.data["history"] = self.data["history"][-self.MAX_HISTORY:]
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log("warning", f"[SALDO] Falha ao salvar dados: {e}")

    @property
    def total_orbs(self) -> int:
        return int(self.data.get("total_orbs", 0))

    @property
    def missions_completed(self) -> int:
        return int(self.data.get("missions_completed", 0))

    def add(self, orbs: int, quest_name: str):
        orbs = int(orbs or 0)
        if orbs <= 0:
            return
        self.data["total_orbs"] = self.total_orbs + orbs
        self.data["missions_completed"] = self.missions_completed + 1
        self.data["history"].append({
            "at": datetime.now().isoformat(timespec="seconds"),
            "quest": quest_name,
            "orbs": orbs,
            "total": self.total_orbs,
        })
        self.save()

    def recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        return list(reversed(self.data.get("history", [])[-limit:]))

    def session_balance(self, before: int) -> Dict[str, int]:
        after = self.total_orbs
        return {"before": before, "after": after, "earned": after - before}


balance_tracker = BalanceTracker(DATA_FILE)

class QuestManager:
    def __init__(self, client: DiscordSelfClient):
        self.client = client
        self.quests: List[Quest] = []
        self.excluded_quests: List[Quest] = []
        self.enrollment_blocked_until: Optional[datetime] = None

    def cleanup(self):
        self.quests.clear()
        self.excluded_quests.clear()

    async def fetch_quests(self, include_excluded: bool = False) -> List[Quest]:
        try:
            response = await self.client.get("/quests/@me")
        except TokenExpiredError:
            raise
        except Exception as e:
            log("error", f"Erro ao buscar quests: {e}")
            self.quests = []
            self.excluded_quests = []
            return []
        
        blocked = response.get("quest_enrollment_blocked_until")
        if blocked:
            try:
                self.enrollment_blocked_until = datetime.fromisoformat(blocked.replace("Z", "+00:00"))
            except Exception:
                self.enrollment_blocked_until = None
        else:
            self.enrollment_blocked_until = None

        self.quests = [Quest.from_dict(q) for q in response.get("quests", [])]
        if include_excluded:
            self.excluded_quests = [Quest.from_dict(q) for q in response.get("excluded_quests", [])]
        return self.quests

    async def fetch_orb_balance(self) -> Optional[int]:
        """Tenta ler o saldo real de Orbs na API; retorna None se indisponivel."""
        try:
            response = await self.client.get("/quests/@me")
        except Exception:
            return None
        if not isinstance(response, dict):
            return None
        for container in (response, response.get("user", {}), response.get("balance", {})):
            if not isinstance(container, dict):
                continue
            for key in ("orb_balance", "orbs", "balance", "total_orbs", "orbBalance"):
                val = container.get(key)
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    return int(val)
        return None

    def get_all_quests(self) -> List[Quest]:
        return self.quests

    def get_valid_quests(self) -> List[Quest]:
        return [q for q in self.quests if q.is_valid_to_do()]

    def get_completed_quests(self) -> List[Quest]:
        return [q for q in self.quests if q.is_completed()]

    def get_claimable_quests(self) -> List[Quest]:
        return [q for q in self.quests if q.is_claimable()]

    def get_enrolled_quests(self) -> List[Quest]:
        return [q for q in self.quests if q.is_enrolled()]

    def get_stats(self) -> Dict[str, int]:
        return {
            "total": len(self.quests),
            "valid": len(self.get_valid_quests()),
            "completed": len(self.get_completed_quests()),
            "claimable": len(self.get_claimable_quests()),
            "enrolled": len(self.get_enrolled_quests()),
            "excluded": len(self.excluded_quests),
        }

    async def accept_quest(self, quest: Quest, is_android: bool = False) -> bool:
        # [PROXY] Proteção contra duplicação
        if self.client.user_id and is_quest_already_completed(self.client.user_id, quest.id):
            log("warning", f"[ANTI-DUPLIC] Quest {quest.id} já concluída por {self.client.user_id}. Ignorando.")
            return False
            
        if quest.is_enrolled():
            return True
        if self.enrollment_blocked_until and datetime.now(timezone.utc) < self.enrollment_blocked_until:
            wait = (self.enrollment_blocked_until - datetime.now(timezone.utc)).total_seconds()
            await asyncio.sleep(wait + 5)
        body = {
            "location": QuestContentType.QUEST_HOME_MOBILE.value if is_android else QuestContentType.QUEST_HOME_DESKTOP.value,
            "is_targeted": False,
            "metadata_sealed": None,
            "traffic_metadata_raw": quest.traffic_metadata_raw,
            "traffic_metadata_sealed": quest.traffic_metadata_sealed,
        }
        try:
            response = await self.client.post(f"/quests/{quest.id}/enroll", json=body, is_android=is_android)
            if response:
                quest.update_user_status(response)
                return True
            return False
        except DiscordAPIError as e:
            if e.status == 429:
                retry_after = 2700
                if isinstance(e.data, dict) and "retry_after" in e.data:
                    retry_after = float(e.data["retry_after"])
                await asyncio.sleep(retry_after + 5)
                return await self.accept_quest(quest, is_android)
            else:
                return False
        except TokenExpiredError:
            raise
        except Exception as e:
            return False

    async def claim_reward(self, quest: Quest, callback: Callable = None) -> bool:
        if not quest.is_claimable():
            return False
        body = {
            "location": QuestContentType.QUEST_HOME_DESKTOP.value,
            "platform": 1
        }
        try:
            response = await self.client.post(f"/quests/{quest.id}/claim-reward", json=body)
            if response:
                quest.update_user_status(response)
                if callback:
                    embed = discord.Embed(
                        title="[Recompensa] RECOMPENSA RESGATADA!",
                        description=f"**{quest.quest_name}**\n💎 **{quest.reward_amount} Orbs** recebidos!",
                        color=0xFFD700,
                        timestamp=datetime.now()
                    )
                    if quest.hero_image and is_valid_url(quest.hero_image):
                        embed.set_image(url=quest.hero_image)
                    await callback(embed)
            return True
        except DiscordAPIError as e:
            if e.status == 400 and "captcha" in str(e.data).lower():
                if callback:
                    embed = discord.Embed(
                        title="[Aviso] CAPTCHA DETECTADO",
                        description=f"A quest **{quest.quest_name}** requer verificação manual.\nAcesse o Discord para resgatar.",
                        color=0xFEE75C
                    )
                    await callback(embed)
                return False
            return False
        except TokenExpiredError:
            raise
        except Exception as e:
            return False
        return False

    async def do_watch_video(self, quest: Quest, callback: Callable = None):
        quest_name = quest.quest_name
        task_type = quest.get_task_type()
        if not task_type:
            return False
        seconds_needed = quest.get_task_target()
        speed = bot_config.get("video_speed", 7)
        interval = bot_config.get("video_interval", 1)
        max_future = bot_config.get("video_max_future", 10)
        seconds_done = quest.get_progress_value(task_type)
        enrolled_at = datetime.now(timezone.utc).timestamp()
        if quest.user_status and quest.user_status.get("enrolled_at"):
            try:
                enrolled_at = datetime.fromisoformat(
                    quest.user_status["enrolled_at"].replace("Z", "+00:00")
                ).timestamp()
            except Exception:
                pass
        progress_message = None
        if callback:
            initial_desc = f"[Vídeo] Iniciando: **{quest_name}** ({format_time(seconds_needed)})"
            embed = discord.Embed(description=initial_desc, color=0xFEE75C)
            progress_message = await callback(embed)
        completed = False
        attempts = 0
        max_attempts = 500
        while seconds_done < seconds_needed and attempts < max_attempts:
            attempts += 1
            max_allowed = int(time.time() - enrolled_at) + max_future
            diff = max_allowed - seconds_done
            timestamp = min(seconds_needed, seconds_done + speed)
            if diff < speed:
                wait = speed - diff + 1
                await asyncio.sleep(wait)
                continue
            try:
                response = await self.client.post(
                    f"/quests/{quest.id}/video-progress",
                    json={"timestamp": timestamp}
                )
                if response:
                    quest.update_user_status(response)
                    new_val = quest.get_progress_value(task_type)
                    if new_val > seconds_done:
                        seconds_done = new_val
                    if response.get("completed_at"):
                        completed = True
                        break
                if callback and progress_message:
                    percent = min(100, int((seconds_done / seconds_needed) * 100))
                    bar = make_progress_bar(percent)
                    desc = f"[Vídeo] **{quest_name}**\n{bar}"
                    embed = discord.Embed(description=desc, color=0xFEE75C)
                    progress_message = await callback(embed, progress_message)
            except TokenExpiredError:
                raise
            except DiscordAPIError as e:
                if e.status == 429:
                    retry_after = float(e.data.get("retry_after", 1.0))
                    await asyncio.sleep(retry_after + 1)
                    continue
                else:
                    log("error", f"Erro API no progresso de vídeo: {e}")
            except Exception as e:
                log("error", f"Erro inesperado no heartbeat: {e}")
            await asyncio.sleep(interval)
        if not completed and seconds_done >= seconds_needed:
            try:
                response = await self.client.post(
                    f"/quests/{quest.id}/video-progress",
                    json={"timestamp": seconds_needed}
                )
                if response:
                    quest.update_user_status(response)
                    if response.get("completed_at"):
                        completed = True
            except TokenExpiredError:
                raise
            except Exception as e:
                log("warning", f"Erro no envio final de progresso para {quest_name}: {e}")
        if completed and callback:
            try:
                bar = make_progress_bar(100)
                desc = f"[Vídeo] **{quest_name}**\n{bar}\n[OK] Missão completada com sucesso!"
                embed = discord.Embed(description=desc, color=0x57F287, timestamp=datetime.now())
                embed.set_footer(text="Monsters Orbs")
                if progress_message:
                    await callback(embed, progress_message)
                else:
                    await callback(embed)
            except Exception as e:
                log("warning", f"Erro ao enviar callback 100% final: {e}")
        return completed

    async def do_play_on_platform(self, quest: Quest, callback: Callable = None):
        quest_name = quest.quest_name
        app_name = quest.application_name
        task_type = quest.get_task_type()
        if not task_type:
            return False
        seconds_needed = quest.get_task_target()
        app_id = quest.application_id
        progress_message = None
        if callback:
            initial_desc = f"[Plataforma] Iniciando: **{quest_name}** ({app_name})"
            embed = discord.Embed(description=initial_desc, color=0xFEE75C)
            progress_message = await callback(embed)
        if quest.is_completed():
            if callback:
                desc = f"[OK] **{quest_name}** já estava completada!"
                embed = discord.Embed(description=desc, color=0x57F287)
                await callback(embed)
            return True
        max_heartbeats = 500
        heartbeat_count = 0
        initial_progress = quest.get_progress_value(task_type)
        while not quest.is_completed() and heartbeat_count < max_heartbeats:
            heartbeat_count += 1
            try:
                delay = behavioral_delay()
                response = await self.client.post(
                    f"/quests/{quest.id}/heartbeat",
                    json={"application_id": app_id, "terminal": False}
                )
                quest.update_user_status(response)
                if response.get("completed_at"):
                    if callback and progress_message:
                        bar = make_progress_bar(100)
                        desc = f"[Plataforma] **{quest_name}**\n{bar}"
                        embed = discord.Embed(description=desc, color=0x57F287)
                        await callback(embed, progress_message)
                    elif callback:
                        desc = f"[OK] **{quest_name}** completada!"
                        embed = discord.Embed(description=desc, color=0x57F287)
                        await callback(embed)
                    return True
                new_done = quest.get_progress_value(task_type)
                if new_done > initial_progress:
                    if callback and progress_message:
                        percent = min(100, int((new_done / seconds_needed) * 100))
                        bar = make_progress_bar(percent)
                        time_str = format_time(new_done)
                        total_str = format_time(seconds_needed)
                        desc = f"[Plataforma] **{quest_name}**: {time_str} / {total_str}\n{bar}"
                        embed = discord.Embed(description=desc, color=0xFEE75C)
                        progress_message = await callback(embed, progress_message)
                    initial_progress = new_done
            except TokenExpiredError:
                raise
            except DiscordAPIError as e:
                if e.status == 429:
                    retry_after = float(e.data.get("retry_after", 1.0))
                    await asyncio.sleep(retry_after + 1)
                    continue
                else:
                    log("error", f"Erro API no heartbeat: {e}")
            except Exception as e:
                log("error", f"Erro inesperado no heartbeat: {e}")
            await asyncio.sleep(delay)
        try:
            terminal_resp = await self.client.post(
                f"/quests/{quest.id}/heartbeat",
                json={"application_id": app_id, "terminal": True}
            )
            if terminal_resp:
                quest.update_user_status(terminal_resp)
        except TokenExpiredError:
            raise
        except Exception as e:
            log("warning", f"Erro no heartbeat terminal: {e}")
        if quest.is_completed() and callback and progress_message:
            try:
                bar = make_progress_bar(100)
                desc = f"[Plataforma] **{quest_name}**\n{bar}\n[OK] Missão completada com sucesso!"
                embed = discord.Embed(description=desc, color=0x57F287, timestamp=datetime.now())
                embed.set_footer(text="Monsters Orbs")
                await callback(embed, progress_message)
            except Exception as e:
                log("warning", f"Erro ao enviar callback 100% final: {e}")
        return quest.is_completed()

    async def do_play_activity(self, quest: Quest, callback: Callable = None):
        quest_name = quest.quest_name
        app_name = quest.application_name
        task_type = quest.get_task_type()
        if not task_type:
            return False
        seconds_needed = quest.get_task_target()
        stream_key = f"call:{quest.id}:1"
        progress_message = None
        if callback:
            initial_desc = f"[Atividade] Iniciando: **{quest_name}** ({app_name})"
            embed = discord.Embed(description=initial_desc, color=0xFEE75C)
            progress_message = await callback(embed)
        if quest.is_completed():
            if callback:
                desc = f"[OK] **{quest_name}** já estava completada!"
                embed = discord.Embed(description=desc, color=0x57F287)
                await callback(embed)
            return True
        max_heartbeats = 500
        heartbeat_count = 0
        initial_progress = quest.get_progress_value(task_type)
        while not quest.is_completed() and heartbeat_count < max_heartbeats:
            heartbeat_count += 1
            try:
                delay = behavioral_delay()
                response = await self.client.post(
                    f"/quests/{quest.id}/heartbeat",
                    json={"stream_key": stream_key, "terminal": False}
                )
                quest.update_user_status(response)
                if response.get("completed_at"):
                    if callback and progress_message:
                        bar = make_progress_bar(100)
                        desc = f"[Atividade] **{quest_name}**\n{bar}"
                        embed = discord.Embed(description=desc, color=0x57F287)
                        await callback(embed, progress_message)
                    elif callback:
                        desc = f"[OK] **{quest_name}** completada!"
                        embed = discord.Embed(description=desc, color=0x57F287)
                        await callback(embed)
                    return True
                new_done = quest.get_progress_value(task_type)
                if new_done > initial_progress:
                    if callback and progress_message:
                        percent = min(100, int((new_done / seconds_needed) * 100))
                        bar = make_progress_bar(percent)
                        time_str = format_time(new_done)
                        total_str = format_time(seconds_needed)
                        desc = f"[Atividade] **{quest_name}**: {time_str} / {total_str}\n{bar}"
                        embed = discord.Embed(description=desc, color=0xFEE75C)
                        progress_message = await callback(embed, progress_message)
                    initial_progress = new_done
            except TokenExpiredError:
                raise
            except DiscordAPIError as e:
                if e.status == 429:
                    retry_after = float(e.data.get("retry_after", 1.0))
                    await asyncio.sleep(retry_after + 1)
                    continue
                else:
                    log("error", f"Erro API no heartbeat de atividade: {e}")
            except Exception as e:
                log("error", f"Erro inesperado no heartbeat de atividade: {e}")
            await asyncio.sleep(delay)
        try:
            await self.client.post(
                f"/quests/{quest.id}/heartbeat",
                json={"stream_key": stream_key, "terminal": True}
            )
        except TokenExpiredError:
            raise
        except Exception as e:
            log("warning", f"Erro no heartbeat terminal de atividade: {e}")
        return quest.is_completed()

    async def do_stream_on_desktop(self, quest: Quest, callback: Callable = None) -> bool:
        quest_name = quest.quest_name
        app_name = quest.application_name
        task_type = quest.get_task_type()
        if not task_type:
            return False
        seconds_needed = quest.get_task_target()
        stream_key = f"guild:0:{quest.id}:0"
        progress_message = None
        if callback:
            initial_desc = f"[Stream] Iniciando: **{quest_name}** ({app_name})"
            embed = discord.Embed(description=initial_desc, color=0xFEE75C)
            progress_message = await callback(embed)
        if quest.is_completed():
            if callback:
                desc = f"[OK] **{quest_name}** já estava completada!"
                embed = discord.Embed(description=desc, color=0x57F287)
                await callback(embed)
            return True
        max_heartbeats = 500
        heartbeat_count = 0
        initial_progress = quest.get_progress_value(task_type)
        while not quest.is_completed() and heartbeat_count < max_heartbeats:
            heartbeat_count += 1
            try:
                delay = behavioral_delay(mean=30, std=5, min_d=15, max_d=60)
                response = await self.client.post(
                    f"/quests/{quest.id}/heartbeat",
                    json={"stream_key": stream_key, "terminal": False}
                )
                quest.update_user_status(response)
                if response.get("completed_at"):
                    if callback and progress_message:
                        bar = make_progress_bar(100)
                        desc = f"[Stream] **{quest_name}**\n{bar}"
                        embed = discord.Embed(description=desc, color=0x57F287)
                        await callback(embed, progress_message)
                    elif callback:
                        desc = f"[OK] **{quest_name}** completada!"
                        embed = discord.Embed(description=desc, color=0x57F287)
                        await callback(embed)
                    return True
                new_done = quest.get_progress_value(task_type)
                if new_done > initial_progress:
                    if callback and progress_message:
                        percent = min(100, int((new_done / seconds_needed) * 100))
                        bar = make_progress_bar(percent)
                        time_str = format_time(new_done)
                        total_str = format_time(seconds_needed)
                        desc = f"[Stream] **{quest_name}**: {time_str} / {total_str}\n{bar}"
                        embed = discord.Embed(description=desc, color=0xFEE75C)
                        progress_message = await callback(embed, progress_message)
                    initial_progress = new_done
            except TokenExpiredError:
                raise
            except DiscordAPIError as e:
                if e.status == 429:
                    retry_after = float(e.data.get("retry_after", 1.0))
                    await asyncio.sleep(retry_after + 1)
                    continue
                else:
                    log("error", f"Erro API no heartbeat de stream: {e}")
            except Exception as e:
                log("error", f"Erro inesperado no heartbeat de stream: {e}")
            await asyncio.sleep(delay)
        try:
            await self.client.post(
                f"/quests/{quest.id}/heartbeat",
                json={"stream_key": stream_key, "terminal": True}
            )
        except TokenExpiredError:
            raise
        except Exception as e:
            log("warning", f"Erro no heartbeat terminal de stream: {e}")
        return quest.is_completed()

    async def do_achievement(self, quest: Quest, callback: Callable = None):
        quest_name = quest.quest_name
        app_id = quest.application_id
        app_name = quest.application_name
        task = quest.task_config.get("ACHIEVEMENT_IN_ACTIVITY", {})
        quest_target = task.get("target", 1)
        progress_message = None
        if callback:
            initial_desc = f"[Conquista] Iniciando: **{quest_name}** ({app_name})"
            embed = discord.Embed(description=initial_desc, color=0xFEE75C)
            progress_message = await callback(embed)
        try:
            auth_response = await self.client.post(
                "/oauth2/authorize",
                params={
                    "response_type": "code",
                    "client_id": app_id,
                    "scope": "identify applications.commands applications.entitlements",
                    "state": ""
                },
                json={
                    "permissions": "0",
                    "authorize": True,
                    "integration_type": 1,
                    "location_context": {
                        "guild_id": "10000",
                        "channel_id": "10000",
                        "channel_type": 10000
                    }
                }
            )
            location = auth_response.get("location", "")
            auth_code = None
            if location:
                match = re.search(r'code=([^&]+)', location)
                if match:
                    auth_code = match.group(1)
            if not auth_code:
                if callback:
                    desc = f"[Erro] Falha ao obter código de autorização para {app_name}"
                    embed = discord.Embed(description=desc, color=0xED4245)
                    await callback(embed)
                return False
        except TokenExpiredError:
            raise
        except Exception as e:
            if callback:
                desc = f"[Erro] Erro OAuth2 em {app_name}: {str(e)[:100]}"
                embed = discord.Embed(description=desc, color=0xED4245)
                await callback(embed)
            return False
        try:
            ticket_resp = await self.client.post(f"/applications/{app_id}/proxy-tickets", json={})
            proxy_ticket = ticket_resp.get("ticket")
            if not proxy_ticket:
                if callback:
                    desc = f"[Erro] Falha ao obter proxy ticket para {app_name}"
                    embed = discord.Embed(description=desc, color=0xED4245)
                    await callback(embed)
                return False
            referrer = f"https://{app_id}.discordsays.com/?instance_id=example-cl-instance&platform=desktop&discord_proxy_ticket={proxy_ticket}"
            auth_headers = {
                "Content-Type": "application/json",
                "X-Auth-Token": "",
                "X-Discord-Quest-ID": quest.id,
                "Referer": referrer,
                "User-Agent": Constants.USER_AGENT,
            }
            async with aiohttp.ClientSession(timeout=self.client.timeout) as sess:
                async with sess.post(
                    f"https://{app_id}.discordsays.com/.proxy/acf/authorize",
                    headers=auth_headers,
                    json={"code": auth_code}
                ) as resp:
                    auth_data = await resp.json()
                    token = auth_data.get("token")
                    if not token:
                        if callback:
                            desc = f"[Erro] Falha ao obter token de autenticação para {app_name}"
                            embed = discord.Embed(description=desc, color=0xED4245)
                            await callback(embed)
                        return False
                    auth_headers["X-Auth-Token"] = token
                    async with aiohttp.ClientSession(timeout=self.client.timeout) as sess:
                        async with sess.post(
                            f"https://{app_id}.discordsays.com/.proxy/acf/quest/progress",
                            headers=auth_headers,
                            json={"progress": quest_target}
                        ) as resp:
                            success = resp.status < 400
                            if not success:
                                if callback:
                                    desc = f"[Erro] Falha ao enviar progresso para {app_name}"
                                    embed = discord.Embed(description=desc, color=0xED4245)
                                    await callback(embed)
                                return False
        except TokenExpiredError:
            raise
        except Exception as e:
            if callback:
                desc = f"[Erro] Erro na execução da conquista: {str(e)[:100]}"
                embed = discord.Embed(description=desc, color=0xED4245)
                await callback(embed)
            return False
        try:
            tokens = await self.client.get("/oauth2/tokens")
            for t in tokens:
                if t.get("application", {}).get("id") == app_id:
                    await self.client.delete(f"/oauth2/tokens/{t['id']}")
                    break
        except TokenExpiredError:
            raise
        except Exception as e:
            log("warning", f"Erro ao revogar token OAuth2 para app {app_id}: {e}")
        if callback and progress_message:
            bar = make_progress_bar(100)
            desc = f"[Conquista] **{quest_name}**\n{bar}"
            embed = discord.Embed(description=desc, color=0x57F287)
            await callback(embed, progress_message)
        elif callback:
            desc = f"[OK] **{quest_name}** completada!"
            embed = discord.Embed(description=desc, color=0x57F287)
            await callback(embed)
        return True

    async def process_all_quests_parallel(self, callback=None):
        valid_quests = self.get_valid_quests()
        if not valid_quests:
            return {"success": 0, "failed": 0, "total": 0}

        engine = StealthParallelEngine()
        max_concurrent = bot_config.get("parallel_max_concurrent", 5)
        if len(valid_quests) > max_concurrent:
            valid_quests = valid_quests[:max_concurrent]

        results = {"success": 0, "failed": 0, "total": len(valid_quests)}
        running_tasks = []

        for i, quest in enumerate(valid_quests):
            offset = engine.staggered_delay(i, len(valid_quests))

            async def run_with_offset(q, idx, delay_offset):
                await asyncio.sleep(delay_offset)
                if not q.is_enrolled():
                    await asyncio.sleep(random.uniform(1.5, 4.0))
                success = await self.accept_quest(q)
                if not success:
                    return False
                return await self._process_single_quest_stealth(q, engine, callback)

            task = asyncio.create_task(run_with_offset(quest, i, offset))
            running_tasks.append(task)

        outcomes = await asyncio.gather(*running_tasks, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, Exception):
                results["failed"] += 1
            elif outcome:
                results["success"] += 1
                # [PROXY] Marca como concluída globalmente
                if self.client.user_id:
                    mark_quest_completed(self.client.user_id, quest.id)
            else:
                results["failed"] += 1
        return results

    async def _process_single_quest_stealth(self, quest, engine, callback=None):
        # [PROXY] Verificação anti-duplicação
        if self.client.user_id and is_quest_already_completed(self.client.user_id, quest.id):
            log("warning", f"[ANTI-DUPLIC] Quest {quest.id} já concluída por {self.client.user_id}. Ignorando.")
            return False

        task_type = quest.get_task_type()
        if not task_type:
            return False

        handlers = {
            "WATCH_VIDEO": self._do_video_parallel,
            "WATCH_VIDEO_ON_MOBILE": self._do_video_parallel,
            "PLAY_ON_DESKTOP": self._do_play_parallel,
            "PLAY_ON_DESKTOP_V2": self._do_play_parallel,
            "PLAY_ON_XBOX": self._do_play_parallel,
            "PLAY_ON_PLAYSTATION": self._do_play_parallel,
            "STREAM_ON_DESKTOP": self._do_stream_parallel,
            "PLAY_ACTIVITY": self._do_activity_parallel,
            "ACHIEVEMENT_IN_ACTIVITY": self._do_achievement_parallel,
        }
        handler = handlers.get(task_type)
        if handler:
            result = await handler(quest, engine, callback)
            if result and self.client.user_id:
                mark_quest_completed(self.client.user_id, quest.id)
            return result
        return False

    async def _do_play_parallel(self, quest, engine, callback=None):
        task_type = quest.get_task_type()
        seconds_needed = quest.get_task_target()
        app_id = quest.application_id
        quest_name = quest.quest_name
        seconds_done = quest.get_progress_value(task_type)
        try:
            await self.client.post(
                f"/quests/{quest.id}/heartbeat",
                json={"application_id": app_id, "terminal": False}
            )
        except Exception as e:
            log("warning", f"[{quest_name}] Heartbeat inicial falhou: {e}")
        max_heartbeats = 500
        heartbeat_count = 0
        while not quest.is_completed() and heartbeat_count < max_heartbeats:
            heartbeat_count += 1
            delay = engine.heartbeat_delay(quest.id, task_type)
            hour = datetime.now().hour
            if hour < 8 or hour > 23:
                delay *= 1.5
            await asyncio.sleep(delay)
            try:
                response = await self.client.post(
                    f"/quests/{quest.id}/heartbeat",
                    json={"application_id": app_id, "terminal": False}
                )
                quest.update_user_status(response)
                if response.get("completed_at"):
                    return True
                new_done = quest.get_progress_value(task_type)
                if callback and new_done > seconds_done:
                    percent = min(100, int((new_done / seconds_needed) * 100)) if seconds_needed > 0 else 0
                    await callback(f"[Plataforma] {quest_name}: {percent}%")
                seconds_done = new_done
            except TokenExpiredError:
                raise
            except DiscordAPIError as e:
                if e.status == 429:
                    retry_after = float(e.data.get("retry_after", 1.0))
                    await asyncio.sleep(retry_after + 1)
                    continue
                else:
                    log("error", f"[{quest_name}] Erro API: {e}")
            except Exception as e:
                log("error", f"[{quest_name}] Erro heartbeat: {e}")
            await asyncio.sleep(random.uniform(10, 20))
        try:
            await self.client.post(
                f"/quests/{quest.id}/heartbeat",
                json={"application_id": app_id, "terminal": True}
            )
        except Exception as e:
            log("warning", f"[{quest_name}] Terminal falhou: {e}")
        return quest.is_completed()

    async def _do_video_parallel(self, quest, engine, callback=None):
        task_type = quest.get_task_type()
        seconds_needed = quest.get_task_target()
        quest_name = quest.quest_name
        speed = bot_config.get("video_speed", 7)
        max_future = bot_config.get("video_max_future", 10)
        seconds_done = quest.get_progress_value(task_type)
        enrolled_at = datetime.now(timezone.utc).timestamp()
        if quest.user_status and quest.user_status.get("enrolled_at"):
            try:
                enrolled_at = datetime.fromisoformat(
                    quest.user_status["enrolled_at"].replace("Z", "+00:00")
                ).timestamp()
            except Exception:
                pass
        completed = False
        attempts = 0
        max_attempts = 500
        while seconds_done < seconds_needed and attempts < max_attempts:
            attempts += 1
            max_allowed = int(time.time() - enrolled_at) + max_future
            diff = max_allowed - seconds_done
            timestamp = min(seconds_needed, seconds_done + speed)
            if diff < speed:
                wait = speed - diff + random.uniform(0.5, 1.5)
                await asyncio.sleep(wait)
                continue
            jittered_timestamp = engine.video_timestamp_jitter(timestamp)
            try:
                response = await self.client.post(
                    f"/quests/{quest.id}/video-progress",
                    json={"timestamp": jittered_timestamp}
                )
                if response:
                    quest.update_user_status(response)
                    new_val = quest.get_progress_value(task_type)
                    if new_val > seconds_done:
                        seconds_done = new_val
                    if callback:
                        percent = min(100, int((seconds_done / seconds_needed) * 100)) if seconds_needed > 0 else 0
                        await callback(f"[Vídeo] {quest_name}: {percent}%")
                    if response.get("completed_at"):
                        completed = True
                        break
            except TokenExpiredError:
                raise
            except DiscordAPIError as e:
                if e.status == 429:
                    retry_after = float(e.data.get("retry_after", 1.0))
                    await asyncio.sleep(retry_after + 1)
                    continue
                else:
                    log("error", f"[{quest_name}] Erro vídeo: {e}")
            except Exception as e:
                log("warning", f"[{quest_name}] Erro vídeo: {e}")
            await asyncio.sleep(random.uniform(2, 5))
            video_interval = engine.heartbeat_delay(quest.id, "WATCH_VIDEO")
            await asyncio.sleep(video_interval)
        if not completed and seconds_done >= seconds_needed:
            try:
                final_ts = engine.video_timestamp_jitter(seconds_needed)
                response = await self.client.post(
                    f"/quests/{quest.id}/video-progress",
                    json={"timestamp": final_ts}
                )
                if response and response.get("completed_at"):
                    completed = True
            except Exception as e:
                log("warning", f"[{quest_name}] Final vídeo falhou: {e}")
        return completed

    async def _do_stream_parallel(self, quest, engine, callback=None):
        task_type = quest.get_task_type()
        seconds_needed = quest.get_task_target()
        quest_name = quest.quest_name
        stream_key = f"guild:0:{quest.id}:{random.randint(1000, 9999)}"
        seconds_done = quest.get_progress_value(task_type)
        max_heartbeats = 500
        heartbeat_count = 0
        while not quest.is_completed() and heartbeat_count < max_heartbeats:
            heartbeat_count += 1
            delay = engine.heartbeat_delay(quest.id, "STREAM_ON_DESKTOP")
            await asyncio.sleep(delay)
            try:
                response = await self.client.post(
                    f"/quests/{quest.id}/heartbeat",
                    json={"stream_key": stream_key, "terminal": False}
                )
                quest.update_user_status(response)
                if response.get("completed_at"):
                    return True
                new_done = quest.get_progress_value(task_type)
                if callback and new_done > seconds_done:
                    percent = min(100, int((new_done / seconds_needed) * 100)) if seconds_needed > 0 else 0
                    await callback(f"[Stream] {quest_name}: {percent}%")
                seconds_done = new_done
            except TokenExpiredError:
                raise
            except DiscordAPIError as e:
                if e.status == 429:
                    retry_after = float(e.data.get("retry_after", 1.0))
                    await asyncio.sleep(retry_after + 1)
                    continue
                else:
                    log("error", f"[{quest_name}] Stream erro: {e}")
            except Exception as e:
                log("error", f"[{quest_name}] Stream erro: {e}")
            await asyncio.sleep(random.uniform(15, 30))
        try:
            await self.client.post(
                f"/quests/{quest.id}/heartbeat",
                json={"stream_key": stream_key, "terminal": True}
            )
        except Exception:
            pass
        return quest.is_completed()

    async def _do_activity_parallel(self, quest, engine, callback=None):
        task_type = quest.get_task_type()
        seconds_needed = quest.get_task_target()
        quest_name = quest.quest_name
        stream_key = f"call:{quest.id}:{random.randint(1, 5)}"
        seconds_done = quest.get_progress_value(task_type)
        max_heartbeats = 500
        heartbeat_count = 0
        while not quest.is_completed() and heartbeat_count < max_heartbeats:
            heartbeat_count += 1
            delay = engine.heartbeat_delay(quest.id, "PLAY_ACTIVITY")
            await asyncio.sleep(delay)
            try:
                response = await self.client.post(
                    f"/quests/{quest.id}/heartbeat",
                    json={"stream_key": stream_key, "terminal": False}
                )
                quest.update_user_status(response)
                if response.get("completed_at"):
                    return True
                new_done = quest.get_progress_value(task_type)
                if callback and new_done > seconds_done:
                    percent = min(100, int((new_done / seconds_needed) * 100)) if seconds_needed > 0 else 0
                    await callback(f"[Atividade] {quest_name}: {percent}%")
                seconds_done = new_done
            except TokenExpiredError:
                raise
            except DiscordAPIError as e:
                if e.status == 429:
                    retry_after = float(e.data.get("retry_after", 1.0))
                    await asyncio.sleep(retry_after + 1)
                    continue
                else:
                    log("error", f"[{quest_name}] Activity erro: {e}")
            except Exception as e:
                log("error", f"[{quest_name}] Activity erro: {e}")
            await asyncio.sleep(random.uniform(10, 20))
        try:
            await self.client.post(
                f"/quests/{quest.id}/heartbeat",
                json={"stream_key": stream_key, "terminal": True}
            )
        except Exception:
            pass
        return quest.is_completed()

    async def _do_achievement_parallel(self, quest, engine, callback=None):
        return await self.do_achievement(quest, callback)

    async def process_quest(self, quest: Quest, callback: Callable = None) -> bool:
        # [PROXY] Verificação anti-duplicação
        if self.client.user_id and is_quest_already_completed(self.client.user_id, quest.id):
            log("warning", f"[ANTI-DUPLIC] Quest {quest.id} já concluída por {self.client.user_id}. Ignorando.")
            if callback:
                desc = f"[Ignorado] Quest **{quest.quest_name}** já foi concluída anteriormente."
                embed = discord.Embed(description=desc, color=0xFEE75C)
                await callback(embed)
            return False

        task_type = quest.get_task_type()
        if not task_type:
            if callback:
                desc = f"[Erro] Tipo de tarefa desconhecido: {quest.quest_name}"
                embed = discord.Embed(description=desc, color=0xED4245)
                await callback(embed)
            return False

        is_android = task_type == "WATCH_VIDEO_ON_MOBILE"
        if not quest.is_enrolled():
            if callback:
                desc = f"[Inscrição] Aceitando: **{quest.quest_name}**..."
                embed = discord.Embed(description=desc, color=0xFEE75C)
                await callback(embed)
            success = await self.accept_quest(quest, is_android)
            if not success:
                if callback:
                    desc = f"[Erro] Falha ao aceitar quest: {quest.quest_name}"
                    embed = discord.Embed(description=desc, color=0xED4245)
                    await callback(embed)
                return False

        handlers = {
            "WATCH_VIDEO": self.do_watch_video,
            "WATCH_VIDEO_ON_MOBILE": self.do_watch_video,
            "PLAY_ON_DESKTOP": self.do_play_on_platform,
            "PLAY_ON_DESKTOP_V2": self.do_play_on_platform,
            "PLAY_ON_XBOX": self.do_play_on_platform,
            "PLAY_ON_PLAYSTATION": self.do_play_on_platform,
            "PLAY_ACTIVITY": self.do_play_activity,
            "ACHIEVEMENT_IN_ACTIVITY": self.do_achievement,
            "STREAM_ON_DESKTOP": self.do_stream_on_desktop,
        }
        handler = handlers.get(task_type)
        if handler:
            result = await handler(quest, callback)
            if result and bot_config.get("auto_claim_rewards", True):
                await asyncio.sleep(2)
                claim_result = await self.claim_reward(quest, callback)
            # [PROXY] Marca como concluída após sucesso total
            if result and self.client.user_id:
                mark_quest_completed(self.client.user_id, quest.id)
            return result
        if callback:
            desc = f"[Erro] Task não suportada: {task_type} na quest {quest.quest_name}"
            embed = discord.Embed(description=desc, color=0xED4245)
            await callback(embed)
        return False

class UserSessionManager:
    def __init__(self):
        self.sessions: Dict[str, Dict] = {}

    def add_session(self, user_id: str, token: str, client: DiscordSelfClient, manager: QuestManager):
        self.sessions[user_id] = {
            "token": token,
            "client": client,
            "manager": manager,
            "processing_semaphore": asyncio.Semaphore(1),
            "manual_task": None,
            "auto_task": None,
            "current_manual_quest_id": None
        }

    def get_session(self, user_id: str) -> Optional[Dict]:
        return self.sessions.get(user_id)

    async def remove_session_async(self, user_id: str):
        session = self.sessions.pop(user_id, None)
        if session:
            try:
                client = session.get("client")
                if client:
                    await client.close()
            except Exception as e:
                log("warning", f"Erro ao fechar cliente na remoção da sessão {user_id}: {e}")
            finally:
                session.clear()

    def remove_session(self, user_id: str):
        session = self.sessions.pop(user_id, None)
        if session:
            try:
                client = session.get("client")
                if client:
                    async def _close():
                        try:
                            await client.close()
                        except Exception as e:
                            log("warning", f"Erro ao fechar cliente {user_id}: {e}")
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(_close())
                    except RuntimeError:
                        pass
            except Exception as e:
                log("warning", f"Erro ao agendar fechamento da sessão {user_id}: {e}")
            finally:
                session.clear()

    def is_logged_in(self, user_id: str) -> bool:
        return user_id in self.sessions

session_manager = UserSessionManager()

# ==============================
# Função de verificação de acesso ao servidor original
# ==============================
async def check_access(bot_instance: commands.Bot, user_id: int) -> bool:
    """Acesso restrito ao dono do bot (uso pessoal)."""
    if OWNER_ID:
        return user_id == OWNER_ID
    try:
        app_info = await bot_instance.application_info()
        return user_id == app_info.owner.id
    except Exception:
        return False

class TokenModal(ui.Modal, title="[Segurança] Login com Token"):
    token_input = ui.TextInput(
        label="Token de Usuário do Discord",
        placeholder="Cole seu token aqui...",
        style=discord.TextStyle.paragraph,
        required=True,
        min_length=20,
        max_length=200
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Verificar se o usuário está no servidor original
        if not await check_access(interaction.client, interaction.user.id):
            embed = discord.Embed(
                title="[Acesso Negado]",
                description="Este bot é de uso pessoal e restrito ao dono.",
                color=0xED4245
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        token = self.token_input.value.strip()
        user_id = str(interaction.user.id)
        if session_manager.is_logged_in(user_id):
            embed = discord.Embed(
                title="[Erro] Você já está logado!",
                description="Faça logout antes de fazer login com outro token.",
                color=0xED4245
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            async with DiscordSelfClient(token, user_id=user_id) as client:  # [PROXY] passa user_id
                await client.update_build_number()
                me = await client.get("/users/@me")
                username = me.get("username", "Usuário")
                discriminator = me.get("discriminator", "0")
                user_discord_id = me.get("id", "N/A")
                avatar_hash = me.get("avatar")
                avatar_url = f"https://cdn.discordapp.com/avatars/{user_discord_id}/{avatar_hash}.png" if avatar_hash else None
                manager = QuestManager(client)
                await manager.fetch_quests()
                valid_quests = manager.get_valid_quests()
                stats = manager.get_stats()
                session_manager.add_session(user_id, token, client, manager)
                embed = discord.Embed(
                    title="[OK] Login Bem-Sucedido",
                    description=f"Conectado como: **{username}#{discriminator}**",
                    color=0x57F287,
                    timestamp=datetime.now()
                )
                embed.add_field(name="ID", value=f"`{user_discord_id}`", inline=True)
                embed.add_field(name="Missões Pendentes", value=f"`{len(valid_quests)}`", inline=True)
                embed.add_field(name="Completas", value=f"`{stats['completed']}`", inline=True)
                embed.add_field(name="Saldo (histórico)", value=f"`{balance_tracker.total_orbs}` 💎", inline=True)
                if avatar_url:
                    embed.set_thumbnail(url=avatar_url)
                embed.set_footer(text="Monsters Orbs")
                await interaction.followup.send(embed=embed, ephemeral=True)
        except TokenExpiredError as e:
            embed = discord.Embed(
                title="[Erro] Token Inválido ou Expirado",
                description=str(e),
                color=0xED4245
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            error_msg = str(e)
            if "401" in error_msg or "Unauthorized" in error_msg:
                error_msg = "O token está inválido ou expirado!"
            embed = discord.Embed(
                title="[Erro] Falha na Autenticação",
                description=f"Não foi possível conectar. `{error_msg[:150]}`",
                color=0xED4245
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

class QuestSelect(ui.Select):
    def __init__(self, user_quests: List[Quest]):
        options = []
        for quest in user_quests[:25]:
            orb_text = f" 💎{quest.reward_amount}" if quest.reward_amount > 0 else ""
            label = f"{quest.quest_name[:80]}{orb_text}"
            desc = f"{quest.get_task_type() or 'Unknown'} | {format_time(quest.get_progress_value(quest.get_task_type()))}/{format_time(quest.get_task_target())}"
            options.append(discord.SelectOption(
                label=label,
                value=quest.id,
                description=desc[:100]
            ))
        super().__init__(placeholder="[Seleção] Selecione uma missão...", options=options)

    async def callback(self, interaction: discord.Interaction):
        # Verificar se o usuário está no servidor original
        if not await check_access(interaction.client, interaction.user.id):
            embed = discord.Embed(
                title="[Acesso Negado]",
                description="Este bot é de uso pessoal e restrito ao dono.",
                color=0xED4245
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        user_id = str(interaction.user.id)
        session = session_manager.get_session(user_id)
        if not session:
            embed = discord.Embed(title="[Erro] Você não está logado!", description="Use o botão 'Login' primeiro.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        selected_quest_id = self.values[0]
        selected_quest = next((q for q in session["manager"].get_valid_quests() if q.id == selected_quest_id), None)
        if not selected_quest:
            embed = discord.Embed(title="[Erro] Missão não encontrada.", description="A missão pode não estar mais disponível.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if session["auto_task"] and not session["auto_task"].done():
            embed = discord.Embed(title="[Erro] Processamento em Andamento", description="Aguarde ou cancele o processamento automático.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        if session["manual_task"] and not session["manual_task"].done():
            embed = discord.Embed(title="[Erro] Missão em Processamento", description="Aguarde a conclusão da missão atual.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        session["current_manual_quest_id"] = selected_quest.id

        last_progress_msg = None
        async def manual_callback(embed_or_str, message_to_edit=None):
            nonlocal last_progress_msg
            try:
                if isinstance(embed_or_str, str):
                    embed = discord.Embed(description=embed_or_str, color=0xFEE75C)
                else:
                    embed = embed_or_str
                if message_to_edit:
                    await message_to_edit.edit(embed=embed)
                    last_progress_msg = message_to_edit
                    return message_to_edit
                else:
                    msg = await interaction.followup.send(embed=embed, ephemeral=True)
                    last_progress_msg = msg
                    return msg
            except discord.NotFound:
                try:
                    msg = await interaction.followup.send(embed=embed, ephemeral=True)
                    last_progress_msg = msg
                    return msg
                except Exception as followup_e:
                    log("error", f"Erro ao enviar mensagem de callback via followup para {user_id}: {followup_e}")
                    return None
            except Exception as e:
                log("warning", f"Erro ao editar mensagem de callback para {user_id}: {e}")
                try:
                    msg = await interaction.followup.send(embed=embed, ephemeral=True)
                    last_progress_msg = msg
                    return msg
                except Exception as followup_e:
                    log("error", f"Erro ao enviar mensagem de callback via followup para {user_id}: {followup_e}")
                    return None

        async def run_manual_quest():
            result = False
            try:
                await session["processing_semaphore"].acquire()
                result = await session["manager"].process_quest(selected_quest, callback=manual_callback)
                if result:
                    await asyncio.sleep(1)
                    if last_progress_msg:
                        try:
                            bar = make_progress_bar(100)
                            desc = f"[OK] **{selected_quest.quest_name}** COMPLETADA!\n{bar}\n[Recompensa] Recompensa resgatada automaticamente."
                            embed = discord.Embed(description=desc, color=0x57F287, timestamp=datetime.now())
                            embed.set_footer(text="Monsters Orbs | Missão Finalizada")
                            await last_progress_msg.edit(embed=embed)
                        except Exception as e:
                            log("warning", f"Erro ao enviar mensagem 100% final: {e}")
                    try:
                        bar = make_progress_bar(100)
                        desc = f"[OK] **{selected_quest.quest_name}** COMPLETADA!\n{bar}"
                        embed = discord.Embed(description=desc, color=0x57F287)
                        await interaction.followup.send(embed=embed, ephemeral=True)
                    except Exception as e2:
                        log("error", f"Erro fallback 100%: {e2}")
                else:
                    try:
                        desc = f"[Erro] **{selected_quest.quest_name}** não foi concluída."
                        embed = discord.Embed(description=desc, color=0xED4245)
                        if last_progress_msg:
                            await last_progress_msg.edit(embed=embed)
                        else:
                            await interaction.followup.send(embed=embed, ephemeral=True)
                    except Exception as e:
                        log("error", f"Erro ao enviar mensagem de falha: {e}")
            except TokenExpiredError:
                log("warning", f"Sessão expirada durante processamento manual para user {user_id}")
                try:
                    discord_user = await bot.fetch_user(int(user_id))
                    if discord_user:
                        embed = discord.Embed(title="[Erro] SESSÃO EXPIRADA", description="O token expirou durante o processamento.", color=0xED4245)
                        await discord_user.send(embed=embed)
                except Exception as e:
                    log("error", f"Erro DM expiração: {e}")
            except Exception as e:
                log("error", f"Erro inesperado no processamento manual: {e}")
                try:
                    desc = f"[Erro] Erro durante o processamento: {str(e)[:200]}"
                    embed = discord.Embed(description=desc, color=0xED4245)
                    if last_progress_msg:
                        await last_progress_msg.edit(embed=embed)
                    else:
                        await interaction.followup.send(embed=embed, ephemeral=True)
                except Exception:
                    pass
            finally:
                semaphore = session.get("processing_semaphore")
                session["manual_task"] = None
                session["current_manual_quest_id"] = None
                try:
                    session_manager.remove_session(user_id)
                except Exception as e:
                    log("error", f"Erro ao remover sessão {user_id}: {e}")
                finally:
                    if semaphore:
                        try:
                            semaphore.release()
                        except RuntimeError:
                            pass

        try:
            embed = discord.Embed(
                description="[OK] Processamento finalizado. Logout automático realizado.",
                color=0x57F287,
                timestamp=datetime.now()
            )
            embed.set_footer(text="Monsters Orbs")
            await interaction.edit_original_response(embed=embed)
        except discord.NotFound:
            await interaction.followup.send(
                embed=discord.Embed(description="[OK] Processamento finalizado. Logout automático realizado.", color=0x57F287),
                ephemeral=True
            )
        except Exception as e:
            log("warning", f"Erro ao enviar mensagem final de confirmação: {e}")

        if session["processing_semaphore"].locked():
            embed = discord.Embed(title="[Erro] Operação em Andamento", description="Aguarde a conclusão.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        task = asyncio.create_task(run_manual_quest())
        session["manual_task"] = task
        embed = discord.Embed(description=f"[Processamento] Iniciando: **{selected_quest.quest_name}**", color=0xFEE75C)
        await interaction.response.send_message(embed=embed, ephemeral=True)

class QuestSelectView(ui.View):
    def __init__(self, user_quests: List[Quest]):
        super().__init__(timeout=120)
        self.add_item(QuestSelect(user_quests))

class ByteConfirmView(ui.View):
    def __init__(self, user_id, session, interaction):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.session = session
        self.interaction = interaction
        self.confirmed = False

    @ui.button(label="[OK] Aceitar e Continuar", style=discord.ButtonStyle.success, custom_id="byte_accept")
    async def accept_button(self, interaction: discord.Interaction, button: ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("[Erro] Isso não é para você!", ephemeral=True)
            return
        self.confirmed = True
        self.stop()
        await interaction.response.defer()
        await self._run_byte_method()

    @ui.button(label="[Cancelar]", style=discord.ButtonStyle.danger, custom_id="byte_cancel")
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("[Erro] Isso não é para você!", ephemeral=True)
            return
        self.confirmed = False
        self.stop()
        embed = discord.Embed(
            title="[Cancelado]",
            description="Operação BYTE cancelada pelo usuário.",
            color=0xED4245
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _run_byte_method(self):
        user_id = self.user_id
        session = self.session
        manager = session["manager"]
        valid_quests = manager.get_valid_quests()
        if not valid_quests:
            embed = discord.Embed(
                title="[Informação] Nenhuma Missão",
                description="Não há missões válidas para processar.",
                color=0xFEE75C
            )
            try:
                await self.interaction.followup.send(embed=embed, ephemeral=True)
            except:
                pass
            return

        embed_start = discord.Embed(
            title="[Execução] Método BYTE Iniciado",
            description=f"Processando **{len(valid_quests)}** missões em paralelo com proteções stealth...",
            color=0x2C2F33
        )
        try:
            await self.interaction.followup.send(embed=embed_start, ephemeral=True)
        except:
            pass

        discord_user = None
        try:
            discord_user = await bot.fetch_user(int(user_id))
        except Exception as e:
            log("error", f"Não foi possível encontrar usuário {user_id}: {e}")

        async def byte_callback(msg_or_embed, message_to_edit=None):
            if not discord_user:
                return None
            try:
                if isinstance(msg_or_embed, str):
                    embed = discord.Embed(description=msg_or_embed, color=0xFEE75C)
                else:
                    embed = msg_or_embed
                if message_to_edit:
                    await message_to_edit.edit(embed=embed)
                    return message_to_edit
            except Exception as e:
                log("error", f"Erro DM BYTE: {e}")
            return None

        try:
            await session["processing_semaphore"].acquire()
            before_balance = balance_tracker.total_orbs
            results = await manager.process_all_quests_parallel(callback=byte_callback)
            session_earned = 0
            if bot_config.get("auto_claim_rewards", True):
                try:
                    await manager.fetch_quests()
                except Exception:
                    pass
                for q in list(manager.get_claimable_quests()):
                    try:
                        if await manager.claim_reward(q, callback=byte_callback) and q.has_claimed():
                            balance_tracker.add(q.reward_amount, q.quest_name)
                            session_earned += q.reward_amount
                    except Exception as e:
                        log("warning", f"[BYTE] Falha ao resgatar {q.quest_name}: {e}")
            embed_final = discord.Embed(
                title="[Conclusão] Quests Finalizadas, Boa Sorte!",
                description=(
                    f"**{results['success']}** concluídas | **{results['failed']}** falhas | **{results['total']}** total\n\n"
                    f"💎 **Saldo antes:** `{before_balance}`\n"
                    f"💎 **Saldo depois:** `{balance_tracker.total_orbs}`\n"
                    f"✨ **Ganhos nesta sessão:** `{session_earned}`"
                ),
                color=0x57F287,
                timestamp=datetime.now()
            )
            embed_final.set_footer(text="Monsters Orbs | Método BYTE")
            try:
                await self.interaction.followup.send(embed=embed_final, ephemeral=True)
            except:
                pass
            if discord_user:
                try:
                    await discord_user.send(embed=embed_final)
                except:
                    pass
        except Exception as e:
            log("error", f"Erro no método BYTE: {e}")
            embed_err = discord.Embed(
                title="[Erro] Erro no BYTE",
                description=f"Erro durante execução: {str(e)[:200]}",
                color=0xED4245
            )
            try:
                await self.interaction.followup.send(embed=embed_err, ephemeral=True)
            except:
                pass
        finally:
            semaphore = session.get("processing_semaphore")
            if semaphore:
                try:
                    semaphore.release()
                except RuntimeError:
                    pass
            session_manager.remove_session(user_id)
            log("info", f"[BYTE] Sessão {user_id} finalizada e removida.")

class MainPanelView(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Verifica se o usuário está no servidor original ANTES de qualquer ação
        if not await check_access(interaction.client, interaction.user.id):
            embed = discord.Embed(
                title="[Acesso Negado]",
                description="Este bot é de uso pessoal e restrito ao dono.",
                color=0xED4245
            )
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=embed, ephemeral=True)
            return False
        return True

    @ui.button(label="[Login]", style=discord.ButtonStyle.secondary, custom_id="panel_login")
    async def login_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.response.is_done():
            return
        if session_manager.is_logged_in(str(interaction.user.id)):
            embed = discord.Embed(title="[Erro] Você já está logado!", description="Faça logout antes de logar com outro token.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        modal = TokenModal()
        await interaction.response.send_modal(modal)

    @ui.button(label="[Auto-Quest]", style=discord.ButtonStyle.secondary, custom_id="panel_autoquest")
    async def autoquest_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.response.is_done():
            return
        user_id = str(interaction.user.id)
        session = session_manager.get_session(user_id)
        if not session:
            embed = discord.Embed(title="[Erro] Você não está logado!", description="Use o botão 'Login' primeiro.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if session["auto_task"] and not session["auto_task"].done():
            embed = discord.Embed(title="[Erro] Processamento em Andamento", description="Aguarde ou cancele primeiro.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if session["manual_task"] and not session["manual_task"].done():
            embed = discord.Embed(title="[Erro] Missão em Processamento", description="Finalize a missão manual primeiro.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        async def auto_callback(embed_or_str, message_to_edit=None):
            return None

        async def run_auto_quest():
            try:
                await session["processing_semaphore"].acquire()
                results = {"success": 0, "failed": 0, "total": 0}
                manager = session["manager"]
                before_balance = balance_tracker.total_orbs
                session_earned = 0
                try:
                    await manager.fetch_quests()
                except Exception as e:
                    log("warning", f"[Auto] Falha ao atualizar missoes: {e}")
                valid_quests = manager.get_valid_quests()
                results["total"] = len(valid_quests)
                discord_user = None
                try:
                    discord_user = await bot.fetch_user(int(user_id))
                except Exception as e:
                    log("error", f"Não foi possível encontrar usuário {user_id}: {e}")
                for i, quest in enumerate(valid_quests, 1):
                    try:
                        async def dm_callback(embed_or_str, message_to_edit=None):
                            if not discord_user:
                                return None
                            try:
                                if isinstance(embed_or_str, str):
                                    embed = discord.Embed(description=embed_or_str, color=0xFEE75C)
                                else:
                                    embed = embed_or_str
                                if message_to_edit:
                                    await message_to_edit.edit(embed=embed)
                                    return message_to_edit
                            except Exception as e:
                                log("error", f"Erro DM progresso: {e}")
                            return None
                        success = await manager.process_quest(quest, callback=dm_callback)
                        quest_link = f"https://discord.com/quests/{quest.id}"
                        quest_asset = quest.hero_image
                        orb_text = f" 💎 {quest.reward_amount} Orbs" if quest.reward_amount > 0 else ""
                        if success:
                            results["success"] += 1
                            if quest.has_claimed() and quest.reward_amount > 0:
                                balance_tracker.add(quest.reward_amount, quest.quest_name)
                                session_earned += quest.reward_amount
                            if discord_user:
                                try:
                                    balance_line = f"\n💎 Saldo: `{balance_tracker.total_orbs}`" if quest.has_claimed() else ""
                                    desc = f"[OK] **{quest.quest_name}** finalizada com sucesso!{orb_text}{balance_line}\n[Ver Missão]({quest_link})"
                                    embed = discord.Embed(title="[Sucesso] MISSÃO CONCLUÍDA", description=desc, color=0x57F287, timestamp=datetime.now())
                                    task_type = quest.get_task_type()
                                    if task_type:
                                        embed.add_field(name="Tipo", value=f"`{task_type}`", inline=True)
                                    if quest_asset and is_valid_url(quest_asset):
                                        embed.set_image(url=quest_asset)
                                    embed.set_footer(text=f"Monsters Orbs | {i}/{results['total']}")
                                    await discord_user.send(embed=embed)
                                except Exception as e:
                                    log("error", f"Erro DM sucesso: {e}")
                        else:
                            results["failed"] += 1
                            if discord_user:
                                try:
                                    desc = f"[Erro] **{quest.quest_name}** não foi concluída.{orb_text}\n[Ver Missão]({quest_link})"
                                    embed = discord.Embed(title="[Falha] MISSÃO NÃO CONCLUÍDA", description=desc, color=0xED4245, timestamp=datetime.now())
                                    if quest_asset and is_valid_url(quest_asset):
                                        embed.set_image(url=quest_asset)
                                    embed.set_footer(text=f"Monsters Orbs | {i}/{results['total']}")
                                    await discord_user.send(embed=embed)
                                except Exception as e:
                                    log("error", f"Erro DM falha: {e}")
                        if i < len(valid_quests):
                            delay = bot_config.get("quest_processing_delay", 2)
                            await asyncio.sleep(delay)
                    except TokenExpiredError:
                        log("warning", f"[Auto] Sessão expirada para user {user_id}")
                        if discord_user:
                            try:
                                embed = discord.Embed(title="[Erro] SESSÃO EXPIRADA", description="O token expirou. Missões restantes não concluídas.", color=0xED4245)
                                await discord_user.send(embed=embed)
                            except Exception as e:
                                log("error", f"Erro DM expiração: {e}")
                        break
                    except Exception as e:
                        log("error", f"[Auto] Erro processando quest {quest.id}: {e}")
                        results["failed"] += 1
                if discord_user:
                    try:
                        embed = discord.Embed(
                            title="[Finalização] PROCESSAMENTO FINALIZADO",
                            description=(
                                "Todas as missões disponíveis foram processadas.\n\n"
                                f"💎 **Saldo antes:** `{before_balance}`\n"
                                f"💎 **Saldo depois:** `{balance_tracker.total_orbs}`\n"
                                f"✨ **Ganhos nesta sessão:** `{session_earned}`"
                            ),
                            color=0x5865F2,
                            timestamp=datetime.now()
                        )
                        embed.add_field(name="Concluídas", value=f"`{results['success']}`", inline=True)
                        embed.add_field(name="Falhas", value=f"`{results['failed']}`", inline=True)
                        embed.add_field(name="Total", value=f"`{results['total']}`", inline=True)
                        embed.add_field(name="Missões pagas (total)", value=f"`{balance_tracker.missions_completed}`", inline=True)
                        embed.set_footer(text="Monsters Orbs")
                        await discord_user.send(embed=embed)
                    except Exception as e:
                        log("error", f"Erro DM finalização: {e}")
            finally:
                semaphore = session.get("processing_semaphore")
                if semaphore:
                    try:
                        semaphore.release()
                    except RuntimeError:
                        pass
                session_manager.remove_session(user_id)
                log("info", f"[Auto] Finalizado para user {user_id}.")

        if session["processing_semaphore"].locked():
            embed = discord.Embed(title="[Erro] Operação em Andamento", description="Aguarde a conclusão.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        task = asyncio.create_task(run_auto_quest())
        session["auto_task"] = task
        embed = discord.Embed(
            title="[Auto-Quest] Iniciado",
            description=(
                f"Processando **{len(session['manager'].get_valid_quests())}** missões em segundo plano.\n"
                f"💎 **Saldo antes:** `{balance_tracker.total_orbs}`\n"
                "Resultados serão enviados por DM."
            ),
            color=0xFEE75C
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="[Selecionar Quest]", style=discord.ButtonStyle.secondary, custom_id="panel_select_quest")
    async def select_quest_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.response.is_done():
            return
        user_id = str(interaction.user.id)
        session = session_manager.get_session(user_id)
        if not session:
            embed = discord.Embed(title="[Erro] Você não está logado!", description="Use o botão 'Login' primeiro.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        valid_quests = session["manager"].get_valid_quests()
        if not valid_quests:
            embed = discord.Embed(title="[Informação] Nenhuma Missão Disponível", description="Não há missões válidas no momento.", color=0xFEE75C)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        view = QuestSelectView(valid_quests)
        await interaction.response.send_message(view=view, ephemeral=True)

    @ui.button(label="[Progresso]", style=discord.ButtonStyle.secondary, custom_id="panel_progress")
    async def progress_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.response.is_done():
            return
        user_id = str(interaction.user.id)
        session = session_manager.get_session(user_id)
        if not session:
            embed = discord.Embed(title="[Erro] Você não está logado!", description="Use o botão 'Login' primeiro.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        valid_quests = session["manager"].get_valid_quests()
        completed_quests = session["manager"].get_completed_quests()
        claimable_quests = session["manager"].get_claimable_quests()
        embed = discord.Embed(title="[Progresso] Seu Progresso", color=0x5865F2, timestamp=datetime.now())
        embed.add_field(name="Pendentes", value=f"`{len(valid_quests)}`", inline=True)
        embed.add_field(name="Completas", value=f"`{len(completed_quests)}`", inline=True)
        embed.add_field(name="Resgatáveis", value=f"`{len(claimable_quests)}`", inline=True)
        if valid_quests:
            details = "\n".join([
                f"• **{q.quest_name}**: {make_progress_bar(min(100, int((q.get_progress_value(q.get_task_type()) / q.get_task_target()) * 100)) if q.get_task_target() > 0 else 0)}"
                for q in valid_quests[:5]
            ])
            if len(valid_quests) > 5:
                details += f"\n*...e mais {len(valid_quests) - 5} missões*"
            embed.add_field(name="Detalhes das Pendentes:", value=details[:1020], inline=False)
        embed.set_footer(text="Monsters Orbs")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="[Status]", style=discord.ButtonStyle.secondary, custom_id="panel_status")
    async def status_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.response.is_done():
            return
        user_id = str(interaction.user.id)
        session = session_manager.get_session(user_id)
        if not session:
            embed = discord.Embed(title="[Erro] Você não está logado!", description="Use o botão 'Login' primeiro.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        stats = session["manager"].get_stats()
        claimable = session["manager"].get_claimable_quests()
        total_orbs = sum(q.reward_amount for q in claimable)
        embed = discord.Embed(title="[Status] Status Geral", color=0x5865F2, timestamp=datetime.now())
        embed.add_field(name="Orbs Estimados", value=f"`{total_orbs}`", inline=True)
        embed.add_field(name="Pendentes", value=f"`{stats['valid']}`", inline=True)
        embed.add_field(name="Resgatáveis", value=f"`{stats['claimable']}`", inline=True)
        embed.add_field(name="Completas", value=f"`{stats['completed']}`", inline=True)
        embed.add_field(name="Inscritas", value=f"`{stats['enrolled']}`", inline=True)
        embed.add_field(name="Total", value=f"`{stats['total']}`", inline=True)
        embed.set_footer(text="Monsters Orbs")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="[Saldo]", style=discord.ButtonStyle.success, custom_id="panel_balance")
    async def balance_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.response.is_done():
            return
        user_id = str(interaction.user.id)
        session = session_manager.get_session(user_id)
        api_balance = None
        claimable_orbs = 0
        if session:
            try:
                api_balance = await session["manager"].fetch_orb_balance()
            except Exception:
                api_balance = None
            claimable_orbs = sum(q.reward_amount for q in session["manager"].get_claimable_quests())
        embed = discord.Embed(title="[Saldo] Seus Orbs", color=0xFFD700, timestamp=datetime.now())
        embed.add_field(name="Saldo (histórico)", value=f"`{balance_tracker.total_orbs}` 💎", inline=True)
        embed.add_field(name="Missões pagas", value=f"`{balance_tracker.missions_completed}`", inline=True)
        embed.add_field(name="A resgatar agora", value=f"`{claimable_orbs}` 💎", inline=True)
        if api_balance is not None:
            embed.add_field(name="Saldo na API", value=f"`{api_balance}` 💎", inline=True)
        recent = balance_tracker.recent(8)
        if recent:
            lines = [f"• `+{h['orbs']}` 💎 — {h['quest'][:45]}" for h in recent]
            embed.add_field(name="Últimos ganhos", value="\n".join(lines)[:1020], inline=False)
        else:
            embed.add_field(name="Últimos ganhos", value="Nenhum ganho registrado ainda.", inline=False)
        embed.set_footer(text="Monsters Orbs")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="[Cancelar]", style=discord.ButtonStyle.secondary, custom_id="panel_cancel_quest")
    async def cancel_quest_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.response.is_done():
            return
        user_id = str(interaction.user.id)
        session = session_manager.get_session(user_id)
        if not session:
            embed = discord.Embed(title="[Erro] Você não está logado!", description="Use o botão 'Login' primeiro.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if session["manual_task"] and not session["manual_task"].done():
            semaphore = session.get("processing_semaphore")
            session["manual_task"].cancel()
            session["manual_task"] = None
            session["current_manual_quest_id"] = None
            if semaphore:
                try:
                    semaphore.release()
                except RuntimeError:
                    pass
            session_manager.remove_session(user_id)
            embed = discord.Embed(title="[Cancelado] Missão Cancelada", description="Processamento interrompido. Logout automático realizado.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        elif session["auto_task"] and not session["auto_task"].done():
            embed = discord.Embed(title="[Aviso] Cancelamento Auto-Quest", description="O cancelamento automático não é suportado. Ele continuará até o fim.", color=0xFEE75C)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            embed = discord.Embed(title="[Erro] Nenhuma Missão Ativa", description="Não há missão em andamento para cancelar.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @ui.button(label="BYTE", style=discord.ButtonStyle.secondary, custom_id="panel_byte")
    async def byte_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.response.is_done():
            return
        user_id = str(interaction.user.id)
        session = session_manager.get_session(user_id)
        if not session:
            embed = discord.Embed(title="[Erro] Você não está logado!", description="Use o botão 'Login' primeiro.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if session["auto_task"] and not session["auto_task"].done():
            embed = discord.Embed(title="[Erro] Processamento em Andamento", description="Aguarde ou cancele o processamento automático.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if session["manual_task"] and not session["manual_task"].done():
            embed = discord.Embed(title="[Erro] Missão em Processamento", description="Finalize a missão manual primeiro.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        if session["processing_semaphore"].locked():
            embed = discord.Embed(title="[Erro] Operação em Andamento", description="Aguarde a conclusão.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed_warning = discord.Embed(
            title="[Aviso] AVISO DE SEGURANÇA — MÉTODO BYTE",
            description="Este método executa múltiplas missões simultaneamente. Embora tenha proteções anti-detecção, **NÃO é 100% seguro**.\nA equipe **Monsters Orbs NÃO se responsabiliza** por banimentos, suspensões ou perda de recompensas.\n**Use por sua conta e risco.**",
            color=0xFEE75C
        )
        embed_warning.set_footer(text="Monsters Orbs | Método BYTE")
        view = ByteConfirmView(user_id, session, interaction)
        await interaction.response.send_message(embed=embed_warning, view=view, ephemeral=True)

class QuestBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        self.add_view(MainPanelView())

    async def on_ready(self):
        try:
            await self.tree.sync()
        except Exception as e:
            log("error", f"Erro ao sincronizar comandos: {e}")
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="Monsters Orbs")
        )
        log("info", f'{self.user} está online!')

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        await self.process_commands(message)

    async def close(self):
        await super().close()

bot = None
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("ERRO: Token do BOT Discord não configurado!")
        print("Configure a variável de ambiente BOT_TOKEN antes de iniciar.")
        sys.exit(1)
    bot = QuestBot()

    @bot.tree.command(name="painel", description="Exibe o painel principal do Monsters Orbs.")
    async def painel(interaction: discord.Interaction):
        if not await check_access(bot, interaction.user.id):
            embed = discord.Embed(
                title="[Acesso Negado]",
                description="Este bot é de uso pessoal e restrito ao dono.",
                color=0xED4245
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        avatar_url = bot.user.avatar.url if bot.user.avatar else None
        embed = discord.Embed(
            title="Monsters Orbs",
            description=(
                "Gerenciamento inteligente e automatizado de missões do Discord.\n"
                "**Segurança**: Nenhum token é armazenado permanentemente.\n"
                "**Aviso**: Use por sua conta e risco."
            ),
            color=0x2C2F33,
            timestamp=datetime.now()
        )
        embed.add_field(
            name="Como usar",
            value=(
                "1. Clique em Login\n"
                "2. Informe seu token válido\n"
                "3. Gerencie suas missões pelo painel"
            ),
            inline=False
        )
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        embed.set_author(name=bot.user.name, icon_url=avatar_url)
        embed.set_footer(text="Monsters Orbs | Use por sua conta e risco.")
        view = MainPanelView()
        await interaction.response.send_message(embed=embed, view=view)

    @bot.tree.command(name="saldo", description="Mostra seu saldo de Orbs e o histórico de ganhos.")
    async def saldo(interaction: discord.Interaction):
        if not await check_access(bot, interaction.user.id):
            await interaction.response.send_message(
                embed=discord.Embed(title="[Acesso Negado]", description="Este bot é de uso pessoal e restrito ao dono.", color=0xED4245),
                ephemeral=True
            )
            return
        session = session_manager.get_session(str(interaction.user.id))
        api_balance = None
        claimable_orbs = 0
        pending = 0
        if session:
            try:
                api_balance = await session["manager"].fetch_orb_balance()
            except Exception:
                api_balance = None
            claimable_orbs = sum(q.reward_amount for q in session["manager"].get_claimable_quests())
            pending = len(session["manager"].get_valid_quests())
        embed = discord.Embed(title="[Saldo] Seus Orbs", color=0xFFD700, timestamp=datetime.now())
        embed.add_field(name="Saldo (histórico)", value=f"`{balance_tracker.total_orbs}` 💎", inline=True)
        embed.add_field(name="Missões pagas", value=f"`{balance_tracker.missions_completed}`", inline=True)
        embed.add_field(name="A resgatar agora", value=f"`{claimable_orbs}` 💎", inline=True)
        embed.add_field(name="Missões pendentes", value=f"`{pending}`", inline=True)
        if api_balance is not None:
            embed.add_field(name="Saldo na API", value=f"`{api_balance}` 💎", inline=True)
        recent = balance_tracker.recent(10)
        if recent:
            lines = [f"• `+{h['orbs']}` 💎 — {h['quest'][:45]}" for h in recent]
            embed.add_field(name="Últimos ganhos", value="\n".join(lines)[:1020], inline=False)
        embed.set_footer(text="Monsters Orbs")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="statusbot", description="Mostra o status geral do bot (Apenas Dono).")
    async def statusbot(interaction: discord.Interaction):
        if not await check_access(bot, interaction.user.id):
            embed = discord.Embed(title="[Erro] Acesso Negado", description="Apenas o dono pode usar este comando.", color=0xED4245)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = discord.Embed(title="[Status] Status do Bot", color=0x5865F2, timestamp=datetime.now())
        embed.add_field(name="Usuários Logados", value=f"`{len(session_manager.sessions)}`", inline=True)
        embed.add_field(name="Proxy Rotation", value=f"`{'Ativado' if bot_config.get('use_proxies', True) else 'Desativado'}`", inline=True)
        embed.set_footer(text="Monsters Orbs")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    bot.run(BOT_TOKEN)