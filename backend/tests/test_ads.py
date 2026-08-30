import pathlib
import subprocess
from datetime import date, datetime, timezone
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import sessionmaker

from fastapi import HTTPException

import app.routers.ads as ads_module
from app.auth import hash_password
from app.credential_crypto import encrypt_credential
from app.models import AdCampaign, AdPerformanceMetric, AdRankSnapshot, BaeminShopBrand, BrandAdClickMetric, Order, Store, StorePlatformConnection, Subscription, User


def make_campaign(db_session, store, current_cpc=400, target_rank=3, shop_no=None):
    campaign = AdCampaign(
        store_id=store.id, category="치킨", current_cpc=current_cpc, target_rank=target_rank,
        status="active", shop_no=shop_no,
    )
    db_session.add(campaign)
    db_session.commit()
    return campaign


def _upgrade_to_pro(db_session, user_id):
    """ads.py의 사용자 세션 라우트 6개가 require_pro_plan 가드를 쓰므로, 이 파일에서
    실제 데이터 조회 흐름을 검증하는 기존 테스트들은 Pro 구독을 가정해야 회귀하지 않는다
    (conftest.seeded_user 기본값은 Basic). Basic 유저가 실제로 막히는지는 별도 신규
    테스트(test_ads_pro_guard.py 성격의 403 테스트들)가 검증한다."""
    db_session.query(Subscription).filter_by(user_id=user_id).update(
        {"plan": "pro", "expires_at": date(2099, 1, 1)}
    )
    db_session.commit()


def test_ads_performance_computes_acos_from_raw_metrics(client, db_session, seeded_user, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"])
    db_session.add(AdPerformanceMetric(
        campaign_id=campaign.id, metric_date=date.today(),
        ad_spend=169_000, clicks=1000, ad_orders=184, ad_revenue=184 * 25_000,
    ))
    db_session.commit()

    res = client.get("/ads/performance", headers=auth_headers)
    assert res.status_code == 200
    row = res.json()[0]
    assert row["cpc"] == 169.0
    assert row["cvr"] == 0.184
    assert row["acos"] is not None
    assert row["score"] is not None


def test_ads_performance_aggregates_multiple_days(client, db_session, seeded_user, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"])
    db_session.add_all([
        AdPerformanceMetric(campaign_id=campaign.id, metric_date=date.today(), ad_spend=10_000, clicks=100, ad_orders=10, ad_revenue=200_000),
        AdPerformanceMetric(campaign_id=campaign.id, metric_date=date.today(), ad_spend=15_000, clicks=150, ad_orders=15, ad_revenue=300_000),
    ])
    db_session.commit()

    row = client.get("/ads/performance", headers=auth_headers).json()[0]
    assert row["ad_spend"] == 25_000
    assert row["clicks"] == 250
    assert row["ad_orders"] == 25


def test_ads_performance_order_share(client, db_session, seeded_user, platforms, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"])
    db_session.add(AdPerformanceMetric(
        campaign_id=campaign.id, metric_date=date.today(), ad_spend=10_000, clicks=100, ad_orders=20, ad_revenue=400_000,
    ))
    for i in range(100):  # 매장 전체 주문 100건 (order_share 분모) — ad_orders 20건은 AdPerformanceMetric 집계값
        db_session.add(Order(
            store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id, order_no=f"OS-{i}",
            ordered_at=datetime.now(timezone.utc), menu_summary="치킨", order_type="delivery", amount=15_000,
        ))
    db_session.commit()

    row = client.get("/ads/performance?days=14", headers=auth_headers).json()[0]
    assert row["order_share"] == 0.2  # 20 / 100


def test_ads_performance_order_share_none_when_no_orders(client, db_session, seeded_user, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    make_campaign(db_session, seeded_user["store"])
    row = client.get("/ads/performance", headers=auth_headers).json()[0]
    assert row["order_share"] is None


def test_rank_monitoring_returns_latest_snapshot_and_recommendation(client, db_session, seeded_user, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"], target_rank=3)
    db_session.add_all([
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 25, 9, 0, tzinfo=timezone.utc),
                        current_rank=3, competitor_est_cpc=390, status="normal", recommended_action="keep"),
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc),
                        current_rank=7, competitor_est_cpc=650, status="rank_dropped",
                        recommended_action="raise_cpc", suggested_cpc=700),
    ])
    db_session.commit()

    row = client.get("/ads/rank-monitoring", headers=auth_headers).json()[0]
    assert row["current_rank"] == 7  # 가장 최신 스냅샷
    assert row["rank_status"] == "rank_dropped"
    assert row["suggested_cpc"] == 700


def test_rank_monitoring_no_snapshot_yet(client, db_session, seeded_user, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    make_campaign(db_session, seeded_user["store"])
    row = client.get("/ads/rank-monitoring", headers=auth_headers).json()[0]
    assert row["current_rank"] is None
    assert row["recommended_action"] == "keep"


def test_rank_by_distance_returns_points_sorted_by_distance(client, db_session, seeded_user, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"], target_rank=3)
    db_session.add_all([
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc),
                        current_rank=17, distance_km="0.00", point_label="0km", total_scanned=17, ads_above=4),
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 28, 8, 1, tzinfo=timezone.utc),
                        current_rank=9, distance_km="2.37", point_label="1.5~2.5km", total_scanned=10, ads_above=3),
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 28, 8, 2, tzinfo=timezone.utc),
                        current_rank=17, distance_km="2.61", point_label="2.5~3.5km", total_scanned=17, ads_above=5),
    ])
    db_session.commit()

    row = client.get("/ads/rank-by-distance", headers=auth_headers).json()[0]
    assert [p["point_label"] for p in row["points"]] == ["0km", "1.5~2.5km", "2.5~3.5km"]
    assert row["points"][1]["current_rank"] == 9


