import tkinter as tk
from tkinter import ttk, messagebox
import threading
import serial
import serial.tools.list_ports
import vgamepad as vg
import time
import json
import os

# --- ARQUIVO DE CONFIGURAÇÃO ---
CONFIG_FILE = 'config.json'

# --- CONFIGURAÇÕES PADRÃO ---
DEFAULT_CONFIG = {
    "porta_serial": "COM3",
    "baud_rate": 9600,
    "volante": {
        "centro": 512,
        "deadzone": 25,
        "invertido": False,
        "sensibilidade": 100,  # 0-200%
        "suavizacao": 0  # 0-10 frames
    },
    "acelerador": {
        "invertido": False,
        "deadzone": 5,
        "sensibilidade": 100,
        "curva": "linear"  # linear, progressivo, agressivo
    },
    "freio": {
        "invertido": False,
        "deadzone": 5,
        "sensibilidade": 100,
        "curva": "linear"
    },
    "mapeamento_botoes": {
        "marcha_up": "XUSB_GAMEPAD_RIGHT_SHOULDER",
        "marcha_down": "XUSB_GAMEPAD_LEFT_SHOULDER",
        "freio_mao": "XUSB_GAMEPAD_A",
        "buzina": "XUSB_GAMEPAD_B",
        "luzes": "XUSB_GAMEPAD_X",
        "camera": "XUSB_GAMEPAD_Y"
    },
    "perfil_ativo": "BeamNG Default"
}

# Perfis pré-configurados
PERFIS = {
    "BeamNG Default": {
        "volante": {"deadzone": 25, "sensibilidade": 100},
        "acelerador": {"deadzone": 5, "sensibilidade": 100, "curva": "linear"},
        "freio": {"deadzone": 5, "sensibilidade": 100, "curva": "linear"}
    },
    "Rally": {
        "volante": {"deadzone": 15, "sensibilidade": 120},
        "acelerador": {"deadzone": 10, "sensibilidade": 90, "curva": "progressivo"},
        "freio": {"deadzone": 8, "sensibilidade": 110, "curva": "agressivo"}
    },
    "Drift": {
        "volante": {"deadzone": 10, "sensibilidade": 130},
        "acelerador": {"deadzone": 3, "sensibilidade": 110, "curva": "agressivo"},
        "freio": {"deadzone": 5, "sensibilidade": 120, "curva": "progressivo"}
    },
    "Precisão": {
        "volante": {"deadzone": 35, "sensibilidade": 80},
        "acelerador": {"deadzone": 15, "sensibilidade": 85, "curva": "progressivo"},
        "freio": {"deadzone": 15, "sensibilidade": 85, "curva": "progressivo"}
    }
}

# --- VARIÁVEIS GLOBAIS ---
config = {}
arduino = None
gamepad = vg.VX360Gamepad()
running = True
modo_teste = False
historico_volante = []

# --- FUNÇÕES DE CONFIGURAÇÃO ---
def carregar_configuracoes():
    global config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
            print(f"✓ Configurações carregadas de {CONFIG_FILE}")
        except:
            config = DEFAULT_CONFIG.copy()
            salvar_configuracoes()
    else:
        config = DEFAULT_CONFIG.copy()
        salvar_configuracoes()

def salvar_configuracoes():
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
        print(f"✓ Configurações salvas")
    except Exception as e:
        print(f"✗ Erro ao salvar: {e}")

# --- FUNÇÕES DE MAPEAMENTO ---
def aplicar_curva(valor, tipo_curva):
    """Aplica diferentes curvas de resposta."""
    if tipo_curva == "linear":
        return valor
    elif tipo_curva == "progressivo":
        return valor ** 1.5
    elif tipo_curva == "agressivo":
        return valor ** 0.7
    return valor

def mapear_eixo_avancado(valor, cfg, min_input=0, max_input=1023):
    """Mapeamento avançado com deadzone, inversão, sensibilidade e curva."""
    # Normalizar para 0-1
    valor_norm = (valor - min_input) / (max_input - min_input)
    
    # Aplicar inversão
    if cfg.get('invertido', False):
        valor_norm = 1.0 - valor_norm
    
    # Aplicar deadzone
    deadzone = cfg.get('deadzone', 0) / 100.0
    if valor_norm < deadzone:
        valor_norm = 0.0
    else:
        valor_norm = (valor_norm - deadzone) / (1.0 - deadzone)
    
    # Aplicar curva
    valor_norm = aplicar_curva(valor_norm, cfg.get('curva', 'linear'))
    
    # Aplicar sensibilidade
    sensibilidade = cfg.get('sensibilidade', 100) / 100.0
    valor_norm = min(1.0, valor_norm * sensibilidade)
    
    return valor_norm

