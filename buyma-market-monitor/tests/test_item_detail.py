from pathlib import Path

from crawler.item_detail import parse_item_detail, build_item_detail_url


FIXTURE = Path(__file__).parent / "fixtures" / "item_detail_normal.html"


def test_build_item_detail_url():
    assert build_item_detail_url("133150020") == "https://www.buyma.com/item/133150020/"


def test_parse_item_detail_extracts_core_fields():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert meta["name"] and "LEMAIRE" in meta["name"]
    assert meta["brand"] == "LEMAIRE" or "LEMAIRE" in (meta["brand"] or "")
    assert meta["origin_country"] == "韓国"
    assert meta["image_url"].startswith("https://")
    assert meta["category_path"]
    assert ">" in meta["category_path"] or " > " in meta["category_path"]
    assert isinstance(meta["description"], str) and len(meta["description"]) > 0


def test_parse_item_detail_missing_fields_dont_crash():
    meta = parse_item_detail("<html><body></body></html>")
    assert meta["name"] == ""
    assert meta["brand"] is None
    assert meta["origin_country"] is None
    assert meta["image_url"] is None
    assert meta["category_path"] is None
    assert meta["description"] == ""


def test_parse_item_detail_malformed_json_ld():
    """Malformed JSON-LD must be silently skipped; no exception raised."""
    html = (
        "<html><head>"
        '<script type="application/ld+json">not valid json</script>'
        "</head><body></body></html>"
    )
    meta = parse_item_detail(html)
    # All nullable fields fall back to defaults; no exception raised
    assert meta["brand"] is None
    assert meta["origin_country"] is None
    assert meta["category_path"] is None


def test_parse_item_detail_list_typed_json_ld():
    """JSON-LD wrapped in a top-level array must be flattened correctly."""
    html = (
        "<html><head>"
        '<script type="application/ld+json">'
        '[{"@type":"ProductGroup","brand":{"name":"TestBrand"}}]'
        "</script>"
        "</head><body></body></html>"
    )
    meta = parse_item_detail(html)
    assert meta["brand"] == "TestBrand"


def test_parse_item_detail_image_urls():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert isinstance(meta["image_urls"], list)
    assert len(meta["image_urls"]) == 6
    # dedupe preserves order, no duplicates
    assert len(meta["image_urls"]) == len(set(meta["image_urls"]))
    assert meta["image_urls"][0] == "https://cdn-images.buyma.com/imgdata/item/260609/0133150020/796464791/org.jpg"
    assert all(u.startswith("https://") for u in meta["image_urls"])


def test_parse_item_detail_image_urls_empty_when_no_variants():
    meta = parse_item_detail("<html><body></body></html>")
    assert meta["image_urls"] == []


def test_parse_item_detail_view_fav_counts():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    # Discovered: ac_count='0', fav_count='0人'
    assert meta["view_count"] == 0
    assert meta["fav_count"] == 0


def test_parse_item_detail_counts_none_when_absent():
    meta = parse_item_detail("<html><body></body></html>")
    assert meta["view_count"] is None
    assert meta["fav_count"] is None
    assert meta["inquiry_count"] is None


def test_parse_item_detail_fav_count_strips_suffix():
    # fav_count text like "5人" must parse to 5
    html = '<html><body><span class="fav_count">5人</span></body></html>'
    meta = parse_item_detail(html)
    assert meta["fav_count"] == 5


def test_parse_item_detail_inquiry_count():
    html = '<html><body><p id="tabmenu_inqcnt">231</p></body></html>'
    meta = parse_item_detail(html)
    assert meta["inquiry_count"] == 231


def test_parse_item_detail_inquiry_count_none_when_absent():
    meta = parse_item_detail("<html><body></body></html>")
    assert meta["inquiry_count"] is None


def test_parse_item_detail_inquiry_count_from_fixture():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert meta["inquiry_count"] == 0


def test_parse_item_detail_brand_model_number():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert meta["brand_model_number"] == "BG0074 LF1320"


def test_parse_item_detail_tags_fixture():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert meta["tags"] == ["ユニセックス", "円高還元セール", "ロゴ"]


