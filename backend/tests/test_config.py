from pathlib import Path

from pydantic import SecretStr

from app.core.config import Settings, get_settings


def test_functional_test_process_forces_mock_provider() -> None:
    settings = get_settings()

    assert settings.model_provider == "mock"
    assert settings.model_name == "mock-commerce-agent"


def test_settings_reads_raw_model_api_key_file(tmp_path: Path) -> None:
    key_file = tmp_path / ".env.ds"
    key_file.write_text("test-deepseek-key\n", encoding="utf-8")

    key = Settings(model_api_key=None, model_api_key_file=str(key_file)).resolved_model_api_key()

    assert key is not None
    assert key.get_secret_value() == "test-deepseek-key"


def test_settings_reads_assignment_style_model_api_key_file(tmp_path: Path) -> None:
    key_file = tmp_path / ".env.ds"
    key_file.write_text("MODEL_API_KEY=test-deepseek-key\n", encoding="utf-8")

    key = Settings(model_api_key=None, model_api_key_file=str(key_file)).resolved_model_api_key()

    assert key is not None
    assert key.get_secret_value() == "test-deepseek-key"


def test_empty_direct_key_falls_back_to_secret_file(tmp_path: Path) -> None:
    key_file = tmp_path / ".env.ds"
    key_file.write_text("test-deepseek-key\n", encoding="utf-8")

    key = Settings(
        model_api_key=SecretStr(""), model_api_key_file=str(key_file)
    ).resolved_model_api_key()

    assert key is not None
    assert key.get_secret_value() == "test-deepseek-key"