def test_rank_by_distance_keeps_only_latest_snapshot_per_point(client, db_session, seeded_user, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"], target_rank=3)
    db_session.add_all([
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc),
                        current_rank=20, distance_km="0.00", point_label="0km", total_scanned=20, ads_above=4),
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc),
                        current_rank=17, distance_km="0.00", point_label="0km", total_scanned=17, ads_above=4),
    ])
    db_session.commit()

    row = client.get("/ads/rank-by-distance", headers=auth_headers).json()[0]
    assert len(row["points"]) == 1
    assert row["points"][0]["current_rank"] == 17  # 가장 최신 값만 남는다


def test_rank_by_distance_ignores_time_series_mock_rows(client, db_session, seeded_user, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"])
    db_session.add(AdRankSnapshot(
        campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 25, 9, 0, tzinfo=timezone.utc),
        current_rank=3, competitor_est_cpc=390, status="normal", recommended_action="keep",
    ))
    db_session.commit()

    row = client.get("/ads/rank-by-distance", headers=auth_headers).json()[0]
    assert row["points"] == []


def test_click_performance_computes_acos_from_real_formula(client, db_session, seeded_user, platforms, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    db_session.add(BrandAdClickMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804914", metric_date=date.today(),
        ad_spend=34730, impressions=4632, clicks=106, ad_orders=16, ad_revenue=427000,
    ))
    db_session.commit()

    resp = client.get(
        f"/ads/click-performance?store_id={seeded_user['store'].id}&shop_no=14804914&days=30",
        headers=auth_headers,
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["shop_no"] == "14804914"
    assert body["ad_spend"] == 34730
    assert body["clicks"] == 106
    # CPC = 34730 / 106 ≈ 327.64
    assert body["cpc"] == round(34730 / 106, 2)
    # CVR = 16 / 106 ≈ 0.1509
    assert body["cvr"] == round(16 / 106, 4)
    assert body["acos"] is not None
    assert body["score"] is not None


def test_click_performance_scopes_to_requested_shop_no_only(client, db_session, seeded_user, platforms, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    db_session.add_all([
        BrandAdClickMetric(
            store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
            shop_no="14804912", metric_date=date.today(),
            ad_spend=95, impressions=40, clicks=1, ad_orders=0, ad_revenue=0,
        ),
        BrandAdClickMetric(
            store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
            shop_no="14804914", metric_date=date.today(),
            ad_spend=34730, impressions=4632, clicks=106, ad_orders=16, ad_revenue=427000,
        ),
    ])
    db_session.commit()

    resp = client.get(
        f"/ads/click-performance?store_id={seeded_user['store'].id}&shop_no=14804912&days=30",
        headers=auth_headers,
    )
    body = resp.json()
    assert body["ad_spend"] == 95  # 14804914분이 섞이면 안 됨


def test_click_performance_no_data_returns_zeroed_response(client, db_session, seeded_user, platforms, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    resp = client.get(
        f"/ads/click-performance?store_id={seeded_user['store'].id}&shop_no=99999999&days=30",
        headers=auth_headers,
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["ad_spend"] == 0
    assert body["acos"] is None  # 분모 0 — 계산 불가
    assert body["score"] is None


def test_ad_campaign_shop_no_defaults_to_none(db_session, seeded_user):
    campaign = make_campaign(db_session, seeded_user["store"])
    assert campaign.shop_no is None


def _bind_run_local_crawl_to_test_db(db_session, monkeypatch):
    """_run_local_crawl은 요청 스코프 db_session이 아니라 app.db.SessionLocal로
    자기 세션을 새로 연다(백그라운드 스레드에서 실행되므로 요청 세션을 공유할
    수 없다 — review_sync.run_review_sync_job과 동일한 패턴). db_session
    픽스처와 같은 (StaticPool) 엔진에 바인딩된 별도 sessionmaker로 바꿔치기해야
    _run_local_crawl이 이 테스트가 커밋해둔 캠페인/연결 행을 볼 수 있다."""
    monkeypatch.setattr(ads_module, "SessionLocal", sessionmaker(bind=db_session.get_bind(), autoflush=False))


def test_run_local_crawl_hard_fails_before_subprocess_when_shop_info_fetch_fails(
    db_session, seeded_user, platforms, monkeypatch
):
    """shop_no가 있는 캠페인에서 배민 로그인(fetch_shop_info 이전 단계)이
    실패하면 크롤러 서브프로세스를 아예 띄우지 않고 HTTPException(502)로
    중단해야 한다 — .env로 조용히 폴백하면 엉뚱한 가게 실측 결과가 이
    캠페인 몫으로 저장될 위험이 있다는 게 이 하드 에러의 존재 이유다."""
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="14804318")

    _bind_run_local_crawl_to_test_db(db_session, monkeypatch)

    def _raise_login(login_id, password):
        raise RuntimeError("배민 로그인 실패(테스트로 유발)")

    monkeypatch.setattr(ads_module, "baemin_login", _raise_login)
    mock_run = Mock()
    monkeypatch.setattr(ads_module.subprocess, "run", mock_run)

    with pytest.raises(HTTPException) as exc_info:
        ads_module._run_local_crawl(campaign.id)

    assert exc_info.value.status_code == 502
    assert "가게 정보 조회에 실패" in exc_info.value.detail
    mock_run.assert_not_called()  # 크롤러 서브프로세스는 절대 시작되면 안 된다


def test_run_local_crawl_skips_injection_entirely_when_campaign_has_no_shop_no(
    db_session, seeded_user, monkeypatch
):
    """shop_no가 없는 캠페인(예: 닭갈비연구소)은 배민 로그인/가게정보 조회
    블록을 아예 건너뛰고 기존처럼 crawler/.env 값 그대로(=주입 없는
    _crawler_subprocess_env() 결과) 서브프로세스를 실행해야 한다. 자격증명
    복호화·로그인이 절대 시도되지 않는다는 것까지 함께 확인한다."""
    campaign = make_campaign(db_session, seeded_user["store"], shop_no=None)

    _bind_run_local_crawl_to_test_db(db_session, monkeypatch)

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("shop_no가 없는 캠페인은 이 함수를 호출하면 안 된다")

    monkeypatch.setattr(ads_module, "decrypt_credential", _fail_if_called)
    monkeypatch.setattr(ads_module, "baemin_login", _fail_if_called)
    monkeypatch.setattr(ads_module, "fetch_shop_info", _fail_if_called)

    fake_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    mock_run = Mock(return_value=fake_proc)
    monkeypatch.setattr(ads_module.subprocess, "run", mock_run)
    monkeypatch.setattr(ads_module, "ingest_csv", lambda csv_path, campaign_id: (0, 0))

    result = ads_module._run_local_crawl(campaign.id)

    assert result == (0, 0)
    mock_run.assert_called_once()
    passed_env = mock_run.call_args.kwargs["env"]
    plain_env = ads_module._crawler_subprocess_env()
    # 주입 블록을 건너뛰었으니 서브프로세스에 넘어간 env는 그냥
    # _crawler_subprocess_env()의 결과 그대로다(주입된 STORE_DISPLAY_NAME 등이
    # 섞여 들어가지 않음).
    assert passed_env.keys() == plain_env.keys()


def test_ad_campaign_shop_no_can_be_set(db_session, seeded_user):
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="14804318")
    assert campaign.shop_no == "14804318"


def test_ads_performance_uses_real_brand_click_metrics_when_shop_no_set(client, db_session, seeded_user, platforms, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    from app.models import BrandAdClickMetric
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="14804318")
    db_session.add(BrandAdClickMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804318", metric_date=date.today(),
        ad_spend=34730, impressions=4632, clicks=106, ad_orders=16, ad_revenue=427000,
    ))
    db_session.commit()

    row = client.get("/ads/performance", headers=auth_headers).json()[0]
    assert row["campaign_id"] == campaign.id
    # CPC = 34730 / 106 ≈ 327.64 (실측 브랜드 데이터에서 계산됨, Mock ad_performance_metrics 아님)
    assert row["cpc"] == round(34730 / 106, 2)
    assert row["ad_spend"] == 34730


def test_ads_performance_ignores_ad_performance_metrics_when_shop_no_set(client, db_session, seeded_user, platforms, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    from app.models import BrandAdClickMetric
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="14804318")
    db_session.add(AdPerformanceMetric(
        campaign_id=campaign.id, metric_date=date.today(),
        ad_spend=999999, clicks=1, ad_orders=0, ad_revenue=0,
    ))
    db_session.add(BrandAdClickMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804318", metric_date=date.today(),
        ad_spend=1000, impressions=100, clicks=10, ad_orders=1, ad_revenue=25000,
    ))
    db_session.commit()

    row = client.get("/ads/performance", headers=auth_headers).json()[0]
    assert row["ad_spend"] == 1000  # AdPerformanceMetric(999999)이 아니라 BrandAdClickMetric 값


