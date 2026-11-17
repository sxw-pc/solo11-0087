from bs4 import BeautifulSoup


def test_portal_paginates_deterministically(portal_url):
    page1 = BeautifulSoup(
        __import__("requests").get(
            portal_url + "/notices?page=1&page_size=3", timeout=5
        ).text,
        "html.parser",
    )
    ids1 = [node.get_text(strip=True) for node in page1.select("span.notice-id")]
    assert ids1 == ["TA-2026-0001", "TA-2026-0002", "TA-2026-0003"]
    assert page1.select_one("a.next-page") is not None

    page3 = BeautifulSoup(
        __import__("requests").get(
            portal_url + "/notices?page=3&page_size=3", timeout=5
        ).text,
        "html.parser",
    )
    ids3 = [node.get_text(strip=True) for node in page3.select("span.notice-id")]
    assert ids3 == ["TA-2026-0007", "TA-2026-0008"]
    assert page3.select_one("a.next-page") is None


def test_detail_is_synthetic_and_has_no_external_assets(portal_url):
    response = __import__("requests").get(portal_url + "/notices/TA-2026-0001", timeout=5)
    assert response.status_code == 200
    assert "滨河片区智慧照明设备采购项目招标公告" in response.text
    assert "全部数据均为虚构演示素材" in response.text
    assert "src=\"http" not in response.text
    assert "https://" not in response.text


def test_missing_attachment_returns_404(portal_url):
    response = __import__("requests").get(
        portal_url + "/attachments/missing_attachment.txt", timeout=5
    )
    assert response.status_code == 404


def test_health_and_invalid_page(portal_url):
    import requests

    assert requests.get(portal_url + "/health", timeout=5).json()["fixtures"] == 8
    assert requests.get(portal_url + "/notices?page=0", timeout=5).status_code == 400
    assert requests.get(portal_url + "/notices?page=99", timeout=5).status_code == 400
