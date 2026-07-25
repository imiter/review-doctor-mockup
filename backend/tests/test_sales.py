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
