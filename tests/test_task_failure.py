"""Unit tests for structured task-failure encoding/rendering."""

import pytest

from lib.i18n import _ as translate_message
from lib.task_failure import FAILURE_CODE_KEYS, encode_failure, render_failure


def _translator(locale: str):
    def translate(key: str, **kwargs):
        return translate_message(key, locale=locale, **kwargs)

    return translate


class TestEncodeFailure:
    def test_encode_code_only(self):
        assert encode_failure("restart_lost_image") == "[restart_lost_image]"

    def test_encode_with_params_is_sorted_json(self):
        encoded = encode_failure("provider_unsupported_media", provider_id="grok", media_type="image")
        assert encoded == '[provider_unsupported_media] {"media_type": "image", "provider_id": "grok"}'

    def test_encode_unknown_code_raises(self):
        with pytest.raises(KeyError):
            encode_failure("totally_unknown_code")

    def test_every_known_code_round_trips_through_render(self):
        # Each known code must encode and render to a non-empty, non-raw string.
        translate = _translator("en")
        for code in FAILURE_CODE_KEYS:
            encoded = encode_failure(code, provider_id="p", media_type="video", detail="boom")
            rendered = render_failure(encoded, translate)
            assert rendered
            assert not rendered.startswith("["), f"{code} rendered to raw code: {rendered}"


class TestRenderKnownCodes:
    def test_renders_per_locale(self):
        encoded = encode_failure("provider_unsupported_media", provider_id="grok", media_type="image")
        assert render_failure(encoded, _translator("zh")) == "供应商 grok 不支持 image 生成"
        assert render_failure(encoded, _translator("en")) == "Provider grok does not support image generation"
        vi = render_failure(encoded, _translator("vi"))
        assert "grok" in vi and "image" in vi
        # locales differ
        assert render_failure(encoded, _translator("zh")) != render_failure(encoded, _translator("en"))

    def test_renders_code_only(self):
        encoded = encode_failure("restart_lost_image")
        zh = render_failure(encoded, _translator("zh"))
        en = render_failure(encoded, _translator("en"))
        assert zh and en and zh != en
        assert "[" not in zh

    def test_detail_param_is_interpolated_untranslated(self):
        encoded = encode_failure("resume_expired_detail", detail="HTTP 404 job gone")
        zh = render_failure(encoded, _translator("zh"))
        en = render_failure(encoded, _translator("en"))
        assert "HTTP 404 job gone" in zh
        assert "HTTP 404 job gone" in en


class TestPassthrough:
    def test_none_and_empty(self):
        assert render_failure(None, _translator("en")) is None
        assert render_failure("", _translator("en")) == ""

    def test_raw_exception_text_passthrough(self):
        raw = "RuntimeError: provider returned 500"
        assert render_failure(raw, _translator("en")) == raw

    def test_legacy_chinese_row_passthrough(self):
        legacy = "供应商 grok 不支持 image 生成"
        assert render_failure(legacy, _translator("en")) == legacy

    def test_legacy_bracket_prefix_with_chinese_tail_passthrough(self):
        # Old format: [code] followed by free Chinese text (non-JSON tail).
        legacy = "[restart_lost] image 任务无法接续，需手动重试以避免重复计费"
        assert render_failure(legacy, _translator("en")) == legacy

    def test_unknown_bracket_code_passthrough(self):
        msg = '[some_future_code] {"x": 1}'
        assert render_failure(msg, _translator("en")) == msg

    def test_malformed_json_params_passthrough(self):
        msg = "[provider_unsupported_media] {not valid json"
        assert render_failure(msg, _translator("en")) == msg

    def test_non_object_json_params_passthrough(self):
        msg = "[provider_unsupported_media] [1, 2, 3]"
        assert render_failure(msg, _translator("en")) == msg

    def test_detail_with_braces_does_not_break_format(self):
        # str(exc) can contain literal braces; they must not be re-interpreted by .format.
        encoded = encode_failure("resume_unsupported_detail", detail="weird {placeholder} text")
        rendered = render_failure(encoded, _translator("en"))
        assert "weird {placeholder} text" in rendered
