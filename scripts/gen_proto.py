#!/usr/bin/env python3
"""Generate the Python gRPC stubs from proto/ into src/vacancy_loadtest/pb/.

The .proto files are the ones shipped with the task and are kept unmodified. protoc emits
absolute imports (``import vacancy_pb2 as vacancy__pb2``) that only work when the output
directory is on sys.path; we rewrite them to package-relative imports so the generated code
lives inside the ``vacancy_loadtest.pb`` package.

Usage:
    python scripts/gen_proto.py           # (re)generate
    python scripts/gen_proto.py --check   # exit 1 if the committed stubs are out of date
"""

from __future__ import annotations

import argparse
import filecmp
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTO_DIR = ROOT / "proto"
OUT_DIR = ROOT / "src" / "vacancy_loadtest" / "pb"

_IMPORT_RE = re.compile(r"^import (\w+_pb2) as (\w+)$", re.MULTILINE)
_INIT_TEXT = '"""Generated gRPC/protobuf code. Do not edit: run scripts/gen_proto.py."""\n'


def generate(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    protos = sorted(PROTO_DIR.glob("*.proto"))
    if not protos:
        sys.exit(f"no .proto files found in {PROTO_DIR}")
    cmd = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"-I{PROTO_DIR}",
        f"--python_out={out_dir}",
        f"--grpc_python_out={out_dir}",
        f"--pyi_out={out_dir}",
        *map(str, protos),
    ]
    subprocess.run(cmd, check=True)
    for path in list(out_dir.glob("*_pb2*.py")) + list(out_dir.glob("*_pb2.pyi")):
        text = path.read_text(encoding="utf-8")
        new_text = _IMPORT_RE.sub(r"from . import \1 as \2", text)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
    (out_dir / "__init__.py").write_text(_INIT_TEXT, encoding="utf-8")


def check() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "pb"
        generate(fresh)
        names = sorted(p.name for p in fresh.iterdir())
        committed = sorted(p.name for p in OUT_DIR.iterdir() if p.name != "__pycache__")
        stale = [
            n
            for n in names
            if n not in committed or not filecmp.cmp(fresh / n, OUT_DIR / n, shallow=False)
        ]
        extra = [n for n in committed if n not in names]
        if stale or extra:
            print("generated code is out of date; run `python scripts/gen_proto.py`")
            for n in stale:
                print(f"  changed/missing: {n}")
            for n in extra:
                print(f"  unexpected:      {n}")
            return 1
    print("generated code is up to date")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--check", action="store_true", help="verify the committed stubs match the protos"
    )
    args = parser.parse_args(argv)
    if args.check:
        return check()
    generate(OUT_DIR)
    print(f"generated {len(list(OUT_DIR.glob('*.py')))} modules in {OUT_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
