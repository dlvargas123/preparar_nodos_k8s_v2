#!/usr/bin/env python3
import subprocess
from pathlib import Path
import time
import os
import argparse
import sys
from datetime import datetime

# ANSI Colors
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"

# Logs
log_file = "/var/log/checklist-rke2-cloudinit.log"
report_file = "/var/log/checklist-rke2-reporte.txt"

HELM_BUILDKITE_APT_KEY_ID = "DDF78C3E6EBB2D2CC223C95C62BA89D07698DBC6"


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_logs():
    Path("/var/log").mkdir(parents=True, exist_ok=True)
    Path(log_file).touch(exist_ok=True)
    Path(report_file).touch(exist_ok=True)


def log(msg):
    ensure_logs()
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{now()}] {msg}\n")


def run_command(cmd, timeout=1800):
    """
    Ejecuta comandos para checks.
    Retorna stdout si RC=0, de lo contrario retorna string vacío.
    Deja stdout/stderr en log para diagnóstico.
    """
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    env["APT_LISTCHANGES_FRONTEND"] = "none"
    env["NEEDRESTART_MODE"] = "a"

    log(f"CMD: {cmd}")
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
            executable="/bin/bash",
        )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        log(f"RC: {proc.returncode}")
        if stdout:
            log(f"STDOUT:\n{stdout}")
        if stderr:
            log(f"STDERR:\n{stderr}")
        return stdout if proc.returncode == 0 else ""
    except Exception as e:
        log(f"ERROR ejecutando comando: {cmd} | {e}")
        return ""


def run_shell(cmd, timeout=1800):
    """
    Ejecuta comandos de remediación.
    Retorna True si RC=0.
    """
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    env["APT_LISTCHANGES_FRONTEND"] = "none"
    env["NEEDRESTART_MODE"] = "a"

    log(f"REMEDIATE CMD: {cmd}")
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
            executable="/bin/bash",
        )
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        log(f"RC: {proc.returncode}")
        if stdout:
            log(f"STDOUT:\n{stdout}")
        if stderr:
            log(f"STDERR:\n{stderr}")
        return proc.returncode == 0
    except Exception as e:
        log(f"ERROR remediando comando: {cmd} | {e}")
        return False


def check_mark(condition):
    return f"{GREEN}✅{RESET}" if condition else f"{RED}❌{RESET}"


# ============================================================
# CHECKS DETALLADOS PARA kubectl y helm
# ============================================================

def check_kubectl_installed():
    remediation = (
        "set -euo pipefail; "
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get update; "
        "apt-get install -y curl ca-certificates; "
        "cd /tmp; "
        "KUBECTL_VERSION=$(curl -L -s https://dl.k8s.io/release/stable.txt); "
        "curl -LO https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl; "
        "curl -LO https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256; "
        "echo \"$(cat kubectl.sha256)  kubectl\" | sha256sum --check; "
        "install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl; "
        "kubectl version --client=true"
    )
    ok = bool(run_command("command -v kubectl")) and bool(run_command("kubectl version --client=true"))
    return ok, remediation


def check_helm_installed():
    """
    Instala Helm usando exactamente el repositorio Buildkite y validando fingerprint.
    Pensado para cloud-init: no usa sudo, no pregunta, usa -y y DEBIAN_FRONTEND=noninteractive.
    """
    helm_ok = bool(run_command("command -v helm")) and bool(run_command("helm version --short"))

    remediation = f"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export APT_LISTCHANGES_FRONTEND=none
export NEEDRESTART_MODE=a

HELM_BUILDKITE_APT_KEY_ID="{HELM_BUILDKITE_APT_KEY_ID}"

