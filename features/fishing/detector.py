from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app import config
from app.lazy_imports import lazy_import
from core.screen import CaptureRegion

cv2 = lazy_import("cv2")
np = lazy_import("numpy")


@dataclass(frozen=True)
class TemplateImage:
    path: Path
    gray: np.ndarray
    mask: Optional[np.ndarray]
    width: int
    height: int


@dataclass(frozen=True)
class DetectionResult:
    found: bool
    score: float
    top_left: Optional[tuple[int, int]]
    center: Optional[tuple[int, int]]
    width: int
    height: int


@dataclass(frozen=True)
class ReadyColorBlob:
    bbox: tuple[int, int, int, int]
    center: tuple[int, int]
    area: float
    score: float


def load_template(template_path: Path) -> TemplateImage:
    image = cv2.imread(str(template_path), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise FileNotFoundError(f"Template image not found: {template_path}")

    if image.ndim == 2:
        gray = image
        mask = None
        height, width = gray.shape[:2]
        return TemplateImage(path=template_path, gray=gray, mask=mask, width=width, height=height)

    if image.shape[2] == 4:
        bgr = image[:, :, :3]
        alpha = image[:, :, 3]
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        mask = np.where(alpha > 0, 255, 0).astype(np.uint8)
        if cv2.countNonZero(mask) == 0:
            mask = None
    else:
        bgr = image[:, :, :3]
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        mask = None

    height, width = gray.shape[:2]
    return TemplateImage(path=template_path, gray=gray, mask=mask, width=width, height=height)


def find_best_match(
    frame_bgr: np.ndarray,
    capture_region: CaptureRegion,
    template: TemplateImage,
    threshold: float,
) -> DetectionResult:
    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

    if frame_gray.shape[0] < template.height or frame_gray.shape[1] < template.width:
        return DetectionResult(
            found=False,
            score=-1.0,
            top_left=None,
            center=None,
            width=template.width,
            height=template.height,
        )

    if template.mask is not None:
        match_result = cv2.matchTemplate(
            frame_gray,
            template.gray,
            cv2.TM_CCORR_NORMED,
            mask=template.mask,
        )
    else:
        match_result = cv2.matchTemplate(frame_gray, template.gray, cv2.TM_CCORR_NORMED)

    _, max_score, _, max_location = cv2.minMaxLoc(match_result)
    local_top_left = (
        capture_region.left + int(max_location[0]),
        capture_region.top + int(max_location[1]),
    )
    center = (
        local_top_left[0] + (template.width // 2),
        local_top_left[1] + (template.height // 2),
    )

    return DetectionResult(
        found=max_score >= threshold,
        score=float(max_score),
        top_left=local_top_left,
        center=center,
        width=template.width,
        height=template.height,
    )


def find_best_match_multi_scale(
    frame_bgr: np.ndarray,
    capture_region: CaptureRegion,
    template: TemplateImage,
    threshold: float,
    scales: tuple = (0.75, 0.85, 1.0, 1.15),
) -> tuple[DetectionResult, float]:
    best_result = None
    best_scale = 1.0
    best_score = -1.0

    for scale in scales:
        if scale < 0.5:
            continue

        scaled_width = max(1, int(template.width * scale))
        scaled_height = max(1, int(template.height * scale))
        scaled_template = cv2.resize(template.gray, (scaled_width, scaled_height))
        scaled_mask = None
        if template.mask is not None:
            scaled_mask = cv2.resize(template.mask, (scaled_width, scaled_height))

        frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        if frame_gray.shape[0] < scaled_height or frame_gray.shape[1] < scaled_width:
            continue

        if scaled_mask is not None:
            match_result = cv2.matchTemplate(
                frame_gray,
                scaled_template,
                cv2.TM_CCORR_NORMED,
                mask=scaled_mask,
            )
        else:
            match_result = cv2.matchTemplate(frame_gray, scaled_template, cv2.TM_CCORR_NORMED)

        _, max_score, _, max_location = cv2.minMaxLoc(match_result)
        if max_score > best_score:
            best_score = max_score
            best_scale = scale
            local_top_left = (
                capture_region.left + int(max_location[0]),
                capture_region.top + int(max_location[1]),
            )
            center = (
                local_top_left[0] + (scaled_width // 2),
                local_top_left[1] + (scaled_height // 2),
            )
            best_result = DetectionResult(
                found=max_score >= threshold,
                score=float(max_score),
                top_left=local_top_left,
                center=center,
                width=scaled_width,
                height=scaled_height,
            )

    if best_result is None:
        return (
            DetectionResult(
                found=False,
                score=-1.0,
                top_left=None,
                center=None,
                width=template.width,
                height=template.height,
            ),
            1.0,
        )
    return best_result, best_scale


def _crop_frame_for_roi(
    frame_bgr: np.ndarray,
    roi: tuple[int, int, int, int],
) -> tuple[np.ndarray, int, int]:
    roi_left, roi_top, roi_width, roi_height = roi
    frame_height, frame_width = frame_bgr.shape[:2]

    if (
        roi_left >= 0
        and roi_top >= 0
        and roi_left + roi_width <= frame_width
        and roi_top + roi_height <= frame_height
    ):
        return (
            frame_bgr[roi_top : roi_top + roi_height, roi_left : roi_left + roi_width],
            roi_left,
            roi_top,
        )

    if roi_width == frame_width and roi_height == frame_height:
        return frame_bgr, roi_left, roi_top

    clipped_left = max(0, roi_left)
    clipped_top = max(0, roi_top)
    clipped_right = min(frame_width, roi_left + roi_width)
    clipped_bottom = min(frame_height, roi_top + roi_height)
    if clipped_right <= clipped_left or clipped_bottom <= clipped_top:
        return np.zeros((0, 0, 3), dtype=frame_bgr.dtype), roi_left, roi_top

    return (
        frame_bgr[clipped_top:clipped_bottom, clipped_left:clipped_right],
        clipped_left,
        clipped_top,
    )


def build_ready_color_mask(
    frame_bgr: np.ndarray,
    roi: tuple[int, int, int, int],
) -> tuple[np.ndarray, tuple[int, int]]:
    crop_bgr, offset_x, offset_y = _crop_frame_for_roi(frame_bgr, roi)
    if crop_bgr.size == 0:
        return np.zeros((0, 0), dtype=np.uint8), (offset_x, offset_y)

    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    lower = np.array(config.CONFIG.ready_color_hsv_lower, dtype=np.uint8)
    upper = np.array(config.CONFIG.ready_color_hsv_upper, dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)

    kernel_size = max(1, int(config.CONFIG.ready_color_morph_kernel))
    kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, kernel)
    return mask, (offset_x, offset_y)


def score_ready_color_blob(blob: ReadyColorBlob) -> float:
    min_area = max(1, config.CONFIG.ready_color_min_area)
    max_area = max(min_area + 1, config.CONFIG.ready_color_max_area)
    normalized_area = min(1.0, max(0.0, (blob.area - min_area) / (max_area - min_area)))
    x, y, width, height = blob.bbox
    aspect = width / max(1, height)
    aspect_score = 1.0 - min(1.0, abs(aspect - 1.0))
    return float((normalized_area * 0.65) + (aspect_score * 0.35))


def detect_ready_color_blobs(
    frame_bgr: np.ndarray,
    roi: tuple[int, int, int, int],
) -> list[ReadyColorBlob]:
    mask, (offset_x, offset_y) = build_ready_color_mask(frame_bgr, roi)
    if mask.size == 0:
        return []

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blobs: list[ReadyColorBlob] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < config.CONFIG.ready_color_min_area:
            continue
        if area > config.CONFIG.ready_color_max_area:
            continue

        x, y, width, height = cv2.boundingRect(contour)
        screen_bbox = (offset_x + x, offset_y + y, width, height)
        center = (screen_bbox[0] + width // 2, screen_bbox[1] + height // 2)
        blob = ReadyColorBlob(
            bbox=screen_bbox,
            center=center,
            area=area,
            score=0.0,
        )
        blobs.append(
            ReadyColorBlob(
                bbox=blob.bbox,
                center=blob.center,
                area=blob.area,
                score=score_ready_color_blob(blob),
            )
        )

    blobs.sort(key=lambda item: item.score, reverse=True)
    return blobs
