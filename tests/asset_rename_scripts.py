from typing import Any


def narration_script(**overrides: Any) -> dict[str, Any]:
    segment = {
        "segment_id": "E1S01",
        "duration_seconds": 4,
        "novel_text": "原文",
        "characters_in_segment": ["角色A"],
        "scenes": ["场景A"],
        "props": ["道具A"],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
    }
    script = {
        "episode": 1,
        "title": "标题",
        "content_mode": "narration",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "segments": [segment],
    }
    script.update(overrides)
    return script


def drama_script() -> dict[str, Any]:
    scene = {
        "scene_id": "E1S01",
        "duration_seconds": 8,
        "scene_type": "剧情",
        "characters_in_scene": ["角色A"],
        "scenes": ["场景A"],
        "props": [],
        "utterances": [
            {"kind": "dialogue", "speaker": "角色A", "text": "台词"},
            {"kind": "voiceover", "speaker": None, "text": "旁白"},
        ],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
    }
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "drama",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "scenes": [scene],
    }


def ad_script() -> dict[str, Any]:
    shot = {
        "shot_id": "E1S01",
        "section": "hook",
        "duration_seconds": 5,
        "voiceover_text": "口播文案",
        "characters_in_shot": ["角色A"],
        "scenes": [],
        "props": [],
        "products_in_shot": ["商品A"],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {
            "action": "转身",
            "camera_motion": "Static",
            "ambiance_audio": "风声",
            "dialogue": [{"speaker": "角色A", "line": "广告词"}],
        },
    }
    return {"episode": 1, "title": "标题", "content_mode": "ad", "shots": [shot]}


def make_reference_script(episode: int = 1) -> dict[str, Any]:
    return {
        "episode": episode,
        "title": "标题",
        "content_mode": "narration",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "video_units": [
            {
                "unit_id": "E1U1",
                "text": "@[角色A] 走进 @[场景A]",
                "duration_seconds": 8,
            }
        ],
    }
