from app.core.config import Settings


def test_deepseek_environment_aliases_are_supported(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_APIKEY", "sk-test-only")

    settings = Settings(_env_file=None)

    assert settings.assistant_api_key == "sk-test-only"
    assert settings.assistant_model == "deepseek-flash"


def test_assistant_api_key_legacy_environment_name_is_supported(monkeypatch):
    monkeypatch.setenv("ASSISTANT_API_KEY", "sk-test-only")

    settings = Settings(_env_file=None)

    assert settings.assistant_api_key == "sk-test-only"
