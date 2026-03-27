#!/usr/bin/env python3
"""
html_to_pdf.py
--------------
Converts a multi-slide HTML presentation into a PDF.

Install
-------
    pip install playwright img2pdf pillow
    playwright install chromium

Usage
-----
    python html_to_pdf.py
"""

import sys
import asyncio
import shutil
from pathlib import Path

import img2pdf

# ── Config ────────────────────────────────────────────────────────────────────
HTML_FILE  = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("pres.html")
PDF_OUT    = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("pres_output/presentation.pdf")
SLIDE_W    = 1280   
SLIDE_H    = 720
SCALE      = 2      
JPEG_Q     = 95     
WAIT_LOAD  = 3000   
WAIT_SLIDE = 150    
# ──────────────────────────────────────────────────────────────────────────────

SETUP_JS = f"""
() => {{
  // Kill all CSS animations & transitions
  const s = document.createElement('style');
  s.textContent = '*, *::before, *::after {{ animation-duration: 0s !important; transition-duration: 0s !important; }}';
  document.head.appendChild(s);

  // Flush all Chart.js instances (no animation)
  if (window.Chart) {{
    Chart.defaults.animation = false;
    Object.values(Chart.instances || {{}}).forEach(c => {{
      try {{ c.update('none'); }} catch(e) {{}}
    }});
  }}

  // Hide navigation chrome (topbar, TOC, toasts, helper bar)
  document.querySelectorAll(
    '.topbar, .toc-drawer, .helper-bar, .toast, #toc-drawer, #helper-bar'
  ).forEach(el => {{ el.style.display = 'none'; }});

  // Lock deck-wrap to viewport width with no padding
  const dw = document.querySelector('.deck-wrap');
  if (dw) dw.style.cssText = 'padding:0!important;margin:0!important;width:{SLIDE_W}px!important;';

  // Lock slideStage to exact slide dimensions
  const stage = document.getElementById('slideStage');
  if (stage) stage.style.cssText =
    'width:{SLIDE_W}px!important;height:{SLIDE_H}px!important;' +
    'overflow:hidden!important;position:relative!important;margin:0!important;';
}}
"""

ACTIVATE_SLIDE_JS = """
(i) => {
  // Use the deck's own navigation function if available
  if (typeof updateSlide === 'function') {
    updateSlide(i, false);
  } else {
    document.querySelectorAll('section.slide').forEach((s, idx) => {
      s.classList.toggle('active', idx === i);
    });
  }

  // Re-flush charts after slide switch
  if (window.Chart) {
    Object.values(Chart.instances || {}).forEach(c => {
      try { c.update('none'); } catch(e) {}
    });
  }

  window.scrollTo(0, 0);
}
"""


async def capture_slides(html_path: Path, tmp_dir: Path) -> list[Path]:
    from playwright.async_api import async_playwright

    tmp_dir.mkdir(parents=True, exist_ok=True)
    slide_imgs = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--font-render-hinting=none",
            ],
        )
        page = await browser.new_page(
            viewport={"width": SLIDE_W, "height": SLIDE_H},
            device_scale_factor=SCALE,
        )

        print(f"Loading {html_path.name} ...")
        await page.goto(html_path.as_uri(), wait_until="networkidle", timeout=120_000)
        await page.wait_for_timeout(WAIT_LOAD)

        try:
            await page.evaluate("() => document.fonts.ready")
        except Exception:
            pass

        await page.evaluate(SETUP_JS)

        count = await page.locator("section.slide").count()
        print(f"Found {count} slides — capturing ...")

        for i in range(count):
            try:
                await page.evaluate(f"({ACTIVATE_SLIDE_JS})({i})")
                await page.wait_for_timeout(WAIT_SLIDE)

                out_path = tmp_dir / f"slide_{i+1:03d}.jpg"
                await page.screenshot(
                    path=str(out_path),
                    type="jpeg",
                    quality=JPEG_Q,
                    clip={"x": 0, "y": 0, "width": SLIDE_W, "height": SLIDE_H},
                )
                slide_imgs.append(out_path)
                print(f"  [{i+1:02d}/{count}] captured", end="\r")

            except Exception as exc:
                print(f"\n  [WARNING] Slide {i+1} failed ({exc}), skipping ...")

        await browser.close()

    print(f"\n✓ Captured {len(slide_imgs)} / {count} slides")
    return slide_imgs


def build_pdf(slide_imgs: list[Path], pdf_path: Path) -> None:
    if not slide_imgs:
        sys.exit("ERROR: No slide images to assemble — aborting.")

    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    page_w = img2pdf.in_to_pt(13.333)
    page_h = img2pdf.in_to_pt(7.5)
    layout = img2pdf.get_layout_fun(pagesize=(page_w, page_h))

    print(f"Building PDF ({len(slide_imgs)} pages) -> {pdf_path} ...")
    pdf_bytes = img2pdf.convert([str(p) for p in slide_imgs], layout_fun=layout)
    pdf_path.write_bytes(pdf_bytes)

    size_mb = pdf_path.stat().st_size / 1_048_576
    print(f"✓ Done: {pdf_path.name}  |  {size_mb:.1f} MB  |  {len(slide_imgs)} pages")


def main():
    html_path = HTML_FILE.resolve()
    if not html_path.exists():
        sys.exit(f"ERROR: HTML file not found: {html_path}")

    tmp_dir = PDF_OUT.parent / "_slides_tmp"

    try:
        slide_imgs = asyncio.run(capture_slides(html_path, tmp_dir))
    except RuntimeError:
        import nest_asyncio
        nest_asyncio.apply()
        slide_imgs = asyncio.get_event_loop().run_until_complete(
            capture_slides(html_path, tmp_dir)
        )

    try:
        build_pdf(slide_imgs, PDF_OUT)
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
            print("✓ Cleaned up temp files")


if __name__ == "__main__":
    main()
