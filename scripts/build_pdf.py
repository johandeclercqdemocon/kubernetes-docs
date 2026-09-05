"""Build a single PDF of the whole book from the markdown sources.

    python scripts/build_pdf.py                 # writes <repo-name>.pdf
    python scripts/build_pdf.py --out book.pdf

Reads `README.md` for the title, then every `chapters/*.md` and `appendices/*.md`
in filename order. Filenames are numbered, so sorting them is the running order.

Needs three packages that the book itself does not:

    uv pip install weasyprint markdown pygments

They are deliberately NOT in pyproject.toml. The book's own promise is that it
runs with no dependencies; typesetting it is a separate job with separate needs,
and conflating the two would make the dependency-free claim false.

The table of contents gets real page numbers via CSS `target-counter`, which is
why this uses WeasyPrint rather than assembling pages by hand.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

try:
    import markdown
    from weasyprint import HTML
except ImportError:
    sys.exit(
        "missing typesetting dependencies. Install them into a throwaway venv:\n"
        "    uv venv /tmp/pdfenv && uv pip install --python /tmp/pdfenv/bin/python "
        "weasyprint markdown pygments\n"
        "    /tmp/pdfenv/bin/python scripts/build_pdf.py"
    )

ROOT = pathlib.Path(__file__).resolve().parent.parent

CSS = """
@page {
  size: A4;
  margin: 22mm 20mm 20mm 20mm;
  @bottom-center { content: counter(page); font: 9pt Georgia, serif; color: #666; }
  @top-center    { content: string(chaptitle); font: 8.5pt Georgia, serif; color: #888; }
}
@page :first { @bottom-center { content: normal; } @top-center { content: normal; } }

body { font: 10.5pt/1.55 Georgia, 'Times New Roman', serif; color: #1a1a1a; hyphens: auto; }

h1 { font-size: 20pt; margin: 0 0 4mm; line-height: 1.25; string-set: chaptitle content();
     page-break-before: always; page-break-after: avoid; }
h1.title-main { page-break-before: avoid; string-set: chaptitle ''; }
h2 { font-size: 13pt; margin: 7mm 0 2mm; page-break-after: avoid; }
h3 { font-size: 11pt; margin: 5mm 0 2mm; page-break-after: avoid; }
p  { margin: 0 0 2.6mm; text-align: justify; }

code { font: 9pt 'DejaVu Sans Mono', monospace; background: #f4f4f4; padding: 0 2px;
       border-radius: 2px; }
pre  { font: 8.4pt/1.4 'DejaVu Sans Mono', monospace; background: #f7f7f7;
       border-left: 2.5pt solid #ccc; padding: 2.5mm 3mm; margin: 3mm 0;
       white-space: pre-wrap; word-wrap: break-word; page-break-inside: avoid; }
pre code { background: none; padding: 0; }

blockquote { margin: 3.5mm 0; padding: 2mm 4mm; background: #f4f6f8;
             border-left: 2.5pt solid #7799bb; page-break-inside: avoid; }
blockquote p { margin: 0 0 1.5mm; }
blockquote p:last-child { margin-bottom: 0; }

table { border-collapse: collapse; width: 100%; margin: 3.5mm 0; font-size: 9pt;
        page-break-inside: avoid; }
th, td { border: 0.4pt solid #bbb; padding: 1.4mm 2mm; text-align: left; vertical-align: top; }
th { background: #eef1f4; font-weight: bold; }

ul, ol { margin: 0 0 2.6mm; padding-left: 6mm; }
li { margin-bottom: 1.2mm; }
a { color: #24457a; text-decoration: none; }
hr { border: none; border-top: 0.4pt solid #ccc; margin: 5mm 0; }
strong { font-weight: bold; }

/* --- title page --- */
.titlepage { page-break-after: always; text-align: center; padding-top: 55mm; }
.titlepage h1 { font-size: 27pt; border: none; margin-bottom: 6mm; page-break-before: avoid; }
.titlepage .sub { font-size: 12.5pt; color: #444; font-style: italic; margin-bottom: 30mm;
                  padding: 0 15mm; }
.titlepage .meta { font-size: 9.5pt; color: #777; line-height: 1.7; }

/* --- table of contents, with real page numbers --- */
.toc { page-break-after: always; }
.toc h1 { page-break-before: avoid; }
.toc ol { list-style: none; padding-left: 0; }
.toc li { margin-bottom: 1.1mm; font-size: 10pt; }
.toc a::after { content: leader('.') target-counter(attr(href), page); color: #666; }
.toc .app { margin-top: 2mm; }
"""

CODEHILITE = """
.codehilite .k, .codehilite .kn, .codehilite .kd { color: #0000aa; font-weight: bold; }
.codehilite .s, .codehilite .s1, .codehilite .s2, .codehilite .sd { color: #aa2200; }
.codehilite .c, .codehilite .c1, .codehilite .cm { color: #667788; font-style: italic; }
.codehilite .nf, .codehilite .nc { color: #005555; font-weight: bold; }
.codehilite .mi, .codehilite .mf { color: #aa5500; }
.codehilite .o, .codehilite .ow { color: #333333; }
.codehilite .bp, .codehilite .nb { color: #336699; }
"""


def read_title(base: pathlib.Path) -> tuple[str, str]:
    """Title and subtitle from README.md: the H1, then the first paragraph."""
    readme = (base / "README.md").read_text()
    title = next((l[2:].strip() for l in readme.splitlines() if l.startswith("# ")), ROOT.name)
    subtitle = ""
    seen_h1 = False
    for line in readme.splitlines():
        if line.startswith("# "):
            seen_h1 = True
            continue
        if seen_h1 and line.strip() and not line.startswith(("#", "!", "[")):
            subtitle = line.strip()
            break
    return title, subtitle


def source_files(base: pathlib.Path) -> list[pathlib.Path]:
    """Chapters then appendices, each in filename order.

    `base` is the repo root for the English book and `nl/` for the Dutch one;
    the layout below it is identical, so one builder serves both.
    """
    return sorted((base / "chapters").glob("*.md")) + sorted((base / "appendices").glob("*.md"))


def strip_links(text: str) -> str:
    """Turn relative markdown links into plain text, leaving images alone.

    In a single-file PDF a link to `../appendices/e-solutions.md` resolves to
    nothing. Cross-references read fine as prose, and the section titles are in
    the table of contents.

    The negative lookbehind is load-bearing: without it `![alt](fig.svg)` matches
    as a link and becomes `!alt`, silently deleting every figure from the PDF
    while leaving a stray exclamation mark behind.
    """
    def replace(match: re.Match) -> str:
        label, target = match.group(1), match.group(2)
        return label if not target.startswith(("http://", "https://", "mailto:")) else match.group(0)

    return re.sub(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)", replace, text)


def rebase_images(text: str, source: pathlib.Path) -> str:
    """Rewrite image paths to be relative to the repo root.

    Chapters live in `chapters/` and refer to `../figures/x.svg`, which is right
    for GitHub. The PDF concatenates every file under one `base_url` of the repo
    root, where `../figures/` points *outside* the repo.

    WeasyPrint drops a missing image silently -- no warning, no error, no gap in
    the page. The first build of this book shipped with every figure absent and
    a PDF that looked entirely fine, which is why this function exists and why
    `build_figures.py` output is checked into the tree rather than assumed.
    """
    def replace(match: re.Match) -> str:
        alt, target = match.group(1), match.group(2)
        if target.startswith(("http://", "https://", "data:")):
            return match.group(0)
        resolved = (source.parent / target).resolve()
        try:
            return f"![{alt}]({resolved.relative_to(ROOT)})"
        except ValueError:
            return match.group(0)

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace, text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Typeset the book as a single PDF.")
    parser.add_argument("--out", default=None, help="output path (default <repo-name>.pdf)")
    parser.add_argument("--source", default=".",
                        help="directory holding README.md, chapters/ and appendices/ "
                             "(default '.'; use 'nl' for the Dutch translation)")
    args = parser.parse_args()

    base = (ROOT / args.source).resolve()
    title, subtitle = read_title(base)
    files = source_files(base)
    if not files:
        sys.exit(f"no chapters/*.md or appendices/*.md under {base}")

    missing = [
        str(p) for f in files
        for p in [(f.parent / m).resolve()
                  for m in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", f.read_text())
                  if not m.startswith(("http://", "https://", "data:"))]
        if not p.exists()
    ]
    if missing:
        sys.exit("figures referenced but not on disk (run scripts/build_figures.py):\n  "
                 + "\n  ".join(missing))

    md = markdown.Markdown(extensions=["fenced_code", "tables", "codehilite", "attr_list"],
                           extension_configs={"codehilite": {"guess_lang": False}})

    sections, toc = [], []
    for i, path in enumerate(files):
        text = rebase_images(strip_links(path.read_text()), path)
        heading = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), path.stem)
        anchor = f"sec{i}"
        toc.append((anchor, heading, path.parent.name == "appendices"))
        md.reset()
        body = md.convert(text)
        body = body.replace("<h1>", f'<h1 id="{anchor}">', 1)
        sections.append(body)

    words = sum(len(p.read_text().split()) for p in files)
    toc_items = "".join(
        f'<li class="{"app" if is_app else ""}"><a href="#{a}">{h}</a></li>'
        for a, h, is_app in toc
    )

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>{CSS}{CODEHILITE}</style></head><body>
<div class="titlepage">
  <h1 class="title-main">{title}</h1>
  <div class="sub">{subtitle}</div>
  <div class="meta">
    {len(files)} sections &middot; approximately {words:,} words<br>
    Johan De Clercq<br>
    Generated from the markdown sources
  </div>
</div>
<div class="toc"><h1 class="title-main">Contents</h1><ol>{toc_items}</ol></div>
{"".join(sections)}
</body></html>"""

    suffix = "" if base == ROOT else f"-{base.name}"
    out = pathlib.Path(args.out) if args.out else ROOT / f"{ROOT.name}{suffix}.pdf"
    HTML(string=html, base_url=str(ROOT)).write_pdf(out)
    print(f"  {out.name}: {len(files)} sections, {words:,} words, "
          f"{out.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
