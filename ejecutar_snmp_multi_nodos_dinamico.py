cat > /root/ejecutar_snmp_multi_nodos_dinamico.py <<'PY'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import getpass
import ipaddress
import re
import shlex
import socket
import sys
import time

try:
    import paramiko
except ImportError:
    print("[ERROR] Falta paramiko.")
    print("Instala con:")
    print("  apt-get update && apt-get install -y python3-paramiko")
    sys.exit(1)


SCRIPT_URL = "https://raw.githubusercontent.com/dlvargas123/preparar_nodos_k8s_v2/refs/heads/main/configurar_snmp.py"


def pedir_ips():
    print("Pega las IPs objetivo.")
    print("Puedes pegarlas una por línea, separadas por espacios o por comas.")
    print("Cuando termines, presiona ENTER en una línea vacía.")
    print("")

    lineas = []

    while True:
        linea = input()
        if linea.strip() == "":
            break
        lineas.append(linea)

    texto = "\n".join(lineas).strip()

    if not texto:
        print("[ERROR] No ingresaste ninguna IP.")
        sys.exit(1)

    valores = re.split(r"[\s,;]+", texto)

    ips = []
    vistas = set()
    invalidas = []

    for valor in valores:
        valor = valor.strip()

        if not valor:
            continue

        try:
            ip = str(ipaddress.ip_address(valor))
            if ip not in vistas:
                ips.append(ip)
                vistas.add(ip)
        except ValueError:
            invalidas.append(valor)

    if invalidas:
        print("[ERROR] Valores inválidos:")
        for item in invalidas:
            print(f"  - {item}")
        sys.exit(1)

    return ips


def check_ssh(ip, port=22, timeout=5):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def ejecutar_en_nodo(ip, ssh_user, ssh_password, sudo_password, timeout=900):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        print(f"[INFO] Conectando por SSH a {ssh_user}@{ip} ...")

        client.connect(
            hostname=ip,
            username=ssh_user,
            password=ssh_password,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
            look_for_keys=False,
            allow_agent=False,
        )

        print(f"[INFO] SSH conectado en {ip}")
        print(f"[INFO] Ejecutando sudo su y configurar_snmp.py en {ip}")

        comando_root = (
            "export DEBIAN_FRONTEND=noninteractive "
            "NEEDRESTART_MODE=a "
            "NEEDRESTART_SUSPEND=1 "
            "APT_LISTCHANGES_FRONTEND=none "
            "TERM=dumb; "
            f"curl -fsSL {shlex.quote(SCRIPT_URL)} | python3 -"
        )

        if ssh_user == "root":
            comando_final = f"bash -lc {shlex.quote(comando_root)}"
        else:
            comando_final = f"sudo -S -p '' su - root -c {shlex.quote(comando_root)}"

        stdin, stdout, stderr = client.exec_command(
            comando_final,
            get_pty=False,
            timeout=timeout,
        )

        if ssh_user != "root":
            stdin.write(sudo_password + "\n")
            stdin.flush()

        salida = []
        inicio = time.time()

        while not stdout.channel.exit_status_ready():
            if stdout.channel.recv_ready():
                texto = stdout.channel.recv(4096).decode("utf-8", errors="replace")
                salida.append(texto)
                print(texto, end="")

            if stdout.channel.recv_stderr_ready():
                texto = stderr.channel.recv_stderr(4096).decode("utf-8", errors="replace")
                salida.append(texto)
                print(texto, end="")

            if time.time() - inicio > timeout:
                stdout.channel.close()
                return False, 124, "Timeout ejecutando comando remoto."

            time.sleep(0.5)

        while stdout.channel.recv_ready():
            texto = stdout.channel.recv(4096).decode("utf-8", errors="replace")
            salida.append(texto)
            print(texto, end="")

        while stdout.channel.recv_stderr_ready():
            texto = stderr.channel.recv_stderr(4096).decode("utf-8", errors="replace")
            salida.append(texto)
            print(texto, end="")

        rc = stdout.channel.recv_exit_status()
        salida_texto = "".join(salida).strip()

        if rc == 0:
            return True, rc, salida_texto

        return False, rc, salida_texto

    except Exception as e:
        return False, 255, str(e)

    finally:
        client.close()


def main():
    print("=== Ejecutar configurar_snmp.py por SSH + sudo su ===")
    print("")

    ips = pedir_ips()

    print("")
    print("IPs cargadas:")
    for ip in ips:
        print(f"  - {ip}")
    print(f"Total: {len(ips)}")
    print("")

    ssh_user = input("Usuario SSH: ").strip()

    if not ssh_user:
        print("[ERROR] Usuario SSH vacío.")
        sys.exit(1)

    ssh_password = getpass.getpass("Contraseña SSH: ")

    if ssh_user == "root":
        sudo_password = ""
    else:
        mismo_pass = input("¿La contraseña de sudo su es la misma del SSH? [S/n]: ").strip().lower()

        if mismo_pass in ("", "s", "si", "sí", "y", "yes"):
            sudo_password = ssh_password
        else:
            sudo_password = getpass.getpass("Contraseña sudo su: ")

    ok_nodes = []
    fail_nodes = []

    print("")
    print("[INFO] Iniciando ejecución nodo por nodo...")
    print("")

    for ip in ips:
        print("")
        print(f"========== {ip} ==========")

        if not check_ssh(ip):
            print(f"[FAIL] {ip}: puerto 22 no responde.")
            fail_nodes.append(ip)
            continue

        ok, rc, output = ejecutar_en_nodo(
            ip=ip,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
            sudo_password=sudo_password,
        )

        if ok:
            print(f"\n[OK] {ip}: SNMP configurado correctamente.")
            ok_nodes.append(ip)
        else:
            print(f"\n[FAIL] {ip}: error rc={rc}")
            if output:
                print("--- Última salida/error ---")
                print(output[-2000:])
            fail_nodes.append(ip)

    print("")
    print("========== RESUMEN ==========")

    print(f"OK: {len(ok_nodes)}")
    for ip in ok_nodes:
        print(f"  - {ip}")

    print(f"FAIL: {len(fail_nodes)}")
    for ip in fail_nodes:
        print(f"  - {ip}")

    if fail_nodes:
        sys.exit(2)


if __name__ == "__main__":
    main()
PY

chmod +x /root/ejecutar_snmp_multi_nodos_dinamico.py
