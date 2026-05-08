#!/usr/bin/env python3
import os
import sys
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

WG_INTERFACE = "wg0"
WG_DIR = Path("/etc/wireguard")
WG_CONF = WG_DIR / f"{WG_INTERFACE}.conf"


def run(cmd, check=True):
    print(f"\n[CMD] {' '.join(cmd)}")
    return subprocess.run(cmd, check=check)


def require_root():
    if os.geteuid() != 0:
        print("[ERROR] Ejecuta como root:")
        print("sudo python3 instalar_wireguard_cliente.py")
        sys.exit(1)


def install_packages():
    print("[INFO] Instalando paquetes necesarios...")
    run(["apt", "update"])
    run([
        "apt",
        "install",
        "-y",
        "wireguard",
        "wireguard-tools",
        "resolvconf",
        "iproute2",
        "iputils-ping"
    ])


def read_wireguard_config():
    print("\nPega aquí TODA la configuración WireGuard.")
    print("Cuando termines, escribe FIN en una línea nueva y presiona Enter.\n")
    print("Ejemplo:")
    print("[Interface]")
    print("PrivateKey = ...")
    print("...")
    print("FIN\n")

    lines = []

    while True:
        try:
            line = input()
        except EOFError:
            break

        if line.strip().upper() == "FIN":
            break

        lines.append(line)

    config = "\n".join(lines).strip() + "\n"

    if not config.strip():
        print("[ERROR] No pegaste ninguna configuración.")
        sys.exit(1)

    return config


def validate_config(config):
    required_items = [
        "[Interface]",
        "PrivateKey",
        "Address",
        "[Peer]",
        "PublicKey",
        "AllowedIPs",
        "Endpoint"
    ]

    missing = [item for item in required_items if item not in config]

    if missing:
        print("[ERROR] La configuración parece incompleta.")
        print("Faltan estos campos:")
        for item in missing:
            print(f" - {item}")
        sys.exit(1)

    if "PresharedKey" not in config:
        print("[WARN] No encontré PresharedKey. Continuaré, pero valida si tu peer la requiere.")

    print("[OK] Configuración WireGuard validada.")


def prepare_wireguard_dir():
    WG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(WG_DIR, 0o700)


def stop_existing_service():
    print("[INFO] Deteniendo servicio WireGuard si está activo...")
    run(["systemctl", "stop", f"wg-quick@{WG_INTERFACE}"], check=False)


def backup_existing_config():
    if WG_CONF.exists():
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = WG_CONF.with_suffix(f".conf.bak.{timestamp}")
        shutil.copy2(WG_CONF, backup_path)
        print(f"[INFO] Backup creado: {backup_path}")


def write_config(config):
    print(f"[INFO] Escribiendo configuración en {WG_CONF}")
    WG_CONF.write_text(config)
    os.chmod(WG_CONF, 0o600)


def start_wireguard():
    print("[INFO] Habilitando y levantando WireGuard...")
    run(["systemctl", "enable", f"wg-quick@{WG_INTERFACE}"])
    run(["systemctl", "restart", f"wg-quick@{WG_INTERFACE}"])


def show_status():
    print("\n========== ESTADO DEL SERVICIO ==========")
    run(["systemctl", "--no-pager", "--full", "status", f"wg-quick@{WG_INTERFACE}"], check=False)

    print("\n========== ESTADO WIREGUARD ==========")
    run(["wg", "show", WG_INTERFACE], check=False)

    print("\n========== INTERFAZ ==========")
    run(["ip", "addr", "show", WG_INTERFACE], check=False)

    print("\n========== RUTAS ==========")
    run(["ip", "route"], check=False)

    print("\n========== PRUEBA DE PING ==========")
    run(["ping", "-c", "3", "10.8.0.1"], check=False)


def main():
    require_root()

    print("======================================")
    print(" Instalador cliente WireGuard Ubuntu")
    print("======================================")

    install_packages()

    config = read_wireguard_config()
    validate_config(config)

    prepare_wireguard_dir()
    stop_existing_service()
    backup_existing_config()
    write_config(config)
    start_wireguard()
    show_status()

    print("\n[FINALIZADO]")
    print(f"WireGuard quedó configurado en: {WG_CONF}")
    print("Validación manual:")
    print(f"  sudo wg show {WG_INTERFACE}")
    print(f"  ip addr show {WG_INTERFACE}")
    print("  ping 10.8.0.1")


if __name__ == "__main__":
    main()
