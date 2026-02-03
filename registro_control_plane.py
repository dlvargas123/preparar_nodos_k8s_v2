#!/usr/bin/env python3
import subprocess
import sys

# =========================
# Nodos destino (Control Plane + ETCD)
# =========================
NODES = ["10.0.0.11", "10.0.0.12", "10.0.0.13"]

# =========================
# SSH runner (salida liviana)
# =========================
def run_ssh_cmd(node, cmd, label=None):
    tag = f"{label} " if label else ""
    print(f"➡️  {tag}{node} ...", end=" ", flush=True)
    try:
        subprocess.run(
            ["ssh", f"root@{node}", cmd],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        print("✅")
        return True
    except subprocess.CalledProcessError as e:
        print("❌")
        err = (e.stderr or "").strip()
        if err:
            print(f"   🔻 {node}: {err}")
        return False

# =========================
# Validar conectividad SSH
# =========================
def validate_ssh_connections(nodes):
    print("\n🟣 Verificando conectividad SSH...\n")
    all_ok = True
    for node in nodes:
        ok = run_ssh_cmd(node, "echo OK", label="SSH")
        if not ok:
            all_ok = False
    return all_ok

# =========================
# PROGRAMA PRINCIPAL
# =========================
print("\n📌 Instalador de nodos (modo LITE) — Control Plane + ETCD\n")

# 1) Validar SSH
if not validate_ssh_connections(NODES):
    print("\n❌ ERROR: No todos los nodos tienen SSH accesible. Abortando.\n")
    sys.exit(1)

print("\n✅ SSH OK en todos los nodos.\n")

# 2) Pedir Registration Command (texto ajustado)
registration_command = input(
    "🟣 Ingresa tu Registration Command_cluster_k8s EXACTO para Control Plane + ETCD (pégalo completo):\n\n> "
).strip()

if not registration_command:
    print("\n❌ ERROR: No ingresaste ningún comando. Abortando.\n")
    sys.exit(1)

# 3) Ejecutar registration command
print("\n🟣 Ejecutando Registration Command en nodos...\n")
success_registration = True
for node in NODES:
    if not run_ssh_cmd(node, registration_command, label="REG"):
        success_registration = False

if not success_registration:
    print("\n⚠️  Hubo errores registrando uno o más nodos. Abortando.\n")
    sys.exit(1)

# 4) Mensaje final y finalizar (sin esperas ni copias)
print("\n✅ Registro de Control Plane + ETCD completado con éxito.\n")
print("➡️  Por favor continúe con el registro de los Worker Nodes.\n")
sys.exit(0)
