"""结构化校验消息的渲染语义：默认语言、指定 translator、嵌套翻译键、literal 通道。"""

import pytest

from lib.i18n import _
from lib.validation_messages import MessageRef, ValidationMessage, ValidationResult

pytestmark = pytest.mark.unit


def _translator(locale: str):
    def translate(key: str, **kwargs: object) -> str:
        return _(key, locale=locale, **kwargs)

    return translate


class TestValidationMessage:
    def test_render_defaults_to_chinese(self):
        message = ValidationMessage("val_missing_field", {"field": "title"})
        assert message.render() == "缺少必填字段: title"

    def test_render_uses_supplied_translator(self):
        message = ValidationMessage("val_missing_field", {"field": "title"})
        assert message.render(_translator("en")) == "Missing required field: title"

    def test_message_ref_param_is_translated_before_substitution(self):
        message = ValidationMessage(
            "val_refs_unregistered",
            {
                "prefix": "E01S01",
                "field": "characters",
                "asset_type": MessageRef("asset_type_character"),
                "names": "Hero",
            },
        )
        assert "角色" in message.render()
        assert "角色" not in message.render(_translator("en"))

    def test_literal_channel_passes_text_through_unchanged(self):
        message = ValidationMessage.literal("pydantic: field required")
        assert message.render() == "pydantic: field required"
        assert message.render(_translator("vi")) == "pydantic: field required"


class TestValidationResult:
    def test_errors_property_renders_in_default_locale(self):
        result = ValidationResult(valid=False, error_messages=[ValidationMessage("val_missing_field", {"field": "id"})])
        assert result.errors == ["缺少必填字段: id"]

    def test_render_warnings_honours_translator(self):
        result = ValidationResult(
            valid=True, warning_messages=[ValidationMessage("val_missing_field", {"field": "id"})]
        )
        assert result.render_warnings(_translator("en")) == ["Missing required field: id"]
