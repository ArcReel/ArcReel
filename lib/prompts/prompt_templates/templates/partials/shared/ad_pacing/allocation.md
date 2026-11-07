General rules (all tiers):

- hook and cta have absolute durations (hook 2-4s, cta 3-6s), not scaled proportionally with the tier; allocate extra seconds first to selling_point/demo, then trust
- price_promo always directly adjoins cta to form the closing conversion block
- Even when the hook is not a product image, the product should appear within the first 3 seconds (text / partial / handheld views are all acceptable)
- A section longer than 6 seconds must split into multiple shots; average 3-5 seconds per shot across the film, with 2-3-second quick cuts allowed at the opening; favor more shots over fewer (multiple settings and angles are supported by official platform data)
- The 30-second tier is recommended by default; organize the 90-second tier as a small story rather than a flat list of selling points, only for high-ticket products or products requiring education

{{ variant("shared/ad_pacing/tier", ad_duration_tier) }}
{% if off_tier_target_duration %}

The target total duration of {{ off_tier_target_duration }} seconds is outside the approved tiers. Proportionally adapt the nearest tier’s allocation template ({{ ad_duration_tier }} seconds) to {{ off_tier_target_duration }} seconds: hook and cta keep their absolute durations; allocate expansion or contraction first to selling_point/demo, then trust, following the general rules.
{% endif %}
