import uvicorn

from overlaparr import server


def _captured_run(monkeypatch):
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: calls.append((a, kw)))
    return calls


def test_main_runs_factory_without_uvicorn_log_config(monkeypatch):
    monkeypatch.delenv("OVERLAPARR_PORT", raising=False)
    calls = _captured_run(monkeypatch)
    server.main()
    assert calls == [(("overlaparr.main:app_factory",), {
        "factory": True, "host": "0.0.0.0", "port": 8686, "log_config": None, "proxy_headers": False,
    })]


def test_main_honours_port_env(monkeypatch):
    monkeypatch.setenv("OVERLAPARR_PORT", "9000")
    calls = _captured_run(monkeypatch)
    server.main()
    assert calls[0][1]["port"] == 9000
