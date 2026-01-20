package com.buyma.repository;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;
import java.util.Map;

/**
 * 바이마 상품 등록을 위한 MyBatis Mapper 인터페이스
 *
 * 대상 테이블:
 * - ace_products: 등록 대상 상품 정보
 * - ace_product_images: 상품 이미지 (cloudflare_image_url 사용)
 * - ace_product_options: 상품 옵션 (color, size)
 * - ace_product_variants: 상품 재고 정보
 * - ace_product_shipping: 배송 방법
 * - buyma_tokens: API 액세스 토큰
 * - api_call_logs: API 호출 로그
 */
@Mapper
public interface BuymaRepository {

    // ==================== 토큰 관련 ====================

    /**
     * 최신 액세스 토큰 조회
     */
    String selectAccessToken();

    /**
     * 토큰 저장/업데이트
     */
    void upsertToken(@Param("accessToken") String accessToken,
                     @Param("refreshToken") String refreshToken);

    // ==================== 상품 조회 관련 ====================

    /**
     * 등록 대상 상품 목록 조회
     * 조건: is_published = 0 AND control = 'publish' AND category_id > 0
     *
     * @param limit 조회 제한 수 (0이면 전체)
     */
    List<Map<String, Object>> selectProductsToRegister(@Param("limit") int limit);

    /**
     * 등록 대상 상품 총 수 조회
     */
    int countProductsToRegister();

    /**
     * 특정 상품 조회
     */
    Map<String, Object> selectProductById(@Param("aceProductId") int aceProductId);

    /**
     * 상품 이미지 조회
     * cloudflare_image_url 우선, 없으면 source_image_url 사용
     *
     * @param aceProductId ace_products.id
     */
    List<Map<String, Object>> selectProductImages(@Param("aceProductId") int aceProductId);

    /**
     * 상품 옵션 조회 (color, size)
     *
     * @param aceProductId ace_products.id
     */
    List<Map<String, Object>> selectProductOptions(@Param("aceProductId") int aceProductId);

    /**
     * 상품 재고(variants) 조회
     *
     * @param aceProductId ace_products.id
     */
    List<Map<String, Object>> selectProductVariants(@Param("aceProductId") int aceProductId);

    /**
     * 배송 방법 조회
     *
     * @param aceProductId ace_products.id
     */
    List<Integer> selectShippingMethods(@Param("aceProductId") int aceProductId);

    // ==================== 상품 업데이트 관련 ====================

    /**
     * 상품 등록 성공 시 업데이트
     *
     * @param aceProductId ace_products.id
     * @param requestUid 바이마 요청 UID
     * @param requestJson 요청 JSON
     */
    void updateProductRegistered(@Param("aceProductId") int aceProductId,
                                 @Param("requestUid") String requestUid,
                                 @Param("requestJson") String requestJson);

    /**
     * 상품 등록 실패 시 업데이트
     *
     * @param aceProductId ace_products.id
     * @param errorMessage 에러 메시지
     * @param requestJson 요청 JSON
     */
    void updateProductFailed(@Param("aceProductId") int aceProductId,
                             @Param("errorMessage") String errorMessage,
                             @Param("requestJson") String requestJson);

    /**
     * 웹훅 결과 업데이트 (성공/실패 모두)
     *
     * @param refNum reference_number
     * @param buymaProductId 바이마 상품 ID (성공시)
     * @param status 상태 (SUCCESS/FAIL)
     * @param errorMsg 에러 메시지 (실패시)
     */
    void updateWebhookResult(@Param("refNum") String refNum,
                             @Param("buymaProductId") String buymaProductId,
                             @Param("status") String status,
                             @Param("errorMsg") String errorMsg);

    // ==================== API 로그 관련 ====================

    /**
     * API 호출 로그 저장
     */
    void insertApiCallLog(@Param("apiType") String apiType,
                          @Param("apiAction") String apiAction,
                          @Param("aceProductId") Integer aceProductId,
                          @Param("requestJson") String requestJson,
                          @Param("responseJson") String responseJson,
                          @Param("httpStatusCode") int httpStatusCode,
                          @Param("requestUid") String requestUid,
                          @Param("isSuccess") boolean isSuccess,
                          @Param("errorMessage") String errorMessage);
}
