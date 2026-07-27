from pathlib import Path

import pytest

from rank_finder import StoreNameUnmatchableError, check_store_name_matchable, find_rank, parse_items

FIXTURE = Path(__file__).parent / "fixtures" / "sample_category_page_source.xml"


def test_parse_items_reads_real_fixture_without_error():
    xml = FIXTURE.read_text()
    items = parse_items([xml])
    assert len(items) > 0
    assert all("name" in item and "is_ad" in item for item in items)


def test_parse_items_dedupes_across_scrolls():
    xml = FIXTURE.read_text()
    # 같은 XML을 두 번(스크롤 겹침 흉내) 넣어도 항목 수가 늘어나지 않아야 한다
    items_once = parse_items([xml])
    items_twice = parse_items([xml, xml])
    assert len(items_once) == len(items_twice)


def test_parse_items_finds_known_store_and_marks_ad():
    # 이 픽스처(피자 카테고리, 스크롤 전 첫 화면)에는 실측 결과 "기본순"
    # 리스트에 가게명 노드가 정확히 1개뿐이고, 광고로 표시돼 있었다.
    xml = FIXTURE.read_text()
    items = parse_items([xml])
    assert items == [{"name": "미친피자 노원직영점", "is_ad": True}]


def test_find_rank_for_known_store_in_fixture():
    xml = FIXTURE.read_text()
    items = parse_items([xml])
    result = find_rank(items, "미친피자 노원직영점")
    assert result == {"rank": 1, "total_scanned": 1, "ads_above": 0}


def test_find_rank_not_found_returns_none():
    xml = FIXTURE.read_text()
    items = parse_items([xml])
    result = find_rank(items, "존재하지않는가게이름XYZ123")
    assert result["rank"] is None
    assert result["total_scanned"] == len(items)


def test_parse_items_ignores_promo_banner():
    # 실제 에뮬레이터 실행에서 확인된 케이스: "곧 사라져요! 이번 주 한정
    # 쿠폰 확인" 같은 프로모션 배너가 가게명으로 잘못 파싱된 적이 있었다.
    xml = (
        '<hierarchy>'
        '<node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">'
        '<node index="1" text="" resource-id="" class="android.view.View" '
        'content-desc="곧 사라져요! 이번 주 한정 쿠폰 확인" bounds="[39,2031][900,2092]" />'
        '</node>'
        '</hierarchy>'
    )
    items = parse_items([xml])
    assert items == []


def test_parse_items_strips_operating_status_suffix():
    # 실제 에뮬레이터 실행(Task 6)에서 확인된 케이스: 영업 준비중/마감 임박
    # 가게는 content-desc가 "{가게명}, 오늘\n오후 01:00 오픈"처럼 영업 상태
    # 문구가 콤마로 이어붙어 노출된다 — 콤마 앞부분만 가게명으로 남아야 한다.
    xml = (
        '<hierarchy>'
        '<node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">'
        '<node index="1" text="" resource-id="" class="android.view.View" '
        'content-desc="큰집닭강정당고개점, 오늘\n오후 01:00 오픈" bounds="[39,2031][500,2092]" />'
        '</node>'
        '</hierarchy>'
    )
    items = parse_items([xml])
    assert items == [{"name": "큰집닭강정당고개점", "is_ad": False}]


def test_parse_items_ignores_address_bar_node():
    # 상단 주소 표시줄(", 상계동 000-0000")처럼 콤마로 시작하는 content-desc는
    # _clean_store_name이 빈 문자열로 정리한다 — 빈 이름은 항목에 넣지 않는다.
    xml = (
        '<hierarchy>'
        '<node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">'
        '<node index="1" text="" resource-id="" class="android.view.View" '
        'content-desc=", 상계동 000-0000" bounds="[0,256][391,362]" />'
        '</node>'
        '</hierarchy>'
    )
    items = parse_items([xml])
    assert items == []


def test_parse_items_keeps_store_name_when_suffix_matches_decoration_keyword():
    # 실측 케이스(output/debug/search_giyoungee2.xml): "배달팁 무료" 배지가
    # 콤마로 이어붙어 content-desc가 "기영이 숯불두마리치킨 노원상계점,
    # 배달팁 무료"로 노출된다. 장식 필터의 "무료" 패턴을 콤마 이전(가게명)
    # 부분까지 포함한 원문 전체에 검사하면 실제 가게명 노드가 통째로
    # 걸러지는 버그가 있었다 — 콤마로 정리한 이름만 검사해야 한다.
    xml = (
        '<hierarchy>'
        '<node index="0" text="" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">'
        '<node index="1" text="" resource-id="" class="android.view.View" '
        'content-desc="기영이 숯불두마리치킨 노원상계점, 배달팁 무료" bounds="[39,1259][344,1548]" />'
        '</node>'
        '</hierarchy>'
    )
    items = parse_items([xml])
    assert items == [{"name": "기영이 숯불두마리치킨 노원상계점", "is_ad": False}]


def test_check_store_name_matchable_passes_for_normal_name():
    check_store_name_matchable("미친피자 노원직영점")  # 예외 없이 통과해야 함


def test_check_store_name_matchable_raises_for_decorated_name():
    with pytest.raises(StoreNameUnmatchableError):
        check_store_name_matchable("990원치킨")
