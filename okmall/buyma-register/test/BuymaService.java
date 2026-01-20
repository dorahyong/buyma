package com.oneblocks.domain.common.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.oneblocks.domain.common.repository.BuymaRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.*;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Service
public class BuymaService {

    @Autowired
    private BuymaRepository buymaRepository;

    private final RestTemplate restTemplate = new RestTemplate();
    private final ObjectMapper mapper = new ObjectMapper();
    private final String API_URL = "https://sandbox.personal-shopper-api.buyma.com/api/v1/products.json";
    private static final Logger log = LoggerFactory.getLogger(BuymaRepository.class);

    // 1. 토큰을 DB에 저장
    public void saveToken(String accessToken, String refreshToken) {
        buymaRepository.upsertToken(accessToken, refreshToken);
    }
    /**
     * ACE DB의 모든 상품을 등록하고, 전송 데이터를 로그로 남깁니다.
     */
    public Map<String, Object> registerAllProductsFromAce() {
        // 1. DB에서 토큰 및 대상 상품 조회
        String token = buymaRepository.selectLatestToken();
        List<Map<String, Object>> products = buymaRepository.selectAceProductsAll();

        int successCount = 0;
        int failCount = 0;

        log.info("========== [BATCH REGISTRATION START] ==========");
        log.info("대상 상품 수: {}", products.size());

        for (Map<String, Object> product : products) {
            try {
                String refNum = (String) product.get("reference_number");
                List<Map<String, Object>> optionsList = buymaRepository.selectAceOptionsByRef(refNum);

                // 2. JSON Payload 구성 시작
                ObjectNode root = mapper.createObjectNode();
                ObjectNode pNode = root.putObject("product");

                pNode.put("reference_number", refNum);
                pNode.put("control", "publish");

                // [가공] 상품명 60자 제한
                String productName = (String) product.get("name");
                if (productName != null && productName.length() > 60) {
                    productName = productName.substring(0, 57) + "...";
                }
                pNode.put("name", productName);
                pNode.put("comments", (String) product.get("name"));

                pNode.put("brand_id", (Integer) product.get("brand_id"));

                // [가공] 카테고리 ID (샌드박스에서 확인된 유효한 ID로 임시 설정)
                pNode.put("category_id", 10101);

                pNode.put("price", (Integer) product.get("sales_price_jpy"));
                pNode.put("available_until", "2026/03/31");
                pNode.put("buying_area_id", "2003004001");
                pNode.put("shipping_area_id", "2003004001");
                pNode.put("duty", "included");

                // 이미지 하드코딩
                ArrayNode imgNode = pNode.putArray("images");
                String imgUrl = "https://assets.adidas.com/images/h_2000,f_auto,q_auto,fl_lossy,c_fill,g_auto/61f87dec481e4512823ea7fb0080ba1a_9366/Black_BB5476_01_standard.jpg";
                imgNode.addObject().put("path", imgUrl).put("position", 1);
                imgNode.addObject().put("path", imgUrl).put("position", 2);

                // 배송방법 (364 고정)
                pNode.putArray("shipping_methods").addObject().put("shipping_method_id", 364);

                // 3. Options 가공 및 포지션 재정렬
                ArrayNode optionsJsonArray = pNode.putArray("options");
                List<Map<String, Object>> cleanColors = new ArrayList<>();
                List<Map<String, Object>> cleanSizes = new ArrayList<>();

                int colorPos = 1;
                int sizePos = 1;

                for (Map<String, Object> opt : optionsList) {
                    String type = (String) opt.get("type");
                    String value = (String) opt.get("value");
                    Integer masterId = (Integer) opt.get("master_id");

                    // [가공] 한글 컬러명 치환
                    if ("color".equals(type) && "멀티 컬러".equals(value)) {
                        value = "Multi Color";
                    }

                    ObjectNode o = optionsJsonArray.addObject();
                    o.put("type", type);
                    o.put("value", value);
                    o.put("master_id", (masterId == null || masterId == 0) ? 1 : masterId); // 0 방지

                    // [에러 해결] 타입별로 1번부터 빈틈없이 부여
                    if ("color".equals(type)) {
                        o.put("position", colorPos++);
                    } else {
                        o.put("position", sizePos++);
                    }

                    // Variants 조립을 위한 보정 데이터 저장
                    Map<String, Object> cleanOpt = new HashMap<>(opt);
                    cleanOpt.put("value", value);
                    if ("color".equals(type)) cleanColors.add(cleanOpt);
                    else cleanSizes.add(cleanOpt);
                }

                // 4. Variants 생성
                ArrayNode variantsNode = pNode.putArray("variants");
                for (Map<String, Object> c : cleanColors) {
                    for (Map<String, Object> s : cleanSizes) {
                        ObjectNode v = variantsNode.addObject();
                        ArrayNode vOpts = v.putArray("options");
                        vOpts.addObject().put("type", "color").put("value", (String) c.get("value"));
                        vOpts.addObject().put("type", "size").put("value", (String) s.get("value"));
                        v.put("stock_type", "stock_in_hand");
                        v.put("stocks", 1);
                    }
                }

                // 5. 전송 및 로그 출력
                log.info(">>> [REQ JSON] {}", root.toString());
                HttpHeaders headers = new HttpHeaders();
                headers.setContentType(MediaType.APPLICATION_JSON);
                headers.set("X-Buyma-Personal-Shopper-Api-Access-Token", token);

                ResponseEntity<String> response = restTemplate.exchange(
                        API_URL, HttpMethod.POST, new HttpEntity<>(root.toString(), headers), String.class
                );

                log.info("<<< [BUYMA RES] Status: {}, Body: {}", response.getStatusCode(), response.getBody());
                successCount++;

            } catch (Exception e) {
                failCount++;
                log.error("!!! [REG ERROR] Ref: {} - Msg: {}", product.get("reference_number"), e.getMessage());
            }
        }

        log.info("========== [BATCH REGISTRATION END] ==========");
        return Map.of("success", successCount, "fail", failCount);
    }
}