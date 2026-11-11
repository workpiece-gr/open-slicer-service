"""/v1/project keeps the response shape the Workpiece admin caller validates.

The website (app/fdm-project-artifact.ts) checks: printer.key/label/envelope_mm/
temporary_generic/selection, request.{material,quality,strength,quantity,supports,
orientation,arrangement,verification_requested,response_mode}, profiles.*.identity and
sha256, project.filename/media_type/bytes/sha256/inspection, verification, warnings,
and the binary-mode headers X-Workpiece-Project-SHA256 / X-Workpiece-Project-Metadata.
"""

import base64
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from tests.test_slice_api import auth, fake_orca, write_box_stl  # noqa: F401 - fixtures


@pytest.fixture
def project_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main, "ENABLE_EXPERIMENTAL_PROJECT_API", True)
    return TestClient(main.app)


def project_request(client: TestClient, stl: Path, headers=None, **fields):
    data = {"material": "pla", "quality": "balanced", "strength": "functional", "quantity": "3", "printer": "auto", **fields}
    with stl.open("rb") as handle:
        return client.post("/v1/project", headers=headers if headers is not None else auth(), files={"file": (stl.name, handle, "model/stl")}, data=data)


def test_project_json_mode_keeps_the_admin_contract_and_routes_to_the_enabled_printer(project_client, fake_orca, tmp_path: Path):
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    response = project_request(project_client, stl)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"experimental", "engine", "source", "printer", "request", "profiles", "project", "verification", "warnings"}
    assert body["experimental"] is True
    assert body["engine"]["service_commit"] == "a" * 40
    printer = body["printer"]
    assert printer["key"] == "ratrig_vcore3_300"
    assert printer["temporary_generic"] is False
    assert printer["envelope_mm"] == [300.0, 300.0, 300.0]
    assert printer["selection"] == "automatic_smallest_capable"
    assert printer["enabled"] is True and printer["calibrated"] is True and printer["candidates"] == ["ratrig_vcore3_300"]
    request = body["request"]
    assert request == {
        "material": "pla",
        "quality": "balanced",
        "strength": "functional",
        "quantity": 3,
        "supports": "automatic",
        "orientation": "orca_auto_plus_controlled_stability_rescue",
        "arrangement": "orca_auto",
        "verification_requested": True,
        "response_mode": "json",
    }
    for kind in ("machine", "process", "filament"):
        assert len(body["profiles"][kind]["sha256"]) == 64
    assert body["profiles"]["machine"]["identity"] == "ratrig_vcore3_300:machine"
    assert body["profiles"]["process"]["identity"] == "ratrig_vcore3_300:pla:balanced:functional:process"
    assert body["profiles"]["filament"]["identity"] == "ratrig_vcore3_300:pla:filament"
    project = body["project"]
    assert project["filename"] == "workpiece-production.3mf" and project["media_type"] == "model/3mf"
    assert project["inspection"]["instance_count"] == 3
    assert project["inspection"]["embedded"]["project_settings"] is True
    assert project["layout_repair"]["plate_count"] >= 1
    raw = base64.b64decode(project["base64"], validate=True)
    assert hashlib.sha256(raw).hexdigest() == project["sha256"] and len(raw) == project["bytes"]
    verification = body["verification"]
    assert verification["performed"] is True and verification["reopened_in_fresh_orca_process"] is True
    assert verification["plate_count"] == 1 and verification["total_print_time_seconds"] == 3723
    assert verification["total_filament_grams"] == 12.5
    assert isinstance(body["warnings"], list) and all(isinstance(item, str) for item in body["warnings"])
    assert not any("Ender 3 profile" in item for item in body["warnings"])


def test_project_binary_mode_headers_match_the_website_validator(project_client, fake_orca, tmp_path: Path):
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    response = project_request(project_client, stl, quantity="1", verify="false", response_mode="binary")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("model/3mf")
    metadata = json.loads(base64.urlsafe_b64decode(response.headers["x-workpiece-project-metadata"]).decode("utf-8"))
    assert metadata["schema_version"] == 1
    assert metadata["printer"]["key"] == "ratrig_vcore3_300" and metadata["printer"]["selection"] == "automatic_smallest_capable"
    assert metadata["printer"]["temporary_generic"] is False
    assert metadata["request"]["verification_requested"] is False and metadata["request"]["response_mode"] == "binary"
    assert metadata["request"]["quantity"] == 1 and metadata["request"]["supports"] == "automatic" and metadata["request"]["arrangement"] == "orca_auto"
    assert metadata["verification"]["performed"] is False
    assert metadata["project"]["inspection"]["instance_count"] == 1
    assert metadata["project"]["layout_repair"]["applied"] is True
    assert response.headers["x-workpiece-project-sha256"] == metadata["project"]["sha256"] == hashlib.sha256(response.content).hexdigest()
    assert len(response.content) == metadata["project"]["bytes"]


def test_project_refusals_carry_codes(project_client, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    assert project_request(project_client, stl, headers={}).status_code == 401
    disabled = project_request(project_client, stl, printer="ender3_generic_235")
    assert disabled.status_code == 422 and disabled.json()["detail"]["code"] == "PRINTER_DISABLED"
    huge = write_box_stl(tmp_path / "huge.stl", 400, 20, 20)
    no_fit = project_request(project_client, huge)
    assert no_fit.status_code == 422 and no_fit.json()["detail"]["code"] == "NO_PRINTER_FITS"
    bad_quantity = project_request(project_client, stl, quantity="0")
    assert bad_quantity.status_code == 422 and bad_quantity.json()["detail"]["code"] == "INVALID_PARAMETER"
    monkeypatch.setattr(main, "ENABLE_EXPERIMENTAL_PROJECT_API", False)
    off = project_request(project_client, stl)
    assert off.status_code == 404 and off.json()["detail"]["code"] == "PROJECT_DISABLED"


def test_project_and_slice_share_the_orca_slot(project_client, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(main, "QUEUE_TIMEOUT_SECONDS", 1)
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    assert main.ORCA_LOCK.acquire(timeout=1)
    try:
        response = project_request(project_client, stl)
    finally:
        main.ORCA_LOCK.release()
    assert response.status_code == 503 and response.json()["detail"]["code"] == "BUSY"


def test_project_slice_failure_is_coded(project_client, fake_orca, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FAKE_ORCA_FAIL", "1")
    stl = write_box_stl(tmp_path / "cube.stl", 20, 20, 20)
    response = project_request(project_client, stl)
    assert response.status_code == 422 and response.json()["detail"]["code"] == "SLICE_FAILED"
    assert main.ORCA_LOCK.locked() is False
