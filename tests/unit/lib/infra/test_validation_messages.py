"""校验结果与默认语言渲染：Agent 与 CLI 边界缺省中文，Web 边界按传入的 translator。"""

from arcreel_market_core.validation_messages import MessageJoin, MessageRef, ValidationMessage
from lib.i18n import _
from lib.infra.validation_messages import ValidationResult, default_translate


def _translator(locale: str):
    def translate(key: str, **kwargs: object) -> str:
        return _(key, locale=locale, **kwargs)

    return translate


class TestDefaultTranslate:
    def test_renders_in_chinese(self):
        message = ValidationMessage("val_missing_field", {"field": "title"})
        assert message.render(default_translate) == "缺少必填字段: title"

    def test_message_ref_param_follows_the_locale(self):
        message = ValidationMessage(
            "val_refs_unregistered",
            {
                "prefix": "E01S01",
                "field": "characters",
                "asset_type": MessageRef("asset_type_character"),
                "names": "Hero",
            },
        )
        assert "角色" in message.render(default_translate)
        assert "角色" not in message.render(_translator("en"))

    def test_message_join_translates_each_fragment(self):
        message = ValidationMessage(
            "val_missing_field",
            {"field": MessageJoin(("title", MessageRef("asset_type_character")), separator=" / ")},
        )
        assert message.render(default_translate) == "缺少必填字段: title / 角色"


class TestValidationResult:
    def test_errors_property_renders_in_default_locale(self):
        result = ValidationResult(valid=False, error_messages=[ValidationMessage("val_missing_field", {"field": "id"})])
        assert result.errors == ["缺少必填字段: id"]

    def test_render_warnings_honours_translator(self):
        result = ValidationResult(
            valid=True, warning_messages=[ValidationMessage("val_missing_field", {"field": "id"})]
        )
        assert result.render_warnings(_translator("en")) == ["Missing required field: id"]
