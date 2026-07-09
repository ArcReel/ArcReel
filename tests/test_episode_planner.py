"""分集规划服务的行为测试（mock 文本后端，不触真实 LLM）。

只断言外部行为：账本内容、派生文件、planning_cursor、报错与返回摘要；
不断言 prompt 文本细节或内部调用次数（重试语义除外——重试是对外契约）。
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from lib.episode_planner import (
    EpisodePlanner,
    EpisodePlanningError,
    PlanningConflictError,
    ReplanConfirmationRequired,
    _find_all_overlapping,
)
from lib.text_backends.base import TextGenerationResult

# 源文：三段剧情，句子互不重复，锚点可唯一定位
SOURCE = (
    "第一章 山村少年。李恒在山村长大，自幼习得吐纳之法。"
    "一日他在后山偶得一枚古玉，玉中藏着剑诀。"
    "第二章 下山。李恒辞别师父，踏上去往青云城的路。"
    "城门口他撞见了被追杀的少女。"
    "第三章 风波。少女身份成谜，李恒被卷入漩涡之中。"
)

ANCHOR_EP1 = "玉中藏着剑诀。"
ANCHOR_EP2 = "被追杀的少女。"


def _end_of(anchor: str, text: str = SOURCE) -> int:
    return text.index(anchor) + len(anchor)


class _FakeTextGenerator:
    """按顺序回放预置响应的 TextGenerator 替身，并记录每次请求。"""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.requests = []
        self.model = "fake-model"

    async def generate(self, request, project_name=None):
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("FakeTextGenerator 预置响应已耗尽")
        text = self._responses.pop(0)
        return TextGenerationResult(text=text, provider="fake", model="fake-model")


def _write_project(
    tmp_path: Path,
    *,
    content_mode: str = "narration",
    episodes: list | None = None,
    planning_cursor: dict | None = None,
    extra: dict | None = None,
    source_text: str = SOURCE,
) -> Path:
    project_dir = tmp_path / "demo-proj"
    (project_dir / "source").mkdir(parents=True)
    project = {
        "schema_version": 3,
        "title": "测试项目",
        "content_mode": content_mode,
        "generation_mode": "storyboard",
        "characters": {},
        "scenes": {},
        "props": {},
        "episodes": episodes or [],
        "planning_cursor": planning_cursor,
    }
    if extra:
        project.update(extra)
    (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    (project_dir / "source" / "novel.txt").write_text(source_text, encoding="utf-8")
    return project_dir


def _load_project(project_dir: Path) -> dict:
    return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))


def _plan_response(episodes: list[dict]) -> str:
    return json.dumps({"episodes": episodes}, ensure_ascii=False)


class TestFindAllOverlapping:
    def test_collects_overlapping_starts(self):
        assert _find_all_overlapping("aaaa", "aaa") == [0, 1]

    def test_empty_needle_returns_empty_without_hanging(self):
        # 空 needle 防御边界：直接返回 []，不进入逐位滑动（调用方 anchor 受 min_length=2 约束）
        assert _find_all_overlapping("abcabc", "") == []
        assert _find_all_overlapping("", "") == []


class TestPlan:
    async def test_plan_writes_ledger_derives_files_and_advances_cursor(self, tmp_path: Path):
        project_dir = _write_project(tmp_path)
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {"title": "古玉藏诀", "hook": "玉中剑诀来历成谜", "end_anchor": ANCHOR_EP1},
                        {"title": "城门遇袭", "hook": "少女为何被追杀", "end_anchor": ANCHOR_EP2},
                    ]
                )
            ]
        )
        planner = EpisodePlanner(project_dir, generator=fake)

        result = await planner.plan()

        project = _load_project(project_dir)
        eps = project["episodes"]
        assert [e["episode"] for e in eps] == [1, 2]
        assert eps[0]["title"] == "古玉藏诀"
        assert eps[0]["hook"] == "玉中剑诀来历成谜"
        assert eps[0]["ledger_status"] == "planned"
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": _end_of(ANCHOR_EP1)}
        assert eps[1]["source_range"] == {
            "source_file": "source/novel.txt",
            "start": _end_of(ANCHOR_EP1),
            "end": _end_of(ANCHOR_EP2),
        }
        assert project["planning_cursor"] == {"source_file": "source/novel.txt", "offset": _end_of(ANCHOR_EP2)}

        ep1 = (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8")
        ep2 = (project_dir / "source" / "episode_2.txt").read_text(encoding="utf-8")
        assert ep1 == SOURCE[: _end_of(ANCHOR_EP1)]
        assert ep2 == SOURCE[_end_of(ANCHOR_EP1) : _end_of(ANCHOR_EP2)]

        assert [s.episode for s in result.episodes] == [1, 2]
        assert result.episodes[0].title == "古玉藏诀"
        assert result.episodes[0].hook == "玉中剑诀来历成谜"
        assert result.episodes[0].reading_units > 0
        assert result.source_exhausted is False

    async def test_plan_backfills_old_flow_episodes_before_planning(self, tmp_path: Path):
        """旧拆分流程留下的集（无账本字段 + 余文文件）先回填再续规划，余文文件随提交清理。"""
        project_dir = _write_project(
            tmp_path,
            episodes=[{"episode": 1, "title": "旧集", "script_file": "scripts/episode_1.json"}],
        )
        ep1_end = _end_of(ANCHOR_EP1)
        (project_dir / "source" / "episode_1.txt").write_text(SOURCE[:ep1_end], encoding="utf-8")
        (project_dir / "source" / "_remaining.txt").write_text(SOURCE[ep1_end:], encoding="utf-8")
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "城门遇袭", "hook": "少女为何被追杀", "end_anchor": ANCHOR_EP2}])]
        )

        await EpisodePlanner(project_dir, generator=fake).plan()

        project = _load_project(project_dir)
        eps = {e["episode"]: e for e in project["episodes"]}
        assert eps[1]["ledger_status"] == "planned"  # 回填吸收旧集
        assert eps[1]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": ep1_end}
        assert eps[2]["source_range"] == {
            "source_file": "source/novel.txt",
            "start": ep1_end,
            "end": _end_of(ANCHOR_EP2),
        }
        # 规划窗口从回填出的进度续起：发给模型的原文不含第 1 集内容
        assert ANCHOR_EP1 not in fake.requests[0].prompt
        assert ANCHOR_EP2 in fake.requests[0].prompt
        assert not (project_dir / "source" / "_remaining.txt").exists()
        assert (project_dir / "source" / "episode_2.txt").read_text(encoding="utf-8") == SOURCE[
            ep1_end : _end_of(ANCHOR_EP2)
        ]

    async def test_plan_retries_with_failure_reason_when_anchor_invalid(self, tmp_path: Path):
        """机械校验失败自动重试：锚点不存在 → 重试 prompt 附失败原因，第二轮通过。"""
        project_dir = _write_project(tmp_path)
        bad_anchor = "这句话不在原文里。"
        fake = _FakeTextGenerator(
            [
                _plan_response([{"title": "坏", "hook": "坏", "end_anchor": bad_anchor}]),
                _plan_response([{"title": "古玉藏诀", "hook": "玉中剑诀来历成谜", "end_anchor": ANCHOR_EP1}]),
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 2
        retry_prompt = fake.requests[1].prompt
        assert bad_anchor in retry_prompt  # 失败原因指明具体锚点
        assert "不存在" in retry_prompt
        assert [s.title for s in result.episodes] == ["古玉藏诀"]
        project = _load_project(project_dir)
        assert len(project["episodes"]) == 1

    async def test_plan_retries_on_ambiguous_and_non_monotonic_anchors(self, tmp_path: Path):
        """锚点不唯一 / 范围不连续同样触发重试，失败原因可区分。"""
        repeated = "李恒抬头看了看天。"
        source = "山中清晨。" + repeated + "他继续赶路。" + repeated + "夜幕降临，他找到了破庙。"
        project_dir = _write_project(tmp_path, source_text=source)
        fake = _FakeTextGenerator(
            [
                _plan_response([{"title": "甲", "hook": "甲", "end_anchor": repeated}]),  # 出现两次
                _plan_response(
                    [
                        {"title": "甲", "hook": "甲", "end_anchor": "他继续赶路。"},
                        {"title": "乙", "hook": "乙", "end_anchor": "山中清晨。"},  # 早于上一集结尾
                    ]
                ),
                _plan_response([{"title": "丙", "hook": "丙", "end_anchor": "他继续赶路。"}]),
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 3
        assert "2 次" in fake.requests[1].prompt  # 不唯一：报出现次数
        assert "连续" in fake.requests[2].prompt  # 不连续：报范围推进问题
        assert [s.title for s in result.episodes] == ["丙"]

    async def test_plan_rejects_overlapping_anchor_occurrences_as_ambiguous(self, tmp_path: Path):
        """锚点重叠出现同样判不唯一：非重叠计数会把 "哪哪哪" 中的 "哪哪" 误判为唯一定位。"""
        source = "天哪哪哪，山里炸开了锅。李恒收剑而立。"
        project_dir = _write_project(tmp_path, source_text=source)
        fake = _FakeTextGenerator(
            [
                _plan_response([{"title": "甲", "hook": "甲", "end_anchor": "哪哪"}]),  # 重叠出现两次
                _plan_response([{"title": "乙", "hook": "乙", "end_anchor": "李恒收剑而立。"}]),
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 2
        assert "2 次" in fake.requests[1].prompt  # 按允许重叠的口径计数
        assert [s.title for s in result.episodes] == ["乙"]

    async def test_plan_resolves_anchor_with_halfwidth_punctuation(self, tmp_path: Path):
        """锚点与源文仅差全/半角标点宽度（，→ , 与 。→ .）：折叠容错还原精确 NFC 偏移，一次通过不重试。"""
        project_dir = _write_project(tmp_path)
        half_width_anchor = "古玉,玉中藏着剑诀."  # 源文是全角 ，与 。，模型回显成半角 , 与 .
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "玉中剑诀来历成谜", "end_anchor": half_width_anchor}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1  # 容错命中，未触发重试
        eps = _load_project(project_dir)["episodes"]
        # 还原出的偏移与精确匹配口径逐字节一致
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": _end_of(ANCHOR_EP1)}
        ep1 = (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8")
        assert ep1 == SOURCE[: _end_of(ANCHOR_EP1)]
        assert ep1.endswith("玉中藏着剑诀。")  # 源文标点未被改写（仍是全角 。）
        assert [s.title for s in result.episodes] == ["古玉藏诀"]

    async def test_plan_drama_resolves_anchor_with_punctuation_width_mismatch(self, tmp_path: Path):
        """drama 草稿同样走折叠容错：标点宽度不匹配（。→ .）时解析到正确精确偏移。"""
        project_dir = _write_project(tmp_path, content_mode="drama")
        half_width_anchor = "被追杀的少女."  # 源文全角 。
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {
                            "title": "城门遇袭",
                            "hook": "少女为何被追杀",
                            "end_anchor": half_width_anchor,
                            "story_beats": ["辞别师父", "城门遇袭"],
                            "next_episode_teaser": "风波将起",
                        }
                    ]
                )
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1  # 容错命中，未触发重试
        eps = _load_project(project_dir)["episodes"]
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": _end_of(ANCHOR_EP2)}
        assert eps[0]["outline"]["story_beats"] == ["辞别师父", "城门遇袭"]
        ep1 = (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8")
        assert ep1 == SOURCE[: _end_of(ANCHOR_EP2)]
        assert [s.title for s in result.episodes] == ["城门遇袭"]

    async def test_plan_resolves_anchor_with_whitespace_width_mismatch(self, tmp_path: Path):
        """锚点空白宽度不匹配（全角空格 ↔ 半角空格）：折叠容错还原精确偏移。"""
        source = "The hero　rose at dawn. Then darkness fell over the land."  # 含全角空格 U+3000
        project_dir = _write_project(tmp_path, source_text=source)
        half_width_anchor = "The hero rose at dawn."  # 模型回显成半角空格
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "Dawn", "hook": "hook", "end_anchor": half_width_anchor}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1
        eps = _load_project(project_dir)["episodes"]
        end = source.index("dawn.") + len("dawn.")
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": end}
        assert (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8") == source[:end]
        assert [s.title for s in result.episodes] == ["Dawn"]

    async def test_plan_resolves_anchor_with_unicode_nfc_mismatch(self, tmp_path: Path):
        """锚点与源文 Unicode 规范形不一致（锚为 NFD 分解形 ↔ 源文 NFC）：折叠兜底对锚副本
        施加与 window 相同的 normalize_source_text 归一再匹配，还原精确偏移；Tier1 与源文坐标不变。"""
        source = unicodedata.normalize("NFC", "Tôi yêu tiếng Việt. Hôm nay trời đẹp lắm.")
        project_dir = _write_project(tmp_path, source_text=source)
        nfc_anchor = "Tôi yêu tiếng Việt."
        nfd_anchor = unicodedata.normalize("NFD", nfc_anchor)  # 模型回显成分解形
        assert nfd_anchor != nfc_anchor and len(nfd_anchor) != len(nfc_anchor)
        fake = _FakeTextGenerator([_plan_response([{"title": "Mở đầu", "hook": "hook", "end_anchor": nfd_anchor}])])

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1  # 容错命中，未触发重试
        eps = _load_project(project_dir)["episodes"]
        end = source.index(nfc_anchor) + len(nfc_anchor)  # 精确 NFC 偏移
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": end}
        ep1 = (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8")
        assert ep1 == source[:end]  # 切片取 NFC 原文，未改写源文
        assert [s.title for s in result.episodes] == ["Mở đầu"]

    async def test_plan_resolves_anchor_with_crlf_and_combining_length_change(self, tmp_path: Path):
        """长度护栏：锚同时含 \\r\\n 与会改变长度的组合字符（NFD），两者都让锚比源文对应子串更长。
        归一副本走 normalize_source_text（NFC + 换行统一），end 跨度按归一锚长还原，断言源文偏移/
        切片逐字节落在归一坐标系上、不被 \\r 或组合字符撑长漂移；Tier1 仍逐字节不变。"""
        # 源文已是存储坐标系形态（NFC + \n）；窗口与切片均在此坐标系上
        source = "Tôi yêu tiếng Việt.\nHôm nay trời đẹp lắm. Mọi thứ thật tuyệt vời."
        project_dir = _write_project(tmp_path, source_text=source)
        nfc_sub = "Tôi yêu tiếng Việt.\nHôm nay"  # 源文中的精确子串（NFC + \n）
        # 模型回显：组合字符分解（NFD，变长）+ 换行写成 \r\n（再 +1）
        crlf_nfd_anchor = unicodedata.normalize("NFD", nfc_sub).replace("\n", "\r\n")
        assert "\r\n" in crlf_nfd_anchor and len(crlf_nfd_anchor) > len(nfc_sub)

        fake = _FakeTextGenerator(
            [_plan_response([{"title": "Mở đầu", "hook": "hook", "end_anchor": crlf_nfd_anchor}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 1  # 容错命中，未触发重试
        eps = _load_project(project_dir)["episodes"]
        end = source.index(nfc_sub) + len(nfc_sub)  # 归一坐标系下的精确末偏移（非锚原长）
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": end}
        ep1 = (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8")
        assert ep1 == source[:end]  # 切片逐字节落在源文坐标上，未被 \r/组合字符撑长漂移
        assert [s.title for s in result.episodes] == ["Mở đầu"]

    async def test_plan_rejects_anchor_ambiguous_after_punctuation_folding(self, tmp_path: Path):
        """折叠后仍命中多处：维持「无法唯一定位」拒绝并重试，不静默挑第一处。"""
        source = "他说好的。她说好的。后来他离开了。"  # 两处「说好的。」全角句号
        project_dir = _write_project(tmp_path, source_text=source)
        fake = _FakeTextGenerator(
            [
                _plan_response([{"title": "甲", "hook": "甲", "end_anchor": "说好的."}]),  # 半角 .，折叠后命中两处
                _plan_response([{"title": "乙", "hook": "乙", "end_anchor": "后来他离开了。"}]),
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 2
        assert "无法唯一定位" in fake.requests[1].prompt
        assert "2 次" in fake.requests[1].prompt
        assert [s.title for s in result.episodes] == ["乙"]

    async def test_plan_ignores_negative_cursor_offset(self, tmp_path: Path):
        """游标 offset 为负：按非法游标忽略，从源文开头规划而非尾部静默取段。"""
        project_dir = _write_project(tmp_path, planning_cursor={"source_file": "source/novel.txt", "offset": -5})
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "玉中剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )

        await EpisodePlanner(project_dir, generator=fake).plan()

        eps = _load_project(project_dir)["episodes"]
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": _end_of(ANCHOR_EP1)}

    async def test_plan_continues_from_cursor_advanced_into_next_source_file(self, tmp_path: Path):
        """游标已合法推进到后一个源文件（如升级回填的失锚项目）：续规划从游标起，不重复规划该文件前缀。"""
        c = _end_of(ANCHOR2_MID, SOURCE2)
        project_dir = _write_project(
            tmp_path,
            episodes=[_entry(1, 0, len(SOURCE))],
            planning_cursor={"source_file": "source/novel2.txt", "offset": c},
        )
        (project_dir / "source" / "novel2.txt").write_text(SOURCE2, encoding="utf-8")
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "结伴赶路", "hook": "兽潮来袭", "end_anchor": "途中遭遇兽潮。"}])]
        )

        await EpisodePlanner(project_dir, generator=fake).plan()

        eps = {e["episode"]: e for e in _load_project(project_dir)["episodes"]}
        assert eps[2]["source_range"] == {"source_file": "source/novel2.txt", "start": c, "end": len(SOURCE2)}

    async def test_plan_raises_and_leaves_project_untouched_after_retry_exhaustion(self, tmp_path: Path):
        """重试耗尽：抛 EpisodePlanningError，账本与文件零变更（原子性）。"""
        project_dir = _write_project(tmp_path)
        before = (project_dir / "project.json").read_text(encoding="utf-8")
        bad = _plan_response([{"title": "坏", "hook": "坏", "end_anchor": "不在原文里"}])
        fake = _FakeTextGenerator([bad, bad, bad])

        with pytest.raises(EpisodePlanningError):
            await EpisodePlanner(project_dir, generator=fake).plan()

        assert (project_dir / "project.json").read_text(encoding="utf-8") == before
        assert not list((project_dir / "source").glob("episode_*.txt"))

    async def test_plan_truncation_short_circuits_retry_and_hints_leverage(self, tmp_path: Path):
        """结构化输出被截断时不重试，直接冒泡 EpisodePlanningError 并附带调小窗口/集数的提示（见 docs/adr/0044）。"""
        from lib.text_backends.base import TextOutputTruncatedError

        class _TruncatingGenerator:
            model = "fake-model"

            def __init__(self):
                self.call_count = 0

            async def generate(self, request, project_name=None):
                self.call_count += 1
                raise TextOutputTruncatedError(provider="fake", model="fake-model", output_tokens=64000)

        project_dir = _write_project(tmp_path)
        before = (project_dir / "project.json").read_text(encoding="utf-8")
        fake = _TruncatingGenerator()

        with pytest.raises(EpisodePlanningError) as exc_info:
            await EpisodePlanner(project_dir, generator=fake).plan()

        # 截断不重试：只发生一次调用，不像 schema/机械校验失败那样耗尽 max_attempts
        assert fake.call_count == 1
        assert "planning_window_chars" in str(exc_info.value)
        assert "planning_max_episodes" in str(exc_info.value)
        assert (project_dir / "project.json").read_text(encoding="utf-8") == before

    async def test_plan_accepts_uppercase_json_fence(self, tmp_path: Path):
        """模型输出大写 ```JSON 围栏也能解析（围栏标记不区分大小写）。"""
        project_dir = _write_project(tmp_path)
        fenced = (
            "```JSON\n" + _plan_response([{"title": "古玉藏诀", "hook": "钩子", "end_anchor": ANCHOR_EP1}]) + "\n```"
        )
        fake = _FakeTextGenerator([fenced])

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert [s.title for s in result.episodes] == ["古玉藏诀"]
        assert len(fake.requests) == 1  # 一次通过，未触发重试

    async def test_plan_drama_writes_outline_to_ledger(self, tmp_path: Path):
        """drama 条目加厚为分集大纲：story_beats + next_episode_teaser 落账本 outline。"""
        project_dir = _write_project(tmp_path, content_mode="drama")
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {
                            "title": "古玉藏诀",
                            "hook": "玉中剑诀来历成谜",
                            "end_anchor": ANCHOR_EP1,
                            "story_beats": ["山村习武", "后山得玉"],
                            "next_episode_teaser": "下集李恒下山",
                        }
                    ]
                )
            ]
        )

        await EpisodePlanner(project_dir, generator=fake).plan()

        eps = _load_project(project_dir)["episodes"]
        assert eps[0]["outline"] == {
            "story_beats": ["山村习武", "后山得玉"],
            "next_episode_teaser": "下集李恒下山",
        }

    async def test_plan_drama_rejects_response_missing_story_beats(self, tmp_path: Path):
        """drama 模式缺 story_beats 的输出被 schema 校验打回重试。"""
        project_dir = _write_project(tmp_path, content_mode="drama")
        fake = _FakeTextGenerator(
            [
                _plan_response([{"title": "甲", "hook": "甲", "end_anchor": ANCHOR_EP1}]),
                _plan_response(
                    [
                        {
                            "title": "甲",
                            "hook": "甲",
                            "end_anchor": ANCHOR_EP1,
                            "story_beats": ["节点"],
                            "next_episode_teaser": None,
                        }
                    ]
                ),
            ]
        )

        await EpisodePlanner(project_dir, generator=fake).plan()

        assert len(fake.requests) == 2
        assert "schema" in fake.requests[1].prompt

    async def test_plan_screenplay_respects_author_divisions(self, tmp_path: Path):
        """screenplay：mock 返回尊重作者分集的规划 → 账本落作者的边界 / 标题 / 钩子 / 大纲。"""
        project_dir = _write_project(tmp_path, content_mode="drama", extra={"source_kind": "screenplay"})
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {
                            "title": "山村少年",  # 作者写明的集标题，照搬
                            "hook": "古玉剑诀来历成谜",
                            "end_anchor": ANCHOR_EP1,
                            "story_beats": ["山村习武", "后山得玉"],
                            "next_episode_teaser": "下集李恒下山",
                        },
                        {
                            "title": "下山",
                            "hook": "少女为何被追杀",
                            "end_anchor": ANCHOR_EP2,
                            "story_beats": ["辞别师父", "城门遇袭"],
                            "next_episode_teaser": "下集风波将起",
                        },
                    ]
                )
            ]
        )

        await EpisodePlanner(project_dir, generator=fake).plan()

        eps = _load_project(project_dir)["episodes"]
        assert [e["title"] for e in eps] == ["山村少年", "下山"]
        assert eps[0]["hook"] == "古玉剑诀来历成谜"
        assert eps[0]["source_range"] == {"source_file": "source/novel.txt", "start": 0, "end": _end_of(ANCHOR_EP1)}
        assert eps[1]["source_range"] == {
            "source_file": "source/novel.txt",
            "start": _end_of(ANCHOR_EP1),
            "end": _end_of(ANCHOR_EP2),
        }
        assert eps[0]["outline"] == {"story_beats": ["山村习武", "后山得玉"], "next_episode_teaser": "下集李恒下山"}

    async def test_plan_prompt_branches_on_source_kind(self, tmp_path: Path):
        """screenplay 规划 prompt 携带「尊重作者分集 / 无则按剧情弧语义切 / 不依赖固定标记」；novel 不翻面。"""

        def _one_episode_generator() -> _FakeTextGenerator:
            return _FakeTextGenerator(
                [
                    _plan_response(
                        [
                            {
                                "title": "甲",
                                "hook": "甲",
                                "end_anchor": ANCHOR_EP1,
                                "story_beats": ["节点"],
                                "next_episode_teaser": None,
                            }
                        ]
                    )
                ]
            )

        screenplay_dir = _write_project(tmp_path / "scr", content_mode="drama", extra={"source_kind": "screenplay"})
        scr_fake = _one_episode_generator()
        await EpisodePlanner(screenplay_dir, generator=scr_fake).plan()
        scr_prompt = scr_fake.requests[0].prompt

        novel_dir = _write_project(tmp_path / "nov", content_mode="drama")
        nov_fake = _one_episode_generator()
        await EpisodePlanner(novel_dir, generator=nov_fake).plan()
        nov_prompt = nov_fake.requests[0].prompt

        # screenplay：尊重作者分集、无则按剧情弧语义切、不依赖固定标记
        assert "尊重作者" in scr_prompt
        assert "固定标记" in scr_prompt
        assert "剧情弧" in scr_prompt
        assert "剧本原文片段" in scr_prompt
        # novel：仍是「切分为若干集」的创作口径，不含 screenplay 专属指令（回归）
        assert "尊重作者" not in nov_prompt
        assert "固定标记" not in nov_prompt
        assert "小说原文片段" in nov_prompt

    async def test_plan_window_setting_limits_prompt_window(self, tmp_path: Path):
        """planning_window_chars 项目设置覆盖内部默认：窗口外内容不进 prompt。"""
        window_chars = _end_of(ANCHOR_EP1) + 4
        project_dir = _write_project(tmp_path, extra={"planning_window_chars": window_chars})
        fake = _FakeTextGenerator([_plan_response([{"title": "古玉藏诀", "hook": "钩子", "end_anchor": ANCHOR_EP1}])])

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert ANCHOR_EP2 not in fake.requests[0].prompt  # 窗口被截断
        assert result.source_exhausted is False
        cursor = _load_project(project_dir)["planning_cursor"]
        assert cursor == {"source_file": "source/novel.txt", "offset": _end_of(ANCHOR_EP1)}

    async def test_plan_max_episodes_setting_truncates_batch(self, tmp_path: Path):
        """planning_max_episodes 覆盖每批集数上限：超出的集截断留给下一批。"""
        project_dir = _write_project(tmp_path, extra={"planning_max_episodes": 1})
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {"title": "甲", "hook": "甲", "end_anchor": ANCHOR_EP1},
                        {"title": "乙", "hook": "乙", "end_anchor": ANCHOR_EP2},
                    ]
                )
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert [s.title for s in result.episodes] == ["甲"]
        project = _load_project(project_dir)
        assert len(project["episodes"]) == 1
        assert project["planning_cursor"]["offset"] == _end_of(ANCHOR_EP1)

    async def test_plan_final_window_with_whitespace_tail_marks_source_exhausted(self, tmp_path: Path):
        """规划到全文结尾（仅剩空白）：末集贴齐文末，cursor 到文末，报告源文耗尽。"""
        source = SOURCE + "\n\n"
        project_dir = _write_project(tmp_path, source_text=source)
        last_anchor = "卷入漩涡之中。"
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {"title": "甲", "hook": "甲", "end_anchor": ANCHOR_EP2},
                        {"title": "乙", "hook": "乙", "end_anchor": last_anchor},
                    ]
                )
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert result.source_exhausted is True
        project = _load_project(project_dir)
        assert project["planning_cursor"]["offset"] == len(source)
        assert project["episodes"][-1]["source_range"]["end"] == len(source)
        ep2_file = (project_dir / "source" / "episode_2.txt").read_text(encoding="utf-8")
        assert ep2_file.endswith("漩涡之中。\n\n")

    async def test_plan_on_exhausted_source_returns_without_llm_call(self, tmp_path: Path):
        """游标已到文末：直接返回 source_exhausted，不调模型、不动账本。"""
        project_dir = _write_project(
            tmp_path,
            planning_cursor={"source_file": "source/novel.txt", "offset": len(SOURCE)},
        )
        fake = _FakeTextGenerator([])

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert result.source_exhausted is True
        assert result.episodes == []
        assert fake.requests == []

    async def test_plan_advances_to_next_source_when_current_exhausted(self, tmp_path: Path):
        """多源文件：当前源文件已规划完时，自动从下一个源文件起点续规划。"""
        source2 = "第二部 新的征程。李恒踏入上界，结识了新的同伴。"
        anchor2 = "结识了新的同伴。"
        project_dir = _write_project(
            tmp_path,
            episodes=[_entry(1, 0, len(SOURCE))],
            planning_cursor={"source_file": "source/novel.txt", "offset": len(SOURCE)},
        )
        (project_dir / "source" / "novel2.txt").write_text(source2, encoding="utf-8")
        (project_dir / "source" / "episode_1.txt").write_text(SOURCE, encoding="utf-8")
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "新的征程", "hook": "上界有什么", "end_anchor": anchor2}])]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert "第二部" in fake.requests[0].prompt  # 窗口取自下一个源文件
        project = _load_project(project_dir)
        eps = {e["episode"]: e for e in project["episodes"]}
        assert eps[2]["source_range"] == {"source_file": "source/novel2.txt", "start": 0, "end": len(source2)}
        assert project["planning_cursor"] == {"source_file": "source/novel2.txt", "offset": len(source2)}
        assert (project_dir / "source" / "episode_2.txt").read_text(encoding="utf-8") == source2
        assert result.source_exhausted is True  # 第二个文件也到结尾，且没有更多源文件

    async def test_plan_not_exhausted_when_more_source_files_remain(self, tmp_path: Path):
        """规划到当前源文件结尾但还有后续源文件：不报源文耗尽。"""
        project_dir = _write_project(tmp_path)
        (project_dir / "source" / "novel2.txt").write_text("第二部 新的征程。", encoding="utf-8")
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {"title": "甲", "hook": "甲", "end_anchor": ANCHOR_EP2},
                        {"title": "乙", "hook": "乙", "end_anchor": "卷入漩涡之中。"},
                    ]
                )
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).plan()

        assert result.source_exhausted is False
        cursor = _load_project(project_dir)["planning_cursor"]
        assert cursor == {"source_file": "source/novel.txt", "offset": len(SOURCE)}

    async def test_plan_keeps_unanchored_episode_file_untouched(self, tmp_path: Path):
        """unanchored 集的物理文件是其最终记录：规划提交既不重写也不删除它。"""
        unanchored_text = "这段内容在源文里找不到，是人工改过的。"
        project_dir = _write_project(
            tmp_path,
            episodes=[
                {
                    "episode": 1,
                    "title": "失锚集",
                    "script_file": "scripts/episode_1.json",
                    "source_range": None,
                    "ledger_status": "unanchored",
                }
            ],
            planning_cursor={"source_file": "source/novel.txt", "offset": 0},
        )
        (project_dir / "source" / "episode_1.txt").write_text(unanchored_text, encoding="utf-8")
        fake = _FakeTextGenerator([_plan_response([{"title": "甲", "hook": "甲", "end_anchor": ANCHOR_EP1}])])

        await EpisodePlanner(project_dir, generator=fake).plan()

        assert (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8") == unanchored_text
        eps = {e["episode"]: e for e in _load_project(project_dir)["episodes"]}
        assert eps[1]["ledger_status"] == "unanchored"
        assert eps[2]["ledger_status"] == "planned"

    async def test_plan_forwards_instructions_into_prompt(self, tmp_path: Path):
        """首批规划带 instructions 时，原文进「用户规划意见（必须全部落实）」分节，不用 replan 的「重排」措辞。"""
        project_dir = _write_project(tmp_path)
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )

        await EpisodePlanner(project_dir, generator=fake).plan(instructions="严格按章节切分，一章一集")

        prompt = fake.requests[0].prompt
        assert "# 用户规划意见（必须全部落实）" in prompt
        assert "严格按章节切分，一章一集" in prompt
        assert "用户重排意见" not in prompt  # 首批不是重排

    async def test_plan_without_instructions_omits_section(self, tmp_path: Path):
        """不传 instructions 时 prompt 无指令分节，与今日纯剧情弧行为逐字一致。"""
        project_dir = _write_project(tmp_path)
        fake = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )

        await EpisodePlanner(project_dir, generator=fake).plan()

        prompt = fake.requests[0].prompt
        assert "用户规划意见" not in prompt
        assert "必须全部落实" not in prompt

    async def test_plan_blank_instructions_treated_as_absent(self, tmp_path: Path):
        """纯空白 instructions strip 后视同未传：prompt 与不传时逐字一致（无指令分节）。"""
        blank = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )
        none = _FakeTextGenerator(
            [_plan_response([{"title": "古玉藏诀", "hook": "剑诀来历成谜", "end_anchor": ANCHOR_EP1}])]
        )

        await EpisodePlanner(_write_project(tmp_path / "b"), generator=blank).plan(instructions="   \n  ")
        await EpisodePlanner(_write_project(tmp_path / "n"), generator=none).plan()

        assert blank.requests[0].prompt == none.requests[0].prompt


def _entry(
    num: int,
    start: int,
    end: int,
    *,
    status: str = "planned",
    title: str | None = None,
    source_file: str = "source/novel.txt",
) -> dict:
    return {
        "episode": num,
        "title": title or f"第{num}集",
        "script_file": f"scripts/episode_{num}.json",
        "source_range": {"source_file": source_file, "start": start, "end": end},
        "hook": f"钩子{num}",
        "ledger_status": status,
    }


def _planned_three(tmp_path: Path, *, statuses: tuple[str, str, str] = ("planned", "planned", "planned")) -> Path:
    """已规划 3 集（覆盖全文）的项目：[0,a) [a,b) [b,文末)，cursor 在文末。"""
    a, b = _end_of(ANCHOR_EP1), _end_of(ANCHOR_EP2)
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, 0, a, status=statuses[0]),
            _entry(2, a, b, status=statuses[1]),
            _entry(3, b, len(SOURCE), status=statuses[2]),
        ],
        planning_cursor={"source_file": "source/novel.txt", "offset": len(SOURCE)},
    )
    for num, (s, e) in enumerate([(0, a), (a, b), (b, len(SOURCE))], start=1):
        (project_dir / "source" / f"episode_{num}.txt").write_text(SOURCE[s:e], encoding="utf-8")
    return project_dir


# 第二个源文件：续作内容，锚点可唯一定位
SOURCE2 = "第二部 上界风云。李恒踏入上界，灵气扑面而来。他在坊市结识了云岚宗弟子苏沐。两人结伴前往宗门，途中遭遇兽潮。"

ANCHOR2_MID = "云岚宗弟子苏沐。"


def _planned_two_files(
    tmp_path: Path, *, statuses: tuple[str, str, str, str] = ("planned", "planned", "planned", "planned")
) -> Path:
    """已规划 4 集横跨两个源文件的项目：1-2 集在 novel.txt、3-4 集在 novel2.txt，cursor 在第二个文件末尾。"""
    a = _end_of(ANCHOR_EP1)
    c = _end_of(ANCHOR2_MID, SOURCE2)
    project_dir = _write_project(
        tmp_path,
        episodes=[
            _entry(1, 0, a, status=statuses[0]),
            _entry(2, a, len(SOURCE), status=statuses[1]),
            _entry(3, 0, c, status=statuses[2], source_file="source/novel2.txt"),
            _entry(4, c, len(SOURCE2), status=statuses[3], source_file="source/novel2.txt"),
        ],
        planning_cursor={"source_file": "source/novel2.txt", "offset": len(SOURCE2)},
    )
    (project_dir / "source" / "novel2.txt").write_text(SOURCE2, encoding="utf-8")
    ranges = [(SOURCE, 0, a), (SOURCE, a, len(SOURCE)), (SOURCE2, 0, c), (SOURCE2, c, len(SOURCE2))]
    for num, (text, s, e) in enumerate(ranges, start=1):
        (project_dir / "source" / f"episode_{num}.txt").write_text(text[s:e], encoding="utf-8")
    return project_dir


class TestReplan:
    async def test_replan_repartitions_from_episode_keeping_prior_fixed(self, tmp_path: Path):
        """replan 重排 from_episode 起的范围：之前的集不动，新布局落账本并重写派生文件。"""
        project_dir = _planned_three(tmp_path)
        a = _end_of(ANCHOR_EP1)
        new_anchor = "踏上去往青云城的路。"
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {"title": "辞别下山", "hook": "青云城里有什么", "end_anchor": new_anchor},
                        {"title": "城门风波", "hook": "少女是谁", "end_anchor": "卷入漩涡之中。"},
                    ]
                )
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).replan(2, "第2集在下山处收尾")

        prompt = fake.requests[0].prompt
        assert "# 用户重排意见（必须全部落实）" in prompt  # replan 保留「重排」措辞
        assert "第2集在下山处收尾" in prompt  # 用户意见进 prompt
        assert "钩子1" in prompt  # 之前的集作为已定上下文
        assert ANCHOR_EP1 not in prompt  # 已定范围的原文不重发

        project = _load_project(project_dir)
        eps = {e["episode"]: e for e in project["episodes"]}
        assert len(eps) == 3
        assert eps[1]["title"] == "第1集"  # 未受影响
        assert eps[2]["title"] == "辞别下山"
        new_mid = SOURCE.index(new_anchor) + len(new_anchor)
        assert eps[2]["source_range"] == {"source_file": "source/novel.txt", "start": a, "end": new_mid}
        assert eps[3]["source_range"] == {"source_file": "source/novel.txt", "start": new_mid, "end": len(SOURCE)}
        assert eps[2]["ledger_status"] == "planned"
        # cursor 不变（重排范围闭合）
        assert project["planning_cursor"] == {"source_file": "source/novel.txt", "offset": len(SOURCE)}
        # 派生文件重写一致
        assert (project_dir / "source" / "episode_2.txt").read_text(encoding="utf-8") == SOURCE[a:new_mid]
        assert (project_dir / "source" / "episode_3.txt").read_text(encoding="utf-8") == SOURCE[new_mid:]
        assert [s.episode for s in result.episodes] == [2, 3]
        assert result.stale_episodes == []

    async def test_replan_requires_confirmation_for_consumed_episodes(self, tmp_path: Path):
        """波及已消费集且未确认：返回受影响清单，账本与文件零变更。"""
        project_dir = _planned_three(tmp_path, statuses=("consumed", "consumed", "planned"))
        before = (project_dir / "project.json").read_text(encoding="utf-8")
        fake = _FakeTextGenerator([])

        result = await EpisodePlanner(project_dir, generator=fake).replan(2, "重排")

        assert isinstance(result, ReplanConfirmationRequired)
        assert result.consumed_episodes == [2]
        assert fake.requests == []  # 未确认不调模型
        assert (project_dir / "project.json").read_text(encoding="utf-8") == before

    async def test_replan_confirmed_marks_consumed_as_stale(self, tmp_path: Path):
        """确认后重排：被波及的已消费集在新布局中标 stale（产物不删，状态拉回重做）。"""
        project_dir = _planned_three(tmp_path, statuses=("consumed", "consumed", "planned"))
        new_anchor = "踏上去往青云城的路。"
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {"title": "辞别下山", "hook": "甲", "end_anchor": new_anchor},
                        {"title": "城门风波", "hook": "乙", "end_anchor": "卷入漩涡之中。"},
                    ]
                )
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).replan(2, "重排", confirm_consumed=True)

        eps = {e["episode"]: e for e in _load_project(project_dir)["episodes"]}
        assert eps[1]["ledger_status"] == "consumed"  # 未波及
        assert eps[2]["ledger_status"] == "stale"
        assert eps[3]["ledger_status"] == "planned"  # 原第 3 集本就未消费
        assert result.stale_episodes == [2]

    async def test_replan_shrinking_episode_count_cleans_removed_files(self, tmp_path: Path):
        """重排集数变少：被移除集号的派生文件清理，账本不留旧条目。"""
        project_dir = _planned_three(tmp_path)
        fake = _FakeTextGenerator([_plan_response([{"title": "合并集", "hook": "甲", "end_anchor": "卷入漩涡之中。"}])])

        await EpisodePlanner(project_dir, generator=fake).replan(2, "后两集合成一集")

        project = _load_project(project_dir)
        assert [e["episode"] for e in project["episodes"]] == [1, 2]
        a = _end_of(ANCHOR_EP1)
        assert project["episodes"][1]["source_range"] == {
            "source_file": "source/novel.txt",
            "start": a,
            "end": len(SOURCE),
        }
        assert not (project_dir / "source" / "episode_3.txt").exists()

    async def test_replan_writes_global_volume_preference_back_to_settings(self, tmp_path: Path):
        """全局性意见（每集体量）回写项目设置，后续批次自动继承。"""
        project_dir = _planned_three(tmp_path)
        response = json.dumps(
            {
                "episodes": [
                    {"title": "甲", "hook": "甲", "end_anchor": "卷入漩涡之中。"},
                ],
                "episode_target_units": 800,
            },
            ensure_ascii=False,
        )
        fake = _FakeTextGenerator([response])

        result = await EpisodePlanner(project_dir, generator=fake).replan(2, "整体每集再短一点，800字左右")

        assert _load_project(project_dir)["episode_target_units"] == 800
        assert result.settings_updated == {"episode_target_units": 800}

    async def test_replan_rejects_unanchored_in_affected_range(self, tmp_path: Path):
        """重排范围波及 unanchored 集：拒绝执行（失锚集锁定，不参与重排）。"""
        a = _end_of(ANCHOR_EP1)
        project_dir = _write_project(
            tmp_path,
            episodes=[
                _entry(1, 0, a),
                {
                    "episode": 2,
                    "title": "失锚集",
                    "script_file": "scripts/episode_2.json",
                    "source_range": None,
                    "ledger_status": "unanchored",
                },
            ],
            planning_cursor={"source_file": "source/novel.txt", "offset": a},
        )
        fake = _FakeTextGenerator([])

        with pytest.raises(EpisodePlanningError, match="unanchored|失锚"):
            await EpisodePlanner(project_dir, generator=fake).replan(1, "重排")

    async def test_replan_confirmed_rejects_newly_consumed_during_execution(self, tmp_path: Path):
        """确认后的重排执行期间又有集被消费：旧确认不覆盖新消费集，提交中止。"""
        project_dir = _planned_three(tmp_path, statuses=("planned", "consumed", "planned"))

        class _ConsumingGenerator(_FakeTextGenerator):
            async def generate(self, request, project_name=None):
                # 模拟模型调用期间第 3 集被并发消费
                project = _load_project(project_dir)
                for entry in project["episodes"]:
                    if entry["episode"] == 3:
                        entry["ledger_status"] = "consumed"
                (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
                return await super().generate(request, project_name)

        fake = _ConsumingGenerator(
            [_plan_response([{"title": "合并集", "hook": "甲", "end_anchor": "卷入漩涡之中。"}])]
        )

        with pytest.raises(PlanningConflictError, match="新的已消费集"):
            await EpisodePlanner(project_dir, generator=fake).replan(2, "重排", confirm_consumed=True)

        eps = {e["episode"]: e for e in _load_project(project_dir)["episodes"]}
        assert eps[2]["title"] == "第2集"  # 重排未生效
        assert eps[3]["ledger_status"] == "consumed"

    async def test_replan_rejects_concurrent_boundary_change_within_same_range(self, tmp_path: Path):
        """执行期间同闭合范围内的切分边界被并发改动：合并范围相同也必须判冲突，不得静默覆盖。"""
        project_dir = _planned_three(tmp_path)
        b = _end_of(ANCHOR_EP2)

        class _BoundaryShiftingGenerator(_FakeTextGenerator):
            async def generate(self, request, project_name=None):
                # 模拟模型调用期间第 2/3 集分界被并发挪动（闭合范围不变）
                project = _load_project(project_dir)
                for entry in project["episodes"]:
                    if entry["episode"] == 2:
                        entry["source_range"]["end"] = b + 2
                    if entry["episode"] == 3:
                        entry["source_range"]["start"] = b + 2
                (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
                return await super().generate(request, project_name)

        fake = _BoundaryShiftingGenerator(
            [_plan_response([{"title": "合并集", "hook": "甲", "end_anchor": "卷入漩涡之中。"}])]
        )

        with pytest.raises(PlanningConflictError, match="并发"):
            await EpisodePlanner(project_dir, generator=fake).replan(2, "重排")

        eps = {e["episode"]: e for e in _load_project(project_dir)["episodes"]}
        assert eps[2]["title"] == "第2集"  # 重排未生效，并发写入的边界保留
        assert eps[2]["source_range"]["end"] == b + 2

    async def test_replan_rejects_unknown_from_episode(self, tmp_path: Path):
        project_dir = _planned_three(tmp_path)

        with pytest.raises(EpisodePlanningError, match="from_episode"):
            await EpisodePlanner(project_dir, generator=_FakeTextGenerator([])).replan(9, "重排")

    async def test_replan_rejects_discontinuous_ranges_in_same_source_file(self, tmp_path: Path):
        """同源文件内相邻条目断档：静默合并会把范围外的原文一并重切，必须拒绝重排。"""
        a = _end_of(ANCHOR_EP1)
        project_dir = _write_project(
            tmp_path,
            episodes=[_entry(1, 0, a), _entry(2, a + 3, len(SOURCE))],  # [a, a+3) 断档
            planning_cursor={"source_file": "source/novel.txt", "offset": len(SOURCE)},
        )
        fake = _FakeTextGenerator([])

        with pytest.raises(EpisodePlanningError, match="不连续"):
            await EpisodePlanner(project_dir, generator=fake).replan(1, "重排")

        assert fake.requests == []

    async def test_replan_rejects_inverted_range_entry(self, tmp_path: Path):
        """单集反向范围（start >= end）是脏数据：即使能被相邻集合并吸收也必须拒绝重排。"""
        a = _end_of(ANCHOR_EP1)
        project_dir = _write_project(
            tmp_path,
            episodes=[_entry(1, 0, a), _entry(2, a, a - 3), _entry(3, a - 3, len(SOURCE))],
            planning_cursor={"source_file": "source/novel.txt", "offset": len(SOURCE)},
        )
        fake = _FakeTextGenerator([])

        with pytest.raises(EpisodePlanningError, match="范围无效"):
            await EpisodePlanner(project_dir, generator=fake).replan(1, "重排")

        assert fake.requests == []

    async def test_replan_across_source_files_recuts_each_file_slice(self, tmp_path: Path):
        """跨源文件重排：按文件拆 slice 独立重切，集号跨文件连续，文件边界即集边界，cursor 不动。"""
        project_dir = _planned_two_files(tmp_path)
        a = _end_of(ANCHOR_EP1)
        new_anchor = "踏上去往青云城的路。"
        fake = _FakeTextGenerator(
            [
                # 第一段（novel.txt 内 [a, 文末)）：重切为 2 集
                _plan_response(
                    [
                        {"title": "辞别下山", "hook": "青云城里有什么", "end_anchor": new_anchor},
                        {"title": "城门风波", "hook": "少女是谁", "end_anchor": "卷入漩涡之中。"},
                    ]
                ),
                # 第二段（novel2.txt 全文）：重切为 1 集
                _plan_response([{"title": "上界风云", "hook": "兽潮来袭", "end_anchor": "途中遭遇兽潮。"}]),
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).replan(2, "第2集在下山处收尾，第二部合成一集")

        # 每段一次独立调用：窗口只含本文件 slice 的原文
        assert len(fake.requests) == 2
        assert "第三章" in fake.requests[0].prompt
        assert "上界风云" not in fake.requests[0].prompt
        assert "第二部" in fake.requests[1].prompt
        assert "第三章" not in fake.requests[1].prompt
        # 后一段的已定上下文衔接前一段刚规划出的集
        assert "城门风波" in fake.requests[1].prompt

        project = _load_project(project_dir)
        eps = {e["episode"]: e for e in project["episodes"]}
        assert sorted(eps) == [1, 2, 3, 4]
        assert eps[1]["title"] == "第1集"  # 未受影响
        new_mid = SOURCE.index(new_anchor) + len(new_anchor)
        assert eps[2]["source_range"] == {"source_file": "source/novel.txt", "start": a, "end": new_mid}
        # 文件边界贴齐为集边界：前一文件最后一集收在文末，后一文件第一集从 0 起
        assert eps[3]["source_range"] == {"source_file": "source/novel.txt", "start": new_mid, "end": len(SOURCE)}
        assert eps[4]["source_range"] == {"source_file": "source/novel2.txt", "start": 0, "end": len(SOURCE2)}
        # cursor 不变（重排范围闭合）
        assert project["planning_cursor"] == {"source_file": "source/novel2.txt", "offset": len(SOURCE2)}
        # 派生文件按新账本重写
        assert (project_dir / "source" / "episode_2.txt").read_text(encoding="utf-8") == SOURCE[a:new_mid]
        assert (project_dir / "source" / "episode_3.txt").read_text(encoding="utf-8") == SOURCE[new_mid:]
        assert (project_dir / "source" / "episode_4.txt").read_text(encoding="utf-8") == SOURCE2
        assert [s.episode for s in result.episodes] == [2, 3, 4]
        assert result.stale_episodes == []

    async def test_replan_across_source_files_each_slice_must_close(self, tmp_path: Path):
        """跨文件重排每段各自闭合：非末段新布局没盖到本文件片段末尾时同样打回重试。"""
        project_dir = _planned_two_files(tmp_path)
        fake = _FakeTextGenerator(
            [
                _plan_response([{"title": "甲", "hook": "甲", "end_anchor": "踏上去往青云城的路。"}]),  # 第一段留尾巴
                _plan_response([{"title": "乙", "hook": "乙", "end_anchor": "卷入漩涡之中。"}]),
                _plan_response([{"title": "丙", "hook": "丙", "end_anchor": "途中遭遇兽潮。"}]),
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).replan(2, "重排")

        assert len(fake.requests) == 3
        assert "不能留尾巴" in fake.requests[1].prompt  # 第一段重试，失败原因指向闭合
        assert "不能留尾巴" not in fake.requests[0].prompt
        eps = {e["episode"]: e for e in _load_project(project_dir)["episodes"]}
        assert eps[2]["source_range"] == {
            "source_file": "source/novel.txt",
            "start": _end_of(ANCHOR_EP1),
            "end": len(SOURCE),
        }
        assert eps[3]["source_range"] == {"source_file": "source/novel2.txt", "start": 0, "end": len(SOURCE2)}
        assert [s.episode for s in result.episodes] == [2, 3]

    async def test_replan_across_source_files_consumed_confirmation_and_stale(self, tmp_path: Path):
        """跨文件重排的已消费集不引入特例：后一文件的已消费集同样先确认、确认后标 stale。"""
        project_dir = _planned_two_files(tmp_path, statuses=("planned", "planned", "planned", "consumed"))
        before = (project_dir / "project.json").read_text(encoding="utf-8")
        responses = [
            _plan_response([{"title": "甲", "hook": "甲", "end_anchor": "卷入漩涡之中。"}]),
            _plan_response(
                [
                    {"title": "乙", "hook": "乙", "end_anchor": ANCHOR2_MID},
                    {"title": "丙", "hook": "丙", "end_anchor": "途中遭遇兽潮。"},
                ]
            ),
        ]
        planner = EpisodePlanner(project_dir, generator=_FakeTextGenerator(responses))

        unconfirmed = await planner.replan(2, "重排")

        assert isinstance(unconfirmed, ReplanConfirmationRequired)
        assert unconfirmed.consumed_episodes == [4]
        assert (project_dir / "project.json").read_text(encoding="utf-8") == before  # 未确认零变更

        result = await EpisodePlanner(project_dir, generator=_FakeTextGenerator(responses)).replan(
            2, "重排", confirm_consumed=True
        )

        eps = {e["episode"]: e for e in _load_project(project_dir)["episodes"]}
        assert eps[2]["ledger_status"] == "planned"
        assert eps[3]["ledger_status"] == "planned"
        assert eps[4]["ledger_status"] == "stale"
        assert result.stale_episodes == [4]

    async def test_replan_across_source_files_renumbers_continuously_when_count_grows(self, tmp_path: Path):
        """跨文件重排前段集数增多：后段集号顺延不冲突，新增集号派生文件写出。"""
        project_dir = _planned_two_files(tmp_path)
        fake = _FakeTextGenerator(
            [
                _plan_response(
                    [
                        {"title": "甲", "hook": "甲", "end_anchor": "踏上去往青云城的路。"},
                        {"title": "乙", "hook": "乙", "end_anchor": ANCHOR_EP2},
                        {"title": "丙", "hook": "丙", "end_anchor": "卷入漩涡之中。"},
                    ]
                ),
                _plan_response([{"title": "丁", "hook": "丁", "end_anchor": "途中遭遇兽潮。"}]),
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).replan(2, "前面切细一点")

        project = _load_project(project_dir)
        assert [e["episode"] for e in project["episodes"]] == [1, 2, 3, 4, 5]
        eps = {e["episode"]: e for e in project["episodes"]}
        assert eps[4]["source_range"]["source_file"] == "source/novel.txt"
        assert eps[5]["source_range"] == {"source_file": "source/novel2.txt", "start": 0, "end": len(SOURCE2)}
        assert (project_dir / "source" / "episode_5.txt").read_text(encoding="utf-8") == SOURCE2
        assert [s.episode for s in result.episodes] == [2, 3, 4, 5]

    async def test_replan_across_source_files_shrinking_cleans_removed_files(self, tmp_path: Path):
        """跨文件重排总集数变少：被移除集号的派生文件清理，账本不留旧条目。"""
        project_dir = _planned_two_files(tmp_path)
        fake = _FakeTextGenerator(
            [
                _plan_response([{"title": "甲", "hook": "甲", "end_anchor": "卷入漩涡之中。"}]),
                _plan_response([{"title": "乙", "hook": "乙", "end_anchor": "途中遭遇兽潮。"}]),
            ]
        )

        await EpisodePlanner(project_dir, generator=fake).replan(2, "两部各合成一集")

        project = _load_project(project_dir)
        assert [e["episode"] for e in project["episodes"]] == [1, 2, 3]
        assert not (project_dir / "source" / "episode_4.txt").exists()

    async def test_replan_across_source_files_writes_back_global_preference_from_any_slice(self, tmp_path: Path):
        """跨文件重排的全局性意见不挑段：任一段结构化返回每集体量都回写项目设置。"""
        project_dir = _planned_two_files(tmp_path)
        fake = _FakeTextGenerator(
            [
                _plan_response([{"title": "甲", "hook": "甲", "end_anchor": "卷入漩涡之中。"}]),
                json.dumps(
                    {
                        "episodes": [{"title": "乙", "hook": "乙", "end_anchor": "途中遭遇兽潮。"}],
                        "episode_target_units": 800,
                    },
                    ensure_ascii=False,
                ),
            ]
        )

        result = await EpisodePlanner(project_dir, generator=fake).replan(2, "整体每集再短一点，800字左右")

        assert _load_project(project_dir)["episode_target_units"] == 800
        assert result.settings_updated == {"episode_target_units": 800}

    async def test_replan_rejects_interleaved_source_files_without_llm_call(self, tmp_path: Path):
        """同一源文件在重排范围内非连续出现（集号与源文件顺序错乱）：fail-fast，不调模型不动账本。"""
        a = _end_of(ANCHOR_EP1)
        project_dir = _write_project(
            tmp_path,
            episodes=[
                _entry(1, 0, a),
                _entry(2, 0, _end_of(ANCHOR2_MID, SOURCE2), source_file="source/novel2.txt"),
                _entry(3, a, len(SOURCE)),  # novel.txt 再次出现
            ],
            planning_cursor={"source_file": "source/novel.txt", "offset": len(SOURCE)},
        )
        (project_dir / "source" / "novel2.txt").write_text(SOURCE2, encoding="utf-8")
        before = (project_dir / "project.json").read_text(encoding="utf-8")
        fake = _FakeTextGenerator([])

        with pytest.raises(EpisodePlanningError, match="非连续"):
            await EpisodePlanner(project_dir, generator=fake).replan(1, "重排")

        assert fake.requests == []
        assert (project_dir / "project.json").read_text(encoding="utf-8") == before

    async def test_replan_rejects_invalid_slice_range_without_llm_call(self, tmp_path: Path):
        """账本片段范围无效（start >= end）：调模型之前直接报错，不烧重试。"""
        a = _end_of(ANCHOR_EP1)
        project_dir = _write_project(
            tmp_path,
            episodes=[_entry(1, 0, a), _entry(2, a, a)],  # 第 2 集零宽范围
            planning_cursor={"source_file": "source/novel.txt", "offset": a},
        )
        fake = _FakeTextGenerator([])

        with pytest.raises(EpisodePlanningError, match="范围无效"):
            await EpisodePlanner(project_dir, generator=fake).replan(2, "重排")

        assert fake.requests == []

    async def test_replan_retries_until_layout_covers_span_end(self, tmp_path: Path):
        """重排范围闭合：新布局没盖到范围末尾时打回重试。"""
        project_dir = _planned_three(tmp_path)
        fake = _FakeTextGenerator(
            [
                _plan_response([{"title": "甲", "hook": "甲", "end_anchor": "踏上去往青云城的路。"}]),  # 留尾巴
                _plan_response([{"title": "乙", "hook": "乙", "end_anchor": "卷入漩涡之中。"}]),
            ]
        )

        await EpisodePlanner(project_dir, generator=fake).replan(2, "重排")

        assert len(fake.requests) == 2
        assert "不能留尾巴" in fake.requests[1].prompt  # 失败原因专属文案，静态规则部分不含
        assert "不能留尾巴" not in fake.requests[0].prompt


class TestReconcileFailFast:
    """派生文件对账的错误分支必须中止提交：提交成功 ⇒ 对账完成。"""

    @staticmethod
    def _corrupt_entry_1(project_dir: Path, mutate) -> str:
        """改写第 1 集账本条目制造脏数据，返回改写后的 project.json 原文。"""
        project = _load_project(project_dir)
        mutate(project["episodes"][0])
        (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
        return (project_dir / "project.json").read_text(encoding="utf-8")

    async def test_commit_aborts_when_anchored_entry_has_invalid_source_range(self, tmp_path: Path):
        """账本中锚定集的原文范围类型非法：提交中止，账本与派生文件零变更。"""
        project_dir = _planned_three(tmp_path)
        before = self._corrupt_entry_1(project_dir, lambda e: e["source_range"].update(start="0"))
        fake = _FakeTextGenerator([_plan_response([{"title": "合并集", "hook": "甲", "end_anchor": "卷入漩涡之中。"}])])

        with pytest.raises(EpisodePlanningError, match="对账"):
            await EpisodePlanner(project_dir, generator=fake).replan(2, "后两集合成一集")

        assert (project_dir / "project.json").read_text(encoding="utf-8") == before
        assert (project_dir / "source" / "episode_3.txt").exists()  # 旧文件未被清理

    async def test_commit_aborts_when_source_range_out_of_bounds(self, tmp_path: Path):
        """账本中锚定集的原文范围越界（end 超源文长度）：提交中止，零变更。"""
        project_dir = _planned_three(tmp_path)
        before = self._corrupt_entry_1(project_dir, lambda e: e["source_range"].update(end=len(SOURCE) + 999))
        fake = _FakeTextGenerator([_plan_response([{"title": "合并集", "hook": "甲", "end_anchor": "卷入漩涡之中。"}])])

        with pytest.raises(EpisodePlanningError, match="越界"):
            await EpisodePlanner(project_dir, generator=fake).replan(2, "后两集合成一集")

        assert (project_dir / "project.json").read_text(encoding="utf-8") == before

    async def test_commit_aborts_when_entry_source_file_missing(self, tmp_path: Path):
        """账本引用的源文件缺失：派生文件重写失败中止提交，不留半成品账本。"""
        project_dir = _planned_three(tmp_path)
        before = self._corrupt_entry_1(project_dir, lambda e: e["source_range"].update(source_file="source/gone.txt"))
        fake = _FakeTextGenerator([_plan_response([{"title": "合并集", "hook": "甲", "end_anchor": "卷入漩涡之中。"}])])

        with pytest.raises(EpisodePlanningError, match="重写失败"):
            await EpisodePlanner(project_dir, generator=fake).replan(2, "后两集合成一集")

        assert (project_dir / "project.json").read_text(encoding="utf-8") == before
        assert (project_dir / "source" / "episode_3.txt").exists()

    async def test_commit_validation_failure_leaves_derived_files_untouched(self, tmp_path: Path):
        """校验类失败中止提交时不得留下部分重写的派生文件：全部校验通过后才统一落盘。"""
        project_dir = _planned_three(tmp_path)
        sentinel = "哨兵旧内容"
        (project_dir / "source" / "episode_1.txt").write_text(sentinel, encoding="utf-8")
        project = _load_project(project_dir)
        project["episodes"][1]["source_range"]["end"] = len(SOURCE) + 999  # 第 2 集越界，对账时居第 1 集之后
        (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
        fake = _FakeTextGenerator([_plan_response([{"title": "丙", "hook": "丙", "end_anchor": "卷入漩涡之中。"}])])

        with pytest.raises(EpisodePlanningError, match="越界"):
            await EpisodePlanner(project_dir, generator=fake).replan(3, "重排")

        # 排序在前的第 1 集合法，但因第 2 集校验失败，其派生文件不得被提前重写
        assert (project_dir / "source" / "episode_1.txt").read_text(encoding="utf-8") == sentinel

    async def test_commit_aborts_when_derived_episode_file_is_symlink(self, tmp_path: Path):
        """派生集文件是符号链接：写入会跟随链接落到项目外，必须中止提交。"""
        project_dir = _planned_three(tmp_path)
        outside = tmp_path / "outside.txt"
        outside.write_text("外部文件", encoding="utf-8")
        target = project_dir / "source" / "episode_2.txt"
        target.unlink()
        target.symlink_to(outside)
        before = (project_dir / "project.json").read_text(encoding="utf-8")
        fake = _FakeTextGenerator([_plan_response([{"title": "合并集", "hook": "甲", "end_anchor": "卷入漩涡之中。"}])])

        with pytest.raises(EpisodePlanningError, match="符号链接"):
            await EpisodePlanner(project_dir, generator=fake).replan(2, "后两集合成一集")

        assert outside.read_text(encoding="utf-8") == "外部文件"  # 链接目标未被覆写
        assert (project_dir / "project.json").read_text(encoding="utf-8") == before
