"""
X (Twitter) 隐身客户端 - 反自动化检测
双通道发推：OAuth API 主通道 + Cookie GraphQL 备用通道
TLS 指纹伪装 + 行为模拟 + 代理支持
"""

import hashlib
import hmac
import json
import random
import time
import urllib.parse
import uuid
from base64 import b64encode

from curl_cffi import requests as cffi_requests
from rich.console import Console

import config

console = Console()

# X API v2 端点
TWEET_API_V2 = "https://api.twitter.com/2/tweets"

# X 内部 GraphQL 端点（Cookie 通道）
GRAPHQL_CREATE_TWEET = "https://x.com/i/api/graphql/a1p9RWpkYKBjWv_I3WzS-A/CreateTweet"

# X 官方 Bearer Token（公开的，用于内部 API 调用）
X_INTERNAL_BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs=1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

# User-Agent 池 - 模拟多种真实浏览器
UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

# Chrome 版本对应的 Sec-CH-UA
SEC_CH_UA_MAP = {
    "120": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "121": '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
}


class StealthXClient:
    """反检测 X 客户端"""

    def __init__(self, auth_token: str = None, ct0: str = None):
        """
        初始化隐身客户端
        auth_token/ct0: 可从 AccountInfo 传入，覆盖 .env 配置
        """
        self.proxy_url = config.get_proxy_url()
        self.ua = random.choice(UA_POOL)
        self._session = None
        # 账号级别凭证（优先于 .env）
        self._auth_token = auth_token or config.X_AUTH_TOKEN
        self._ct0 = ct0 or config.X_CT0

    @property
    def session(self):
        """懒加载 curl_cffi session"""
        if self._session is None:
            kwargs = {"impersonate": "chrome120"}
            if self.proxy_url:
                kwargs["proxy"] = self.proxy_url
            self._session = cffi_requests.Session(**kwargs)
        return self._session

    def post_tweet(self, text: str) -> dict:
        """
        发推 - 双通道自动降级
        返回: {"success": bool, "tweet_id": str, "channel": str}
        """
        self._random_delay(2, 5)

        # 主通道: OAuth API
        if self._has_api_credentials():
            try:
                console.print("[dim]  → 尝试 OAuth API 通道发推...[/dim]")
                result = self._post_via_oauth_api(text)
                if result["success"]:
                    console.print(f"[green]  ✓ OAuth API 发推成功[/green] (ID: {result.get('tweet_id', 'N/A')})")
                    return result
            except Exception as e:
                console.print(f"[yellow]  ⚠ OAuth API 通道失败: {e}[/yellow]")

        # 备用通道: Cookie + GraphQL
        if self._has_cookie_credentials():
            try:
                console.print("[dim]  → 降级到 Cookie GraphQL 通道...[/dim]")
                self._warmup_session()
                result = self._post_via_cookie_graphql(text)
                if result["success"]:
                    console.print(f"[green]  ✓ Cookie GraphQL 发推成功[/green] (ID: {result.get('tweet_id', 'N/A')})")
                    return result
            except Exception as e:
                console.print(f"[red]  ✗ Cookie GraphQL 通道失败: {e}[/red]")

        return {"success": False, "tweet_id": None, "channel": "none", "error": "所有通道均失败"}

    # ─── OAuth API 通道 ───

    def _post_via_oauth_api(self, text: str) -> dict:
        """X API v2 + OAuth 1.0a 签名 + 伪装 Headers"""
        headers = self._build_stealth_headers()
        headers["Content-Type"] = "application/json"

        # OAuth 1.0a 签名
        oauth_header = self._build_oauth_header("POST", TWEET_API_V2)
        headers["Authorization"] = oauth_header

        payload = json.dumps({"text": text})

        resp = self.session.post(
            TWEET_API_V2,
            headers=headers,
            data=payload,
            timeout=30,
        )

        if resp.status_code == 201:
            data = resp.json()
            tweet_id = data.get("data", {}).get("id", "")
            return {"success": True, "tweet_id": tweet_id, "channel": "oauth_api"}
        elif resp.status_code == 429:
            raise RateLimitError(f"API 限流: {resp.text}")
        elif resp.status_code == 403:
            raise ForbiddenError(f"API 拒绝: {resp.text}")
        else:
            raise Exception(f"API 错误 {resp.status_code}: {resp.text[:200]}")

    def _build_oauth_header(self, method: str, url: str, params: dict = None) -> str:
        """构建 OAuth 1.0a Authorization header"""
        if params is None:
            params = {}

        oauth_params = {
            "oauth_consumer_key": config.X_API_KEY,
            "oauth_nonce": uuid.uuid4().hex,
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_token": config.X_ACCESS_TOKEN,
            "oauth_version": "1.0",
        }

        # 合并参数并排序
        all_params = {**oauth_params, **params}
        sorted_params = sorted(all_params.items())
        param_string = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted_params)

        # 签名基础字符串
        base_string = f"{method.upper()}&{_pct(url)}&{_pct(param_string)}"

        # 签名密钥
        signing_key = f"{_pct(config.X_API_SECRET)}&{_pct(config.X_ACCESS_SECRET)}"

        # HMAC-SHA1 签名
        signature = b64encode(
            hmac.new(
                signing_key.encode(),
                base_string.encode(),
                hashlib.sha1,
            ).digest()
        ).decode()

        oauth_params["oauth_signature"] = signature

        # 构建 header
        auth_header = "OAuth " + ", ".join(
            f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth_params.items())
        )
        return auth_header

    # ─── Cookie GraphQL 通道 ───

    def _post_via_cookie_graphql(self, text: str) -> dict:
        """Cookie 登录态 + X 内部 GraphQL CreateTweet"""
        headers = self._build_stealth_headers()
        headers.update({
            "Authorization": f"Bearer {X_INTERNAL_BEARER}",
            "Content-Type": "application/json",
            "X-Csrf-Token": self._ct0,
            "X-Twitter-Auth-Type": "OAuth2Session",
            "X-Twitter-Active-User": "yes",
            "X-Twitter-Client-Language": "en",
        })

        # 设置 Cookie（使用账号级别凭证）
        cookies = {
            "auth_token": self._auth_token,
            "ct0": self._ct0,
        }

        # GraphQL payload
        payload = json.dumps({
            "variables": {
                "tweet_text": text,
                "dark_request": False,
                "media": {
                    "media_entities": [],
                    "possibly_sensitive": False,
                },
                "semantic_annotation_ids": [],
            },
            "features": {
                "communities_web_enable_tweet_community_results_fetch": True,
                "c9s_tweet_anatomy_moderator_badge_enabled": True,
                "tweetypie_unmention_optimization_enabled": True,
                "responsive_web_edit_tweet_api_enabled": True,
                "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
                "view_counts_everywhere_api_enabled": True,
                "longform_notetweets_consumption_enabled": True,
                "responsive_web_twitter_article_tweet_consumption_enabled": True,
                "tweet_awards_web_tipping_enabled": False,
                "creator_subscriptions_quote_tweet_preview_enabled": False,
                "longform_notetweets_rich_text_read_enabled": True,
                "longform_notetweets_inline_media_enabled": True,
                "articles_preview_enabled": True,
                "rweb_video_timestamps_enabled": True,
                "rweb_tipjar_consumption_enabled": True,
                "responsive_web_graphql_exclude_directive_enabled": True,
                "verified_phone_label_enabled": False,
                "freedom_of_speech_not_reach_fetch_enabled": True,
                "standardized_nudges_misinfo": True,
                "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
                "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
                "responsive_web_graphql_timeline_navigation_enabled": True,
                "responsive_web_enhance_cards_enabled": False,
            },
            "queryId": "a1p9RWpkYKBjWv_I3WzS-A",
        })

        resp = self.session.post(
            GRAPHQL_CREATE_TWEET,
            headers=headers,
            cookies=cookies,
            data=payload,
            timeout=30,
        )

        if resp.status_code == 200:
            data = resp.json()
            # 检查 GraphQL 级别的错误
            if data.get("errors"):
                err_msg = data["errors"][0].get("message", "未知GraphQL错误")
                err_code = data["errors"][0].get("code", 0)
                if err_code == 344:
                    raise RateLimitError(f"每日发推限制: {err_msg}")
                raise Exception(f"GraphQL 业务错误 [{err_code}]: {err_msg}")
            tweet_result = (
                data.get("data", {})
                .get("create_tweet", {})
                .get("tweet_results", {})
                .get("result", {})
            )
            tweet_id = tweet_result.get("rest_id", "")
            if not tweet_id:
                raise Exception(f"发推成功但未获取到 tweet_id，响应: {str(data)[:200]}")
            return {"success": True, "tweet_id": tweet_id, "channel": "cookie_graphql"}
        elif resp.status_code == 429:
            raise RateLimitError(f"GraphQL 限流: {resp.text[:200]}")
        elif resp.status_code == 403:
            raise ForbiddenError(f"GraphQL 拒绝 (Cookie 可能过期): {resp.text[:200]}")
        else:
            raise Exception(f"GraphQL 错误 {resp.status_code}: {resp.text[:200]}")

    # ─── 反检测工具方法 ───

    def _build_stealth_headers(self) -> dict:
        """构建模拟真实浏览器的完整 Headers"""
        # 检测 UA 中的 Chrome 版本
        chrome_ver = "120"
        for ver in SEC_CH_UA_MAP:
            if f"Chrome/{ver}" in self.ua:
                chrome_ver = ver
                break

        return {
            "User-Agent": self.ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-CH-UA": SEC_CH_UA_MAP.get(chrome_ver, SEC_CH_UA_MAP["120"]),
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"macOS"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Referer": "https://x.com/",
            "Origin": "https://x.com",
            "DNT": "1",
            "Connection": "keep-alive",
        }

    def _warmup_session(self):
        """预热 session - 先访问 X 首页建立正常会话"""
        try:
            console.print("[dim]  → 预热会话中...[/dim]")
            self._random_delay(1, 3)
            headers = self._build_stealth_headers()
            self.session.get(
                "https://x.com/",
                headers=headers,
                timeout=15,
            )
            self._random_delay(1, 2)
        except Exception:
            pass  # 预热失败不影响主流程

    def _random_delay(self, min_sec: float = 1, max_sec: float = 5):
        """随机延迟，模拟人类行为"""
        delay = random.uniform(min_sec, max_sec)
        time.sleep(delay)

    def _has_api_credentials(self) -> bool:
        """检查是否有 OAuth API 凭证"""
        return all([
            config.X_API_KEY,
            config.X_API_SECRET,
            config.X_ACCESS_TOKEN,
            config.X_ACCESS_SECRET,
        ])

    def _has_cookie_credentials(self) -> bool:
        """检查是否有 Cookie 凭证"""
        return all([self._auth_token, self._ct0])


# ─── 自定义异常 ───

class RateLimitError(Exception):
    pass

class ForbiddenError(Exception):
    pass


# ─── 工具函数 ───

def _pct(s: str) -> str:
    """OAuth percent-encode"""
    return urllib.parse.quote(str(s), safe="")
