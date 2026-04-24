#!/usr/bin/env python3

import subprocess
from pathlib import Path
import time
import os
import argparse
import sys
import json
import socket
from datetime import datetime

# ==========================
# COLORES
# ==========================

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"

# ==========================
# CONFIGURACIÓN
# ==========================

DEFAULT_TIMEZONE = os.getenv("TIMEZONE", "America/Bogota")
DNS_SERVER = os.getenv("DNS_SERVER", "8.8.8.8")
MOUNT_POINT_LONGHORN = os.getenv("LONGHORN_PATH", "/var/lib/longhorn")

TIMESTAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
LOG_FILE = os.getenv("LOG_FILE", f"/root/preparar-nodo-rke2-{TIMESTAMP}.log")
REPORT_FILE = os.getenv("REPORT_FILE", f"/root/preparar-nodo-rke2-reporte-{TIMESTAMP}.txt")
JSON_REPORT_FILE = os.getenv("JSON_REPORT_FILE", f"/root/preparar-nodo-rke2-reporte-{TIMESTAMP}.json")

COMMAND_RESULTS = []
CHECK_RESULTS = []


# ==========================
# UTILIDADES
# ==========================

def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(msg + "\n")


def run_command(cmd):
    try:
        return subprocess.check_output(
            cmd,
            shell=True,
            stderr=subprocess.DEVNULL,
            text=True,
            executable="/bin/bash"
        ).strip()
    except subprocess.CalledProcessError:
        return ""


