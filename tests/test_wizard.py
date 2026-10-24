"""Tests for the interactive wizard and the command line it builds."""

import asyncio
import shutil

from PIL import Image

from dataclasses import replace

from posterize.cli_impl import build_parser, check_arguments, effective_defaults
from posterize.color_filament import SAMPLE_FILAMENTS_PATH
from posterize.config import ConfigValues
from posterize.wizard import PosterizeWizard
from posterize.wizard_command import WizardChoices


def make_choices(config: ConfigValues | None = None, **fields: object) -> WizardChoices:
    defaults = WizardChoices.from_defaults(effective_defaults(config or {}))
    return replace(defaults, **fields)


def test_defaults_produce_only_the_image():
    assert make_choices(image="art.png").to_argv() == ["art.png"]


def test_typed_defaults_are_not_repeated():
    choices = make_choices(
        image="art.png", base_height="10.0", max_filaments="4", base_color="000000"
    )

    assert choices.to_argv() == ["art.png"]


def test_full_model_choices():
    choices = make_choices(
        image="my art.png",
        size_by="height",
        size_mm="400",
        split=True,
        plate_size="180",
        offset_mode="manual",
        plate_offset="3, 4",
        per_plate_palette=True,
        max_filaments="6",
        base_color="#1a1a1a",
        dowel_holes=False,
        mount="edge",
        keyhole_inset="30",
        output_dir="out",
    )

    assert choices.to_argv() == [
        "my art.png",
        "--height",
        "400",
        "--plate-size",
        "180",
        "--plate-offset",
        "3,4",
        "--per-plate-palette",
        "--max-filaments",
        "6",
        "--base-color",
        "#1a1a1a",
        "--no-dowel-holes",
        "--keyhole",
        "--keyhole-inset",
        "30",
        "--output-dir",
        "out",
    ]
    assert choices.command_line().startswith("posterize 'my art.png' --height 400")


def test_plate_options_are_dropped_when_not_splitting():
    choices = make_choices(
        image="art.png", offset_mode="optimize", per_plate_palette=True
    )

    assert choices.to_argv() == ["art.png"]


def test_stud_spacing_uses_wall_preset_and_skips_inset():
    choices = make_choices(image="art.png", mount="stud", keyhole_inset="30")

    assert choices.to_argv() == ["art.png", "--wall"]


def test_no_base_drops_base_and_mount_options():
    choices = make_choices(
        image="art.png", base=False, base_mask="mask.png", mount="stud", split=True
    )

    assert choices.to_argv() == ["art.png", "--plate-size", "256", "--no-base"]


def test_colors_mode_keeps_only_color_options():
    choices = make_choices(
        image="art.png", mode="colors", split=True, max_filaments="8", base=False
    )

    assert choices.to_argv() == [
        "art.png",
        "--optimal-filament-colors",
        "--max-filaments",
        "8",
    ]


def test_every_emitted_flag_parses():
    choices = make_choices(
        image="art.png",
        size_mm="600",
        split=True,
        offset_mode="optimize",
        force_dowel_holes=True,
        color_distance="12",
        alpha_threshold="64",
        base_height="12",
        base_mask="mask.png",
        raised_height="1.5",
        mount="stud",
        keyhole_offset="5",
        keyhole_edge_clearance="4",
        build_dir="b",
        openscad="/bin/openscad",
        potrace="/bin/potrace",
        command_metadata=True,
    )

    args = build_parser().parse_args(choices.to_argv())

    assert args.width == 600
    assert args.optimize_plate_offset
    assert args.force_dowel_holes
    assert args.wall
    assert args.include_command_metadata


def test_check_arguments_reports_errors(tmp_path):
    image = tmp_path / "art.png"
    Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(image)

    assert check_arguments([str(tmp_path / "missing.png")]) is not None
    assert "--max-filaments" in (
        check_arguments([str(image), "--max-filaments", "0"]) or ""
    )
    assert check_arguments([str(image), "--optimal-filament-colors"]) is None


def test_wizard_prints_the_built_command(tmp_path, monkeypatch, isolated_config):
    Image.new("RGBA", (40, 20), (97, 198, 128, 255)).save(tmp_path / "art.png")
    shutil.copy(SAMPLE_FILAMENTS_PATH, isolated_config.parent / "filaments.json")
    monkeypatch.chdir(tmp_path)

    async def drive() -> object:
        app = PosterizeWizard({})
        async with app.run_test(size=(140, 40)) as pilot:
            app.query_one("#image").value = "art.png"
            app.action_next_step()
            app.query_one("#size_mm").value = "300"
            app.action_next_step()
            app.query_one("#split").value = True
            await pilot.pause()
            for _ in range(5):
                app.action_next_step()
            await pilot.pause()
            assert app.step == "review"
            await pilot.click("#print")
        return app.return_value

    assert asyncio.run(drive()) == (
        "print",
        ["art.png", "--width", "300", "--plate-size", "256"],
    )


def test_config_plate_size_splits_by_default():
    config: ConfigValues = {"plate_size": 256.0, "per_plate_palette": True}

    assert make_choices(config, image="art.png").to_argv() == ["art.png"]
    assert make_choices(config, image="art.png", split=False).to_argv() == [
        "art.png",
        "--single-plate",
    ]


def test_switches_are_compared_with_config_defaults():
    config: ConfigValues = {"dowel_holes": False, "include_command_metadata": True}
    choices = make_choices(
        config,
        image="art.png",
        split=True,
        dowel_holes=True,
        command_metadata=False,
    )

    assert choices.to_argv() == [
        "art.png",
        "--plate-size",
        "256",
        "--dowel-holes",
        "--no-command-metadata",
    ]
