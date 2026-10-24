"""Tests for the per-plate orchestrator code path."""

import os
import sys
from unittest.mock import patch

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from posterize.color_filament import Filament, RGBColor
from posterize.pipeline_runner import PipelineResult, PipelineRunner
from posterize.plate_grid import PlateGrid
from posterize.threemf import ColorSlot


def run_per_plate_pipeline(*args, **kwargs):
    return PipelineRunner().run_per_plate_pipeline(*args, **kwargs)


@pytest.fixture
def sample_image(tmp_path):
    """40x20 RGBA image — left red, right green.

    At width=500mm: model is 500x250mm.
    """
    img = Image.new("RGBA", (40, 20))
    pixels = img.load()
    for y in range(20):
        for x in range(20):
            pixels[x, y] = (255, 0, 0, 255)
        for x in range(20, 40):
            pixels[x, y] = (0, 255, 0, 255)
    path = str(tmp_path / "test.png")
    img.save(path)
    return path


@pytest.fixture
def filaments():
    return [
        Filament("Red", "FF0000", RGBColor(255, 0, 0)),
        Filament("Green", "00FF00", RGBColor(0, 255, 0)),
        Filament("Blue", "0000FF", RGBColor(0, 0, 255)),
    ]


def fake_run_pipeline(config):
    """Fake pipeline that creates a dummy scad file and returns plausible results."""
    os.makedirs(config.build_dir, exist_ok=True)
    scad_path = os.path.join(config.build_dir, "model.scad")
    with open(scad_path, "w") as f:
        f.write("// fake scad")
    return PipelineResult(
        scad_path=scad_path,
        color_slots=[
            ColorSlot("base", "#000000FF", 1),
            ColorSlot("Red", "#FF0000FF", 2),
        ],
        filament_slots={2: config.filaments[0]},
        img_w=10,
        img_h=10,
    )


def fake_render_stl(scad_path, part, target_width, outpath, **kwargs):
    """Fake render that writes a minimal binary STL."""
    import struct

    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    with open(outpath, "wb") as f:
        f.write(b"\0" * 80)  # header
        f.write(struct.pack("<I", 1))  # 1 triangle
        # normal + 3 vertices + attribute
        f.write(
            struct.pack(
                "<12fH",
                0,
                0,
                1,  # normal
                0,
                0,
                0,  # v1
                1,
                0,
                0,  # v2
                0,
                1,
                0,  # v3
                0,
            )
        )  # attribute
    return True


def test_plate_center_uses_actual_cropped_pixel_bounds_not_nominal_plate_size():
    model_width = 983.9184597961495
    mm_per_px = model_width / 724
    grid = PlateGrid(
        image_width_px=724,
        image_height_px=883,
        target_width_mm=model_width,
        plate_size_mm=1200.0,
        rows=5,
        cols=4,
        model_width_mm=model_width,
        model_height_mm=1200.0,
        mm_per_px=mm_per_px,
        x_bounds_px=(0.0, 181.0, 362.0, 543.0, 724.0),
        y_bounds_px=(0.0, 176.0, 352.0, 528.0, 704.0, 883.0),
    )

    center_x, center_y = grid.plate_center_mm(1, 1)

    assert center_x == pytest.approx(-122.9898, abs=1e-3)
    assert center_y == pytest.approx(241.2231, abs=1e-3)