# Evitar conflictos con repos anteriores de Helm
rm -f /etc/apt/sources.list.d/helm*.list || true
rm -f /etc/apt/sources.list.d/*helm*.list || true
rm -f /etc/apt/sources.list.d/baltocdn*.list || true
rm -f /usr/share/keyrings/helm.gpg || true
rm -f "${{TMPDIR:-/tmp}}/helm.gpg" || true

apt-get update
apt-get install -y curl gpg apt-transport-https ca-certificates

curl -fsSL https://packages.buildkite.com/helm-linux/helm-debian/gpgkey > "${{TMPDIR:-/tmp}}/helm.gpg"

FINGERPRINT="$(gpg --show-keys --with-colons "${{TMPDIR:-/tmp}}/helm.gpg" | awk -F: '$1 == "fpr" {{print $10}}' | head -n 1)"

if [ "$FINGERPRINT" != "$HELM_BUILDKITE_APT_KEY_ID" ]; then
  echo "ERROR: Unexpected Helm APT key ID: potential key compromise"
  echo "Esperado: $HELM_BUILDKITE_APT_KEY_ID"
  echo "Recibido: $FINGERPRINT"
  exit 1
fi

cat "${{TMPDIR:-/tmp}}/helm.gpg" | gpg --dearmor | tee /usr/share/keyrings/helm.gpg > /dev/null
chmod 0644 /usr/share/keyrings/helm.gpg

echo "deb [signed-by=/usr/share/keyrings/helm.gpg] https://packages.buildkite.com/helm-linux/helm-debian/any/ any main" > /etc/apt/sources.list.d/helm-stable-debian.list

apt-get update
apt-get install -y helm

command -v helm
helm version --short
"""

    return helm_ok, remediation


def check_kubeconfig_folder():
    return Path("/root/.kube").exists(), "mkdir -p /root/.kube && chmod 700 /root/.kube"


# ============================================================
# RESTO DE CHECKS
# ============================================================

def check_apt():
    output = run_command("apt-get check")
    remediation = (
        "set -e; "
        "export DEBIAN_FRONTEND=noninteractive; "
        "apt-get update && apt-get upgrade -y && apt-get autoremove -y"
    )
    return bool(output) or True, remediation


def check_hostname():
    hostname = run_command("hostname")
    if Path("/etc/hosts").exists():
        return hostname in Path("/etc/hosts").read_text(errors="ignore"), f"echo '127.0.0.1 {hostname}' >> /etc/hosts"
    return False, "touch /etc/hosts && echo '127.0.0.1 $(hostname)' >> /etc/hosts"


def check_timezone_chrony(timezone):
    tz = timezone in run_command("timedatectl | grep 'Time zone' || true")
    chrony = "active" in run_command("systemctl is-active chrony || true")
    remediation = f"timedatectl set-timezone {timezone} && apt-get update && apt-get install -y chrony && systemctl enable --now chrony"
    return tz and chrony, remediation


def check_kernel_modules():
    overlay = run_command("lsmod | grep '^overlay' || true")
    brnf = run_command("lsmod | grep '^br_netfilter' || true")
    remediation = (
        "modprobe overlay && "
        "modprobe br_netfilter && "
        "printf 'overlay\\nbr_netfilter\\n' > /etc/modules-load.d/k8s.conf"
    )
    return bool(overlay and brnf), remediation


def check_sysctl():
    ipt = "1" == run_command("sysctl -n net.bridge.bridge-nf-call-iptables 2>/dev/null || true")
    ip6t = "1" == run_command("sysctl -n net.bridge.bridge-nf-call-ip6tables 2>/dev/null || true")
    fwd = "1" == run_command("sysctl -n net.ipv4.ip_forward 2>/dev/null || true")
    remediation = (
        "modprobe overlay || true; "
        "modprobe br_netfilter || true; "
        "printf 'net.bridge.bridge-nf-call-iptables = 1\\nnet.bridge.bridge-nf-call-ip6tables = 1\\nnet.ipv4.ip_forward = 1\\n' > /etc/sysctl.d/99-kubernetes-cri.conf; "
        "sysctl --system"
    )
    return ipt and ip6t and fwd, remediation


def check_swap():
    remediation = "swapoff -a || true; sed -i.bak '/ swap / s/^/#/' /etc/fstab || true; sed -i.bak2 '/swap/d' /etc/fstab || true"
    return run_command("swapon --show") == "", remediation


def check_logs():
    return Path("/var/log/journal").exists(), "mkdir -p /var/log/journal && systemctl restart systemd-journald"


def check_services():
    active = all("active" in run_command(f"systemctl is-active {svc} || true") for svc in ["auditd", "sysstat", "watchdog"])
    remediation = "apt-get update && apt-get install -y auditd sysstat watchdog && systemctl enable --now auditd sysstat watchdog"
    return active, remediation


def check_dns():
    resolv_conf = run_command("cat /etc/resolv.conf 2>/dev/null || true")
    resolvectl = run_command("resolvectl dns 2>/dev/null || true")
    remediation = (
        "mkdir -p /etc/systemd/resolved.conf.d; "
        "printf '[Resolve]\\nDNS=8.8.8.8 1.1.1.1\\nFallbackDNS=8.8.4.4 1.0.0.1\\nDNSStubListener=yes\\n' > /etc/systemd/resolved.conf.d/rke2-dns.conf; "
        "systemctl enable systemd-resolved || true; "
        "systemctl restart systemd-resolved || true"
    )
    return "8.8.8.8" in resolv_conf or "8.8.8.8" in resolvectl, remediation


def check_longhorn(longhorn_device=None, format_longhorn_device=False):
    mounted = "/var/lib/longhorn" in run_command("mount | grep /var/lib/longhorn || true")

    if not longhorn_device:
        return mounted, "echo 'No se definió --longhorn-device; validar Longhorn manualmente'"

    format_cmd = ""
    if format_longhorn_device:
        format_cmd = f"if ! blkid {longhorn_device}; then mkfs.ext4 -F {longhorn_device}; fi; "

    remediation = (
        "set -e; "
        "apt-get update; "
        "apt-get install -y open-iscsi nfs-common util-linux e2fsprogs; "
        "systemctl enable --now iscsid || true; "
        "systemctl enable --now open-iscsi || true; "
        "mkdir -p /var/lib/longhorn; "
        f"test -b {longhorn_device}; "
        f"{format_cmd}"
        f"UUID=$(blkid -o value -s UUID {longhorn_device}); "
        "grep -q '/var/lib/longhorn' /etc/fstab || echo \"UUID=${UUID} /var/lib/longhorn ext4 defaults,noatime 0 2\" >> /etc/fstab; "
        "mount -a; "
        "mount | grep /var/lib/longhorn"
    )
    return mounted, remediation


def check_rke2_sysctl():
    swap = "0" == run_command("sysctl -n vm.swappiness 2>/dev/null || true")
    watches_raw = run_command("sysctl -n fs.inotify.max_user_watches 2>/dev/null || true")
    instances_raw = run_command("sysctl -n fs.inotify.max_user_instances 2>/dev/null || true")

    try:
        watches = int(watches_raw) >= 1048576
        instances = int(instances_raw) >= 8192
    except Exception:
        watches = False
        instances = False

    remediation = (
        "printf 'vm.swappiness = 0\\nfs.inotify.max_user_watches = 1048576\\nfs.inotify.max_user_instances = 8192\\n' > /etc/sysctl.d/99-rke2.conf; "
        "sysctl --system"
    )
    return swap and watches and instances, remediation


def check_ping():
    return "bytes from" in run_command("ping -c 1 -W 3 8.8.8.8 || true"), None


# ============================================================
# FLUJO PRINCIPAL NO INTERACTIVO
# ============================================================

def write_report(passed, remediated, failed, skipped):
    lines = []
    lines.append("============================================")
    lines.append("REPORTE PREPARACIÓN NODO UBUNTU PARA RKE2")
    lines.append("============================================")
    lines.append(f"Fecha: {now()}")
    lines.append(f"Estado general: {'APTO' if not failed else 'NO APTO'}")
    lines.append("")
    lines.append(f"OK desde inicio: {len(passed)}")
    lines.append(f"Corregidos: {len(remediated)}")
    lines.append(f"Fallidos: {len(failed)}")
    lines.append(f"Omitidos/sin remediación: {len(skipped)}")
    lines.append("")

    if passed:
        lines.append("Controles OK:")
        for item in passed:
            lines.append(f"- {item}")
        lines.append("")

    if remediated:
        lines.append("Controles corregidos automáticamente:")
        for item in remediated:
            lines.append(f"- {item}")
        lines.append("")

    if failed:
        lines.append("Controles fallidos:")
        for item in failed:
            lines.append(f"- {item}")
        lines.append("")

    if skipped:
        lines.append("Controles omitidos o sin remediación:")
        for item in skipped:
            lines.append(f"- {item}")
        lines.append("")

    lines.append(f"Log técnico: {log_file}")
    lines.append("============================================")

    Path(report_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timezone", default="America/Bogota", help="Zona horaria deseada. Ejemplo: America/Bogota")
    parser.add_argument("--longhorn-device", default=None, help="Dispositivo para montar en /var/lib/longhorn. Ejemplo: /dev/vdb")
    parser.add_argument("--format-longhorn-device", action="store_true", help="Formatea el disco Longhorn si no tiene filesystem")
    parser.add_argument("--skip-longhorn", action="store_true", help="Omite validación/remediación de Longhorn")
    parser.add_argument("--only-helm", action="store_true", help="Solo instala/valida Helm")
    args = parser.parse_args()

    ensure_logs()
    log("========== INICIO CHECKLIST RKE2 CLOUD-INIT ==========")

    if os.geteuid() != 0:
        print(f"{RED}❌ Debes ejecutar este script como root.{RESET}")
        sys.exit(1)

    if "ubuntu" not in run_command("cat /etc/os-release 2>/dev/null || true").lower():
        print(f"{YELLOW}⚠️ Este script fue diseñado para Ubuntu. Continuando bajo responsabilidad del usuario.{RESET}")

    print("\n=== 🧪 Bootstrap No Interactivo del Nodo Ubuntu para RKE2 ===\n")
    print(f"📁 Log técnico: {log_file}")
    print(f"📄 Reporte: {report_file}\n")

    if args.only_helm:
        checks = [
            ("helm instalado", check_helm_installed),
        ]
    else:
        checks = [
            ("Sistema apt funcional/actualizado", check_apt),
            ("Hostname en /etc/hosts", check_hostname),
            ("Zona horaria y chrony", lambda: check_timezone_chrony(args.timezone)),
            ("Módulos kernel overlay y br_netfilter", check_kernel_modules),
            ("Parámetros de red sysctl", check_sysctl),
            ("Swap desactivado", check_swap),
            ("Directorio persistente de logs", check_logs),
            ("Servicios auditd, sysstat, watchdog", check_services),
            ("DNS resolv.conf/systemd-resolved", check_dns),
            ("Sysctl para RKE2", check_rke2_sysctl),
            ("Conectividad a internet", check_ping),
            ("kubectl instalado", check_kubectl_installed),
            ("helm instalado", check_helm_installed),
            ("Carpeta /root/.kube existe", check_kubeconfig_folder),
        ]

        if not args.skip_longhorn:
            checks.append(("Volumen Longhorn montado", lambda: check_longhorn(args.longhorn_device, args.format_longhorn_device)))

    passed = []
    remediated = []
    failed = []
    skipped = []

    for name, check_fn in checks:
        print(f"🔍 Verificando: {name}...", end=" ", flush=True)

        try:
            ok, remediation = check_fn()
        except Exception as e:
            ok = False
            remediation = None
            log(f"ERROR en check {name}: {e}")

        print(check_mark(ok))
        log(f"CHECK {name}: {'OK' if ok else 'FAIL'}")

        if ok:
            passed.append(name)
            continue

        if not remediation:
            print(f"{YELLOW}⚠️ {name} no tiene remediación automática.{RESET}")
            skipped.append(name)
            continue

        print(f"⚙️ Aplicando remediación automática: {name}")
        remediation_ok = run_shell(remediation)

        time.sleep(1)

        try:
            ok_after, _ = check_fn()
        except Exception as e:
            ok_after = False
            log(f"ERROR revalidando {name}: {e}")

        if remediation_ok and ok_after:
            print(f"{GREEN}✅ Corregido y validado: {name}{RESET}")
            remediated.append(name)
        else:
            print(f"{RED}❌ No se pudo corregir: {name}{RESET}")
            failed.append(name)

    print("\n=== ✅ Resumen Final ===")
    print(f"{GREEN}✔️ OK desde inicio: {len(passed)}{RESET}")
    print(f"{YELLOW}🔧 Corregidos: {len(remediated)}{RESET}")
    print(f"{RED}🚫 Fallidos: {len(failed)}{RESET}")
    print(f"{YELLOW}⚠️ Omitidos/sin remediación: {len(skipped)}{RESET}")

    report_lines = write_report(passed, remediated, failed, skipped)
    print(f"\n📁 Log guardado en: {log_file}")
    print(f"📄 Reporte guardado en: {report_file}")

    print("\n=== 📄 Reporte ===")
    for line in report_lines:
        print(line)

    log("========== FIN CHECKLIST RKE2 CLOUD-INIT ==========")

    if failed:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
