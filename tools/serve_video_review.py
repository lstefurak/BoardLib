#!/usr/bin/env python3
"""Serve an explicit finished-video directory on loopback, with video seeking.

No directory listing or files outside the output root are served, including
symlink targets. Binding to a LAN or public interface is deliberately unsupported.
"""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


MEDIA_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".jpg", ".jpeg", ".png", ".webp", ".gif"}


class ReviewHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory: str, **kwargs):
        self.root = Path(directory).resolve()
        self.byte_range = None
        super().__init__(*args, directory=str(self.root), **kwargs)

    def list_directory(self, path):
        self.send_error(403, "Directory listing is disabled")
        return None

    def send_head(self):
        self.byte_range = None
        parsed = urlsplit(self.path)
        if parsed.path == "/":
            self.path = "/review.html"
        path = Path(self.translate_path(self.path)).resolve()
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            self.send_error(403)
            return None
        requested_parts = Path(unquote(parsed.path)).parts
        if any(part.startswith(".") for part in (*relative.parts, *requested_parts)):
            self.send_error(403)
            return None
        if relative != Path("review.html") and path.suffix.lower() not in MEDIA_SUFFIXES:
            self.send_error(403, "Only review media is served")
            return None
        header = self.headers.get("Range")
        if not header or not path.is_file():
            return super().send_head()
        size = path.stat().st_size
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
        if match and any(match.groups()):
            first, last = match.groups()
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            else:
                start, end = max(0, size - int(last)), size - 1
            if 0 <= start <= end and start < size:
                file = path.open("rb")
                file.seek(start)
                self.byte_range = (start, end)
                self.send_response(206)
                self.send_header("Content-Type", self.guess_type(str(path)))
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                return file
        self.send_response(416)
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return None

    def copyfile(self, source, outputfile):
        if self.byte_range is None:
            return super().copyfile(source, outputfile)
        remaining = self.byte_range[1] - self.byte_range[0] + 1
        while remaining:
            chunk = source.read(min(256 * 1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def make_server(output_dir: Path, port: int = 8770) -> ThreadingHTTPServer:
    root = output_dir.resolve()
    if not root.is_dir() or not (root / "review.html").is_file():
        raise ValueError("output directory must contain a generated review.html")
    if not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    return ThreadingHTTPServer(("127.0.0.1", port), partial(ReviewHandler, directory=str(root)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args(argv)
    try:
        with make_server(args.output_dir, args.port) as server:
            print(f"Review server: http://127.0.0.1:{server.server_port}/review.html", flush=True)
            server.serve_forever()
        return 0
    except (OSError, ValueError) as error:
        parser.error(str(error))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
