from baemin_navigator import contains_mountain_lot


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
