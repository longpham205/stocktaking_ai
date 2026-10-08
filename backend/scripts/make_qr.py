"""Mã QR mở web POS trên điện thoại (run_real tự gọi ở chế độ tunnel).

    python scripts/make_qr.py https://xxx.trycloudflare.com --out ../backups/tunnel_qr.png
    python scripts/make_qr.py --lan --out ../backups/lan_qr.png   # địa chỉ Wi-Fi của máy, cổng 5173

Chỉ dùng OpenCV (có sẵn trong môi trường chạy pipeline thật). Mã được đọc lại để chắc khớp địa chỉ.
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

import cv2


def lan_address(port: int) -> str:
    """http://<IP của card mạng đang ra ngoài>:<port> (không gửi gói tin nào)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 80))
        return f"http://{probe.getsockname()[0]}:{port}"


def render(url: str, caption: str):
    code = cv2.QRCodeEncoder_create().encode(url)
    scale = max(1, 900 // code.shape[0])
    image = cv2.resize(code, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    image = cv2.copyMakeBorder(image, 90, 120, 40, 40, cv2.BORDER_CONSTANT, value=255)
    image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR) if image.ndim == 2 else image
    font_scale = min(1.3, 1.3 * (image.shape[1] - 80) / max(1, cv2.getTextSize(url, cv2.FONT_HERSHEY_SIMPLEX, 1.3, 3)[0][0]))
    cv2.putText(image, caption, (40, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(image, url, (40, image.shape[0] - 45), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), 3, cv2.LINE_AA)
    return image


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Mã QR mở web POS trên điện thoại.")
    parser.add_argument("url", nargs="?", help="địa chỉ trong mã (bỏ trống khi dùng --lan)")
    parser.add_argument("--lan", action="store_true", help="dùng địa chỉ Wi-Fi của máy, cổng --port")
    parser.add_argument("--port", type=int, default=5173)
    parser.add_argument("--caption", default="Scan to open Stocktaking AI POS")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    url = lan_address(args.port) if args.lan else args.url
    if not url:
        parser.error("cần địa chỉ hoặc --lan")

    image = render(url, args.caption)
    decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(image)
    if decoded != url:
        print(f"LỖI: mã QR đọc lại ra '{decoded}', không khớp '{url}'", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", image)[1].tofile(str(args.out))
    print(f"QR: {args.out} -> {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
