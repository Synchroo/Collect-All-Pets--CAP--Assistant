"""
CAP Rebirth Bot - Core Engine v3.5
Mantém 100% do funcionamento validado da v3.4 + Auto-Correção Inteligente da Rotação de Ovos:
- Usa a sequência da lista do Checklist como referência absoluta.
- Caso o ovo ativo na barra inferior fique fora de sincronia com o tier atual do Checklist
  (por falha de clique, lag do servidor ou intervenção manual), corrige automaticamente
  clicando passo a passo até alinhar com o ovo correto da lista.
"""

import time
import os
import sys
import ctypes
import threading
import logging
import cv2
import numpy as np
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional, Tuple, Callable, List
import win32gui
import win32service
import keyboard

from bot_vision import VisionReader, PET_NAMES, EGG_CYCLE, EGG_BAR_CYCLE
from bot_input import DirectInputController

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("CAPCore")


class BotState(Enum):
    IDLE          = auto()
    COUNTDOWN     = auto()
    EGG_FARMING   = auto()
    SYNC_EGG      = auto()
    TORCH_PHASE   = auto()
    REBIRTH_PHASE = auto()
    PAUSED        = auto()


@dataclass
class WindowProfile:
    """Configuração e estado independente para cada janela do Roblox aberta."""
    hwnd:            int
    index:           int  = 1
    title:           str  = "Roblox"
    rect: Tuple[int, int, int, int] = (0, 0, 800, 600)
    is_primary:      bool = False
    auto_interact_e: bool = False  # ⚡ [E] Light & Rebirth (Desativado por padrão)
    auto_eggs:       bool = False  # 🥚 Trocar Ovos [Z] (Desativado por padrão)
    auto_move_altar: bool = False  # 🏛️ Andar nas 4 Tochas + Mesa (Desativado por padrão)
    auto_claim_quest:bool = True   # 🏆 Coletar Missão [C] (Ativo no Modo Básico Padrão)
    auto_anti_afk:   bool = True   # 🛡️ Anti-AFK [C]+[Q] (Ativo no Modo Básico Padrão)
    auto_reconnect:  bool = True   # 🔌 Auto-Reconectar se cair (Ativo no Modo Básico Padrão)
    current_tier_index: int = 0
    quests_claimed:  int  = 0
    rebirth_count:   int  = 0
    reconnect_count: int  = 0
    quest_ready_streak: int = 0
    last_quest_claim_ts: float = 0.0
    last_afk_reset_ts: float = 0.0

    @property
    def pos_tag(self) -> str:
        x, y, r, b = self.rect
        w, h = r - x, b - y
        return f"#{self.index} [ID:{self.hwnd}] ({x},{y} {w}x{h})"

    @property
    def rebirth_mode(self) -> str:
        """Retorna 'full' (Altar+E+Ovos), 'semi' (E+Ovos, mov. manual) ou 'off' (Modo Leve)."""
        if self.auto_move_altar:
            return "full"
        if self.auto_interact_e and self.auto_eggs:
            return "semi"
        return "off"

    def set_rebirth_mode(self, mode: str):
        """Aplica modo de Rebirth ('off', 'semi' ou 'full') nesta janela."""
        m = (mode or "off").strip().lower()
        if m == "full":
            self.auto_interact_e = True
            self.auto_eggs = True
            self.auto_move_altar = True
        elif m == "semi":
            self.auto_interact_e = True
            self.auto_eggs = True
            self.auto_move_altar = False
        else:
            self.auto_interact_e = False
            self.auto_eggs = False
            self.auto_move_altar = False


@dataclass
class BotConfig:
    hotkey_toggle:         str   = "f8"
    hotkey_pause:          str   = "f9"
    hotkey_e:              str   = "f7"    # Acionar [E] manualmente
    hotkey_rebirth:        str   = "f6"    # Segurar [E] manualmente
    auto_eggs:             bool  = False   # Troca e auto-correção de ovos pelo Checklist (Hotkey [Z])
    auto_interact_e:       bool  = False   # Interação automática com [E] Light e [E] Rebirth (Hotkey [E])
    auto_move_altar:       bool  = False   # Movimentação automática (Click-to-Move) nas 4 Tochas + Mesa após Mythical
    auto_claim_quest:      bool  = True    # Coleta automática de missão completa no banner superior (Hotkey [C])
    auto_anti_afk:         bool  = True    # Sistema Anti-AFK Multi-Telas (Roblox Account Manager)
    auto_reconnect:        bool  = True    # Auto-Reconectar ao detectar tela Desconectado / Disconnected
    anti_afk_interval_sec: float = 120.0   # Intervalo configurável (s) para alternar 1º plano nas janelas secundárias
    auto_pause_on_mouse:   bool  = True    # Pausar automaticamente quando o usuário usar o PC (Teclado/Mouse)
    mouse_idle_resume_sec: float = 60.0    # Segundos de PC ocioso (sem Teclado/Mouse) para retomar o bot
    rebirth_hold_time:     float = 4.6     # Tempo base mínimo segurando [E] no Rebirth
    screen_check_hz:       float = 0.20    # Intervalo configurável de leitura da tela (padrão 0.20s = 5x/seg)
    notif_tray_minimize:   bool  = False   # Balão ao minimizar para a Bandeja (Desativado por padrão para não poluir)
    notif_rebirth:         bool  = False   # Balão ao concluir cada Renascimento (Desativado por padrão)
    notif_reconnect:       bool  = True    # Balão ao reconectar de queda/desconexão (Ativo por padrão)
    notif_afk_warn:        bool  = True    # Balão de alerta Anti-AFK 15/18 min antes de cair (Ativo por padrão)


@dataclass
class BotStatus:
    state:               BotState = BotState.IDLE
    running:             bool     = False
    paused:              bool     = False
    auto_paused_mouse:   bool     = False
    user_in_game:        bool     = False
    mouse_idle_remaining:int      = 0
    roblox_hwnd:         int      = 0
    roblox_title:        str      = ""
    roblox_window_count: int      = 0
    rebirth_count:       int      = 0
    quests_claimed:      int      = 0
    anti_afk_pulses:     int      = 0
    reconnect_count:     int      = 0
    current_tier_index:  int      = 0
    current_tier_name:   str      = "Common"
    detected_active_egg: str      = "Common"
    checkmarks_found:    int      = 0
    torches_lit:         int      = 0
    last_action:         str      = "Aguardando início..."
    start_time: Optional[float]   = None
    tier_status: List[bool]       = field(default_factory=lambda: [False] * 8)

    @property
    def uptime_str(self) -> str:
        if self.start_time is None:
            return "00:00:00"
        elapsed = int(time.time() - self.start_time)
        h, r = divmod(elapsed, 3600)
        m, s = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


