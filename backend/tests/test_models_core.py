from datetime import datetime

from app.models import MockClock, Owner, Platform, Store, StorePlatform


def test_store_platform_relationships(db_session):
    owner = Owner(name="김사장", phone="010-0000-0000")
    store = Store(owner=owner, name="우리치킨 1호점", address="서울시 어딘가 1")
    platform = Platform(code="baemin", name="배달의민족", default_commission_rate=0.068)
    sp = StorePlatform(store=store, platform=platform, platform_store_name="우리치킨-강남")
    db_session.add_all([owner, store, platform, sp])
    db_session.flush()

    assert sp.store.owner.name == "김사장"
    assert sp.platform.code == "baemin"
    assert store.store_platforms == [sp]


def test_mock_clock_row(db_session):
    db_session.add(MockClock(id=1, mock_now=datetime(2026, 7, 25, 9, 0)))
    db_session.flush()
    clock = db_session.get(MockClock, 1)
    assert clock.mock_now == datetime(2026, 7, 25, 9, 0)
