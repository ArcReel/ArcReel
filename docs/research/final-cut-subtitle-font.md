# 成片字幕的随包字体选型

> 状态：调研完成，结论交由地图 [#2667](https://github.com/ArcReel/ArcReel/issues/2667)「Wayfinder: Agent 自动剪辑」汇总。
> 关联：[#2782](https://github.com/ArcReel/ArcReel/issues/2782)（本票）；前置决策 ADR 0095（成片用随包 `imageio-ffmpeg` 渲染，字幕字体随仓库自带一款 OFL 中文字体，经 `fontsdir` 交给 libass，只带粗体一个字重）。
> 数据日期：2026-09-30。字体文件取自各项目的官方仓库或 Release（链接见各节），许可证取自随字体发布的 LICENSE 与字体 `name` 表。标「实测」的结果由下文的脚本在本机（macOS arm64）或 Docker（`python:3.12-slim` arm64）中得到；标「推断」的是本文的推理，不是来源原文。

## 结论

1. **推荐随包 `SourceHanSansCN-Bold.otf`（思源黑体 CN Bold，Source Han Sans 2.005），原样分发，不自行子集化。**
   - 来源：[adobe-fonts/source-han-sans](https://github.com/adobe-fonts/source-han-sans) `release` 分支 `SubsetOTF/CN/SourceHanSansCN-Bold.otf`（Release `2.005R`，2025-06-18）。这是 Adobe 官方出的「按地区子集」版本，只保留中国大陆用字需要的字形，单文件 8.57 MB（gzip 后 7.51 MB）。
   - 字形覆盖（实测）：GB2312 全部 6763 个汉字、CJK 统一汉字 20992 个、ASCII、常用中英文标点、越南语全部 178 个字母（含 U+1EA0–U+1EF9 整段和 Ơ ơ Ư ư Đ đ Ă ă）与 ₫，全部命中。界面三种语言（zh / en / vi）i18n 文案里出现过的字符也全部命中。
   - 剪映端：pyJianYingDraft 的 `FontType.SourceHanSansCN_Bold` 就是剪映字体库里的同名字体，属免费字体。成片与剪映草稿可以用同一款字体。
2. **字幕文本在进入渲染前必须做 NFC 规范化。** 思源黑体只带 U+0300、U+0301 两个组合附加符，缺 U+0302、U+0303、U+0306、U+0309、U+031B、U+0323。实测分解形式（NFD）的越南语会被 libass 按字逐个回退：macOS 上混进 Helvetica，Docker 里直接画成方框。NFC 之后全部落在预组合字符上，渲染正确。
3. **渲染前按字体 cmap 预检字幕里的字符，把缺字当成可报告的问题，不依赖回退。** ffmpeg 的 `subtitles` 滤镜不设默认字体。Docker 镜像里没有系统字体时，缺字会画成方框，字体名写错时整条字幕不显示，而且都不报错；macOS、Windows 上又会静默换成系统字体。三个平台行为不一致，只有预检能保证各环境出片一致。
4. **ASS 里的字体名写 `Source Han Sans CN`（nameID 1），`Bold` 置真。** 不要写全名 `Source Han Sans CN Bold`：对 CFF 字体 libass 只认 PostScript 名，全名匹配不上（实测）。
5. **不用可变字体（`NotoSansSC[wght].ttf`）。** libass 只看到默认实例 Thin，按 `Noto Sans SC` 匹配不上；按 `Noto Sans SC Thin` 匹配上后是 Thin 加伪粗体（实测）。
6. **备选：Noto Sans SC Bold（noto-cjk `Sans/SubsetOTF/SC/NotoSansSC-Bold.otf`，8.54 MB）。** 与思源黑体是同一套设计，覆盖相同，视觉上无法分辨；但停在 2.004（2022-01），剪映里也没有同名条目。只有将来确实要自行子集化时才考虑换它（见 §4 保留字体名）。

## 1. 候选与实测对比

| 候选 | 许可证 | 版本 | 文件 | 体积 raw / gzip | 越南语 | 汉字 | 剪映同款 |
|---|---|---|---|---|---|---|---|
| **思源黑体 CN Bold**（Source Han Sans，按地区子集 OTF） | OFL 1.1，保留字体名 `Source` | 2.005（2025-06） | `SourceHanSansCN-Bold.otf` | 8.57 / 7.51 MB | 预组合全覆盖；缺 6 个组合符 | 20992 | `SourceHanSansCN_Bold`（同一款） |
| 思源黑体 SC Bold（按语言 OTF，含全部地区字形） | 同上 | 2.005 | `SourceHanSansSC-Bold.otf` | 16.96 / 14.22 MB | 同上 | 20992 | 同上 |
| Noto Sans SC Bold（noto-cjk 按地区子集 OTF） | OFL 1.1 | 2.004（2022-01） | `NotoSansSC-Bold.otf` | 8.54 / 7.45 MB | 同上 | 20976 | 无同名；设计同 `SourceHanSansCN_Bold` |
| Noto Sans CJK SC Bold（noto-cjk 按语言 OTF） | OFL 1.1 | 2.004 | `NotoSansCJKsc-Bold.otf` | 17.00 / 14.18 MB | 同上 | 20976 | 同上 |
| Noto Sans SC Bold 静态 TTF（Google Fonts API 实例化） | OFL 1.1，保留字体名 `Source` | 2.004-H2 | `NotoSansSC-Bold.ttf` | 10.53 / 6.37 MB | 同上 | 20976 | 同上 |
| Noto Sans SC 可变字体（Google Fonts） | 同上 | 2.004 | `NotoSansSC[wght].ttf` | 17.77 / 11.30 MB | 同上 | 20976 | 同上；libass 只用到 Thin，排除 |
| 思源柔黑体 CN Bold（Resource Han Rounded） | OFL 1.1 | 0.990（2018-12） | `ResourceHanRoundedCN-Bold.ttf` | 13.98 / 7.38 MB | 同上 | 20976 | `ResourceHanRoundedCN_Bold` |
| 更纱黑体 SC Bold（Sarasa Gothic，Unhinted） | OFL 1.1 | 1.0.42（2026-09） | `SarasaGothicSC-Bold.ttf` | 12.90 / 7.57 MB | 全覆盖（含组合符） | 20992 | 无 |
| 霞鹜文楷 Medium（LXGW WenKai） | OFL 1.1 | 1.522（2026-03） | `LXGWWenKai-Medium.ttf` | 25.38 / 13.06 MB | 全覆盖（含组合符） | 20992 | `LXGWWenKai_Bold`；上游已不发 Bold，楷体不适合做字幕 |
| 得意黑（Smiley Sans） | OFL 1.1 | 2.0.1（2024-02） | `SmileySans-Oblique.otf` | 2.00 / 1.64 MB | 预组合全覆盖；缺全部 8 个组合符与 ₫ | 8057 | `得意黑`；只有一个斜体字重，缺字多 |

体积为实测文件字节数；gzip 为 `gzip -6` 的结果，近似 Docker 镜像层的压缩后增量。所有候选都是 `OS/2.fsType = 0`（可安装嵌入，无限制）。

来源：
- 思源黑体：[source-han-sans README（按地区子集 OTF 一节）](https://github.com/adobe-fonts/source-han-sans/tree/release#region-specific-subset-otfs)、[LICENSE.txt](https://github.com/adobe-fonts/source-han-sans/blob/release/LICENSE.txt)（「Copyright 2014-2025 Adobe, with Reserved Font Name 'Source'. Source is a trademark of Adobe」）、[Release 2.005R](https://github.com/adobe-fonts/source-han-sans/releases/tag/2.005R)。
- Noto CJK：[notofonts/noto-cjk](https://github.com/notofonts/noto-cjk)（最新 Sans Release 为 `Sans2.004`，2022-01-27）、[Sans/LICENSE](https://github.com/notofonts/noto-cjk/blob/main/Sans/LICENSE)（只有 OFL 正文，没有保留字体名声明；OTF 的 `name` 表 ID 0 也没有）。
- Google Fonts 的 Noto Sans SC：[google/fonts `ofl/notosanssc`](https://github.com/google/fonts/tree/main/ofl/notosanssc)（`METADATA.pb` 列出 `vietnamese` 子集；`upstream_info.md` 说明二进制取自 noto-cjk `Sans2.004` 的 `Sans/Variable/TTF/Subset/NotoSansSC-VF.ttf`，CJK 字形源自 Adobe Source Han Sans；`OFL.txt` 首行带 `Reserved Font Name 'Source'`）。静态 Bold TTF 由 `https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@700` 返回的地址取得。
- 其他：[CyanoHao/Resource-Han-Rounded](https://github.com/CyanoHao/Resource-Han-Rounded/releases/tag/v0.990)、[be5invis/Sarasa-Gothic](https://github.com/be5invis/Sarasa-Gothic/releases/tag/v1.0.42)、[lxgw/LxgwWenKai](https://github.com/lxgw/LxgwWenKai/releases/tag/v1.522)、[atelier-anchor/smiley-sans](https://github.com/atelier-anchor/smiley-sans/releases/tag/v2.0.1)。
- 没有纳入的：阿里巴巴普惠体、HarmonyOS Sans、MiSans、OPPO Sans 都是厂商自有许可证，不是 OFL；文泉驿微米黑是 GPL（见 `docs/research/final-cut-render-backends.md`）。

**思源黑体与 Noto Sans SC 的关系（实测）：** 两者 cmap 相差不到 40 个码位；逐字比较 3000 个随机字符的轮廓，点序与结构大多不同（2.004 到 2.005 之间有重绘或重排），但用同一段文字渲染对比，肉眼看不出差别。Google Fonts 的上游说明也写明 Noto CJK 的字形源自 Source Han Sans。

## 2. 越南语覆盖实测

检查集合：

- 越南语全部 178 个字母（12 个元音字母 × 6 个声调 × 大小写，加 17 个辅音字母 × 大小写；非 ASCII 的 134 个），由 `unicodedata.normalize("NFC", 基字母 + 声调符)` 生成；
- Latin Extended Additional 的越南语整段 U+1EA0–U+1EF9（90 个）；
- NFD 输入会用到的 8 个组合附加符：U+0300 U+0301 U+0302 U+0303 U+0306 U+0309 U+031B U+0323；
- ₫（U+20AB）；ASCII 95 个；常用西文与中文标点；GB2312 的 6763 个汉字；
- `frontend/src/i18n/{zh,en,vi}/*.ts` 字符串里实际出现的全部字符（zh 1255、en 105、vi 192 个）。

结果（实测）：

| 字体 | 越南语字母 178 | U+1EA0–1EF9 | 组合附加符 8 | ₫ | GB2312 汉字 | i18n zh/en/vi |
|---|---|---|---|---|---|---|
| 思源黑体 CN Bold / SC Bold | 全 | 全 | 缺 6：U+0302 U+0303 U+0306 U+0309 U+031B U+0323 | 有 | 全 | 全 |
| Noto Sans SC Bold（OTF / 静态 TTF / 可变） | 全 | 全 | 缺同样 6 个 | 有 | 全 | 全 |
| Noto Sans CJK SC Bold | 全 | 全 | 缺同样 6 个 | 有 | 全 | 全 |
| 思源柔黑体 CN Bold | 全 | 全 | 缺同样 6 个 | 有 | 全 | 全 |
| 更纱黑体 SC Bold | 全 | 全 | 全 | 有 | 全 | 全 |
| 霞鹜文楷 Medium | 全 | 全 | 全 | 有 | 全 | 全 |
| 得意黑 | 全 | 全 | 缺 8 个（U+0300–U+0323 全部） | 缺 | 全 | 各缺 3 个：U+2264 ≤、U+2265 ≥、U+2318 ⌘ |

复现方法（字体文件下载到临时目录，不入库）：

```bash
mkdir -p /tmp/fontres && cd /tmp/fontres
curl -sSLO https://raw.githubusercontent.com/adobe-fonts/source-han-sans/release/SubsetOTF/CN/SourceHanSansCN-Bold.otf
uv run --no-project --with fonttools python check_coverage.py SourceHanSansCN-Bold.otf \
  --i18n-dir <ArcReel>/frontend/src/i18n
```

`check_coverage.py`：

```python
"""Check a font's cmap against the Simplified Chinese / English / Vietnamese subtitle character sets."""
import argparse, itertools, pathlib, re, unicodedata
from fontTools.ttLib import TTCollection, TTFont

VI_VOWELS = "aăâeêioôơuưy"
VI_TONES = ["", "̀", "́", "̃", "̉", "̣"]  # 无、huyền、sắc、ngã、hỏi、nặng
VI_CONSONANTS = "bcdđghklmnpqrstvx"

def vi_letters():
    out = set()
    for base in VI_VOWELS + VI_VOWELS.upper():
        for tone in VI_TONES:
            c = unicodedata.normalize("NFC", base + tone)
            assert len(c) == 1
            out.add(c)
    return out | set(VI_CONSONANTS + VI_CONSONANTS.upper())

def gb2312_hanzi():
    out = set()
    for hi, lo in itertools.product(range(0xB0, 0xF8), range(0xA1, 0xFF)):
        try:
            ch = bytes([hi, lo]).decode("gb2312")
        except UnicodeDecodeError:
            continue
        if "一" <= ch <= "鿿":
            out.add(ch)
    return out

def i18n_chars(root):
    out = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        text = "".join(p.read_text(encoding="utf-8") for p in d.glob("*.ts"))
        strs = re.findall(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"|`((?:[^`\\]|\\.)*)`", text)
        chars = {unicodedata.normalize("NFC", c) for g in strs for s in g for c in s if not c.isspace()}
        out[d.name] = {c for c in chars if len(c) == 1}
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fonts", nargs="+")          # .ttc/.otc 可写 path#N 指定第 N 个 face
    ap.add_argument("--i18n-dir", type=pathlib.Path)
    a = ap.parse_args()
    sets = {
        "vi-letters": vi_letters(),
        "vi-U+1EA0..1EF9": {chr(c) for c in range(0x1EA0, 0x1EFA)},
        "vi-combining": {chr(c) for c in (0x300, 0x301, 0x302, 0x303, 0x306, 0x309, 0x31B, 0x323)},
        "vi-dong": {"₫"},
        "ascii": {chr(c) for c in range(0x20, 0x7F)},
        "latin-typo": set("“”‘’…—–·•€£¥©®™°×÷±"),
        "zh-punct": set("，。、；：？！“”‘’（）《》〈〉【】「」『』…—～·￥％＋－＝／＼｜＠＃＆＊"),
        "gb2312-hanzi": gb2312_hanzi(),
    }
    if a.i18n_dir:
        sets |= {f"i18n-{k}": v for k, v in i18n_chars(a.i18n_dir).items()}
    for spec in a.fonts:
        path, _, idx = spec.partition("#")
        font = TTCollection(path).fonts[int(idx or 0)] if path.lower().endswith((".ttc", ".otc")) else TTFont(path, lazy=True)
        cps = set(font.getBestCmap())
        print(f"\n## {font['name'].getDebugName(4)}  cmap={len(cps)}")
        for label, chars in sets.items():
            miss = sorted(c for c in chars if ord(c) not in cps)
            print(f"  {label:<18} {'OK' if not miss else f'MISSING {len(miss)}'}", " ".join(f"U+{ord(c):04X}" for c in miss[:60]))

if __name__ == "__main__":
    main()
```

**组合附加符缺失的实际影响（实测）。** 用随包 ffmpeg（macOS arm64 为 `imageio-ffmpeg` 0.6.0 自带的 7.1；Docker 里为 johnvansickle 的 7.0.2 静态构建）烧录同一句越南语的 NFC 与 NFD 两种形式：

- NFC：全部正确。
- NFD：libass 对每个缺失的组合符单独找回退字体（日志 `Glyph 0x302 not found, selecting one more font`）。macOS 上换成 Helvetica-Bold，附加符位置错乱；Docker 里找不到任何回退（`failed to find any fallback with glyph 0x302`），画成方框。即使字体里有 U+0300/U+0301，分解形式的附加符也没有正确叠放。

所以不论选哪款字体，字幕文本都要先做 `unicodedata.normalize("NFC", text)`。越南语输入法和大多数来源给出的本来就是 NFC；NFD 主要来自 macOS 文件名、部分复制粘贴和旧式「Unicode tổ hợp」输入（推断）。剪映端建议同样传 NFC 文本。

## 3. 体积与子集化

- **推荐文件的增量：** 镜像与本地安装包各多 8.57 MB（压缩后约 7.5 MB）。相比 `imageio-ffmpeg` 单个 wheel 的 21–31 MB（见 `docs/research/final-cut-render-backends.md`）是小头。字体文件进 Git 后会永久留在历史里，升级一次字体再加一份；这是 ADR 0095「随仓库自带」的既定代价，本文不另作处理。
- **官方已提供的精简形态：** 思源黑体与 Noto CJK 都发布「按地区子集」OTF（CN / SC 子集约 8.5 MB，是按语言完整版 17 MB 的一半）。这是上游自己出的版本，原样分发不算修改，可以保留字体名。**只带粗体一个文件即可**，两家都提供单字重的单文件下载。
- **自行子集化的收益与代价（实测体积）：** 按 GB2312 汉字 + 拉丁 / 越南语 + 常用标点（共 8317 个码位）对思源黑体 CN Bold 子集化，得到 2.07 MB。但：
  - GB2312 之外的字（人名、地名、古风小说里常见的生僻字）会缺字。而第 2 节已经说明缺字在 Docker 里就是方框，所以子集越小，出问题的字幕越多。
  - 按 OFL，子集属于「Modified Version」（许可证原文：「any derivative made by adding to, deleting, or substituting -- in part or in whole -- any of the components of the Original Version」），不得再使用保留字体名（第 3 条）。思源黑体的保留字体名是 `Source`，子集后必须改名，改名后的名字也不能再含「Source」；OFL FAQ 2.6 明确说子集化属于修改，「would not normally allow the use of RFNs」。
  - Noto Sans SC 在 noto-cjk 仓库的 LICENSE 与字体 `name` 表里都没有保留字体名声明，子集后可以保留名字；但 Google Fonts 分发的同一字体的 `OFL.txt` 又写了 `Reserved Font Name 'Source'`，两处不一致。真要自行子集化，更稳妥的做法是无论选哪款都改名（推断）。
  - 结论：8.5 MB 的代价换不缺字、不改名，值得。**不自行子集化。**
- **分发义务：** OFL 第 2 条允许与任何软件捆绑分发，条件是每份副本附带版权声明和许可证。把 `LICENSE.txt`（原样取自上游）放在字体旁边即可。烧进视频的字幕不受 OFL 约束（OFL FAQ 1.13：「creating any kind of graphic using a font under the OFL does not make the resulting artwork subject to the OFL」；FAQ 1.1 把 video titling 列为允许的用途）。来源：[OFL-FAQ](https://openfontlicense.org/ofl-faq/)。

## 4. 剪映草稿端

来源：pyJianYingDraft 0.2.7 与 0.3.0 的 PyPI wheel 源码（`pyJianYingDraft/text_segment.py`、`pyJianYingDraft/metadata/font_meta.py`），[README](https://github.com/GuanYixuan/pyJianYingDraft#添加文本)。

- **可以指定字体。** `TextSegment(text, timerange, font=FontType.X, style=..., ...)`；不传 `font` 时用剪映默认字体（源码注释：「默认为系统字体」）。导出时只把字体的资源 ID 写进文本素材 `content` 的 `styles[0].font = {"id": resource_id, "path": "D:"}`，源码注释「并不会真正在此处放置字体文件」，字体由剪映自己下载。
- **已知限制：** README 的能力表对 5.9 与 10.8 都标注「未缓存的字体需要二次打开草稿」。即用户本机剪映没下载过这款字体时，第一次打开草稿可能显示默认字体，需要重新打开一次。人工 QA 清单应包含这一项（与 #2751 的决策一致）。
- **可选字体：** `FontType` 在 0.2.7 与 0.3.0 中完全相同，共 798 项，其中免费 480 项、VIP 318 项（`EffectMeta` 第二个参数 `is_vip`）。与候选的对应关系：

| 成片字体 | 剪映 `FontType` | 免费 | 一致程度 |
|---|---|---|---|
| 思源黑体 CN Bold | `SourceHanSansCN_Bold`（`"SourceHanSansCN-Bold"`，resource_id `7265596643066516029`） | 是 | 同一款字体 |
| Noto Sans SC Bold | 无同名；用 `SourceHanSansCN_Bold` | 是 | 同一套设计，名字不同 |
| 思源柔黑体 CN Bold | `ResourceHanRoundedCN_Bold` | 是 | 同一款字体 |
| 霞鹜文楷 | `LXGWWenKai_Bold` / `_Regular` / `_Light` | 是 | 同名，但上游现行版本已无 Bold |
| 得意黑 | `得意黑` | 是 | 同一款字体 |
| 更纱黑体 | 无 | — | 无同款 |

- 剪映字体库里那份 `SourceHanSansCN-Bold` 的具体版本无法从 pyJianYingDraft 得知（只有资源 ID 与 md5）。它与 2.005 在字形上是否逐一相同、越南语覆盖是否一致，只能靠人工 QA 在剪映里看（推断：同名同家族，覆盖应一致）。
- 现有 `server/services/presentation/jianying_draft_service.py` 的 `TextStyle(bold=True)` 在未指定字体时用来加粗默认字体。指定 `SourceHanSansCN_Bold` 之后，这个标志可能在已是粗体的字形上再叠一层伪粗体，与成片不一致；是否要改成 `bold=False`，放进人工 QA 对比确认（推断，未实跑剪映）。

## 5. libass：字体名匹配与集合文件

来源：[libass `ass_fontselect.c`](https://github.com/libass/libass/blob/master/libass/ass_fontselect.c)、[FFmpeg 7.0 `libavfilter/vf_subtitles.c`](https://github.com/FFmpeg/FFmpeg/blob/release/7.0/libavfilter/vf_subtitles.c)，以及下面的实测。

- **`fontsdir` 的工作方式：** `vf_subtitles.c` 调用 `ass_set_fonts_dir(library, fontsdir)`；libass 的 `load_fonts_from_dir` 把目录里每个不以 `.` 开头的文件整体读进内存，作为内嵌字体注册。无法解析的文件（例如 LICENSE.txt）会被跳过。目录里只放这一个字体文件和许可证即可。
- **能匹配上的名字：** `get_font_info` 只收集 `name` 表里 Windows 平台的 nameID 1（家族名，含所有语言）和 nameID 4（全名），外加 PostScript 名。家族名匹配不区分大小写。全名与 PostScript 名的匹配见 `matches_full_or_postscript_name`：两者不一致时，PostScript 轮廓（CFF）字体只认 PostScript 名。实测（`SourceHanSansCN-Bold.otf` 单独放在 `fontsdir`）：
  - `Source Han Sans CN`、`思源黑体 CN`、`SourceHanSansCN-Bold` 都能匹配上；
  - `Source Han Sans CN Bold`（全名）匹配不上，回退到系统字体；
  - `Noto Sans SC` 匹配不上：macOS 上回退为 PingFang SC Semibold，Docker 里**整条字幕不显示**（日志 `failed to find any fallback with glyph 0x0`）。
- **没有默认字体：** `vf_subtitles.c` 调用 `ass_set_fonts(renderer, NULL, NULL, 1, NULL, 1)`，默认字体路径与默认家族都是空的；`ass_font_select` 找不到名字时只剩系统字体提供者的 `get_fallback`。所以名字写错、缺字时的结果完全取决于系统字体：
  - Docker `python:3.12-slim` 里没有 `/etc/fonts` 和 `/usr/share/fonts`，静态 ffmpeg 报 `Fontconfig error: Cannot load default config file` 后仍可渲染 `fontsdir` 里的字体（实测），但没有任何回退；
  - macOS（CoreText）和 Windows（DirectWrite）会静默换成系统字体。
  - 因此字体名要写死成常量，并有测试断言它与随包字体的 nameID 1 一致；缺字靠第 2 节的预检发现。
- **`force_style` 与自己写样式：** `force_style` 用逗号分隔 `键=值` 覆盖样式字段（`vf_subtitles.c` 用 `av_strtok(force_style, ",")` 拆分后交给 `ass_set_style_overrides`）。成片的 ASS 是我们自己生成的，直接在 `[V4+ Styles]` 里写 `Fontname` 和 `Bold` 更清楚，不需要 `force_style`。
- **粗体：** 请求字重 700、字体本身是 700 时，libass 不会加伪粗体。只有请求字重比字体高 150 以上且字体没有 Bold 标志时才会加（见 `font_attributes_similarity` 中关于 faux-bold 的注释）。
- **`.ttc` / `.otc` 集合：** `process_fontdata` 会遍历集合里的每个 face（`face->num_faces`），所以集合文件可以用。实测：把思源黑体 CN Bold 与 Noto Sans SC Bold 合成一个 `.ttc` 放进 `fontsdir`，按 `Noto Sans SC` 匹配到 face 1，按 `Source Han Sans CN` 匹配到 face 0。但官方的集合只有按语言的完整超集（`SourceHanSans.ttc` 等，数十到上百 MB），只带一个字重时没有理由选集合，用单个 OTF 即可。
- **可变字体：** 实测 libass 只登记了默认实例，家族名为 `Noto Sans SC Thin`；按 `Noto Sans SC` 或 `NotoSansSC-Bold` 都匹配不上，按 `Noto Sans SC Thin` 能匹配，但画出来是 Thin 加伪粗体。排除。
- **Windows 路径转义（推断）：** `fontsdir=` 和 `filename=` 的值位于 filtergraph 里，Windows 路径中的 `:` 与 `\` 需要按 [FFmpeg filtergraph 转义规则](https://ffmpeg.org/ffmpeg-filters.html#Notes-on-filtergraph-escaping)转义，实现时要在 Windows 上实测。

## 6. 实测环境与脚本

- `imageio-ffmpeg` 0.6.0：macOS arm64 二进制为 `ffmpeg-macos-aarch64-v7.1`，构建参数含 `--enable-libass --enable-libfreetype --enable-fontconfig --enable-libharfbuzz`，字体提供者为 CoreText；Linux aarch64 二进制为 `ffmpeg-linux-aarch64-v7.0.2`（johnvansickle 静态构建），含 `--enable-fontconfig --enable-libfribidi --enable-libass`。
- 渲染实测：用 `color` 源生成一帧 1280×720 画面，`-vf "subtitles=filename=test.ass:fontsdir=<dir>:force_style='FontName=<name>,Bold=1'"`，加 `-loglevel debug` 读取 libass 的 `fontselect:` 日志确认实际选中的字体，再人工查看输出的 PNG。
- 轮廓比较：用 fontTools 的 `DecomposingRecordingPen` 逐字比较思源黑体 CN Bold 与 Noto Sans SC Bold 的轮廓指令。
- 子集体积探测：`fontTools.subset`，保留全部 OpenType 特性与 `name` 记录，只用于估算体积，不作为分发方案。
