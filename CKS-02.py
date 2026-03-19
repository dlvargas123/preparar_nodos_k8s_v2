import os
import pwd
import grp
import subprocess
from datetime import datetime

# Mapeo de rutas solicitado vs rutas reales en RKE2
targets = {
    # Rutas solicitadas (Legacy/Vanilla)
    "/etc/kubernetes/manifests/kube-apiserver.yaml": "root:root",
    "/etc/kubernetes/controller-manager.conf": "root:root",
    "/etc/kubernetes/scheduler.conf": "root:root",
    "/etc/kubernetes/admin.conf": "root:root",
    "/var/lib/etcd/default.etcd": "etcd:etcd",
    "/etc/cni/net.d/": "root:root",
    "/etc/kubernetes/manifests/etcd.yaml": "root:root",
    "/etc/kubernetes/manifests/kube-scheduler.yaml": "root:root",
    "/etc/kubernetes/manifests/kube-controller-manager.yaml": "root:root",
    
    # Rutas REALES en RKE2 (Equivalentes)
    "/var/lib/rancher/rke2/server/manifests": "root:root",
    "/var/lib/rancher/rke2/server/db/etcd": "etcd:etcd", # Nota: Solo si el usuario etcd existe
    "/var/lib/rancher/rke2/agent/kubeconfig": "root:root"
}

REPORT_FILE = f"evidencia_rke2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def get_id(name, type="user"):
    try:
        return pwd.getpwnam(name).pw_uid if type == "user" else grp.getgrnam(name).gr_gid
    except KeyError:
        return None

def check_acls(path):
    try:
        result = subprocess.run(['getfacl', '-p', path], capture_output=True, text=True)
        if "+" in result.stdout:
            return "ALERTA: ACLs adicionales detectadas."
        return "LIMPIO: Sin ACLs adicionales."
    except FileNotFoundError:
        return "INFO: getfacl no instalado."

def run_hardening():
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE AUDITORÍA Y HARDENING RKE2 - {datetime.now()}\n")
        report.write("="*60 + "\n")

        for path, owner_group in targets.items():
            if not os.path.exists(path):
                msg = f"[NO APLICA] {path} - El archivo no existe en la arquitectura RKE2."
                print(msg)
                report.write(msg + "\n")
                continue

            u_name, g_name = owner_group.split(":")
            uid = get_id(u_name, "user")
            gid = get_id(g_name, "group")

            if uid is not None and gid is not None:
                try:
                    # Cambio de propiedad
                    os.chown(path, uid, gid)
                    acl_status = check_acls(path)
                    res = f"[APLICADO] {path} -> {owner_group} | ACLs: {acl_status}"
                    print(res)
                    report.write(res + "\n")
                except Exception as e:
                    report.write(f"[ERROR] No se pudo modificar {path}: {e}\n")
            else:
                report.write(f"[SKIPPED] Usuario o grupo {owner_group} no existe en el OS.\n")

    print(f"\nProceso finalizado. Reporte generado en: {REPORT_FILE}")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("ERROR: Debe ejecutar como root.")
    else:
        run_hardening()
