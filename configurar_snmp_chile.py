#!/usr/bin/env python3
"""Instala, configura y valida Net-SNMP en Ubuntu.

Debe ejecutarse como root:
    sudo python3 configurar_snmp.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


SNMP_DIR = Path("/etc/snmp")
CONFIG_FILE = SNMP_DIR / "snmpd.conf"
COMMUNITY = "ifxcliente"

AUTHORIZED_IPS = [
    "190.61.4.34",
    "190.61.4.35",
    "190.61.4.36",
    "190.61.4.170",
    "201.217.193.220",
    "200.62.3.206",
]


def run(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Ejecuta un comando y devuelve su resultado."""
    return subprocess.run(
        command,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def require_root() -> None:
    if os.geteuid() != 0:
        print("ERROR: Este programa debe ejecutarse como root.")
        print(f"Ejemplo: sudo python3 {Path(__file__).name}")
        sys.exit(1)


def ensure_packages() -> None:
    missing = []
    if shutil.which("snmpd") is None:
        missing.append("snmpd")
    if shutil.which("snmpget") is None:
        missing.append("snmp")

    if not missing:
        print("[OK] SNMP y sus herramientas ya están instalados.")
        return

    print(f"[INFO] Instalando paquetes: {', '.join(missing)}")
    run(["apt-get", "update"])
    result = run(["apt-get", "install", "-y", *missing])
    print(result.stdout.strip())
    print("[OK] Paquetes instalados correctamente.")


def build_configuration() -> str:
    source_rules = "\n".join(
        f"com2sec snmpserver {ip:<15} {COMMUNITY}" for ip in AUTHORIZED_IPS
    )

    return f"""###############################################################################
# CONFIGURACIÓN SNMP ADMINISTRADA AUTOMÁTICAMENTE
# Acceso SNMP v1/v2c de solo lectura desde orígenes autorizados.
###############################################################################

# Escuchar peticiones externas en todas las interfaces IPv4 por UDP/161.
agentAddress udp:161

###############################################################################
# CONTROL DE ACCESO
###############################################################################

# Nombre de seguridad   Origen            Comunidad
{source_rules}
com2sec local           127.0.0.1         {COMMUNITY}

# Grupo                 Modelo            Nombre de seguridad
group ifxgroupro         v1                snmpserver
group ifxgroupro         v2c               snmpserver
group ifxgrouplocal      v1                local
group ifxgrouplocal      v2c               local

# Vista permitida: todo el árbol OID en modo lectura.
view all included .1

# Grupo                  Contexto Modelo  Nivel   Prefijo Lectura Escritura Notif
access ifxgroupro         ""       any     noauth  exact   all     none      none
access ifxgrouplocal      ""       any     noauth  exact   all     none      none

###############################################################################
# INFORMACIÓN DEL SISTEMA
###############################################################################

sysDescr     IFX ORION
sysContact   UNIX SYSADMIN <sysadmin@ifxcorp.com>
sysName      IFX
sysLocation  IFX
"""


def backup_configuration() -> Path | None:
    if not CONFIG_FILE.exists():
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = CONFIG_FILE.with_name(f"snmpd.conf.backup_{timestamp}")
    shutil.copy2(CONFIG_FILE, backup)
    print(f"[OK] Respaldo creado: {backup}")
    return backup


def write_configuration(configuration: str) -> None:
    SNMP_DIR.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_FILE.with_suffix(".conf.tmp")

    try:
        temporary.write_text(configuration, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, CONFIG_FILE)
    finally:
        if temporary.exists():
            temporary.unlink()

    print(f"[OK] Configuración escrita en {CONFIG_FILE}")


def enable_and_restart(backup: Path | None) -> None:
    try:
        run(["systemctl", "enable", "snmpd"])
        run(["systemctl", "restart", "snmpd"])
        status = run(["systemctl", "is-active", "snmpd"]).stdout.strip()
        if status != "active":
            raise RuntimeError(f"Estado inesperado del servicio: {status}")
        print("[OK] Servicio snmpd habilitado y activo.")
    except (subprocess.CalledProcessError, RuntimeError) as error:
        print("[ERROR] snmpd no inició con la nueva configuración.")

        logs = run(
            ["journalctl", "-u", "snmpd", "-n", "30", "--no-pager"],
            check=False,
        ).stdout
        print(logs.strip())

        if backup and backup.exists():
            print("[INFO] Restaurando la configuración anterior...")
            shutil.copy2(backup, CONFIG_FILE)
            run(["systemctl", "restart", "snmpd"], check=False)

        raise RuntimeError("No fue posible aplicar la configuración SNMP.") from error


def validate_listener() -> bool:
    output = run(["ss", "-lunp"], check=False).stdout
    lines = [line for line in output.splitlines() if ":161" in line]

    print("\n=== PUERTO UDP/161 ===")
    if not lines:
        print("[ERROR] No se encontró un proceso escuchando en UDP/161.")
        return False

    print("\n".join(lines))

    external = any(
        address in line
        for line in lines
        for address in ("0.0.0.0:161", "*:161")
    )
    if external:
        print("[OK] SNMP escucha peticiones externas por UDP/161.")
        return True

    print("[ERROR] SNMP está escuchando, pero no en todas las interfaces IPv4.")
    return False


def validate_local_query() -> bool:
    print("\n=== CONSULTA SNMP LOCAL ===")
    result = run(
        [
            "snmpget",
            "-v2c",
            "-c",
            COMMUNITY,
            "-t",
            "2",
            "-r",
            "1",
            "127.0.0.1",
            "1.3.6.1.2.1.1.1.0",
        ],
        check=False,
    )

    if result.returncode == 0:
        print(result.stdout.strip())
        print("[OK] El agente SNMP responde consultas locales.")
        return True

    print(result.stdout.strip())
    print("[ERROR] El agente SNMP no respondió la consulta local.")
    return False


def print_final_summary(listener_ok: bool, query_ok: bool) -> None:
    print("\n" + "=" * 72)
    print("RESUMEN FINAL")
    print("=" * 72)
    print("Servicio snmpd: ACTIVO")
    print(f"Escucha UDP/161 externa: {'CORRECTA' if listener_ok else 'REVISAR'}")
    print(f"Respuesta SNMP local: {'CORRECTA' if query_ok else 'REVISAR'}")
    print(f"Modo de acceso: SOLO LECTURA, comunidad SNMP v1/v2c")
    print(f"Orígenes autorizados: {', '.join(AUTHORIZED_IPS)}")
    print("\nPara confirmar peticiones reales desde el monitoreo, ejecutar:")
    print("  sudo tcpdump -ni any -vv 'udp port 161'")
    print("\nIMPORTANTE: Verifique también que el firewall permita UDP/161")
    print("únicamente desde las IP autorizadas.")


def main() -> int:
    print("=" * 72)
    print("INSTALACIÓN Y CONFIGURACIÓN SEGURA DE SNMP")
    print("=" * 72)

    require_root()

    try:
        ensure_packages()
        backup = backup_configuration()
        write_configuration(build_configuration())
        enable_and_restart(backup)
        listener_ok = validate_listener()
        query_ok = validate_local_query()
        print_final_summary(listener_ok, query_ok)
        return 0 if listener_ok and query_ok else 2
    except subprocess.CalledProcessError as error:
        print(f"[ERROR] Falló el comando: {' '.join(error.cmd)}")
        if error.stdout:
            print(error.stdout.strip())
        return 1
    except Exception as error:
        print(f"[ERROR] {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
