#!/usr/bin/env python3
import os
import subprocess
import sys
import time

def run_command(command, required=False):
    """Ejecuta un comando de sistema y retorna la salida."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            check=True,
            capture_output=True,
            text=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        if required:
            print(f"[!] ERROR ejecutando: {command}")
            if e.stderr:
                print(e.stderr.strip())
            sys.exit(1)
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
        print(f"    [OK] {link} -> {target}")
        return True
    except Exception as e:
        print(f"[!] ERROR al crear el enlace simbólico: {e}")
        return False

def configure_network():
    print("=== CONFIGURADOR DE DNS PARA K8S NODES (IFX NETWORK) ===")

    # 1. Validar privilegios de Root
    if os.geteuid() != 0:
        print("[!] ERROR: Este script debe ejecutarse con sudo/root.")
        sys.exit(1)

    # 2. Identificar Interfaz, Gateway e IP actual
    print("[*] Detectando configuración de red activa...")
    interface = run_command("ip route | grep default | head -n1 | awk '{print $5}'")
    gateway = run_command("ip route | grep default | head -n1 | awk '{print $3}'")

    if not interface or not gateway:
        print("[!] ERROR: No se pudo auto-detectar la interfaz o gateway.")
        sys.exit(1)

    ip_with_mask = run_command(f"ip -4 addr show {interface} | grep 'inet ' | awk '{{print $2}}' | head -n1")
    if not ip_with_mask:
        print("[!] ERROR: No se pudo detectar la IP de la interfaz.")
        sys.exit(1)

    print(f"    > Interfaz: {interface}")
    print(f"    > IP Actual: {ip_with_mask}")
    print(f"    > Gateway: {gateway}")

    # 3. DNS corporativos
    dns_servers = ["172.29.1.10", "172.29.1.12", "172.29.2.14"]
    all_dns = ", ".join(dns_servers)
    search_domains = ["akros.tech"]

    netplan_content = f"""# Configuración generada por script de automatización DNS
network:
  version: 2
  ethernets:
    {interface}:
      dhcp4: false
      addresses:
        - {ip_with_mask}
      routes:
        - to: default
          via: {gateway}
      nameservers:
        search: [{", ".join(search_domains)}]
        addresses: [{all_dns}]
"""

    # 4. Escribir el archivo de Netplan
    config_file = "/etc/netplan/00-installer-config.yaml"
    print(f"[*] Aplicando cambios persistentes en {config_file}...")
    try:
        with open(config_file, "w") as f:
            f.write(netplan_content)
        print("    [OK] Archivo Netplan actualizado.")
    except Exception as e:
        print(f"[!] ERROR al escribir el archivo: {e}")
        sys.exit(1)

    # 5. Restaurar resolv.conf al flujo normal de Ubuntu/systemd-resolved
    if not fix_resolv_conf_symlink():
        sys.exit(1)

    # 6. Aplicar cambios
    print("[*] Ejecutando 'netplan generate'...")
    run_command("netplan generate", required=True)

    print("[*] Ejecutando 'netplan apply'...")
    run_command("netplan apply", required=True)

    print("[*] Reiniciando y refrescando systemd-resolved...")
    run_command("systemctl restart systemd-resolved", required=True)
    run_command("resolvectl flush-caches")
    time.sleep(3)

def verify_resolution():
    print("\n=== PRUEBAS DE VALIDACIÓN FINAL ===")
    dominio_test = "istio-devops-nonprod.akros.tech"
    dns_target = "172.29.2.14"
    success = True

    # Prueba 1: Verificación de resolvectl status
    print(f"[*] 1. Verificando que {dns_target} aparezca en resolvectl status...")
    status = run_command(f"resolvectl status | grep -F '{dns_target}'")
    if status:
        print(f"    [OK] Servidor {dns_target} detectado por el sistema.")
    else:
        print(f"    [FAIL] El servidor DNS {dns_target} no aparece como activo.")
        success = False

    # Prueba 2: Resolución nativa del sistema con getent
    print(f"[*] 2. Validando resolución nativa con getent para {dominio_test}...")
    getent_res = run_command(f"getent ahosts {dominio_test}")
    if getent_res:
        first_line = getent_res.splitlines()[0]
        print(f"    [OK] El sistema resuelve {dominio_test}")
        print(f"    {first_line}")
    else:
        print(f"    [FAIL] getent no pudo resolver {dominio_test}")
        success = False

    # Prueba 3: Validación con nslookup usando el resolvedor del sistema
    print(f"[*] 3. Validando resolución con nslookup para {dominio_test}...")
    ns_res = run_command(f"nslookup {dominio_test}")
    if ns_res and ("Address:" in ns_res or "Addresses:" in ns_res):
        print(f"    [OK] nslookup resolvió {dominio_test}")
        print(f"    {ns_res.splitlines()[-1]}")
    else:
        print(f"    [FAIL] nslookup no pudo resolver {dominio_test}")
        success = False

    # Prueba 4: Validación directa consultando específicamente al DNS nuevo
    print(f"[*] 4. Validando consulta directa al DNS {dns_target}...")
    ns_res_direct = run_command(f"nslookup {dominio_test} {dns_target}")
    if ns_res_direct and ("Address:" in ns_res_direct or "Addresses:" in ns_res_direct):
        print(f"    [OK] El DNS {dns_target} responde por {dominio_test}")
        print(f"    {ns_res_direct.splitlines()[-1]}")
    else:
        print(f"    [FAIL] El DNS {dns_target} no resolvió {dominio_test}")
        success = False

    # Prueba 5: resolvectl query
    print(f"[*] 5. Validando con resolvectl query...")
    resolvectl_query = run_command(f"resolvectl query {dominio_test}")
    if resolvectl_query and "not found" not in resolvectl_query.lower():
        print(f"    [OK] resolvectl resolvió {dominio_test}")
        print(f"    {resolvectl_query.splitlines()[0]}")
    else:
        print(f"    [FAIL] resolvectl no pudo resolver {dominio_test}")
        success = False

    if not success:
        print("\n[!] ERROR: El host NO quedó resolviendo correctamente.")
        sys.exit(1)

    print("\n[OK] Validación completada: el host resuelve correctamente con el DNS configurado.")

if __name__ == "__main__":
    configure_network()
    verify_resolution()
    print("\n[OK] Proceso finalizado correctamente.")
    print("[!] IMPORTANTE: Ejecuta 'kubectl rollout restart deployment coredns -n kube-system' si necesitas que los Pods refresquen el cambio.")
