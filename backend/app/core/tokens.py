"""API Token 生成与哈希:认证(deps)与管理端点(api_tokens)共用。

明文 `at_` 前缀 + 48 位随机 hex;库里只落 SHA-256 哈希,明文仅创建响应
返回一次(spec 决策:泄露面最小化)。
"""

import hashlib
import secrets

TOKEN_PREFIX = "at_"


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_hex(24)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
