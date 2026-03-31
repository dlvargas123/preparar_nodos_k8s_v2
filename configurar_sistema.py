#!/usr/bin/env python3
import os
import subprocess
import sys
import time
import shutil
import getpass

# --- CONFIGURACIÓN DE COLORES (UX EN TERMINAL) ---
class Color:
    VERDE = '\033[92m'
    AMARILLO = '\033[93m'
    ROJO = '\033[91m'
    CYAN = '\033[96m'
    BOLD = '\033[1m'
    FIN = '\033[0m'

def print_step(text):
    print(f"\n{Color.CYAN}{Color.BOLD}>>> {text}{Color.FIN}")

def print_success(text):
    print(f"{Color.VERDE} [OK] {text}{Color.FIN}")

def print_error(text):
    print(f"{Color.ROJO} [ERROR] {text}{Color.FIN}")

def check_root():
    if os.geteuid() != 0:
        print_error("Este script requiere privilegios de superusuario (sudo).")
        sys.exit(1)

def run_cmd(command):
    return subprocess.run(command, shell=True, capture_output=True, text=True)

def header():
    os.system('clear')
    print(f"{Color.CYAN}{'='*60}")
    print(f"{Color.BOLD}   SISTEMA DE CONFIGURACIÓN DE NODOS UBUNTU")
    print(f"               By dlvargas")
    print(f"{'='*60}{Color.FIN}\n")

def gestionar_usuario():
    print_step("CONFIGURACIÓN DE USUARIO ADMINISTRADOR")
    user = input(f"{Color.AMARILLO}Nombre del nuevo usuario: {Color.FIN}").strip()
    while True:
        p1 = getpass.getpass(f"{Color.AMARILLO}Contraseña: {Color.FIN}")
        p2 = getpass.getpass(f"{Color.AMARILLO}Confirma contraseña: {Color.FIN}")
        if p1 == p2 and len(p1) > 0:
            break
        print_error("Las contraseñas no coinciden o están vacías.")
    
    run_cmd(f"useradd -m -s /bin/bash {user}")
    subprocess.run(['chpasswd'], input=f"{user}:{p1}", text=True)
    run_cmd(f"usermod -aG sudo {user}")
    print_success(f"Usuario '{user}' creado y añadido al grupo sudo.")
    return user

