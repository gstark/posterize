"""Tests for the config file and how it changes CLI defaults."""

import asyncio
from pathlib import Path

import pytest
from PIL import Image

from posterize.cli_impl import (
    _RaisingParser,
    _validated_cli_config,
    build_parser,
    check_arguments,
    main,
)
from posterize.config import (
    CONFIG_ENV_VAR,
    config_path,
    format_config,
    load_config,
    save_config,
)
from posterize.color_filament import (
    SAMPLE_FILAMENTS_PATH,
    ColorFilamentError,
    load_filaments,
    save_filaments,
)
from posterize.config_tui import ConfigEditor
from posterize.filament_manager import FilamentManager
from posterize.errors import InputValidationError, UserFacingError


def test_config_path_uses_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.delenv(CONFIG_ENV_VAR)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert config_path() == tmp_path / "posterize" / "config.toml"


def test_config_path_defaults_to_dot_config(monkeypatch, tmp_path):
    monkeypatch.delenv(CONFIG_ENV_VAR)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert config_path() == tmp_path / ".config" / "posterize" / "config.toml"


def test_missing_file_means_no_overrides(isolated_config):
    assert load_config(isolated_config) == {}


def test_save_and_load_round_trip(isolated_config):
    values = {
        "plate_size": 256.0,
        "max_filaments": 6,
        "base_color": "#1A1A1A",
        "include_command_metadata": True,
        "filaments": 'C:\\my "lib".json',
    }

    save_config(values, isolated_config)

    assert load_config(isolated_config) == values
    assert format_config(values).startswith("# Size and plates\nplate_size = 256.0\n")


def test_integer_values_load_as_floats_for_float_settings(isolated_config):
    isolated_config.write_text("plate_size = 256\n")

    assert load_config(isolated_config) == {"plate_size": 256.0}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("colour = 1\n", "unknown setting 'colour'"),
        ("max_filaments = 2.5\n", "max_filaments must be a whole number"),
        ("alpha_threshold = 300\n", "alpha_threshold must be at most 255"),
        ("plate_size = 0\n", "plate_size must be greater than 0"),
        ('base_color = "red"\n', "base_color must be a hex color"),
        ("dowel_holes = 1\n", "dowel_holes must be true or false"),
        ("plate_size = \n", "not valid TOML"),
    ],
)
def test_invalid_config_is_reported(isolated_config, text, message):
    isolated_config.write_text(text)

    with pytest.raises(UserFacingError, match=message):
        load_config(isolated_config)


def test_config_replaces_parser_defaults_and_flags_still_win():
    parser = build_parser(config={"max_filaments": 6, "dowel_holes": False})

    assert parser.parse_args(["a.png"]).max_filaments == 6
    assert parser.parse_args(["a.png", "--max-filaments", "3"]).max_filaments == 3
    assert parser.parse_args(["a.png"]).dowel_holes is False
    assert parser.parse_args(["a.png", "--dowel-holes"]).dowel_holes is True


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "art.png"
    Image.new("RGBA", (100, 50), (255, 0, 0, 255)).save(path)
    return path


def _validated(argv, config):
    parser = build_parser(_RaisingParser, config)
    return _validated_cli_config(parser, parser.parse_args(argv))


def test_single_plate_cancels_config_plate_size(image):
    config = {
        "plate_size": 40.0,
        "per_plate_palette": True,
        "optimize_plate_offset": True,
        "default_width": 120.0,
    }

    split = _validated([str(image), "--optimal-filament-colors"], config)
    single = _validated(
        [str(image), "--optimal-filament-colors", "--single-plate"], config
    )

    assert (split.plate_size, split.per_plate_palette) == (40.0, True)
    assert split.target_width == 120.0
    assert single.plate_size is None
    assert single.per_plate_palette is False
    assert single.optimize_plate_offset is False


def test_explicit_plate_flag_without_plate_size_is_an_error(image):
    with pytest.raises(InputValidationError, match="requires --plate-size"):
        _validated([str(image), "--optimal-filament-colors", "--per-plate-palette"], {})


def test_check_arguments_uses_config(image):
    assert check_arguments([str(image), "--optimal-filament-colors"], {}) is None
    error = check_arguments(
        [str(image), "--optimal-filament-colors"], {"plate_size": 0.01}
    )
    assert error is not None and "--plate-size invalid" in error


def test_config_command_prints_file_without_a_terminal(isolated_config, capsys):
    save_config({"max_filaments": 6}, isolated_config)

    main(["config"])

    out = capsys.readouterr().out
    assert out.startswith(f"# {isolated_config}\n")
    assert "max_filaments = 6" in out


def test_bad_config_file_stops_a_run(isolated_config):
    isolated_config.write_text("colour = 1\n")

    with pytest.raises(SystemExit, match="unknown setting"):
        main(["art.png"])


