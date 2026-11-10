"""Command line interface (headless runs, batch processing, debugging).

  python -m subvideo.cli new  <project_dir> --video <file> | --url <link>
  python -m subvideo.cli run  <project_dir> <stage> [key=value ...]
  python -m subvideo.cli auto <project_dir> [--from translate] [--skip qa,ocr]
  python -m subvideo.cli stages
"""

from __future__ import annotations

import argparse
import json
import sys


def _parse_kv(items: list[str]) -> dict:
    out = {}
    for it in items:
        if "=" not in it:
            continue
        k, v = it.split("=", 1)
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def main(argv: list[str] | None = None) -> int:
    from .config import Settings
    from .core.jobs import PrintContext
    from .core.project import Project
    from .pipeline.stages import AUTO_STEPS, STAGES, run_stage

    Settings.get().apply_env()
    ap = argparse.ArgumentParser(prog="subvideo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("new")
    a.add_argument("project")
    a.add_argument("--video")
    a.add_argument("--url")
    a.add_argument("--copy", action="store_true")
    b = sub.add_parser("run")
    b.add_argument("project")
    b.add_argument("stage")
    b.add_argument("params", nargs="*")
    c = sub.add_parser("auto")
    c.add_argument("project")
    c.add_argument("--from", dest="start_from")
    c.add_argument("--skip", default="")
    sub.add_parser("stages")
    args = ap.parse_args(argv)

    if args.cmd == "stages":
        for k, (label, _) in STAGES.items():
            print(f"{k:16s} {label}")
        return 0
    ctx = PrintContext()
    if args.cmd == "new":
        p = Project.create(args.project)
        if args.video:
            print(run_stage("import_media", p, {"file": args.video, "copy": args.copy}, ctx))
        elif args.url:
            print(run_stage("download", p, {"url": args.url}, ctx))
        p.save()
        return 0
    p = Project.load(args.project)
    try:
        if args.cmd == "run":
            msg = run_stage(args.stage, p, _parse_kv(args.params), ctx)
        else:
            skip = {s.strip() for s in args.skip.split(",") if s.strip()}
            steps = [k for k, _ in AUTO_STEPS if k not in skip]
            msg = run_stage("auto", p, {"steps": steps, "start_from": args.start_from}, ctx)
    finally:
        p.save()
    print("\n" + str(msg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
