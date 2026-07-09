from crawler.item_status import classify_status_from_response, ItemStatus


def test_404_means_deleted():
    assert classify_status_from_response(404, "") == ItemStatus.DELETED


def test_200_means_sold_out():
    assert classify_status_from_response(200, "<html>...</html>") == ItemStatus.SOLD_OUT


def test_410_also_means_deleted():
    assert classify_status_from_response(410, "") == ItemStatus.DELETED


def test_unexpected_status_raises():
    import pytest
    with pytest.raises(ValueError):
        classify_status_from_response(500, "")
