#!/usr/bin/env python3
import os
import subprocess
import sys
import json
import time
from dataclasses import dataclass, asdict
from typing import List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import paramiko


# ===================== CONFIG =====================
SSH_USER = "root"
SSH_PASSWORD = "colombia2017"
SSH_PORT = 22
SSH_TIMEOUT_SEC = 12

REMOTE_SRC = "/etc/rancher/rke2/rke2.yaml"
REMOTE_DIR = "/root/.kube"
REMOTE_CONFIG = "/root/.kube/config"
REMOTE_CONFIG_CLIENTE = "/root/.kube/config-cliente"

MAX_WORKERS = 8
WRITE_REPORT_JSON = True
REPORT_JSON_PATH = "reporte_copia_kubeconfig.json"
PRINT_CAT_CLIENTE = True
# ==================================================


@dataclass
class HostResult:
    node: str
    ip: str
    ok: bool
    step: str
    message: str
    duration_sec: float
    printed_config: bool


def run_local(cmd: list[str]) -> tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def validate_ipv4(ip: str) -> bool:
    parts = ip.strip().split(".")
    if len(parts) != 4:
        return False
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return False
    return all(0 <= n <= 255 for n in nums)


def pick_kubeconfig_local() -> str:
    """
    Escoge un kubeconfig local para ejecutar kubectl en el nodo donde corres el script:
    1) $KUBECONFIG (primer path si viene con :)
    2) /root/.kube/config
    3) /etc/rancher/rke2/rke2.yaml
    """
    kc_env = os.environ.get("KUBECONFIG", "").strip()
    if kc_env:
        first = kc_env.split(":")[0]
        if os.path.exists(first) and os.path.getsize(first) > 0:
            return first

    for c in ["/root/.kube/config", "/etc/rancher/rke2/rke2.yaml"]:
        if os.path.exists(c) and os.path.getsize(c) > 0:
            return c

    raise FileNotFoundError("No encontré kubeconfig válido en $KUBECONFIG, /root/.kube/config o /etc/rancher/rke2/rke2.yaml")


def get_control_plane_nodes(kubeconfig_path: str) -> List[Tuple[str, str]]:
    """
    Ejecuta: kubectl --kubeconfig <kc> get nodes -o wide
    Filtra los que tengan 'control-plane' en ROLES y extrae INTERNAL-IP
    Retorna lista de (node_name, internal_ip)
    """
    rc, out, err = run_local(["kubectl", "--kubeconfig", kubeconfig_path, "get", "nodes", "-o", "wide"])
    if rc != 0:
        raise RuntimeError(f"kubectl falló (rc={rc}) usando {kubeconfig_path}: {err.strip() or out.strip()}")

    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if len(lines) < 2:
        return []

    cols = lines[0].split()
    try:
        idx_name = cols.index("NAME")
        idx_roles = cols.index("ROLES")
        idx_internal = cols.index("INTERNAL-IP")
    except ValueError:
        # fallback típico: NAME(0), ROLES(2), INTERNAL-IP(5)
        idx_name, idx_roles, idx_internal = 0, 2, 5

    res: List[Tuple[str, str]] = []
    for ln in lines[1:]:
        parts = ln.split()
        if len(parts) <= max(idx_name, idx_roles, idx_internal):
            continue

        name = parts[idx_name]
        roles = parts[idx_roles]
        internal_ip = parts[idx_internal]

        if "control-plane" in roles:
            res.append((name, internal_ip))

    return res


