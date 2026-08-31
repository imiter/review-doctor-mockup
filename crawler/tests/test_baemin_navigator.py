from baemin_navigator import _match_grid_column, contains_mountain_lot


def test_contains_mountain_lot_detects_mountain_lot_address():
    # 실측 케이스: "경기 남양주시 내각리 산56-21"처럼 산 지번(임야) 주소
    assert contains_mountain_lot("경기 남양주시 내각리 산56-21") is True
    assert contains_mountain_lot("경기 의정부시 장암동 산150") is True


def test_contains_mountain_lot_ignores_place_names_with_san():
    # "부산", "온산읍"처럼 산 뒤에 숫자가 바로 오지 않는 지명은 걸리지 않아야 한다
    assert contains_mountain_lot("부산광역시 해운대구") is False
    assert contains_mountain_lot("울산 울주군 온산읍") is False


def test_contains_mountain_lot_ignores_normal_address():
    assert contains_mountain_lot("서울 노원구 상계로23다길 5") is False
    assert contains_mountain_lot("서울 노원구 상계동 389-474") is False


def test_match_grid_column_matches_exact_label():
    row = ["패스트푸드", "찜·탕", "족발·보쌈", "분식", "카페·디저트"]
    assert _match_grid_column(row, "찜·탕") == 1


def test_match_grid_column_matches_when_grid_label_is_prefix_of_category():
    # 실측 케이스(2026-08-31, 곱도리탕 캠페인): fetch_shop_info API는
    # "찜·탕·찌개"를 돌려주는데 실제 탭 라벨은 "찜·탕"뿐이라, 접두사
    # 매칭이 없으면 이 카테고리는 절대 찾을 수 없었다.
    row = ["패스트푸드", "찜·탕", "족발·보쌈", "분식", "카페·디저트"]
    assert _match_grid_column(row, "찜·탕·찌개") == 1


def test_match_grid_column_matches_meat_grill_prefix():
    row = ["한식", "고기", "양식", "아시안", "야식"]
    assert _match_grid_column(row, "고기·구이") == 1


def test_match_grid_column_returns_none_when_no_match():
    row = ["한식", "고기", "양식", "아시안", "야식"]
    assert _match_grid_column(row, "백반·죽·국수") is None


def test_match_grid_column_does_not_match_unrelated_category():
    row = ["한식", "고기", "양식", "아시안", "야식"]
    assert _match_grid_column(row, "일식") is None
