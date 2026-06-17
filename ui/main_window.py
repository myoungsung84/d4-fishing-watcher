from __future__ import annotations

import logging
import queue
import re
import threading
import tkinter as tk
from dataclasses import dataclass
from enum import Enum, auto
from tkinter import ttk
from typing import Callable, Literal, TypedDict

from app.logger import AppLogger
from ui.fonts import load_pretendard
from app.settings import (
    AppSettings,
    DEFAULT_SETTINGS,
    load_settings,
    save_settings,
    validate_game_keys,
    validate_hotkey_pair,
)
from app.state import AppStage, RunStatus, WindowStatus
from core.hotkeys import GlobalHotkeyManager, normalize_tk_key
from core.screen import WindowRect
from features.fishing import engine
from features.fishing.worker import FishingWorker
from ui.icons import TkAppIcons, load_tk_app_icons
from ui.theme import COLORS, apply_theme
from ui.windows import apply_windows_window_polish, get_window_work_area, set_windows_app_user_model_id

LOGGER = logging.getLogger(__name__)

RoiRect = tuple[int, int, int, int]
SettingCaptureTarget = Literal["start", "stop", "social", "interact", "reel"]


class CaptureTargetState(TypedDict):
    name: SettingCaptureTarget | None


class SelectionRectState(TypedDict):
    id: int | None


class DragStartState(TypedDict):
    x: int | None
    y: int | None


class WorkerEventType(Enum):
    LOG = auto()
    STATE = auto()
    STEP = auto()
    WINDOW = auto()


@dataclass(frozen=True)
class WorkerEvent:
    event_type: WorkerEventType
    payload: str | RunStatus | AppStage | WindowStatus


