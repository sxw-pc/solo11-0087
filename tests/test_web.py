from tender_anchor.web import create_review_app


def test_review_console_pages_and_security_headers(crawled):
    _, paths, _ = crawled
    client = create_review_app(paths.db_path, data_dir=paths.root).test_client()
    for url in ("/", "/notices", "/review", "/runs", "/audit", "/notices/TA-2026-0001"):
        response = client.get(url)
        assert response.status_code == 200, url
        assert "标讯锚" in response.get_data(as_text=True)
        assert response.headers["Cache-Control"] == "no-store"
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]


def test_dashboard_and_detail_show_domain_data(crawled):
    _, paths, _ = crawled
    client = create_review_app(paths.db_path, data_dir=paths.root).test_client()
    dashboard = client.get("/").get_data(as_text=True)
    assert "滨河片区智慧照明设备采购项目招标公告" in dashboard
    assert "MISSING_BUDGET_AMOUNT" in dashboard
    detail = client.get("/notices/TA-2026-0001").get_data(as_text=True)
    assert "1280000.00" in detail
    assert "example_terms.txt" in detail
    assert "TA-2026-0003" in detail
    assert 'href="http' not in detail


def test_filters_review_queue_and_errors(crawled):
    _, paths, _ = crawled
    client = create_review_app(paths.db_path, data_dir=paths.root).test_client()
    tender = client.get("/notices?kind=tender").get_data(as_text=True)
    assert "共 4 条" in tender
    review = client.get("/review?status=open").get_data(as_text=True)
    assert "ATTACHMENT_FETCH_FAILED" in review
    assert client.get("/notices?kind=unknown").status_code == 400
    assert client.get("/notices/TA-404").status_code == 404
    assert client.post("/notices").status_code == 405


def test_exports_are_served_as_downloads(crawled):
    _, paths, _ = crawled
    client = create_review_app(paths.db_path, data_dir=paths.root).test_client()
    json_response = client.get("/export/notices.json")
    assert json_response.status_code == 200
    assert "attachment" in json_response.headers["Content-Disposition"]
    assert b'"notice_id": "TA-2026-0001"' in json_response.data
    csv_response = client.get("/export/notices.csv")
    assert csv_response.status_code == 200
    assert "notice_id,kind,kind_label" in csv_response.get_data(as_text=True).splitlines()[0]
    empty_data = paths.root / "empty-export-check"
    empty_data.mkdir()
    missing_client = create_review_app(paths.db_path, data_dir=empty_data).test_client()
    assert missing_client.get("/export/notices.json").status_code == 404