def run_shell(cmd, name="comando"):
    print(f"{BLUE}⚙️ Ejecutando:{RESET} {name}")
    log(f"Ejecutando [{name}]: {cmd}")

    start = time.time()

    result = subprocess.run(
        cmd,
        shell=True,
        text=True,
        executable="/bin/bash",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    duration = round(time.time() - start, 2)

    COMMAND_RESULTS.append({
        "name": name,
        "command": cmd,
        "returncode": result.returncode,
        "duration_seconds": duration,
        "stdout": result.stdout[-3000:],
        "stderr": result.stderr[-3000:]
    })

    if result.returncode == 0:
        print(f"{GREEN}✅ OK:{RESET} {name}")
        log(f"OK [{name}] duración={duration}s")
        return True

    print(f"{RED}❌ FALLÓ:{RESET} {name}")
    print(result.stderr.strip()[-1000:])
    log(f"ERROR [{name}] código={result.returncode} duración={duration}s")
    log(result.stderr.strip()[-3000:])
    return False


def check_mark(condition):
    return f"{GREEN}✅{RESET}" if condition else f"{RED}❌{RESET}"


def require_root():
    if os.geteuid() != 0:
        print(f"{RED}❌ Debes ejecutar este script como root.{RESET}")
        sys.exit(1)


def check_ubuntu():
    os_release = run_command("cat /etc/os-release").lower()

    if "ubuntu" not in os_release:
        print(f"{YELLOW}⚠️ Este script fue diseñado para Ubuntu.{RESET}")
        print(f"{YELLOW}⚠️ Puede funcionar en Debian, pero no es el objetivo principal.{RESET}")


def hostname():
    return socket.gethostname()


# ==========================
# CHECKS
# ==========================

def check_apt():
    output = run_command("apt list --upgradable 2>/dev/null | grep -v '^Listing' || true")
    ok = output.strip() == ""

    remediation = (
        "apt-get update && "
        "DEBIAN_FRONTEND=noninteractive apt-get upgrade -y && "
        "DEBIAN_FRONTEND=noninteractive apt-get autoremove -y"
    )

    return ok, remediation, "Sistema sin paquetes pendientes de actualización"


def check_hostname():
    current_hostname = run_command("hostname")
    hosts_content = Path("/etc/hosts").read_text() if Path("/etc/hosts").exists() else ""

    ok = current_hostname in hosts_content
    remediation = f"grep -q '{current_hostname}' /etc/hosts || echo '127.0.0.1 {current_hostname}' >> /etc/hosts"

    return ok, remediation, f"Hostname detectado: {current_hostname}"


def check_timezone_chrony(timezone):
    timezone_output = run_command("timedatectl | grep 'Time zone' || true")
    chrony_status = run_command("systemctl is-active chrony || true")

    ok = timezone in timezone_output and chrony_status == "active"

    remediation = (
        f"timedatectl set-timezone {timezone} && "
        "apt-get update && "
        "apt-get install -y chrony && "
        "systemctl enable --now chrony"
    )

    return ok, remediation, f"Zona horaria objetivo: {timezone}, chrony: {chrony_status}"


def check_kernel_modules():
    overlay = run_command("lsmod | grep '^overlay' || true")
    brnf = run_command("lsmod | grep '^br_netfilter' || true")

    ok = bool(overlay and brnf)

    remediation = (
        "modprobe overlay && "
        "modprobe br_netfilter && "
        "printf 'overlay\\nbr_netfilter\\n' > /etc/modules-load.d/k8s.conf"
    )

    return ok, remediation, "Módulos requeridos: overlay, br_netfilter"


def check_sysctl_network():
    ipt = "= 1" in run_command("sysctl net.bridge.bridge-nf-call-iptables 2>/dev/null || true")
    ip6t = "= 1" in run_command("sysctl net.bridge.bridge-nf-call-ip6tables 2>/dev/null || true")
    fwd = "= 1" in run_command("sysctl net.ipv4.ip_forward 2>/dev/null || true")

    ok = ipt and ip6t and fwd

    remediation = (
        "cat > /etc/sysctl.d/99-kubernetes-cri.conf <<'EOF'\n"
        "net.bridge.bridge-nf-call-iptables = 1\n"
        "net.bridge.bridge-nf-call-ip6tables = 1\n"
        "net.ipv4.ip_forward = 1\n"
        "EOF\n"
        "sysctl --system"
    )

    return ok, remediation, "Sysctl Kubernetes networking"


def check_swap():
    swap_output = run_command("swapon --show")
    ok = swap_output == ""

    remediation = "swapoff -a && sed -i.bak '/swap/d' /etc/fstab"

    return ok, remediation, "Swap debe estar desactivado"


def check_logs():
    ok = Path("/var/log/journal").exists()

    remediation = "mkdir -p /var/log/journal && systemctl restart systemd-journald"

    return ok, remediation, "Persistencia de journald en /var/log/journal"


def check_services():
    services = ["auditd", "sysstat", "watchdog"]

    statuses = {
        svc: run_command(f"systemctl is-active {svc} || true")
        for svc in services
    }

    ok = all(statuses[svc] == "active" for svc in services)

    remediation = (
        "apt-get update && "
        "apt-get install -y auditd sysstat watchdog && "
        "systemctl enable --now auditd || true && "
        "systemctl enable --now sysstat || true && "
        "systemctl enable --now watchdog || true"
    )

    return ok, remediation, f"Servicios: {statuses}"


def check_dns():
    resolv_conf = run_command("cat /etc/resolv.conf 2>/dev/null || true")
    resolvectl_dns = run_command("resolvectl dns 2>/dev/null || true")

    ok = DNS_SERVER in resolv_conf or DNS_SERVER in resolvectl_dns

    remediation = (
        "mkdir -p /etc/systemd/resolved.conf.d && "
        f"cat > /etc/systemd/resolved.conf.d/dns-k8s.conf <<'EOF'\n"
        "[Resolve]\n"
        f"DNS={DNS_SERVER}\n"
        "FallbackDNS=1.1.1.1\n"
        "EOF\n"
        "systemctl restart systemd-resolved || true"
    )

    return ok, remediation, f"DNS objetivo: {DNS_SERVER}"


def check_longhorn():
    mounted = MOUNT_POINT_LONGHORN in run_command(f"mount | grep {MOUNT_POINT_LONGHORN} || true")
    path_exists = Path(MOUNT_POINT_LONGHORN).exists()

    ok = mounted and path_exists

    return ok, None, f"Montaje Longhorn esperado: {MOUNT_POINT_LONGHORN}"


def check_rke2_sysctl():
    swap = "= 0" in run_command("sysctl vm.swappiness 2>/dev/null || true")
    watches = "1048576" in run_command("sysctl fs.inotify.max_user_watches 2>/dev/null || true")
    instances = "8192" in run_command("sysctl fs.inotify.max_user_instances 2>/dev/null || true")

    ok = swap and watches and instances

    remediation = (
        "cat > /etc/sysctl.d/99-rke2.conf <<'EOF'\n"
        "vm.swappiness=0\n"
        "fs.inotify.max_user_watches=1048576\n"
        "fs.inotify.max_user_instances=8192\n"
        "EOF\n"
        "sysctl --system"
    )

    return ok, remediation, "Sysctl recomendado para RKE2"


def check_ping():
    ok = "bytes from" in run_command("ping -c 1 -W 3 8.8.8.8 || true")

    return ok, None, "Conectividad hacia 8.8.8.8"


def check_kubectl_installed():
    ok = bool(run_command("which kubectl"))

    remediation = (
        "cd /tmp && "
        "curl -LO \"https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl\" && "
        "curl -LO \"https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl.sha256\" && "
        "echo \"$(cat kubectl.sha256)  kubectl\" | sha256sum --check && "
        "install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && "
        "rm -f /tmp/kubectl /tmp/kubectl.sha256"
    )

    return ok, remediation, "kubectl en /usr/local/bin o PATH"


def check_helm_installed():
    ok = bool(run_command("which helm"))

    remediation = (
        "apt-get update && "
        "apt-get install -y apt-transport-https ca-certificates curl gpg && "
        "curl https://baltocdn.com/helm/signing.asc | gpg --dearmor | tee /usr/share/keyrings/helm.gpg > /dev/null && "
        "echo \"deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/helm.gpg] https://baltocdn.com/helm/stable/debian/ all main\" "
        "| tee /etc/apt/sources.list.d/helm-stable-debian.list && "
        "apt-get update && "
        "apt-get install -y helm"
    )

    return ok, remediation, "helm instalado en PATH"


def check_kubeconfig_folder():
    ok = Path("/root/.kube").exists()

    remediation = "mkdir -p /root/.kube"

    return ok, remediation, "Carpeta /root/.kube"


# ==========================
# LISTA DE CHECKS
# ==========================

def build_checks(timezone):
    return [
        {
            "name": "Sistema actualizado apt",
            "fn": check_apt,
            "critical": False,
        },
        {
            "name": "Hostname en /etc/hosts",
            "fn": check_hostname,
            "critical": True,
        },
        {
            "name": "Zona horaria y chrony",
            "fn": lambda: check_timezone_chrony(timezone),
            "critical": False,
        },
        {
            "name": "Módulos kernel overlay y br_netfilter",
            "fn": check_kernel_modules,
            "critical": True,
        },
        {
            "name": "Parámetros de red sysctl",
            "fn": check_sysctl_network,
            "critical": True,
        },
        {
            "name": "Swap desactivado",
            "fn": check_swap,
            "critical": True,
        },
        {
            "name": "Directorio persistente de logs",
            "fn": check_logs,
            "critical": False,
        },
        {
            "name": "Servicios auditd, sysstat, watchdog",
            "fn": check_services,
            "critical": False,
        },
        {
            "name": "DNS configurado",
            "fn": check_dns,
            "critical": False,
        },
        {
            "name": "Volumen Longhorn montado",
            "fn": check_longhorn,
            "critical": True,
        },
        {
            "name": "Sysctl para RKE2",
            "fn": check_rke2_sysctl,
            "critical": True,
        },
        {
            "name": "Conectividad a internet",
            "fn": check_ping,
            "critical": False,
        },
        {
            "name": "kubectl instalado",
            "fn": check_kubectl_installed,
            "critical": False,
        },
        {
            "name": "helm instalado",
            "fn": check_helm_installed,
            "critical": False,
        },
        {
            "name": "Carpeta /root/.kube existe",
            "fn": check_kubeconfig_folder,
            "critical": False,
        },
    ]


# ==========================
# EJECUCIÓN DE CHECKS
# ==========================

def run_single_check(check):
    name = check["name"]
    critical = check["critical"]

    ok, remediation, details = check["fn"]()

    result = {
        "name": name,
        "ok": ok,
        "critical": critical,
        "details": details,
        "has_remediation": remediation is not None,
        "remediation_applied": False,
        "remediation_success": None,
        "ok_after_remediation": ok,
    }

    mark = check_mark(ok)
    critical_text = "CRÍTICO" if critical else "NO CRÍTICO"

    print(f"🔍 {name} [{critical_text}]... {mark}")
    log(f"CHECK inicial - {name} - ok={ok} - critical={critical} - details={details}")

    return result, remediation


def execute_checks_with_remediation(checks, check_only=False):
    results = []

    for check in checks:
        result, remediation = run_single_check(check)

        if not result["ok"] and remediation and not check_only:
            print(f"{YELLOW}🛠️ Remediando:{RESET} {result['name']}")
            success = run_shell(remediation, result["name"])

            result["remediation_applied"] = True
            result["remediation_success"] = success

            print(f"{BLUE}🔁 Punto de control posterior:{RESET} {result['name']}")

            ok_after, _, details_after = check["fn"]()
            result["ok_after_remediation"] = ok_after
            result["details_after_remediation"] = details_after

            print(f"   Resultado posterior: {check_mark(ok_after)}")
            log(f"CHECK posterior - {result['name']} - ok={ok_after} - details={details_after}")

        elif not result["ok"] and remediation is None:
            print(f"{YELLOW}⚠️ Sin remediación automática:{RESET} {result['name']}")
            log(f"SIN REMEDIACIÓN - {result['name']}")

        elif check_only:
            print(f"{YELLOW}Modo check-only: no se aplica remediación.{RESET}")

        results.append(result)

    return results


def final_verification(checks):
    print("\n=== 🔁 Verificación final completa ===\n")

    final = []

    for check in checks:
        ok, _, details = check["fn"]()

        item = {
            "name": check["name"],
            "ok": ok,
            "critical": check["critical"],
            "details": details,
        }

        final.append(item)

        critical_text = "CRÍTICO" if check["critical"] else "NO CRÍTICO"
        print(f"🔎 {check['name']} [{critical_text}]... {check_mark(ok)}")
        log(f"VERIFICACIÓN FINAL - {check['name']} - ok={ok} - details={details}")

    return final


# ==========================
# REPORTE
# ==========================

def generate_report(initial_results, final_results, timezone, check_only):
    total = len(final_results)
    final_ok = [x for x in final_results if x["ok"]]
    final_failed = [x for x in final_results if not x["ok"]]
    critical_failed = [x for x in final_results if not x["ok"] and x["critical"]]

    remediated = [
        x for x in initial_results
        if x["remediation_applied"] and x["ok_after_remediation"]
    ]

    remediation_failed = [
        x for x in initial_results
        if x["remediation_applied"] and not x["ok_after_remediation"]
    ]

    skipped = [
        x for x in initial_results
        if not x["ok"] and not x["has_remediation"]
    ]

    status = "EXITOSO" if not critical_failed else "CON PENDIENTES CRÍTICOS"

    lines = []
    lines.append("==============================================")
    lines.append("REPORTE PREPARACIÓN NODO RKE2")
    lines.append("==============================================")
    lines.append(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"Hostname: {hostname()}")
    lines.append(f"Zona horaria objetivo: {timezone}")
    lines.append(f"Modo check-only: {check_only}")
    lines.append(f"Estado final: {status}")
    lines.append("")
    lines.append("RESUMEN")
    lines.append("----------------------------------------------")
    lines.append(f"Checks totales: {total}")
    lines.append(f"Checks OK finales: {len(final_ok)}")
    lines.append(f"Checks pendientes finales: {len(final_failed)}")
    lines.append(f"Pendientes críticos: {len(critical_failed)}")
    lines.append(f"Remediados correctamente: {len(remediated)}")
    lines.append(f"Remediaciones fallidas: {len(remediation_failed)}")
    lines.append(f"Sin remediación automática: {len(skipped)}")
    lines.append("")
    lines.append("RESULTADO FINAL POR CHECK")
    lines.append("----------------------------------------------")

    for item in final_results:
        state = "OK" if item["ok"] else "FAIL"
        critical = "CRÍTICO" if item["critical"] else "NO CRÍTICO"
        lines.append(f"[{state}] [{critical}] {item['name']} - {item['details']}")

    if remediated:
        lines.append("")
        lines.append("REMEDIADOS CORRECTAMENTE")
        lines.append("----------------------------------------------")
        for item in remediated:
            lines.append(f"- {item['name']}")

    if remediation_failed:
        lines.append("")
        lines.append("REMEDIACIONES FALLIDAS")
        lines.append("----------------------------------------------")
        for item in remediation_failed:
            lines.append(f"- {item['name']}")

    if skipped:
        lines.append("")
        lines.append("SIN REMEDIACIÓN AUTOMÁTICA")
        lines.append("----------------------------------------------")
        for item in skipped:
            lines.append(f"- {item['name']}")

    if critical_failed:
        lines.append("")
        lines.append("PENDIENTES CRÍTICOS")
        lines.append("----------------------------------------------")
        for item in critical_failed:
            lines.append(f"- {item['name']}")

    lines.append("")
    lines.append("ARCHIVOS")
    lines.append("----------------------------------------------")
    lines.append(f"Log: {LOG_FILE}")
    lines.append(f"Reporte TXT: {REPORT_FILE}")
    lines.append(f"Reporte JSON: {JSON_REPORT_FILE}")
    lines.append("")

    Path(REPORT_FILE).write_text("\n".join(lines))

    json_data = {
        "timestamp": datetime.now().isoformat(),
        "hostname": hostname(),
        "timezone": timezone,
        "check_only": check_only,
        "status": status,
        "summary": {
            "total_checks": total,
            "final_ok": len(final_ok),
            "final_failed": len(final_failed),
            "critical_failed": len(critical_failed),
            "remediated": len(remediated),
            "remediation_failed": len(remediation_failed),
            "skipped_without_remediation": len(skipped),
        },
        "initial_results": initial_results,
        "final_results": final_results,
        "commands": COMMAND_RESULTS,
        "files": {
            "log": LOG_FILE,
            "report_txt": REPORT_FILE,
            "report_json": JSON_REPORT_FILE,
        }
    }

    Path(JSON_REPORT_FILE).write_text(json.dumps(json_data, indent=2))

    print("\n=== 📋 Mini reporte ===")
    print(f"Estado final: {GREEN if not critical_failed else RED}{status}{RESET}")
    print(f"Checks OK finales: {len(final_ok)}/{total}")
    print(f"Pendientes críticos: {len(critical_failed)}")
    print(f"Remediados correctamente: {len(remediated)}")
    print(f"Remediaciones fallidas: {len(remediation_failed)}")
    print("")
    print(f"📁 Log: {LOG_FILE}")
    print(f"📄 Reporte TXT: {REPORT_FILE}")
    print(f"🧾 Reporte JSON: {JSON_REPORT_FILE}")

    return len(critical_failed) == 0


# ==========================
# MAIN
# ==========================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help="Zona horaria deseada. Ejemplo: America/Bogota"
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Solo revisar, no aplicar cambios"
    )

    args = parser.parse_args()

    require_root()
    check_ubuntu()

    Path(LOG_FILE).write_text("")
    log("Inicio preparación automática nodo RKE2")

    print("\n=== 🚀 Preparación automática de nodo Ubuntu para RKE2 ===")
    print(f"🌎 Zona horaria objetivo: {args.timezone}")
    print(f"📦 Longhorn path esperado: {MOUNT_POINT_LONGHORN}")
    print(f"🧾 Log: {LOG_FILE}")
    print(f"📄 Reporte: {REPORT_FILE}")
    print("")

    checks = build_checks(args.timezone)

    print("=== 🧪 Puntos de control iniciales ===\n")
    initial_results = execute_checks_with_remediation(
        checks,
        check_only=args.check_only
    )

    final_results = final_verification(checks)

    success = generate_report(
        initial_results=initial_results,
        final_results=final_results,
        timezone=args.timezone,
        check_only=args.check_only
    )

    if success:
        print(f"\n{GREEN}✅ Nodo preparado correctamente para RKE2 según los checks críticos.{RESET}")
        sys.exit(0)

    print(f"\n{RED}❌ Nodo con pendientes críticos. Revisa el reporte antes de continuar con RKE2.{RESET}")
    sys.exit(2)


if __name__ == "__main__":
    main()
