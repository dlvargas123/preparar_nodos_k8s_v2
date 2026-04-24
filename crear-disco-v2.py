#!/usr/bin/env python3

import subprocess
import os
import time
import shutil
import sys

# ==========================
# CONFIGURACIÓN AUTOMÁTICA
# ==========================

DISK_INDEX = int(os.getenv("DISK_INDEX", "2"))  # Disco número 2 por defecto
VG_NAME = os.getenv("VG_NAME", "vg_longhorn")
LV_NAME = os.getenv("LV_NAME", "lv_longhorn")
MOUNT_POINT = os.getenv("MOUNT_POINT", "/var/lib/longhorn")

# Seguridad mínima: deja esto en True porque el script borra el disco seleccionado
AUTO_CONFIRM_DESTROY = True


def run_cmd(cmd, capture_output=False, allow_error=False):
    try:
        if capture_output:
            return subprocess.check_output(cmd, text=True).strip()
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        if allow_error:
            return None

        print(f"\n❌ Error ejecutando: {' '.join(cmd)}")
        print(e)
        sys.exit(1)


def require_root():
    if os.geteuid() != 0:
        print("❌ Este script debe ejecutarse como root.")
        sys.exit(1)


def require_commands():
    required = [
        "lsblk",
        "parted",
        "partprobe",
        "pvcreate",
        "vgcreate",
        "lvcreate",
        "mkfs.xfs",
        "blkid",
        "mount",
        "findmnt",
        "wipefs",
        "udevadm",
    ]

    missing = []

    for cmd in required:
        if shutil.which(cmd) is None:
            missing.append(cmd)

    if missing:
        print("❌ Faltan comandos requeridos:")
        for cmd in missing:
            print(f"   - {cmd}")

        print("\nEn Debian/Ubuntu puedes instalar dependencias con:")
        print("apt update && apt install -y lvm2 xfsprogs parted util-linux")
        sys.exit(1)


def listar_dispositivos():
    print("📦 Discos disponibles:\n")

    output = run_cmd(
        ["lsblk", "-d", "-e", "7,11", "-o", "NAME,SIZE,TYPE"],
        capture_output=True
    )

    lines = output.splitlines()
    devices = []

    for i, line in enumerate(lines[1:], 1):
        parts = line.split()

        if len(parts) >= 3 and parts[-1] == "disk":
            name = parts[0]
            size = parts[1]
            disk = f"/dev/{name}"
            devices.append(disk)
            print(f"{i}. {disk} ({size}) - disk")

    return devices


def get_partition_name(disk):
    if "nvme" in disk or "mmcblk" in disk:
        return disk + "p1"

    return disk + "1"


def get_root_disk():
    root_source = run_cmd(
        ["findmnt", "-n", "-o", "SOURCE", "/"],
        capture_output=True
    )

    pkname = run_cmd(
        ["lsblk", "-no", "PKNAME", root_source],
        capture_output=True,
        allow_error=True
    )

    if pkname:
        return f"/dev/{pkname}"

    return root_source


def wait_for_partition(partition, timeout=20):
    print(f"⏳ Esperando a que aparezca {partition}...")

    for _ in range(timeout):
        if os.path.exists(partition):
            return

        time.sleep(1)

    print(f"❌ No apareció la partición {partition}.")
    sys.exit(1)


def command_exists_success(cmd):
    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    return result.returncode == 0


def validate_not_existing_lvm():
    if command_exists_success(["vgs", VG_NAME]):
        print(f"❌ Ya existe el Volume Group '{VG_NAME}'.")
        print("El script se detiene para evitar sobrescribir configuración existente.")
        sys.exit(1)

    lv_path = f"/dev/{VG_NAME}/{LV_NAME}"

    if os.path.exists(lv_path):
        print(f"❌ Ya existe el Logical Volume '{lv_path}'.")
        sys.exit(1)


def backup_fstab():
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = f"/etc/fstab.backup-{timestamp}"

    shutil.copy2("/etc/fstab", backup_path)
    print(f"🧾 Backup de /etc/fstab creado en: {backup_path}")


