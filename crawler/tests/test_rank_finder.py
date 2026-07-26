from pathlib import Path

from rank_finder import find_rank, parse_items

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
