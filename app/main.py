import flet as ft
import paho.mqtt.client as mqtt
import random
import datetime
import threading
import time
import json
import os
import re
import tempfile

# --- CONFIGURAÇÕES MQTT ---
MQTT_SERVER = "ifspb-czemnumeros.com.br"
MQTT_PORT = 1883
MQTT_USER = "esp12_01"
MQTT_PASS = "esp12_01"

automacoes = {
    "casa/rele1": {"timer_minutos": 0, "hora_agenda": "18:00", "dias_agenda": set(), "timer_objeto": None},
    "casa/rele2": {"timer_minutos": 0, "hora_agenda": "18:00", "dias_agenda": set(), "timer_objeto": None}
}

def main(page: ft.Page):
    try:
        # --- DEFINIÇÃO DO CAMINHO DE CONFIGURAÇÃO ---
        config_dir = None
        try:
            upload_dir = page.get_upload_dir()
            if upload_dir and os.access(upload_dir, os.W_OK):
                config_dir = upload_dir
        except Exception:
            pass

        if config_dir is None:
            home = os.path.expanduser("~/.meu_jardim")
            try:
                os.makedirs(home, exist_ok=True)
                config_dir = home
            except Exception:
                config_dir = tempfile.gettempdir()

        CONFIG_FILE = os.path.join(config_dir, "config_automacoes_jardim.json")
        print(f"DEBUG: Caminho de configuração: {CONFIG_FILE}")

        # --- PERSISTÊNCIA ---
        def salvar_dados_no_disco():
            try:
                dados_para_salvar = {
                    "casa/rele1": {
                        "timer_minutos": automacoes["casa/rele1"]["timer_minutos"],
                        "hora_agenda": automacoes["casa/rele1"]["hora_agenda"],
                        "dias_agenda": list(automacoes["casa/rele1"]["dias_agenda"])
                    },
                    "casa/rele2": {
                        "timer_minutos": automacoes["casa/rele2"]["timer_minutos"],
                        "hora_agenda": automacoes["casa/rele2"]["hora_agenda"],
                        "dias_agenda": list(automacoes["casa/rele2"]["dias_agenda"])
                    }
                }
                with open(CONFIG_FILE, "w") as f:
                    json.dump(dados_para_salvar, f, indent=2)
                print("DEBUG: Dados salvos.")
            except Exception as e:
                print(f"Erro ao salvar: {e}")

        def carregar_dados_local():
            try:
                if os.path.exists(CONFIG_FILE):
                    with open(CONFIG_FILE, "r") as f:
                        dados_dict = json.load(f)
                    for pino in ["casa/rele1", "casa/rele2"]:
                        if pino in dados_dict:
                            automacoes[pino]["hora_agenda"] = dados_dict[pino].get("hora_agenda", "18:00")
                            automacoes[pino]["timer_minutos"] = dados_dict[pino].get("timer_minutos", 0)
                            automacoes[pino]["dias_agenda"] = set(dados_dict[pino].get("dias_agenda", []))
            except Exception as e:
                print(f"Erro ao carregar: {e}")

        carregar_dados_local()

        # --- INTERFACE ---
        page.title = "Meu Jardim - Automação"
        page.theme_mode = ft.ThemeMode.DARK
        page.window_width = 400
        page.window_height = 700
        page.horizontal_alignment = "center"

        page.appbar = ft.AppBar(
            title=ft.Text("🏡 AUTOMAÇÃO", size=22, weight="bold"),
            center_title=True,
            bgcolor="surface",
        )

        status_led = ft.Container(width=12, height=12, bgcolor="red", border_radius=6)
        status_text = ft.Text("Desconectado", color="red", weight="bold")

        # --- MQTT ---
        client_id = f'flet-mqtt-{random.randint(0, 1000)}'
        mqttc = mqtt.Client(client_id=client_id)
        mqttc.username_pw_set(MQTT_USER, MQTT_PASS)

        def on_connect(client, userdata, flags, rc):
            if rc == 0:
                status_led.bgcolor = "green"
                status_text.value = "Online"
                status_text.color = "green"
            else:
                status_led.bgcolor = "red"
                status_text.value = f"Erro {rc}"
                status_text.color = "red"
            page.update()

        def on_disconnect(client, userdata, rc):
            status_led.bgcolor = "red"
            status_text.value = "Desconectado"
            status_text.color = "red"
            page.update()
            def reconectar():
                while True:
                    try:
                        client.reconnect()
                        break
                    except Exception:
                        time.sleep(5)
            threading.Thread(target=reconectar, daemon=True).start()

        mqttc.on_connect = on_connect
        mqttc.on_disconnect = on_disconnect

        def publicar_mqtt(topic, msg):
            try:
                mqttc.publish(topic, msg)
            except Exception as e:
                print(f"Erro ao publicar {topic}: {e}")
                page.snack_bar = ft.SnackBar(ft.Text("Falha na comunicação MQTT"), duration=2000)
                page.snack_bar.open = True
                page.update()

        def mudar_rele(e):
            topic = e.control.data
            msg = "ON" if e.control.value else "OFF"
            if msg == "OFF" and automacoes[topic]["timer_objeto"] is not None:
                try:
                    automacoes[topic]["timer_objeto"].cancel()
                    automacoes[topic]["timer_objeto"] = None
                    automacoes[topic]["timer_minutos"] = 0
                except Exception:
                    pass
            publicar_mqtt(topic, msg)

        def desligar_por_timeout(pino_rele, switch_componente):
            try:
                publicar_mqtt(pino_rele, "OFF")
                switch_componente.value = False
                automacoes[pino_rele]["timer_objeto"] = None
                automacoes[pino_rele]["timer_minutos"] = 0
                page.snack_bar = ft.SnackBar(ft.Text(f"⏰ {pino_rele} desligado!"), duration=2000)
                page.snack_bar.open = True
                page.update()
            except Exception as e:
                print(f"Erro no timeout: {e}")

        def acionar_temporizador(pino_rele, txt_timer, switch_componente):
            try:
                minutos = int(txt_timer.value)
            except ValueError:
                page.snack_bar = ft.SnackBar(ft.Text("Insira um número válido!"), duration=2000)
                page.snack_bar.open = True
                page.update()
                return
            if minutos <= 0:
                page.snack_bar = ft.SnackBar(ft.Text("Tempo deve ser > 0!"), duration=2000)
                page.snack_bar.open = True
                page.update()
                return
            if automacoes[pino_rele]["timer_objeto"] is not None:
                try:
                    automacoes[pino_rele]["timer_objeto"].cancel()
                except Exception:
                    pass
            publicar_mqtt(pino_rele, "ON")
            switch_componente.value = True
            page.snack_bar = ft.SnackBar(ft.Text(f"⏱ Temporizador {minutos} min"), duration=2000)
            page.snack_bar.open = True
            page.update()
            segundos = minutos * 60
            t = threading.Timer(segundos, desligar_por_timeout, args=[pino_rele, switch_componente])
            automacoes[pino_rele]["timer_objeto"] = t
            automacoes[pino_rele]["timer_minutos"] = minutos
            t.start()
            salvar_dados_no_disco()

        def hora_valida(h):
            return re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", h) is not None

        # --- CRIAÇÃO DOS BLOCOS DE AUTOMAÇÃO ---
        def criar_bloco_automacao(pino_rele, switch_componente):
            txt_timer = ft.TextField(
                label="Minutos", 
                value=str(automacoes[pino_rele].get("timer_minutos", 0)), 
                width=80,
                text_align="center", 
                dense=True,
                content_padding=5
            )
            txt_hora = ft.TextField(
                label="HH:MM", 
                value=automacoes[pino_rele].get("hora_agenda", "18:00"), 
                width=80,
                text_align="center", 
                dense=True,
                content_padding=5
            )

            def salvar_agenda(e):
                hora = txt_hora.value
                if not hora_valida(hora):
                    page.snack_bar = ft.SnackBar(ft.Text("Formato inválido! Use HH:MM"), duration=2000)
                    page.snack_bar.open = True
                    page.update()
                    return
                automacoes[pino_rele]["hora_agenda"] = hora
                salvar_dados_no_disco()
                page.snack_bar = ft.SnackBar(ft.Text(f"Agenda salva para as {hora}!"), duration=2000)
                page.snack_bar.open = True
                page.update()

            def alternar_dia_btn(e):
                container_alvo = e.control.content
                dia = container_alvo.data
                if dia in automacoes[pino_rele]["dias_agenda"]:
                    automacoes[pino_rele]["dias_agenda"].discard(dia)
                    container_alvo.bgcolor = "transparent"
                    container_alvo.border = ft.Border(
                        top=ft.BorderSide(1, "white30"),
                        bottom=ft.BorderSide(1, "white30"),
                        left=ft.BorderSide(1, "white30"),
                        right=ft.BorderSide(1, "white30")
                    )
                else:
                    automacoes[pino_rele]["dias_agenda"].add(dia)
                    container_alvo.bgcolor = "blue"
                    container_alvo.border = ft.Border(
                        top=ft.BorderSide(1, "blue"),
                        bottom=ft.BorderSide(1, "blue"),
                        left=ft.BorderSide(1, "blue"),
                        right=ft.BorderSide(1, "blue")
                    )
                salvar_dados_no_disco()
                page.update()

            dias_semana = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
            lista_botoes = []
            for d in dias_semana:
                esta_selecionado = d in automacoes[pino_rele]["dias_agenda"]
                bgcolor = "blue" if esta_selecionado else "transparent"
                cor_borda = "blue" if esta_selecionado else "white30"
                container_chip = ft.Container(
                    content=ft.Text(d, size=10, weight="bold", color="white"),
                    alignment=ft.Alignment.CENTER,
                    padding=4,
                    width=38,
                    height=28,
                    border_radius=6,
                    border=ft.Border(
                        top=ft.BorderSide(1, cor_borda),
                        bottom=ft.BorderSide(1, cor_borda),
                        left=ft.BorderSide(1, cor_borda),
                        right=ft.BorderSide(1, cor_borda)
                    ),
                    bgcolor=bgcolor,
                    data=d
                )
                detector_clique = ft.GestureDetector(
                    content=container_chip,
                    on_tap=alternar_dia_btn
                )
                lista_botoes.append(detector_clique)

            def acionar_temporizador_local(e):
                acionar_temporizador(pino_rele, txt_timer, switch_componente)

            return ft.ExpansionTile(
                title=ft.Text("Configurar Temporizador e Agenda", size=13, color="blue200"),
                maintain_state=True,
                expanded=True,
                controls=[
                    ft.Container(
                        padding=8,
                        bgcolor="black26",
                        border_radius=8,
                        content=ft.Column(
                            [
                                ft.Row([
                                    ft.Icon(ft.Icons.TIMER, color="amber", size=18),
                                    ft.Text("Desligar em:", weight="w500", size=12),
                                    txt_timer,
                                    ft.IconButton(
                                        icon=ft.Icons.PLAY_ARROW_ROUNDED, 
                                        icon_color="green",
                                        icon_size=20,
                                        on_click=acionar_temporizador_local
                                    )
                                ], alignment="spaceBetween", vertical_alignment="center"),
                                
                                ft.Divider(height=8, color="white10"),
                                
                                ft.Row([
                                    ft.Icon(ft.Icons.SCHEDULE, color="blue", size=18),
                                    ft.Text("Ligar às:", weight="w500", size=12),
                                    txt_hora,
                                    ft.IconButton(
                                        icon=ft.Icons.SAVE, 
                                        icon_color="blue",
                                        icon_size=20,
                                        on_click=salvar_agenda
                                    )
                                ], alignment="spaceBetween", vertical_alignment="center"),
                                
                                ft.Text("Dias da semana:", size=11, color="white60"),
                                ft.Row(
                                    lista_botoes, 
                                    wrap=True,
                                    alignment="center", 
                                    spacing=4
                                )
                            ],
                            spacing=6,
                            tight=True,
                        )
                    )
                ]
            )

        # --- SWITCHES ---
        switch_luz = ft.Switch(value=False, data="casa/rele1", on_change=mudar_rele)
        switch_tomada = ft.Switch(value=False, data="casa/rele2", on_change=mudar_rele)

        # --- CARDS ---
        card_luz = ft.Card(
            content=ft.Container(
                padding=10,
                content=ft.Column([
                    ft.Row([
                        ft.Row([ft.Icon(ft.Icons.LIGHTBULB, color="amber", size=24), ft.Text("Luz (D6)", size=16, weight="w500")]),
                        switch_luz
                    ], alignment="spaceBetween"),
                    criar_bloco_automacao("casa/rele1", switch_luz)
                ], spacing=5)
            )
        )

        card_tomada = ft.Card(
            content=ft.Container(
                padding=10,
                content=ft.Column([
                    ft.Row([
                        ft.Row([ft.Icon(ft.Icons.POWER, color="blueGrey200", size=24), ft.Text("Tomada (D7)", size=16, weight="w500")]),
                        switch_tomada
                    ], alignment="spaceBetween"),
                    criar_bloco_automacao("casa/rele2", switch_tomada)
                ], spacing=5)
            )
        )

        # --- COLUNA PRINCIPAL ---
        conteudo_principal = ft.Column(
            controls=[
                ft.Container(height=10),
                ft.Row([status_led, status_text], alignment="center"),
                ft.Divider(height=10),
                card_luz,
                card_tomada,
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            spacing=10
        )

        page.add(conteudo_principal)
        page.update()

        # --- THREADS ---
        def conectar_mqtt():
            try:
                mqttc.connect(MQTT_SERVER, MQTT_PORT, 60)
                mqttc.loop_start()
            except Exception as e:
                print(f"Erro ao conectar MQTT: {e}")

        def loop_verificacao_horario():
            while True:
                agora = datetime.datetime.now()
                dias_map = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
                dia_atual_str = dias_map[agora.weekday()]
                hora_atual_str = agora.strftime("%H:%M")
                for pino, dados in automacoes.items():
                    if dados["hora_agenda"] == hora_atual_str and dia_atual_str in dados["dias_agenda"]:
                        if dados["timer_objeto"] is not None:
                            try:
                                dados["timer_objeto"].cancel()
                                dados["timer_objeto"] = None
                            except Exception:
                                pass
                        publicar_mqtt(pino, "ON")
                        if pino == "casa/rele1":
                            switch_luz.value = True
                        elif pino == "casa/rele2":
                            switch_tomada.value = True
                        page.update()
                time.sleep(60)

        threading.Thread(target=conectar_mqtt, daemon=True).start()
        threading.Thread(target=loop_verificacao_horario, daemon=True).start()

    except Exception as e:
        import traceback
        erro_texto = traceback.format_exc()
        page.add(ft.Text(f"ERRO: {erro_texto}", color="red", size=16))
        page.update()

# --- PONTO DE ENTRADA (versão mais nova) ---
ft.app(target=main)