def test_parse_item_detail_tags_filters_ui_labels():
    html = (
        '<html><body><p>タグ</p><div>'
        '<a href="#">ユニセックス</a><a href="#">ロゴ</a>'
        '<a href="#">もっと見る</a><a href="#">閉じる</a>'
        '</div></body></html>'
    )
    meta = parse_item_detail(html)
    assert meta["tags"] == ["ユニセックス", "ロゴ"]


def test_parse_item_detail_tags_empty_when_absent():
    meta = parse_item_detail("<html><body></body></html>")
    assert meta["tags"] == []
    assert meta["brand_model_number"] is None


def test_parse_item_detail_theme_fixture():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert meta["themes"] == "円高還元セール特集！"


def test_parse_item_detail_theme_none_when_absent():
    meta = parse_item_detail("<html><body></body></html>")
    assert meta["themes"] is None


def test_parse_item_detail_listed_at_fixture():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert meta["listed_at"] == "2026-06-09T15:20:53+09:00"


def test_parse_item_detail_listed_at_none_when_absent():
    meta = parse_item_detail("<html><body></body></html>")
    assert meta["listed_at"] is None


def test_parse_item_detail_stylehaus_fixture():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert meta["has_style_haus"] is True
    # fixture has 2 youtube buttons; related articles are ignored
    assert meta["stylehaus_video_count"] == 2


def test_parse_item_detail_stylehaus_false_when_absent():
    meta = parse_item_detail("<html><body></body></html>")
    assert meta["has_style_haus"] is False
    assert meta["stylehaus_video_count"] == 0


def test_parse_item_detail_stylehaus_footer_alone_does_not_count():
    html = '''<html><body>
      <footer><a href="https://stylehaus.jp/">STYLE HAUS</a></footer>
    </body></html>'''
    meta = parse_item_detail(html)
    assert meta["has_style_haus"] is False
    assert meta["stylehaus_video_count"] == 0


def test_parse_item_detail_stylehaus_posts_alone_do_not_count():
    html = '''<html><body>
      <img class="js-stylehaus-post-img" data-article-id="1" alt="article" />
    </body></html>'''
    meta = parse_item_detail(html)
    assert meta["has_style_haus"] is False
    assert meta["stylehaus_video_count"] == 0


def test_parse_item_detail_variants_shape():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert isinstance(meta["variants"], list)
    assert len(meta["variants"]) == 1
    v = meta["variants"][0]
    assert set(v.keys()) == {
        "variant_sku", "color", "size", "price",
        "availability", "stock_min", "stock_max",
    }
    assert isinstance(v["variant_sku"], str) and v["variant_sku"]
    if v["availability"] is not None:
        assert "/" not in v["availability"]


def test_parse_variants_availability_strips_url():
    html = '''<html><body><script type="application/ld+json">
    {"@type":"ProductGroup","hasVariant":[
      {"@type":"Product","sku":"ABC","color":"RED","size":"M",
       "offers":{"price":1000,"availability":"https://schema.org/InStock"}}
    ]}
    </script></body></html>'''
    meta = parse_item_detail(html)
    v = meta["variants"][0]
    assert v["variant_sku"] == "ABC"
    assert v["color"] == "RED"
    assert v["size"] == "M"
    assert v["price"] == 1000
    assert v["availability"] == "InStock"
    assert v["stock_min"] is None
    assert v["stock_max"] is None


def test_parse_variants_stock_range():
    html = '''<html><body><script type="application/ld+json">
    {"@type":"ProductGroup","hasVariant":[
      {"@type":"Product","sku":"X","offers":{"lowPrice":500,
       "inventoryLevel":{"@type":"QuantitativeValue","minValue":7,"maxValue":15}}}
    ]}
    </script></body></html>'''
    meta = parse_item_detail(html)
    v = meta["variants"][0]
    assert v["price"] == 500
    assert v["stock_min"] == 7
    assert v["stock_max"] == 15


def test_parse_variants_empty_when_no_product_group():
    meta = parse_item_detail("<html><body></body></html>")
    assert meta["variants"] == []


