set shell := ["bash", "-euo", "pipefail", "-c"]

repo := "gstark/posterize"
tap := "gstark/tap"
formula := "posterize"

default:
    @just --list

# Build, publish a GitHub release, and update the formula in the Homebrew tap
homebrew:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -n "$(git status --porcelain)" ]]; then
      echo "The working tree has changes. Commit them first." >&2
      exit 1
    fi

    base="$(/usr/bin/awk -F'"' '/^version = / { print $2; exit }' pyproject.toml)"
    version="${base}-$(/bin/date +%Y%m%d%H%M)"
    archive="build/posterize-${version}.tar.gz"

    # Build the wheel first, so a packaging error stops the release before anything is published.
    /bin/rm -rf build
    uv build --wheel --out-dir build/wheel
    git archive --format=tar.gz --prefix="posterize-${version}/" -o "$archive" HEAD
    sha="$(/usr/bin/shasum -a 256 "$archive" | /usr/bin/cut -d' ' -f1)"

    git push origin "$(git branch --show-current)"
    gh release create "v${version}" "$archive" -R {{ repo }} \
      --target "$(git rev-parse HEAD)" \
      --title "posterize $version" \
      --notes "Build of $(git rev-parse --short HEAD)."

    tap_dir="$(brew --repository {{ tap }})"
    git -C "$tap_dir" pull --ff-only
    /bin/mkdir -p "$tap_dir/Formula"
    cat > "$tap_dir/Formula/{{ formula }}.rb" <<EOF
    class Posterize < Formula
      include Language::Python::Virtualenv

      desc "Convert flat-color PNGs into multi-plate, multi-color 3MF prints"
      homepage "https://github.com/{{ repo }}"
      url "https://github.com/{{ repo }}/releases/download/v${version}/posterize-${version}.tar.gz"
      version "$version"
      sha256 "$sha"
      license "MIT"
      head "https://github.com/{{ repo }}.git", branch: "main"

      depends_on "uv" => :build
      depends_on "potrace"
      depends_on "python@3.14"

      def install
        # uv installs the versions in uv.lock. bambu3mf comes from GitHub, so it cannot be a resource block.
        virtualenv_create(libexec, "python3.14")
        ENV["UV_PROJECT_ENVIRONMENT"] = libexec
        ENV["UV_PYTHON_DOWNLOADS"] = "never"
        system "uv", "sync", "--frozen", "--no-dev", "--no-editable",
               "--python", Formula["python@3.14"].opt_bin/"python3.14"
        bin.install_symlink libexec/"bin/posterize"
      end

      def caveats
        <<~EOS
          posterize needs OpenSCAD, which Homebrew ships only as a cask:
            brew install --cask openscad@snapshot

          Run \`posterize config\` to set your defaults and filament library.
        EOS
      end

      test do
        assert_match "Convert a flat-color PNG", shell_output("#{bin}/posterize --help")

        ENV["POSTERIZE_CONFIG"] = testpath/"config.toml"
        system libexec/"bin/python", "-c", <<~PYTHON
          from PIL import Image
          image = Image.new("RGBA", (4, 2), (255, 0, 0, 255))
          image.putpixel((0, 0), (0, 0, 255, 255))
          image.save("art.png")
        PYTHON
        output = shell_output("#{bin}/posterize art.png --optimal-filament-colors")
        assert_match "#FF0000", output
        assert_match "#0000FF", output

        system libexec/"bin/python", "-c", "import bambu3mf, lib3mf, textual"
      end
    end
    EOF
    git -C "$tap_dir" add "Formula/{{ formula }}.rb"
    git -C "$tap_dir" commit -m "{{ formula }} $version"
    git -C "$tap_dir" push

    echo "Released $version. Run 'brew install {{ tap }}/{{ formula }}' to install it."
