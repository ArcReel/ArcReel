"""Creative-quality checklist for local short-drama production."""

CREATIVE_QUALITY_RULES = """短剧创作质量检查：
- 开篇优先钩子：危机、反差、秘密、失控动作或强情绪先出现，再交代背景。
- 中段保持转折密度：约每 15 秒宜出现动作转折、关系撕裂、信息反转或情绪升级，避免长段平铺说明。
- 付费/追更节点要有可见的 paywall marker：误会扩大、身份揭露、关键证据出现、人物做出不可逆选择。
- 结尾提供 satisfaction + cliffhanger：本集情绪有回报，同时末镜留下下一集必须看的悬念。
- 合规检查在 Phase 1 以 warn 为主；只有机械可判定的缺失资产、非法时长、空提示等才应升级为 block。""".strip()


__all__ = ["CREATIVE_QUALITY_RULES"]
