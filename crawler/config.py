"""crawler 전역 설정. 프로세스 환경변수를 우선하고, 없는 값만 .env 파일에서
채운다(표준 dotenv 우선순위 관례) — 백엔드가 실제 배민 브랜드 정보를
환경변수로 주입해 크롤러를 실행할 때(ads.py의 _run_local_crawl 참고) .env
파일을 건드리지 않고도 그 값이 우선 적용되게 하기 위함이다. 개발자가
크롤러만 단독으로 실행할 땐(환경변수 없음) 지금처럼 .env 파일 값을 그대로
쓴다."""

import os
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
    store_lat: float | None = None
    store_lng: float | None = None


def _get(env_file_values: dict, key: str) -> str | None:
    """프로세스 환경변수(os.environ)를 .env 파일 값보다 우선한다."""
    return os.environ.get(key) or env_file_values.get(key) or None


def load_settings(env_path: str = ".env") -> Settings:
    file_values = dotenv_values(env_path)
    resolved = {
        k: _get(file_values, k)
        for k in ("KAKAO_REST_API_KEY", "STORE_ADDRESS", "STORE_DISPLAY_NAME", "CATEGORY_LABEL")
    }
    missing = [k for k, v in resolved.items() if not v]
    if missing:
        raise RuntimeError(f".env에 다음 값이 없습니다: {', '.join(missing)}")

    lat_str = _get(file_values, "STORE_LAT")
    lng_str = _get(file_values, "STORE_LNG")

    return Settings(
        kakao_api_key=resolved["KAKAO_REST_API_KEY"],
        store_address=resolved["STORE_ADDRESS"],
        store_display_name=resolved["STORE_DISPLAY_NAME"],
        category_label=resolved["CATEGORY_LABEL"],
        store_lat=float(lat_str) if lat_str else None,
        store_lng=float(lng_str) if lng_str else None,
    )
