from datetime import datetime, timezone

from scrapers.baemin_notices import map_notices


def test_map_notices_extracts_display_notices():
    raw = {
        "next": False,
        "notices": [
            {
                "id": 2197877, "shopNumber": 14804318,
                "contents": "리뷰 이벤트 공지",
                "images": [], "createdAt": "2025-10-17T13:37:00.14668",
                "displayStatus": "DISPLAY", "blockType": "NONE", "blockMessage": "",
            },
        ],
    }

    result = map_notices(raw)

    assert result == [{
        "external_notice_id": 2197877,
        "contents": "리뷰 이벤트 공지",
        "display_status": "DISPLAY",
        "block_type": "NONE",
        "notice_created_at": datetime(2025, 10, 17, 13, 37, 0, 146680, tzinfo=timezone.utc),
    }]


def test_map_notices_handles_multiple_notices():
    raw = {
        "next": False,
        "notices": [
            {
                "id": 2197877, "shopNumber": 14804318,
                "contents": "첫 번째 공지",
                "images": [], "createdAt": "2025-10-17T13:37:00.14668",
                "displayStatus": "DISPLAY", "blockType": "NONE", "blockMessage": "",
            },
            {
                "id": 2197878, "shopNumber": 14804318,
                "contents": "두 번째 공지",
                "images": [], "createdAt": "2025-10-18T14:00:00",
                "displayStatus": "DISPLAY", "blockType": "NONE", "blockMessage": "",
            },
        ],
    }

    result = map_notices(raw)

    assert len(result) == 2
    assert result[0]["external_notice_id"] == 2197877
    assert result[1]["external_notice_id"] == 2197878


def test_map_notices_handles_missing_created_at():
    raw = {
        "next": False,
        "notices": [
            {
                "id": 2197877, "shopNumber": 14804318,
                "contents": "공지",
                "images": [], "createdAt": None,
                "displayStatus": "DISPLAY", "blockType": "NONE", "blockMessage": "",
            },
        ],
    }

    result = map_notices(raw)

    assert result[0]["notice_created_at"] is None


def test_map_notices_handles_missing_contents():
    raw = {
        "next": False,
        "notices": [
            {
                "id": 2197877, "shopNumber": 14804318,
                "contents": None,
                "images": [], "createdAt": "2025-10-17T13:37:00",
                "displayStatus": "DISPLAY", "blockType": "NONE", "blockMessage": "",
            },
        ],
    }

    result = map_notices(raw)

    assert result[0]["contents"] == ""


def test_map_notices_returns_empty_list_for_empty_notices():
    raw = {
        "next": False,
        "notices": [],
    }

    result = map_notices(raw)

    assert result == []


def test_map_notices_handles_default_display_status_and_block_type():
    raw = {
        "next": False,
        "notices": [
            {
                "id": 2197877, "shopNumber": 14804318,
                "contents": "공지",
                "images": [], "createdAt": "2025-10-17T13:37:00",
                # displayStatus and blockType missing
            },
        ],
    }

    result = map_notices(raw)

    assert result[0]["display_status"] == "DISPLAY"
    assert result[0]["block_type"] == "NONE"