class D4FishingWatcherWindow:
    _UI_STAGE_BY_APP_STAGE: dict[AppStage, int] = {
        AppStage.CAST: 0,
        AppStage.LOOT: 1,
        AppStage.FIND_BOBBER: 2,
        AppStage.WAIT_READY: 2,
        AppStage.REEL: 3,
    }
    _CIRCLE_TEXT_BY_APP_STAGE: dict[AppStage, tuple[str, str]] = {
        AppStage.READY_TO_RUN: ("시작 준비", "낚시 엔진 준비 중"),
        AppStage.FIND_WINDOW: ("창 확인", "Diablo IV 창 확인 중"),
        AppStage.CAST: ("낚시 던짐", "낚싯대를 던집니다"),
        AppStage.FIND_BOBBER: ("입질 대기", "물고기가 물 때까지 기다립니다"),
        AppStage.WAIT_READY: ("입질 대기", "입질 신호를 기다립니다"),
        AppStage.REEL: ("회수", "입질 후 낚싯대를 회수합니다"),
        AppStage.LOOT: ("줍기", "전리품을 주워 인벤토리로 옮깁니다"),
        AppStage.RECAST: ("다음 준비", "다음 낚시를 준비합니다"),
        AppStage.STOPPING: ("중지 중", "현재 동작을 마무리합니다"),
        AppStage.ERROR: ("오류", "확인이 필요합니다"),
    }
    _UI_STAGE_LABELS = ("낚시 던짐", "줍기", "입질 대기", "회수")
    _UI_STAGE_DETAILS = (
        "낚싯대를 던집니다",
        "전리품을 주워\n인벤토리로 옮깁니다",
        "물고기가 물 때까지\n기다립니다",
        "입질 후 낚싯대를\n회수합니다",
    )

    def __init__(self) -> None:
        self._font_family: str = load_pretendard()
        set_windows_app_user_model_id("D4FishingWatcher.App")
        self.root: tk.Tk = tk.Tk()
        self.root.title("D4 Fishing Watcher")
        self.root.geometry("650x780")
        self.root.minsize(620, 720)
        self._icons: TkAppIcons = load_tk_app_icons()
        self._apply_window_icon(self.root)
        apply_windows_window_polish(self.root)

        self.window_status_var = tk.StringVar(value="Diablo IV 창을 찾지 못했습니다")
        self.window_detail_var = tk.StringVar(value="찾을 수 없음")
        self.run_status_var = tk.StringVar(value=RunStatus.IDLE.value)
        self.stage_var = tk.StringVar(value=AppStage.IDLE.value)
        self.roi_status_var = tk.StringVar(value="게임 창을 먼저 확인해 주세요")
        self.status_badge_var = tk.StringVar(value="● 확인 필요")
        self.headline_var = tk.StringVar(value="지금은 준비 중입니다")
        self.detail_var = tk.StringVar(value="Diablo IV 창을 확인해 주세요.")
        self.last_message_var = tk.StringVar(value="아직 기록이 없습니다.")
        self.hotkey_summary_var = tk.StringVar(value="")
        self.game_key_summary_var = tk.StringVar(value="")
        self.primary_action_var = tk.StringVar(value="낚시 시작")
        self.primary_hint_var = tk.StringVar(value="")
        self.today_cast_count_var = tk.StringVar(value="0회")
        self.today_catch_count_var = tk.StringVar(value="0회")
        self.today_run_time_var = tk.StringVar(value="0분")
        self.total_cast_count_var = tk.StringVar(value="0회")
        self.total_catch_count_var = tk.StringVar(value="0회")
        self.total_run_time_var = tk.StringVar(value="0분")
        self.detection_status_var = tk.StringVar(value="대기")

        self.worker_events: queue.Queue[WorkerEvent] = queue.Queue()
        self.ui_callbacks: queue.Queue[Callable[[], None]] = queue.Queue()
        self._fishing_worker: FishingWorker | None = None
        self._roi_window: tk.Toplevel | None = None
        self._hotkey_window: tk.Toplevel | None = None
        self._worker_status = RunStatus.IDLE
        self._detected_window_rect: WindowRect | None = None
        self._settings: AppSettings = load_settings()
        engine.set_runtime_game_keys(self._settings.game_keys)
        self._hotkey_manager = GlobalHotkeyManager(
            on_start=lambda: self._ui_call(self._on_start_hotkey),
            on_stop=lambda: self._ui_call(self._on_stop_hotkey),
        )
        self._closing = False
        self._notice_lines: list[str] = []
        self._max_notice_lines = 80
        self._current_stage: AppStage | None = None

        self.logger = AppLogger()
        engine.load_fishing_stats()

        self.status_badge: ttk.Label
        self.header_settings_button: ttk.Button
        self.primary_button: ttk.Button
        self.primary_hint_label: ttk.Label
        self._main_canvas: tk.Canvas
        self._circle_arc_id: int = 0
        self._circle_spin_id: int = 0
        self._circle_title_id: int = 0
        self._circle_detail_id: int = 0
        self._stage_dot_ids: list[int] = []
        self._stage_label_ids: list[int] = []
        self._stage_detail_ids: list[int] = []
        self._stage_line_ids: list[int] = []
        self._spin_angle: float = 90.0
        self._spin_running: bool = False

        self._build_layout()
        self._set_runtime_state(RunStatus.IDLE, AppStage.IDLE)
        self._refresh_roi_status()
        self._refresh_hotkey_summary()
        self._refresh_game_key_summary()
        self._register_hotkeys_on_startup()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_worker_events)
        self.root.after(1000, self._refresh_stats_loop)
        self._notice("프로그램이 준비되었습니다.")
        self._update_controls_for_state()
        self.logger.info("[BOOT] GUI ready. 메인 윈도우 실행 흐름 준비 완료.")

    def run(self) -> None:
        self.root.mainloop()

    def _build_layout(self) -> None:
        F = self._font_family
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)
        self._configure_styles()

        outer_pad = 18

        header = ttk.Frame(self.root, padding=(outer_pad, 14, outer_pad, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        if self._icons.header is not None:
            icon = ttk.Label(header, image=self._icons.header)
            icon.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 14))
        ttk.Label(header, text="D4 Fishing Watcher", style="HeroTitle.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="Diablo IV 낚시 도우미", style="HeroSubtitle.TLabel").grid(row=1, column=1, sticky="w", pady=(2, 0))
        self.status_badge = ttk.Label(header, textvariable=self.status_badge_var, style="Badge.TLabel")
        self.status_badge.grid(row=0, column=2, rowspan=2, sticky="e", padx=(12, 12))
        self.header_settings_button = ttk.Button(
            header,
            text="설정",
            command=self._open_hotkey_settings_window,
            style="Header.TButton",
        )
        self.header_settings_button.grid(row=0, column=3, rowspan=2, sticky="e")

        notice = ttk.Frame(self.root, padding=(18, 13), style="Panel.TFrame")
        notice.grid(row=1, column=0, sticky="ew", padx=outer_pad, pady=(0, 10))
        notice.columnconfigure(0, weight=1)
        ttk.Label(notice, textvariable=self.headline_var, style="NoticeLead.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(notice, textvariable=self.detail_var, style="NoticeMuted.TLabel", wraplength=560).grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(5, 0),
        )
        action = ttk.Frame(notice, style="Panel.TFrame")
        action.grid(row=0, column=1, rowspan=2, sticky="e", padx=(18, 0))
        self.primary_button = ttk.Button(
            action,
            textvariable=self.primary_action_var,
            style="Primary.TButton",
            command=self._on_primary_action,
            width=14,
        )
        self.primary_button.grid(row=0, column=0)
        self.primary_hint_label = ttk.Label(
            action,
            textvariable=self.primary_hint_var,
            style="NoticeMuted.TLabel",
            anchor="center",
        )
        self.primary_hint_label.grid(row=1, column=0, pady=(5, 0), sticky="ew")

        main_panel = ttk.Frame(self.root, padding=(0, 0), style="Panel.TFrame")
        main_panel.grid(row=2, column=0, sticky="nsew", padx=outer_pad, pady=(0, 10))
        main_panel.columnconfigure(0, weight=1)
        main_panel.rowconfigure(0, weight=1)
        self._main_canvas = tk.Canvas(main_panel, height=330, bg=COLORS["panel"], highlightthickness=0)
        self._main_canvas.grid(row=0, column=0, sticky="nsew")
        self._main_canvas.bind("<Configure>", self._redraw_main_canvas)

        stats = ttk.Frame(self.root, padding=(18, 10), style="Panel.TFrame")
        stats.grid(row=3, column=0, sticky="ew", padx=outer_pad, pady=(0, 16))
        stats.columnconfigure(0, minsize=72)
        for col in (1, 2, 3):
            stats.columnconfigure(col, weight=1, uniform="stats")
        ttk.Label(stats, text="", style="StatsHead.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12))
        ttk.Label(stats, text="시도", style="StatsHead.TLabel", anchor="e").grid(row=0, column=1, sticky="ew")
        ttk.Label(stats, text="성공", style="StatsHead.TLabel", anchor="e").grid(row=0, column=2, sticky="ew")
        ttk.Label(stats, text="시간", style="StatsHead.TLabel", anchor="e").grid(row=0, column=3, sticky="ew")
        ttk.Separator(stats, orient="horizontal").grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Label(stats, text="오늘", style="StatsRowHead.TLabel").grid(row=2, column=0, sticky="w", pady=(9, 0))
        ttk.Label(stats, textvariable=self.today_cast_count_var, style="StatsValue.TLabel", anchor="e").grid(row=2, column=1, sticky="ew", pady=(9, 0))
        ttk.Label(stats, textvariable=self.today_catch_count_var, style="StatsValue.TLabel", anchor="e").grid(row=2, column=2, sticky="ew", pady=(9, 0))
        ttk.Label(stats, textvariable=self.today_run_time_var, style="StatsValue.TLabel", anchor="e").grid(row=2, column=3, sticky="ew", pady=(9, 0))
        ttk.Separator(stats, orient="horizontal").grid(row=3, column=0, columnspan=4, sticky="ew", pady=(9, 0))
        ttk.Label(stats, text="전체", style="StatsRowHead.TLabel").grid(row=4, column=0, sticky="w", pady=(9, 0))
        ttk.Label(stats, textvariable=self.total_cast_count_var, style="StatsValue.TLabel", anchor="e").grid(row=4, column=1, sticky="ew", pady=(9, 0))
        ttk.Label(stats, textvariable=self.total_catch_count_var, style="StatsValue.TLabel", anchor="e").grid(row=4, column=2, sticky="ew", pady=(9, 0))
        ttk.Label(stats, textvariable=self.total_run_time_var, style="StatsValue.TLabel", anchor="e").grid(row=4, column=3, sticky="ew", pady=(9, 0))

    def _configure_styles(self) -> None:
        apply_theme(self.root, font_family=self._font_family)

    def _apply_window_icon(self, window: tk.Tk | tk.Toplevel) -> None:
        self._icons.apply_to(window)

    def _draw_kbd_icon(self, c: tk.Canvas) -> None:
        w, h = 34, 24
        c.create_rectangle(2, 4, w - 2, h - 3, outline=COLORS["muted"], width=1, fill=COLORS["panel_alt"])
        for kx in (6, 11, 16, 21, 26):
            c.create_rectangle(kx - 2, 7, kx + 2, 11, fill=COLORS["muted"], outline="")
        for kx in (8, 14, 20):
            c.create_rectangle(kx - 3, 14, kx + 3, 18, fill=COLORS["muted"], outline="")

    def _redraw_main_canvas(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        canvas = self._main_canvas
        canvas.delete("all")
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        F = self._font_family
        cx = width // 2
        top_y = 16
        top_h = max(190, int(height * 0.62))
        box_w = 190 if width < 700 else 204
        box_h = 112
        box_gap = 24
        box_x = width - box_w - 18
        max_radius_for_box = box_x - cx - box_gap
        cy = top_y + top_h // 2
        radius = max(76, min(98, top_h // 2 - 14, max_radius_for_box))
        ring_width = 12

        self._draw_round_rect(canvas, 1, 1, width - 2, height - 2, 10, COLORS["panel"], COLORS["panel"])
        canvas.create_oval(cx - radius + 16, cy - radius + 16, cx + radius - 16, cy + radius - 16, fill="#0a1828", outline="")
        canvas.create_arc(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            start=90,
            extent=-359.99,
            style=tk.ARC,
            outline=COLORS["panel_alt"],
            width=ring_width,
        )
        self._circle_arc_id = canvas.create_arc(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            start=90,
            extent=0,
            style=tk.ARC,
            outline=COLORS["accent"],
            width=ring_width,
        )
        self._circle_spin_id = canvas.create_arc(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            start=self._spin_angle,
            extent=-42,
            style=tk.ARC,
            outline="#75d7ff",
            width=4,
        )
        self._circle_title_id = canvas.create_text(cx, cy - 12, text="대기 중", fill=COLORS["text"], font=(F, 22, "bold"))
        self._circle_detail_id = canvas.create_text(cx, cy + 23, text="게임 창 확인 대기", fill=COLORS["muted"], font=(F, 12), width=radius * 2 - 28)

        box_y = top_y + max(0, (top_h - box_h) // 2)
        if box_x >= cx + radius + box_gap:
            self._draw_round_rect(canvas, box_x, box_y, box_x + box_w, box_y + box_h, 8, COLORS["panel_soft"], COLORS["border"])
            canvas.create_text(box_x + 18, box_y + 28, text="단축키", fill=COLORS["text"], font=(F, 11, "bold"), anchor="w")
            key_x = box_x + box_w - 20
            label_x = box_x + 18
            canvas.create_text(label_x, box_y + 68, text="낚시 시작/중지", fill=COLORS["muted"], font=(F, 9), anchor="w")
            canvas.create_text(key_x, box_y + 68, text=self._settings.hotkeys.start_fishing, fill=COLORS["text"], font=(F, 11, "bold"), anchor="e")
            canvas.create_text(label_x, box_y + 90, text="낚시 위치 설정", fill=COLORS["muted"], font=(F, 9), anchor="w")
            canvas.create_text(key_x, box_y + 94, text=self._settings.hotkeys.stop_fishing, fill=COLORS["text"], font=(F, 11, "bold"), anchor="e")

        stage_y = max(cy + radius + 22, int(height * 0.67))
        self._draw_stage_progress(canvas, width, height, stage_y)
        self._update_circle(self._current_stage)
        self._update_stage_progress(self._current_stage)

    def _draw_round_rect(
        self,
        canvas: tk.Canvas,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        radius: int,
        fill: str,
        outline: str,
    ) -> None:
        canvas.create_rectangle(x0 + radius, y0, x1 - radius, y1, fill=fill, outline=fill)
        canvas.create_rectangle(x0, y0 + radius, x1, y1 - radius, fill=fill, outline=fill)
        canvas.create_arc(x0, y0, x0 + radius * 2, y0 + radius * 2, start=90, extent=90, fill=fill, outline=fill)
        canvas.create_arc(x1 - radius * 2, y0, x1, y0 + radius * 2, start=0, extent=90, fill=fill, outline=fill)
        canvas.create_arc(x1 - radius * 2, y1 - radius * 2, x1, y1, start=270, extent=90, fill=fill, outline=fill)
        canvas.create_arc(x0, y1 - radius * 2, x0 + radius * 2, y1, start=180, extent=90, fill=fill, outline=fill)
        canvas.create_line(x0 + radius, y0, x1 - radius, y0, fill=outline)
        canvas.create_line(x0 + radius, y1, x1 - radius, y1, fill=outline)
        canvas.create_line(x0, y0 + radius, x0, y1 - radius, fill=outline)
        canvas.create_line(x1, y0 + radius, x1, y1 - radius, fill=outline)

    def _draw_stage_progress(self, canvas: tk.Canvas, width: int, height: int, y: int) -> None:
        F = self._font_family
        label_y = y + 40
        detail_y = y + 65
        margin = max(58, min(88, int(width * 0.11)))
        available = max(1, width - margin * 2)
        step = available / 3
        self._stage_dot_ids = []
        self._stage_label_ids = []
        self._stage_detail_ids = []
        self._stage_line_ids = []
        positions = [int(margin + step * i) for i in range(4)]
        for i in range(3):
            line_id = canvas.create_line(positions[i] + 32, y, positions[i + 1] - 32, y, fill=COLORS["border"], width=2)
            self._stage_line_ids.append(line_id)
        for index, x in enumerate(positions):
            dot_id = canvas.create_oval(x - 17, y - 17, x + 17, y + 17, fill=COLORS["panel_alt"], outline=COLORS["border"], width=2)
            label_id = canvas.create_text(x, label_y, text=self._UI_STAGE_LABELS[index], fill=COLORS["text"], font=(F, 11, "bold"))
            detail_id = canvas.create_text(
                x,
                detail_y,
                text=self._UI_STAGE_DETAILS[index],
                fill=COLORS["muted"],
                font=(F, 10),
                justify="center",
                width=128,
            )
            self._stage_dot_ids.append(dot_id)
            self._stage_label_ids.append(label_id)
            self._stage_detail_ids.append(detail_id)

    def _animate_spin(self) -> None:
        if not self._spin_running:
            if self._circle_spin_id:
                self._main_canvas.itemconfigure(self._circle_spin_id, extent=0)
            return
        self._spin_angle = (self._spin_angle - 9) % 360
        if self._circle_spin_id:
            self._main_canvas.itemconfigure(self._circle_spin_id, start=self._spin_angle, extent=-42)
        self.root.after(55, self._animate_spin)

    def _update_circle(self, stage: AppStage | None) -> None:
        if self._circle_arc_id == 0:
            return
        status = self._worker_status
        show_ring = False
        if status is RunStatus.RUNNING and stage is not None:
            active = self._UI_STAGE_BY_APP_STAGE.get(stage)
            if active is not None:
                extent = -((active + 1) * 90)
                show_ring = True
            elif stage in (AppStage.RECAST, AppStage.STOPPING):
                extent = -18
                show_ring = True
            else:
                extent = 0
            title, detail = self._CIRCLE_TEXT_BY_APP_STAGE.get(stage, ("낚시 중", "진행 중입니다"))
        elif status is RunStatus.STOPPED:
            extent = 0
            title, detail = "중지됨", ""
        elif status is RunStatus.ERROR:
            extent = 0
            title, detail = "오류", "확인이 필요합니다"
        elif status is RunStatus.CHECKING_WINDOW:
            extent = 0
            title, detail = "확인 중", "게임 창을 확인합니다"
        elif status is RunStatus.STOPPING:
            extent = -300
            show_ring = True
            title, detail = "중지 중", "현재 동작을 마무리합니다"
        elif status is RunStatus.SELECTING_ROI:
            extent = 0
            title, detail = "위치 설정", "낚시 위치 선택 대기"
        elif self._detected_window_rect is None:
            extent = 0
            title, detail = "대기 중", "게임 창 확인 대기"
        elif engine.get_current_fishing_search_roi() is None:
            extent = 0
            title, detail = "설정 필요", "낚시 위치를 설정해 주세요"
        else:
            extent = 0
            title, detail = "준비 완료", "낚시를 시작할 수 있습니다"
        self._main_canvas.itemconfigure(self._circle_arc_id, extent=extent if show_ring else 0)
        self._main_canvas.itemconfigure(self._circle_title_id, text=title)
        self._main_canvas.itemconfigure(self._circle_detail_id, text=detail)
        should_spin = show_ring
        if should_spin and not self._spin_running:
            self._spin_running = True
            self._animate_spin()
        elif not should_spin:
            self._spin_running = False
            if self._circle_spin_id:
                self._main_canvas.itemconfigure(self._circle_spin_id, extent=0)

    def _update_stage_progress(self, stage: AppStage | None) -> None:
        if not self._stage_dot_ids:
            return
        active = -1
        if self._worker_status is RunStatus.RUNNING and stage is not None:
            active = self._UI_STAGE_BY_APP_STAGE.get(stage, -1)
        for index, dot_id in enumerate(self._stage_dot_ids):
            if 0 <= active and index < active:
                fill = COLORS["accent_dark"]
                outline = COLORS["accent"]
                text = "✓"
                label_color = COLORS["text"]
                detail_color = COLORS["muted"]
            elif index == active:
                fill = COLORS["accent_dark"]
                outline = COLORS["accent"]
                text = "●"
                label_color = COLORS["text"]
                detail_color = COLORS["text"]
            else:
                fill = COLORS["panel_alt"]
                outline = COLORS["border"]
                text = "○"
                label_color = COLORS["muted"]
                detail_color = COLORS["muted"]
            self._main_canvas.itemconfigure(dot_id, fill=fill, outline=outline)
            x0, y0, x1, y1 = self._main_canvas.coords(dot_id)
            dot_text_tag = f"stage-dot-text-{index}"
            self._main_canvas.delete(dot_text_tag)
            self._main_canvas.create_text(
                (x0 + x1) / 2,
                (y0 + y1) / 2,
                text=text,
                fill=COLORS["text"] if index <= active else COLORS["muted"],
                font=(self._font_family, 11, "bold"),
                tags=dot_text_tag,
            )
            self._main_canvas.itemconfigure(self._stage_label_ids[index], fill=label_color)
            self._main_canvas.itemconfigure(self._stage_detail_ids[index], fill=detail_color)
        for index, line_id in enumerate(self._stage_line_ids):
            self._main_canvas.itemconfigure(line_id, fill=COLORS["accent_dark"] if 0 <= active and index < active else COLORS["border"])

    def _register_hotkeys_on_startup(self) -> None:
        try:
            self._hotkey_manager.start(self._settings.hotkeys)
            self._notice(
                f"시작 단축키 {self._settings.hotkeys.start_fishing}, "
                f"중지 단축키 {self._settings.hotkeys.stop_fishing}이 설정되었습니다."
            )
            self.logger.info(
                "[HOTKEY] 전역 단축키 등록 완료 "
                f"시작={self._settings.hotkeys.start_fishing}, 중지={self._settings.hotkeys.stop_fishing}"
            )
        except Exception as exc:
            LOGGER.exception("Failed to register global hotkeys on startup")
            self.logger.error(f"[HOTKEY] 전역 단축키 등록 실패: {exc}")
            self._notice("단축키 등록에 실패했습니다. 버튼으로 조작해 주세요.")

    def _refresh_hotkey_summary(self) -> None:
        hotkeys = self._settings.hotkeys
        self.hotkey_summary_var.set(f"시작 {hotkeys.start_fishing} · 중지 {hotkeys.stop_fishing}")
        self._update_controls_for_state()

    def _refresh_game_key_summary(self) -> None:
        game_keys = self._settings.game_keys
        self.game_key_summary_var.set(
            f"소셜 {game_keys.social_menu} · 상호작용 {game_keys.interact_pickup} · 회수 {game_keys.reel}"
        )

    def _on_primary_action(self) -> None:
        if self._worker_status in (RunStatus.RUNNING, RunStatus.SELECTING_ROI):
            self._on_stop()
            return
        self._on_start()

    def _open_hotkey_settings_window(self) -> None:
        if self._hotkey_window is not None:
            try:
                if self._hotkey_window.winfo_exists():
                    self._hotkey_window.lift()
                    self._hotkey_window.focus_force()
                    return
            except tk.TclError:
                self._hotkey_window = None

        window = tk.Toplevel(self.root)
        self._hotkey_window = window
        window.withdraw()
        window.title("설정")
        window.configure(bg=COLORS["bg"])
        window.resizable(True, True)
        window.minsize(540, 500)
        window.transient(self.root)

        start_var = tk.StringVar(value=self._settings.hotkeys.start_fishing)
        stop_var = tk.StringVar(value=self._settings.hotkeys.stop_fishing)
        social_var = tk.StringVar(value=self._settings.game_keys.social_menu)
        interact_var = tk.StringVar(value=self._settings.game_keys.interact_pickup)
        reel_var = tk.StringVar(value=self._settings.game_keys.reel)
        message_var = tk.StringVar(value="변경을 누른 뒤 사용할 키 하나를 입력하세요.")
        capture_target: CaptureTargetState = {"name": None}

        frame = ttk.Frame(window, padding=(18, 16), style="Dialog.TFrame")
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)

        header = ttk.Frame(frame, style="Dialog.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="설정", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="게임에서 지정한 키와 동일하게 설정해 주세요.",
            style="DialogMuted.TLabel",
            wraplength=420,
        ).grid(row=1, column=0, sticky="ew", pady=(4, 0))

        content_canvas = tk.Canvas(frame, bg=COLORS["panel"], highlightthickness=0)
        content_scrollbar = ttk.Scrollbar(frame, orient="vertical", command=content_canvas.yview)
        content_canvas.configure(yscrollcommand=content_scrollbar.set)
        content_canvas.grid(row=1, column=0, sticky="nsew")
        content_scrollbar.grid(row=1, column=1, sticky="ns", padx=(8, 0))

        content_frame = ttk.Frame(content_canvas, style="Dialog.TFrame")
        content_window_id = content_canvas.create_window(0, 0, window=content_frame, anchor="nw")
        content_frame.columnconfigure(0, weight=1)

        def sync_scroll_region(_event: tk.Event[tk.Misc] | None = None) -> None:
            bbox = content_canvas.bbox("all")
            if bbox is not None:
                content_canvas.configure(scrollregion=bbox)

        def sync_content_width(event: tk.Event[tk.Misc]) -> None:
            content_canvas.itemconfigure(content_window_id, width=event.width)

        def on_mouse_wheel(event: tk.Event[tk.Misc]) -> str:
            content_canvas.yview_scroll(-1 * int(event.delta / 120), "units")
            return "break"

        content_frame.bind("<Configure>", sync_scroll_region)
        content_canvas.bind("<Configure>", sync_content_width)
        content_canvas.bind("<MouseWheel>", on_mouse_wheel)
        content_frame.bind("<MouseWheel>", on_mouse_wheel)

        program_frame = ttk.LabelFrame(content_frame, text="프로그램 단축키", padding=(12, 10))
        program_frame.grid(row=0, column=0, sticky="ew")
        program_frame.columnconfigure(1, weight=1)

        game_frame = ttk.LabelFrame(content_frame, text="Diablo IV 조작키", padding=(12, 10))
        game_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        game_frame.columnconfigure(1, weight=1)

        ttk.Label(program_frame, text="낚시 시작", style="InfoLabel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(program_frame, textvariable=start_var, style="DialogValue.TLabel", width=12).grid(
            row=0,
            column=1,
            sticky="w",
            padx=(16, 8),
        )
        ttk.Button(
            program_frame,
            text="변경",
            width=8,
            command=lambda: begin_capture("start"),
        ).grid(row=0, column=2, sticky="ew")

        ttk.Label(program_frame, text="낚시 중지", style="InfoLabel.TLabel").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Label(program_frame, textvariable=stop_var, style="DialogValue.TLabel", width=12).grid(
            row=1,
            column=1,
            sticky="w",
            padx=(16, 8),
            pady=(10, 0),
        )
        ttk.Button(
            program_frame,
            text="변경",
            width=8,
            command=lambda: begin_capture("stop"),
        ).grid(row=1, column=2, sticky="ew", pady=(10, 0))

        self._create_key_setting_row(game_frame, 0, "소셜 메뉴 호출", social_var, lambda: begin_capture("social"))
        self._create_key_setting_row(game_frame, 1, "상호작용 / 줍기", interact_var, lambda: begin_capture("interact"))
        self._create_key_setting_row(game_frame, 2, "낚싯대 회수", reel_var, lambda: begin_capture("reel"))

        ttk.Label(content_frame, textvariable=message_var, style="DialogMuted.TLabel", wraplength=420).grid(
            row=2,
            column=0,
            sticky="ew",
            pady=(12, 0),
        )

        button_frame = ttk.Frame(frame, style="Dialog.TFrame")
        button_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        button_frame.columnconfigure(1, weight=1)
        ttk.Button(button_frame, text="기본값 복원", width=12, command=lambda: restore_defaults()).grid(
            row=0,
            column=0,
            sticky="w",
        )
        ttk.Button(button_frame, text="취소", width=8, command=lambda: close_window()).grid(
            row=0,
            column=2,
            sticky="e",
        )
        ttk.Button(button_frame, text="저장", width=8, style="Accent.TButton", command=lambda: save_hotkeys()).grid(
            row=0,
            column=3,
            sticky="e",
            padx=(8, 0),
        )

        def begin_capture(target: SettingCaptureTarget) -> None:
            capture_target["name"] = target
            labels: dict[SettingCaptureTarget, str] = {
                "start": "낚시 시작",
                "stop": "낚시 중지",
                "social": "소셜 메뉴 호출",
                "interact": "상호작용 / 줍기",
                "reel": "낚싯대 회수",
            }
            label = labels[target]
            message_var.set(f"{label} 키 입력 대기 중입니다. 사용할 키 하나를 누르세요. ESC는 취소입니다.")
            window.focus_force()

        def restore_defaults() -> None:
            capture_target["name"] = None
            start_var.set(DEFAULT_SETTINGS.hotkeys.start_fishing)
            stop_var.set(DEFAULT_SETTINGS.hotkeys.stop_fishing)
            social_var.set(DEFAULT_SETTINGS.game_keys.social_menu)
            interact_var.set(DEFAULT_SETTINGS.game_keys.interact_pickup)
            reel_var.set(DEFAULT_SETTINGS.game_keys.reel)
            message_var.set("기본값으로 복원했습니다. 저장을 누르면 반영됩니다.")
            self._notice("설정 기본값을 불러왔습니다.")
            self.logger.info("[SETTINGS] 기본값 복원 선택")

        def close_window() -> None:
            self._hotkey_window = None
            try:
                window.grab_release()
            except tk.TclError:
                pass
            window.destroy()

        def save_hotkeys() -> None:
            hotkeys, error = validate_hotkey_pair(start_var.get(), stop_var.get())
            if hotkeys is None:
                message_var.set(error or "단축키 설정을 확인하세요.")
                return
            game_keys, error = validate_game_keys(social_var.get(), interact_var.get(), reel_var.get())
            if game_keys is None:
                message_var.set(error or "게임 조작키 설정을 확인하세요.")
                return

            previous_settings = self._settings
            next_settings = AppSettings(hotkeys=hotkeys, game_keys=game_keys)
            try:
                self._hotkey_manager.register(hotkeys)
            except Exception as exc:
                LOGGER.exception("Failed to register updated global hotkeys")
                try:
                    self._hotkey_manager.register(previous_settings.hotkeys)
                except Exception:
                    LOGGER.exception("Failed to restore previous hotkeys after registration failure")
                message_var.set("전역 단축키 등록에 실패했습니다. 기존 설정을 유지합니다.")
                self.logger.error(f"[HOTKEY] 전역 단축키 등록 실패: {exc}")
                return

            if not save_settings(next_settings):
                try:
                    self._hotkey_manager.register(previous_settings.hotkeys)
                    engine.set_runtime_game_keys(previous_settings.game_keys)
                except Exception:
                    LOGGER.exception("Failed to restore previous hotkeys after save failure")
                message_var.set("설정 파일 저장에 실패했습니다. 기존 설정을 유지합니다.")
                self.logger.error("[HOTKEY] 설정 파일 저장 실패")
                return

            self._settings = next_settings
            engine.set_runtime_game_keys(game_keys)
            self._refresh_hotkey_summary()
            self._refresh_game_key_summary()
            self._notice("조작키 설정이 저장되었습니다.")
            self.logger.info(
                "[SETTINGS] 저장 완료: "
                f"시작={hotkeys.start_fishing}, 중지={hotkeys.stop_fishing}, "
                f"소셜={game_keys.social_menu}, 상호작용={game_keys.interact_pickup}, 회수={game_keys.reel}"
            )
            close_window()

        def on_key_press(event: tk.Event[tk.Misc]) -> str | None:
            target = capture_target["name"]
            if target is None:
                return None

            if event.keysym == "Escape":
                capture_target["name"] = None
                message_var.set("단축키 변경을 취소했습니다.")
                return "break"

            key_name, error = normalize_tk_key(event.keysym, int(event.keycode), int(event.state))
            if key_name is None:
                message_var.set(error or "지원하지 않는 키입니다.")
                return "break"

            if target == "start":
                start_var.set(key_name)
            elif target == "stop":
                stop_var.set(key_name)
            elif target == "social":
                social_var.set(key_name)
            elif target == "interact":
                interact_var.set(key_name)
            elif target == "reel":
                reel_var.set(key_name)
            capture_target["name"] = None
            message_var.set(f"{key_name} 키를 선택했습니다. 저장을 누르면 반영됩니다.")
            return "break"

        window.bind("<KeyPress>", on_key_press)
        window.protocol("WM_DELETE_WINDOW", close_window)
        window.update_idletasks()
        sync_scroll_region()
        self._center_child_window(window, width=560, height=540)
        self._apply_window_icon(window)
        window.deiconify()
        apply_windows_window_polish(window)
        window.lift()
        window.focus_force()
        window.grab_set()

    def _create_key_setting_row(
        self,
        parent: tk.Misc,
        row: int,
        label_text: str,
        value_var: tk.StringVar,
        command: Callable[[], None],
    ) -> None:
        ttk.Label(parent, text=label_text, style="InfoLabel.TLabel").grid(
            row=row,
            column=0,
            sticky="w",
            pady=(10 if row else 0, 0),
        )
        ttk.Label(parent, textvariable=value_var, style="DialogValue.TLabel", width=12).grid(
            row=row,
            column=1,
            sticky="w",
            padx=(16, 8),
            pady=(10 if row else 0, 0),
        )
        ttk.Button(parent, text="변경", width=8, command=command).grid(
            row=row,
            column=2,
            sticky="ew",
            pady=(10 if row else 0, 0),
        )

    def _center_child_window(self, window: tk.Toplevel, *, width: int, height: int) -> None:
        self.root.update_idletasks()
        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        root_width = max(1, self.root.winfo_width())
        root_height = max(1, self.root.winfo_height())
        x = root_x + max(0, (root_width - width) // 2)
        y = root_y + max(0, (root_height - height) // 2)
        work_area = get_window_work_area(self.root)
        if work_area is None:
            work_area = (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        work_x, work_y, work_width, work_height = work_area
        x = min(max(work_x, x), work_x + max(0, work_width - width))
        y = min(max(work_y, y), work_y + max(0, work_height - height))
        window.geometry(f"{width}x{height}+{x}+{y}")

    def _on_start(self) -> None:
        if self._worker_status in (
            RunStatus.CHECKING_WINDOW,
            RunStatus.SELECTING_ROI,
            RunStatus.RUNNING,
            RunStatus.STOPPING,
        ):
            LOGGER.debug("Start request ignored in state: %s", self._worker_status.value)
            return

        worker = self._fishing_worker
        if worker is not None and worker.is_running():
            self._notice("이미 낚시를 진행하고 있습니다.")
            self.logger.warning("[START] 이미 실행 중입니다.")
            return

        if self._roi_window is not None:
            self._notice("낚시 위치를 먼저 선택해 주세요.")
            self.logger.warning("[ROI] 영역 설정이 이미 진행 중입니다.")
            return

        if worker is not None:
            self._handle_worker_finished()

        engine.clear_current_fishing_search_roi()
        self._refresh_roi_status("설정 안 됨")
        self._set_window_status(WindowStatus.NOT_FOUND, "창 확인 중")
        self._set_runtime_state(RunStatus.CHECKING_WINDOW, AppStage.WINDOW_DETECTION)
        self._notice("Diablo IV 창을 확인합니다.")
        self.logger.info("[START] 시작 요청")

        thread = threading.Thread(target=self._detect_window_for_start_worker, daemon=True)
        thread.start()

    def _detect_window_for_start_worker(self) -> None:
        try:
            rect = engine.refresh_diablo_window_rect(log_missing=False)
        except Exception as exc:
            LOGGER.exception("Diablo IV window detection failed")
            self._ui_call(lambda exc=exc: self._handle_window_detection_error(exc))
            return

        self._ui_call(lambda rect=rect: self._handle_window_detection_result(rect))

    def _handle_window_detection_result(self, rect: WindowRect | None) -> None:
        if self._closing:
            return

        if rect is None:
            self._detected_window_rect = None
            self._set_window_status(WindowStatus.NOT_FOUND, "창 정보 없음")
            self._set_runtime_state(RunStatus.IDLE, AppStage.IDLE)
            self._notice("Diablo IV 창을 찾지 못했습니다.")
            self.logger.warning("[WINDOW] Diablo IV 창을 찾지 못했습니다. 게임 실행 후 다시 시작하세요.")
            return

        self._detected_window_rect = rect
        engine.set_current_window_rect(rect)
        self._set_window_status(WindowStatus.FOUND, "연결됨")
        self._set_runtime_state(RunStatus.SELECTING_ROI, AppStage.ROI_SELECTION)
        self._notice("낚시 위치를 드래그해 선택해 주세요.")
        self.logger.info(
            "[WINDOW] Diablo IV 창 감지 성공 "
            f"left={rect.left}, top={rect.top}, width={rect.width}, height={rect.height}"
        )
        self._open_roi_selection_window(rect)

    def _handle_window_detection_error(self, exc: Exception) -> None:
        self._detected_window_rect = None
        self._set_window_status(WindowStatus.NOT_FOUND, "창 감지 오류")
        self._set_runtime_state(RunStatus.ERROR, AppStage.ERROR)
        self._notice("게임 창 확인 중 오류가 발생했습니다.")
        self.logger.error(f"[ERROR] 창 감지 실패: {exc}")

    def _open_roi_selection_window(self, rect: WindowRect) -> None:
        if self._roi_window is not None:
            return

        window = tk.Toplevel(self.root)
        self._roi_window = window
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.attributes("-alpha", 0.34)
        window.configure(bg="#111827")
        window.geometry(f"{rect.width}x{rect.height}+{rect.left}+{rect.top}")

        canvas = tk.Canvas(window, bg="#111827", highlightthickness=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        canvas.create_text(
            rect.width // 2,
            34,
            text="낚시 감지에 사용할 영역을 드래그해 선택하세요.  ESC: 취소",
            fill="#ffffff",
            font=("Malgun Gothic", 15, "bold"),
        )

        selection_rect: SelectionRectState = {"id": None}
        drag_start: DragStartState = {"x": None, "y": None}

        def clamp_point(x: int, y: int) -> tuple[int, int]:
            return max(0, min(rect.width, x)), max(0, min(rect.height, y))

        def normalize_roi(
            start_x: int,
            start_y: int,
            end_x: int,
            end_y: int,
        ) -> RoiRect:
            start_x, start_y = clamp_point(start_x, start_y)
            end_x, end_y = clamp_point(end_x, end_y)
            left = min(start_x, end_x)
            top = min(start_y, end_y)
            right = max(start_x, end_x)
            bottom = max(start_y, end_y)
            return left, top, right - left, bottom - top

        def on_press(event: tk.Event[tk.Misc]) -> None:
            start_x, start_y = clamp_point(int(event.x), int(event.y))
            drag_start["x"] = start_x
            drag_start["y"] = start_y
            rect_id = selection_rect["id"]
            if rect_id is not None:
                canvas.delete(rect_id)
            selection_rect["id"] = canvas.create_rectangle(
                start_x,
                start_y,
                start_x,
                start_y,
                outline="#38bdf8",
                width=3,
            )

        def on_drag(event: tk.Event[tk.Misc]) -> None:
            rect_id = selection_rect["id"]
            start_x = drag_start["x"]
            start_y = drag_start["y"]
            if rect_id is None or start_x is None or start_y is None:
                return
            end_x, end_y = clamp_point(int(event.x), int(event.y))
            canvas.coords(rect_id, start_x, start_y, end_x, end_y)

        def on_release(event: tk.Event[tk.Misc]) -> None:
            start_x = drag_start["x"]
            start_y = drag_start["y"]
            if start_x is None or start_y is None:
                self._finish_roi_selection(None)
                return
            end_x, end_y = clamp_point(int(event.x), int(event.y))
            self._finish_roi_selection(normalize_roi(start_x, start_y, end_x, end_y))

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)
        window.bind("<Escape>", lambda _event=None: self._finish_roi_selection(None))
        window.bind("<ButtonPress-3>", lambda _event=None: self._finish_roi_selection(None))
        window.focus_force()

    def _focus_game_window(self) -> None:
        try:
            import win32gui

            keywords = ("diablo iv", "디아블로 iv")
            handles: list[int] = []

            def _cb(hwnd: int, _: object) -> bool:
                title = win32gui.GetWindowText(hwnd).lower()
                if any(k in title for k in keywords):
                    handles.append(hwnd)
                return True

            win32gui.EnumWindows(_cb, None)
            if handles:
                win32gui.SetForegroundWindow(handles[0])
                LOGGER.debug("[FOCUS] Diablo IV 창으로 포커스 이동")
        except Exception:
            LOGGER.debug("[FOCUS] 게임 창 포커스 이동 실패", exc_info=True)

    def _finish_roi_selection(self, roi: RoiRect | None) -> None:
        window = self._roi_window
        self._roi_window = None
        if window is not None:
            try:
                if window.winfo_exists():
                    window.destroy()
            except tk.TclError as exc:
                LOGGER.debug("ROI selection window already closed: %s", exc)

        if self._closing:
            return

        if roi is None:
            engine.clear_current_fishing_search_roi()
            self._refresh_roi_status("설정 취소")
            self._set_runtime_state(RunStatus.IDLE, AppStage.IDLE)
            self._notice("낚시 위치 설정을 취소했습니다.")
            self.logger.warning("[ROI] 감지 영역 설정을 취소했습니다.")
            return

        rect = self._detected_window_rect
        selected_roi = engine.set_fishing_search_roi_local(roi, rect)
        if selected_roi is None:
            engine.clear_current_fishing_search_roi()
            self._refresh_roi_status("잘못된 영역")
            self._set_runtime_state(RunStatus.IDLE, AppStage.IDLE)
            self._notice("낚시 위치가 너무 작습니다. 다시 설정해 주세요.")
            self.logger.warning("[ROI] 감지 영역 설정에 실패했습니다.")
            return

        x, y, width, height = selected_roi
        self._refresh_roi_status(f"설정 완료: {width} x {height} (x={x}, y={y})")
        self._notice("낚시 위치가 설정되었습니다.")
        self.logger.info(f"[ROI] 감지 영역 설정 완료: local x={x} y={y} w={width} h={height}")
        self._focus_game_window()
        self._start_worker_after_roi()

    def _start_worker_after_roi(self) -> None:
        self._set_runtime_state(RunStatus.RUNNING, AppStage.READY_TO_RUN)
        self.logger.info("[WORKER] 낚시 worker 시작 요청")

        try:
            worker = FishingWorker(
                on_log=self._enqueue_worker_log,
                on_state=self._enqueue_worker_state,
                on_step=self._enqueue_worker_step,
                on_window=self._enqueue_worker_window,
            )
            self._fishing_worker = worker
            started = worker.start()
        except Exception as exc:
            LOGGER.exception("Fishing worker start failed")
            self._fishing_worker = None
            self._set_runtime_state(RunStatus.ERROR, AppStage.ERROR)
            self._notice("낚시 시작에 실패했습니다.")
            self.logger.error(f"[ERROR] worker 시작 실패: {exc}")
            return

        if not started:
            self._fishing_worker = None
            self._set_runtime_state(RunStatus.IDLE, AppStage.IDLE)
            self._notice("낚시를 시작하지 못했습니다.")
            self.logger.warning("[START] worker 시작이 거부되었습니다.")

    def _on_stop(self) -> None:
        if self._worker_status is RunStatus.SELECTING_ROI and self._roi_window is not None:
            self.logger.info("[HOTKEY] 영역 설정 중지 요청: 선택을 취소합니다.")
            self._finish_roi_selection(None)
            return

        if self._worker_status is RunStatus.CHECKING_WINDOW:
            LOGGER.debug("Stop request ignored during window detection")
            return

        worker = self._fishing_worker
        if worker is None:
            self._set_runtime_state(RunStatus.STOPPED, AppStage.IDLE)
            self._notice("실행 중인 낚시가 없습니다.")
            self.logger.info("[STOP] 실행 중인 worker가 없습니다.")
            return

        if not worker.is_running():
            self._handle_worker_finished()
            return

        if self._worker_status is RunStatus.STOPPING:
            self.logger.warning("[STOP] 이미 중지 요청 중입니다.")
            return

        self._set_runtime_state(RunStatus.STOPPING, AppStage.STOPPING)
        self._notice("낚시를 안전하게 중지합니다.")
        self.logger.info("[STOP] 낚시 worker 중지 요청")
        worker.stop()

    def _on_start_hotkey(self) -> None:
        if self._closing:
            return
        if self._hotkey_window is not None:
            LOGGER.debug("Start hotkey ignored while settings window is open")
            return
        if self._worker_status not in (RunStatus.IDLE, RunStatus.STOPPED, RunStatus.ERROR):
            LOGGER.debug("Start hotkey ignored in state: %s", self._worker_status.value)
            return
        self._show_main_window_for_hotkey()
        self._notice("시작 단축키가 입력되었습니다.")
        self.logger.info(f"[HOTKEY] 시작 단축키 입력: {self._settings.hotkeys.start_fishing}")
        self._on_start()

    def _on_stop_hotkey(self) -> None:
        if self._closing:
            return
        if self._hotkey_window is not None:
            LOGGER.debug("Stop hotkey ignored while settings window is open")
            return
        if self._worker_status not in (RunStatus.RUNNING, RunStatus.SELECTING_ROI, RunStatus.CHECKING_WINDOW):
            LOGGER.debug("Stop hotkey ignored in state: %s", self._worker_status.value)
            return
        self._notice("중지 단축키가 입력되었습니다.")
        self.logger.info(f"[HOTKEY] 중지 단축키 입력: {self._settings.hotkeys.stop_fishing}")
        self._on_stop()

    def _show_main_window_for_hotkey(self) -> None:
        try:
            self.root.deiconify()
            self.root.lift()
        except tk.TclError:
            LOGGER.debug("Failed to raise main window for hotkey", exc_info=True)

    def _enqueue_worker_log(self, message: str) -> None:
        self.worker_events.put(WorkerEvent(WorkerEventType.LOG, message))

    def _enqueue_worker_state(self, status: RunStatus) -> None:
        self.worker_events.put(WorkerEvent(WorkerEventType.STATE, status))

    def _enqueue_worker_step(self, stage: AppStage) -> None:
        self.worker_events.put(WorkerEvent(WorkerEventType.STEP, stage))

    def _enqueue_worker_window(self, status: WindowStatus) -> None:
        self.worker_events.put(WorkerEvent(WorkerEventType.WINDOW, status))

    def _poll_worker_events(self) -> None:
        while True:
            try:
                callback = self.ui_callbacks.get_nowait()
            except queue.Empty:
                break
            callback()

        while True:
            try:
                event = self.worker_events.get_nowait()
            except queue.Empty:
                break

            self._handle_worker_event(event)

        self._cleanup_finished_worker()

        if not self._closing:
            self.root.after(100, self._poll_worker_events)

    def _refresh_stats_loop(self) -> None:
        self._refresh_stats()
        if not self._closing:
            self.root.after(1000, self._refresh_stats_loop)

    def _refresh_stats(self) -> None:
        snapshot = engine.get_fishing_stats_snapshot()
        # today_* globals accumulate only at session end; add live session data while running
        live_cast = snapshot.session_cast_count if snapshot.is_running else 0
        live_catch = snapshot.session_catch_count if snapshot.is_running else 0
        live_run = snapshot.session_run_seconds if snapshot.is_running else 0
        self.today_cast_count_var.set(self._format_count(snapshot.today_cast_count + live_cast))
        self.today_catch_count_var.set(self._format_count(snapshot.today_catch_count + live_catch))
        self.today_run_time_var.set(self._format_duration(snapshot.today_run_seconds + live_run))
        self.total_cast_count_var.set(self._format_count(snapshot.total_cast_count))
        self.total_catch_count_var.set(self._format_count(snapshot.total_catch_count))
        self.total_run_time_var.set(self._format_duration(snapshot.total_run_seconds))

    @staticmethod
    def _format_count(count: int) -> str:
        return f"{max(0, int(count)):,}회"

    @staticmethod
    def _format_duration(seconds: int) -> str:
        seconds = max(0, int(seconds))
        if seconds <= 0:
            return "0분"
        if seconds < 60:
            return f"{seconds}초"
        minutes, _seconds_part = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}시간 {minutes}분"
        return f"{minutes}분"

    def _handle_worker_event(self, event: WorkerEvent) -> None:
        payload = event.payload
        if event.event_type is WorkerEventType.LOG and isinstance(payload, str):
            if "[ERROR]" in payload or "오류" in payload:
                self.logger.error(payload)
            elif "[WARN]" in payload:
                self.logger.warning(payload)
            else:
                self.logger.info(payload)
            user_message = self._to_user_message(payload)
            if user_message:
                self._notice(user_message)
            return

        if event.event_type is WorkerEventType.STATE and isinstance(payload, RunStatus):
            self._set_runtime_state(payload)
            return

        if event.event_type is WorkerEventType.STEP and isinstance(payload, AppStage):
            self._set_runtime_state(self._worker_status, payload)
            return

        if event.event_type is WorkerEventType.WINDOW and isinstance(payload, WindowStatus):
            self._set_window_status(payload, self.window_detail_var.get())

    def _cleanup_finished_worker(self) -> None:
        worker = self._fishing_worker
        if worker is None or worker.is_running():
            return

        self._handle_worker_finished()

    def _handle_worker_finished(self) -> None:
        self._fishing_worker = None
        if self._worker_status in (RunStatus.ERROR, RunStatus.IDLE):
            self._update_controls_for_state()
            return

        self._set_runtime_state(RunStatus.STOPPED, AppStage.IDLE)
        self._notice("낚시를 중지했습니다.")

    def _set_runtime_state(
        self,
        status: RunStatus,
        stage: AppStage | None = None,
    ) -> None:
        self._worker_status = status
        if stage is not None:
            self._current_stage = stage
        self.run_status_var.set(self._format_run_status(status))
        self.status_badge_var.set(self._format_status_badge(status, stage))
        if stage is not None:
            self.stage_var.set(self._format_stage(stage))
            if status is RunStatus.RUNNING:
                self.detail_var.set(self._format_stage_detail(stage))
        self.detection_status_var.set(self._format_detection_status(status, stage))
        self._update_controls_for_state()

    def _update_controls_for_state(self) -> None:
        status = self._worker_status
        hotkeys = self._settings.hotkeys
        roi_ready = engine.get_current_fishing_search_roi() is not None

        if status is RunStatus.RUNNING:
            self.headline_var.set("낚시가 진행 중입니다")
            self.primary_action_var.set("낚시 중지")
            self.primary_hint_var.set(f"{hotkeys.stop_fishing}으로도 중지할 수 있습니다.")
            self.primary_button.configure(style="Danger.TButton", state=tk.NORMAL)
        elif status is RunStatus.SELECTING_ROI:
            self.headline_var.set("낚시 위치를 설정해 주세요")
            self.detail_var.set("물가의 낚시 아이콘 영역을 드래그해 선택해 주세요.")
            self.primary_action_var.set("위치 설정 취소")
            self.primary_hint_var.set("")
            self.primary_button.configure(style="Danger.TButton", state=tk.NORMAL)
        elif status in (RunStatus.CHECKING_WINDOW, RunStatus.STOPPING):
            if status is RunStatus.CHECKING_WINDOW:
                self.headline_var.set("Diablo IV 창을 확인하는 중입니다")
                self.detail_var.set("잠시만 기다려 주세요.")
            else:
                self.headline_var.set("낚시를 중지하는 중입니다")
                self.detail_var.set("현재 동작을 마무리하고 멈춥니다.")
            self.primary_action_var.set("처리 중")
            self.primary_hint_var.set("")
            self.primary_button.configure(style="Primary.TButton", state=tk.DISABLED)
        else:
            if self._detected_window_rect is None:
                self.headline_var.set("Diablo IV 창이 필요합니다")
                self.detail_var.set("게임을 먼저 실행한 뒤 낚시를 시작해 주세요.")
                self.primary_hint_var.set("")
            elif not roi_ready:
                self.headline_var.set("낚시 위치를 설정해 주세요")
                self.detail_var.set("낚시 아이콘 영역을 선택해 주세요.")
                self.primary_hint_var.set("")
            else:
                self.headline_var.set("낚시를 중지했습니다" if status is RunStatus.STOPPED else "낚시 준비 완료")
                self.detail_var.set("낚시를 시작할 수 있습니다.")
                self.primary_hint_var.set(f"{hotkeys.start_fishing}으로도 시작할 수 있습니다.")
            self.primary_action_var.set("낚시 시작")
            self.primary_button.configure(style="Primary.TButton", state=tk.NORMAL)

        badge_style = "Badge.TLabel"
        if status is RunStatus.RUNNING:
            badge_style = "SuccessBadge.TLabel"
        elif status in (RunStatus.CHECKING_WINDOW, RunStatus.SELECTING_ROI, RunStatus.STOPPING):
            badge_style = "WarningBadge.TLabel"
        elif status is RunStatus.ERROR:
            badge_style = "DangerBadge.TLabel"
        self.status_badge.configure(style=badge_style)

        active_stage = self._current_stage if status is RunStatus.RUNNING else None
        self._update_circle(active_stage)
        self._update_stage_progress(active_stage)

    @staticmethod
    def _format_run_status(status: RunStatus) -> str:
        labels = {
            RunStatus.IDLE: "시작 전",
            RunStatus.CHECKING_WINDOW: "게임 창 확인 중",
            RunStatus.SELECTING_ROI: "낚시 위치 설정 중",
            RunStatus.RUNNING: "낚시 중",
            RunStatus.STOPPING: "중지 중",
            RunStatus.STOPPED: "중지됨",
            RunStatus.ERROR: "오류 발생",
        }
        return labels.get(status, status.value)

    @staticmethod
    def _format_stage(stage: AppStage | None) -> str:
        if stage is None:
            return "--"
        labels = {
            AppStage.IDLE: "게임 창 확인 대기",
            AppStage.WINDOW_DETECTION: "Diablo IV 창 확인 중",
            AppStage.ROI_SELECTION: "낚시 위치 설정 중",
            AppStage.READY_TO_RUN: "낚시를 시작하는 중",
            AppStage.FIND_WINDOW: "Diablo IV 창 확인 중",
            AppStage.CAST: "낚싯대를 던지고 있습니다",
            AppStage.FIND_BOBBER: "낚시 아이콘을 확인하는 중",
            AppStage.WAIT_READY: "입질을 기다리고 있습니다",
            AppStage.REEL: "낚싯대를 회수하고 있습니다",
            AppStage.LOOT: "아이템을 줍고 있습니다",
            AppStage.RECAST: "다음 낚시를 준비하고 있습니다",
            AppStage.STOPPING: "중지 중",
            AppStage.ERROR: "오류가 발생했습니다",
            AppStage.RUNNING: "진행 중",
        }
        return labels.get(stage, "--")

    @staticmethod
    def _format_stage_detail(stage: AppStage | None) -> str:
        if stage is None:
            return "낚시가 진행 중입니다."
        labels = {
            AppStage.READY_TO_RUN: "낚시 엔진을 준비하고 있습니다.",
            AppStage.FIND_WINDOW: "Diablo IV 창 상태를 확인하고 있습니다.",
            AppStage.CAST: "낚시 시작 아이콘을 확인하고 낚싯대를 던집니다.",
            AppStage.FIND_BOBBER: "선택한 영역에서 낚시 상태를 확인하고 있습니다.",
            AppStage.WAIT_READY: "입질 신호를 기다리고 있습니다.",
            AppStage.REEL: "입질 후 낚싯대를 회수합니다.",
            AppStage.LOOT: "획득한 아이템을 줍습니다.",
            AppStage.RECAST: "같은 위치에서 다음 낚시를 준비합니다.",
            AppStage.RUNNING: "낚시 루프가 진행 중입니다.",
        }
        return labels.get(stage, "낚시가 진행 중입니다.")

    def _format_status_badge(self, status: RunStatus, stage: AppStage | None) -> str:
        if status is RunStatus.CHECKING_WINDOW:
            return "● 확인 중"
        if status is RunStatus.RUNNING:
            return "● 낚시 중"
        if status is RunStatus.SELECTING_ROI:
            return "● 위치 설정"
        if status is RunStatus.STOPPING:
            return "● 중지 중"
        if status is RunStatus.ERROR:
            return "● 오류"
        if status is RunStatus.IDLE and self._detected_window_rect is None:
            return "● 대기 중"
        if status in (RunStatus.IDLE, RunStatus.STOPPED) and engine.get_current_fishing_search_roi() is None:
            return "● 설정 필요"
        if status is RunStatus.STOPPED:
            return "● 중지됨"
        return "● 준비"

    @staticmethod
    def _format_detection_status(status: RunStatus, stage: AppStage | None) -> str:
        if status is RunStatus.CHECKING_WINDOW:
            return "게임 창 확인 중"
        if status is RunStatus.SELECTING_ROI:
            return "낚시 위치 설정 중"
        if status is RunStatus.RUNNING and stage in (AppStage.READY_TO_RUN, AppStage.RUNNING, None):
            return "낚시 진행 중"
        if stage in (AppStage.FIND_BOBBER, AppStage.WAIT_READY):
            return "입질 대기 중"
        if stage is AppStage.REEL:
            return "회수 중"
        if stage is AppStage.CAST:
            return "아이콘 확인 중"
        if status is RunStatus.ERROR:
            return "확인 필요"
        if status is RunStatus.STOPPED:
            return "중지됨"
        return "대기"

    def _set_window_status(self, status: WindowStatus, detail: str) -> None:
        self.window_status_var.set(
            "Diablo IV 창을 찾았습니다" if status is WindowStatus.FOUND else "Diablo IV 창을 찾지 못했습니다"
        )
        self.window_detail_var.set(detail)

    def _refresh_roi_status(self, override: str | None = None) -> None:
        if override is not None:
            self.roi_status_var.set(self._format_roi_status_text(override))
            self._update_controls_for_state()
            return

        roi = engine.get_current_fishing_search_roi()
        if roi is None:
            if self._detected_window_rect is None:
                self.roi_status_var.set("게임 창을 먼저 확인해 주세요")
            else:
                self.roi_status_var.set("낚시 위치를 설정해 주세요")
            self._update_controls_for_state()
            return
        self.roi_status_var.set("낚시 위치가 설정되었습니다")
        self._update_controls_for_state()

    @staticmethod
    def _format_roi_status_text(value: str) -> str:
        if "완료" in value:
            return "낚시 위치가 설정되었습니다"
        if "취소" in value:
            return "낚시 위치를 설정해 주세요"
        if "잘못" in value:
            return "낚시 위치를 다시 설정해 주세요"
        if "안 됨" in value:
            return "낚시 위치를 설정해 주세요"
        return value

    def _notice(self, message: str) -> None:
        user_message = self._to_user_message(message)
        if not user_message:
            return
        if self._notice_lines and self._notice_lines[-1] == user_message:
            return
        self.last_message_var.set(user_message)
        self.detail_var.set(user_message)
        self._notice_lines.append(user_message)
        if len(self._notice_lines) > self._max_notice_lines:
            self._notice_lines = self._notice_lines[-self._max_notice_lines :]

    def _to_user_message(self, message: str) -> str:
        replacements = [
            ("[BOOT] GUI ready. 메인 윈도우 실행 흐름 준비 완료.", "프로그램이 준비되었습니다."),
            ("[START] 시작 요청", "Diablo IV 창을 확인합니다."),
            ("[WINDOW] Diablo IV 창을 찾지 못했습니다. 게임 실행 후 다시 시작하세요.", "Diablo IV 창을 찾지 못했습니다."),
            ("[ROI] 감지 영역 설정을 취소했습니다.", "낚시 위치 설정을 취소했습니다."),
            ("[ROI] 감지 영역 설정에 실패했습니다.", "낚시 위치를 다시 설정해 주세요."),
            ("[WORKER] 낚시 worker thread 시작", "낚시를 시작합니다."),
            ("[WORKER] 정상 종료", "낚시를 중지했습니다."),
            ("[STOP] 낚시 worker 중지 요청", "낚시를 안전하게 중지합니다."),
            ("[WORKER] 중지 요청을 받았습니다.", "낚시를 안전하게 중지합니다."),
            ("[CAST] start key press", "낚싯대를 던질 준비를 합니다."),
            ("[LOOT] START 클릭 후 아이템 줍기", "아이템을 줍는 중입니다."),
            ("[READY] wait start", "입질을 기다리는 중입니다."),
        ]
        for raw, friendly in replacements:
            if message == raw or raw in message:
                return friendly

        if "단축키 등록 실패" in message:
            return "단축키 등록에 실패했습니다. 버튼으로 조작해 주세요."
        if "단축키 입력" in message and "시작" in message:
            return "시작 단축키가 입력되었습니다."
        if "단축키 입력" in message and "중지" in message:
            return "중지 단축키가 입력되었습니다."
        if "설정 완료" in message and ("ROI" in message or "감지 영역" in message):
            return "낚시 위치가 설정되었습니다."
        if "Diablo IV 창 감지 성공" in message or "Diablo IV 창을 찾았습니다" in message:
            return "Diablo IV 창을 찾았습니다."
        if "START" in message and "not found" in message:
            return "START 아이콘을 찾지 못했습니다. 위치를 다시 확인해 주세요."
        if "낚싯줄 던짐" in message or "[CAST] click start" in message:
            return "낚싯대를 던졌습니다."
        if "입질 감지" in message:
            return "입질을 감지했습니다."
        if "낚아올리는 중" in message or "[REEL]" in message:
            return "낚싯대를 회수했습니다."
        if "다음 낚시 준비" in message:
            return "다음 낚시를 준비합니다."

        cleaned = re.sub(r"^\[\d{2}:\d{2}:\d{2}\]\s+\w+\s+", "", message).strip()
        cleaned = re.sub(r"\[[A-Z0-9_-]+\]\s*", "", cleaned).strip()
        cleaned = re.sub(r"\b(INFO|DEBUG|WARNING|ERROR)\b\s*", "", cleaned).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if "score=" in cleaned or "scale=" in cleaned or "roi=" in cleaned or "local x=" in cleaned:
            return ""
        return cleaned

    def _on_close(self) -> None:
        self._closing = True
        if self._roi_window is not None:
            self._finish_roi_selection(None)
        if self._hotkey_window is not None:
            try:
                if self._hotkey_window.winfo_exists():
                    self._hotkey_window.destroy()
            except tk.TclError:
                pass
            self._hotkey_window = None

        worker = self._fishing_worker
        if worker is not None and worker.is_running():
            self.logger.info("[EXIT] 실행 중인 worker 중지 요청")
            try:
                worker.stop()
                worker.join(timeout=1.0)
            except Exception as exc:
                LOGGER.exception("Worker stop during close failed")
                self.logger.error(f"[EXIT] worker 종료 처리 중 오류: {exc}")
            if worker.is_running():
                self.logger.warning("[EXIT] worker 종료 대기 timeout, daemon thread로 종료를 이어갑니다.")

        try:
            self._hotkey_manager.stop()
        except Exception as exc:
            LOGGER.exception("Global hotkey listener stop failed")
            self.logger.error(f"[EXIT] 단축키 listener 종료 실패: {exc}")

        self.root.destroy()

    def _ui_call(self, callback: Callable[[], None]) -> None:
        if self._closing:
            return
        if threading.current_thread() is threading.main_thread():
            callback()
        else:
            self.ui_callbacks.put(callback)
            try:
                self.root.after(0, self._poll_worker_events)
            except RuntimeError:
                LOGGER.debug("UI callback queued while mainloop is inactive")

    @staticmethod
    def _format_window_rect(rect: WindowRect) -> str:
        return f"left={rect.left}, top={rect.top}, {rect.width} x {rect.height}"