def mapear_volante_avancado(valor, cfg):
    """Mapeamento específico para volante com centro."""
    centro = cfg.get('centro', 512)
    deadzone = cfg.get('deadzone', 25)
    sensibilidade = cfg.get('sensibilidade', 100) / 100.0
    invertido = cfg.get('invertido', False)
    
    # Zona morta
    if abs(valor - centro) < deadzone:
        return 0.0
    
    # Mapear para -1.0 a 1.0
    if valor >= centro + deadzone:
        eixo = (valor - centro - deadzone) / (1023 - centro - deadzone)
    else:
        eixo = (valor - centro + deadzone) / (centro - deadzone)
    
    # Aplicar sensibilidade
    eixo = max(-1.0, min(1.0, eixo * sensibilidade))
    
    # Aplicar inversão
    if invertido:
        eixo = -eixo
    
    # Suavização
    suavizacao = cfg.get('suavizacao', 0)
    if suavizacao > 0:
        historico_volante.append(eixo)
        if len(historico_volante) > suavizacao:
            historico_volante.pop(0)
        eixo = sum(historico_volante) / len(historico_volante)
    
    return eixo

# --- FUNÇÃO PRINCIPAL DE CONTROLE ---
def controle_loop():
    global arduino, running, gamepad, config, modo_teste
    
    # Mapear botões
    botoes_map = {}
    for nome, btn_str in config['mapeamento_botoes'].items():
        try:
            botoes_map[nome] = getattr(vg.XUSB_BUTTON, btn_str)
        except:
            print(f"✗ Botão inválido: {btn_str}")
    
    estado_botoes = {}
    
    while running:
        if arduino is None or not arduino.is_open:
            time.sleep(1)
            continue
        
        try:
            line = arduino.readline().decode('utf-8').strip()
            
            if line.startswith('<') and line.endswith('>'):
                data = line[1:-1].split(',')
                if len(data) >= 5:
                    valores = list(map(int, data[:5]))
                    volante_val, acelerador_val, freio_val, marcha_up, marcha_down = valores
                    
                    # Processar botões adicionais se existirem
                    botoes_extras = data[5:] if len(data) > 5 else []
                    
                    if not modo_teste:
                        # Volante
                        eixo_x = mapear_volante_avancado(volante_val, config['volante'])
                        gamepad.left_joystick_float(x_value_float=eixo_x, y_value_float=0.0)
                        
                        # Acelerador
                        acel = mapear_eixo_avancado(acelerador_val, config['acelerador'])
                        gamepad.right_trigger_float(value_float=acel)
                        
                        # Freio
                        freio = mapear_eixo_avancado(freio_val, config['freio'])
                        gamepad.left_trigger_float(value_float=freio)
                        
                        # Marchas
                        for btn_nome, estado in [('marcha_up', marcha_up), ('marcha_down', marcha_down)]:
                            if btn_nome in botoes_map:
                                if estado == 1 and estado_botoes.get(btn_nome) != 1:
                                    gamepad.press_button(button=botoes_map[btn_nome])
                                elif estado == 0 and estado_botoes.get(btn_nome) == 1:
                                    gamepad.release_button(button=botoes_map[btn_nome])
                                estado_botoes[btn_nome] = estado
                        
                        gamepad.update()
                    
                    # Atualizar GUI
                    root.after(0, update_gui, volante_val, acelerador_val, freio_val, 
                              marcha_up, marcha_down, eixo_x if not modo_teste else 0)
        
        except:
            time.sleep(0.1)

def update_gui(volante, acel, freio, up, down, eixo_processado):
    """Atualiza interface com valores em tempo real."""
    lbl_volante_val.config(text=f"{volante} → {eixo_processado:.2f}")
    lbl_acel_val.config(text=f"{acel}")
    lbl_freio_val.config(text=f"{freio}")
    
    # Barras visuais
    barra_volante.set((eixo_processado + 1.0) * 50)
    barra_acel.set((acel / 1023.0) * 100)
    barra_freio.set((freio / 1023.0) * 100)
    
    # Indicadores de marcha
    lbl_marcha_up.config(bg="green" if up else "gray80")
    lbl_marcha_down.config(bg="red" if down else "gray80")

