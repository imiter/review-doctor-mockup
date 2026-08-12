from datetime import date, timedelta

from app.models import DailySettlement
from app.routers.sales import _period_range


def test_period_range_day_is_today_only():
    start, end = _period_range("day")
    assert start == end == date.today()


def test_period_range_week_is_seven_days():
    start, end = _period_range("week")
    assert (end - start).days == 6
    assert end == date.today()


def test_period_range_month_is_thirty_days():
    start, end = _period_range("month")
    assert (end - start).days == 29


def test_period_range_this_month_starts_on_first():
    start, end = _period_range("this_month")
    assert start.day == 1
    assert end == date.today()


def test_sales_summary_sums_within_period_excludes_outside(client, db_session, seeded_user, platforms, auth_headers):
    store, platform = seeded_user["store"], platforms["baemin"]
    db_session.add_all([
        DailySettlement(store_id=store.id, platform_id=platform.id, settle_date=date.today(), sales_amount=10_000, deposit_amount=8_000),
        DailySettlement(store_id=store.id, platform_id=platform.id, settle_date=date.today() - timedelta(days=3), sales_amount=20_000, deposit_amount=17_000),
        DailySettlement(store_id=store.id, platform_id=platform.id, settle_date=date.today() - timedelta(days=40), sales_amount=99_999, deposit_amount=90_000),
    ])
    db_session.commit()

    week = client.get("/sales/summary?period=week", headers=auth_headers).json()
    assert week["total_sales"] == 30_000  # 40일 전 데이터는 제외

    day = client.get("/sales/summary?period=day", headers=auth_headers).json()
    assert day["total_sales"] == 10_000

    deposits = client.get("/deposits/summary?period=week", headers=auth_headers).json()
    assert deposits["total_deposit"] == 25_000


def test_sales_matches_deposit_gap_reflects_real_world_delay(client, db_session, seeded_user, platforms, auth_headers):
    """매출 ≠ 입금 현장 문제: 같은 기간이라도 정산 지연으로 두 합계가 달라야 한다."""
    store, platform = seeded_user["store"], platforms["baemin"]
    db_session.add(DailySettlement(
        store_id=store.id, platform_id=platform.id, settle_date=date.today(),
        sales_amount=100_000, deposit_amount=0,  # 오늘 매출은 아직 입금 전
    ))
    db_session.commit()

    sales = client.get("/sales/summary?period=day", headers=auth_headers).json()["total_sales"]
    deposit = client.get("/deposits/summary?period=day", headers=auth_headers).json()["total_deposit"]
    assert sales == 100_000
    assert deposit == 0
    assert sales != deposit


def test_sales_daily_returns_one_row_per_date(client, db_session, seeded_user, platforms, auth_headers):
    store, platform = seeded_user["store"], platforms["baemin"]
    db_session.add_all([
        DailySettlement(store_id=store.id, platform_id=platform.id, settle_date=date.today(), sales_amount=10_000, deposit_amount=8_000),
        DailySettlement(store_id=store.id, platform_id=platform.id, settle_date=date.today() - timedelta(days=1), sales_amount=20_000, deposit_amount=17_000),
    ])
    db_session.commit()

    res = client.get("/sales/daily?days=7", headers=auth_headers).json()
    assert len(res) == 2
    assert res[0]["date"] < res[1]["date"]  # 날짜 오름차순
    assert sum(r["amount"] for r in res) == 30_000


def test_deposits_daily_matches_deposit_column(client, db_session, seeded_user, platforms, auth_headers):
    store, platform = seeded_user["store"], platforms["baemin"]
    db_session.add(DailySettlement(store_id=store.id, platform_id=platform.id, settle_date=date.today(), sales_amount=10_000, deposit_amount=8_000))
    db_session.commit()

    res = client.get("/deposits/daily?days=1", headers=auth_headers).json()
    assert res == [{"date": date.today().isoformat(), "amount": 8_000}]


def test_sales_breakdown_computes_commission_from_platform_rate(client, db_session, seeded_user, platforms, auth_headers):
    store, platform = seeded_user["store"], platforms["baemin"]  # rate 0.068
    db_session.add(DailySettlement(store_id=store.id, platform_id=platform.id, settle_date=date.today(), sales_amount=100_000, deposit_amount=89_200))
    db_session.commit()

    res = client.get("/sales/breakdown?period=day", headers=auth_headers).json()
    row = res["platforms"][0]
    assert row["platform_name"] == "배달의민족"
    assert row["sales_amount"] == 100_000
    assert row["commission_estimate"] == 6_800   # 100,000 * 0.068
    assert row["payment_fee_estimate"] == 3_000  # 100,000 * 0.03
    assert row["net_estimate"] == 90_200
    assert row["actual_deposit"] == 89_200       # 추정치와 실제 입금이 정산 주기 차이로 다를 수 있음
    assert row["is_estimate"] is True  # 신규 컬럼이 전부 NULL이라 추정치로 폴백


