#!/usr/bin/env python3
"""查当前项目视频模型能力 + 用户项目偏好（model 粒度，所有 generation_mode 通用）。

用法:
    python .claude/skills/manage-project/scripts/get_video_capabilities.py --project <name>

stdout 输出 JSON:
    {"provider_id": "grok",
     "model": "grok-imagine-video",
     "supported_durations": [1, ..., 15],   # model 能力
     "max_duration": 15,                    # 派生
     "max_reference_images": 7,             # provider 粒度 fallback
     "source": "registry" | "custom",
     "default_duration": 4,                 # 用户项目偏好（可能 null）
     "content_mode": "narration",
     "generation_mode": "reference_video"}

找不到项目 / 无法解析能力时：退出码非 0，stderr 打印错误。
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 允许从仓库任意工作目录直接运行该脚本
# 文件路径：{repo_root}/agent_runtime_profile/.claude/skills/manage-project/scripts/get_video_capabilities.py
# parents[5] 对应 repo root
PROJECT_ROOT = Path(__file__).resolve().parents[5]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.config.resolver import ConfigResolver  # noqa: E402
from lib.db import async_session_factory  # noqa: E402


async def _resolve(project_name: str) -> dict:
    resolver = ConfigResolver(async_session_factory)
    return await resolver.video_capabilities(project_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="查项目视频模型能力 + 项目偏好（通用，所有 generation_mode）")
    parser.add_argument("--project", required=True, help="项目名")
    args = parser.parse_args()

    try:
        payload = asyncio.run(_resolve(args.project))
    except FileNotFoundError as exc:
        print(f"项目未找到或缺 project.json: {exc}", file=sys.stderr)
        sys.exit(2)
    except ValueError as exc:
        print(f"无法解析视频模型能力: {exc}", file=sys.stderr)
        sys.exit(3)

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
