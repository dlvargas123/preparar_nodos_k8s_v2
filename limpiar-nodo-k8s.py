#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import glob
import time

# --- CONFIGURACIÓN ---
CONFIRM_WORD = "YES_RESET"
DIRECTORIES_TO_REMOVE = [
    "/etc/rancher/rke2",
    "/var/lib/rancher/rke2",
    "/var/lib/kubelet",
    "/etc/cni",
    "/opt/cni",
    "/var/lib/cni",
    "/run/flannel",
    "/var/lib/calico",
    "/var/lib/etcd",
    "/var/lib/longhorn",
    "/var/lib/rancher/longhorn"
]

INTERFACES_TO_DELETE = ["flannel.1", "cni0", "cni1", "kube-ipvs0"]

# --- UTILIDADES ---
def run_cmd(cmd, ignore_errors=True):
    print(f">>> Ejecutando: {cmd}")
    try:
        subprocess.run(cmd, shell=True, check=not ignore_errors, capture_output=True)
    except subprocess.CalledProcessError as e:
        if not ignore_errors:
            print(f"Error fatal: {e}")
            sys.exit(1)

def remove_path(path):
    if os.path.exists(path):
        print(f"--- Eliminando: {path}")
        try:
            if os.path.isfile(path) or os.path.islink(path):
                os.unlink(path)
            else:
                shutil.rmtree(path)
        except Exception as e:
            print(f"No se pudo eliminar {path}: {e}")

def unmount_k8s_volumes():
    print("--- Desmontando volúmenes de Kubernetes persistentes...")
    # Buscamos montajes activos en rutas de kubelet o longhorn
    with open('/proc/mounts', 'r') as f:
        mounts = [line.split()[1] for line in f if '/var/lib/kubelet' in line or '/var/lib/longhorn' in line]
    
    # Desmontar en orden inverso (más profundo primero)
    for mount in sorted(mounts, reverse=True):
        run_cmd(f"umount -f {mount}")

# --- FLUJO PRINCIPAL ---
def main():
    if os.geteuid() != 0:
        print("Este script debe ejecutarse como ROOT (sudo).")
        sys.exit(1)

    print("="*55)
    print("  RESET NODE PRO - RKE2 + LONGHORN + SYSPREP (PYTHON)")
    print("="*55)
    print("Peligro: Se borrarán todos los datos de K8s y Longhorn.")
    
    confirm = input(f"Escribe '{CONFIRM_WORD}' para continuar: ")
    if confirm != CONFIRM_WORD:
        print("Cancelado.")
        sys.exit(0)

    # 1. Detener servicios
    print("\n[1] Deteniendo servicios...")
    for svc in ["rke2-server", "rke2-agent"]:
        run_cmd(f"systemctl stop {svc}")
        run_cmd(f"systemctl disable {svc}")

    # 2. Matar procesos residuales
    print("\n[2] Matando procesos residuales (containerd/shim/kubelet)...")
    run_cmd("pkill -9 -f 'containerd-shim|kubelet|etcd'")

    # 3. Limpiar montajes (Crucial para no dejar el sistema inestable)
    unmount_k8s_volumes()

    # 4. Eliminar directorios
    print("\n[3] Eliminando datos y configuraciones...")
    for path in DIRECTORIES_TO_REMOVE:
        remove_path(path)
    
    # Limpiar binarios
    for binary in glob.glob("/usr/local/bin/rke2*"):
        remove_path(binary)

    # 5. Red e Iptables
    print("\n[4] Limpiando red e Iptables...")
    for iface in INTERFACES_TO_DELETE:
        run_cmd(f"ip link delete {iface}")
    
    run_cmd("iptables -F && iptables -X && iptables -t nat -F && iptables -t nat -X")
    run_cmd("iptables -t mangle -F && iptables -t mangle -X")

    # 6. Sysprep Ubuntu
    print("\n[5] Aplicando Sysprep...")
    
    # Machine ID y Hostname
    with open('/etc/hostname', 'w') as f: f.write('localhost')
    run_cmd("truncate -s 0 /etc/machine-id")
    remove_path("/var/lib/dbus/machine-id")
    
    # Claves SSH
    for key in glob.glob("/etc/ssh/ssh_host_*"):
        remove_path(key)

    # Logs
    log_files = ["/var/log/wtmp", "/var/log/btmp", "/var/log/lastlog", "/var/log/auth.log"]
    for log in log_files:
        if os.path.exists(log):
            run_cmd(f"truncate -s 0 {log}")
    
    # Borrar todos los .log recursivamente en /var/log
    run_cmd("find /var/log -type f -name '*.log' -delete")

    # Histories
    run_cmd("find /home -name '.bash_history' -delete")
    remove_path("/root/.bash_history")

    # 7. Finalización
    run_cmd("apt clean")
    run_cmd("sync")
    
    print("\n" + "✅"*20)
    print("NODO RESETEADO EXITOSAMENTE.")
    print("Recomendación: Ejecuta 'reboot' ahora.")

if __name__ == "__main__":
    main()
