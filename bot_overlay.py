"""
CAP Rebirth Bot - Multi-Window Visual Indicator System v4.2.1
Suporta TODAS as janelas abertas do Roblox (1, 2, 3... N) simultaneamente em ambos os modos:
1. MultiWindowOverlayHUD (Modo 1 - Na Tela do Jogo):
   - Cria um Overlay independente para CADA janela do Roblox aberta.
   - Usa Win32 SetWindowRgn (Região Vazada Oca): 99% da tela 3D do jogo NÃO possui janela por cima
     (zero lag de DWM, zero travamento visual, zero interferência no DirectX).
   - Recorta automaticamente (RGN_DIFF) áreas cobertas por outras janelas do Roblox que estejam na frente.
   - Alinhamento 1:1 exato (cl_left, win_top) com o frame capturado pelo PrintWindow.
2. LiveMiniMonitor (Modo 2 - Janela Externa Ao Vivo):
   - Exibe TODAS as janelas abertas do Roblox lado a lado (mosaico dinâmico 1..N) com os contornos
     exatos detectados pelo VisionReader.
"""

import ctypes
import threading
import time
import tkinter as tk
import cv2
import numpy as np
from PIL import Image, ImageTk
import win32con
import win32gui
from typing import Optional, Dict, Tuple, List

from bot_vision import PET_NAMES
from bot_i18n import tr

gdi32 = ctypes.windll.gdi32
user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]

RGN_AND = 1
RGN_OR = 2
RGN_XOR = 3
RGN_DIFF = 4
RGN_COPY = 5


class WindowVisionSnapshot:
    """Armazena os dados visuais exatos detectados em uma janela específica do Roblox."""

    def __init__(self, hwnd: int, index: int, is_primary: bool):
        self.hwnd: int = hwnd
        self.index: int = index
        self.is_primary: bool = is_primary
        self.screen_x: int = 0
        self.screen_y: int = 0
        self.width: int = 0
        self.height: int = 0
        self.win_rect: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self.title_bar_h: int = 31

        # Opções ativas nesta janela
        self.auto_interact_e: bool = False
        self.auto_eggs: bool = False
        self.auto_move_altar: bool = False
        self.auto_claim_quest: bool = True
        self.auto_anti_afk: bool = True
        self.auto_reconnect: bool = True

        self.frame_bgr: Optional[np.ndarray] = None

        # Regiões detectadas com coordenadas exatas do frame
        self.quest_details: Optional[Tuple[Tuple[int, int, int, int], Tuple[int, int, int, int], bool, float]] = None
        self.checklist_rect: Tuple[int, int, int, int] = (0, 0, 0, 0)
        self.checklist_row_ys: List[int] = []
        self.tier_checks: List[bool] = [False] * 8
        self.egg_details: Optional[Tuple[int, int, int, str]] = None
        self.table_hit: Optional[Tuple[int, int, float]] = None
        self.table_rect: Optional[Tuple[int, int, int, int, float]] = None
        self.torch_states: Dict[str, Optional[bool]] = {"SW": None, "NW": None, "NE": None, "SE": None}
        self.altar_pts: Optional[Dict[str, Tuple[int, int]]] = None
        self.prompt_type: Optional[str] = None
        self.prompt_score: float = 0.0
        self.prompt_pos: Tuple[int, int] = (0, 0)
        self.pet_continue_hit: Optional[Tuple[int, int, int, int, float]] = None
        self.disconnect_hit: Optional[Tuple[int, int, int, int, float]] = None


def _create_hollow_frame_rgn(x1: int, y1: int, x2: int, y2: int, thickness: int = 2) -> int:
    """Cria uma região Win32 oca (apenas a borda de 'thickness' pixels, 100% vazia por dentro)."""
    outer = gdi32.CreateRectRgn(int(x1), int(y1), int(x2), int(y2))
    if (x2 - x1) > thickness * 2 and (y2 - y1) > thickness * 2:
        inner = gdi32.CreateRectRgn(
            int(x1 + thickness),
            int(y1 + thickness),
            int(x2 - thickness),
            int(y2 - thickness),
        )
        gdi32.CombineRgn(outer, outer, inner, RGN_DIFF)
        gdi32.DeleteObject(inner)
    return outer


