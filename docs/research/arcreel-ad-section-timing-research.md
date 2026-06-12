# 带货短视频八段框架时长配比调研

> 用途：广告/短片模式（content_mode=ad）剧本生成 prompt 中各时长档配比数字的依据。四档配比表已经维护者审定（2026-06-11）；本文保留调研来源链路与推断标注，便于后续校定与扩展档位。调研方法：通读 coreyhaines31/marketingskills 仓库（50 个 skill）+ 公开行业资料交叉核实。

## 一、marketingskills 仓库盘点

仓库（https://github.com/coreyhaines31/marketingskills）中与本任务相关的内容如下。**该仓库没有按 15/30/60/90 秒分档的广告时长配比表**——这是 SaaS 营销向的 skill 集，最接近的是 social skill 里两个带秒数区间的短视频结构模板。可迁移的结构性原则不少：

| Skill（路径） | 适用经验 |
|---|---|
| **social**（`skills/social/SKILL.md` 的 Short-Form Video 章节） | 最直接相关。① "3 秒法则"：视觉钩+口播钩+文字钩三钩齐发，第 1 秒就要命中；② 平台最优时长：TikTok 15-60s / Reels 15-30s / Shorts 30-60s；③ **Problem-Solution 结构（15-30s）：hook 0-3s → 痛点放大 3-10s → 解决方案 10-25s → CTA 25-30s**（30 秒档的直接骨架）；④ 常见错误：钩子铺垫太慢、能更短就更短、没有 CTA |
| **social/references/short-form-video.md** | ① 钩子库四类（好奇/价值/故事/争议），可直接喂给 hook 段的 prompt；② 脚本模板固定 "Hook 0-3s + Body + **CTA 末尾 3-5s**"；③ Story Arc（45-60s）：hook 0-3 / 铺垫 3-15 / 过程 15-45 / 结果 45-55 / CTA 55-60；④ **镜头节奏：B-roll 快切 1-3s/镜，slideshow 2-4s/页**（与单镜头 2-6s 约束吻合） |
| **ads**（`skills/ads/SKILL.md` + `references/ad-copy-templates.md`） | ① PAS（问题→放大→解决→CTA）、BAB、Social Proof Lead、**Direct Response 公式（大胆承诺→证明→带紧迫感的 CTA）**——后者是 trust→price_promo→cta 的排序依据；② CTA 分软/硬/紧迫三档，按目标选；③ **测试优先级：hook 对效果影响最大**，其次 headline、卖点、CTA |
| **ad-creative**（`skills/ad-creative/SKILL.md`） | ① 写创意先定 3-5 个"角度"（痛点/结果/社证/紧迫/身份……），与八段 taxonomy 一一对应；② TikTok 广告文案限 80 字符内的平台规格意识 |
| **marketing-psychology**（`skills/marketing-psychology/SKILL.md`） | ① AIDA（注意→兴趣→欲望→行动）佐证八段排序；② "建立信任→权威+社证"（trust 段素材方向）、"制造紧迫→稀缺+损失厌恶"（price_promo 段写法）；③ 价格呈现技巧（Rule of 100、心理账户"每天 1 元"）可进 price_promo 的 prompt |
| **video**（`skills/video/SKILL.md`） | 偏制作工具链，对配比无输入；但"**85% 社交视频静音观看**、9:16、AI 模型渲染不了可读文字"三条对镜头 prompt 生成有约束价值 |
| **copywriting**（`references/copy-frameworks.md`） | 标题公式（"{结果} without {痛点}"等）可复用为 hook 台词模板；落地页章节排序（问题→方案→社证→FAQ→CTA）再次印证段落顺序 |

**提炼出的可迁移原则**（仓库证据 → 配比表的用法）：

1. **hook 和 CTA 是"绝对时长"段**：所有模板无论总长 15s 还是 60s，hook 恒为 0-3s、CTA 恒为末尾 3-5s——加长视频不等比放大头尾。
2. **加长的时间全部给中段**（方案/演示/证明），这是档位间唯一的伸缩区。
3. **痛点→方案→证明→行动的顺序不随时长变**，短档位是"砍段"而不是"乱序"。
4. **单镜头 1-4s 快切、内容上每隔几秒一个新信息点**是短视频底层节奏。

