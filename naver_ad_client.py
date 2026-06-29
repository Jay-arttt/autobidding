"""
네이버 검색광고 API 클라이언트
인증 서명 생성 및 공통 HTTP 요청 처리
"""

import hashlib
import hmac
import base64
import time
import requests
from typing import Optional, Dict, Any


class NaverAdClient:
    BASE_URL = "https://api.naver.com"

    def __init__(self, api_key: str, secret_key: str, customer_id: str):
        self.api_key = api_key
        self.secret_key = secret_key
        self.customer_id = customer_id

    def _get_signature(self, timestamp: str, method: str, path: str) -> str:
        message = f"{timestamp}.{method}.{path}"
        hashed = hmac.new(
            self.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(hashed.digest()).decode("utf-8")

    def _get_headers(self, method: str, path: str) -> Dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        signature = self._get_signature(timestamp, method, path)
        return {
            "Content-Type": "application/json; charset=UTF-8",
            "X-Timestamp": timestamp,
            "X-API-KEY": self.api_key,
            "X-Customer": self.customer_id,
            "X-Signature": signature,
        }

    def get(self, path: str, params: Optional[Dict] = None) -> Any:
        headers = self._get_headers("GET", path)
        resp = requests.get(
            self.BASE_URL + path, headers=headers, params=params, timeout=10
        )
        resp.raise_for_status()
        return resp.json()

    def put(self, path: str, body: Dict, params: Optional[Dict] = None) -> Any:
        headers = self._get_headers("PUT", path)
        resp = requests.put(
            self.BASE_URL + path,
            headers=headers,
            params=params,
            json=body,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, body: Dict, params: Optional[Dict] = None) -> Any:
        headers = self._get_headers("POST", path)
        resp = requests.post(
            self.BASE_URL + path,
            headers=headers,
            params=params,
            json=body,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
