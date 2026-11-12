"""HTTP contract of the quoting path with a fake OrcaSlicer CLI.

Requests pass through the ApiGuard (authentication, admission, body bounds) and the
native supervisor (one OrcaSlicer process, containment), exactly as in production.
"""

import json
import os
import struct
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.native_supervisor import NativeError, NativeSupervisor

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


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


@pytest.fixture
def fake_orca(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Install a fake ORCA_BIN plus an xvfb-run stub on PATH; configure the token.

    Paths are quoted: the checkout may live in a directory with spaces."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    orca = bin_dir / "fake-orca"
    orca.write_text(f"#!/bin/sh\nexec {shell_quote(sys.executable)} {shell_quote(str(FAKE_ORCA))} \"$@\"\n")
    orca.chmod(0o755)
    xvfb = bin_dir / "xvfb-run"
    xvfb.write_text('#!/bin/sh\n[ "$1" = "-a" ] && shift\nexec "$@"\n')
    xvfb.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    for variable in ("FAKE_ORCA_SLEEP", "FAKE_ORCA_FAIL", "FAKE_ORCA_STDERR", "FAKE_ORCA_NO_SUPPORT"):
        monkeypatch.delenv(variable, raising=False)
    # The app lifespan shuts the supervisor down when a TestClient context closes, so
    # every test gets its own supervisor (the lifespan reads the module global).
    monkeypatch.setattr(main, "NATIVE", NativeSupervisor())
    monkeypatch.setattr(main, "ORCA_BIN", orca)
    monkeypatch.setattr(main, "WORKPIECE_PROJECT_API_TOKEN", TOKEN)
    monkeypatch.setattr(main, "SERVICE_COMMIT_SHA", "a" * 40)
    monkeypatch.setattr(main, "SOURCE_CODE_URL", "https://github.com/workpiece-gr/open-slicer-service")
    return orca


@pytest.fixture
def client():
    with TestClient(main.app) as client:
        yield client


def auth() -> dict:
    return {"Authorization": f"Bearer {TOKEN}"}


def slice_request(client: TestClient, stl: Path, headers: dict | None = None, **fields):
    data = {"material": "pla", "quality": "balanced", "strength": "functional", **fields}
    with stl.open("rb") as handle:
        return client.post("/v1/slice", headers=headers if headers is not None else auth(), files={"file": (stl.name, handle, "model/stl")}, data=data)


def test_healthz_is_public_and_health_needs_the_token(client: TestClient, fake_orca):
    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/").status_code == 200
    unauthenticated = client.get("/health")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["detail"]["code"] == "SERVICE_AUTH_REQUIRED"
    response = client.get("/health", headers=auth())
    assert response.status_code == 200
    body = response.json()
    assert body["slice_authenticated"] is True
    queue = body["queue"]
    assert queue["concurrency"] == 1 and queue["max_pending"] == 2 and queue["timeout_seconds"] == 30
    assert queue["busy"] is False  # GET /health is authenticated but never waits in the workload queue
    assert queue["native_busy"] is False
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
    assert body["service_commit"] == "a" * 40
    # Older health keys stay for the website readiness check.
    assert body["project_3mf"]["queue_timeout_seconds"] == main.QUEUE_TIMEOUT_SECONDS
    assert body["project_3mf"]["printers"]["ender3_generic_235"]["enabled"] is False


def test_healthz_reports_not_ready_without_a_token(client: TestClient, fake_orca, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main, "WORKPIECE_PROJECT_API_TOKEN", "")
    response = client.get("/healthz")
    assert response.status_code == 503 and response.json()["detail"]["code"] == "SERVICE_NOT_READY"


def test_slice_requires_the_service_token_before_the_body_is_read(client: TestClient, fake_orca, tmp_path: Path):
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    missing = slice_request(client, stl, headers={})
    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "SERVICE_AUTH_REQUIRED"
    assert missing.headers["www-authenticate"] == "Bearer"
    assert missing.headers.get("connection") == "close"
    wrong = slice_request(client, stl, headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    assert TOKEN not in wrong.text


def test_slice_fails_closed_when_no_token_is_configured(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main, "WORKPIECE_PROJECT_API_TOKEN", "")
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    response = slice_request(client, stl, headers={"Authorization": "Bearer anything"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "SERVICE_AUTH_UNCONFIGURED"


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
    assert summary["support_grams"] == pytest.approx(12.5 / 3, abs=0.001)
    assert summary["bounding_box_mm"] == [20.0, 20.0, 20.0]
    assert summary["triangle_count"] == 12
    assert body["extrusion"]["extruded_mm_by_feature"] == {"Outer wall": 1.0, "Sparse infill": 1.0, "Support": 0.5, "Support interface": 0.5}
    assert body["engine"]["service_commit"] == "a" * 40
    for kind in ("machine", "process", "filament"):
        assert len(body["profiles"][kind]["sha256"]) == 64
    assert body["profiles"]["process"]["identity"] == "ratrig_vcore3_300:pla:balanced:functional:process"
    assert body["effective_process"]["supports"] == "automatic" and body["effective_process"]["support_type"] == "tree(auto)"
    assert body["toolpath"]["total_extrusion_moves"] == 4 and len(body["toolpath"]["moves"]) == 4
    assert len(body["gcode_sha256"]) == 64 and len(body["source"]["sha256"]) == 64
    assert [item["code"] for item in body["warnings"]] == ["supports_used"]
    # The native worker is gone and the slot is free once the response is out.
    assert not Path(f"/proc/{main.NATIVE.last_execution['pid']}").exists()
    assert main.NATIVE._slot.locked() is False


def test_slice_without_supports_reports_zero_and_warns_near_envelope(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FAKE_ORCA_NO_SUPPORT", "1")
    monkeypatch.setenv("FAKE_ORCA_STDERR", "Warning: something to look at\nplain line")
    stl = write_box_stl(tmp_path / "big.stl", 280, 20, 20)
    body = slice_request(client, stl).json()
    assert body["summary"]["supports_used"] is False and body["summary"]["support_grams"] == 0.0
    assert [item["code"] for item in body["warnings"]] == ["near_envelope_limit", "orca_notice"]
    assert body["warnings"][1]["detail"] == "Warning: something to look at"


def read_recorded_invocations(record: Path) -> list[dict]:
    return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_slice_hands_orca_an_absolute_first_layer_line_width(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # The committed RatRig profiles say "110%"; Orca's CLI arranger would read that as 110 mm
    # per skirt loop and refuse any part longer than about 144 mm (exit -50).
    record = tmp_path / "orca-calls.jsonl"
    monkeypatch.setenv("FAKE_ORCA_RECORD", str(record))
    stl = write_box_stl(tmp_path / "bar.stl", 250, 20, 20)
    response = slice_request(client, stl)
    assert response.status_code == 200, response.text
    calls = read_recorded_invocations(record)
    assert len(calls) == 1
    assert calls[0]["process"]["initial_layer_line_width"] == "0.44"
    assert calls[0]["process"]["line_width"] == "110%"
    assert "--allow-rotations=0" in calls[0]["argv"] and "--orient" in calls[0]["argv"]
    assert [item["code"] for item in response.json()["warnings"]] == ["supports_used"]


def test_slice_retries_once_with_rotations_when_the_arranger_places_nothing(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # A rod that only fits the square bed diagonally: the axis-aligned arrange ends with
    # Orca's -50, the one retry lets the arranger rotate, and the quote says so.
    record = tmp_path / "orca-calls.jsonl"
    monkeypatch.setenv("FAKE_ORCA_RECORD", str(record))
    monkeypatch.setenv("FAKE_ORCA_NO_OBJECTS_WITHOUT_ROTATIONS", "1")
    stl = write_box_stl(tmp_path / "rod.stl", 20, 20, 350)
    response = slice_request(client, stl, preoriented="true")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["code"] for item in body["warnings"]] == ["supports_used", "arranged_with_rotation", "near_envelope_limit"]
    assert body["summary"]["filament_grams"] == 12.5
    calls = read_recorded_invocations(record)
    assert [("--allow-rotations=1" in call["argv"]) for call in calls] == [False, True]
    # Only the rotation flag changes between the two runs.
    assert [arg for arg in calls[0]["argv"] if not arg.startswith("--allow-rotations")] == [arg for arg in calls[1]["argv"] if not arg.startswith("--allow-rotations")]
    assert all("--orient" not in call["argv"] for call in calls)
    assert main.NATIVE._slot.locked() is False


def test_slice_does_not_rotate_a_broad_part_the_project_would_refuse(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # 280 x 100 x 10 passes routing standing on its edge, Orca lays it flat, and the project's
    # layout repair refuses it because only rod-like parts may rotate. The quote keeps the same
    # policy: no rotation retry, so the arranger's -50 stays a SLICE_FAILED.
    record = tmp_path / "orca-calls.jsonl"
    monkeypatch.setenv("FAKE_ORCA_RECORD", str(record))
    monkeypatch.setenv("FAKE_ORCA_NO_OBJECTS_WITHOUT_ROTATIONS", "1")
    stl = write_box_stl(tmp_path / "broad.stl", 280, 100, 10)
    response = slice_request(client, stl)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "SLICE_FAILED" and detail["engine_result"]["return_code"] == -50
    assert "pass --allow-rotations" in detail["diagnostic"]
    assert len(read_recorded_invocations(record)) == 1


def test_slice_does_not_retry_other_engine_failures(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    record = tmp_path / "orca-calls.jsonl"
    monkeypatch.setenv("FAKE_ORCA_RECORD", str(record))
    monkeypatch.setenv("FAKE_ORCA_FAIL", "1")
    stl = write_box_stl(tmp_path / "bar.stl", 250, 20, 20)
    response = slice_request(client, stl)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "SLICE_FAILED"
    assert len(read_recorded_invocations(record)) == 1


def test_orca_no_suitable_objects_detection():
    import subprocess

    failed = subprocess.CompletedProcess(args=[], returncode=206, stdout="", stderr="")
    assert main.orca_found_no_suitable_objects(failed, {"return_code": -50}) is True
    assert main.orca_found_no_suitable_objects(failed, None) is True
    assert main.orca_found_no_suitable_objects(failed, {"return_code": 1}) is False
    assert main.orca_found_no_suitable_objects(subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=""), None) is False
    assert main.orca_found_no_suitable_objects(subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""), {"return_code": -50}) is False


def test_slice_preview_none_skips_the_toolpath_sample(client: TestClient, fake_orca, tmp_path: Path):
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    body = slice_request(client, stl, preview="none").json()
    assert body["toolpath"] == {"features": [], "moves": [], "bounds": None, "total_extrusion_moves": 4, "sample_stride": 0}
    assert body["request"]["preview"] == "none"


def test_slice_refuses_a_part_no_enabled_printer_fits(client: TestClient, fake_orca, tmp_path: Path):
    stl = write_box_stl(tmp_path / "huge.stl", 400, 20, 20)
    response = slice_request(client, stl)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "NO_PRINTER_FITS" and "ratrig_vcore3_300" in detail["detail"]


def test_slice_refuses_explicit_disabled_or_unknown_printers(client: TestClient, fake_orca, tmp_path: Path):
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    disabled = slice_request(client, stl, printer="ender3_generic_235")
    assert disabled.status_code == 422 and disabled.json()["detail"]["code"] == "PRINTER_DISABLED"
    unknown = slice_request(client, stl, printer="prusa_mk4")
    assert unknown.status_code == 422 and unknown.json()["detail"]["code"] == "PRINTER_UNKNOWN"
    explicit = slice_request(client, stl, printer="ratrig_vcore3_300")
    assert explicit.status_code == 200 and explicit.json()["printer"]["selection"] == "requested"


def test_slice_parameter_media_and_source_refusals_carry_codes(client: TestClient, fake_orca, tmp_path: Path):
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


def test_slice_refuses_stl_above_the_triangle_limit(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main, "MAX_STL_TRIANGLES", 6)
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    response = slice_request(client, stl)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "SOURCE_INVALID" and "complexity" in detail["detail"]


def test_guard_rejects_a_second_file_part_and_non_multipart_bodies(client: TestClient, fake_orca, tmp_path: Path):
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    with stl.open("rb") as one, stl.open("rb") as two:
        response = client.post("/v1/slice", headers=auth(), files=[("file", ("a.stl", one, "model/stl")), ("file", ("b.stl", two, "model/stl"))])
    assert response.status_code == 413 and response.json()["detail"]["code"] == "MULTIPART_PART_LIMIT"
    urlencoded = client.post("/v1/slice", headers=auth(), data={"material": "pla"})
    assert urlencoded.status_code == 415 and urlencoded.json()["detail"]["code"] == "UNSUPPORTED_BODY_TYPE"


def test_slice_reports_native_queue_busy_when_the_reservation_is_held(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    @asynccontextmanager
    async def busy(_timeout):
        raise NativeError("NATIVE_QUEUE_BUSY", "Native processing queue is busy.")
        yield  # pragma: no cover

    monkeypatch.setattr(main.NATIVE, "reservation", busy)
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    response = slice_request(client, stl)
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "NATIVE_QUEUE_BUSY" and detail["native_code"] == "NATIVE_QUEUE_BUSY"


def test_slice_timeout_and_engine_failure_codes(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    monkeypatch.setattr(main, "SLICE_TIMEOUT_SECONDS", 1)
    monkeypatch.setenv("FAKE_ORCA_SLEEP", "3")
    timeout = slice_request(client, stl)
    assert timeout.status_code == 504
    assert timeout.json()["detail"] == {"code": "SLICE_TIMEOUT", "detail": "Native execution deadline exceeded.", "native_code": "NATIVE_TIMEOUT"}
    assert not Path(f"/proc/{main.NATIVE.last_execution['pid']}").exists()
    assert main.NATIVE.poisoned is False
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
    unauthenticated = client.get("/v1/printers")
    assert unauthenticated.status_code == 401 and unauthenticated.json()["detail"]["code"] == "SERVICE_AUTH_REQUIRED"
    response = client.get("/v1/printers", headers=auth())
    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "workpiece-fdm-printers/1"
    assert set(body["printers"]) == {"ratrig_vcore3_300", "ender3_generic_235"}
    assert body["printers"]["ender3_generic_235"]["enabled"] is False
    assert "profile_source" not in body["printers"]["ratrig_vcore3_300"]
    assert "note" not in body["printers"]["ender3_generic_235"]


def test_native_slot_and_admission_are_released_after_a_failed_request(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FAKE_ORCA_FAIL", "1")
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    assert slice_request(client, stl).status_code == 422
    assert main.NATIVE._slot.locked() is False
    guard = main.GUARD["instance"]
    assert guard.lock.locked() is False and guard.admitted == 0
    monkeypatch.delenv("FAKE_ORCA_FAIL")
    assert slice_request(client, stl).status_code == 200


def test_concurrent_requests_are_serialized_by_the_guard(client: TestClient, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Three parallel quotes: at most one OrcaSlicer process at a time, all succeed."""
    monkeypatch.setenv("FAKE_ORCA_SLEEP", "0.2")
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    results: list[int] = []
    pids: list[int] = []
    lock = threading.Lock()

    def worker():
        response = slice_request(client, stl)
        with lock:
            results.append(response.status_code)
            if response.status_code == 200:
                pids.append(main.NATIVE.last_execution["pid"])

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert results == [200, 200, 200]
    assert all(not Path(f"/proc/{pid}").exists() for pid in pids)
    guard = main.GUARD["instance"]
    assert guard.lock.locked() is False and guard.admitted == 0 and main.NATIVE._slot.locked() is False