## 二、行业资料关键结论（附来源）

**平台官方/官方数据类**

| # | 结论 | 来源 |
|---|---|---|
| 1 | TikTok 官方：CTR 最高的视频中 **63% 在前 3 秒内亮出核心信息或产品** | TikTok 官方 PDF《9 Creative Tips to drive performance》 https://ads.tiktok.com/business/library/Auction_Ads_Creative_Tips.pdf （转引：https://lebesgue.io/tiktok-ads/how-to-increase-tiktok-ctr-9-creative-tips ） |
| 2 | TikTok 官方：**前 6 秒捕获 90% 的广告记忆效果**；推荐 hook–body–close 三段结构 | TikTok For Business 博客 https://ads.tiktok.com/business/en/blog/creative-best-practices-top-performing-ads （该域名直接抓取被证书校验拦截，数字经 https://www.stackmatix.com/blog/tiktok-hook-first-3-seconds 与 https://www.2pointagency.com/glossary/tiktok-creative-best-practices-the-3-second-rule/ 交叉确认） |
| 3 | TikTok 数据：**多场景/多机位切换的视频转化率高 38%**、展示量高 40%+，**99% 的电商爆款视频用了多场景多角度**；93% 爆款带音频 | https://tinuiti.com/blog/paid-social/tiktok-best-practices/ （引 TikTok 官方数据；原文 https://ads.tiktok.com/business/en-US/blog/creative-that-drives-conversions ） |
| 4 | TikTok 数据：**1/4 的最佳表现视频时长落在 21-34 秒**（展示量 +1.6%） | https://creatify.ai/blog/tiktok-ads-complete-guide-to-creating-high-performing-creatives-in-2026 （引 TikTok 数据） |
| 5 | Meta：Reels 广告上限 90s；甜点区 15-60s，**超 30s 在 FB Reels 上明显难保持注意力**；多数活动理想长度 6-15s；**产品/痛点/结果要在前 0.3-2 秒入画** | https://www.jonloomer.com/meta-video-ad-length-requirements/ 、https://thedesignsfirm.com/en/blog/facebook-video-ad-length 、https://www.brandwatch.com/blog/facebook-video-ads-best-practices/ 、官方规格 https://www.facebook.com/business/help/817989058548892 |

**抖音/中文带货类**

| # | 结论 | 来源 |
|---|---|---|
| 6 | 爆款带货脚本四块占比：**开头 0-3 秒痛点提问 → 紧跟 20% 时长强化需求 → 中间 50% 逐一讲卖点+产品展示 → 最后 10% 给"必须买的理由+福利/价格优势"促单** | 青瓜传媒《爆款带货短视频脚本结构拆解》 https://www.opp2.com/317828.html （已直接抓取核实） |
| 7 | 黄金 3 秒：**超 50% 用户在前 3 秒决定划走与否**；钩子三范式（悬念/痛点/利益点直出） | 蝉妈妈《抖音电商黄金3秒优化方法论》 https://www.chanmama.com/yunyingquan/article/1428.html （已直接抓取核实） |
| 8 | 3 秒留存 ≥58% 为健康线、整体完播 ≥32%（爆款 45%+）；**每 5 秒设一个兴趣点**；**新手从 30 秒练起，90 秒对节奏把控要求很高** | https://www.cnblogs.com/huizhudev/p/19148789 （行业经验帖，非官方口径） |
| 9 | 电商带货视频多为 **15 秒左右、30 秒封顶**；8-15 秒内要见到产品亮点/价格/促销；**价格是影响下单的最重要因素**，价格优势放结尾促单 | https://www.changbiyuan.com/douyin/duanshipin/2022/duanshipin_1003/54743.html |

**UGC / Direct Response 结构类**