def test_ads_performance_without_shop_no_still_uses_mock(client, db_session, seeded_user, auth_headers):
    """회귀 테스트 — shop_no 없는 캠페인은 이번 변경으로 전혀 영향받지 않는다."""
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"])
    db_session.add(AdPerformanceMetric(
        campaign_id=campaign.id, metric_date=date.today(),
        ad_spend=10_000, clicks=100, ad_orders=10, ad_revenue=200_000,
    ))
    db_session.commit()

    row = client.get("/ads/performance", headers=auth_headers).json()[0]
    assert row["ad_spend"] == 10_000


def test_rank_monitoring_uses_real_distance_snapshot_when_shop_no_set(client, db_session, seeded_user, auth_headers):
    """distance_km == 0 필터가 실제로 걸려야만 통과한다 — 일부러 distance_km=0
    행을 시간상 더 오래되게, distance_km NULL(Mock)/비0km 행을 더 최신으로
    심는다. "그냥 가장 최신 스냅샷을 쓴다"는 버그로 후퇴해도 우연히 맞는
    답이 나오지 않도록 하기 위함 — 필터가 없으면 이 테스트는 반드시 실패한다."""
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"], target_rank=3, shop_no="14804318")
    db_session.add_all([
        # 반경별 실측 스냅샷(distance_km=0) — 시간상 더 오래됐지만 "현재 순위"의 근거가 돼야 함
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc),
                        current_rank=36, distance_km=0, point_label="0km", total_scanned=36, ads_above=8),
        # 시간별 Mock 스냅샷(distance_km NULL) — 시간상 더 최신이지만 shop_no 있는 캠페인이면 무시돼야 함
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc),
                        current_rank=1, competitor_est_cpc=390, status="normal", recommended_action="keep"),
        # 반경별이지만 0km가 아닌 실측 스냅샷 — 이것도 가장 최신이지만 distance_km != 0이라 무시돼야 함
        AdRankSnapshot(campaign_id=campaign.id, snapshot_at=datetime(2026, 8, 15, 10, 0, tzinfo=timezone.utc),
                        current_rank=9, distance_km="2.37", point_label="1.5~2.5km", total_scanned=10, ads_above=3),
    ])
    db_session.commit()

    row = client.get("/ads/rank-monitoring", headers=auth_headers).json()[0]
    assert row["current_rank"] == 36  # 가장 최신인 Mock(1위)도, 가장 최신인 1.5~2.5km(9위)도 아니라 distance_km=0(36위)
    assert row["rank_status"] == "rank_dropped"  # 36 > target_rank(3)
    assert row["recommended_action"] == "raise_cpc"
    assert row["competitor_est_cpc"] is None  # 배민이 노출하지 않아 항상 None(추정치 계산 제거)
    assert row["suggested_cpc"] == campaign.current_cpc + 30  # 실제 현재 CPC 기준 +30원 추천(campaign 기본 current_cpc=400)


