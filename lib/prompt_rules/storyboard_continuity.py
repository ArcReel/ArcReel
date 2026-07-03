"""Storyboard continuity and asset-discipline rules for short-drama production."""

STORYBOARD_CONTINUITY_RULES = """分镜连续性与 C/S/P 资产纪律：
- C/S/P（Character / Scene / Prop）引用必须来自 project.json 已登记资产；角色、场景、道具名称逐字一致。
- 场景切换只在真正的时间、地点、情绪段落变化处发生；连续镜头宜保留上一镜头的关键姿态、道具位置或视线方向。
- 依赖上一镜头的分镜，要在视觉描述里留下 first-frame / last-frame 可衔接信号，例如手势延续、门的开合状态、光线方向。
- 多角色场面优先稳定主角相对位置与视线关系，避免同一段里角色站位、服装、持物突然漂移。
- 缺少关键参考图、角色卡或场景卡时，应作为 QA finding 暴露；prompt 不应用临时想象补齐核心资产。""".strip()


__all__ = ["STORYBOARD_CONTINUITY_RULES"]
