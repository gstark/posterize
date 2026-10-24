"""Tests for typed SCAD and 3MF assembly boundaries."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from posterize.cli_impl import main
from posterize.external_tools import ExternalTools
from posterize.project_builder import build_3mf, build_dowel_3mf
from posterize.scad_rendering import format_3mf_description, generate_scad, render_stl
from posterize.color_filament import Filament, RGBColor
from posterize.scad import (
    SCAD_CLIP_Z_SLOP_MM,
    SCAD_DOWEL_PART,
    SCAD_MODEL_FILENAME,
    SCAD_UNCLIPPED_PLATE_WIDTH,
)
from posterize.threemf import (
    ColorSlot,
    PackageMetadata,
    assign_color_slots,
    bambu_filament_payloads,
)


class RecordingPlate:
    def add_stl_group(self, entries):
        self.entries = entries


class RecordingProject:
    instances: list["RecordingProject"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        RecordingProject.instances.append(self)

    def add_plate(self, name):
        return RecordingPlate()

    def save(self, output_path):
        self.output_path = output_path


def fake_render_stl(scad_path, part, target_width, outpath, **kwargs):
    return True


def test_generate_scad_uses_centralized_template_names_and_clip_sentinels(tmp_path):
    scad_path = generate_scad(
        [str(tmp_path / "red.svg")],
        str(tmp_path / "base.svg"),
        10,
        10,
        str(tmp_path),
    )

    assert os.path.basename(scad_path) == SCAD_MODEL_FILENAME
    scad = open(scad_path).read()
    assert f"plate_w <= {SCAD_UNCLIPPED_PLATE_WIDTH}" in scad
    assert f"base_height + raised_height + {SCAD_CLIP_Z_SLOP_MM}" in scad


def test_render_stl_uses_unclipped_sentinel_by_default(tmp_path):
    out = tmp_path / "part.stl"

    def fake_openscad(cmd, **kwargs):
        out.write_bytes(b"\0" * 100)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    with patch(
        "posterize.external_tools.subprocess.run", side_effect=fake_openscad
    ) as run:
        assert (
            render_stl(
                "model.scad",
                1,
                100,
                str(out),
                tools=ExternalTools(openscad="openscad", potrace="unused"),
            )
            is True
        )

    cmd = run.call_args.args[0]
    assert f"plate_w={SCAD_UNCLIPPED_PLATE_WIDTH}" in cmd


def test_color_slot_mapping_builds_bambu_payloads():
    filament = Filament("Red", "FF0000", RGBColor(255, 0, 0))
    match = type("Match", (), {"filament": filament})()

    slots, filament_slots = assign_color_slots(
        [match], base_color="#123456", no_base=False
    )

    assert slots == [
        ColorSlot("base", "#123456FF", 1),
        ColorSlot("Red", "#FF0000FF", 2),
    ]
    assert filament_slots == {2: filament}
    assert bambu_filament_payloads(slots, filament_slots) == [
        {"settings_id": "Bambu PLA Matte @BBL P1S 0.4 nozzle", "colour": "#123456"},
        {"settings_id": "Bambu PLA Matte @BBL P1S 0.4 nozzle", "colour": "#FF0000"},
    ]


def test_format_3mf_description_omits_command_metadata_by_default():
    secret_command = [
        "posterize",
        "input.png",
        "--api-key=sk_live_representative_secret",
        "--token",
        "secret-token-value",
        "--ssh-key",
        "/Users/alice/.ssh/id_rsa",
    ]

    assert format_3mf_description(secret_command) is None
    assert PackageMetadata(command_line=secret_command).description() is None


def test_format_3mf_description_includes_command_metadata_when_explicitly_enabled():
    command = ["posterize", "input.png", "--plate-size", "200"]

    assert (
        format_3mf_description(command, include_command_metadata=True)
        == "posterize input.png --plate-size 200"
    )
    assert (
        PackageMetadata(
            command_line=command, include_command_metadata=True
        ).description()
        == "posterize input.png --plate-size 200"
    )


def test_build_dowel_3mf_uses_base_color_and_dowel_part(tmp_path):
    RecordingProject.instances.clear()
    calls = []

    def fake_dowel_renderer(scad_path, part, target_width, outpath, **kwargs):
        calls.append((scad_path, part, target_width, outpath, kwargs))
        return True

    with patch("posterize.project_builder.BambuProject", RecordingProject):
        count = build_dowel_3mf(
            scad_path="model.scad",
            output_path=str(tmp_path / "model-dowel.3mf"),
            base_color="#123456",
            target_width=200,
            tmpdir=str(tmp_path),
            stl_renderer=fake_dowel_renderer,
        )

    assert count == 1
    assert RecordingProject.instances[-1].kwargs["filaments"] == [
        {"settings_id": "Bambu PLA Matte @BBL P1S 0.4 nozzle", "colour": "#123456"}
    ]
    assert RecordingProject.instances[-1].output_path.endswith("model-dowel.3mf")
    assert calls[0][1] == SCAD_DOWEL_PART


def test_build_3mf_omits_secret_like_command_metadata_by_default(tmp_path):
    RecordingProject.instances.clear()
    secret_command = [
        "posterize",
        "input.png",
        "--api-key=sk_live_representative_secret",
        "--token",
        "secret-token-value",
    ]

    with (
        patch("posterize.project_builder.BambuProject", RecordingProject),
        patch("posterize.project_builder.render_stl", side_effect=fake_render_stl),
    ):
        count = build_3mf(
            scad_path="model.scad",
            output_path=str(tmp_path / "out.3mf"),
            color_slots=[ColorSlot("base", "#000000FF", 1)],
            target_width=200,
            plates=[(0, 0, 100, 100, 0, 0)],
            tmpdir=str(tmp_path),
            command_line=secret_command,
        )

    assert count == 1
    description = RecordingProject.instances[-1].kwargs["description"]
    assert description is None
    assert "sk_live_representative_secret" not in str(description)
    assert "secret-token-value" not in str(description)


def test_cli_disables_command_metadata_by_default():
    with (
        patch.object(sys, "argv", ["posterize", "input.png"]),
        patch("posterize.cli_impl._run_cli") as run_cli,
    ):
        main()

    args = run_cli.call_args.args[1]
    assert args.include_command_metadata is False


def test_cli_parses_command_metadata_opt_in():
    with (
        patch.object(sys, "argv", ["posterize", "input.png", "--command-metadata"]),
        patch("posterize.cli_impl._run_cli") as run_cli,
    ):
        main()

    args = run_cli.call_args.args[1]
    assert args.include_command_metadata is True
