"""单集目标时长（秒）的单一真相源：字段名、硬区间、读时解析。

项目级可选设置，落在 ``project.json`` 顶层。它描述的是**整集成片的期望体量**，与
``default_duration``（单个分镜 / unit 的默认秒数偏好）是两个尺度：前者约束「拆多少个」，
后者约束「每个多长」，两者同为软偏好、可被内容需要覆盖，不做硬阻断（拒绝硬性创作限制的
理由见 ``.out-of-scope/product-enforced-creative-limits.md``）。

ad 项目不持有该字段：广告 / 短片的整集体量由 ``target_duration`` 预算 + 配比表管，两套
并存会让分镜规划同时收到两个互相竞争的总量口径。禁止口径与 ``default_duration`` 在 ad 上
的禁止一致：改写侧（REST PATCH、``patch_project``）字段出现即拒绝，含显式 null——null 在
那里是「清除」动作；创建侧的 null 即「未提供」，与不传等价，不落盘也不报错。

写入侧（创建 / PATCH 请求模型、``patch_project`` 白名单）与读取侧共用 ``is_valid_episode_target_duration``
这一把尺；消费方只经 ``project_episode_target_duration`` 取值，不各自读 project.json 字段。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: 单集目标时长在 ``project.json`` 的顶层字段名（秒）。
EPISODE_TARGET_DURATION_FIELD: str = "episode_target_duration"

#: 单集目标时长的硬区间（闭区间，秒）。宽松区间只拦明显误输入（填了毫秒、填了分钟数、
#: 填了整部剧的时长），区间内不做任何倾向性提示——具体取多少是创作决策。
#: 下界 10 秒：短于此的「整集」装不下一个完整的最短档视频单元加一句台词，只可能是误输入。
#: 上界 600 秒：十分钟已远超短视频单集体量，更长的成片诉求应走分集而非把一集撑大。
MIN_EPISODE_TARGET_DURATION: int = 10
MAX_EPISODE_TARGET_DURATION: int = 600


def is_valid_episode_target_duration(value: Any) -> bool:
    """该数值是否是落在硬区间内的整数秒（``10 <= value <= 600``）。

    前端输入校验、请求模型校验、``patch_project`` 强制转换与持久化后的读时守卫共用这一把尺。
    只接受 ``int``：秒数是整数量纲，浮点值一律判为不可用而非静默取整——静默取整会让用户看到
    的设置与实际生效值不同。``bool`` 是 ``int`` 子类，显式排除，避免 JSON 里的 ``true`` 被
    当成 1 秒。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return MIN_EPISODE_TARGET_DURATION <= value <= MAX_EPISODE_TARGET_DURATION


def project_episode_target_duration(project: Mapping[str, Any] | None) -> int | None:
    """从 project.json 解析单集目标时长，未填 / 脏值 / 越界一律返回 ``None``。

    返回 ``None`` 即「未设目标」，脚本规划的提示词不注入该软约束、审核面板不展示对比。
    写入侧已按同一把尺拒绝越界值，这里的守卫是对手改 project.json 与历史脏数据的读时兜底：
    一个坏掉的偏好字段不值得让一次已付费的生成崩掉。
    """
    if not isinstance(project, Mapping):
        return None
    raw = project.get(EPISODE_TARGET_DURATION_FIELD)
    return raw if is_valid_episode_target_duration(raw) else None