def test_daily_settlement_breakdown_columns_default_to_null(db_session, seeded_user, platforms):
    store, platform = seeded_user["store"], platforms["baemin"]
    row = DailySettlement(
        store_id=store.id, platform_id=platform.id, settle_date=date.today(),
        sales_amount=1_000, deposit_amount=900,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    assert row.commission_amount is None
    assert row.delivery_fee_amount is None
    assert row.customer_discount_amount is None
    assert row.ad_cost_amount is None


def test_daily_settlement_breakdown_columns_store_explicit_values(db_session, seeded_user, platforms):
    store, platform = seeded_user["store"], platforms["baemin"]
    row = DailySettlement(
        store_id=store.id, platform_id=platform.id, settle_date=date.today(),
        sales_amount=1_000, deposit_amount=900,
        commission_amount=100, delivery_fee_amount=50,
        customer_discount_amount=30, ad_cost_amount=20,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    assert row.commission_amount == 100
    assert row.delivery_fee_amount == 50
    assert row.customer_discount_amount == 30
    assert row.ad_cost_amount == 20


def test_sales_breakdown_uses_real_values_when_columns_filled(client, db_session, seeded_user, platforms, auth_headers):
    store, platform = seeded_user["store"], platforms["baemin"]
    db_session.add(DailySettlement(
        store_id=store.id, platform_id=platform.id, settle_date=date.today(),
        sales_amount=200_000, deposit_amount=150_000,
        commission_amount=20_000, delivery_fee_amount=10_000,
        customer_discount_amount=15_000, ad_cost_amount=3_000,
    ))
    db_session.commit()

    res = client.get("/sales/breakdown?period=day", headers=auth_headers).json()
    row = res["platforms"][0]
    assert row["is_estimate"] is False
    assert row["sales_amount"] == 200_000
    assert row["commission_amount"] == 20_000
    assert row["delivery_fee_amount"] == 10_000
    assert row["customer_discount_amount"] == 15_000
    assert row["ad_cost_amount"] == 3_000
    # misc = 200000 - 20000 - 10000 - 15000 - 3000 - 150000
    assert row["misc_amount"] == 2_000
    assert row["actual_deposit"] == 150_000
    assert "commission_estimate" not in row  # 추정치 필드는 실측 응답에 안 섞임


def test_sales_breakdown_any_not_all_rows_with_real_data_flips_is_estimate(client, db_session, seeded_user, platforms, auth_headers):
    """기간 내 일부 행만 신규 컬럼이 채워져도 is_estimate는 False가 된다.
    (func.count()는 ANY 의미지 ALL이 아니다.)
    """
    store, platform = seeded_user["store"], platforms["baemin"]
    db_session.add_all([
        # 첫 번째 행: 신규 컬럼 채워짐
        DailySettlement(
            store_id=store.id, platform_id=platform.id, settle_date=date.today(),
            sales_amount=150_000, deposit_amount=100_000,
            commission_amount=15_000, delivery_fee_amount=8_000,
            customer_discount_amount=10_000, ad_cost_amount=2_000,
        ),
        # 두 번째 행: 신규 컬럼 NULL (같은 기간, 다른 날짜)
        DailySettlement(
            store_id=store.id, platform_id=platform.id, settle_date=date.today() - timedelta(days=2),
            sales_amount=100_000, deposit_amount=80_000,
        ),
    ])
    db_session.commit()

    res = client.get("/sales/breakdown?period=week", headers=auth_headers).json()
    row = res["platforms"][0]
    assert row["is_estimate"] is False  # 일부 행이라도 실측값이 있으면 False
    assert row["sales_amount"] == 250_000  # 150 + 100
    assert row["commission_amount"] == 15_000  # 15 + 0 (NULL 행은 coalesce로 0)
    assert row["delivery_fee_amount"] == 8_000
    assert row["customer_discount_amount"] == 10_000
    assert row["ad_cost_amount"] == 2_000
    # misc = 250000 - 15000 - 8000 - 10000 - 2000 - 180000 = 35000
    assert row["misc_amount"] == 35_000
    assert row["actual_deposit"] == 180_000


def test_sales_breakdown_all_rows_without_real_data_in_period_falls_back_to_estimate(client, db_session, seeded_user, platforms, auth_headers):
    """기간 안에 신규 컬럼이 채워진 행이 하나도 없으면 전체 기간을 추정치로 폴백한다.
    (모든 행의 commission_amount, 등이 NULL인 경우)"""
    store, platform = seeded_user["store"], platforms["baemin"]
    db_session.add(DailySettlement(
        store_id=store.id, platform_id=platform.id,
        settle_date=date.today() - timedelta(days=15),
        sales_amount=100_000, deposit_amount=89_200,
    ))
    db_session.commit()

    res = client.get("/sales/breakdown?period=month", headers=auth_headers).json()
    row = res["platforms"][0]
    assert row["is_estimate"] is True
