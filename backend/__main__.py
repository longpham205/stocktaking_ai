"""Khởi chạy web POS:  python -m backend --config configs/config.demo.yaml --data-dir data_demo"""

from __future__ import annotations

import argparse
import logging
import socket
import sys

from .catalog import JsonCatalog
from .config import load_settings
from .inference import FakeExecutor, LocalExecutor
from .server import PosServer
from .service import App


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend", description="Web POS thanh toán bằng ảnh")
    ap.add_argument("--config", default="configs/config.yaml", help="config pipeline AI (mặc định configs/config.yaml)")
    ap.add_argument("--backend-config", default=None, help="configs/backend.yaml (tuỳ chọn)")
    ap.add_argument("--data-dir", default=None, help="thư mục dữ liệu web (db, ảnh giao dịch), ví dụ data_demo")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--fake", action="store_true", help="dùng bộ nhận diện GIẢ (thử giao diện, không nạp model)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows: console/file mã hoá cũ không được làm sập vì tiếng Việt
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", datefmt="%H:%M:%S")

    settings = load_settings(args.config, args.backend_config, args.data_dir, args.host, args.port)
    catalog = JsonCatalog(settings.metadata_dir / settings.products_filename)
    if args.fake:
        executor = FakeExecutor(list(catalog.all()))
        print(">> CHẾ ĐỘ GIẢ: kết quả nhận diện là ngẫu nhiên, chỉ để thử giao diện.")
    else:
        print(">> Đang nạp pipeline AI (lần đầu có thể mất vài chục giây)...")
        try:
            executor = LocalExecutor(settings.pipeline_config)
        except Exception as exc:  # báo rõ, không tự chuyển sang chế độ giả
            print(f"\nLỖI: không nạp được pipeline AI: {type(exc).__name__}: {exc}\n"
                  "Gợi ý: chạy `python run.py --mode validate --config <cfg>` để build index trước; "
                  "hoặc thêm --fake để chỉ thử giao diện.", file=sys.stderr)
            return 2
    app = App(settings, catalog, executor)
    try:
        httpd = PosServer((settings.host, settings.port), app)
    except OSError as exc:
        print(f"\nLỖI: không mở được cổng {settings.port}: {exc}\nĐổi cổng bằng --port hoặc tắt tiến trình đang chiếm cổng.",
              file=sys.stderr)
        return 2
    print("=" * 64)
    print(f" Web POS đang chạy:  http://localhost:{settings.port}")
    print(f" Trong mạng LAN:     http://{_lan_ip()}:{settings.port}   (camera điện thoại cần HTTPS -> dùng tunnel)")
    print(f" Dữ liệu: {settings.db_path}")
    print(" Ctrl+C để dừng.")
    print("=" * 64)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nĐang dừng...")
    finally:
        httpd.server_close()
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
