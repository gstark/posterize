# posterize

Convert flat-color PNGs into multi-color 3MF files for Bambu Studio.

Architecture notes live in [docs/architecture.md](docs/architecture.md).

## Install

```bash
brew install gstark/tap/posterize
brew install --cask openscad@snapshot
```

posterize uses OpenSCAD and potrace. Homebrew installs potrace with the
formula, but OpenSCAD is only available as a cask.

## Commands

Run the CLI from the project environment:

```bash
uv run posterize --help
```

Run it with no arguments to open an interactive wizard. The wizard walks
through the options, previews the plates in your filament colors, and then
runs the command or prints it.

Run `posterize config` to set your own defaults, such as the plate size and
the filament library. The settings go in `~/.config/posterize/config.toml`
(`$XDG_CONFIG_HOME` and `%APPDATA%` are honored; `POSTERIZE_CONFIG` sets
another path). Flags on the command line always win over the config file.

The filament library defaults to `filaments.json` in the same folder as the
config file. The Filaments section of `posterize config` lists your filaments
and lets you add, edit, and remove them. If you have no library yet, start from
the sample list and remove the filaments you do not own.

Quality gates:

```bash
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy posterize posterize_build_backend.py
```

Apply formatting with:

```bash
uv run --locked ruff format .
```