# --- INTERFACE GRÁFICA ---
def criar_interface():
    global root, lbl_volante_val, lbl_acel_val, lbl_freio_val
    global lbl_marcha_up, lbl_marcha_down
    global barra_volante, barra_acel, barra_freio
    
    root = tk.Tk()
    root.title("🎮 Volante Pro - Configurador BeamNG.drive")
    root.geometry("900x700")
    root.resizable(True, True)
    
    # Estilo
    style = ttk.Style()
    style.theme_use('clam')
    
    # Notebook (Abas)
    notebook = ttk.Notebook(root)
    notebook.pack(fill='both', expand=True, padx=10, pady=10)
    
    # === ABA 1: MONITOR ===
    aba_monitor = ttk.Frame(notebook)
    notebook.add(aba_monitor, text="📊 Monitor")
    
    # Status
    frame_status = ttk.LabelFrame(aba_monitor, text="Status da Conexão", padding=10)
    frame_status.pack(fill='x', padx=10, pady=5)
    
    lbl_status = ttk.Label(frame_status, text="🔌 Verificando conexão...", font=("Arial", 11))
    lbl_status.pack()
    
    btn_frame = ttk.Frame(frame_status)
    btn_frame.pack(pady=5)
    
    ttk.Button(btn_frame, text="🔄 Reconectar", command=reconectar_serial).pack(side='left', padx=5)
    ttk.Button(btn_frame, text="🧪 Modo Teste", command=toggle_teste).pack(side='left', padx=5)
    
    # Valores em tempo real
    frame_valores = ttk.LabelFrame(aba_monitor, text="Valores em Tempo Real", padding=10)
    frame_valores.pack(fill='both', expand=True, padx=10, pady=5)
    
    # Volante
    ttk.Label(frame_valores, text="🎯 Volante:", font=("Arial", 11, "bold")).grid(row=0, column=0, sticky='w', pady=5)
    lbl_volante_val = ttk.Label(frame_valores, text="0 → 0.00", font=("Arial", 10))
    lbl_volante_val.grid(row=0, column=1, sticky='w', padx=10)
    barra_volante = tk.DoubleVar(value=50)
    ttk.Progressbar(frame_valores, variable=barra_volante, length=300).grid(row=0, column=2, padx=10)
    
    # Acelerador
    ttk.Label(frame_valores, text="⚡ Acelerador:", font=("Arial", 11, "bold")).grid(row=1, column=0, sticky='w', pady=5)
    lbl_acel_val = ttk.Label(frame_valores, text="0", font=("Arial", 10))
    lbl_acel_val.grid(row=1, column=1, sticky='w', padx=10)
    barra_acel = tk.DoubleVar(value=0)
    ttk.Progressbar(frame_valores, variable=barra_acel, length=300).grid(row=1, column=2, padx=10)
    
    # Freio
    ttk.Label(frame_valores, text="🛑 Freio:", font=("Arial", 11, "bold")).grid(row=2, column=0, sticky='w', pady=5)
    lbl_freio_val = ttk.Label(frame_valores, text="0", font=("Arial", 10))
    lbl_freio_val.grid(row=2, column=1, sticky='w', padx=10)
    barra_freio = tk.DoubleVar(value=0)
    ttk.Progressbar(frame_valores, variable=barra_freio, length=300).grid(row=2, column=2, padx=10)
    
    # Marchas
    frame_marchas = ttk.Frame(frame_valores)
    frame_marchas.grid(row=3, column=0, columnspan=3, pady=10)
    
    ttk.Label(frame_marchas, text="Marchas:", font=("Arial", 11, "bold")).pack(side='left', padx=5)
    lbl_marcha_up = tk.Label(frame_marchas, text="▲ UP", bg="gray80", width=8, height=2)
    lbl_marcha_up.pack(side='left', padx=5)
    lbl_marcha_down = tk.Label(frame_marchas, text="▼ DOWN", bg="gray80", width=8, height=2)
    lbl_marcha_down.pack(side='left', padx=5)
    
    # === ABA 2: VOLANTE ===
    aba_volante = ttk.Frame(notebook)
    notebook.add(aba_volante, text="🎯 Volante")
    criar_aba_config(aba_volante, 'volante', True)
    
    # === ABA 3: PEDAIS ===
    aba_pedais = ttk.Frame(notebook)
    notebook.add(aba_pedais, text="🦶 Pedais")
    
    frame_acel = ttk.LabelFrame(aba_pedais, text="⚡ Acelerador", padding=10)
    frame_acel.pack(fill='x', padx=10, pady=5)
    criar_aba_config(frame_acel, 'acelerador', False)
    
    frame_freio = ttk.LabelFrame(aba_pedais, text="🛑 Freio", padding=10)
    frame_freio.pack(fill='x', padx=10, pady=5)
    criar_aba_config(frame_freio, 'freio', False)
    
    # === ABA 4: BOTÕES ===
    aba_botoes = ttk.Frame(notebook)
    notebook.add(aba_botoes, text="🎮 Botões")
    criar_aba_botoes(aba_botoes)
    
    # === ABA 5: PERFIS ===
    aba_perfis = ttk.Frame(notebook)
    notebook.add(aba_perfis, text="⚙️ Perfis")
    criar_aba_perfis(aba_perfis)
    
    # === ABA 6: CONEXÃO ===
    aba_conexao = ttk.Frame(notebook)
    notebook.add(aba_conexao, text="🔌 Conexão")
    criar_aba_conexao(aba_conexao)
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    return root

