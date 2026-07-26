from unittest.mock import MagicMock, patch

import pytest

from geocode import GeocodeError, address_to_coords


def _mock_response(status_code, json_body):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    return resp


@patch("geocode.requests.get")
def test_address_to_coords_success(mock_get):
    mock_get.return_value = _mock_response(200, {
        "documents": [{"y": "37.6542", "x": "127.0620"}]
    })
    lat, lng = address_to_coords("서울시 노원구 당고개로 1", api_key="dummy")
    assert lat == 37.6542
    assert lng == 127.0620


@patch("geocode.requests.get")
def test_address_to_coords_sends_auth_header(mock_get):
    mock_get.return_value = _mock_response(200, {"documents": [{"y": "1", "x": "2"}]})
    address_to_coords("아무 주소", api_key="my-key")
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "KakaoAK my-key"


@patch("geocode.requests.get")
def test_address_to_coords_no_results_raises(mock_get):
    mock_get.return_value = _mock_response(200, {"documents": []})
    with pytest.raises(GeocodeError, match="일치하는 주소"):
        address_to_coords("존재하지 않는 주소", api_key="dummy")


@patch("geocode.requests.get")
def test_address_to_coords_http_error_raises(mock_get):
    mock_get.return_value = _mock_response(401, {})
    with pytest.raises(GeocodeError, match="401"):
        address_to_coords("서울시 노원구 당고개로 1", api_key="bad-key")