def test_rank_monitoring_no_real_snapshot_yet_when_shop_no_set(client, db_session, seeded_user, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    make_campaign(db_session, seeded_user["store"], shop_no="14804318")
    row = client.get("/ads/rank-monitoring", headers=auth_headers).json()[0]
    assert row["current_rank"] is None
    assert row["recommended_action"] == "keep"


def test_rank_monitoring_without_shop_no_still_uses_mock_snapshot(client, db_session, seeded_user, auth_headers):
    """회귀 테스트 — shop_no 없는 캠페인은 기존 시간별 Mock 스냅샷 로직 그대로."""
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"], target_rank=3)
    db_session.add(AdRankSnapshot(
        campaign_id=campaign.id, snapshot_at=datetime(2026, 7, 25, 10, 0, tzinfo=timezone.utc),
        current_rank=7, competitor_est_cpc=650, status="rank_dropped",
        recommended_action="raise_cpc", suggested_cpc=700,
    ))
    db_session.commit()

    row = client.get("/ads/rank-monitoring", headers=auth_headers).json()[0]
    assert row["current_rank"] == 7
    assert row["suggested_cpc"] == 700  # Mock 경로는 suggested_cpc를 그대로 줌


def test_rank_monitoring_blocked_for_basic_plan(client, db_session, seeded_user, auth_headers):
    """seeded_user는 conftest 기본값대로 Basic 플랜 그대로 둔다(_upgrade_to_pro 호출 없음).
    개발자도구로 백엔드를 직접 호출해도 프론트 잠금을 우회할 수 없어야 한다."""
    make_campaign(db_session, seeded_user["store"])
    res = client.get("/ads/rank-monitoring", headers=auth_headers)
    assert res.status_code == 403
    assert res.json()["detail"]["error_code"] == "pro_required"


def test_rank_by_distance_run_blocked_for_basic_plan(client, db_session, seeded_user, auth_headers):
    """실기기 크롤링을 트리거하는 엔드포인트(3~5분, 실제 컴퓨팅 비용 발생)라 특히
    Basic 유저가 직접 호출해도 절대 실행되면 안 된다."""
    campaign = make_campaign(db_session, seeded_user["store"])
    res = client.post(f"/ads/rank-by-distance/run?campaign_id={campaign.id}", headers=auth_headers)
    assert res.status_code == 403
    assert res.json()["detail"]["error_code"] == "pro_required"


def test_ads_performance_blocked_for_basic_plan(client, db_session, seeded_user, auth_headers):
    make_campaign(db_session, seeded_user["store"])
    res = client.get("/ads/performance", headers=auth_headers)
    assert res.status_code == 403
    assert res.json()["detail"]["error_code"] == "pro_required"


def test_update_campaign_target_rank(client, db_session, seeded_user, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"], target_rank=5, shop_no="14804318")

    res = client.patch(f"/ads/campaigns/{campaign.id}", json={"target_rank": 2}, headers=auth_headers)

    assert res.status_code == 200
    assert res.json() == {"campaign_id": campaign.id, "target_rank": 2}
    db_session.refresh(campaign)
    assert campaign.target_rank == 2


def test_update_campaign_target_rank_rejects_other_users_campaign(client, db_session, seeded_user, auth_headers):
    """_campaign_for_user의 소유권 검사(store.user_id != user.id) 자체를
    검증한다 — 존재하지 않는 store_id로 흉내내면 "캠페인 못 찾음" 분기와
    "소유권 불일치" 분기가 똑같이 404를 내서 구분이 안 되므로, 실제로 다른
    유저 + 다른 스토어 + 그 스토어 소유 캠페인을 만들어 진짜 소유권
    불일치 상황을 재현한다."""
    _upgrade_to_pro(db_session, seeded_user["user"].id)

    other_user = User(
        email="other@dris.kr", password_hash=hash_password("other1234!"), nickname="박사장",
        phone_hash="b" * 64, marketing_agreed=True, created_at=datetime.now(timezone.utc),
    )
    db_session.add(other_user)
    db_session.flush()
    other_store = Store(user_id=other_user.id, name="다른가게", category="치킨", created_at=datetime.now(timezone.utc))
    db_session.add(other_store)
    db_session.commit()

    other_campaign = make_campaign(db_session, other_store, target_rank=5, shop_no="99999999")

    res = client.patch(f"/ads/campaigns/{other_campaign.id}", json={"target_rank": 2}, headers=auth_headers)

    assert res.status_code == 404


def test_update_campaign_target_rank_rejects_below_one(client, db_session, seeded_user, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"], target_rank=5, shop_no="14804318")

    res = client.patch(f"/ads/campaigns/{campaign.id}", json={"target_rank": 0}, headers=auth_headers)

    assert res.status_code == 422


def test_apply_bid_updates_current_cpc_and_starts_crawl(
    db_session, seeded_user, platforms, monkeypatch, tmp_path
):
    """입찰 제출 성공 → current_cpc 갱신 → 기존 크롤 인프라(_run_local_crawl)로
    이어지는 흐름을 검증한다. submit_cpc_bid/baemin_login/subprocess.run을
    전부 mock해 실제 Playwright/배민 접근 없이 로직만 확인한다."""
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    campaign = make_campaign(db_session, seeded_user["store"], current_cpc=95, shop_no="14804318")
    _bind_run_local_crawl_to_test_db(db_session, monkeypatch)

    monkeypatch.setattr(ads_module, "baemin_login", lambda login_id, password: Mock(page=Mock(), close=Mock()))
    monkeypatch.setattr(ads_module, "submit_cpc_bid", lambda page, shop_no, amount: None)
    monkeypatch.setattr(ads_module, "time", Mock(sleep=Mock()))
    # _apply_bid_then_crawl은 성공하면 이어서 _run_local_crawl을 호출하고,
    # shop_no가 있는 캠페인이라 그 안에서 다시 로그인 + fetch_shop_info를
    # 부른다(입찰가 제출용 로그인과는 별개 호출) — 이것도 mock해야
    # _run_local_crawl이 실제 배민 응답을 기다리다 502로 죽지 않는다.
    monkeypatch.setattr(ads_module, "fetch_shop_info", lambda page, shop_no: {
        "name": "치밥대장", "category": "치킨", "road_address": "서울시 노원구",
        "latitude": 37.6, "longitude": 127.0,
    })
    fake_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    monkeypatch.setattr(ads_module.subprocess, "run", Mock(return_value=fake_proc))
    monkeypatch.setattr(ads_module, "ingest_csv", lambda csv_path, campaign_id: (1, 0))

    inserted, skipped = ads_module._apply_bid_then_crawl(campaign.id, 125)

    assert (inserted, skipped) == (1, 0)
    db_session.refresh(campaign)
    assert campaign.current_cpc == 125
    ads_module.time.sleep.assert_called_once_with(ads_module._BID_APPLY_WAIT_SEC)


def test_apply_bid_leaves_current_cpc_unchanged_when_submit_cpc_bid_fails(
    db_session, seeded_user, platforms, monkeypatch
):
    """submit_cpc_bid(배민 실제 반영 API)가 실패하면 campaign.current_cpc는
    절대 갱신되면 안 된다 — _apply_bid_then_crawl은 코드 순서상 submit_cpc_bid가
    끝난 뒤에야 current_cpc를 쓰지만, 이 정합성은 향후 리팩터링으로 조용히
    깨질 수 있으니 회귀 테스트로 고정해둔다. submit_cpc_bid에서 바로 죽으므로
    _run_local_crawl(fetch_shop_info/subprocess.run/ingest_csv)까지는 절대
    도달하지 않는다 — 그래서 이 테스트는 그것들을 mock하지 않는다."""
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    campaign = make_campaign(db_session, seeded_user["store"], current_cpc=95, shop_no="14804318")
    original_cpc = campaign.current_cpc
    _bind_run_local_crawl_to_test_db(db_session, monkeypatch)

    monkeypatch.setattr(ads_module, "baemin_login", lambda login_id, password: Mock(page=Mock(), close=Mock()))

    def _raise_submit(page, shop_no, amount):
        raise RuntimeError("배민 입찰 제출 실패(테스트로 유발)")

    monkeypatch.setattr(ads_module, "submit_cpc_bid", _raise_submit)

    with pytest.raises(HTTPException) as exc_info:
        ads_module._apply_bid_then_crawl(campaign.id, 999)

    assert exc_info.value.status_code == 502
    db_session.refresh(campaign)
    assert campaign.current_cpc == original_cpc  # 시도한 새 금액(999)이 아니라 원래 값 그대로


def test_apply_bid_rejects_campaign_without_shop_no(db_session, seeded_user, monkeypatch):
    campaign = make_campaign(db_session, seeded_user["store"], shop_no=None)
    _bind_run_local_crawl_to_test_db(db_session, monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        ads_module._apply_bid_then_crawl(campaign.id, 125)

    assert exc_info.value.status_code == 500


def test_apply_bid_delegates_crawl_to_worker_when_no_local_crawler(
    db_session, seeded_user, platforms, monkeypatch
):
    """Railway 배포 환경(로컬 crawler venv 없음)에서는 입찰가 반영 후
    재측정 크롤을 _run_local_crawl로 직접 돌릴 수 없다 — ads_rank_by_distance_run과
    동일하게 CRAWL_WORKER_URL로 위임해야 한다(/internal/run-crawl POST). 위임에
    성공하면 실제 크롤 결과는 이 프로세스가 알 수 없으므로(워커가 안다) (0, 0)을
    반환하는 것만 확인한다 — 진짜 결과는 GET .../run/status가 워커에 직접 물어본다."""
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    campaign = make_campaign(db_session, seeded_user["store"], current_cpc=95, shop_no="14804318")
    _bind_run_local_crawl_to_test_db(db_session, monkeypatch)

    monkeypatch.setattr(ads_module, "baemin_login", lambda login_id, password: Mock(page=Mock(), close=Mock()))
    monkeypatch.setattr(ads_module, "submit_cpc_bid", lambda page, shop_no, amount: None)
    monkeypatch.setattr(ads_module, "time", Mock(sleep=Mock()))
    monkeypatch.setattr(ads_module, "_CRAWLER_PYTHON", pathlib.Path("/nonexistent/crawler/venv/bin/python"))
    monkeypatch.setattr(ads_module, "_CRAWL_WORKER_URL", "http://worker.example.com")
    monkeypatch.setattr(ads_module, "_CRAWL_WORKER_SECRET", "test-secret")

    fake_response = Mock(status_code=200)
    mock_post = Mock(return_value=fake_response)
    monkeypatch.setattr(ads_module.httpx, "post", mock_post)

    result = ads_module._apply_bid_then_crawl(campaign.id, 125)

    assert result == (0, 0)
    db_session.refresh(campaign)
    assert campaign.current_cpc == 125  # 입찰가 반영 자체는 워커 위임 여부와 무관하게 성공
    mock_post.assert_called_once_with(
        "http://worker.example.com/internal/run-crawl",
        params={"campaign_id": campaign.id},
        headers={"X-Worker-Secret": "test-secret"},
        timeout=15,
    )


def test_apply_bid_raises_clear_error_when_neither_local_crawler_nor_worker_available(
    db_session, seeded_user, platforms, monkeypatch
):
    """로컬 crawler venv도 CRAWL_WORKER_URL도 없는 환경(설정 누락)에서는
    재측정을 시작할 방법이 없다는 걸 명확한 500으로 알려야 한다 — 이때도
    입찰가 자체는 이미 배민에 반영됐다는 사실을 에러 메시지에 반드시
    남겨야 한다(사용자가 "입찰 실패"로 오해해 재시도하지 않도록)."""
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    conn = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id
    ).one()
    conn.credential_ciphertext = encrypt_credential("test_id", "test_pw")
    campaign = make_campaign(db_session, seeded_user["store"], current_cpc=95, shop_no="14804318")
    _bind_run_local_crawl_to_test_db(db_session, monkeypatch)

    monkeypatch.setattr(ads_module, "baemin_login", lambda login_id, password: Mock(page=Mock(), close=Mock()))
    monkeypatch.setattr(ads_module, "submit_cpc_bid", lambda page, shop_no, amount: None)
    monkeypatch.setattr(ads_module, "time", Mock(sleep=Mock()))
    monkeypatch.setattr(ads_module, "_CRAWLER_PYTHON", pathlib.Path("/nonexistent/crawler/venv/bin/python"))
    monkeypatch.setattr(ads_module, "_CRAWL_WORKER_URL", "")

    with pytest.raises(HTTPException) as exc_info:
        ads_module._apply_bid_then_crawl(campaign.id, 125)

    assert exc_info.value.status_code == 500
    assert "이미 배민에 정상 반영" in exc_info.value.detail
    db_session.refresh(campaign)
    assert campaign.current_cpc == 125  # 입찰가는 이미 반영된 채로 남아 있어야 한다


def test_apply_bid_endpoint_blocked_for_basic_plan(client, db_session, seeded_user, auth_headers):
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="14804318")
    res = client.post(
        f"/ads/rank-by-distance/apply-bid?campaign_id={campaign.id}&amount=125", headers=auth_headers
    )
    assert res.status_code == 403
    assert res.json()["detail"]["error_code"] == "pro_required"


def test_apply_bid_endpoint_rejects_non_positive_amount(client, db_session, seeded_user, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="14804318")
    res = client.post(
        f"/ads/rank-by-distance/apply-bid?campaign_id={campaign.id}&amount=0", headers=auth_headers
    )
    assert res.status_code == 400


def test_apply_bid_endpoint_delegates_login_and_bid_to_worker_when_no_local_crawler(
    client, db_session, seeded_user, monkeypatch, auth_headers
):
    """Railway처럼 로컬 crawler venv가 없는 배포 환경에서는 이 엔드포인트가
    배민 로그인/입찰 제출을 직접 실행하면 안 된다 — 클라우드 IP에서 로그인을
    시도하면 로그인 폼이 렌더링되지 않고 fill()이 타임아웃으로 막히는 현상이
    실측 확인됐다(2026-08-18, 봇 탐지로 추정). CRAWL_WORKER_URL의
    /internal/apply-bid로 전체(로그인+제출+재측정)를 위임해야 한다.
    baemin_login/submit_cpc_bid를 호출되면 실패하는 스파이로 바꿔 이
    프로세스에서 절대 실행되지 않음을 증명한다."""
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="14804318")
    monkeypatch.setattr(ads_module, "_CRAWLER_PYTHON", pathlib.Path("/nonexistent/crawler/venv/bin/python"))
    monkeypatch.setattr(ads_module, "_CRAWL_WORKER_URL", "http://worker.example.com")
    monkeypatch.setattr(ads_module, "_CRAWL_WORKER_SECRET", "test-secret")

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("이 프로세스에서 직접 로그인/제출을 시도하면 안 된다")

    monkeypatch.setattr(ads_module, "baemin_login", _fail_if_called)
    monkeypatch.setattr(ads_module, "submit_cpc_bid", _fail_if_called)

    fake_response = Mock(status_code=200, json=Mock(return_value={"status": "started"}))
    mock_post = Mock(return_value=fake_response)
    monkeypatch.setattr(ads_module.httpx, "post", mock_post)

    res = client.post(
        f"/ads/rank-by-distance/apply-bid?campaign_id={campaign.id}&amount=125", headers=auth_headers
    )

    assert res.status_code == 200
    assert res.json() == {"status": "started"}
    mock_post.assert_called_once_with(
        "http://worker.example.com/internal/apply-bid",
        params={"campaign_id": campaign.id, "amount": 125},
        headers={"X-Worker-Secret": "test-secret"},
        timeout=15,
    )


def test_apply_bid_endpoint_reports_worker_connection_failure_as_502(
    client, db_session, seeded_user, monkeypatch, auth_headers
):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="14804318")
    monkeypatch.setattr(ads_module, "_CRAWLER_PYTHON", pathlib.Path("/nonexistent/crawler/venv/bin/python"))
    monkeypatch.setattr(ads_module, "_CRAWL_WORKER_URL", "http://worker.example.com")

    def _raise(*args, **kwargs):
        raise ads_module.httpx.RequestError("연결 실패")

    monkeypatch.setattr(ads_module.httpx, "post", _raise)

    res = client.post(
        f"/ads/rank-by-distance/apply-bid?campaign_id={campaign.id}&amount=125", headers=auth_headers
    )

    assert res.status_code == 502


