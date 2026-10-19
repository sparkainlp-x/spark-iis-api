# Copyright (C) 2026 Jean-François Brisson / Spark AI NLP. SPDX-License-Identifier: AGPL-3.0-only
"""``spark-iis demo`` (seed-42 synthetic demo) and ``spark-iis serve`` (local server, simulated devices)."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from .boundary import BOUNDARY_STATEMENT


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spark-iis", description=BOUNDARY_STATEMENT)
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="write the seed-42 SYNTHETIC demo outputs")
    demo.add_argument("--out", type=Path, default=Path("examples/demo"))
    demo.add_argument("--seed", type=int, default=42)
    serve = sub.add_parser("serve", help="run the API locally (needs the 'serve' extra)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    if args.command == "demo":
        from .demo import run_demo

        print(json.dumps(run_demo(args.out, args.seed), indent=2, sort_keys=True))
        return 0
    try:  # pragma: no cover - exercised manually; needs uvicorn
        import uvicorn
    except ImportError:  # pragma: no cover
        parser.error("serve needs uvicorn: pip install 'spark-iis-api[serve]'")
    from .app import create_app  # pragma: no cover

    print(BOUNDARY_STATEMENT)  # pragma: no cover
    uvicorn.run(create_app(), host=args.host, port=args.port)  # pragma: no cover
    return 0  # pragma: no cover
