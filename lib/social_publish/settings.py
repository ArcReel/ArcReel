"""分发凭证在 system_settings 里的键名。

单独成模块：系统配置路由（写侧）与分发服务（读侧）都要引用这三个键，而配置路由不该为了
三个字符串把整条演示读模型的 import 链拉进来。
"""

from __future__ import annotations

SETTING_API_KEY = "upload_post_api_key"
SETTING_PROFILE = "upload_post_profile"
SETTING_BASE_URL = "upload_post_base_url"

__all__ = ["SETTING_API_KEY", "SETTING_BASE_URL", "SETTING_PROFILE"]
