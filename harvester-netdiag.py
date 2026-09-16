#!/usr/bin/env python3

import json
import os
import re
import shlex
import signal
import subprocess
import sys
from typing import Any


# Imagen usada por kubectl debug.
# El acceso al host se realiza con: chroot /host
DEBUG_IMAGE = os.environ.get("DEBUG_IMAGE", "ubuntu:24.04")

# Perfil recomendado para acceder al namespace del host.
DEBUG_PROFILE = os.environ.get("DEBUG_PROFILE", "sysadmin")


def run_command(
    command: list[str],
    capture_output: bool = False,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """
    Ejecuta un comando externo mostrando errores de forma controlada.
    """
    try:
        return subprocess.run(
            command,
            text=True,
            capture_output=capture_output,
            check=check,
        )
    except FileNotFoundError:
        print(f"\nERROR: no se encontró el comando requerido: {command[0]}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nOperación interrumpida por el usuario.")
        return subprocess.CompletedProcess(command, 130)


def check_kubectl() -> None:
    """
    Verifica que kubectl exista y que el contexto actual funcione.
    """
    result = run_command(
        ["kubectl", "version", "--client"],
        capture_output=True,
    )

    if result.returncode != 0:
        print("ERROR: kubectl no está disponible o no responde.")
        print(result.stderr.strip())
        sys.exit(1)

    context = run_command(
        ["kubectl", "config", "current-context"],
        capture_output=True,
    )

    if context.returncode == 0:
        print(f"Contexto kubectl: {context.stdout.strip()}")
    else:
        print("ADVERTENCIA: no se pudo determinar el contexto actual.")


def get_nodes() -> list[dict[str, Any]]:
    """
    Obtiene los nodos del clúster en formato JSON.
    """
    result = run_command(
        ["kubectl", "get", "nodes", "-o", "json"],
        capture_output=True,
    )

    if result.returncode != 0:
        print("ERROR al obtener los nodos del clúster:")
        print(result.stderr.strip())
        return []

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        print("ERROR: kubectl devolvió una respuesta JSON no válida.")
        return []

    nodes = []

    for item in data.get("items", []):
        metadata = item.get("metadata", {})
        status = item.get("status", {})
        spec = item.get("spec", {})

        name = metadata.get("name", "desconocido")
        addresses = {
            address.get("type"): address.get("address")
            for address in status.get("addresses", [])
        }

        conditions = status.get("conditions", [])
        ready_status = "Unknown"

        for condition in conditions:
            if condition.get("type") == "Ready":
                ready_status = condition.get("status", "Unknown")
                break

        roles = []
        labels = metadata.get("labels", {})

        for label_key in labels:
            if label_key.startswith("node-role.kubernetes.io/"):
                role = label_key.split("/", 1)[1]
                roles.append(role)

        nodes.append(
            {
                "name": name,
                "ready": ready_status,
                "internal_ip": addresses.get("InternalIP", "-"),
                "roles": ",".join(roles) if roles else "-",
                "version": status.get("nodeInfo", {}).get(
                    "kubeletVersion", "-"
                ),
                "unschedulable": spec.get("unschedulable", False),
            }
        )

    return nodes


def print_nodes(nodes: list[dict[str, Any]]) -> None:
    """
    Muestra los nodos en formato tabular.
    """
    print()
    print("=" * 110)
    print("NODOS DEL CLUSTER HARVESTER")
    print("=" * 110)

    header = (
        f"{'ID':<4} "
        f"{'NOMBRE':<20} "
        f"{'READY':<10} "
        f"{'SCHEDULING':<12} "
        f"{'IP INTERNA':<16} "
        f"{'ROLES':<25} "
        f"{'VERSION'}"
    )

    print(header)
    print("-" * 110)

    for index, node in enumerate(nodes, start=1):
        ready = node["ready"]
        scheduling = "Disabled" if node["unschedulable"] else "Enabled"

        print(
            f"{index:<4} "
            f"{node['name']:<20} "
            f"{ready:<10} "
            f"{scheduling:<12} "
            f"{node['internal_ip']:<16} "
            f"{node['roles']:<25} "
            f"{node['version']}"
        )

    print("=" * 110)


def select_node(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Permite seleccionar un nodo por número.
    """
    if not nodes:
        print("No hay nodos disponibles.")
        return None

    print_nodes(nodes)

    while True:
        value = input(
            "\nSelecciona el número del nodo "
            "o escribe 'q' para volver: "
        ).strip()

        if value.lower() in {"q", "quit", "exit"}:
            return None

        if not value.isdigit():
            print("Introduce un número válido.")
            continue

        index = int(value) - 1

        if index < 0 or index >= len(nodes):
            print("Número fuera de rango.")
            continue

        selected = nodes[index]

        if selected["ready"] != "True":
            print(
                f"\nADVERTENCIA: el nodo {selected['name']} está "
                f"en estado {selected['ready']}."
            )

            confirmation = input(
                "¿Quieres continuar de todas formas? [s/N]: "
            ).strip().lower()

            if confirmation not in {"s", "si", "sí", "y", "yes"}:
                continue

        return selected


def validate_target(target: str) -> bool:
    """
    Validación básica para evitar caracteres de shell no deseados.
    Permite IPs, hostnames y FQDN.
    """
    if not target:
        return False

    if len(target) > 253:
        return False

    pattern = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    return bool(re.fullmatch(pattern, target))


def run_debug_command(node: str, host_command: str) -> int:
    """
    Ejecuta un comando dentro del namespace del host seleccionado.
    """
    command = [
        "kubectl",
        "debug",
        f"node/{node}",
        "--profile=" + DEBUG_PROFILE,
        "--image=" + DEBUG_IMAGE,
        "--attach=true",
        "-i",
        "-t",
        "--",
        "chroot",
        "/host",
        "/bin/bash",
        "-c",
        host_command,
    ]

    print()
    print(f"Ejecutando en el host {node}:")
    print(f"  {host_command}")
    print()
    print("Para detener una prueba continua utiliza Ctrl+C.")
    print()

    try:
        result = subprocess.run(command)
        return result.returncode
    except KeyboardInterrupt:
        print("\nPrueba interrumpida.")
        return 130


def open_node_shell(node: str) -> None:
    """
    Abre una shell en el sistema operativo del nodo.
    """
    command = [
        "kubectl",
        "debug",
        f"node/{node}",
        "--profile=" + DEBUG_PROFILE,
        "--image=" + DEBUG_IMAGE,
        "--attach=true",
        "-i",
        "-t",
        "--",
        "chroot",
        "/host",
        "/bin/bash",
    ]

    print()
    print(f"Abriendo shell en {node}.")
    print("Para salir ejecuta: exit")
    print()

    try:
        subprocess.run(command)
    except KeyboardInterrupt:
        print("\nSesión interrumpida.")


def run_ping(node: str) -> None:
    """
    Ejecuta un ping continuo desde el host del nodo seleccionado.
    """
    target = input(
        "Introduce la IP o hostname destino del ping: "
    ).strip()

    if not validate_target(target):
        print("Destino no válido.")
        return

    interval = input(
        "Intervalo entre paquetes en segundos [1]: "
    ).strip()

    if not interval:
        interval = "1"

    if not re.fullmatch(r"[0-9]+([.][0-9]+)?", interval):
        print("Intervalo no válido.")
        return

    target_quoted = shlex.quote(target)
    interval_quoted = shlex.quote(interval)

    # -D agrega timestamp en Linux.
    host_command = (
        f"echo 'Ping desde $(hostname) hacia {target}'; "
        f"exec ping -D -i {interval_quoted} {target_quoted}"
    )

    run_debug_command(node, host_command)


def run_trace(node: str) -> None:
    """
    Ejecuta traceroute o tracepath desde el host del nodo seleccionado.
    """
    target = input(
        "Introduce la IP o hostname destino de la traza: "
    ).strip()

    if not validate_target(target):
        print("Destino no válido.")
        return

    target_quoted = shlex.quote(target)

    host_command = f"""
echo "Traza desde $(hostname) hacia {target}";
if command -v traceroute >/dev/null 2>&1; then
    exec traceroute -n {target_quoted};
elif command -v tracepath >/dev/null 2>&1; then
    exec tracepath -n {target_quoted};
elif command -v busybox >/dev/null 2>&1 && busybox traceroute --help >/dev/null 2>&1; then
    exec busybox traceroute -n {target_quoted};
else
    echo "No se encontró traceroute, tracepath ni busybox traceroute en el host.";
    echo "Comandos disponibles relacionados:";
    command -v traceroute || true;
    command -v tracepath || true;
    command -v busybox || true;
    exit 127;
fi
""".strip()

    run_debug_command(node, host_command)


def node_menu(node: dict[str, Any]) -> None:
    """
    Menú de operaciones sobre el nodo seleccionado.
    """
    node_name = node["name"]

    while True:
        print()
        print("=" * 70)
        print(f"NODO SELECCIONADO: {node_name}")
        print(f"IP interna: {node['internal_ip']}")
        print("=" * 70)
        print("1) Abrir shell en el nodo")
        print("2) Ping continuo desde el nodo")
        print("3) Traceroute/tracepath desde el nodo")
        print("4) Mostrar información actual del nodo")
        print("0) Volver a seleccionar nodo")
        print("=" * 70)

        option = input("Selecciona una opción: ").strip()

        if option == "1":
            open_node_shell(node_name)

        elif option == "2":
            run_ping(node_name)

        elif option == "3":
            run_trace(node_name)

        elif option == "4":
            result = run_command(
                [
                    "kubectl",
                    "get",
                    "node",
                    node_name,
                    "-o",
                    "wide",
                ]
            )
            if result.returncode != 0:
                print("No se pudo consultar el nodo.")

        elif option == "0":
            return

        else:
            print("Opción no válida.")


def main() -> None:
    print("Herramienta interactiva de diagnóstico de red para Harvester")
    print(f"Imagen debug: {DEBUG_IMAGE}")
    print(f"Perfil debug: {DEBUG_PROFILE}")

    check_kubectl()

    while True:
        nodes = get_nodes()

        if not nodes:
            input(
                "\nNo se pudieron obtener nodos. "
                "Pulsa Enter para reintentar..."
            )
            continue

        print()
        print("=" * 70)
        print("MENÚ PRINCIPAL")
        print("=" * 70)
        print("1) Listar nodos")
        print("2) Seleccionar nodo y conectar")
        print("0) Salir")
        print("=" * 70)

        option = input("Selecciona una opción: ").strip()

        if option == "1":
            print_nodes(nodes)
            input("\nPulsa Enter para continuar...")

        elif option == "2":
            selected = select_node(nodes)
            if selected:
                node_menu(selected)

        elif option == "0":
            print("Saliendo.")
            break

        else:
            print("Opción no válida.")


if __name__ == "__main__":
    main()
