"""Translation must not change persisted style values produced by historical migrations."""

import pytest

from lib.project.project_migrations.v13_to_v14_legacy_style_values import migrate_project_dict


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Photographic", "真人电视剧风格，精品短剧画风，大师级构图"),
        (
            "Anime",
            "商业动画画风，柔和光影效果，轻柔的赛璐珞上色，柔和的漫射光线，清晰干净的细轮廓线条，参考京都动画作品，参考石立太一动画作品，2d动画",
        ),
        ("3D Animation", "3D、游戏CG，影视级、虚幻引擎渲染"),
    ],
)
def test_legacy_label_keeps_the_original_persisted_style(label: str, expected: str) -> None:
    assert migrate_project_dict({"schema_version": 13, "style": label})["style"] == expected
