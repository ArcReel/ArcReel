"""PROTOTYPE — 试写样例集与假资产表（throwaway）。"""

from __future__ import annotations

from logic import FakeAsset

# 假资产表：仿 project.json 三 bucket + #1383 拍板的 reference_audio / 既有 voice_style
ASSETS: dict[str, FakeAsset] = {
    "林澈": FakeAsset("林澈", "character", voice_style="清亮的年轻女声，语速偏快", has_reference_audio=True),
    "周明远": FakeAsset("周明远", "character", voice_style="低沉沙哑的中年男声", has_reference_audio=False),
    "陈默": FakeAsset("陈默", "character", voice_style="平静克制的青年男声", has_reference_audio=True),
    "苏晚晴": FakeAsset("苏晚晴", "character", voice_style="温柔的少女声线", has_reference_audio=True),
    "雨夜天台": FakeAsset("雨夜天台", "scene"),
    "老宅书房": FakeAsset("老宅书房", "scene"),
    "怀表": FakeAsset("怀表", "prop"),
}

SAMPLES: list[tuple[str, str]] = [
    (
        "标准剧集：双角色对白往返 + 画外音",
        """傍晚雨夜都市文戏，冷调低饱和，气氛压抑。

镜头1 (5s)：中景固定机位，@[林澈] 撑伞站在 @[雨夜天台] 边缘，雨水顺着伞沿滴落，她望向远处霓虹，眼神空洞。
{十年了，她第一次回到这座城市。}

镜头2 (5s)：镜头缓慢推近，@[周明远] 从天台门口走出，脚步沉重地停在她身后三步。
@[周明远]：{我就知道你会来这里。}
@[林澈]：{你不该来的。}

镜头3 (4s)：近景特写，@[林澈] 转身，手指攥紧伞柄，肩膀微微颤抖，@[周明远] 递出一块 @[怀表]。
@[周明远]：{他留给你的，我替他守了十年。}""",
    ),
    (
        "speaker 仅在台词位出现（画面描述未提及）",
        """镜头1 (6s)：中景平稳跟拍，@[苏晚晴] 在 @[老宅书房] 里翻找旧信件，木地板发出轻微吱呀声。
@[陈默]：{别找了，信不在这里。}

镜头2 (4s)：镜头摇向门口，一个身影逆光而立。
@[苏晚晴]：{你把它藏哪儿了？}""",
    ),
    (
        "写歪案例：未注册引用 / 花括号未闭合 / 台词混写在描述里",
        """镜头1 (5s)：中景，@[林澈] 和 @[神秘老者] 对坐，烛光摇曳。
@[林澈]：{这封信是谁写的？

镜头2 (5s)：近景，@[林澈] 低头看信，喃喃说 {不可能，他明明已经死了}，手开始发抖。""",
    ),
    (
        "存量格式：Shot N (Xs): header 兼容",
        """Shot 1 (5s): 中景固定机位，@[林澈] 在 @[老宅书房] 里点亮油灯，环视四周落满灰尘的书架。
Shot 2 (5s): 镜头缓慢拉远，@[林澈] 从书架上取下 @[怀表]，轻轻擦去表面灰尘。
@[林澈]：{原来一直在这里。}""",
    ),
    (
        "单镜无台词（自然回落 B/C 类，无 utterances）",
        """镜头1 (8s)：远景航拍缓慢前推，暴雨中的 @[雨夜天台]，霓虹灯光在湿滑地面上晕开倒影。""",
    ),
]
