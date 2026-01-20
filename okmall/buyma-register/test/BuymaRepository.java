package com.oneblocks.domain.common.repository;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;
import java.util.Map;

@Mapper
public interface BuymaRepository {
    void upsertToken(@Param("accessToken") String accessToken, @Param("refreshToken") String refreshToken);
    String selectLatestToken();
    List<Map<String, Object>> selectAceProductsAll();
    List<Map<String, Object>> selectAceOptionsByRef(String referenceNumber);
    // void updateBuymaProductId(@Param("refNum") String refNum, @Param("buymaProductId") String buymaProductId);
    void updateWebhookResult(
            @Param("refNum") String refNum,
            @Param("buymaProductId") String buymaProductId,
            @Param("status") String status,
            @Param("errorMsg") String errorMsg
    );
}