| # | 结论 | 来源 |
|---|---|---|
| 10 | UGC 直效公式 **Hook → Problem → Solution → Value Prop → Social Proof → CTA**；hook 2-3 秒封顶；"15s 压缩中段、30s 均衡、60s 扩演示" | https://motionapp.com/blog/how-to-write-ugc-ad-scripts （已抓取核实） |
| 11 | **60 秒 UGC 五段配时：hook 0-3 / 痛点 3-10 / 方案 10-25 / 社证 25-45 / CTA 45-60**；每 5-8 秒换一个画面；每个脚本写 ≥5 个 hook 分开测 | https://www.retiplex.com/blog/ugc-ad-script-guide （已抓取核实） |
| 12 | **90 秒电商演示型结构：利益钩 0-5 / 引导式演示 6-75（卖点边演边证，社证织入中段）/ 紧迫 CTA 76-90**；**90 秒叙事型：人物与冲突 0-15 / 过程 16-75 / 揭晓+CTA 76-90**；30 秒社交广告：hook 0-3 / 价值 4-25 / CTA 26-30；**15 秒 TikTok 原生式：花式开场 0-1 / 高密度价值 2-12 / 好奇缺口+CTA 13-15** | https://shortgenius.com/blog/ad-script-example （已抓取核实） |
| 13 | 解释型视频脚本配比 **Problem 30% / Solution 40% / Proof 20% / Action 10%**；语速换算 **150 英文词/分钟**：30s=60-75 词、60s=140-160 词、90s=210-230 词 | https://vidico.com/news/explainer-video-script-examples/ （已抓取核实） |
| 14 | UGC 口播 **30 秒约 75-120 词**；前 3-5 秒定生死 | https://billo.app/blog/ugc-scripts/ （已抓取核实） |
| 15 | VSL 经验：**hook 承担约 80% 的成败权重**；"最大的流失发生在前 10-30 秒"；证明段（具体数字的真实顾客结果）直接扛转化；2026 年趋势是 30-90 秒"micro-VSL" | https://www.blog.theperformers.io/p/video-sales-letters-ads 、https://adlibrary.com/guides/vsl-ads-ecommerce-guide |
| 16 | 直效 UGC 推荐 15-30 秒；"最常见的错误是铺垫太长" | https://www.rathlymarketing.com/faq/best-ugc-ad-structure/ （已抓取核实） |

## 三、四档配比表（已审定）

**通用规则**（适用于全部档位，与配比表一并写进剧本生成 prompt）：

- hook 与 cta 是**绝对时长段**（hook 2-4s、cta 3-6s），不随档位等比放大；加长的秒数优先给 selling_point/demo，其次 trust（依据 #10、#11、#12、仓库原则 1-2）。
- price_promo 永远紧贴 cta 构成"促单收尾块"（依据 #6、#9、ads skill 的 Direct Response 公式）。
- 即使 hook 不是产品画面，**产品也应在前 3 秒内入画**（文字/局部/手持均可）（依据 #1、#5）。
- 单 section 超过 6 秒必须拆成多个镜头；全片平均 3-5 秒/镜，开头允许 2-3 秒快切（依据 #11 每 5-8 秒换画面、#8 每 5 秒一个兴趣点、仓库 1-3s B-roll 快切）。
- 镜头数宁多勿少：多场景多角度有官方背书的转化提升（依据 #3）。

### 档位一：15 秒（冲动型/投流款，5-6 镜头）

| section | 秒数 | 累计 | 镜头数 | 说明 |
|---|---|---|---|---|
| hook | 3 | 0-3 | 1 | 痛点提问式或结果前置式开场，**由 hook 兼任 pain_point** |
| product_reveal | 2 | 3-5 | 1 | 产品入画+名称 |
| selling_point | 3 | 5-8 | 1 | 只讲 1 个核心卖点 |
| demo | 4 | 8-12 | 1-2 | 1 个使用场景/效果对比 |
| cta | 3 | 12-15 | 1 | 行动指令，**可带一句促销词兼任 price_promo** |

**砍掉**：pain_point（折叠进 hook——PAS 的 problem 本来就在 0-3s，见 #6、#7）、trust（15 秒装不下独立证明段，见 #12 的 15s 式）、price_promo（折叠进 cta 一句话，见 #12 "好奇缺口+CTA 13-15"）。
**依据**：整体骨架对齐 #12 的 15 秒 TikTok 原生式（0-1 开场 / 2-12 高密度价值 / 13-15 CTA）：本表 3-12 秒的 reveal+selling+demo 合计 9 秒即"高密度价值块"；hook 3s 与 cta 3s 取自仓库 social skill 模板与 #1。镜头数 = 15s ÷ 2.5-3s 快切。

