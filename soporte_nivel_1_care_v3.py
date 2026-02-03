#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Dict, List, Tuple, Optional

# =========================
# Alcance (tu oferta)
# =========================
INFRA_NAMESPACES = {"kube-system"}  # Infra base soportada por tu servicio

# =========================
# Colores ANSI (para cat y consola)
# =========================
RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"

RED   = "\033[31m"
GREEN = "\033[32m"
YELLOW= "\033[33m"
CYAN  = "\033[36m"
WHITE = "\033[37m"

def c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"

def ok(text: str) -> str:
    return c(text, GREEN)

def warn(text: str) -> str:
    return c(text, YELLOW)

def bad(text: str) -> str:
    return c(text, RED)

def info(text: str) -> str:
    return c(text, CYAN)

def faint(text: str) -> str:
    return f"{DIM}{text}{RESET}"

# =========================
# Helpers
# =========================
def now_ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def summarize(text: str, max_chars: int = 180) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    return t if len(t) <= max_chars else (t[: max_chars - 3] + "...")

def run_cmd(cmd: str, timeout: int = 60) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(
            shlex.split(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired as e:
        return 124, "", f"TIMEOUT after {timeout}s: {e}"
    except Exception as e:
        return 1, "", f"ERROR executing command: {e}"

def run_cmd_shell(cmd: str, timeout: int = 30) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return 1, "", f"ERROR executing shell command: {e}"

def write_text(path: str, content: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write((content or "").rstrip() + "\n")
    return path

def write_evidence(folder: str, filename: str, content: str) -> str:
    path = os.path.join(folder, filename)
    return write_text(path, content)

def resolve_kubectl() -> Optional[str]:
    env_path = os.environ.get("KUBECTL")
    if env_path and os.path.isfile(env_path) and os.access(env_path, os.X_OK):
        return env_path
    p = shutil.which("kubectl")
    if p:
        return p
    rc, out, _ = run_cmd_shell("command -v kubectl")
    if rc == 0 and out:
        return out.strip()
    return None

def kubectl_available(kubectl: str) -> Tuple[bool, str]:
    cmd = f"{kubectl} version --client"
    rc, out, err = run_cmd(cmd, timeout=20)
    if rc == 0:
        return True, out
    return False, err or out or "kubectl version --client failed"

def get_context(kubectl: str) -> str:
    rc, out, err = run_cmd(f"{kubectl} config current-context", timeout=15)
    return out if rc == 0 else f"(no context) {err}"

def row(cat, step, verif, cmd, expected, actual, status, evidence, scope="INFRA") -> Dict:
    return {
        "categoria": cat,
        "paso": step,
        "verificacion": verif,
        "comando": cmd,
        "salida_esperada": expected,
        "salida_resumen": actual,
        "estado": status,     # OK / FALLA / N/A
        "evidencia": evidence,
        "scope": scope,       # INFRA / N_A
    }

# =========================
# Sugerencias (solo INFRA)
# =========================
SUGGESTIONS = {
    "2.1": "Si hay nodos NotReady: revisar kubelet, recursos y red. Ver 'kubectl describe node <nodo>' y eventos en kube-system.",
    "2.2": "Si faltan pods del control plane o no están Running: revisar servicio rke2-server en control-plane y logs (journalctl -u rke2-server).",
    "2.3": "Si etcd no está OK: posible quorum/IO/red. Evitar reinicios masivos. Escalar a N3 si persiste.",
    "3.2": "Si Pressure=True: liberar disco/memoria, limpiar imágenes, revisar pods pesados. Priorizar estabilidad del nodo.",
    "4.2": "Si falla DNS interno: validar CoreDNS, svc/endpoints DNS y CNI (cilium).",
    "4.3": "Si svc DNS (53) sin endpoints: CoreDNS caído o sin endpoints. Revisar pods CoreDNS.",
    "5.1": "Warnings en kube-system pueden indicar degradación. Identificar objeto afectado y revisar logs.",
    "6.1": "Si versiones inconsistentes: upgrade parcial. Detener cambios y alinear por procedimiento.",
}

def suggestion_for_step(step: str) -> str:
    return SUGGESTIONS.get(step, "Revisar evidencia y escalar según severidad.")

# =========================
# Estado final (solo INFRA cuenta)
# =========================
def final_state(rows: List[Dict]) -> str:
    infra_fails = [r for r in rows if r["scope"] == "INFRA" and r["estado"] != "OK"]
    critical_steps = {"2.1", "2.2", "2.3"}  # si fallan: no disponible

    if any(r["paso"] in critical_steps for r in infra_fails):
        return "PLATAFORMA NO DISPONIBLE (INFRA)"
    if infra_fails:
        return "PLATAFORMA DEGRADADA (INFRA)"
    return "PLATAFORMA OPERATIVA (INFRA)"

def final_badge(final: str) -> str:
    if "OPERATIVA" in final:
        return ok(final)
    if "DEGRADADA" in final:
        return warn(final)
    return bad(final)

# =========================
# Extracción de evidencias (para pegarlas resumidas en el reporte)
# =========================
def read_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"ERROR leyendo {path}: {e}"

def extract_blocks(raw: str) -> Tuple[str, str]:
    if "STDOUT:" in raw:
        stdout_part = raw.split("STDOUT:", 1)[1]
        if "STDERR:" in stdout_part:
            stdout_block, stderr_part = stdout_part.split("STDERR:", 1)
            return stdout_block.strip(), stderr_part.strip()
        return stdout_part.strip(), ""
    return raw.strip(), ""

def clip_lines(text: str, max_lines: int = 14, max_chars: int = 2200) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    lines = t.splitlines()
    clipped = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        clipped += "\n" + faint(f"... ({len(lines)-max_lines} líneas omitidas)")
    if len(clipped) > max_chars:
        clipped = clipped[:max_chars] + "\n" + faint("... (texto recortado)")
    return clipped

def evidence_section(evid_dir: str, filename: str, title: str, max_lines: int = 14) -> str:
    path = os.path.join(evid_dir, filename)
    raw = read_file(path)

    stdout_block, stderr_block = extract_blocks(raw)
    stdout_clip = clip_lines(stdout_block, max_lines=max_lines)
    stderr_clip = clip_lines(stderr_block, max_lines=6)

    out = []
    out.append(f"{BOLD}{WHITE}▣ {title}{RESET}  {faint(filename)}")
    if stdout_clip:
        out.append(f"{faint('STDOUT:')}\n{stdout_clip}")
    else:
        out.append(f"{faint('STDOUT:')} (vacío)")
    if stderr_clip:
        out.append(f"{faint('STDERR:')}\n{stderr_clip}")
    out.append("")
    return "\n".join(out)

# =========================
# Checks (minimal + evidencias)
# =========================
def check_nodes_ready(kubectl: str, evid_dir: str) -> Dict:
    cmd = f"{kubectl} get nodes"
    rc, out, err = run_cmd(cmd, timeout=30)
    ev_path = write_evidence(evid_dir, "2_1_nodes_ready.txt",
                             f"CMD: {cmd}\nRC: {rc}\n\nSTDOUT:\n{out}\n\nSTDERR:\n{err}\n")

    expected = "Todos los nodos en estado Ready"
    if rc != 0 or not out:
        return row("2. Estado del cluster", "2.1", "Nodos Ready", cmd,
                   expected, summarize(err or "Sin salida"), "FALLA", ev_path)

    not_ready = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            name, st = parts[0], parts[1]
            if st.lower() != "ready":
                not_ready.append(f"{name}:{st}")

    if not_ready:
        return row("2. Estado del cluster", "2.1", "Nodos Ready", cmd,
                   expected, f"Nodos no Ready: {', '.join(not_ready)}", "FALLA", ev_path)

    return row("2. Estado del cluster", "2.1", "Nodos Ready", cmd,
               expected, "OK", "OK", ev_path)

def _kube_system_pods(kubectl: str, evid_dir: str, filename: str) -> Tuple[int, str, str, str]:
    cmd = f"{kubectl} get pods -n kube-system"
    rc, out, err = run_cmd(cmd, timeout=45)
    ev_path = write_evidence(evid_dir, filename,
                             f"CMD: {cmd}\nRC: {rc}\n\nSTDOUT:\n{out}\n\nSTDERR:\n{err}\n")
    return rc, out, err, ev_path

def check_control_plane(kubectl: str, evid_dir: str) -> Dict:
    rc, out, err, ev_path = _kube_system_pods(kubectl, evid_dir, "2_2_control_plane_pods.txt")
    cmd = f"{kubectl} get pods -n kube-system"
    expected = "kube-apiserver/scheduler/controller-manager Running/Ready"

    if rc != 0 or not out:
        return row("2. Estado del cluster", "2.2", "Control plane", cmd,
                   expected, summarize(err or "Sin salida"), "FALLA", ev_path)

    required = ["kube-apiserver", "kube-scheduler", "kube-controller-manager"]
    missing, badp = [], []
    for r in required:
        matches = [ln for ln in out.splitlines() if r in ln]
        if not matches:
            missing.append(r)
            continue
        for ln in matches:
            parts = ln.split()
            if len(parts) >= 3:
                ready, st = parts[1], parts[2]
                if st.lower() != "running" or not ready.startswith("1/"):
                    badp.append(f"{parts[0]}({ready},{st})")

    if missing:
        return row("2. Estado del cluster", "2.2", "Control plane", cmd,
                   expected, f"Faltan: {', '.join(missing)}", "FALLA", ev_path)
    if badp:
        return row("2. Estado del cluster", "2.2", "Control plane", cmd,
                   expected, f"No OK: {', '.join(badp)}", "FALLA", ev_path)

    return row("2. Estado del cluster", "2.2", "Control plane", cmd,
               expected, "OK", "OK", ev_path)

def check_etcd(kubectl: str, evid_dir: str) -> Dict:
    rc, out, err, ev_path = _kube_system_pods(kubectl, evid_dir, "2_3_etcd_health.txt")
    cmd = f"{kubectl} get pods -n kube-system"
    expected = "Pods etcd Running/Ready"

    if rc != 0 or not out:
        return row("2. Estado del cluster", "2.3", "etcd", cmd,
                   expected, summarize(err or "Sin salida"), "FALLA", ev_path)

    etcd_lines = [ln for ln in out.splitlines() if "etcd" in ln]
    if not etcd_lines:
        return row("2. Estado del cluster", "2.3", "etcd", cmd,
                   expected, "No se ven pods etcd en kube-system", "FALLA", ev_path)

    badp = []
    for ln in etcd_lines:
        parts = ln.split()
        if len(parts) >= 3:
            ready, st = parts[1], parts[2]
            if st.lower() != "running" or not ready.startswith("1/"):
                badp.append(f"{parts[0]}({ready},{st})")

    if badp:
        return row("2. Estado del cluster", "2.3", "etcd", cmd,
                   expected, f"No OK: {', '.join(badp)}", "FALLA", ev_path)

    return row("2. Estado del cluster", "2.3", "etcd", cmd,
               expected, "OK", "OK", ev_path)

def check_pressure(kubectl: str, evid_dir: str) -> Dict:
    cmd = f"{kubectl} describe nodes"
    rc, out, err = run_cmd(cmd, timeout=60)
    ev_path = write_evidence(evid_dir, "3_2_describe_nodes.txt",
                             f"CMD: {cmd}\nRC: {rc}\n\nSTDOUT:\n{out}\n\nSTDERR:\n{err}\n")
    expected = "Disk/Memory/PIDPressure != True"

    if rc != 0 or not out:
        return row("3. Capacidad (según contrato)", "3.2", "Sin presión crítica", cmd,
                   expected, summarize(err or "Sin salida"), "FALLA", ev_path)

    pressures = re.findall(r"(DiskPressure|MemoryPressure|PIDPressure)\s+(\w+)", out)
    badp = [f"{p}:{v}" for p, v in pressures if v.lower() == "true"]
    if badp:
        return row("3. Capacidad (según contrato)", "3.2", "Sin presión crítica", cmd,
                   expected, f"Presión crítica: {', '.join(sorted(set(badp)))}", "FALLA", ev_path)

    return row("3. Capacidad (según contrato)", "3.2", "Sin presión crítica", cmd,
               expected, "OK", "OK", ev_path)

def check_dns_service_endpoints(kubectl: str, evid_dir: str) -> Dict:
    cmd_svc = f"{kubectl} get svc -n kube-system"
    cmd_ep  = f"{kubectl} get endpoints -n kube-system"
    rc1, out1, err1 = run_cmd(cmd_svc, timeout=30)
    rc2, out2, err2 = run_cmd(cmd_ep, timeout=30)

    ev_path = write_evidence(
        evid_dir, "4_3_dns_service_endpoints.txt",
        f"CMD: {cmd_svc}\nRC: {rc1}\n\nSTDOUT:\n{out1}\n\nSTDERR:\n{err1}\n\n"
        f"CMD: {cmd_ep}\nRC: {rc2}\n\nSTDOUT:\n{out2}\n\nSTDERR:\n{err2}\n"
    )

    expected = "Service DNS (53) con endpoints != <none>"
    dns_svc_names = []

    if rc1 == 0 and out1:
        for ln in out1.splitlines()[1:]:
            parts = ln.split()
            if len(parts) >= 5:
                name = parts[0]
                ports = " ".join(parts[4:-1]) if len(parts) > 6 else parts[4]
                if "53/" in ports:
                    dns_svc_names.append(name)

    if not dns_svc_names:
        return row("4. Conectividad base", "4.3", "DNS svc endpoints", f"{cmd_svc} ; {cmd_ep}",
                   expected, "No se encontró Service DNS (53) en kube-system", "FALLA", ev_path)

    if rc2 != 0 or not out2:
        return row("4. Conectividad base", "4.3", "DNS svc endpoints", f"{cmd_svc} ; {cmd_ep}",
                   expected, summarize(err2 or "Sin salida"), "FALLA", ev_path)

    for svc in dns_svc_names:
        line = next((l for l in out2.splitlines()[1:] if l.startswith(svc + " ")), None)
        if line:
            parts = line.split()
            eps = parts[1] if len(parts) > 1 else ""
            if eps and eps.lower() != "<none>":
                return row("4. Conectividad base", "4.3", "DNS svc endpoints", f"{cmd_svc} ; {cmd_ep}",
                           expected, "OK", "OK", ev_path)

    return row("4. Conectividad base", "4.3", "DNS svc endpoints", f"{cmd_svc} ; {cmd_ep}",
               expected, f"Svc DNS ({', '.join(dns_svc_names)}) sin endpoints", "FALLA", ev_path)

def check_dns_resolution(kubectl: str, evid_dir: str) -> Dict:
    cmd = f"{kubectl} run dns-check --rm -i --restart=Never --image=busybox:1.36 -- nslookup kubernetes.default.svc.cluster.local"
    rc, out, err = run_cmd(cmd, timeout=90)
    ev_path = write_evidence(evid_dir, "4_2_dns_resolution.txt",
                             f"CMD: {cmd}\nRC: {rc}\n\nSTDOUT:\n{out}\n\nSTDERR:\n{err}\n")

    expected = "nslookup resuelve kubernetes.default.svc.cluster.local"
    if rc == 0 and ("Name:" in out or "Address" in out):
        return row("4. Conectividad base", "4.2", "DNS interno", cmd,
                   expected, "OK", "OK", ev_path)

    msg = (err or out or "").lower()
    if "forbidden" in msg or "rbac" in msg:
        return row("4. Conectividad base", "4.2", "DNS interno", cmd,
                   expected, "N/A (RBAC no permite pod de prueba)", "N/A", ev_path, scope="N_A")

    return row("4. Conectividad base", "4.2", "DNS interno", cmd,
               expected, summarize(err or out or "Sin salida"), "FALLA", ev_path)

def check_events_infra_only(kubectl: str, evid_dir: str) -> Dict:
    cmd = f"{kubectl} get events -A --sort-by='.lastTimestamp'"
    rc, out, err = run_cmd(cmd, timeout=60)
    tail = "\n".join(out.splitlines()[-250:]) if out else ""
    ev_path = write_evidence(evid_dir, "5_1_events.txt",
                             f"CMD: {cmd}\nRC: {rc}\n\nSTDOUT (tail 250):\n{tail}\n\nSTDERR:\n{err}\n")
    expected = "Sin Warning en kube-system (infra)"

    if rc != 0 or not out:
        return row("5. Eventos (INFRA)", "5.1", "Warnings kube-system", cmd,
                   expected, summarize(err or "Sin salida"), "FALLA", ev_path)

    infra_warn = []
    for ln in out.splitlines()[1:]:
        parts = ln.split(None, 5)
        if len(parts) < 6:
            continue
        ns, _, typ, reason, obj, msg = parts
        if typ != "Warning":
            continue
        if ns in INFRA_NAMESPACES:
            infra_warn.append(f"{reason} {obj} {summarize(msg, 110)}")

    if infra_warn:
        return row("5. Eventos (INFRA)", "5.1", "Warnings kube-system", cmd,
                   expected, f"Warnings INFRA: {len(infra_warn)} (ver evidencia)", "FALLA", ev_path)

    return row("5. Eventos (INFRA)", "5.1", "Warnings kube-system", cmd,
               expected, "OK", "OK", ev_path)

def check_versions_consistency(kubectl: str, evid_dir: str) -> Dict:
    cmd = f"{kubectl} get nodes -o json"
    rc, out, err = run_cmd(cmd, timeout=60)

    ev_path = write_evidence(
        evid_dir, "6_1_versions.txt",
        f"CMD: {cmd}\nRC: {rc}\n\nSTDOUT (head 20k):\n{(out[:20000] if out else '')}\n\nSTDERR:\n{err}\n"
    )

    expected = "kubeletVersion consistente"
    if rc != 0 or not out:
        return row("6. Upgrades/parches", "6.1", "Consistencia versión", cmd,
                   expected, summarize(err or "Sin salida"), "FALLA", ev_path)

    kubelet_versions = set(re.findall(r'"kubeletVersion"\s*:\s*"([^"]+)"', out))
    if kubelet_versions and len(kubelet_versions) > 1:
        return row("6. Upgrades/parches", "6.1", "Consistencia versión", cmd,
                   expected, f"Inconsistente: {', '.join(sorted(kubelet_versions))}", "FALLA", ev_path)

    return row("6. Upgrades/parches", "6.1", "Consistencia versión", cmd,
               expected, "OK", "OK", ev_path)

# =========================
# Reporte ULTRA LITE + Evidencias pegadas
# =========================
def build_report(kubectl: str, ts: str, evid_dir: str, report_path: str, rows: List[Dict]) -> str:
    # ✅ AJUSTE: Mostrar TODOS los nodos (sin "omitidos")
    rcN, outN, errN = run_cmd(f"{kubectl} get nodes -o wide", timeout=60)
    nodes_summary = outN if (rcN == 0 and outN) else summarize(errN or "No disponible", 180)

    infra_fails = [r for r in rows if r["scope"] == "INFRA" and r["estado"] != "OK"]
    na = [r for r in rows if r["estado"] == "N/A" or r["scope"] == "N_A"]

    final = final_state(rows)

    # SOLO fallas INFRA (ultra lite)
    if infra_fails:
        blocks = []
        for r in infra_fails:
            blocks.append(f"{bad('✖')} {BOLD}{r['paso']}{RESET} {r['verificacion']}")
            blocks.append(f"    {faint('Hallazgo:')} {summarize(r['salida_resumen'], 240)}")
            blocks.append(f"    {faint('Sugerencia:')} {suggestion_for_step(r['paso'])}")
            blocks.append(f"    {faint('Evidencia:')} {r['evidencia']}")
            blocks.append("")
        fails_txt = "\n".join(blocks).rstrip()
    else:
        fails_txt = ok("✔ Sin hallazgos INFRA.")

    # N/A minimal
    if na:
        na_lines = []
        for r in na[:3]:
            na_lines.append(f"{warn('•')} {r['paso']} {r['verificacion']}: {summarize(r['salida_resumen'], 120)}")
        if len(na) > 3:
            na_lines.append(faint(f"(+{len(na)-3} checks N/A adicionales)"))
        na_txt = "\n".join(na_lines)
    else:
        na_txt = faint("(Sin checks N/A)")

    # Evidencias resumidas
    evid = []
    evid.append(evidence_section(evid_dir, "00_kubectl_check.txt", "KUBECTL / Cliente", max_lines=10))
    evid.append(evidence_section(evid_dir, "2_1_nodes_ready.txt", "2.1 Nodos Ready", max_lines=14))
    evid.append(evidence_section(evid_dir, "2_2_control_plane_pods.txt", "2.2 Control plane pods", max_lines=14))
    evid.append(evidence_section(evid_dir, "2_3_etcd_health.txt", "2.3 etcd pods", max_lines=14))
    evid.append(evidence_section(evid_dir, "3_2_describe_nodes.txt", "3.2 Node Pressure (extracto)", max_lines=16))
    evid.append(evidence_section(evid_dir, "4_2_dns_resolution.txt", "4.2 DNS Resolution", max_lines=14))
    evid.append(evidence_section(evid_dir, "4_3_dns_service_endpoints.txt", "4.3 DNS Service & Endpoints", max_lines=18))
    evid.append(evidence_section(evid_dir, "5_1_events.txt", "5.1 Events (tail)", max_lines=16))
    evid.append(evidence_section(evid_dir, "6_1_versions.txt", "6.1 Versions / kubelet", max_lines=14))

    evid_block = "\n".join(evid).rstrip() + "\n"

    header = f"""{BOLD}{WHITE}================================================={RESET}
{BOLD}{WHITE}REPORTE SOPORTE - INFRA RKE2 (ULTRA LITE){RESET}
{BOLD}{WHITE}================================================={RESET}

{info('Fecha/Hora:')} {ts}
{info('Contexto:')} {get_context(kubectl)}
{info('Kubectl:')} {kubectl}
{info('Powered by:')} DLVARGAS

{BOLD}Resultado:{RESET} {final_badge(final)}
{faint('Evidencias:')} {evid_dir}
{faint('Archivo:')} {report_path}

{BOLD}{WHITE}------------------- RESUMEN NODOS -------------------{RESET}
{nodes_summary}

{BOLD}{WHITE}------------------- HALLAZGOS INFRA ------------------{RESET}
{fails_txt}

{BOLD}{WHITE}----------------------- N/A --------------------------{RESET}
{na_txt}

{BOLD}{WHITE}---------------- EVIDENCIAS RESUMIDAS -----------------{RESET}
{evid_block}

{BOLD}{WHITE}--------------------- ALCANCE -------------------------{RESET}
Este reporte evalúa SOLO infraestructura base del cluster RKE2 (kube-system).
No incluye aplicaciones del cliente ni stacks fuera del servicio.

""".rstrip() + "\n"
    return header

# =========================
# Main
# =========================
def main() -> int:
    ts = now_ts()
    evid_dir = os.path.abspath(f"evidencias_{ts}")
    os.makedirs(evid_dir, exist_ok=True)
    report_path = os.path.abspath(f"reporte_soporte_{ts}.txt")

    kubectl = resolve_kubectl()
    if not kubectl:
        write_evidence(evid_dir, "00_env_debug.txt",
                       f"PATH={os.environ.get('PATH','')}\nKUBECTL={os.environ.get('KUBECTL','')}\n")
        print(bad("❌ No pude encontrar 'kubectl' en PATH."))
        print("   Solución: export KUBECTL=/usr/local/bin/kubectl  (o agrega /usr/local/bin al PATH)")
        print(f"   Evidencias en: {evid_dir}")
        return 2

    ok_bin, info_txt = kubectl_available(kubectl)
    write_evidence(evid_dir, "00_kubectl_check.txt", f"KUBECTL={kubectl}\n\n{info_txt}\n")
    if not ok_bin:
        print(bad("❌ Encontré kubectl pero no pude ejecutarlo."))
        print(f"   KUBECTL: {kubectl}")
        print(f"   Detalle: {info_txt}")
        print(f"   Evidencias en: {evid_dir}")
        return 3

    # Checks INFRA
    rows: List[Dict] = []
    rows += [
        check_nodes_ready(kubectl, evid_dir),
        check_control_plane(kubectl, evid_dir),
        check_etcd(kubectl, evid_dir),
        check_pressure(kubectl, evid_dir),
        check_dns_service_endpoints(kubectl, evid_dir),
        check_dns_resolution(kubectl, evid_dir),
        check_events_infra_only(kubectl, evid_dir),
        check_versions_consistency(kubectl, evid_dir),
    ]

    # Reporte final
    report = build_report(kubectl, ts, evid_dir, report_path, rows)
    write_text(report_path, report)
    write_evidence(evid_dir, "reporte_soporte.txt", report)

    final = final_state(rows)
    print("\n" + info("✅ Checklist INFRA ejecutado (ULTRA LITE + evidencias resumidas)."))
    print(f"{info('📌 Resultado final:')} {final_badge(final)}")
    print(f"{info('📌 Reporte:')} {report_path}")
    print(f"{info('📁 Evidencias:')} {evid_dir}")
    print(f"\n{BOLD}👉 Ver reporte:{RESET}  cat {report_path}\n")
    return 0

if __name__ == "__main__":
    sys.exit(main())
