通用规则（适用于全部档位）：

- hook 与 cta 是绝对时长段（hook 2-4s、cta 3-6s），不随档位等比放大；加长的秒数优先给 selling_point/demo，其次 trust
- price_promo 永远紧贴 cta 构成「促单收尾块」
- 即使 hook 不是商品画面，商品也应在前 3 秒内入画（文字/局部/手持均可）
- 单 section 超过 6 秒必须拆成多个分镜；全片平均 3-5 秒/分镜，开头允许 2-3 秒快切；分镜数宁多勿少（多场景多角度有平台官方数据背书）
- 30 秒档为默认推荐档；90 秒档用「小故事」组织而非平铺卖点，仅适合高客单/需教育的商品

{{ variant("shared/ad_pacing/tier", ad_duration_tier) }}
{% if off_tier_target_duration %}

目标总时长 {{ off_tier_target_duration }} 秒不在审定档位内，按距离最小的档位 {{ ad_duration_tier }} 秒的配比模板按比例适配到 {{ off_tier_target_duration }} 秒：hook 与 cta 是绝对时长段维持原秒数，伸缩量按通用规则优先给 selling_point/demo，其次 trust。
{% endif %}