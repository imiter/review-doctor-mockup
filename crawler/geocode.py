"""주소 문자열 → (위도, 경도). 카카오 로컬 API 사용."""

import requests

KAKAO_GEOCODE_URL = "https://dapi.kakao.com/v2/local/search/address.json"


class GeocodeError(Exception):
    pass


def address_to_coords(address: str, api_key: str) -> tuple[float, float]:
    res = requests.get(
        KAKAO_GEOCODE_URL,
        headers={"Authorization": f"KakaoAK {api_key}"},
        params={"query": address},
        timeout=10,
    )
    if res.status_code != 200:
        raise GeocodeError(f"카카오 API 오류 (status={res.status_code}): {address}")

    documents = res.json().get("documents", [])
    if not documents:
        raise GeocodeError(f"일치하는 주소를 찾지 못했습니다: {address}")

    doc = documents[0]
    return float(doc["y"]), float(doc["x"])