class TestPerPlateOrchestrator:
    @patch("posterize.pipeline_runner.render_stl", side_effect=fake_render_stl)
    @patch(
        "posterize.pipeline_runner.PipelineRunner.run_pipeline",
        side_effect=fake_run_pipeline,
    )
    def test_produces_single_row_3mf(
        self, mock_pipeline, mock_render, sample_image, filaments, tmp_path
    ):
        """plate_size=250 on 500x250mm model → 2 tiles in row 1, plus dowel file."""
        build_dir = str(tmp_path / "build")
        output_dir = str(tmp_path / "output")

        run_per_plate_pipeline(
            image_path=sample_image,
            filaments=filaments,
            build_dir=build_dir,
            output_dir=output_dir,
            plate_size=250,
            max_filaments=2,
            width=500,
        )

        output_files = sorted(f for f in os.listdir(output_dir) if f.endswith(".3mf"))
        assert output_files == ["test-dowel.3mf", "test-row-1.3mf"]

    @patch("posterize.pipeline_runner.render_stl", side_effect=fake_render_stl)
    @patch(
        "posterize.pipeline_runner.PipelineRunner.run_pipeline",
        side_effect=fake_run_pipeline,
    )
    def test_skips_fully_transparent_tiles(
        self, mock_pipeline, mock_render, filaments, tmp_path
    ):
        img = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
        pixels = img.load()
        for y in range(10, 20):
            for x in range(10, 20):
                pixels[x, y] = (255, 0, 0, 255)
        image_path = str(tmp_path / "sparse.png")
        img.save(image_path)

        run_per_plate_pipeline(
            image_path=image_path,
            filaments=filaments,
            build_dir=str(tmp_path / "build"),
            output_dir=str(tmp_path / "output"),
            plate_size=10,
            max_filaments=2,
            width=20,
        )

        assert mock_pipeline.call_count == 1
        assert "r2_c2" in os.fspath(mock_pipeline.call_args.args[0].build_dir)
        output_files = [
            f for f in os.listdir(tmp_path / "output") if f.endswith(".3mf")
        ]
        assert sorted(output_files) == ["sparse-dowel.3mf", "sparse-row-2.3mf"]

    @patch("posterize.pipeline_runner.render_stl", side_effect=fake_render_stl)
    @patch(
        "posterize.pipeline_runner.PipelineRunner.run_pipeline",
        side_effect=fake_run_pipeline,
    )
    def test_isolated_build_dirs(
        self, mock_pipeline, mock_render, sample_image, filaments, tmp_path
    ):
        """plate_size=250 → 2 tiles, each with own build subdir."""
        build_dir = str(tmp_path / "build")
        output_dir = str(tmp_path / "output")

        run_per_plate_pipeline(
            image_path=sample_image,
            filaments=filaments,
            build_dir=build_dir,
            output_dir=output_dir,
            plate_size=250,
            max_filaments=2,
            width=500,
        )

        build_dirs = [
            os.fspath(call.args[0].build_dir) for call in mock_pipeline.call_args_list
        ]
        assert len(set(build_dirs)) == 2
        assert any("r1_c1" in d for d in build_dirs)
        assert any("r1_c2" in d for d in build_dirs)

    @patch("posterize.pipeline_runner.render_stl", side_effect=fake_render_stl)
    @patch(
        "posterize.pipeline_runner.PipelineRunner.run_pipeline",
        side_effect=fake_run_pipeline,
    )
    def test_large_plate_produces_single_row_3mf(
        self, mock_pipeline, mock_render, sample_image, filaments, tmp_path
    ):
        """plate_size larger than model → 1x1 grid, 1 row output file plus dowel."""
        build_dir = str(tmp_path / "build")
        output_dir = str(tmp_path / "output")

        run_per_plate_pipeline(
            image_path=sample_image,
            filaments=filaments,
            build_dir=build_dir,
            output_dir=output_dir,
            plate_size=1000,
            max_filaments=2,
            width=500,
        )

        assert mock_pipeline.call_count == 1
        output_files = sorted(f for f in os.listdir(output_dir) if f.endswith(".3mf"))
        assert output_files == ["test-dowel.3mf", "test-row-1.3mf"]

    @patch("posterize.pipeline_runner.render_stl", side_effect=fake_render_stl)
    @patch(
        "posterize.pipeline_runner.PipelineRunner.run_pipeline",
        side_effect=fake_run_pipeline,
    )
    def test_base_color_forwarded(
        self, mock_pipeline, mock_render, sample_image, filaments, tmp_path
    ):
        build_dir = str(tmp_path / "build")
        output_dir = str(tmp_path / "output")

        run_per_plate_pipeline(
            image_path=sample_image,
            filaments=filaments,
            build_dir=build_dir,
            output_dir=output_dir,
            plate_size=1000,
            max_filaments=2,
            width=500,
            base_color="#FF00FF",
        )

        config = mock_pipeline.call_args_list[0].args[0]
        assert config.base_color == "#FF00FF"

    @patch("posterize.pipeline_runner.render_stl", side_effect=fake_render_stl)
    @patch(
        "posterize.pipeline_runner.PipelineRunner.run_pipeline",
        side_effect=fake_run_pipeline,
    )
    def test_pipeline_receives_cropped_image(
        self, mock_pipeline, mock_render, sample_image, filaments, tmp_path
    ):
        """plate_size=250 → 2 tiles, each gets a different cropped image."""
        build_dir = str(tmp_path / "build")
        output_dir = str(tmp_path / "output")

        run_per_plate_pipeline(
            image_path=sample_image,
            filaments=filaments,
            build_dir=build_dir,
            output_dir=output_dir,
            plate_size=250,
            max_filaments=2,
            width=500,
        )

        image_paths = [call.args[0].image_path for call in mock_pipeline.call_args_list]
        assert len(set(image_paths)) == 2
        for p in image_paths:
            assert os.path.isfile(p)

    @patch("posterize.pipeline_runner.render_stl", side_effect=fake_render_stl)
    @patch(
        "posterize.pipeline_runner.PipelineRunner.run_pipeline",
        side_effect=fake_run_pipeline,
    )
    def test_two_tiles_produce_two_plates_in_row_3mf(
        self, mock_pipeline, mock_render, sample_image, filaments, tmp_path
    ):
        """plate_size=250 → 2 tiles should yield 2 plates in row 1's 3MF."""
        build_dir = str(tmp_path / "build")
        output_dir = str(tmp_path / "output")

        run_per_plate_pipeline(
            image_path=sample_image,
            filaments=filaments,
            build_dir=build_dir,
            output_dir=output_dir,
            plate_size=250,
            max_filaments=2,
            width=500,
        )

        assert mock_pipeline.call_count == 2

    @patch("posterize.pipeline_runner.render_stl", side_effect=fake_render_stl)
    @patch(
        "posterize.pipeline_runner.PipelineRunner.run_pipeline",
        side_effect=fake_run_pipeline,
    )
    def test_multiple_rows_produce_multiple_row_3mfs(
        self, mock_pipeline, mock_render, sample_image, filaments, tmp_path
    ):
        """plate_size=200 on 500x250mm model → 3x2 grid, 2 row output files."""
        build_dir = str(tmp_path / "build")
        output_dir = str(tmp_path / "output")

        run_per_plate_pipeline(
            image_path=sample_image,
            filaments=filaments,
            build_dir=build_dir,
            output_dir=output_dir,
            plate_size=200,
            max_filaments=2,
            width=500,
        )

        output_files = sorted(f for f in os.listdir(output_dir) if f.endswith(".3mf"))
        assert output_files == ["test-dowel.3mf", "test-row-1.3mf", "test-row-2.3mf"]

    @patch("posterize.pipeline_runner.render_stl", side_effect=fake_render_stl)
    @patch(
        "posterize.pipeline_runner.PipelineRunner.run_pipeline",
        side_effect=fake_run_pipeline,
    )
    def test_rejects_mismatched_base_mask_before_dowel_specs(
        self, mock_pipeline, mock_render, filaments, tmp_path, capsys
    ):
        image_path = tmp_path / "source.png"
        base_mask = tmp_path / "mask.png"
        Image.new("RGBA", (80, 40), (255, 0, 0, 255)).save(image_path)
        Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(base_mask)

        with pytest.raises(ValueError, match="base mask size 10x10"):
            run_per_plate_pipeline(
                image_path=str(image_path),
                filaments=filaments,
                build_dir=str(tmp_path / "build"),
                output_dir=str(tmp_path / "output"),
                plate_size=40,
                max_filaments=2,
                width=80,
                base_mask=str(base_mask),
                force_dowel_holes=True,
            )

        assert "Adding a dowel hole" not in capsys.readouterr().out
        mock_pipeline.assert_not_called()
        mock_render.assert_not_called()

    @patch("posterize.pipeline_runner.render_stl", side_effect=fake_render_stl)
    @patch(
        "posterize.pipeline_runner.PipelineRunner.run_pipeline",
        side_effect=fake_run_pipeline,
    )
    def test_dowel_holes_are_skipped_when_shared_edge_lacks_inward_depth(
        self, mock_pipeline, mock_render, filaments, tmp_path
    ):
        img = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
        pixels = img.load()
        for y in range(20):
            for x in range(10, 35):
                pixels[x, y] = (255, 0, 0, 255)
        image_path = str(tmp_path / "thin_edge.png")
        img.save(image_path)

        run_per_plate_pipeline(
            image_path=image_path,
            filaments=filaments,
            build_dir=str(tmp_path / "build"),
            output_dir=str(tmp_path / "output"),
            plate_size=20,
            max_filaments=2,
            width=40,
        )

        base_calls = [
            call
            for call in mock_render.call_args_list
            if (call.args[1] if len(call.args) > 1 else call.kwargs.get("part")) == 1
        ]
        dowel_specs = [call.kwargs["dowel_spec"] for call in base_calls]
        assert dowel_specs[0]["east"] == []
        assert dowel_specs[1]["west"] == []

    @patch("posterize.pipeline_runner.render_stl", side_effect=fake_render_stl)
    @patch(
        "posterize.pipeline_runner.PipelineRunner.run_pipeline",
        side_effect=fake_run_pipeline,
    )
    def test_dowel_depth_uses_source_alpha(
        self, mock_pipeline, mock_render, filaments, tmp_path
    ):
        img = Image.new("RGBA", (80, 40), (0, 0, 0, 0))
        pixels = img.load()
        for y in range(40):
            for x in (0, 39, 40, 79):
                pixels[x, y] = (255, 0, 0, 255)
        image_path = str(tmp_path / "line_art.png")
        img.save(image_path)

        run_per_plate_pipeline(
            image_path=image_path,
            filaments=filaments,
            build_dir=str(tmp_path / "build"),
            output_dir=str(tmp_path / "output"),
            plate_size=40,
            max_filaments=2,
            width=80,
        )

        base_calls = [
            call
            for call in mock_render.call_args_list
            if (call.args[1] if len(call.args) > 1 else call.kwargs.get("part")) == 1
        ]
        dowel_specs = [call.kwargs["dowel_spec"] for call in base_calls]
        assert dowel_specs[0]["east"] == []
        assert dowel_specs[1]["west"] == []

    @patch("posterize.pipeline_runner.render_stl", side_effect=fake_render_stl)
    @patch(
        "posterize.pipeline_runner.PipelineRunner.run_pipeline",
        side_effect=fake_run_pipeline,
    )
    def test_force_dowel_holes_keeps_depth_filtered_per_plate_holes(
        self, mock_pipeline, mock_render, filaments, tmp_path, capsys
    ):
        img = Image.new("RGBA", (80, 40), (0, 0, 0, 0))
        pixels = img.load()
        for y in range(40):
            for x in (0, 39, 40, 79):
                pixels[x, y] = (255, 0, 0, 255)
        image_path = str(tmp_path / "line_art.png")
        img.save(image_path)

        run_per_plate_pipeline(
            image_path=image_path,
            filaments=filaments,
            build_dir=str(tmp_path / "build"),
            output_dir=str(tmp_path / "output"),
            plate_size=40,
            max_filaments=2,
            width=80,
            force_dowel_holes=True,
        )

        base_calls = [
            call
            for call in mock_render.call_args_list
            if (call.args[1] if len(call.args) > 1 else call.kwargs.get("part")) == 1
        ]
        dowel_specs = [call.kwargs["dowel_spec"] for call in base_calls]
        expected_offsets = [20.0]
        assert dowel_specs[0]["east"] == expected_offsets
        assert dowel_specs[1]["west"] == expected_offsets
        assert (
            "Adding a dowel hole at r1_c1 east / r1_c2 west offset 20mm "
            "even though the depth is only 1mm and we need 15mm"
        ) in capsys.readouterr().out
