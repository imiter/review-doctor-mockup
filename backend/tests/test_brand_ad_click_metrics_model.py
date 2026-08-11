from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import BrandAdClickMetric


def test_brand_ad_click_metric_round_trips(db_session, seeded_user, platforms):
    db_session.add(BrandAdClickMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804912", metric_date=date(2026, 8, 1),
        ad_spend=95, impressions=40, clicks=1, ad_orders=0, ad_revenue=0,
    ))
    db_session.commit()

    row = db_session.query(BrandAdClickMetric).filter_by(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804912", metric_date=date(2026, 8, 1),
    ).one()
    assert row.ad_spend == 95
    assert row.impressions == 40
    assert row.clicks == 1


def test_brand_ad_click_metric_unique_constraint_blocks_duplicate_key(db_session, seeded_user, platforms):
    db_session.add(BrandAdClickMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804912", metric_date=date(2026, 8, 1),
        ad_spend=95, impressions=40, clicks=1, ad_orders=0, ad_revenue=0,
    ))
    db_session.commit()

    db_session.add(BrandAdClickMetric(
        store_id=seeded_user["store"].id, platform_id=platforms["baemin"].id,
        shop_no="14804912", metric_date=date(2026, 8, 1),
        ad_spend=999, impressions=999, clicks=9, ad_orders=9, ad_revenue=9000,
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()
