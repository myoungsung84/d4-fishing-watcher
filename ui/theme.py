from __future__ import annotations

import tkinter as tk
from tkinter import ttk


COLORS: dict[str, str] = {
    "bg": "#08111f",
    "panel": "#0e1b2d",
    "panel_alt": "#13243a",
    "panel_soft": "#101f33",
    "border": "#20354d",
    "text": "#f2f6fa",
    "muted": "#9fb0c3",
    "accent": "#2fb7c9",
    "accent_dark": "#176b80",
    "success": "#45c486",
    "warning": "#e6a34a",
    "danger": "#e15d64",
    "input": "#0b1727",
    "notice": "#0c1a2c",
}


def apply_theme(root: tk.Tk | tk.Toplevel) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(background=COLORS["bg"])
    style.configure(".", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 10))
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Panel.TFrame", background=COLORS["panel"])
    style.configure("PanelAlt.TFrame", background=COLORS["panel_alt"])
    style.configure("Notice.TFrame", background=COLORS["notice"])
    style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("AppName.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 13, "bold"))
    style.configure("Title.TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 16, "bold"))
    style.configure("Muted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
    style.configure("PanelMuted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"])
    style.configure("PanelTitle.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI", 11, "bold"))
    style.configure("PanelValue.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI", 10, "bold"))
    style.configure("PanelLead.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI", 12, "bold"))
    style.configure("NoticeLead.TLabel", background=COLORS["notice"], foreground=COLORS["text"], font=("Segoe UI", 11, "bold"))
    style.configure("NoticeMuted.TLabel", background=COLORS["notice"], foreground=COLORS["muted"])
    style.configure("SmallStatValue.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI", 15, "bold"))
    style.configure("SmallStatLabel.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=("Segoe UI", 9))
    style.configure("CardTitle.TLabel", background=COLORS["panel_alt"], foreground=COLORS["muted"], font=("Segoe UI", 9))
    style.configure("CardValue.TLabel", background=COLORS["panel_alt"], foreground=COLORS["text"], font=("Segoe UI", 18, "bold"))
    style.configure("StatusValue.TLabel", background=COLORS["panel_alt"], foreground=COLORS["text"], font=("Segoe UI", 11, "bold"))
    style.configure("Badge.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], padding=(4, 2), font=("Segoe UI", 9, "bold"))
    style.configure("SuccessBadge.TLabel", background=COLORS["bg"], foreground=COLORS["success"], padding=(4, 2), font=("Segoe UI", 9, "bold"))
    style.configure("WarningBadge.TLabel", background=COLORS["bg"], foreground=COLORS["warning"], padding=(4, 2), font=("Segoe UI", 9, "bold"))
    style.configure("DangerBadge.TLabel", background=COLORS["bg"], foreground=COLORS["danger"], padding=(4, 2), font=("Segoe UI", 9, "bold"))
    style.configure("InfoLabel.TLabel", background=COLORS["panel"], foreground=COLORS["muted"])
    style.configure("InfoValue.TLabel", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI", 10, "bold"))
    style.configure("Dialog.TFrame", background=COLORS["panel"])
    style.configure("Dialog.TLabel", background=COLORS["panel"], foreground=COLORS["text"])
    style.configure("DialogMuted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"])
    style.configure("DialogValue.TLabel", background=COLORS["panel_alt"], foreground=COLORS["text"], padding=(8, 5), font=("Segoe UI", 10, "bold"))

    style.configure(
        "TLabelframe",
        background=COLORS["panel"],
        foreground=COLORS["text"],
        bordercolor=COLORS["panel"],
        lightcolor=COLORS["panel"],
        darkcolor=COLORS["panel"],
        padding=(10, 8),
    )
    style.configure("TLabelframe.Label", background=COLORS["panel"], foreground=COLORS["text"], font=("Segoe UI", 10, "bold"))
    style.configure("TButton", background=COLORS["panel_alt"], foreground=COLORS["text"], bordercolor=COLORS["panel_alt"], focusthickness=0, padding=(12, 8))
    style.map(
        "TButton",
        background=[("active", COLORS["accent_dark"]), ("disabled", "#172234")],
        foreground=[("disabled", "#9aa6b5")],
    )
    style.configure("Primary.TButton", background=COLORS["accent_dark"], foreground=COLORS["text"], font=("Segoe UI", 11, "bold"), padding=(16, 10))
    style.map("Primary.TButton", background=[("active", COLORS["accent"]), ("disabled", "#173447")])
    style.configure("Danger.TButton", background="#7a2c35", foreground=COLORS["text"], font=("Segoe UI", 11, "bold"), padding=(16, 10))
    style.map("Danger.TButton", background=[("active", COLORS["danger"]), ("disabled", "#3a2630")])
    style.configure("Accent.TButton", background=COLORS["accent_dark"], foreground=COLORS["text"])
    style.configure("TScrollbar", background=COLORS["panel_alt"], troughcolor=COLORS["bg"], bordercolor=COLORS["border"], arrowcolor=COLORS["muted"])
