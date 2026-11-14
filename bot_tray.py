"""
CAP Assistant — Gerenciador de Bandeja do Sistema (System Tray) & Notificações de Balão
- 100% Seguro em Windows 10/11 64-bit (Zero crash de WndProc / DefWindowProc).
- Combina o Ícone Nativo da Bandeja do Windows (Shell_NotifyIconW 64-bit) com um
  Mini-Dock de Bandeja no canto do relógio (garantindo que o app NUNCA suma se o Windows 11
  ocultar ícones no menu '^' ou se o modo Jogo/Foco bloquear balões nativos).
- Emite Balões de Notificação (Toast Visuais + Nativos) para:
  * Minimizar para a Bandeja.
  * Alerta Anti-AFK (15 min e 18 min sem ação antes do limite de 20 min do Roblox).
  * Reconexão automática realizada.
  * Renascimento concluído.
"""

import ctypes
from ctypes import wintypes
import os
import sys
import tkinter as tk
import win32con
import win32gui

shell32 = ctypes.windll.shell32
user32 = ctypes.windll.user32

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_INFO = 0x00000001
NIIF_WARNING = 0x00000002


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class SystemTrayManager:
    """
    Gerencia a Bandeja do Sistema (System Tray), o Mini-Dock de Bandeja próximo ao relógio
    do Windows e os Balões de Notificação (Toast) sem risco de crash em 64-bit.
    """

    def __init__(
        self,
        root: tk.Tk,
        on_restore_cb,
        on_toggle_start_cb,
        on_toggle_pause_cb,
        on_exit_cb,
        tr_fn,
    ):
        self.root = root
        self.on_restore_cb = on_restore_cb
        self.on_toggle_start_cb = on_toggle_start_cb
        self.on_toggle_pause_cb = on_toggle_pause_cb
        self.on_exit_cb = on_exit_cb
        self.tr_fn = tr_fn

        self._hwnd = 0
        self._hicon = 0
        self._native_added = False
        self._menu: tk.Menu = None

        self._dock_win: tk.Toplevel = None
        self._dock_lbl_status: tk.Label = None
        self._drag_off_x = 0
        self._drag_off_y = 0
        self._dock_pos = None  # (x, y) salvo se o usuário arrastar a pílula

        self._balloon_win: tk.Toplevel = None
        self._balloon_after_id = None

        self._init_native_tray()

    def _load_app_icon(self) -> int:
        base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        ico_path = os.path.join(base_dir, "cap_assistant.ico")
        if os.path.exists(ico_path):
            try:
                user32.LoadImageW.restype = wintypes.HANDLE
                hicon = user32.LoadImageW(
                    0,
                    ico_path,
                    win32con.IMAGE_ICON,
                    0,
                    0,
                    win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE,
                )
                if hicon:
                    return int(hicon)
            except Exception:
                pass
        try:
            return int(win32gui.LoadIcon(0, win32con.IDI_APPLICATION))
        except Exception:
            return 0

    def _init_native_tray(self):
        """Registra ícone nativo usando o HWND da janela raiz (sem criar WNDCLASS/DefWindowProc instável)."""
        try:
            self.root.update_idletasks()
            hwnd = win32gui.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            self._hwnd = int(hwnd)
            self._hicon = self._load_app_icon()
            self._ensure_native_icon()
        except Exception:
            self._hwnd = 0
            self._native_added = False

    def _fill_nid(self, flags: int, tip: str = "CAP Assistant v4.2") -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = 0
        nid.hIcon = self._hicon
        nid.szTip = (tip or "CAP Assistant v4.2")[:127]
        return nid

    def _ensure_native_icon(self) -> bool:
        if not self._hwnd:
            return False
        try:
            nid = self._fill_nid(NIF_ICON | NIF_TIP, "CAP Assistant v4.2 — Ativo")
            if not self._native_added:
                ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
                self._native_added = ok
            else:
                ok = bool(shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))
            return ok
        except Exception:
            return False

    # ── Mini-Dock de Bandeja (Pílula Flutuante Próxima ao Relógio do Windows) ──

    def show_tray_dock(self, status_text: str = "🟢 CAP Assistant (Bandeja)"):
        """
        Exibe uma pílula compacta no canto inferior direito (próximo ao relógio da bandeja)
        quando a janela principal está oculta na Bandeja.
        - Clique Esquerdo ou botão '↗ Abrir': Restaura a janela principal imediatamente.
        - Clique Direito: Abre o menu rápido da Bandeja (Abrir, Iniciar/Parar, Pausar, Sair).
        - Arrastar: Permite mover a pílula caso deseje.
        """
        try:
            if self._dock_win is not None and self._dock_win.winfo_exists():
                if self._dock_lbl_status is not None:
                    self._dock_lbl_status.config(text=status_text)
                self._dock_win.deiconify()
                self._dock_win.lift()
                return
        except Exception:
            pass

        try:
            w = tk.Toplevel(self.root)
            w.wm_overrideredirect(True)
            try:
                w.wm_attributes("-topmost", True)
                w.wm_attributes("-alpha", 0.95)
            except Exception:
                pass
            w.configure(bg="#00d4ff")

            inner = tk.Frame(w, bg="#101524", padx=8, pady=5)
            inner.pack(fill="both", expand=True, padx=1, pady=1)

            lbl_ico = tk.Label(
                inner,
                text="🐾",
                fg="#00e676",
                bg="#101524",
                font=("Segoe UI", 10, "bold"),
                cursor="hand2",
            )
            lbl_ico.pack(side="left", padx=(0, 4))

            lbl_st = tk.Label(
                inner,
                text=status_text,
                fg="#e4e8ff",
                bg="#101524",
                font=("Segoe UI", 8, "bold"),
                cursor="hand2",
            )
            lbl_st.pack(side="left", padx=(0, 8))
            self._dock_lbl_status = lbl_st

            btn_open = tk.Button(
                inner,
                text="↗ Abrir",
                fg="#090a10",
                bg="#00e676",
                activebackground="#00c853",
                activeforeground="white",
                relief="flat",
                cursor="hand2",
                padx=6,
                pady=1,
                font=("Segoe UI", 8, "bold"),
                command=self.on_restore_cb,
            )
            btn_open.pack(side="left", padx=(0, 3))

            btn_menu = tk.Button(
                inner,
                text="☰",
                fg="#ffd740",
                bg="#242a42",
                activebackground="#00d4ff",
                activeforeground="#090a10",
                relief="flat",
                cursor="hand2",
                padx=5,
                pady=1,
                font=("Segoe UI", 8, "bold"),
                command=self._popup_context_menu,
            )
            btn_menu.pack(side="left")

            def _start_drag(event):
                self._drag_off_x = event.x
                self._drag_off_y = event.y

            def _do_drag(event):
                try:
                    nx = w.winfo_x() + (event.x - self._drag_off_x)
                    ny = w.winfo_y() + (event.y - self._drag_off_y)
                    w.wm_geometry(f"+{nx}+{ny}")
                    self._dock_pos = (nx, ny)
                except Exception:
                    pass

            for wdg in (inner, lbl_ico, lbl_st):
                wdg.bind("<ButtonPress-1>", _start_drag)
                wdg.bind("<B1-Motion>", _do_drag)
                wdg.bind("<Double-Button-1>", lambda e: self.on_restore_cb())
                wdg.bind("<ButtonRelease-3>", lambda e: self._popup_context_menu())

            w.update_idletasks()
            req_w = max(235, w.winfo_reqwidth())
            req_h = max(34, w.winfo_reqheight())
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()

            if self._dock_pos is not None:
                dx, dy = self._dock_pos
            else:
                dx = max(10, sw - req_w - 16)
                dy = max(10, sh - req_h - 54)

            w.wm_geometry(f"+{dx}+{dy}")
            self._dock_win = w
        except Exception:
            pass

    def hide_tray_dock(self):
        if self._dock_win is not None:
            try:
                self._dock_win.destroy()
            except Exception:
                pass
            self._dock_win = None
            self._dock_lbl_status = None

    def update_tooltip(self, tip_text: str):
        if self._hwnd and self._native_added:
            try:
                nid = self._fill_nid(NIF_TIP, tip_text)
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            except Exception:
                pass

    # ── Balão de Notificação (Toast Visual Garantido + Balão Nativo) ──────────

    def show_balloon(self, title: str, message: str, warning: bool = False):
        """
        Exibe uma notificação de balão (Toast) no canto inferior direito da tela (próximo ao relógio),
        funcionando 100% mesmo com o Roblox aberto ou Assistente de Foco do Windows ativo.
        """
        self.root.after(0, lambda: self._show_balloon_main_thread(title, message, warning))

    def _show_balloon_main_thread(self, title: str, message: str, warning: bool = False):
        # 1. Tenta enviar também pelo Shell_NotifyIconW nativo se disponível
        if self._hwnd and self._ensure_native_icon():
            try:
                nid = self._fill_nid(
                    NIF_ICON | NIF_TIP | NIF_INFO,
                    "CAP Assistant v4.2",
                )
                nid.szInfoTitle = (title or "CAP Assistant")[:63]
                nid.szInfo = (message or "")[:255]
                nid.uTimeoutOrVersion = 6000
                nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
                shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            except Exception:
                pass

        # 2. Exibe Balão Toast Visual próprio no canto inferior direito (acima do relógio / dock)
        try:
            if self._balloon_after_id is not None:
                try:
                    self.root.after_cancel(self._balloon_after_id)
                except Exception:
                    pass
                self._balloon_after_id = None

            if self._balloon_win is not None:
                try:
                    self._balloon_win.destroy()
                except Exception:
                    pass
                self._balloon_win = None

            bw = tk.Toplevel(self.root)
            bw.wm_overrideredirect(True)
            try:
                bw.wm_attributes("-topmost", True)
                bw.wm_attributes("-alpha", 0.96)
            except Exception:
                pass

            border_col = "#ffd740" if warning else "#00d4ff"
            bw.configure(bg=border_col)

            box = tk.Frame(bw, bg="#101524", padx=12, pady=9)
            box.pack(fill="both", expand=True, padx=2, pady=2)

            top_r = tk.Frame(box, bg="#101524")
            top_r.pack(fill="x")

            lbl_t = tk.Label(
                top_r,
                text=title or "CAP Assistant",
                fg=border_col,
                bg="#101524",
                font=("Segoe UI", 9, "bold"),
                anchor="w",
                justify="left",
            )
            lbl_t.pack(side="left", fill="x", expand=True)

            btn_x = tk.Button(
                top_r,
                text="✕",
                fg="#656b8a",
                bg="#101524",
                activebackground="#23283e",
                activeforeground="white",
                relief="flat",
                bd=0,
                cursor="hand2",
                font=("Segoe UI", 8, "bold"),
                command=self._close_balloon,
            )
            btn_x.pack(side="right", padx=(6, 0))

            lbl_m = tk.Label(
                box,
                text=message or "",
                fg="#e4e8ff",
                bg="#101524",
                font=("Segoe UI", 8),
                anchor="w",
                justify="left",
                wraplength=310,
            )
            lbl_m.pack(fill="x", pady=(4, 0))

            for wdg in (box, lbl_t, lbl_m):
                wdg.bind("<Button-1>", lambda e: (self._close_balloon(), self.on_restore_cb()))

            bw.update_idletasks()
            w_req = max(290, bw.winfo_reqwidth())
            h_req = max(68, bw.winfo_reqheight())
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()

            bx = max(10, sw - w_req - 16)
            by = max(10, sh - h_req - 98)
            bw.wm_geometry(f"+{bx}+{by}")

            self._balloon_win = bw
            timeout_ms = 8000 if warning else 5000
            self._balloon_after_id = self.root.after(timeout_ms, self._close_balloon)
        except Exception:
            pass

    def _close_balloon(self):
        if self._balloon_after_id is not None:
            try:
                self.root.after_cancel(self._balloon_after_id)
            except Exception:
                pass
            self._balloon_after_id = None
        if self._balloon_win is not None:
            try:
                self._balloon_win.destroy()
            except Exception:
                pass
            self._balloon_win = None

    def _popup_context_menu(self):
        try:
            if self._menu is not None:
                try:
                    self._menu.destroy()
                except Exception:
                    pass
            m = tk.Menu(
                self.root,
                tearoff=0,
                bg="#161926",
                fg="#e4e8ff",
                activebackground="#00d4ff",
                activeforeground="#090a10",
                font=("Segoe UI", 9, "bold"),
            )
            m.add_command(label=f"🐾 {self.tr_fn('tray_open')}", command=self.on_restore_cb)
            m.add_separator()
            m.add_command(label=f"▶/⏹ {self.tr_fn('tray_start_stop')}", command=self.on_toggle_start_cb)
            m.add_command(label=f"⏸/▶ {self.tr_fn('tray_pause_resume')}", command=self.on_toggle_pause_cb)
            m.add_separator()
            m.add_command(label=f"❌ {self.tr_fn('tray_exit')}", command=self.on_exit_cb)
            self._menu = m

            px = self.root.winfo_pointerx()
            py = self.root.winfo_pointery()
            m.tk_popup(px, max(20, py - 110))
        except Exception:
            pass

    def destroy(self):
        self._close_balloon()
        self.hide_tray_dock()
        if self._hwnd and self._native_added:
            try:
                nid = self._fill_nid(0)
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            except Exception:
                pass
            self._native_added = False
        self._hwnd = 0
