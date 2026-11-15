"""
CAP Rebirth Bot - Vision Module v3.5
1. Prompt [E] Light (Foto 1)
2. Prompt [E] Rebirth (Foto 2)
3. Círculo branco de progresso segurando [E] (Foto 3)
4. Checklist lateral dos 8 tiers de ovos com Check verde (Foto 4)
5. Identificação exata dos 7 ovos da barra inferior via templates reais (egg_refs_7.npz) + cor.
"""

import cv2
import numpy as np
from PIL import Image
import win32gui
import win32ui
import ctypes
import os
import sys
import time
from typing import Optional, Tuple, List, Dict

PET_NAMES = [
    ("Common",     42),
    ("Uncommon",   48),
    ("Rare",       60),
    ("Epic",       48),
    ("Legendary",  24),
    ("Prodigious",  6),
    ("Ascended",    6),
    ("Mythical",    6),
]

# Os 7 ovos existentes na rotação do botão da barra inferior:
# Common -> Uncommon -> Rare -> Epic -> Legendary -> Prodigious -> Ascended -> Common
EGG_BAR_CYCLE = [
    "Common",
    "Uncommon",
    "Rare",
    "Epic",
    "Legendary",
    "Prodigious",
    "Ascended",
]

EGG_CYCLE = [name for name, _ in PET_NAMES]


