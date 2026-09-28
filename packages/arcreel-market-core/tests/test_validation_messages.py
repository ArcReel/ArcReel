"""结构化校验消息的渲染语义：指定 translator、嵌套翻译键、literal 通道与无目录渲染。"""

from arcreel_market_core.validation_messages import (
    LITERAL_KEY,
    MessageJoin,
    MessageRef,
    ValidationMessage,
    code_translator,
)

_CATALOG = {
    "missing_field": "missing {field}",
    "wrapped": "wrapped: {detail}",
    "asset_character": "character",
    "asset_scene": "scene",
    LITERAL_KEY: "{text}",
}


def _translate(key: str, **params: object) -> str:
    return _CATALOG[key].format(**params)


class TestValidationMessage:
    def test_render_uses_supplied_translator(self):
        assert ValidationMessage("missing_field", {"field": "title"}).render(_translate) == "missing title"

    def test_message_ref_param_is_translated_before_substitution(self):
        message = ValidationMessage("missing_field", {"field": MessageRef("asset_character")})

        assert message.render(_translate) == "missing character"

    def test_validation_message_param_is_rendered_with_the_same_translator(self):
        message = ValidationMessage("wrapped", {"detail": ValidationMessage("missing_field", {"field": "id"})})

        assert message.render(_translate) == "wrapped: missing id"

    def test_message_join_renders_fragments_with_separator(self):
        message = ValidationMessage(
            "missing_field", {"field": MessageJoin(("title", MessageRef("asset_character")), separator=" / ")}
        )

        assert message.render(_translate) == "missing title / character"

    def test_message_join_nests_recursively(self):
        inner = MessageJoin(("novel.", MessageRef("asset_scene")), separator="")
        message = ValidationMessage("missing_field", {"field": MessageJoin((inner, "title"))})

        assert message.render(_translate) == "missing novel.scene; title"

    def test_literal_channel_passes_text_through_unchanged(self):
        message = ValidationMessage.literal("pydantic: field required")

        assert message.render(_translate) == "pydantic: field required"
        assert message.render(code_translator) == "pydantic: field required"


class TestCodeTranslator:
    def test_key_without_params_is_the_key_itself(self):
        assert ValidationMessage("val_ce_schema_forbidden_constraint").render(code_translator) == (
            "val_ce_schema_forbidden_constraint"
        )

    def test_params_follow_the_key_sorted_by_name(self):
        message = ValidationMessage("val_market_projection_mismatch", {"index_value": "2", "field": "version"})

        assert message.render(code_translator) == "val_market_projection_mismatch(field=version, index_value=2)"

    def test_nested_references_render_as_their_keys(self):
        message = ValidationMessage("val_ce_removed_field", {"reason": MessageRef("val_ce_removed_reason_mime_types")})

        assert message.render(code_translator) == "val_ce_removed_field(reason=val_ce_removed_reason_mime_types)"
