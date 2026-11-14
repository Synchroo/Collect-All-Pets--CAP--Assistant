# 🎮 CAP Assistant v4.2.5

**Assistente Visual Multi-Janelas, Multi-Idioma (12 Idiomas) e 100% Externo para Collect All Pets (Roblox)**

O **CAP Assistant** é um utilitário desktop de código aberto para Windows que auxilia jogadores de *Collect All Pets* no gerenciamento de **1 ou múltiplas janelas simultâneas** (compatível com *Roblox Account Manager*), combinando visão computacional passiva (`OpenCV`) e atalhos nativos oficiais do próprio jogo (`[E]`, `[Z]`, `[C]`, `[Q]`).

---

## 🔒 Segurança, Transparência e Privacidade (100% Externo)

O projeto foi desenhado desde a base sob princípios estritos de segurança e transparência:

1. **Zero Injeção e Zero Leitura de Memória:**
   - O CAP Assistant **não injeta** DLLs, scripts Lua ou código no processo do Roblox.
   - **Não lê, não escaneia e jamais altera** a memória RAM do jogo (diferente de *exploits* ou *cheats*).
2. **Visão Computacional Passiva ("Olho Humano"):**
   - O aplicativo apenas observa a imagem renderizada da janela usando a API padrão do Windows (`PrintWindow`), identificando visualmente quando o botão de missão fica verde, quando os *checks* de raridade completam ou onde estão a Mesa Branca e as 4 Tochas do Altar.
3. **Uso Exclusivo de Teclas Oficiais do Jogo (`SendInput`):**
   - Para interagir, o assistente envia os próprios atalhos nativos criados pelo *Collect All Pets* através da API padrão de acessibilidade do Windows (`SendInput`):
     - **`[C]`** — Coletar missão concluída (*Collect Quest & Close Window*)
     - **`[Z]`** — Alternar tipo de ovo na barra inferior (*Toggle Egg Type*)
     - **`[E]`** — Interagir com Tochas (`[E] Light`), Mesa Branca (`[E] Rebirth`) e fechar a tela de Pet Descoberto (`Continue`)
     - **`[Q]`** — Fechar janela aberta (utilizado no pulso periódico de Anti-AFK `[C]+[Q]`)
4. **100% Local e Sem Coleta de Dados:**
   - **Zero Rede / Telemetria:** O aplicativo não faz conexões com a internet, não envia imagens e nunca solicita login, senha ou cookies da sua conta.

---

## ✨ Principais Recursos (v4.2.5)

- **🪟 Suporte Multi-Janelas Independente (`1..N`):**
  - Detecta todas as janelas abertas do Roblox e permite ativar ou desativar funções individualmente por conta, além de definir qual janela é a **⭐ Principal**.
- **🔥 3 Modos de Rebirth no Painel Principal:**
  - **🚫 Desativado (Modo Leve):** Mantém apenas Coleta de Missões `[C]`, Anti-AFK `[C+Q]` e Auto-Reconectar com uso mínimo de CPU. Funciona em qualquer área do mapa (*Jungle, Grove, Grotto*).
  - **⚡ Semi-Auto (`[E]` + Ovos `[Z]`):** Automatiza a troca de ovos (de *Common* até *Ascended*) e as interações `[E]` nas tochas e na mesa, deixando a movimentação manual. **Não sofre interferência do Auto-Pause** enquanto você estiver jogando dentro do Roblox.
  - **🏛️ Full-Auto (Rebirth Completo):** Ao atingir `240/240` (*Mythical* ✔), percorre rigorosamente a sequência das **4 Tochas (`SW ➔ NW ➔ NE ➔ SE`)** ancorado na **Mesa Branca** (com memória de tochas acesas e imunidade a moedas de ouro voando), realiza o Rebirth na Mesa Branca e reinicia o ciclo em *Common*.
- **🐣 Fechamento Automático da Tela de Pet Descoberto (`Continue`):**
  - Se ao coletar uma missão de chocar ovo (`[C]`) o jogo abrir a tela *"New Pet Discovered! / Continue"*, o assistente identifica a tela (e a ausência das referências do Rebirth) e confirma automaticamente com **`[E]`** para retornar ao jogo sem travar o ciclo.
- **🎯 Calibrador Visual Interativo do Altar:**
  - Permite ajustar com 1 clique na imagem da sua tela onde o personagem deve pisar em cada uma das 4 Tochas e na Mesa Branca, adaptando-se a qualquer tamanho de *Skin*/Avatar, resolução ou ângulo de câmera.
