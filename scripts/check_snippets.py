"""Check that this book's commands and links still hold together.

    python3 scripts/check_snippets.py          # static checks only, always safe
    python3 scripts/check_snippets.py --run    # also execute against a live cluster

Static checks, all mechanical and all designed to have no false positives:

  links     every relative markdown link resolves to a file that exists
  shell     every ```bash block parses (`bash -n`) -- catches broken quoting,
            heredocs and substitutions
  files     every `-f examples/...` referenced by a command exists in the repo

Deliberately NOT checked: whether a command is marked destructive. This book
marks `**destructive**` by judgement -- deleting data you might care about, not
routine cleanup of resources the chapter just created -- so a mechanical rule
flags ~70 blocks that are correctly unmarked, and trains you to ignore output.

`--run` is opt-in because these commands are real. It needs the kind cluster from
`examples/cluster/kind-cluster.yaml`, it is slow, and it changes cluster state.
It skips two categories it cannot safely run: blocks preceded by a destructive
marker, and blocks containing UPPERCASE placeholders (the book's convention for
"substitute your own value").
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent

BASH_BLOCK = re.compile(r"```bash\n(.*?)```", re.DOTALL)
LINK = re.compile(r"\[[^\]]*\]\(([^)#]+?)(?:#[^)]*)?\)")
FILE_REF = re.compile(r"-f\s+((?:examples|manifests)/[^\s\"']+)")
# The book's placeholder convention: UPPERCASE tokens are for you to substitute.
PLACEHOLDER = re.compile(r"(?<![\w/-])[A-Z][A-Z0-9_]{2,}(?![\w-])")
DESTRUCTIVE_MARK = re.compile(r"destructive", re.IGNORECASE)


def markdown_files() -> list[pathlib.Path]:
    return [
        p for p in sorted(ROOT.rglob("*.md"))
        if not any(part in {".git", ".venv", "node_modules"} for part in p.parts)
    ]


def check_links(fails: list[str]) -> int:
    n = 0
    for md in markdown_files():
        for link in LINK.findall(md.read_text()):
            if link.startswith(("http://", "https://", "mailto:")):
                continue
            n += 1
            if not (md.parent / link).resolve().exists():
                fails.append(f"broken link  {md.relative_to(ROOT)} -> {link}")
    return n


def check_shell(fails: list[str]) -> int:
    n = 0
    for md in markdown_files():
        for i, m in enumerate(BASH_BLOCK.finditer(md.read_text()), 1):
            n += 1
            proc = subprocess.run(["bash", "-n"], input=m.group(1), capture_output=True, text=True)
            if proc.returncode != 0:
                first = (proc.stderr.strip().splitlines() or ["?"])[0]
                fails.append(f"shell syntax {md.relative_to(ROOT)} #{i}: {first[:90]}")
    return n


def check_file_refs(fails: list[str]) -> int:
    """Only look inside ```bash blocks: prose mentions paths like `examples/...`."""
    n = 0
    for md in markdown_files():
        commands = "\n".join(BASH_BLOCK.findall(md.read_text()))
        for ref in sorted(set(FILE_REF.findall(commands))):
            if "..." in ref:
                continue
            n += 1
            if not (ROOT / ref).exists():
                fails.append(f"missing file {md.relative_to(ROOT)} -> {ref}")
    return n


def run_live(fails: list[str]) -> int:
    """Execute the runnable blocks. Opt-in; changes cluster state."""
    ran = skipped = 0
    for md in markdown_files():
        text = md.read_text()
        for i, m in enumerate(BASH_BLOCK.finditer(text), 1):
            code = m.group(1)
            preamble = text[max(0, m.start() - 400):m.start()]
            if DESTRUCTIVE_MARK.search(preamble) or PLACEHOLDER.search(code):
                skipped += 1
                continue
            ran += 1
            proc = subprocess.run(
                ["bash", "-e"], input=code, cwd=ROOT,
                capture_output=True, text=True, timeout=300,
            )
            if proc.returncode != 0:
                tail = ((proc.stderr or proc.stdout).strip().splitlines() or ["?"])[-1]
                fails.append(f"command      {md.relative_to(ROOT)} #{i}: {tail[:90]}")
    print(f"  {ran:5} blocks executed, {skipped} skipped (destructive or placeholder)")
    return ran


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true",
                        help="execute commands against a live cluster (slow, changes state)")
    args = parser.parse_args()

    fails: list[str] = []
    for label, n in (
        ("links", check_links(fails)),
        ("shell blocks", check_shell(fails)),
        ("file refs", check_file_refs(fails)),
    ):
        print(f"  {n:5} {label} checked")

    if args.run:
        run_live(fails)

    if fails:
        print(f"\n{len(fails)} problem(s):\n")
        for f in fails:
            print("  " + f)
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
