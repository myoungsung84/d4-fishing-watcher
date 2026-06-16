from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent

@dataclass(frozen=True)
class FishingConfig:
    root_start_icon_source: str = "fishing_start_icon_transparent.png"
    root_ready_icon_source: str = "fishing_icon_transparent.png"
    window_keywords: list[str] = field(default_factory=lambda: ["Diablo IV", "디아블로 IV"])
    start_template_path: str = "templates/fishing_start_icon.png"
    ready_template_path: str = "templates/fishing_ready_icon.png"
    fishing_roi_select_key: str = "page_up"
    fishing_roi_min_width: int = 120
    fishing_roi_min_height: int = 120
    stop_hotkey: str = "page_down"
    fallback_base_width: int = 3440
    fallback_base_height: int = 1440
    initial_key: str = "3"
    reel_key: str = "e"
    after_start_click_before_loot_delay_seconds: float = 0.3
    after_loot_before_ready_delay_seconds: float = 0.3
    after_reel_delay_seconds: float = 1.0
    after_scroll_delay_seconds: float = 1.0
    loot_use_window_center: bool = True
    loot_center_ratio_x: float = 0.5
    loot_center_ratio_y: float = 0.5
    character_center_offset_x: int = 0
    character_center_offset_y: int = 0
    pickup_random_enabled: bool = True
    pickup_area_offset_x: int = 70
    pickup_area_offset_y: int = -45
    pickup_random_radius_x: int = 210
    pickup_random_radius_y: int = 160
    pickup_random_click_interval: float = 0.05
    pickup_random_first_center: bool = True
    loot_click_offsets: List[Tuple[int, int]] = field(
        default_factory=lambda: [
            (0, 0),
            (0, -45),
            (-35, -45),
            (35, -45),
            (-65, -35),
            (65, -35),
            (-45, 0),
            (45, 0),
            (-80, 0),
            (80, 0),
            (-55, -75),
            (55, -75),
            (90, -55),
            (-90, -55),
            (0, 45),
            (-40, 45),
            (40, 45),
            (-70, 65),
            (70, 65),
        ]
    )
    loot_sweep_passes: int = 2
    loot_click_interval_seconds: float = 0.015
    loot_key: str = "r"
    loot_key_press_count: int = 3
    loot_key_interval: float = 0.04
    loot_use_mouse_click: bool = False
    loot_scroll_amount: int = 5
    loot_scroll_repeat: int = 1
    loot_scroll_interval_seconds: float = 0.0
    start_threshold: float = 0.92
    start_template_scales: Tuple[float, ...] = (0.75, 0.85, 1.0, 1.15)
    start_recent_roi_radius_x: int = 180
    start_recent_roi_radius_y: int = 180
    start_character_search_radius_x: int = 520
    start_character_search_radius_y: int = 420
    ready_threshold: float = 0.91
    start_roi: Optional[Tuple[int, int, int, int]] = None
    ready_roi: Optional[Tuple[int, int, int, int]] = None
    ready_roi_ratio: Tuple[float, float, float, float] = (
        0.4506,
        0.1736,
        0.1453,
        0.2917,
    )
    ready_adaptive_search_enabled: bool = True
    ready_search_around_character: bool = True
    ready_fast_roi_enabled: bool = True
    character_ready_fast_radius_x: int = 160
    character_ready_fast_radius_y: int = 120
    ready_multi_scale_enabled: bool = True
    ready_template_scales: Tuple[float, ...] = (0.85, 1.0, 1.15)
    ready_scan_per_second: float = 10.0
    ready_scan_fps: float = 10.0
    ready_poll_interval: float = 0.1
    ready_debug_overlay_fps: float = 3.0
    ready_debug_overlay_interval: float = 0.333
    ready_timeout_reel_enabled: bool = True
    ready_timeout_reel_wait: float = 1.0
    next_cast_stabilize_after_timeout: float = 1.0
    ready_confirm_enabled: bool = False
    ready_quick_confirm_enabled: bool = False
    ready_quick_confirm_delay: float = 0.10
    ready_quick_confirm_max_distance: int = 45
    ready_use_last_roi: bool = False
    ready_use_bobber_roi: bool = False
    ready_single_confirm_rois: Tuple[str, ...] = ("character_fast", "character_full", "default")
    ready_confirm_max_distance: int = 35
    ready_confirm_timeout: float = 0.35
    ready_instant_confirm_score: float = 0.965
    ready_character_fast_threshold_bonus: float = 0.02
    character_ready_center_offset_x: int = 70
    character_ready_center_offset_y: int = -140
    character_ready_search_radius_x: int = 260
    character_ready_search_radius_y: int = 160
    ready_max_below_character_y: int = 40
    ready_fallback_to_default_roi: bool = True
    ready_min_screen_y: int = 180
    ready_last_pos_radius_x: int = 180
    ready_last_pos_radius_y: int = 180
    ready_debug_log: bool = True
    ready_debug_save_misses: bool = False
    ready_debug_save_interval_seconds: float = 3.0
    ready_debug_save_dir: str = "data/debug/ready_miss"
    ready_color_detection_enabled: bool = True
    ready_color_hsv_lower: Tuple[int, int, int] = (70, 60, 120)
    ready_color_hsv_upper: Tuple[int, int, int] = (95, 255, 255)
    ready_color_min_area: int = 20
    ready_color_max_area: int = 3000
    ready_color_min_frames: int = 2
    ready_color_morph_kernel: int = 3
    bobber_search_timeout_sec: float = 30.0
    bobber_track_fail_limit: int = 5
    bobber_reacquire_fail_limit: int = 10
    bobber_track_padding: int = 90
    bobber_reacquire_padding: int = 180
    bobber_locate_delay: float = 0.35
    bobber_locate_timeout: float = 1.5
    bobber_search_radius_x: int = 260
    bobber_search_radius_y: int = 220
    bobber_roi_radius_x: int = 90
    bobber_roi_radius_y: int = 90
    bobber_debug_log: bool = True
    bobber_indicator_hsv_lower: Tuple[int, int, int] = (70, 40, 80)
    bobber_indicator_hsv_upper: Tuple[int, int, int] = (110, 255, 255)
    bobber_indicator_min_area: int = 10
    ready_ignore_regions: List[Tuple[int, int, int, int]] = field(default_factory=list)
    ready_ignore_region_ratios: List[Tuple[float, float, float, float]] = field(
        default_factory=lambda: [
            (0.3721, 0.3125, 0.0349, 0.0833),
        ]
    )
    max_wait_ready_seconds: float = 25.0
    detect_interval_seconds: float = 0.08
    mouse_move_duration: float = 0.0
    debug_save_ready_match: bool = False
    debug_save_ready_roi: bool = False
    debug_dir: str = "debug"
    ready_wait_log_interval_seconds: float = 1.0
    window_wait_log_interval_seconds: float = 1.0
    dry_run: bool = False


