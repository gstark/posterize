"""Tests for solid-base support under the color layers."""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from posterize.scad_rendering import generate_scad

CLI = [sys.executable, "-m", "posterize"]


def test_generate_scad_default_uses_solid_base(tmp_path):
    scad_path = generate_scad(
        ["color_red.svg"],
        "base.svg",
        img_w=40,
        img_h=20,
        build_dir=str(tmp_path),
    )

    scad = open(scad_path).read()

    assert "linear_extrude(height = base_height)" in scad
    assert "translate([0, 0, base_height]) {" in scad
    assert "translate([0, 0, base_height + raised_height])" in scad
    assert "top_skin_height" not in scad
    assert "base_shell_wall_thickness" not in scad
    assert "module base_inner_footprint()" not in scad
    assert "module top_skin_shape()" not in scad


def test_help_hides_removed_top_skin_flag():
    result = subprocess.run(
        [*CLI, "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert "--top-skin-height" not in result.stdout


def test_help_hides_removed_base_mask_mode_option():
    result = subprocess.run(
        [*CLI, "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert "--base-mask-mode" not in result.stdout
    assert "convex-hull" not in result.stdout
