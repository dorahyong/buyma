package com.buyma.service;

import com.buyma.api.BuymaApiClient;
import com.buyma.api.BuymaApiClient.ApiResponse;
import com.buyma.repository.BuymaRepository;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.*;

/**
 * 바이마 상품 등록 서비스
 *
 * 처리 흐름:
 * 1. ace_products에서 등록 대상 조회 (is_published=0, control='publish')
 * 2. 각 상품별 images, options, variants, shipping 조회
 * 3. 바이마 API 요청 JSON 생성
 * 4. BuymaApiClient로 등록 API 호출
 * 5. 결과에 따라 DB 업데이트
 *
 * 바이마 API 고정값:
 * - buying_area_id: 2002003000
 * - shipping_area_id: 2002003000
 * - shipping_method_id: 369
 * - theme_id: 98
 * - duty: included
 */
@Service
public class BuymaProductService {

    private static final Logger log = LoggerFactory.getLogger(BuymaProductService.class);
    private static final ObjectMapper mapper = new ObjectMapper();

    // 바이마 API 고정값
    private static final String BUYING_AREA_ID = "2002003000";
    private static final String SHIPPING_AREA_ID = "2002003000";
    private static final int SHIPPING_METHOD_ID = 369;
    private static final int THEME_ID = 98;
    private static final String DUTY = "included";

    // 상품명 최대 길이
    private static final int MAX_NAME_LENGTH = 60;
    // 상품 설명 최대 길이
    private static final int MAX_COMMENTS_LENGTH = 3000;

    @Autowired
    private BuymaRepository buymaRepository;

    @Autowired
    private BuymaApiClient buymaApiClient;

    /**
     * 전체 상품 등록 실행
     *
     * @param limit 등록할 최대 상품 수 (0이면 전체)
     * @return 등록 결과 Map
     */
    public Map<String, Object> registerAllProducts(int limit) {
        log.info("========== [바이마 상품 등록 시작] ==========");

        // 카운터 초기화
        int successCount = 0;
        int failCount = 0;
        int skipCount = 0;

        // API 요청 카운터 초기화
        buymaApiClient.resetRequestCount();

        // 등록 대상 상품 조회
        List<Map<String, Object>> products = buymaRepository.selectProductsToRegister(limit);
        int totalCount = products.size();

        log.info("[등록 대상] 총 {}건", totalCount);

        if (totalCount == 0) {
            log.info("[등록 대상 없음] 모든 상품이 이미 등록되었거나 등록 대상이 없습니다.");
            return createResult(0, 0, 0, 0);
        }

        // 각 상품별 등록 처리
        int processedCount = 0;
        for (Map<String, Object> product : products) {
            processedCount++;
            int aceProductId = ((Number) product.get("id")).intValue();
            String refNum = (String) product.get("reference_number");

            log.info("---------- [{}/{}] 상품 처리 시작 ----------", processedCount, totalCount);
            log.info("[상품 정보] id={}, reference_number={}, name={}",
                     aceProductId, refNum, product.get("name"));

            try {
                // 1. 관련 데이터 조회
                List<Map<String, Object>> images = buymaRepository.selectProductImages(aceProductId);
                List<Map<String, Object>> options = buymaRepository.selectProductOptions(aceProductId);
                List<Map<String, Object>> variants = buymaRepository.selectProductVariants(aceProductId);
                List<Integer> shippingMethods = buymaRepository.selectShippingMethods(aceProductId);

                // 배송방법 기본값 설정
                if (shippingMethods == null || shippingMethods.isEmpty()) {
                    shippingMethods = Collections.singletonList(SHIPPING_METHOD_ID);
                }

                // 2. 필수 데이터 검증
                if (images == null || images.isEmpty()) {
                    log.warn("[SKIP] 이미지 없음 - ace_product_id={}", aceProductId);
                    skipCount++;
                    continue;
                }
                if (options == null || options.isEmpty()) {
                    log.warn("[SKIP] 옵션 없음 - ace_product_id={}", aceProductId);
                    skipCount++;
                    continue;
                }
                if (variants == null || variants.isEmpty()) {
                    log.warn("[SKIP] 재고 정보 없음 - ace_product_id={}", aceProductId);
                    skipCount++;
                    continue;
                }

                // 3. 바이마 API 요청 JSON 생성
                String requestJson = buildProductJson(product, images, options, variants, shippingMethods);

                if (requestJson == null) {
                    log.error("[SKIP] JSON 생성 실패 - ace_product_id={}", aceProductId);
                    skipCount++;
                    continue;
                }

                // 4. API 호출
                ApiResponse response = buymaApiClient.registerProduct(requestJson);

                // 5. 결과 처리 및 DB 업데이트
                if (response.isSuccess()) {
                    buymaRepository.updateProductRegistered(aceProductId, response.getRequestUid(), requestJson);
                    buymaRepository.insertApiCallLog("product", "create", aceProductId,
                                                requestJson, response.getBody(),
                                                response.getStatusCode(), response.getRequestUid(),
                                                true, null);
                    successCount++;
                    log.info("[SUCCESS] ace_product_id={}, request_uid={}", aceProductId, response.getRequestUid());

                } else {
                    buymaRepository.updateProductFailed(aceProductId, response.getErrorMessage(), requestJson);
                    buymaRepository.insertApiCallLog("product", "create", aceProductId,
                                                requestJson, response.getBody(),
                                                response.getStatusCode(), null,
                                                false, response.getErrorMessage());
                    failCount++;
                    log.error("[FAIL] ace_product_id={}, error={}", aceProductId, response.getErrorMessage());

                    // Rate Limit 초과 시 중단
                    if (response.getStatusCode() == 429) {
                        log.error("[CRITICAL] Rate Limit 초과! 등록 중단");
                        break;
                    }
                }

            } catch (Exception e) {
                failCount++;
                log.error("[EXCEPTION] ace_product_id={}, error={}", aceProductId, e.getMessage(), e);
                buymaRepository.updateProductFailed(aceProductId, e.getMessage(), null);
            }
        }

        log.info("========== [바이마 상품 등록 완료] ==========");
        return createResult(totalCount, successCount, failCount, skipCount);
    }

