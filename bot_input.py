"""
CAP Rebirth Bot - DirectInput Controller v3.4
- Segurar tecla [E] real via DirectX Scan Code + pydirectinput.
- Clique no botão de ovo com atualização de Hover (MOUSEEVENTF_MOVE) antes do clique.
"""

import time
import threading
import random
import logging
import ctypes
import win32gui
import win32con
import win32api
import pydirectinput

pydirectinput.FAILSAFE = True
pydirectinput.PAUSE = 0.01

log = logging.getLogger("CAPInput")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def enable_per_monitor_dpi_awareness():
    """
    Garante que o processo opere em coordenadas físicas reais (1:1 com pixels da tela)
    em qualquer monitor ou escala de DPI do Windows (100%, 125%, 150%, 175%, 200%).
    """
    try:
        # Windows 10 1703+: DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 (-4)
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        # Windows 8.1+: PROCESS_PER_MONITOR_DPI_AWARE (2)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


enable_per_monitor_dpi_awareness()

MOUSEEVENTF_MOVE      = 0x0001
MOUSEEVENTF_LEFTDOWN  = 0x0002
MOUSEEVENTF_LEFTUP    = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP   = 0x0010
KEYEVENTF_SCANCODE    = 0x0008
KEYEVENTF_KEYUP       = 0x0002

VK_E   = 0x45
SCAN_E = 0x12

# Mapeamento oficial (VK_CODE, HARDWARE_SCANCODE) dos atalhos do Collect All Pets
GAME_KEY_MAP = {
    "1":        (0x31, 0x02),  # Pets Equipped and Fusing
    "2":        (0x32, 0x03),  # Pet Index
    "3":        (0x33, 0x04),  # Badges
    "4":        (0x34, 0x05),  # Game Settings
    "5":        (0x35, 0x06),  # Game Shop
    "q":        (0x51, 0x10),  # Close Window
    "e":        (0x45, 0x12),  # Interact Key ([E] Light / [E] Rebirth)
    "r":        (0x52, 0x13),  # Equip Best Pets
    "f":        (0x46, 0x21),  # Toggle Pets Follow/Stay
    "g":        (0x47, 0x22),  # Toggle Generate Station
    "z":        (0x5A, 0x2C),  # Toggle Egg Type (Trocar Ovo)
    "x":        (0x58, 0x2D),  # Autohatch On/Off
    "c":        (0x43, 0x2E),  # Collect Quest & Close Window (Coletar Missão)
    "space":    (0x20, 0x39),  # Jump / Anti-AFK
    "ctrlleft": (0xA2, 0x1D),  # Toggle Discovered Pets
}


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint),
    ]


