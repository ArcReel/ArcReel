"""失败文案按显示名目录把供应商与模型 ID 换成名称。"""

import string

import pytest

from lib.i18n import MESSAGES, SUPPORTED_LOCALES, _, render_message
from lib.i18n.display_names import DISPLAY_NAME_PARAMS, MODEL_PARAM
from tests.factories import make_display_names

PROVIDER_ID = "custom-7"
MODEL_ID = "relay-video-x"
PROVIDER_NAME = "我的中转站"
MODEL_NAME = "中转视频模型"

#: 名字像供应商或模型参数、但按原值显示的占位符，及其原因。
RAW_ID_PLACEHOLDERS = {
    "model_id": "自定义供应商设置页的模型行校验，主语是用户正在填写的模型 ID",
}


def _placeholders(template: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(template) if field}


@pytest.mark.parametrize("locale", SUPPORTED_LOCALES)
def test_every_provider_or_model_placeholder_renders_the_display_name(locale: str):
    names = make_display_names(
        locale, providers={PROVIDER_ID: PROVIDER_NAME}, models={(PROVIDER_ID, MODEL_ID): MODEL_NAME}
    )

    def translate(key: str, **params) -> str:
        return _(key, locale=locale, **params)

    checked = []
    for key, template in MESSAGES[locale].items():
        fields = _placeholders(template)
        id_fields = {field for field in fields if "provider" in field or "model" in field} - RAW_ID_PLACEHOLDERS.keys()
        if not id_fields:
            continue
        unregistered = id_fields - DISPLAY_NAME_PARAMS
        assert not unregistered, (
            f"{locale}/{key} 的占位符 {sorted(unregistered)} 不在 DISPLAY_NAME_PARAMS 里，渲染时不会换成显示名"
        )
        # 其余占位符填数值：兼容 {limit:g} 这类格式说明
        params: dict[str, object] = dict.fromkeys(fields, 1)
        params.update({field: MODEL_ID if field == MODEL_PARAM else PROVIDER_ID for field in id_fields})

        rendered = render_message(key, params, translate, names)

        assert PROVIDER_ID not in rendered, f"{locale}/{key}: {rendered}"
        assert MODEL_ID not in rendered, f"{locale}/{key}: {rendered}"
        if id_fields - {MODEL_PARAM}:
            assert PROVIDER_NAME in rendered, f"{locale}/{key}: {rendered}"
        if MODEL_PARAM in id_fields:
            assert MODEL_NAME in rendered, f"{locale}/{key}: {rendered}"
        checked.append(key)
    assert checked


def test_a_provider_missing_from_the_catalog_is_named_generically_and_its_model_keeps_the_id():
    names = make_display_names("en")

    params = names.apply({"provider": PROVIDER_ID, "model": MODEL_ID, "count": 3})

    assert params == {"provider": "Deleted provider", "model": MODEL_ID, "count": 3}
