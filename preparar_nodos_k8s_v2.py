import subprocess
from pathlib import Path
import time
import os
import argparse

# ANSI Colors
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RESET = "\033[0m"

log_file = "checklist.log"

def log(msg):
    with open(log_file, "a") as f:
        f.write(msg + "\n")

def run_command(cmd):
    try:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL, text=True).strip()
    except subprocess.CalledProcessError:
        return ""

def check_mark(condition):
    return f"{GREEN}✅{RESET}" if condition else f"{RED}❌{RESET}"

# ✅ NUEVOS CHECKS DETALLADOS PARA kubectl y helm

def check_kubectl_installed():
    return bool(run_command("which kubectl")), (
        "curl -LO \"https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl\" && "
        "curl -LO \"https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl.sha256\" && "
        "echo \"$(cat kubectl.sha256)  kubectl\" | sha256sum --check && "
        "sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl"
    )

def check_helm_installed():
    return bool(run_command("which helm")), (
        "curl https://baltocdn.com/helm/signing.asc | gpg --dearmor | sudo tee /usr/share/keyrings/helm.gpg > /dev/null && "
        "sudo apt-get install apt-transport-https --yes && "
        "echo \"deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/helm.gpg] "
        "https://baltocdn.com/helm/stable/debian/ all main\" | "
        "sudo tee /etc/apt/sources.list.d/helm-stable-debian.list && "
        "sudo apt-get update && "
        "sudo apt-get install helm -y"
    )

def check_kubeconfig_folder():
    return Path("/root/.kube").exists(), "mkdir -p /root/.kube"

# ✅ RESTO DE CHECKS

def check_apt():
    output = run_command("apt list --upgradable")
    return "upgradable" not in output, "sudo apt update && sudo apt upgrade -y && sudo apt autoremove -y"

def check_hostname():
    hostname = run_command("hostname")
    if Path("/etc/hosts").exists():
        return hostname in Path("/etc/hosts").read_text(), f"echo '127.0.0.1 {hostname}' | sudo tee -a /etc/hosts"
    return False, "Editar /etc/hosts manualmente"

def check_timezone_chrony(timezone):
    tz = timezone in run_command("timedatectl | grep 'Time zone'")
    chrony = "active" in run_command("systemctl is-active chrony")
    return tz and chrony, f"sudo timedatectl set-timezone {timezone} && sudo apt install -y chrony && sudo systemctl enable --now chrony"

def check_kernel_modules():
    overlay = run_command("lsmod | grep overlay")
    brnf = run_command("lsmod | grep br_netfilter")
    return bool(overlay and brnf), "sudo modprobe overlay && sudo modprobe br_netfilter && echo -e 'overlay\\nbr_netfilter' | sudo tee /etc/modules-load.d/k8s.conf"

def check_sysctl():
    ipt = "= 1" in run_command("sysctl net.bridge.bridge-nf-call-iptables")
    ip6t = "= 1" in run_command("sysctl net.bridge.bridge-nf-call-ip6tables")
    fwd = "= 1" in run_command("sysctl net.ipv4.ip_forward")
    return ipt and ip6t and fwd, (
        "echo 'net.bridge.bridge-nf-call-iptables = 1\n"
        "net.bridge.bridge-nf-call-ip6tables = 1\n"
        "net.ipv4.ip_forward = 1' | sudo tee /etc/sysctl.d/99-kubernetes-cri.conf && sudo sysctl --system"
    )

def check_swap():
    return run_command("swapon --show") == "", "sudo swapoff -a && sudo sed -i '/swap/d' /etc/fstab"

def check_logs():
    return Path("/var/log/journal").exists(), "sudo mkdir -p /var/log/journal && sudo systemctl restart systemd-journald"

def check_services():
    active = all("active" in run_command(f"systemctl is-active {svc}") for svc in ["auditd", "sysstat", "watchdog"])
    return active, "sudo apt install -y auditd sysstat watchdog && sudo systemctl enable --now auditd sysstat watchdog"

def check_dns():
    return "8.8.8.8" in run_command("cat /etc/resolv.conf"), (
        "sudo apt install -y resolvconf && "
        "echo 'nameserver 8.8.8.8' | sudo tee /etc/resolvconf/resolv.conf.d/head && sudo resolvconf -u"
    )

def check_longhorn():
    return "/var/lib/longhorn" in run_command("mount | grep /var/lib/longhorn"), "Verifica manualmente el montaje en /var/lib/longhorn"