def criar_aba_config(parent, tipo, is_volante):
    """Cria controles de configuração para eixo."""
    cfg = config[tipo]
    
    row = 0
    
    if is_volante:
        # Centro
        ttk.Label(parent, text="Centro:").grid(row=row, column=0, sticky='w', pady=5)
        var_centro = tk.IntVar(value=cfg.get('centro', 512))
        spin_centro = ttk.Spinbox(parent, from_=0, to=1023, textvariable=var_centro, width=10)
        spin_centro.grid(row=row, column=1, sticky='w', padx=5)
        ttk.Button(parent, text="Auto-Calibrar", 
                  command=lambda: auto_calibrar_centro(var_centro)).grid(row=row, column=2, padx=5)
        row += 1
        
        # Suavização
        ttk.Label(parent, text="Suavização:").grid(row=row, column=0, sticky='w', pady=5)
        var_suav = tk.IntVar(value=cfg.get('suavizacao', 0))
        ttk.Scale(parent, from_=0, to=10, variable=var_suav, orient='horizontal', length=200).grid(row=row, column=1, columnspan=2, sticky='w', padx=5)
        ttk.Label(parent, textvariable=var_suav).grid(row=row, column=3)
        row += 1
    
    # Deadzone
    ttk.Label(parent, text="Zona Morta:").grid(row=row, column=0, sticky='w', pady=5)
    var_dz = tk.IntVar(value=cfg.get('deadzone', 5))
    ttk.Scale(parent, from_=0, to=100, variable=var_dz, orient='horizontal', length=200).grid(row=row, column=1, columnspan=2, sticky='w', padx=5)
    ttk.Label(parent, textvariable=var_dz).grid(row=row, column=3)
    row += 1
    
    # Sensibilidade
    ttk.Label(parent, text="Sensibilidade (%):").grid(row=row, column=0, sticky='w', pady=5)
    var_sens = tk.IntVar(value=cfg.get('sensibilidade', 100))
    ttk.Scale(parent, from_=50, to=200, variable=var_sens, orient='horizontal', length=200).grid(row=row, column=1, columnspan=2, sticky='w', padx=5)
    ttk.Label(parent, textvariable=var_sens).grid(row=row, column=3)
    row += 1
    
    if not is_volante:
        # Curva
        ttk.Label(parent, text="Curva:").grid(row=row, column=0, sticky='w', pady=5)
        var_curva = tk.StringVar(value=cfg.get('curva', 'linear'))
        combo_curva = ttk.Combobox(parent, textvariable=var_curva, 
                                   values=['linear', 'progressivo', 'agressivo'], state='readonly', width=15)
        combo_curva.grid(row=row, column=1, sticky='w', padx=5)
        row += 1
    
    # Inverter
    var_inv = tk.BooleanVar(value=cfg.get('invertido', False))
    ttk.Checkbutton(parent, text="Inverter Eixo", variable=var_inv).grid(row=row, column=0, columnspan=2, sticky='w', pady=5)
    row += 1
    
    # Botão salvar
    def salvar():
        if is_volante:
            config[tipo]['centro'] = var_centro.get()
            config[tipo]['suavizacao'] = var_suav.get()
        else:
            config[tipo]['curva'] = var_curva.get()
        
        config[tipo]['deadzone'] = var_dz.get()
        config[tipo]['sensibilidade'] = var_sens.get()
        config[tipo]['invertido'] = var_inv.get()
        salvar_configuracoes()
        messagebox.showinfo("✓ Salvo", f"Configurações de {tipo} salvas!")
    
    ttk.Button(parent, text="💾 Salvar", command=salvar).grid(row=row, column=0, columnspan=4, pady=10)

