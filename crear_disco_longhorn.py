#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess
import argparse
from shutil import which

# =========================
# CONFIG (tu caso: /dev/vdb)
# =========================
DISK = "/dev/vdb"
VG_NAME = "vg_longhorn"
LV_NAME = "lv_longhorn"
MOUNTPOINT = "/var/lib/longhorn"
FS_TYPE = "xfs"


# ---------- Helpers de salida ----------
GREEN = "\033[92m"
RED   = "\033[91m"
YELL  = "\033[93m"
CYAN  = "\033[96m"
RESET = "\033[0m"

def ok(msg):   print(f"{GREEN}✔ {msg}{RESET}")
def warn(msg): print(f"{YELL}⚠ {msg}{RESET}")
def info(msg): print(f"{CYAN}• {msg}{RESET}")
def err(msg):  print(f"{RED}✘ {msg}{RESET}")


# ---------- Runner ----------
def run_cmd(cmd, capture_output=False, check=True):
    """
    Ejecuta comandos shell.
    - capture_output=True -> retorna stdout (str)
    - check=True -> si falla, aborta
    """
    try:
        if capture_output:
            out = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
            return out.decode().strip()
        else:
            subprocess.run(cmd, shell=True, check=check)
            return ""
    except subprocess.CalledProcessError as e:
        err(f"Error ejecutando: {cmd}")
        if hasattr(e, "output") and e.output:
            print(e.output.decode(errors="ignore"))
        sys.exit(1)


def require_root():
    if os.geteuid() != 0:
        err("Este script debe ejecutarse como root.")
        sys.exit(1)


def require_cmds(cmds):
    missing = [c for c in cmds if which(c) is None]
    if missing:
        err(f"Faltan comandos requeridos: {', '.join(missing)}")
        print("Instálalos y vuelve a intentar (ejemplo Ubuntu/Debian):")
        print("  apt-get update && apt-get install -y lvm2 parted xfsprogs util-linux")
        sys.exit(1)


def partition_path(disk: str, part_num: int = 1) -> str:
    """
    /dev/vdb -> /dev/vdb1
    /dev/nvme0n1 -> /dev/nvme0n1p1
    """
    base = os.path.basename(disk)
    if base.startswith("nvme") or base.startswith("mmcblk"):
        return f"{disk}p{part_num}"
    return f"{disk}{part_num}"


def disk_exists(disk: str) -> bool:
    return os.path.exists(disk)


def list_children_and_mounts(disk: str) -> list:
    """
    Retorna lista de dicts con: name, mountpoint
    para disco y sus particiones.
    """
    # NAME,MOUNTPOINTS en modo "raw"
    out = run_cmd(f"lsblk -nr -o NAME,MOUNTPOINTS {disk}", capture_output=True)
    rows = []
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        name = parts[0]
        mnt = parts[1].strip() if len(parts) > 1 else ""
        rows.append({"name": f"/dev/{name}", "mountpoint": mnt})
    return rows


def has_signatures(disk: str) -> bool:
    """
    Detecta firmas (filesystem/LVM/raid) con wipefs -n (no borra, solo lista).
    """
    out = run_cmd(f"wipefs -n {disk} || true", capture_output=True)
    return bool(out.strip())


def has_partitions(disk: str) -> bool:
    """
    Verifica si el disco ya tiene particiones (p.ej /dev/vdb1).
    """
    out = run_cmd(f"lsblk -nr -o NAME {disk}", capture_output=True)
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    # primera línea es el disco, siguientes son particiones
    return len(lines) > 1


def vg_exists(vg: str) -> bool:
    rc = subprocess.run(f"vgs {vg} >/dev/null 2>&1", shell=True).returncode
    return rc == 0


def lv_exists(vg: str, lv: str) -> bool:
    rc = subprocess.run(f"lvs {vg}/{lv} >/dev/null 2>&1", shell=True).returncode
    return rc == 0


def ensure_not_mounted_anywhere(disk: str):
    rows = list_children_and_mounts(disk)
    mounted = [r for r in rows if r["mountpoint"]]
    if mounted:
        err("Hay montajes activos en el disco/particiones. No puedo continuar:")
        for m in mounted:
            print(f"  - {m['name']} montado en: {m['mountpoint']}")
        sys.exit(1)


def create_gpt_and_partition(disk: str):
    """
    Crea label GPT y una sola partición 1MiB-100%, marcada para LVM.
    """
    info("Creando tabla GPT y partición única (LVM) ...")
    run_cmd(f"parted -s {disk} mklabel gpt")
    run_cmd(f"parted -s -a optimal {disk} mkpart primary 1MiB 100%")
    # Marcar partición 1 como "lvm"
    run_cmd(f"parted -s {disk} set 1 lvm on")
    # Refrescar particiones
    run_cmd(f"partprobe {disk} || true", check=False)
    run_cmd("udevadm settle || true", check=False)


