"""crawler 전역 설정. .env에서 읽어온다 — 값을 스크립트가 추측하지 않는다."""

from dataclasses import dataclass

from dotenv import dotenv_values

RADII_KM = [1, 2, 3, 4]


@dataclass(frozen=True)
class Settings:
    kakao_api_key: str
    store_address: str
    store_display_name: str
    category_label: str


def load_settings(env_path: str = ".env") -> Settings:
    values = dotenv_values(env_path)
    missing = [
        k for k in ("KAKAO_REST_API_KEY", "STORE_ADDRESS", "STORE_DISPLAY_NAME", "CATEGORY_LABEL")
        if not values.get(k)
    ]
    if missing:
        raise RuntimeError(f".env에 다음 값이 없습니다: {', '.join(missing)}")
    return Settings(
        kakao_api_key=values["KAKAO_REST_API_KEY"],
        store_address=values["STORE_ADDRESS"],
        store_display_name=values["STORE_DISPLAY_NAME"],
        category_label=values["CATEGORY_LABEL"],
    )
