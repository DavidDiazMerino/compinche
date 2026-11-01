"""Explicit local demo launcher; credentials stay in the server process."""
import argparse
from pathlib import Path
import os

import uvicorn

from .replay import load_env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=8017)
    args = parser.parse_args()
    if args.env_file and args.env_file.exists():
        load_env(args.env_file)
    if args.data_dir:
        os.environ["COMPINCHE_DATA_DIR"] = str(args.data_dir.resolve())
    uvicorn.run("backend.app:app", host="127.0.0.1", port=args.port, timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()
