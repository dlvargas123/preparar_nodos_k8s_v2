import os
import stat

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

# Inicializamos el reporte
report = {
    "found": [],
    "not_found": [],
    "changed": []
}

# Función para ajustar permisos y registrar evidencia
def adjust_permissions(path, perms):
    # Comprobamos si el archivo o directorio existe
    if os.path.exists(path):
        # Registro en "found" si existe
        report["found"].append(path)

        # Convertimos los permisos a entero
        perm_int = int(perms, 8)

        # Ajustamos los permisos
        current_permissions = oct(os.stat(path).st_mode)[-3:]
        if current_permissions != perms:
            os.chmod(path, perm_int)
            # Registramos el cambio
            report["changed"].append((path, current_permissions, perms))

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
        # Registro en "not_found" si no existe
        report["not_found"].append(path)
        print(f"El archivo/directorio {path} no existe. Saltando...")

# Validamos y ajustamos los permisos para cada archivo/directorio en la lista
for path, perms in paths.items():
    adjust_permissions(path, perms)

# Generar el reporte final
with open("/tmp/reporte_ajustes_permisos.txt", "w") as f:
    f.write("### Reporte de Archivos Encontrados ###\n")
    for item in report["found"]:
        f.write(f"Encontrado: {item}\n")
    
    f.write("\n### Archivos No Encontrados ###\n")
    for item in report["not_found"]:
        f.write(f"No encontrado: {item}\n")
    
    f.write("\n### Cambios Realizados ###\n")
    for item in report["changed"]:
        f.write(f"Archivo: {item[0]}, Permisos Anteriores: {item[1]}, Nuevos Permisos: {item[2]}\n")

# Mostrar el contenido del reporte final
print("\nReporte final generado en /tmp/reporte_ajustes_permisos.txt")
with open("/tmp/reporte_ajustes_permisos.txt", "r") as f:
    print(f.read())

print("Proceso de ajuste de permisos finalizado.")