def test_internal_apply_bid_rejects_wrong_worker_secret(client, db_session, seeded_user, monkeypatch):
    monkeypatch.setattr(ads_module, "_CRAWL_WORKER_SECRET", "correct-secret")
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="14804318")

    res = client.post(
        f"/internal/apply-bid?campaign_id={campaign.id}&amount=125",
        headers={"X-Worker-Secret": "wrong-secret"},
    )

    assert res.status_code == 403


def test_internal_apply_bid_404s_for_missing_campaign(client, monkeypatch):
    monkeypatch.setattr(ads_module, "_CRAWL_WORKER_SECRET", "correct-secret")

    res = client.post(
        "/internal/apply-bid?campaign_id=999999&amount=125",
        headers={"X-Worker-Secret": "correct-secret"},
    )

    assert res.status_code == 404


def test_internal_apply_bid_starts_background_job(client, db_session, seeded_user, monkeypatch):
    """엔드포인트 계약(비밀키 인증·잠금·상태 초기화·백그라운드 위임)만
    검증한다 — 실제 로그인/입찰 로직은 _apply_bid_then_crawl 단위 테스트가
    이미 검증하므로 여기서는 _execute_bid_apply_job 자체를 스파이로
    바꾼다(반드시 락을 풀어야 다음 테스트가 409로 막히지 않는다 —
    실제 함수의 finally: _crawl_lock.release() 계약을 그대로 흉내낸다)."""
    monkeypatch.setattr(ads_module, "_CRAWL_WORKER_SECRET", "correct-secret")
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="14804318")
    called = {}

    def fake_execute(campaign_id, amount):
        called["campaign_id"] = campaign_id
        called["amount"] = amount
        ads_module._crawl_lock.release()

    monkeypatch.setattr(ads_module, "_execute_bid_apply_job", fake_execute)

    res = client.post(
        f"/internal/apply-bid?campaign_id={campaign.id}&amount=125",
        headers={"X-Worker-Secret": "correct-secret"},
    )

    assert res.status_code == 200
    assert res.json() == {"status": "started"}
    assert called == {"campaign_id": campaign.id, "amount": 125}


