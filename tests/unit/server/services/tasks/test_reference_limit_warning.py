import pytest

from server.services.tasks.reference_video_tasks import _reference_limit_warning


@pytest.mark.parametrize("model", ["sora-2", "sora-2-pro", "acme-video-1", "Whatever"])
def test_single_ref_limit_warning_is_independent_of_model_name(model):
    warning = _reference_limit_warning(provider_id="openai", model=model, count=3, max_refs=1)
    assert warning == {
        "key": "ref_single_ref_only",
        "params": {"provider": "openai", "model": model},
    }


def test_multi_ref_limit_uses_generic_warning():
    warning = _reference_limit_warning(provider_id="p", model="sora-2", count=5, max_refs=3)
    assert warning["key"] == "ref_too_many_images"
    assert warning["params"]["max_count"] == 3
