"""
认证核心模块

提供密码生成、JWT token 创建/验证、凭据校验等功能。
"""

import logging
import os
import secrets
import string
import time
from pathlib import Path
from typing import Optional

import jwt

from lib import PROJECT_ROOT

logger = logging.getLogger(__name__)

# JWT 签名密钥缓存
_cached_token_secret: Optional[str] = None

# Token 有效期：7 天
TOKEN_EXPIRY_SECONDS = 7 * 24 * 3600


def generate_password(length: int = 16) -> str:
    """生成随机字母数字密码"""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def get_token_secret() -> str:
    """获取 JWT 签名密钥

    优先使用 AUTH_TOKEN_SECRET 环境变量，否则自动生成并缓存。
    """
    global _cached_token_secret

    env_secret = os.environ.get("AUTH_TOKEN_SECRET")
    if env_secret:
        return env_secret

    if _cached_token_secret is not None:
        return _cached_token_secret

    _cached_token_secret = secrets.token_hex(32)
    logger.info("已自动生成 JWT 签名密钥")
    return _cached_token_secret


def create_token(username: str) -> str:
    """创建 JWT token

    Args:
        username: 用户名

    Returns:
        JWT token 字符串
    """
    now = time.time()
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + TOKEN_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, get_token_secret(), algorithm="HS256")


def verify_token(token: str) -> Optional[dict]:
    """验证 JWT token

    Args:
        token: JWT token 字符串

    Returns:
        成功返回 payload dict，失败返回 None
    """
    try:
        payload = jwt.decode(token, get_token_secret(), algorithms=["HS256"])
        return payload
    except (jwt.InvalidTokenError, jwt.ExpiredSignatureError):
        return None


def check_credentials(username: str, password: str) -> bool:
    """校验用户名密码

    从 AUTH_USERNAME（默认 admin）和 AUTH_PASSWORD 环境变量读取。

    Args:
        username: 输入的用户名
        password: 输入的密码

    Returns:
        凭据是否正确
    """
    expected_username = os.environ.get("AUTH_USERNAME", "admin")
    expected_password = os.environ.get("AUTH_PASSWORD", "")
    return secrets.compare_digest(username, expected_username) and secrets.compare_digest(password, expected_password)


def ensure_auth_password(env_path: Optional[str] = None) -> str:
    """确保 AUTH_PASSWORD 已设置

    如果 AUTH_PASSWORD 环境变量为空，自动生成密码，写入环境变量，
    回写到 .env 文件，并用 logger.warning 输出到控制台。

    Args:
        env_path: .env 文件路径，默认为项目根目录的 .env

    Returns:
        当前的 AUTH_PASSWORD 值
    """
    password = os.environ.get("AUTH_PASSWORD")
    if password:
        return password

    # 自动生成密码
    password = generate_password()
    os.environ["AUTH_PASSWORD"] = password

    # 回写到 .env 文件
    if env_path is None:
        env_path = str(PROJECT_ROOT / ".env")

    env_file = Path(env_path)
    try:
        if env_file.exists():
            lines = env_file.read_text().splitlines()
            new_lines = []
            found = False
            for line in lines:
                if not found and line.strip().startswith("AUTH_PASSWORD="):
                    new_lines.append(f"AUTH_PASSWORD={password}")
                    found = True
                else:
                    new_lines.append(line)
            if not found:
                new_lines.append(f"AUTH_PASSWORD={password}")
            new_content = "\n".join(new_lines) + "\n"
            # 使用原地写入（truncate + write）保留 inode，兼容 Docker bind mount
            with open(env_file, "r+") as f:
                f.seek(0)
                f.write(new_content)
                f.truncate()
        else:
            env_file.write_text(f"AUTH_PASSWORD={password}\n")
    except OSError:
        logger.warning("无法写入 .env 文件: %s", env_path)

    logger.warning(
        "已自动生成认证密码，请查看 .env 文件中的 AUTH_PASSWORD 字段"
    )
    return password