def check_rke2_sysctl():
    swap = "= 0" in run_command("sysctl vm.swappiness")
    watches = "1048576" in run_command("sysctl fs.inotify.max_user_watches")
    instances = "8192" in run_command("sysctl fs.inotify.max_user_instances")
    return swap and watches and instances, (
        "echo 'vm.swappiness=0\n"
        "fs.inotify.max_user_watches=1048576\n"
        "fs.inotify.max_user_instances=8192' | sudo tee /etc/sysctl.d/99-rke2.conf && sudo sysctl --system"
    )

def check_ping():
    return "bytes from" in run_command("ping -c 1 8.8.8.8"), None

# ✅ FLUJO PRINCIPAL

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto", action="store_true", help="Aplicar todas las remediaciones sin preguntar")
    parser.add_argument("--timezone", default="America/Santiago", help="Zona horaria deseada (ejemplo: America/Bogota)")
    args = parser.parse_args()

    if os.geteuid() != 0:
        print(f"{RED}❌ Debes ejecutar este script como root.{RESET}")
        return

    if "ubuntu" not in run_command("cat /etc/os-release").lower():
        print(f"{YELLOW}⚠️ Este script fue diseñado para Ubuntu. Ejecutar en otras distribuciones puede causar problemas.{RESET}")

    print("\n=== 🧪 Diagnóstico Previo del Nodo Ubuntu para RKE2 ===\n")

    # Construir lista de checks con timezone elegido
    checks = [
        ("Sistema actualizado (apt)", check_apt),
        ("Hostname en /etc/hosts", check_hostname),
        ("Zona horaria y chrony", lambda: check_timezone_chrony(args.timezone)),
        ("Módulos kernel (overlay, br_netfilter)", check_kernel_modules),
        ("Parámetros de red sysctl", check_sysctl),
        ("Swap desactivado", check_swap),
        ("Directorio persistente de logs", check_logs),
        ("Servicios auditd, sysstat, watchdog", check_services),
        ("DNS resolv.conf", check_dns),
        ("Volumen Longhorn montado", check_longhorn),
        ("Sysctl para RKE2", check_rke2_sysctl),
        ("Conectividad a internet", check_ping),
        ("kubectl instalado", check_kubectl_installed),
        ("helm instalado", check_helm_installed),
        ("Carpeta /root/.kube existe", check_kubeconfig_folder),
    ]

    failed_steps = []
    passed, remediated, skipped = [], [], []

    for name, check_fn in checks:
        print(f"🔍 Verificando: {name}...", end=" ", flush=True)
        ok, _ = check_fn()
        mark = check_mark(ok)
        print(mark)
        log(f"[{mark}] {name}")
        if ok:
            passed.append(name)
        else:
            failed_steps.append((name, check_fn))

    time.sleep(2)
    print("\n=== 🔧 Remediación Interactiva ===\n")

    for name, check_fn in failed_steps:
        _, remediation = check_fn()
        if remediation:
            if args.auto:
                print(f"⚙️ Aplicando automáticamente: {name}")
                os.system(remediation)
                remediated.append(name)
            else:
                resp = input(f"{RED}¿Deseas remediar: {name}? (s/n): {RESET}").strip().lower()
                if resp == "s":
                    print(f"⚙️ Ejecutando: {remediation}")
                    os.system(remediation)
                    remediated.append(name)
                else:
                    skipped.append(name)
        else:
            print(f"{YELLOW}⚠️ {name} no tiene remediación automática.{RESET}")
            skipped.append(name)

    # ✅ Preguntar si quiere cambiar zona horaria
    resp = input(f"\n{YELLOW}¿Deseas actualizar la zona horaria? (s/n): {RESET}").strip().lower()
    if resp == "s":
        nueva_zona = input(f"{YELLOW}Escribe la nueva zona horaria (ejemplo America/Bogota): {RESET}").strip()
        print(f"\n⚙️ Aplicando configuración de zona horaria: {nueva_zona}")

        remediation_cmd = f"sudo timedatectl set-timezone {nueva_zona} && sudo apt install -y chrony && sudo systemctl enable --now chrony"
        os.system(remediation_cmd)

        # Verificar resultado final
        ok, _ = check_timezone_chrony(nueva_zona)
        mark = check_mark(ok)
        print(f"🔍 Resultado configuración zona horaria: {mark}")
        log(f"[{mark}] Zona horaria configurada en {nueva_zona}")

    print("\n=== ✅ Resumen Final ===")
    print(f"{GREEN}✔️ Pasaron: {len(passed)}{RESET}")
    print(f"{YELLOW}🔧 Corregidos: {len(remediated)}{RESET}")
    print(f"{RED}🚫 Omitidos o sin remediación: {len(skipped)}{RESET}")
    print(f"\n📁 Log guardado en: {log_file}")

if __name__ == "__main__":
    main()
