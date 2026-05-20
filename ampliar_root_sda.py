#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import time


DISK = "/dev/sda"
PART_NUM = "3"
PARTITION = "/dev/sda3"
LV_PATH = "/dev/ubuntu-vg/ubuntu-lv"
MOUNTPOINT = "/"
RESCAN_PATH = "/sys/class/block/sda/device/rescan"


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
        print("  sudo ./ampliar_root_sda.py")
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
    ]

    missing = [cmd for cmd in required if not exists(cmd)]

    if missing:
        print(f"ERROR: faltan comandos requeridos: {', '.join(missing)}")
        sys.exit(1)

    if not exists("growpart"):
        print("AVISO: growpart no está instalado.")
        print("Como /dev/sda3 ya mide 96.9G, probablemente no hace falta.")
        print("Si quieres instalarlo:")
        print("  apt update && apt install -y cloud-guest-utils")


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


def rescan_disk():
    print(f"\n== Reescaneando {DISK} ==")

    if os.path.exists(RESCAN_PATH):
        with open(RESCAN_PATH, "w") as f:
            f.write("1\n")
    else:
        print(f"AVISO: no existe {RESCAN_PATH}")

    time.sleep(2)
    run(["lsblk", DISK], check=False)


def grow_partition_if_possible():
    print(f"\n== Intentando ampliar {PARTITION} al máximo de {DISK} ==")

    if exists("growpart"):
        result = run(["growpart", DISK, PART_NUM], check=False, capture=True)

        if result.returncode != 0:
            print("growpart no aplicó cambios. Puede que /dev/sda3 ya esté al máximo.")
    else:
        print("Saltando growpart porque no está instalado.")

    if exists("partprobe"):
        run(["partprobe", DISK], check=False)

    if exists("partx"):
        run(["partx", "-u", DISK], check=False)

    if exists("udevadm"):
        run(["udevadm", "settle"], check=False)

    time.sleep(2)
    run(["lsblk", DISK], check=False)


def grow_root():
    print(f"\n== Redimensionando PV {PARTITION} ==")
    run(["pvresize", PARTITION], check=True)

    print(f"\n== Ampliando LV root al máximo disponible ==")
    run(["lvextend", "-r", "-l", "+100%FREE", LV_PATH], check=True)


def main():
    require_root()
    require_commands()
    validate_paths()

    show_status("ESTADO INICIAL")

    print("\nEste script ampliará el root usando:")
    print(f"  Disco:      {DISK}")
    print(f"  Partición:  {PARTITION}")
    print(f"  LV:         {LV_PATH}")
    print(f"  Mountpoint: {MOUNTPOINT}")
    print("")
    print("Resultado esperado aproximado:")
    print("  / debería pasar de 48G a cerca de 96G.")

    confirm = input("\nEscribe SI para continuar: ").strip()

    if confirm != "SI":
        print("Cancelado.")
        sys.exit(1)

    rescan_disk()
    grow_partition_if_possible()
    grow_root()

    show_status("ESTADO FINAL")

    print("\nProceso finalizado.")


if __name__ == "__main__":
    main()
