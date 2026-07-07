#!/usr/bin/env python3
import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

LOG_FILE = "/var/log/longhorn-lvm-auto.log"

VG_NAME = "vg_longhorn"
LV_NAME = "lv_longhorn"
MOUNT_POINT = "/var/lib/longhorn"
DEFAULT_DISK_INDEX = 2


def log(msg: str):
    Path("/var/log").mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def run_cmd(cmd: str, capture_output: bool = False, check: bool = True) -> str:
    print(f"➡️  {cmd}")
    log(f"CMD: {cmd}")

    proc = subprocess.run(
        cmd,
        shell=True,
        text=True,
        capture_output=True,
    )

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    log(f"RC: {proc.returncode}")
    if stdout:
        log(f"STDOUT:\n{stdout}")
    if stderr:
        log(f"STDERR:\n{stderr}")

    if check and proc.returncode != 0:
        print(f"\n❌ Error ejecutando: {cmd}")
        if stdout:
            print(stdout)
        if stderr:
            print(stderr)
        print(f"\n📁 Revisa el log: {LOG_FILE}")
        sys.exit(1)

    if capture_output:
        return stdout

    return stdout


def exists_cmd(binary: str) -> bool:
    rc = subprocess.run(f"command -v {binary}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    return rc == 0


def install_dependencies():
    print("\n🔧 Instalando dependencias necesarias: lvm2, xfsprogs, parted...")
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    run_cmd("apt-get update")
    run_cmd("apt-get install -y lvm2 xfsprogs parted util-linux")


def listar_dispositivos():
    print("📦 Discos detectados:")
    output = run_cmd("lsblk -d -e 7,11 -n -o NAME,SIZE,MODEL,TYPE", capture_output=True)
    devices = []

    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue

        name = parts[0]
        size = parts[1]
        dev_type = parts[-1]
        model = " ".join(parts[2:-1])

        if dev_type != "disk":
            continue

        device = f"/dev/{name}"
        devices.append(device)
        print(f"  {len(devices)}. {device} ({size}) - {model}")

    return devices


def partition_name(disk: str) -> str:
    # /dev/vdb -> /dev/vdb1, /dev/sdb -> /dev/sdb1, /dev/nvme0n1 -> /dev/nvme0n1p1
    if re.search(r"\d$", disk):
        return f"{disk}p1"
    return f"{disk}1"


def get_root_disk():
    source = run_cmd("findmnt -n -o SOURCE /", capture_output=True, check=False)
    if not source:
        return ""

    source = source.strip()

    # Si / está en /dev/vda1, esto devuelve vda.
    pkname = run_cmd(f"lsblk -no PKNAME {source} 2>/dev/null | head -n 1", capture_output=True, check=False).strip()
    if pkname:
        return f"/dev/{pkname}"

    real_source = run_cmd(f"readlink -f {source} 2>/dev/null || true", capture_output=True, check=False).strip()
    if real_source:
        pkname = run_cmd(f"lsblk -no PKNAME {real_source} 2>/dev/null | head -n 1", capture_output=True, check=False).strip()
        if pkname:
            return f"/dev/{pkname}"

    return source


def disk_has_mounts(disk: str) -> bool:
    output = run_cmd(f"lsblk -nr -o MOUNTPOINTS {disk} 2>/dev/null | grep -v '^$' || true", capture_output=True, check=False)
    return bool(output.strip())


def lvm_exists():
    return Path(f"/dev/{VG_NAME}/{LV_NAME}").exists()


def longhorn_is_mounted():
    rc = subprocess.run(f"mountpoint -q {MOUNT_POINT}", shell=True).returncode
    return rc == 0


def validate_target_disk(disk: str):
    if not Path(disk).exists():
        print(f"❌ El disco seleccionado no existe: {disk}")
        sys.exit(1)

    root_disk = get_root_disk()
    print(f"🔐 Disco raíz detectado: {root_disk or 'No identificado'}")

    if root_disk and disk == root_disk:
        print(f"❌ Protección activa: no se puede usar el disco raíz {disk}")
        sys.exit(1)

    if disk_has_mounts(disk):
        print(f"❌ Protección activa: el disco {disk} tiene particiones montadas.")
        run_cmd(f"lsblk {disk}", check=False)
        sys.exit(1)


def create_partition(disk: str, partition: str):
    print(f"\n🧹 Limpiando firmas y creando partición LVM en {disk}...")

    run_cmd(f"swapoff -a || true", check=False)
    run_cmd(f"wipefs -a {disk}")
    run_cmd(f"parted -s {disk} mklabel gpt")
    run_cmd(f"parted -s {disk} mkpart primary 1MiB 100%")
    run_cmd(f"parted -s {disk} set 1 lvm on")
    run_cmd(f"partprobe {disk} || true", check=False)
    run_cmd("udevadm settle || true", check=False)
    time.sleep(3)

    if not Path(partition).exists():
        run_cmd(f"partprobe {disk} || true", check=False)
        run_cmd("udevadm settle || true", check=False)
        time.sleep(3)

    if not Path(partition).exists():
        print(f"❌ No se creó la partición esperada: {partition}")
        run_cmd(f"lsblk {disk}", check=False)
        sys.exit(1)

    print(f"✅ Partición creada: {partition}")


def create_lvm(partition: str):
    print("\n💾 Creando volumen físico...")
    run_cmd(f"pvcreate -ff -y {partition}")

    print(f"📦 Creando grupo de volúmenes '{VG_NAME}'...")
    run_cmd(f"vgcreate {VG_NAME} {partition}")

    print(f"📁 Creando volumen lógico '{LV_NAME}' con todo el espacio disponible...")
    run_cmd(f"lvcreate -n {LV_NAME} -l 100%FREE {VG_NAME}")


def format_lvm():
    lv_path = f"/dev/{VG_NAME}/{LV_NAME}"
    print("\n🧱 Formateando volumen como XFS...")
    run_cmd(f"mkfs.xfs -f {lv_path}")


def mount_longhorn():
    lv_path = f"/dev/{VG_NAME}/{LV_NAME}"

    print(f"\n📁 Creando carpeta {MOUNT_POINT}...")
    run_cmd(f"mkdir -p {MOUNT_POINT}")

    print(f"📌 Montando volumen en {MOUNT_POINT}...")
    if not longhorn_is_mounted():
        run_cmd(f"mount {lv_path} {MOUNT_POINT}")

    print("📝 Agregando entrada en /etc/fstab...")
    uuid = run_cmd(f"blkid -s UUID -o value {lv_path}", capture_output=True)

    fstab_line = f"UUID={uuid} {MOUNT_POINT} xfs defaults,noatime 0 0"
    fstab_path = Path("/etc/fstab")
    fstab_path.touch(exist_ok=True)
    lines = fstab_path.read_text(encoding="utf-8", errors="ignore").splitlines()

    new_lines = []
    for line in lines:
        clean = line.strip()
        if not clean or clean.startswith("#"):
            new_lines.append(line)
            continue
        if MOUNT_POINT in clean:
            continue
        new_lines.append(line)

    new_lines.append(fstab_line)
    fstab_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    run_cmd("mount -a")


def final_validation():
    print("\n🔎 Validación final:")
    run_cmd(f"lsblk")
    run_cmd(f"vgs")
    run_cmd(f"lvs")
    run_cmd(f"df -hT | grep {MOUNT_POINT}")

    if not longhorn_is_mounted():
        print(f"❌ {MOUNT_POINT} no quedó montado.")
        sys.exit(1)

    print(f"\n✅ Proceso completado correctamente. {MOUNT_POINT} está montado para Longhorn.")
    print(f"📁 Log: {LOG_FILE}")


def main():
    parser = argparse.ArgumentParser(description="Crear LVM XFS para Longhorn de forma no interactiva usando el disco 2 por defecto.")
    parser.add_argument("--disk-index", type=int, default=DEFAULT_DISK_INDEX, help="Índice del disco según lsblk. Por defecto: 2")
    parser.add_argument("--disk", default=None, help="Disco explícito. Ejemplo: /dev/vdb. Si se define, ignora --disk-index")
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("❌ Este script debe ejecutarse como root.")
        sys.exit(1)

    print("🚀 Automatización para crear Volumen LVM Longhorn con XFS")
    print("⚠️ Este script APLICA cambios reales: crea partición, LVM, XFS y monta en /var/lib/longhorn.")
    print("⚠️ Por defecto selecciona el disco número 2 del listado.")
    print("")

    devices = listar_dispositivos()
    if not devices:
        print("❌ No se encontraron discos disponibles.")
        sys.exit(1)

    if args.disk:
        disk = args.disk
    else:
        index = args.disk_index - 1
        if index < 0 or index >= len(devices):
            print(f"❌ No existe el índice de disco {args.disk_index}. Discos detectados: {len(devices)}")
            sys.exit(1)
        disk = devices[index]

    partition = partition_name(disk)

    print("\n============================================================")
    print("🚀 LVM Longhorn no interactivo - APLICANDO CAMBIOS")
    print("============================================================")
    print(f"Disco seleccionado: {disk}")
    print(f"Partición objetivo: {partition}")
    print(f"VG/LV: {VG_NAME}/{LV_NAME}")
    print(f"Mount point: {MOUNT_POINT}")
    print("============================================================")

    if longhorn_is_mounted():
        print(f"✅ {MOUNT_POINT} ya está montado. No se realizan cambios.")
        final_validation()
        return

    install_dependencies()

    if lvm_exists():
        print(f"✅ Ya existe /dev/{VG_NAME}/{LV_NAME}. Se intentará montar sin recrear LVM.")
        mount_longhorn()
        final_validation()
        return

    validate_target_disk(disk)
    create_partition(disk, partition)
    create_lvm(partition)
    format_lvm()
    mount_longhorn()
    final_validation()


if __name__ == "__main__":
    main()
