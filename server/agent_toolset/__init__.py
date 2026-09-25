"""Agent 工具集：每个工具一份宿主无关声明，ArcReel Agent 与外部 Agent 各由一个薄 adapter 投影。

声明与统一调用入口不依赖任何宿主 SDK；内嵌 adapter（``embedded``）与远程 adapter（``remote``）
只负责项目如何确定、结果如何装进各自的信封。
"""
