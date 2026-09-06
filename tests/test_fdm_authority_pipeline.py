from __future__ import annotations

from pathlib import Path

import pytest

from app.fdm_authority_pipeline import (
    FdmAuthorityPipelineError,
    build_fdm_authority_pipeline,
    parse_exact_gcode_statistics,
)


def test_exact_gcode_statistics_are_parsed_from_retained_bytes():
    payload = b"""; estimated printing time (normal mode) = 1h 2m 3s\n; total filament used [g] = 12.34\n; total layer number: 456\n"""
    assert parse_exact_gcode_statistics(payload) == {
        "print_time_seconds": 3723,
        "filament_grams": 12.34,
        "layer_count": 456,
    }


def test_exact_gcode_statistics_fail_closed_when_incomplete_or_unreadable():
    with pytest.raises(FdmAuthorityPipelineError, match="print-time"):
        parse_exact_gcode_statistics(b"; total filament used [g] = 1.0\n; total layer number: 2\n")
    with pytest.raises(FdmAuthorityPipelineError, match="UTF-8"):
        parse_exact_gcode_statistics(b"\xff\xfe\xfd")


def test_pipeline_does_not_invent_machine_qualification(tmp_path: Path):
    project = tmp_path / "retained.3mf"
    project.write_bytes(b"not-reached-because-qualification-fails-first")
    with pytest.raises(FdmAuthorityPipelineError, match="qualification evidence id"):
        build_fdm_authority_pipeline(
            orca_bin=tmp_path / "orca",
            source_bytes=b"immutable-source",
            source_filename="source-original.stl",
            project_path=project,
            generation_receipt={},
            profile_bytes={"machine": b"m", "process": b"p", "filament": b"f"},
            toolchain_receipt={},
            toolchain_lock_bytes=b"lock",
            toolchain_manifest_bytes=b"manifest",
            package_inventory_bytes=b"packages",
            output_dir=tmp_path / "gcode",
            material="pla",
            quality="balanced",
            strength="functional",
            quantity=1,
            printer_key="ratrig_vcore3_300",
            machine_production_ready=True,
            machine_qualification_evidence_id="",
            validator_service_commit="a" * 40,
            pricing_service_commit="a" * 40,
        )


def test_pipeline_requires_exact_simple_source_filename(tmp_path: Path):
    project = tmp_path / "retained.3mf"
    project.write_bytes(b"project")
    with pytest.raises(FdmAuthorityPipelineError, match="simple retained filename"):
        build_fdm_authority_pipeline(
            orca_bin=tmp_path / "orca",
            source_bytes=b"immutable-source",
            source_filename="../source.stl",
            project_path=project,
            generation_receipt={},
            profile_bytes={"machine": b"m", "process": b"p", "filament": b"f"},
            toolchain_receipt={},
            toolchain_lock_bytes=b"lock",
            toolchain_manifest_bytes=b"manifest",
            package_inventory_bytes=b"packages",
            output_dir=tmp_path / "gcode",
            material="pla",
            quality="balanced",
            strength="functional",
            quantity=1,
            printer_key="ratrig_vcore3_300",
            machine_production_ready=False,
            machine_qualification_evidence_id="not-qualified",
            validator_service_commit="a" * 40,
            pricing_service_commit="a" * 40,
        )
