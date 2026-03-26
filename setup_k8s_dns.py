#!/usr/bin/env python3
import os
import subprocess
import sys
import time

def run_command(command):
    """Ejecuta un comando de sistema y retorna la salida."""
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None

def fix_resolv_conf_symlink():
    """Restaura el enlace simbólico nativo de Ubuntu para DNS."""
    target = "/run/systemd/resolve/stub-resolv.conf"
    link = "/etc/resolv.conf"
    print("[*] Asegurando enlace simbólico nativo en /etc/resolv.conf...")
    try:
        if os.path.islink(link):
            os.unlink(link)
        elif os.path.exists(link):
            os.remove(link)
        os.symlink(target, link)
        return True
    except Exception as e:
        print(f"[!] ERROR al crear el enlace simbólico: {e}")
        return False

def configure_network():
    print("=== CONFIGURADOR DE DNS PARA K8S NODES (IFX NETWORK) ===")
    
    # 1. Validar privilegios de Root
    if os.geteuid() != 0:
        print("[!] ERROR: Este script debe ejecutarse con sudo.")
        sys.exit(1)

    # 2. Identificar Interfaz, Gateway e IP actual
    print("[*] Detectando configuración de red activa...")
    # Se ajustó el awk para manejar posibles variaciones en la salida de ip route
    interface = run_command("ip route | grep default | head -n1 | awk '{print $5}'")
    gateway = run_command("ip route | grep default | head -n1 | awk '{print $3}'")
    ip_with_mask = run_command(f"ip -4 addr show {interface} | grep inet | awk '{{print $2}}' | head -n1")

    if not interface or not gateway or not ip_with_mask:
        print("[!] ERROR: No se pudo auto-detectar la red. Revisa 'ip route'.")
        sys.exit(1)

    print(f"    > Interfaz: {interface}")
    print(f"    > IP Actual: {ip_with_mask}")
    print(f"    > Gateway: {gateway}")

    # 3. Definir los DNS Corporativos (Unicamente los internos)
    dns_servers = ["172.29.1.10", "172.29.1.12"]
    all_dns = ", ".join(dns_servers)

    netplan_content = f"""# Configuración generada por script de automatización DNS
network:
  version: 2
  ethernets:
    {interface}:
      addresses:
      - {ip_with_mask}
      routes:
      - to: default
        via: {gateway}
      nameservers:
        addresses: [{all_dns}]
"""

    # 4. Escribir el archivo de Netplan
    config_file = "/etc/netplan/00-installer-config.yaml"
    print(f"[*] Aplicando cambios persistentes en {config_file}...")
    try:
        with open(config_file, "w") as f:
            f.write(netplan_content)
    except Exception as e:
        print(f"[!] ERROR al escribir el archivo: {e}")
        sys.exit(1)

    # 5. Aplicar cambios y restaurar salud del sistema
    fix_resolv_conf_symlink()
    
    print("[*] Ejecutando 'netplan apply'...")
    run_command("netplan apply")
    
    print("[*] Reiniciando y refrescando systemd-resolved...")
    run_command("systemctl restart systemd-resolved")
    run_command("resolvectl flush-caches")
    time.sleep(2)

def verify_resolution():
    print("\n=== PRUEBAS DE VALIDACIÓN FINAL ===")
    dominio_test = "backend-uat1.akros.tech"
    dns_target = "172.29.1.10"

    # Prueba 1: Verificación de resolvectl
    print(f"[*] 1. Verificando DNS en resolvectl status...")
    status = run_command(f"resolvectl status | grep {dns_target}")
    if status:
        print(f"    [OK] Servidor {dns_target} detectado por el sistema.")
    else:
        print(f"    [FAIL] El servidor DNS no aparece como activo.")

    # Prueba 2: Resolución nativa
    print(f"[*] 2. Validando resolución nativa de Ubuntu (nslookup)...")
    ns_res = run_command(f"nslookup {dominio_test}")
    if ns_res and "Address:" in ns_res:
        print(f"    [OK] El sistema resuelve {dominio_test}")
        print(f"    {ns_res.splitlines()[-1]}")
    else:
        print(f"    [FAIL] El sistema NO resuelve el nombre.")

if __name__ == "__main__":
    configure_network()
    verify_resolution()
    print("\n[!] IMPORTANTE: Ejecuta 'kubectl rollout restart deployment coredns -n kube-system' para que los Pods tomen el cambio.")
