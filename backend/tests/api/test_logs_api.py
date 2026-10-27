import io
import json
import logging
import zipfile

from overlaparr.api import logs

LOGS = "/api/v1/logs"


def _line(n, level, msg=None, exc=None):
    data = {"ts": f"2026-10-27T00:00:{n:02d}+00:00", "level": level, "logger": "t",
            "msg": msg or f"m{n}"}
    if exc:
        data["exc"] = exc
    return json.dumps(data, ensure_ascii=False)


def _detach_app_logging():
    """Keep the app's own log records out of the file under test."""
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, "_overlaparr", False)]:
        root.removeHandler(h)
        h.close()


def _log_path(authed):
    path = authed.app.state.log_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_logs_tail_level_filter(authed):
    path = _log_path(authed)
    _detach_app_logging()
    path.with_name(path.name + ".1").write_text(
        "\n".join([_line(1, "ERROR", "old error"), _line(2, "INFO")]) + "\n", encoding="utf-8")
    path.write_text("\n".join([
        _line(3, "DEBUG"),
        _line(4, "WARNING", "warned"),
        "not json at all",
        _line(5, "INFO", "info"),
        _line(6, "CRITICAL", "crit", exc="Traceback ..."),
        "",
    ]), encoding="utf-8")

    r = authed.get(LOGS)  # default INFO
    assert r.status_code == 200, r.text
    assert [e["msg"] for e in r.json()] == ["crit", "info", "warned", "m2", "old error"]
    first = r.json()[0]
    assert first == {"ts": "2026-10-27T00:00:06+00:00", "level": "CRITICAL", "logger": "t",
                     "msg": "crit", "exc": "Traceback ..."}
    assert r.json()[1]["exc"] is None

    assert [e["msg"] for e in authed.get(f"{LOGS}?level=WARNING").json()] == [
        "crit", "warned", "old error"]
    assert [e["msg"] for e in authed.get(f"{LOGS}?level=ERROR&limit=1").json()] == ["crit"]
    assert len(authed.get(f"{LOGS}?level=DEBUG").json()) == 6
    assert authed.get(f"{LOGS}?level=LOUD").status_code == 422
    assert authed.get(f"{LOGS}?limit=2001").status_code == 422
    assert authed.get(f"{LOGS}?limit=0").status_code == 422


def test_tail_reads_from_the_end(tmp_path):
    path = tmp_path / "big.log"
    lines = [_line(i % 60, "INFO", f"n{i}") for i in range(5000)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    got = list(logs._reverse_lines(path, block=1000))
    assert [json.loads(g)["msg"] for g in got] == [f"n{i}" for i in reversed(range(5000))]
    multibyte = tmp_path / "mb.log"
    multibyte.write_text(_line(1, "INFO", "é" * 700) + "\n" + _line(2, "INFO", "ü"),
                         encoding="utf-8")
    got = [json.loads(g)["msg"] for g in logs._reverse_lines(multibyte, block=7)]
    assert got == ["ü", "é" * 700]


def test_logs_missing_file(authed):
    path = authed.app.state.log_path
    _detach_app_logging()
    path.unlink(missing_ok=True)
    assert authed.get(LOGS).json() == []


def test_logs_download_zip(authed):
    _detach_app_logging()
    path = _log_path(authed)
    path.with_name(path.name + ".1").write_text(_line(1, "INFO") + "\n", encoding="utf-8")
    path.with_name(path.name + ".2").write_text(_line(0, "INFO") + "\n", encoding="utf-8")
    path.with_name(path.name + ".bak").write_text("nope", encoding="utf-8")
    r = authed.get(f"{LOGS}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == 'attachment; filename="overlaparr-logs.zip"'
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert sorted(z.namelist()) == ["overlaparr.log", "overlaparr.log.1", "overlaparr.log.2"]
        assert json.loads(z.read("overlaparr.log.1"))["msg"] == "m1"
