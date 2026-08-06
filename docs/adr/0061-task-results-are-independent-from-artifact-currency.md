---
status: accepted
---

# 任务结果与产物时效独立

批量生成结果按本次请求穷尽为 `requested = succeeded ∪ failed ∪ blocked`，产物则独立报告 current、stale、missing 或 blocked；两组状态不能互相推导。强制重生一个 current 产物失败时，本次任务是 failed，但旧产物仍是 current。这个分离避免为了表达一次执行失败而篡改现有产物事实，也让 Agent 能准确区分“本次没做成”和“目前没有可用内容”。