    /**
     * 단건 상품 등록
     *
     * @param aceProductId 등록할 상품 ID
     */
    public ApiResponse registerSingleProduct(int aceProductId) {
        log.info("[단건 등록] ace_product_id={}", aceProductId);

        // 상품 조회
        Map<String, Object> product = buymaRepository.selectProductById(aceProductId);

        if (product == null) {
            log.error("[단건 등록 실패] 상품을 찾을 수 없음 - ace_product_id={}", aceProductId);
            ApiResponse errorResponse = new ApiResponse();
            errorResponse.setSuccess(false);
            errorResponse.setErrorMessage("상품을 찾을 수 없습니다.");
            return errorResponse;
        }

        // 이미 등록된 상품 체크
        Object isPublished = product.get("is_published");
        if (isPublished != null && ((Number) isPublished).intValue() == 1) {
            log.warn("[단건 등록 스킵] 이미 등록된 상품 - ace_product_id={}", aceProductId);
            ApiResponse errorResponse = new ApiResponse();
            errorResponse.setSuccess(false);
            errorResponse.setErrorMessage("이미 등록된 상품입니다.");
            return errorResponse;
        }

        List<Map<String, Object>> images = buymaRepository.selectProductImages(aceProductId);
        List<Map<String, Object>> options = buymaRepository.selectProductOptions(aceProductId);
        List<Map<String, Object>> variants = buymaRepository.selectProductVariants(aceProductId);
        List<Integer> shippingMethods = buymaRepository.selectShippingMethods(aceProductId);

        if (shippingMethods == null || shippingMethods.isEmpty()) {
            shippingMethods = Collections.singletonList(SHIPPING_METHOD_ID);
        }

        String requestJson = buildProductJson(product, images, options, variants, shippingMethods);

        if (requestJson == null) {
            ApiResponse errorResponse = new ApiResponse();
            errorResponse.setSuccess(false);
            errorResponse.setErrorMessage("JSON 생성 실패");
            return errorResponse;
        }

        log.info("[요청 JSON] {}", requestJson);

        ApiResponse response = buymaApiClient.registerProduct(requestJson);

        // 결과 DB 업데이트
        if (response.isSuccess()) {
            buymaRepository.updateProductRegistered(aceProductId, response.getRequestUid(), requestJson);
            buymaRepository.insertApiCallLog("product", "create", aceProductId,
                                        requestJson, response.getBody(),
                                        response.getStatusCode(), response.getRequestUid(),
                                        true, null);
        } else {
            buymaRepository.updateProductFailed(aceProductId, response.getErrorMessage(), requestJson);
            buymaRepository.insertApiCallLog("product", "create", aceProductId,
                                        requestJson, response.getBody(),
                                        response.getStatusCode(), null,
                                        false, response.getErrorMessage());
        }

        return response;
    }

