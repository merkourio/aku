import pytest
from fastapi.testclient import TestClient

from aku import config
from aku.api.security import check
from aku.api.server import create_app
from aku.sessions.manager import Manager

from test_manager import make_session

PORT = 4587
HOST = f"127.0.0.1:{PORT}"


@pytest.fixture
def client(monkeypatch):
    secrets = {}
    monkeypatch.setattr(config, "get_secret", lambda name: secrets.get(name))
    monkeypatch.setattr(config, "set_secret", lambda name, value: secrets.__setitem__(name, value) if value else secrets.pop(name, None))
    with TestClient(create_app(Manager(), port=PORT), base_url=f"http://{HOST}") as c:
        c.secrets = secrets
        yield c


def test_guard_rules():
    ok = {"host": HOST}
    assert check("http", "GET", ok, PORT) is None
    assert check("http", "POST", ok, PORT) is None  # curl / the CLI: no Origin at all
    assert check("http", "POST", {**ok, "origin": f"http://{HOST}", "sec-fetch-site": "same-origin"}, PORT) is None
    assert check("http", "GET", {"host": "evil.example"}, PORT) == "bad host"  # DNS rebinding
    assert check("http", "GET", {"host": "127.0.0.1:9999"}, PORT) == "bad host"
    assert check("http", "POST", {**ok, "origin": "https://evil.example"}, PORT) == "bad origin"
    assert check("http", "POST", {**ok, "origin": "http://127.0.0.1:3000", "sec-fetch-site": "same-site"}, PORT) == "bad origin"
    assert check("websocket", "GET", {**ok, "origin": "https://evil.example"}, PORT) == "bad origin"
    assert check("http", "GET", {**ok, "sec-fetch-site": "cross-site"}, PORT) == "cross-site request"  # <audio src=...>
    assert check("http", "GET", {**ok, "sec-fetch-site": "none"}, PORT) is None  # typing the address


def test_foreign_requests_are_refused(client):
    assert client.get("/status", headers={"host": "evil.example"}).status_code == 403
    assert client.post("/toggle", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.get("/sessions", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.get("/status").status_code == 200


def test_foreign_websocket_is_refused(client):
    url = f"ws://{HOST}/live"  # the test client only sends our Host header with a full URL
    with pytest.raises(Exception):
        with client.websocket_connect(url, headers={"origin": "https://evil.example"}):
            pass
    with pytest.raises(Exception):
        with client.websocket_connect("ws://evil.example/live"):
            pass
    with client.websocket_connect(url, headers={"origin": f"http://{HOST}"}) as ws:
        assert ws.receive_json()["type"] == "status"


def test_settings_roundtrip_and_secrets_are_write_only(client):
    r = client.put("/settings", json={"user_name": "Taylor", "openai_api_key": "sk-test", "port": 1, "unknown": 1})
    body = r.json()
    assert body["user_name"] == "Taylor" and body["has_openai_key"] is True
    assert "sk-test" not in r.text and "openai_api_key" not in body
    assert client.secrets["openai_api_key"] == "sk-test"
    assert client.put("/settings", json={"openai_api_key": ""}).json()["has_openai_key"] is False
    assert client.put("/settings", json={"backend": "nope"}).status_code == 400
    assert client.put("/settings", json={"output_folder": "relative"}).status_code == 400
    assert client.get("/settings").json()["diarizer_in_use"] == "basic"  # no token -> built-in
    client.secrets["hf_token"] = "hf_x"
    assert client.get("/settings").json()["diarizer_in_use"] == "pyannote"


def test_vocab_endpoints(client):
    client.put("/vocab", json={"sets": [{"name": "Work", "terms": ["ExampleCo", "exampleco", " Exampleville "]}], "rules": []})
    assert client.get("/vocab").json()["sets"] == [{"name": "Work", "terms": ["ExampleCo", "Exampleville"]}]
    r = client.post("/vocab/replace-rule", json={"wrong": "Collect", "right": "ExampleCo", "set": "Work"})
    assert r.json()["rules"][0]["wrong"] == "Collect"
    assert client.post("/vocab/replace-rule", json={"wrong": "same", "right": "same"}).status_code == 400


def test_session_flow_over_http(client, tmp_path):
    s = make_session()
    assert [x["id"] for x in client.get("/sessions").json()] == [s.id]
    assert client.get("/sessions/nope").status_code == 404
    r = client.put(f"/sessions/{s.id}/speakers/S1", json={"name": "Anna", "remember_voice": True})
    assert r.json()["speakers"][0]["name"] == "Anna"
    clip = client.get(f"/sessions/{s.id}/speakers/S1/clip")
    assert clip.headers["content-type"] == "audio/wav" and clip.content[:4] == b"RIFF"
    assert client.get(f"/sessions/{s.id}/audio?start=1&end=2").content[:4] == b"RIFF"
    r = client.post(f"/sessions/{s.id}/finalize", json={"title": "Sync", "output_folder": str(tmp_path / "v")})
    assert r.json()["status"] == "finalised" and (tmp_path / "v" / "2026-11-11 1615 Sync.md").exists()
    assert client.get("/voiceprints").json()[0]["name"] == "Anna"
    assert client.delete("/voiceprints/Anna").json() == {"forgotten": 1}
    assert client.post(f"/sessions/{s.id}/stop", json={"speakers": "auto"}).status_code == 400  # not recording
    assert client.post(f"/sessions/{s.id}/improve", json={"engine": "magic"}).status_code == 400
    assert client.delete(f"/sessions/{s.id}/audio").json()["has_audio"] is False
    assert client.get(f"/sessions/{s.id}/speakers/S1/clip").status_code == 404
    assert client.delete(f"/sessions/{s.id}").json() == {"deleted": s.id}
    assert client.get("/sessions").json() == []


def test_cloud_backend_needs_a_key(client):
    r = client.post("/sessions", json={"backend": "cloud"})
    assert r.status_code == 400 and "API key" in r.json()["detail"]
