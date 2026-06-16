from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

LOG_TYPE_COLORS = {
    "start": "#8fd19e",
    "cast": "#8cc8ff",
    "loot": "#b7c7d8",
    "wait": "#d6c98f",
    "bite": "#f0b35a",
    "catch": "#86efac",
    "next": "#9aa7b5",
    "stop": "#d48a8a",
    "warning": "#facc15",
    "error": "#f87171",
    "info": "#cfd8e3",
}

LOG_VISIBLE_LINES = 14


@dataclass(frozen=True)
class OverlaySnapshot:
    title: str = "낚시"
    status: str = "대기 중"
    started_at_text: str = "-"
    duration_text: str = "0초"
    cast_count: int = 0
    catch_count: int = 0
    total_cast_count: int = 0
    total_catch_count: int = 0
    total_run_text: str = "0초"
    logs: List[Any] = field(default_factory=list)
    is_running: bool = False
    anchor_rect: Optional[Tuple[int, int, int, int]] = None
    ready_debug: Optional[Any] = None


@dataclass(frozen=True)
class OverlayCommand:
    name: str
    payload: Any = None


class StatsOverlay:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.width = 420
        self.height = 720
        self.margin = 20

        self._status_label: Optional[tk.Label] = None
        self._started_value_label: Optional[tk.Label] = None
        self._duration_value_label: Optional[tk.Label] = None
        self._cast_value_label: Optional[tk.Label] = None
        self._catch_value_label: Optional[tk.Label] = None
        self._total_cast_value_label: Optional[tk.Label] = None
        self._total_catch_value_label: Optional[tk.Label] = None
        self._total_run_value_label: Optional[tk.Label] = None
        self._ready_debug_frame: Optional[tk.Frame] = None
        self._ready_debug_labels: dict[str, tk.Label] = {}
        self._ready_preview_label: Optional[tk.Label] = None
        self._ready_preview_image: Optional[tk.PhotoImage] = None
        self._log_labels: List[tk.Label] = []
        self._roi_window: Optional[tk.Toplevel] = None
        self._roi_complete_callback: Optional[Callable[[Optional[Tuple[int, int, int, int]]], None]] = None
        self._snapshot = OverlaySnapshot()

        self._configure_window()
        self._build_ui()

    def _configure_window(self) -> None:
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.95)
        self.root.configure(bg="#08131f")
        self.root.geometry(f"{self.width}x{self.height}+100+100")
        self.root.withdraw()

    def _build_ui(self) -> None:
        panel = tk.Frame(
            self.root,
            bg="#0f1a27",
            highlightbackground="#a0bedc",
            highlightthickness=1,
            bd=0,
        )
        panel.pack(fill="both", expand=True, padx=2, pady=2)

        header = tk.Frame(panel, bg="#152334", bd=0)
        header.pack(fill="x", padx=10, pady=(10, 8))

        tk.Label(
            header,
            text="낚시",
            bg="#152334",
            fg="#dbe7f3",
            font=("Malgun Gothic", 13, "bold"),
        ).pack(side="left")

        self._status_label = tk.Label(
            header,
            text="대기 중",
            bg="#152334",
            fg="#9aa7b5",
            font=("Malgun Gothic", 11, "bold"),
        )
        self._status_label.pack(side="right")

        session_frame = self._create_section(panel, "세션 통계")
        self._started_value_label = self._add_stat_row(session_frame, "시작 시간", "-")
        self._duration_value_label = self._add_stat_row(session_frame, "오늘 실행 시간", "0초")
        self._cast_value_label = self._add_stat_row(session_frame, "오늘 시도", "0회")
        self._catch_value_label = self._add_stat_row(session_frame, "오늘 성공", "0회")

        total_frame = self._create_section(panel, "누적 통계")
        self._total_cast_value_label = self._add_stat_row(total_frame, "전체 시도", "0회")
        self._total_catch_value_label = self._add_stat_row(total_frame, "전체 성공", "0회")
        self._total_run_value_label = self._add_stat_row(total_frame, "전체 실행 시간", "0초")

        ready_debug_body = self._create_section(panel, "READY DEBUG")
        self._ready_debug_frame = ready_debug_body.master
        for key, label_text in (
            ("status", "상태"),
            ("search_roi", "탐색 ROI"),
            ("active_roi", "찌 ROI"),
            ("ready_color", "색상 감지"),
            ("ready_color_blob", "색상 Blob"),
            ("ready_color_hsv", "색상 HSV"),
            ("roi", "ROI"),
            ("score", "점수"),
            ("pos", "좌표"),
            ("reason", "이유"),
            ("elapsed", "경과"),
            ("scan_time", "스캔"),
        ):
            self._ready_debug_labels[key] = self._add_stat_row(ready_debug_body, label_text, "-")

        self._ready_preview_label = tk.Label(
            ready_debug_body,
            text="",
            bg="#162334",
            fg="#8fa3b7",
            width=220,
            height=120,
        )
        self._ready_preview_label.pack(fill="x", pady=(4, 0))

        log_frame = self._create_section(panel, "최근 로그", expand=True)
        log_body = tk.Frame(log_frame, bg="#162334")
        log_body.pack(fill="both", expand=True)

        for _ in range(LOG_VISIBLE_LINES):
            label = tk.Label(
                log_body,
                text="",
                justify="left",
                anchor="w",
                bg="#162334",
                fg="#cfd8e3",
                font=("Malgun Gothic", 9),
            )
            label.pack(fill="x", pady=1)
            self._log_labels.append(label)

    def _create_section(self, parent: tk.Widget, title: str, expand: bool = False) -> tk.Frame:
        frame = tk.Frame(
            parent,
            bg="#162334",
            highlightbackground="#5f768d",
            highlightthickness=1,
            bd=0,
        )
        frame.pack(fill="x" if not expand else "both", expand=expand, padx=10, pady=(0, 8))

        tk.Label(
            frame,
            text=title,
            bg="#162334",
            fg="#9fb7cc",
            anchor="w",
            font=("Malgun Gothic", 10, "bold"),
        ).pack(fill="x", padx=10, pady=(8, 4))

        body = tk.Frame(frame, bg="#162334")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        return body

    def _add_stat_row(self, parent: tk.Widget, key: str, value: str) -> tk.Label:
        row = tk.Frame(parent, bg="#162334")
        row.pack(fill="x", pady=1)

        tk.Label(
            row,
            text=key,
            bg="#162334",
            fg="#8fa3b7",
            anchor="w",
            font=("Malgun Gothic", 10),
        ).pack(side="left")

        value_label = tk.Label(
            row,
            text=value,
            bg="#162334",
            fg="#e6edf5",
            anchor="e",
            font=("Malgun Gothic", 10, "bold"),
        )
        value_label.pack(side="right")
        return value_label

    def show(self) -> None:
        self._position_bottom_right(self._snapshot.anchor_rect)
        self.root.deiconify()
        self.root.lift()

    def hide(self) -> None:
        self.root.withdraw()

    def _position_bottom_right(self, anchor_rect: Optional[Tuple[int, int, int, int]]) -> None:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        if anchor_rect is not None:
            left, top, width, height = anchor_rect
            x = left + width - self.width - self.margin
            y = top + height - self.height - self.margin
        else:
            x = screen_width - self.width - self.margin
            y = screen_height - self.height - 60

        x = min(max(0, x), max(0, screen_width - self.width))
        y = min(max(0, y), max(0, screen_height - self.height))
        self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")

    def update_snapshot(self, snapshot: OverlaySnapshot) -> None:
        self._snapshot = snapshot
        self._render()

    def _render(self) -> None:
        snapshot = self._snapshot

        self._apply_running_state_style(snapshot)
        self._position_bottom_right(snapshot.anchor_rect)

        if self._status_label is not None:
            self._status_label.configure(text=snapshot.status)
            if snapshot.status == "실행 중":
                self._status_label.configure(fg="#8fd19e")
            elif snapshot.status == "중단됨":
                self._status_label.configure(fg="#d48a8a")
            else:
                self._status_label.configure(fg="#9aa7b5")

        if self._started_value_label is not None:
            self._started_value_label.configure(text=snapshot.started_at_text)
        if self._duration_value_label is not None:
            self._duration_value_label.configure(text=snapshot.duration_text)
        if self._cast_value_label is not None:
            self._cast_value_label.configure(text=f"{snapshot.cast_count}회")
        if self._catch_value_label is not None:
            self._catch_value_label.configure(text=f"{snapshot.catch_count}회")
        if self._total_cast_value_label is not None:
            self._total_cast_value_label.configure(text=f"{snapshot.total_cast_count:,}회")
        if self._total_catch_value_label is not None:
            self._total_catch_value_label.configure(text=f"{snapshot.total_catch_count:,}회")
        if getattr(self, "_total_run_value_label", None) is not None:
            self._total_run_value_label.configure(text=snapshot.total_run_text)

        self._render_ready_debug(snapshot)

        render_entries = self._build_log_render_entries(snapshot.logs)
        for idx, label in enumerate(self._log_labels):
            self._apply_log_label(label, render_entries[idx])

    def _render_ready_debug(self, snapshot: OverlaySnapshot) -> None:
        ready_debug = snapshot.ready_debug if snapshot.is_running else None
        should_show = bool(ready_debug and ready_debug.get("enabled", False))

        if self._ready_debug_frame is not None:
            if should_show:
                self._ready_debug_frame.pack(fill="x", padx=10, pady=(0, 8))
            else:
                self._ready_debug_frame.pack_forget()

        if not should_show:
            self._ready_preview_image = None
            if self._ready_preview_label is not None:
                self._ready_preview_label.configure(image="", text="")
            return

        score = ready_debug.get("score")
        threshold = ready_debug.get("threshold")
        scale = ready_debug.get("scale")
        roi_type = str(ready_debug.get("roi_type", "-"))
        roi_value = ready_debug.get("roi")
        if roi_value:
            roi_text = f"{roi_type} {roi_value}"
        else:
            roi_text = roi_type
        candidates = ready_debug.get("candidates") or []
        if candidates:
            candidate_text = " / ".join(
                f"{idx + 1}:{float(candidate.get('score', 0.0)):.2f}@{candidate.get('center')}"
                for idx, candidate in enumerate(candidates[:3])
                if isinstance(candidate, dict)
            )
        else:
            candidate_text = ""
        score_text = "-"
        if score is not None and threshold is not None:
            score_text = f"{score:.3f} / {threshold:.3f}"
            if scale is not None:
                score_text = f"{score_text}  x{scale:.2f}"

        values = {
            "status": str(ready_debug.get("status", "-")),
            "search_roi": str(ready_debug.get("search_roi", "-") or "-"),
            "active_roi": str(ready_debug.get("active_bobber_roi", "-") or "-"),
            "ready_color": self._format_ready_color_status(ready_debug),
            "ready_color_blob": self._format_ready_color_blob(ready_debug),
            "ready_color_hsv": self._format_ready_color_hsv(ready_debug),
            "roi": roi_text,
            "score": score_text,
            "pos": str(ready_debug.get("pos", "-")),
            "reason": candidate_text or str(ready_debug.get("skip_reason", "-") or "-"),
            "elapsed": str(ready_debug.get("elapsed", "-") or "-"),
            "scan_time": str(ready_debug.get("scan_time", "-")),
        }

        for key, value in values.items():
            label = self._ready_debug_labels.get(key)
            if label is not None:
                label.configure(text=value)

        image_updated = bool(ready_debug.get("image_updated", False))
        image_data = ready_debug.get("image")
        if self._ready_preview_label is not None:
            if not image_updated:
                return

            if image_data:
                try:
                    self._ready_preview_image = tk.PhotoImage(data=image_data, format="png")
                    self._ready_preview_label.configure(image=self._ready_preview_image, text="")
                except tk.TclError:
                    self._ready_preview_image = None
                    self._ready_preview_label.configure(image="", text="preview unavailable")
            else:
                self._ready_preview_image = None
                self._ready_preview_label.configure(image="", text="")

    def _format_ready_color_status(self, ready_debug: Any) -> str:
        ready_color = ready_debug.get("ready_color") or {}
        enabled = bool(ready_color.get("enabled", False))
        state = "on" if enabled else "off"
        hits = int(ready_color.get("hits", 0) or 0)
        required = int(ready_color.get("required", 0) or 0)
        return f"{state} {hits}/{required}"

    def _format_ready_color_blob(self, ready_debug: Any) -> str:
        ready_color = ready_debug.get("ready_color") or {}
        blob = ready_color.get("blob")
        area = ready_color.get("area")
        if not blob:
            return "-"
        if area is None:
            return str(blob)
        return f"{blob} area={float(area):.1f}"

    def _format_ready_color_hsv(self, ready_debug: Any) -> str:
        ready_color = ready_debug.get("ready_color") or {}
        lower = ready_color.get("hsv_lower")
        upper = ready_color.get("hsv_upper")
        if not lower or not upper:
            return "-"
        return f"{lower}-{upper}"

    def _build_log_render_entries(self, logs: Optional[List[Any]]) -> List[Any]:
        visible_logs = list(logs or [])[-LOG_VISIBLE_LINES:]

        if not visible_logs:
            render_entries: List[Any] = ["대기 중..."] + [None] * (LOG_VISIBLE_LINES - 1)
        else:
            render_entries = list(reversed(visible_logs))

        if len(render_entries) > LOG_VISIBLE_LINES:
            render_entries = render_entries[:LOG_VISIBLE_LINES]

        if len(render_entries) < LOG_VISIBLE_LINES:
            render_entries = render_entries + [None] * (LOG_VISIBLE_LINES - len(render_entries))

        if __debug__:
            assert len(render_entries) == LOG_VISIBLE_LINES
            if visible_logs:
                assert render_entries[0] == visible_logs[-1]

        return render_entries

    def _apply_running_state_style(self, snapshot: OverlaySnapshot) -> None:
        status = snapshot.status
        if snapshot.is_running or status == "실행 중":
            alpha = 0.95
        elif status == "중단됨":
            alpha = 0.50
        else:
            alpha = 0.58

        self.root.attributes("-alpha", alpha)

    def _apply_log_label(self, label: tk.Label, log_entry: Any) -> None:
        if log_entry is None:
            label.configure(text="")
            return

        if hasattr(log_entry, "message"):
            text = f"{getattr(log_entry, 'time', '')} {getattr(log_entry, 'message', '')}".strip()
            log_type = getattr(log_entry, "type", "info")
        elif isinstance(log_entry, dict):
            time_text = str(log_entry.get("time", ""))
            message = str(log_entry.get("message", ""))
            text = f"{time_text} {message}".strip()
            log_type = str(log_entry.get("type", "info"))
        else:
            text = str(log_entry)
            log_type = "info"

        color = LOG_TYPE_COLORS.get(log_type, LOG_TYPE_COLORS["info"])
        label.configure(text=text, fg=color)

    def start_roi_selection(
        self,
        on_complete: Callable[[Optional[Tuple[int, int, int, int]]], None],
    ) -> None:
        self.cancel_roi_selection(notify=False)
        self._roi_complete_callback = on_complete

        window = tk.Toplevel(self.root)
        self._roi_window = window
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.attributes("-alpha", 0.30)
        window.configure(bg="black")

        screen_width = window.winfo_screenwidth()
        screen_height = window.winfo_screenheight()
        window.geometry(f"{screen_width}x{screen_height}+0+0")

        canvas = tk.Canvas(window, bg="black", highlightthickness=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        canvas.create_text(
            screen_width // 2,
            42,
            text="Drag fishing search area",
            fill="#ffffff",
            font=("Malgun Gothic", 18, "bold"),
        )

        selection_rect: dict[str, Optional[int]] = {"id": None}
        drag_start: dict[str, Optional[int]] = {"x": None, "y": None}

        def normalize_roi(start_x: int, start_y: int, end_x: int, end_y: int) -> Tuple[int, int, int, int]:
            left = min(start_x, end_x)
            top = min(start_y, end_y)
            right = max(start_x, end_x)
            bottom = max(start_y, end_y)
            return left, top, right - left, bottom - top

        def on_press(event) -> None:
            drag_start["x"] = int(event.x_root)
            drag_start["y"] = int(event.y_root)
            if selection_rect["id"] is not None:
                canvas.delete(selection_rect["id"])
            selection_rect["id"] = canvas.create_rectangle(
                event.x,
                event.y,
                event.x,
                event.y,
                outline="#2dd4bf",
                width=3,
            )

        def on_drag(event) -> None:
            rect_id = selection_rect["id"]
            start_x = drag_start["x"]
            start_y = drag_start["y"]
            if rect_id is None or start_x is None or start_y is None:
                return
            local_start_x = start_x - window.winfo_rootx()
            local_start_y = start_y - window.winfo_rooty()
            canvas.coords(rect_id, local_start_x, local_start_y, event.x, event.y)

        def on_release(event) -> None:
            start_x = drag_start["x"]
            start_y = drag_start["y"]
            if start_x is None or start_y is None:
                self._finish_roi_selection(None)
                return
            roi = normalize_roi(start_x, start_y, int(event.x_root), int(event.y_root))
            self._finish_roi_selection(roi)

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        window.bind("<Escape>", lambda _event=None: self._finish_roi_selection(None))
        window.bind("<ButtonPress-3>", lambda _event=None: self._finish_roi_selection(None))
        window.focus_force()

    def cancel_roi_selection(self, notify: bool = True) -> None:
        if self._roi_window is None:
            return
        self._finish_roi_selection(None if notify else None, notify=notify)

    def _finish_roi_selection(
        self,
        roi: Optional[Tuple[int, int, int, int]],
        notify: bool = True,
    ) -> None:
        callback = self._roi_complete_callback if notify else None
        window = self._roi_window
        self._roi_complete_callback = None
        self._roi_window = None

        if window is not None:
            try:
                if window.winfo_exists():
                    window.destroy()
            except tk.TclError:
                pass

        if callback is not None:
            self.root.after(50, lambda: callback(roi))


class OverlayController:
    def __init__(self) -> None:
        self._queue: "queue.Queue[OverlayCommand]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self._visible = False
        self._started_lock = threading.Lock()

    def start(self) -> None:
        with self._started_lock:
            if self._started:
                return
            self._started = True
            print("[overlay] start requested")
            self._thread = threading.Thread(
                target=self._run_ui,
                name="FishingStatsOverlay",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._queue.put(OverlayCommand("shutdown"))

    def update_snapshot(self, snapshot: OverlaySnapshot) -> None:
        if not self._started:
            self.start()
        self._queue.put(OverlayCommand("snapshot", snapshot))

    def request_roi_selection(
        self,
        on_complete: Callable[[Optional[Tuple[int, int, int, int]]], None],
    ) -> None:
        if not self._started:
            self.start()
        self._queue.put(OverlayCommand("roi_select", on_complete))

    def cancel_roi_selection(self) -> None:
        if not self._started:
            return
        self._queue.put(OverlayCommand("roi_cancel"))

    def _run_ui(self) -> None:
        print("[overlay] ui thread started")
        root = tk.Tk()
        print("[overlay] root created")
        overlay = StatsOverlay(root)

        def poll_queue() -> None:
            latest: Optional[OverlaySnapshot] = None
            should_shutdown = False
            while True:
                try:
                    command = self._queue.get_nowait()
                except queue.Empty:
                    break
                if command.name == "snapshot":
                    latest = command.payload
                elif command.name == "roi_select":
                    overlay.start_roi_selection(command.payload)
                elif command.name == "roi_cancel":
                    overlay.cancel_roi_selection()
                elif command.name == "shutdown":
                    should_shutdown = True

            if latest is not None:
                print(f"[overlay] snapshot received {latest.status} {len(latest.logs)}")
                overlay.update_snapshot(latest)
                if latest.is_running:
                    if not self._visible:
                        overlay.show()
                        self._visible = True
                        print("[overlay] show")
                elif self._visible:
                    overlay.hide()
                    self._visible = False
                    print("[overlay] hide")

            if should_shutdown:
                overlay.cancel_roi_selection(notify=False)
                root.destroy()
                return

            root.after(200, poll_queue)

        poll_queue()
        root.mainloop()

