import os
import stat
import subprocess

# Lista de archivos críticos de Kubernetes
files = [
    "/etc/kubernetes/manifests/kube-apiserver.yaml",
    "/etc/kubernetes/manifests/kube-controller-manager.yaml",
    "/etc/kubernetes/manifests/kube-scheduler.yaml",
    "/etc/kubernetes/manifests/etcd.yaml",
    "/etc/cni/net.d/",
    "/var/lib/etcd/default.etcd",
    "/etc/kubernetes/admin.conf",
    "/etc/kubernetes/scheduler.conf",
    "/etc/kubernetes/controller-manager.conf",
    "/etc/kubernetes/pki/"
]

# Función para obtener los permisos de los archivos
def get_file_permissions(file_path):
    try:
        file_stats = os.stat(file_path)
        permissions = stat.filemode(file_stats.st_mode)
        owner = file_stats.st_uid
        group = file_stats.st_gid
        return permissions, owner, group
    except FileNotFoundError:
        return "Archivo no encontrado", "-", "-"

# Generar el reporte
def generate_report():
    report = "Reporte de permisos de archivos críticos de Kubernetes:\n\n"
    
    for file in files:
        permissions, owner, group = get_file_permissions(file)
        report += f"Archivo: {file}\n"
        report += f"Permisos: {permissions}\n"
        report += f"Propietario (UID): {owner}\n"
        report += f"Grupo (GID): {group}\n\n"
    
    # Guardar el reporte en un archivo
    with open("/tmp/reporte_permisos_k8s.txt", "w") as f:
        f.write(report)
    
    print("Reporte generado exitosamente en /tmp/reporte_permisos_k8s.txt")

# Llamar a la función para generar el reporte
generate_report()