### 档位二：30 秒（标准带货位，默认推荐档，8-10 镜头）

| section | 秒数 | 累计 | 镜头数 | 说明 |
|---|---|---|---|---|
| hook | 3 | 0-3 | 1 | 三钩齐发（画面+口播+花字） |
| pain_point | 4 | 3-7 | 1-2 | 放大痛点、共鸣场景 |
| product_reveal | 3 | 7-10 | 1 | 产品登场作为"答案" |
| selling_point | 6 | 10-16 | 2 | 1-2 个卖点 |
| demo | 6 | 16-22 | 2 | 上手/上脸/实测 |
| trust | 3 | 22-25 | 1 | 一句话社证（销量/评分/前后对比），可做花字叠加 |
| price_promo | 2 | 25-27 | 1 | 价格卡/优惠闪现 |
| cta | 3 | 27-30 | 1 | 行动指令+紧迫感 |

**砍/缩**：八段全保但 trust 与 price_promo 压成"一句话镜头"；若产品极低客单、无可信背书，**首砍 trust**，秒数还给 demo。
**依据**：骨架严格对齐仓库 social skill 的 Problem-Solution 15-30s 模板（hook 0-3 / 痛点 3-10 / 方案 10-25 / CTA 25-30）——本表 3-10 为 pain+reveal、10-25 为 selling+demo+trust、25-30 为 price+cta，三块边界完全重合；同时吻合 #12 的 30s 式。trust 保留为独立一拍的理由见 #16 与 #15（证明扛转化）。21-34s 是 TikTok 数据的最优时长带（#4），此档为默认推荐档。

### 档位三：60 秒（完整说服链，13-16 镜头）

| section | 秒数 | 累计 | 镜头数 | 说明 |
|---|---|---|---|---|
| hook | 3 | 0-3 | 1 | 同上 |
| pain_point | 7 | 3-10 | 2 | 痛点场景化（1-2 个具体情境） |
| product_reveal | 5 | 10-15 | 1-2 | 登场+是什么+给谁用 |
| selling_point | 12 | 15-27 | 3 | 2-3 个卖点，每个 4-6s 一镜 |
| demo | 15 | 27-42 | 3-4 | 多角度演示/前后对比/数据可视化 |
| trust | 8 | 42-50 | 2 | 评价截图+销量/资质，两镜 |
| price_promo | 5 | 50-55 | 1-2 | 原价锚定→到手价→限时福利 |
| cta | 5 | 55-60 | 1 | 行动指令+重申核心利益 |

**取舍**：八段全保、各自成块；60 秒的增量几乎全给了 selling_point+demo（合计 27s，占 45%）。
**依据**：pain 3-10、hook 0-3、收尾块直接取自 #11（retiplex 60s 模板）；与 #11 的差异是把它 20 秒的社证块拆薄（trust 8s），秒数转给 demo——理由是 #10"60s 扩的是演示"、#6 中间 50% 是"卖点逐一讲述+产品展示"（本表 selling+demo=27s=45%≈青瓜的 50%）。比例核对：痛点 12%/方案块 53%/证明 13%/促单 17%，对照 #13 的 30/40/20/10——痛点更轻、促单更重，因为目标场景是直效带货而非解释型视频，促单收尾占比向 #6 的"最后 10%+价格优势"和 DR 公式靠拢。

### 档位四：90 秒（叙事型/高客单，18-22 镜头）

| section | 秒数 | 累计 | 镜头数 | 说明 |
|---|---|---|---|---|
| hook | 4 | 0-4 | 1 | 可用悬念/故事钩（"3 个月前我还……"） |
| pain_point | 10 | 4-14 | 2-3 | 人物+冲突小叙事 |
| product_reveal | 6 | 14-20 | 1-2 | 转折点：遇见产品 |
| selling_point | 20 | 20-40 | 3-4 | 3 个卖点，逐个展开 |
| demo | 24 | 40-64 | 4-6 | 多场景使用过程（核心块） |
| trust | 12 | 64-76 | 2-3 | 用户证言/检测报告/销量 |
| price_promo | 8 | 76-84 | 2 | 价格锚定+限时优惠拆解 |
| cta | 6 | 84-90 | 1 | 紧迫感收口 |

