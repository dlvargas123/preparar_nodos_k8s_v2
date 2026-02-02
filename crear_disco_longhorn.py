import subprocess
import os

def run_cmd(cmd, interactive=False, capture_output=False):
    try:
        if interactive:
            subprocess.run(cmd, shell=True)
        elif capture_output:
            return subprocess.check_output(cmd, shell=True).decode().strip()
        else:
            subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Error ejecutando: {cmd}")
        print(e)
        exit(1)

def listar_dispositivos():
    print("📦 Discos disponibles:\n")
    output = run_cmd("lsblk -d -e 7,11 -o NAME,SIZE,MODEL,TYPE", capture_output=True)
    lines = output.splitlines()
    devices = []
    for i, line in enumerate(lines[1:], 1):  # saltar encabezado
        parts = line.split()
        if len(parts) >= 2:
            name = parts[0]
            size = parts[1]
            model = " ".join(parts[2:-1]) if len(parts) > 3 else parts[2] if len(parts) == 3 else ""
            devices.append(f"/dev/{name}")
            print(f"{i}. /dev/{name} ({size}) - {model}")
    return devices

def main():
    if os.geteuid() != 0:
        print("❌ Este script debe ejecutarse como root.")
        exit(1)

    print("🚀 Asistente para crear Volumen LVM y montar disco con XFS\n")

    dispositivos = listar_dispositivos()
    if not dispositivos:
        print("❌ No se encontraron discos disponibles.")
        return

    seleccion = input("\n👉 Selecciona el número del disco a usar: ").strip()
    try:
        index = int(seleccion) - 1
        if index < 0 or index >= len(dispositivos):
            raise ValueError()
        disk = dispositivos[index]
    except ValueError:
        print("❌ Selección inválida.")
        return

    partition = disk + "1"

    input(f"\nPresiona Enter para abrir el particionador interactivo de {disk}...")
    run_cmd(f"cfdisk {disk}", interactive=True)

    print("\n💾 Creando volumen físico...")
    run_cmd(f"pvcreate {partition}")

    print("📦 Creando grupo de volúmenes 'vg_longhorn'...")
    run_cmd(f"vgcreate vg_longhorn {partition}")

    print("📁 Creando volumen lógico 'lv_longhorn' con todo el espacio disponible...")
    run_cmd("lvcreate -n lv_longhorn -l 100%FREE vg_longhorn")

    print("🧱 Formateando volumen como XFS...")
    run_cmd("mkfs.xfs /dev/vg_longhorn/lv_longhorn")

    print("📁 Creando carpeta /var/lib/longhorn...")
    run_cmd("mkdir -p /var/lib/longhorn")

    print("📌 Montando volumen en /var/lib/longhorn...")
    run_cmd("mount /dev/vg_longhorn/lv_longhorn /var/lib/longhorn")

    print("📝 Agregando entrada en /etc/fstab...")
    uuid = run_cmd("blkid -s UUID -o value /dev/vg_longhorn/lv_longhorn", capture_output=True)
    fstab_line = f"UUID={uuid} /var/lib/longhorn xfs defaults 0 0\n"
    with open("/etc/fstab", "a") as fstab:
        fstab.write(fstab_line)

    print("\n✅ Proceso completado. Verifica con:")
    run_cmd("df -hT | grep longhorn", interactive=True)

if __name__ == "__main__":
    main()