CONFIG = FishingConfig()

# Module-level aliases for existing imports.
root_start_icon_source = CONFIG.root_start_icon_source
root_ready_icon_source = CONFIG.root_ready_icon_source
window_keywords = CONFIG.window_keywords
start_template_path = CONFIG.start_template_path
ready_template_path = CONFIG.ready_template_path
FISHING_ROI_SELECT_KEY = CONFIG.fishing_roi_select_key
FISHING_ROI_MIN_WIDTH = CONFIG.fishing_roi_min_width
FISHING_ROI_MIN_HEIGHT = CONFIG.fishing_roi_min_height
stop_hotkey = CONFIG.stop_hotkey
fallback_base_width = CONFIG.fallback_base_width
fallback_base_height = CONFIG.fallback_base_height
initial_key = CONFIG.initial_key
reel_key = CONFIG.reel_key
after_start_click_before_loot_delay_seconds = CONFIG.after_start_click_before_loot_delay_seconds
after_loot_before_ready_delay_seconds = CONFIG.after_loot_before_ready_delay_seconds
after_reel_delay_seconds = CONFIG.after_reel_delay_seconds
after_scroll_delay_seconds = CONFIG.after_scroll_delay_seconds
loot_use_window_center = CONFIG.loot_use_window_center
loot_center_ratio_x = CONFIG.loot_center_ratio_x
loot_center_ratio_y = CONFIG.loot_center_ratio_y
character_center_offset_x = CONFIG.character_center_offset_x
character_center_offset_y = CONFIG.character_center_offset_y
pickup_random_enabled = CONFIG.pickup_random_enabled
pickup_area_offset_x = CONFIG.pickup_area_offset_x
pickup_area_offset_y = CONFIG.pickup_area_offset_y
pickup_random_radius_x = CONFIG.pickup_random_radius_x
pickup_random_radius_y = CONFIG.pickup_random_radius_y
pickup_random_click_interval = CONFIG.pickup_random_click_interval
pickup_random_first_center = CONFIG.pickup_random_first_center
loot_click_offsets = CONFIG.loot_click_offsets
loot_offsets = CONFIG.loot_click_offsets
loot_sweep_passes = CONFIG.loot_sweep_passes
loot_click_interval_seconds = CONFIG.loot_click_interval_seconds
loot_key = CONFIG.loot_key
loot_key_press_count = CONFIG.loot_key_press_count
loot_key_interval = CONFIG.loot_key_interval
loot_use_mouse_click = CONFIG.loot_use_mouse_click
loot_scroll_amount = CONFIG.loot_scroll_amount
loot_scroll_repeat = CONFIG.loot_scroll_repeat
loot_scroll_interval_seconds = CONFIG.loot_scroll_interval_seconds
start_threshold = CONFIG.start_threshold
start_template_scales = CONFIG.start_template_scales
start_recent_roi_radius_x = CONFIG.start_recent_roi_radius_x
start_recent_roi_radius_y = CONFIG.start_recent_roi_radius_y
start_character_search_radius_x = CONFIG.start_character_search_radius_x
start_character_search_radius_y = CONFIG.start_character_search_radius_y
ready_threshold = CONFIG.ready_threshold
start_roi = CONFIG.start_roi
ready_roi = CONFIG.ready_roi
ready_roi_ratio = CONFIG.ready_roi_ratio
ready_adaptive_search_enabled = CONFIG.ready_adaptive_search_enabled
ready_search_around_character = CONFIG.ready_search_around_character
ready_fast_roi_enabled = CONFIG.ready_fast_roi_enabled
character_ready_fast_radius_x = CONFIG.character_ready_fast_radius_x
character_ready_fast_radius_y = CONFIG.character_ready_fast_radius_y
ready_multi_scale_enabled = CONFIG.ready_multi_scale_enabled
ready_template_scales = CONFIG.ready_template_scales
READY_SCAN_PER_SECOND = CONFIG.ready_scan_per_second
ready_scan_fps = CONFIG.ready_scan_fps
READY_POLL_INTERVAL = CONFIG.ready_poll_interval
ready_poll_interval = CONFIG.ready_poll_interval
ready_debug_overlay_fps = CONFIG.ready_debug_overlay_fps
ready_debug_overlay_interval = CONFIG.ready_debug_overlay_interval
ready_timeout_reel_enabled = CONFIG.ready_timeout_reel_enabled
ready_timeout_reel_wait = CONFIG.ready_timeout_reel_wait
next_cast_stabilize_after_timeout = CONFIG.next_cast_stabilize_after_timeout
ready_confirm_enabled = CONFIG.ready_confirm_enabled
ready_quick_confirm_enabled = CONFIG.ready_quick_confirm_enabled
ready_quick_confirm_delay = CONFIG.ready_quick_confirm_delay
ready_quick_confirm_max_distance = CONFIG.ready_quick_confirm_max_distance
ready_use_last_roi = CONFIG.ready_use_last_roi
ready_use_bobber_roi = CONFIG.ready_use_bobber_roi
ready_single_confirm_rois = CONFIG.ready_single_confirm_rois
ready_confirm_max_distance = CONFIG.ready_confirm_max_distance
ready_confirm_timeout = CONFIG.ready_confirm_timeout
ready_instant_confirm_score = CONFIG.ready_instant_confirm_score
ready_character_fast_threshold_bonus = CONFIG.ready_character_fast_threshold_bonus
character_ready_center_offset_x = CONFIG.character_ready_center_offset_x
character_ready_center_offset_y = CONFIG.character_ready_center_offset_y
character_ready_search_radius_x = CONFIG.character_ready_search_radius_x
character_ready_search_radius_y = CONFIG.character_ready_search_radius_y
ready_max_below_character_y = CONFIG.ready_max_below_character_y
ready_fallback_to_default_roi = CONFIG.ready_fallback_to_default_roi
ready_min_screen_y = CONFIG.ready_min_screen_y
ready_last_pos_radius_x = CONFIG.ready_last_pos_radius_x
ready_last_pos_radius_y = CONFIG.ready_last_pos_radius_y
ready_debug_log = CONFIG.ready_debug_log
ready_debug_save_misses = CONFIG.ready_debug_save_misses
ready_debug_save_interval_seconds = CONFIG.ready_debug_save_interval_seconds
ready_debug_save_dir = CONFIG.ready_debug_save_dir
READY_COLOR_DETECTION_ENABLED = CONFIG.ready_color_detection_enabled
READY_COLOR_HSV_LOWER = CONFIG.ready_color_hsv_lower
READY_COLOR_HSV_UPPER = CONFIG.ready_color_hsv_upper
READY_COLOR_MIN_AREA = CONFIG.ready_color_min_area
READY_COLOR_MAX_AREA = CONFIG.ready_color_max_area
READY_COLOR_MIN_FRAMES = CONFIG.ready_color_min_frames
READY_COLOR_MORPH_KERNEL = CONFIG.ready_color_morph_kernel
BOBBER_SEARCH_TIMEOUT_SEC = CONFIG.bobber_search_timeout_sec
BOBBER_TRACK_FAIL_LIMIT = CONFIG.bobber_track_fail_limit
BOBBER_REACQUIRE_FAIL_LIMIT = CONFIG.bobber_reacquire_fail_limit
BOBBER_TRACK_PADDING = CONFIG.bobber_track_padding
BOBBER_REACQUIRE_PADDING = CONFIG.bobber_reacquire_padding
bobber_locate_delay = CONFIG.bobber_locate_delay
bobber_locate_timeout = CONFIG.bobber_locate_timeout
bobber_search_radius_x = CONFIG.bobber_search_radius_x
bobber_search_radius_y = CONFIG.bobber_search_radius_y
bobber_roi_radius_x = CONFIG.bobber_roi_radius_x
bobber_roi_radius_y = CONFIG.bobber_roi_radius_y
bobber_debug_log = CONFIG.bobber_debug_log
bobber_indicator_hsv_lower = CONFIG.bobber_indicator_hsv_lower
bobber_indicator_hsv_upper = CONFIG.bobber_indicator_hsv_upper
bobber_indicator_min_area = CONFIG.bobber_indicator_min_area
ready_ignore_regions = CONFIG.ready_ignore_regions
ready_ignore_region_ratios = CONFIG.ready_ignore_region_ratios
max_wait_ready_seconds = CONFIG.max_wait_ready_seconds
detect_interval_seconds = CONFIG.detect_interval_seconds
mouse_move_duration = CONFIG.mouse_move_duration
debug_save_ready_match = CONFIG.debug_save_ready_match
debug_save_ready_roi = CONFIG.debug_save_ready_roi
debug_dir = CONFIG.debug_dir
ready_wait_log_interval_seconds = CONFIG.ready_wait_log_interval_seconds
window_wait_log_interval_seconds = CONFIG.window_wait_log_interval_seconds
dry_run = CONFIG.dry_run


def resolve_path(relative_path: str) -> Path:
    return (BASE_DIR / relative_path).resolve()


def resolve_root_icon_path(file_name: str) -> Path:
    return (BASE_DIR / file_name).resolve()