    /**
     * 웹훅 처리
     *
     * @param event 이벤트 타입
     * @param body 웹훅 본문
     */
    public void processWebhook(String event, String body) {
        try {
            JsonNode root = mapper.readTree(body);

            // 관리번호 추출 (실패 웹훅은 root에, 성공 웹훅은 product 노드에)
            String refNum = root.has("reference_number") ? root.get("reference_number").asText() : null;

            if (refNum == null && root.has("product")) {
                refNum = root.get("product").get("reference_number").asText();
            }

            // 이벤트별 처리
            if ("product/create".equals(event) || "product/update".equals(event)) {
                // 성공
                String buymaId = root.get("product").get("id").asText();
                log.info(">>> [WEBHOOK SUCCESS] Ref: {}, BuymaID: {}", refNum, buymaId);

                if (refNum != null) {
                    buymaRepository.updateWebhookResult(refNum, buymaId, "SUCCESS", null);
                }

            } else if ("product/fail_to_create".equals(event) || "product/fail_to_update".equals(event)) {
                // 실패
                String errorMsg = root.has("errors") ? root.get("errors").toString() : "Unknown Error";
                log.error(">>> [WEBHOOK FAILURE] Ref: {}, Msg: {}", refNum, errorMsg);

                if (refNum != null) {
                    buymaRepository.updateWebhookResult(refNum, null, "FAIL", errorMsg);
                } else {
                    log.warn("!!! [WARN] reference_number를 찾을 수 없어 DB 업데이트를 스킵합니다.");
                }
            }

        } catch (Exception e) {
            log.error("[웹훅 처리 오류] {}", e.getMessage(), e);
            throw new RuntimeException("웹훅 처리 실패: " + e.getMessage(), e);
        }
    }

