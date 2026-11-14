"""
CAP Rebirth Bot - Interface Gráfica v4.2
- Suporte Global Multi-Idioma (12 Idiomas na Interface + Visão Computacional Universal Multi-Idioma)
- Sistema Duplo de Indicadores Visuais em Tempo Real (Ligar/Desligar Independente):
  * 🎯 Overlay HUD Transparente Click-Through sobre a tela do Roblox (invisível para capturas)
  * 📺 Mini-Monitor Ao Vivo em janela dedicada do App
- Gerenciador Multi-Janelas Ilimitado (1..N contas abertas via Roblox Account Manager)
- Seleção independente de recursos por janela (Principal, [E], Ovos [Z], Altar, Missão [C], Anti-AFK [C+Q], Reconectar)
- Velocidade de leitura da tela, frequência de giro de janelas e Auto-Pausa do PC (Teclado/Mouse) 100% personalizáveis
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import sys
import os
import json
import time
import logging
import threading
import cv2
import numpy as np
from PIL import Image, ImageTk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot_core import RebirthBot, BotStatus, BotState, RobloxFinder, WindowProfile
from bot_vision import PET_NAMES
from bot_i18n import LANGUAGES, tr, tr_items
from bot_overlay import VisualIndicatorManager
from bot_tray import SystemTrayManager

APP_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)
SETTINGS_FILE = os.path.join(APP_DIR, "bot_settings.json")

C = {
    "bg":       "#0d0f18",
    "card":     "#161926",
    "dark":     "#090a10",
    "accent":   "#00d4ff",
    "green":    "#00e676",
    "red":      "#ff1744",
    "yellow":   "#ffd740",
    "text":     "#e4e8ff",
    "dim":      "#656b8a",
    "border":   "#23283e",
    "btn_start":"#00c853",
    "btn_stop": "#d50000",
    "btn_pause":"#ff8f00",
    "test_btn": "#242a42",
}

EGG_COLORS = [
    "#ffffff",  # Common
    "#55dd55",  # Uncommon
    "#4488ff",  # Rare
    "#d844ff",  # Epic
    "#ff9900",  # Legendary
    "#ff3388",  # Prodigious
    "#00eeff",  # Ascended
    "#ff3333",  # Mythical
]


class CAPBotGUI:
    W, H = 650, 915

    def __init__(self):
        self.lang = "PT-BR"
        self._lang_label_to_code = {label: code for code, label in LANGUAGES}
        self._lang_code_to_label = {code: label for code, label in LANGUAGES}
        self._loading_settings = False

        self.bot = RebirthBot()
        self.bot.add_callback(self._on_bot_update)
        self.bot.add_event_callback(self._on_bot_event)
        self._win_row_vars: dict[int, dict] = {}
        self._last_hwnd_tuple = ()
        self._tip_win = None
        self._tip_after_id = None
        self._tip_widget = None
        self._guide_win = None
        self._guide_widgets: dict[str, tk.Widget] = {}
        self._safety_win = None
        self._calib_win = None
        self._settings_win = None
        self._settings_hk_btns: dict[str, tk.Button] = {}
        self._checklist_visible = True
        self._log_visible = True
        self._in_tray = False
        self._afk_warn_stage_by_hwnd: dict[int, int] = {}
        self._build_root()
        self.tray_mgr = SystemTrayManager(
            self.root,
            on_restore_cb=self._restore_from_tray,
            on_toggle_start_cb=self._toggle,
            on_toggle_pause_cb=self._pause,
            on_exit_cb=self._on_close,
            tr_fn=self._t,
        )
        self.visual_mgr = VisualIndicatorManager(
            self.root,
            self.bot,
            on_monitor_closed_cb=self._on_mini_monitor_closed_by_x,
        )
        self.bot.set_live_frame_callback(self.visual_mgr.notify_immediate_frame)
        self._rebirth_mode = "off"
        self._build_ui()
        self._load_startup_preferences()
        self._refresh_window_list_ui(force=True)
        self._poll_roblox()
        self._tick_uptime()
        self._tick_visual_indicators()
        self._tick_afk_warning_monitor()

    def _t(self, key: str, **kwargs) -> str:
        return tr(self.lang, key, **kwargs)

    # ── Sistema de Balão de Explicação (Hover Tooltips Multi-Idioma) ──────────

    def _bind_tooltip(self, widget: tk.Widget, i18n_key: str):
        """Associa um balão explicativo dinâmico (traduzido em tempo real) ao passar o mouse."""
        def _on_enter(event):
            self._schedule_tooltip(widget, i18n_key, event.x_root, event.y_root)

        def _on_leave(event):
            self._hide_tooltip()

        def _on_motion(event):
            if self._tip_win is not None and self._tip_widget == widget:
                self._reposition_tooltip(event.x_root, event.y_root)

        widget.bind("<Enter>", _on_enter, add="+")
        widget.bind("<Leave>", _on_leave, add="+")
        widget.bind("<Motion>", _on_motion, add="+")
        widget.bind("<ButtonPress>", _on_leave, add="+")

    def _schedule_tooltip(self, widget: tk.Widget, i18n_key: str, x_root: int, y_root: int):
        self._hide_tooltip()
        self._tip_widget = widget
        self._tip_after_id = self.root.after(
            260, lambda: self._show_tooltip(widget, i18n_key, x_root, y_root)
        )

    def _show_tooltip(self, widget: tk.Widget, i18n_key: str, x_root: int, y_root: int):
        if self._tip_widget != widget:
            return
        try:
            if not widget.winfo_exists():
                return
        except Exception:
            return

        text = self._t(i18n_key)
        if not text or text == i18n_key:
            return

        try:
            px = widget.winfo_pointerx()
            py = widget.winfo_pointery()
        except Exception:
            px, py = x_root, y_root

        tw = tk.Toplevel(self.root)
        tw.wm_overrideredirect(True)
        try:
            tw.wm_attributes("-topmost", True)
        except Exception:
            pass
        tw.configure(bg=C["accent"])

        inner = tk.Frame(tw, bg="#101524", padx=10, pady=7)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        lbl = tk.Label(
            inner,
            text=text,
            fg=C["text"],
            bg="#101524",
            font=("Segoe UI", 8),
            justify="left",
            anchor="w",
            wraplength=360,
        )
        lbl.pack()

        self._tip_win = tw
        self._reposition_tooltip(px, py)

    def _reposition_tooltip(self, px: int, py: int):
        if self._tip_win is None:
            return
        try:
            self._tip_win.update_idletasks()
            w = self._tip_win.winfo_reqwidth()
            h = self._tip_win.winfo_reqheight()
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()

            x = px + 14
            y = py + 18
            if x + w + 8 > sw:
                x = max(8, px - w - 12)
            if y + h + 8 > sh:
                y = max(8, py - h - 12)
            self._tip_win.wm_geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _hide_tooltip(self):
        if self._tip_after_id is not None:
            try:
                self.root.after_cancel(self._tip_after_id)
            except Exception:
                pass
            self._tip_after_id = None
        if self._tip_win is not None:
            try:
                self._tip_win.destroy()
            except Exception:
                pass
            self._tip_win = None
        self._tip_widget = None

    def _build_root(self):
        self.root = tk.Tk()
        self.root.title("CAP Assistant v4.2 — Multi-Idioma Global, Overlay HUD & Multi-Janelas")
        self.root.resizable(True, True)
        self.root.configure(bg=C["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.root.update_idletasks()
        sx = self.root.winfo_screenwidth()
        sy = self.root.winfo_screenheight()

        # Auto-ajusta a altura e largura da janela para caber perfeitamente em qualquer monitor
        # (ex: notebooks 1366x768, 1600x900, 1920x1080, 2560x1440) sem cortar o rodapé!
        avail_h = max(500, sy - 68)
        target_h = min(900, avail_h)
        target_w = min(self.W, max(580, sx - 40))
        min_h = min(500, target_h)
        self.root.minsize(580, min_h)
        pos_x = max(0, (sx - target_w) // 2)
        pos_y = max(0, min((sy - target_h) // 2, sy - target_h - 38))
        self.root.geometry(f"{target_w}x{target_h}+{pos_x}+{pos_y}")

    def _fit_window_to_content(self):
        """Reajusta suavemente a altura da janela quando o usuário oculta/mostra seções."""
        try:
            self.root.update_idletasks()
            sy = self.root.winfo_screenheight()
            avail_h = max(480, sy - 68)
            req_h = self._body_inner.winfo_reqheight() + 92
            cur_w = max(580, self.root.winfo_width())
            new_h = max(420, min(avail_h, req_h))
            self.root.geometry(f"{cur_w}x{new_h}")
        except Exception:
            pass

    def _build_ui(self):
        # ── Header Fixo no Topo ──────────────────────────────────────────────
        hdr = tk.Frame(self.root, bg=C["dark"], pady=7, padx=10)
        hdr.pack(fill="x", side="top")

        hdr_top = tk.Frame(hdr, bg=C["dark"])
        hdr_top.pack(fill="x")

        tk.Label(
            hdr_top,
            text="🐾  CAP Assistant v4.2",
            fg=C["accent"],
            bg=C["dark"],
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left")

        self._btn_tray = tk.Button(
            hdr_top,
            text=self._t("btn_tray"),
            fg="#10B981",
            bg=C["test_btn"],
            activebackground=C["border"],
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=6,
            pady=3,
            font=("Segoe UI", 8, "bold"),
            command=self._minimize_to_tray,
        )
        self._btn_tray.pack(side="right", padx=(4, 0))
        self._bind_tooltip(self._btn_tray, "tip_btn_tray")

        self._btn_shortcuts = tk.Button(
            hdr_top,
            text=self._t("btn_settings_app"),
            fg=C["yellow"],
            bg=C["test_btn"],
            activebackground=C["border"],
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=6,
            pady=3,
            font=("Segoe UI", 8, "bold"),
            command=self._open_app_settings_modal,
        )
        self._btn_shortcuts.pack(side="right", padx=(4, 0))
        self._bind_tooltip(self._btn_shortcuts, "tip_btn_settings_app")

        self._btn_safety = tk.Button(
            hdr_top,
            text=self._t("btn_safety"),
            fg=C["accent"],
            bg=C["test_btn"],
            activebackground=C["border"],
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=6,
            pady=3,
            font=("Segoe UI", 8, "bold"),
            command=self._show_safety_info,
        )
        self._btn_safety.pack(side="right", padx=(4, 0))
        self._bind_tooltip(self._btn_safety, "tip_btn_safety")

        self._btn_guide = tk.Button(
            hdr_top,
            text=self._t("btn_guide"),
            fg=C["green"],
            bg=C["test_btn"],
            activebackground=C["border"],
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=6,
            pady=3,
            font=("Segoe UI", 8, "bold"),
            command=self._show_user_guide,
        )
        self._btn_guide.pack(side="right", padx=(4, 0))
        self._bind_tooltip(self._btn_guide, "tip_btn_guide")

        # Seletor de Idioma Global (12 Idiomas)
        lang_frame = tk.Frame(hdr_top, bg=C["dark"])
        lang_frame.pack(side="right")

        lbl_globe = tk.Label(
            lang_frame,
            text="🌐",
            fg=C["accent"],
            bg=C["dark"],
            font=("Segoe UI", 10),
        )
        lbl_globe.pack(side="left", padx=(0, 3))

        self._var_lang = tk.StringVar(value=self._lang_code_to_label[self.lang])
        self._cmb_lang = ttk.Combobox(
            lang_frame,
            textvariable=self._var_lang,
            values=[label for _, label in LANGUAGES],
            state="readonly",
            width=12,
            font=("Segoe UI", 8, "bold"),
        )
        self._cmb_lang.pack(side="left")
        self._cmb_lang.bind("<<ComboboxSelected>>", self._on_language_change)
        self._bind_tooltip(lbl_globe, "tip_lang")
        self._bind_tooltip(self._cmb_lang, "tip_lang")

        self._lbl_subtitle = tk.Label(
            hdr,
            text=self._t("subtitle"),
            fg=C["dim"],
            bg=C["dark"],
            font=("Segoe UI", 8),
            anchor="w",
        )
        self._lbl_subtitle.pack(fill="x", pady=(2, 0))
        tk.Frame(self.root, bg=C["accent"], height=2).pack(fill="x", side="top")

        # ── Barra de Status Fixa no Rodapé ───────────────────────────────────
        self._status_line = tk.StringVar(value=self._t("status_ready"))
        tk.Label(
            self.root,
            textvariable=self._status_line,
            fg=C["dim"],
            bg=C["dark"],
            font=("Consolas", 8),
            anchor="w",
            padx=8,
            pady=2,
        ).pack(fill="x", side="bottom")

        # ── Área Central Responsiva com Rolagem Automática para Telas Menores ──
        body_wrap = tk.Frame(self.root, bg=C["bg"])
        body_wrap.pack(fill="both", expand=True)

        self._main_canvas = tk.Canvas(body_wrap, bg=C["bg"], highlightthickness=0, bd=0)
        self._main_vbar = tk.Scrollbar(body_wrap, orient="vertical", command=self._main_canvas.yview)
        body = tk.Frame(self._main_canvas, bg=C["bg"], padx=10, pady=6)
        self._body_inner = body

        def _on_body_configure(event=None):
            self._main_canvas.configure(scrollregion=self._main_canvas.bbox("all"))
            # Exibe a barra de rolagem lateral somente se a altura do conteúdo ultrapassar a janela
            req_h = body.winfo_reqheight()
            can_h = self._main_canvas.winfo_height()
            if req_h > can_h + 6:
                if not self._main_vbar.winfo_ismapped():
                    self._main_vbar.pack(side="right", fill="y")
            else:
                if self._main_vbar.winfo_ismapped():
                    self._main_vbar.pack_forget()

        body.bind("<Configure>", _on_body_configure)
        self._main_canvas_win = self._main_canvas.create_window((0, 0), window=body, anchor="nw")

        def _on_canvas_configure(event):
            self._main_canvas.itemconfig(self._main_canvas_win, width=event.width)
            _on_body_configure()

        self._main_canvas.bind("<Configure>", _on_canvas_configure)
        self._main_canvas.configure(yscrollcommand=self._main_vbar.set)
        self._main_canvas.pack(side="left", fill="both", expand=True)

        def _on_global_mousewheel(event):
            try:
                w = event.widget
                if w == self._mw_canvas or str(w).startswith(str(self._mw_list_frame)):
                    self._mw_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                    return
                if self._body_inner.winfo_reqheight() > self._main_canvas.winfo_height() + 6:
                    self._main_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass

        self.root.bind_all("<MouseWheel>", _on_global_mousewheel, add="+")

        # ── Detecção do Cliente Roblox + Ovo Ativo ───────────────────────────
        client_card = tk.Frame(body, bg=C["card"], highlightbackground=C["border"], highlightthickness=1)
        client_card.pack(fill="x", pady=(0, 5))

        c_row = tk.Frame(client_card, bg=C["card"], padx=8, pady=4)
        c_row.pack(fill="x")
        self._lbl_roblox_status = tk.Label(
            c_row,
            text=self._t("searching_roblox"),
            fg=C["yellow"],
            bg=C["card"],
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )
        self._lbl_roblox_status.pack(side="left")

        self._lbl_active_egg = tk.Label(
            c_row,
            text=self._t("egg_bar", egg="..."),
            fg=C["accent"],
            bg=C["card"],
            font=("Segoe UI", 9, "bold"),
            anchor="e",
        )
        self._lbl_active_egg.pack(side="right")

        # ── GERENCIADOR MULTI-JANELAS (1..N JANELAS INDEPENDENTES) ───────────
        mw_card = tk.Frame(body, bg=C["card"], highlightbackground=C["border"], highlightthickness=1)
        mw_card.pack(fill="x", pady=(0, 5))

        mw_hdr = tk.Frame(mw_card, bg=C["card"], padx=8, pady=4)
        mw_hdr.pack(fill="x")

        self._lbl_mw_title = tk.Label(
            mw_hdr,
            text=self._t("mw_title"),
            fg=C["accent"],
            bg=C["card"],
            font=("Segoe UI", 8, "bold"),
        )
        self._lbl_mw_title.pack(side="left")

        self._btn_refresh_wins = tk.Button(
            mw_hdr,
            text=self._t("btn_refresh"),
            fg=C["text"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            padx=6,
            pady=1,
            font=("Segoe UI", 7, "bold"),
            command=lambda: self._refresh_window_list_ui(force=True),
        )
        self._btn_refresh_wins.pack(side="right")

        mw_scroll_wrap = tk.Frame(mw_card, bg=C["dark"], padx=4, pady=3)
        mw_scroll_wrap.pack(fill="x", padx=6, pady=(0, 5))

        self._mw_canvas = tk.Canvas(
            mw_scroll_wrap,
            bg=C["dark"],
            height=90,
            highlightthickness=0,
            bd=0,
        )
        self._mw_scrollbar = tk.Scrollbar(
            mw_scroll_wrap, orient="vertical", command=self._mw_canvas.yview
        )
        self._mw_list_frame = tk.Frame(self._mw_canvas, bg=C["dark"])

        self._mw_list_frame.bind(
            "<Configure>",
            lambda e: self._mw_canvas.configure(scrollregion=self._mw_canvas.bbox("all")),
        )
        self._mw_canvas_win = self._mw_canvas.create_window(
            (0, 0), window=self._mw_list_frame, anchor="nw"
        )
        self._mw_canvas.bind(
            "<Configure>",
            lambda e: self._mw_canvas.itemconfig(self._mw_canvas_win, width=e.width),
        )
        self._mw_canvas.configure(yscrollcommand=self._mw_scrollbar.set)

        self._mw_canvas.pack(side="left", fill="both", expand=True)
        self._mw_scrollbar.pack(side="right", fill="y")

        # ── ESPAÇO DEDICADO DE REBIRTH (MODO LEVE / SEMI-AUTO / FULL-AUTO) ──
        reb_card = tk.Frame(body, bg=C["card"], highlightbackground=C["border"], highlightthickness=1)
        reb_card.pack(fill="x", pady=(0, 5))

        reb_hdr = tk.Frame(reb_card, bg=C["card"], padx=8, pady=3)
        reb_hdr.pack(fill="x")

        self._lbl_rebirth_sec_title = tk.Label(
            reb_hdr,
            text=self._t("rebirth_sec_title"),
            fg=C["yellow"],
            bg=C["card"],
            font=("Segoe UI", 8, "bold"),
        )
        self._lbl_rebirth_sec_title.pack(side="left")

        reb_btns_row = tk.Frame(reb_card, bg=C["card"], padx=6, pady=2)
        reb_btns_row.pack(fill="x")

        self._btn_reb_off = tk.Button(
            reb_btns_row,
            text=self._t("reb_mode_off"),
            fg=C["text"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            pady=4,
            font=("Segoe UI", 8, "bold"),
            command=lambda: self._set_rebirth_mode("off"),
        )
        self._btn_reb_off.pack(side="left", fill="x", expand=True, padx=(0, 3))

        self._btn_reb_semi = tk.Button(
            reb_btns_row,
            text=self._t("reb_mode_semi"),
            fg=C["yellow"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            pady=4,
            font=("Segoe UI", 8, "bold"),
            command=lambda: self._set_rebirth_mode("semi"),
        )
        self._btn_reb_semi.pack(side="left", fill="x", expand=True, padx=3)

        self._btn_reb_full = tk.Button(
            reb_btns_row,
            text=self._t("reb_mode_full"),
            fg=C["green"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            pady=4,
            font=("Segoe UI", 8, "bold"),
            command=lambda: self._set_rebirth_mode("full"),
        )
        self._btn_reb_full.pack(side="left", fill="x", expand=True, padx=(3, 0))

        # ── CONFIGURAÇÕES DE VELOCIDADE, ALTERNÂNCIA, AUTO-PAUSA & INDICADORES VISUAIS ──
        cfg_card = tk.Frame(body, bg=C["card"], highlightbackground=C["border"], highlightthickness=1)
        cfg_card.pack(fill="x", pady=(0, 5))

        self._lbl_cfg_title = tk.Label(
            cfg_card,
            text=self._t("cfg_title"),
            fg=C["accent"],
            bg=C["card"],
            font=("Segoe UI", 8, "bold"),
        )
        self._lbl_cfg_title.pack(anchor="w", padx=8, pady=(4, 2))

        # Linha 1: Velocidade de Leitura da Tela + Frequência de Alternância entre Janelas
        row_afk = tk.Frame(cfg_card, bg=C["card"], padx=8, pady=2)
        row_afk.pack(fill="x")

        self._lbl_screen_hz = tk.Label(
            row_afk,
            text=self._t("lbl_screen_hz"),
            fg=C["green"],
            bg=C["card"],
            font=("Segoe UI", 8, "bold"),
        )
        self._lbl_screen_hz.pack(side="left")

        self._var_screen_hz = tk.StringVar(value="0.15")
        sp_hz = tk.Spinbox(
            row_afk,
            from_=0.05,
            to=5.00,
            increment=0.05,
            format="%.2f",
            textvariable=self._var_screen_hz,
            width=5,
            bg=C["dark"],
            fg=C["green"],
            insertbackground="white",
            buttonbackground=C["test_btn"],
            relief="flat",
            font=("Consolas", 9, "bold"),
            command=self._update_config,
        )
        self._sp_hz = sp_hz
        sp_hz.pack(side="left", padx=(4, 10))
        sp_hz.bind("<KeyRelease>", lambda e: self._update_config())

        self._lbl_afk_interval = tk.Label(
            row_afk,
            text=self._t("lbl_afk_interval"),
            fg=C["text"],
            bg=C["card"],
            font=("Segoe UI", 8, "bold"),
        )
        self._lbl_afk_interval.pack(side="left")

        self._var_afk_interval = tk.StringVar(value="120")
        sp_afk = tk.Spinbox(
            row_afk,
            from_=5,
            to=900,
            increment=10,
            textvariable=self._var_afk_interval,
            width=5,
            bg=C["dark"],
            fg=C["accent"],
            insertbackground="white",
            buttonbackground=C["test_btn"],
            relief="flat",
            font=("Consolas", 9, "bold"),
            command=self._update_config,
        )
        self._sp_afk = sp_afk
        sp_afk.pack(side="left", padx=(4, 4))
        sp_afk.bind("<KeyRelease>", lambda e: self._update_config())

        # Linha 2: Auto-Pausa ao usar o PC (Teclado ou Mouse) + tempo de ociosidade
        row_mouse = tk.Frame(cfg_card, bg=C["card"], padx=8, pady=2)
        row_mouse.pack(fill="x")

        self._var_auto_pause_mouse = tk.BooleanVar(value=True)
        self._chk_pause_pc = tk.Checkbutton(
            row_mouse,
            text=self._t("chk_pause_pc"),
            variable=self._var_auto_pause_mouse,
            bg=C["card"],
            fg=C["yellow"],
            selectcolor=C["dark"],
            activebackground=C["card"],
            font=("Segoe UI", 8, "bold"),
            command=self._update_config,
        )
        self._chk_pause_pc.pack(side="left")

        self._var_mouse_idle_sec = tk.StringVar(value="60")
        sp_mouse = tk.Spinbox(
            row_mouse,
            from_=5,
            to=600,
            increment=5,
            textvariable=self._var_mouse_idle_sec,
            width=5,
            bg=C["dark"],
            fg=C["yellow"],
            insertbackground="white",
            buttonbackground=C["test_btn"],
            relief="flat",
            font=("Consolas", 9, "bold"),
            command=self._update_config,
        )
        self._sp_mouse = sp_mouse
        sp_mouse.pack(side="left", padx=(4, 4))
        sp_mouse.bind("<KeyRelease>", lambda e: self._update_config())

        # Linha 3: Sistema Duplo de Indicadores Visuais (Overlay HUD + Mini-Monitor Ao Vivo)
        row_vis = tk.Frame(cfg_card, bg=C["dark"], padx=8, pady=3)
        row_vis.pack(fill="x", padx=6, pady=(2, 4))

        self._lbl_visual_title = tk.Label(
            row_vis,
            text=self._t("lbl_visual_title"),
            fg=C["accent"],
            bg=C["dark"],
            font=("Segoe UI", 8, "bold"),
        )
        self._lbl_visual_title.pack(side="left", padx=(0, 6))

        self._var_overlay_hud = tk.BooleanVar(value=False)
        self._chk_overlay_hud = tk.Checkbutton(
            row_vis,
            text=self._t("chk_overlay_hud"),
            variable=self._var_overlay_hud,
            bg=C["dark"],
            fg=C["green"],
            selectcolor=C["card"],
            activebackground=C["dark"],
            font=("Segoe UI", 8, "bold"),
            command=self._on_toggle_visual_indicators,
        )
        self._chk_overlay_hud.pack(side="left", padx=(0, 8))

        self._var_mini_monitor = tk.BooleanVar(value=False)
        self._chk_mini_monitor = tk.Checkbutton(
            row_vis,
            text=self._t("chk_mini_monitor"),
            variable=self._var_mini_monitor,
            bg=C["dark"],
            fg=C["accent"],
            selectcolor=C["card"],
            activebackground=C["dark"],
            font=("Segoe UI", 8, "bold"),
            command=self._on_toggle_visual_indicators,
        )
        self._chk_mini_monitor.pack(side="left")

        # Linha 4: Modo de Inicialização (Padrão Básico vs Última Config) + Salvar/Exportar & Carregar/Importar
        row_preset = tk.Frame(cfg_card, bg=C["dark"], padx=6, pady=3)
        row_preset.pack(fill="x", padx=6, pady=(0, 4))

        self._lbl_startup_mode = tk.Label(
            row_preset,
            text=self._t("lbl_startup_mode"),
            fg=C["yellow"],
            bg=C["dark"],
            font=("Segoe UI", 7, "bold"),
        )
        self._lbl_startup_mode.pack(side="left", padx=(0, 3))

        self._var_startup_mode = tk.StringVar(value="default")
        self._rad_start_default = tk.Radiobutton(
            row_preset,
            text=self._t("rad_start_default"),
            variable=self._var_startup_mode,
            value="default",
            bg=C["dark"],
            fg=C["text"],
            selectcolor=C["card"],
            activebackground=C["dark"],
            font=("Segoe UI", 7, "bold"),
            command=self._on_startup_mode_change,
        )
        self._rad_start_default.pack(side="left", padx=(0, 4))

        self._rad_start_last = tk.Radiobutton(
            row_preset,
            text=self._t("rad_start_last"),
            variable=self._var_startup_mode,
            value="last",
            bg=C["dark"],
            fg=C["green"],
            selectcolor=C["card"],
            activebackground=C["dark"],
            font=("Segoe UI", 7, "bold"),
            command=self._on_startup_mode_change,
        )
        self._rad_start_last.pack(side="left", padx=(0, 4))

        self._btn_load_cfg = tk.Button(
            row_preset,
            text=self._t("btn_load_cfg"),
            fg=C["accent"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            padx=5,
            pady=1,
            font=("Segoe UI", 7, "bold"),
            command=self._import_config_dialog,
        )
        self._btn_load_cfg.pack(side="right", padx=(3, 0))

        self._btn_save_cfg = tk.Button(
            row_preset,
            text=self._t("btn_save_cfg"),
            fg=C["green"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            padx=5,
            pady=1,
            font=("Segoe UI", 7, "bold"),
            command=self._export_config_dialog,
        )
        self._btn_save_cfg.pack(side="right")

        hk_t_up = self.bot.cfg.hotkey_toggle.upper()
        hk_p_up = self.bot.cfg.hotkey_pause.upper()

        # ── Botões de Controle Principal ─────────────────────────────────────
        btn_row = tk.Frame(body, bg=C["bg"])
        btn_row.pack(fill="x", pady=(0, 3))

        self._btn_start = tk.Button(
            btn_row,
            text=self._t("btn_start", hk=hk_t_up),
            fg="white",
            bg=C["btn_start"],
            activebackground="#00e676",
            font=("Segoe UI", 11, "bold"),
            relief="flat",
            cursor="hand2",
            pady=7,
            command=self._toggle,
        )
        self._btn_start.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self._btn_pause = tk.Button(
            btn_row,
            text=self._t("btn_pause", hk=hk_p_up),
            fg="white",
            bg=C["btn_pause"],
            activebackground="#ffa000",
            font=("Segoe UI", 11, "bold"),
            relief="flat",
            cursor="hand2",
            pady=7,
            state="disabled",
            command=self._pause,
        )
        self._btn_pause.pack(side="left", fill="x", expand=True, padx=(4, 0))

        self._lbl_hotkeys_hint = tk.Label(
            body,
            text=self._t("hotkeys_hint", hk_start=hk_t_up, hk_pause=hk_p_up),
            fg=C["dim"],
            bg=C["bg"],
            font=("Segoe UI", 7),
        )
        self._lbl_hotkeys_hint.pack(pady=(0, 5))

        # ── Suite de Testes Rápidos ──────────────────────────────────────────
        test_frame = tk.Frame(body, bg=C["card"], highlightbackground=C["border"], highlightthickness=1)
        test_frame.pack(fill="x", pady=(0, 5))

        self._lbl_diag_title = tk.Label(
            test_frame,
            text=self._t("diag_title"),
            fg=C["dim"],
            bg=C["card"],
            font=("Segoe UI", 8, "bold"),
        )
        self._lbl_diag_title.pack(anchor="w", padx=8, pady=(3, 1))

        t_btns = tk.Frame(test_frame, bg=C["card"], padx=6, pady=4)
        t_btns.pack(fill="x")

        self._btn_t_read = tk.Button(
            t_btns,
            text=self._t("btn_t_read"),
            fg=C["text"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 8),
            command=self._test_screen,
        )
        self._btn_t_read.pack(side="left", fill="x", expand=True, padx=2)

        self._btn_t_egg = tk.Button(
            t_btns,
            text=self._t("btn_t_egg"),
            fg=C["text"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 8, "bold"),
            command=self._test_egg,
        )
        self._btn_t_egg.pack(side="left", fill="x", expand=True, padx=2)

        self._btn_t_e = tk.Button(
            t_btns,
            text=self._t("btn_t_e"),
            fg=C["text"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 8),
            command=self._test_press_e,
        )
        self._btn_t_e.pack(side="left", fill="x", expand=True, padx=2)

        self._btn_t_rebirth = tk.Button(
            t_btns,
            text=self._t("btn_t_rebirth"),
            fg=C["text"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 8),
            command=self._test_rebirth_hold,
        )
        self._btn_t_rebirth.pack(side="left", fill="x", expand=True, padx=2)

        self._btn_t_altar = tk.Button(
            t_btns,
            text=self._t("btn_t_altar"),
            fg=C["accent"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 8, "bold"),
            command=self._test_altar_route,
        )
        self._btn_t_altar.pack(side="left", fill="x", expand=True, padx=2)

        self._btn_calib_altar = tk.Button(
            t_btns,
            text=self._t("btn_calib_altar"),
            fg="#F97316",
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 8, "bold"),
            command=self._open_altar_calibration_modal,
        )
        self._btn_calib_altar.pack(side="left", fill="x", expand=True, padx=2)

        self._btn_t_quest = tk.Button(
            t_btns,
            text=self._t("btn_t_quest"),
            fg=C["green"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 8, "bold"),
            command=self._test_quest,
        )
        self._btn_t_quest.pack(side="left", fill="x", expand=True, padx=2)

        self._btn_t_afk = tk.Button(
            t_btns,
            text=self._t("btn_t_afk"),
            fg=C["yellow"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 8, "bold"),
            command=self._test_anti_afk,
        )
        self._btn_t_afk.pack(side="left", fill="x", expand=True, padx=2)

        self._btn_t_rec = tk.Button(
            t_btns,
            text=self._t("btn_t_rec"),
            fg=C["accent"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 8, "bold"),
            command=self._test_reconnect,
        )
        self._btn_t_rec.pack(side="left", fill="x", expand=True, padx=2)

        # ── Cards de Estatísticas ─────────────────────────────────────────────
        stat_row = tk.Frame(body, bg=C["bg"])
        stat_row.pack(fill="x", pady=(0, 5))

        self._card_rebirths = self._make_card(stat_row, self._t("card_rebirths"), "0", C["accent"])
        self._card_rebirths.pack(side="left", fill="both", expand=True, padx=(0, 3))

        self._card_quests = self._make_card(stat_row, self._t("card_quests"), "0", C["green"])
        self._card_quests.pack(side="left", fill="both", expand=True, padx=(3, 3))

        self._card_torches = self._make_card(stat_row, self._t("card_afk"), "0", C["yellow"])
        self._card_torches.pack(side="left", fill="both", expand=True, padx=(3, 3))

        self._card_uptime = self._make_card(stat_row, self._t("card_uptime"), "00:00:00", C["green"])
        self._card_uptime.pack(side="left", fill="both", expand=True, padx=(3, 0))

        # ── Status dos 8 Tiers do Checklist (Recolhível: Mostrar / Ocultar) ───
        egg_box = tk.Frame(body, bg=C["card"], highlightbackground=C["border"], highlightthickness=1)
        egg_box.pack(fill="x", pady=(0, 5))

        egg_hdr = tk.Frame(egg_box, bg=C["card"], padx=8, pady=3)
        egg_hdr.pack(fill="x")

        self._lbl_checklist_title = tk.Label(
            egg_hdr,
            text=self._t("checklist_title"),
            fg=C["accent"],
            bg=C["card"],
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
        )
        self._lbl_checklist_title.pack(side="left")
        self._lbl_checklist_title.bind("<Button-1>", lambda e: self._toggle_checklist_visibility())

        self._btn_toggle_checklist = tk.Button(
            egg_hdr,
            text=self._t("btn_hide_sec"),
            fg=C["yellow"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            padx=6,
            pady=0,
            font=("Segoe UI", 7, "bold"),
            command=self._toggle_checklist_visibility,
        )
        self._btn_toggle_checklist.pack(side="right")

        grid = tk.Frame(egg_box, bg=C["card"], padx=6, pady=2)
        grid.pack(fill="x")
        self._checklist_body_frame = grid

        self._egg_widgets = []
        for i, (name, total) in enumerate(PET_NAMES):
            r = i // 2
            col = i % 2
            cell = tk.Frame(grid, bg=C["dark"], padx=6, pady=2)
            cell.grid(row=r, column=col, padx=3, pady=1, sticky="ew")
            grid.columnconfigure(col, weight=1)

            lbl_name = tk.Label(
                cell,
                text=f"{name} ({total})",
                fg=EGG_COLORS[i],
                bg=C["dark"],
                font=("Segoe UI", 8, "bold"),
                width=14,
                anchor="w",
            )
            lbl_name.pack(side="left")

            lbl_st = tk.Label(
                cell,
                text=self._t("st_pending"),
                fg=C["dim"],
                bg=C["dark"],
                font=("Consolas", 8),
            )
            lbl_st.pack(side="right")
            self._bind_tooltip(cell, "tip_checklist")
            self._bind_tooltip(lbl_name, "tip_checklist")
            self._bind_tooltip(lbl_st, "tip_checklist")
            self._egg_widgets.append(lbl_st)

        # ── Log de Atividade em Tempo Real (Recolhível: Mostrar / Ocultar) ────
        log_box = tk.Frame(body, bg=C["card"], highlightbackground=C["border"], highlightthickness=1)
        log_box.pack(fill="both", expand=True)

        log_hdr = tk.Frame(log_box, bg=C["card"], padx=8, pady=3)
        log_hdr.pack(fill="x")
        self._lbl_log_title = tk.Label(
            log_hdr,
            text=self._t("log_title"),
            fg=C["accent"],
            bg=C["card"],
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
        )
        self._lbl_log_title.pack(side="left")
        self._lbl_log_title.bind("<Button-1>", lambda e: self._toggle_log_visibility())

        self._btn_toggle_log = tk.Button(
            log_hdr,
            text=self._t("btn_hide_sec"),
            fg=C["yellow"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            padx=6,
            pady=0,
            font=("Segoe UI", 7, "bold"),
            command=self._toggle_log_visibility,
        )
        self._btn_toggle_log.pack(side="right", padx=(4, 0))

        self._btn_clear_log = tk.Button(
            log_hdr,
            text=self._t("btn_clear"),
            fg=C["dim"],
            bg=C["card"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 7),
            command=self._clear_log,
        )
        self._btn_clear_log.pack(side="right", padx=(2, 0))

        self._btn_save_log = tk.Button(
            log_hdr,
            text=self._t("btn_save_log"),
            fg=C["green"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            padx=5,
            pady=0,
            font=("Segoe UI", 7, "bold"),
            command=self._save_log_to_file,
        )
        self._btn_save_log.pack(side="right", padx=(2, 2))

        self._btn_copy_log = tk.Button(
            log_hdr,
            text=self._t("btn_copy_log"),
            fg=C["accent"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            padx=5,
            pady=0,
            font=("Segoe UI", 7, "bold"),
            command=self._copy_log_to_clipboard,
        )
        self._btn_copy_log.pack(side="right", padx=(2, 2))

        self._log_body_frame = tk.Frame(log_box, bg=C["card"])
        self._log_body_frame.pack(fill="both", expand=True)

        self._log = tk.Text(
            self._log_body_frame,
            height=5,
            bg=C["dark"],
            fg=C["text"],
            font=("Consolas", 8),
            relief="flat",
            state="disabled",
            wrap="word",
            padx=6,
            pady=4,
        )
        self._log.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        for tag, col in [
            ("info", C["text"]),
            ("ok", C["green"]),
            ("warn", C["yellow"]),
            ("err", C["red"]),
            ("hi", C["accent"]),
        ]:
            self._log.tag_config(tag, foreground=col)

        self._bind_all_static_tooltips()
        self._setup_log_handler()

    def _bind_all_static_tooltips(self):
        """Associa balões explicativos (tooltips) a todos os controles e indicadores da janela."""
        self._bind_tooltip(self._lbl_roblox_status, "tip_roblox_status")
        self._bind_tooltip(self._lbl_active_egg, "tip_active_egg")
        self._bind_tooltip(self._lbl_mw_title, "tip_win_role")
        self._bind_tooltip(self._btn_refresh_wins, "tip_btn_refresh")
        self._bind_tooltip(self._lbl_rebirth_sec_title, "tip_reb_semi")
        self._bind_tooltip(self._btn_reb_off, "tip_reb_off")
        self._bind_tooltip(self._btn_reb_semi, "tip_reb_semi")
        self._bind_tooltip(self._btn_reb_full, "tip_reb_full")

        self._bind_tooltip(self._lbl_screen_hz, "tip_screen_hz")
        self._bind_tooltip(self._sp_hz, "tip_screen_hz")
        self._bind_tooltip(self._lbl_afk_interval, "tip_afk_interval")
        self._bind_tooltip(self._sp_afk, "tip_afk_interval")
        self._bind_tooltip(self._chk_pause_pc, "tip_pause_pc")
        self._bind_tooltip(self._sp_mouse, "tip_pause_pc")

        self._bind_tooltip(self._lbl_visual_title, "tip_overlay_hud")
        self._bind_tooltip(self._chk_overlay_hud, "tip_overlay_hud")
        self._bind_tooltip(self._chk_mini_monitor, "tip_mini_monitor")

        self._bind_tooltip(self._lbl_startup_mode, "tip_rad_default")
        self._bind_tooltip(self._rad_start_default, "tip_rad_default")
        self._bind_tooltip(self._rad_start_last, "tip_rad_last")
        self._bind_tooltip(self._btn_save_cfg, "tip_btn_save_cfg")
        self._bind_tooltip(self._btn_load_cfg, "tip_btn_load_cfg")

        self._bind_tooltip(self._btn_start, "tip_btn_start")
        self._bind_tooltip(self._btn_pause, "tip_btn_pause")

        self._bind_tooltip(self._btn_t_read, "tip_t_read")
        self._bind_tooltip(self._btn_t_egg, "tip_t_egg")
        self._bind_tooltip(self._btn_t_e, "tip_t_e")
        self._bind_tooltip(self._btn_t_rebirth, "tip_t_rebirth")
        self._bind_tooltip(self._btn_t_altar, "tip_t_altar")
        self._bind_tooltip(self._btn_calib_altar, "tip_btn_calib_altar")
        self._bind_tooltip(self._btn_t_quest, "tip_t_quest")
        self._bind_tooltip(self._btn_t_afk, "tip_t_afk")
        self._bind_tooltip(self._btn_t_rec, "tip_t_rec")

        for card_w, key in [
            (self._card_rebirths, "tip_card_rebirths"),
            (self._card_quests, "tip_card_quests"),
            (self._card_torches, "tip_card_afk"),
            (self._card_uptime, "tip_card_uptime"),
        ]:
            self._bind_tooltip(card_w, key)
            self._bind_tooltip(card_w._title_label, key)
            self._bind_tooltip(card_w._val_label, key)

        self._bind_tooltip(self._lbl_checklist_title, "tip_checklist")
        self._bind_tooltip(self._btn_toggle_checklist, "tip_toggle_checklist")
        self._bind_tooltip(self._btn_toggle_log, "tip_toggle_log")
        self._bind_tooltip(self._btn_clear_log, "tip_btn_clear")
        self._bind_tooltip(self._btn_copy_log, "tip_btn_copy_log")
        self._bind_tooltip(self._btn_save_log, "tip_btn_save_log")

    # ── Ocultar / Mostrar Seções (Checklist de Ovos & Log de Atividade) ───────

    def _set_checklist_visibility(self, visible: bool, fit_win: bool = True):
        self._checklist_visible = bool(visible)
        if self._checklist_visible:
            self._checklist_body_frame.pack(fill="x")
            self._btn_toggle_checklist.config(text=self._t("btn_hide_sec"), fg=C["yellow"])
        else:
            self._checklist_body_frame.pack_forget()
            self._btn_toggle_checklist.config(text=self._t("btn_show_sec"), fg=C["accent"])
        if fit_win:
            self._fit_window_to_content()

    def _toggle_checklist_visibility(self):
        self._set_checklist_visibility(not self._checklist_visible, fit_win=True)
        self._auto_save_settings()

    def _set_log_visibility(self, visible: bool, fit_win: bool = True):
        self._log_visible = bool(visible)
        if self._log_visible:
            self._log_body_frame.pack(fill="both", expand=True)
            self._btn_toggle_log.config(text=self._t("btn_hide_sec"), fg=C["yellow"])
        else:
            self._log_body_frame.pack_forget()
            self._btn_toggle_log.config(text=self._t("btn_show_sec"), fg=C["accent"])
        if fit_win:
            self._fit_window_to_content()

    def _toggle_log_visibility(self):
        self._set_log_visibility(not self._log_visible, fit_win=True)
        self._auto_save_settings()

    # ── Minimizar para a Bandeja do Sistema (System Tray) & Alertas de Balão ──

    def _minimize_to_tray(self):
        self._hide_tooltip()
        self._in_tray = True
        st_lbl = "🟢 CAP Assistant (Ativo)" if self.bot.status.running else "⚪ CAP Assistant (Bandeja)"
        self.tray_mgr.show_tray_dock(st_lbl)
        self.root.withdraw()
        if self.bot.cfg.notif_tray_minimize:
            self.tray_mgr.show_balloon(
                self._t("tray_minimized_title"),
                self._t("tray_minimized_msg"),
                warning=False,
            )

    def _restore_from_tray(self):
        self._in_tray = False
        self.tray_mgr.hide_tray_dock()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _on_bot_event(self, event_type: str, title: str, message: str):
        """Recebe eventos importantes do RebirthBot (Reconexão, Renascimento) e exibe balão na Bandeja conforme configurado."""
        if event_type == "rebirth" and not self.bot.cfg.notif_rebirth:
            return
        if event_type == "reconnect" and not self.bot.cfg.notif_reconnect:
            return
        self.root.after(
            0,
            lambda: self.tray_mgr.show_balloon(
                title, message, warning=(event_type == "reconnect")
            ),
        )

    def _tick_afk_warning_monitor(self):
        """
        Monitora o tempo sem pulso Anti-AFK de todas as janelas abertas do Roblox:
        Caso o usuário deixe o assistente em 'Pausa' manual ou em 'Auto-Pausa (Usando o PC)' por muito tempo,
        emite alertas de balão no System Tray aos 15 minutos (5 min antes de cair) e aos 18 minutos
        (2 min antes de cair pelo limite de 20 minutos do Roblox), caso a notificação esteja ativa.
        """
        try:
            s = self.bot.status
            is_paused_or_busy = (s.running and (s.paused or s.auto_paused_mouse)) or (not s.running)
            now = time.time()

            for hwnd, prof in list(self.bot.window_profiles.items()):
                if not prof.auto_anti_afk:
                    continue
                elapsed_sec = now - float(prof.last_afk_reset_ts or now)
                elapsed_min = int(elapsed_sec // 60)

                if elapsed_sec < 120:
                    # Resetou o timer AFK recentemente -> zera estágio de alerta desta janela
                    self._afk_warn_stage_by_hwnd[hwnd] = 0
                    continue

                if not is_paused_or_busy:
                    continue

                prev_stage = self._afk_warn_stage_by_hwnd.get(hwnd, 0)
                if elapsed_sec >= 18 * 60 and prev_stage < 2:
                    self._afk_warn_stage_by_hwnd[hwnd] = 2
                    rem_min = max(1, 20 - elapsed_min)
                    title = self._t("afk_warn_title", rem=rem_min)
                    msg = self._t("afk_warn_msg", idx=prof.index, elap=elapsed_min)
                    if self.bot.cfg.notif_afk_warn:
                        self.tray_mgr.show_balloon(title, msg, warning=True)
                    self._append_log(f"⚠️ {title} {msg}", "warn")
                elif elapsed_sec >= 15 * 60 and prev_stage < 1:
                    self._afk_warn_stage_by_hwnd[hwnd] = 1
                    rem_min = max(1, 20 - elapsed_min)
                    title = self._t("afk_warn_title", rem=rem_min)
                    msg = self._t("afk_warn_msg", idx=prof.index, elap=elapsed_min)
                    if self.bot.cfg.notif_afk_warn:
                        self.tray_mgr.show_balloon(title, msg, warning=True)
                    self._append_log(f"⚠️ {title} {msg}", "warn")
        except Exception:
            pass
        self.root.after(5000, self._tick_afk_warning_monitor)

    # ── Calibrador Visual Interativo do Altar (Tochas + Mesa Central) ─────────

    def _open_altar_calibration_modal(self):
        """
        Abre uma ferramenta visual interativa onde o usuário vê a captura real da sua janela do Roblox
        e clica nos 5 pontos exatos onde deseja que o personagem pise:
        1. Tocha 1 (SW - Inferior Esquerda)
        2. Tocha 2 (NW - Superior Esquerda)
        3. Tocha 3 (NE - Superior Direita)
        4. Tocha 4 (SE - Inferior Direita)
        5. Frente da Mesa Central (P0 - Rebirth)
        Salva tanto o offset relativo à Mesa Central (para funcionar mesmo se a câmera mexer levemente)
        quanto a proporção direta da tela, além do tempo máximo de caminhada por tocha.
        """
        if self._calib_win is not None:
            try:
                if self._calib_win.winfo_exists():
                    self._calib_win.lift()
                    self._calib_win.focus_force()
                    return
            except Exception:
                pass

        pri = self.bot.get_primary_profile()
        if not pri:
            messagebox.showwarning(self._t("calib_win_title"), self._t("no_roblox"))
            return

        frame = self.bot.vision.capture_window(pri.hwnd)
        if frame is None or frame.size == 0:
            messagebox.showwarning(self._t("calib_win_title"), "Não foi possível capturar a janela do Roblox.")
            return

        win = tk.Toplevel(self.root)
        self._calib_win = win
        win.title(self._t("calib_win_title"))
        win.configure(bg=C["bg"])
        win.transient(self.root)

        sx = win.winfo_screenwidth()
        sy = win.winfo_screenheight()
        win_w = min(820, max(640, sx - 60))
        win_h = min(740, max(540, sy - 70))
        win.geometry(f"{win_w}x{win_h}+{max(0, (sx - win_w) // 2)}+{max(0, (sy - win_h) // 2)}")

        hdr = tk.Frame(win, bg=C["dark"], padx=12, pady=8)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=f"🎯  {self._t('calib_hdr_title')}",
            fg=C["accent"],
            bg=C["dark"],
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            hdr,
            text=self._t("calib_hdr_sub"),
            fg=C["text"],
            bg=C["dark"],
            font=("Segoe UI", 8),
            anchor="w",
        ).pack(fill="x", pady=(2, 0))
        tk.Frame(win, bg=C["accent"], height=2).pack(fill="x")

        step_keys = ["SW", "NW", "NE", "SE", "P0"]
        step_i18n = {
            "SW": "calib_step_sw",
            "NW": "calib_step_nw",
            "NE": "calib_step_ne",
            "SE": "calib_step_se",
            "P0": "calib_step_p0",
        }
        state = {
            "frame": frame,
            "step_idx": 0,
            "clicked_pts": {},  # key -> (fx, fy) no frame original
            "scale": 1.0,
            "off_x": 0,
            "off_y": 0,
            "photo": None,
        }

        # Pré-carrega os pontos atuais (da IA ou da calibração já ativa)
        auto_pts = self.bot.vision.detect_altar_points(frame) or {}
        for k in step_keys:
            if k in auto_pts:
                state["clicked_pts"][k] = auto_pts[k]

        info_bar = tk.Frame(win, bg="#101524", padx=10, pady=6)
        info_bar.pack(fill="x", padx=10, pady=(6, 4))

        lbl_step = tk.Label(
            info_bar,
            text=self._t(step_i18n["SW"]),
            fg=C["yellow"],
            bg="#101524",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )
        lbl_step.pack(side="left", fill="x", expand=True)

        cur_cal = self.bot.vision.custom_altar_calibration
        lbl_mode = tk.Label(
            info_bar,
            text=self._t("calib_status_custom" if cur_cal.get("enabled") else "calib_status_auto"),
            fg=C["green"] if cur_cal.get("enabled") else C["accent"],
            bg="#101524",
            font=("Consolas", 8, "bold"),
        )
        lbl_mode.pack(side="right")

        # Barra de seleção rápida de qual ponto editar + Tempo de caminhada
        ctrl_bar = tk.Frame(win, bg=C["card"], padx=10, pady=5)
        ctrl_bar.pack(fill="x", padx=10, pady=(0, 4))

        step_btns = {}

        def _select_step(idx: int):
            state["step_idx"] = idx
            if idx < len(step_keys):
                k = step_keys[idx]
                lbl_step.config(text=self._t(step_i18n[k]), fg=C["yellow"])
            else:
                lbl_step.config(text=self._t("calib_done"), fg=C["green"])
            for i_b, b_w in step_btns.items():
                b_w.config(
                    bg=C["accent"] if i_b == idx else C["test_btn"],
                    fg="#090a10" if i_b == idx else C["text"],
                )
            _redraw_preview()

        for i_k, k_name in enumerate(step_keys):
            b = tk.Button(
                ctrl_bar,
                text=f"{i_k+1}. {k_name}",
                bg=C["test_btn"],
                fg=C["text"],
                relief="flat",
                cursor="hand2",
                padx=8,
                pady=2,
                font=("Consolas", 8, "bold"),
                command=lambda ii=i_k: _select_step(ii),
            )
            b.pack(side="left", padx=(0, 4))
            step_btns[i_k] = b

        tk.Label(
            ctrl_bar,
            text=self._t("calib_walk_time"),
            fg=C["text"],
            bg=C["card"],
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left", padx=(12, 4))

        var_walk_sec = tk.StringVar(value=f"{float(cur_cal.get('walk_wait_sec', 3.8)):.1f}")
        sp_walk = tk.Spinbox(
            ctrl_bar,
            from_=1.8,
            to=8.0,
            increment=0.2,
            format="%.1f",
            textvariable=var_walk_sec,
            width=5,
            bg=C["dark"],
            fg=C["green"],
            insertbackground="white",
            relief="flat",
            font=("Consolas", 9, "bold"),
        )
        sp_walk.pack(side="left")

        # Canvas interativo com a imagem da janela do jogo
        can_wrap = tk.Frame(win, bg=C["dark"], highlightbackground=C["border"], highlightthickness=1)
        can_wrap.pack(fill="both", expand=True, padx=10, pady=4)

        can = tk.Canvas(can_wrap, bg="#07090e", highlightthickness=0, cursor="crosshair")
        can.pack(fill="both", expand=True)

        def _redraw_preview(event=None):
            f_bgr = state["frame"]
            if f_bgr is None:
                return
            fh, fw = f_bgr.shape[:2]
            cw = max(200, can.winfo_width())
            ch = max(160, can.winfo_height())
            scale = min(cw / float(fw), ch / float(fh))
            nw, nh = max(100, int(fw * scale)), max(80, int(fh * scale))
            off_x = (cw - nw) // 2
            off_y = (ch - nh) // 2
            state["scale"] = scale
            state["off_x"] = off_x
            state["off_y"] = off_y

            vis = f_bgr.copy()
            t_rect = self.bot.vision.detect_altar_table_rect(vis)
            if t_rect is not None:
                rx, ry, rw, rh, _ = t_rect
                cv2.rectangle(
                    vis,
                    (int(rx), int(ry)),
                    (int(rx + rw), int(ry + rh)),
                    (255, 200, 40),
                    2,
                )

            for idx_k, k in enumerate(step_keys):
                if k in state["clicked_pts"]:
                    px, py = state["clicked_pts"][k]
                    is_cur = (idx_k == state["step_idx"])
                    col = (0, 255, 120) if k == "P0" else ((0, 220, 255) if is_cur else (20, 130, 255))
                    r = 12 if is_cur else 9
                    cv2.circle(vis, (int(px), int(py)), r, col, -1)
                    cv2.circle(vis, (int(px), int(py)), r + 2, (255, 255, 255), 2)
                    cv2.putText(
                        vis,
                        f"{idx_k+1}:{k}",
                        (int(px) + 12, int(py) + 5),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.55,
                        (255, 255, 255),
                        2,
                    )

            resized = cv2.resize(vis, (nw, nh), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            state["photo"] = ImageTk.PhotoImage(Image.fromarray(rgb))
            can.delete("all")
            can.create_image(off_x, off_y, anchor="nw", image=state["photo"])

        def _on_canvas_click(event):
            f_bgr = state["frame"]
            if f_bgr is None:
                return
            fh, fw = f_bgr.shape[:2]
            scale = state["scale"]
            if scale <= 0:
                return
            fx = int(round((event.x - state["off_x"]) / scale))
            fy = int(round((event.y - state["off_y"]) / scale))
            if not (0 <= fx < fw and 0 <= fy < fh):
                return

            idx = state["step_idx"]
            if idx >= len(step_keys):
                idx = 0
            k = step_keys[idx]
            state["clicked_pts"][k] = (fx, fy)
            next_idx = idx + 1 if idx + 1 < len(step_keys) else len(step_keys)
            _select_step(next_idx)

        can.bind("<Configure>", _redraw_preview)
        can.bind("<Button-1>", _on_canvas_click)

        # Rodapé de botões
        ftr = tk.Frame(win, bg=C["dark"], padx=10, pady=8)
        ftr.pack(fill="x", side="bottom")

        def _do_recapture():
            p_now = self.bot.get_primary_profile()
            if not p_now:
                return
            f_new = self.bot.vision.capture_window(p_now.hwnd)
            if f_new is not None and f_new.size > 0:
                state["frame"] = f_new
                _redraw_preview()

        def _do_reset_auto():
            self.bot.vision.set_custom_altar_calibration({"enabled": False})
            lbl_mode.config(text=self._t("calib_status_auto"), fg=C["accent"])
            auto_now = self.bot.vision.detect_altar_points(state["frame"]) or {}
            for k in step_keys:
                if k in auto_now:
                    state["clicked_pts"][k] = auto_now[k]
            _select_step(0)
            self._auto_save_settings()
            self._append_log("🤖 [Altar] Calibração redefinida para Detecção Automática por IA (Padrão).", "hi")

        def _do_save_custom():
            f_bgr = state["frame"]
            fh, fw = f_bgr.shape[:2]
            s_f = fh / 552.0
            t_hit = self.bot.vision.detect_altar_table(f_bgr)
            offsets = {}
            ratios = {}
            for k in step_keys:
                if k not in state["clicked_pts"]:
                    continue
                px, py = state["clicked_pts"][k]
                ratios[k] = [round(px / float(fw), 5), round(py / float(fh), 5)]
                if t_hit is not None:
                    tcx, tcy, _ = t_hit
                    offsets[k] = [
                        round((px - tcx) / float(s_f), 2),
                        round((py - tcy) / float(s_f), 2),
                    ]
            try:
                w_sec = max(1.8, min(8.0, float(var_walk_sec.get().replace(",", "."))))
            except Exception:
                w_sec = 3.8

            cal_data = {
                "enabled": True,
                "mode": "table_offset" if t_hit is not None else "screen_ratio",
                "offsets": offsets,
                "ratios": ratios,
                "walk_wait_sec": w_sec,
            }
            self.bot.vision.set_custom_altar_calibration(cal_data)
            lbl_mode.config(text=self._t("calib_status_custom"), fg=C["green"])
            self._auto_save_settings()
            self._append_log(
                f"🎯 [Altar] Calibração personalizada salva com sucesso ({len(ratios)} pontos | espera={w_sec:.1f}s)!",
                "ok",
            )
            messagebox.showinfo(
                self._t("calib_win_title"),
                f"Calibração personalizada salva e ativada com sucesso!\nModo: {cal_data['mode']} | Tempo máx./tocha: {w_sec:.1f}s",
                parent=win,
            )

        tk.Button(
            ftr,
            text=self._t("calib_btn_recapture"),
            fg=C["text"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 8, "bold"),
            padx=10,
            pady=5,
            command=_do_recapture,
        ).pack(side="left", padx=(0, 6))

        tk.Button(
            ftr,
            text=self._t("calib_btn_reset_auto"),
            fg=C["yellow"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 8, "bold"),
            padx=10,
            pady=5,
            command=_do_reset_auto,
        ).pack(side="left", padx=(0, 6))

        tk.Button(
            ftr,
            text=self._t("calib_btn_close"),
            fg=C["text"],
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 8, "bold"),
            padx=12,
            pady=5,
            command=win.destroy,
        ).pack(side="right")

        tk.Button(
            ftr,
            text=self._t("calib_btn_save"),
            fg="white",
            bg=C["btn_start"],
            activebackground=C["green"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            padx=14,
            pady=5,
            command=_do_save_custom,
        ).pack(side="right", padx=(0, 6))

        _select_step(0)

    # ── Personalização de Atalhos (1 Tecla ou Combinação de Teclas) ──────────

    def _refresh_hotkey_labels(self):
        hk_t = self.bot.cfg.hotkey_toggle.upper()
        hk_p = self.bot.cfg.hotkey_pause.upper()
        self._lbl_hotkeys_hint.config(text=self._t("hotkeys_hint", hk_start=hk_t, hk_pause=hk_p))
        if self._settings_win is not None:
            try:
                if self._settings_win.winfo_exists():
                    if "play" in self._settings_hk_btns:
                        self._settings_hk_btns["play"].config(text=self._t("btn_hk_play", hk=hk_t))
                    if "pause" in self._settings_hk_btns:
                        self._settings_hk_btns["pause"].config(text=self._t("btn_hk_pause", hk=hk_p))
            except Exception:
                pass
        self._apply_status(self.bot.status)

    def _reset_default_hotkeys(self):
        ok, msg = self.bot.update_hotkeys("f8", "f9")
        self._refresh_hotkey_labels()
        self._auto_save_settings()
        self._append_log(f"⌨ {msg}", "ok" if ok else "warn")

    def _open_hotkey_recorder(self, target: str):
        """
        Abre janela interativa para gravar 1 tecla (ex: F8, Insert, Home)
        OU uma combinação de teclas (ex: Ctrl+Shift+S, Alt+P, Q+E).
        """
        win = tk.Toplevel(self.root)
        win.title(self._t("hk_modal_title"))
        win.geometry("460x255")
        win.resizable(False, False)
        win.configure(bg=C["bg"])
        win.transient(self.root)
        win.grab_set()

        win.update_idletasks()
        sx = win.winfo_screenwidth()
        sy = win.winfo_screenheight()
        win.geometry(f"+{(sx - 460) // 2}+{(sy - 255) // 2}")

        hdr = tk.Frame(win, bg=C["dark"], pady=8, padx=14)
        hdr.pack(fill="x")
        target_name = "PLAY / STOP" if target == "toggle" else "PAUSE / RESUME"
        tk.Label(
            hdr,
            text=f"⌨  {self._t('hk_modal_title')} — {target_name}",
            fg=C["accent"],
            bg=C["dark"],
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Frame(win, bg=C["accent"], height=2).pack(fill="x")

        body = tk.Frame(win, bg=C["bg"], padx=14, pady=10)
        body.pack(fill="both", expand=True)

        tk.Label(
            body,
            text=self._t("hk_modal_instr"),
            fg=C["text"],
            bg=C["bg"],
            font=("Segoe UI", 8),
            justify="left",
            anchor="w",
            wraplength=425,
        ).pack(fill="x", pady=(0, 8))

        cur_hk = self.bot.cfg.hotkey_toggle if target == "toggle" else self.bot.cfg.hotkey_pause
        captured_combo = [cur_hk]
        currently_down = []

        disp_box = tk.Frame(body, bg=C["dark"], highlightbackground=C["accent"], highlightthickness=2, pady=12)
        disp_box.pack(fill="x", pady=(0, 10))

        lbl_combo = tk.Label(
            disp_box,
            text=f"[ {cur_hk.upper()} ]",
            fg=C["green"],
            bg=C["dark"],
            font=("Consolas", 14, "bold"),
        )
        lbl_combo.pack()

        _KEYSYM_MAP = {
            "control_l": "ctrl", "control_r": "ctrl",
            "shift_l": "shift", "shift_r": "shift",
            "alt_l": "alt", "alt_r": "alt",
            "iso_level3_shift": "alt",
            "prior": "page up", "next": "page down",
            "return": "enter", "back_space": "backspace",
            "caps_lock": "caps lock", "num_lock": "num lock",
            "scroll_lock": "scroll lock",
            "kp_0": "0", "kp_1": "1", "kp_2": "2", "kp_3": "3", "kp_4": "4",
            "kp_5": "5", "kp_6": "6", "kp_7": "7", "kp_8": "8", "kp_9": "9",
            "kp_add": "+", "kp_subtract": "-", "kp_multiply": "*", "kp_divide": "/",
            "plus": "+", "minus": "-", "asterisk": "*", "slash": "/",
        }
        _MOD_ORDER = {"ctrl": 0, "alt": 1, "shift": 2}

        def _norm_key(event) -> str:
            ks = (event.keysym or "").lower()
            if ks == "escape":
                return "esc"
            if ks in _KEYSYM_MAP:
                return _KEYSYM_MAP[ks]
            if len(ks) == 1 and ks.isalnum():
                return ks
            if ks.startswith("f") and ks[1:].isdigit():
                return ks
            if ks in ("insert", "delete", "home", "end", "space", "tab", "pause", "up", "down", "left", "right"):
                return ks
            return ks.replace("_", " ")

        def _format_combo(keys_list: list) -> str:
            mods = sorted([k for k in keys_list if k in _MOD_ORDER], key=lambda x: _MOD_ORDER[x])
            others = [k for k in keys_list if k not in _MOD_ORDER]
            return "+".join(mods + others)

        def _on_key_press(event):
            k = _norm_key(event)
            if not k:
                return
            if k == "esc" and not currently_down:
                win.destroy()
                return
            if k not in currently_down:
                currently_down.append(k)
            combo_str = _format_combo(currently_down)
            if combo_str:
                captured_combo[0] = combo_str
                lbl_combo.config(text=f"[ {combo_str.upper()} ]", fg=C["yellow"])

        def _on_key_release(event):
            k = _norm_key(event)
            if k in currently_down:
                currently_down.remove(k)
            if not currently_down and captured_combo[0]:
                lbl_combo.config(text=f"[ {captured_combo[0].upper()} ]", fg=C["green"])

        def _save_and_close():
            new_hk = captured_combo[0].strip().lower()
            if not new_hk:
                return
            new_t = new_hk if target == "toggle" else self.bot.cfg.hotkey_toggle
            new_p = new_hk if target == "pause" else self.bot.cfg.hotkey_pause
            ok, msg = self.bot.update_hotkeys(new_t, new_p)
            if not ok:
                messagebox.showwarning(self._t("hk_modal_title"), msg, parent=win)
                return
            self._refresh_hotkey_labels()
            self._auto_save_settings()
            self._append_log(f"⌨ {msg}", "ok")
            win.destroy()

        btn_row = tk.Frame(body, bg=C["bg"])
        btn_row.pack(fill="x")

        tk.Button(
            btn_row,
            text=self._t("hk_modal_save"),
            fg="white",
            bg=C["btn_start"],
            activebackground=C["green"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            pady=5,
            command=_save_and_close,
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))

        tk.Button(
            btn_row,
            text=self._t("hk_modal_cancel"),
            fg=C["text"],
            bg=C["test_btn"],
            activebackground=C["border"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            pady=5,
            command=win.destroy,
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

        win.bind("<KeyPress>", _on_key_press)
        win.bind("<KeyRelease>", _on_key_release)
        win.focus_force()

    # ── Troca Dinâmica de Idioma (12 Idiomas) ─────────────────────────────────

    def _on_language_change(self, event=None):
        selected_label = self._var_lang.get()
        new_code = self._lang_label_to_code.get(selected_label, "PT-BR")
        if new_code == self.lang:
            return
        self.lang = new_code
        self._apply_language()
        self._auto_save_settings()
        self._append_log(f"🌐 Idioma alterado para: {selected_label} ({new_code})", "hi")

    def _apply_language(self):
        self._lbl_subtitle.config(text=self._t("subtitle"))
        self._btn_guide.config(text=self._t("btn_guide"))
        self._btn_safety.config(text=self._t("btn_safety"))
        self._btn_shortcuts.config(text=self._t("btn_settings_app"))
        self._btn_tray.config(text=self._t("btn_tray"))
        self._lbl_mw_title.config(text=self._t("mw_title"))
        self._btn_refresh_wins.config(text=self._t("btn_refresh"))
        self._lbl_rebirth_sec_title.config(text=self._t("rebirth_sec_title"))
        self._btn_reb_off.config(text=self._t("reb_mode_off"))
        self._btn_reb_semi.config(text=self._t("reb_mode_semi"))
        self._btn_reb_full.config(text=self._t("reb_mode_full"))
        self._sync_rebirth_mode_ui()
        self._lbl_cfg_title.config(text=self._t("cfg_title"))
        self._lbl_screen_hz.config(text=self._t("lbl_screen_hz"))
        self._lbl_afk_interval.config(text=self._t("lbl_afk_interval"))
        self._chk_pause_pc.config(text=self._t("chk_pause_pc"))
        self._lbl_visual_title.config(text=self._t("lbl_visual_title"))
        self._chk_overlay_hud.config(text=self._t("chk_overlay_hud"))
        self._chk_mini_monitor.config(text=self._t("chk_mini_monitor"))
        self._lbl_startup_mode.config(text=self._t("lbl_startup_mode"))
        self._rad_start_default.config(text=self._t("rad_start_default"))
        self._rad_start_last.config(text=self._t("rad_start_last"))
        self._btn_save_cfg.config(text=self._t("btn_save_cfg"))
        self._btn_load_cfg.config(text=self._t("btn_load_cfg"))
        self._lbl_diag_title.config(text=self._t("diag_title"))
        self._btn_t_read.config(text=self._t("btn_t_read"))
        self._btn_t_egg.config(text=self._t("btn_t_egg"))
        self._btn_t_e.config(text=self._t("btn_t_e"))
        self._btn_t_rebirth.config(text=self._t("btn_t_rebirth"))
        self._btn_t_altar.config(text=self._t("btn_t_altar"))
        self._btn_calib_altar.config(text=self._t("btn_calib_altar"))
        self._btn_t_quest.config(text=self._t("btn_t_quest"))
        self._btn_t_afk.config(text=self._t("btn_t_afk"))
        self._btn_t_rec.config(text=self._t("btn_t_rec"))
        self._card_rebirths._title_label.config(text=self._t("card_rebirths"))
        self._card_quests._title_label.config(text=self._t("card_quests"))
        self._card_torches._title_label.config(text=self._t("card_afk"))
        self._card_uptime._title_label.config(text=self._t("card_uptime"))
        self._lbl_checklist_title.config(text=self._t("checklist_title"))
        self._btn_toggle_checklist.config(
            text=self._t("btn_hide_sec" if self._checklist_visible else "btn_show_sec")
        )
        self._lbl_log_title.config(text=self._t("log_title"))
        self._btn_toggle_log.config(
            text=self._t("btn_hide_sec" if self._log_visible else "btn_show_sec")
        )
        self._btn_clear_log.config(text=self._t("btn_clear"))
        self._btn_copy_log.config(text=self._t("btn_copy_log"))
        self._btn_save_log.config(text=self._t("btn_save_log"))

        self._refresh_hotkey_labels()
        self._refresh_guide_window_language()
        self._refresh_safety_window_language()
        self._refresh_window_list_ui(force=True)
        self._apply_status(self.bot.status)

    # ── Persistência de Configuração (Padrão vs Última Config + Exportar/Importar) ──

    def _build_config_dict(self) -> dict:
        cal = self.bot.vision.custom_altar_calibration
        return {
            "version": "4.2.3",
            "startup_mode": self._var_startup_mode.get(),
            "lang": self.lang,
            "hotkey_toggle": self.bot.cfg.hotkey_toggle,
            "hotkey_pause": self.bot.cfg.hotkey_pause,
            "screen_check_hz": self._var_screen_hz.get(),
            "anti_afk_interval_sec": self._var_afk_interval.get(),
            "auto_pause_on_mouse": bool(self._var_auto_pause_mouse.get()),
            "mouse_idle_resume_sec": self._var_mouse_idle_sec.get(),
            "overlay_hud": bool(self._var_overlay_hud.get()),
            "mini_monitor": bool(self._var_mini_monitor.get()),
            "show_checklist": bool(self._checklist_visible),
            "show_log": bool(self._log_visible),
            "notifications": {
                "afk_warn": bool(self.bot.cfg.notif_afk_warn),
                "reconnect": bool(self.bot.cfg.notif_reconnect),
                "rebirth": bool(self.bot.cfg.notif_rebirth),
                "tray_minimize": bool(self.bot.cfg.notif_tray_minimize),
            },
            "altar_calibration": {
                "enabled": bool(cal.get("enabled", False)),
                "mode": str(cal.get("mode", "table_offset")),
                "offsets": {k: list(v) for k, v in cal.get("offsets", {}).items()},
                "ratios": {k: list(v) for k, v in cal.get("ratios", {}).items()},
                "walk_wait_sec": float(cal.get("walk_wait_sec", 3.8)),
            },
            "window_profiles": self.bot.export_index_profiles(),
        }

    def _apply_config_dict(self, data: dict, apply_windows: bool = True):
        if not isinstance(data, dict):
            return
        self._loading_settings = True
        try:
            if "startup_mode" in data and data["startup_mode"] in ("default", "last"):
                self._var_startup_mode.set(data["startup_mode"])

            if "lang" in data and data["lang"] in self._lang_code_to_label:
                self.lang = data["lang"]
                self._var_lang.set(self._lang_code_to_label[self.lang])

            hk_t = str(data.get("hotkey_toggle", self.bot.cfg.hotkey_toggle)).strip().lower()
            hk_p = str(data.get("hotkey_pause", self.bot.cfg.hotkey_pause)).strip().lower()
            if hk_t and hk_p and hk_t != hk_p:
                self.bot.update_hotkeys(hk_t, hk_p)

            if "screen_check_hz" in data:
                self._var_screen_hz.set(str(data["screen_check_hz"]))
            if "anti_afk_interval_sec" in data:
                self._var_afk_interval.set(str(data["anti_afk_interval_sec"]))
            if "auto_pause_on_mouse" in data:
                self._var_auto_pause_mouse.set(bool(data["auto_pause_on_mouse"]))
            if "mouse_idle_resume_sec" in data:
                self._var_mouse_idle_sec.set(str(data["mouse_idle_resume_sec"]))

            if "overlay_hud" in data:
                self._var_overlay_hud.set(bool(data["overlay_hud"]))
                self.visual_mgr.set_overlay_enabled(bool(data["overlay_hud"]))
            if "mini_monitor" in data:
                self._var_mini_monitor.set(bool(data["mini_monitor"]))
                self.visual_mgr.set_monitor_enabled(bool(data["mini_monitor"]))

            if "show_checklist" in data:
                self._set_checklist_visibility(bool(data["show_checklist"]), fit_win=False)
            if "show_log" in data:
                self._set_log_visibility(bool(data["show_log"]), fit_win=False)

            if isinstance(data.get("notifications"), dict):
                nd = data["notifications"]
                self.bot.cfg.notif_afk_warn = bool(nd.get("afk_warn", True))
                self.bot.cfg.notif_reconnect = bool(nd.get("reconnect", True))
                self.bot.cfg.notif_rebirth = bool(nd.get("rebirth", False))
                self.bot.cfg.notif_tray_minimize = bool(nd.get("tray_minimize", False))

            if isinstance(data.get("altar_calibration"), dict):
                self.bot.vision.set_custom_altar_calibration(data["altar_calibration"])

            if apply_windows and isinstance(data.get("window_profiles"), dict):
                self.bot.apply_saved_index_profiles(data["window_profiles"])
        finally:
            self._loading_settings = False

        self._update_config()
        self._apply_language()
        self._refresh_window_list_ui(force=True)
        self._fit_window_to_content()

    def _load_startup_preferences(self):
        """
        Ao iniciar o aplicativo:
        - Se 'startup_mode' salvo em bot_settings.json for 'last', restaura a última configuração completa.
        - Se for 'default' (padrão), abre todas as janelas apenas com o Básico (Missão [C] + Anti-AFK [C+Q] + Reconectar),
          mas preserva seus atalhos personalizados (Play/Stop e Pausa), notificações, visibilidade de seções, calibração do Altar e idioma!
        """
        if not os.path.exists(SETTINGS_FILE):
            self.bot.reset_all_windows_to_basic()
            return
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            mode = data.get("startup_mode", "default")
            self._var_startup_mode.set(mode if mode in ("default", "last") else "default")
            if mode == "last":
                self._apply_config_dict(data, apply_windows=True)
                self._append_log("🔄 Última configuração restaurada automaticamente!", "hi")
            else:
                hk_t = str(data.get("hotkey_toggle", "f8")).strip().lower()
                hk_p = str(data.get("hotkey_pause", "f9")).strip().lower()
                if hk_t and hk_p and hk_t != hk_p:
                    self.bot.update_hotkeys(hk_t, hk_p)
                if "lang" in data and data["lang"] in self._lang_code_to_label:
                    self.lang = data["lang"]
                    self._var_lang.set(self._lang_code_to_label[self.lang])
                if "show_checklist" in data:
                    self._set_checklist_visibility(bool(data["show_checklist"]), fit_win=False)
                if "show_log" in data:
                    self._set_log_visibility(bool(data["show_log"]), fit_win=False)
                if isinstance(data.get("notifications"), dict):
                    nd = data["notifications"]
                    self.bot.cfg.notif_afk_warn = bool(nd.get("afk_warn", True))
                    self.bot.cfg.notif_reconnect = bool(nd.get("reconnect", True))
                    self.bot.cfg.notif_rebirth = bool(nd.get("rebirth", False))
                    self.bot.cfg.notif_tray_minimize = bool(nd.get("tray_minimize", False))
                if isinstance(data.get("altar_calibration"), dict):
                    self.bot.vision.set_custom_altar_calibration(data["altar_calibration"])
                self.bot.reset_all_windows_to_basic()
                self._apply_language()
                self._fit_window_to_content()
        except Exception as e:
            self._append_log(f"⚠️ Aviso ao ler bot_settings.json: {e}", "warn")

    def _auto_save_settings(self):
        if self._loading_settings:
            return
        try:
            data = self._build_config_dict()
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _on_startup_mode_change(self):
        mode = self._var_startup_mode.get()
        if mode == "default":
            self.bot.reset_all_windows_to_basic()
            self._refresh_window_list_ui(force=True)
            self._auto_save_settings()
            self._append_log(
                "📌 Modo 'Sempre Abrir Padrão' ativado: Todas as janelas redefinidas para o Básico (Missão + Anti-AFK + Reconectar).",
                "hi",
            )
        else:
            self._auto_save_settings()
            self._append_log(
                "🔄 Modo 'Sempre Abrir na Última Config' ativado: Suas escolhas serão lembradas automaticamente ao abrir o App!",
                "ok",
            )

    def _export_config_dialog(self):
        self._auto_save_settings()
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Salvar / Exportar Configuração do Bot",
            initialdir=os.path.dirname(SETTINGS_FILE),
            initialfile="cap_bot_config.json",
            defaultextension=".json",
            filetypes=[("Arquivo de Configuração JSON", "*.json"), ("Todos os arquivos", "*.*")],
        )
        if not path:
            return
        try:
            data = self._build_config_dict()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self._append_log(f"💾 Configuração salva/exportada com sucesso em: {path}", "ok")
        except Exception as e:
            messagebox.showerror("Erro ao Exportar", f"Não foi possível salvar o arquivo:\n{e}")

    def _import_config_dialog(self):
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Carregar / Importar Configuração do Bot",
            initialdir=os.path.dirname(SETTINGS_FILE),
            filetypes=[("Arquivo de Configuração JSON", "*.json"), ("Todos os arquivos", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._apply_config_dict(data, apply_windows=True)
            self._auto_save_settings()
            self._append_log(f"📂 Configuração carregada/importada com sucesso de: {path}", "ok")
        except Exception as e:
            messagebox.showerror("Erro ao Importar", f"Não foi possível carregar o arquivo:\n{e}")

    # ── Indicadores Visuais (Overlay HUD Click-Through & Mini-Monitor Ao Vivo) ──

    def _on_toggle_visual_indicators(self):
        hud_on = self._var_overlay_hud.get()
        mon_on = self._var_mini_monitor.get()
        self.visual_mgr.set_overlay_enabled(hud_on)
        self.visual_mgr.set_monitor_enabled(mon_on)
        if hud_on or mon_on:
            self.visual_mgr.update(lang=self.lang)
        self._auto_save_settings()
        modes = []
        if hud_on:
            modes.append("🎯 Overlay HUD")
        if mon_on:
            modes.append("📺 Mini-Monitor")
        state_str = " + ".join(modes) if modes else "Desligados"
        self._append_log(f"👁️ Indicadores Visuais: {state_str}", "hi")

    def _on_mini_monitor_closed_by_x(self):
        self._var_mini_monitor.set(False)
        self._auto_save_settings()

    def _tick_visual_indicators(self):
        try:
            if self.visual_mgr.any_enabled:
                self.visual_mgr.update(lang=self.lang)
        except Exception:
            pass
        self.root.after(180, self._tick_visual_indicators)

    # ── Espaço de Rebirth (Modo Leve / Semi-Auto / Full-Auto) ────────────────

    def _set_rebirth_mode(self, mode: str):
        """
        Ativa rapidamente o modo de Rebirth desejado na Janela Principal (e fallback global):
        - 'off'  -> Desativado (Modo Básico Leve): E=Off, Ovos=Off, Altar=Off
        - 'semi' -> Semi-Auto: E=On + Ovos=On, Altar=Off (movimentação manual, sem pausa ao jogar)
        - 'full' -> Full-Auto: E=On + Ovos=On + Altar=On (4 Tochas + Mesa Branca automáticas)
        """
        m = (mode or "off").strip().lower()
        self._rebirth_mode = m

        if m == "full":
            e_on, z_on, a_on = True, True, True
        elif m == "semi":
            e_on, z_on, a_on = True, True, False
        else:
            e_on, z_on, a_on = False, False, False

        self.bot.cfg.auto_interact_e = e_on
        self.bot.cfg.auto_eggs = z_on
        self.bot.cfg.auto_move_altar = a_on

        pri = self.bot.get_primary_profile()
        if pri is not None:
            pri.set_rebirth_mode(m)
            if pri.hwnd in self._win_row_vars:
                v = self._win_row_vars[pri.hwnd]
                v["e"].set(e_on)
                v["eggs"].set(z_on)
                v["altar"].set(a_on)

        self._last_hwnd_tuple = tuple(
            (p.hwnd, p.is_primary, p.rect, p.auto_interact_e, p.auto_eggs, p.auto_move_altar)
            for p in self.bot.window_profiles.values()
        )
        self._sync_rebirth_mode_ui()
        self._auto_save_settings()

        if m == "semi":
            self._append_log(
                "⚡🥚 [Rebirth Semi-Auto Ativado] Interações [E] + Troca de Ovos [Z] ligados! "
                "Movimentação manual (NÃO pausa quando você estiver jogando no Roblox).",
                "hi",
            )
        elif m == "full":
            self._append_log(
                "🏛️🔥 [Rebirth Full-Auto Ativado] Interações [E] + Ovos [Z] + Altar Automático (4 Tochas + Mesa Branca) ligados!",
                "ok",
            )
        else:
            self._append_log(
                "🛡️ [Rebirth Desativado — Modo Leve] Apenas Missão [C], Anti-AFK [C+Q] e Reconectar ativos.",
                "info",
            )

    def _sync_rebirth_mode_ui(self):
        """Sincroniza o destaque dos botões e a explicação do Espaço de Rebirth com a Janela Principal."""
        pri = self.bot.get_primary_profile()
        if pri is not None:
            m = pri.rebirth_mode
        else:
            if self.bot.cfg.auto_move_altar:
                m = "full"
            elif self.bot.cfg.auto_interact_e and self.bot.cfg.auto_eggs:
                m = "semi"
            else:
                m = "off"

        self._rebirth_mode = m

        # Atualiza visual dos 3 botões de modo
        self._btn_reb_off.config(
            bg=C["accent"] if m == "off" else C["test_btn"],
            fg="#090a10" if m == "off" else C["dim"],
        )
        self._btn_reb_semi.config(
            bg=C["yellow"] if m == "semi" else C["test_btn"],
            fg="#090a10" if m == "semi" else C["yellow"],
        )
        self._btn_reb_full.config(
            bg=C["btn_start"] if m == "full" else C["test_btn"],
            fg="white" if m == "full" else C["green"],
        )

    # ── Renderização Dinâmica da Lista de Janelas (1..N) ──────────────────────

    def _refresh_window_list_ui(self, force: bool = False):
        profiles = self.bot.sync_windows()
        cur_tuple = tuple(
            (p.hwnd, p.is_primary, p.rect, p.auto_interact_e, p.auto_eggs, p.auto_move_altar)
            for p in profiles
        )
        if not force and cur_tuple == self._last_hwnd_tuple:
            return
        self._last_hwnd_tuple = cur_tuple

        for child in self._mw_list_frame.winfo_children():
            child.destroy()
        self._win_row_vars.clear()

        if not profiles:
            tk.Label(
                self._mw_list_frame,
                text=self._t("no_windows_msg"),
                fg=C["dim"],
                bg=C["dark"],
                font=("Segoe UI", 8),
                pady=12,
            ).pack(fill="x")
            return

        for prof in profiles:
            self._build_window_row(prof)
        self._sync_rebirth_mode_ui()

    def _build_window_row(self, prof: WindowProfile):
        is_pri = prof.is_primary
        row_bg = "#1b2236" if is_pri else C["card"]
        border_col = C["accent"] if is_pri else C["border"]

        card = tk.Frame(
            self._mw_list_frame,
            bg=row_bg,
            highlightbackground=border_col,
            highlightthickness=1,
            padx=6,
            pady=3,
        )
        card.pack(fill="x", pady=2, padx=2)

        top_line = tk.Frame(card, bg=row_bg)
        top_line.pack(fill="x")

        role_txt = self._t("role_primary") if is_pri else self._t("role_secondary")
        role_col = C["yellow"] if is_pri else C["accent"]

        lbl_role = tk.Label(
            top_line,
            text=f"{role_txt} — {self._t('window_lbl')} {prof.pos_tag}",
            fg=role_col,
            bg=row_bg,
            font=("Segoe UI", 8, "bold"),
        )
        lbl_role.pack(side="left")
        self._bind_tooltip(lbl_role, "tip_win_role")

        btn_front = tk.Button(
            top_line,
            text=self._t("btn_bring_front"),
            fg="white",
            bg=C["test_btn"],
            relief="flat",
            cursor="hand2",
            padx=6,
            pady=0,
            font=("Segoe UI", 7, "bold"),
            command=lambda h=prof.hwnd: self._focus_specific_window(h),
        )
        btn_front.pack(side="right", padx=(4, 0))
        self._bind_tooltip(btn_front, "tip_btn_bring_front")

        if not is_pri:
            btn_pri = tk.Button(
                top_line,
                text=self._t("btn_make_primary"),
                fg=C["yellow"],
                bg=C["test_btn"],
                relief="flat",
                cursor="hand2",
                padx=6,
                pady=0,
                font=("Segoe UI", 7),
                command=lambda h=prof.hwnd: self._make_window_primary(h),
            )
            btn_pri.pack(side="right")
            self._bind_tooltip(btn_pri, "tip_btn_make_primary")

        opt_line = tk.Frame(card, bg=row_bg)
        opt_line.pack(fill="x", pady=(2, 0))

        v_e     = tk.BooleanVar(value=prof.auto_interact_e)
        v_eggs  = tk.BooleanVar(value=prof.auto_eggs)
        v_altar = tk.BooleanVar(value=prof.auto_move_altar)
        v_quest = tk.BooleanVar(value=prof.auto_claim_quest)
        v_afk   = tk.BooleanVar(value=prof.auto_anti_afk)
        v_rec   = tk.BooleanVar(value=prof.auto_reconnect)

        self._win_row_vars[prof.hwnd] = {
            "e": v_e,
            "eggs": v_eggs,
            "altar": v_altar,
            "quest": v_quest,
            "afk": v_afk,
            "rec": v_rec,
        }

        def _make_chk(parent, text, var, tip_key, fg_col=C["text"]):
            cb = tk.Checkbutton(
                parent,
                text=text,
                variable=var,
                bg=row_bg,
                fg=fg_col,
                selectcolor=C["dark"],
                activebackground=row_bg,
                font=("Segoe UI", 7, "bold"),
                command=lambda h=prof.hwnd: self._on_window_profile_toggle(h),
            )
            cb.pack(side="left", padx=(0, 5))
            self._bind_tooltip(cb, tip_key)

        _make_chk(opt_line, self._t("opt_e"), v_e, "tip_opt_e")
        _make_chk(opt_line, self._t("opt_eggs"), v_eggs, "tip_opt_eggs")
        _make_chk(opt_line, self._t("opt_altar"), v_altar, "tip_opt_altar")
        _make_chk(opt_line, self._t("opt_quest"), v_quest, "tip_opt_quest", C["green"])
        _make_chk(opt_line, self._t("opt_afk"), v_afk, "tip_opt_afk", C["yellow"])
        _make_chk(opt_line, self._t("opt_rec"), v_rec, "tip_opt_rec", C["accent"])

    def _on_window_profile_toggle(self, hwnd: int):
        if hwnd not in self.bot.window_profiles or hwnd not in self._win_row_vars:
            return
        prof = self.bot.window_profiles[hwnd]
        v = self._win_row_vars[hwnd]
        prof.auto_interact_e  = v["e"].get()
        prof.auto_eggs        = v["eggs"].get()
        prof.auto_move_altar  = v["altar"].get()
        prof.auto_claim_quest = v["quest"].get()
        prof.auto_anti_afk    = v["afk"].get()
        prof.auto_reconnect   = v["rec"].get()
        self._last_hwnd_tuple = tuple(
            (p.hwnd, p.is_primary, p.rect, p.auto_interact_e, p.auto_eggs, p.auto_move_altar)
            for p in self.bot.window_profiles.values()
        )
        if prof.is_primary:
            self.bot.cfg.auto_interact_e = prof.auto_interact_e
            self.bot.cfg.auto_eggs = prof.auto_eggs
            self.bot.cfg.auto_move_altar = prof.auto_move_altar
        self._sync_rebirth_mode_ui()
        self._auto_save_settings()

    def _make_window_primary(self, hwnd: int):
        self.bot.set_primary_window(hwnd)
        self._refresh_window_list_ui(force=True)
        self._auto_save_settings()
        self._append_log(f"⭐ Janela ID:{hwnd} definida como PRINCIPAL!", "hi")

    def _focus_specific_window(self, hwnd: int):
        self.bot.controller.force_foreground(hwnd, settle_delay=0.20)
        self._append_log(f"👁️ Janela ID:{hwnd} trazida para o 1º Plano!", "info")

    def _make_card(self, parent, title: str, val: str, color: str):
        f = tk.Frame(parent, bg=C["card"], highlightbackground=C["border"], highlightthickness=1)
        t_lbl = tk.Label(f, text=title, fg=C["dim"], bg=C["card"], font=("Segoe UI", 7, "bold"))
        t_lbl.pack(anchor="w", padx=6, pady=(3, 0))
        lbl = tk.Label(f, text=val, fg=color, bg=C["card"], font=("Consolas", 12, "bold"))
        lbl.pack(anchor="w", padx=6, pady=(0, 3))
        f._title_label = t_lbl
        f._val_label = lbl
        return f

    def _update_config(self):
        try:
            val_hz = float(self._var_screen_hz.get().strip().replace(",", "."))
            self.bot.cfg.screen_check_hz = max(0.05, min(10.0, val_hz))
        except Exception:
            pass

        try:
            val_afk = float(self._var_afk_interval.get().strip())
            self.bot.cfg.anti_afk_interval_sec = max(5.0, min(3600.0, val_afk))
        except Exception:
            pass

        self.bot.cfg.auto_pause_on_mouse = self._var_auto_pause_mouse.get()
        try:
            val_idle = float(self._var_mouse_idle_sec.get().strip())
            self.bot.cfg.mouse_idle_resume_sec = max(5.0, min(3600.0, val_idle))
        except Exception:
            pass

        self._auto_save_settings()

    def _setup_log_handler(self):
        gui = self

        class Handler(logging.Handler):
            def emit(self_, record):
                msg = self_.format(record)
                tag = "info"
                if any(k in msg for k in ["✅", "🎉", "🏆", "🛡️", "▶️", "🔌", "sucesso", "completo", "concluído", "resgatada"]):
                    tag = "ok"
                elif any(k in msg for k in ["⚠️", "🖱️", "⌨️", "PAUSADO", "Auto-Pausa", "Aguardando"]):
                    tag = "warn"
                elif any(k in msg for k in ["❌", "ERRO", "fechado"]):
                    tag = "err"
                elif any(k in msg for k in ["🟢", "🔥", "✨", "🥚", "📋", "🔄", "⭐", "🌐", "👁️"]):
                    tag = "hi"
                gui._append_log(msg, tag)

        h = Handler()
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(h)

    # ── Menu Didático Visual: Como Usar o CAP Assistant ─────────────────────

    def _render_visual_section_cards(self, parent_frame: tk.Frame, sections_spec: list):
        """Renderiza cartões visuais modernos com badges coloridos à esquerda e descrição à direita."""
        for child in parent_frame.winfo_children():
            child.destroy()

        for title_key, items_key, accent_col in sections_spec:
            card = tk.Frame(
                parent_frame,
                bg=C["card"],
                highlightbackground=accent_col,
                highlightthickness=1,
                padx=10,
                pady=8,
            )
            card.pack(fill="x", pady=(0, 10), padx=2)

            hdr_bar = tk.Frame(card, bg="#101524", padx=10, pady=5)
            hdr_bar.pack(fill="x", pady=(0, 6))
            tk.Frame(hdr_bar, bg=accent_col, width=4, height=18).pack(side="left", padx=(0, 8))
            tk.Label(
                hdr_bar,
                text=self._t(title_key),
                fg=accent_col,
                bg="#101524",
                font=("Segoe UI", 9, "bold"),
                anchor="w",
                justify="left",
            ).pack(side="left", fill="x", expand=True)

            rows_data = tr_items(self.lang, items_key)
            for badge_txt, desc_txt, is_hi in rows_data:
                row_bg = "#0c101c" if is_hi else C["card"]
                row = tk.Frame(
                    card,
                    bg=row_bg,
                    highlightbackground=C["border"] if is_hi else row_bg,
                    highlightthickness=1 if is_hi else 0,
                    padx=8,
                    pady=5,
                )
                row.pack(fill="x", padx=2, pady=2)

                badge_col = C["green"] if is_hi else C["yellow"]
                lbl_badge = tk.Label(
                    row,
                    text=badge_txt,
                    fg=badge_col,
                    bg="#161d31" if is_hi else "#121624",
                    font=("Consolas", 9, "bold"),
                    width=17,
                    anchor="center",
                    padx=4,
                    pady=2,
                )
                lbl_badge.pack(side="left", anchor="n", padx=(0, 10), pady=1)

                lbl_desc = tk.Label(
                    row,
                    text=desc_txt,
                    fg=C["text"],
                    bg=row_bg,
                    font=("Segoe UI", 9, "bold" if is_hi else "normal"),
                    anchor="w",
                    justify="left",
                    wraplength=490,
                )
                lbl_desc.pack(side="left", fill="x", expand=True)

    def _show_user_guide(self):
        if self._guide_win is not None:
            try:
                if self._guide_win.winfo_exists():
                    self._guide_win.lift()
                    self._guide_win.focus_force()
                    return
            except Exception:
                pass

        win = tk.Toplevel(self.root)
        self._guide_win = win
        self._guide_widgets.clear()

        win.title(self._t("guide_win_title"))
        win.geometry("730x680")
        win.minsize(640, 500)
        win.resizable(True, True)
        win.configure(bg=C["bg"])
        win.transient(self.root)

        win.update_idletasks()
        sx = win.winfo_screenwidth()
        sy = win.winfo_screenheight()
        win.geometry(f"+{max(0, (sx - 730) // 2)}+{max(0, (sy - 680) // 2)}")

        hdr = tk.Frame(win, bg=C["dark"], pady=10, padx=14)
        hdr.pack(fill="x")

        lbl_hdr = tk.Label(
            hdr,
            text=self._t("guide_hdr_title"),
            fg=C["accent"],
            bg=C["dark"],
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        )
        lbl_hdr.pack(fill="x")
        self._guide_widgets["hdr_title"] = lbl_hdr

        lbl_sub = tk.Label(
            hdr,
            text=self._t("guide_hdr_sub"),
            fg=C["green"],
            bg=C["dark"],
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        )
        lbl_sub.pack(fill="x", pady=(3, 0))
        self._guide_widgets["hdr_sub"] = lbl_sub

        tk.Frame(win, bg=C["accent"], height=2).pack(fill="x")

        ftr = tk.Frame(win, bg=C["dark"], padx=14, pady=8)
        ftr.pack(fill="x", side="bottom")

        btn_close = tk.Button(
            ftr,
            text=self._t("guide_btn_close"),
            fg="white",
            bg=C["btn_start"],
            activebackground=C["green"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            pady=6,
            command=win.destroy,
        )
        btn_close.pack(fill="x")
        self._guide_widgets["btn_close"] = btn_close

        wrap = tk.Frame(win, bg=C["bg"], padx=10, pady=8)
        wrap.pack(fill="both", expand=True)

        canvas = tk.Canvas(wrap, bg=C["bg"], highlightthickness=0, bd=0)
        vbar = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        content = tk.Frame(canvas, bg=C["bg"])
        self._guide_widgets["content"] = content

        content.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        c_win = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfig(c_win, width=e.width),
        )
        canvas.configure(yscrollcommand=vbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            try:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass

        win.bind("<MouseWheel>", _on_mousewheel)
        self._populate_guide_cards()

    def _populate_guide_cards(self):
        content = self._guide_widgets.get("content")
        if not content or not content.winfo_exists():
            return
        sections_spec = [
            ("guide_sec1_title", "guide_sec1_items", C["green"]),
            ("guide_sec2_title", "guide_sec2_items", C["accent"]),
            ("guide_sec3_title", "guide_sec3_items", C["yellow"]),
            ("guide_sec4_title", "guide_sec4_items", C["accent"]),
        ]
        self._render_visual_section_cards(content, sections_spec)

    def _refresh_guide_window_language(self):
        """Se a janela 'Como Usar' estiver aberta ao trocar de idioma, reconstrói os cartões ao vivo."""
        if self._guide_win is None:
            return
        try:
            if not self._guide_win.winfo_exists():
                return
            self._guide_win.title(self._t("guide_win_title"))
            if "hdr_title" in self._guide_widgets:
                self._guide_widgets["hdr_title"].config(text=self._t("guide_hdr_title"))
            if "hdr_sub" in self._guide_widgets:
                self._guide_widgets["hdr_sub"].config(text=self._t("guide_hdr_sub"))
            if "btn_close" in self._guide_widgets:
                self._guide_widgets["btn_close"].config(text=self._t("guide_btn_close"))
            self._populate_guide_cards()
        except Exception:
            pass

    # ── Menu Didático de Segurança e Funcionamento do CAP Assistant ─────────

    def _show_safety_info(self):
        if self._safety_win is not None:
            try:
                if self._safety_win.winfo_exists():
                    self._safety_win.lift()
                    self._safety_win.focus_force()
                    return
            except Exception:
                pass

        win = tk.Toplevel(self.root)
        self._safety_win = win
        self._safety_widgets = {}

        win.title(self._t("safety_win_title"))
        win.geometry("700x610")
        win.minsize(620, 480)
        win.resizable(True, True)
        win.configure(bg=C["bg"])
        win.transient(self.root)

        win.update_idletasks()
        sx = win.winfo_screenwidth()
        sy = win.winfo_screenheight()
        win.geometry(f"+{max(0, (sx - 700) // 2)}+{max(0, (sy - 610) // 2)}")

        hdr = tk.Frame(win, bg=C["dark"], pady=10, padx=14)
        hdr.pack(fill="x")

        lbl_hdr = tk.Label(
            hdr,
            text=self._t("safety_hdr_title"),
            fg=C["green"],
            bg=C["dark"],
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        )
        lbl_hdr.pack(fill="x")
        self._safety_widgets["hdr_title"] = lbl_hdr

        lbl_sub = tk.Label(
            hdr,
            text=self._t("safety_hdr_sub"),
            fg=C["accent"],
            bg=C["dark"],
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        )
        lbl_sub.pack(fill="x", pady=(3, 0))
        self._safety_widgets["hdr_sub"] = lbl_sub

        tk.Frame(win, bg=C["green"], height=2).pack(fill="x")

        ftr = tk.Frame(win, bg=C["dark"], padx=14, pady=8)
        ftr.pack(fill="x", side="bottom")

        btn_close = tk.Button(
            ftr,
            text=self._t("safety_btn_close"),
            fg="white",
            bg=C["btn_start"],
            activebackground=C["green"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            pady=6,
            command=win.destroy,
        )
        btn_close.pack(fill="x")
        self._safety_widgets["btn_close"] = btn_close

        wrap = tk.Frame(win, bg=C["bg"], padx=10, pady=8)
        wrap.pack(fill="both", expand=True)

        canvas = tk.Canvas(wrap, bg=C["bg"], highlightthickness=0, bd=0)
        vbar = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        content = tk.Frame(canvas, bg=C["bg"])
        self._safety_widgets["content"] = content

        content.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        c_win = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfig(c_win, width=e.width),
        )
        canvas.configure(yscrollcommand=vbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            try:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            except Exception:
                pass

        win.bind("<MouseWheel>", _on_mousewheel)
        self._populate_safety_cards()

    def _populate_safety_cards(self):
        if not hasattr(self, "_safety_widgets"):
            return
        content = self._safety_widgets.get("content")
        if not content or not content.winfo_exists():
            return
        sections_spec = [
            ("safety_sec1_title", "safety_sec1_items", C["green"]),
            ("safety_sec2_title", "safety_sec2_items", C["accent"]),
            ("safety_sec3_title", "safety_sec3_items", C["yellow"]),
        ]
        self._render_visual_section_cards(content, sections_spec)

    def _refresh_safety_window_language(self):
        if self._safety_win is None or not hasattr(self, "_safety_widgets"):
            return
        try:
            if not self._safety_win.winfo_exists():
                return
            self._safety_win.title(self._t("safety_win_title"))
            if "hdr_title" in self._safety_widgets:
                self._safety_widgets["hdr_title"].config(text=self._t("safety_hdr_title"))
            if "hdr_sub" in self._safety_widgets:
                self._safety_widgets["hdr_sub"].config(text=self._t("safety_hdr_sub"))
            if "btn_close" in self._safety_widgets:
                self._safety_widgets["btn_close"].config(text=self._t("safety_btn_close"))
            self._populate_safety_cards()
        except Exception:
            pass

    # ── Menu de Configurações do App (Atalhos Globais + Notificações em Balão + Hotkeys do Jogo) ──

    def _open_app_settings_modal(self):
        if self._settings_win is not None:
            try:
                if self._settings_win.winfo_exists():
                    self._settings_win.lift()
                    self._settings_win.focus_force()
                    return
            except Exception:
                pass

        win = tk.Toplevel(self.root)
        self._settings_win = win
        self._settings_hk_btns = {}
        win.title(self._t("settings_win_title"))
        win.configure(bg=C["bg"])
        win.transient(self.root)

        win.update_idletasks()
        sx = win.winfo_screenwidth()
        sy = win.winfo_screenheight()
        win_w = min(640, max(540, sx - 60))
        win_h = min(660, max(500, sy - 70))
        win.geometry(f"{win_w}x{win_h}+{max(0, (sx - win_w) // 2)}+{max(0, (sy - win_h) // 2)}")

        hdr = tk.Frame(win, bg=C["dark"], pady=9, padx=14)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text=self._t("settings_hdr_title"),
            fg=C["accent"],
            bg=C["dark"],
            font=("Segoe UI", 11, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            hdr,
            text=self._t("settings_hdr_sub"),
            fg=C["text"],
            bg=C["dark"],
            font=("Segoe UI", 8),
            anchor="w",
        ).pack(fill="x", pady=(2, 0))
        tk.Frame(win, bg=C["accent"], height=2).pack(fill="x")

        ftr = tk.Frame(win, bg=C["dark"], padx=14, pady=8)
        ftr.pack(fill="x", side="bottom")
        tk.Button(
            ftr,
            text=self._t("settings_btn_close"),
            fg="white",
            bg=C["btn_start"],
            activebackground=C["green"],
            relief="flat",
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            pady=6,
            command=win.destroy,
        ).pack(fill="x")

        wrap = tk.Frame(win, bg=C["bg"], padx=10, pady=8)
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg=C["bg"], highlightthickness=0, bd=0)
        vbar = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas, bg=C["bg"])

        body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        c_win = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(c_win, width=e.width))
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        # ── Seção 1: Atalhos Globais do Assistente (1 tecla ou combinação) ────
        sec_hk = tk.Frame(body, bg=C["card"], highlightbackground=C["accent"], highlightthickness=1, padx=10, pady=8)
        sec_hk.pack(fill="x", pady=(0, 8))
        tk.Label(
            sec_hk,
            text=self._t("settings_sec_hotkeys"),
            fg=C["accent"],
            bg=C["card"],
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            sec_hk,
            text=self._t("settings_sec_hotkeys_sub"),
            fg=C["dim"],
            bg=C["card"],
            font=("Segoe UI", 8),
            anchor="w",
            justify="left",
            wraplength=570,
        ).pack(fill="x", pady=(2, 6))

        hk_row = tk.Frame(sec_hk, bg=C["dark"], padx=8, pady=6)
        hk_row.pack(fill="x")

        hk_t_up = self.bot.cfg.hotkey_toggle.upper()
        hk_p_up = self.bot.cfg.hotkey_pause.upper()

        btn_s_play = tk.Button(
            hk_row,
            text=self._t("btn_hk_play", hk=hk_t_up),
            fg=C["green"],
            bg=C["test_btn"],
            activebackground=C["border"],
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=12,
            pady=4,
            font=("Consolas", 9, "bold"),
            command=lambda: self._open_hotkey_recorder("toggle"),
        )
        btn_s_play.pack(side="left", padx=(0, 8))
        self._settings_hk_btns["play"] = btn_s_play

        btn_s_pause = tk.Button(
            hk_row,
            text=self._t("btn_hk_pause", hk=hk_p_up),
            fg=C["yellow"],
            bg=C["test_btn"],
            activebackground=C["border"],
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=12,
            pady=4,
            font=("Consolas", 9, "bold"),
            command=lambda: self._open_hotkey_recorder("pause"),
        )
        btn_s_pause.pack(side="left", padx=(0, 8))
        self._settings_hk_btns["pause"] = btn_s_pause

        tk.Button(
            hk_row,
            text=self._t("btn_hk_reset"),
            fg=C["text"],
            bg=C["card"],
            activebackground=C["border"],
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=10,
            pady=4,
            font=("Segoe UI", 8, "bold"),
            command=self._reset_default_hotkeys,
        ).pack(side="right")

        # ── Seção 2: Notificações em Balão na Bandeja (System Tray) ───────────
        sec_nf = tk.Frame(body, bg=C["card"], highlightbackground=C["green"], highlightthickness=1, padx=10, pady=8)
        sec_nf.pack(fill="x", pady=(0, 8))
        tk.Label(
            sec_nf,
            text=self._t("settings_sec_notifs"),
            fg=C["green"],
            bg=C["card"],
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            sec_nf,
            text=self._t("settings_sec_notifs_sub"),
            fg=C["dim"],
            bg=C["card"],
            font=("Segoe UI", 8),
            anchor="w",
            justify="left",
            wraplength=570,
        ).pack(fill="x", pady=(2, 6))

        var_nf_afk = tk.BooleanVar(value=bool(self.bot.cfg.notif_afk_warn))
        var_nf_rec = tk.BooleanVar(value=bool(self.bot.cfg.notif_reconnect))
        var_nf_reb = tk.BooleanVar(value=bool(self.bot.cfg.notif_rebirth))
        var_nf_try = tk.BooleanVar(value=bool(self.bot.cfg.notif_tray_minimize))

        def _on_notif_toggle():
            self.bot.cfg.notif_afk_warn = bool(var_nf_afk.get())
            self.bot.cfg.notif_reconnect = bool(var_nf_rec.get())
            self.bot.cfg.notif_rebirth = bool(var_nf_reb.get())
            self.bot.cfg.notif_tray_minimize = bool(var_nf_try.get())
            self._auto_save_settings()

        for var_nf, key_lbl, fg_col in [
            (var_nf_afk, "notif_chk_afk", C["yellow"]),
            (var_nf_rec, "notif_chk_reconnect", C["accent"]),
            (var_nf_reb, "notif_chk_rebirth", C["green"]),
            (var_nf_try, "notif_chk_tray", C["text"]),
        ]:
            row_n = tk.Frame(sec_nf, bg=C["dark"], padx=8, pady=3)
            row_n.pack(fill="x", pady=2)
            tk.Checkbutton(
                row_n,
                text=self._t(key_lbl),
                variable=var_nf,
                bg=C["dark"],
                fg=fg_col,
                selectcolor=C["card"],
                activebackground=C["dark"],
                activeforeground="white",
                font=("Segoe UI", 8, "bold"),
                anchor="w",
                command=_on_notif_toggle,
            ).pack(fill="x")

        # ── Seção 3: Todos os Atalhos do Jogo Individualmente (Collect All Pets) ──
        sec_gm = tk.Frame(body, bg=C["card"], highlightbackground=C["border"], highlightthickness=1, padx=10, pady=8)
        sec_gm.pack(fill="x", pady=(0, 4))
        tk.Label(
            sec_gm,
            text=self._t("settings_sec_game_keys"),
            fg=C["yellow"],
            bg=C["card"],
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 2))
        tk.Label(
            sec_gm,
            text="Itens marcados com ★ são utilizados nativamente pelo CAP Assistant em vez de cliques.",
            fg=C["green"],
            bg=C["card"],
            font=("Segoe UI", 8, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 6))

        def _add_game_key_subsection(sub_title: str, items: list):
            sub_box = tk.Frame(sec_gm, bg="#101524", highlightbackground=C["border"], highlightthickness=1, padx=8, pady=5)
            sub_box.pack(fill="x", pady=(0, 6))
            tk.Label(
                sub_box,
                text=sub_title,
                fg=C["accent"],
                bg="#101524",
                font=("Segoe UI", 8, "bold"),
                anchor="w",
            ).pack(fill="x", pady=(0, 4))
            for key_lbl, desc_lbl, is_bot in items:
                row_g = tk.Frame(sub_box, bg=C["dark"] if is_bot else C["card"], padx=8, pady=2)
                row_g.pack(fill="x", pady=1)
                tk.Label(
                    row_g,
                    text=key_lbl,
                    fg=C["green"] if is_bot else C["yellow"],
                    bg=row_g["bg"],
                    font=("Consolas", 8, "bold"),
                    width=14,
                    anchor="w",
                ).pack(side="left")
                tk.Label(
                    row_g,
                    text=desc_lbl,
                    fg=C["text"],
                    bg=row_g["bg"],
                    font=("Segoe UI", 8, "bold" if is_bot else "normal"),
                    anchor="w",
                ).pack(side="left", fill="x", expand=True)

        _add_game_key_subsection(
            "🧭 3.1 NAVEGAÇÃO E TELAS DO JOGO",
            [
                ("[1]", "Pets equipados e fusão (Pets Equipped and Fusing)", False),
                ("[2]", "Índice de Pets (Pet Index)", False),
                ("[3]", "Medalhas / Conquistas (Badges)", False),
                ("[4]", "Configurações do Jogo (Game Settings)", False),
                ("[5]", "Loja do Jogo (Game Shop)", False),
                ("★ [Q]", "Fechar janela aberta (Close Window) — Usado no Anti-AFK [C+Q]", True),
            ],
        )

        _add_game_key_subsection(
            "⚡ 3.2 AÇÕES DE JOGO (PRIORIDADE DO CAP ASSISTANT)",
            [
                ("★ [E]", "Interagir ([E] Light nas Tochas / [E] Rebirth na Mesa / Fechar Tela de Pet)", True),
                ("★ [Z]", "Alternar tipo de ovo na barra inferior (Toggle Egg Type) — Sem clique!", True),
                ("★ [C]", "Coletar missão completa e fechar janela (Collect Quest & Close Window)", True),
                ("[R]", "Equipar melhores pets (Equip Best Pets)", False),
                ("[F]", "Alternar entre seguir / ficar parado (Toggle Pets Follow/Stay)", False),
                ("[F-F-R]", "Gerar/nascer os melhores pets instantaneamente na nova zona", False),
                ("[G]", "Alternar estação geradora (Toggle Generate Station)", False),
                ("[X]", "Ativar/desativar abertura automática de ovos (Autohatch On/Off)", False),
                ("[CTRL Esq.]", "Alternar pets descobertos no checklist (Toggle Discovered Pets)", False),
            ],
        )

        win.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

    _show_shortcuts_info = _open_app_settings_modal

    # ── Testes ───────────────────────────────────────────────────────────────

    def _test_screen(self):
        res = self.bot.test_screen_read()
        self._append_log(f"🔍 [TESTE TELA]\n{res}", "hi")
        messagebox.showinfo("Diagnóstico Visual", res)

    def _test_egg(self):
        res = self.bot.test_egg_click()
        self._append_log(f"🥚 [OVO HOTKEY Z] {res}", "hi")

    def _test_press_e(self):
        def _th():
            msg = self.bot.test_press_e()
            self._append_log(f"🔥 [AÇÃO E] {msg}", "hi")

        threading.Thread(target=_th, daemon=True).start()

    def _test_rebirth_hold(self):
        def _th():
            msg = self.bot.test_rebirth_hold()
            self._append_log(f"✨ [SEGURAR E REBIRTH] {msg}", "hi")

        threading.Thread(target=_th, daemon=True).start()

    def _test_altar_route(self):
        def _th():
            self._append_log("🧭 [TESTE ALTAR] Iniciando volta pelas 4 Tochas + Mesa Central...", "hi")
            msg = self.bot.test_altar_route()
            self._append_log(f"🧭 [TESTE ALTAR] {msg}", "ok")

        threading.Thread(target=_th, daemon=True).start()

    def _test_quest(self):
        res = self.bot.test_quest_claim()
        self._append_log(f"🏆 [MISSÃO HOTKEY C] {res}", "ok" if ("resgatada" in res or "enviada" in res) else "warn")

    def _test_anti_afk(self):
        def _th():
            self._append_log(
                "🛡️ [GIRO MULTI-JANELAS] Trazendo cada janela para o 1º Plano e enviando [C]+[Q]...", "hi"
            )
            msg = self.bot.test_anti_afk_pulse()
            self._append_log(f"🛡️ [GIRO MULTI-JANELAS] {msg}", "ok")

        threading.Thread(target=_th, daemon=True).start()

    def _test_reconnect(self):
        def _th():
            msg = self.bot.test_reconnect()
            self._append_log(f"🔌 [RECONECTAR] {msg}", "ok")

        threading.Thread(target=_th, daemon=True).start()

    # ── Ações ────────────────────────────────────────────────────────────────

    def _toggle(self):
        self._update_config()
        self.bot.toggle()

    def _pause(self):
        self.bot.pause_resume()

    def _clear_log(self):
        self._log.config(state="normal")
        self._log.delete("1.0", "end")
        self._log.config(state="disabled")

    def _copy_log_to_clipboard(self):
        try:
            txt = self._log.get("1.0", "end-1c").strip()
            if not txt:
                self._append_log("ℹ️ [Log] O Log de Atividade está vazio no momento.", "warn")
                return
            self.root.clipboard_clear()
            self.root.clipboard_append(txt)
            self.root.update_idletasks()
            lines_n = len(txt.splitlines())
            self._append_log(f"📋 [Log Copiado] {lines_n} linha(s) copiada(s) para a Área de Transferência!", "ok")
        except Exception as e:
            self._append_log(f"❌ Erro ao copiar log: {e}", "err")

    def _save_log_to_file(self):
        try:
            txt = self._log.get("1.0", "end-1c").strip()
            if not txt:
                self._append_log("ℹ️ [Log] O Log de Atividade está vazio no momento.", "warn")
                return
            ts_str = time.strftime("%Y-%m-%d_%H-%M-%S")
            path = filedialog.asksaveasfilename(
                parent=self.root,
                title="Salvar Log de Atividade — CAP Assistant",
                defaultextension=".txt",
                initialfile=f"cap_assistant_log_{ts_str}.txt",
                filetypes=[("Arquivo de Texto (.txt)", "*.txt"), ("Arquivo de Log (.log)", "*.log"), ("Todos", "*.*")],
            )
            if not path:
                return
            with open(path, "w", encoding="utf-8") as f:
                f.write(txt + "\n")
            self._append_log(f"💾 [Log Salvo] Histórico salvo com sucesso em: {os.path.basename(path)}", "ok")
        except Exception as e:
            self._append_log(f"❌ Erro ao salvar log: {e}", "err")

    def _append_log(self, text: str, tag: str = "info"):
        def _do():
            self._log.config(state="normal")
            self._log.insert("end", text + "\n", tag)
            self._log.see("end")
            self._log.config(state="disabled")

        self.root.after(0, _do)

    def _on_bot_update(self, s: BotStatus):
        self.root.after(0, lambda: self._apply_status(s))

    def _apply_status(self, s: BotStatus):
        self._refresh_window_list_ui(force=False)
        hk_t = self.bot.cfg.hotkey_toggle.upper()
        hk_p = self.bot.cfg.hotkey_pause.upper()

        if s.running:
            self._btn_start.config(
                text=self._t("btn_stop", hk=hk_t), bg=C["btn_stop"], activebackground=C["red"]
            )
            self._btn_pause.config(state="normal")
        else:
            self._btn_start.config(
                text=self._t("btn_start", hk=hk_t), bg=C["btn_start"], activebackground=C["green"]
            )
            self._btn_pause.config(state="disabled")

        if s.paused:
            self._btn_pause.config(text=self._t("btn_resume", hk=hk_p), bg="#2e7d32")
        elif s.auto_paused_mouse:
            if getattr(s, "user_in_game", False):
                self._btn_pause.config(
                    text=self._t("btn_in_game_afk", s=s.mouse_idle_remaining), bg="#0288d1"
                )
            else:
                self._btn_pause.config(
                    text=self._t("btn_pc_in_use", s=s.mouse_idle_remaining), bg="#ff6d00"
                )
        else:
            self._btn_pause.config(text=self._t("btn_pause", hk=hk_p), bg=C["btn_pause"])

        self._card_rebirths._val_label.config(text=str(s.rebirth_count))
        self._card_quests._val_label.config(text=str(s.quests_claimed))
        self._card_torches._val_label.config(text=str(s.anti_afk_pulses))
        self._status_line.set(f"● {s.last_action}")

        if s.detected_active_egg:
            self._lbl_active_egg.config(text=self._t("egg_bar", egg=s.detected_active_egg))

        pri = self.bot.get_primary_profile()
        if pri and not pri.auto_eggs and not pri.auto_move_altar:
            for lbl in self._egg_widgets:
                lbl.config(text=self._t("st_disabled"), fg=C["dim"])
            return

        for i, lbl in enumerate(self._egg_widgets):
            if i < len(s.tier_status) and s.tier_status[i]:
                lbl.config(text=self._t("st_check"), fg=C["green"])
            elif i == s.current_tier_index and s.running and s.state == BotState.EGG_FARMING:
                lbl.config(text=self._t("st_monitoring"), fg=C["yellow"])
            else:
                lbl.config(text=self._t("st_pending"), fg=C["dim"])

    def _poll_roblox(self):
        self._refresh_window_list_ui(force=False)

        if self.bot.status.running:
            self.root.after(1800, self._poll_roblox)
            return

        profiles = self.bot.sync_windows()
        pri = self.bot.get_primary_profile()
        if pri:
            win_count = len(profiles)
            self._lbl_roblox_status.config(
                text=self._t("roblox_connected", n=win_count, idx=pri.index, hwnd=pri.hwnd),
                fg=C["green"],
            )
            if not pri.auto_eggs and not pri.auto_move_altar:
                self._lbl_active_egg.config(text=self._t("egg_disabled"))
                for lbl in self._egg_widgets:
                    lbl.config(text=self._t("st_disabled"), fg=C["dim"])
            else:
                frame = self.bot.vision.capture_window(pri.hwnd)
                if frame is not None:
                    egg = self.bot.vision.detect_active_egg(frame)
                    self._lbl_active_egg.config(text=self._t("egg_bar", egg=egg))
                    st = self.bot.vision.analyze_tiers(frame)
                    for i, lbl in enumerate(self._egg_widgets):
                        if i < len(st) and st[i]:
                            lbl.config(text=self._t("st_check"), fg=C["green"])
                        else:
                            lbl.config(text=self._t("st_pending"), fg=C["dim"])
        else:
            self._lbl_roblox_status.config(text=self._t("no_roblox"), fg=C["red"])
            self._lbl_active_egg.config(text=self._t("egg_bar", egg="---"))

        self.root.after(1800, self._poll_roblox)

    def _tick_uptime(self):
        if self.bot.status.running:
            self._card_uptime._val_label.config(text=self.bot.status.uptime_str)
        self.root.after(1000, self._tick_uptime)

    def _on_close(self):
        if self._in_tray:
            self._restore_from_tray()
        if self.bot.status.running:
            if messagebox.askyesno(self._t("close_confirm_title"), self._t("close_confirm_msg")):
                self.bot.stop()
                self.visual_mgr.set_overlay_enabled(False)
                self.visual_mgr.set_monitor_enabled(False)
                self.tray_mgr.destroy()
                time.sleep(0.2)
                self.root.destroy()
        else:
            self.visual_mgr.set_overlay_enabled(False)
            self.visual_mgr.set_monitor_enabled(False)
            self.tray_mgr.destroy()
            self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    try:
        import ctypes
        console_hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if console_hwnd:
            ctypes.windll.user32.ShowWindow(console_hwnd, 0)
    except Exception:
        pass
    app = CAPBotGUI()
    app.run()