- **👁️ Indicadores Visuais Zero-Lag:**
  - **🎯 Overlay HUD:** Desenha molduras vazadas (*Win32 SetWindowRgn*, 100% *Click-Through*) diretamente sobre a janela do jogo sem pesar no DirectX/DWM.
  - **📺 Mini-Monitor Ao Vivo:** Exibe em janela separada o mosaico em tempo real de todas as contas abertas.
- **⚙️ Menu de Configurações, Bandeja do Sistema (`System Tray`) e Alertas:**
  - Personalização dos atalhos globais de **Play/Stop** e **Pausa** (suporta **1 tecla** ou **combinações de teclas**, como `Ctrl+Shift+F8`).
  - Minimização para a **Bandeja do Windows (System Tray)** com escolha de quais notificações em balão ativar (incluindo **Alerta Anti-AFK aos 15 e 18 minutos** caso o assistente seja esquecido em Pausa antes do limite de 20 minutos do Roblox).
- **🌐 Interface Global em 12 Idiomas:**
  - Português (`PT-BR`), Inglês (`EN`), Espanhol (`ES`), Francês (`FR`), Alemão (`DE`), Italiano (`IT`), Russo (`RU`), Turco (`TR`), Árabe (`AR`), Chinês (`ZH`), Japonês (`JA`) e Coreano (`KO`).

---

## 🚀 Como Executar

### Opção A: Versão Portable (`CAP_Assistant.exe`) — Sem instalar Python
1. Baixe o arquivo **`CAP_Assistant.exe`** na aba [**Releases**](../../releases) deste repositório.
2. Execute o `CAP_Assistant.exe` (suas preferências serão salvas localmente no arquivo `bot_settings.json` na mesma pasta).

> **ℹ️ Nota sobre o Windows SmartScreen / Antivírus:**  
> Como o executável Portable é empacotado com *PyInstaller* e não possui assinatura digital comercial da Microsoft (que exige certificado pago anual), o Windows pode exibir o aviso *"O Windows protegeu o computador"*. Você pode verificar o hash **SHA-256** abaixo ou rodar diretamente pelo código-fonte em Python (Opção B).
>
> **SHA-256 (`CAP_Assistant.exe` v4.2.5):**
> ```text
> 28AFA54D91AF82F0D946684B03D434DCDDAB7C5E07E043789B294A4BCE8B700D
> ```

---

### Opção B: Rodar Diretamente pelo Código-Fonte (Python 3.11+)

1. Clone este repositório:
   ```bash
   git clone https://github.com/SEU_USUARIO/cap-assistant.git
   cd cap-assistant
   ```
2. Instale as dependências oficiais:
   ```bash
   pip install -r requirements.txt
   ```
3. Inicie o aplicativo:
   ```bash
   python bot_gui.py
   ```
   *(Ou dê dois cliques em `INICIAR_BOT.bat`).*

---

## 🛠️ Como Compilar seu Próprio Executável Portable do Zero

Para total transparência, qualquer pessoa pode gerar seu próprio `CAP_Assistant.exe` diretamente a partir do código-fonte:

```bash
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm CAP_Assistant.spec
```
O executável único será gerado em `dist/CAP_Assistant.exe`.

---

## 📂 Estrutura do Projeto

| Arquivo | Descrição |
| :--- | :--- |
| [`bot_gui.py`](bot_gui.py) | Interface gráfica principal, Calibrador do Altar, Guia Visual, Menu de Configurações e Log |
| [`bot_core.py`](bot_core.py) | Motor de automação multi-janelas, máquina de estados do Rebirth, Missões e Auto-Reconectar |
| [`bot_vision.py`](bot_vision.py) | Leitor de visão computacional (`OpenCV` + `PrintWindow`), detecção de Mesa Branca, Tochas, Ovos e Modais |
| [`bot_input.py`](bot_input.py) | Controlador de entrada via `SendInput` nativo do Windows e detecção de atividade humana |
| [`bot_overlay.py`](bot_overlay.py) | Sistema de **Overlay HUD** vazado (`SetWindowRgn`) e **Mini-Monitor** multi-janelas em tempo real |
| [`bot_tray.py`](bot_tray.py) | Integração nativa com a Bandeja do Sistema (*Windows System Tray*) e notificações em balão |
| [`bot_i18n.py`](bot_i18n.py) | Dicionário completo de tradução para 12 idiomas, *tooltips* e textos didáticos |
| [`CAP_Assistant.spec`](CAP_Assistant.spec) | Especificação de empacotamento portátil do *PyInstaller* |

---

> ⚠️ **Aviso Legal:** Este software é um projeto independente e educativo de acessibilidade/automação visual externa, não possuindo qualquer vínculo com a Roblox Corporation ou com os criadores de *Collect All Pets*. Utilize com responsabilidade.