def ssh_exec(ip: str, command: str) -> Tuple[int, str, str]:
    """
    Ejecuta un comando remoto por SSH y retorna (exit_status, stdout, stderr).
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    client.connect(
        hostname=ip,
        port=SSH_PORT,
        username=SSH_USER,
        password=SSH_PASSWORD,
        timeout=SSH_TIMEOUT_SEC,
        auth_timeout=SSH_TIMEOUT_SEC,
        banner_timeout=SSH_TIMEOUT_SEC,
    )

    stdin, stdout, stderr = client.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    client.close()
    return exit_status, out, err


def copy_and_build_client_config(node: str, ip: str, ha_ip: str) -> HostResult:
    """
    En cada nodo:
    1) mkdir -p /root/.kube
    2) cp /etc/rancher/rke2/rke2.yaml -> /root/.kube/config
    3) cp /root/.kube/config -> /root/.kube/config-cliente
    4) reemplazar SOLO: server: https://127.0.0.1:6443 -> server: https://<ha_ip>:6443  (en config-cliente)
    5) cat /root/.kube/config-cliente e imprimir en pantalla (en el host local, por nodo)
    """
    start = time.time()
    printed = False
    try:
        # 1) mkdir
        rc, out, err = ssh_exec(ip, f"mkdir -p {REMOTE_DIR}")
        if rc != 0:
            return HostResult(node, ip, False, "mkdir", err.strip() or out.strip(), time.time() - start, printed)

        # 2) cp rke2.yaml -> config
        rc, out, err = ssh_exec(ip, f"cp -f {REMOTE_SRC} {REMOTE_CONFIG} && chmod 600 {REMOTE_CONFIG}")
        if rc != 0:
            return HostResult(node, ip, False, "cp_rke2_to_config", err.strip() or out.strip(), time.time() - start, printed)

        # 3) cp config -> config-cliente
        rc, out, err = ssh_exec(ip, f"cp -f {REMOTE_CONFIG} {REMOTE_CONFIG_CLIENTE} && chmod 600 {REMOTE_CONFIG_CLIENTE}")
        if rc != 0:
            return HostResult(node, ip, False, "cp_config_to_cliente", err.strip() or out.strip(), time.time() - start, printed)

        # 4) reemplazar server localhost SOLO si es 127.0.0.1:6443
        # Usamos sed exacto para esa línea.
        sed_cmd = (
            f"grep -q '^\\s*server:\\s*https://127\\.0\\.0\\.1:6443\\s*$' {REMOTE_CONFIG_CLIENTE} && "
            f"sed -i 's|^\\s*server:\\s*https://127\\.0\\.0\\.1:6443\\s*$|server: https://{ha_ip}:6443|' {REMOTE_CONFIG_CLIENTE} || true"
        )
        rc, out, err = ssh_exec(ip, sed_cmd)
        if rc != 0:
            return HostResult(node, ip, False, "patch_server_in_cliente", err.strip() or out.strip(), time.time() - start, printed)

        # Validación: archivo existe y no está vacío
        rc, out, err = ssh_exec(ip, f"test -s {REMOTE_CONFIG_CLIENTE} && echo OK || echo FAIL")
        if "OK" not in out:
            return HostResult(node, ip, False, "validate_non_empty", err.strip() or out.strip(), time.time() - start, printed)

        # Validación: confirmar server en config-cliente (imprime la línea server)
        rc, out, err = ssh_exec(ip, f"grep -n '^\\s*server:' {REMOTE_CONFIG_CLIENTE} || true")
        server_line = out.strip() if out.strip() else "No encontré línea server:"

        # 5) cat e imprimir
        if PRINT_CAT_CLIENTE:
            rc2, cat_out, cat_err = ssh_exec(ip, f"cat {REMOTE_CONFIG_CLIENTE}")
            if rc2 != 0:
                return HostResult(node, ip, False, "cat_cliente", cat_err.strip() or cat_out.strip(), time.time() - start, printed)

            print("\n" + "=" * 90)
            print(f"NODE: {node} | IP: {ip} | /root/.kube/config-cliente (server esperado: https://{ha_ip}:6443)")
            print("-" * 90)
            print(cat_out.rstrip())
            print("=" * 90 + "\n")
            printed = True

        msg = f"Copia OK. {server_line}"
        return HostResult(node, ip, True, "done", msg, time.time() - start, printed)

    except Exception as e:
        return HostResult(node, ip, False, "exception", str(e), time.time() - start, printed)


def main() -> int:
    # 1) pregunta interactiva
    ha_ip = input("👉 Ingresa la IP del HA / Balanceador de la API del cluster (ej: 10.0.0.56): ").strip()
    if not validate_ipv4(ha_ip):
        print(f"❌ IP inválida: '{ha_ip}'", file=sys.stderr)
        return 2

    # 2) obtener nodos control-plane desde kubectl usando kubeconfig local válido
    try:
        kubeconfig_local = pick_kubeconfig_local()
    except Exception as e:
        print(f"❌ No pude seleccionar kubeconfig local: {e}", file=sys.stderr)
        print("➡️ Tip: en un nodo RKE2 normalmente existe /etc/rancher/rke2/rke2.yaml", file=sys.stderr)
        return 2

    print(f"\n✅ Usando kubeconfig local para consultar nodos: {kubeconfig_local}")

    try:
        nodes = get_control_plane_nodes(kubeconfig_local)
    except Exception as e:
        print(f"❌ No pude obtener nodos control-plane: {e}", file=sys.stderr)
        return 2

    if not nodes:
        print("⚠️ No se encontraron nodos con 'control-plane' en ROLES.")
        return 1

    print("\nNodos control-plane detectados:")
    for n, ip in nodes:
        print(f"  - {n} -> {ip}")

    # 3) ejecutar en paralelo por SSH
    print("\nIniciando acciones por SSH en cada control-plane...\n")
    results: List[HostResult] = []

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(nodes))) as ex:
        futs = [ex.submit(copy_and_build_client_config, n, ip, ha_ip) for n, ip in nodes]
        for f in as_completed(futs):
            r = f.result()
            results.append(r)
            status = "✅ OK" if r.ok else "❌ FAIL"
            print(f"{status} | {r.node} ({r.ip}) | paso={r.step} | {r.message} | {r.duration_sec:.2f}s")

    ok_count = sum(1 for r in results if r.ok)
    fail_count = len(results) - ok_count

    print("\nResumen final:")
    print(f"  OK:   {ok_count}")
    print(f"  FAIL: {fail_count}")

    if WRITE_REPORT_JSON:
        payload = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ha_ip": ha_ip,
            "kubeconfig_used_to_list_nodes": kubeconfig_local,
            "total": len(results),
            "ok": ok_count,
            "fail": fail_count,
            "results": [asdict(r) for r in sorted(results, key=lambda x: (not x.ok, x.node))],
        }
        with open(REPORT_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"\n📄 Reporte JSON generado: {REPORT_JSON_PATH}")

    return 0 if fail_count == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
