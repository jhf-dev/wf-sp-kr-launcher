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
from tkinter import BOTH, END, LEFT, X, filedialog, messagebox
import tkinter as tk
from tkinter import ttk

import wftsp_steam_kr_launcher as core


APP_TITLE = "Wind Fantasy SP KR -> Steam TW 패치 런처"
DISPLAY_UNCHANGED = "건드리지 않음"
DISPLAY_WINDOWED = "창모드"
DISPLAY_FULLSCREEN = "전체화면"


def default_kr_root() -> str:
    return ""


def default_tw_root() -> str:
    detected = core.detect_steam_wftsp_root()
    return str(detected) if detected else ""


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
    summary: dict[str, object] = {
        "changed_overlay_files": changed_files,
        "source_progression_patches": source_patches,
        "wind_dll": wind_dll,
        "registry": registry,
        "backup_dir": report.get("backup_dir"),
    }
    if "launch" in report:
        summary["launch"] = report["launch"]
    return summary


class PatchGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("900x660")
        self.minsize(790, 540)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.worker: threading.Thread | None = None

        self.kr_path = tk.StringVar(value=default_kr_root())
        self.tw_path = tk.StringVar(value=default_tw_root())
        self.display_mode = tk.StringVar(value=DISPLAY_UNCHANGED)
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
            text="기존 한국어판 파일을 읽어 Steam판 Win10 클라이언트에 적용하고, 진행 불가 버그픽스를 함께 반영합니다.",
        )
        subtitle.pack(anchor="w", pady=(4, 4))

        notice = ttk.Label(
            root,
            text="패치에는 기존 한국어판의 리소스 파일이 필요합니다. 이 런처와 패치 파일은 게임 리소스를 포함하지 않습니다.",
            foreground="#8a3d00",
        )
        notice.pack(anchor="w", pady=(0, 14))

        self._path_row(root, "한국어판 폴더", self.kr_path, self._browse_kr)
        self._path_row(root, "Steam 대만판 폴더", self.tw_path, self._browse_tw)

        options = ttk.LabelFrame(root, text="실행 옵션", padding=10)
        options.pack(fill=X, pady=(8, 10))

        ttk.Label(options, text="화면 모드").pack(side=LEFT)
        display = ttk.Combobox(
            options,
            textvariable=self.display_mode,
            values=[DISPLAY_UNCHANGED, DISPLAY_WINDOWED, DISPLAY_FULLSCREEN],
            state="readonly",
            width=14,
        )
        display.pack(side=LEFT, padx=(8, 16))

        ttk.Label(options, text="해상도").pack(side=LEFT)
        ttk.Entry(options, textvariable=self.width_value, width=7).pack(side=LEFT, padx=(8, 4))
        ttk.Label(options, text="x").pack(side=LEFT)
        ttk.Entry(options, textvariable=self.height_value, width=7).pack(side=LEFT, padx=(4, 16))
        ttk.Checkbutton(options, text="적용 후 게임 실행", variable=self.launch_after_apply).pack(side=LEFT)

        buttons = ttk.Frame(root)
        buttons.pack(fill=X, pady=(0, 10))
        self.apply_button = ttk.Button(buttons, text="패치 적용", command=self.apply_patch)
        self.apply_button.pack(side=LEFT)
        self.status_button = ttk.Button(buttons, text="상태 확인", command=self.check_status)
        self.status_button.pack(side=LEFT, padx=(8, 0))
        self.restore_button = ttk.Button(buttons, text="TW 원본 복구", command=self.restore_patch)
        self.restore_button.pack(side=LEFT, padx=(8, 0))
        self.game_button = ttk.Button(buttons, text="게임 실행", command=self.launch_game)
        self.game_button.pack(side=LEFT, padx=(8, 0))
        self.config_button = ttk.Button(buttons, text="WindConfig 실행", command=self.launch_config)
        self.config_button.pack(side=LEFT, padx=(8, 0))

        ttk.Label(root, textvariable=self.status_text).pack(anchor="w", pady=(0, 6))

        log_frame = ttk.Frame(root)
        log_frame.pack(fill=BOTH, expand=True)
        self.log = tk.Text(log_frame, wrap="word", height=18)
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side="right", fill="y")

        if self.tw_path.get():
            self._log(f"Steam판 폴더를 자동으로 찾았습니다: {self.tw_path.get()}")
        else:
            self._log("Steam판 폴더를 자동으로 찾지 못했습니다. Steam 대만판 폴더를 직접 선택해 주세요.")
        self._log("한국어판 폴더는 사용자가 보유한 정식 한국어판 경로를 직접 선택해야 합니다.")

    def _path_row(self, parent: ttk.Frame, label: str, variable: tk.StringVar, command) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=X, pady=4)
        ttk.Label(row, text=label, width=18).pack(side=LEFT)
        ttk.Entry(row, textvariable=variable).pack(side=LEFT, fill=X, expand=True, padx=(0, 8))
        ttk.Button(row, text="찾기", command=command).pack(side=LEFT)

    def _browse_kr(self) -> None:
        initialdir = self.kr_path.get().strip() or str(Path.home())
        selected = filedialog.askdirectory(title="한국어판 폴더 선택", initialdir=initialdir)
        if selected:
            self.kr_path.set(selected)

    def _browse_tw(self) -> None:
        initialdir = self.tw_path.get().strip() or str(Path.home())
        selected = filedialog.askdirectory(title="Steam 대만판 폴더 선택", initialdir=initialdir)
        if selected:
            self.tw_path.set(selected)

    def _display_mode_arg(self) -> str | None:
        value = self.display_mode.get()
        if value == DISPLAY_WINDOWED:
            return "windowed"
        if value == DISPLAY_FULLSCREEN:
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

    def _path_from_entry(self, variable: tk.StringVar, label: str, *, required: bool) -> Path:
        value = variable.get().strip()
        if value:
            return Path(value).expanduser()
        if required:
            raise ValueError(f"{label}를 선택해 주세요.")
        return Path.cwd()

    def _args(
        self,
        *,
        dry_run: bool = False,
        no_apply: bool = False,
        require_kr: bool = True,
    ) -> argparse.Namespace:
        kr_root = self._path_from_entry(self.kr_path, "한국어판 폴더", required=require_kr)
        tw_root = self._path_from_entry(self.tw_path, "Steam 대만판 폴더", required=True)
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
        for button in [
            self.apply_button,
            self.status_button,
            self.restore_button,
            self.game_button,
            self.config_button,
        ]:
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
                launch_args = self._args(no_apply=True, require_kr=False)
                launch_report = core.launch_win10(launch_args)
                report["launch"] = launch_report
            return report

        self._run_worker("패치 적용 중", work)

    def check_status(self) -> None:
        self._run_worker("상태 확인 중", lambda: core.status(self._args(dry_run=True)))

    def restore_patch(self) -> None:
        if not messagebox.askyesno(APP_TITLE, "백업된 TW 원본 파일로 복구할까요?"):
            return
        self._run_worker("원본 복구 중", lambda: core.restore_patch(self._args(require_kr=False)))

    def launch_game(self) -> None:
        self._run_worker(
            "게임 실행 중",
            lambda: core.launch_win10(self._args(no_apply=True, require_kr=False)),
        )

    def launch_config(self) -> None:
        self._run_worker(
            "WindConfig 실행 중",
            lambda: core.launch(self._args(no_apply=True, require_kr=False)),
        )


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
