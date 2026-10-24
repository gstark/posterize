"""External tool discovery and user-facing failure tests."""

import argparse
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from posterize.cli_impl import _validated_cli_config
from posterize.color_filament import ColorFilamentError, load_filaments
from posterize.errors import ExternalToolError, OutputAssemblyError
from posterize.external_tools import (
    ExternalTools,
    ToolOverrides,
    discover_external_tools,
)
from posterize.project_builder import build_3mf
from posterize.scad_rendering import generate_svgs, render_stl
from posterize.threemf import ColorSlot

CLI = [sys.executable, "-m", "posterize"]


def make_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


def run_cli(*args):
    return subprocess.run(
        [*CLI, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_cli_validation_derives_effective_config_without_mutating_args(tmp_path):
    image = tmp_path / "image.png"
    filaments = tmp_path / "filaments.json"
    openscad = make_executable(tmp_path / "openscad")
    potrace = make_executable(tmp_path / "potrace")
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(image)
    filaments.write_text('[{"name":"red","hex":"FF0000","rgb":[255,0,0]}]')
    args = argparse.Namespace(
        image=str(image),
        width=None,
        height=None,
        default_width=500.0,
        plate_size=None,
        single_plate=False,
        base_height=10.0,
        raised_height=2.0,
        base_color="000000",
        no_base=False,
        base_mask=None,
        alpha_threshold=128,
        color_distance=30.0,
        max_filaments=4,
        filaments=str(filaments),
        per_plate_palette=False,
        dowel_holes=True,
        force_dowel_holes=False,
        keyhole=False,
        wall=True,
        keyhole_inset=25.0,
        keyhole_stud_spacing=False,
        keyhole_offset=0.0,
        keyhole_edge_clearance=3.0,
        build_dir="build",
        output_dir="output",
        openscad=str(openscad),
        potrace=str(potrace),
        include_command_metadata=True,
        optimize_plate_offset=False,
        plate_offset=None,
    )

    config = _validated_cli_config(argparse.ArgumentParser(), args)

    assert config.base_color == "#000000"
    assert config.keyhole is True
    assert config.keyhole_stud_spacing is True
    assert args.base_color == "000000"
    assert args.keyhole is False
    assert args.keyhole_stud_spacing is False


def test_tool_discovery_uses_cli_overrides(tmp_path):
    openscad = make_executable(tmp_path / "openscad-cli")
    potrace = make_executable(tmp_path / "potrace-cli")

    tools = discover_external_tools(
        ToolOverrides(openscad=str(openscad), potrace=str(potrace)), environ={}
    )

    assert tools.openscad == str(openscad)
    assert tools.potrace == str(potrace)


def test_tool_discovery_uses_environment_overrides(tmp_path):
    openscad = make_executable(tmp_path / "openscad-env")
    potrace = make_executable(tmp_path / "potrace-env")

    tools = discover_external_tools(
        environ={
            "POSTERIZE_OPENSCAD": str(openscad),
            "POSTERIZE_POTRACE": str(potrace),
        }
    )

    assert tools.openscad == str(openscad)
    assert tools.potrace == str(potrace)


def test_tool_discovery_uses_supplied_path_environment(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    openscad = make_executable(tools_dir / "openscad")
    potrace = make_executable(tools_dir / "potrace")

    tools = discover_external_tools(environ={"PATH": str(tools_dir)})

    assert tools.openscad == str(openscad)
    assert tools.potrace == str(potrace)


@pytest.mark.parametrize(
    ("overrides", "environ", "message"),
    [
        (
            ToolOverrides(openscad="", potrace=None),
            {},
            "openscad from --openscad/config must not be empty",
        ),
        (
            ToolOverrides(),
            {"POSTERIZE_OPENSCAD": ""},
            "openscad from POSTERIZE_OPENSCAD must not be empty",
        ),
    ],
)
def test_tool_discovery_rejects_empty_overrides_without_falling_back_to_path(
    tmp_path,
    overrides,
    environ,
    message,
):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    make_executable(tools_dir / "openscad")
    make_executable(tools_dir / "potrace")
    environ = {**environ, "PATH": str(tools_dir)}

    with pytest.raises(ExternalToolError, match=message):
        discover_external_tools(overrides, environ=environ)


def test_missing_tool_reports_actionable_message():
    with pytest.raises(ExternalToolError, match="Install openscad"):
        discover_external_tools(
            ToolOverrides(openscad="missing-openscad", potrace="missing-potrace"),
            environ={},
        )


def test_generate_svgs_removes_stale_output_and_reports_process_failure(tmp_path):
    pbm = tmp_path / "mask.pbm"
    pbm.write_text("P1\n1 1\n0\n")
    stale_svg = tmp_path / "mask.svg"
    stale_svg.write_text("stale")
    tools = ExternalTools(openscad="unused", potrace="potrace")

    with patch("posterize.external_tools.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=7,
            stdout="potrace stdout",
            stderr="potrace stderr",
        )

        with pytest.raises(ExternalToolError) as excinfo:
            generate_svgs([str(pbm)], str(tmp_path), tools=tools)

    assert not stale_svg.exists()
    message = str(excinfo.value)
    assert "potrace failed with exit code 7" in message
    assert "stdout:\npotrace stdout" in message
    assert "stderr:\npotrace stderr" in message


def test_generate_svgs_reports_stale_output_removal_failure(tmp_path):
    pbm = tmp_path / "mask.pbm"
    pbm.write_text("P1\n1 1\n0\n")
    stale_svg = tmp_path / "mask.svg"
    stale_svg.write_text("stale")
    tools = ExternalTools(openscad="unused", potrace="potrace")

    with (
        patch(
            "posterize.external_tools.Path.unlink",
            side_effect=PermissionError("locked"),
        ),
        patch("posterize.external_tools.subprocess.run") as run,
    ):
        with pytest.raises(ExternalToolError) as excinfo:
            generate_svgs([str(pbm)], str(tmp_path), tools=tools)

    run.assert_not_called()
    assert "cannot remove stale potrace output" in str(excinfo.value)
    assert "locked" in str(excinfo.value)


def test_generate_svgs_removes_broken_symlink_as_stale_output(tmp_path):
    pbm = tmp_path / "mask.pbm"
    pbm.write_text("P1\n1 1\n0\n")
    stale_svg = tmp_path / "mask.svg"
    try:
        stale_svg.symlink_to(tmp_path / "missing-target.svg")
    except OSError as exc:
        pytest.skip(f"symlinks are not available: {exc}")
    tools = ExternalTools(openscad="unused", potrace="potrace")

    def fake_potrace(*args, **kwargs):
        stale_svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'width="1pt" height="1pt" viewBox="0 0 1 1">'
            "<metadata>fresh</metadata></svg>"
        )
        return subprocess.CompletedProcess(args[0], 0, "", "")

    with patch("posterize.external_tools.subprocess.run", side_effect=fake_potrace):
        assert generate_svgs([str(pbm)], str(tmp_path), tools=tools) == [str(stale_svg)]

    assert not stale_svg.is_symlink()
    assert "fresh" in stale_svg.read_text()
    assert 'width="1mm"' in stale_svg.read_text()
    assert 'height="1mm"' in stale_svg.read_text()


def test_generate_svgs_reports_invalid_tool_output_without_raw_decode_error(tmp_path):
    pbm = tmp_path / "mask.pbm"
    pbm.write_text("P1\n1 1\n0\n")
    potrace = tmp_path / "potrace"
    potrace.write_text("#!/bin/sh\nprintf '\\377bad stderr' >&2\nexit 3\n")
    potrace.chmod(0o755)
    tools = ExternalTools(openscad="unused", potrace=str(potrace))

    with pytest.raises(ExternalToolError) as excinfo:
        generate_svgs([str(pbm)], str(tmp_path), tools=tools)

    message = str(excinfo.value)
    assert "potrace failed with exit code 3" in message
    assert "stderr:\n�bad stderr" in message


def test_generate_svgs_reports_missing_output_with_tool_output(tmp_path):
    pbm = tmp_path / "mask.pbm"
    pbm.write_text("P1\n1 1\n0\n")
    tools = ExternalTools(openscad="unused", potrace="potrace")

    with patch("posterize.external_tools.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="potrace stdout",
            stderr="potrace stderr",
        )

        with pytest.raises(ExternalToolError) as excinfo:
            generate_svgs([str(pbm)], str(tmp_path), tools=tools)

    message = str(excinfo.value)
    assert "potrace completed but did not create expected SVG" in message
    assert "command: potrace" in message
    assert "stdout:\npotrace stdout" in message
    assert "stderr:\npotrace stderr" in message


def test_render_stl_raises_on_openscad_failure_and_does_not_accept_stale_output(
    tmp_path,
):
    stale_stl = tmp_path / "out.stl"
    stale_stl.write_bytes(b"stale")
    tools = ExternalTools(openscad="openscad", potrace="unused")

    with patch("posterize.external_tools.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=1,
            stdout="openscad stdout",
            stderr="openscad stderr",
        )

        with pytest.raises(ExternalToolError) as excinfo:
            render_stl("model.scad", 1, 100, str(stale_stl), tools=tools)

    assert not stale_stl.exists()
    message = str(excinfo.value)
    assert "openscad failed with exit code 1" in message
    assert "openscad stdout" in message
    assert "openscad stderr" in message


def test_render_stl_treats_empty_openscad_output_as_non_fatal(tmp_path):
    out = tmp_path / "out.stl"
    tools = ExternalTools(openscad="openscad", potrace="unused")

    with patch("posterize.external_tools.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Current top level object is empty.",
        )

        assert render_stl("model.scad", 4, 100, str(out), tools=tools) is False

    assert not out.exists()


def test_render_stl_rejects_bool_return_status(tmp_path):
    out = tmp_path / "out.stl"
    tools = ExternalTools(openscad="openscad", potrace="unused")

    def fake_openscad(*args, **kwargs):
        out.write_bytes(b"\0" * 100)
        return MagicMock(returncode=False, stdout="", stderr="")

    with patch("posterize.external_tools.subprocess.run", side_effect=fake_openscad):
        with pytest.raises(ExternalToolError) as excinfo:
            render_stl("model.scad", 1, 100, str(out), tools=tools)

    assert "openscad returned invalid exit status False" in str(excinfo.value)


def test_render_stl_reports_missing_output_when_openscad_claims_success(tmp_path):
    out = tmp_path / "out.stl"
    tools = ExternalTools(openscad="openscad", potrace="unused")

    with patch("posterize.external_tools.subprocess.run") as run:
        run.return_value = MagicMock(
            returncode=0,
            stdout="openscad stdout",
            stderr="openscad stderr",
        )

        with pytest.raises(ExternalToolError) as excinfo:
            render_stl("model.scad", 1, 100, str(out), tools=tools)

    message = str(excinfo.value)
    assert "openscad completed but did not create expected STL" in message
    assert "stdout:\nopenscad stdout" in message
    assert "stderr:\nopenscad stderr" in message


def test_build_3mf_reports_project_initialization_failure_without_raw_library_error(
    tmp_path,
):
    class BrokenProject:
        def __init__(self, **kwargs):
            raise RuntimeError("bad project")

    with patch("posterize.project_builder.BambuProject", BrokenProject):
        with pytest.raises(OutputAssemblyError) as excinfo:
            build_3mf(
                "model.scad",
                str(tmp_path / "out.3mf"),
                [ColorSlot("base", "#000000FF", 1)],
                100,
                [(0, 0, 100, 100, 0, 0)],
                str(tmp_path),
            )

    message = str(excinfo.value)
    assert "failed to initialize 3MF project" in message
    assert "bad project" in message


def test_build_3mf_reports_plate_creation_failure_without_raw_library_error(tmp_path):
    class BrokenProject:
        def __init__(self, **kwargs):
            pass

        def add_plate(self, name):
            raise RuntimeError("bad plate")

    with patch("posterize.project_builder.BambuProject", BrokenProject):
        with pytest.raises(OutputAssemblyError) as excinfo:
            build_3mf(
                "model.scad",
                str(tmp_path / "out.3mf"),
                [ColorSlot("base", "#000000FF", 1)],
                100,
                [(0, 0, 100, 100, 0, 0)],
                str(tmp_path),
            )

    message = str(excinfo.value)
    assert "failed to create 3MF plate r1_c1" in message
    assert "bad plate" in message


def test_build_3mf_reports_stl_assembly_failure_without_raw_library_error(tmp_path):
    class BrokenPlate:
        def add_stl_group(self, entries):
            raise RuntimeError("bad STL")

    class BrokenProject:
        def __init__(self, **kwargs):
            pass

        def add_plate(self, name):
            return BrokenPlate()

        def save(self, output_path):
            raise AssertionError("should fail before save")

    def fake_render_stl(scad_path, part, target_width, outpath, **kwargs):
        Path(outpath).write_bytes(b"\0" * 100)
        return True

    with (
        patch("posterize.project_builder.BambuProject", BrokenProject),
        patch("posterize.project_builder.render_stl", side_effect=fake_render_stl),
    ):
        with pytest.raises(OutputAssemblyError) as excinfo:
            build_3mf(
                "model.scad",
                str(tmp_path / "out.3mf"),
                [ColorSlot("base", "#000000FF", 1)],
                100,
                [(0, 0, 100, 100, 0, 0)],
                str(tmp_path),
            )

    message = str(excinfo.value)
    assert "failed to add rendered STL files to plate r1_c1" in message
    assert "bad STL" in message


def test_cli_reports_bad_image_without_raw_traceback(tmp_path):
    bad_image = tmp_path / "bad.png"
    bad_image.write_text("not an image")

    result = run_cli(str(bad_image))

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "cannot read input image" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_reports_fully_transparent_image_without_raw_traceback(tmp_path):
    image = tmp_path / "transparent.png"
    filaments = tmp_path / "filaments.json"
    openscad = make_executable(tmp_path / "openscad")
    potrace = make_executable(tmp_path / "potrace")
    Image.new("RGBA", (8, 8), (255, 0, 0, 0)).save(image)
    filaments.write_text('[{"name":"red","hex":"FF0000","rgb":[255,0,0]}]')

    result = run_cli(
        str(image),
        "--filaments",
        str(filaments),
        "--openscad",
        str(openscad),
        "--potrace",
        str(potrace),
        "--build-dir",
        str(tmp_path / "build"),
        "--output-dir",
        str(tmp_path / "output"),
    )

    assert result.returncode != 0
    assert "contains no pixels" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_reports_malformed_filament_json_without_raw_traceback(tmp_path):
    image = tmp_path / "image.png"
    filaments = tmp_path / "filaments.json"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(image)
    filaments.write_text("{not json")

    result = run_cli(str(image), "--filaments", str(filaments))

    assert result.returncode != 0
    assert "malformed JSON" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_reports_unreadable_filament_path_without_raw_traceback(tmp_path):
    image = tmp_path / "image.png"
    filament_dir = tmp_path / "filaments-dir"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(image)
    filament_dir.mkdir()

    result = run_cli(str(image), "--filaments", str(filament_dir))

    assert result.returncode != 0
    assert "cannot read filament library" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ('[{"name":"bad/path","hex":"FF0000","rgb":[255,0,0]}]', "path separators"),
        ('[{"name":"bad\\\\path","hex":"FF0000","rgb":[255,0,0]}]', "path separators"),
        (
            '[{"name":"bad\\u0000name","hex":"FF0000","rgb":[255,0,0]}]',
            "control characters",
        ),
        (
            '[{"name":"bad\\u007fname","hex":"FF0000","rgb":[255,0,0]}]',
            "control characters",
        ),
        ('[{"name":"base","hex":"FF0000","rgb":[255,0,0]}]', "reserved"),
        (
            '[{"name":"red","hex":"FF0000","rgb":[255,0,0]}, {"name":"RED","hex":"00FF00","rgb":[0,255,0]}]',
            "duplicates",
        ),
        ('[{"name":"red","hex":"nothex","rgb":[255,0,0]}]', "hex"),
        ('[{"name":"red","hex":"FF0000","rgb":[true,0,0]}]', "rgb"),
    ],
)
def test_load_filaments_rejects_malformed_filament_fields(
    tmp_path,
    payload,
    message,
):
    filaments = tmp_path / "filaments.json"
    filaments.write_text(payload)

    with pytest.raises(ColorFilamentError) as excinfo:
        load_filaments(filaments)

    assert message in str(excinfo.value)


def test_cli_reports_invalid_base_color_without_raw_traceback(tmp_path):
    image = tmp_path / "image.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(image)

    result = run_cli(str(image), "--base-color", "red")

    assert result.returncode != 0
    assert "--base-color" in result.stderr
    assert "hexadecimal" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_reports_base_mask_size_without_raw_traceback(tmp_path):
    image = tmp_path / "image.png"
    mask = tmp_path / "mask.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(image)
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(mask)

    result = run_cli(str(image), "--base-mask", str(mask))

    assert result.returncode != 0
    assert "does not match input image size 8x8" in result.stderr
    assert "Traceback" not in result.stderr
