from __future__ import annotations

import argparse
import os
import threading

import uvicorn

from .api import create_app
from .config import load_config
from .logging_utils import configure_logging
from .service import MinerSupervisorService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local XMRig supervisor")
    parser.add_argument(
        "--config",
        default=os.environ.get("MINER_SUPERVISOR_CONFIG"),
        help="Path to YAML or JSON config",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.config:
        raise SystemExit("Missing --config or MINER_SUPERVISOR_CONFIG")

    config = load_config(args.config)
    configure_logging(config.log_level)
    service = MinerSupervisorService(config)
    service.install_signal_handlers()
    app = create_app(service)

    worker = threading.Thread(target=service.run_forever, daemon=True)
    worker.start()

    uvicorn.run(app, host=config.api_bind_host, port=config.api_bind_port, log_level=config.log_level.lower())
    service.stop_event.set()
    worker.join(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
