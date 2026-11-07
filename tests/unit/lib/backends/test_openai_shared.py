"""create_openai_client factory behavior."""

from lib.backends.openai_shared import create_openai_client
from lib.config.url_utils import OFFICIAL_OPENAI_BASE_URL


class TestCreateOpenAIClientBaseURL:
    """DB solely supplies base_url; empty values force official endpoints, never silent environment routing overrides."""

    def test_empty_base_url_ignores_env_var(self, monkeypatch):
        # Empty AsyncOpenAI URLs read OPENAI_BASE_URL; factories must explicitly force official defaults.
        monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.com/v1")
        client = create_openai_client(api_key="x", base_url=None)
        assert str(client.base_url).rstrip("/") == OFFICIAL_OPENAI_BASE_URL.rstrip("/")

    def test_whitespace_base_url_ignores_env_var(self, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.com/v1")
        client = create_openai_client(api_key="x", base_url="   ")
        assert str(client.base_url).rstrip("/") == OFFICIAL_OPENAI_BASE_URL.rstrip("/")

    def test_explicit_base_url_preserved(self, monkeypatch):
        # Forward explicit URLs unchanged, unaffected by environment/official defaults.
        monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.com/v1")
        client = create_openai_client(api_key="x", base_url="https://vllm.internal:8000/v1")
        assert str(client.base_url).rstrip("/") == "https://vllm.internal:8000/v1"