def criar_aba_botoes(parent):
    """Interface para mapear botões."""
    botoes_xbox = [
        "XUSB_GAMEPAD_A", "XUSB_GAMEPAD_B", "XUSB_GAMEPAD_X", "XUSB_GAMEPAD_Y",
        "XUSB_GAMEPAD_LEFT_SHOULDER", "XUSB_GAMEPAD_RIGHT_SHOULDER",
        "XUSB_GAMEPAD_BACK", "XUSB_GAMEPAD_START",
        "XUSB_GAMEPAD_LEFT_THUMB", "XUSB_GAMEPAD_RIGHT_THUMB",
        "XUSB_GAMEPAD_DPAD_UP", "XUSB_GAMEPAD_DPAD_DOWN",
        "XUSB_GAMEPAD_DPAD_LEFT", "XUSB_GAMEPAD_DPAD_RIGHT"
    ]
    
    funcoes = list(config['mapeamento_botoes'].keys())
    
    ttk.Label(parent, text="Configure o mapeamento dos botões físicos para o controle Xbox virtual:", 
             font=("Arial", 10)).pack(pady=10)
    
    frame_map = ttk.Frame(parent)
    frame_map.pack(fill='both', expand=True, padx=20, pady=10)
    
    vars_botoes = {}
    row = 0
    
    for funcao in funcoes:
        ttk.Label(frame_map, text=f"{funcao.replace('_', ' ').title()}:", 
                 font=("Arial", 10, "bold")).grid(row=row, column=0, sticky='w', pady=5, padx=5)
        
        var = tk.StringVar(value=config['mapeamento_botoes'].get(funcao, botoes_xbox[0]))
        vars_botoes[funcao] = var
        
        combo = ttk.Combobox(frame_map, textvariable=var, values=botoes_xbox, state='readonly', width=30)
        combo.grid(row=row, column=1, sticky='w', padx=10, pady=5)
        row += 1
    
    def salvar_botoes():
        for funcao, var in vars_botoes.items():
            config['mapeamento_botoes'][funcao] = var.get()
        salvar_configuracoes()
        messagebox.showinfo("✓ Salvo", "Mapeamento de botões salvo!")
    
    ttk.Button(parent, text="💾 Salvar Mapeamento", command=salvar_botoes).pack(pady=20)

def criar_aba_perfis(parent):
    """Interface para gerenciar perfis."""
    ttk.Label(parent, text="Perfis Pré-Configurados para BeamNG.drive:", 
             font=("Arial", 11, "bold")).pack(pady=10)
    
    frame_perfis = ttk.Frame(parent)
    frame_perfis.pack(fill='both', expand=True, padx=20, pady=10)
    
    for nome_perfil, cfg_perfil in PERFIS.items():
        frame_p = ttk.LabelFrame(frame_perfis, text=nome_perfil, padding=10)
        frame_p.pack(fill='x', pady=5)
        
        desc = f"Volante: DZ {cfg_perfil['volante']['deadzone']} | Sens {cfg_perfil['volante']['sensibilidade']}%\n"
        desc += f"Acelerador: {cfg_perfil['acelerador']['curva']} | Freio: {cfg_perfil['freio']['curva']}"
        
        ttk.Label(frame_p, text=desc, font=("Arial", 9)).pack(side='left', padx=10)
        
        def aplicar(p=nome_perfil):
            config['volante'].update(PERFIS[p]['volante'])
            config['acelerador'].update(PERFIS[p]['acelerador'])
            config['freio'].update(PERFIS[p]['freio'])
            config['perfil_ativo'] = p
            salvar_configuracoes()
            messagebox.showinfo("✓ Perfil Aplicado", f"Perfil '{p}' ativado!")
        
        ttk.Button(frame_p, text="Aplicar", command=aplicar).pack(side='right', padx=5)

