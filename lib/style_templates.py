"""风格模版注册表（单一真相源）。

模版 id 命名规则：{category}_{slug}，category ∈ {live, anim}。
prompt 文本来自 docs/生图画风前置提示词4.10.docx。
"""

from __future__ import annotations

# 完整 36 条，顺序即 UI 展示顺序
STYLE_TEMPLATES: dict[str, dict] = {
    # ===== 真人 AI 漫剧 (18) =====
    "live_cinematic_ancient": {"category": "live", "prompt": "画风：精品古装真人短剧风格，专业打光，高质量电视剧质感"},
    "live_zhang_yimou": {"category": "live", "prompt": "画风：参考张艺谋电影风格，极致用色，强烈构图，仪式感叙事"},
    "live_ancient_xianxia": {
        "category": "live",
        "prompt": "画风：精品古装仙侠真人电视剧临江仙风格，美白滤镜，细腻真实的皮肤质感，精致打光，极致高清画质",
    },
    "live_premium_drama": {"category": "live", "prompt": "画风：真人电视剧风格，精品短剧画风，大师级构图"},
    "live_cinema": {
        "category": "live",
        "prompt": "画风：参考院线电影，真人电影风格，达芬奇专业调色，大师级构图，电影色调",
    },
    "live_spartan": {
        "category": "live",
        "prompt": "画风：斯巴达勇士风格，角斗士风格，古装史诗风格，史诗级大片质感，戏剧性的光线，浓重的明暗对比",
    },
    "live_bladerunner": {
        "category": "live",
        "prompt": "画风：银翼杀手2049风格，极简野蛮主义赛博朋克，只用一种颜色来统治画面，粗野主义巨物建筑，气象级的环境粒子，留白",
    },
    "live_got": {
        "category": "live",
        "prompt": "画风：参考权力的游戏电视剧画风，冷色史诗写实，中世纪权谋氛围，粗粝真实质感，低饱和电影调色",
    },
    "live_breaking_bad": {
        "category": "live",
        "prompt": "画风：参考绝命毒师电视剧画风，犯罪题材美学，南美风格滤镜，真实质感滤镜",
    },
    "live_kdrama": {
        "category": "live",
        "prompt": "画风：韩剧偶像剧风格，干净高级的商业影像，柔光美颜，偶像剧式浪漫氛围",
    },
    "live_kurosawa": {
        "category": "live",
        "prompt": "画风：黑泽明风格，高对比黑白质感，强烈自然元素（风雨尘），动态构图，戏剧化光影，人性史诗感",
    },
    "live_nolan": {
        "category": "live",
        "prompt": "画风：诺兰风格，IMAX大画幅质感，冷蓝灰色调，极其锐利的画面，深沉严肃的氛围，精密的光线控制",
    },
    "live_tarantino": {"category": "live", "prompt": "画风：昆汀风格，高对比度，暴力美学，大胆的构图"},
    "live_lynch": {
        "category": "live",
        "prompt": "画风：大卫林奇风格，在看似平淡无奇的日常表象下，隐藏着极度诡异、荒诞、令人毛骨悚然的超现实梦魇",
    },
    "live_anderson": {"category": "live", "prompt": "画风：韦斯安德森风格，糖果色马卡龙配色"},
    "live_wong": {"category": "live", "prompt": "画风：王家卫风格，慵懒暧昧的氛围，颗粒感胶片，东方都市孤独美学"},
    "live_shaw": {"category": "live", "prompt": "画风：参考港式武侠电视剧风格，邵氏电影风格，电影感"},
    "live_cyberpunk": {"category": "live", "prompt": "画风：参考真人赛博朋克电影，电影质感，极致高清画质"},
    # ===== 动画 AI 漫剧 (18) =====
    "anim_3d_cg": {"category": "anim", "prompt": "画风：3D、游戏CG，影视级、虚幻引擎渲染"},
    "anim_cn_3d": {"category": "anim", "prompt": "画风：国风3D、影视级、虚幻引擎渲染"},
    "anim_kyoto": {
        "category": "anim",
        "prompt": "画风：商业动画画风，柔和光影效果，轻柔的赛璐珞上色，柔和的漫射光线，清晰干净的细轮廓线条，参考京都动画作品，参考石立太一动画作品，2d动画",
    },
    "anim_arcane": {"category": "anim", "prompt": "油画三渲二画风：参考《双城之战》(Fortiche / Arcane Style)画风"},
    "anim_us_3d": {"category": "anim", "prompt": "画风：美式3D动画电影风格、影视级、虚幻引擎渲染"},
    "anim_ink_wushan": {
        "category": "anim",
        "prompt": "画风：硬核传统2D水墨，视觉特点：保留生猛的毛笔枯笔笔触，张力拉满。参考《雾山五行》风格",
    },
    "anim_ink_papercut": {
        "category": "anim",
        "prompt": "画风：硬核传统2D水墨/剪纸，视觉特点：保留生猛的毛笔枯笔笔触，色彩借鉴中国传统重彩，战斗动作如中国武术般行云流水，张力拉满。参考《雾山五行》风格",
    },
    "anim_felt": {
        "category": "anim",
        "prompt": "画风：羊毛毡风格，定格动画，真实光影，极致细节，氛围感，故事感，大师级构图",
    },
    "anim_clay": {"category": "anim", "prompt": "画风：黏土动画风格，定格动画，真实光影，大师级构图"},
    "anim_jp_horror": {"category": "anim", "prompt": "画风：低饱和度色调，日式惊悚动画美学"},
    "anim_kr_webtoon": {
        "category": "anim",
        "prompt": "画风：韩国网络漫画风格，半写实动漫风格，简洁柔和的线条画工，流畅的渐变阴影处理，肌肤呈现光泽感，采用柔色调色彩方案，营造浪漫光影效果，采用特写构图手法，营造浓郁的情感氛围，角色细节刻画精细",
    },
    "anim_zzz": {
        "category": "anim",
        "prompt": "画风：次世代高精三渲二 (Next-Gen Cel-Shading 3D) Zenless Zone Zero style，极致干净的赛璐璐线条，结合3D的平滑运镜。面部阴影经过极其严格的法线调整，保证任何角度都唯美",
    },
    "anim_ghibli": {"category": "anim", "prompt": "画风：参考吉卜力动画电影风格，宫崎骏动画风格"},
    "anim_demon_slayer": {
        "category": "anim",
        "prompt": "画风：参考《鬼灭之刃》画风、参考Ufotable飞碟社画风，粗描边",
    },
    "anim_cyberpunk": {"category": "anim", "prompt": "画风：参考动画赛博朋克电影，电影质感，极致高清画质"},
    "anim_bloodborne": {
        "category": "anim",
        "prompt": "画风：参考血源诅咒画风，克苏鲁风格、哥特、写实阴暗、阴冷雾气、低饱和冷色调、虚幻引擎渲染",
    },
    "anim_itojunji": {
        "category": "anim",
        "prompt": "画风：惊悚诡异风、线条锐利，参考伊藤润二动画，数字漫画笔触、轻微颗粒感、哑光质感，惊悚压抑、悬疑感",
    },
    "anim_90s_retro": {
        "category": "anim",
        "prompt": "画风：参考渡边信一郎作品风格，参考神山健治作品，90年代日本复古动漫风格，上世纪九十年代日漫风格的动漫，层次感，线条清晰，迷人氛围",
    },
}


LEGACY_STYLE_MAP: dict[str, str] = {
    "Photographic": "live_premium_drama",
    "Anime": "anim_kyoto",
    "3D Animation": "anim_3d_cg",
}


def resolve_template_prompt(template_id: str) -> str:
    """查表取 prompt。未知 id 抛 KeyError（交给调用方转成 HTTPException）。"""
    return STYLE_TEMPLATES[template_id]["prompt"]


def is_known_template(template_id: str) -> bool:
    return template_id in STYLE_TEMPLATES


def list_templates_by_category() -> dict[str, list[dict]]:
    """按 category 分组，返回列表保持定义顺序。
    每项形如 {'id': 'live_xxx', 'prompt': '...'}。"""
    grouped: dict[str, list[dict]] = {"live": [], "anim": []}
    for tpl_id, data in STYLE_TEMPLATES.items():
        grouped[data["category"]].append({"id": tpl_id, "prompt": data["prompt"]})
    return grouped
