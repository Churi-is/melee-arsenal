"""Command-line entry point for the RE Probe parsers.

    python -m re_probe_parse functions <dump_dir> [--out functions.json]
    python -m re_probe_parse all <dump_dir> [<trace.json>] --out-dir re_probe_reports
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import report as report_module
from .classes import report_classes, write_markdown as classes_md
from .enums import report_enums, write_markdown as enums_md
from .functions import report_functions, write_markdown as functions_md
from .loader import Dump
from .objects import report_objects, write_markdown as objects_md
from .traces import report_trace, write_markdown as traces_md
from .values import report_values, write_markdown as values_md


def _cmd_functions(args: argparse.Namespace) -> int:
    dump = Dump(args.dump_dir)
    result = report_functions(dump)
    print(functions_md(result))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            import json
            json.dump(result, fh, indent=2, default=str)
        print(f"\nwrote {args.out}")
    return 0


def _cmd_classes(args: argparse.Namespace) -> int:
    dump = Dump(args.dump_dir)
    result = report_classes(dump)
    print(classes_md(result))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            import json
            json.dump(result, fh, indent=2, default=str)
        print(f"\nwrote {args.out}")
    return 0


def _cmd_enums(args: argparse.Namespace) -> int:
    dump = Dump(args.dump_dir)
    result = report_enums(dump)
    print(enums_md(result))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            import json
            json.dump(result, fh, indent=2, default=str)
        print(f"\nwrote {args.out}")
    return 0


def _cmd_objects(args: argparse.Namespace) -> int:
    dump = Dump(args.dump_dir)
    result = report_objects(dump)
    print(objects_md(result))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            import json
            json.dump(result, fh, indent=2, default=str)
        print(f"\nwrote {args.out}")
    return 0


def _cmd_values(args: argparse.Namespace) -> int:
    dump = Dump(args.dump_dir)
    result = report_values(dump)
    print(values_md(result))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            import json
            json.dump(result, fh, indent=2, default=str)
        print(f"\nwrote {args.out}")
    return 0


def _cmd_xrefs(args: argparse.Namespace) -> int:
    from .xrefs import report_xrefs, write_markdown as xrefs_md

    dump = Dump(args.dump_dir)
    result = report_xrefs(dump)
    print(xrefs_md(result))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            import json
            json.dump(result, fh, indent=2, default=str)
        print(f"\nwrote {args.out}")
    return 0


def _cmd_traces(args: argparse.Namespace) -> int:
    result = report_trace(args.trace)
    print(traces_md(result))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            import json
            json.dump(result, fh, indent=2, default=str)
        print(f"\nwrote {args.out}")
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    rep = report_module.build_findings(args.dump_dir, args.trace or None)
    md_path, json_path = report_module.save_report(rep, args.out_dir)
    print(write_findings_md(rep))
    print(f"\nwrote {md_path}")
    print(f"wrote {json_path}")
    return 0


def write_findings_md(rep: dict[str, Any]) -> str:
    return report_module.write_markdown(rep)


def _cmd_all(args: argparse.Namespace) -> int:
    rep = report_module.build_findings(args.dump_dir, args.trace or None)
    md_path, json_path = report_module.save_report(rep, args.out_dir)
    print(f"wrote {md_path}")
    print(f"wrote {json_path}")
    # Also dump each raw section next to it for the user to inspect/send.
    import json

    dump = Dump(args.dump_dir)
    sections = {
        "functions.json": report_functions(dump),
        "classes.json": report_classes(dump),
        "enums.json": report_enums(dump),
        "objects.json": report_objects(dump),
        "values.json": report_values(dump),
    }
    if args.trace:
        sections["trace.json"] = report_trace(args.trace)
    for name, result in sections.items():
        path = os.path.join(args.out_dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, default=str)
        print(f"wrote {path}")
    print(f"\nsummary: {rep['summary']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="re_probe_parse", description="Parse RE Probe dumps for melee-arsenal assumption checking")
    sub = ap.add_subparsers(dest="command", required=True)

    def add_dump(parser: argparse.ArgumentParser, with_trace: bool = False) -> None:
        parser.add_argument("dump_dir", help="RE Probe dump directory (contains manifest.json and shards)")
        if with_trace:
            parser.add_argument("trace", nargs="?", help="trace-*.json from `reprobe trace stop`")
        parser.add_argument("--out", help="write raw section JSON to this file")

    p = sub.add_parser("functions", help="UFunction signature report for mod touchpoints")
    add_dump(p)
    p.set_defaults(func=_cmd_functions)

    p = sub.add_parser("classes", help="Class property & struct layout report")
    add_dump(p)
    p.set_defaults(func=_cmd_classes)

    p = sub.add_parser("enums", help="Enum value report (e.g. ETopStatArrow CompareArrow bytes)")
    add_dump(p)
    p.set_defaults(func=_cmd_enums)

    p = sub.add_parser("objects", help="Object index report: donor paths, assets, mod objects")
    add_dump(p)
    p.set_defaults(func=_cmd_objects)

    p = sub.add_parser("values", help="CDO defaults, live instances, globals")
    add_dump(p)
    p.set_defaults(func=_cmd_values)

    p = sub.add_parser("xrefs", help="Cross-reference edges report")
    add_dump(p)
    p.set_defaults(func=_cmd_xrefs)

    p = sub.add_parser("traces", help="Runtime caller→callee trace report")
    p.add_argument("trace", help="trace-*.json from `reprobe trace stop`")
    p.add_argument("--out", help="write raw trace JSON to this file")
    p.set_defaults(func=_cmd_traces)

    p = sub.add_parser("report", help="Aggregate assumption findings (markdown + json)")
    p.add_argument("dump_dir", help="RE Probe dump directory")
    p.add_argument("trace", nargs="?", help="trace-*.json from `reprobe trace stop`")
    p.add_argument("--out-dir", default="re_probe_reports", help="output directory (default: re_probe_reports)")
    p.set_defaults(func=_cmd_report)

    p = sub.add_parser("all", help="Run every parser and write the findings report")
    p.add_argument("dump_dir", help="RE Probe dump directory")
    p.add_argument("trace", nargs="?", help="trace-*.json from `reprobe trace stop`")
    p.add_argument("--out-dir", default="re_probe_reports", help="output directory (default: re_probe_reports)")
    p.set_defaults(func=_cmd_all)

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
