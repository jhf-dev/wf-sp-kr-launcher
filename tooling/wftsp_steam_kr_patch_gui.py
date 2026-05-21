#!/usr/bin/env python3
"""Tkinter GUI for applying the Korean WFTSP patch to the Taiwan Steam build."""

from __future__ import annotations

import argparse
import dataclasses
import json
import queue
import sys
import threading
import traceback
from pathlib import Path
from tkinter import END, LEFT, BOTH, X, filedialog, messagebox
import tkinter as tk
from tkinter import ttk

import wftsp_steam_kr_launcher as core


APP_TITLE = "Wind Fantasy SP KR -> Steam TW 패치 런처"


def default_kr_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def default_tw_root() -> Path:
    return default_kr_root() / "Wind Fantasy SP_TW"


def make_args(
    kr_root: Path,
    tw_root: Path,
    *,
    dry_run: bool = False,
    display_mode: str | None = None,
    width: int | None = None,
    height: int | None = None,
    no_apply: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        kr_root=kr_root,
        tw_root=tw_root,
        dry_run=dry_run,
        display_mode=display_mode,
        width=width,
        height=height,
        no_apply=no_apply,
    )


def compact_apply_summary(report: dict[str, object]) -> dict[str, object]:
    overlay = report.get("overlay", [])
    changed_files = []
    source_patches = []
    if isinstance(overlay, list):
        for item in overlay:
            if not isinstance(item, dict):
                continue
            if item.get("changed"):
                changed_files.append(item.get("path"))
            patch = item.get("source_patch")
            if isinstance(patch, dict):
                source_patches.append(
                    {
                        "path": item.get("path"),
                        "kind": patch.get("kind"),
                        "source_changed": patch.get("changed"),
                    }
                )

    wind_dll = report.get("wind_dll", {})
    registry = report.get("registry", {})
    return {
        "changed_overlay_files": changed_files,
        "source_progression_patches": source_patches,
        "wind_dll": wind_dll,
        "registry": registry,
        "backup_dir": report.get("backup_dir"),
    }


class PatchGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("880x640")
        self.minsize(760, 520)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None

        self.kr_path = tk.StringVar(value=str(default_kr_root()))
        self.tw_path = tk.StringVar(value=str(default_tw_root()))
        self.display_mode = tk.StringVar(value="건드리지 않음")
        self.width_value = tk.StringVar(value="")
        self.height_value = tk.StringVar(value="")
        self.launch_after_apply = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value="대기 중")

        self._build_ui()
        self.after(100, self._drain_events)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill=BOTH, expand=True)

        title = ttk.Label(root, text=APP_TITLE, font=("", 15, "bold"))
        title.pack(anchor="w")

        subtitle = ttk.Label(
            root,
            text="한국어판 폴더에서 필요한 데이터를 읽고, 진행 불가 패치를 메모리에서 반영한 뒤 Steam 대만판 폴더에 적용합니다.",
        )
        subtitle.pack(anchor="w", pady=(4, 14))

        self._path_row(root, "한국어판 폴더", self.kr_path, self._browse_kr)
        self._path_row(root, "Steam 대만판 폴더", self.tw_path, self._browse_tw)

        options = ttk.LabelFrame(root, text="실행 옵션", padding=10)
        options.pack(fill=X, pady=(8, 10))

        ttk.Label(options, text="화면 모드").pack(side=LEFT)
        display = ttk.Combobox(
            options,
            textvariable=self.display_mode,
            values=["건드리지 않음", "창모드", "전체화면"],
            state="readonly",
            width=14,
        )
        display.pack(side=LEFT, padx=(8, 16))

        ttk.Label(options, text="해상도").pack(side=LEFT)
        ttk.Entry(options, textvariable=self.width_value, width=7).pack(side=LEFT, padx=(8, 4))
        ttk.Label(options, text="x").pack(side=LEFT)
        ttk.Entry(options, textvariable=self.height_value, width=7).pack(side=LEFT, padx=(4, 16))
        ttk.Checkbutton(options, text="적용 후 WindConfig 실행", variable=self.launch_after_apply).pack(side=LEFT)

        buttons = ttk.Frame(root)
        buttons.pack(fill=X, pady=(0, 10))
        self.apply_button = ttk.Button(buttons, text="패치 적용", command=self.apply_patch)
        self.apply_button.pack(side=LEFT)
        self.status_button = ttk.Button(buttons, text="상태 확인", command=self.check_status)
        self.status_button.pack(side=LEFT, padx=(8, 0))
        self.restore_button = ttk.Button(buttons, text="TW 원본 복구", command=self.restore_patch)
        self.restore_button.pack(side=LEFT, padx=(8, 0))
        self.launch_button = ttk.Button(buttons, text="WindConfig 실행", command=self.launch_game)
        self.launch_button.pack(side=LEFT, padx=(8, 0))

        ttk.Label(root, textvariable=self.status_text).pack(anchor="w", pady=(0, 6))

        log_frame = ttk.Frame(root)
        log_frame.pack(fill=BOTH, expand=True)
        self.log = tk.Text(log_frame, wrap="word", height=18)
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side="right", fill="y")

        self._log("준비 완료. KR 폴더와 Steam TW 폴더를 확인한 뒤 '패치 적용'을 누르면 됩니다.")

    def _path_row(self, parent: ttk.Frame, label: str, variable: tk.StringVar, command) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=X, pady=4)
        ttk.Label(row, text=label, width=18).pack(side=LEFT)
        ttk.Entry(row, textvariable=variable).pack(side=LEFT, fill=X, expand=True, padx=(0, 8))
        ttk.Button(row, text="찾기", command=command).pack(side=LEFT)

    def _browse_kr(self) -> None:
        selected = filedialog.askdirectory(title="한국어판 폴더 선택", initialdir=self.kr_path.get())
        if selected:
            self.kr_path.set(selected)

    def _browse_tw(self) -> None:
        selected = filedialog.askdirectory(title="Steam 대만판 폴더 선택", initialdir=self.tw_path.get())
        if selected:
            self.tw_path.set(selected)

    def _display_mode_arg(self) -> str | None:
        value = self.display_mode.get()
        if value == "창모드":
            return "windowed"
        if value == "전체화면":
            return "fullscreen"
        return None

    def _int_or_none(self, value: str, name: str) -> int | None:
        value = value.strip()
        if not value:
            return None
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{name} 값은 숫자로 입력해야 합니다.") from exc
        if parsed <= 0:
            raise ValueError(f"{name} 값은 1 이상이어야 합니다.")
        return parsed

    def _args(self, *, dry_run: bool = False, no_apply: bool = False) -> argparse.Namespace:
        kr_root = Path(self.kr_path.get()).expanduser()
        tw_root = Path(self.tw_path.get()).expanduser()
        width = self._int_or_none(self.width_value.get(), "가로 해상도")
        height = self._int_or_none(self.height_value.get(), "세로 해상도")
        return make_args(
            kr_root,
            tw_root,
            dry_run=dry_run,
            display_mode=self._display_mode_arg(),
            width=width,
            height=height,
            no_apply=no_apply,
        )

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in [self.apply_button, self.status_button, self.restore_button, self.launch_button]:
            button.configure(state=state)

    def _run_worker(self, label: str, fn) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_TITLE, "이미 작업이 진행 중입니다.")
            return
        self._set_busy(True)
        self.status_text.set(label)
        self._log(f"\n[{label}] 시작")

        def target() -> None:
            try:
                result = fn()
            except SystemExit as exc:
                self.events.put(("error", str(exc)))
            except Exception:
                self.events.put(("error", traceback.format_exc()))
            else:
                self.events.put(("result", result))
            finally:
                self.events.put(("done", label))

        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "result":
                    self._handle_result(payload)
                elif kind == "error":
                    self._log(str(payload))
                    messagebox.showerror(APP_TITLE, str(payload))
                elif kind == "done":
                    self.status_text.set("완료")
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _handle_result(self, payload: object) -> None:
        if isinstance(payload, dict) and payload.get("action") == "apply":
            self._log(json.dumps(compact_apply_summary(payload), ensure_ascii=False, indent=2))
        else:
            self._log(json.dumps(payload, ensure_ascii=False, indent=2, default=self._json_default))

    @staticmethod
    def _json_default(value: object) -> object:
        if dataclasses.is_dataclass(value):
            return dataclasses.asdict(value)
        if isinstance(value, Path):
            return str(value)
        return repr(value)

    def _log(self, text: str) -> None:
        self.log.insert(END, text + "\n")
        self.log.see(END)

    def apply_patch(self) -> None:
        def work() -> dict[str, object]:
            args = self._args()
            report = core.apply_patch(args)
            if self.launch_after_apply.get():
                launch_args = self._args(no_apply=True)
                launch_report = core.launch(launch_args)
                report["launch"] = launch_report
            return report

        self._run_worker("패치 적용 중", work)

    def check_status(self) -> None:
        self._run_worker("상태 확인 중", lambda: core.status(self._args(dry_run=True)))

    def restore_patch(self) -> None:
        if not messagebox.askyesno(APP_TITLE, "백업된 TW 원본 파일로 복구할까요?"):
            return
        self._run_worker("원본 복구 중", lambda: core.restore_patch(self._args()))

    def launch_game(self) -> None:
        self._run_worker("WindConfig 실행 중", lambda: core.launch(self._args(no_apply=True)))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--self-test"]:
        # Used by packaging smoke tests. This verifies the bundled interpreter,
        # tkinter import, and core patch modules without opening a window.
        _ = default_kr_root()
        _ = default_tw_root()
        return 0
    app = PatchGui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
