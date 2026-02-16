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

SSH_USER = "root"
SSH_PASSWORD = "colombia2017"
SSH_PORT = 22
SSH_TIMEOUT_SEC = 10

REMOTE_SRC = "/etc/rancher/rke2/rke2.yaml"
REMOTE_DST_DIR = "/root/.kube"
REMOTE_DST = "/root/.kube/config"

MAX_WORKERS = 8
WRITE_REPORT_JSON = True
REPORT_JSON_PATH = "reporte_copia_kubeconfig.json"


@dataclass
class HostResult:
    node: str
    ip: str
    ok: bool
    step: str
    message: str
    duration_sec: float


def run_local(cmd: List[str]) -> Tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def pick_kubeconfig() -> str:
    """
    Selecciona un kubeconfig funcional en este orden:
    1) $KUBECONFIG (primer path si viene con :)
    2) /root/.kube/config
    3) /etc/rancher/rke2/rke2.yaml
    """
    kc_env = os.environ.get("KUBECONFIG", "").strip()
    if kc_env:
        first = kc_env.split(":")[0]
        if os.path.exists(first) and os.path.getsize(first) > 0:
            return first

    candidates = ["/root/.kube/config", "/etc/rancher/rke2/rke2.yaml"]
    for c in candidates:
        if os.path.exists(c) and os.path.getsize(c) > 0:
            return c

    raise FileNotFoundError("No encontré kubeconfig válido en $KUBECONFIG, /root/.kube/config, /etc/rancher/rke2/rke2.yaml")


def parse_control_plane_nodes(kubeconfig_path: str) -> List[Tuple[str, str]]:
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
        idx_name, idx_roles, idx_internal = 0, 2, 5

    results: List[Tuple[str, str]] = []
    for ln in lines[1:]:
        parts = ln.split()
        if len(parts) <= max(idx_internal, idx_roles, idx_name):
            continue

        name = parts[idx_name]
        roles = parts[idx_roles]
        internal_ip = parts[idx_internal]

        if "control-plane" in roles:
            results.append((name, internal_ip))

    return results


def ssh_exec(ip: str, command: str) -> Tuple[int, str, str]:
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


def copy_kubeconfig_on_host(node: str, ip: str) -> HostResult:
    start = time.time()
    try:
        rc, out, err = ssh_exec(ip, f"mkdir -p {REMOTE_DST_DIR}")
        if rc != 0:
            return HostResult(node, ip, False, "mkdir", f"Error mkdir: {err.strip() or out.strip()}", time.time() - start)

        rc, out, err = ssh_exec(ip, f"cp -f {REMOTE_SRC} {REMOTE_DST} && chmod 600 {REMOTE_DST}")
        if rc != 0:
            return HostResult(node, ip, False, "cp", f"Error cp: {err.strip() or out.strip()}", time.time() - start)

        rc, out, err = ssh_exec(ip, f"test -s {REMOTE_DST} && echo OK || echo FAIL")
        if "OK" not in out:
            return HostResult(node, ip, False, "validate", f"Validación falló: {err.strip() or out.strip()}", time.time() - start)

        rc, out, err = ssh_exec(ip, f"stat -c '%n | %s bytes | %y' {REMOTE_DST}")
        info = out.strip() if out.strip() else "Copia OK"
        return HostResult(node, ip, True, "done", info, time.time() - start)

    except Exception as e:
        return HostResult(node, ip, False, "exception", str(e), time.time() - start)


def main() -> int:
    try:
        kubeconfig_path = pick_kubeconfig()
    except Exception as e:
        print(f"❌ No pude seleccionar kubeconfig local: {e}", file=sys.stderr)
        print("➡️ Tip: en este nodo existe /etc/rancher/rke2/rke2.yaml, copia a /root/.kube/config y reintenta.", file=sys.stderr)
        return 2

    print(f"Usando kubeconfig local: {kubeconfig_path}")

    try:
        nodes = parse_control_plane_nodes(kubeconfig_path)
    except Exception as e:
        print(f"❌ No pude obtener nodos control-plane: {e}", file=sys.stderr)
        return 2

    if not nodes:
        print("⚠️ No se encontraron nodos con 'control-plane' en ROLES.")
        return 1

    print("\nNodos control-plane detectados:")
    for n, ip in nodes:
        print(f"  - {n} -> {ip}")

    print("\nIniciando copia remota del kubeconfig...\n")

    results: List[HostResult] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(nodes))) as ex:
        futs = [ex.submit(copy_kubeconfig_on_host, n, ip) for n, ip in nodes]
        for f in as_completed(futs):
            r = f.result()
            results.append(r)
            status = "✅ OK" if r.ok else "❌ FAIL"
            print(f"{status} | {r.node} ({r.ip}) | paso={r.step} | {r.message} | {r.duration_sec:.2f}s")

    ok_count = sum(1 for r in results if r.ok)
    fail_count = len(results) - ok_count

    print("\nResumen:")
    print(f"  OK:   {ok_count}")
    print(f"  FAIL: {fail_count}")

    if WRITE_REPORT_JSON:
        payload = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "kubeconfig_used": kubeconfig_path,
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
