"""HTTP contract of the quoting path with a fake OrcaSlicer CLI."""

import os
import struct
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main

TOKEN = "test-slicer-token"
FAKE_ORCA = Path(__file__).with_name("fake_orca.py")


def write_box_stl(path: Path, x: float, y: float, z: float) -> Path:
    faces = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    vertices = [(0, 0, 0), (x, 0, 0), (x, y, 0), (0, y, 0), (0, 0, z), (x, 0, z), (x, y, z), (0, y, z)]
    with path.open("wb") as handle:
        handle.write(b"Workpiece test box".ljust(80, b"\0"))
        handle.write(struct.pack("<I", len(faces)))
        for face in faces:
            points = [vertices[index] for index in face]
            handle.write(struct.pack("<12fH", 0, 0, 1, *points[0], *points[1], *points[2], 0))
    return path


@pytest.fixture
def fake_orca(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Install a fake ORCA_BIN plus an xvfb-run stub on PATH; configure the token."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    orca = bin_dir / "fake-orca"
    orca.write_text(f"#!/bin/sh\nexec {sys.executable} {FAKE_ORCA} \"$@\"\n")
    orca.chmod(0o755)
    xvfb = bin_dir / "xvfb-run"
    xvfb.write_text('#!/bin/sh\n[ "$1" = "-a" ] && shift\nexec "$@"\n')
    xvfb.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    for variable in ("FAKE_ORCA_SLEEP", "FAKE_ORCA_FAIL", "FAKE_ORCA_STDERR", "FAKE_ORCA_NO_SUPPORT"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(main, "ORCA_BIN", orca)
    monkeypatch.setattr(main, "WORKPIECE_PROJECT_API_TOKEN", TOKEN)
    monkeypatch.setattr(main, "SERVICE_COMMIT_SHA", "a" * 40)
    monkeypatch.setattr(main, "SOURCE_CODE_URL", "https://github.com/workpiece-gr/open-slicer-service")
    return orca


@pytest.fixture
def client():
    return TestClient(main.app)


def auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


def slice_request(client: TestClient, stl: Path, headers: dict | None = None, **fields):
    data = {"material": "pla", "quality": "balanced", "strength": "functional", **fields}
    with stl.open("rb") as handle:
        return client.post("/v1/slice", headers=headers if headers is not None else auth(), files={"file": (stl.name, handle, "model/stl")}, data=data)


def test_health_is_public_and_reports_queue_and_printers(client: TestClient, fake_orca):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["queue"] == {"busy": False, "waiting": 0, "timeout_seconds": main.QUEUE_TIMEOUT_SECONDS, "concurrency": 1}
    assert body["printers"]["ratrig_vcore3_300"]["enabled"] is True
    assert body["printers"]["ender3_generic_235"] == {
        "label": "Ender 3 generic / 0.4 mm (temporary)",
        "enabled": False,
        "calibrated": False,
        "temporary_generic": True,
        "envelope_mm": [235.0, 235.0, 235.0],
        "fit_rule": "axis_permutation",
        "materials": ["pla", "petg", "abs", "tpu"],
        "arrange_rotations": True,
        "layout_repair": False,
    }
    assert body["slice_api"] == {"authenticated": True}
    assert body["slice_authenticated"] is True
    assert body["service_commit"] == "a" * 40
    # Older health keys stay for the website readiness check.
    assert body["project_3mf"]["queue_timeout_seconds"] == main.QUEUE_TIMEOUT_SECONDS
    assert body["project_3mf"]["printers"]["ender3_generic_235"]["enabled"] is False


def test_slice_requires_the_service_token(client: TestClient, fake_orca, tmp_path: Path):
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    missing = slice_request(client, stl, headers={})
    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "UNAUTHORIZED"
    assert missing.headers["www-authenticate"] == "Bearer"
    wrong = slice_request(client, stl, headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401


def test_slice_fails_closed_when_no_token_is_configured(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main, "WORKPIECE_PROJECT_API_TOKEN", "")
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    response = slice_request(client, stl, headers={"Authorization": "Bearer anything"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "TOKEN_NOT_CONFIGURED"


def test_slice_routes_internally_and_reports_supports_and_provenance(client: TestClient, fake_orca, tmp_path: Path):
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    response = slice_request(client, stl)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["printer"] == {
        "key": "ratrig_vcore3_300",
        "label": "RatRig V-Core 3 300 / 0.4 mm",
        "envelope_mm": [300.0, 300.0, 300.0],
        "temporary_generic": False,
        "enabled": True,
        "calibrated": True,
        "selection": "automatic_smallest_capable",
        "candidates": ["ratrig_vcore3_300"],
    }
    assert body["request"]["printer"] == "auto" and body["request"]["preview"] == "moves"
    summary = body["summary"]
    assert summary["print_time_seconds"] == 3723 and summary["filament_grams"] == 12.5 and summary["layer_count"] == 2
    assert summary["supports_used"] is True
    # 1.0 mm support of 3.0 mm total extrusion -> one third of 12.5 g
    assert summary["support_grams"] == pytest.approx(12.5 / 3, abs=0.001)
    assert summary["bounding_box_mm"] == [20.0, 20.0, 20.0]
    assert summary["triangle_count"] == 12
    assert body["extrusion"]["extruded_mm_by_feature"] == {"Outer wall": 1.0, "Sparse infill": 1.0, "Support": 0.5, "Support interface": 0.5}
    assert body["engine"]["service_commit"] == "a" * 40
    assert body["engine"]["source"].startswith("https://github.com/")
    for kind in ("machine", "process", "filament"):
        assert len(body["profiles"][kind]["sha256"]) == 64
    assert body["profiles"]["process"]["identity"] == "ratrig_vcore3_300:pla:balanced:functional:process"
    assert body["effective_process"]["supports"] == "automatic"
    assert body["effective_process"]["support_type"] == "tree(auto)"
    assert body["toolpath"]["total_extrusion_moves"] == 4 and len(body["toolpath"]["moves"]) == 4
    assert len(body["gcode_sha256"]) == 64 and len(body["source"]["sha256"]) == 64
    assert [item["code"] for item in body["warnings"]] == ["supports_used"]


def test_slice_without_supports_reports_zero_and_warns_near_envelope(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FAKE_ORCA_NO_SUPPORT", "1")
    monkeypatch.setenv("FAKE_ORCA_STDERR", "Warning: something to look at\nplain line")
    stl = write_box_stl(tmp_path / "big.stl", 280, 20, 20)
    body = slice_request(client, stl).json()
    assert body["summary"]["supports_used"] is False and body["summary"]["support_grams"] == 0.0
    assert [item["code"] for item in body["warnings"]] == ["near_envelope_limit", "orca_notice"]
    assert body["warnings"][1]["detail"] == "Warning: something to look at"


def test_slice_preview_none_skips_the_toolpath_sample(client: TestClient, fake_orca, tmp_path: Path):
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    body = slice_request(client, stl, preview="none").json()
    assert body["toolpath"] == {"features": [], "moves": [], "bounds": None, "total_extrusion_moves": 4, "sample_stride": 0}
    assert body["request"]["preview"] == "none"
    assert body["summary"]["filament_grams"] == 12.5


def test_slice_refuses_a_part_no_enabled_printer_fits(client: TestClient, fake_orca, tmp_path: Path):
    stl = write_box_stl(tmp_path / "huge.stl", 400, 20, 20)
    response = slice_request(client, stl)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "NO_PRINTER_FITS"
    assert "ratrig_vcore3_300" in detail["detail"]


def test_slice_refuses_explicit_disabled_or_unknown_printers(client: TestClient, fake_orca, tmp_path: Path):
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    disabled = slice_request(client, stl, printer="ender3_generic_235")
    assert disabled.status_code == 422 and disabled.json()["detail"]["code"] == "PRINTER_DISABLED"
    unknown = slice_request(client, stl, printer="prusa_mk4")
    assert unknown.status_code == 422 and unknown.json()["detail"]["code"] == "PRINTER_UNKNOWN"
    explicit = slice_request(client, stl, printer="ratrig_vcore3_300")
    assert explicit.status_code == 200 and explicit.json()["printer"]["selection"] == "requested"


def test_slice_parameter_and_media_refusals_carry_codes(client: TestClient, fake_orca, tmp_path: Path):
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    assert slice_request(client, stl, material="wood").json()["detail"]["code"] == "INVALID_PARAMETER"
    assert slice_request(client, stl, preview="video").json()["detail"]["code"] == "INVALID_PARAMETER"
    with stl.open("rb") as handle:
        wrong_type = client.post("/v1/slice", headers=auth(), files={"file": ("cube.obj", handle, "model/obj")}, data={})
    assert wrong_type.status_code == 415 and wrong_type.json()["detail"]["code"] == "UNSUPPORTED_MEDIA"
    garbage = tmp_path / "garbage.stl"
    garbage.write_bytes(b"x" * 200)
    broken = slice_request(client, garbage)
    assert broken.status_code == 400 and broken.json()["detail"]["code"] == "SOURCE_INVALID"


def test_slice_reports_busy_while_another_orca_job_holds_the_slot(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main, "QUEUE_TIMEOUT_SECONDS", 1)
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    assert main.ORCA_LOCK.acquire(timeout=1)
    try:
        response = slice_request(client, stl)
    finally:
        main.ORCA_LOCK.release()
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "BUSY" and detail["retry_after_seconds"] == 1
    assert client.get("/health").json()["queue"]["busy"] is False


def test_slice_timeout_and_engine_failure_codes(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    monkeypatch.setattr(main, "SLICE_TIMEOUT_SECONDS", 1)
    monkeypatch.setenv("FAKE_ORCA_SLEEP", "3")
    timeout = slice_request(client, stl)
    assert timeout.status_code == 504 and timeout.json()["detail"]["code"] == "SLICE_TIMEOUT"
    monkeypatch.delenv("FAKE_ORCA_SLEEP")
    monkeypatch.setattr(main, "SLICE_TIMEOUT_SECONDS", 30)
    monkeypatch.setenv("FAKE_ORCA_FAIL", "1")
    failed = slice_request(client, stl)
    assert failed.status_code == 422
    detail = failed.json()["detail"]
    assert detail["code"] == "SLICE_FAILED"
    assert detail["engine_result"] == {"return_code": 1, "error_string": "fake failure"}
    assert "simulated failure" in detail["diagnostic"]


def test_slice_reports_engine_missing(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main, "ORCA_BIN", tmp_path / "missing-orca")
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    response = slice_request(client, stl)
    assert response.status_code == 503 and response.json()["detail"]["code"] == "ENGINE_MISSING"


def test_printers_endpoint_requires_token_and_hides_paths(client: TestClient, fake_orca):
    assert client.get("/v1/printers").status_code == 401
    response = client.get("/v1/printers", headers=auth())
    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "workpiece-fdm-printers/1"
    assert set(body["printers"]) == {"ratrig_vcore3_300", "ender3_generic_235"}
    assert body["printers"]["ender3_generic_235"]["enabled"] is False
    assert "profile_source" not in body["printers"]["ratrig_vcore3_300"]
    assert "note" not in body["printers"]["ender3_generic_235"]


def test_lock_is_released_after_a_failed_request(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FAKE_ORCA_FAIL", "1")
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    assert slice_request(client, stl).status_code == 422
    assert main.ORCA_LOCK.locked() is False
    monkeypatch.delenv("FAKE_ORCA_FAIL")
    assert slice_request(client, stl).status_code == 200


def test_slice_waits_for_the_slot_instead_of_overlapping(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Two requests never run Orca concurrently: the second waits and then succeeds."""
    monkeypatch.setattr(main, "QUEUE_TIMEOUT_SECONDS", 10)
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    main.ORCA_LOCK.acquire()
    results: list[int] = []

    def worker():
        results.append(slice_request(client, stl).status_code)

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline and client.get("/health").json()["queue"]["waiting"] == 0:
        time.sleep(0.05)
    assert client.get("/health").json()["queue"] == {"busy": True, "waiting": 1, "timeout_seconds": 10, "concurrency": 1}
    main.ORCA_LOCK.release()
    thread.join(timeout=15)
    assert results == [200]

