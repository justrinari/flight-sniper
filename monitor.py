#!/usr/bin/env python3
"""flight-sniper — точка входа.

Команды:
  scan      — обход кэша Aviasales, запись истории
  backfill  — стартовая месячная матрица (пропускается, если база не пуста)
  digest    — ежедневный дайджест в Telegram
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src import runner
from src.config import Config

DEFAULT_CONFIG = Path("config.yaml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="monitor.py", description="flight-sniper")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--db", type=Path, default=runner.DEFAULT_DB)
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="скан кэша Aviasales")
    backfill = sub.add_parser("backfill", help="стартовая месячная матрица")
    backfill.add_argument("--force", action="store_true", help="игнорировать непустую базу")
    sub.add_parser("digest", help="дайджест в Telegram")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = Config.load(args.config)

    try:
        if args.command == "scan":
            result = runner.run_scan(cfg, db_path=args.db)
        elif args.command == "backfill":
            result = runner.run_backfill(cfg, db_path=args.db, force=args.force)
        else:
            result = runner.run_digest(cfg, db_path=args.db)
    except runner.ConfigurationError as exc:
        logging.error("%s", exc)
        return 2

    logging.info("готово: %s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
