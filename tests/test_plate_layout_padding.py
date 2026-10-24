"""Tests for padding the 3MF plate layout with visual blank columns."""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from posterize.project_builder import build_3mf
from posterize.threemf import ColorSlot


class FakePlate:
    def __init__(self, name):
        self.name = name
        self.groups = []

    def add_stl_group(self, entries):
        self.groups.append(entries)


class FakeProject:
    instances: list["FakeProject"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.plates = []
        FakeProject.instances.append(self)

    def add_plate(self, name):
        plate = FakePlate(name)
        self.plates.append(plate)
        return plate

    def save(self, output_path):
        pass


def fake_render_stl(scad_path, part, target_width, outpath, **kwargs):
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    with open(outpath, "wb") as f:
        f.write(b"\0" * 100)
    return True


def test_build_3mf_omits_extra_info_plate_and_uses_description_metadata(tmp_path):
    FakeProject.instances.clear()

    with (
        patch("posterize.project_builder.BambuProject", FakeProject),
        patch("posterize.project_builder.render_stl", side_effect=fake_render_stl),
    ):
        count = build_3mf(
            scad_path="model.scad",
            output_path=str(tmp_path / "out.3mf"),
            color_slots=[ColorSlot("base", "#000000FF", 1)],
            target_width=200,
            plates=[
                (-100, -100, 100, 100, 0, 0),
                (0, -100, 100, 100, 0, 1),
                (-100, 0, 100, 100, 1, 0),
                (0, 0, 100, 100, 1, 1),
            ],
            tmpdir=str(tmp_path),
            command_line=["posterize", "input.png", "--plate-size", "200"],
            include_command_metadata=True,
        )

    assert count == 4
    project = FakeProject.instances[-1]
    assert project.kwargs.get("description") == "posterize input.png --plate-size 200"
    assert [plate.name for plate in project.plates] == [
        "r1_c1",
        "r1_c2",
        "r2_c1",
        "r2_c2",
    ]