def main():
    check_root()
    header()
    reporte = []

    # --- RECOLECCIÓN DE DATOS ---
    
    # 1. Red
    print_step("CONFIGURACIÓN DE RED")
    ifaces = [iface for iface in os.listdir('/sys/class/net/') if iface != 'lo']
    for i, iface in enumerate(ifaces): print(f"  {i}) {iface}")
    
    try:
        idx = int(input(f"\n{Color.AMARILLO}Selecciona interfaz: {Color.FIN}"))
        interfaz = ifaces[idx]
    except:
        print_error("Selección inválida."); return

    ip = input(f"{Color.AMARILLO}IP/CIDR (ej. 192.168.130.81/24): {Color.FIN}")
    gw = input(f"{Color.AMARILLO}Puerta de enlace (Gateway): {Color.FIN}")
    dns = input(f"{Color.AMARILLO}Servidores DNS (separados por coma): {Color.FIN}")
    dns_list = [d.strip() for d in dns.split(',')]

    # 2. Fecha, Hora y NTP
    print_step("CONFIGURACIÓN DE FECHA, HORA Y NTP")
    tz = input(f"{Color.AMARILLO}Zona horaria (Enter para America/Bogota): {Color.FIN}").strip()
    if not tz: tz = "America/Bogota"

    ntp_servers = input(f"{Color.AMARILLO}Servidores NTP (Enter para default Ubuntu, o separa por espacio): {Color.FIN}").strip()

    # 3. Hostname
    nuevo_h = ""
    if input(f"\n{Color.AMARILLO}¿Deseas cambiar el hostname? (s/n): {Color.FIN}").lower() == 's':
        nuevo_h = input(f"{Color.AMARILLO}Nuevo nombre de VM: {Color.FIN}")

    # 4. Usuario y Seguridad
    user_creado = gestionar_usuario() if input(f"\n{Color.AMARILLO}¿Crear usuario admin? (s/n): {Color.FIN}").lower() == 's' else ""
    sec_ssh = input(f"\n{Color.AMARILLO}¿Bloquear login de Root por SSH? (s/n): {Color.FIN}").lower() == 's'

    # --- PROCESO TÉCNICO (APLICACIÓN) ---
    print_step("PROCESANDO CAMBIOS EN EL SISTEMA...")

    # A. Configurar Timezone y NTP
    run_cmd(f"timedatectl set-timezone {tz}")
    
    if ntp_servers:
        # Configurar servidores NTP específicos en timesyncd.conf
        conf_ntp = f"[Time]\nNTP={ntp_servers}\n"
        with open("/etc/systemd/timesyncd.conf", "w") as f:
            f.write(conf_ntp)
        run_cmd("systemctl restart systemd-timesyncd")
        reporte.append(f"NTP: Configurado con {ntp_servers}")
    else:
        run_cmd("timedatectl set-ntp true")
        reporte.append("NTP: Usando servidores por defecto")

    reporte.append(f"Zona horaria establecida: {tz}")

    # B. Depuración Netplan
    ruta = "/etc/netplan/"
    for archivo in os.listdir(ruta):
        if archivo.endswith(".yaml") and not archivo.endswith("-bck"):
            shutil.move(os.path.join(ruta, archivo), os.path.join(ruta, archivo + "-bck"))
    
    # C. Escritura Netplan
    with open("/etc/netplan/99-config-estatica.yaml", "w") as f:
        f.write(f"network:\n  version: 2\n  renderer: networkd\n  ethernets:\n    {interfaz}:\n      addresses: [{ip}]\n      routes: [{{to: default, via: {gw}}}]\n      nameservers: {{addresses: {dns_list}}}\n")
    reporte.append(f"Red estática: {ip} en {interfaz}")

    # D. Hostname
    if nuevo_h:
        with open("/etc/hostname", "w") as f: f.write(nuevo_h + "\n")
        run_cmd(f"sed -i 's/127.0.1.1.*/127.0.1.1\t{nuevo_h}/g' /etc/hosts")
        run_cmd(f"hostnamectl set-hostname {nuevo_h}")
        reporte.append(f"Hostname: '{nuevo_h}'")

    # E. Seguridad SSH
    if sec_ssh:
        run_cmd("sed -i '/^PermitRootLogin/d' /etc/ssh/sshd_config")
        with open("/etc/ssh/sshd_config", "a") as f: f.write("\nPermitRootLogin no\n")
        run_cmd("systemctl restart ssh")
        reporte.append("Seguridad: Root SSH bloqueado")

    # F. MACHINE-ID (COMANDOS SOLICITADOS POR EL USUARIO)
    run_cmd("echo -n > /etc/machine-id")
    run_cmd("rm -f /var/lib/dbus/machine-id")
    run_cmd("ln -s /etc/machine-id /var/lib/dbus/machine-id")
    reporte.append("Machine-ID: Reseteado y vinculado")

    # --- CIERRE Y REPORTE ---
    print(f"\n{Color.BOLD}{'='*40}\n   RESUMEN DE OPERACIÓN\n{'='*40}{Color.FIN}")
    for item in reporte: print_success(item)
    if user_creado: print_success(f"Usuario '{user_creado}' listo")
    print(f"{Color.BOLD}{'='*40}{Color.FIN}")

    print(f"\n{Color.ROJO}{Color.BOLD}!!! PUNTO DE NO RETORNO !!!{Color.FIN}")
    print(f"Al presionar ENTER se aplicará la red y el sistema se reiniciará.")
    print(f"Nueva IP de acceso: {Color.CYAN}{ip}{Color.FIN}")
    
    input(f"\n{Color.BOLD}Presiona ENTER para aplicar cambios, reiniciar ...{Color.FIN}")

    run_cmd("netplan apply")
    run_cmd("systemctl restart systemd-resolved")
    
    print(f"\n{Color.VERDE}Todo listo. Reiniciando en 5 segundos...{Color.FIN}")
    time.sleep(5)
    os.system("reboot")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{Color.ROJO}[!] Script cancelado por el usuario.{Color.FIN}")