def criar_aba_conexao(parent):
    """Interface para configurar conexão serial."""
    ttk.Label(parent, text="Configurações de Conexão Serial:", 
             font=("Arial", 11, "bold")).pack(pady=10)
    
    frame_con = ttk.Frame(parent)
    frame_con.pack(fill='x', padx=20, pady=10)
    
    # Porta
    ttk.Label(frame_con, text="Porta COM:").grid(row=0, column=0, sticky='w', pady=5)
    var_porta = tk.StringVar(value=config.get('porta_serial', 'COM3'))
    
    portas_disponiveis = [port.device for port in serial.tools.list_ports.comports()]
    combo_porta = ttk.Combobox(frame_con, textvariable=var_porta, values=portas_disponiveis, width=20)
    combo_porta.grid(row=0, column=1, sticky='w', padx=10)
    
    ttk.Button(frame_con, text="🔍 Atualizar", 
              command=lambda: combo_porta.config(values=[p.device for p in serial.tools.list_ports.comports()])).grid(row=0, column=2, padx=5)
    
    # Baud Rate
    ttk.Label(frame_con, text="Baud Rate:").grid(row=1, column=0, sticky='w', pady=5)
    var_baud = tk.IntVar(value=config.get('baud_rate', 9600))
    combo_baud = ttk.Combobox(frame_con, textvariable=var_baud, 
                             values=[9600, 19200, 38400, 57600, 115200], state='readonly', width=20)
    combo_baud.grid(row=1, column=1, sticky='w', padx=10)
    
    def salvar_conexao():
        config['porta_serial'] = var_porta.get()
        config['baud_rate'] = var_baud.get()
        salvar_configuracoes()
        messagebox.showinfo("✓ Salvo", "Reinicie a aplicação para aplicar as mudanças de conexão.")
    
    ttk.Button(parent, text="💾 Salvar e Reconectar", command=salvar_conexao).pack(pady=20)

# --- FUNÇÕES AUXILIARES ---
def reconectar_serial():
    global arduino
    try:
        if arduino and arduino.is_open:
            arduino.close()
        arduino = serial.Serial(config['porta_serial'], config['baud_rate'], timeout=1)
        messagebox.showinfo("✓ Conectado", f"Conectado em {config['porta_serial']}")
    except:
        messagebox.showerror("✗ Erro", "Falha ao conectar. Verifique a porta e o cabo.")

def toggle_teste():
    global modo_teste
    modo_teste = not modo_teste
    status = "ATIVADO" if modo_teste else "DESATIVADO"
    messagebox.showinfo("Modo Teste", f"Modo Teste {status}\nO controle virtual está {'pausado' if modo_teste else 'ativo'}.")

def auto_calibrar_centro(var_centro):
    messagebox.showinfo("Auto-Calibrar", "Centralize o volante e aguarde 3 segundos...")
    # Implementar leitura e média dos valores
    messagebox.showinfo("✓ Calibrado", f"Novo centro: {var_centro.get()}")

def on_closing():
    global running, arduino
    running = False
    time.sleep(0.2)
    if arduino and arduino.is_open:
        arduino.close()
    root.destroy()

# --- INICIALIZAÇÃO ---
if __name__ == "__main__":
    print("=" * 60)
    print("🎮 VOLANTE PRO - CONFIGURADOR BEAMNG.DRIVE")
    print("=" * 60)
    
    # Carregar configurações
    carregar_configuracoes()
    print(f"✓ Perfil ativo: {config.get('perfil_ativo', 'Padrão')}")
    
    # Tentar conectar serial
    try:
        porta = config.get('porta_serial', 'COM3')
        baud = config.get('baud_rate', 9600)
        arduino = serial.Serial(porta, baud, timeout=1)
        print(f"✓ Conectado em {porta} @ {baud} baud")
    except serial.SerialException as e:
        print(f"✗ Erro ao conectar em {config.get('porta_serial', 'N/A')}")
        print(f"  Verifique se o Arduino está conectado corretamente.")
        arduino = None
    
    # Criar interface
    root = criar_interface()
    
    # Iniciar thread de controle
    thread = threading.Thread(target=controle_loop, daemon=True)
    thread.start()
    print("✓ Thread de controle iniciada")
    
    print("\n🚀 Aplicativo pronto! Configurações disponíveis nas abas.")
    print("=" * 60)
    
    # Iniciar GUI
    root.mainloop()