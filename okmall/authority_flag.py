# -*- coding: utf-8 -*-
"""
등록판정 단일 정의 (buyma_listings 기준).

"이 상품이 바이마에 올라갔나" 를 묻는 곳은 collect / convert / price / register / stock 전부인데,
그 정의를 여기 한 곳에만 둔다.

2026-08-04: ace 기준으로 판단하던 옛 갈래와, 그것을 켜고 끄던 환경변수 스위치
            (USE_LISTING_AUTHORITY / use_listing_authority())를 전부 제거했다.
            이제 환경변수와 무관하게 항상 목록(buyma_listings) 기준이다.
"""


def registered_sql(ace_alias='a'):
    """★ 등록판정 단일 정의 (SQL 조각).
    "이 ace 가 바이마에 등록됐나" = 그 ace 가 속한 listing(단일=본인·중복=winner 공유)이
    게시중 + 번호 보유. collector/convert/price/register/stock 이 전부 이 한 정의를 공유한다.
    ace_alias = 바깥 쿼리의 ace_products 별칭(기본 'a', 별칭 없으면 'ace_products').
    """
    return ("EXISTS (SELECT 1 FROM source_offerings so "
            "JOIN buyma_listings bl ON bl.id=so.listing_id AND bl.is_active=1 "
            f"WHERE so.ace_product_id={ace_alias}.id AND so.is_active=1 "
            "AND bl.is_published=1 AND bl.buyma_product_id IS NOT NULL)")


def registered_join_where(offering_alias='so', listing_alias='bl'):
    """★ registered_sql 과 '같은 정의'의 조인형.

    registered_sql 은 ace 한 건마다 EXISTS 를 도는 형태라, 대량(수십만) 을 훑을 때 느리다.
    이미 source_offerings·buyma_listings 를 조인해 둔 쿼리에서는 이 형태를 쓰면 같은 판정을
    조인 한 번으로 끝낸다. (lotte 수집기 실측: EXISTS 형 77.7초 → 조인형 4.0초, 결과 동일)

    ☆ 두 함수는 반드시 같은 뜻이어야 한다 — 한쪽만 고치면 등록판정이 갈린다.
    """
    return (f"{offering_alias}.is_active=1 AND {listing_alias}.is_active=1 "
            f"AND {listing_alias}.is_published=1 "
            f"AND {listing_alias}.buyma_product_id IS NOT NULL")
