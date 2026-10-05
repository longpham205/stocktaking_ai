"""Phân loại ảnh sản phẩm BẰNG TAY vào các thư mục gallery, đặt lại tên ảnh theo số thứ tự.

Mở một cửa sổ: xem từng ảnh trong thư mục nguồn, chọn (hoặc tạo) thư mục đích cho nó. Ảnh được
SAO CHÉP vào ``<đích>/<thư mục>/0001.jpg, 0002.jpg, ...`` (số tiếp theo của thư mục đó); ảnh gốc
giữ nguyên trừ khi có ``--move``.

    python scripts/sort_gallery_images.py --source "D:/anh_moi"
    python scripts/sort_gallery_images.py --source "D:/anh_moi" --dest data/gallery_inbox --move

Mặc định đích là ``data/gallery_inbox`` (chạy từ backend/): mỗi thư mục ở đó là MỘT SKU mới, được
cấp ID khi chạy ``python -m engine.catalog.sync_gallery ... --inbox-dir data/gallery_inbox``. Muốn
thêm ảnh cho SKU đã có thì đặt ``--dest data/gallery`` và chọn đúng thư mục của SKU đó.

Phím: 1-9 chọn thư mục theo số · Space lặp lại thư mục vừa dùng · gõ tên + Enter tạo/chọn thư mục
· S hoặc → bỏ qua · Ctrl+Z hoàn tác · Esc thoát.

Làm dở rồi thoát cũng được: nhật ký ``_sort_log.json`` trong thư mục nguồn ghi ảnh nào đã xếp vào
đâu, lần chạy sau tự bỏ qua các ảnh đó. Chỉ cần thư viện chuẩn + Pillow.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
LOG_NAME = "_sort_log.json"
SKIPPED = ""  # giá trị trong nhật ký cho ảnh bị bỏ qua
_FORBIDDEN = set('<>:"/\\|?*')


def list_images(source: Path) -> list[Path]:
    """Ảnh nằm trực tiếp trong thư mục nguồn, theo thứ tự tên."""
    return sorted(p for p in source.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)


def list_folders(dest: Path) -> list[str]:
    if not dest.is_dir():
        return []
    return sorted(d.name for d in dest.iterdir() if d.is_dir() and not d.name.startswith((".", "_")))


def check_folder_name(name: str) -> str:
    """Tên thư mục đã cắt khoảng trắng; ValueError (thông báo tiếng Việt) nếu không dùng được."""
    name = name.strip()
    if not name:
        raise ValueError("Chưa nhập tên thư mục")
    if name.startswith((".", "_")) or name.endswith("."):
        raise ValueError("Tên không được bắt đầu bằng '.' hay '_', không kết thúc bằng '.'")
    if any(ch in _FORBIDDEN or ord(ch) < 32 for ch in name):
        raise ValueError('Tên không được chứa các ký tự < > : " / \\ | ? *')
    if len(name) > 80:
        raise ValueError("Tên quá dài (tối đa 80 ký tự)")
    return name


def next_index(folder: Path) -> int:
    """Số kế tiếp trong thư mục: lớn hơn mọi ảnh đang có tên là số (0001.jpg -> 2)."""
    if not folder.is_dir():
        return 1
    numbers = [int(p.stem) for p in folder.iterdir() if p.is_file() and p.stem.isdigit()]
    return max(numbers, default=0) + 1


def place(src: Path, folder: Path, move: bool = False) -> Path:
    """Đưa ảnh vào thư mục dưới tên số thứ tự kế tiếp (giữ đuôi, viết thường). Trả về đường dẫn mới."""
    folder.mkdir(parents=True, exist_ok=True)
    index = next_index(folder)
    target = folder / f"{index:04d}{src.suffix.lower()}"
    while target.exists():  # đề phòng trùng với file khác đuôi cùng số
        index += 1
        target = folder / f"{index:04d}{src.suffix.lower()}"
    if move:
        shutil.move(str(src), str(target))
    else:
        shutil.copy2(src, target)
    return target


def load_log(source: Path) -> dict[str, str]:
    """Tên ảnh nguồn -> đường dẫn đã xếp ('' = đã bỏ qua)."""
    path = source / LOG_NAME
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} không đúng định dạng (cần một object JSON)")
    return {str(k): str(v) for k, v in data.items()}


def save_log(source: Path, log: dict[str, str]) -> None:
    tmp = source / (LOG_NAME + ".tmp")
    tmp.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(source / LOG_NAME)


@dataclass
class Done:
    """Một thao tác đã làm, để hoàn tác."""

    src: Path
    target: Path | None  # None = bỏ qua


class Sorter:
    """Trạng thái phân loại, không phụ thuộc giao diện."""

    def __init__(self, source: Path, dest: Path, move: bool = False):
        self.source, self.dest, self.move = source, dest, move
        self.log = load_log(source)
        self.pending = [p for p in list_images(source) if p.name not in self.log]
        self.total = len(self.pending)
        self.history: list[Done] = []
        self.last_folder: str | None = None

    @property
    def current(self) -> Path | None:
        return self.pending[0] if self.pending else None

    @property
    def position(self) -> int:
        return self.total - len(self.pending) + 1

    def folders(self) -> list[str]:
        return list_folders(self.dest)

    def count(self, folder: str) -> int:
        path = self.dest / folder
        return sum(1 for p in path.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)

    def assign(self, folder: str) -> Path:
        src = self.current
        if src is None:
            raise ValueError("Không còn ảnh nào")
        folder = check_folder_name(folder)
        target = place(src, self.dest / folder, self.move)
        self.pending.pop(0)
        self.history.append(Done(src, target))
        self.log[src.name] = str(target)
        save_log(self.source, self.log)
        self.last_folder = folder
        return target

    def skip(self) -> None:
        src = self.current
        if src is None:
            return
        self.pending.pop(0)
        self.history.append(Done(src, None))
        self.log[src.name] = SKIPPED
        save_log(self.source, self.log)

    def undo(self) -> Path | None:
        """Hoàn tác thao tác gần nhất của phiên này. Trả về ảnh quay lại hàng chờ."""
        if not self.history:
            return None
        done = self.history.pop()
        if done.target is not None:
            if self.move:
                shutil.move(str(done.target), str(done.src))
            else:
                done.target.unlink(missing_ok=True)
            if done.target.parent.is_dir() and not any(done.target.parent.iterdir()):
                done.target.parent.rmdir()  # thư mục vừa tạo cho đúng ảnh này
        self.log.pop(done.src.name, None)
        save_log(self.source, self.log)
        self.pending.insert(0, done.src)
        return done.src


def run_ui(sorter: Sorter) -> None:
    """Cửa sổ phân loại (tkinter + Pillow, nhập muộn để phần còn lại dùng được không cần màn hình)."""
    import tkinter as tk
    from tkinter import messagebox

    from PIL import Image, ImageOps, ImageTk

    root = tk.Tk()
    root.title("Phân loại ảnh gallery")
    root.geometry("1180x760")
    root.minsize(900, 560)

    picture = tk.Label(root, bg="#222")
    picture.pack(side="left", fill="both", expand=True)

    side = tk.Frame(root, width=340, padx=10, pady=10)
    side.pack(side="right", fill="y")
    side.pack_propagate(False)

    progress = tk.Label(side, anchor="w", font=("Segoe UI", 11, "bold"))
    progress.pack(fill="x")
    filename = tk.Label(side, anchor="w", fg="#666", wraplength=310, justify="left")
    filename.pack(fill="x", pady=(0, 8))

    tk.Label(side, text="Thư mục mới hoặc có sẵn (gõ tên rồi Enter):", anchor="w").pack(fill="x")
    entry = tk.Entry(side, font=("Segoe UI", 11))
    entry.pack(fill="x", pady=(2, 8))

    tk.Label(side, text="Thư mục đích (nhấp đúp, hoặc phím 1-9):", anchor="w").pack(fill="x")
    listbox = tk.Listbox(side, font=("Segoe UI", 10), activestyle="none", exportselection=False)
    listbox.pack(fill="both", expand=True, pady=(2, 8))

    status = tk.Label(side, anchor="w", fg="#0a6", wraplength=310, justify="left")
    status.pack(fill="x", pady=(0, 6))

    buttons = tk.Frame(side)
    buttons.pack(fill="x")
    same_button = tk.Button(buttons, text="Như ảnh trước (Space)")
    same_button.pack(fill="x", pady=1)
    skip_button = tk.Button(buttons, text="Bỏ qua (S / →)")
    skip_button.pack(fill="x", pady=1)
    undo_button = tk.Button(buttons, text="Hoàn tác (Ctrl+Z)")
    undo_button.pack(fill="x", pady=1)

    state: dict[str, object] = {"photo": None, "folders": []}

    def show_picture() -> None:
        src = sorter.current
        if src is None:
            picture.configure(image="", text="Đã hết ảnh.\nĐóng cửa sổ để kết thúc.", fg="white", font=("Segoe UI", 16))
            state["photo"] = None
            return
        width, height = max(picture.winfo_width(), 200), max(picture.winfo_height(), 200)
        try:
            with Image.open(src) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail((width - 8, height - 8))
            photo = ImageTk.PhotoImage(image)
        except (OSError, ValueError) as exc:
            picture.configure(image="", text=f"Không mở được ảnh:\n{exc}", fg="white")
            state["photo"] = None
            return
        state["photo"] = photo  # giữ tham chiếu, nếu không Tk bỏ ảnh
        picture.configure(image=photo, text="")

    def refresh(message: str = "", error: bool = False) -> None:
        folders = sorter.folders()
        state["folders"] = folders
        listbox.delete(0, "end")
        for i, name in enumerate(folders):
            key = f"{i + 1}. " if i < 9 else "    "
            listbox.insert("end", f"{key}{name}  ({sorter.count(name)})")
        src = sorter.current
        progress.configure(text=f"Ảnh {sorter.position}/{sorter.total}" if src else f"Xong {sorter.total}/{sorter.total}")
        filename.configure(text=src.name if src else "")
        same_button.configure(
            text=f"Như ảnh trước: {sorter.last_folder} (Space)" if sorter.last_folder else "Như ảnh trước (Space)",
            state="normal" if sorter.last_folder and src else "disabled",
        )
        status.configure(text=message, fg="#c00" if error else "#0a6")
        show_picture()

    def assign(folder: str) -> None:
        if sorter.current is None:
            return
        try:
            target = sorter.assign(folder)
        except (ValueError, OSError) as exc:
            refresh(str(exc), error=True)
            return
        entry.delete(0, "end")
        root.focus_set()
        refresh(f"-> {target.parent.name}/{target.name}")

    def from_entry(_event: object = None) -> None:
        assign(entry.get())

    def from_list(_event: object = None) -> None:
        picked = listbox.curselection()
        folders = state["folders"]
        if picked and isinstance(folders, list):
            assign(folders[picked[0]])

    def same(_event: object = None) -> None:
        if sorter.last_folder:
            assign(sorter.last_folder)

    def skip(_event: object = None) -> None:
        sorter.skip()
        refresh("Đã bỏ qua")

    def undo(_event: object = None) -> None:
        back = sorter.undo()
        refresh(f"Đã hoàn tác: {back.name}" if back else "Không có gì để hoàn tác", error=back is None)

    def typing() -> bool:
        return root.focus_get() is entry

    def digit(event: object) -> None:
        folders = state["folders"]
        char = getattr(event, "char", "")
        if typing() or not char.isdigit() or char == "0" or not isinstance(folders, list):
            return
        index = int(char) - 1
        if index < len(folders):
            assign(folders[index])

    def unless_typing(action):  # phím tắt chữ không được ăn mất ký tự đang gõ vào ô tên
        return lambda event: None if typing() else action(event)

    def close(_event: object = None) -> None:
        left = len(sorter.pending)
        if left == 0 or messagebox.askokcancel("Thoát", f"Còn {left} ảnh chưa xếp. Thoát? (lần sau chạy lại sẽ tiếp tục)"):
            root.destroy()

    entry.bind("<Return>", from_entry)
    listbox.bind("<Double-Button-1>", from_list)
    listbox.bind("<Return>", from_list)
    same_button.configure(command=same)
    skip_button.configure(command=skip)
    undo_button.configure(command=undo)
    root.bind("<Key>", digit)
    root.bind("<space>", unless_typing(same))
    root.bind("<s>", unless_typing(skip))
    root.bind("<Right>", unless_typing(skip))
    root.bind("<Control-z>", undo)
    root.bind("<Escape>", close)
    root.protocol("WM_DELETE_WINDOW", close)
    picture.bind("<Configure>", lambda _event: show_picture())

    refresh()
    root.mainloop()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Phân loại ảnh bằng tay vào các thư mục gallery.")
    parser.add_argument("--source", required=True, type=Path, help="thư mục chứa ảnh cần phân loại")
    parser.add_argument("--dest", type=Path, default=Path("data/gallery_inbox"), help="nơi tạo các thư mục (mặc định data/gallery_inbox)")
    parser.add_argument("--move", action="store_true", help="CHUYỂN ảnh thay vì sao chép (ảnh gốc biến mất khỏi nguồn)")
    args = parser.parse_args(argv)

    if not args.source.is_dir():
        print(f"LỖI: không thấy thư mục nguồn: {args.source}", file=sys.stderr)
        return 2
    if args.source.resolve() == args.dest.resolve():
        print("LỖI: nguồn và đích phải khác nhau", file=sys.stderr)
        return 2
    try:
        sorter = Sorter(args.source, args.dest, args.move)
    except ValueError as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 2
    done = len(sorter.log)
    print(f"Nguồn: {args.source} — còn {sorter.total} ảnh chưa xếp" + (f" ({done} ảnh đã xử lý ở lần trước)" if done else ""))
    print(f"Đích:  {args.dest.resolve()} ({'CHUYỂN' if args.move else 'sao chép'})")
    if sorter.total == 0:
        print("Không còn ảnh nào để xếp.")
        return 0
    args.dest.mkdir(parents=True, exist_ok=True)
    run_ui(sorter)
    placed = sum(1 for d in sorter.history if d.target is not None)
    print(f"Phiên này: xếp {placed} ảnh, bỏ qua {len(sorter.history) - placed}; còn {len(sorter.pending)} ảnh.")
    for name in sorter.folders():
        print(f"  {name}: {sorter.count(name)} ảnh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
