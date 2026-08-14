import os

import pytest

from config import load_settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("KAKAO_REST_API_KEY", "STORE_ADDRESS", "STORE_DISPLAY_NAME", "CATEGORY_LABEL", "STORE_LAT", "STORE_LNG"):
        monkeypatch.delenv(key, raising=False)


def test_load_settings_reads_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "KAKAO_REST_API_KEY=file-key\n"
        "STORE_ADDRESS=서울시 노원구 당고개로 1\n"
        "STORE_DISPLAY_NAME=치킨대장 당고점\n"
        "CATEGORY_LABEL=치킨\n"
    )
    settings = load_settings(str(env_file))
    assert settings.kakao_api_key == "file-key"
    assert settings.store_display_name == "치킨대장 당고점"
    assert settings.store_lat is None
    assert settings.store_lng is None


def test_load_settings_prefers_process_env_over_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "KAKAO_REST_API_KEY=file-key\n"
        "STORE_ADDRESS=서울시 노원구 당고개로 1\n"
        "STORE_DISPLAY_NAME=파일 값\n"
        "CATEGORY_LABEL=치킨\n"
    )
    monkeypatch.setenv("STORE_DISPLAY_NAME", "프로세스 환경변수 값")
    settings = load_settings(str(env_file))
    assert settings.store_display_name == "프로세스 환경변수 값"  # 파일 값이 아니라 환경변수가 이겨야 함


def test_load_settings_picks_up_store_lat_lng_from_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "KAKAO_REST_API_KEY=file-key\n"
        "STORE_ADDRESS=서울시 노원구 당고개로 1\n"
        "STORE_DISPLAY_NAME=치킨대장 당고점\n"
        "CATEGORY_LABEL=치킨\n"
    )
    monkeypatch.setenv("STORE_LAT", "37.667646")
    monkeypatch.setenv("STORE_LNG", "127.079584")
    settings = load_settings(str(env_file))
    assert settings.store_lat == 37.667646
    assert settings.store_lng == 127.079584


def test_load_settings_missing_required_value_raises(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("KAKAO_REST_API_KEY=file-key\n")
    with pytest.raises(RuntimeError, match=r"\.env에 다음 값이 없습니다"):
        load_settings(str(env_file))
