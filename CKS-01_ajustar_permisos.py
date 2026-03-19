import os
import stat

# Lista de archivos y directorios con sus permisos deseados
# Nota: En Python, los permisos se manejan mejor en base 8 (octal)
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

evidence_file = "/tmp/permisos_evidencia.txt"

def adjust_permissions(path, perms):
    if os.path.exists(path):
        print(f"El archivo/directorio {path} existe. Ajustando permisos a {perms}...")
        try:
            # Convertimos el string '644' a su valor octal real
            os.chmod(path, int(perms, 8))
            
            # Obtener info para la evidencia (similar a 'stat')
            file_stat = os.stat(path)
            mode = oct(file_stat.st_mode & 0o777)
            
            with open(evidence_file, "a") as f:
                f.write(f"Path: {path} | Permisos: {mode} | UID: {file_stat.st_uid} | GID: {file_stat.st_gid}\n")
            
            print(f"Permisos ajustados y evidencia registrada para {path}.")
        except PermissionError:
            print(f"Error: No tienes permisos suficientes para modificar {path}.")
        except Exception as e:
            print(f"Ocurrió un error con {path}: {e}")
    else:
        print(f"El archivo/directorio {path} no existe. Saltando...")

def main():
    # Limpiar archivo de evidencia anterior si existe
    if os.path.exists(evidence_file):
        os.remove(evidence_file)

    for path, perms in paths.items():
        adjust_permissions(path, perms)

    print("\nEvidencia de los permisos ajustados:")
    if os.path.exists(evidence_file):
        with open(evidence_file, "r") as f:
            print(f.read())
    else:
        print("No se generó evidencia (posiblemente no existían los archivos).")

    print("Proceso de ajuste de permisos finalizado.")

if __name__ == "__main__":
    main()
