def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.data == b"ok"
    assert r.headers["Referrer-Policy"] == "no-referrer"
