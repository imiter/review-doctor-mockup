"""crawler 전역 설정. .env에서 읽어온다 — 값을 스크립트가 추측하지 않는다."""

from dataclasses import dataclass

from dotenv import dotenv_values

# 가게 주소를 기점으로 하는 반경 구간(사용자 확정) — 매 구간마다 가게 주소부터
# 다시 계산한 랜덤 지점 1개씩을 뽑는다. 0km(가게 주소 자체)는 run_crawl.py에서
# 별도로 처리한다.
RING_KM_RANGES = [(1.5, 2.5), (2.5, 3.5)]


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