def test_rank_monitoring_includes_display_name_when_shop_no_matches(client, db_session, seeded_user, platforms, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    connection = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
    ).first()
    db_session.add(BaeminShopBrand(
        connection_id=connection.id, shop_no="12345",
        shop_name="[음식배달] 치밥대장 노원당고개점 / 치킨 99999",
    ))
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="12345")
    db_session.commit()

    row = client.get("/ads/rank-monitoring", headers=auth_headers).json()[0]
    assert row["campaign_id"] == campaign.id
    assert row["display_name"] == "치밥대장 노원당고개점"


def test_rank_monitoring_display_name_null_when_no_shop_no(client, db_session, seeded_user, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"])
    db_session.commit()

    row = client.get("/ads/rank-monitoring", headers=auth_headers).json()[0]
    assert row["campaign_id"] == campaign.id
    assert row["display_name"] is None


def test_rank_monitoring_display_name_null_when_shop_no_has_no_matching_brand(client, db_session, seeded_user, auth_headers):
    """shop_no는 있지만 아직 브랜드 목록 동기화 전이라 baemin_shop_brands에
    해당 행이 없는 경우 — store.name으로 폴백하지 않고 None이어야 한다."""
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="99999")
    db_session.commit()

    row = client.get("/ads/rank-monitoring", headers=auth_headers).json()[0]
    assert row["campaign_id"] == campaign.id
    assert row["display_name"] is None


def test_ads_performance_includes_display_name(client, db_session, seeded_user, platforms, auth_headers):
    _upgrade_to_pro(db_session, seeded_user["user"].id)
    connection = db_session.query(StorePlatformConnection).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
    ).first()
    db_session.add(BaeminShopBrand(
        connection_id=connection.id, shop_no="12345",
        shop_name="[음식배달] 치밥대장 노원당고개점 / 치킨 99999",
    ))
    campaign = make_campaign(db_session, seeded_user["store"], shop_no="12345")
    db_session.commit()

    row = client.get("/ads/performance", headers=auth_headers).json()[0]
    assert row["campaign_id"] == campaign.id
    assert row["display_name"] == "치밥대장 노원당고개점"
