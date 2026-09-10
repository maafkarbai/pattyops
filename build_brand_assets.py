"""Export the supplied vector mark for Tk and Windows; build-time only."""
from io import BytesIO
from pathlib import Path

from PIL import Image
from resvg_py import svg_to_bytes

ROOT = Path(__file__).resolve().parent


def main():
    destination = ROOT / "assets"
    destination.mkdir(exist_ok=True)
    image = Image.open(BytesIO(svg_to_bytes(svg_path=str(ROOT / "PattyOps-icon.svg"), width=1024))).convert("RGBA")
    # Remove empty SVG margins, then add consistent optical breathing room.
    mark = image.crop(image.getbbox())
    mark.thumbnail((448, 448), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (512, 512))
    canvas.alpha_composite(mark, ((512 - mark.width) // 2, (512 - mark.height) // 2))
    canvas.save(destination / "pattyops.png")
    canvas.save(destination / "pattyops.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    main()