    /**
     * 바이마 API 요청 JSON 생성
     */
    private String buildProductJson(Map<String, Object> product,
                                    List<Map<String, Object>> images,
                                    List<Map<String, Object>> options,
                                    List<Map<String, Object>> variants,
                                    List<Integer> shippingMethods) {
        try {
            ObjectNode root = mapper.createObjectNode();
            ObjectNode pNode = root.putObject("product");

            // 필수 필드
            pNode.put("reference_number", (String) product.get("reference_number"));
            pNode.put("control", "publish");

            // 상품명 (60자 제한)
            String name = truncateName((String) product.get("name"));
            pNode.put("name", name);

            // 상품 설명 (comments가 null이면 name 사용)
            String comments = (String) product.get("comments");
            if (comments == null || comments.trim().isEmpty()) {
                comments = (String) product.get("name");
            }
            if (comments.length() > MAX_COMMENTS_LENGTH) {
                comments = comments.substring(0, MAX_COMMENTS_LENGTH);
            }
            pNode.put("comments", comments);

            // 브랜드 (brand_id가 0이면 brand_name 사용)
            Object brandIdObj = product.get("brand_id");
            Integer brandId = brandIdObj != null ? ((Number) brandIdObj).intValue() : null;
            String brandName = (String) product.get("brand_name");
            if (brandId != null && brandId > 0) {
                pNode.put("brand_id", brandId);
            } else if (brandName != null && !brandName.isEmpty()) {
                pNode.put("brand_name", brandName);
            } else {
                log.error("[JSON 생성 실패] 브랜드 정보 없음");
                return null;
            }

            // 카테고리
            Object categoryIdObj = product.get("category_id");
            Integer categoryId = categoryIdObj != null ? ((Number) categoryIdObj).intValue() : null;
            if (categoryId == null || categoryId == 0) {
                log.error("[JSON 생성 실패] 카테고리 정보 없음");
                return null;
            }
            pNode.put("category_id", categoryId);

            // 가격
            Object priceObj = product.get("price");
            Integer price = priceObj != null ? ((Number) priceObj).intValue() : null;
            if (price == null || price <= 0) {
                log.error("[JSON 생성 실패] 가격 정보 없음");
                return null;
            }
            pNode.put("price", price);

            // 구매 기한 (available_until)
            Object availableUntil = product.get("available_until");
            String formattedDate;
            if (availableUntil != null) {
                if (availableUntil instanceof java.sql.Date) {
                    formattedDate = ((java.sql.Date) availableUntil).toLocalDate()
                            .format(DateTimeFormatter.ofPattern("yyyy/MM/dd"));
                } else if (availableUntil instanceof java.time.LocalDate) {
                    formattedDate = ((java.time.LocalDate) availableUntil)
                            .format(DateTimeFormatter.ofPattern("yyyy/MM/dd"));
                } else {
                    formattedDate = availableUntil.toString().replace("-", "/");
                }
            } else {
                // 기본값: 현재일 + 30일
                formattedDate = LocalDate.now().plusDays(30)
                        .format(DateTimeFormatter.ofPattern("yyyy/MM/dd"));
            }
            pNode.put("available_until", formattedDate);

            // 지역 정보 (고정값)
            pNode.put("buying_area_id", BUYING_AREA_ID);
            pNode.put("shipping_area_id", SHIPPING_AREA_ID);

            // 구매처명
            String buyingShopName = (String) product.get("buying_shop_name");
            if (buyingShopName != null && !buyingShopName.isEmpty()) {
                pNode.put("buying_shop_name", buyingShopName);
            }

            // 테마, 관세 (고정값)
            pNode.put("theme_id", THEME_ID);
            pNode.put("duty", DUTY);

            // 이미지 배열
            ArrayNode imagesNode = pNode.putArray("images");
            for (Map<String, Object> img : images) {
                String imageUrl = (String) img.get("image_url");
                Object posObj = img.get("position");
                int position = posObj != null ? ((Number) posObj).intValue() : 1;
                if (imageUrl != null && !imageUrl.isEmpty()) {
                    imagesNode.addObject()
                            .put("path", imageUrl)
                            .put("position", position);
                }
            }

            // 배송 방법 배열
            ArrayNode shippingNode = pNode.putArray("shipping_methods");
            for (Integer methodId : shippingMethods) {
                shippingNode.addObject().put("shipping_method_id", methodId);
            }

            // 옵션 배열 (color, size)
            ArrayNode optionsNode = pNode.putArray("options");
            int colorPosition = 1;
            int sizePosition = 1;

            for (Map<String, Object> opt : options) {
                String type = (String) opt.get("type");
                String value = (String) opt.get("value");
                Object masterIdObj = opt.get("master_id");
                Integer masterId = masterIdObj != null ? ((Number) masterIdObj).intValue() : null;

                ObjectNode optNode = optionsNode.addObject();
                optNode.put("type", type);
                optNode.put("value", value);

                // master_id가 null 또는 0이면 기본값 설정
                if (masterId == null || masterId == 0) {
                    masterId = "color".equals(type) ? 99 : 0;
                }
                optNode.put("master_id", masterId);

                // position은 타입별로 1부터 시작
                if ("color".equals(type)) {
                    optNode.put("position", colorPosition++);
                } else {
                    optNode.put("position", sizePosition++);
                }
            }

            // variants 배열
            ArrayNode variantsNode = pNode.putArray("variants");
            for (Map<String, Object> var : variants) {
                ObjectNode varNode = variantsNode.addObject();

                // options_json 파싱하여 사용
                String optionsJson = (String) var.get("options_json");
                if (optionsJson != null && !optionsJson.isEmpty()) {
                    ArrayNode varOpts = (ArrayNode) mapper.readTree(optionsJson);
                    varNode.set("options", varOpts);
                } else {
                    // options_json이 없으면 color_value, size_value로 생성
                    ArrayNode varOpts = varNode.putArray("options");
                    String colorValue = (String) var.get("color_value");
                    String sizeValue = (String) var.get("size_value");
                    if (colorValue != null) {
                        varOpts.addObject().put("type", "color").put("value", colorValue);
                    }
                    if (sizeValue != null) {
                        varOpts.addObject().put("type", "size").put("value", sizeValue);
                    }
                }

                // stock_type, stocks
                String stockType = (String) var.get("stock_type");
                Object stocksObj = var.get("stocks");
                int stocks = stocksObj != null ? ((Number) stocksObj).intValue() : 1;
                varNode.put("stock_type", stockType != null ? stockType : "stock_in_hand");
                varNode.put("stocks", stocks);
            }

            String json = mapper.writeValueAsString(root);
            log.debug("[JSON 생성 완료] {}", json);
            return json;

        } catch (Exception e) {
            log.error("[JSON 생성 예외] {}", e.getMessage(), e);
            return null;
        }
    }

    /**
     * 상품명 60자 제한 처리
     */
    private String truncateName(String name) {
        if (name == null) {
            return "";
        }
        if (name.length() <= MAX_NAME_LENGTH) {
            return name;
        }
        return name.substring(0, MAX_NAME_LENGTH - 3) + "...";
    }

    /**
     * 결과 Map 생성
     */
    private Map<String, Object> createResult(int total, int success, int fail, int skip) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("total", total);
        result.put("success", success);
        result.put("fail", fail);
        result.put("skip", skip);
        result.put("apiRequestCount", buymaApiClient.getRequestCount());

        log.info("[등록 결과] 총 {}건 중 성공 {}건, 실패 {}건, 스킵 {}건, API 호출 {}회",
                 total, success, fail, skip, buymaApiClient.getRequestCount());

        return result;
    }
}
