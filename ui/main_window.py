from __future__ import annotations

import logging
import queue
import re
import threading
import tkinter as tk
from dataclasses import dataclass
from enum import Enum, auto
from tkinter import ttk
from typing import Callable, Optional

from app.logger import AppLogger
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
    def __init__(self) -> None:
        set_windows_app_user_model_id("D4FishingWatcher.App")
        self.root = tk.Tk()
        self.root.title("D4 Fishing Watcher")
        self.root.geometry("900x640")
        self.root.minsize(740, 560)
        self._icons: TkAppIcons = load_tk_app_icons()
        self._apply_window_icon(self.root)
        apply_windows_window_polish(self.root)

        self.window_status_var = tk.StringVar(value="Diablo IV 창을 찾지 못했습니다")
        self.window_detail_var = tk.StringVar(value="찾을 수 없음")
        self.run_status_var = tk.StringVar(value=RunStatus.IDLE.value)
        self.stage_var = tk.StringVar(value=AppStage.IDLE.value)
        self.roi_status_var = tk.StringVar(value="게임 창을 먼저 확인해 주세요")
        self.status_badge_var = tk.StringVar(value="● 확인 필요")
        self.headline_var = tk.StringVar(value="Diablo IV 창을 찾지 못했습니다")
        self.detail_var = tk.StringVar(value="게임을 실행한 뒤 다시 확인해 주세요.")
        self.last_message_var = tk.StringVar(value="아직 기록이 없습니다.")
        self.hotkey_summary_var = tk.StringVar(value="")
        self.game_key_summary_var = tk.StringVar(value="")
        self.primary_action_var = tk.StringVar(value="낚시 시작")
        self.primary_hint_var = tk.StringVar(value="")
        self.cast_count_var = tk.StringVar(value="0회")
        self.catch_count_var = tk.StringVar(value="0회")
        self.run_time_var = tk.StringVar(value="00:00")
        self.progress_stage_var = tk.StringVar(value="게임 창 확인 대기")
        self.recent_result_var = tk.StringVar(value="기록 없음")
        self.detection_status_var = tk.StringVar(value="대기 중")

        self.worker_events: queue.Queue[WorkerEvent] = queue.Queue()
        self.ui_callbacks: queue.Queue[Callable[[], None]] = queue.Queue()
        self._fishing_worker: Optional[FishingWorker] = None
        self._roi_window: Optional[tk.Toplevel] = None
        self._hotkey_window: Optional[tk.Toplevel] = None
        self._worker_status = RunStatus.IDLE
        self._detected_window_rect: Optional[WindowRect] = None
        self._settings: AppSettings = load_settings()
        engine.set_runtime_game_keys(self._settings.game_keys)
        self._hotkey_manager = GlobalHotkeyManager(
            on_start=lambda: self._ui_call(self._on_start_hotkey),
            on_stop=lambda: self._ui_call(self._on_stop_hotkey),
        )
        self._closing = False
        self._notice_lines: list[str] = []
        self._max_notice_lines = 80

        self.logger = AppLogger()
        engine.load_fishing_stats()

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
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        self._configure_styles()

        header = ttk.Frame(self.root, padding=(18, 10, 18, 6))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        if self._icons.header is not None:
            icon = ttk.Label(header, image=self._icons.header)
            icon.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 10))

        ttk.Label(header, text="D4 Fishing Watcher", style="AppName.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(header, text="Diablo IV 낚시 도우미", style="Muted.TLabel").grid(row=1, column=1, sticky="w", pady=(2, 0))
        self.status_badge = ttk.Label(
            header,
            textvariable=self.status_badge_var,
            style="Badge.TLabel",
            anchor="e",
        )
        self.status_badge.grid(row=0, column=2, rowspan=2, sticky="e", padx=(14, 10))
        self.header_settings_button = ttk.Button(
            header,
            text="설정",
            command=self._open_hotkey_settings_window,
        )
        self.header_settings_button.grid(row=0, column=3, rowspan=2, sticky="e")

        notice = ttk.Frame(self.root, padding=(18, 10), style="Notice.TFrame")
        notice.grid(row=1, column=0, sticky="ew", padx=18, pady=(6, 8))
        notice.columnconfigure(0, weight=1)
        ttk.Label(notice, textvariable=self.headline_var, style="NoticeLead.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(notice, textvariable=self.detail_var, style="NoticeMuted.TLabel", wraplength=760).grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(3, 0),
        )

        body = ttk.Frame(self.root, padding=(18, 0, 18, 10))
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1, minsize=320)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body, padding=(16, 14), style="Panel.TFrame")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.columnconfigure(0, weight=1)
        ttk.Label(left, text="낚시 준비", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(left, text="게임 창 확인, 낚시 위치 설정, 시작 순서로 진행합니다.", style="PanelMuted.TLabel", wraplength=290).grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(5, 12),
        )

        readiness = ttk.Frame(left, style="Panel.TFrame")
        readiness.grid(row=2, column=0, sticky="ew")
        readiness.columnconfigure(1, weight=1)
        self._create_status_row(readiness, 0, "게임 창", self.window_status_var)
        self._create_status_row(readiness, 1, "낚시 위치", self.roi_status_var)

        self.primary_button = ttk.Button(
            left,
            textvariable=self.primary_action_var,
            style="Primary.TButton",
            command=self._on_primary_action,
        )
        self.primary_button.grid(row=3, column=0, sticky="ew", pady=(18, 0))
        ttk.Label(left, textvariable=self.primary_hint_var, style="PanelMuted.TLabel").grid(
            row=4,
            column=0,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Separator(left, orient="horizontal").grid(row=5, column=0, sticky="ew", pady=(16, 12))
        key_summary = ttk.Frame(left, style="Panel.TFrame")
        key_summary.grid(row=6, column=0, sticky="ew")
        key_summary.columnconfigure(1, weight=1)
        self._create_status_row(key_summary, 0, "단축키", self.hotkey_summary_var)
        self._create_status_row(key_summary, 1, "게임 조작키", self.game_key_summary_var)

        right = ttk.Frame(body, padding=(16, 14), style="Panel.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        ttk.Label(right, text="진행 상황", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")

        progress = ttk.Frame(right, style="Panel.TFrame")
        progress.grid(row=1, column=0, sticky="ew", pady=(10, 14))
        progress.columnconfigure(0, weight=1)
        ttk.Label(progress, textvariable=self.progress_stage_var, style="PanelLead.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(progress, textvariable=self.detail_var, style="PanelMuted.TLabel", wraplength=390).grid(
            row=1,
            column=0,
            sticky="ew",
            pady=(6, 0),
        )

        quick_status = ttk.Frame(right, style="Panel.TFrame")
        quick_status.grid(row=2, column=0, sticky="ew")
        quick_status.columnconfigure(1, weight=1)
        self._create_status_row(quick_status, 0, "상태", self.detection_status_var)
        self._create_status_row(quick_status, 1, "최근 결과", self.recent_result_var)

        spacer = ttk.Frame(right, style="Panel.TFrame")
        spacer.grid(row=3, column=0, sticky="nsew")

        stats_bar = ttk.Frame(self.root, padding=(18, 9), style="Notice.TFrame")
        stats_bar.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 14))
        for column in range(3):
            stats_bar.columnconfigure(column, weight=1)
        self._create_stat_pill(stats_bar, 0, self.cast_count_var, "누적 낚시")
        self._create_stat_pill(stats_bar, 1, self.catch_count_var, "누적 성공")
        self._create_stat_pill(stats_bar, 2, self.run_time_var, "이번 실행")

    def _configure_styles(self) -> None:
        apply_theme(self.root)

    def _apply_window_icon(self, window: tk.Misc) -> None:
        self._icons.apply_to(window)

    def _create_info_row(
        self,
        parent: ttk.Frame,
        row: int,
        label_text: str,
        value_var: tk.StringVar,
    ) -> None:
        label = ttk.Label(parent, text=label_text, style="InfoLabel.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=(10 if row else 0, 0))
        value = ttk.Label(parent, textvariable=value_var, style="InfoValue.TLabel", wraplength=430)
        value.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=(10 if row else 0, 0))

    def _create_stat_card(
        self,
        parent: ttk.Frame,
        column: int,
        value_var: tk.StringVar,
        label_text: str,
    ) -> None:
        card = ttk.Frame(parent, padding=(14, 12), style="PanelAlt.TFrame")
        card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
        card.columnconfigure(0, weight=1)
        ttk.Label(card, textvariable=value_var, style="CardValue.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(card, text=label_text, style="CardTitle.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 0))

    def _create_status_card(
        self,
        parent: ttk.Frame,
        column: int,
        label_text: str,
        value_var: tk.StringVar,
    ) -> None:
        card = ttk.Frame(parent, padding=(14, 12), style="PanelAlt.TFrame")
        card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text=label_text, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(card, textvariable=value_var, style="StatusValue.TLabel", wraplength=120).grid(
            row=1,
            column=0,
            sticky="w",
            pady=(6, 0),
        )

    def _create_status_row(
        self,
        parent: ttk.Frame,
        row: int,
        label_text: str,
        value_var: tk.StringVar,
    ) -> None:
        ttk.Label(parent, text=label_text, style="PanelMuted.TLabel").grid(
            row=row,
            column=0,
            sticky="w",
            pady=(0 if row == 0 else 9, 0),
        )
        ttk.Label(parent, textvariable=value_var, style="PanelValue.TLabel", wraplength=260).grid(
            row=row,
            column=1,
            sticky="ew",
            padx=(18, 0),
            pady=(0 if row == 0 else 9, 0),
        )

    def _create_stat_pill(
        self,
        parent: ttk.Frame,
        column: int,
        value_var: tk.StringVar,
        label_text: str,
    ) -> None:
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
        ttk.Label(frame, textvariable=value_var, style="SmallStatValue.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text=label_text, style="SmallStatLabel.TLabel").grid(row=1, column=0, sticky="w")

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
        window.resizable(False, False)
        window.transient(self.root)

        start_var = tk.StringVar(value=self._settings.hotkeys.start_fishing)
        stop_var = tk.StringVar(value=self._settings.hotkeys.stop_fishing)
        social_var = tk.StringVar(value=self._settings.game_keys.social_menu)
        interact_var = tk.StringVar(value=self._settings.game_keys.interact_pickup)
        reel_var = tk.StringVar(value=self._settings.game_keys.reel)
        message_var = tk.StringVar(value="변경을 누른 뒤 사용할 키 하나를 입력하세요.")
        capture_target: dict[str, Optional[str]] = {"name": None}

        frame = ttk.Frame(window, padding=(18, 16), style="Dialog.TFrame")
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)

        header = ttk.Frame(frame, style="Dialog.TFrame")
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="설정", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="게임에서 지정한 키와 동일하게 설정해 주세요.",
            style="DialogMuted.TLabel",
            wraplength=420,
        ).grid(row=1, column=0, sticky="ew", pady=(4, 0))

        program_frame = ttk.LabelFrame(frame, text="프로그램 단축키", padding=(12, 10))
        program_frame.grid(row=1, column=0, columnspan=3, sticky="ew")
        program_frame.columnconfigure(1, weight=1)

        game_frame = ttk.LabelFrame(frame, text="Diablo IV 조작키", padding=(12, 10))
        game_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
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

        ttk.Label(frame, textvariable=message_var, style="DialogMuted.TLabel", wraplength=420).grid(
            row=3,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(12, 0),
        )

        button_frame = ttk.Frame(frame, style="Dialog.TFrame")
        button_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(16, 0))
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

        def begin_capture(target: str) -> None:
            capture_target["name"] = target
            labels = {
                "start": "낚시 시작",
                "stop": "낚시 중지",
                "social": "소셜 메뉴 호출",
                "interact": "상호작용 / 줍기",
                "reel": "낚싯대 회수",
            }
            label = labels.get(target, "설정")
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

        def on_key_press(event) -> str | None:
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
        self._center_child_window(window, width=520, height=445)
        self._apply_window_icon(window)
        window.deiconify()
        apply_windows_window_polish(window)
        window.lift()
        window.focus_force()
        window.grab_set()

    def _create_key_setting_row(
        self,
        parent: ttk.Frame,
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

    def _handle_window_detection_result(self, rect: Optional[WindowRect]) -> None:
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

        selection_rect: dict[str, Optional[int]] = {"id": None}
        drag_start: dict[str, Optional[int]] = {"x": None, "y": None}

        def clamp_point(x: int, y: int) -> tuple[int, int]:
            return max(0, min(rect.width, x)), max(0, min(rect.height, y))

        def normalize_roi(
            start_x: int,
            start_y: int,
            end_x: int,
            end_y: int,
        ) -> tuple[int, int, int, int]:
            start_x, start_y = clamp_point(start_x, start_y)
            end_x, end_y = clamp_point(end_x, end_y)
            left = min(start_x, end_x)
            top = min(start_y, end_y)
            right = max(start_x, end_x)
            bottom = max(start_y, end_y)
            return left, top, right - left, bottom - top

        def on_press(event) -> None:
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

        def on_drag(event) -> None:
            rect_id = selection_rect["id"]
            start_x = drag_start["x"]
            start_y = drag_start["y"]
            if rect_id is None or start_x is None or start_y is None:
                return
            end_x, end_y = clamp_point(int(event.x), int(event.y))
            canvas.coords(rect_id, start_x, start_y, end_x, end_y)

        def on_release(event) -> None:
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

    def _finish_roi_selection(self, roi: Optional[tuple[int, int, int, int]]) -> None:
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
        self.cast_count_var.set(f"{snapshot.total_cast_count}회")
        self.catch_count_var.set(f"{snapshot.total_catch_count}회")
        self.run_time_var.set(self._format_seconds(snapshot.session_run_seconds))
        self.recent_result_var.set("기록 없음" if snapshot.recent_result == "--" else snapshot.recent_result)

    @staticmethod
    def _format_seconds(seconds: int) -> str:
        seconds = max(0, int(seconds))
        minutes, seconds_part = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{seconds_part:02d}"
        return f"{minutes:02d}:{seconds_part:02d}"

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
        stage: Optional[AppStage] = None,
    ) -> None:
        self._worker_status = status
        self.run_status_var.set(self._format_run_status(status))
        self.status_badge_var.set(self._format_status_badge(status, stage))
        if stage is not None:
            self.stage_var.set(self._format_stage(stage))
            self.progress_stage_var.set(self._format_stage(stage))
            if status is RunStatus.RUNNING:
                self.detail_var.set(self._format_stage_detail(stage))
        self.detection_status_var.set(self._format_detection_status(status, stage))
        self._update_controls_for_state()

    def _update_controls_for_state(self) -> None:
        status = self._worker_status
        hotkeys = self._settings.hotkeys
        roi_ready = engine.get_current_fishing_search_roi() is not None

        if status is RunStatus.RUNNING:
            self.headline_var.set(self.progress_stage_var.get())
            self.primary_action_var.set("낚시 중지")
            self.primary_hint_var.set(f"{hotkeys.stop_fishing}으로도 중지할 수 있습니다.")
            self.primary_button.configure(style="Danger.TButton", state=tk.NORMAL)
        elif status is RunStatus.SELECTING_ROI:
            self.headline_var.set("낚시 위치를 설정해 주세요")
            self.detail_var.set("물가의 낚시 아이콘 영역을 드래그해 선택해 주세요.")
            self.primary_action_var.set("위치 설정 취소")
            self.primary_hint_var.set(f"{hotkeys.stop_fishing} 또는 ESC로 취소할 수 있습니다.")
            self.primary_button.configure(style="Danger.TButton", state=tk.NORMAL)
        elif status in (RunStatus.CHECKING_WINDOW, RunStatus.STOPPING):
            if status is RunStatus.CHECKING_WINDOW:
                self.headline_var.set("Diablo IV 창을 확인하는 중입니다")
                self.detail_var.set("게임 창을 찾으면 낚시 위치 설정으로 이어집니다.")
            else:
                self.headline_var.set("낚시를 중지하는 중입니다")
                self.detail_var.set("현재 동작을 마무리하고 안전하게 멈춥니다.")
            self.primary_action_var.set("처리 중")
            self.primary_hint_var.set("잠시만 기다려 주세요.")
            self.primary_button.configure(style="Primary.TButton", state=tk.DISABLED)
        else:
            if self._detected_window_rect is None:
                self.headline_var.set("Diablo IV 창을 찾지 못했습니다")
                self.detail_var.set("게임을 실행한 뒤 다시 확인해 주세요.")
                self.primary_action_var.set("게임 창 다시 확인")
                self.primary_hint_var.set("Diablo IV를 실행한 뒤 다시 확인해 주세요.")
            elif not roi_ready:
                self.headline_var.set("낚시 위치를 설정해 주세요")
                self.detail_var.set("물가의 낚시 아이콘 영역을 선택해 주세요.")
                self.primary_action_var.set("낚시 위치 설정")
                self.primary_hint_var.set("위치를 선택하면 낚시가 바로 시작됩니다.")
            else:
                if status is RunStatus.STOPPED:
                    self.headline_var.set("낚시를 중지했습니다")
                else:
                    self.headline_var.set("낚시 준비가 완료되었습니다")
                self.detail_var.set(f"버튼을 누르거나 {hotkeys.start_fishing}으로 시작할 수 있습니다.")
                self.primary_action_var.set("낚시 시작")
                self.primary_hint_var.set(f"{hotkeys.start_fishing}으로도 시작할 수 있습니다.")
            self.primary_button.configure(style="Primary.TButton", state=tk.NORMAL)

        badge_style = "Badge.TLabel"
        if status is RunStatus.RUNNING:
            badge_style = "SuccessBadge.TLabel"
        elif status in (RunStatus.CHECKING_WINDOW, RunStatus.SELECTING_ROI, RunStatus.STOPPING):
            badge_style = "WarningBadge.TLabel"
        elif status is RunStatus.ERROR:
            badge_style = "DangerBadge.TLabel"
        self.status_badge.configure(style=badge_style)

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
    def _format_stage(stage: Optional[AppStage]) -> str:
        labels = {
            AppStage.IDLE: "게임 창 확인 대기",
            AppStage.WINDOW_DETECTION: "Diablo IV 창 확인 중",
            AppStage.ROI_SELECTION: "낚시 위치 설정 중",
            AppStage.READY_TO_RUN: "낚시를 시작하는 중",
            AppStage.FIND_WINDOW: "Diablo IV 창 확인 중",
            AppStage.CAST: "낚싯대를 던지는 중",
            AppStage.FIND_BOBBER: "낚시 아이콘을 확인하는 중",
            AppStage.WAIT_READY: "입질을 기다리는 중",
            AppStage.REEL: "낚싯대를 회수하는 중",
            AppStage.LOOT: "아이템을 줍는 중",
            AppStage.RECAST: "다음 낚시를 준비하는 중",
            AppStage.STOPPING: "중지 중",
            AppStage.ERROR: "오류가 발생했습니다",
            AppStage.RUNNING: "진행 중",
        }
        return labels.get(stage, "--")

    @staticmethod
    def _format_stage_detail(stage: Optional[AppStage]) -> str:
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

    def _format_status_badge(self, status: RunStatus, stage: Optional[AppStage]) -> str:
        if status is RunStatus.IDLE and self._detected_window_rect is None:
            return "● 확인 필요"
        if status in (RunStatus.IDLE, RunStatus.STOPPED) and engine.get_current_fishing_search_roi() is None:
            return "● 위치 필요"
        if status in (RunStatus.IDLE, RunStatus.STOPPED):
            return "● 준비 완료"
        if status is RunStatus.SELECTING_ROI:
            return "● 위치 설정"
        if status is RunStatus.RUNNING:
            return "● 낚시 중"
        if status is RunStatus.STOPPING:
            return "● 중지 중"
        if status is RunStatus.STOPPED:
            return "● 중지됨"
        if status is RunStatus.ERROR:
            return "● 오류 발생"
        if stage is AppStage.ROI_SELECTION:
            return "● 위치 설정"
        return "● 대기"

    @staticmethod
    def _format_detection_status(status: RunStatus, stage: Optional[AppStage]) -> str:
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

    def _refresh_roi_status(self, override: Optional[str] = None) -> None:
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
