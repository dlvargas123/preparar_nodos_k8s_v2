import os
import stat
import subprocess

# Lista de archivos y directorios con los permisos deseados
paths = {
    "/etc/kubernetes/manifests/kube-apiserver.yaml": "644",
    "/etc/kubernetes/manifests/kube-controller-manager.yaml": "644",
    "/etc/kubernetes/manifests/kube-scheduler.yaml": "644",
    "/etc/kubernetes/manifests/etcd.yaml": "644",
    "/etc/cni/net.d/": "644",
    "/var/lib/etcd/default.etcd": "700",
    "/etc/kubernetes/admin.conf": "644",
    "/etc/kubernetes/scheduler.conf": "644",
    "/etc/kubernetes/controller-manager.conf": "644",
}

# Función para ajustar permisos y registrar evidencia
def adjust_permissions(path, perms):
    # Comprobamos si el archivo o directorio existe
    if os.path.exists(path):
        print(f"El archivo/directorio {path} existe. Ajustando permisos a {perms}...")
        # Convertimos los permisos a entero
        perm_int = int(perms, 8)
        
        # Ajustamos los permisos
        os.chmod(path, perm_int)
        
        # Registramos la evidencia con el comando stat
        stat_info = os.stat(path)
        with open("/tmp/permisos_evidencia.txt", "a") as f:
            f.write(f"Evidencia para {path}:\n")
            f.write(f"  Size: {stat_info.st_size} bytes\n")
            f.write(f"  Permissions: {oct(stat_info.st_mode)[-3:]}\n")
            f.write(f"  Last Accessed: {stat_info.st_atime}\n")
            f.write(f"  Last Modified: {stat_info.st_mtime}\n")
            f.write(f"  Last Status Change: {stat_info.st_ctime}\n\n")
        print(f"Permisos ajustados y evidencia registrada para {path}.")
    else:
        print(f"El archivo/directorio {path} no existe. Saltando...")

# Validamos y ajustamos los permisos para cada archivo/directorio en la lista
for path, perms in paths.items():
    adjust_permissions(path, perms)

# Mostrar el contenido de la evidencia registrada
print("\nEvidencia de los permisos ajustados:")
with open("/tmp/permisos_evidencia.txt", "r") as f:
    print(f.read())

print("Proceso de ajuste de permisos finalizado.")
