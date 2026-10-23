"""Generate JobFinder's icon in every format the app needs.

Renders a clean rounded-square mark — emerald body, white briefcase glyph
with a subtle checkmark — at 1024 px and downsamples to every common size.
Exports:

* ``assets/icon.ico``      multi-resolution Windows icon — the executable's
  icon, read by ``jobfinder.spec`` when PyInstaller builds the bundle
* ``assets/icon_256.png``  PNG, currently unused at runtime (it was the tray
  icon for ``QSystemTrayIcon``; T33 deleted the tray along with the Qt app,
  and v4 has no tray). Kept as the source PNG for the other formats.
* ``assets/iconset/*.png`` intermediate PNGs for macOS ``.icns`` packaging
* ``assets/icon.icns``     macOS icon (packaged via ``iconutil`` if available)

The browser tab icon is **not** generated here: the web UI has its own
``frontend/public/favicon.svg``, served from the built SPA.

Run once when the icon design changes::

    python assets/create_icon.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


# ── Brand colours (matched to the app's dark-theme emerald) ────────────────────

ICON_BG = (16, 185, 129, 255)  # #10b981 emerald
ICON_BG_ACCENT = (5, 150, 105, 255)  # #059669 darker emerald (subtle gradient hint)
ICON_FG = (255, 255, 255, 255)  # white glyph
ICON_CHECK = (220, 255, 235, 255)  # near-white for the checkmark

# Sizes needed for each output
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
# Each Apple iconset entry needs both the 1x and the @2x render.
# 16×16 @2x = 32, 32×32 @2x = 64, 128×128 @2x = 256, 256×256 @2x = 512, 512×512 @2x = 1024.
ICNS_ENTRIES = [
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png"),
]
TRAY_SIZE = 256

ROOT = Path(__file__).parent


# ── Drawing helpers ───────────────────────────────────────────────────────────


def _find_font(size: int) -> "ImageFont.FreeTypeFont | ImageFont.ImageFont":
    """Pick a bold sans font that ships with the host OS."""
    candidates = [
        "HelveticaNeue.ttc",
        "Helvetica.ttc",  # macOS
        "SFCompactRounded-Bold.otf",  # macOS (SF)
        "Segoe UI Bold.ttf",
        "segoeuib.ttf",  # Windows
        "DejaVuSans-Bold.ttf",  # Linux
        "Arial Bold.ttf",
        "arialbd.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _draw_icon(size: int) -> Image.Image:
    """Render the JobFinder mark — an emerald rounded square with a large
    white "Jf" monogram plus a small checkmark accent.

    The drawing is deliberately simple so every resolution renders cleanly
    and the shape still reads at 16×16.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── Rounded-square background ─────────────────────────────────────────
    radius = int(size * 0.22)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=ICON_BG)

    # ── Monogram ──────────────────────────────────────────────────────────
    # Single big "J" reads well at every size — two letters get muddy at 16px.
    font_size = int(size * 0.70)
    font = _find_font(font_size)

    # Use textbbox to properly center (accounts for descenders / ascenders)
    bbox = draw.textbbox((0, 0), "J", font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1] - int(size * 0.02)  # optical centre slightly up
    draw.text((tx, ty), "J", fill=ICON_FG, font=font)

    # ── Tiny checkmark accent at the bottom-right corner of the square ────
    # Only rendered at 48+ — it'd just be noise at 16/24/32.
    if size >= 48:
        plate_s = int(size * 0.28)
        inset = int(size * 0.08)
        plate_x = size - plate_s - inset
        plate_y = size - plate_s - inset
        draw.rounded_rectangle(
            [plate_x, plate_y, plate_x + plate_s, plate_y + plate_s],
            radius=int(plate_s * 0.28),
            fill=ICON_BG_ACCENT,
        )
        stroke = max(2, int(plate_s * 0.16))
        cx1 = plate_x + int(plate_s * 0.24)
        cy1 = plate_y + int(plate_s * 0.54)
        cx2 = plate_x + int(plate_s * 0.44)
        cy2 = plate_y + int(plate_s * 0.74)
        cx3 = plate_x + int(plate_s * 0.78)
        cy3 = plate_y + int(plate_s * 0.30)
        draw.line([(cx1, cy1), (cx2, cy2)], fill=ICON_CHECK, width=stroke)
        draw.line([(cx2, cy2), (cx3, cy3)], fill=ICON_CHECK, width=stroke)

    return img


# ── Output writers ────────────────────────────────────────────────────────────


def _render_at_sizes(sizes: list[int]) -> list[Image.Image]:
    """Render the master icon and downsample to each requested size."""
    master = _draw_icon(max(sizes))
    frames = []
    for size in sizes:
        if size == master.size[0]:
            frames.append(master.copy())
        else:
            resized = master.resize((size, size), Image.LANCZOS)
            # A touch of sharpening helps small sizes
            if size <= 48:
                resized = resized.filter(ImageFilter.UnsharpMask(radius=0.5, percent=60))
            frames.append(resized)
    return frames


def write_ico(path: Path) -> None:
    """Pillow's ICO writer takes the master image plus a list of sizes and
    creates each sub-frame internally via resize."""
    master = _draw_icon(max(ICO_SIZES))
    master.save(path, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    print(f"  ✓ {path}  ({len(ICO_SIZES)} resolutions)")


def write_tray_png(path: Path) -> None:
    master = _draw_icon(TRAY_SIZE)
    master.save(path, format="PNG")
    print(f"  ✓ {path}")


def write_icns(path: Path, iconset_dir: Path) -> None:
    """Build a macOS .icns via the system ``iconutil`` tool, if present.

    ``iconset_dir`` must have an ``.iconset`` suffix — that's how iconutil
    identifies an Apple iconset directory.
    """
    iconset_dir.mkdir(parents=True, exist_ok=True)

    unique_sizes = sorted({s for s, _ in ICNS_ENTRIES})
    size_to_img = dict(zip(unique_sizes, _render_at_sizes(unique_sizes)))
    for size, filename in ICNS_ENTRIES:
        size_to_img[size].save(iconset_dir / filename, format="PNG")

    if sys.platform != "darwin":
        print("  ! skipping .icns (iconutil only runs on macOS)")
        return
    try:
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset_dir), "-o", str(path)],
            check=True,
            capture_output=True,
        )
        print(f"  ✓ {path}  ({len(ICNS_ENTRIES)} resolutions)")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        stderr = getattr(exc, "stderr", b"").decode("utf-8", errors="replace")
        print(f"  ! iconutil failed: {exc}\n{stderr}")


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    print(f"Rendering JobFinder icon into {ROOT}/")
    write_ico(ROOT / "icon.ico")
    write_tray_png(ROOT / "icon_256.png")
    write_icns(ROOT / "icon.icns", ROOT / "icon.iconset")
    print("Done.")


if __name__ == "__main__":
    main()
