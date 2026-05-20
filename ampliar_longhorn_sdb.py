#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import time


DISK = "/dev/sdb"
PART_NUM = "1"
PARTITION = "/dev/sdb1"
LV_PATH = "/dev/vg_longhorn/lv_longhorn"
MOUNTPOINT = "/var/lib/longhorn"
RESCAN_PATH = "/sys/class/block/sdb/device/rescan"


def run(cmd, check=True, capture=False):
    print(f"\n$ {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )

    if capture:
        if result.stdout.strip():
            print(result.stdout.strip())
        if result.stderr.strip():
            print(result.stderr.strip())

    if check and result.returncode != 0:
        print(f"\nERROR ejecutando: {' '.join(cmd)}")
        sys.exit(result.returncode)

    return result


def exists(command):
    return shutil.which(command) is not None


def require_root():
    if os.geteuid() != 0:
        print("ERROR: ejecuta como root:")
        print("  sudo ./ampliar_longhorn_sdb.py")
        sys.exit(1)


def require_commands():
    required = [
        "lsblk",
        "df",
        "pvs",
        "vgs",
        "lvs",
        "pvresize",
        "lvextend",
        "xfs_growfs",
        "findmnt",
    ]

    missing = [cmd for cmd in required if not exists(cmd)]

    if missing:
        print(f"ERROR: faltan comandos requeridos: {', '.join(missing)}")
        sys.exit(1)

    if not exists("growpart") and not exists("parted"):
        print("ERROR: falta growpart o parted para crecer la partición.")
        print("")
        print("Recomendado instalar growpart:")
        print("  apt update && apt install -y cloud-guest-utils")
        print("")
        print("O instalar parted:")
        print("  apt update && apt install -y parted")
        sys.exit(1)


def output(cmd):
    result = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if result.returncode != 0:
        return ""

    return result.stdout.strip()


def show_status(title):
    print(f"\n========== {title} ==========")
    run(["lsblk", DISK], check=False)
    run(["df", "-hT", MOUNTPOINT], check=False)
    run(["pvs"], check=False)
    run(["vgs"], check=False)
    run(["lvs"], check=False)


def validate_paths():
    if not os.path.exists(DISK):
        print(f"ERROR: no existe {DISK}")
        sys.exit(1)

    if not os.path.exists(PARTITION):
        print(f"ERROR: no existe {PARTITION}")
        sys.exit(1)

    if not os.path.exists(LV_PATH):
        print(f"ERROR: no existe {LV_PATH}")
        sys.exit(1)

    mounted_source = output(["findmnt", "-no", "SOURCE", MOUNTPOINT])

    if not mounted_source:
        print(f"ERROR: {MOUNTPOINT} no está montado.")
        sys.exit(1)

    print(f"Mount detectado para {MOUNTPOINT}: {mounted_source}")


def rescan_disk():
    print(f"\n== Reescaneando {DISK} ==")

    if os.path.exists(RESCAN_PATH):
        with open(RESCAN_PATH, "w") as f:
            f.write("1\n")
    else:
        print(f"AVISO: no existe {RESCAN_PATH}")

    time.sleep(2)
    run(["lsblk", DISK], check=False)


def reread_partition_table():
    print("\n== Recargando tabla de particiones ==")

    if exists("partprobe"):
        run(["partprobe", DISK], check=False)

    if exists("partx"):
        run(["partx", "-u", DISK], check=False)

    if exists("udevadm"):
        run(["udevadm", "settle"], check=False)

    time.sleep(2)
    run(["lsblk", DISK], check=False)


def grow_partition():
    print(f"\n== Ampliando partición {PARTITION} al máximo de {DISK} ==")

    if exists("growpart"):
        result = run(["growpart", DISK, PART_NUM], check=False, capture=True)

        if result.returncode != 0:
            print("growpart no pudo ampliar la partición.")
            print("Si dice NOCHANGE, probablemente ya estaba al máximo.")
    else:
        print("growpart no está instalado. Usando parted como alternativa.")
        run(["parted", "-s", DISK, "resizepart", PART_NUM, "100%"], check=True)

    reread_partition_table()


def grow_pv_lv_xfs():
    print(f"\n== Redimensionando PV {PARTITION} ==")
    run(["pvresize", PARTITION], check=True)

    print(f"\n== Extendiendo LV {LV_PATH} al máximo disponible ==")
    run(["lvextend", "-l", "+100%FREE", LV_PATH], check=True)

    print(f"\n== Creciendo filesystem XFS en {MOUNTPOINT} ==")
    run(["xfs_growfs", MOUNTPOINT], check=True)


def main():
    require_root()
    require_commands()
    validate_paths()

    show_status("ESTADO INICIAL")

    print("\nEste script ampliará Longhorn usando:")
    print(f"  Disco:      {DISK}")
    print(f"  Partición:  {PARTITION}")
    print(f"  LV:         {LV_PATH}")
    print(f"  Mountpoint: {MOUNTPOINT}")
    print("")
    print("Resultado esperado aproximado:")
    print("  /var/lib/longhorn debería pasar de 110G a cerca de 400G.")

    confirm = input("\nEscribe SI para continuar: ").strip()

    if confirm != "SI":
        print("Cancelado.")
        sys.exit(1)

    rescan_disk()
    grow_partition()
    grow_pv_lv_xfs()

    show_status("ESTADO FINAL")

    print("\nProceso finalizado.")


if __name__ == "__main__":
    main()
