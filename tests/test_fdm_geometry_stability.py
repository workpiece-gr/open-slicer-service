import math

from app.fdm_geometry_stability import (
    POLICY_VERSION,
    resolve_controlled_orientation,
    source_fits_controlled_ratrig_diagonal,
)


def box_vertices(x: float, y: float, z: float):
    return [
        (px, py, pz)
        for px in (0.0, x)
        for py in (0.0, y)
        for pz in (0.0, z)
    ]


IDENTITY = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
RATRIG = (300.0, 300.0, 300.0)


def test_normal_low_part_is_not_rotated_diagonally():
    values, result = resolve_controlled_orientation(IDENTITY, box_vertices(250, 20, 20), RATRIG)
    assert values[:9] == IDENTITY[:9]
    assert result["policyVersion"] == POLICY_VERSION
    assert result["orientationAdjusted"] is False
    assert result["diagonal45Applied"] is False
    assert result["severity"] == "none"


def test_long_low_part_uses_45_degree_only_when_margin_needs_it():
    _values, result = resolve_controlled_orientation(IDENTITY, box_vertices(290, 20, 20), RATRIG)
    assert result["orientationAdjusted"] is True
    assert result["diagonal45Applied"] is True
    assert abs(result["appliedRotation"]["zDegrees"]) == 45
    assert result["appliedRotation"]["layAxis"] is None
    assert result["severity"] == "info"
    assert result["code"] == "controlled_diagonal_orientation"
    assert max(result["finalFootprintMm"][:2]) < 276
    assert math.isclose(result["finalFootprintMm"][2], 20.0, abs_tol=1e-6)


def test_tall_rod_is_laid_down_without_diagonal_when_that_fits():
    _values, result = resolve_controlled_orientation(IDENTITY, box_vertices(20, 20, 240), RATRIG)
    assert result["initialTallSlender"] is True
    assert result["rodLike"] is True
    assert result["orientationAdjusted"] is True
    assert result["diagonal45Applied"] is False
    assert result["finalTallSlender"] is False
    assert result["code"] == "controlled_low_orientation"
    assert math.isclose(result["finalFootprintMm"][2], 20.0, abs_tol=1e-6)
    assert max(result["finalFootprintMm"][:2]) <= 240.0 + 1e-6


def test_350mm_tall_rod_is_laid_down_on_controlled_diagonal():
    assert source_fits_controlled_ratrig_diagonal([20, 20, 350], RATRIG)
    _values, result = resolve_controlled_orientation(IDENTITY, box_vertices(20, 20, 350), RATRIG)
    assert result["initialTallSlender"] is True
    assert result["orientationAdjusted"] is True
    assert result["diagonal45Applied"] is True
    assert result["finalTallSlender"] is False
    assert abs(result["appliedRotation"]["zDegrees"]) == 45
    assert max(result["finalFootprintMm"][:2]) < 276
    assert result["finalFootprintMm"][2] <= 20.0 + 1e-6


def test_part_too_long_even_for_controlled_diagonal_stays_unsupported():
    assert not source_fits_controlled_ratrig_diagonal([20, 20, 390], RATRIG)
    _values, result = resolve_controlled_orientation(IDENTITY, box_vertices(20, 20, 390), RATRIG)
    assert result["initialTallSlender"] is True
    assert result["finalTallSlender"] is True
    assert result["severity"] == "warning"
    assert result["code"] == "tall_slender_print"
    # The resolver reports risk but does not pretend an unsupported envelope fits.
    assert result["finalFootprintMm"][2] > 300


def test_tall_plate_like_part_warns_but_is_not_forced_through_rod_rescue():
    _values, result = resolve_controlled_orientation(IDENTITY, box_vertices(20, 60, 180), RATRIG)
    assert result["initialTallSlender"] is True
    assert result["rodLike"] is False
    assert result["orientationAdjusted"] is False
    assert result["finalTallSlender"] is True
    assert result["severity"] == "warning"
    assert result["code"] == "tall_slender_print"


def test_source_diagonal_allowance_is_conservative_and_rod_only():
    assert source_fits_controlled_ratrig_diagonal([20, 20, 250], RATRIG)
    assert source_fits_controlled_ratrig_diagonal([20, 20, 290], RATRIG)
    assert source_fits_controlled_ratrig_diagonal([20, 20, 350], RATRIG)
    assert not source_fits_controlled_ratrig_diagonal([20, 200, 320], RATRIG)
