#!/usr/bin/env python3
"""
Administrador interactivo de apuntamientos estáticos en CoreDNS para RKE2.

Características:
- Verifica acceso al clúster con kubectl.
- Lee el Corefile actual del ConfigMap.
- Crea respaldo local antes de modificar.
- Permite listar, agregar/actualizar y eliminar registros del bloque hosts.
- Conserva el resto del Corefile.
- Reinicia el Deployment de CoreDNS y valida el rollout.

No requiere librerías externas.
"""

import datetime
import ipaddress
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

NAMESPACE = "kube-system"
CONFIGMAP = "rke2-coredns-rke2-coredns"
DEPLOYMENT = "rke2-coredns-rke2-coredns"


def run_command(args, input_text=None, check=True):
    try:
        result = subprocess.run(
            args,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        print(f"\nERROR: No se encontró el comando: {args[0]}")
        sys.exit(1)

    if check and result.returncode != 0:
        print("\nERROR ejecutando:")
        print(" ".join(args))
        if result.stderr.strip():
            print(result.stderr.strip())
        sys.exit(result.returncode)

    return result


def check_requirements():
    if shutil.which("kubectl") is None:
        print("ERROR: kubectl no está instalado o no está disponible en PATH.")
        sys.exit(1)

    result = run_command(
        ["kubectl", "auth", "can-i", "get", "configmap", "-n", NAMESPACE],
        check=False,
    )
    if result.returncode != 0 or result.stdout.strip().lower() != "yes":
        print(
            f"ERROR: El usuario actual no puede consultar ConfigMaps "
            f"en el namespace {NAMESPACE}."
        )
        sys.exit(1)

    result = run_command(
        ["kubectl", "get", "configmap", CONFIGMAP, "-n", NAMESPACE],
        check=False,
    )
    if result.returncode != 0:
        print(
            f"ERROR: No se encontró el ConfigMap {CONFIGMAP} "
            f"en el namespace {NAMESPACE}."
        )
        if result.stderr.strip():
            print(result.stderr.strip())
        sys.exit(1)


def get_corefile():
    result = run_command(
        [
            "kubectl",
            "get",
            "configmap",
            CONFIGMAP,
            "-n",
            NAMESPACE,
            "-o",
            "jsonpath={.data.Corefile}",
        ]
    )
    corefile = result.stdout
    if not corefile.strip():
        print("ERROR: El ConfigMap no contiene data.Corefile.")
        sys.exit(1)
    return corefile


def backup_configmap():
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = Path.cwd() / f"coredns-backup-{timestamp}.yaml"

    result = run_command(
        [
            "kubectl",
            "get",
            "configmap",
            CONFIGMAP,
            "-n",
            NAMESPACE,
            "-o",
            "yaml",
        ]
    )
    backup_path.write_text(result.stdout, encoding="utf-8")
    return backup_path


def locate_hosts_block(corefile):
    """
    Busca el primer bloque 'hosts { ... }' y retorna:
    (inicio, fin, contenido_interno, indentacion)

    El parser cuenta llaves para evitar cortar incorrectamente el bloque.
    """
    match = re.search(r"(?m)^([ \t]*)hosts(?:\s+[^\{\n]+)?\s*\{", corefile)
    if not match:
        return None

    start = match.start()
    open_brace = corefile.find("{", match.start(), match.end())
    depth = 0

    for index in range(open_brace, len(corefile)):
        char = corefile[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                inner = corefile[open_brace + 1:index]
                return start, end, inner, match.group(1)

    raise ValueError("El bloque hosts existe, pero sus llaves no están balanceadas.")


def parse_hosts_entries(inner):
    entries = {}
    comments = []
    has_fallthrough = False

    for raw_line in inner.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        if line.startswith("#"):
            comments.append(line)
            continue

        if line == "fallthrough" or line.startswith("fallthrough "):
            has_fallthrough = True
            continue

        parts = line.split()
        if len(parts) >= 2:
            try:
                ipaddress.ip_address(parts[0])
            except ValueError:
                comments.append(f"# Entrada no administrada: {line}")
                continue

            ip = parts[0]
            for hostname in parts[1:]:
                entries[hostname.lower()] = (ip, hostname)

    return entries, comments, has_fallthrough


def get_entries(corefile):
    block = locate_hosts_block(corefile)
    if not block:
        return {}, [], True

    _, _, inner, _ = block
    return parse_hosts_entries(inner)


def validate_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def validate_hostname(value):
    if len(value) > 253:
        return False

    hostname = value.rstrip(".")
    labels = hostname.split(".")
    label_pattern = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

    return bool(labels) and all(label_pattern.match(label) for label in labels)


def build_hosts_block(entries, comments=None, indent="    "):
    comments = comments or []
    child_indent = indent + "    "
    lines = [f"{indent}hosts {{"]

    for comment in comments:
        lines.append(f"{child_indent}{comment}")

    for _, (ip, hostname) in sorted(entries.items(), key=lambda item: item[0]):
        lines.append(f"{child_indent}{ip} {hostname}")

    lines.append(f"{child_indent}fallthrough")
    lines.append(f"{indent}}}")
    return "\n".join(lines)


def update_corefile(corefile, entries, comments=None):
    block = locate_hosts_block(corefile)

    if block:
        start, end, _, indent = block
        new_block = build_hosts_block(entries, comments, indent)
        return corefile[:start] + new_block + corefile[end:]

    # Inserta hosts antes de prometheus, forward, cache, loop, reload o loadbalance.
    insertion_match = re.search(
        r"(?m)^([ \t]*)(prometheus|forward|cache|loop|reload|loadbalance)\b",
        corefile,
    )

    if insertion_match:
        indent = insertion_match.group(1)
        position = insertion_match.start()
        new_block = build_hosts_block(entries, comments, indent) + "\n"
        return corefile[:position] + new_block + corefile[position:]

    # Como último recurso, inserta antes de la última llave del bloque principal.
    last_brace = corefile.rfind("}")
    if last_brace == -1:
        raise ValueError("No fue posible identificar el bloque principal del Corefile.")

    new_block = build_hosts_block(entries, comments, "    ") + "\n"
    return corefile[:last_brace] + new_block + corefile[last_brace:]


def apply_corefile(corefile):
    patch = json.dumps(
        {"data": {"Corefile": corefile}},
        ensure_ascii=False,
    )

    result = run_command(
        [
            "kubectl",
            "patch",
            "configmap",
            CONFIGMAP,
            "-n",
            NAMESPACE,
            "--type",
            "merge",
            "-p",
            patch,
        ],
        check=False,
    )

    if result.returncode != 0:
        print("ERROR: No fue posible aplicar el parche.")
        if result.stderr.strip():
            print(result.stderr.strip())
        return False

    print(result.stdout.strip())
    return True


def restart_coredns():
    print("\nReiniciando CoreDNS...")
    result = run_command(
        [
            "kubectl",
            "rollout",
            "restart",
            "deployment",
            DEPLOYMENT,
            "-n",
            NAMESPACE,
        ],
        check=False,
    )
    if result.returncode != 0:
        print("ADVERTENCIA: El parche se aplicó, pero falló el reinicio.")
        if result.stderr.strip():
            print(result.stderr.strip())
        return False

    print(result.stdout.strip())

    result = run_command(
        [
            "kubectl",
            "rollout",
            "status",
            "deployment",
            DEPLOYMENT,
            "-n",
            NAMESPACE,
            "--timeout=120s",
        ],
        check=False,
    )

    if result.returncode != 0:
        print("ADVERTENCIA: El rollout no terminó correctamente.")
        if result.stderr.strip():
            print(result.stderr.strip())
        return False

    print(result.stdout.strip())
    return True


def show_entries(entries):
    print("\n===== APUNTAMIENTOS ADMINISTRADOS EN COREDNS =====")
    if not entries:
        print("No hay registros dentro del bloque hosts.")
        return

    max_ip = max(len(value[0]) for value in entries.values())
    print(f"{'IP'.ljust(max_ip)}  HOSTNAME")
    print(f"{'-' * max_ip}  {'-' * 45}")

    for _, (ip, hostname) in sorted(entries.items(), key=lambda item: item[0]):
        print(f"{ip.ljust(max_ip)}  {hostname}")


def confirm(message):
    answer = input(f"{message} [s/N]: ").strip().lower()
    return answer in {"s", "si", "sí", "y", "yes"}


def add_or_update():
    corefile = get_corefile()
    entries, comments, _ = get_entries(corefile)

    print("\n===== AGREGAR O ACTUALIZAR =====")
    ip = input("Dirección IP: ").strip()
    if not validate_ip(ip):
        print("ERROR: La dirección IP no es válida.")
        return

    hostname = input("Nombre DNS/FQDN: ").strip().rstrip(".")
    if not validate_hostname(hostname):
        print("ERROR: El nombre DNS no es válido.")
        return

    key = hostname.lower()
    previous = entries.get(key)

    if previous:
        print(f"\nEl registro ya existe: {previous[0]} {previous[1]}")
        print(f"Nuevo valor:           {ip} {hostname}")
    else:
        print(f"\nSe agregará: {ip} {hostname}")

    if not confirm("¿Aplicar el cambio?"):
        print("Operación cancelada.")
        return

    entries[key] = (ip, hostname)
    new_corefile = update_corefile(corefile, entries, comments)
    backup = backup_configmap()
    print(f"Respaldo creado: {backup}")

    if apply_corefile(new_corefile):
        restart_coredns()
        print("\nValidación sugerida:")
        print(
            f"kubectl run dns-test --image=busybox:1.36 --restart=Never "
            f"--rm -it -- nslookup {hostname}"
        )


def remove_entry():
    corefile = get_corefile()
    entries, comments, _ = get_entries(corefile)
    show_entries(entries)

    if not entries:
        return

    hostname = input("\nHostname que deseas eliminar: ").strip().rstrip(".")
    key = hostname.lower()

    if key not in entries:
        print("ERROR: El hostname no existe dentro del bloque hosts.")
        return

    ip, original_hostname = entries[key]
    print(f"Se eliminará: {ip} {original_hostname}")

    if not confirm("¿Aplicar la eliminación?"):
        print("Operación cancelada.")
        return

    del entries[key]
    new_corefile = update_corefile(corefile, entries, comments)
    backup = backup_configmap()
    print(f"Respaldo creado: {backup}")

    if apply_corefile(new_corefile):
        restart_coredns()


def show_corefile():
    print("\n===== COREFILE ACTUAL =====")
    print(get_corefile())


def main():
    print("======================================================")
    print(" Administrador interactivo de CoreDNS para RKE2")
    print("======================================================")
    print(f"Namespace : {NAMESPACE}")
    print(f"ConfigMap : {CONFIGMAP}")
    print(f"Deployment: {DEPLOYMENT}")

    check_requirements()

    while True:
        print("\nSelecciona una opción:")
        print("  1. Listar apuntamientos")
        print("  2. Agregar o actualizar apuntamiento")
        print("  3. Eliminar apuntamiento")
        print("  4. Mostrar Corefile completo")
        print("  5. Salir")

        option = input("\nOpción: ").strip()

        try:
            if option == "1":
                corefile = get_corefile()
                entries, _, _ = get_entries(corefile)
                show_entries(entries)
            elif option == "2":
                add_or_update()
            elif option == "3":
                remove_entry()
            elif option == "4":
                show_corefile()
            elif option == "5":
                print("Saliendo.")
                break
            else:
                print("Opción no válida.")
        except KeyboardInterrupt:
            print("\nOperación cancelada por el usuario.")
        except ValueError as exc:
            print(f"ERROR: {exc}")


if __name__ == "__main__":
    main()
