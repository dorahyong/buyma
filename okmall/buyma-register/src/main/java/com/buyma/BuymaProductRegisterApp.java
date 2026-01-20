package com.buyma;

import com.buyma.api.BuymaApiClient;
import com.buyma.repository.BuymaRepository;
import com.buyma.service.BuymaProductService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * 바이마 상품 등록 REST API 컨트롤러
 *
 * 엔드포인트:
 *   POST /api/buyma/register          - 전체 상품 등록
 *   POST /api/buyma/register?limit=N  - N개 상품만 등록
 *   POST /api/buyma/register/{id}     - 특정 상품 단건 등록
 *   GET  /api/buyma/status            - 등록 현황 조회
 *
 * Rate Limit 주의:
 *   - 상품 API: 24시간당 2,500회
 *   - 전체 API: 1시간당 5,000회
 */
@RestController
@RequestMapping("/api/buyma")
public class BuymaProductRegisterApp {

    private static final Logger log = LoggerFactory.getLogger(BuymaProductRegisterApp.class);

    @Autowired
    private BuymaProductService buymaProductService;

    @Autowired
    private BuymaApiClient buymaApiClient;

    @Autowired
    private BuymaRepository buymaRepository;

    /**
     * 상품 배치 등록 API
     *
     * @param limit 등록할 최대 상품 수 (선택, 기본: 전체)
     * @return 등록 결과
     */
    @PostMapping("/register")
    public ResponseEntity<Map<String, Object>> register(
            @RequestParam(value = "limit", defaultValue = "0") int limit) {

        log.info("=======================================================");
        log.info("       바이마 상품 등록 애플리케이션 시작");
        log.info("=======================================================");
        log.info("[요청] 배치 등록, limit={}", limit > 0 ? limit : "전체");

        try {
            // 등록 대상 상품 수 확인
            int totalCount = buymaRepository.countProductsToRegister();
            log.info("[현황] 등록 대상 상품: {}건", totalCount);

            if (totalCount == 0) {
                log.info("[완료] 등록 대상 상품이 없습니다.");
                return ResponseEntity.ok(Map.of(
                        "success", true,
                        "message", "등록 대상 상품이 없습니다.",
                        "total", 0
                ));
            }

            // 배치 등록 실행
            Map<String, Object> result = buymaProductService.registerAllProducts(limit);

            log.info("=======================================================");
            log.info("                    등록 결과");
            log.info("=======================================================");
            log.info("  총 대상     : {} 건", result.get("total"));
            log.info("  성공        : {} 건", result.get("success"));
            log.info("  실패        : {} 건", result.get("fail"));
            log.info("  스킵        : {} 건", result.get("skip"));
            log.info("  API 호출 수 : {} 회", result.get("apiRequestCount"));
            log.info("=======================================================");

            result.put("success", true);
            return ResponseEntity.ok(result);

        } catch (Exception e) {
            log.error("[심각한 오류] {}", e.getMessage(), e);
            return ResponseEntity.internalServerError().body(Map.of(
                    "success", false,
                    "error", e.getMessage()
            ));
        }
    }

    /**
     * 단건 상품 등록 API
     *
     * @param aceProductId 등록할 상품 ID
     * @return 등록 결과
     */
    @PostMapping("/register/{aceProductId}")
    public ResponseEntity<Map<String, Object>> registerSingle(
            @PathVariable("aceProductId") int aceProductId) {

        log.info("=======================================================");
        log.info("       바이마 단건 상품 등록 시작");
        log.info("=======================================================");
        log.info("[요청] 단건 등록, ace_product_id={}", aceProductId);

        try {
            BuymaApiClient.ApiResponse response = buymaProductService.registerSingleProduct(aceProductId);

            log.info("[결과] {}", response);

            if (response.isSuccess()) {
                return ResponseEntity.ok(Map.of(
                        "success", true,
                        "aceProductId", aceProductId,
                        "requestUid", response.getRequestUid() != null ? response.getRequestUid() : "",
                        "message", "등록 요청 성공"
                ));
            } else {
                return ResponseEntity.ok(Map.of(
                        "success", false,
                        "aceProductId", aceProductId,
                        "error", response.getErrorMessage() != null ? response.getErrorMessage() : "Unknown error"
                ));
            }

        } catch (Exception e) {
            log.error("[오류] {}", e.getMessage(), e);
            return ResponseEntity.internalServerError().body(Map.of(
                    "success", false,
                    "aceProductId", aceProductId,
                    "error", e.getMessage()
            ));
        }
    }

    /**
     * 등록 현황 조회 API
     *
     * @return 등록 현황
     */
    @GetMapping("/status")
    public ResponseEntity<Map<String, Object>> getStatus() {
        log.info("[요청] 등록 현황 조회");

        try {
            int pendingCount = buymaRepository.countProductsToRegister();

            return ResponseEntity.ok(Map.of(
                    "success", true,
                    "pendingCount", pendingCount,
                    "message", String.format("등록 대기 중인 상품: %d건", pendingCount)
            ));

        } catch (Exception e) {
            log.error("[오류] {}", e.getMessage(), e);
            return ResponseEntity.internalServerError().body(Map.of(
                    "success", false,
                    "error", e.getMessage()
            ));
        }
    }

    /**
     * 웹훅 수신 API (바이마에서 호출)
     *
     * @param body 웹훅 본문
     * @param event 이벤트 타입 (X-Buyma-Event 헤더)
     * @return 처리 결과
     */
    @PostMapping("/webhook")
    public ResponseEntity<String> handleWebhook(
            @RequestBody String body,
            @RequestHeader(value = "X-Buyma-Event", required = false) String event) {

        log.info("========== [BUYMA WEBHOOK START] ==========");
        log.info("Event Type: {}", event);
        log.info("Full Body: {}", body);

        try {
            buymaProductService.processWebhook(event, body);
            log.info("========== [BUYMA WEBHOOK END] ==========");
            return ResponseEntity.ok("ok");

        } catch (Exception e) {
            log.error(">>> [CRITICAL ERROR] Webhook processing failed: {}", e.getMessage(), e);
            return ResponseEntity.status(500).body("Error");
        }
    }
}