class DirectInputController:
    """Envia comandos DirectX diretamente para o cliente do Roblox e monitora atividade global do PC."""

    def __init__(self):
        self._held_keys = set()
        self._autorepeat = {}  # tecla -> (threading.Event de parada, thread de auto-repeat)
        self.is_bot_moving_mouse: bool = False
        self.last_expected_cursor_pos = self._safe_get_cursor()
        self.last_bot_input_tick: int = self._get_last_input_tick()
        self.last_bot_action_ts: float = time.time()

    @staticmethod
    def _safe_get_cursor():
        try:
            return win32api.GetCursorPos()
        except Exception:
            return (0, 0)

    @staticmethod
    def _get_last_input_tick() -> int:
        """Retorna o tick (ms) do último evento global de Teclado ou Mouse no Windows."""
        try:
            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if user32.GetLastInputInfo(ctypes.byref(lii)):
                return int(lii.dwTime)
        except Exception:
            pass
        return 0

    def sync_cursor_baseline(self):
        """Sincroniza o estado atual de Mouse + Teclado como linha de base após ação do bot."""
        self.last_expected_cursor_pos = self._safe_get_cursor()
        self.last_bot_input_tick = self._get_last_input_tick()
        self.last_bot_action_ts = time.time()

    # Botões do mouse e teclas de câmera/movimento que indicam que o jogador está controlando o jogo/câmera
    # mesmo quando o cursor do mouse fica travado/parado no mesmo pixel pelo Roblox (ex: segurando Botão Direito)
    USER_ACTIVE_VKS = (
        0x01,  # VK_LBUTTON (Clique esquerdo)
        0x02,  # VK_RBUTTON (Botão direito segurado para girar a câmera no Roblox com mouse parado!)
        0x04,  # VK_MBUTTON (Botão do meio / scroll)
        0x05,  # VK_XBUTTON1
        0x06,  # VK_XBUTTON2
        0x57,  # W (Andar para frente)
        0x41,  # A (Andar para esquerda)
        0x53,  # S (Andar para trás)
        0x44,  # D (Andar para direita)
        0x20,  # Space (Pular)
        0x25,  # Seta Esquerda (Girar câmera para esquerda com mouse parado)
        0x26,  # Seta Cima (Avançar / câmera)
        0x27,  # Seta Direita (Girar câmera para direita com mouse parado)
        0x28,  # Seta Baixo (Recuar / câmera)
        0x49,  # I (Zoom In da câmera)
        0x4F,  # O (Zoom Out da câmera)
        0x21,  # PageUp (Inclinar câmera para cima)
        0x22,  # PageDown (Inclinar câmera para baixo)
        0xBC,  # Vírgula '<' (Alinhar câmera)
        0xBE,  # Ponto '>' (Alinhar câmera)
        0x10,  # Shift (Shift-Lock da câmera)
    )

    def is_user_holding_input(self) -> bool:
        """Retorna True se o usuário estiver segurando fisicamente algum botão do mouse ou tecla de movimento/câmera."""
        if self.is_bot_moving_mouse:
            return False
        try:
            for vk in self.USER_ACTIVE_VKS:
                if user32.GetAsyncKeyState(vk) & 0x8000:
                    return True
        except Exception:
            pass
        return False

    def check_user_mouse_moved(self, threshold_px: int = 15) -> bool:
        """
        Detecta QUALQUER indício de uso do PC ou da câmera do jogo pelo usuário:
        1. Movimento físico do cursor do mouse (> threshold_px).
        2. Botão Direito do mouse segurado (girando a câmera com o ponteiro travado/parado no Roblox),
           clique esquerdo/meio ou teclas de câmera/movimento (WASD, Setas, I, O, PageUp/Down, Shift)
           via GetAsyncKeyState.
        3. Qualquer pulso bruto do sensor do mouse (mesmo com cursor travado pelo Roblox), rolagem de
           scroll ou digitação no teclado via GetLastInputInfo, ignorando apenas os inputs gerados pelo bot.
        """
        if self.is_bot_moving_mouse:
            return False

        # 1. Checa deslocamento físico do cursor do mouse na tela
        cur = self._safe_get_cursor()
        prev = self.last_expected_cursor_pos
        dx = abs(cur[0] - prev[0])
        dy = abs(cur[1] - prev[1])
        if (dx + dy) > threshold_px:
            self.last_expected_cursor_pos = cur
            self.last_bot_input_tick = self._get_last_input_tick()
            return True

        # 2. Checa se o usuário está segurando o Botão Direito (mexendo a câmera com mouse parado),
        #    clicando ou segurando teclas de câmera/movimento (Setas, WASD, I/O, Shift, etc.)
        if len(self._held_keys) == 0 and (time.time() - self.last_bot_action_ts) > 0.05:
            try:
                for vk in self.USER_ACTIVE_VKS:
                    if user32.GetAsyncKeyState(vk) & 0x8000:
                        self.last_expected_cursor_pos = cur
                        self.last_bot_input_tick = self._get_last_input_tick()
                        return True
            except Exception:
                pass

        # 3. Checa atividade global de Hardware (Sensor do Mouse travado em câmera, Scroll, Teclado)
        cur_tick = self._get_last_input_tick()
        if cur_tick != 0 and cur_tick != self.last_bot_input_tick:
            # Se o bot está com tecla segurada ou acabou de finalizar um comando (< 0.05s), absorve o tick
            if (time.time() - self.last_bot_action_ts) <= 0.05 or len(self._held_keys) > 0:
                self.last_bot_input_tick = cur_tick
                return False
            diff_ms = (cur_tick - self.last_bot_input_tick) & 0xFFFFFFFF
            if 0 < diff_ms < 0x80000000:
                self.last_bot_input_tick = cur_tick
                self.last_expected_cursor_pos = cur
                return True

        return False

    def force_foreground(self, hwnd: int, settle_delay: float = 0.12) -> bool:
        """
        Garante que a janela do Roblox suba fisicamente para o 1º plano (topo da pilha Z)
        e receba foco ativo do Windows/DirectInput, mesmo quando sobreposta por outra janela do Roblox.
        """
        if not hwnd or not win32gui.IsWindow(hwnd):
            return False
        self.is_bot_moving_mouse = True
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                time.sleep(0.08)

            cur_fg = user32.GetForegroundWindow()
            if cur_fg != hwnd:
                cur_thread = user32.GetWindowThreadProcessId(cur_fg, None) if cur_fg else 0
                my_thread = kernel32.GetCurrentThreadId()
                target_thread = user32.GetWindowThreadProcessId(hwnd, None)

                attached_fg = False
                attached_me = False
                try:
                    if cur_thread and cur_thread != target_thread:
                        attached_fg = bool(user32.AttachThreadInput(cur_thread, target_thread, True))
                    if my_thread and my_thread != target_thread:
                        attached_me = bool(user32.AttachThreadInput(my_thread, target_thread, True))

                    # Destrava restrição de Foreground Lock do Windows via pulso Alt
                    user32.keybd_event(0x12, 0, 0, 0)
                    user32.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)

                    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                    win32gui.SetWindowPos(
                        hwnd,
                        win32con.HWND_TOPMOST,
                        0, 0, 0, 0,
                        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
                    )
                    win32gui.SetWindowPos(
                        hwnd,
                        win32con.HWND_NOTOPMOST,
                        0, 0, 0, 0,
                        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
                    )
                    win32gui.BringWindowToTop(hwnd)
                    user32.SetForegroundWindow(hwnd)
                    user32.SetActiveWindow(hwnd)
                    user32.SetFocus(hwnd)
                finally:
                    if attached_fg:
                        user32.AttachThreadInput(cur_thread, target_thread, False)
                    if attached_me:
                        user32.AttachThreadInput(my_thread, target_thread, False)

                # Notifica o motor do Roblox que a janela foi ativada
                try:
                    win32gui.PostMessage(hwnd, win32con.WM_ACTIVATE, win32con.WA_CLICKACTIVE, 0)
                except Exception:
                    pass

                if settle_delay > 0:
                    time.sleep(settle_delay)
            return True
        except Exception:
            try:
                win32gui.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            return False
        finally:
            self.sync_cursor_baseline()
            self.is_bot_moving_mouse = False

    @staticmethod
    def jitter(base: float, pct: float = 0.08) -> float:
        return base + random.uniform(-base * pct, base * pct)

    def click_at(
        self,
        screen_x: int,
        screen_y: int,
        client_x: int = None,
        client_y: int = None,
        hwnd: int = None,
    ) -> bool:
        """
        Move o cursor até o botão, envia micro-movimentos reais (MOUSEEVENTF_MOVE)
        para o Roblox registrar Hover (inclusive em modais UIBlox/CoreGui), e executa
        um clique físico limpo sem PostMessage conflitante.
        """
        if hwnd:
            self.force_foreground(hwnd)

        self.is_bot_moving_mouse = True
        try:
            sx, sy = int(screen_x), int(screen_y)
            # 1. Posiciona o cursor e executa micro-wiggle físico para forçar o estado de Hover no Roblox
            win32api.SetCursorPos((sx - 3, sy - 1))
            time.sleep(0.03)
            user32.mouse_event(MOUSEEVENTF_MOVE, 3, 1, 0, 0)
            win32api.SetCursorPos((sx, sy))
            self.last_expected_cursor_pos = (sx, sy)
            time.sleep(0.09)

            # 2. Executa o clique físico de hardware (MOUSEEVENTF_LEFTDOWN -> hold -> MOUSEEVENTF_LEFTUP)
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(self.jitter(0.09))
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(self.jitter(0.08))

            self.sync_cursor_baseline()
            return True
        except Exception as e:
            log.warning(f"Erro ao clicar em ({screen_x}, {screen_y}): {e}")
            return False
        finally:
            self.sync_cursor_baseline()
            self.is_bot_moving_mouse = False

    def click_modal_button(
        self,
        screen_x: int,
        screen_y: int,
        hwnd: int = None,
    ) -> bool:
        """
        Clique reforçado específico para botões de modais do Roblox ('Reconectar', 'Reconnect',
        'Retry', 'Try Again'):
        1. Garante foco ativo na janela e dá um clique leve de ativação da janela.
        2. Faz varredura curta de Hover sobre o botão para ativar o estado 'Hovered' do UIBlox.
        3. Executa 2 cliques físicos cadenciados e afasta o cursor 75px acima do botão para não
           cobrir o texto na verificação visual seguinte.
        """
        if hwnd:
            self.force_foreground(hwnd, settle_delay=0.25)

        self.is_bot_moving_mouse = True
        try:
            sx, sy = int(screen_x), int(screen_y)

            # Passo 1: Hover multi-ponto sobre o botão para acordar o listener de mouse do Roblox
            for ox, oy in ((-8, -2), (-3, 0), (3, 1), (0, 0)):
                win32api.SetCursorPos((sx + ox, sy + oy))
                user32.mouse_event(MOUSEEVENTF_MOVE, 1, 0, 0, 0)
                time.sleep(0.03)

            win32api.SetCursorPos((sx, sy))
            self.last_expected_cursor_pos = (sx, sy)
            time.sleep(0.12)

            # Passo 2: 1º Clique físico (ativa a janela caso estivesse inativa ou já aciona o botão)
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.095)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.22)

            # Passo 3: Micro-hover + 2º Clique físico de confirmação no centro do botão
            win32api.SetCursorPos((sx + 2, sy))
            user32.mouse_event(MOUSEEVENTF_MOVE, -2, 0, 0, 0)
            win32api.SetCursorPos((sx, sy))
            time.sleep(0.09)
            user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.095)
            user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.12)

            # Passo 4: Afasta o ponteiro 75px para cima para não cobrir o texto 'Reconectar' na leitura seguinte
            park_x, park_y = sx, max(20, sy - 75)
            win32api.SetCursorPos((park_x, park_y))
            self.last_expected_cursor_pos = (park_x, park_y)
            self.sync_cursor_baseline()
            return True
        except Exception as e:
            log.warning(f"Erro em click_modal_button({screen_x}, {screen_y}): {e}")
            return False
        finally:
            self.sync_cursor_baseline()
            self.is_bot_moving_mouse = False

    def right_click_at(
        self,
        screen_x: int,
        screen_y: int,
        client_x: int = None,
        client_y: int = None,
        hwnd: int = None,
    ) -> bool:
        """
        Executa um clique direito rápido e estacionário (Click to Move do Roblox)
        no ponto (screen_x, screen_y) sem arrastar a câmera.
        """
        if hwnd:
            self.force_foreground(hwnd)

        self.is_bot_moving_mouse = True
        try:
            # 1. Posiciona o cursor e envia micro-movimento ANTES de pressionar o botão direito
            win32api.SetCursorPos((screen_x, screen_y - 1))
            time.sleep(0.025)
            user32.mouse_event(MOUSEEVENTF_MOVE, 0, 1, 0, 0)
            win32api.SetCursorPos((screen_x, screen_y))
            self.last_expected_cursor_pos = (screen_x, screen_y)
            time.sleep(0.04)

            # 2. Clique direito curto e sem movimento (aciona o Click-to-Move sem girar câmera)
            user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
            time.sleep(0.035)
            user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
            time.sleep(0.04)
            self.sync_cursor_baseline()
            return True
        except Exception as e:
            log.warning(f"Erro ao clicar com botão direito em ({screen_x}, {screen_y}): {e}")
            return False
        finally:
            self.sync_cursor_baseline()
            self.is_bot_moving_mouse = False

    # Auto-repeat do teclado físico do Windows: ~250ms de atraso inicial e depois ~30 eventos/s
    AUTOREPEAT_DELAY_SEC = 0.25
    AUTOREPEAT_INTERVAL_SEC = 0.033

    def key_down(self, key: str = "e", hwnd: int = None, autorepeat: bool = False):
        """
        Pressiona e mantém a tecla (ex: 'e') segurada via ScanCode de hardware.

        autorepeat=True imita uma tecla física segurada, reenviando o evento de "tecla pressionada"
        (~30x/s) até key_up(). Sem isso o Roblox cancela o hold do ProximityPrompt '[E] Rebirth'
        cerca de 0,5s depois de começar (o círculo de progresso aparece e some).
        """
        if hwnd:
            self.force_foreground(hwnd)
        k = key.lower()
        self.is_bot_moving_mouse = True
        try:
            pydirectinput.keyDown(k)
            if k in GAME_KEY_MAP:
                vk, scan = GAME_KEY_MAP[k]
                user32.keybd_event(vk, scan, KEYEVENTF_SCANCODE, 0)
            self._held_keys.add(k)
            if autorepeat and k in GAME_KEY_MAP:
                self._start_autorepeat(k)
        except Exception as e:
            log.warning(f"Erro em key_down({k}): {e}")
        finally:
            self.sync_cursor_baseline()
            self.is_bot_moving_mouse = False

    def _start_autorepeat(self, k: str):
        self._stop_autorepeat(k)
        stop_evt = threading.Event()
        vk, scan = GAME_KEY_MAP[k]

        def _run():
            if stop_evt.wait(self.AUTOREPEAT_DELAY_SEC):
                return
            while not stop_evt.is_set():
                try:
                    user32.keybd_event(vk, scan, KEYEVENTF_SCANCODE, 0)
                    self.last_bot_action_ts = time.time()
                except Exception:
                    pass
                stop_evt.wait(self.AUTOREPEAT_INTERVAL_SEC)

        th = threading.Thread(target=_run, name=f"autorepeat-{k}", daemon=True)
        self._autorepeat[k] = (stop_evt, th)
        th.start()

    def _stop_autorepeat(self, k: str):
        entry = self._autorepeat.pop(k, None)
        if entry:
            stop_evt, th = entry
            stop_evt.set()
            th.join(timeout=0.5)

    def key_up(self, key: str = "e"):
        """Solta a tecla que estava segurada."""
        k = key.lower()
        self._stop_autorepeat(k)
        self.is_bot_moving_mouse = True
        try:
            pydirectinput.keyUp(k)
            if k in GAME_KEY_MAP:
                vk, scan = GAME_KEY_MAP[k]
                user32.keybd_event(vk, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0)
        except Exception:
            pass
        finally:
            self._held_keys.discard(k)
            self.sync_cursor_baseline()
            self.is_bot_moving_mouse = False

    def tap_key(self, key: str = "e", hwnd: int = None) -> bool:
        """
        Aperta e solta imediatamente qualquer hotkey nativa do jogo ('z', 'c', 'e', 'q', 'r', 'f', etc.)
        combinando pydirectinput + Hardware ScanCode + PostMessage para 100% de precisão no Roblox.
        """
        if hwnd:
            self.force_foreground(hwnd)
        k = key.lower()
        self.is_bot_moving_mouse = True
        try:
            pydirectinput.keyDown(k)
            if k in GAME_KEY_MAP:
                vk, scan = GAME_KEY_MAP[k]
                user32.keybd_event(vk, scan, KEYEVENTF_SCANCODE, 0)
                if hwnd and win32gui.IsWindow(hwnd):
                    lparam_down = 1 | (scan << 16)
                    win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, lparam_down)
            time.sleep(0.075)
            pydirectinput.keyUp(k)
            if k in GAME_KEY_MAP:
                vk, scan = GAME_KEY_MAP[k]
                user32.keybd_event(vk, scan, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP, 0)
                if hwnd and win32gui.IsWindow(hwnd):
                    lparam_up = 1 | (scan << 16) | (1 << 30) | (1 << 31)
                    win32gui.PostMessage(hwnd, win32con.WM_KEYUP, vk, lparam_up)
            time.sleep(self.jitter(0.05))
            self.sync_cursor_baseline()
            return True
        except Exception as e:
            log.warning(f"Erro ao pressionar tecla {key}: {e}")
            return False
        finally:
            self.sync_cursor_baseline()
            self.is_bot_moving_mouse = False

    def send_background_key(self, key: str, hwnd: int) -> bool:
        """
        Envia tecla silenciosamente em background (WM_ACTIVATE + WM_KEYDOWN/WM_KEYUP com ScanCode)
        para uma janela do Roblox sem roubar o foco da janela ativa.
        """
        if not hwnd or not win32gui.IsWindow(hwnd):
            return False
        k = key.lower()
        vk, scan = GAME_KEY_MAP.get(k, (0x43, 0x2E))
        try:
            lparam_down = 1 | (scan << 16)
            lparam_up = 1 | (scan << 16) | (1 << 30) | (1 << 31)
            win32gui.PostMessage(hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
            win32gui.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, lparam_down)
            time.sleep(0.05)
            win32gui.PostMessage(hwnd, win32con.WM_KEYUP, vk, lparam_up)
            return True
        except Exception:
            return False

    def pulse_anti_afk_windows(self, hwnds: list, return_hwnd: int = None) -> int:
        """
        Executa um giro Anti-AFK (Round-Robin) trazendo cada janela do Roblox para o 1º plano,
        enviando 'C' (Coleta Missão & Fecha Janela) + 'Q' (Fecha Janela) via Hardware ScanCode + PostMessage,
        e devolve o foco para a janela principal ao final.
        """
        if not hwnds:
            return 0

        prev_fg = return_hwnd or user32.GetForegroundWindow()
        pulsed = 0

        for w_hwnd in hwnds:
            if not win32gui.IsWindow(w_hwnd):
                continue
            try:
                # 1. Traz a janela fisicamente para o 1º plano e aguarda estabilizar
                self.force_foreground(w_hwnd, settle_delay=0.38)
                # 2. Envia 'C' (resgata missão se houver + registra input real) e 'Q' (fecha janelas)
                self.tap_key("c", hwnd=w_hwnd)
                time.sleep(0.12)
                self.tap_key("q", hwnd=w_hwnd)
                pulsed += 1
                time.sleep(0.18)
            except Exception as e:
                log.warning(f"Erro no pulso Anti-AFK para HWND {w_hwnd}: {e}")

        if prev_fg and win32gui.IsWindow(prev_fg):
            self.force_foreground(prev_fg, settle_delay=0.20)

        return pulsed

    def hold_key(self, key: str, duration: float, hwnd: int = None) -> bool:
        """Mantém uma tecla pressionada por 'duration' segundos."""
        if hwnd:
            self.force_foreground(hwnd)
        try:
            self.key_down(key, hwnd=None)
            time.sleep(self.jitter(duration))
            self.key_up(key)
            time.sleep(0.05)
            return True
        except Exception as e:
            log.warning(f"Erro ao segurar tecla {key}: {e}")
            self.key_up(key)
            return False

    def release_all(self):
        """Solta preventivamente a tecla 'e'."""
        for k in list(self._held_keys) + ["e"]:
            self.key_up(k)
        self._held_keys.clear()
