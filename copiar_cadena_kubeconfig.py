#!/usr/bin/env python3
import subprocess
import sys
import time
from datetime import datetime

# =========================
# Nodos destino (Control Plane)
# =========================
NODES = ["10.0.0.11", "10.0.0.12", "10.0.0.13"]

# =========================
# Config
# =========================
SSH_CONNECT_TIMEOUT_SEC = 7
RETRIES = 2
RETRY_SLEEP_SEC = 2

LOG_FILE = f"copiado_kubeconfig_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

# =========================
# Colores (ANSI)
# =========================
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GRAY = "\033[90m"

def c(txt, color):
    return f"{color}{txt}{RESET}"

def ok(txt): return c(txt, GREEN)
def bad(txt): return c(txt, RED)
def info(txt): return c(txt, CYAN)
def warn(txt): return c(txt, YELLOW)

def log_to_file(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

def short_err(err: str, limit: int = 160) -> str:
    e = (err or "").strip().replace("\n", " ")
    return e[:limit] + ("..." if len(e) > limit else "")

# =========================
# SSH helpers
# =========================
def run_ssh(node: str, remote_cmd: str):
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT_SEC}",
        "-o", "StrictHostKeyChecking=accept-new",
        f"root@{node}",
        remote_cmd
    ]
    log_to_file(f"SSH {node}: {remote_cmd}")
    try:
        r = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return True, (r.stdout or "").strip(), (r.stderr or "").strip()
    except subprocess.CalledProcessError as e:
        return False, (e.stdout or "").strip(), (e.stderr or "").strip()

def retry(fn, tries, sleep_sec, label):
    last_out, last_err = "", ""
    for attempt in range(1, tries + 1):
        okk, out, err = fn()
        if okk:
            return True, out, err
        last_out, last_err = out, err
        if attempt < tries:
            print(c(f"   ↻ {label}: reintento {attempt+1}/{tries} en {sleep_sec}s", GRAY))
            time.sleep(sleep_sec)
    return False, last_out, last_err

# =========================
# MAIN
# =========================
print("\n" + c("📌 Copiado kubeconfig (LIVE lite) — Control Plane", BOLD + CYAN))
print(c(f"🧾 Log: {LOG_FILE}\n", GRAY))

# Comandos a ejecutar en cada nodo
# - crea /root/.kube si no existe
# - copia kubeconfig y genera copia cliente
REMOTE_CMD = (
    "mkdir -p /root/.kube && "
    "cp -f /etc/rancher/rke2/rke2.yaml /root/.kube/config && "
    "cp -f /root/.kube/config /root/.kube/config_cliente"
)

results = {}

print(info("🟣 Ejecutando copias en nodos...\n"))
for node in NODES:
    okk, _, err = retry(lambda n=node: run_ssh(n, REMOTE_CMD), RETRIES, RETRY_SLEEP_SEC, f"COPY {node}")
    results[node] = {"ok": okk, "err": err}
    if okk:
        print(ok(f"✅ {node}: copiado con éxito"))
    else:
        print(bad(f"❌ {node}: fallo - {short_err(err)}"))

ok_count = sum(1 for v in results.values() if v["ok"])
total = len(results)

print()
if ok_count == total:
    print(ok("✅ Copiado completado con éxito en TODOS los nodos.\n"))
    sys.exit(0)

failed = [n for n, v in results.items() if not v["ok"]]
print(warn(f"⚠️  Copiado incompleto: {ok_count}/{total} OK. Fallaron: {', '.join(failed)}\n"))
sys.exit(1)
