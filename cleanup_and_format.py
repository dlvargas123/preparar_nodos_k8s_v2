import subprocess
import os
import sys

def run_command(command):
    """Ejecuta un comando de shell y maneja errores."""
    try:
        print(f"Executing: {command}")
        # Usamos shell=True para comandos con pipes o redirecciones
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {e.stderr}")
        # No detenemos el script si el volumen no existe (limpieza previa)
        if "not found" in e.stderr or "No such file" in e.stderr:
            return
        # sys.exit(1) # Descomentar si quieres que pare en cualquier error real

def format_disk():
    # 1. Asegurarse de ser root
    if os.geteuid() != 0:
        print("Este script debe ejecutarse como root (sudo).")
        sys.exit(1)

    print("--- Iniciando limpieza de LVM ---")
    
    # Eliminar Logical Volume (-f para forzar y no pedir confirmación)
    run_command("lvremove -f /dev/vg_longhorn/lv_longhorn")
    
    # Eliminar Volume Group
    run_command("vgremove -f vg_longhorn")
    
    # Eliminar Physical Volume
    run_command("pvremove -f /dev/sdb1")

    print("--- Limpiando firmas y tablas de particiones ---")
    
    # Limpiar firmas de la partición y del disco físico
    run_command("wipefs -a /dev/sdb1")
    run_command("wipefs -a /dev/sdb")

    print("--- Formateando disco sdb ---")
    
    # Formatear el disco completo como ext4 (ideal para Longhorn si se va a montar)
    # Usamos -F para forzar el formateo si detecta tablas previas
    run_command("mkfs.ext4 -F /dev/sdb")

    print("--- Verificando estado final ---")
    run_command("lsblk /dev/sdb")

    print("\n[OK] El disco /dev/sdb está limpio y formateado como ext4.")

if __name__ == "__main__":
    format_disk()
