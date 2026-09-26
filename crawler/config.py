"""crawler 전역 설정. 프로세스 환경변수를 우선하고, 없는 값만 .env 파일에서
채운다(표준 dotenv 우선순위 관례) — 백엔드가 실제 배민 브랜드 정보를
환경변수로 주입해 크롤러를 실행할 때(ads.py의 _run_local_crawl 참고) .env
파일을 건드리지 않고도 그 값이 우선 적용되게 하기 위함이다. 개발자가
크롤러만 단독으로 실행할 땐(환경변수 없음) 지금처럼 .env 파일 값을 그대로
쓴다."""

import os
from dataclasses import dataclass

from dotenv import dotenv_values

# 가게 주소를 기점으로 하는 반경 지점(사용자 확정) — 매 지점마다 가게 주소부터
# 다시 계산한 랜덤 방위각(거리는 고정, 방향만 매번 랜덤)으로 1개씩 뽑는다.
# 0km(가게 주소 자체)는 run_crawl.py에서 별도로 처리한다.
#
# 원래는 1.5~2.5km/2.5~3.5km 구간(그 구간 내 거리도 랜덤)이었으나, 최근
# 배민이 배달원 부족으로 배달 반경을 축소했다는 정황(사용자 확인, 2026-09-23)에
# 맞춰 고정 거리로 바꿨다 — 예전 구간 기준으로는 2.5~3.5km 지점이 실측 대부분
# NOT_FOUND(상위 88위 안에도 없음)로 나왔는데, 축소된 배달 반경 밖이라 카테고리
# 리스트에 애초에 안 뜨는 것일 가능성이 실제 원인으로 지목됐다.
#
# 1km/2km 두 지점만으로 1차 실측했을 때 둘 다 광고 슬롯 1위로 나와 경계(어디서부터
# 순위/노출이 꺾이는지)를 전혀 못 봤다 — 좁은 구간에 촘촘히 찍기보다 먼저 넓게
# 펼쳐서 대략적인 경계부터 찾기로 하고 0/1/2/3km 4개 지점으로 넓혔다(2026-09-23).
# 경계를 찾으면 그 근처만 좁혀서 2차로 다시 잴 수 있다.
#
# (min, max)가 같은 튜플이면 sample_ring_point가 그 거리 그대로(방위각만
# 랜덤)를 반환한다 — 별도 "고정 거리" 모드를 새로 만들지 않고 기존 함수를
# 그대로 재사용한다.
RING_KM_RANGES = [(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)]


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
