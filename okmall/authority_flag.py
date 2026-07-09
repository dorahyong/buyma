# -*- coding: utf-8 -*-
"""
단일권위 전환 스위치 (ace → buyma_listings).

reconcile / stock 파이프라인이 "이 상품이 바이마에 올라갔나 / 몇 번 페이지인가"를
판단할 때, 옛 방식(ace_products.is_published 세기)을 쓸지, 새 방식(buyma_listings 자신의
is_published/buyma_product_id 읽기)을 쓸지 결정하는 단일 스위치.

  OFF(기본): 지금까지와 100% 동일 — ace 기준.
  ON        : 새 주소록(listings) 기준.

토글 방법 (코드 수정 없이 환경변수로):
  - OFF : (미설정) 또는  USE_LISTING_AUTHORITY=0
  - ON  : USE_LISTING_AUTHORITY=1

전환 절차: OFF 상태로 갈래만 심어두고 → ON + dry-run 으로 예정 작업 검증 → 소수 실발사 →
          전면 ON → 안정 후 ace 정체성 컬럼 은퇴/삭제. 문제 시 환경변수만 0 으로 즉시 복귀.
"""
import os

_TRUES = ('1', 'true', 'yes', 'on')


def use_listing_authority():
    """True 면 새 주소록(buyma_listings) 기준으로 판단."""
    return os.getenv('USE_LISTING_AUTHORITY', '0').strip().lower() in _TRUES


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
