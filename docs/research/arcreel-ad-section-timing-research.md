# 带货短视频的时长配比依据

2026-06-11 审定的四档配比用于广告／短片模式的剧本生成。实际采用的秒数与分镜数量定义在[各档提示词模板](../../lib/prompts/prompt_templates/templates/partials/shared/ad_pacing/tier/)中，通用规则见[时长分配模板](../../lib/prompts/prompt_templates/templates/partials/shared/ad_pacing/allocation.md)。

## 证据如何影响分配

- 开场尽早给出产品或核心信息，结尾留出行动指引。加长视频时优先扩充卖点和演示。这组顺序取自短视频的「开场—正文—收尾」框架与带货脚本样本。[TikTok 广告创意指南](https://ads.tiktok.com/business/library/Auction_Ads_Creative_Tips.pdf) · [marketingskills 的短视频参考](https://github.com/coreyhaines31/marketingskills/blob/main/skills/social/references/short-form-video.md)
- 15 秒档将痛点并入开场，促销信息可并入行动指引，保留产品揭示、核心卖点与演示。30 秒档参照当时的 Problem-Solution 样本划分开场、正文与收尾，再按 ArcReel 的八段框架进一步细分。[marketingskills](https://github.com/coreyhaines31/marketingskills) · [广告脚本样本](https://shortgenius.com/blog/ad-script-example)
- 60 和 90 秒档扩大中段并增加情景演示。相较于把大量时长集中在社会证明上的样本，ArcReel 为演示保留了更多秒数，以适应图生视频的可生成内容。这是产品取舍。[60 秒脚本样本](https://www.retiplex.com/blog/ugc-ad-script-guide) · [带货短视频脚本拆解](https://www.opp2.com/317828.html)

来源给出的多是粗粒度结构和样例区间。八段的精确秒数由 ArcReel 按卖点与展示内容拆分后归一到总时长，分镜数量也由节奏估算。中文口播字数在当时未找到可用的权威语速依据。不同平台的效果统计和原始取样记录见[调研快照](https://github.com/ArcReel/ArcReel/blob/fed952b5f25fb48a9b26e70a5878705c9ecbf988/docs/research/arcreel-ad-section-timing-research.md)。
