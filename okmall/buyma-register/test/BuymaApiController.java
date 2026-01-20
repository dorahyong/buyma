package com.oneblocks.domain.common.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.oneblocks.domain.common.repository.BuymaRepository;
import com.oneblocks.domain.common.service.BuymaService;
import jakarta.servlet.http.HttpServletResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.util.UriComponentsBuilder;

import java.util.Map;
import java.util.UUID;

@RestController

@RequestMapping("/api/buyma")
public class BuymaApiController {

    @Autowired
    private BuymaService buymaService;

    private final ObjectMapper mapper = new ObjectMapper();
    private static final Logger log = LoggerFactory.getLogger(BuymaApiController.class);

    private final String CLIENT_ID = "RlWNHYuT-uBtO02yJxC7KpfY7I9wFC-w4HUqwliTKzQ";
    private final String CLIENT_SECRET = "a51hQoZvmpAMo4S-xK5jlVUSYeHVi-TOh-eGqBFkdrs";
    private final String REDIRECT_URI = "https://chem-referred-basement-charging.trycloudflare.com/api/buyma/oauth/callback";
    private final String END_POINT = "https://sandbox.personal-shopper-api.buyma.com";
    @Autowired
    private BuymaRepository buymaRepository;

    @GetMapping("/oauth/start")
    public void startOAuth(HttpServletResponse response) throws Exception {
        String url = UriComponentsBuilder.fromHttpUrl(END_POINT + "/oauth/authorize")
                .queryParam("response_type", "code")
                .queryParam("client_id", CLIENT_ID)
                .queryParam("redirect_uri", REDIRECT_URI)
                .queryParam("state", UUID.randomUUID().toString())
                .build(true).toUriString();
        response.sendRedirect(url);
    }

    @GetMapping("/oauth/callback")
    public ResponseEntity<Map<String, Object>> callback(@RequestParam String code) throws Exception {
        // 1. 토큰 교환
        Map<String, Object> body = Map.of(
                "code", code, "client_id", CLIENT_ID, "client_secret", CLIENT_SECRET,
                "grant_type", "authorization_code", "redirect_uri", REDIRECT_URI
        );

        RestTemplate rt = new RestTemplate();
        Map<String, Object> tokenRes = rt.postForObject(END_POINT + "/oauth/token", body, Map.class);

        // 2. 토큰 DB 저장
        buymaService.saveToken((String)tokenRes.get("access_token"), (String)tokenRes.get("refresh_token"));

        // 3. 즉시 전체 등록 실행
        Map<String, Object> result = buymaService.registerAllProductsFromAce();
        return ResponseEntity.ok(result);
    }

    /**
     * 바이마 웹훅 수신 및 상세 로깅 API [cite: 9, 49-54]
     */
    @PostMapping("/webhook")
    public ResponseEntity<String> handleWebhook(
            @RequestBody String body,
            @RequestHeader(value = "X-Buyma-Event", required = false) String event) {

        log.info("========== [BUYMA WEBHOOK START] ==========");
        log.info("Event Type: {}", event);
        log.info("Full Body: {}", body);

        try {
            JsonNode root = mapper.readTree(body);

            // 1. 관리번호 추출 로직 수정 (실패 웹훅은 root에 직접 있음)
            String refNum = root.has("reference_number") ? root.get("reference_number").asText() : null;

            // 성공 웹훅인 경우 product 노드 안에서 추출 시도
            if (refNum == null && root.has("product")) {
                refNum = root.get("product").get("reference_number").asText();
            }

            // 2. 이벤트별 분기 처리
            if ("product/create".equals(event) || "product/update".equals(event)) {
                // 성공
                String buymaId = root.get("product").get("id").asText();
                log.info(">>> [SUCCESS] Ref: {}, ID: {}", refNum, buymaId);

                if (refNum != null) {
                    buymaRepository.updateWebhookResult(refNum, buymaId, "SUCCESS", null);
                }

            } else if ("product/fail_to_create".equals(event) || "product/fail_to_update".equals(event)) {
                // 실패
                String errorMsg = root.has("errors") ? root.get("errors").toString() : "Unknown Error";
                log.error(">>> [FAILURE] Ref: {}, Msg: {}", refNum, errorMsg);

                if (refNum != null) {
                    // 이제 관리번호가 null이 아니므로 DB에 정상 기록됩니다.
                    buymaRepository.updateWebhookResult(refNum, null, "FAIL", errorMsg);
                } else {
                    log.warn("!!! [WARN] reference_number를 찾을 수 없어 DB 업데이트를 스킵합니다.");
                }
            }

            log.info("========== [BUYMA WEBHOOK END] ==========");
            return ResponseEntity.ok("ok");

        } catch (Exception e) {
            log.error(">>> [CRITICAL ERROR] Webhook processing failed: {}", e.getMessage(), e);
            return ResponseEntity.status(500).body("Error");
        }
    }
}