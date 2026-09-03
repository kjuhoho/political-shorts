"""Serve the ``output/`` folder over HTTP.

Instagram and TikTok do not accept a file upload — their APIs *pull* the video
from a public URL. This is a minimal static server for that. It is NOT https and
NOT authenticated, so for real publishing you still need to put it behind a
tunnel / CDN and set ``PUBLIC_MEDIA_BASE_URL`` to that https address.
"""
from __future__ import annotations

import socket
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from .config import settings
from .logging_setup import get_logger

log = get_logger("media_server")


class _Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        log.info("%s - %s", self.address_string(), fmt % args)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def serve(host: str = "0.0.0.0", port: int = 8770) -> None:
    directory = str(settings.output_dir)
    handler = partial(_Handler, directory=directory)
    httpd = ThreadingHTTPServer((host, port), handler)
    lan = _lan_ip()
    print(f"serving {directory}")
    print(f"  local : http://127.0.0.1:{port}/")
    print(f"  LAN   : http://{lan}:{port}/")
    print()
    print("For Instagram/TikTok you need a PUBLIC https URL. Expose this with e.g.:")
    print(f"  cloudflared tunnel --url http://127.0.0.1:{port}")
    print(f"  ngrok http {port}")
    print("then set PUBLIC_MEDIA_BASE_URL in .env to the https address it prints.")
    print("\nCtrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
