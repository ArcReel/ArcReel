"""PROTOTYPE — 用 JSON Schema 2020-12 校验三份起步模板与若干反例（#2123）。

用法：uv run python lib/custom_provider/prototype_declarative_endpoint/validate.py
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

HERE = Path(__file__).parent
SCHEMA = json.loads((HERE / "schema.json").read_text())
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA)


def errors(doc: object) -> list[str]:
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '$'}: {e.message[:140]}" for e in VALIDATOR.iter_errors(doc)
    ]


def main() -> int:
    failed = False
    templates = sorted((HERE / "templates").glob("*.json"))
    for path in templates:
        errs = errors(json.loads(path.read_text()))
        mark = "OK " if not errs else "FAIL"
        if errs:
            failed = True
        print(f"[{mark}] {path.name}")
        for e in errs:
            print(f"       {e}")

    base = json.loads((HERE / "templates" / "generic-submit-poll.json").read_text())

    def variant(mutate) -> dict:
        d = copy.deepcopy(base)
        mutate(d)
        return d

    negatives: list[tuple[str, dict]] = [
        ("kind 未知", variant(lambda d: d.__setitem__("kind", "python"))),
        ("schema_version 非 semver", variant(lambda d: d.__setitem__("schema_version", "1.0"))),
        ("meta 缺 author", variant(lambda d: d["meta"].pop("author"))),
        ("meta.media_type 已移除", variant(lambda d: d["meta"].__setitem__("media_type", "video"))),
        ("inputs.mime_types 已移除", variant(lambda d: d["inputs"]["first_frame"].__setitem__("mime_types", ["image/png"]))),
        ("submit.query 已移除", variant(lambda d: d["submit"].__setitem__("query", {"k": "v"}))),
        ("poll.interval_seconds 已移除", variant(lambda d: d["poll"].__setitem__("interval_seconds", 5))),
        ("capabilities.first_frame 已移除（由 inputs 推导）", variant(lambda d: d["capabilities"].__setitem__("first_frame", True))),
        ("inputs.encoding=url（预留）", variant(lambda d: d["inputs"]["first_frame"].__setitem__("encoding", "url"))),
        ("submit.extract 缺 task_id", variant(lambda d: d["submit"]["extract"].pop("task_id"))),
        ("poll.extract 缺 video_url", variant(lambda d: d["poll"]["extract"].pop("video_url"))),
        ("JSONPath 含 .. 递归", variant(lambda d: d["poll"]["extract"].__setitem__("video_url", ["$..url"]))),
        ("JSONPath 无 $ 前缀", variant(lambda d: d["poll"]["extract"].__setitem__("video_url", ["video_url"]))),
        ("status_map 映射到 expired（不由声明式产生）", variant(lambda d: d["status_map"].__setitem__("gone", "expired"))),
        ("poll.expired_status_codes 已移除", variant(lambda d: d["poll"].__setitem__("expired_status_codes", [404]))),
        ("enum_maps 对 prompt 做映射", variant(lambda d: d["enum_maps"].__setitem__("prompt", {"a": "b"}))),
        (
            "$each 同时给 item 与 key",
            variant(
                lambda d: d["submit"]["body"].__setitem__(
                    "refs", [{"$each": {"in": "inputs.x", "as": "r", "item": "{{ r }}", "key": "k"}}]
                )
            ),
        ),
        (
            "extract.then 未开 json_decode",
            variant(lambda d: d["poll"]["extract"].__setitem__("video_url", {"paths": ["$.a"], "then": ["$.b"]})),
        ),
        ("capabilities.audio_track 非法枚举", variant(lambda d: d["capabilities"].__setitem__("audio_track", "never"))),
        ("顶层多余键", variant(lambda d: d.__setitem__("api_key", "sk-xxx"))),
        ("auth.headers 值非字符串", variant(lambda d: d["auth"]["headers"].__setitem__("X-Num", 1))),
        (
            "extract.source 已移除",
            variant(lambda d: d["poll"]["extract"].__setitem__("status", {"paths": ["$.x"], "source": "headers"})),
        ),
        ("poll.success_status_codes 已移除", variant(lambda d: d["poll"].__setitem__("success_status_codes", [200]))),
        ("poll.retry_status_codes 已移除", variant(lambda d: d["poll"].__setitem__("retry_status_codes", [503]))),
    ]
    print()
    for label, doc in negatives:
        errs = errors(doc)
        mark = "REJ" if errs else "FAIL(应拒绝)"
        if not errs:
            failed = True
        print(f"[{mark}] 反例：{label}")
        for e in errs[:2]:
            print(f"       {e}")

    positives: list[tuple[str, dict]] = [
        (
            "extract 对象形式 + json_decode/then",
            variant(
                lambda d: d["poll"]["extract"].__setitem__(
                    "video_url", {"paths": ["$.data.resultJson"], "json_decode": True, "then": ["$.resultUrls[0]"]}
                )
            ),
        ),
        (
            "$each 数组位置",
            variant(
                lambda d: d["submit"]["body"].__setitem__(
                    "refs", [{"$each": {"in": "inputs.refs", "as": "r", "item": {"image": "{{ r }}"}}}]
                )
            ),
        ),
        (
            "$each 对象位置",
            variant(
                lambda d: d["submit"]["body"].__setitem__(
                    "$each", {"in": "inputs.refs", "as": "r", "key": "image_{{ index }}", "value": "{{ r }}"}
                )
            ),
        ),
        (
            "过滤器路径",
            variant(lambda d: d["poll"]["extract"].__setitem__("video_url", ["$.data[?@.fileType == 'mp4'].fileUrl"])),
        ),
        ("无鉴权端点", variant(lambda d: d.__setitem__("auth", {}))),
        ("capabilities 整节省略", variant(lambda d: d.pop("capabilities"))),
        ("meta 只有 name / author / version", variant(lambda d: (d["meta"].pop("hints"), d["meta"].pop("description")))),
    ]
    print()
    for label, doc in positives:
        errs = errors(doc)
        mark = "OK " if not errs else "FAIL"
        if errs:
            failed = True
        print(f"[{mark}] 正例：{label}")
        for e in errs[:3]:
            print(f"       {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
