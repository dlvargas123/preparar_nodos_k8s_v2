#!/usr/bin/env python3
import os
import subprocess
import sys
import json
import time
import re
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

# Si quieres validar conectividad de kubeconfig-cliente contra el API HA, ponlo en True.
# OJO: requiere que el nodo tenga red hacia el HA:6443 y que kubectl exista.
VALIDATE_WITH_KUBECTL_VERSION = False
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

    raise FileNotFoundError(
        "No encontré kubeconfig válido en $KUBECONFIG, /root/.kube/config o /etc/rancher/rke2/rke2.yaml"
    )


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


def patch_server_preserve_indent_cmd(file_path: str, ha_ip: str) -> str:
    """
    Usa python3 remoto para reemplazar la línea server: https://<algo>:6443
    preservando la indentación existente, sin dañar YAML.

    Regex: ^(\s*)server:\s*https://[^:]+:6443\s*$
    Replace: \1server: https://<ha_ip>:6443
    """
    py = r"""
import re, sys
path = sys.argv[1]
ha = sys.argv[2]
with open(path, "r", encoding="utf-8", errors="replace") as f:
    s = f.read()

pattern = re.compile(r"^(\s*)server:\s*https://[^:\s]+:6443\s*$", re.MULTILINE)
new_s, n = pattern.subn(r"\1server: https://%s:6443" % ha, s)

# Si no matchea por hostname/IP con extras, intentamos algo más flexible:
if n == 0:
    pattern2 = re.compile(r"^(\s*)server:\s*https://.+:6443\s*$", re.MULTILINE)
    new_s, n = pattern2.subn(r"\1server: https://%s:6443" % ha, s)

if n == 0:
    print("NO_MATCH")
    sys.exit(3)

with open(path, "w", encoding="utf-8") as f:
    f.write(new_s)

print("PATCHED", n)
"""
    # lo enviamos como python3 - <<'PY' ... PY  file ha
    # Usamos EOF quoting seguro.
    return f"python3 - '{file_path}' '{ha_ip}' <<'PY'\n{py}\nPY"


def copy_and_build_client_config(node: str, ip: str, ha_ip: str) -> HostResult:
    """
    En cada nodo:
    1) mkdir -p /root/.kube
    2) cp /etc/rancher/rke2/rke2.yaml -> /root/.kube/config
    3) cp /root/.kube/config -> /root/.kube/config-cliente
    4) reemplaza server en config-cliente preservando indentación
    5) cat config-cliente e imprimir
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

        # 4) patch server manteniendo indentación
        cmd_patch = patch_server_preserve_indent_cmd(REMOTE_CONFIG_CLIENTE, ha_ip)
        rc, out, err = ssh_exec(ip, cmd_patch)
        if rc != 0:
            msg = (err.strip() or out.strip() or "Error parchando server")
            return HostResult(node, ip, False, "patch_server_in_cliente", msg, time.time() - start, printed)

        if "NO_MATCH" in out:
            return HostResult(
                node, ip, False, "patch_server_in_cliente",
                "No encontré línea server: ...:6443 para reemplazar en config-cliente",
                time.time() - start, printed
            )

        # Validación rápida: archivo no vacío
        rc, out2, err2 = ssh_exec(ip, f"test -s {REMOTE_CONFIG_CLIENTE} && echo OK || echo FAIL")
        if "OK" not in out2:
            return HostResult(node, ip, False, "validate_non_empty", err2.strip() or out2.strip(), time.time() - start, printed)

        # Mostrar línea server (para confirmar)
        rc, srv_out, srv_err = ssh_exec(ip, f"grep -n '^\\s*server:' {REMOTE_CONFIG_CLIENTE} || true")
        server_line = srv_out.strip() if srv_out.strip() else "No encontré línea server:"

        # (Opcional) validar que kubectl parsea ese kubeconfig y puede leer version (si hay red y kubectl)
        if VALIDATE_WITH_KUBECTL_VERSION:
            rc, vout, verr = ssh_exec(ip, f"kubectl --kubeconfig {REMOTE_CONFIG_CLIENTE} version --short 2>&1 || true")
            # no lo hacemos fatal si no hay conectividad; solo lo reportamos
            server_line += f" | kubectl version output: {vout.strip()[:160]}"

        # 5) cat e imprimir
        if PRINT_CAT_CLIENTE:
            rc3, cat_out, cat_err = ssh_exec(ip, f"cat {REMOTE_CONFIG_CLIENTE}")
            if rc3 != 0:
                return HostResult(node, ip, False, "cat_cliente", cat_err.strip() or cat_out.strip(), time.time() - start, printed)

            print("\n" + "=" * 90)
            print(f"NODE: {node} | IP: {ip} | /root/.kube/config-cliente (server => https://{ha_ip}:6443)")
            print("-" * 90)
            print(cat_out.rstrip())
            print("=" * 90 + "\n")
            printed = True

        msg = f"Copia OK. {server_line}"
        return HostResult(node, ip, True, "done", msg, time.time() - start, printed)

    except Exception as e:
        return HostResult(node, ip, False, "exception", str(e), time.time() - start, printed)


def main() -> int:
    ha_ip = input("👉 Ingresa la IP del HA / Balanceador de la API del cluster (ej: 10.0.0.56): ").strip()
    if not validate_ipv4(ha_ip):
        print(f"❌ IP inválida: '{ha_ip}'", file=sys.stderr)
        return 2

    # kubeconfig local para listar nodos
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