def test_editor_saves_only_changed_values(isolated_config):
    save_config({"max_filaments": 6}, isolated_config)

    async def drive() -> list[Path] | None:
        app = ConfigEditor(isolated_config, load_config(isolated_config))
        async with app.run_test(size=(140, 40)) as pilot:
            app.query_one("#plate_size").value = "256"
            app.query_one("#max_filaments").value = ""
            app.query_one("#include_command_metadata").value = True
            await pilot.pause()
            await pilot.press("ctrl+s")
        return app.return_value

    assert asyncio.run(drive()) == [isolated_config]
    assert load_config(isolated_config) == {
        "plate_size": 256.0,
        "include_command_metadata": True,
    }


def test_editor_refuses_to_save_invalid_values(isolated_config):
    async def drive() -> tuple[list[Path] | None, str]:
        app = ConfigEditor(isolated_config, {})
        async with app.run_test(size=(140, 40)) as pilot:
            app.query_one("#alpha_threshold").value = "999"
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()
            status = str(app.query_one("#status").render())
            await pilot.press("ctrl+q")
        return app.return_value, status

    saved, status = asyncio.run(drive())

    assert saved is None
    assert "Opacity cut-off must be at most 255" in status
    assert not isolated_config.exists()


def test_filaments_default_to_the_config_directory(isolated_config):
    args = build_parser().parse_args(["a.png"])

    assert args.filaments == str(isolated_config.parent / "filaments.json")


def test_missing_default_library_points_at_the_editor(image, isolated_config):
    error = check_arguments([str(image)], {})

    assert error is not None
    assert str(isolated_config.parent / "filaments.json") in error
    assert "posterize config" in error


def test_save_filaments_round_trip(tmp_path):
    library = load_filaments(SAMPLE_FILAMENTS_PATH)[:3]
    path = tmp_path / "nested" / "filaments.json"

    save_filaments(path, library)

    assert load_filaments(path) == library


def test_save_filaments_rejects_empty_and_duplicate_lists(tmp_path):
    first = load_filaments(SAMPLE_FILAMENTS_PATH)[0]

    with pytest.raises(ColorFilamentError, match="at least one"):
        save_filaments(tmp_path / "f.json", [])
    with pytest.raises(ColorFilamentError, match="duplicates"):
        save_filaments(tmp_path / "f.json", [first, first])


def _set_entry(app, name: str, hex_code: str) -> None:
    app.query_one("#filament-name").value = name
    app.query_one("#filament-hex").value = hex_code


def test_editor_manages_the_filament_library(isolated_config):
    library = isolated_config.parent / "filaments.json"

    async def drive() -> tuple[object, list[str]]:
        app = ConfigEditor(isolated_config, {})
        async with app.run_test(size=(140, 60)) as pilot:
            manager = app.query_one(FilamentManager)
            await pilot.pause()
            assert manager.filaments == []

            _set_entry(app, "", "#123456")
            manager.query_one("#filament-add").press()
            await pilot.pause()
            messages = [str(manager.query_one("#filament-status").render())]

            for name, hex_code in [
                ("Red", "ff0000"),
                ("Blue", "#0000FF"),
                ("red", "#00FF00"),
            ]:
                _set_entry(app, name, hex_code)
                manager.query_one("#filament-add").press()
                await pilot.pause()
            messages.append(str(manager.query_one("#filament-status").render()))

            _set_entry(app, "Navy", "#000080")
            manager.query_one("#filament-update").press()
            await pilot.pause()
            await pilot.press("ctrl+s")
        return app.return_value, messages

    saved, messages = asyncio.run(drive())

    assert messages == [
        "Enter a name.",
        "A filament named 'Red' already exists.",
    ]
    assert saved == [library, isolated_config]
    assert [(f.name, f.hex) for f in load_filaments(library)] == [
        ("Red", "FF0000"),
        ("Navy", "000080"),
    ]


def test_editor_starts_from_the_sample_list_and_removes(isolated_config):
    library = isolated_config.parent / "filaments.json"
    sample = load_filaments(SAMPLE_FILAMENTS_PATH)

    async def drive() -> None:
        app = ConfigEditor(isolated_config, {})
        async with app.run_test(size=(140, 60)) as pilot:
            manager = app.query_one(FilamentManager)
            manager.query_one("#use-sample").press()
            await pilot.pause()
            manager.query_one("#filament-remove").press()
            await pilot.pause()
            await pilot.press("ctrl+s")

    asyncio.run(drive())

    assert load_filaments(library) == sample[1:]


def test_editor_follows_a_new_library_path(isolated_config, tmp_path):
    other = tmp_path / "mine.json"
    save_filaments(other, load_filaments(SAMPLE_FILAMENTS_PATH)[:2])

    async def drive() -> int:
        app = ConfigEditor(isolated_config, {})
        async with app.run_test(size=(140, 60)) as pilot:
            app.query_one("#filaments").value = str(other)
            await pilot.pause()
            return len(app.query_one(FilamentManager).filaments)

    assert asyncio.run(drive()) == 2