class SingleWindowOverlay:
    """
    Overlay HUD para UMA janela do Roblox usando Win32 SetWindowRgn (Região Vazada).
    - Como a janela só existe nos 2 pixels dos contornos e na tarja fina de topo,
      o motor 3D do Roblox roda 100% livre sem qualquer composição DWM sobre a cena 3D.
    - Inclui cache de assinatura visual (_last_render_sig) para só redesenhar / chamar
      SetWindowRgn quando as coordenadas ou estados realmente mudam (zero peso na CPU/GPU).
    """

    def __init__(self, root: tk.Tk, hwnd: int):
        self.root = root
        self.target_hwnd = hwnd
        self.win: Optional[tk.Toplevel] = None
        self.canvas: Optional[tk.Canvas] = None
        self._overlay_hwnd: int = 0
        self._last_geom: str = ""
        self._last_render_sig = None
        self._create_window()

    def _create_window(self):
        try:
            self.win = tk.Toplevel(self.root)
            self.win.title(f"CAP Overlay {self.target_hwnd}")
            self.win.overrideredirect(True)
            self.win.configure(bg="#0B1120")
            self.win.attributes("-topmost", True)

            self.canvas = tk.Canvas(
                self.win,
                bg="#0B1120",
                highlightthickness=0,
                bd=0,
            )
            self.canvas.pack(fill="both", expand=True)

            self.win.update_idletasks()
            raw_id = int(self.win.winfo_id())
            parent_id = win32gui.GetParent(raw_id)
            self._overlay_hwnd = parent_id if parent_id else raw_id
            self._last_render_sig = None

            for h in (raw_id, self._overlay_hwnd):
                if not h:
                    continue
                try:
                    ex_style = win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE)
                    ex_style |= (
                        win32con.WS_EX_LAYERED
                        | win32con.WS_EX_TRANSPARENT
                        | win32con.WS_EX_TOPMOST
                        | win32con.WS_EX_TOOLWINDOW
                        | getattr(win32con, "WS_EX_NOACTIVATE", 0x08000000)
                    )
                    win32gui.SetWindowLong(h, win32con.GWL_EXSTYLE, ex_style)
                    user32.SetLayeredWindowAttributes(h, 0, 235, win32con.LWA_ALPHA)
                except Exception:
                    pass
        except Exception:
            self.destroy()

    def destroy(self):
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
        self.win = None
        self.canvas = None
        self._overlay_hwnd = 0
        self._last_render_sig = None

    def render(
        self,
        snap: WindowVisionSnapshot,
        bot,
        occluding_rects: List[Tuple[int, int, int, int]],
    ):
        if self.win is None or self.canvas is None or not self.win.winfo_exists():
            self._create_window()
        if self.win is None or self.canvas is None:
            return

        if snap.width <= 100 or snap.height <= 100 or not win32gui.IsWindow(snap.hwnd) or win32gui.IsIconic(snap.hwnd):
            self.win.withdraw()
            self._last_render_sig = None
            return

        geom = f"{snap.width}x{snap.height}+{snap.screen_x}+{snap.screen_y}"
        if geom != self._last_geom:
            self._last_geom = geom
            self._last_render_sig = None
            try:
                self.win.geometry(geom)
            except Exception:
                return

        self.win.deiconify()

        st = bot.status
        if not st.running:
            state_col = "#38BDF8"
            state_lbl = "PRONTO"
        elif st.auto_paused_mouse:
            if getattr(st, "user_in_game", False):
                state_col = "#10B981"
                state_lbl = f"EM JOGO (AFK Pausa {st.mouse_idle_remaining}s)"
            else:
                state_col = "#F97316"
                state_lbl = f"PAUSA PC ({st.mouse_idle_remaining}s)"
        elif st.paused:
            state_col = "#F59E0B"
            state_lbl = "PAUSADO"
        else:
            state_col = "#10B981"
            state_lbl = st.state.value

        role_tag = f"⭐ #{snap.index} PRINCIPAL" if snap.is_primary else f"🔹 #{snap.index} SECUNDÁRIA"
        feats = []
        if snap.auto_interact_e and snap.auto_eggs and not snap.auto_move_altar:
            feats.append("Semi-Auto[E+Z]")
        elif snap.auto_interact_e and snap.auto_eggs and snap.auto_move_altar:
            feats.append("Full-Auto[Altar]")
        else:
            if snap.auto_interact_e:
                feats.append("[E]")
            if snap.auto_eggs:
                feats.append("Ovos[Z]")
            if snap.auto_move_altar:
                feats.append("Altar")
        if snap.auto_claim_quest:
            feats.append("Missão[C]")
        if snap.auto_anti_afk:
            feats.append("AFK[C+Q]")
        if snap.auto_reconnect:
            feats.append("Recon")
        feat_str = "+".join(feats) if feats else "Off"
        pill_text = f"{role_tag} | {state_lbl} | {feat_str}"

        checks_done = sum(1 for x in snap.tier_checks if x)
        altar_sig = (
            tuple((k, snap.altar_pts.get(k), snap.torch_states.get(k)) for k in ("SW", "NW", "NE", "SE", "P0"))
            if snap.altar_pts
            else None
        )
        trect_sig = snap.table_rect[:4] if snap.table_rect else None
        quest_sig = (snap.quest_details[0], snap.quest_details[1], snap.quest_details[2]) if snap.quest_details else None
        pet_sig = snap.pet_continue_hit[:2] if snap.pet_continue_hit else None
        disc_sig = snap.disconnect_hit[:2] if snap.disconnect_hit else None

        render_sig = (
            geom,
            pill_text,
            state_col,
            quest_sig if snap.auto_claim_quest else None,
            (snap.checklist_rect, checks_done) if (snap.auto_eggs or snap.auto_move_altar) else None,
            snap.egg_details if snap.auto_eggs else None,
            (trect_sig, altar_sig) if snap.auto_move_altar else None,
            (snap.prompt_type, snap.prompt_pos) if (snap.auto_interact_e or snap.auto_move_altar) else None,
            pet_sig,
            disc_sig if snap.auto_reconnect else None,
            tuple(occluding_rects),
        )
        if render_sig == self._last_render_sig:
            return
        self._last_render_sig = render_sig

        c = self.canvas
        c.delete("all")
        w, h = snap.width, snap.height

        # Região Win32 acumulada: apenas os pixels dentro de master_rgn serão visíveis!
        master_rgn = gdi32.CreateRectRgn(0, 0, 0, 0)

        def _add_solid_rect(x1: int, y1: int, x2: int, y2: int):
            r = gdi32.CreateRectRgn(max(0, int(x1)), max(0, int(y1)), min(w, int(x2)), min(h, int(y2)))
            gdi32.CombineRgn(master_rgn, master_rgn, r, RGN_OR)
            gdi32.DeleteObject(r)

        def _add_hollow_box(x1: int, y1: int, x2: int, y2: int, color: str, thick: int = 3, label: str = ""):
            x1, y1 = max(2, int(x1)), max(2, int(y1))
            x2, y2 = min(w - 2, int(x2)), min(h - 2, int(y2))
            if x2 <= x1 + 6 or y2 <= y1 + 6:
                return
            r = _create_hollow_frame_rgn(x1, y1, x2, y2, thickness=thick)
            gdi32.CombineRgn(master_rgn, master_rgn, r, RGN_OR)
            gdi32.DeleteObject(r)
            c.create_rectangle(x1, y1, x2, y2, fill=color, outline=color)

            if label:
                lw = min(w - x1 - 4, max(68, len(label) * 7 + 12))
                lh = 18
                ly1 = max(2, y1 - lh) if y1 >= lh + 4 else min(h - lh - 2, y2)
                lx2 = min(w - 2, x1 + lw)
                _add_solid_rect(x1, ly1, lx2, ly1 + lh)
                c.create_rectangle(x1, ly1, lx2, ly1 + lh, fill="#0B1120", outline=color, width=1)
                c.create_text(
                    x1 + 5,
                    ly1 + lh // 2,
                    anchor="w",
                    text=label,
                    fill=color,
                    font=("Segoe UI", 8, "bold"),
                )

        # 1. Tarja de Status Compacta posicionada no espaço livre da Barra de Título
        pill_w = min(w - 220, max(220, len(pill_text) * 6 + 20))
        pill_h = 20
        if snap.title_bar_h >= 24:
            px1 = max(92, w - pill_w - 148)
            py1 = max(6, snap.title_bar_h - pill_h - 3)
        else:
            px1 = max(8, w - pill_w - 8)
            py1 = max(4, h - pill_h - 6)
        px2 = px1 + pill_w
        py2 = py1 + pill_h

        _add_solid_rect(px1, py1, px2, py2)
        c.create_rectangle(px1, py1, px2, py2, fill="#0B1120", outline=state_col, width=2)
        c.create_text(
            px1 + 8,
            py1 + pill_h // 2,
            anchor="w",
            text=pill_text,
            fill=state_col,
            font=("Consolas", 8, "bold"),
        )

        # 2. Banner de Missão (Exatamente nas coordenadas reais detectadas!)
        if snap.auto_claim_quest and snap.quest_details is not None:
            (qx, qy, qw, qh), (bx, by, bw, bh), is_ready, g_rat = snap.quest_details
            if is_ready:
                _add_hollow_box(qx - 2, qy - 2, qx + qw + 2, qy + qh + 2, "#10B981", thick=3, label="🏆 PRONTA -> [C]")
                _add_hollow_box(bx - 2, by - 2, bx + bw + 2, by + bh + 2, "#10B981", thick=3)
            else:
                _add_hollow_box(qx - 2, qy - 2, qx + qw + 2, qy + qh + 2, "#0EA5E9", thick=2, label="🏆 Missão [C]")

        # 3. Checklist de 8 Tiers (Somente se Ovos [Z] ou Altar estiverem ativos nesta janela!)
        if (snap.auto_eggs or snap.auto_move_altar) and snap.checklist_rect[2] > 0:
            cx, cy, cw, ch = snap.checklist_rect
            chk_col = "#10B981" if checks_done == 8 else "#F59E0B"
            _add_hollow_box(
                cx - 3,
                cy - 3,
                cx + cw + 3,
                cy + ch + 3,
                chk_col,
                thick=2,
                label=f"📋 Checks: {checks_done}/8",
            )

        # 4. Botão de Ovo na Barra Inferior (Somente se Ovos [Z] estiver ativo E o botão estiver visível!)
        if snap.auto_eggs and snap.egg_details is not None:
            ecx, ecy, er, ename = snap.egg_details
            _add_hollow_box(
                ecx - er - 4,
                ecy - er - 4,
                ecx + er + 4,
                ecy + er + 4,
                "#D946EF",
                thick=2,
                label=f"🥚 {ename} [Z]",
            )

        # 5. MESA BRANCA do Altar e Waypoints das 4 Tochas (mostrando tochas já acesas!)
        if snap.auto_move_altar and snap.table_rect is not None:
            rx, ry, rw, rh, tscore = snap.table_rect
            _add_hollow_box(
                int(rx),
                int(ry),
                int(rx + rw),
                int(ry + rh),
                "#38BDF8",
                thick=2,
                label=f"🏛️ Mesa Branca ({tscore:.0%})",
            )
            if snap.altar_pts:
                for k in ("SW", "NW", "NE", "SE", "P0"):
                    if k in snap.altar_pts:
                        ptx, pty = snap.altar_pts[k]
                        is_lit = snap.torch_states.get(k) is True
                        if k == "P0":
                            col = "#10B981"
                            lbl_k = "🏛️ P0"
                        elif is_lit:
                            col = "#10B981"
                            lbl_k = f"🔥 {k} (Acesa)"
                        else:
                            col = "#F97316"
                            lbl_k = f"🧭 {k}"
                        _add_hollow_box(ptx - 8, pty - 8, ptx + 8, pty + 8, col, thick=2, label=lbl_k)

        # 6. Prompt [E] Light / Rebirth
        if (snap.auto_interact_e or snap.auto_move_altar) and snap.prompt_type:
            px, py = snap.prompt_pos
            if px > 0 and py > 0:
                pcol = "#10B981" if snap.prompt_type == "LIGHT" else "#EC4899"
                _add_hollow_box(
                    px - 26,
                    py - 20,
                    px + 26,
                    py + 20,
                    pcol,
                    thick=3,
                    label=f"⚡ [E] {snap.prompt_type}",
                )

        # 6B. Tela 'New Pet Discovered! / Continue' (Fechar com [E])
        if snap.pet_continue_hit is not None:
            pcx, pcy, _, _, pconf = snap.pet_continue_hit
            _add_hollow_box(
                pcx - 115,
                pcy - 34,
                pcx + 115,
                pcy + 34,
                "#10B981",
                thick=3,
                label=f"🐣 FECHAR PET -> [E] ({pconf:.0%})",
            )

        # 7. Modal de Desconexão (Reconectar / Reconnect)
        if snap.auto_reconnect and snap.disconnect_hit is not None:
            dx, dy, _, _, dconf = snap.disconnect_hit
            _add_hollow_box(
                dx - 70,
                dy - 20,
                dx + 70,
                dy + 20,
                "#EF4444",
                thick=3,
                label=f"🔌 RECONECTAR ({dconf:.0%})",
            )

        # 8. Recorta (RGN_DIFF) áreas que estejam cobertas por outra janela à frente desta!
        for ox1, oy1, ox2, oy2 in occluding_rects:
            rel_x1 = max(0, ox1 - snap.screen_x)
            rel_y1 = max(0, oy1 - snap.screen_y)
            rel_x2 = min(w, ox2 - snap.screen_x)
            rel_y2 = min(h, oy2 - snap.screen_y)
            if rel_x2 > rel_x1 and rel_y2 > rel_y1:
                occ_rgn = gdi32.CreateRectRgn(int(rel_x1), int(rel_y1), int(rel_x2), int(rel_y2))
                gdi32.CombineRgn(master_rgn, master_rgn, occ_rgn, RGN_DIFF)
                gdi32.DeleteObject(occ_rgn)

        if self._overlay_hwnd:
            user32.SetWindowRgn(self._overlay_hwnd, master_rgn, True)
        else:
            gdi32.DeleteObject(master_rgn)