def add_fstab_entry(uuid):
    fstab_line = f"UUID={uuid} {MOUNT_POINT} xfs defaults,noatime 0 0\n"

    with open("/etc/fstab", "r") as fstab:
        content = fstab.read()

    if uuid in content or MOUNT_POINT in content:
        print("⚠️ Ya existe una entrada relacionada en /etc/fstab. No se duplicará.")
        return

    with open("/etc/fstab", "a") as fstab:
        fstab.write(fstab_line)

    print("✅ Entrada agregada a /etc/fstab.")


def main():
    require_root()
    require_commands()

    print("🚀 Creación automática de volumen LVM para Longhorn con XFS\n")

    if not AUTO_CONFIRM_DESTROY:
        print("❌ AUTO_CONFIRM_DESTROY está desactivado.")
        sys.exit(1)

    dispositivos = listar_dispositivos()

    if len(dispositivos) < DISK_INDEX:
        print(f"\n❌ No existe el disco número {DISK_INDEX}.")
        sys.exit(1)

    disk = dispositivos[DISK_INDEX - 1]
    partition = get_partition_name(disk)
    root_disk = get_root_disk()

    print(f"\n🎯 Disco seleccionado automáticamente: {disk}")
    print(f"🧠 Disco raíz detectado: {root_disk}")

    if disk == root_disk:
        print("\n❌ El disco seleccionado parece ser el disco del sistema.")
        print("El script se detiene para evitar borrar el sistema operativo.")
        sys.exit(1)

    validate_not_existing_lvm()

    print("\n⚠️ ADVERTENCIA:")
    print(f"Se eliminará TODO el contenido de {disk}.")
    print("El proceso continuará automáticamente en 5 segundos...")
    time.sleep(5)

    print(f"\n🧹 Limpiando firmas anteriores en {disk}...")
    run_cmd(["wipefs", "-a", disk])

    print(f"🧱 Creando tabla de particiones GPT en {disk}...")
    run_cmd(["parted", "-s", disk, "mklabel", "gpt"])

    print("📐 Creando partición primaria usando todo el disco...")
    run_cmd(["parted", "-s", disk, "mkpart", "primary", "0%", "100%"])

    print("🔄 Recargando tabla de particiones...")
    run_cmd(["partprobe", disk])
    run_cmd(["udevadm", "settle"])

    wait_for_partition(partition)

    print(f"💾 Creando volumen físico en {partition}...")
    run_cmd(["pvcreate", "-y", partition])

    print(f"📦 Creando grupo de volúmenes '{VG_NAME}'...")
    run_cmd(["vgcreate", VG_NAME, partition])

    print(f"📁 Creando volumen lógico '{LV_NAME}' con todo el espacio disponible...")
    run_cmd(["lvcreate", "-y", "-n", LV_NAME, "-l", "100%FREE", VG_NAME])

    lv_path = f"/dev/{VG_NAME}/{LV_NAME}"

    print("🧱 Formateando volumen como XFS...")
    run_cmd(["mkfs.xfs", "-f", lv_path])

    print(f"📁 Creando carpeta {MOUNT_POINT}...")
    os.makedirs(MOUNT_POINT, exist_ok=True)

    print(f"📌 Montando volumen en {MOUNT_POINT}...")
    run_cmd(["mount", lv_path, MOUNT_POINT])

    print("📝 Configurando /etc/fstab...")
    backup_fstab()

    uuid = run_cmd(
        ["blkid", "-s", "UUID", "-o", "value", lv_path],
        capture_output=True
    )

    add_fstab_entry(uuid)

    print("\n🧪 Probando configuración de montaje con mount -a...")
    run_cmd(["mount", "-a"])

    print("\n✅ Proceso completado correctamente.")
    print("\n📌 Verificación:")
    run_cmd(["df", "-hT", MOUNT_POINT])


if __name__ == "__main__":
    main()