def format_and_mount(lv_path: str, mountpoint: str, force: bool):
    info(f"Formateando {lv_path} como {FS_TYPE} ...")
    mkfs_cmd = f"mkfs.{FS_TYPE} {lv_path}"
    if force and FS_TYPE == "xfs":
        mkfs_cmd = f"mkfs.xfs -f {lv_path}"
    elif force:
        mkfs_cmd = f"mkfs.{FS_TYPE} -f {lv_path}"
    run_cmd(mkfs_cmd)

    info(f"Creando carpeta {mountpoint} ...")
    run_cmd(f"mkdir -p {mountpoint}")

    info(f"Montando {lv_path} en {mountpoint} ...")
    run_cmd(f"mount {lv_path} {mountpoint}")


def upsert_fstab(uuid: str, mountpoint: str, fstype: str):
    """
    Inserta o reemplaza la línea de fstab para el mountpoint.
    """
    new_line = f"UUID={uuid} {mountpoint} {fstype} defaults 0 0\n"
    fstab_path = "/etc/fstab"

    # Leer fstab
    try:
        with open(fstab_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = []

    replaced = False
    out_lines = []
    for line in lines:
        # Si ya hay una línea para ese mountpoint, la reemplazamos
        if line.strip() and not line.lstrip().startswith("#"):
            parts = line.split()
            if len(parts) >= 2 and parts[1] == mountpoint:
                out_lines.append(new_line)
                replaced = True
                continue
        out_lines.append(line)

    if not replaced:
        out_lines.append(new_line)

    with open(fstab_path, "w", encoding="utf-8") as f:
        f.writelines(out_lines)


def main():
    parser = argparse.ArgumentParser(
        description="Asistente NO interactivo: crea partición LVM en /dev/vdb, VG/LV y monta en /var/lib/longhorn."
    )
    parser.add_argument("--force", action="store_true",
                        help="Forzar operación borrando firmas/metadata existentes en el disco (DESTRUCTIVO).")
    args = parser.parse_args()

    require_root()
    require_cmds(["lsblk", "parted", "partprobe", "udevadm", "wipefs", "pvcreate", "vgcreate", "lvcreate", "mkfs.xfs", "blkid", "mount"])

    print("🚀 Asistente NO interactivo para preparar Longhorn (LVM + XFS)\n")
    info(f"Disco objetivo: {DISK}")
    info(f"VG: {VG_NAME} | LV: {LV_NAME} | Mount: {MOUNTPOINT}")

    if not disk_exists(DISK):
        err(f"No existe el disco {DISK}. Verifica con: lsblk")
        sys.exit(1)

    # Evitar sorpresas: no seguir si algo está montado
    ensure_not_mounted_anywhere(DISK)

    # Evitar colisiones con VG/LV existentes
    if vg_exists(VG_NAME) or lv_exists(VG_NAME, LV_NAME):
        err(f"Ya existe VG/LV con el nombre {VG_NAME}/{LV_NAME}. Cambia nombres o elimina el existente.")
        sys.exit(1)

    # Preflight de seguridad
    if has_partitions(DISK) or has_signatures(DISK):
        warn("Detecté que el disco tiene particiones y/o firmas existentes (filesystem/LVM/etc).")
        if not args.force:
            err("No continuaré sin --force para evitar pérdida accidental de datos.")
            print("Si estás seguro, ejecuta:")
            print(f"  python3 {os.path.basename(__file__)} --force")
            sys.exit(1)
        else:
            warn("--force habilitado: se limpiarán firmas/metadata existentes.")
            run_cmd(f"wipefs -a {DISK}")
            # Por si hay metadata LVM vieja en particiones, intentar limpiar PVs del disco:
            # (no falla si no aplica)
            run_cmd(f"pvremove -ff -y {DISK}* >/dev/null 2>&1 || true", check=False)

    # 1) Crear partición
    create_gpt_and_partition(DISK)
    part = partition_path(DISK, 1)
    ok(f"Partición creada: {part}")

    # 2) LVM
    info("Creando volumen físico (PV) ...")
    pv_cmd = f"pvcreate {part}"
    if args.force:
        pv_cmd = f"pvcreate -ff -y {part}"
    run_cmd(pv_cmd)

    info(f"Creando grupo de volúmenes (VG) {VG_NAME} ...")
    run_cmd(f"vgcreate {VG_NAME} {part}")

    info(f"Creando volumen lógico (LV) {LV_NAME} usando 100%FREE ...")
    run_cmd(f"lvcreate -n {LV_NAME} -l 100%FREE {VG_NAME}")

    lv_path = f"/dev/{VG_NAME}/{LV_NAME}"

    # 3) Formatear + montar
    format_and_mount(lv_path, MOUNTPOINT, args.force)

    # 4) fstab por UUID (idempotente)
    info("Registrando montaje en /etc/fstab por UUID ...")
    uuid = run_cmd(f"blkid -s UUID -o value {lv_path}", capture_output=True)
    upsert_fstab(uuid, MOUNTPOINT, FS_TYPE)
    ok("fstab actualizado.")

    print("\n✅ Proceso completado. Validación:")
    run_cmd(f"df -hT | grep -E '({MOUNTPOINT}|Filesystem)'", check=False)

    print("\n📌 Nota: este path lo puedes registrar como disco en Longhorn (Default Data Path o disco adicional).")


if __name__ == "__main__":
    main()