class MultiWindowOverlayHUD:
    """Gerencia Overlays independentes para TODAS as janelas abertas do Roblox (1, 2, 3... N)."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.enabled = False
        self.overlays: Dict[int, SingleWindowOverlay] = {}
        self._last_z_check_ts: float = 0.0
        self._cached_z_ordered: List[int] = []
        self._cached_occluders: Dict[int, List[Tuple[int, int, int, int]]] = {}

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        if not self.enabled:
            self._destroy_all()

    def _destroy_all(self):
        for ov in list(self.overlays.values()):
            ov.destroy()
        self.overlays.clear()
        self._cached_z_ordered.clear()
        self._cached_occluders.clear()

    @staticmethod
    def _get_visible_window_bounds(hwnd: int) -> Tuple[int, int, int, int]:
        """Retorna o retângulo visível real da janela na tela (sem a sombra invisível de 7px do DWM)."""
        rect = _RECT()
        try:
            res = dwmapi.DwmGetWindowAttribute(
                int(hwnd),
                DWMWA_EXTENDED_FRAME_BOUNDS,
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
            if res == 0 and rect.right > rect.left and rect.bottom > rect.top:
                return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        except Exception:
            pass
        try:
            return win32gui.GetWindowRect(hwnd)
        except Exception:
            return (0, 0, 0, 0)

    @classmethod
    def _is_real_occluding_window(cls, hwnd: int, ignore_hwnds: set) -> bool:
        """
        Verifica se 'hwnd' é uma janela real visível na tela (ex: Explorer, navegador, Discord, GUI do Bot)
        que está cobrindo fisicamente as janelas atrás dela na ordem Z do Windows.
        Ignora janelas minimizadas, ocultas, 'cloaked' pelo DWM (apps UWP suspensos) ou transparentes.
        """
        if not hwnd or hwnd in ignore_hwnds:
            return False
        try:
            if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                return False

            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            if ex_style & win32con.WS_EX_TRANSPARENT:
                return False
            if (ex_style & win32con.WS_EX_TOOLWINDOW) and not (ex_style & win32con.WS_EX_APPWINDOW):
                return False

            cls_name = win32gui.GetClassName(hwnd)
            if cls_name in (
                "Progman",
                "WorkerW",
                "Shell_TrayWnd",
                "Shell_SecondaryTrayWnd",
                "DummyDWMListenerWindow",
                "Windows.UI.Core.CoreWindow",
                "ime",
                "MSCTFIME UI",
                "CiceroUIWndFrame",
            ):
                return False

            cloaked = ctypes.c_int(0)
            dwmapi.DwmGetWindowAttribute(
                int(hwnd),
                DWMWA_CLOAKED,
                ctypes.byref(cloaked),
                ctypes.sizeof(cloaked),
            )
            if cloaked.value != 0:
                return False

            x1, y1, x2, y2 = cls._get_visible_window_bounds(hwnd)
            if (x2 - x1) <= 60 or (y2 - y1) <= 40:
                return False

            return True
        except Exception:
            return False

    @classmethod
    def _get_z_order_and_occluders(
        cls,
        target_hwnds: List[int],
        overlay_hwnds: set,
    ) -> Tuple[List[int], Dict[int, List[Tuple[int, int, int, int]]]]:
        """
        Percorre a ordem Z global do Windows (do topo mais à frente para o fundo mais atrás):
        - Para cada janela do Roblox em 'target_hwnds', registra TODAS as janelas reais visíveis
          que vieram ANTES dela na ordem Z.
        """
        target_set = set(target_hwnds)
        ordered_targets: List[int] = []
        occluders_per_target: Dict[int, List[Tuple[int, int, int, int]]] = {h: [] for h in target_hwnds}
        front_rects_so_far: List[Tuple[int, int, int, int]] = []

        def _enum_cb(hwnd, _):
            if hwnd in overlay_hwnds:
                return True

            if hwnd in target_set:
                if hwnd not in ordered_targets:
                    ordered_targets.append(hwnd)
                    occluders_per_target[hwnd] = list(front_rects_so_far)
                    try:
                        if win32gui.IsWindowVisible(hwnd) and not win32gui.IsIconic(hwnd):
                            front_rects_so_far.append(cls._get_visible_window_bounds(hwnd))
                    except Exception:
                        pass
                return True

            if cls._is_real_occluding_window(hwnd, overlay_hwnds):
                front_rects_so_far.append(cls._get_visible_window_bounds(hwnd))

            return True

        try:
            win32gui.EnumWindows(_enum_cb, None)
        except Exception:
            pass

        for h in target_hwnds:
            if h not in ordered_targets:
                ordered_targets.append(h)

        return ordered_targets, occluders_per_target

    def render_all(self, snapshots: List[WindowVisionSnapshot], bot):
        if not self.enabled:
            return

        active_hwnds = [s.hwnd for s in snapshots]
        for old_h in list(self.overlays.keys()):
            if old_h not in active_hwnds:
                self.overlays[old_h].destroy()
                del self.overlays[old_h]

        snap_map = {s.hwnd: s for s in snapshots}
        for hwnd in active_hwnds:
            if hwnd not in self.overlays:
                self.overlays[hwnd] = SingleWindowOverlay(self.root, hwnd)

        now = time.time()
        if (now - self._last_z_check_ts) >= 0.65 or set(self._cached_z_ordered) != set(active_hwnds):
            self._last_z_check_ts = now
            overlay_hwnd_set = set()
            for ov in self.overlays.values():
                if ov._overlay_hwnd:
                    overlay_hwnd_set.add(ov._overlay_hwnd)
                if ov.win is not None:
                    try:
                        overlay_hwnd_set.add(int(ov.win.winfo_id()))
                    except Exception:
                        pass
            self._cached_z_ordered, self._cached_occluders = self._get_z_order_and_occluders(
                active_hwnds, overlay_hwnd_set
            )

        for hwnd in self._cached_z_ordered:
            snap = snap_map.get(hwnd)
            if not snap or hwnd not in self.overlays:
                continue
            occ_rects = self._cached_occluders.get(hwnd, [])
            self.overlays[hwnd].render(snap, bot, occluding_rects=occ_rects)


class LiveMiniMonitor:
    """
    Modo 2: Mini-Monitor Ao Vivo Multi-Janelas (1..N).
    Exibe TODAS as janelas abertas do Roblox simultaneamente em mosaico com posicionamento
    100% fiel às coordenadas exatas do VisionReader.
    """

    def __init__(self, root: tk.Tk, on_close_cb=None):
        self.root = root
        self.enabled = False
        self.on_close_cb = on_close_cb
        self.win: Optional[tk.Toplevel] = None
        self.lbl_img: Optional[tk.Label] = None
        self.lbl_info: Optional[tk.Label] = None
        self._photo: Optional[ImageTk.PhotoImage] = None

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        if self.enabled:
            self._ensure_window()
        else:
            self._destroy_window()

    def _ensure_window(self):
        if self.win is not None and self.win.winfo_exists():
            return
        self.win = tk.Toplevel(self.root)
        self.win.title(tr("PT-BR", "chk_mini_monitor"))
        self.win.geometry("760x480")
        self.win.configure(bg="#090D16")
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self._on_user_close)

        top = tk.Frame(self.win, bg="#111827", padx=8, pady=5)
        top.pack(fill="x")
        self.lbl_info = tk.Label(
            top,
            text="📺 Aguardando captura das janelas do Roblox...",
            bg="#111827",
            fg="#38BDF8",
            font=("Consolas", 9, "bold"),
            anchor="w",
        )
        self.lbl_info.pack(fill="x")

        self.lbl_img = tk.Label(self.win, bg="#090D16")
        self.lbl_img.pack(fill="both", expand=True, padx=6, pady=6)

    def _on_user_close(self):
        self.enabled = False
        self._destroy_window()
        if self.on_close_cb:
            try:
                self.on_close_cb()
            except Exception:
                pass

    def _destroy_window(self):
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
        self.win = None
        self.lbl_img = None
        self.lbl_info = None
        self._photo = None

    def _annotate_single_window(self, snap: WindowVisionSnapshot, bot) -> Optional[np.ndarray]:
        if snap.frame_bgr is None or snap.frame_bgr.size == 0:
            return None

        vis = snap.frame_bgr.copy()
        h, w = vis.shape[:2]

        role_str = f"#{snap.index} PRIMARY" if snap.is_primary else f"#{snap.index} SECONDARY"
        hdr_col = (0, 215, 255) if snap.is_primary else (255, 190, 40)
        cv2.rectangle(vis, (0, 0), (w - 1, h - 1), hdr_col, 3)

        if snap.auto_claim_quest and snap.quest_details is not None:
            (qx, qy, qw, qh), (bx, by, bw, bh), is_ready, g_rat = snap.quest_details
            if is_ready:
                cv2.rectangle(vis, (qx, qy), (qx + qw, qy + qh), (16, 230, 100), 3)
                cv2.rectangle(vis, (bx, by), (bx + bw, by + bh), (16, 230, 100), 3)
                cv2.putText(
                    vis,
                    "QUEST READY -> [C]",
                    (qx, min(h - 10, qy + qh + 18)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.52,
                    (16, 230, 100),
                    2,
                )
            else:
                cv2.rectangle(vis, (qx, qy), (qx + qw, qy + qh), (235, 180, 20), 2)
                cv2.rectangle(vis, (bx, by), (bx + bw, by + bh), (235, 180, 20), 2)
                cv2.putText(
                    vis,
                    "Quest Monitor [C]",
                    (qx, min(h - 10, qy + qh + 16)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.46,
                    (235, 180, 20),
                    2,
                )

        if snap.checklist_rect[2] > 0:
            cx, cy, cw, ch = snap.checklist_rect
            checks_done = sum(1 for x in snap.tier_checks if x)
            if snap.auto_eggs or snap.auto_move_altar:
                chk_col = (16, 215, 110) if checks_done == 8 else (20, 175, 250)
                lbl_txt = f"Checks: {checks_done}/8"
            else:
                chk_col = (140, 140, 140)
                lbl_txt = f"Checks: {checks_done}/8 (Modo Leve)"

            cv2.rectangle(vis, (cx, cy), (cx + cw, cy + ch), chk_col, 2)
            cv2.putText(
                vis,
                lbl_txt,
                (cx, max(20, cy - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                chk_col,
                2,
            )
            for idx, ry in enumerate(snap.checklist_row_ys):
                done = snap.tier_checks[idx] if idx < len(snap.tier_checks) else False
                dot_col = (16, 235, 100) if done else (90, 100, 120)
                cv2.circle(vis, (cx + 12, ry), 5, dot_col, -1)

        if snap.egg_details is not None:
            ecx, ecy, er, ename = snap.egg_details
            ecol = (240, 90, 220) if snap.auto_eggs else (140, 140, 140)
            cv2.rectangle(vis, (ecx - er, ecy - er), (ecx + er, ecy + er), ecol, 2)
            cv2.putText(
                vis,
                f"Egg: {ename} [Z]",
                (max(6, ecx - er), max(22, ecy - er - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                ecol,
                2,
            )

        # 5. MESA BRANCA do Altar e Waypoints (mostrando tochas já acesas!)
        if snap.table_rect is not None:
            rx, ry, rw, rh, tscore = snap.table_rect
            cv2.rectangle(
                vis,
                (int(rx), int(ry)),
                (int(rx + rw), int(ry + rh)),
                (248, 189, 56),
                2,
            )
            cv2.putText(
                vis,
                f"Mesa Branca ({tscore:.0%})",
                (int(rx), max(22, int(ry - 6))),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.48,
                (248, 189, 56),
                2,
            )

        if snap.altar_pts:
            for k in ("SW", "NW", "NE", "SE", "P0"):
                if k in snap.altar_pts:
                    px, py = snap.altar_pts[k]
                    is_lit = snap.torch_states.get(k) is True
                    if k == "P0" or is_lit:
                        col = (16, 230, 110)
                        lbl_k = f"{k}(LIT)" if is_lit else k
                    else:
                        col = (22, 130, 255)
                        lbl_k = k
                    cv2.circle(vis, (px, py), 8, col, 2)
                    cv2.putText(vis, lbl_k, (px + 10, py + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 2)

        if snap.prompt_type:
            px, py = snap.prompt_pos
            if px > 0 and py > 0:
                cv2.circle(vis, (px, py), 22, (180, 72, 245), 3)
                cv2.putText(
                    vis,
                    f"[E] {snap.prompt_type}",
                    (max(10, px - 40), max(22, py - 26)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.54,
                    (180, 72, 245),
                    2,
                )

        if snap.pet_continue_hit is not None:
            pcx, pcy, _, _, pconf = snap.pet_continue_hit
            cv2.rectangle(vis, (pcx - 110, pcy - 32), (pcx + 110, pcy + 32), (16, 230, 110), 3)
            cv2.putText(
                vis,
                f"CONTINUE [E] ({pconf:.0%})",
                (max(10, pcx - 95), max(22, pcy - 38)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.54,
                (16, 230, 110),
                2,
            )

        if snap.disconnect_hit is not None:
            dx, dy, _, _, dconf = snap.disconnect_hit
            cv2.rectangle(vis, (dx - 70, dy - 20), (dx + 70, dy + 20), (68, 68, 245), 3)
            cv2.putText(
                vis,
                f"RECONNECT ({dconf:.0%})",
                (max(10, dx - 65), max(22, dy - 26)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.54,
                (68, 68, 245),
                2,
            )

        feats = []
        if snap.auto_eggs:
            feats.append("Eggs[Z]")
        if snap.auto_move_altar:
            feats.append("Altar")
        if snap.auto_claim_quest:
            feats.append("Quest[C]")
        if snap.auto_anti_afk:
            feats.append("AFK[C+Q]")
        if snap.auto_reconnect:
            feats.append("Recon")
        banner_txt = f"{role_str} (ID:{snap.hwnd}) | {'+'.join(feats)}"
        cv2.rectangle(vis, (4, 4), (min(w - 4, 14 + len(banner_txt) * 8), 28), (15, 23, 42), -1)
        cv2.putText(vis, banner_txt, (10, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.46, hdr_col, 1)

        return vis

    def render_all(self, snapshots: List[WindowVisionSnapshot], bot, lang: str = "PT-BR"):
        if not self.enabled:
            return
        self._ensure_window()
        if self.win is None or self.lbl_img is None:
            return

        self.win.title(tr(lang, "chk_mini_monitor"))

        annotated_frames = []
        for s in snapshots:
            ann = self._annotate_single_window(s, bot)
            if ann is not None:
                annotated_frames.append((s, ann))

        if not annotated_frames:
            if self.lbl_info:
                self.lbl_info.config(text="⚠️ Nenhuma janela do Roblox capturada no momento.")
            return

        avail_w = max(360, self.win.winfo_width() - 16)
        avail_h = max(240, self.win.winfo_height() - 48)
        n = len(annotated_frames)

        cols = 1 if n == 1 else (2 if n <= 4 else 3)
        rows = (n + cols - 1) // cols
        cell_w = max(180, (avail_w - (cols - 1) * 6) // cols)
        cell_h = max(140, (avail_h - (rows - 1) * 6) // rows)

        mosaic = np.full((rows * cell_h + (rows - 1) * 6, cols * cell_w + (cols - 1) * 6, 3), 12, dtype=np.uint8)

        for idx, (_, img) in enumerate(annotated_frames):
            r = idx // cols
            c = idx % cols
            ih, iw = img.shape[:2]
            scale = min(cell_w / float(iw), cell_h / float(ih))
            nw, nh = max(80, int(iw * scale)), max(60, int(ih * scale))
            resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)

            y0 = r * (cell_h + 6) + (cell_h - nh) // 2
            x0 = c * (cell_w + 6) + (cell_w - nw) // 2
            mosaic[y0 : y0 + nh, x0 : x0 + nw] = resized

        rgb = cv2.cvtColor(mosaic, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        self._photo = ImageTk.PhotoImage(pil_img)
        self.lbl_img.config(image=self._photo)

        if self.lbl_info:
            st = bot.status
            summary = " | ".join(
                f"#{s.index}({'⭐' if s.is_primary else '🔹'} Checks:{sum(1 for x in s.tier_checks if x)}/8)"
                for s, _ in annotated_frames
            )
            self.lbl_info.config(
                text=f"📺 Monitorando {n} Janela(s) Roblox em Tempo Real | Estado: {st.state.value} | {summary}"
            )


class VisualIndicatorManager:
    """
    Coordena a leitura visual precisa para TODAS as janelas abertas do Roblox (1..N) em uma
    Thread de Background dedicada (Zero-Lag na interface Tkinter!):
    - Reaproveita frames recém-capturados pelo bot (`bot.latest_frames`) para nunca duplicar PrintWindow.
    - Suporta atualização instantânea (`notify_immediate_frame`) durante a caminhada do Altar para que
      os waypoints e a Mesa Branca atualizem imediatamente após cada movimento!
    """

    def __init__(self, root: tk.Tk, bot, on_monitor_closed_cb=None):
        self.root = root
        self.bot = bot
        self.lang = "PT-BR"
        self.snapshots: List[WindowVisionSnapshot] = []
        self._snap_lock = threading.Lock()
        self._wake_event = threading.Event()
        self._dirty_render = False
        self.overlay_hud = MultiWindowOverlayHUD(root)
        self.mini_monitor = LiveMiniMonitor(root, on_close_cb=on_monitor_closed_cb)

        # Conecta callback de frame ao vivo do bot para atualização imediata da posição/overlay no Altar
        if hasattr(self.bot, "set_live_frame_callback"):
            self.bot.set_live_frame_callback(self.notify_immediate_frame)

        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    @property
    def any_enabled(self) -> bool:
        return self.overlay_hud.enabled or self.mini_monitor.enabled

    def set_overlay_enabled(self, enabled: bool):
        self.overlay_hud.set_enabled(enabled)
        if enabled:
            self._wake_event.set()

    def set_monitor_enabled(self, enabled: bool):
        self.mini_monitor.set_enabled(enabled)
        if enabled:
            self._wake_event.set()

    def notify_immediate_frame(self, hwnd: int, frame: np.ndarray):
        """
        Acionado imediatamente pelo bot quando um movimento no Altar termina ou um frame novo é estabilizado,
        acordando a thread dedicada da Overlay sem travar a thread de movimento do bot!
        """
        if not self.any_enabled or frame is None:
            return
        self._wake_event.set()

    def _render_on_ui_thread(self):
        if not self.any_enabled:
            return
        with self._snap_lock:
            snaps = list(self.snapshots)
            self._dirty_render = False
        self.overlay_hud.render_all(snaps, self.bot)
        if self.mini_monitor.enabled:
            self.mini_monitor.render_all(snaps, self.bot, lang=self.lang)

    def update(self, lang: str = "PT-BR"):
        self.lang = lang
        if not self.any_enabled:
            return
        self._render_on_ui_thread()

    def _worker_loop(self):
        while True:
            if not self.any_enabled:
                self._wake_event.wait(timeout=0.40)
                self._wake_event.clear()
                continue

            t0 = time.time()
            try:
                self._refresh_all_snapshots()
                if self.any_enabled:
                    try:
                        self.root.after(0, self._render_on_ui_thread)
                    except Exception:
                        pass
            except Exception:
                pass

            elapsed = time.time() - t0
            wait_sec = max(0.06, 0.22 - elapsed)
            self._wake_event.wait(timeout=wait_sec)
            self._wake_event.clear()

    def _build_snapshot_for_window(
        self, prof, provided_frame: Optional[np.ndarray] = None
    ) -> Optional[WindowVisionSnapshot]:
        hwnd = prof.hwnd
        if not win32gui.IsWindow(hwnd) or win32gui.IsIconic(hwnd):
            return None
        try:
            win_left, win_top, win_right, win_bottom = win32gui.GetWindowRect(hwnd)
            cl_left, cl_top = win32gui.ClientToScreen(hwnd, (0, 0))
            _, _, cw, ch = win32gui.GetClientRect(hwnd)
        except Exception:
            return None

        if cw <= 100 or ch <= 100:
            return None

        frame = provided_frame
        if frame is None:
            # Reaproveita frame recente já capturado pelo bot (< 0.26s) para evitar duplo PrintWindow!
            latest_map = getattr(self.bot, "latest_frames", None)
            if isinstance(latest_map, dict) and hwnd in latest_map:
                ts_f, cached_f = latest_map[hwnd]
                if (time.time() - ts_f) <= 0.26 and cached_f is not None and cached_f.size > 0:
                    frame = cached_f

        if frame is None:
            frame = self.bot.vision.capture_window(hwnd)
        if frame is None or frame.size == 0:
            return None

        snap = WindowVisionSnapshot(hwnd=hwnd, index=prof.index, is_primary=prof.is_primary)
        snap.screen_x = win_left
        snap.screen_y = win_top
        snap.width = int(frame.shape[1])
        snap.height = int(frame.shape[0])
        snap.win_rect = (win_left, win_top, win_right, win_bottom)
        snap.title_bar_h = max(0, cl_top - win_top)

        snap.auto_interact_e = prof.auto_interact_e
        snap.auto_eggs = prof.auto_eggs
        snap.auto_move_altar = prof.auto_move_altar
        snap.auto_claim_quest = prof.auto_claim_quest
        snap.auto_anti_afk = prof.auto_anti_afk
        snap.auto_reconnect = prof.auto_reconnect
        if self.mini_monitor.enabled:
            snap.frame_bgr = frame

        mon_on = self.mini_monitor.enabled
        try:
            if prof.auto_claim_quest or mon_on:
                snap.quest_details = self.bot.vision.locate_quest_banner_details(frame)

            if prof.auto_eggs or prof.auto_move_altar or mon_on:
                chk_rect, row_ys, tiers = self.bot.vision.locate_checklist_details(frame)
                snap.checklist_rect = chk_rect
                snap.checklist_row_ys = row_ys
                snap.tier_checks = tiers

            if prof.auto_eggs or mon_on:
                snap.egg_details = self.bot.vision.locate_egg_button_details(frame)

            if prof.auto_move_altar or prof.auto_interact_e or mon_on:
                ptype, pscore, ppos = self.bot.vision.detect_prompt_type(frame)
                snap.prompt_type = ptype
                snap.prompt_score = pscore
                snap.prompt_pos = ppos
                trect = self.bot.vision.detect_altar_table_rect(frame)
                snap.table_rect = trect
                if trect is not None:
                    snap.table_hit = (trect[0], trect[1], trect[4])
                    snap.altar_pts = self.bot.vision.detect_altar_points(frame, table_rect=trect)
                    snap.torch_states = self.bot.vision.detect_torch_states(frame, table_rect=trect)

            snap.pet_continue_hit = self.bot.vision.detect_pet_continue_screen(hwnd, frame)

            if prof.auto_reconnect or mon_on:
                snap.disconnect_hit = self.bot.vision.detect_disconnect_dialog(hwnd, frame)
        except Exception:
            pass

        return snap

    def _refresh_all_snapshots(self):
        profiles = self.bot.sync_windows()
        new_snaps: List[WindowVisionSnapshot] = []

        for prof in profiles:
            snap = self._build_snapshot_for_window(prof)
            if snap is not None:
                new_snaps.append(snap)

        with self._snap_lock:
            self.snapshots = new_snaps
            self._dirty_render = True
