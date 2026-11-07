# Research on timing proportions for eight-part commerce short videos

> Purpose: evidence for duration-tier allocations in ad/short-film (content_mode=ad) script prompts. Maintainers approved all four tables on 2026-06-11. Retain sources/inference labels for later calibration/extensions. Method: read all 50 coreyhaines31/marketingskills skills and cross-check public industry sources.

## 1. marketingskills inventory

Relevant repository content (<https://github.com/coreyhaines31/marketingskills>) follows. **No 15/30/60/90-second allocation tables exist there**: these are SaaS marketing skills, with two timed social-video templates nearest the task. Transferable structural principles remain:

| Skill/path | Applicable experience |
|---|---|
| **social**, Short-Form Video in `skills/social/SKILL.md` | ① Three-second rule: simultaneous visual/spoken/text hooks, hitting in second one; ② preferred TikTok 15–60s / Reels 15–30s / Shorts 30–60s; ③ **Problem-Solution 15–30s: hook 0–3 → amplify pain 3–10 → solution 10–25 → CTA 25–30**, direct 30-second skeleton; ④ avoid slow hooks, unnecessary length, absent CTAs |
| **social/references/short-form-video.md** | ① Curiosity/value/story/controversy hooks for prompts; ② fixed Hook 0–3 + Body + **final 3–5s CTA**; ③ 45–60s story arc: hook 0–3 / setup 3–15 / process 15–45 / result 45–55 / CTA 55–60; ④ **B-roll 1–3s/shot, slideshow 2–4s/page**, matching 2–6s shot limits |
| **ads**, `skills/ads/SKILL.md` + `references/ad-copy-templates.md` | ① PAS (problem → amplification → solution → CTA), BAB, Social Proof Lead, **Direct Response: bold promise → proof → urgent CTA**, supporting trust→price_promo→cta; ② soft/hard/urgent CTAs by goal; ③ **hooks affect performance most**, then headlines, selling points, CTAs |
| **ad-creative**, `skills/ad-creative/SKILL.md` | ① Choose 3–5 angles first (pain/results/proof/urgency/identity...), corresponding to eight-part taxonomy; ② platform awareness: TikTok ad copy below 80 characters |
| **marketing-psychology**, `skills/marketing-psychology/SKILL.md` | ① AIDA (attention → interest → desire → action) supports order; ② authority/social proof informs trust, scarcity/loss aversion price_promo; ③ Rule of 100/mental accounting such as CNY1/day informs price_promo |
| **video**, `skills/video/SKILL.md` | Production tooling gives no proportions, but **85% muted viewing**, 9:16, and models' inability to render readable text constrain shot prompts |
| **copywriting**, `references/copy-frameworks.md` | Headlines such as result-without-pain become spoken hooks; landing-page problem→solution→proof→FAQ→CTA supports sequence |

**Transferable principles**, repository evidence → table use:

1. **Hook/CTA have absolute durations**: 15s or 60s templates retain hook 0–3 and final CTA 3–5, without proportional expansion.
2. **Give longer duration to the middle** (solution/demo/proof), the only scalable portion.
3. **Pain→solution→proof→action order stays**, shortening by removal, not reordering.
4. **Fast 1–4s shots and new information every few seconds** underpin pacing.

## 2. Industry findings and sources

**Platforms/official data**

| # | Finding | Source |
|---|---|---|
| 1 | TikTok: **63% of highest-CTR videos show core messages/products in three seconds** | Official 9 Creative Tips PDF <https://ads.tiktok.com/business/library/Auction_Ads_Creative_Tips.pdf>, cited via <https://lebesgue.io/tiktok-ads/how-to-increase-tiktok-ctr-9-creative-tips> |
| 2 | TikTok: **first six seconds capture 90% of ad recall**; hook–body–close recommended | <https://ads.tiktok.com/business/en/blog/creative-best-practices-top-performing-ads>; direct retrieval blocked by certificate validation, numbers cross-checked via <https://www.stackmatix.com/blog/tiktok-hook-first-3-seconds> and <https://www.2pointagency.com/glossary/tiktok-creative-best-practices-the-3-second-rule/> |
| 3 | TikTok: **multiscene/angle videos convert 38% more**, gain 40%+ impressions; **99% of ecommerce hits use multiple scenes/angles**, 93% audio | <https://tinuiti.com/blog/paid-social/tiktok-best-practices/> citing official <https://ads.tiktok.com/business/en-US/blog/creative-that-drives-conversions> |
| 4 | **One quarter of best videos are 21–34s**, +1.6% impressions | <https://creatify.ai/blog/tiktok-ads-complete-guide-to-creating-high-performing-creatives-in-2026> citing TikTok |
| 5 | Meta Reels maximum 90s, preferred 15–60; **beyond 30s Facebook attention is harder**, most campaigns ideal 6–15; **show product/pain/result within 0.3–2s** | <https://www.jonloomer.com/meta-video-ad-length-requirements/>, <https://thedesignsfirm.com/en/blog/facebook-video-ad-length>, <https://www.brandwatch.com/blog/facebook-video-ads-best-practices/>, official <https://www.facebook.com/business/help/817989058548892> |

**Douyin/Chinese commerce**

| # | Finding | Source |
|---|---|---|
| 6 | Four-part commerce: **pain question 0–3s → 20% reinforce demand → middle 50% selling points/product display → final 10% reasons-to-buy/benefits/price closing** | Qinggua Media, <https://www.opp2.com/317828.html>, directly retrieved |
| 7 | **Over 50% decide to swipe in three seconds**; suspense/pain/direct benefit hooks | Chanmama, <https://www.chanmama.com/yunyingquan/article/1428.html>, directly retrieved |
| 8 | Healthy three-second retention ≥58%, completion ≥32%, hits 45%+; **interest every five seconds**, beginners start 30s, 90s demands pacing | <https://www.cnblogs.com/huizhudev/p/19148789>, practitioner experience, not official |
| 9 | Commerce commonly **15s, maximum 30s**; show strengths/prices/promos by 8–15s; **price most affects purchase**, end with price advantages | <https://www.changbiyuan.com/douyin/duanshipin/2022/duanshipin_1003/54743.html> |

**UGC/direct response**

| # | Finding | Source |
|---|---|---|
| 10 | **Hook→Problem→Solution→Value Prop→Social Proof→CTA**, hook ≤2–3s; 15s compresses middle, 30 balances, 60 expands demos | <https://motionapp.com/blog/how-to-write-ugc-ad-scripts>, retrieved |
| 11 | **60s: hook 0–3 / pain 3–10 / solution 10–25 / proof 25–45 / CTA 45–60**; change views every 5–8s; test ≥5 hooks/script | <https://www.retiplex.com/blog/ugc-ad-script-guide>, retrieved |
| 12 | **90s commerce: benefit hook 0–5 / guided demo 6–75 with proof / urgent CTA 76–90**; **90s narrative: characters and conflict 0–15 / process 16–75 / reveal+CTA 76–90**; 30s: hook 0–3 / value 4–25 / CTA 26–30; **15s TikTok: opening 0–1 / dense value 2–12 / curiosity+CTA 13–15** | <https://shortgenius.com/blog/ad-script-example>, retrieved |
| 13 | Explainers: **Problem30% / Solution40% / Proof20% / Action10%**; **150 English words/min**: 30s 60–75, 60s 140–160, 90s 210–230 | <https://vidico.com/news/explainer-video-script-examples/>, retrieved |
| 14 | **30s UGC speech approximately 75–120 words**, first 3–5s crucial | <https://billo.app/blog/ugc-scripts/>, retrieved |
| 15 | VSL experience: **hook approximately 80% of success**, greatest dropoff10–30s; concrete numerical customer-result proof drives conversion; 2026 trend30–90s micro-VSL | <https://www.blog.theperformers.io/p/video-sales-letters-ads>, <https://adlibrary.com/guides/vsl-ads-ecommerce-guide> |
| 16 | Direct-response UGC15–30s; most common mistake is excessive setup | <https://www.rathlymarketing.com/faq/best-ugc-ad-structure/>, retrieved |

## 3. Four approved allocation tables

**General rules**, all tiers, included with tables in script prompts:

- Hook/cta are **absolute** (hook2–4s, cta3–6s), not proportional; extra time primarily selling_point/demo, then trust (#10/#11/#12/repository principles1–2).
- price_promo always adjoins cta as closing block (#6/#9/ads Direct Response).
- Even without product hooks, **show products within three seconds**, including text/details/handheld (#1/#5).
- Sections over six seconds require multiple shots; average3–5s, opening2–3s cuts (#11 5–8s views, #8 five-second interest, repository1–3s B-roll).
- Prefer more shots: official multiscene/angle conversion evidence (#3).

### Tier 1: 15s, impulse/paid acquisition, 5–6 shots

| section | Seconds | Cumulative | Shots | Notes |
|---|---|---|---|---|
| hook | 3 | 0-3 | 1 | Pain question/result-first; **also pain_point** |
| product_reveal | 2 | 3-5 | 1 | Product/name |
| selling_point | 3 | 5-8 | 1 | One core benefit |
| demo | 4 | 8-12 | 1-2 | One use case/result comparison |
| cta | 3 | 12-15 | 1 | Action, **one promo sentence may cover price_promo** |

**Remove** pain_point into hook (PAS problem0–3, #6/#7), trust (no independent proof room, #12), price_promo into CTA (#12 curiosity+CTA13–15).
**Basis**: #12's native TikTok15s (opening0–1/value2–12/CTA13–15): reveal+selling+demo3–12 gives nine-second dense value; hook/CTA three seconds follow social templates/#1. Shots=15÷2.5–3s.

### Tier 2: 30s, standard commerce/default recommendation, 8–10 shots

| section | Seconds | Cumulative | Shots | Notes |
|---|---|---|---|---|
| hook | 3 | 0-3 | 1 | Visual/spoken/text hooks together |
| pain_point | 4 | 3-7 | 1-2 | Amplify relatable pain |
| product_reveal | 3 | 7-10 | 1 | Product as answer |
| selling_point | 6 | 10-16 | 2 | 1–2 benefits |
| demo | 6 | 16-22 | 2 | Hands-on/face application/testing |
| trust | 3 | 22-25 | 1 | One proof statement: sales/rating/comparison, possible text overlay |
| price_promo | 2 | 25-27 | 1 | Price card/promo flash |
| cta | 3 | 27-30 | 1 | Action/urgency |

**Remove/shorten**: retain all eight but trust/price as one-sentence shots; low-ticket products without credible endorsements **drop trust first**, returning time to demo.
**Basis**: exact social Problem-Solution boundaries: pain+reveal3–10, selling+demo+trust10–25, price+cta25–30; also #12. Independent trust follows #16/#15 proof conversion. TikTok21–34s optimal band (#4) supports default recommendation.

### Tier 3: 60s, full persuasion, 13–16 shots

| section | Seconds | Cumulative | Shots | Notes |
|---|---|---|---|---|
| hook | 3 | 0-3 | 1 | Same |
| pain_point | 7 | 3-10 | 2 | 1–2 concrete contexts |
| product_reveal | 5 | 10-15 | 1-2 | Product/what/for whom |
| selling_point | 12 | 15-27 | 3 | 2–3 benefits, 4–6s shots each |
| demo | 15 | 27-42 | 3-4 | Angles/comparisons/data visuals |
| trust | 8 | 42-50 | 2 | Reviews+sales/qualifications, two shots |
| price_promo | 5 | 50-55 | 1-2 | Original-price anchor→actual price→limited benefit |
| cta | 5 | 55-60 | 1 | Action/restate benefit |

**Tradeoff**: all eight separate; added time primarily selling_point+demo27s,45%.
**Basis**: #11 pain3–10/hook0–3/closing. Reduce its20s proof to8, shifting to demo per #10 and #6 middle50% selling/display (27s 45% approximately 50%). Proportions: pain12%/solution53%/proof13%/closing17% versus #13's30/40/20/10: lighter pain/heavier closing for direct commerce, not explanation, aligned toward #6 final10%+price/DR.

### Tier 4: 90s, narrative/high-ticket, 18–22 shots

| section | Seconds | Cumulative | Shots | Notes |
|---|---|---|---|---|
| hook | 4 | 0-4 | 1 | Suspense/story, e.g. three months ago... |
| pain_point | 10 | 4-14 | 2-3 | Character/conflict mini-story |
| product_reveal | 6 | 14-20 | 1-2 | Product as turning point |
| selling_point | 20 | 20-40 | 3-4 | Three expanded benefits |
| demo | 24 | 40-64 | 4-6 | Multiscene use, core block |
| trust | 12 | 64-76 | 2-3 | Testimonials/tests/sales |
| price_promo | 8 | 76-84 | 2 | Anchors/limited promotions |
| cta | 6 | 84-90 | 1 | Urgent close |

**Tradeoff**: retain eight; versus60 add demo9/selling_point8/trust4/pain_point3 seconds.
**Basis**: #12 narrative conflict0–15 matches hook+pain0–14; guided demo6–75 corresponds reveal+selling+demo14–64 then trust64–76; both urgent76–90 exactly match price+cta. **Risk**: #5 attention beyond30s/90s Reels limit and #8 pacing mean high-ticket/education products only. Require mini-stories rather than flat benefits, with new information approximately every five seconds.

**Speech-length reference**: English 15s ≈40 words,30s ≈60–120 (#13/#14),60s ≈140–160,90s ≈210–230 (#13). Chinese at approximately 4 characters/s:15s ≈60/30s ≈120/60s ≈240/90s ≈360. **This conversion is inferred without direct sources; calibrate using measured TTS speech.**

## 4. Sources versus inference

**Directly sourced numbers**:

- Hook0–3 (#1/#6/#7/#11/#12/social); final CTA3–6 (#11/#12/short-form-video.md).
- 30s boundaries0–3/3–25/25–30: Problem-Solution and #12.
- 60s pain3–10/closing start: #11.
- 90s conflict0–15/closing76–90: #12.
- Shot2–6/fast2–3: repository B-roll1–3/slideshow2–4, #11 views5–8, #8 interest5.
- Products in3s (#1 63%),6s 90% recall (#2),multiscene38% conversion (#3),21–34 band (#4),3s/20/50/10 (#6),30/40/20/10 (#13),word counts (#13/#14).

**Structural inferences without one direct source**:

1. **Every exact section split** (30s trust3/price 2,60s reveal5,90s selling20): sources give3–5 broad blocks, interpolated into eight parts while retaining boundaries and distributing by benefits/shot lengths. Exact totals normalize prompts for execution.
2. **Removing three15s sections**: industry15s has3–4, never eight. PAS problem already0–3 so hooks cover it; promotions fit curiosity+CTA (#12); product/benefit/demo are indispensable minimal persuasion (#9 product strengths by8–15s).
3. **Shot ranges**: tier÷2–6s, favoring medium/high counts because #3 supports multiple scenes, not directly sourced ranges.
4. **Reducing retiplex20s proof to8–12s in60/90**: compromise between proof-heavy #11 and demo-heavy #6/#10, favoring demos for generated commerce visuals' feasibility. Product judgment approved with tables.
5. **Chinese characters/second**: inferred by analogy from English 150 words/min, without authoritative Chinese speech-rate sources.

**Not found/verified**: no tier tables in marketingskills; two TikTok blogs could not be retrieved because of certificates, numbers cross-checked through at least two third-party citations. Three-second retention ≥58% is practitioner #8, not official Douyin; treat as reference only.
