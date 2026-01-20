package com.buyma.api;

import com.buyma.repository.BuymaRepository;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.*;
import org.springframework.stereotype.Service;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestTemplate;

import jakarta.annotation.PostConstruct;

/**
 * 바이마 REST API 클라이언트
 *
 * API 엔드포인트: https://personal-shopper-api.buyma.com
 * 인증: X-Buyma-Personal-Shopper-Api-Access-Token 헤더
 *
 * Rate Limit:
 * - 전체 API: 5,000회/1시간
 * - 상품 API: 2,500회/24시간
 */
@Service
public class BuymaApiClient {

    private static final Logger log = LoggerFactory.getLogger(BuymaApiClient.class);
    private static final ObjectMapper mapper = new ObjectMapper();

    // 본번 환경 (운영)
    private static final String API_BASE_URL = "https://personal-shopper-api.buyma.com";
    // 샌드박스 환경 (테스트)
    // private static final String API_BASE_URL = "https://sandbox.personal-shopper-api.buyma.com";

    private static final String PRODUCTS_ENDPOINT = "/api/v1/products.json";

    @Autowired
    private BuymaRepository buymaRepository;

    private final RestTemplate restTemplate = new RestTemplate();

    // Rate Limit 관리
    private int requestCount = 0;
    private long lastRequestTime = 0;
    private static final long REQUEST_INTERVAL_MS = 1500; // 요청 간 1.5초 대기

    @PostConstruct
    public void init() {
        log.info("[BuymaApiClient] 초기화 완료. API URL: {}", API_BASE_URL);
    }

    /**
     * 상품 등록 API 호출
     * POST /api/v1/products.json
     *
     * @param productJson 상품 등록 요청 JSON 문자열
     * @return API 응답 결과
     */
    public ApiResponse registerProduct(String productJson) {
        String url = API_BASE_URL + PRODUCTS_ENDPOINT;

        try {
            // Rate Limit 대기
            waitForRateLimit();

            // 토큰 조회
            String accessToken = buymaRepository.selectAccessToken();
            if (accessToken == null || accessToken.isEmpty()) {
                log.error("[API 오류] 액세스 토큰이 없습니다.");
                ApiResponse errorResponse = new ApiResponse();
                errorResponse.setSuccess(false);
                errorResponse.setErrorMessage("액세스 토큰이 없습니다.");
                return errorResponse;
            }

            log.info("[API 요청] POST {}", url);
            log.debug("[요청 JSON] {}", productJson);

            // HTTP 헤더 설정
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            headers.set("X-Buyma-Personal-Shopper-Api-Access-Token", accessToken);

            HttpEntity<String> entity = new HttpEntity<>(productJson, headers);

            // API 호출
            ResponseEntity<String> response = restTemplate.exchange(
                    url, HttpMethod.POST, entity, String.class
            );

            requestCount++;
            lastRequestTime = System.currentTimeMillis();

            int statusCode = response.getStatusCode().value();
            String responseBody = response.getBody();

            log.info("[API 응답] Status: {}", statusCode);
            log.debug("[응답 Body] {}", responseBody);

            // 응답 파싱
            ApiResponse apiResponse = new ApiResponse();
            apiResponse.setStatusCode(statusCode);
            apiResponse.setBody(responseBody);

            if (statusCode == 201) {
                // 성공: request_uid 추출
                JsonNode root = mapper.readTree(responseBody);
                if (root.has("request_uid")) {
                    apiResponse.setRequestUid(root.get("request_uid").asText());
                }
                apiResponse.setSuccess(true);
                log.info("[등록 요청 성공] request_uid: {}", apiResponse.getRequestUid());
            } else {
                apiResponse.setSuccess(false);
                apiResponse.setErrorMessage("HTTP " + statusCode);
            }

            return apiResponse;

        } catch (HttpClientErrorException e) {
            requestCount++;
            lastRequestTime = System.currentTimeMillis();

            int statusCode = e.getStatusCode().value();
            String responseBody = e.getResponseBodyAsString();

            log.error("[API 오류] Status: {}, Body: {}", statusCode, responseBody);

            ApiResponse apiResponse = new ApiResponse();
            apiResponse.setStatusCode(statusCode);
            apiResponse.setBody(responseBody);
            apiResponse.setSuccess(false);

            if (statusCode == 422) {
                // 검증 실패
                try {
                    JsonNode root = mapper.readTree(responseBody);
                    if (root.has("errors")) {
                        apiResponse.setErrorMessage(root.get("errors").toString());
                    } else {
                        apiResponse.setErrorMessage(responseBody);
                    }
                } catch (Exception ex) {
                    apiResponse.setErrorMessage(responseBody);
                }
                log.error("[등록 실패 - 422] errors: {}", apiResponse.getErrorMessage());

            } else if (statusCode == 429) {
                // Rate Limit 초과
                apiResponse.setErrorMessage("Rate Limit 초과");
                log.warn("[Rate Limit 초과] 대기 필요");

            } else {
                apiResponse.setErrorMessage("HTTP " + statusCode + ": " + responseBody);
            }

            return apiResponse;

        } catch (Exception e) {
            log.error("[API 호출 예외] {}", e.getMessage(), e);
            ApiResponse errorResponse = new ApiResponse();
            errorResponse.setSuccess(false);
            errorResponse.setErrorMessage("Exception: " + e.getMessage());
            return errorResponse;
        }
    }

    /**
     * Rate Limit 대기
     */
    private void waitForRateLimit() {
        long elapsed = System.currentTimeMillis() - lastRequestTime;
        if (elapsed < REQUEST_INTERVAL_MS) {
            long waitTime = REQUEST_INTERVAL_MS - elapsed;
            log.debug("[Rate Limit] {}ms 대기", waitTime);
            try {
                Thread.sleep(waitTime);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
    }

    /**
     * 현재 요청 횟수 반환
     */
    public int getRequestCount() {
        return requestCount;
    }

    /**
     * 요청 횟수 초기화
     */
    public void resetRequestCount() {
        this.requestCount = 0;
    }

    /**
     * API 응답 데이터 클래스
     */
    public static class ApiResponse {
        private boolean success;
        private int statusCode;
        private String body;
        private String requestUid;
        private String errorMessage;
        private Long rateLimitReset;

        public boolean isSuccess() { return success; }
        public void setSuccess(boolean success) { this.success = success; }

        public int getStatusCode() { return statusCode; }
        public void setStatusCode(int statusCode) { this.statusCode = statusCode; }

        public String getBody() { return body; }
        public void setBody(String body) { this.body = body; }

        public String getRequestUid() { return requestUid; }
        public void setRequestUid(String requestUid) { this.requestUid = requestUid; }

        public String getErrorMessage() { return errorMessage; }
        public void setErrorMessage(String errorMessage) { this.errorMessage = errorMessage; }

        public Long getRateLimitReset() { return rateLimitReset; }
        public void setRateLimitReset(Long rateLimitReset) { this.rateLimitReset = rateLimitReset; }

        @Override
        public String toString() {
            return "ApiResponse{success=" + success + ", statusCode=" + statusCode +
                   ", requestUid='" + requestUid + "', errorMessage='" + errorMessage + "'}";
        }
    }
}
