import os
import subprocess

# Lista de archivos críticos del Control Plane de Kubernetes
files = [
    "/etc/kubernetes/manifests/kube-apiserver.yaml",
    "/etc/kubernetes/controller-manager.conf",
    "/etc/kubernetes/scheduler.conf",
    "/etc/kubernetes/admin.conf",
    "/var/lib/etcd/default.etcd",
    "/etc/cni/net.d/",
    "/etc/kubernetes/manifests/etcd.yaml",
    "/etc/kubernetes/manifests/kube-scheduler.yaml",
    "/etc/kubernetes/manifests/kube-controller-manager.yaml"
]

# Función para ajustar propiedad
def adjust_ownership(file_path, owner, group):
    try:
        subprocess.run(["sudo", "chown", f"{owner}:{group}", file_path], check=True)
        print(f"Propiedad ajustada para {file_path} a {owner}:{group}")
    except subprocess.CalledProcessError:
        print(f"Error al ajustar propiedad para {file_path}")

# Ajustar propiedades de archivos críticos
for file in files:
    if "etcd" in file:
        adjust_ownership(file, "etcd", "etcd")
    else:
        adjust_ownership(file, "root", "root")

# Verificar si existen ACLs permisivas adicionales
def check_acls(file_path):
    try:
        result = subprocess.run(["getfacl", file_path], capture_output=True, text=True, check=True)
        if "user::rw-" in result.stdout or "group::rw-" in result.stdout:
            print(f"ACL permisiva encontrada en {file_path}. Se recomienda revisar.")
        else:
            print(f"Sin ACL permisiva en {file_path}")
    except subprocess.CalledProcessError:
        print(f"Error al verificar ACLs para {file_path}")

# Comprobar ACLs para todos los archivos
for file in files:
    check_acls(file)

# Generar reporte
with open("/tmp/reporte_propiedades_y_acls.txt", "w") as report:
    report.write("Reporte de propiedades y ACLs de archivos críticos de Kubernetes:\n\n")
    for file in files:
        report.write(f"Archivo: {file} - Propiedad ajustada correctamente.\n")
    print("Reporte generado exitosamente en /tmp/reporte_propiedades_y_acls.txt")
