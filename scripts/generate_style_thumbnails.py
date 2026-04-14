"""一次性脚本：用 Grok (Aurora) 生成 36 条风格缩略图。

运行：
    uv run python scripts/generate_style_thumbnails.py

输出：frontend/public/style-thumbnails/<id>.png（1:1，半身像 + 背景）
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.image_backends.base import ImageGenerationRequest  # noqa: E402
from lib.image_backends.grok import GrokImageBackend  # noqa: E402
from lib.style_templates import STYLE_TEMPLATES  # noqa: E402

DB_PATH = ROOT / "projects" / ".arcreel.db"
OUT_DIR = ROOT / "frontend" / "public" / "style-thumbnails"

CONCURRENCY = 4

# 每个风格的人物主体 + 背景氛围（subject）
SUBJECTS: dict[str, str] = {
    # ===== live (18) =====
    "live_cinematic_ancient": "一位身着唐代青色广袖长裙的东方女子，眉目清丽，发髻别玉簪，背景：朱红木格窗与暖色烛光的古装内室",
    "live_zhang_yimou": "一位身着大红戏曲华服的东方女子，眉心点朱砂，神情凝重，背景：高墙朱门、成列红灯笼的封闭大院",
    "live_ancient_xianxia": "一位白衣长发的东方仙侠女子，眉眼温柔，指尖浮光，背景：云海翻涌的青色山峦",
    "live_premium_drama": "一位都市现代女性，亚洲面孔，知性气质，淡妆短发，背景：柔光书房和米色墙面",
    "live_cinema": "一位中年男子，风衣立领，眼神深邃，背景：夜色街道的暖黄路灯和霓虹倒影",
    "live_spartan": "一位肌肉强健的古代武士，额头有汗与血痕，红色披风，背景：沙尘与火光弥漫的战场",
    "live_bladerunner": "一位都市女子，短发微湿，身着黑色高领外套，背景：赛博朋克霓虹雨夜、巨型全息广告",
    "live_got": "一位中世纪西方贵族男子，披皮草斗篷，蓄须冷峻，背景：阴雨雪原与远处的灰色石堡",
    "live_breaking_bad": "一位秃顶中年白人男性，蓄山羊胡，戴黑色圆框眼镜，背景：沙漠公路与傍晚橙红天空",
    "live_kdrama": "一位年轻东亚男子，清爽短发，针织衫浅色系，温柔微笑，背景：柔光落地窗与白色室内",
    "live_kurosawa": "一位披铠甲的日本武士，神情严峻，长发束起，背景：大风中倾斜的芦苇原与暴雨前的阴云",
    "live_nolan": "一位身穿西装的男子，表情深沉，领带微乱，背景：冷蓝色调的现代摩天楼与玻璃幕墙反光",
    "live_tarantino": "一位身穿黑西装黄衬衫的男子，嘴角含笑，叼着牙签，背景：饱和度浓烈的复古 diner 餐厅红色卡座",
    "live_lynch": "一位金发女子，浓妆红唇，凝视镜头，表情略显空洞，背景：昏暗房间与一盏孤零零的红色台灯",
    "live_anderson": "一位制服少年，正对镜头呆立，对称构图，背景：马卡龙糖果色墙面与对称门框",
    "live_wong": "一位旗袍东方女子，斜倚身影，半遮于烟雾，背景：昏黄狭窄的霓虹小巷与玻璃折射",
    "live_shaw": "一位身披武侠长袍的东方侠客，手持长剑，束发目光冷峻，背景：古老客栈与烛光",
    "live_cyberpunk": "一位金属机甲改造的都市战士，眼部发光线路，背景：高楼霓虹和雨夜",
    # ===== anim (18) =====
    "anim_3d_cg": "一位穿未来战甲的女战士，银发精致纹理，背景：科幻宇宙基地走廊、虚幻引擎质感光影",
    "anim_cn_3d": "一位国风 3D 少女，挽起长发，身着淡绿襦裙，背景：烟雨江南水乡与青瓦白墙",
    "anim_kyoto": "一位日式校服女高中生，柔和阳光下微笑，短发随风，背景：春日樱花树下的小路",
    "anim_arcane": "一位红发叛逆少女，脸颊点彩绘，眼神锐利，背景：蒸汽朋克都市的锈迹铁桥与紫色能量结晶",
    "anim_us_3d": "一位圆润可爱的 3D 小男孩主角，大眼睛咧嘴笑，背景：五彩斑斓的奇幻森林",
    "anim_ink_wushan": "一位水墨画风的东方剑客，黑衣飞扬，手持长剑，背景：浓墨挥洒的山岭与留白",
    "anim_ink_papercut": "一位红衣剪纸风少年，造型扁平化，背景：深色山峦剪影与鲜红朝阳",
    "anim_felt": "一个羊毛毡质感的小孩，毛茸茸大眼睛，背景：毛线纹理的温暖小屋与壁炉",
    "anim_clay": "一位黏土风质感的滑稽小男子，咧嘴笑，背景：黏土搭建的彩色集市摊位",
    "anim_jp_horror": "一位苍白长发少女，眼神空洞，白色连衣裙，背景：黄昏废弃老宅与灰色天花板",
    "anim_kr_webtoon": "一位韩漫半写实美男，柔和渐变阴影，发丝光泽，背景：柔焦都市窗边与暖光",
    "anim_zzz": "一位酷飒银发机甲少女，赛璐璐线条锐利，侧脸精致，背景：霓虹都市街道与能量光效",
    "anim_ghibli": "一位穿裙子的少女，风吹起发丝，脸颊红润，背景：碧绿草原与蓝天白云",
    "anim_demon_slayer": "一位身着黑羽织和服的剑士少年，额头纹印，粗描边风格，背景：黑夜与红月下的山林",
    "anim_cyberpunk": "一位动画风格的改造人少女，机械臂发光，背景：霓虹雨夜都市街角",
    "anim_bloodborne": "一位哥特风猎人男子，黑色皮大衣和三角帽，冷峻阴郁，背景：浓雾笼罩的维多利亚石砌老城",
    "anim_itojunji": "一位表情诡异的黑发女子，锐利线条与哑光质感，背景：昏暗扭曲的老屋走廊与异样阴影",
    "anim_90s_retro": "一位 90 年代日漫风格的黑发少女，眉眼锋利，背景：黄昏都市天台与霓虹招牌",
}


def load_grok_api_key() -> str:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute(
            "SELECT api_key FROM provider_credential WHERE provider='grok' AND is_active=1 LIMIT 1"
        )
        row = cur.fetchone()
        if not row or not row[0]:
            raise RuntimeError(f"未在 {DB_PATH} 的 provider_credential 表找到 grok 活跃凭证")
        return row[0]
    finally:
        conn.close()


def build_prompt(tpl_id: str) -> str:
    tpl = STYLE_TEMPLATES[tpl_id]
    subject = SUBJECTS[tpl_id]
    style = tpl["prompt"]
    return (
        f"{subject}。{style}。"
        "中景半身像（medium bust portrait），人物位于画面中心，正面或三分之二侧面，"
        "人物为画面主角，背景氛围体现风格基调，1:1 构图，"
        "光线聚焦于人物面部，高质量高清细节。"
    )


async def generate_one(
    backend: GrokImageBackend,
    sem: asyncio.Semaphore,
    tpl_id: str,
    out_path: Path,
) -> tuple[str, bool, str]:
    if out_path.exists():
        # 删除旧图以便重生成
        out_path.unlink()
    prompt = build_prompt(tpl_id)
    async with sem:
        try:
            await backend.generate(
                ImageGenerationRequest(
                    prompt=prompt,
                    output_path=out_path,
                    aspect_ratio="1:1",
                    image_size="1K",
                )
            )
            return (tpl_id, True, "ok")
        except Exception as exc:
            return (tpl_id, False, f"{type(exc).__name__}: {exc}")


async def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    missing = set(STYLE_TEMPLATES) - set(SUBJECTS)
    if missing:
        raise RuntimeError(f"SUBJECTS 缺少: {sorted(missing)}")

    api_key = load_grok_api_key()
    backend = GrokImageBackend(api_key=api_key)
    sem = asyncio.Semaphore(CONCURRENCY)

    tasks = [
        generate_one(backend, sem, tpl_id, OUT_DIR / f"{tpl_id}.png")
        for tpl_id in STYLE_TEMPLATES
    ]

    ok = fail = 0
    for coro in asyncio.as_completed(tasks):
        tpl_id, success, msg = await coro
        if success:
            ok += 1
            print(f"  ✅ {tpl_id}")
        else:
            fail += 1
            print(f"  ❌ {tpl_id} — {msg}")

    print(f"\n完成：成功 {ok} / 失败 {fail} / 共 {len(STYLE_TEMPLATES)}")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