**取舍**：八段全保；与 60s 相比增量给 demo（+9）、selling_point（+8）、trust（+4）、pain_point（+3）。
**依据**：骨架对齐 #12 的两个 90s 模板——叙事型"人物与冲突 0-15"（本表 hook+pain=0-14）、演示型"引导式演示 6-75"（本表 reveal+selling+demo=14-64 为演示主体、trust 64-76 织入其后）、两模板共同的"紧迫 CTA 76-90"（本表 price_promo+cta=76-90 完全重合）。**风险提示**：#5（>30s 在 FB Reels 难保注意力、90s 是 Reels 上限）与 #8（90 秒对节奏要求很高）都指向此档仅适合高客单/需教育的产品，prompt 中应要求 90s 档用"小故事"组织而非平铺卖点，且维持每 ~5 秒一个新信息点。

**附：口播字数参考**（约束台词长度）：英文 15s≈40 词、30s≈60-120 词（#13、#14）、60s≈140-160 词、90s≈210-230 词（#13）。中文按 ~4 字/秒折算约 15s≈60 字 / 30s≈120 字 / 60s≈240 字 / 90s≈360 字——**此换算为推断值，无直接来源，建议按 TTS 实测语速校定**。

## 四、出处与推断标注

**有直接出处的数字**：

- hook 恒 0-3s（多源一致：#1/#6/#7/#11/#12/仓库 social skill）；cta 恒末尾 3-6s（#11/#12/仓库 short-form-video.md）。
- 30s 档三大块边界（0-3 / 3-25 / 25-30）：仓库 Problem-Solution 模板与 #12 双源直接给出。
- 60s 档 pain 3-10、收尾 CTA 块起点：#11 原文秒数。
- 90s 档 0-15 冲突块与 76-90 促单块：#12 原文秒数。
- 单镜头 2-6s/快切 2-3s：仓库 B-roll 1-3s、slideshow 2-4s、#11 每 5-8s 换画面、#8 每 5s 兴趣点。
- "前 3 秒亮产品"（#1 的 63%）、"前 6 秒 90% 记忆"（#2）、"多场景 38% 转化提升"（#3）、"21-34s 最优带"（#4）、占比骨架 3s/20%/50%/10%（#6）与 30/40/20/10（#13）、字数换算（#13/#14）。

**基于结构原则的推断（无单一直接出处）**：

1. **所有 section 级精确到秒的切分值**（如 30 档 trust=3s、price_promo=2s；60 档 reveal=5s；90 档 selling=20s 等）：行业来源只给到 3-5 个粗块的区间，本表是把粗块边界按八段 taxonomy 内插得出——内插原则是"粗块边界不动、块内按卖点数/镜头长度均分"。每档总和精确等于档位时长是为 prompt 可执行性做的归一化。
2. **15 秒档砍三段的决策**：业界 15s 模板只有 3-4 段，从未出现 8 段；砍 pain_point/trust/price_promo 而非别段，推理依据是 PAS 中 problem 本就占据 0-3s（可由 hook 兼任）、promo 可压成 CTA 内一句话（#12 的 15s 式末段即"好奇缺口+CTA"合体）、而产品/卖点/演示是带货视频不可砍的最小说服单元（#9：8-15 秒内要见到产品亮点）。
3. **镜头数区间**：由"档位时长 ÷ 单镜头 2-6s"推算并向中高值取（多场景有 #3 官方背书），非任何来源直接给出。
4. **60/90 档把 retiplex 式的 20 秒社证块压到 8-12s**：在 #11（社证重）与 #6/#10（演示重）两派之间折中，偏向后者因为目标场景是图生视频的带货片，演示画面可生成性更强；此为产品向判断，已随配比表一并经维护者审定。
5. **中文字数/秒**：从英文 150 词/分钟类比推断，未找到权威中文语速来源。

**未发现/未验证**：marketingskills 仓库无任何按档位的时长配比内容（已如实盘点）；TikTok 官方博客两个 URL 因证书问题无法直接抓取原文，相关数字均经 ≥2 个第三方转引交叉确认；"3 秒留存 ≥58%"出自行业经验帖（#8）而非抖音官方口径，引用时建议降级为参考值。
