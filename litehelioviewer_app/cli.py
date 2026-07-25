from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import requests

from .config import PYTHON_EXE, ROOT


DEFAULT_URL = "http://127.0.0.1:8765"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="litehelioviewer", description="Control LiteHelioviewer.")
    parser.add_argument("--url", default=DEFAULT_URL)
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="Start the local web server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    load = sub.add_parser("load", help="Load a Helioviewer preset layer")
    load.add_argument("preset", choices=["hmi-magnetogram", "hmi-continuum", "lasco-c2", "lasco-c3", "aia-94", "aia-131", "aia-171", "aia-193", "aia-211", "aia-304", "aia-335", "aia-1600", "aia-1700", "aia-4500"])
    load.add_argument("--date", required=True)
    load.add_argument("--server", default="GSFC")
    load.add_argument("--opacity", type=float, default=1.0)

    fits = sub.add_parser("fits", help="Load a local FITS file")
    fits.add_argument("path")
    fits.add_argument("--opacity", type=float, default=1.0)
    fits.add_argument("--cmap", default="auto")

    pfss = sub.add_parser("pfss", help="Show PFSS field-line overlay")
    pfss.add_argument("--date", default="")
    pfss.add_argument("--central-lon", type=float, default=None)
    sub.add_parser("clear", help="Remove all layers")
    sub.add_parser("layers", help="List layers")
    sub.add_parser("samp", help="Start SAMP receiver")

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        return serve_app(args.host, args.port)
    if args.cmd == "load":
        return post(args.url, "/api/load/helioviewer", {"preset": args.preset, "date": args.date, "server": args.server, "opacity": args.opacity})
    if args.cmd == "fits":
        return post(args.url, "/api/load/fits-path", {"path": str(Path(args.path).resolve()), "opacity": args.opacity, "cmap": args.cmap})
    if args.cmd == "pfss":
        payload = {"enable": True}
        if args.date:
            payload["date"] = args.date
        if args.central_lon is not None:
            payload["central_lon"] = args.central_lon
        return post(args.url, "/api/control/pfss", payload)
    if args.cmd == "clear":
        response = requests.delete(args.url.rstrip("/") + "/api/layers", timeout=20)
        print_response(response)
        return 0 if response.ok else 1
    if args.cmd == "layers":
        response = requests.get(args.url.rstrip("/") + "/api/layers", timeout=20)
        print_response(response)
        return 0 if response.ok else 1
    if args.cmd == "samp":
        return post(args.url, "/api/samp/start", {})
    return 1


def serve_app(host: str, port: int) -> int:
    import uvicorn

    uvicorn.run("litehelioviewer_app.app:app", host=host, port=port, reload=False, app_dir=str(ROOT))
    return 0


def post(base_url: str, path: str, payload: dict) -> int:
    response = requests.post(base_url.rstrip("/") + path, json=payload, timeout=120)
    print_response(response)
    return 0 if response.ok else 1


def print_response(response: requests.Response) -> None:
    try:
        data = response.json()
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        print(response.text)


def start_detached(port: int = 8765) -> subprocess.Popen:
    return subprocess.Popen(
        [PYTHON_EXE, "-m", "litehelioviewer_app.cli", "serve", "--port", str(port)],
        cwd=str(ROOT),
        creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform.startswith("win") else 0,
    )


if __name__ == "__main__":
    raise SystemExit(main())