class VisionReader:
    """Lê elementos visuais na janela do Roblox via PrintWindow (Direct Device Context)."""

    def __init__(self):
        self._light_ref: Optional[np.ndarray] = None
        self._rebirth_ref: Optional[np.ndarray] = None
        self._hold_ref: Optional[np.ndarray] = None
        self._hold_inner_ref: Optional[np.ndarray] = None
        self._light_label_mask: Optional[np.ndarray] = None
        self._rebirth_label_mask: Optional[np.ndarray] = None
        self._cached_row_ys: Optional[List[float]] = None
        self._cached_step: Optional[float] = None
        self._cached_shape: Optional[Tuple[int, int]] = None
        self._egg_templs: Dict[str, np.ndarray] = {}
        self._egg_centroids: Dict[str, np.ndarray] = {}
        self._table_crystal_ref: Optional[np.ndarray] = None
        self._pillar_corner_refs: Dict[str, np.ndarray] = {}
        self.custom_altar_calibration: Dict[str, object] = {
            "enabled": False,
            "mode": "table_offset",  # 'table_offset' (relativo à Mesa Central) ou 'screen_ratio' (fixo na janela)
            "offsets": {},           # {'SW': (dx_s, dy_s), 'NW': ..., 'NE': ..., 'SE': ..., 'P0': ...}
            "ratios": {},            # {'SW': (rx, ry), 'NW': ..., 'NE': ..., 'SE': ..., 'P0': ...}
            "walk_wait_sec": 3.8,
        }
        self._load_templates()
        # Memória persistente das tochas acesas no ciclo atual (uma vez acesa, não apaga até concluir o Rebirth)
        self.lit_torches_memory: Dict[str, bool] = {
            "SW": False,
            "NW": False,
            "NE": False,
            "SE": False,
        }
        # Contagem de confirmações visuais consecutivas por tocha (evita falso-positivo à distância)
        self._torch_lit_streak: Dict[str, int] = {
            "SW": 0,
            "NW": 0,
            "NE": 0,
            "SE": 0,
        }
        # Tochas em que o personagem já interagiu com [E] Light neste ciclo
        self._interacted_torches: set = set()

        # ── Sistema Cinemático Inteligente (Dead-Reckoning + Âncora por Estação) ──
        # Mantém a posição estimada da Mesa Branca mesmo quando uma tocha ao Norte (NW/NE) ou poluição visual
        # (Pets de qualquer cor, estrelas gigantes, cristais) encobre temporariamente a mesa:
        self._circuit_active: bool = False
        self._current_station: str = "P0"  # 'P0', 'SW', 'NW', 'NE', 'SE'
        self._last_visual_table_rect: Optional[Tuple[int, int, int, int, float]] = None
        self._last_visual_table_ts: float = 0.0
        self._kinematic_table_xy: Optional[Tuple[float, float]] = None
        self._kinematic_updated_ts: float = 0.0

    # Vetores isométricos canônicos (char_x - rx, char_y - ry) em unidades s = fh / 552.0
    # medidos diretamente nas 5 estações reais do Altar (P0, SW, NW, NE, SE):
    STATION_CHAR_TO_TABLE_S: Dict[str, Tuple[float, float]] = {
        "P0": (44.0, 99.0),
        "SW": (-66.0, 138.0),
        "NW": (8.0, -112.0),
        "NE": (220.0, -28.0),
        "SE": (156.0, 179.0),
    }

    def mark_torch_lit(self, torch_key: str, interacted: bool = False):
        """Registra na memória que uma tocha específica ('SW', 'NW', 'NE', 'SE') foi acesa ou confirmada acesa."""
        if torch_key in self.lit_torches_memory:
            self.lit_torches_memory[torch_key] = True
            self._torch_lit_streak[torch_key] = max(2, self._torch_lit_streak.get(torch_key, 0))
            if interacted:
                self._interacted_torches.add(torch_key)

    def unmark_torch_lit(self, torch_key: str):
        """Remove marcação prematura caso o prompt '[E] Light' apareça ao chegar na tocha (prova real de apagada)."""
        if torch_key in self.lit_torches_memory:
            self.lit_torches_memory[torch_key] = False
            self._torch_lit_streak[torch_key] = 0
            self._interacted_torches.discard(torch_key)

    def reset_lit_torches_memory(self):
        """Limpa a memória das 4 tochas (chamado ao concluir o Rebirth ou iniciar novo ciclo do zero)."""
        for k in ("SW", "NW", "NE", "SE"):
            self.lit_torches_memory[k] = False
            self._torch_lit_streak[k] = 0
        self._interacted_torches.clear()

    def notify_circuit_start(self, frame: Optional[np.ndarray] = None):
        """Inicia o rastreamento cinemático inteligente a partir do ponto inicial (P0)."""
        self._circuit_active = True
        self._current_station = "P0"
        if frame is not None and frame.size > 0:
            rect = self.detect_altar_table_rect(frame, allow_kinematic=False)
            if rect is not None:
                rx, ry, _, _, _ = rect
                self._kinematic_table_xy = (float(rx), float(ry))
                self._kinematic_updated_ts = time.time()

    def notify_movement_target(self, target_key: str, click_x: int, click_y: int, fw: int, fh: int):
        """
        Atualiza de forma inteligente a previsão de onde a Mesa Branca estará na tela após o personagem
        caminhar do centro da tela (char_x, char_y) até (click_x, click_y) rumo à estação 'target_key'.
        No Roblox, quando o personagem anda (dx, dy) na tela, o mundo/Mesa se desloca (-dx, -dy) na câmera!
        """
        if fw <= 0 or fh <= 0:
            return
        s = fh / 552.0
        char_x = fw * 0.50
        char_y = fh * 0.485
        base_station = target_key.replace("_CLOSE", "")

        # 1. Previsão canônica pela estação de destino (onde a mesa fica quando o personagem está em base_station)
        vec = self.STATION_CHAR_TO_TABLE_S.get(base_station)
        canon_rx = (char_x - vec[0] * s) if vec else None
        canon_ry = (char_y - vec[1] * s) if vec else None

        # 2. Previsão diferencial pelo vetor de movimento clicado a partir da última posição conhecida da mesa
        if self._kinematic_table_xy is not None:
            prev_rx, prev_ry = self._kinematic_table_xy
            move_dx = (click_x - char_x) * 0.82
            move_dy = (click_y - char_y) * 0.82
            diff_rx = prev_rx - move_dx
            diff_ry = prev_ry - move_dy
            if canon_rx is not None and canon_ry is not None and not target_key.endswith("_CLOSE"):
                # Combina 55% deslocamento relativo da âncora anterior + 45% geometria canônica da estação
                pred_rx = 0.55 * diff_rx + 0.45 * canon_rx
                pred_ry = 0.55 * diff_ry + 0.45 * canon_ry
            else:
                pred_rx, pred_ry = diff_rx, diff_ry
        elif canon_rx is not None and canon_ry is not None:
            pred_rx, pred_ry = canon_rx, canon_ry
        else:
            return

        self._kinematic_table_xy = (float(pred_rx), float(pred_ry))
        self._kinematic_updated_ts = time.time()
        self._current_station = base_station

    def notify_station_reached(self, station_key: str, frame: Optional[np.ndarray] = None):
        """Informa que o personagem chegou e estabilizou na estação 'station_key' ('P0', 'SW', 'NW', 'NE', 'SE')."""
        base_station = station_key.replace("_CLOSE", "")
        self._current_station = base_station
        if frame is not None and frame.size > 0:
            rect = self.detect_altar_table_rect(frame, allow_kinematic=False)
            if rect is not None:
                rx, ry, _, _, _ = rect
                self._kinematic_table_xy = (float(rx), float(ry))
                self._kinematic_updated_ts = time.time()
                return
            fh, fw = frame.shape[:2]
            s = fh / 552.0
            vec = self.STATION_CHAR_TO_TABLE_S.get(base_station)
            if vec is not None:
                canon_rx = fw * 0.50 - vec[0] * s
                canon_ry = fh * 0.485 - vec[1] * s
                if self._kinematic_table_xy is not None:
                    self._kinematic_table_xy = (
                        0.45 * self._kinematic_table_xy[0] + 0.55 * canon_rx,
                        0.45 * self._kinematic_table_xy[1] + 0.55 * canon_ry,
                    )
                else:
                    self._kinematic_table_xy = (float(canon_rx), float(canon_ry))
                self._kinematic_updated_ts = time.time()

    def notify_circuit_end(self):
        """Encerra o modo cinemático ativo ao concluir ou sair do circuito do Altar."""
        self._circuit_active = False
        self._current_station = "P0"

    def set_custom_altar_calibration(self, calib: Optional[dict]):
        """Atualiza em tempo real a calibração personalizada dos 5 pontos do Altar."""
        if not isinstance(calib, dict):
            self.custom_altar_calibration["enabled"] = False
            return
        self.custom_altar_calibration["enabled"] = bool(calib.get("enabled", False))
        self.custom_altar_calibration["mode"] = str(calib.get("mode", "table_offset"))
        if isinstance(calib.get("offsets"), dict):
            self.custom_altar_calibration["offsets"] = {
                k: (float(v[0]), float(v[1]))
                for k, v in calib["offsets"].items()
                if isinstance(v, (list, tuple)) and len(v) == 2
            }
        if isinstance(calib.get("ratios"), dict):
            self.custom_altar_calibration["ratios"] = {
                k: (float(v[0]), float(v[1]))
                for k, v in calib["ratios"].items()
                if isinstance(v, (list, tuple)) and len(v) == 2
            }
        try:
            self.custom_altar_calibration["walk_wait_sec"] = max(
                1.5, min(10.0, float(calib.get("walk_wait_sec", 3.8)))
            )
        except Exception:
            self.custom_altar_calibration["walk_wait_sec"] = 3.8

    @staticmethod
    def frame_to_screen_and_client(hwnd: int, fx: int, fy: int) -> Tuple[int, int, int, int]:
        """
        Converte qualquer coordenada (fx, fy) do frame capturado por PrintWindow(hwnd, dc, 2)
        para (client_x, client_y, screen_x, screen_y) reais no monitor:
        - PrintWindow captura a janela completa iniciando exatamente em (win_left, win_top) de GetWindowRect(hwnd).
        - Logo, screen_x = win_left + fx e screen_y = win_top + fy (sem desvio da barra de título de 31px
          nem da borda invisível DWM de 8px).
        """
        fx_i, fy_i = int(round(fx)), int(round(fy))
        if not hwnd:
            return (fx_i, fy_i, fx_i, fy_i)
        try:
            win_left, win_top, _, _ = win32gui.GetWindowRect(hwnd)
            scr_x = int(win_left + fx_i)
            scr_y = int(win_top + fy_i)
            try:
                cl_left, cl_top = win32gui.ClientToScreen(hwnd, (0, 0))
                cl_x = max(0, int(scr_x - cl_left))
                cl_y = max(0, int(scr_y - cl_top))
            except Exception:
                cl_x, cl_y = fx_i, fy_i
            return (cl_x, cl_y, scr_x, scr_y)
        except Exception:
            return (fx_i, fy_i, fx_i, fy_i)

    def _load_templates(self):
        """Carrega as imagens de referência para 'E Light', 'E Rebirth', 'Hold E' e os 7 ovos da barra."""
        base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

        light_path = os.path.join(base_dir, "prompt_light_ref.png")
        if os.path.exists(light_path):
            self._light_ref = cv2.imread(light_path)
            if self._light_ref is not None:
                boxes = self._find_green_prompt_boxes(self._light_ref)
                if boxes:
                    bx, by, bw, bh = boxes[0]
                    self._light_label_mask = self._extract_label_mask(
                        self._light_ref[by:by + bh, bx:bx + bw]
                    )

        rebirth_path = os.path.join(base_dir, "prompt_rebirth_ref.png")
        if os.path.exists(rebirth_path):
            self._rebirth_ref = cv2.imread(rebirth_path)
            if self._rebirth_ref is not None:
                boxes = self._find_green_prompt_boxes(self._rebirth_ref)
                if boxes:
                    bx, by, bw, bh = boxes[0]
                    self._rebirth_label_mask = self._extract_label_mask(
                        self._rebirth_ref[by:by + bh, bx:bx + bw]
                    )

        hold_path = os.path.join(base_dir, "prompt_hold_ref.png")
        if os.path.exists(hold_path):
            self._hold_ref = cv2.imread(hold_path)

        hold_inner_path = os.path.join(base_dir, "prompt_hold_inner.png")
        if os.path.exists(hold_inner_path):
            self._hold_inner_ref = cv2.imread(hold_inner_path)
        elif self._hold_ref is not None and self._hold_ref.shape[0] >= 66 and self._hold_ref.shape[1] >= 72:
            self._hold_inner_ref = self._hold_ref[18:66, 24:72].copy()

        egg_refs_path = os.path.join(base_dir, "egg_refs_7.npz")
        if os.path.exists(egg_refs_path):
            try:
                data = np.load(egg_refs_path)
                for name in EGG_BAR_CYCLE:
                    if f"t_{name}" in data and f"c_{name}" in data:
                        self._egg_templs[name] = data[f"t_{name}"]
                        self._egg_centroids[name] = data[f"c_{name}"]
            except Exception:
                pass

        crystal_path = os.path.join(base_dir, "table_crystal_ref.png")
        if os.path.exists(crystal_path):
            self._table_crystal_ref = cv2.imread(crystal_path)

        pillar_refs_path = os.path.join(base_dir, "altar_pillar_refs.npz")
        if os.path.exists(pillar_refs_path):
            try:
                pdata = np.load(pillar_refs_path)
                for key in ("NW", "NE", "SW", "SE"):
                    if key in pdata:
                        self._pillar_corner_refs[key] = pdata[key]
            except Exception:
                pass

    @staticmethod
    def _find_green_prompt_boxes(img: np.ndarray, scale_h: float = 1.0) -> List[Tuple[int, int, int, int]]:
        """Localiza caixas verdes de interação do Roblox ('[E] Light' e '[E] Rebirth') em qualquer resolução."""
        if img is None or img.size == 0:
            return []
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array([68, 55, 185]), np.array([84, 165, 255]))
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_w = max(52, int(64 * scale_h))
        min_h = max(26, int(30 * scale_h))
        boxes = []
        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            area = cv2.contourArea(c)
            if w >= min_w and h >= min_h and 1.3 <= (w / float(h)) <= 3.0 and (area / float(w * h)) > 0.55:
                boxes.append((x, y, w, h))
        return boxes

    @staticmethod
    def _extract_label_mask(box_bgr: np.ndarray) -> Optional[np.ndarray]:
        """Extrai a máscara binária do texto branco à direita do botão [E] ('Light' ou 'Rebirth')."""
        if box_bgr is None or box_bgr.size == 0:
            return None
        bh, bw = box_bgr.shape[:2]
        right = box_bgr[int(bh * 0.18):int(bh * 0.85), int(bw * 0.44):int(bw * 0.95)]
        if right.size == 0:
            return None
        hsv = cv2.cvtColor(right, cv2.COLOR_BGR2HSV)
        white = cv2.inRange(hsv, np.array([0, 0, 200]), np.array([180, 55, 255]))
        ys, xs = np.where(white > 0)
        if len(xs) < 15:
            return None
        return white[ys.min():ys.max() + 1, xs.min():xs.max() + 1]

    def detect_prompt_type(self, frame: np.ndarray) -> Tuple[Optional[str], float, Tuple[int, int]]:
        """
        Identifica se há um prompt ativo na tela e classifica exatamente entre:
        - "LIGHT": Prompt '[E] Light' da tocha (Foto 1)
        - "REBIRTH": Prompt '[E] Rebirth' da mesa (Foto 2)
        - "HOLDING": Círculo branco de progresso em volta da bola cinza [E] (Foto 3)
        - None: Nenhum prompt relevante
        """
        if frame is None or frame.size == 0:
            return None, 0.0, (0, 0)

        h, w = frame.shape[:2]
        if w > 300 and h > 300:
            rx1, rx2 = int(w * 0.12), int(w * 0.88)
            ry1, ry2 = int(h * 0.12), int(h * 0.88)
        else:
            rx1, rx2, ry1, ry2 = 0, w, 0, h

        roi = frame[ry1:ry2, rx1:rx2]
        if roi.size == 0:
            return None, 0.0, (0, 0)

        scale_h = (h / 552.0) if h > 300 else 1.0
        boxes = self._find_green_prompt_boxes(roi, scale_h=scale_h)
        if boxes and self._light_label_mask is not None and self._rebirth_label_mask is not None:
            for (bx, by, bw, bh) in boxes:
                box_crop = roi[by:by + bh, bx:bx + bw]
                lab = self._extract_label_mask(box_crop)
                if lab is None:
                    continue

                lh, lw = lab.shape[:2]
                ratio = lw / float(bh)

                lab_l = cv2.resize(lab, (self._light_label_mask.shape[1], self._light_label_mask.shape[0]))
                lab_r = cv2.resize(lab, (self._rebirth_label_mask.shape[1], self._rebirth_label_mask.shape[0]))

                score_l = float(cv2.matchTemplate(lab_l, self._light_label_mask, cv2.TM_CCOEFF_NORMED)[0, 0])
                score_r = float(cv2.matchTemplate(lab_r, self._rebirth_label_mask, cv2.TM_CCOEFF_NORMED)[0, 0])

                cx = rx1 + bx + bw // 2
                cy = ry1 + by + bh // 2

                if score_r > 0.52 and score_r > score_l and 0.80 <= ratio <= 1.35:
                    return "REBIRTH", score_r, (cx, cy)

                if score_l > 0.52 and score_l > score_r and 0.56 <= ratio <= 0.88:
                    return "LIGHT", score_l, (cx, cy)

                # Fallback Multi-Idioma (Universal: PT '[E] Luz' / '[E] Renascer', EN '[E] Light' / '[E] Rebirth', etc.):
                # se a caixa verde possui o círculo [E] à esquerda e texto branco à direita
                if cy < int(h * 0.82) and 0.24 <= ratio <= 2.15:
                    left_badge = box_crop[int(bh * 0.12):int(bh * 0.88), int(bw * 0.04):int(bw * 0.46)]
                    if left_badge.size > 0:
                        lhsv = cv2.cvtColor(left_badge, cv2.COLOR_BGR2HSV)
                        low_sat_bright = np.count_nonzero((lhsv[:, :, 1] < 48) & (lhsv[:, :, 2] > 145))
                        badge_ratio = low_sat_bright / float(left_badge.shape[0] * left_badge.shape[1])
                        if badge_ratio >= 0.20:
                            t_rect = self.detect_altar_table_rect(frame)
                            if t_rect is not None:
                                trx, try_, trw, trh, _ = t_rect
                                t_mid_x = trx + trw // 2
                                t_mid_y = try_ + trh // 2
                                if (
                                    abs(cx - t_mid_x) <= max(int(105 * scale_h), 75)
                                    and abs(cy - t_mid_y) <= max(int(95 * scale_h), 70)
                                    and ratio >= 0.72
                                ):
                                    return "REBIRTH", max(score_r, 0.62), (cx, cy)
                            if ratio >= 0.90:
                                return "REBIRTH", max(score_r, 0.58), (cx, cy)
                            return "LIGHT", max(score_l, 0.62), (cx, cy)

        hold_found, hold_score, hold_pos = self.detect_hold_circle(frame)
        if hold_found:
            return "HOLDING", hold_score, hold_pos

        return None, 0.0, (0, 0)

    def detect_hold_circle(self, frame: np.ndarray) -> Tuple[bool, float, Tuple[int, int]]:
        """
        Detecta a bola cinza '[E]' com círculo branco de progresso (Foto 3)
        enquanto a tecla E está sendo mantida pressionada para renascer.
        """
        if frame is None or self._hold_inner_ref is None:
            return False, 0.0, (0, 0)

        h, w = frame.shape[:2]
        if w > 300 and h > 300:
            rx1, rx2 = int(w * 0.15), int(w * 0.85)
            ry1, ry2 = int(h * 0.15), int(h * 0.85)
            base_s = h / 784.0
        else:
            rx1, rx2, ry1, ry2 = 0, w, 0, h
            base_s = 1.0

        roi = frame[ry1:ry2, rx1:rx2]
        if roi.size == 0:
            return False, 0.0, (0, 0)

        best_val = 0.0
        best_box = (0, 0, 0, 0)

        for factor in (0.75, 0.90, 1.0, 1.15):
            scale = base_s * factor
            tw = int(round(self._hold_inner_ref.shape[1] * scale))
            th = int(round(self._hold_inner_ref.shape[0] * scale))
            if tw >= roi.shape[1] or th >= roi.shape[0] or tw < 16 or th < 16:
                continue
            st = cv2.resize(self._hold_inner_ref, (tw, th))
            res = cv2.matchTemplate(roi, st, cv2.TM_CCOEFF_NORMED)
            _, mv, _, ml = cv2.minMaxLoc(res)
            if mv > best_val:
                best_val = mv
                best_box = (rx1 + ml[0], ry1 + ml[1], tw, th)

        if best_val > 0.72:
            bx, by, bw, bh = best_box
            patch = frame[by:by + bh, bx:bx + bw]
            hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
            mean_sat = float(hsv[:, :, 1].mean())
            if mean_sat < 38:
                return True, float(best_val), (bx + bw // 2, by + bh // 2)

        return False, float(best_val), (0, 0)

    @staticmethod
    def capture_window(hwnd: int) -> Optional[np.ndarray]:
        """
        Captura a janela completa do Roblox usando PrintWindow(hwnd, dc, 2).
        IMPORTANTE: PrintWindow(hwnd, dc, 2) inicia em (win_left, win_top) de GetWindowRect(hwnd).
        Por isso, o bitmap deve ter dimensão (cap_w, cap_h) suficiente para cobrir a barra de título
        (~31px) + toda a área útil (ch), evitando cortar os 31 pixels inferiores da barra de Ovos/Pets!
        """
        try:
            if not win32gui.IsWindow(hwnd):
                return None

            _, _, cw, ch = win32gui.GetClientRect(hwnd)
            if cw <= 100 or ch <= 100:
                return None

            try:
                win_left, win_top, win_right, win_bottom = win32gui.GetWindowRect(hwnd)
                cl_left, cl_top = win32gui.ClientToScreen(hwnd, (0, 0))
                dx = max(0, cl_left - win_left)
                dy = max(0, cl_top - win_top)
                cap_w = max(cw, dx + cw, win_right - win_left)
                cap_h = max(ch, dy + ch, win_bottom - win_top)
            except Exception:
                cap_w, cap_h = cw, ch

            hwndDC = win32gui.GetWindowDC(hwnd)
            mfcDC = win32ui.CreateDCFromHandle(hwndDC)
            saveDC = mfcDC.CreateCompatibleDC()
            saveBitMap = win32ui.CreateBitmap()
            saveBitMap.CreateCompatibleBitmap(mfcDC, cap_w, cap_h)
            saveDC.SelectObject(saveBitMap)

            ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), 2)

            bmpinfo = saveBitMap.GetInfo()
            bmpstr = saveBitMap.GetBitmapBits(True)
            img = Image.frombuffer(
                "RGB",
                (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                bmpstr,
                "raw",
                "BGRX",
                0,
                1,
            )

            win32gui.DeleteObject(saveBitMap.GetHandle())
            saveDC.DeleteDC()
            mfcDC.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwndDC)

            return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        except Exception:
            return None

    def analyze_tiers(self, frame: np.ndarray) -> List[bool]:
        """
        Analisa individualmente cada um dos 8 checkboxes da lista de ovos (Foto 4):
        0: Common (42/42)
        1: Uncommon (48/48)
        2: Rare (60/60)
        3: Epic (48/48)
        4: Legendary (24/24)
        5: Prodigious (6/6)
        6: Ascended (6/6)
        7: Mythical (6/6)
        """
        if frame is None or frame.size == 0:
            return [False] * 8

        h, w = frame.shape[:2]
        is_crop = (w < 400 and h < 400)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # Matiz do Check verde (Hue 45..66), ignorando a linha verde neon dos pedestais (Hue 70..75)
        mask = cv2.inRange(hsv, np.array([45, 95, 115]), np.array([66, 255, 255]))

        # Escala adaptativa do Checklist para qualquer resolução (552p, 613p, 784p, 961p, 1017p)
        ui_s = (h / 784.0) if not is_crop else 1.0
        min_dim = max(4, int(round(5 * ui_s)))
        max_dim = max(14, int(round(20 * ui_s)))
        min_area = max(8.0, 18.0 * (ui_s ** 2))
        max_area = max(65.0, 155.0 * (ui_s ** 2))
        min_row_dist = max(10, int(round(16 * ui_s)))

        if not is_crop:
            # Em janelas 812x613 ou 4:3, os checkboxes ficam em x = 5..18 (0.006*w), então começamos em 0.003*w!
            col_x1, col_x2 = max(1, int(w * 0.003)), int(w * 0.055)
            roi_y1, roi_y2 = int(h * 0.38), int(h * 0.78)
        else:
            col_x1, col_x2 = 0, int(w * 0.14)
            roi_y1, roi_y2 = int(h * 0.10), int(h * 0.98)

        sub = mask[roi_y1:roi_y2, col_x1:col_x2]
        contours, _ = cv2.findContours(sub, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            area = cv2.contourArea(cnt)
            aspect = cw / float(ch) if ch > 0 else 0.0
            if min_dim <= cw <= max_dim and min_dim <= ch <= max_dim and min_area <= area <= max_area and 0.60 <= aspect <= 1.85:
                gx, gy = col_x1 + x, roi_y1 + y
                # Verifica a borda circular escura do checkbox ao redor do Check verde (rejeita linha neon quebrada)
                ring = hsv[max(0, gy - 3):min(h, gy + ch + 3), max(0, gx - 3):min(w, gx + cw + 3)]
                dark_border = float(np.mean(ring[:, :, 2] < 165)) if ring.size > 0 else 0.0
                if dark_border >= 0.04:
                    candidates.append((gx, gy + ch // 2, cw, ch, area))

        # Estima a grade exata das 8 linhas usando os 7 nomes coloridos do Checklist (Uncommon..Mythical)
        # com V >= 195 em Legendary para nunca confundir com a terra marrom do fundo (V ~ 145)!
        lx1, lx2 = (int(w * 0.032), int(w * 0.155)) if not is_crop else (int(w * 0.16), int(w * 0.55))
        l_hsv = hsv[roi_y1:roi_y2, lx1:lx2]
        anchors = {}
        for r_idx, m in [
            (1, cv2.inRange(l_hsv, np.array([62, 110, 165]), np.array([78, 255, 255]))),   # Uncommon (Verde)
            (2, cv2.inRange(l_hsv, np.array([103, 120, 175]), np.array([118, 255, 255]))), # Rare (Azul)
            (3, cv2.inRange(l_hsv, np.array([134, 100, 165]), np.array([154, 255, 255]))), # Epic (Roxo)
            (4, cv2.inRange(l_hsv, np.array([16, 125, 195]), np.array([25, 255, 255]))),   # Legendary (Dourado V>=195)
            (5, cv2.inRange(l_hsv, np.array([155, 95, 180]), np.array([173, 255, 255]))),  # Prodigious (Rosa)
            (6, cv2.inRange(l_hsv, np.array([85, 90, 185]), np.array([99, 255, 255]))),    # Ascended (Ciano)
            (
                7,
                cv2.inRange(l_hsv, np.array([0, 130, 175]), np.array([6, 255, 255]))
                | cv2.inRange(l_hsv, np.array([173, 130, 175]), np.array([180, 255, 255])),
            ),
        ]:
            ys = np.where(m > 0)[0]
            if len(ys) >= 8 and (ys.max() - ys.min()) <= int(round(26 * ui_s)):
                anchors[r_idx] = float(np.median(ys)) + roi_y1

        row_ys = None
        step = None
        if len(anchors) >= 2:
            keys = sorted(anchors.keys())
            pair_steps = []
            for idx_a in range(len(keys)):
                for idx_b in range(idx_a + 1, len(keys)):
                    ka, kb = keys[idx_a], keys[idx_b]
                    st_ab = (anchors[kb] - anchors[ka]) / float(kb - ka)
                    if (13 * ui_s) <= st_ab <= (36 * ui_s):
                        pair_steps.append(st_ab)
            if pair_steps:
                step = float(np.median(pair_steps))
                y0_est = [anchors[k] - k * step for k in keys]
                y0 = float(np.median(y0_est))
                row_ys = [y0 + i * step for i in range(8)]
                self._cached_row_ys = row_ys
                self._cached_step = step
                self._cached_shape = (h, w)

        tier_status = [False] * 8
        if not candidates:
            return tier_status

        xs = [c[0] for c in candidates]
        best_x = max(set(xs), key=lambda target: sum(1 for x in xs if abs(x - target) <= 3))
        raw_valid = [c for c in candidates if abs(c[0] - best_x) <= 3]
        raw_valid.sort(key=lambda c: c[1])

        # Desduplica contornos na mesma linha vertical
        valid = []
        for c in raw_valid:
            if not valid or abs(c[1] - valid[-1][1]) >= min_row_dist:
                valid.append(c)
            elif c[4] > valid[-1][4]:
                valid[-1] = c

        # Se exatamente 8 checks válidos estão presentes com espaçamento vertical uniforme (grade de 8 linhas),
        # valida 8/8 diretamente e atualiza o cache das linhas (imune a textos 3D flutuando sobre os nomes)!
        if len(valid) == 8:
            diffs8 = [valid[i + 1][1] - valid[i][1] for i in range(7)]
            med8 = float(np.median(diffs8))
            if (13 * ui_s) <= med8 <= (36 * ui_s) and all(0.72 * med8 <= d <= 1.30 * med8 for d in diffs8):
                self._cached_row_ys = [float(c[1]) for c in valid]
                self._cached_step = med8
                self._cached_shape = (h, w)
                return [True] * 8

        if row_ys is None and self._cached_row_ys is not None and self._cached_shape == (h, w):
            row_ys = self._cached_row_ys
            step = self._cached_step

        if row_ys is not None and step is not None:
            for _, cy, _, _, _ in valid:
                best_i = min(range(8), key=lambda i: abs(cy - row_ys[i]))
                if abs(cy - row_ys[best_i]) <= step * 0.42:
                    tier_status[best_i] = True
            return tier_status

        # Sem âncoras de texto em cache: exige espaçamento vertical uniforme entre os checks consecutivos
        if len(valid) >= 2:
            diffs = [valid[i + 1][1] - valid[i][1] for i in range(len(valid) - 1)]
            med_step = float(np.median(diffs))
            if (13 * ui_s) <= med_step <= (36 * ui_s) and all(0.72 * med_step <= d <= 1.30 * med_step for d in diffs):
                for idx in range(min(len(valid), 8)):
                    tier_status[idx] = True
        elif len(valid) == 1:
            tier_status[0] = True

        return tier_status

    def checklist_occluded_rows(self, frame: np.ndarray, min_dark_ratio: float = 0.55) -> List[int]:
        """
        Retorna os índices (0..7) das linhas do Checklist cobertas por um painel escuro e sem cor,
        como o painel de Estatísticas de desempenho do Roblox (Data Send / Instances / Memory).
        Numa linha encoberta ~80% da área fica cinza-escura (S < 45, V 15..75); numa linha normal, < 3%.
        Usa a grade de linhas calculada pela última chamada de analyze_tiers() no mesmo tamanho de janela.
        """
        if frame is None or frame.size == 0:
            return []
        h, w = frame.shape[:2]
        if self._cached_row_ys is None or self._cached_step is None or self._cached_shape != (h, w):
            return []
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        x1, x2 = max(1, int(w * 0.003)), int(w * 0.155)
        half = self._cached_step * 0.4
        rows = []
        for i, y in enumerate(self._cached_row_ys[:8]):
            y1, y2 = max(0, int(y - half)), min(h, int(y + half))
            reg = hsv[y1:y2, x1:x2]
            if reg.size == 0:
                continue
            dark = np.mean((reg[:, :, 1] < 45) & (reg[:, :, 2] >= 15) & (reg[:, :, 2] <= 75))
            if dark >= min_dark_ratio:
                rows.append(i)
        return rows

    def is_all_tiers_complete(self, frame: np.ndarray) -> bool:
        """Retorna True quando todos os 8 tiers (240/240) estão com o Check verde marcado."""
        return all(self.analyze_tiers(frame))

    @staticmethod
    def get_target_from_tiers(tier_status: List[bool]) -> Tuple[int, str]:
        """
        Retorna o índice (0..7) e nome do primeiro tier que ainda NÃO tem o Check verde.
        Se todos os 8 estiverem marcados (240/240), retorna (8, "REBIRTH_READY").
        """
        for i in range(8):
            if not tier_status[i]:
                return i, EGG_CYCLE[i]
        return 8, "REBIRTH_READY"

    @staticmethod
    def get_expected_bar_egg(tier_idx: int) -> str:
        """
        Retorna qual dos 7 ovos da barra inferior deve estar ativo para o tier_idx (0..7) do Checklist:
        - 0 (Common)     -> Common
        - 1 (Uncommon)   -> Uncommon
        - 2 (Rare)       -> Rare
        - 3 (Epic)       -> Epic
        - 4 (Legendary)  -> Legendary
        - 5 (Prodigious) -> Prodigious
        - 6 (Ascended) e 7 (Mythical) -> Ascended
        """
        return EGG_BAR_CYCLE[max(0, min(tier_idx, 6))]

    def _locate_and_classify_egg(self, frame: np.ndarray) -> Tuple[int, int, str]:
        """
        Localiza o botão de ovo na barra inferior 100% POR IMAGEM (independente de monitor 17" ou 32",
        4:3 ou 16:9) combinando a âncora visual do botão verde 'Pets' e os 7 templates reais de ovos
        (egg_refs_7.npz) escalados proporcionalmente à altura 'h' da janela.
        Retorna (egg_cx, egg_cy, egg_name).
        """
        if frame is None or frame.size == 0:
            return (0, 0, "Common")

        h, w = frame.shape[:2]
        s_egg = h / 790.0  # fallback caso o botão Pets não seja encontrado

        # 1. Localiza visualmente o botão verde 'Pets' na barra inferior para ancorar a escala e busca
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        ry1, ry2 = int(h * 0.82), h
        rx1, rx2 = int(w * 0.32), int(w * 0.68)
        sub_pets = hsv[ry1:ry2, rx1:rx2]
        exp_x = int(round(w * 0.50 - 374 * (h / 961.0)))
        exp_y = int(round(h * 0.945))
        found_pets = False
        pets_bw = int(round(140 * s_egg))
        pets_bh = int(round(56 * s_egg))

        if sub_pets.size > 0:
            mask_pets = cv2.inRange(sub_pets, np.array([68, 95, 185]), np.array([85, 215, 255]))
            cnts, _ = cv2.findContours(mask_pets, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if cnts:
                c = max(cnts, key=cv2.contourArea)
                if cv2.contourArea(c) > 100:
                    bx, by, bw, bh = cv2.boundingRect(c)
                    pets_cx = rx1 + bx + bw // 2
                    pets_cy = ry1 + by + bh // 2
                    pets_bw, pets_bh = bw, bh
                    # A barra inferior do Roblox escala proporcionalmente à largura real 'bw' do botão Pets!
                    s_egg = max(0.35, bw / 140.0)
                    exp_x = int(round(pets_cx - bw * 2.22))
                    exp_y = int(round(pets_cy - bh * 0.06))
                    found_pets = True

        # 2. Busca por Template Multi-Escala + Centróide de Cor nos 7 ovos reais (restrita à faixa da barra inferior)
        if self._egg_templs and self._egg_centroids:
            pad_x = max(18, int(round(pets_bw * 0.28))) if found_pets else max(32, int(round(140 * s_egg)))
            pad_y = max(16, int(round(pets_bh * 0.62))) if found_pets else max(28, int(round(45 * s_egg)))
            sx1 = max(0, exp_x - pad_x)
            sy1 = max(0, exp_y - pad_y)
            sx2 = min(w, exp_x + pad_x)
            sy2 = min(h, exp_y + pad_y)
            roi = frame[sy1:sy2, sx1:sx2]

            if roi.shape[0] >= 20 and roi.shape[1] >= 20:
                best_name = "Common"
                best_score = -999.0
                best_pt = (exp_x, exp_y)

                for name in EGG_BAR_CYCLE:
                    t = self._egg_templs.get(name)
                    c_ref = self._egg_centroids.get(name)
                    if t is None or c_ref is None:
                        continue
                    for factor in (0.82, 0.91, 1.00, 1.09):
                        tw = max(12, int(round(t.shape[1] * s_egg * factor)))
                        th = max(12, int(round(t.shape[0] * s_egg * factor)))
                        if tw >= roi.shape[1] or th >= roi.shape[0]:
                            continue
                        st = cv2.resize(t, (tw, th))
                        res = cv2.matchTemplate(roi, st, cv2.TM_CCOEFF_NORMED)
                        _, mv, _, ml = cv2.minMaxLoc(res)
                        ecx = sx1 + ml[0] + tw // 2
                        ecy = sy1 + ml[1] + th // 2
                        r_p = max(5, int(round(11 * s_egg)))
                        patch = frame[
                            max(0, ecy - r_p):min(h, ecy + r_p),
                            max(0, ecx - r_p):min(w, ecx + r_p),
                        ]
                        if patch.size > 0:
                            bgr_m = patch.mean(axis=(0, 1))
                            color_dist = float(np.linalg.norm(bgr_m - c_ref[:3])) / 255.0
                            dist_pen = 0.15 * (
                                abs(ecx - exp_x) / float(max(1, pad_x))
                                + abs(ecy - exp_y) / float(max(1, pad_y))
                            )
                            score = float(mv) - 0.8 * color_dist - dist_pen
                            if score > best_score:
                                best_score = score
                                best_name = name
                                best_pt = (ecx, ecy)

                if best_score > 0.30:
                    return (best_pt[0], best_pt[1], best_name)

        # 3. Fallback cromático no centro estimado (exp_x, exp_y)
        r = max(8, int(round(14 * s_egg)))
        egg_crop = frame[max(0, exp_y - r):min(h, exp_y + r), max(0, exp_x - r):min(w, exp_x + r)]
        if egg_crop.size == 0:
            return (exp_x, exp_y, "Common")

        hsv_c = cv2.cvtColor(egg_crop, cv2.COLOR_BGR2HSV)
        H = float(hsv_c[:, :, 0].mean())
        S = float(hsv_c[:, :, 1].mean())
        B = float(egg_crop[:, :, 0].mean())
        G = float(egg_crop[:, :, 1].mean())
        R = float(egg_crop[:, :, 2].mean())

        if S < 45:
            e_name = "Common"
        elif 25 <= H <= 48 and G > 200 and R > 180 and B < 140:
            e_name = "Uncommon"
        elif 80 <= H <= 102 and B > 170 and G > 170 and R < 145:
            e_name = "Ascended"
        elif 102 < H <= 125 and B > G + 50 and B > R + 70:
            e_name = "Rare"
        elif 125 < H <= 165 or (R > 180 and B > 180 and G < 150):
            e_name = "Epic"
        elif R > 195 and 105 <= G <= 165 and 100 <= B <= 165 and abs(G - B) < 30:
            e_name = "Prodigious"
        elif R > G + 30 and G > B + 20 and B < 95:
            e_name = "Legendary"
        else:
            e_name = "Common"

        return (exp_x, exp_y, e_name)

    def get_egg_button_coords(self, hwnd: int, frame: np.ndarray) -> Tuple[int, int, int, int]:
        """
        Retorna (client_x, client_y, screen_x, screen_y) no centro exato do botão de ovo
        localizado por imagem (compatível com monitores 17", 32", 4:3, 16:9, etc.).
        IMPORTANTE: PrintWindow(hwnd, dc, 2) captura a partir de (win_left, win_top) de GetWindowRect.
        """
        rel_egg_x, rel_egg_y, _ = self._locate_and_classify_egg(frame)
        try:
            win_left, win_top, _, _ = win32gui.GetWindowRect(hwnd) if hwnd else (0, 0, 0, 0)
        except Exception:
            win_left, win_top = 0, 0

        screen_x = win_left + rel_egg_x
        screen_y = win_top + rel_egg_y

        return (rel_egg_x, rel_egg_y, screen_x, screen_y)

    def detect_active_egg(self, frame: np.ndarray) -> str:
        """
        Identifica com precisão qual dos 7 ovos da barra inferior está selecionado no momento
        (Common, Uncommon, Rare, Epic, Legendary, Prodigious, Ascended).
        """
        if frame is None or frame.size == 0:
            return "Common"
        _, _, egg_name = self._locate_and_classify_egg(frame)
        return egg_name

    # ── Detecção Visual Dinâmica do Altar (Mesa Central + Quinas dos Pilares) ──

    @staticmethod
    def _build_all_egg_tiers_mask(frame: np.ndarray, hsv: np.ndarray) -> np.ndarray:
        """
        Constrói uma máscara universal abrangendo TODAS as 8 cores de Ovos/Pets da lista do Checklist
        (Common, Uncommon, Rare, Epic, Legendary, Prodigious, Ascended, Mythical) + Estrelas Douradas (★)
        + amostragem dinâmica das cores reais dos nomes no Checklist da tela atual.
        Garante que a fileira de Pets na base da tela nunca bloqueie a detecção da parte inferior da
        Mesa Branca quando o jogador está posicionado ao Norte (NW / NE), seja qual for o ovo ativo!
        """
        fh, fw = hsv.shape[:2]
        # 1. Faixas HSV das 8 cores de referência dos Ovos/Pets + Estrelas Douradas:
        # - Common: Branco/Cinza claro (já coberto por white_surface, mas reforçado aqui)
        m_common = cv2.inRange(hsv, np.array([0, 0, 185], dtype=np.uint8), np.array([180, 55, 255], dtype=np.uint8))
        # - Uncommon: Verde (#52E565)
        m_uncommon = cv2.inRange(hsv, np.array([35, 50, 150], dtype=np.uint8), np.array([82, 255, 255], dtype=np.uint8))
        # - Rare: Azul Royal/Celeste (#4BA3FF)
        m_rare = cv2.inRange(hsv, np.array([100, 60, 150], dtype=np.uint8), np.array([126, 255, 255], dtype=np.uint8))
        # - Epic: Roxo/Violeta (#B56BFF)
        m_epic = cv2.inRange(hsv, np.array([125, 50, 145], dtype=np.uint8), np.array([148, 255, 255], dtype=np.uint8))
        # - Legendary + Estrelas Douradas (★): Amarelo/Ouro/Laranja (#FFD13B / #FFA826)
        m_legendary = cv2.inRange(hsv, np.array([13, 65, 165], dtype=np.uint8), np.array([35, 255, 255], dtype=np.uint8))
        # - Prodigious: Rosa/Magenta (#FF77B8)
        m_prodigious = cv2.inRange(hsv, np.array([144, 45, 165], dtype=np.uint8), np.array([175, 250, 255], dtype=np.uint8))
        # - Ascended: Ciano/Água-marinha (#74E8FF)
        m_ascended = cv2.inRange(hsv, np.array([82, 45, 165], dtype=np.uint8), np.array([102, 250, 255], dtype=np.uint8))
        # - Mythical: Vermelho/Carmesim (#FF4B4B)
        m_myth_1 = cv2.inRange(hsv, np.array([0, 65, 155], dtype=np.uint8), np.array([13, 255, 255], dtype=np.uint8))
        m_myth_2 = cv2.inRange(hsv, np.array([174, 65, 155], dtype=np.uint8), np.array([180, 255, 255], dtype=np.uint8))

        combined = m_common
        for m in (m_uncommon, m_rare, m_epic, m_legendary, m_prodigious, m_ascended, m_myth_1, m_myth_2):
            combined = cv2.bitwise_or(combined, m)

        # 2. Amostragem dinâmica complementar dos textos coloridos do Checklist à esquerda (se visível)
        cy1, cy2 = int(fh * 0.40), int(fh * 0.72)
        cx1, cx2 = int(fw * 0.04), int(fw * 0.14)
        if cy2 > cy1 and cx2 > cx1:
            chk_hsv = hsv[cy1:cy2, cx1:cx2]
            sat_pixels = chk_hsv[(chk_hsv[:, :, 1] >= 65) & (chk_hsv[:, :, 2] >= 165)]
            if len(sat_pixels) > 25:
                hues = sat_pixels[:, 0]
                hist = np.bincount(hues, minlength=181)
                for h_peak in np.where(hist >= max(8, len(hues) // 18))[0]:
                    h_lo = max(0, int(h_peak) - 6)
                    h_hi = min(180, int(h_peak) + 6)
                    dyn_m = cv2.inRange(
                        hsv, np.array([h_lo, 45, 155], dtype=np.uint8), np.array([h_hi, 255, 255], dtype=np.uint8)
                    )
                    combined = cv2.bitwise_or(combined, dyn_m)

        # 3. Dilatação 3x3 para cobrir também o contorno escuro fino dos círculos dos Pets e Estrelas
        k3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        return cv2.dilate(combined, k3, iterations=1)

    def _update_kinematic_anchor(
        self, rx: int, ry: int, rw: int, rh: int, score: float, fw: int, fh: int
    ) -> Tuple[int, int, int, int, float]:
        """Salva a leitura visual validada da Mesa Branca e sincroniza o rastreador cinemático."""
        rect = (int(rx), int(ry), int(rw), int(rh), float(score))
        now = time.time()
        self._last_visual_table_rect = rect
        self._last_visual_table_ts = now
        self._kinematic_table_xy = (float(rx), float(ry))
        self._kinematic_updated_ts = now
        return rect

    def detect_altar_table_rect(
        self, frame: np.ndarray, allow_kinematic: bool = True
    ) -> Optional[Tuple[int, int, int, int, float]]:
        """
        Localiza o retângulo exato (rx, ry, rw, rh, score) da MESA BRANCA central do Altar:
        1. Estágio 1A (PRIORIDADE - Template Completo do Cristal da Mesa dentro do ROI do Altar):
           Recorta o mapa de correlação para a área válida do Altar ANTES de calcular max_v (impedindo
           que cristais vermelhos de recompensa na lateral direita x > 0.76*fw distorçam o limiar!),
           validando os 3 lados da Mesa Branca (esquerda, direita e abaixo) com suporte às 8 cores de Ovos/Pets.
        2. Estágio 1B (POSIÇÃO NORTE - Topo 58% do Cristal da Mesa):
           Quando o jogador está ao Norte (NW / entre NW e NE / NE), a Mesa Branca fica na parte inferior
           da tela e os círculos de Pets cobrem a base do Cristal Vermelho. Compara os 58% superiores do
           Cristal + lados brancos + base com máscara das 8 cores de Ovos/Pets!
        3. Estágio 2 (FALLBACK ESTRITO):
           Superfície retangular horizontal branca (ou branca+Pets na metade inferior) com cristal/número
           laranja na borda superior e cercada pelo piso cinza-frio do Altar.
        4. Estágio 3 (RASTREAMENTO CINEMÁTICO INTELIGENTE):
           Durante a volta ativa no Altar (`_circuit_active`), se elementos visuais gigantes cobrirem
           momentaneamente toda a mesa, calcula a posição exata da Mesa Branca a partir do ponto original
           (P0) e do deslocamento de cada movimento realizado!
        """
        if frame is None or frame.size == 0:
            return None

        fh, fw = frame.shape[:2]
        s = fh / 552.0

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        # O piso cinza do Altar tem V = 196..208; o topo da Mesa Branca tem V = 222..250!
        # Exigir V >= 218 em white_cool impede 100% que o piso cinza do Altar seja confundido com a Mesa Branca!
        white_cool = cv2.inRange(
            hsv, np.array([75, 0, 218], dtype=np.uint8), np.array([138, 42, 255], dtype=np.uint8)
        )
        white_warm = cv2.inRange(
            hsv, np.array([14, 0, 220], dtype=np.uint8), np.array([45, 36, 255], dtype=np.uint8)
        )
        white_surface = cv2.bitwise_or(white_cool, white_warm)

        # Máscara universal para TODAS as 8 cores de Ovos/Pets do Checklist + Estrelas Douradas (★)
        all_egg_tiers_mask = self._build_all_egg_tiers_mask(frame, hsv)
        white_or_all_pets = cv2.bitwise_or(white_surface, all_egg_tiers_mask)

        # ── Estágio 1A (PRIORIDADE): Cristal Completo da Mesa Branca validado nos 3 lados ──
        if self._table_crystal_ref is not None:
            ch_ref, cw_ref = self._table_crystal_ref.shape[:2]
            best_c = None
            x_min, x_max = int(fw * 0.14), int(fw * 0.70)
            y_min, y_max = int(fh * 0.06), int(fh * 0.88)

            for factor in (0.90, 1.00, 1.10):
                tw = max(14, int(round(cw_ref * s * factor)))
                th = max(18, int(round(ch_ref * s * factor)))
                if tw >= fw or th >= fh:
                    continue
                tmpl = (
                    cv2.resize(self._table_crystal_ref, (tw, th))
                    if (tw, th) != (cw_ref, ch_ref)
                    else self._table_crystal_ref
                )
                res = cv2.matchTemplate(frame, tmpl, cv2.TM_CCOEFF_NORMED)
                rh_res, rw_res = res.shape[:2]
                rx_lo, rx_hi = max(0, min(rw_res - 1, x_min)), max(1, min(rw_res, x_max))
                ry_lo, ry_hi = max(0, min(rh_res - 1, y_min)), max(1, min(rh_res, y_max))
                res_roi = res[ry_lo:ry_hi, rx_lo:rx_hi]
                if res_roi.size == 0:
                    continue
                _, max_v, _, _ = cv2.minMaxLoc(res_roi)
                # Exige max_v >= 0.66 para nunca confundir o texto 'Mythical' ou lenha de tocha (0.51..0.53)
                # quando a conta ainda está em < 240/240 (ex: 150 / 240 sem cristal na mesa)!
                if max_v < 0.66:
                    continue
                thresh_v = max(0.66, max_v - 0.12)
                ys_r, xs_r = np.where(res_roi >= thresh_v)
                step_stride = 2 if len(xs_r) > 140 else 1
                for xr, yr in zip(xs_r[::step_stride], ys_r[::step_stride]):
                    x = int(rx_lo + xr)
                    y = int(ry_lo + yr)
                    # Os lados esquerdo (18*s) e direito da Mesa Branca são SEMPRE brancos puros (white_surface)
                    lx1, lx2 = max(0, x - int(19 * s)), max(1, x - int(2 * s))
                    ly1, ly2 = max(0, y + int(4 * s)), min(fh, y + th + int(15 * s))
                    w_left = (
                        float(np.mean(white_surface[ly1:ly2, lx1:lx2] > 0))
                        if lx2 > lx1 and ly2 > ly1
                        else 0.0
                    )
                    rx1 = min(fw - 1, x + tw + int(45 * s))
                    rx2 = min(fw, x + tw + int(95 * s))
                    w_right = (
                        float(np.mean(white_surface[ly1:ly2, rx1:rx2] > 0))
                        if rx2 > rx1 and ly2 > ly1
                        else 0.0
                    )
                    if w_right <= 0.35:
                        continue

                    by1 = min(fh - 1, y + th + int(4 * s))
                    by2 = min(fh, y + th + int(38 * s))
                    bx1 = max(0, x - int(15 * s))
                    bx2 = min(fw, x + tw + int(65 * s))
                    # Abaixo do cristal: se a mesa estiver na metade inferior (posições ao Norte), aceita também as 8 cores de Ovos/Pets!
                    b_mask = white_or_all_pets if y >= int(fh * 0.52) else white_surface
                    w_below = (
                        float(np.mean(b_mask[by1:by2, bx1:bx2] > 0))
                        if bx2 > bx1 and by2 > by1
                        else 0.0
                    )

                    # Quando o personagem está em NE, a borda esquerda da mesa (18*s) encosta nos números '24/24' do Checklist (x <= 0.26*fw),
                    # mas o lado direito (w_right > 0.75) e a parte inferior (w_below > 0.75) da mesa continuam 100% brancos!
                    left_ok = (w_left > 0.22) or (x <= int(fw * 0.26) and w_right > 0.75 and w_below > 0.75)
                    if not left_ok:
                        continue

                    if w_below > 0.35:
                        sc = float(res[y, x])
                        w_avg = (w_left + w_right + w_below) / 3.0
                        cand = (sc * (0.6 + 0.4 * w_avg), sc, int(x), int(y))
                        if best_c is None or cand[0] > best_c[0]:
                            best_c = cand

            if best_c is not None and best_c[1] >= 0.72:
                _, sc_c, cx_c, cy_c = best_c
                t_rx = max(2, int(round(cx_c - 18 * s)))
                t_ry = max(2, int(round(cy_c + 8 * s)))
                t_rw = min(fw - t_rx - 2, int(round(150 * s)))
                t_rh = min(fh - t_ry - 2, int(round(92 * s)))
                return self._update_kinematic_anchor(t_rx, t_ry, t_rw, t_rh, float(sc_c), fw, fh)

            # ── Estágio 1B (ESPECIAL POSIÇÃO NORTE): Topo 58% do Cristal quando Pets cobrem a base ──
            best_top = None
            y_north_min = int(fh * 0.50)
            for factor in (0.90, 1.00, 1.10):
                tw = max(14, int(round(cw_ref * s * factor)))
                th = max(18, int(round(ch_ref * s * factor)))
                if tw >= fw or th >= fh:
                    continue
                tmpl_full = (
                    cv2.resize(self._table_crystal_ref, (tw, th))
                    if (tw, th) != (cw_ref, ch_ref)
                    else self._table_crystal_ref
                )
                th_top = max(11, int(round(th * 0.58)))
                tw_top = max(11, int(round(tw * 0.75)))
                tmpl_top = tmpl_full[:th_top, :tw_top]
                res_t = cv2.matchTemplate(frame, tmpl_top, cv2.TM_CCOEFF_NORMED)
                rh_t, rw_t = res_t.shape[:2]
                rx_lo, rx_hi = max(0, min(rw_t - 1, x_min)), max(1, min(rw_t, x_max))
                ry_lo, ry_hi = max(0, min(rh_t - 1, y_north_min)), max(1, min(rh_t, y_max))
                if ry_hi <= ry_lo or rx_hi <= rx_lo:
                    continue
                res_t_roi = res_t[ry_lo:ry_hi, rx_lo:rx_hi]
                _, max_vt, _, _ = cv2.minMaxLoc(res_t_roi)
                if max_vt < 0.78:
                    continue
                thresh_vt = max(0.78, max_vt - 0.08)
                ys_t, xs_t = np.where(res_t_roi >= thresh_vt)
                step_stride = 2 if len(xs_t) > 120 else 1
                for xr, yr in zip(xs_t[::step_stride], ys_t[::step_stride]):
                    x = int(rx_lo + xr)
                    y = int(ry_lo + yr)
                    # Na posição Norte, o topo da mesa (ao lado dos 58% superiores do cristal) é branco puro!
                    lx1, lx2 = max(0, x - int(24 * s)), max(1, x - int(2 * s))
                    ly1, ly2 = max(0, y + int(4 * s)), min(fh, y + th_top + int(6 * s))
                    w_left = (
                        float(np.mean(white_surface[ly1:ly2, lx1:lx2] > 0))
                        if lx2 > lx1 and ly2 > ly1
                        else 0.0
                    )
                    if w_left <= 0.25:
                        continue

                    rx1 = min(fw - 1, x + tw + int(45 * s))
                    rx2 = min(fw, x + tw + int(95 * s))
                    w_right = (
                        float(np.mean(white_surface[ly1:ly2, rx1:rx2] > 0))
                        if rx2 > rx1 and ly2 > ly1
                        else 0.0
                    )
                    if w_right <= 0.35:
                        continue

                    by1 = min(fh - 1, y + th + int(4 * s))
                    by2 = min(fh, y + th + int(38 * s))
                    bx1 = max(0, x - int(15 * s))
                    bx2 = min(fw, x + tw + int(65 * s))
                    w_below = (
                        float(np.mean(white_or_all_pets[by1:by2, bx1:bx2] > 0))
                        if bx2 > bx1 and by2 > by1
                        else 0.0
                    )
                    if w_below > 0.35:
                        sc = float(res_t[y, x])
                        w_avg = (w_left + w_right + w_below) / 3.0
                        cand = (sc * (0.6 + 0.4 * w_avg), sc, int(x), int(y))
                        if best_top is None or cand[0] > best_top[0]:
                            best_top = cand

            if best_top is not None:
                _, sc_t, cx_t, cy_t = best_top
                t_rx = max(2, int(round(cx_t - 18 * s)))
                t_ry = max(2, int(round(cy_t + 8 * s)))
                t_rw = min(fw - t_rx - 2, int(round(150 * s)))
                t_rh = min(fh - t_ry - 2, int(round(92 * s)))
                return self._update_kinematic_anchor(t_rx, t_ry, t_rw, t_rh, float(sc_t), fw, fh)

            if best_c is not None:
                _, sc_c, cx_c, cy_c = best_c
                t_rx = max(2, int(round(cx_c - 18 * s)))
                t_ry = max(2, int(round(cy_c + 8 * s)))
                t_rw = min(fw - t_rx - 2, int(round(150 * s)))
                t_rh = min(fh - t_ry - 2, int(round(92 * s)))
                return self._update_kinematic_anchor(t_rx, t_ry, t_rw, t_rh, float(sc_c), fw, fh)

        # ── Estágio 2 (GEOMETRIA 3D DA LAJE BRANCA): Funciona COM ou SEM cristal (ex: 150 / 240)! ──
        # O tampo branco da Mesa Central tem ~150*s x 92*s (V >= 218) e é cercado acima, abaixo e nos lados
        # pelo piso cinza do Altar (V = 175..214), nunca confundindo com a escada sul (que é larga demais)!
        grey_floor = cv2.inRange(
            hsv, np.array([95, 8, 175], dtype=np.uint8), np.array([132, 42, 214], dtype=np.uint8)
        )
        roi_mask = np.zeros_like(white_surface)
        roi_mask[int(fh * 0.14) : int(fh * 0.72), int(fw * 0.18) : int(fw * 0.76)] = white_surface[
            int(fh * 0.14) : int(fh * 0.72), int(fw * 0.18) : int(fw * 0.76)
        ]
        k_close = cv2.getStructuringElement(
            cv2.MORPH_RECT, (max(7, int(round(26 * s))), max(5, int(round(14 * s))))
        )
        closed = cv2.morphologyEx(roi_mask, cv2.MORPH_CLOSE, k_close)
        cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best_rect = None
        for c in cnts:
            rx, ry, rw, rh = cv2.boundingRect(c)
            if not (int(122 * s) <= rw <= int(198 * s) and int(60 * s) <= rh <= int(118 * s)):
                continue
            ar = rw / float(max(1, rh))
            if not (1.35 <= ar <= 2.35):
                continue
            c_area = cv2.contourArea(c)
            fill = c_area / float(max(1, rw * rh))
            if fill < 0.68:
                continue
            pad = max(12, int(round(28 * s)))
            g_top = grey_floor[max(0, ry - pad) : max(1, ry - 4), rx : rx + rw]
            g_bot = grey_floor[min(fh - 1, ry + rh + 4) : min(fh, ry + rh + pad), rx : rx + rw]
            g_lft = grey_floor[ry : ry + rh, max(0, rx - pad) : max(1, rx - 4)]
            g_rgt = grey_floor[ry : ry + rh, min(fw - 1, rx + rw + 4) : min(fw, rx + rw + pad)]
            gt = float(np.mean(g_top > 0)) if g_top.size > 0 else 0.0
            gb = float(np.mean(g_bot > 0)) if g_bot.size > 0 else 0.0
            gl = float(np.mean(g_lft > 0)) if g_lft.size > 0 else 0.0
            gr = float(np.mean(g_rgt > 0)) if g_rgt.size > 0 else 0.0
            if gt >= 0.35 and (gb + gl + gr) / 3.0 >= 0.35:
                sc_slab = min(0.92, float(fill * 0.55 + ((gt + gb + gl + gr) / 4.0) * 0.45))
                cand = (rw * rh, int(rx), int(ry), int(rw), int(rh), sc_slab)
                if best_rect is None or cand[0] > best_rect[0]:
                    best_rect = cand
        if best_rect is not None:
            _, rx, ry, rw, rh, sc_slab = best_rect
            return self._update_kinematic_anchor(rx, ry, rw, rh, sc_slab, fw, fh)

        # ── Estágio 3 (RASTREAMENTO CINEMÁTICO INTELIGENTE): Previsão por Estação + Deslocamento ──
        if allow_kinematic and self._circuit_active and self._kinematic_table_xy is not None:
            if (time.time() - self._kinematic_updated_ts) <= 8.0:
                # Confirma ao menos que o piso cinza do Altar ainda está presente na tela
                grey_center = cv2.inRange(
                    hsv[int(fh * 0.15) : int(fh * 0.82), int(fw * 0.20) : int(fw * 0.78)],
                    np.array([92, 6, 120], dtype=np.uint8),
                    np.array([138, 52, 226], dtype=np.uint8),
                )
                if grey_center.size > 0 and float(np.mean(grey_center > 0)) >= 0.12:
                    krx, kry = self._kinematic_table_xy
                    t_rx = max(2, min(fw - 30, int(round(krx))))
                    t_ry = max(2, min(fh - 30, int(round(kry))))
                    t_rw = min(fw - t_rx - 2, int(round(150 * s)))
                    t_rh = min(fh - t_ry - 2, int(round(92 * s)))
                    return (t_rx, t_ry, t_rw, t_rh, 0.68)

        return None

    def detect_altar_table(
        self, frame: np.ndarray, allow_kinematic: bool = True
    ) -> Optional[Tuple[int, int, float]]:
        """
        Localiza a MESA BRANCA central do Altar (retornando o canto superior esquerdo rx, ry
        da própria Mesa Branca e o score de confiança).
        """
        rect_hit = self.detect_altar_table_rect(frame, allow_kinematic=allow_kinematic)
        if rect_hit is not None:
            rx, ry, _, _, score = rect_hit
            return (rx, ry, score)
        return None

    # Offsets nominais do centro da bacia de cada tocha em relação ao canto superior esquerdo (rx, ry)
    # da Mesa Branca, medidos na escala isométrica uniforme s = fh / 552.0:
    TORCH_BOWL_OFFSETS_S: Dict[str, Tuple[float, float]] = {
        "SW": (-69.0, 192.0),
        "NW": (-60.0, -98.0),
        "NE": (233.0, -95.0),
        "SE": (255.0, 188.0),
    }

    def _inspect_single_torch_bowl(
        self,
        hsv: np.ndarray,
        rx: int,
        ry: int,
        s: float,
        tk_key: str,
    ) -> Tuple[Optional[bool], float, float, float]:
        """
        Inspeciona com precisão cirúrgica o interior da bacia quadrada branca da tocha 'tk_key'
        ('SW', 'NW', 'NE', 'SE') usando escala s = fh / 552.0:
        - Centraliza automaticamente na mediana de (chama | lenha) dentro da bacia branca (r_search = 22*s),
          NUNCA invadindo o anel dourado das colunas gigantes NW/NE!
        - Retorna (is_lit_or_none, flame_ratio, wood_ratio, white_bowl_ratio).
        """
        fh, fw = hsv.shape[:2]
        ox_s, oy_s = self.TORCH_BOWL_OFFSETS_S[tk_key]
        nx = int(round(rx + ox_s * s))
        ny = int(round(ry + oy_s * s))

        r_search = max(10, int(round(22 * s)))
        if nx - r_search < 10 or nx + r_search > fw - 10 or ny - r_search < 18 or ny + r_search > fh - 24:
            return (None, 0.0, 0.0, 0.0)

        # Máscaras estritas dentro da região da bacia:
        # 1. Chama viva amarela/laranja brilhante (H=14..32, S=105..255, V=220..255)
        flame_mask = cv2.inRange(
            hsv, np.array([14, 105, 220], dtype=np.uint8), np.array([32, 255, 255], dtype=np.uint8)
        )
        # 2. Lenha marrom-avermelhada escura de tocha apagada (H=3..13, S=130..245, V=110..225)
        wood_mask = cv2.inRange(
            hsv, np.array([3, 130, 110], dtype=np.uint8), np.array([13, 245, 225], dtype=np.uint8)
        )
        # 3. Bacia quadrada branca da tocha ao redor (H=75..138, S=0..50, V=195..255)
        white_bowl_mask = cv2.inRange(
            hsv, np.array([75, 0, 195], dtype=np.uint8), np.array([138, 50, 255], dtype=np.uint8)
        )

        sy1, sy2 = max(0, ny - r_search), min(fh, ny + r_search)
        sx1, sx2 = max(0, nx - r_search), min(fw, nx + r_search)
        fw_sub = cv2.bitwise_or(flame_mask[sy1:sy2, sx1:sx2], wood_mask[sy1:sy2, sx1:sx2])
        ys_fw, xs_fw = np.where(fw_sub > 0)
        if len(xs_fw) >= 8:
            cx = int(sx1 + np.median(xs_fw))
            cy = int(sy1 + np.median(ys_fw))
        else:
            cx, cy = nx, ny

        r_in = max(8, int(round(16 * s)))
        r_bw = max(11, int(round(22 * s)))
        iy1, iy2 = max(0, cy - r_in), min(fh, cy + r_in)
        ix1, ix2 = max(0, cx - r_in), min(fw, cx + r_in)
        by1, by2 = max(0, cy - r_bw), min(fh, cy + r_bw)
        bx1, bx2 = max(0, cx - r_bw), min(fw, cx + r_bw)

        if (iy2 - iy1) < 6 or (ix2 - ix1) < 6:
            return (None, 0.0, 0.0, 0.0)

        fr = float(np.mean(flame_mask[iy1:iy2, ix1:ix2] > 0))
        wr = float(np.mean(wood_mask[iy1:iy2, ix1:ix2] > 0))
        whr = float(np.mean(white_bowl_mask[by1:by2, bx1:bx2] > 0))

        # Filtro anti-moedas de ouro voando / textos / estrelas (que possuem contorno preto/escuro V < 85):
        # Uma tocha real (chama ou lenha dentro da bacia branca) NUNCA tem pixels pretos/escuros (V < 85)!
        dark_outline_ratio = float(np.mean(hsv[iy1:iy2, ix1:ix2, 2] < 85))
        if dark_outline_ratio >= 0.05:
            return (None, fr, wr, whr)

        # Se a bacia branca da tocha não está visível ou não está sobre o piso cinza do Altar (ex: fora do Altar)
        r_fl = max(16, int(round(32 * s)))
        fl_sub = hsv[max(0, cy - r_fl) : min(fh, cy + r_fl), max(0, cx - r_fl) : min(fw, cx + r_fl)]
        fl_grey = float(
            np.mean(
                cv2.inRange(fl_sub, np.array([92, 6, 130], dtype=np.uint8), np.array([138, 48, 222], dtype=np.uint8)) > 0
            )
        ) if fl_sub.size > 0 else 0.0
        if whr < 0.12 or fl_grey < 0.18:
            return (None, fr, wr, whr)

        # Tocha ACESA: bola de fogo amarela/laranja preenche o centro da bacia (>= 20%) e domina sobre a lenha (> 2.5x)
        if fr >= 0.20 and fr > wr * 2.5:
            return (True, fr, wr, whr)

        # Tocha APAGADA: lenha marrom-avermelhada visível com pouca/nenhuma chama amarela
        if wr >= 0.04 and fr < 0.14:
            return (False, fr, wr, whr)

        if fr < 0.08:
            return (False, fr, wr, whr)

        return (None, fr, wr, whr)

    def detect_torch_states(
        self,
        frame: np.ndarray,
        table_rect: Optional[Tuple[int, int, int, int, float]] = None,
    ) -> Dict[str, Optional[bool]]:
        """
        Verifica com dupla checagem redundante quais das 4 tochas ('SW', 'NW', 'NE', 'SE') estão ACESAS:
        - Usa offsets isométricos precisos (`s = fh / 552.0`) centrados na bacia branca de cada tocha
          (sem jamais tocar nos anéis dourados das colunas superiores NW/NE!).
        - Exige 2 confirmações visuais fortes consecutivas (`_torch_lit_streak >= 2`) para gravar na memória
          uma tocha vista à distância, evitando qualquer falso-positivo em tochas apagadas!
        - Mantém fixa na memória toda tocha em que o bot já interagiu (`_interacted_torches`) ou confirmou acesa.
        """
        states: Dict[str, Optional[bool]] = {
            k: (True if self.lit_torches_memory.get(k) else None)
            for k in ("SW", "NW", "NE", "SE")
        }
        if frame is None or frame.size == 0:
            return states

        if table_rect is None:
            table_rect = self.detect_altar_table_rect(frame)
        if table_rect is None:
            return states

        fh, fw = frame.shape[:2]
        s = fh / 552.0
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        rx, ry = table_rect[0], table_rect[1]

        for tk_key in ("SW", "NW", "NE", "SE"):
            # Se o personagem já acendeu/confirmou esta tocha de perto neste ciclo, permanece True até o Rebirth!
            if tk_key in self._interacted_torches and self.lit_torches_memory.get(tk_key):
                states[tk_key] = True
                continue

            vis_state, fr, wr, _ = self._inspect_single_torch_bowl(hsv, rx, ry, s, tk_key)

            if vis_state is True:
                self._torch_lit_streak[tk_key] = self._torch_lit_streak.get(tk_key, 0) + 1
                # Para gravar na memória à distância, exige 2 leituras consecutivas ou chama muito intensa (fr >= 0.28)
                if self._torch_lit_streak[tk_key] >= 2 or (fr >= 0.28 and fr > wr * 3.5):
                    self.lit_torches_memory[tk_key] = True
                states[tk_key] = True
            elif vis_state is False:
                # Se ainda não interagiu de perto com a tocha e vê claramente a lenha apagada sem chama,
                # corrige eventual falso-positivo anterior!
                self._torch_lit_streak[tk_key] = 0
                if tk_key not in self._interacted_torches and wr >= 0.06 and fr < 0.09:
                    self.lit_torches_memory[tk_key] = False
                states[tk_key] = True if self.lit_torches_memory.get(tk_key) else False
            else:
                states[tk_key] = True if self.lit_torches_memory.get(tk_key) else None

        return states

    def is_near_lit_torch(
        self, frame: np.ndarray, torch_key: str, strict_visual: bool = True
    ) -> bool:
        """
        Verificação redundante ao chegar presencialmente na tocha 'torch_key' ('SW', 'NW', 'NE', 'SE'):
        1. Se o prompt '[E] Light' estiver visível na tela, a tocha está 100% APAGADA -> limpa qualquer
           memória prematura e retorna False imediatamente!
        2. Caso contrário, inspeciona a bacia da tocha de perto (e a memória confirmada) para atestar se está acesa.
        """
        if frame is None or frame.size == 0:
            return bool(self.lit_torches_memory.get(torch_key))

        # Redundância 1: Se '[E] Light' está visível na tela agora, a tocha ao lado do personagem está APAGADA!
        p_type, _, _ = self.detect_prompt_type(frame)
        if p_type == "LIGHT":
            self.unmark_torch_lit(torch_key)
            return False

        trect = self.detect_altar_table_rect(frame)
        if trect is not None:
            fh, _ = frame.shape[:2]
            s = fh / 552.0
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            vis_state, fr, wr, _ = self._inspect_single_torch_bowl(hsv, trect[0], trect[1], s, torch_key)
            if vis_state is True:
                self.mark_torch_lit(torch_key, interacted=True)
                return True
            if vis_state is False and strict_visual and torch_key not in self._interacted_torches:
                # Vendo de perto a lenha apagada sem chama -> garante que não pule a tocha!
                self.unmark_torch_lit(torch_key)
                return False

        return bool(self.lit_torches_memory.get(torch_key))

    @staticmethod
    def detect_pets_button_top(frame: np.ndarray) -> Tuple[int, int]:
        """
        Localiza visualmente a borda superior do botão verde 'Pets' na parte inferior da tela.
        """
        h, w = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        ry1, ry2 = int(h * 0.84), int(h * 0.99)
        rx1, rx2 = int(w * 0.35), int(w * 0.65)
        sub = hsv[ry1:ry2, rx1:rx2]
        if sub.size > 0:
            mask = cv2.inRange(sub, np.array([68, 95, 185]), np.array([85, 215, 255]))
            cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if cnts:
                c = max(cnts, key=cv2.contourArea)
                if cv2.contourArea(c) > 150:
                    bx, by, bw, _ = cv2.boundingRect(c)
                    return (rx1 + bx + int(bw * 0.38), ry1 + by)
        return (int(w * 0.488), int(h * 0.904))

    def detect_altar_points(
        self,
        frame: np.ndarray,
        table_rect: Optional[Tuple[int, int, int, int, float]] = None,
    ) -> Optional[Dict[str, Tuple[int, int]]]:
        """
        Geometria Comprovada de 25h+ (v4.1_stable) ancorada na MESA BRANCA:
        1. Usa a escala isométrica uniforme `s = h / 552.0` e a posição `(rx, ry)` da Mesa Branca
           (visual ou cinemática) para reconstruir `cx = rx + 18*s`, `cy = ry - 8*s` e calcular
           o corredor interno exato (`0.40 * tc + 0.60 * p_corner`, e `_CLOSE` em `0.28 * tc + 0.72 * p_corner`).
        2. Para a 4ª Tocha (`SE` - Inferior Direita):
           - Quando a câmera está no norte do Altar (`tc[1] >= int(h * 0.64)`), o pilar SE fica coberto
             pelos Pets/estrelas na base da tela -> usa o meio da diagonal até o botão 'Pets' (`0.50 * tc + 0.50 * pets`).
           - Quando a câmera já desceu (`tc[1] < int(h * 0.64)`), usa a geometria rígida Mesa+Pilar SE
             (sem deixar template matching de pilar confundir círculos de Pets/Cristais!).
        3. Mesmo quando o usuário usa Calibração Personalizada (inclusive se tiver calibrado em modo
           `screen_ratio` a partir de P0), desloca os pontos de acordo com o movimento da câmera/Mesa Branca
           a cada passo (`P0 -> SW -> NW -> NE -> SE -> P0`)!
        """
        if table_rect is None:
            table_rect = self.detect_altar_table_rect(frame)
        if table_rect is None:
            return None

        rx, ry, rw, rh, _ = table_rect
        h, w = frame.shape[:2]
        s = h / 552.0

        # Coordenada de referência (cx, cy) reconstruída de forma 100% determinística a partir de (rx, ry) da Mesa Branca:
        cx = int(round(rx + 18 * s))
        cy = int(round(ry - 8 * s))

        # Quinas da Mesa Branca na visão atual da câmera:
        t_corners = {
            "NW": (int(round(cx - 18 * s)), int(round(cy + 8 * s))),
            "NE": (int(round(cx + 132 * s)), int(round(cy + 8 * s))),
            "SW": (int(round(cx - 20 * s)), int(round(cy + 100 * s))),
            "SE": (int(round(cx + 134 * s)), int(round(cy + 100 * s))),
        }

        # Posição esperada do recorte (28x28) da quina interna de cada pilar em relação à Mesa Branca:
        exp_corner_tl = {
            "NW": (int(round(cx - 72 * s)), int(round(cy - 60 * s))),
            "NE": (int(round(cx + 162 * s)), int(round(cy - 60 * s))),
            "SW": (int(round(cx - 82 * s)), int(round(cy + 157 * s))),
            "SE": (int(round(cx + 178 * s)), int(round(cy + 157 * s))),
        }

        points: Dict[str, Tuple[int, int]] = {}
        pw = max(14, int(round(28 * s)))
        ph = max(14, int(round(28 * s)))
        pad = max(20, int(round(45 * s)))

        for key in ("SW", "NW", "NE", "SE"):
            ex, ey = exp_corner_tl[key]
            px, py = ex, ey

            # Para SE, a região inferior direita tem forte poluição visual (Pets, Estrelas, Cristais),
            # então só aceita template match de pilar SE se for altíssimo (>= 0.78); nos demais usa >= 0.60 (v4.1_stable).
            tmpl_raw = self._pillar_corner_refs.get(key)
            if tmpl_raw is not None:
                tmpl = (
                    cv2.resize(tmpl_raw, (pw, ph))
                    if (tmpl_raw.shape[1], tmpl_raw.shape[0]) != (pw, ph)
                    else tmpl_raw
                )
                sx1 = max(0, ex - pad)
                sy1 = max(0, ey - pad)
                sx2 = min(w, ex + pw + pad)
                sy2 = min(h, ey + ph + pad)
                if (sx2 - sx1) >= pw and (sy2 - sy1) >= ph:
                    sub = frame[sy1:sy2, sx1:sx2]
                    res = cv2.matchTemplate(sub, tmpl, cv2.TM_CCOEFF_NORMED)
                    _, mval, _, mloc = cv2.minMaxLoc(res)
                    min_pillar_sc = 0.78 if key == "SE" else 0.60
                    if mval >= min_pillar_sc:
                        px = sx1 + mloc[0]
                        py = sy1 + mloc[1]

            tc = t_corners[key]

            # Caso especial comprovado (v4.1_stable) para a 4ª Tocha (SE - inferior direito):
            # SOMENTE quando a câmera está no norte do Altar (ex: na Tocha 3 NE, onde tc[1] >= 0.64*h),
            # o pilar SE fica coberto pelos Pets/estrelas na base da tela -> usa o meio da diagonal até o botão 'Pets'.
            # Quando a câmera já desceu (tc[1] < 0.64*h), usa sempre a geometria Mesa+Pilar SE para nunca clicar na escada!
            if key == "SE" and tc[1] >= int(h * 0.64):
                pets_x, pets_y = self.detect_pets_button_top(frame)
                click_x = int(round(0.50 * tc[0] + 0.50 * pets_x))
                click_y = int(round(0.50 * tc[1] + 0.50 * pets_y))
                close_x = int(round(0.45 * tc[0] + 0.55 * pets_x))
                close_y = int(round(0.45 * tc[1] + 0.55 * pets_y))
            else:
                if key == "NW":
                    p_corner = (px + int(round(25 * s)), py + int(round(25 * s)))
                elif key == "NE":
                    p_corner = (px + int(round(3 * s)), py + int(round(25 * s)))
                elif key == "SW":
                    p_corner = (px + int(round(25 * s)), py + int(round(3 * s)))
                else:
                    p_corner = (px + int(round(3 * s)), py + int(round(3 * s)))

                # Posiciona 60% em direção à quina do pilar e 40% da mesa (mais perto da tocha e longe da quina da mesa!)
                click_x = int(round(0.40 * tc[0] + 0.60 * p_corner[0]))
                click_y = int(round(0.40 * tc[1] + 0.60 * p_corner[1]))

                # Folga extra para NE (Tocha 3 - Superior Direita) para nunca clicar ou raspar na quina direita da mesa:
                if key == "NE":
                    click_x += int(round(8 * s))
                    click_y -= int(round(10 * s))

                # Ponto de aproximação fina (72% pilar / 28% mesa) caso o 1º clique pare a 1 passo do [E] Light:
                close_x = int(round(0.28 * tc[0] + 0.72 * p_corner[0]))
                close_y = int(round(0.28 * tc[1] + 0.72 * p_corner[1]))
                if key == "NE":
                    close_x += int(round(8 * s))
                    close_y -= int(round(6 * s))

            points[key] = (
                max(int(w * 0.12), min(int(w * 0.88), click_x)),
                max(int(h * 0.12), min(int(h * 0.89), click_y)),
            )
            points[f"{key}_CLOSE"] = (
                max(int(w * 0.12), min(int(w * 0.88), close_x)),
                max(int(h * 0.12), min(int(h * 0.89), close_y)),
            )

        # Ponto P0: Frente central da Mesa Branca (para ativar [E] Rebirth)
        points["P0"] = (
            max(int(w * 0.12), min(int(w * 0.88), int(round(cx + 46 * s)))),
            max(int(h * 0.14), min(int(h * 0.86), int(round(cy + 106 * s)))),
        )
        points["P0_CLOSE"] = (
            max(int(w * 0.12), min(int(w * 0.88), int(round(cx + 46 * s)))),
            max(int(h * 0.14), min(int(h * 0.86), int(round(cy + 94 * s)))),
        )

        # ── Sobrescrita Inteligente pela Calibração Personalizada do Usuário (🎯 Calibrar Altar) ──
        # Mesmo se o usuário calibrou em 'screen_ratio' parado em P0, converte dinamicamente pelo
        # deslocamento da Mesa Branca (rx, ry) a cada movimento para nunca clicar em ponto estático errado!
        calib = getattr(self, "custom_altar_calibration", None)
        if isinstance(calib, dict) and calib.get("enabled"):
            mode = calib.get("mode", "table_offset")
            offsets = calib.get("offsets", {})
            ratios = calib.get("ratios", {})
            # Posição de referência da Mesa Branca quando o personagem está no ponto original P0:
            p0_vec = self.STATION_CHAR_TO_TABLE_S["P0"]
            rx_at_p0 = w * 0.50 - p0_vec[0] * s
            ry_at_p0 = h * 0.485 - p0_vec[1] * s

            for k in ("SW", "NW", "NE", "SE", "P0"):
                cust_pt = None
                if mode == "table_offset" and k in offsets:
                    dx_s, dy_s = offsets[k]
                    cust_pt = (
                        max(int(w * 0.06), min(int(w * 0.94), int(round(rx + dx_s * s)))),
                        max(int(h * 0.08), min(int(h * 0.92), int(round(ry + dy_s * s)))),
                    )
                elif k in ratios:
                    rx_r, ry_r = ratios[k]
                    # Desloca o ponto calibrado em P0 pelo movimento real da Mesa Branca (rx - rx_at_p0, ry - ry_at_p0):
                    shift_x = rx - rx_at_p0
                    shift_y = ry - ry_at_p0
                    cust_pt = (
                        max(int(w * 0.06), min(int(w * 0.94), int(round(rx_r * w + shift_x)))),
                        max(int(h * 0.08), min(int(h * 0.92), int(round(ry_r * h + shift_y)))),
                    )
                if cust_pt is not None:
                    points[k] = cust_pt
                    # Na aproximação fina (_CLOSE), avança 15% na direção do ponto canônico _CLOSE da tocha
                    if f"{k}_CLOSE" in points:
                        def_close = points[f"{k}_CLOSE"]
                        points[f"{k}_CLOSE"] = (
                            int(round(0.75 * cust_pt[0] + 0.25 * def_close[0])),
                            int(round(0.75 * cust_pt[1] + 0.25 * def_close[1])),
                        )
                    else:
                        points[f"{k}_CLOSE"] = cust_pt

        return points

    def detect_visible_torches(self, frame: np.ndarray) -> List[Tuple[int, int]]:
        """
        Localiza qualquer tocha visível na tela (acesa com chama amarela/laranja ou apagada com
        lenha marrom-avermelhada dentro da bacia branca sobre o piso cinza do Altar).
        Usado como referência de recuperação quando o personagem sai do Altar e não vê a Mesa Central.
        """
        if frame is None or frame.size == 0:
            return []

        h, w = frame.shape[:2]
        s = h / 552.0
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # Lenha marrom-avermelhada (tocha apagada) + Chama amarela/laranja (tocha acesa)
        wood_mask = cv2.inRange(hsv, np.array([5, 115, 145]), np.array([17, 230, 255]))
        flame_mask = cv2.inRange(hsv, np.array([16, 130, 230]), np.array([34, 255, 255]))
        torch_core = cv2.bitwise_or(wood_mask, flame_mask)

        # Bacia branca do pilar da tocha e piso cinza do Altar ao redor
        bowl_white = cv2.inRange(hsv, np.array([85, 0, 195]), np.array([125, 55, 255]))
        floor_grey = cv2.inRange(hsv, np.array([90, 10, 135]), np.array([118, 48, 225]))

        # Mascara bordas de HUD (checklist à esquerda, barra superior, estrelas inferiores e moedas à direita)
        roi = np.zeros_like(torch_core)
        y1, y2 = int(h * 0.14), int(h * 0.72)
        x1, x2 = int(w * 0.16), int(w * 0.78)
        roi[y1:y2, x1:x2] = torch_core[y1:y2, x1:x2]

        k = max(3, int(round(9 * s)))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        closed = cv2.morphologyEx(roi, cv2.MORPH_CLOSE, kernel)
        cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        torches: List[Tuple[int, int]] = []
        min_area = max(35, int(round(75 * s * s)))
        max_area = max(600, int(round(2800 * s * s)))
        r1 = max(12, int(round(28 * s)))
        r2 = max(24, int(round(56 * s)))

        for c in cnts:
            area = cv2.contourArea(c)
            if not (min_area <= area <= max_area):
                continue
            bx, by, bw, bh = cv2.boundingRect(c)
            cx, cy = bx + bw // 2, by + bh // 2

            w_patch = bowl_white[max(0, cy - r1):min(h, cy + r1), max(0, cx - r1):min(w, cx + r1)]
            w_rat = float(np.mean(w_patch > 0)) if w_patch.size > 0 else 0.0

            f_patch = floor_grey[max(0, cy - r2):min(h, cy + r2), max(0, cx - r2):min(w, cx + r2)]
            f_rat = float(np.mean(f_patch > 0)) if f_patch.size > 0 else 0.0

            if w_rat >= 0.15 and f_rat >= 0.35:
                torches.append((cx, cy))

        return torches

    def find_altar_recovery_point(
        self, hwnd: int, frame: np.ndarray
    ) -> Optional[Tuple[int, int, int, int, str]]:
        """
        Sistema de Auto-Recuperação em 2 Estágios caso o personagem saia da área da Mesa Central:
        - Estágio 2 (Mesa visível): Usa a Mesa Central como referência para clicar em P0 (frente da mesa)
          e reiniciar o ciclo na posição inicial padrão.
        - Estágio 1 (Mesa NÃO visível): Usa qualquer Tocha visível (ou o piso/escada branca do Altar) como
          referência para se aproximar do centro do Altar até que a Mesa Central volte a aparecer na tela.
        Retorna (client_x, client_y, screen_x, screen_y, mode_str).
        """
        if frame is None or frame.size == 0:
            return None

        h, w = frame.shape[:2]
        s = h / 552.0

        def _to_screen(rx: int, ry: int, mode: str) -> Tuple[int, int, int, int, str]:
            rx = max(int(w * 0.15), min(int(w * 0.85), int(rx)))
            ry = max(int(h * 0.16), min(int(h * 0.84), int(ry)))
            try:
                cl_left, cl_top = win32gui.ClientToScreen(hwnd, (0, 0))
            except Exception:
                try:
                    rect = win32gui.GetWindowRect(hwnd)
                    cl_left, cl_top = rect[0], rect[1]
                except Exception:
                    cl_left, cl_top = 0, 0
            return (rx, ry, cl_left + rx, cl_top + ry, mode)

        # Estágio 2: Mesa Central já está visível -> volta direto para P0 (ponto inicial em frente à mesa)
        pts = self.detect_altar_points(frame)
        if pts and "P0" in pts:
            p0_x, p0_y = pts["P0"]
            return _to_screen(p0_x, p0_y, "TABLE_P0")

        # Estágio 1A: Mesa não visível -> busca qualquer Tocha visível como referência de aproximação
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        floor_grey = cv2.inRange(hsv, np.array([90, 10, 135]), np.array([118, 48, 225]))
        floor_roi = np.zeros_like(floor_grey)
        floor_roi[int(h * 0.12):int(h * 0.82), int(w * 0.22):int(w * 0.82)] = floor_grey[
            int(h * 0.12):int(h * 0.82), int(w * 0.22):int(w * 0.82)
        ]

        ys_f, xs_f = np.where(floor_roi > 0)
        has_floor = len(xs_f) > int(2200 * s * s)
        floor_cx = int(np.median(xs_f)) if has_floor else w // 2
        floor_cy = int(np.median(ys_f)) if has_floor else h // 2

        torches = self.detect_visible_torches(frame)
        if torches:
            # Média das tochas visíveis puxada ~35% na direção do centro do piso do Altar
            # (para não clicar exatamente em cima do pilar da tocha e sim no piso ao lado dela rumo à mesa)
            avg_tx = sum(t[0] for t in torches) / len(torches)
            avg_ty = sum(t[1] for t in torches) / len(torches)
            target_x = int(round(0.65 * avg_tx + 0.35 * floor_cx))
            target_y = int(round(0.65 * avg_ty + 0.35 * floor_cy))
            return _to_screen(target_x, target_y, f"TORCH_REF({len(torches)})")

        # Estágio 1B: Se o personagem desceu para o sul do Altar e apenas a escada branca do Altar aparece no topo
        white_mask = cv2.inRange(hsv, np.array([80, 0, 208]), np.array([120, 48, 255]))
        stairs_roi = np.zeros_like(white_mask)
        stairs_roi[int(h * 0.13):int(h * 0.85), int(w * 0.20):int(w * 0.78)] = white_mask[
            int(h * 0.13):int(h * 0.85), int(w * 0.20):int(w * 0.78)
        ]
        cnts_s, _ = cv2.findContours(stairs_roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in sorted(cnts_s, key=cv2.contourArea, reverse=True):
            sx, sy, sw, sh = cv2.boundingRect(c)
            if sw >= int(200 * s) and cv2.contourArea(c) >= int(3000 * s * s):
                st_cx = sx + sw // 2
                st_cy = max(int(h * 0.17), sy + sh // 2)
                return _to_screen(st_cx, st_cy, "ALTAR_STAIRS")

        # Estágio 1C: Se parte do piso cinza do Altar estiver visível
        if has_floor:
            return _to_screen(floor_cx, floor_cy, "ALTAR_FLOOR")

        return None

    def get_altar_click_coords(
        self, hwnd: int, frame: np.ndarray, target_key: str
    ) -> Optional[Tuple[int, int, int, int]]:
        """
        Retorna (client_x, client_y, screen_x, screen_y) para o ponto 'target_key'
        ('SW', 'NW', 'NE', 'SE', 'SW_CLOSE', 'NW_CLOSE', 'NE_CLOSE', 'SE_CLOSE', 'P0', 'P0_CLOSE')
        calculado visualmente no frame atual (ou pela calibração personalizada do usuário).
        - Quando usa a geometria padrão comprovada (v4.1_stable de 25h+), converte para tela usando
          ClientToScreen(hwnd, (0, 0)) + (rel_x, rel_y) exatamente como foi calibrada em v4.1_stable!
        - Quando usa Calibração Personalizada (clicada diretamente na imagem do PrintWindow), usa
          frame_to_screen_and_client(hwnd, rel_x, rel_y).
        """
        calib = getattr(self, "custom_altar_calibration", None)
        use_custom = isinstance(calib, dict) and bool(calib.get("enabled"))

        pts = self.detect_altar_points(frame)
        if (not pts or target_key not in pts) and frame is not None and frame.size > 0:
            # Se a calibração personalizada estiver em modo 'screen_ratio', funciona mesmo sem detectar a mesa
            base_k = target_key.replace("_CLOSE", "")
            if use_custom and base_k in calib.get("ratios", {}):
                h, w = frame.shape[:2]
                rx_r, ry_r = calib["ratios"][base_k]
                rel_x = int(round(rx_r * w))
                rel_y = int(round(ry_r * h))
                return self.frame_to_screen_and_client(hwnd, rel_x, rel_y)
            return None

        if not pts or target_key not in pts:
            return None

        rel_x, rel_y = pts[target_key]
        if use_custom:
            return self.frame_to_screen_and_client(hwnd, rel_x, rel_y)

        try:
            cl_left, cl_top = win32gui.ClientToScreen(hwnd, (0, 0))
        except Exception:
            try:
                rect = win32gui.GetWindowRect(hwnd)
                cl_left, cl_top = rect[0], rect[1]
            except Exception:
                cl_left, cl_top = 0, 0

        return (rel_x, rel_y, cl_left + rel_x, cl_top + rel_y)

    # ── Detecção Visual de Missão Completa (Barra Superior) ───────────────────

    def detect_completed_quest(
        self, hwnd: int, frame: np.ndarray
    ) -> Optional[Tuple[int, int, int, int, float]]:
        """
        Identifica visualmente se a missão no banner ciano superior está 100% concluída:
        1. Localiza o banner ciano superior da missão em qualquer resolução (17" 4:3, 32" 16:9, etc.).
        2. Verifica se o botão de recompensa à direita está ACESO (verde menta brilhante, g_btn >= 0.40)
           em vez de apagado (azul-ciano).
        3. Confirma que o final da barra de progresso à esquerda está totalmente preenchido de verde
           (g_bar_end >= 0.70 e sem fundo branco vazio w_bar_end <= 0.10) ou com Check verde no canto.
        Retorna (client_x, client_y, screen_x, screen_y, confidence) do centro exato do botão verde
        para dar 1 clique e resgatar, ou None se a missão ainda estiver em progresso.
        """
        if frame is None or frame.size == 0:
            return None

        h, w = frame.shape[:2]
        is_crop = h < 220

        if is_crop:
            ry0, rx0 = 0, 0
            roi = frame
        else:
            ry0, ry1 = int(h * 0.03), int(h * 0.20)
            rx0, rx1 = int(w * 0.24), int(w * 0.72)
            roi = frame[ry0:ry1, rx0:rx1]

        if roi.size == 0:
            return None

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)

        # Fundo azul-ciano do banner da missão (Hue 90..102, Sat 75..145, Val 200..255)
        c_mask = cv2.inRange(hsv, np.array([90, 75, 200]), np.array([102, 145, 255]))
        k_w = max(7, int(round(roi.shape[1] * 0.03)))
        k_h = max(5, int(round(roi.shape[0] * 0.10)))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k_w, k_h))
        closed = cv2.morphologyEx(c_mask, cv2.MORPH_CLOSE, kernel)
        cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return None

        c = max(cnts, key=cv2.contourArea)
        bx, by, bw, bh = cv2.boundingRect(c)
        if bw < int(roi.shape[1] * 0.35) or bh < 24:
            return None

        # Recorta na borda escura vertical direita do banner para nunca invadir a engrenagem ⚙️ ou Collector Score
        sub_c = c_mask[by : by + bh, bx : bx + bw]
        col_sums = np.sum(sub_c > 0, axis=0)
        for rel_col in range(int(bh * 5.2), bw):
            if col_sums[rel_col] <= max(2, int(bh * 0.08)):
                bw = rel_col
                break

        banner_hsv = hsv[by : by + bh, bx : bx + bw]

        # 1. Botão de recompensa no lado direito do banner (68% a 98% da largura, 12% a 88% da altura)
        bx1, bx2 = int(bw * 0.68), int(bw * 0.98)
        by1, by2 = int(bh * 0.12), int(bh * 0.88)
        btn_hsv = banner_hsv[by1:by2, bx1:bx2]
        if btn_hsv.size == 0:
            return None

        # Verde menta do botão ativo/completo (Hue 68..84, Sat 105..215, Val 200..255)
        g_btn_mask = cv2.inRange(btn_hsv, np.array([68, 105, 200]), np.array([84, 215, 255]))
        g_btn_ratio = float(np.mean(g_btn_mask > 0))
        if g_btn_ratio < 0.40:
            return None

        # 2. Final da barra de progresso (50% a 65% da largura, 55% a 85% da altura)
        bar_end = banner_hsv[int(bh * 0.55) : int(bh * 0.85), int(bw * 0.50) : int(bw * 0.65)]
        if bar_end.size == 0:
            return None

        g_bar_end = float(
            np.mean(cv2.inRange(bar_end, np.array([68, 105, 200]), np.array([84, 215, 255])) > 0)
        )
        w_bar_end = float(
            np.mean(cv2.inRange(bar_end, np.array([0, 0, 235]), np.array([180, 30, 255])) > 0)
        )

        # 3. Check verde no canto superior esquerdo do banner (2% a 14% da largura, 10% a 55% da altura)
        chk_hsv = banner_hsv[int(bh * 0.10) : int(bh * 0.55), int(bw * 0.02) : int(bw * 0.14)]
        chk_rat = (
            float(np.mean(cv2.inRange(chk_hsv, np.array([45, 95, 130]), np.array([68, 255, 255])) > 0))
            if chk_hsv.size > 0
            else 0.0
        )

        # Exige botão direito verde E barra cheia sem branco no final (ou Check verde marcado)
        if not ((g_bar_end >= 0.70 and w_bar_end <= 0.10) or chk_rat >= 0.025):
            return None

        # Calcula o centro exato do botão verde para o clique
        btn_cnts, _ = cv2.findContours(g_btn_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if btn_cnts:
            bc = max(btn_cnts, key=cv2.contourArea)
            gx, gy, gw, gh = cv2.boundingRect(bc)
            rel_x = rx0 + bx + bx1 + gx + gw // 2
            rel_y = ry0 + by + by1 + gy + gh // 2
        else:
            rel_x = rx0 + bx + int(bw * 0.83)
            rel_y = ry0 + by + int(bh * 0.50)

        try:
            win_left, win_top, _, _ = win32gui.GetWindowRect(hwnd) if hwnd else (0, 0, 0, 0)
        except Exception:
            win_left, win_top = 0, 0

        return (rel_x, rel_y, win_left + rel_x, win_top + rel_y, g_btn_ratio)

    # ── Detecção Visual da Tela de Pet Descoberto / 'Continue' (Pós-Missão de Ovo) ──

    def detect_pet_continue_screen(
        self, hwnd: int, frame: np.ndarray
    ) -> Optional[Tuple[int, int, int, int, float]]:
        """
        Detecta a tela 'New Pet Discovered! / Continue' (que abre ao resgatar uma missão de chocar ovo [C]):
        1. Localiza o grande botão verde-menta 'Continue' na parte inferior central da tela:
           enquanto o botão normal 'Pets' tem largura ~0.18*h (proporção L/A ~2.3), o botão 'Continue'
           tem largura >= 0.30*h (proporção L/A ~3.0..4.5).
        2. Confirma que o cenário verde-neon hexagonal da tela de Pet está visível (ou que a barra de Ovos /
           banner superior de missão desapareceram).
        Retorna (client_x, client_y, screen_x, screen_y, confidence) para confirmar com [E] ou clicar em 'Continue'!
        """
        if frame is None or frame.size == 0:
            return None

        h, w = frame.shape[:2]
        if h < 200 or w < 250:
            return None

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        ry1, ry2 = int(h * 0.76), int(h * 0.99)
        rx1, rx2 = int(w * 0.22), int(w * 0.78)
        sub_btn = hsv[ry1:ry2, rx1:rx2]
        if sub_btn.size == 0:
            return None

        mask_btn = cv2.inRange(sub_btn, np.array([68, 95, 185]), np.array([85, 215, 255]))
        cnts, _ = cv2.findContours(mask_btn, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return None

        c = max(cnts, key=cv2.contourArea)
        bx, by, bw, bh = cv2.boundingRect(c)
        # O botão 'Pets' tem bw ~ 0.178*h (110px em h=617); o botão 'Continue' tem bw ~ 0.39*h (241px em h=617)!
        if bw < int(h * 0.28) or bh < int(h * 0.065):
            return None

        ar = bw / float(max(1, bh))
        if not (2.65 <= ar <= 4.80):
            return None

        fill_rat = cv2.contourArea(c) / float(max(1, bw * bh))
        if fill_rat < 0.65:
            return None

        # Verifica o piso verde-neon hexagonal da tela de Pet (H=50..72, S=75..215, V=190..255)
        fl_sub = hsv[int(h * 0.42) : int(h * 0.76), int(w * 0.18) : int(w * 0.82)]
        neon_grass_rat = (
            float(
                np.mean(
                    cv2.inRange(fl_sub, np.array([50, 75, 190]), np.array([72, 215, 255])) > 0
                )
            )
            if fl_sub.size > 0
            else 0.0
        )
        # Confirma que a arena verde-neon está presente (ou que o banner de missão sumiu)
        if neon_grass_rat < 0.25 and self.locate_quest_banner_details(frame) is not None:
            return None

        rel_x = rx1 + bx + bw // 2
        rel_y = ry1 + by + bh // 2
        cl_x, cl_y, scr_x, scr_y = self.frame_to_screen_and_client(hwnd, rel_x, rel_y)
        conf = min(0.99, float(0.65 + 0.35 * neon_grass_rat))
        return (cl_x, cl_y, scr_x, scr_y, conf)

    # ── Detecção Visual de Tela de Desconexão (PT-BR 'Reconectar' / EN 'Reconnect') ──

    def detect_disconnect_dialog(
        self, hwnd: int, frame: np.ndarray
    ) -> Optional[Tuple[int, int, int, int, float]]:
        """
        Identifica visualmente qualquer modal de desconexão ou falha de conexão do Roblox em qualquer idioma:
        - 'Reconectar' / 'Reconnect' (Erro 277, 278, 288, 267, etc.)
        - 'Retry' / 'Try Again' / 'Tentar novamente' (Falha de conexão, teleporte ou entrada no servidor)
        Suporta tanto o modal clássico CoreGui (#393B3D, V~60) quanto o modal escuro UIBlox/AppShell
        (#232527 / #191B1D, V~25..50), e layouts com 2 botões (Sair/Cancelar + Reconectar/Retry/Try Again)
        ou 1 botão único centralizado/largo (Retry / Try Again).
        Retorna (client_x, client_y, screen_x, screen_y, confidence) do centro exato do botão de ação,
        ou None se a janela estiver conectada normalmente.
        """
        if frame is None or frame.size == 0:
            return None

        h, w = frame.shape[:2]
        is_crop = (w < 500 or h < 360)

        if is_crop:
            ry0, rx0 = 0, 0
            roi = frame
        else:
            ry0, ry1 = int(h * 0.12), int(h * 0.86)
            rx0, rx1 = int(w * 0.12), int(w * 0.88)
            roi = frame[ry0:ry1, rx0:rx1]

        if roi.size == 0:
            return None

        rh, rw = roi.shape[:2]
        hsv = cv2.cvtColor(roi, cv2.cvtColor if False else cv2.COLOR_BGR2HSV)

        # 1. Máscara do botão claro sólido de ação ('Reconectar' / 'Reconnect' / 'Retry' / 'Try Again')
        white_mask = cv2.inRange(hsv, np.array([0, 0, 195]), np.array([180, 38, 255]))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (21, 7))
        closed = cv2.morphologyEx(white_mask, cv2.MORPH_CLOSE, kernel)

        # Usa RETR_LIST para encontrar o botão branco mesmo quando há janela branca aberta atrás do modal
        cnts, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return None

        best_hit = None
        best_conf = 0.0

        for c in cnts:
            bx, by, bw, bh = cv2.boundingRect(c)
            if bw < 55 or bh < 15 or bw > int(rw * 0.88) or bh > int(rh * 0.28):
                continue

            ar = bw / float(max(1, bh))
            if not (2.0 <= ar <= 13.5):
                continue

            if not is_crop:
                abs_cx = rx0 + bx + bw // 2
                abs_cy = ry0 + by + bh // 2
                if not (int(h * 0.30) <= abs_cy <= int(h * 0.82)):
                    continue
                if not (int(w * 0.22) <= abs_cx <= int(w * 0.78)):
                    continue

            btn_raw_white = float(np.mean(white_mask[by : by + bh, bx : bx + bw] > 0))
            if btn_raw_white < 0.60:
                continue

            # Confirma texto escuro ('Reconectar', 'Reconnect', 'Retry', 'Try Again') dentro do botão
            inner_v = hsv[
                by + int(bh * 0.18) : by + int(bh * 0.82),
                bx + int(bw * 0.12) : bx + int(bw * 0.88),
                2,
            ]
            if inner_v.size == 0:
                continue
            dark_text_ratio = float(np.mean(inner_v < 155))
            if not (0.005 <= dark_text_ratio <= 0.48):
                continue

            # 2. Valida o corpo cinza-escuro/grafite do modal acima dos botões (#393B3D, #232527, #191B1D)
            ya1 = max(0, by - int(bh * 4.2))
            ya2 = max(0, by - int(bh * 0.35))
            if (ya2 - ya1) < int(bh * 1.4):
                continue

            spans = [
                ("two_btn_right", max(0, bx - int(bw * 1.15)), min(rw, bx + bw)),
                ("single_btn",    max(0, bx - int(bw * 0.15)), min(rw, bx + int(bw * 1.15))),
                ("two_btn_left",  bx, min(rw, bx + int(bw * 2.15))),
            ]

            layout_score = 0.0
            for mode, xa1, xa2 in spans:
                if (xa2 - xa1) < int(bw * 0.85):
                    continue
                body_hsv = hsv[ya1:ya2, xa1:xa2]
                if body_hsv.size == 0:
                    continue

                dark_body_mask = (
                    (body_hsv[:, :, 1] <= 48)
                    & (body_hsv[:, :, 2] >= 18)
                    & (body_hsv[:, :, 2] <= 108)
                )
                dark_body_ratio = float(np.mean(dark_body_mask))
                if dark_body_ratio < 0.52:
                    continue

                mean_body_sat = float(np.mean(body_hsv[:, :, 1]))
                if mean_body_sat > 32.0:
                    continue

                if mode == "two_btn_right":
                    xl1 = max(0, bx - int(bw * 1.12))
                    xl2 = max(0, bx - int(bw * 0.08))
                    if (xl2 - xl1) < int(bw * 0.45):
                        continue
                    left_hsv = hsv[by : by + bh, xl1:xl2]
                    dark_left_mask = (
                        (left_hsv[:, :, 1] <= 48)
                        & (left_hsv[:, :, 2] >= 18)
                        & (left_hsv[:, :, 2] <= 125)
                    )
                    dark_left_ratio = float(np.mean(dark_left_mask))
                    white_left_ratio = float(np.mean(left_hsv[:, :, 2] > 210))
                    if dark_left_ratio >= 0.48 and white_left_ratio <= 0.30:
                        sc = (btn_raw_white * 0.35) + (dark_body_ratio * 0.45) + (dark_left_ratio * 0.20)
                        layout_score = max(layout_score, sc)

                elif mode == "two_btn_left":
                    xr1 = min(rw, bx + int(bw * 1.08))
                    xr2 = min(rw, bx + int(bw * 2.12))
                    if (xr2 - xr1) < int(bw * 0.45):
                        continue
                    right_hsv = hsv[by : by + bh, xr1:xr2]
                    dark_right_mask = (
                        (right_hsv[:, :, 1] <= 48)
                        & (right_hsv[:, :, 2] >= 18)
                        & (right_hsv[:, :, 2] <= 125)
                    )
                    dark_right_ratio = float(np.mean(dark_right_mask))
                    white_right_ratio = float(np.mean(right_hsv[:, :, 2] > 210))
                    if dark_right_ratio >= 0.48 and white_right_ratio <= 0.30:
                        sc = (btn_raw_white * 0.35) + (dark_body_ratio * 0.45) + (dark_right_ratio * 0.20)
                        layout_score = max(layout_score, sc)

                elif mode == "single_btn":
                    yb1 = min(rh, by + bh + 2)
                    yb2 = min(rh, by + bh + max(6, int(bh * 0.45)))
                    if yb2 > yb1 and dark_body_ratio >= 0.62 and mean_body_sat <= 24.0:
                        bot_pad = hsv[yb1:yb2, bx : bx + bw]
                        dark_pad_ratio = float(
                            np.mean(
                                (bot_pad[:, :, 1] <= 48)
                                & (bot_pad[:, :, 2] >= 18)
                                & (bot_pad[:, :, 2] <= 108)
                            )
                        )
                        if dark_pad_ratio >= 0.60:
                            sc = (btn_raw_white * 0.35) + (dark_body_ratio * 0.45) + (dark_pad_ratio * 0.20)
                            layout_score = max(layout_score, sc)

            if layout_score > best_conf:
                best_conf = layout_score
                rel_x = rx0 + bx + bw // 2
                rel_y = ry0 + by + bh // 2
                best_hit = (rel_x, rel_y, best_conf)

        if not best_hit:
            return None

        rel_x, rel_y, conf = best_hit
        # IMPORTANTE: PrintWindow(hwnd, dc, 2) captura a janela a partir de (win_left, win_top) de GetWindowRect!
        try:
            win_left, win_top, _, _ = win32gui.GetWindowRect(hwnd)
        except Exception:
            win_left, win_top = 0, 0

        return (rel_x, rel_y, win_left + rel_x, win_top + rel_y, conf)

    # ── Métodos Auxiliares para Indicadores Visuais Precisos (Overlay HUD & Mini-Monitor) ──

    def locate_quest_banner_details(
        self, frame: np.ndarray
    ) -> Optional[Tuple[Tuple[int, int, int, int], Tuple[int, int, int, int], bool, float]]:
        """
        Localiza o retângulo exato (x, y, w, h) do banner ciano superior de missão
        e o retângulo exato (bx, by, bw, bh) do botão de recompensa à direita,
        parando exatamente na borda direita do banner (sem invadir a engrenagem ⚙️ ou Collector Score).
        Retorna (banner_rect, btn_rect, is_ready, green_ratio) ou None se o banner não estiver visível.
        """
        if frame is None or frame.size == 0:
            return None

        h, w = frame.shape[:2]
        ry0, ry1 = int(h * 0.03), int(h * 0.20)
        rx0, rx1 = int(w * 0.24), int(w * 0.72)
        roi = frame[ry0:ry1, rx0:rx1]
        if roi.size == 0:
            return None

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        c_mask = cv2.inRange(hsv, np.array([90, 75, 200]), np.array([102, 145, 255]))
        k_w = max(7, int(round(roi.shape[1] * 0.03)))
        k_h = max(5, int(round(roi.shape[0] * 0.10)))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k_w, k_h))
        closed = cv2.morphologyEx(c_mask, cv2.MORPH_CLOSE, kernel)
        cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return None

        c = max(cnts, key=cv2.contourArea)
        bx, by, bw, bh = cv2.boundingRect(c)
        if bw < int(roi.shape[1] * 0.35) or bh < 24:
            return None

        # Recorta na borda escura vertical direita do banner para nunca alcançar a engrenagem de opções ⚙️
        sub_c = c_mask[by : by + bh, bx : bx + bw]
        col_sums = np.sum(sub_c > 0, axis=0)
        for rel_col in range(int(bh * 5.2), bw):
            if col_sums[rel_col] <= max(2, int(bh * 0.08)):
                bw = rel_col
                break

        banner_rect = (rx0 + bx, ry0 + by, bw, bh)
        btn_x1 = rx0 + bx + int(bw * 0.68)
        btn_y1 = ry0 + by + int(bh * 0.12)
        btn_w = int(bw * 0.30)
        btn_h = int(bh * 0.76)
        btn_rect = (btn_x1, btn_y1, btn_w, btn_h)

        banner_hsv = hsv[by : by + bh, bx : bx + bw]
        btn_hsv = banner_hsv[int(bh * 0.12) : int(bh * 0.88), int(bw * 0.68) : int(bw * 0.98)]
        g_btn_ratio = 0.0
        is_ready = False
        if btn_hsv.size > 0:
            g_btn_mask = cv2.inRange(btn_hsv, np.array([68, 105, 200]), np.array([84, 215, 255]))
            g_btn_ratio = float(np.mean(g_btn_mask > 0))
            if g_btn_ratio >= 0.40:
                bar_end = banner_hsv[int(bh * 0.55) : int(bh * 0.85), int(bw * 0.50) : int(bw * 0.65)]
                if bar_end.size > 0:
                    g_bar_end = float(
                        np.mean(cv2.inRange(bar_end, np.array([68, 105, 200]), np.array([84, 215, 255])) > 0)
                    )
                    w_bar_end = float(
                        np.mean(cv2.inRange(bar_end, np.array([0, 0, 235]), np.array([180, 30, 255])) > 0)
                    )
                    if g_bar_end >= 0.70 and w_bar_end <= 0.10:
                        is_ready = True

        return (banner_rect, btn_rect, is_ready, g_btn_ratio)

    def locate_checklist_details(
        self, frame: np.ndarray
    ) -> Tuple[Tuple[int, int, int, int], List[int], List[bool]]:
        """
        Retorna (checklist_rect (x, y, w, h), row_ys [8], tier_checks [8]) com posicionamento
        100% alinhado aos 8 tiers reais (Common até Mythical) na lateral esquerda da tela.
        """
        if frame is None or frame.size == 0:
            return ((0, 0, 0, 0), [], [False] * 8)

        # Se a tela 'New Pet Discovered! / Continue' estiver aberta, oculta o retângulo do Checklist
        if self.detect_pet_continue_screen(0, frame) is not None:
            return ((0, 0, 0, 0), [], [False] * 8)

        h, w = frame.shape[:2]
        ui_s = h / 784.0
        tiers = self.analyze_tiers(frame)

        if self._cached_row_ys is not None and len(self._cached_row_ys) == 8 and self._cached_shape == (h, w):
            row_ys = [int(round(y)) for y in self._cached_row_ys]
        else:
            y_start = int(round(h * 0.495))
            y_end = int(round(h * 0.740))
            step = (y_end - y_start) / 7.0
            row_ys = [int(round(y_start + i * step)) for i in range(8)]

        pad_y = max(12, int(round(16 * ui_s)))
        cx1 = max(2, int(round(4 * ui_s)))
        cy1 = max(10, row_ys[0] - pad_y)
        cx2 = min(int(w * 0.25), cx1 + max(145, int(round(205 * ui_s))))
        cy2 = min(h - 10, row_ys[-1] + pad_y)
        return ((cx1, cy1, cx2 - cx1, cy2 - cy1), row_ys, tiers)

    def locate_egg_button_details(
        self, frame: np.ndarray
    ) -> Optional[Tuple[int, int, int, str]]:
        """
        Verifica se o botão de ovo da barra inferior está REALMENTE visível na tela
        (exige botão verde 'Pets' visível + template match real do ovo >= 0.35),
        escalando proporcionalmente à largura medida (bw) do botão verde 'Pets' para qualquer tamanho de janela.
        Retorna (egg_cx, egg_cy, radius, egg_name) ou None se a barra de ovos não estiver visível.
        """
        if frame is None or frame.size == 0:
            return None

        h, w = frame.shape[:2]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        ry1, ry2 = int(h * 0.82), h
        rx1, rx2 = int(w * 0.32), int(w * 0.68)
        sub_pets = hsv[ry1:ry2, rx1:rx2]
        if sub_pets.size == 0:
            return None

        mask_pets = cv2.inRange(sub_pets, np.array([68, 95, 185]), np.array([85, 215, 255]))
        cnts, _ = cv2.findContours(mask_pets, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return None

        c = max(cnts, key=cv2.contourArea)
        if cv2.contourArea(c) <= 100:
            return None

        bx, by, bw, bh = cv2.boundingRect(c)
        # Se for o botão largo 'Continue' da tela de Pet Descoberto (bw >= 0.26*h), a barra de ovos não está visível!
        if bw >= int(h * 0.26):
            return None
        pets_cx = rx1 + bx + bw // 2
        pets_cy = ry1 + by + bh // 2
        # Escala 100% proporcional à barra inferior do Roblox (em h=790 ref, bw=140 e ovo=44x44)
        s_egg = max(0.35, bw / 140.0)
        exp_x = int(round(pets_cx - bw * 2.22))
        exp_y = int(round(pets_cy - bh * 0.06))
        if exp_x < 12 or exp_y < 12 or exp_x >= w - 12 or exp_y >= h - 12:
            return None

        if not self._egg_templs or not self._egg_centroids:
            return None

        pad_x = max(18, int(round(bw * 0.28)))
        pad_y = max(16, int(round(bh * 0.62)))
        sx1, sy1 = max(0, exp_x - pad_x), max(0, exp_y - pad_y)
        sx2, sy2 = min(w, exp_x + pad_x), min(h, exp_y + pad_y)
        roi = frame[sy1:sy2, sx1:sx2]
        if roi.shape[0] < 20 or roi.shape[1] < 20:
            return None

        best_name = "Common"
        best_score = -999.0
        best_pt = (exp_x, exp_y)

        for name in EGG_BAR_CYCLE:
            t = self._egg_templs.get(name)
            c_ref = self._egg_centroids.get(name)
            if t is None or c_ref is None:
                continue
            for factor in (0.82, 0.91, 1.00, 1.09):
                tw = max(12, int(round(t.shape[1] * s_egg * factor)))
                th = max(12, int(round(t.shape[0] * s_egg * factor)))
                if tw >= roi.shape[1] or th >= roi.shape[0]:
                    continue
                st = cv2.resize(t, (tw, th))
                res = cv2.matchTemplate(roi, st, cv2.TM_CCOEFF_NORMED)
                _, mv, _, ml = cv2.minMaxLoc(res)
                ecx = sx1 + ml[0] + tw // 2
                ecy = sy1 + ml[1] + th // 2
                r_p = max(5, int(round(11 * s_egg)))
                patch = frame[
                    max(0, ecy - r_p) : min(h, ecy + r_p),
                    max(0, ecx - r_p) : min(w, ecx + r_p),
                ]
                if patch.size > 0:
                    bgr_m = patch.mean(axis=(0, 1))
                    color_dist = float(np.linalg.norm(bgr_m - c_ref[:3])) / 255.0
                    dist_pen = 0.15 * (
                        abs(ecx - exp_x) / float(max(1, pad_x))
                        + abs(ecy - exp_y) / float(max(1, pad_y))
                    )
                    score = float(mv) - 0.8 * color_dist - dist_pen
                    if score > best_score:
                        best_score = score
                        best_name = name
                        best_pt = (ecx, ecy)

        # Se o botão de ovo não estiver na tela (ex: jogador está na Selva longe da base), retorna None!
        if best_score < 0.35:
            return None

        radius = max(14, int(round(21 * s_egg)))
        return (best_pt[0], best_pt[1], radius, best_name)

