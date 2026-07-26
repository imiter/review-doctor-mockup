"""누적된 page_source XML에서 가게 항목을 순서대로 파싱하고 순위를 찾는다.
Appium/네트워크 의존 없는 순수 함수.

실제 속성명은 Task 2 스파이크(SPIKE_NOTES.md)에서 확인한 값을 쓴다:
- 가게명은 content-desc가 있고 text가 비어있는 android.view.View 노드에
  노출된다 (텍스트가 아니라 content-desc). 같은 화면에는 카테고리 탭
  문구·거리·가격·별점 등 다른 텍스트/설명 노드도 섞여 있어, 단순히 "text
  또는 content-desc가 있는 모든 노드"를 항목으로 취급하면 안 된다 —
  장식성 문구를 제외하는 필터가 필요하다.
- 광고 배지는 가게명 노드와 별도의 content-desc="추천 광고 영역" 노드로
  노출된다. 이 노드는 문서 순서상 가게명 노드 "다음"(같은 카드 내부)에
  나타난다.
"""

import re
import xml.etree.ElementTree as ET

# 가게명이 아닌 장식성 content-desc를 걸러내기 위한 패턴들 (SPIKE_NOTES.md 관찰값 기반)
_DECORATION_PATTERNS = [
    re.compile(r"\d+%"),
    re.compile(r"\d+원"),
    re.compile(r"\d+(\.\d+)?km"),
    re.compile(r"\d+개"),
    re.compile(r"\d+점"),
    re.compile("리뷰"),
    re.compile("탭"),
    re.compile("버튼"),
    re.compile("영역"),
    re.compile("무료"),
    re.compile("할인"),
    re.compile("가능"),
    re.compile("가격"),
    re.compile("별점"),
    re.compile("거리"),
    re.compile("메뉴"),
    re.compile("최소주문"),
    re.compile("쿠폰"),
    re.compile("!"),  # 프로모션 배너 문구는 느낌표를 포함하는 경우가 많고, 실제 가게명에는 거의 안 쓰인다 (실측: "곧 사라져요! 이번 주 한정 쿠폰 확인" 배너가 가게명으로 잘못 파싱됨)
]

_MIN_STORE_NAME_NODE_WIDTH = 200  # 아이콘/버튼류 노드를 걸러내기 위한 최소 폭(px)

_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def _is_decorated(text: str) -> bool:
    return any(p.search(text) for p in _DECORATION_PATTERNS)


class StoreNameUnmatchableError(Exception):
    """STORE_DISPLAY_NAME이 장식 필터에 걸려 절대 매칭될 수 없을 때 발생한다."""


def check_store_name_matchable(store_display_name: str) -> None:
    """STORE_DISPLAY_NAME이 장식 필터(_is_decorated)에 걸리면 즉시 실패시킨다.

    장식 필터에 걸리는 이름은 parse_items가 절대 가게 항목으로 인식하지
    못해 항상 NOT_FOUND가 나온다 — 이 실패는 "가게가 리스트에 없음"과
    구별이 안 돼서 조용히 잘못된 결론(예: 배달 반경 밖)으로 오인되기
    쉽다. 크롤링 시작 전에 이 함수로 미리 걸러낸다."""
    if _is_decorated(store_display_name):
        raise StoreNameUnmatchableError(
            f"STORE_DISPLAY_NAME '{store_display_name}'이 장식 문구 필터에 걸려 "
            "가게 항목으로 인식될 수 없습니다 (숫자+원/%/km/개/점, '!', "
            "'쿠폰' 등 포함 여부를 확인하세요). 이 상태로 실행하면 항상 "
            "NOT_FOUND만 나옵니다."
        )


def _bounds_width(bounds: str) -> int:
    m = _BOUNDS_RE.match(bounds)
    if not m:
        return 0
    x1, _, x2, _ = map(int, m.groups())
    return x2 - x1


def _is_store_name_node(node) -> bool:
    content_desc = node.attrib.get("content-desc", "")
    text = node.attrib.get("text", "")
    cls = node.attrib.get("class", "")
    bounds = node.attrib.get("bounds", "")
    return bool(
        content_desc
        and not text
        and cls == "android.view.View"
        and not _is_decorated(content_desc)
        and _bounds_width(bounds) >= _MIN_STORE_NAME_NODE_WIDTH
    )


def _is_ad_marker_node(node) -> bool:
    return "광고" in node.attrib.get("content-desc", "")


def _clean_store_name(content_desc: str) -> str:
    """가게명 노드의 content-desc에서 순수 가게명만 뽑아낸다.

    영업 준비중/마감 임박 가게는 "{가게명}, 오늘\\n오후 01:00 오픈"처럼
    영업 상태 문구가 콤마로 이어붙어 노출되는 경우가 실측(Task 6 실제
    에뮬레이터 실행)으로 확인됐다 — 콤마 앞부분만 가게명으로 취급한다.
    """
    return content_desc.split(",")[0].strip()


def parse_items(xml_sources: list[str]) -> list[dict]:
    """누적된 page_source XML들에서 가게 항목을 순서대로 파싱한다.

    각 가게명 노드를 만나면 새 항목을 시작하고, 다음 가게명 노드(또는 해당
    xml_source 끝)가 나오기 전까지 "광고" 배지 노드가 있는지로 is_ad를
    판정한다. 이름이 이미 등장했으면(스크롤 겹침) 건너뛴다.
    """
    seen_names: set[str] = set()
    items: list[dict] = []

    for xml_str in xml_sources:
        root = ET.fromstring(xml_str)
        nodes = list(root.iter())

        i = 0
        while i < len(nodes):
            node = nodes[i]
            if not _is_store_name_node(node):
                i += 1
                continue

            name = _clean_store_name(node.attrib["content-desc"])

            # 다음 가게명 노드(또는 끝)까지 스캔하며 광고 배지 확인
            is_ad = False
            j = i + 1
            while j < len(nodes) and not _is_store_name_node(nodes[j]):
                if _is_ad_marker_node(nodes[j]):
                    is_ad = True
                j += 1

            # content-desc가 콤마로 시작하면(예: 주소 표시줄 ", 상계동 ...")
            # 정리 후 빈 문자열이 남는다 — 항목으로 넣지 않는다.
            if name and name not in seen_names:
                seen_names.add(name)
                items.append({"name": name, "is_ad": is_ad})

            i = j

    return items


def find_rank(items: list[dict], target_name: str) -> dict:
    ads_above = 0
    for idx, item in enumerate(items, start=1):
        if item["name"] == target_name:
            return {"rank": idx, "total_scanned": len(items), "ads_above": ads_above}
        if item["is_ad"]:
            ads_above += 1
    return {"rank": None, "total_scanned": len(items), "ads_above": ads_above}