def test_parse_size_chart_from_measurement_table():
    html = '''<html><body><table>
      <tr><th>サイズの名称</th><th>ウエスト</th><th>ヒップ</th></tr>
      <tr><td>S</td><td>68.0cm</td><td>110.0cm</td></tr>
      <tr><td>M</td><td>76.0cm</td><td>114.0cm</td></tr>
    </table></body></html>'''
    meta = parse_item_detail(html)
    assert meta["size_chart"] == {
        "S": {"ウエスト": "68.0cm", "ヒップ": "110.0cm"},
        "M": {"ウエスト": "76.0cm", "ヒップ": "114.0cm"},
    }


def test_size_chart_ignores_stock_matrix_table():
    # stock matrix has color-name headers and ○/× cells, NO cm — must be ignored
    html = '''<html><body><table>
      <tr><th>サイズの名称</th><th>DARK GREY</th><th>NAVY</th></tr>
      <tr><td>S</td><td>○</td><td>○</td></tr>
    </table></body></html>'''
    meta = parse_item_detail(html)
    assert meta["size_chart"] is None


def test_size_chart_ignores_shoe_stock_matrix_with_cm_sizes():
    # Shoe stock matrix: size names contain "cm", data cells are ○/× (availability),
    # NOT measurements. Must NOT be parsed as a size chart.
    html = '''<html><body><table>
      <tr><th>サイズの名称</th><th>BLACK</th></tr>
      <tr><td>23(23cm)</td><td>○</td></tr>
      <tr><td>24(24cm)</td><td>○</td></tr>
    </table></body></html>'''
    meta = parse_item_detail(html)
    assert meta["size_chart"] is None


def test_size_guide_text_present_or_none():
    html = '<html><body><h3>色・サイズ</h3><div>S=韓国95 M=韓国100</div></body></html>'
    meta = parse_item_detail(html)
    assert meta["size_guide_text"] is not None
    assert "韓国95" in meta["size_guide_text"]


def test_size_guide_none_when_absent():
    meta = parse_item_detail("<html><body></body></html>")
    assert meta["size_guide_text"] is None
    assert meta["size_chart"] is None


def test_parse_size_chart_fixture():
    # Fixture has measurement table: header [サイズの名称, 幅, 高さ, マチ],
    # one data row: ONE SIZE → 幅=68.0cm, 高さ=25.0cm, マチ=14.0cm
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    chart = meta["size_chart"]
    assert isinstance(chart, dict) and len(chart) > 0
    assert "ONE SIZE" in chart
    assert chart["ONE SIZE"]["幅"] == "68.0cm"
    assert chart["ONE SIZE"]["高さ"] == "25.0cm"
    assert chart["ONE SIZE"]["マチ"] == "14.0cm"


def test_size_guide_text_extracted_from_fixture():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert meta["size_guide_text"] is not None
    # substring observed in the real fixture section:
    assert "実寸" in meta["size_guide_text"]


def test_size_guide_h3_with_child_anchor():
    html = ('<html><body><h3>色・サイズ'
            '<a href="#">サイズガイド</a></h3>'
            '<div>実寸: 着丈70cm</div>'
            '<h3>商品コメント</h3><div>other</div></body></html>')
    meta = parse_item_detail(html)
    assert meta["size_guide_text"] is not None
    assert "着丈70cm" in meta["size_guide_text"]
    # must stop at next h3 — must NOT include "other"
    assert "other" not in meta["size_guide_text"]


def test_parse_item_detail_no_raw_meta_json():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert "raw_meta_json" not in meta


def test_parse_item_detail_description_is_full_productgroup():
    html = FIXTURE.read_text(encoding="utf-8")
    meta = parse_item_detail(html)
    assert len(meta["description"]) == 1499


def test_description_prefers_productgroup_over_og():
    html = '''<html><head>
      <meta property="og:description" content="SHORT">
      </head><body><script type="application/ld+json">
      {"@type":"ProductGroup","description":"FULL LONG BODY TEXT"}
      </script></body></html>'''
    meta = parse_item_detail(html)
    assert meta["description"] == "FULL LONG BODY TEXT"