class RobloxFinder:
    """Localiza todas as janelas do cliente Roblox (suporte a múltiplos monitores e Roblox Account Manager)."""

    @staticmethod
    def get_all_windows() -> List[Tuple[int, str, Tuple[int, int, int, int]]]:
        windows = []
        seen_hwnds = set()

        def enum_cb(hwnd, _):
            if hwnd in seen_hwnds or not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
            cls = win32gui.GetClassName(hwnd) or ""

            if cls == "WINDOWSCLIENT":
                rect = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                if w > 200 and h > 200:
                    seen_hwnds.add(hwnd)
                    windows.append((hwnd, title, rect))
            elif (
                "roblox" in title.lower()
                and cls not in ("Chrome_WidgetWin_1", "MozillaWindowClass")
                and "manager" not in title.lower()
                and "rebirth bot" not in title.lower()
                and "cap assistant" not in title.lower()
            ):
                rect = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                if w > 200 and h > 200:
                    seen_hwnds.add(hwnd)
                    windows.append((hwnd, title, rect))

        try:
            GENERIC_ALL = 0x10000000
            hwinsta = win32service.OpenWindowStation("WinSta0", False, GENERIC_ALL)
            hwinsta.SetProcessWindowStation()
            hdesk = win32service.OpenDesktop("Default", 0, False, GENERIC_ALL)
            win32gui.EnumDesktopWindows(hdesk, enum_cb, None)
        except Exception:
            pass

        if not windows:
            try:
                win32gui.EnumWindows(enum_cb, None)
            except Exception:
                pass

        # Ordena de forma determinística por posição na tela (Topo->Baixo, Esq->Dir, HWND)
        windows.sort(key=lambda w: (w[2][1] // 80, w[2][0] // 80, w[0]))
        return windows

    @staticmethod
    def get_primary_window() -> Optional[Tuple[int, str, Tuple[int, int, int, int]]]:
        windows = RobloxFinder.get_all_windows()
        if not windows:
            return None

        cur_fg = win32gui.GetForegroundWindow()
        for w in windows:
            if w[0] == cur_fg:
                return w

        return windows[0]

    @staticmethod
    def is_roblox_window(hwnd: int) -> bool:
        """Verifica se o HWND informado pertence a uma janela de jogo do Roblox."""
        if not hwnd or not win32gui.IsWindow(hwnd):
            return False
        try:
            cls = win32gui.GetClassName(hwnd) or ""
            if cls == "WINDOWSCLIENT":
                return True
            title = (win32gui.GetWindowText(hwnd) or "").lower()
            if (
                "roblox" in title
                and cls not in ("Chrome_WidgetWin_1", "MozillaWindowClass")
                and "manager" not in title
                and "rebirth bot" not in title
                and "cap assistant" not in title
            ):
                return True
        except Exception:
            pass
        return False


class RebirthBot:
    """Orquestrador Multi-Janelas de Ovos [Z], Tochas ('[E] Light'), Renascimento ('[E] Rebirth'), Missões [C] e Anti-AFK."""

    def __init__(self, config: BotConfig = None):
        self.cfg        = config or BotConfig()
        self.status     = BotStatus()
        self.controller = DirectInputController()
        self.vision     = VisionReader()
        self._stop      = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._callbacks: List[Callable] = []
        self._event_callbacks: List[Callable] = []
        self.window_profiles: dict[int, WindowProfile] = {}
        self.saved_index_profiles: dict[int, dict] = {}
        self._last_user_activity_ts: float = 0.0
        self._hotkey_refs: list = []
        self.latest_frames: dict[int, Tuple[float, np.ndarray]] = {}
        self._live_frame_cb: Optional[Callable] = None

        self._register_hotkeys()

    def set_live_frame_callback(self, fn: Optional[Callable]):
        """Registra callback fn(hwnd, frame) para atualização imediata das Overlays durante movimentos."""
        self._live_frame_cb = fn

    def push_live_frame(self, hwnd: int, frame: Optional[np.ndarray], immediate_overlay: bool = False):
        """Armazena o último frame capturado (evitando PrintWindow duplicado na Overlay) e notifica HUD se solicitado."""
        if frame is None or not hwnd:
            return
        self.latest_frames[hwnd] = (time.time(), frame)
        if immediate_overlay and self._live_frame_cb is not None:
            try:
                self._live_frame_cb(hwnd, frame)
            except Exception:
                pass

    def add_event_callback(self, fn: Callable):
        """Registra callback fn(event_type, title, message) para notificações na bandeja (System Tray)."""
        self._event_callbacks.append(fn)

    def _emit_event(self, event_type: str, title: str, message: str):
        for fn in self._event_callbacks:
            try:
                fn(event_type, title, message)
            except Exception:
                pass

    def _register_hotkeys(self):
        for ref in self._hotkey_refs:
            try:
                keyboard.remove_hotkey(ref)
            except Exception:
                pass
        self._hotkey_refs.clear()
        try:
            if self.cfg.hotkey_toggle:
                self._hotkey_refs.append(keyboard.add_hotkey(self.cfg.hotkey_toggle, self.toggle))
            if self.cfg.hotkey_pause:
                self._hotkey_refs.append(keyboard.add_hotkey(self.cfg.hotkey_pause, self.pause_resume))
            if self.cfg.hotkey_e:
                self._hotkey_refs.append(keyboard.add_hotkey(self.cfg.hotkey_e, self.test_press_e))
            if self.cfg.hotkey_rebirth:
                self._hotkey_refs.append(keyboard.add_hotkey(self.cfg.hotkey_rebirth, self.test_rebirth_hold))
        except Exception as e:
            log.warning(f"Não foi possível registrar hotkeys: {e}")

    def update_hotkeys(self, toggle_hk: str, pause_hk: str) -> Tuple[bool, str]:
        """Atualiza em tempo real os atalhos de Iniciar/Parar e Pausar/Retomar (suporta tecla única ou combinação)."""
        t_clean = (toggle_hk or "f8").strip().lower()
        p_clean = (pause_hk or "f9").strip().lower()
        if t_clean == p_clean:
            return False, "Os atalhos de Iniciar/Parar e Pausar não podem ser iguais!"

        old_t, old_p = self.cfg.hotkey_toggle, self.cfg.hotkey_pause
        self.cfg.hotkey_toggle = t_clean
        self.cfg.hotkey_pause = p_clean
        try:
            for ref in self._hotkey_refs:
                try:
                    keyboard.remove_hotkey(ref)
                except Exception:
                    pass
            self._hotkey_refs.clear()
            self._hotkey_refs.append(keyboard.add_hotkey(self.cfg.hotkey_toggle, self.toggle))
            self._hotkey_refs.append(keyboard.add_hotkey(self.cfg.hotkey_pause, self.pause_resume))
            if self.cfg.hotkey_e and self.cfg.hotkey_e not in (t_clean, p_clean):
                self._hotkey_refs.append(keyboard.add_hotkey(self.cfg.hotkey_e, self.test_press_e))
            if self.cfg.hotkey_rebirth and self.cfg.hotkey_rebirth not in (t_clean, p_clean):
                self._hotkey_refs.append(keyboard.add_hotkey(self.cfg.hotkey_rebirth, self.test_rebirth_hold))
            return True, f"Atalhos atualizados: Play/Stop=[{t_clean.upper()}] | Pausa=[{p_clean.upper()}]"
        except Exception as e:
            self.cfg.hotkey_toggle, self.cfg.hotkey_pause = old_t, old_p
            self._register_hotkeys()
            return False, f"Atalho inválido ({e})"

    def sync_windows(self) -> List[WindowProfile]:
        """
        Sincroniza a lista de janelas abertas do Roblox (suporta infinitas janelas 1..N)
        preservando as escolhas individuais que o usuário já marcou para cada HWND.
        Por padrão, TODAS as janelas começam apenas no Modo Básico (Missão [C] + Anti-AFK [C+Q] + Reconectar),
        ou carregam o perfil salvo por índice (#1, #2, #3...) se estiver ativo.
        """
        raw_wins = RobloxFinder.get_all_windows()
        active_hwnds = [w[0] for w in raw_wins]

        # Remove janelas que foram fechadas
        for old_h in list(self.window_profiles.keys()):
            if old_h not in active_hwnds:
                del self.window_profiles[old_h]

        has_primary = any(p.is_primary for p in self.window_profiles.values())

        for idx, (hwnd, title, rect) in enumerate(raw_wins, start=1):
            if hwnd not in self.window_profiles:
                saved = self.saved_index_profiles.get(idx, {})
                if "is_primary" in saved and not has_primary and saved["is_primary"]:
                    is_pri = True
                    has_primary = True
                else:
                    is_pri = (not has_primary and idx == 1 and not any(
                        s.get("is_primary") for s in self.saved_index_profiles.values()
                    ))
                    if is_pri:
                        has_primary = True

                # Padrão: TODAS as janelas (inclusive a #1) começam apenas com Missão [C] + Anti-AFK [C+Q] + Reconectar
                self.window_profiles[hwnd] = WindowProfile(
                    hwnd=hwnd,
                    index=idx,
                    title=title,
                    rect=rect,
                    is_primary=is_pri,
                    auto_interact_e=bool(saved.get("auto_interact_e", False)),
                    auto_eggs=bool(saved.get("auto_eggs", False)),
                    auto_move_altar=bool(saved.get("auto_move_altar", False)),
                    auto_claim_quest=bool(saved.get("auto_claim_quest", True)),
                    auto_anti_afk=bool(saved.get("auto_anti_afk", True)),
                    auto_reconnect=bool(saved.get("auto_reconnect", True)),
                    last_afk_reset_ts=time.time(),
                )
            else:
                prof = self.window_profiles[hwnd]
                prof.index = idx
                prof.title = title
                prof.rect = rect

        # Garante que sempre exista 1 janela marcada como principal se houver janelas abertas
        if self.window_profiles and not any(p.is_primary for p in self.window_profiles.values()):
            first_h = active_hwnds[0]
            self.window_profiles[first_h].is_primary = True

        self.status.roblox_window_count = len(self.window_profiles)
        return [self.window_profiles[h] for h in active_hwnds if h in self.window_profiles]

    def reset_all_windows_to_basic(self):
        """Redefine todas as janelas abertas para o Padrão Básico (Missão [C] + Anti-AFK [C+Q] + Reconectar)."""
        self.saved_index_profiles.clear()
        profiles = self.sync_windows()
        for idx, prof in enumerate(profiles, start=1):
            prof.is_primary = (idx == 1)
            prof.auto_interact_e = False
            prof.auto_eggs = False
            prof.auto_move_altar = False
            prof.auto_claim_quest = True
            prof.auto_anti_afk = True
            prof.auto_reconnect = True

    def apply_saved_index_profiles(self, index_profiles: dict):
        """Aplica um dicionário de perfis por índice de janela ('1', '2', ...) às janelas abertas e futuras."""
        self.saved_index_profiles = {int(k): dict(v) for k, v in index_profiles.items() if str(k).isdigit()}
        profiles = self.sync_windows()
        has_pri = False
        for prof in profiles:
            saved = self.saved_index_profiles.get(prof.index)
            if saved:
                prof.is_primary = bool(saved.get("is_primary", prof.index == 1))
                if prof.is_primary:
                    has_pri = True
                prof.auto_interact_e = bool(saved.get("auto_interact_e", False))
                prof.auto_eggs = bool(saved.get("auto_eggs", False))
                prof.auto_move_altar = bool(saved.get("auto_move_altar", False))
                prof.auto_claim_quest = bool(saved.get("auto_claim_quest", True))
                prof.auto_anti_afk = bool(saved.get("auto_anti_afk", True))
                prof.auto_reconnect = bool(saved.get("auto_reconnect", True))
        if profiles and not has_pri:
            profiles[0].is_primary = True

    def export_index_profiles(self) -> dict:
        """Exporta as escolhas atuais de cada janela indexadas por posição (#1, #2, #3...)."""
        out = dict(self.saved_index_profiles)
        for prof in self.sync_windows():
            out[prof.index] = {
                "is_primary": prof.is_primary,
                "auto_interact_e": prof.auto_interact_e,
                "auto_eggs": prof.auto_eggs,
                "auto_move_altar": prof.auto_move_altar,
                "auto_claim_quest": prof.auto_claim_quest,
                "auto_anti_afk": prof.auto_anti_afk,
                "auto_reconnect": prof.auto_reconnect,
            }
        self.saved_index_profiles = out
        return {str(k): v for k, v in out.items()}

    def set_primary_window(self, hwnd: int):
        """Define qual janela é a Principal (foco padrão do bot)."""
        for h, prof in self.window_profiles.items():
            prof.is_primary = (h == hwnd)
        if hwnd in self.window_profiles:
            self.status.roblox_hwnd = hwnd
            self.status.roblox_title = self.window_profiles[hwnd].title
            self._notify()

    def get_primary_profile(self) -> Optional[WindowProfile]:
        self.sync_windows()
        for prof in self.window_profiles.values():
            if prof.is_primary and win32gui.IsWindow(prof.hwnd):
                return prof
        for prof in self.window_profiles.values():
            if win32gui.IsWindow(prof.hwnd):
                prof.is_primary = True
                return prof
        return None

    def add_callback(self, fn: Callable):
        self._callbacks.append(fn)

    def _notify(self):
        for fn in self._callbacks:
            try:
                fn(self.status)
            except Exception:
                pass

    def toggle(self):
        if self.status.running:
            self.stop()
        else:
            self.start()

    def pause_resume(self):
        self.status.paused = not self.status.paused
        if not self.status.paused:
            self.status.auto_paused_mouse = False
            self.status.user_in_game = False
            self.status.mouse_idle_remaining = 0
            self._last_user_activity_ts = 0.0
            self.controller.sync_cursor_baseline()
        state_txt = "PAUSADO" if self.status.paused else "RETOMADO"
        self.status.last_action = f"Assistente {state_txt}"
        log.info(f"Assistente {state_txt}")
        self._notify()

    def start(self):
        if self.status.running:
            return

        primary_prof = self.get_primary_profile()
        if not primary_prof:
            self.status.last_action = "❌ ERRO: Roblox não encontrado! Abra o jogo primeiro."
            log.error(self.status.last_action)
            self._notify()
            return

        hwnd = primary_prof.hwnd
        title = primary_prof.title
        self.status.roblox_hwnd       = hwnd
        self.status.roblox_title      = title
        self.status.running           = True
        self.status.paused            = False
        self.status.auto_paused_mouse = False
        self.status.user_in_game      = False
        self.status.mouse_idle_remaining = 0
        self._last_user_activity_ts   = 0.0
        self.status.state             = BotState.COUNTDOWN
        self.status.start_time        = time.time()
        self.status.torches_lit       = 0
        self.vision.reset_lit_torches_memory()
        self.status.last_action       = f"Iniciando na Janela #{primary_prof.index} (ID:{hwnd})..."
        self._stop.clear()
        self.controller.sync_cursor_baseline()

        log.info(
            f"🟢 CAP Assistant Ativado! Janela Principal: #{primary_prof.index} (HWND {hwnd}) | "
            f"Total de janelas Roblox: {len(self.window_profiles)}"
        )
        self._notify()

        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        log.info("🔴 CAP Assistant Desativado")
        self.status.running           = False
        self.status.paused            = False
        self.status.auto_paused_mouse = False
        self.status.user_in_game      = False
        self.status.mouse_idle_remaining = 0
        self._last_user_activity_ts   = 0.0
        self.status.state             = BotState.IDLE
        self.status.last_action       = "Parado pelo usuário"
        self._stop.set()
        self.controller.release_all()
        self._notify()

    def _is_roblox_foreground(self) -> Tuple[bool, int]:
        """
        Retorna (True, fg_hwnd) se a janela atualmente em 1º plano no Windows
        for uma janela do Roblox; caso contrário retorna (False, fg_hwnd).
        """
        try:
            fg_hwnd = int( win32gui.GetForegroundWindow() or 0 )
        except Exception:
            fg_hwnd = 0
        if not fg_hwnd:
            return False, 0
        if fg_hwnd in self.window_profiles and win32gui.IsWindow(fg_hwnd):
            return True, fg_hwnd
        return RobloxFinder.is_roblox_window(fg_hwnd), fg_hwnd

    def _check_and_handle_user_mouse(self, allow_in_game: bool = True) -> bool:
        """
        Verifica se o usuário está usando o PC (qualquer tecla digitada no teclado,
        botão direito/esquerdo segurado com mouse parado girando câmera, scroll ou movimento do mouse).
        - Se o usuário estiver FORA do jogo (outra janela em 1º plano): pausa totalmente até o PC
          ficar ocioso por mouse_idle_resume_sec segundos (ou até o usuário voltar para o jogo).
        - Se o usuário estiver DENTRO do jogo (Roblox em 1º plano):
          * Mantém ativos o Auto-Coletar Missões [C] e a Troca de Ovos [Z] (quando allow_in_game=True).
          * Bloqueia o ciclo Anti-AFK [C+Q] e a caminhada do Altar até completar o tempo de ociosidade.
        """
        if not self.cfg.auto_pause_on_mouse:
            self.status.auto_paused_mouse = False
            self.status.user_in_game = False
            self.status.mouse_idle_remaining = 0
            return True

        idle_target = max(5.0, float(self.cfg.mouse_idle_resume_sec))
        thresh = 8 if self.status.auto_paused_mouse else 15

        if self.controller.check_user_mouse_moved(threshold_px=thresh):
            was_already_paused = self.status.auto_paused_mouse
            self._last_user_activity_ts = time.time()
            self.status.auto_paused_mouse = True
            in_game_now, _ = self._is_roblox_foreground()
            self.status.user_in_game = in_game_now
            if not was_already_paused:
                self.controller.release_all()
                if in_game_now:
                    log.info(
                        f"🎮 [Usuário no Jogo] Uso detectado (Mouse/Câmera/Teclado)! "
                        f"Mantendo Missão [C] e Semi-Auto ([E] + Ovos [Z]) ativos; Anti-AFK pausado até {int(idle_target)}s sem uso."
                    )
                else:
                    log.info(
                        f"⌨️🖱️ [Auto-Pausa] Uso do PC fora do jogo detectado! "
                        f"Aguardando {int(idle_target)}s de ociosidade para retomar..."
                    )

        if not self.status.auto_paused_mouse:
            return True

        while self.status.running and not self._stop.is_set():
            if self.status.paused:
                self.status.auto_paused_mouse = False
                self.status.user_in_game = False
                self.status.mouse_idle_remaining = 0
                return True

            if self.controller.check_user_mouse_moved(threshold_px=8):
                self._last_user_activity_ts = time.time()

            elapsed_idle = time.time() - self._last_user_activity_ts
            rem = max(0, int(round(idle_target - elapsed_idle)))
            self.status.mouse_idle_remaining = rem
            in_game, _ = self._is_roblox_foreground()
            self.status.user_in_game = in_game

            if elapsed_idle >= idle_target:
                self.status.auto_paused_mouse = False
                self.status.user_in_game = False
                self.status.mouse_idle_remaining = 0
                self.controller.sync_cursor_baseline()
                log.info(
                    f"▶️ [Auto-Retomada] {int(idle_target)}s sem atividade manual! "
                    f"Retomando automação completa (incluindo Anti-AFK)..."
                )
                self.status.last_action = "▶️ Ocioso! Retomando automação completa..."
                self._notify()
                return True

            # Se a chamada atual NÃO permite execução com usuário ativo (ex: Anti-AFK ou Altar), aborta já
            if not allow_in_game:
                return False

            # Se o usuário está DENTRO do jogo, não bloqueia o loop: permite Missão [C] e Ovos [Z]!
            if in_game:
                return True

            # Usuário está FORA do jogo (usando outra janela no PC): aguarda ficar ocioso ou voltar ao jogo
            self.status.last_action = (
                f"⌨️🖱️ Auto-Pausa (PC em uso fora do jogo) — Retomando em {rem}s..."
            )
            self._notify()
            time.sleep(0.20)

        self.status.auto_paused_mouse = False
        self.status.user_in_game = False
        return False

    def _sleep(self, seconds: float, allow_in_game: bool = True) -> bool:
        elapsed = 0.0
        step = 0.05
        while elapsed < seconds:
            if self._stop.is_set() or not self.status.running:
                return False
            if not self._check_and_handle_user_mouse(allow_in_game=allow_in_game):
                return False
            while self.status.paused and self.status.running:
                time.sleep(0.1)
                if self._stop.is_set():
                    return False
                self.controller.sync_cursor_baseline()
            time.sleep(step)
            elapsed += step
        return True

    # ── Ações Unitárias e Auto-Correção (Prioridade por Hotkeys Nativas) ──────

    def _click_egg_once(self, hwnd: int, frame=None) -> bool:
        """
        Alterna 1 vez o tipo de ovo usando prioritariamente a Hotkey nativa [Z] ('Toggle Egg Type')
        do Collect All Pets (sem precisar mover o mouse na tela).
        """
        return self.controller.tap_key("z", hwnd)

    def _auto_correct_egg_to_expected(self, hwnd: int, expected_egg: str):
        """
        Corrige automaticamente a rotação de ovos passo a passo usando a Hotkey [Z] (1 toque por vez),
        conferindo o ícone do ovo na barra após cada toque até atingir 'expected_egg'.
        Para imediatamente assim que o ovo correto aparece na barra.
        """
        self.status.state = BotState.SYNC_EGG
        for step_i in range(7):
            if self._stop.is_set() or not self.status.running:
                return

            f = self.vision.capture_window(hwnd)
            if f is None:
                return

            # Se aparecer um prompt [E] Light ou [E] Rebirth durante a correção, dá prioridade a ele
            if self.cfg.auto_interact_e:
                p_type, _, _ = self.vision.detect_prompt_type(f)
                if p_type in ("LIGHT", "REBIRTH"):
                    return

            cur_egg = self.vision.detect_active_egg(f)
            self.status.detected_active_egg = cur_egg

            if cur_egg == expected_egg:
                log.info(f"✅ [Auto-Correção] Ovo sincronizado com o Checklist: {expected_egg}!")
                self._notify()
                return

            log.info(
                f"🔧 [Auto-Correção] Barra está em '{cur_egg}', mas o Checklist pede '{expected_egg}'. "
                f"Pressionando [Z] ({step_i + 1}/7)..."
            )
            self.status.last_action = (
                f"🔧 Corrigindo ovo [Z]: {cur_egg} -> {expected_egg}..."
            )
            self._notify()

            # Se após 4 tentativas alguma janela do jogo estiver bloqueando, envia [Q] (Close Window) antes de [Z]
            if step_i >= 4 and not self.status.auto_paused_mouse:
                self.controller.tap_key("q", hwnd)
                time.sleep(0.08)

            self._click_egg_once(hwnd, f)
            if not self._sleep(0.55):
                return

        # Atualiza leitura final após o ciclo
        f_end = self.vision.capture_window(hwnd)
        if f_end is not None:
            self.status.detected_active_egg = self.vision.detect_active_egg(f_end)
            self._notify()

    def _is_checklist_reset(self, frame: Optional[np.ndarray]) -> bool:
        """
        True quando o Checklist aparece zerado (0/8) E a HUD normal do jogo está visível
        (barra de Ovos ou banner de Missão). Exigir a HUD evita confundir um Checklist
        apenas encoberto (tela de Pet Descoberto, janela aberta, frame preto) com um Rebirth real.
        """
        if frame is None or frame.size == 0:
            return False
        if sum(self.vision.analyze_tiers(frame)) != 0:
            return False
        return (
            self.vision.locate_egg_button_details(frame) is not None
            or self.vision.locate_quest_banner_details(frame) is not None
        )

    def _wait_checklist_reset(self, hwnd: int, timeout: float = 2.5, required_streak: int = 2) -> bool:
        """Aguarda até 'timeout' segundos o Checklist zerar em 'required_streak' leituras consecutivas."""
        t0 = time.time()
        streak = 0
        while time.time() - t0 < timeout:
            if self._stop.is_set() or not self.status.running:
                return False
            if self._is_checklist_reset(self.vision.capture_window(hwnd)):
                streak += 1
                if streak >= required_streak:
                    return True
            else:
                streak = 0
            time.sleep(0.15)
        return False

    def _hold_e_until_rebirth_complete(self, hwnd: int) -> bool:
        """
        Mantém a tecla [E] pressionada continuamente (key_down('e')) até completar
        o círculo branco em volta da bola cinza (Foto 3) e o Checklist zerar.

        Retorna True SOMENTE se o Rebirth foi confirmado visualmente (Checklist passou de >0
        para 0/8 com a HUD visível). Retorna False se o [E] foi solto sem o Checklist zerar
        (ex.: prompt classificado errado numa tocha, personagem saiu do alcance, bot parado),
        para que o chamador NÃO conte um Rebirth falso nem troque o ovo com [Z].
        """
        pre_frame = self.vision.capture_window(hwnd)
        pre_checks = sum(self.vision.analyze_tiers(pre_frame)) if pre_frame is not None else None

        self.controller.force_foreground(hwnd)
        self.controller.key_down("e", hwnd)
        start_t = time.time()
        missing_count = 0
        reset_streak = 0
        confirmed = False

        try:
            while time.time() - start_t < 6.5:
                if self._stop.is_set() or not self.status.running:
                    break
                time.sleep(0.12)
                elapsed = time.time() - start_t

                frame = self.vision.capture_window(hwnd)
                if frame is not None and elapsed >= 3.2:
                    if self._is_checklist_reset(frame):
                        reset_streak += 1
                        if reset_streak >= 2:
                            confirmed = True
                            break
                    else:
                        reset_streak = 0

                if elapsed < self.cfg.rebirth_hold_time:
                    continue

                if frame is not None:
                    p_type, _, _ = self.vision.detect_prompt_type(frame)
                    if p_type in ("HOLDING", "REBIRTH"):
                        missing_count = 0
                    else:
                        missing_count += 1
                        if missing_count >= 2:
                            break
        finally:
            self.controller.key_up("e")

        # O Checklist pode levar alguns instantes para zerar depois que o círculo completa
        if not confirmed:
            confirmed = self._wait_checklist_reset(hwnd, timeout=2.5)

        # Checklist já estava zerado antes de segurar [E]: "zerado depois" não prova nada
        if confirmed and pre_checks == 0:
            log.warning(
                "⚠️ [Rebirth] Checklist já estava 0/8 antes de segurar [E] — "
                "não é possível confirmar o Renascimento visualmente."
            )
            confirmed = False

        if not confirmed:
            log.warning(
                "⚠️ [Rebirth] [E] solto sem o Checklist zerar — Renascimento NÃO confirmado "
                "(contador e troca de ovo [Z] mantidos)."
            )
        return confirmed

    def _execute_post_rebirth_switch(self, hwnd: int):
        """
        Aguarda o Checklist zerar após o Rebirth e dá 1 clique no botão de ovo
        para sair de Ascended para Common.
        """
        prof = self.window_profiles.get(hwnd)
        if prof is not None:
            prof.rebirth_count += 1
        self.status.rebirth_count += 1
        self.status.torches_lit = 0
        self.vision.reset_lit_torches_memory()
        log.info(f"🎉 Renascimento #{self.status.rebirth_count} concluído!")
        self.status.last_action = (
            f"🎉 Renascimento #{self.status.rebirth_count} concluído! "
            f"Trocando ovo 1x (Ascended -> Common)..."
        )
        self._notify()
        self._emit_event(
            "rebirth",
            "CAP Assistant — Renascimento Concluído! 🎉",
            f"Renascimento #{self.status.rebirth_count} realizado com sucesso! Reiniciando ciclo em Common.",
        )

        wait_start = time.time()
        while time.time() - wait_start < 2.5:
            if self._stop.is_set() or not self.status.running:
                return
            f_chk = self.vision.capture_window(hwnd)
            if f_chk is not None and sum(self.vision.analyze_tiers(f_chk)) == 0:
                break
            time.sleep(0.15)

        if not self._sleep(0.5):
            return

        if (prof.auto_eggs if prof is not None else self.cfg.auto_eggs):
            self._click_egg_once(hwnd)
            log.info("🥚 Pós-Rebirth: Ovo trocado 1 vez (Ascended -> Common). Monitorando Checklist...")
            self.status.current_tier_index = 0
            self.status.current_tier_name = "Common"
            self.status.detected_active_egg = "Common"
            self.status.tier_status = [False] * 8
            self.status.checkmarks_found = 0
            self._notify()
            self._sleep(0.8)

    def _recover_to_altar_home(self, hwnd: int, force_test: bool = False) -> bool:
        """
        Procedimento de Auto-Recuperação em 2 Estágios caso o personagem saia do Altar ou perca a referência:
        1. Se NÃO vir a Mesa Central: usa qualquer Tocha visível (ou piso do Altar) como referência
           para se aproximar passo a passo até a Mesa Central entrar no campo de visão.
        2. Assim que vir a Mesa Central: usa a mesa de referência para voltar ao ponto inicial (P0,
           frente da Mesa Central), estabiliza o personagem e permite reiniciar o ciclo das 4 tochas.
        """
        log.warning("🛟 [Auto-Recuperação] Iniciando retorno automático ao ponto inicial da Mesa Central (P0)...")
        self.status.last_action = "🛟 Recuperando posição: buscando Tocha/Mesa de referência..."
        self._notify()

        for step in range(1, 6):
            if self._stop.is_set() or (not self.status.running and not force_test):
                return False

            f_rec = self.vision.capture_window(hwnd)
            if f_rec is None:
                time.sleep(0.20)
                continue

            rec = self.vision.find_altar_recovery_point(hwnd, f_rec)
            if rec is None:
                log.warning(f"⚠️ [Auto-Recuperação {step}/5] Nenhuma referência do Altar visível neste frame.")
                time.sleep(0.35)
                continue

            rcx, rcy, rsx, rsy, mode = rec
            if mode == "TABLE_P0":
                log.info(
                    f"🏛️ [Auto-Recuperação {step}/5] Mesa Central localizada! "
                    f"Voltando ao ponto inicial P0 ({rcx}, {rcy})..."
                )
                self.status.last_action = "🏛️ Mesa localizada! Voltando ao ponto inicial (P0)..."
                self._notify()
                self.controller.right_click_at(rsx, rsy, rcx, rcy, hwnd)
                time.sleep(1.45)

                # Confirma parada colada à frente da mesa (P0_CLOSE) para garantir ponto zero limpo
                f_check = self.vision.capture_window(hwnd)
                if f_check is not None and self.vision.detect_altar_table(f_check) is not None:
                    c_close = self.vision.get_altar_click_coords(hwnd, f_check, "P0")
                    if c_close:
                        ccx, ccy, csx, csy = c_close
                        self.controller.right_click_at(csx, csy, ccx, ccy, hwnd)
                        time.sleep(0.75)
                    log.info("✅ [Auto-Recuperação] Personagem reposicionado no ponto inicial (P0) da Mesa Central!")
                    return True
            else:
                log.info(
                    f"🧭 [Auto-Recuperação {step}/5] Mesa fora de visão -> "
                    f"Usando referência '{mode}' em ({rcx}, {rcy}) para se aproximar do Altar..."
                )
                self.status.last_action = f"🧭 Aproximando pela referência {mode} ({step}/5)..."
                self._notify()
                self.controller.right_click_at(rsx, rsy, rcx, rcy, hwnd)
                time.sleep(1.35)

        f_final = self.vision.capture_window(hwnd)
        if f_final is not None:
            self.push_live_frame(hwnd, f_final, immediate_overlay=True)
        return f_final is not None and self.vision.detect_altar_table(f_final) is not None

    def _capture_settled_altar_frame(self, hwnd: int, max_wait: float = 1.15) -> Optional[np.ndarray]:
        """
        Aguarda o personagem/câmera desacelerar após o último movimento até que a posição da
        Mesa Branca estabilize entre 2 leituras consecutivas (variação <= 3px) e atualiza a Overlay
        imediatamente ANTES de calcular o próximo clique de movimento.
        """
        t0 = time.time()
        prev_pos: Optional[Tuple[int, int]] = None
        last_frame: Optional[np.ndarray] = None

        while (time.time() - t0) < max_wait:
            f = self.vision.capture_window(hwnd)
            if f is None:
                time.sleep(0.08)
                continue
            last_frame = f
            t_rect = self.vision.detect_altar_table_rect(f)
            if t_rect is not None:
                rx, ry, rw, rh, _ = t_rect
                cx_t, cy_t = rx + rw // 2, ry + rh // 2
                if prev_pos is not None:
                    if abs(cx_t - prev_pos[0]) <= 3 and abs(cy_t - prev_pos[1]) <= 3:
                        self.push_live_frame(hwnd, f, immediate_overlay=True)
                        return f
                prev_pos = (cx_t, cy_t)
            else:
                # Sem mesa visível (ou modo proporção de tela): aguarda breve estabilização da câmera
                if (time.time() - t0) >= 0.24:
                    self.push_live_frame(hwnd, f, immediate_overlay=True)
                    return f
            time.sleep(0.09)

        if last_frame is not None:
            self.push_live_frame(hwnd, last_frame, immediate_overlay=True)
        return last_frame

    def _execute_altar_circuit(self, hwnd: int, force_test: bool = False) -> bool:
        """
        Executa a volta completa pelo Altar seguindo RIGOROSAMENTE a sequência de 4 Tochas + Mesa Branca:
        1. Tocha 1 (SW - Inferior Esq.) -> Anda até a tocha -> Interage [E] Light e/ou verifica se já está acesa -> Avança
        2. Tocha 2 (NW - Superior Esq.) -> Anda até a tocha -> Interage [E] Light e/ou verifica se já está acesa -> Avança
        3. Tocha 3 (NE - Superior Dir.) -> Anda até a tocha -> Interage [E] Light e/ou verifica se já está acesa -> Avança
        4. Tocha 4 (SE - Inferior Dir.) -> Anda até a tocha -> Interage [E] Light e/ou verifica se já está acesa -> Avança
        5. Frente da Mesa Branca (P0)   -> [E] Rebirth (segura E até completar o círculo branco)

        Regras fundamentais de estabilidade:
        - A MESA BRANCA é a âncora absoluta de todos os movimentos.
        - Nunca pula a caminhada de uma tocha à distância: sempre caminha na sequência 1 -> 2 -> 3 -> 4 -> P0.
        - Uma vez que uma tocha é acesa ou confirmada acesa, mantém esse estado na memória (`lit_torches_memory`)
          até o Rebirth concluir (impedindo qualquer oscilação por animação/fumaça da chama).
        """
        torch_steps = [
            ("SW", "Tocha 1 (SW - Inferior Esq.)"),
            ("NW", "Tocha 2 (NW - Superior Esq.)"),
            ("NE", "Tocha 3 (NE - Superior Dir.)"),
            ("SE", "Tocha 4 (SE - Inferior Dir.)"),
        ]

        for circuit_attempt in range(1, 3):
            if self._stop.is_set() or (not self.status.running and not force_test):
                self.vision.notify_circuit_end()
                return False

            frame0 = self._capture_settled_altar_frame(hwnd, max_wait=0.85)
            if frame0 is None:
                self.vision.notify_circuit_end()
                return False

            # Se a tela de Pet Descoberto ('Continue') estiver aberta, fecha com [E] antes de avaliar a Mesa
            if self._dismiss_pet_continue_if_needed(hwnd, frame0):
                frame0 = self._capture_settled_altar_frame(hwnd, max_wait=0.75) or frame0

            # Se já estiver diante da Mesa Branca com [E] Rebirth ativo, executa o Rebirth direto
            if not force_test and self.cfg.auto_interact_e:
                p_type0, p_score0, p_loc0 = self.vision.detect_prompt_type(frame0)
                if p_type0 == "REBIRTH":
                    log.info(
                        f"✨ [E Rebirth] já ativo em {p_loc0} ({p_score0:.2f})! "
                        f"Segurando [E] até completar o círculo..."
                    )
                    self.status.state = BotState.REBIRTH_PHASE
                    self.status.last_action = "✨ [E Rebirth] ativo! Segurando [E]..."
                    self._notify()
                    if self._hold_e_until_rebirth_complete(hwnd):
                        self._execute_post_rebirth_switch(hwnd)
                        self.vision.notify_circuit_end()
                        return True
                    log.warning("⚠️ [Altar] Rebirth não confirmado no início da volta. Seguindo para as 4 Tochas...")
                    frame0 = self._capture_settled_altar_frame(hwnd, max_wait=0.75) or frame0

            # Se a Mesa Branca não estiver visível no início, executa Auto-Recuperação (Tocha -> Mesa P0)
            if self.vision.detect_altar_table(frame0, allow_kinematic=False) is None:
                log.warning("⚠️ [Altar] Mesa Branca fora de visão! Acionando Auto-Recuperação por Tocha/Mesa...")
                if not self._recover_to_altar_home(hwnd, force_test=force_test):
                    self.status.last_action = "⚠️ Mesa Branca não visível. Tentando recuperar pelo Altar..."
                    self._notify()
                    self.vision.notify_circuit_end()
                    return False
                frame0 = self._capture_settled_altar_frame(hwnd, max_wait=0.80) or frame0

            # Inicia o rastreamento cinemático inteligente ancorado no ponto original P0
            self.vision.notify_circuit_start(frame0)

            self.status.state = BotState.TORCH_PHASE
            self.status.torches_lit = 0
            self._notify()

            aborted_for_recovery = False

            for idx, (t_key, t_label) in enumerate(torch_steps, 1):
                if self._stop.is_set() or (not self.status.running and not force_test):
                    self.vision.notify_circuit_end()
                    return False
                if not force_test and not self._check_and_handle_user_mouse(allow_in_game=False):
                    self.vision.notify_circuit_end()
                    return False

                # Aguarda estabilização da câmera e atualiza posições + Overlays ANTES de calcular o clique!
                f_step = self._capture_settled_altar_frame(hwnd, max_wait=0.95)
                if f_step is None:
                    self.vision.notify_circuit_end()
                    return False

                # Se abriu a tela de Pet Descoberto ('Continue') durante a caminhada, fecha com [E] imediatamente
                if self._dismiss_pet_continue_if_needed(hwnd, f_step):
                    f_step = self._capture_settled_altar_frame(hwnd, max_wait=0.75) or f_step

                # Se durante o trajeto o Checklist já zerou (Rebirth ocorreu), encerra a volta
                if not force_test and sum(self.vision.analyze_tiers(f_step)) == 0 and self.vision.locate_egg_button_details(f_step) is not None:
                    self.vision.reset_lit_torches_memory()
                    self.vision.notify_circuit_end()
                    return True

                # Garante que a MESA BRANCA (âncora absoluta visual + cinemática) está disponível!
                t_rect_step = self.vision.detect_altar_table_rect(f_step, allow_kinematic=True)
                if t_rect_step is None:
                    log.warning(
                        f"⚠️ [Altar] Perdeu visão da Mesa Branca antes de {t_label}! "
                        f"Recuperando para P0 e reiniciando ciclo..."
                    )
                    if self._recover_to_altar_home(hwnd, force_test=force_test):
                        aborted_for_recovery = True
                        break
                    self.vision.notify_circuit_end()
                    return False

                # Atualiza a leitura de tochas acesas visíveis a partir da âncora da Mesa Branca
                self.vision.detect_torch_states(f_step, table_rect=t_rect_step)

                coords = self.vision.get_altar_click_coords(hwnd, f_step, t_key)
                if not coords:
                    log.warning(f"⚠️ [Altar] Não foi possível calcular o ponto {t_label}.")
                    continue

                cx, cy, sx, sy = coords
                log.info(
                    f"🧭 [Altar {idx}/4 - Tentativa {circuit_attempt}] "
                    f"Click-to-Move para {t_label} em ({cx}, {cy})..."
                )
                self.status.last_action = f"🧭 Movendo para {t_label} ({idx}/4)..."
                self._notify()

                # Atualiza previsão cinemática para o movimento rumo a t_key e executa o 1º PASSO OBRIGATÓRIO:
                fh_s, fw_s = f_step.shape[:2]
                self.vision.notify_movement_target(t_key, cx, cy, fw_s, fh_s)
                self.controller.right_click_at(sx, sy, cx, cy, hwnd)

                # 2º PASSO: Ao chegar na tocha, interage ([E] Light) e/ou verifica com redundância se já foi acesa
                lit_this_torch = False
                retried_close = False
                preventive_tapped = False
                t_wait = time.time()
                max_wait_t = float(
                    getattr(self.vision, "custom_altar_calibration", {}).get("walk_wait_sec", 3.6)
                )

                while time.time() - t_wait < max_wait_t:
                    if self._stop.is_set() or (not self.status.running and not force_test):
                        self.vision.notify_circuit_end()
                        return False
                    if not force_test and not self._check_and_handle_user_mouse(allow_in_game=False):
                        self.vision.notify_circuit_end()
                        return False
                    time.sleep(0.08)
                    f_cur = self.vision.capture_window(hwnd)
                    if f_cur is None:
                        continue
                    self.push_live_frame(hwnd, f_cur, immediate_overlay=False)

                    elapsed_t = time.time() - t_wait
                    p_type, p_score, p_loc = self.vision.detect_prompt_type(f_cur)

                    if p_type == "LIGHT":
                        # Redundância: se '[E] Light' apareceu, limpa qualquer falso-positivo anterior e acende!
                        self.vision.unmark_torch_lit(t_key)
                        log.info(
                            f"🔥 [{t_label}] '[E] Light' detectado em {p_loc} ({p_score:.2f})! "
                            f"Acendendo tocha ({idx}/4)..."
                        )
                        self.controller.force_foreground(hwnd)
                        self.controller.tap_key("e", hwnd)
                        time.sleep(0.28)

                        # Confirma se o prompt [E] Light apagou; se ainda estiver na tela (ex: personagem
                        # ainda estava freando no 1º toque), aperta [E] mais uma vez antes de sair!
                        f_verify = self.vision.capture_window(hwnd)
                        if f_verify is not None:
                            self.push_live_frame(hwnd, f_verify, immediate_overlay=True)
                            p_v, _, _ = self.vision.detect_prompt_type(f_verify)
                            if p_v == "LIGHT":
                                log.info(f"🔥 [{t_label}] Reforçando toque em [E] para garantir ativação...")
                                self.controller.tap_key("e", hwnd)
                                time.sleep(0.22)

                        self.vision.mark_torch_lit(t_key, interacted=True)
                        self.vision.notify_station_reached(t_key, f_verify if f_verify is not None else f_cur)
                        self.status.torches_lit = idx
                        self.status.last_action = f"🔥 {t_label} acesa e salva na memória ({idx}/4)!"
                        self._notify()
                        lit_this_torch = True
                        break

                    # Após caminhar até a tocha (1.35s), estabiliza a leitura na estação da tocha:
                    if not retried_close and elapsed_t >= 1.35:
                        retried_close = True
                        cust_cal = getattr(self.vision, "custom_altar_calibration", {})
                        is_ratio_mode = bool(cust_cal.get("enabled") and cust_cal.get("mode") == "screen_ratio")
                        _fs = self._capture_settled_altar_frame(hwnd, max_wait=0.50)
                        f_settled = _fs if _fs is not None else f_cur
                        self.vision.notify_station_reached(t_key, f_settled)

                        if not is_ratio_mode and self.vision.detect_altar_table(f_settled, allow_kinematic=True) is None:
                            log.warning(f"⚠️ [{t_label}] Saiu da área da Mesa Branca durante o movimento! Recuperando...")
                            if self._recover_to_altar_home(hwnd, force_test=force_test):
                                aborted_for_recovery = True
                            break

                        # Verifica se o prompt [E] Light apareceu ao parar na tocha
                        p_s, p_sc, p_lc = self.vision.detect_prompt_type(f_settled)
                        if p_s == "LIGHT":
                            self.vision.unmark_torch_lit(t_key)
                            log.info(
                                f"🔥 [{t_label}] '[E] Light' detectado em {p_lc} ({p_sc:.2f})! "
                                f"Acendendo tocha ({idx}/4)..."
                            )
                            self.controller.force_foreground(hwnd)
                            self.controller.tap_key("e", hwnd)
                            time.sleep(0.28)
                            f_v2 = self.vision.capture_window(hwnd)
                            if f_v2 is not None:
                                self.push_live_frame(hwnd, f_v2, immediate_overlay=True)
                                if self.vision.detect_prompt_type(f_v2)[0] == "LIGHT":
                                    self.controller.tap_key("e", hwnd)
                                    time.sleep(0.20)
                            self.vision.mark_torch_lit(t_key, interacted=True)
                            self.status.torches_lit = idx
                            self.status.last_action = f"🔥 {t_label} acesa e salva na memória ({idx}/4)!"
                            self._notify()
                            lit_this_torch = True
                            break

                        # Estabilidade Comprovada v4.1_stable (25h+):
                        # NUNCA pula a aproximação fina (_CLOSE) nem o toque preventivo em [E] por cor amarela,
                        # garantindo 100% de imunidade contra moedas de ouro voando até o personagem!
                        # Faz a aproximação fina (_CLOSE) ancorada na Mesa Branca:
                        c_close = self.vision.get_altar_click_coords(hwnd, f_settled, f"{t_key}_CLOSE")
                        if not c_close:
                            c_close = self.vision.get_altar_click_coords(hwnd, f_settled, t_key)
                        if c_close:
                            rcx, rcy, rsx, rsy = c_close
                            log.info(f"🧭 [{t_label}] Aproximação fina da tocha em ({rcx}, {rcy})...")
                            fh_c, fw_c = f_settled.shape[:2]
                            self.vision.notify_movement_target(f"{t_key}_CLOSE", rcx, rcy, fw_c, fh_c)
                            self.controller.right_click_at(rsx, rsy, rcx, rcy, hwnd)

                    # Se após 2.45s já está parado junto à tocha (ou já acesa, ou coberta por estrela/ouro),
                    # dá um toque preventivo em [E] (inofensivo se já estiver acesa; acende se estiver apagada!)
                    if not preventive_tapped and elapsed_t >= 2.45:
                        preventive_tapped = True
                        self.controller.force_foreground(hwnd)
                        self.controller.tap_key("e", hwnd)
                        time.sleep(0.22)
                        self.controller.tap_key("e", hwnd)
                        time.sleep(0.18)

                if aborted_for_recovery:
                    break

                if not lit_this_torch:
                    self.vision.mark_torch_lit(t_key, interacted=True)
                    f_after_t = self.vision.capture_window(hwnd)
                    self.vision.notify_station_reached(t_key, f_after_t)
                    log.info(f"ℹ️ [{t_label}] Interação preventiva concluída / tocha registrada na memória ({idx}/4). Avançando...")
                    self.status.torches_lit = idx
                    self._notify()

            if aborted_for_recovery:
                log.info("🔄 [Altar] Reiniciando volta nas 4 Tochas a partir do ponto inicial (P0)...")
                continue

            # ── 5. Retorno à Frente da Mesa Branca (P0) para fazer o Rebirth ──────
            if self._stop.is_set() or (not self.status.running and not force_test):
                self.vision.notify_circuit_end()
                return False

            f_end = self._capture_settled_altar_frame(hwnd, max_wait=0.95)
            if f_end is None:
                self.vision.notify_circuit_end()
                return False

            if self.vision.detect_altar_table(f_end, allow_kinematic=True) is None:
                if not self._recover_to_altar_home(hwnd, force_test=force_test):
                    self.vision.notify_circuit_end()
                    return False
                f_end = self._capture_settled_altar_frame(hwnd, max_wait=0.85)
                if f_end is None:
                    self.vision.notify_circuit_end()
                    return False

            p0_coords = self.vision.get_altar_click_coords(hwnd, f_end, "P0")
            if p0_coords:
                p0_cx, p0_cy, p0_sx, p0_sy = p0_coords
                log.info(
                    f"🏛️ [Altar 5/5] 4 Tochas concluídas! Posicionando em frente à Mesa Branca ({p0_cx}, {p0_cy})..."
                )
                self.status.state = BotState.REBIRTH_PHASE
                self.status.last_action = "🏛️ Voltando à Mesa Branca para fazer Rebirth..."
                self._notify()

                fh_e, fw_e = f_end.shape[:2]
                self.vision.notify_movement_target("P0", p0_cx, p0_cy, fw_e, fh_e)
                self.controller.right_click_at(p0_sx, p0_sy, p0_cx, p0_cy, hwnd)

                t_reb = time.time()
                nudged_close = False
                while time.time() - t_reb < 3.6:
                    if self._stop.is_set() or (not self.status.running and not force_test):
                        self.vision.notify_circuit_end()
                        return False
                    time.sleep(0.09)
                    f_reb = self.vision.capture_window(hwnd)
                    if f_reb is None:
                        continue
                    self.push_live_frame(hwnd, f_reb, immediate_overlay=False)

                    p_type, p_score, p_loc = self.vision.detect_prompt_type(f_reb)
                    if p_type == "REBIRTH":
                        log.info(
                            f"✨ [E Rebirth] Identificado na Mesa Branca em {p_loc} ({p_score:.2f})! "
                            f"Segurando [E] até completar o círculo branco..."
                        )
                        self.status.last_action = (
                            "✨ [E Rebirth] detectado na Mesa Branca! Segurando [E] até completar o círculo..."
                        )
                        self._notify()
                        if not force_test or self.cfg.auto_interact_e:
                            if not self._hold_e_until_rebirth_complete(hwnd):
                                # Sai da espera e cai na lógica de repetição da volta (limpa tochas + P0)
                                break
                            self._execute_post_rebirth_switch(hwnd)
                        self.vision.notify_circuit_end()
                        return True

                    # Se após 1.35s chegou perto da mesa mas [E] Rebirth ainda não apareceu, aproxima na borda frontal (P0_CLOSE)
                    if not nudged_close and (time.time() - t_reb) >= 1.35:
                        nudged_close = True
                        _frs = self._capture_settled_altar_frame(hwnd, max_wait=0.50)
                        f_reb_settled = _frs if _frs is not None else f_reb
                        self.vision.notify_station_reached("P0", f_reb_settled)
                        c_close = self.vision.get_altar_click_coords(hwnd, f_reb_settled, "P0_CLOSE")
                        if c_close:
                            ccx, ccy, csx, csy = c_close
                            log.info(f"🏛️ [Altar] Aproximando da borda frontal da Mesa Branca ({ccx}, {ccy})...")
                            self.controller.right_click_at(csx, csy, ccx, ccy, hwnd)

            # Se chegou à Mesa Branca mas [E] Rebirth não apareceu (alguma tocha pode não ter acendido),
            # limpa a memória de tochas, realinha em P0 e repete a sequência 1->2->3->4->P0!
            if circuit_attempt == 1:
                self.vision.reset_lit_torches_memory()
                log.warning(
                    "🔄 [Altar] '[E] Rebirth' não apareceu após a volta (alguma tocha pode não ter acendido). "
                    "Limpando memória de tochas e reposicionando em P0 para repetir o ciclo..."
                )
                self._recover_to_altar_home(hwnd, force_test=force_test)

        self.vision.notify_circuit_end()
        return False

    # ── Testes e Diagnóstico ──────────────────────────────────────────────────

    def test_screen_read(self) -> str:
        target = RobloxFinder.get_primary_window()
        if not target:
            return "Roblox não encontrado!"
        hwnd, title, _ = target
        frame = self.vision.capture_window(hwnd)
        if frame is None:
            return "Falha ao capturar frame via PrintWindow."

        tier_status = self.vision.analyze_tiers(frame)
        checks = sum(tier_status)
        is_complete = all(tier_status)
        target_idx, target_egg = self.vision.get_target_from_tiers(tier_status)
        expected_bar_egg = self.vision.get_expected_bar_egg(target_idx) if not is_complete else "Ascended"
        active_egg = self.vision.detect_active_egg(frame)
        cx, cy, sx, sy = self.vision.get_egg_button_coords(hwnd, frame)

        p_type, p_score, p_loc = self.vision.detect_prompt_type(frame)
        if p_type:
            prompt_txt = f"{p_type} (Confiança: {p_score:.2f} em {p_loc})"
        else:
            prompt_txt = "Nenhum prompt ativo na tela"

        altar_pts = self.vision.detect_altar_points(frame)
        if altar_pts:
            altar_txt = (
                f"SW={altar_pts['SW']} | NW={altar_pts['NW']} | "
                f"NE={altar_pts['NE']} | SE={altar_pts['SE']} | Mesa(P0)={altar_pts['P0']}"
            )
        else:
            altar_txt = "Mesa Central não visível no ângulo atual"

        quest_hit = self.vision.detect_completed_quest(hwnd, frame)
        if quest_hit:
            q_cx, q_cy, q_sx, q_sy, q_conf = quest_hit
            quest_txt = f"🏆 COMPLETA! Botão verde em ({q_sx}, {q_sy}) [Verde={q_conf:.0%}]"
        else:
            quest_txt = "Em progresso (botão apagado)"

        try:
            vis = frame.copy()
            cv2.circle(vis, (cx, cy), 18, (0, 255, 255), 2)
            if quest_hit:
                cv2.circle(vis, (quest_hit[0], quest_hit[1]), 18, (0, 255, 0), 3)
            if altar_pts:
                for k in ("SW", "NW", "NE", "SE"):
                    pt = altar_pts[k]
                    cv2.circle(vis, pt, 7, (0, 0, 255), -1)
                    cv2.circle(vis, pt, 8, (255, 255, 255), 2)
                cv2.circle(vis, altar_pts["P0"], 7, (255, 0, 255), -1)
            if p_type and p_loc != (0, 0):
                cv2.circle(vis, p_loc, 24, (0, 255, 0), 3)
                cv2.putText(
                    vis,
                    p_type,
                    (p_loc[0] - 30, p_loc[1] - 25),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 0),
                    2,
                )
            app_dir = (
                os.path.dirname(sys.executable)
                if getattr(sys, "frozen", False)
                else os.path.dirname(os.path.abspath(__file__))
            )
            dbg_path = os.path.join(app_dir, "debug_leitura_completa.png")
            cv2.imwrite(dbg_path, vis)
        except Exception:
            pass

        details = []
        for i, (p_name, _) in enumerate(PET_NAMES):
            chk = "✔" if tier_status[i] else "○"
            details.append(f"{chk} {p_name}")

        status_flag = "🎉 240/240 PRONTO PARA RENASCER" if is_complete else f"{checks}/8 Tiers Concluídos"

        return (
            f"Client: '{title}'\n"
            f"Status: {status_flag}\n"
            f"Checklist: ({', '.join(details[:4])}\n            {', '.join(details[4:])})\n"
            f"Tier Alvo do Checklist: {target_egg} (Ovo esperado: {expected_bar_egg})\n"
            f"Ovo Detectado na Barra: {active_egg}\n"
            f"Missão Superior: {quest_txt}\n"
            f"Prompt [E] na Tela: {prompt_txt}\n"
            f"Pontos Visuais do Altar: {altar_txt}\n"
            f"Posição do Botão de Ovo: ({sx}, {sy})"
        )

    def test_quest_claim(self) -> str:
        target = RobloxFinder.get_primary_window()
        if not target:
            return "Roblox não encontrado!"
        hwnd, _, _ = target
        frame = self.vision.capture_window(hwnd)
        quest_hit = self.vision.detect_completed_quest(hwnd, frame) if frame is not None else None
        if self.controller.tap_key("c", hwnd):
            time.sleep(0.42)
            p_prof = self.window_profiles.get(hwnd)
            f_after = self.vision.capture_window(hwnd)
            closed_pet = self._dismiss_pet_continue_if_needed(hwnd, f_after, post_quest=True, prof=p_prof)
            pet_tag = " + 🐣 Tela de Pet fechada com [E]!" if closed_pet else ""
            if quest_hit:
                self.status.quests_claimed += 1
                self._notify()
                return (
                    f"🏆 Missão resgatada via Hotkey [C] ('Collect Quest & Close Window')! "
                    f"[Botão Verde={quest_hit[4]:.0%}]{pet_tag}"
                )
            return f"⌨️ Hotkey [C] ('Collect Quest & Close Window') enviada ao Roblox!{pet_tag}"
        return "Erro ao enviar Hotkey [C]."

    def _dismiss_pet_continue_if_needed(
        self,
        hwnd: int,
        frame: Optional[np.ndarray] = None,
        post_quest: bool = False,
        prof: Optional[WindowProfile] = None,
    ) -> bool:
        """
        Verifica se a janela do Roblox abriu a tela de Pet Descoberto ('New Pet Discovered! / Continue'),
        muito comum ao resgatar uma missão de chocar ovo com [C] enquanto está fazendo Rebirth:
        1. Detecta visualmente o botão verde 'Continue' e a arena verde-neon da tela de Pet.
        2. Adicionalmente, se `post_quest=True` (logo após coletar uma missão [C]) e o modo Rebirth estiver
           ativo (`auto_eggs` ou `auto_move_altar`), verifica se as referências do Rebirth (barra de Ovos,
           Mesa Branca e banner de Missão) sumiram da tela.
        3. Pressiona [E] (que confirma o botão verde 'Continue' embaixo) para fechar a janela do Pet e retornar
           ao jogo imediatamente; se ainda permanecer na tela após 0.38s, reforça com [E] + clique em 'Continue'.
        """
        if not hwnd or not win32gui.IsWindow(hwnd):
            return False

        f = frame if frame is not None else self.vision.capture_window(hwnd)
        if f is None or f.size == 0:
            return False

        pet_hit = self.vision.detect_pet_continue_screen(hwnd, f)
        lost_rebirth_refs = False

        if pet_hit is None and post_quest:
            reb_active = (
                (prof.auto_eggs or prof.auto_move_altar)
                if prof is not None
                else (self.cfg.auto_eggs or self.cfg.auto_move_altar)
            )
            if reb_active:
                has_egg_bar = self.vision.locate_egg_button_details(f) is not None
                has_table = self.vision.detect_altar_table(f, allow_kinematic=False) is not None
                has_quest_banner = self.vision.locate_quest_banner_details(f) is not None
                if not has_egg_bar and not has_table and not has_quest_banner:
                    lost_rebirth_refs = True

        if pet_hit is None and not lost_rebirth_refs:
            return False

        win_label = f"Janela #{prof.index}" if prof else f"HWND {hwnd}"
        reason_str = (
            f"Botão 'Continue' ({pet_hit[4]:.0%})"
            if pet_hit is not None
            else "Referências do Rebirth ocultadas pós-missão"
        )
        log.info(
            f"🐣 [{win_label}] Tela de Pet Descoberto detectada ({reason_str})! "
            f"Confirmando com [E] para fechar e retornar ao jogo..."
        )
        self.status.last_action = f"🐣 [{win_label}] Fechando tela de Pet Descoberto com [E]..."
        self._notify()

        self.controller.force_foreground(hwnd, settle_delay=0.12)
        self.controller.tap_key("e", hwnd)
        time.sleep(0.38)

        f2 = self.vision.capture_window(hwnd)
        if f2 is not None:
            self.push_live_frame(hwnd, f2)
            still_pet = self.vision.detect_pet_continue_screen(hwnd, f2)
            if still_pet is not None:
                pcx, pcy, psx, psy, _ = still_pet
                log.info(
                    f"🐣 [{win_label}] Reforçando fechamento da tela de Pet ([E] + clique em 'Continue' {pcx},{pcy})..."
                )
                self.controller.tap_key("e", hwnd)
                time.sleep(0.15)
                self.controller.left_click_at(psx, psy, pcx, pcy, hwnd)
                time.sleep(0.30)

        return True

    def _check_and_handle_disconnect(
        self, hwnd: int, frame, prof: Optional[WindowProfile] = None
    ) -> bool:
        """
        Verifica se a janela do Roblox exibe qualquer modal de desconexão ou falha de conexão
        ('Reconectar' / 'Reconnect' / 'Retry' / 'Try Again' / 'Tentar novamente'):
        - Traz a janela para o 1º plano e clica no botão de ação ('Reconnect' / 'Retry' / 'Try Again').
        - Revalida após o 1º clique e aplica 2º clique de confirmação caso o 1º tenha apenas focado a janela.
        - Se a janela usava Auto-Renascimento (Altar / [E] / Ovos), desativa essas opções
          por segurança (já que o personagem renasce no Spawn), mantendo ativos apenas
          Missão [C], Anti-AFK [C+Q] e Auto-Reconectar.
        """
        if frame is None:
            return False

        disc_hit = self.vision.detect_disconnect_dialog(hwnd, frame)
        if not disc_hit:
            return False

        cx, cy, sx, sy, conf = disc_hit
        win_label = f"Janela #{prof.index} (ID:{hwnd})" if prof else f"HWND {hwnd}"
        log.info(
            f"🔌 [Auto-Reconectar] Modal de Desconexão/Erro detectado na {win_label} "
            f"[Conf={conf:.0%}]! Clicando em 'Reconnect / Retry / Try Again' ({sx}, {sy})..."
        )
        self.status.reconnect_count += 1
        if prof:
            prof.reconnect_count += 1
            if prof.auto_move_altar or prof.auto_interact_e or prof.auto_eggs:
                prof.auto_move_altar = False
                prof.auto_interact_e = False
                prof.auto_eggs = False
                log.info(
                    f"🛡️ [Pós-Reconexão] Auto-Renascimento desativado na {win_label} "
                    f"(mantendo apenas Ciclo de Anti-AFK [C+Q] e Missões [C])."
                )

        self.status.last_action = (
            f"🔌 Reconectando (Reconnect/Retry) {win_label} (#{self.status.reconnect_count})..."
        )
        self._notify()

        self.controller.force_foreground(hwnd, settle_delay=0.35)

        # Afasta o cursor preventivamente do centro da janela para que o ponteiro branco do mouse
        # não cubra o texto 'Reconectar' na recaptura visual em 1º plano
        try:
            w_l, w_t, _, _ = win32gui.GetWindowRect(hwnd)
            win32api.SetCursorPos((int(w_l + 60), int(w_t + 60)))
            time.sleep(0.08)
        except Exception:
            pass

        # Recaptura com a janela já em 1º plano para coordenadas 100% precisas
        f_fg = self.vision.capture_window(hwnd)
        if f_fg is not None:
            fg_hit = self.vision.detect_disconnect_dialog(hwnd, f_fg)
            if fg_hit:
                cx, cy, sx, sy, _ = fg_hit

        def _do_modal_click(rel_cx: int, rel_cy: int):
            _, _, scr_x, scr_y = self.vision.frame_to_screen_and_client(hwnd, rel_cx, rel_cy)
            self.controller.click_modal_button(scr_x, scr_y, hwnd=hwnd)

        _do_modal_click(cx, cy)
        time.sleep(0.60)

        # Se o modal ainda estiver aberto após a 1ª sequência, aplica uma 2ª confirmação certeira
        f_after = self.vision.capture_window(hwnd)
        if f_after is not None:
            still_hit = self.vision.detect_disconnect_dialog(hwnd, f_after)
            if still_hit:
                cx2, cy2, _, _, _ = still_hit
                log.info(f"🔌 [Auto-Reconectar] Reforçando clique no botão 'Retry / Try Again / Reconnect' na {win_label}...")
                _do_modal_click(cx2, cy2)
                time.sleep(0.60)

        self._emit_event(
            "reconnect",
            "CAP Assistant — Reconexão Automática",
            f"Modal de desconexão detectado na {win_label}. Botão Reconectar acionado automaticamente!",
        )

        if prof:
            prof.last_afk_reset_ts = time.time()
        time.sleep(1.0)
        return True

    def test_reconnect(self) -> str:
        profiles = self.sync_windows()
        if not profiles:
            return "Roblox não encontrado!"
        reconnected_list = []
        primary_prof = self.get_primary_profile()
        for prof in profiles:
            w_hwnd = prof.hwnd
            if not win32gui.IsWindow(w_hwnd):
                continue
            frame = self.vision.capture_window(w_hwnd)
            if frame is None:
                continue
            hit = self.vision.detect_disconnect_dialog(w_hwnd, frame)
            if hit:
                self._check_and_handle_disconnect(w_hwnd, frame, prof)
                reconnected_list.append(f"Janela #{prof.index} ({hit[4]:.0%})")

        if primary_prof and win32gui.IsWindow(primary_prof.hwnd):
            self.controller.force_foreground(primary_prof.hwnd, settle_delay=0.25)

        if reconnected_list:
            return f"🔌 Reconectado com sucesso em: {', '.join(reconnected_list)}!"
        return "🟢 Nenhuma tela de desconexão detectada nas janelas abertas (Jogos conectados normalmente)."

    def _run_secondary_windows_cycle(self, primary_hwnd: int, is_manual_test: bool = False) -> str:
        """
        Alterna o 1º plano (Foreground) por todas as janelas abertas do Roblox (1..N),
        executando de forma independente apenas os recursos marcados para cada janela
        (Auto-Reconectar, Anti-AFK [C+Q], Missão [C], Ovos [Z], Interação [E] ou Altar)
        e retorna para a janela principal.
        """
        profiles = self.sync_windows()
        if not profiles:
            return "Nenhuma janela do Roblox encontrada!"

        if not is_manual_test and not self._check_and_handle_user_mouse(allow_in_game=False):
            return "⏸️ Giro Anti-AFK suspenso (usuário ativo no jogo)."

        acted_windows = 0
        quests_now = 0

        # 1. Se a janela principal tiver Anti-AFK ativo, envia C+Q nela também
        pri_prof = self.window_profiles.get(primary_hwnd)
        if pri_prof and pri_prof.auto_anti_afk and win32gui.IsWindow(primary_hwnd):
            self.controller.force_foreground(primary_hwnd, settle_delay=0.25)
            self.controller.tap_key("c", primary_hwnd)
            time.sleep(0.10)
            self.controller.tap_key("q", primary_hwnd)
            pri_prof.last_afk_reset_ts = time.time()
            acted_windows += 1

        # 2. Percorre todas as janelas secundárias trazendo cada uma para o 1º plano
        for prof in profiles:
            w_hwnd = prof.hwnd
            if w_hwnd == primary_hwnd or not win32gui.IsWindow(w_hwnd):
                continue

            if not (
                prof.auto_reconnect
                or prof.auto_anti_afk
                or prof.auto_claim_quest
                or prof.auto_eggs
                or prof.auto_interact_e
                or prof.auto_move_altar
            ):
                continue

            if not is_manual_test and (self._stop.is_set() or not self.status.running):
                break
            if not is_manual_test and not self._check_and_handle_user_mouse(allow_in_game=False):
                log.info("🎮 [Anti-AFK Interrompido] Usuário assumiu o controle no jogo.")
                break

            log.info(
                f"🔄 [Alternando Janela] Trazendo Janela #{prof.index} (ID:{w_hwnd}) para o 1º Plano..."
            )
            self.status.last_action = (
                f"🔄 Alternando 1º Plano -> Janela #{prof.index} (ID:{w_hwnd})..."
            )
            self._notify()

            # Traz fisicamente a janela secundária para a frente (topo da tela) e aguarda renderizar
            self.controller.force_foreground(w_hwnd, settle_delay=0.45)

            w_frame = self.vision.capture_window(w_hwnd)

            # 0) Auto-Reconectar na janela secundária (caso tenha caído por Erro 278 / 288 / etc.)
            if prof.auto_reconnect and w_frame is not None:
                if self._check_and_handle_disconnect(w_hwnd, w_frame, prof):
                    acted_windows += 1
                    continue

            # A) Auto-Coletar Missão [C] na janela secundária (agora desobstruída em 1º plano!)
            if prof.auto_claim_quest and w_frame is not None:
                q_hit = self.vision.detect_completed_quest(w_hwnd, w_frame)
                if q_hit:
                    prof.quests_claimed += 1
                    self.status.quests_claimed += 1
                    quests_now += 1
                    log.info(
                        f"🏆 [Janela #{prof.index}] Missão Completa detectada ({q_hit[4]:.0%})! "
                        f"Resgatando com [C]..."
                    )
                    self.controller.tap_key("c", w_hwnd)
                    time.sleep(0.42)
                    self._dismiss_pet_continue_if_needed(w_hwnd, None, post_quest=True, prof=prof)

            # A2) Se a janela secundária estiver na tela de Pet Descoberto ('Continue'), fecha com [E]
            self._dismiss_pet_continue_if_needed(w_hwnd, w_frame, post_quest=False, prof=prof)

            # B) Anti-AFK [C] + [Q] em 1º plano
            if prof.auto_anti_afk:
                self.controller.tap_key("c", w_hwnd)
                time.sleep(0.14)
                self.controller.tap_key("q", w_hwnd)
                time.sleep(0.14)
                prof.last_afk_reset_ts = time.time()
                log.info(
                    f"🛡️ [Janela #{prof.index}] Pulso Anti-AFK [C]+[Q] executado em 1º plano!"
                )

            # C) Interação [E] Light / Rebirth na janela secundária (se ativado nela)
            if prof.auto_interact_e and w_frame is not None:
                p_type, p_score, p_loc = self.vision.detect_prompt_type(w_frame)
                if p_type == "LIGHT":
                    log.info(f"🔥 [Janela #{prof.index}] [E Light] detectado! Pressionando [E]...")
                    self.controller.tap_key("e", w_hwnd)
                    time.sleep(0.25)
                elif p_type == "REBIRTH":
                    log.info(f"✨ [Janela #{prof.index}] [E Rebirth] detectado! Segurando [E]...")
                    # _execute_post_rebirth_switch já incrementa prof.rebirth_count (evita contagem dupla)
                    if self._hold_e_until_rebirth_complete(w_hwnd):
                        self._execute_post_rebirth_switch(w_hwnd)

            # D) Troca de Ovos [Z] & Altar na janela secundária (SOMENTE se ativado nela -> Economia de CPU)
            if (prof.auto_eggs or prof.auto_move_altar) and w_frame is not None:
                w_tiers = self.vision.analyze_tiers(w_frame)
                w_egg = self.vision.detect_active_egg(w_frame)
                if all(w_tiers) and prof.auto_move_altar:
                    log.info(
                        f"🏛️ [Janela #{prof.index}] 240/240 completo! Executando Altar..."
                    )
                    # Rebirths confirmados já são contados dentro do circuito (_execute_post_rebirth_switch)
                    self._execute_altar_circuit(w_hwnd)
                elif prof.auto_eggs:
                    t_idx, _ = self.vision.get_target_from_tiers(w_tiers)
                    if t_idx < 8 and all(w_tiers[:t_idx]):
                        prof.current_tier_index = t_idx
                        exp_egg = self.vision.get_expected_bar_egg(t_idx)
                        if w_egg != exp_egg:
                            log.info(
                                f"🥚 [Janela #{prof.index}] Sincronizando ovo ({w_egg} -> {exp_egg}) via [Z]..."
                            )
                            self._auto_correct_egg_to_expected(w_hwnd, exp_egg)

            acted_windows += 1
            time.sleep(0.25)

        # 3. Devolve imediatamente a Janela Principal para o 1º plano (exceto se o usuário assumiu outra janela)
        if primary_hwnd and win32gui.IsWindow(primary_hwnd) and (is_manual_test or not self.status.auto_paused_mouse):
            log.info(
                f"⭐ Retornando Janela Principal (ID:{primary_hwnd}) para o 1º Plano..."
            )
            self.controller.force_foreground(primary_hwnd, settle_delay=0.30)

        self.status.anti_afk_pulses += 1
        self._notify()
        return (
            f"🛡️ Giro Multi-Janelas concluído em {acted_windows}/{len(profiles)} janela(s) "
            f"(Alternância em 1º Plano + [C]+[Q] | Missões resgatadas agora: {quests_now})!"
        )

    def test_anti_afk_pulse(self) -> str:
        primary_prof = self.get_primary_profile()
        if not primary_prof:
            return "Nenhuma janela do Roblox encontrada!"
        return self._run_secondary_windows_cycle(primary_prof.hwnd, is_manual_test=True)

    def test_altar_route(self) -> str:
        primary_prof = self.get_primary_profile()
        if not primary_prof:
            return "Roblox não encontrado!"
        hwnd = primary_prof.hwnd
        self._stop.clear()
        ok = self._execute_altar_circuit(hwnd, force_test=True)
        if ok:
            return "Volta pelo Altar (4 Tochas + Mesa Central) concluída com sucesso!"
        return "Volta pelo Altar finalizada (verifique se a Mesa Central estava visível)."

    def test_egg_click(self) -> str:
        primary_prof = self.get_primary_profile()
        if not primary_prof:
            return "Roblox não encontrado!"
        if self.controller.tap_key("z", primary_prof.hwnd):
            return "🥚 Hotkey nativa [Z] ('Toggle Egg Type') enviada com sucesso!"
        return "Erro ao enviar Hotkey [Z]."

    def test_press_e(self) -> str:
        primary_prof = self.get_primary_profile()
        if not primary_prof:
            return "Roblox não encontrado!"
        hwnd = primary_prof.hwnd
        self.controller.force_foreground(hwnd)
        self.controller.tap_key("e", hwnd)
        return "Tecla [E] pressionada e solta com sucesso!"

    def test_rebirth_hold(self) -> str:
        primary_prof = self.get_primary_profile()
        if not primary_prof:
            return "Roblox não encontrado!"
        if self._hold_e_until_rebirth_complete(primary_prof.hwnd):
            return "Tecla [E] mantida pressionada até completar o Renascimento!"
        return "Tecla [E] mantida pressionada, mas o Renascimento não foi confirmado (Checklist não zerou)."

    # ── Loop Principal ────────────────────────────────────────────────────────

    def _run_loop(self):
        if not self._sleep(0.5):
            return

        primary_prof = self.get_primary_profile()
        if not primary_prof:
            self.stop()
            return
        hwnd = primary_prof.hwnd
        self.status.roblox_hwnd = hwnd

        # Garante que a janela principal comece em 1º plano
        self.controller.force_foreground(hwnd, settle_delay=0.25)

        was_all_complete = False
        if primary_prof.auto_eggs or primary_prof.auto_move_altar:
            init_frame = self.vision.capture_window(hwnd)
            if init_frame is not None:
                init_tiers = self.vision.analyze_tiers(init_frame)
                idx, _ = self.vision.get_target_from_tiers(init_tiers)
                was_all_complete = all(init_tiers)
                self.status.current_tier_index = min(idx, 7)
                self.status.current_tier_name = (
                    EGG_CYCLE[self.status.current_tier_index] if idx < 8 else "240/240 Completo"
                )
                self.status.tier_status = init_tiers
                self.status.checkmarks_found = sum(init_tiers)
                self.status.detected_active_egg = self.vision.detect_active_egg(init_frame)
                log.info(
                    f"📋 Checklist inicial: {self.status.checkmarks_found}/8 completos | "
                    f"Monitorando tier: {self.status.current_tier_name} | "
                    f"Ovo na Barra: {self.status.detected_active_egg}"
                )

        confirm_check_count = 0
        complete_confirm_count = 0
        mismatch_count = 0
        tier_sync_count = 0
        quest_ready_count = 0
        last_light_time = 0.0
        last_quest_claim_time = 0.0
        last_reconnect_check_time = 0.0
        last_focus_afk_time = time.time()

        while self.status.running and not self._stop.is_set():
            try:
                if self.status.paused:
                    time.sleep(0.15)
                    self.controller.sync_cursor_baseline()
                    continue

                if not self._check_and_handle_user_mouse(allow_in_game=True):
                    break

                in_game_now, fg_hwnd = self._is_roblox_foreground()
                self.status.user_in_game = in_game_now
                user_active_in_game = bool(self.status.auto_paused_mouse and in_game_now)

                # Atualiza janela principal caso o usuário tenha trocado a estrela ⭐ Principal na GUI
                cur_pri = self.get_primary_profile()
                if cur_pri and cur_pri.hwnd != hwnd:
                    hwnd = cur_pri.hwnd
                    self.status.roblox_hwnd = hwnd
                    if not user_active_in_game:
                        self.controller.force_foreground(hwnd, settle_delay=0.30)

                if not win32gui.IsWindow(hwnd):
                    log.error("Janela principal do Roblox fechada!")
                    self.status.last_action = "❌ Roblox fechado."
                    break

                # Se o usuário estiver jogando ativamente em uma janela do Roblox, opera nela sem roubar foco
                active_hwnd = (
                    fg_hwnd
                    if (user_active_in_game and fg_hwnd in self.window_profiles and win32gui.IsWindow(fg_hwnd))
                    else hwnd
                )
                p_prof = self.window_profiles.get(active_hwnd)
                # No modo Semi-Auto (ou quando o usuário está dentro do jogo movimentando manualmente),
                # as Interações [E] (Light/Rebirth) e Ovos [Z] NÃO sofrem interferência do Auto-Pause!
                do_interact_e  = (p_prof.auto_interact_e  if p_prof else self.cfg.auto_interact_e)
                do_eggs        = (p_prof.auto_eggs        if p_prof else self.cfg.auto_eggs)
                do_move_altar  = (p_prof.auto_move_altar  if p_prof else self.cfg.auto_move_altar)  and not user_active_in_game
                do_claim_quest = (p_prof.auto_claim_quest if p_prof else self.cfg.auto_claim_quest)
                do_reconnect   = (p_prof.auto_reconnect   if p_prof else self.cfg.auto_reconnect)   and not user_active_in_game

                frame = self.vision.capture_window(active_hwnd)
                if frame is None:
                    time.sleep(0.25)
                    continue
                self.push_live_frame(active_hwnd, frame)

                # ── 0. Detecção de Desconexão / Retry / Try Again em TODAS as Janelas ──
                user_holding_input = self.controller.is_user_holding_input()
                if not user_holding_input and (time.time() - last_reconnect_check_time) >= 2.0:
                    last_reconnect_check_time = time.time()
                    if (p_prof.auto_reconnect if p_prof else self.cfg.auto_reconnect) and self._check_and_handle_disconnect(active_hwnd, frame, p_prof):
                        was_all_complete = False
                        complete_confirm_count = 0
                        confirm_check_count = 0
                        mismatch_count = 0
                        tier_sync_count = 0
                        last_focus_afk_time = time.time()
                        if not self._sleep(2.0):
                            break
                        continue

                    # Verifica também todas as demais janelas com Auto-Reconectar ativo via PrintWindow
                    sec_reconnected = False
                    for s_prof in list(self.window_profiles.values()):
                        if s_prof.hwnd == active_hwnd or not s_prof.auto_reconnect or not win32gui.IsWindow(s_prof.hwnd):
                            continue
                        s_frame = self.vision.capture_window(s_prof.hwnd)
                        if s_frame is not None and self._check_and_handle_disconnect(s_prof.hwnd, s_frame, s_prof):
                            sec_reconnected = True
                    if sec_reconnected:
                        last_focus_afk_time = time.time()
                        if win32gui.IsWindow(active_hwnd):
                            self.controller.force_foreground(active_hwnd, settle_delay=0.25)

                # ── 0B. Fechamento Automático da Tela de Pet Descoberto ('Continue' -> [E]) ──
                if self._dismiss_pet_continue_if_needed(active_hwnd, frame, post_quest=False, prof=p_prof):
                    frame = self.vision.capture_window(active_hwnd)
                    if frame is None:
                        continue
                    self.push_live_frame(active_hwnd, frame)

                # ── 1. Detecção Imediata de '[E] Light' (Foto 1) e '[E] Rebirth' (Foto 2) ──
                if do_interact_e:
                    p_type, p_score, p_loc = self.vision.detect_prompt_type(frame)

                    if p_type == "LIGHT" and (time.time() - last_light_time) > 0.45:
                        log.info(
                            f"🔥 [E Light] Identificado em {p_loc} ({p_score:.2f})! "
                            f"Pressionando e soltando [E]..."
                        )
                        self.status.state = BotState.TORCH_PHASE
                        self.status.torches_lit = min(4, self.status.torches_lit + 1)
                        self.status.last_action = (
                            f"🔥 [E Light] detectado! Tocha acionada ({self.status.torches_lit}/4)"
                        )
                        self._notify()

                        self.controller.force_foreground(active_hwnd)
                        self.controller.tap_key("e", active_hwnd)
                        last_light_time = time.time()
                        if not self._sleep(0.40):
                            break
                        continue

                    elif p_type == "REBIRTH":
                        log.info(
                            f"✨ [E Rebirth] Identificado em {p_loc} ({p_score:.2f})! "
                            f"Mantendo [E] pressionado até completar o círculo branco..."
                        )
                        self.status.state = BotState.REBIRTH_PHASE
                        self.status.last_action = (
                            "✨ [E Rebirth] detectado! Segurando [E] até completar o círculo..."
                        )
                        self._notify()

                        if not self._hold_e_until_rebirth_complete(active_hwnd):
                            self.status.last_action = "⚠️ [E Rebirth] solto sem o Checklist zerar — Rebirth não confirmado."
                            self._notify()
                            if not self._sleep(0.5):
                                break
                            continue
                        self._execute_post_rebirth_switch(active_hwnd)
                        was_all_complete = False
                        complete_confirm_count = 0
                        confirm_check_count = 0
                        mismatch_count = 0
                        tier_sync_count = 0
                        continue

                # ── 1B. Monitoramento Simultâneo e Coleta Imediata de Missões [C] em TODAS as Janelas ──
                now_q = time.time()
                if (now_q - last_quest_claim_time) >= 0.85:
                    last_quest_claim_time = now_q
                    claimed_any_quest = False

                    for q_prof in list(self.window_profiles.values()):
                        q_hwnd = q_prof.hwnd
                        if not q_prof.auto_claim_quest or not win32gui.IsWindow(q_hwnd):
                            continue
                        if (now_q - q_prof.last_quest_claim_ts) < 1.2:
                            continue

                        q_frame = frame if q_hwnd == active_hwnd else self.vision.capture_window(q_hwnd)
                        if q_frame is None:
                            continue
                        if q_hwnd != active_hwnd:
                            self.push_live_frame(q_hwnd, q_frame)

                        quest_hit = self.vision.detect_completed_quest(q_hwnd, q_frame)
                        if quest_hit:
                            q_prof.quest_ready_streak += 1
                            if q_prof.quest_ready_streak >= 2:
                                # Se a missão pronta é em outra janela e o usuário está segurando botão da câmera/teclado agora,
                                # mantém engatilhado e coleta assim que o botão for solto
                                if q_hwnd != active_hwnd and user_active_in_game:
                                    if self.controller.is_user_holding_input():
                                        q_prof.quest_ready_streak = 2
                                        continue

                                q_prof.quest_ready_streak = 0
                                q_prof.last_quest_claim_ts = time.time()
                                q_cx, q_cy, q_sx, q_sy, q_conf = quest_hit

                                if q_hwnd != active_hwnd:
                                    self.controller.force_foreground(q_hwnd, settle_delay=0.22)
                                    self.controller.tap_key("c", q_hwnd)
                                    time.sleep(0.42)
                                    self._dismiss_pet_continue_if_needed(q_hwnd, None, post_quest=True, prof=q_prof)
                                    if q_prof.auto_anti_afk:
                                        time.sleep(0.08)
                                        self.controller.tap_key("q", q_hwnd)
                                    if win32gui.IsWindow(active_hwnd):
                                        self.controller.force_foreground(active_hwnd, settle_delay=0.18)
                                        if p_prof and p_prof.auto_anti_afk and not user_active_in_game:
                                            self.controller.tap_key("q", active_hwnd)
                                            p_prof.last_afk_reset_ts = time.time()
                                else:
                                    self.controller.tap_key("c", active_hwnd)
                                    time.sleep(0.42)
                                    self._dismiss_pet_continue_if_needed(active_hwnd, None, post_quest=True, prof=q_prof)
                                    if q_prof.auto_anti_afk and not user_active_in_game:
                                        time.sleep(0.08)
                                        self.controller.tap_key("q", active_hwnd)

                                q_prof.quests_claimed += 1
                                self.status.quests_claimed += 1
                                q_prof.last_afk_reset_ts = time.time()
                                # Reinicia automaticamente o temporizador do ciclo Anti-AFK (pois o resgate [C] já conta como Anti-AFK)
                                last_focus_afk_time = time.time()
                                claimed_any_quest = True

                                log.info(
                                    f"🏆 [Janela #{q_prof.index}] Missão Completa detectada [Verde={q_conf:.0%}]! "
                                    f"Coletada de imediato via [C] (#{self.status.quests_claimed}) — Ciclo Anti-AFK reiniciado!"
                                )
                                self.status.last_action = (
                                    f"🏆 [Janela #{q_prof.index}] Missão coletada via [C] (#{self.status.quests_claimed}) — Ciclo AFK reiniciado!"
                                )
                                self._notify()
                        else:
                            q_prof.quest_ready_streak = 0

                    if claimed_any_quest:
                        if not self._sleep(0.30):
                            break
                        continue

                # ── 1C. Alternador Multi-Janelas em 1º Plano (Anti-AFK [C+Q] & Tarefas por Janela) ──
                now_t = time.time()
                interval_s = max(5.0, float(self.cfg.anti_afk_interval_sec))
                any_win_afk_due = any(
                    w_p.auto_anti_afk
                    and win32gui.IsWindow(w_p.hwnd)
                    and (now_t - w_p.last_afk_reset_ts) >= interval_s
                    for w_p in self.window_profiles.values()
                )
                if (now_t - last_focus_afk_time) >= interval_s or (
                    any_win_afk_due and not self.status.auto_paused_mouse
                ):
                    last_focus_afk_time = now_t
                    if self.status.auto_paused_mouse:
                        # Se o usuário está jogando ativamente na janela em 1º plano, apenas ESSA janela
                        # teve atividade real; as janelas secundárias em background continuam contando o tempo sem AFK!
                        if user_active_in_game and active_hwnd in self.window_profiles:
                            self.window_profiles[active_hwnd].last_afk_reset_ts = now_t
                        log.info(
                            "🛡️⏸️ [Anti-AFK Suspenso] Usuário usando o jogo (Mouse/Câmera/Teclado) — "
                            "Ciclo Anti-AFK não executado."
                        )
                    else:
                        self._run_secondary_windows_cycle(hwnd, is_manual_test=False)
                        last_focus_afk_time = time.time()
                        if not self.status.auto_paused_mouse and win32gui.IsWindow(hwnd):
                            self.controller.force_foreground(hwnd, settle_delay=0.20)
                        frame = self.vision.capture_window(active_hwnd)
                        if frame is None:
                            continue

                # ── 2. Economia de Recursos: Só lê Ovos e Checklist se Auto-Ovos ou Altar estiverem ativos! ──
                if not do_eggs and not do_move_altar:
                    self.status.state = BotState.EGG_FARMING
                    self.status.detected_active_egg = "Desativado (Modo Leve)"
                    if user_active_in_game:
                        self.status.last_action = (
                            f"🎮 Em Jogo (Câmera/Mouse ativo) — Missão [C] Ativa | Anti-AFK Pausado ({self.status.mouse_idle_remaining}s)"
                        )
                    else:
                        self.status.last_action = (
                            f"🛡️ Ciclo Leve Ativo (Missão [C] / Anti-AFK / Reconectar) — Leitura: {self.cfg.screen_check_hz:.2f}s"
                        )
                    self._notify()
                    if not self._sleep(self.cfg.screen_check_hz):
                        break
                    continue

                # ── 3. Monitoramento Sequencial do Checklist de Ovos (Foto 4) ───────────
                tier_status = self.vision.analyze_tiers(frame)
                checks = sum(tier_status)
                active_egg = self.vision.detect_active_egg(frame)
                self.status.tier_status = tier_status
                self.status.checkmarks_found = checks
                self.status.detected_active_egg = active_egg

                # Todos os 8 checks verdes marcados (240/240 - incluindo Mythical completo)
                if all(tier_status) and (
                    self.status.current_tier_index >= 6
                    or active_egg == "Ascended"
                    or was_all_complete
                ):
                    was_all_complete = True
                    mismatch_count = 0
                    complete_confirm_count += 1
                    self.status.state = BotState.TORCH_PHASE
                    self.status.current_tier_name = "240/240 Completo"

                    if do_move_altar and complete_confirm_count >= 5:
                        complete_confirm_count = 0
                        log.info(
                            "🔥 Checklist 240/240 (Mythical ✔) confirmado! "
                            "Iniciando volta automática nas 4 Tochas + Mesa Central..."
                        )
                        if self._execute_altar_circuit(active_hwnd):
                            was_all_complete = False
                            confirm_check_count = 0
                            mismatch_count = 0
                            tier_sync_count = 0
                            continue
                        if not self._sleep(0.5):
                            break
                        continue

                    self.status.last_action = (
                        f"🔥 240/240 (Mythical ✔) Confirmando ({complete_confirm_count}/5)..."
                        if do_move_altar
                        else "🔥 240/240 Completo! Vá até as Tochas ([E] Light) e Mesa ([E] Rebirth)..."
                    )
                    self._notify()
                    if not self._sleep(self.cfg.screen_check_hz):
                        break
                    continue
                else:
                    complete_confirm_count = 0
                    if checks == 0:
                        self.vision.reset_lit_torches_memory()

                # Se estava em 240/240 e o Checklist acabou de zerar (Rebirth concluído manualmente)
                if was_all_complete and checks == 0:
                    was_all_complete = False
                    complete_confirm_count = 0
                    confirm_check_count = 0
                    mismatch_count = 0
                    tier_sync_count = 0
                    self.vision.reset_lit_torches_memory()
                    self._execute_post_rebirth_switch(active_hwnd)
                    continue

                cur_idx = self.status.current_tier_index
                if cur_idx > 7:
                    cur_idx = 0
                    self.status.current_tier_index = 0

                # ── 4. Avanço Normal (1 toque [Z] quando o tier atual ganha Check verde) ──
                if all(tier_status[: cur_idx + 1]):
                    confirm_check_count += 1
                    mismatch_count = 0
                    if confirm_check_count >= 3:
                        confirm_check_count = 0
                        completed_name = EGG_CYCLE[cur_idx]

                        if cur_idx < 6:
                            next_idx = cur_idx + 1
                            next_name = EGG_CYCLE[next_idx]
                            if do_eggs:
                                log.info(
                                    f"✅ {completed_name} completo (Check verde)! "
                                    f"Trocando ovo 1x [Z] -> {next_name}..."
                                )
                                self.status.state = BotState.SYNC_EGG
                                self.status.last_action = (
                                    f"✅ {completed_name} completo! Trocando para {next_name} [Z]..."
                                )
                                self._notify()

                                self._click_egg_once(active_hwnd, frame)
                                self.status.current_tier_index = next_idx
                                self.status.current_tier_name = next_name
                                self._notify()

                                if not self._sleep(0.85):
                                    break
                                continue
                            else:
                                self.status.current_tier_index = next_idx
                                self.status.current_tier_name = next_name
                        elif cur_idx == 6:
                            log.info(
                                "✅ Ascended (6/6) completo! "
                                "Aguardando Mythical (6/6) completar para 240/240..."
                            )
                            self.status.current_tier_index = 7
                            self.status.current_tier_name = "Mythical"
                            self._notify()
                else:
                    confirm_check_count = 0

                # ── 5. Auto-Correção de Rotação / Ovo guiada pela sequência do Checklist ──
                target_idx, _ = self.vision.get_target_from_tiers(tier_status)
                if target_idx < 8 and all(tier_status[:target_idx]):
                    if self.status.current_tier_index != target_idx:
                        tier_sync_count += 1
                        if tier_sync_count >= 4:
                            tier_sync_count = 0
                            self.status.current_tier_index = target_idx
                            self.status.current_tier_name = EGG_CYCLE[target_idx]
                    else:
                        tier_sync_count = 0

                    expected_egg = self.vision.get_expected_bar_egg(self.status.current_tier_index)
                    if do_eggs and active_egg != expected_egg:
                        mismatch_count += 1
                        if mismatch_count >= 6:
                            mismatch_count = 0
                            self._auto_correct_egg_to_expected(active_hwnd, expected_egg)
                            if not self._sleep(0.5):
                                break
                            continue
                    else:
                        mismatch_count = 0

                self.status.state = BotState.EGG_FARMING
                cur_name = EGG_CYCLE[self.status.current_tier_index]
                cur_total = PET_NAMES[self.status.current_tier_index][1]
                self.status.current_tier_name = cur_name
                if user_active_in_game:
                    semi_tag = " + [E] Light/Rebirth" if do_interact_e else ""
                    self.status.last_action = (
                        f"🎮 Em Jogo (AFK Pausa {self.status.mouse_idle_remaining}s) — "
                        f"🥚 {cur_name} [{checks}/8]{semi_tag} + Missão [C] Ativos"
                    )
                else:
                    self.status.last_action = (
                        f"🥚 Monitorando {cur_name} (0/{cur_total} -> {cur_total}/{cur_total}) "
                        f"[{checks}/8 Checks verdes]"
                    )
                self._notify()

                if not self._sleep(self.cfg.screen_check_hz):
                    break

            except Exception as e:
                log.error(f"Erro no loop do bot: {e}", exc_info=True)
                self.status.last_action = f"⚠️ Erro: {e}"
                self._notify()
                time.sleep(1.0)

        self.status.running = False
        self.status.state   = BotState.IDLE
        self.controller.release_all()
        self._notify()
