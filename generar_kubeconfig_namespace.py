#!/usr/bin/env python3
"""
K8S KUBECONFIG GENERATOR PRO - IFX Networks Edition
Autor: Dlvargas
Fecha: 2026
"""

import base64
import subprocess
import sys
import os
from pathlib import Path

# --- CONFIGURACIÓN DE COLORES ANSI ---
class Cores:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

# --- UTILIDADES VISUALES ---
def print_header(text):
    print(f"\n{Cores.OKBLUE}{Cores.BOLD}{'='*65}")
    print(f" {text.center(63)} ")
    print(f"{'='*65}{Cores.ENDC}\n")

def print_step(step_num, text):
    print(f"{Cores.OKCYAN}{Cores.BOLD}[Paso {step_num}] {Cores.ENDC}{text}")

def print_success(text):
    print(f"{Cores.OKGREEN}✅ {text}{Cores.ENDC}")

def print_warning(text):
    print(f"{Cores.WARNING}⚠️ {text}{Cores.ENDC}")

def print_error(text):
    print(f"{Cores.FAIL}❌ {text}{Cores.ENDC}")

def print_info(text):
    print(f"{Cores.OKBLUE}ℹ️ {text}{Cores.ENDC}")

# --- LÓGICA DE COMANDOS ---
def run_command(cmd, input_data=None, silent=False):
    try:
        result = subprocess.run(cmd, input=input_data, text=True, capture_output=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        if not silent:
            print_error(f"Error al ejecutar: {' '.join(cmd)}")
            print(f"    Detalle: {e.stderr}")
        sys.exit(1)

# --- FLUJO PRINCIPAL ---
def get_namespaces():
    print_info("Escaneando clúster en busca de Namespaces...")
    output = run_command(["kubectl", "get", "namespaces", "-o", "jsonpath={.items[*].metadata.name}"])
    namespaces = output.split()

    print(f"\n{Cores.BOLD}Namespaces Disponibles en IFX Cloud:{Cores.ENDC}")
    print("-" * 45)
    for i, ns in enumerate(namespaces, 1):
        if ns in ['kube-system', 'kube-public', 'kube-node-lease', 'default', 'cattle-system']:
            print(f"{Cores.WARNING}{i:2}) {ns:<30} (Infraestructura){Cores.ENDC}")
        else:
            print(f"{Cores.OKBLUE}{i:2}) {ns}{Cores.ENDC}")
    print("-" * 45)

    while True:
        try:
            choice = input(f"\n{Cores.BOLD}Selecciona el número del Namespace: {Cores.ENDC}")
            idx = int(choice)
            if 1 <= idx <= len(namespaces):
                selected = namespaces[idx-1]
                print_success(f"Objetivo fijado: {Cores.BOLD}{selected}{Cores.ENDC}")
                return selected
        except (ValueError, IndexError):
            pass
        print_warning("Selección no válida. Inténtalo de nuevo.")

def get_access_profile():
    print(f"\n{Cores.BOLD}Niveles de Acceso (RBAC Profiles):{Cores.ENDC}")
    print("-" * 65)
    print(f" {Cores.OKGREEN}1) Read-Only{Cores.ENDC}      -> (Visualización total, ideal para auditoría)")
    print(f" {Cores.WARNING}2) Developer{Cores.ENDC}      -> (Gestión de Apps: Deployments, Pods, SVC)")
    print(f" {Cores.FAIL}3) Namespace Admin{Cores.ENDC} -> (Control total sobre el Namespace seleccionado)")
    print("-" * 65)

    while True:
        choice = input(f"\n{Cores.BOLD}Selecciona el perfil de permisos (1/2/3): {Cores.ENDC}")
        if choice == '1': return "view"
        elif choice == '2': return "edit"
        elif choice == '3': return "admin"
        print_warning("Opción inválida.")

def generate_kubeconfig():
    os.system('cls' if os.name == 'nt' else 'clear')

    # BANNER PERSONALIZADO IFX
    print_header("GENERADOR DE CADENAS DE CONEXIÓN KUBERNETES - IFX NETWORKS")

    current_ctx = run_command(["kubectl", "config", "current-context"], silent=True)
    print_info(f"Contexto Administrativo Detectado: {Cores.BOLD}{current_ctx}{Cores.ENDC}\n")

    # Paso 1
    print_step(1, "Identificación del Namespace.")
    target_ns = get_namespaces()

    # Paso 2
    print_step(2, "Asignación de privilegios.")
    profile_name = get_access_profile()

    # Paso 3
    print_step(3, "Inyectando ServiceAccount y Secrets en el clúster...")
    sa_name = f"ifx-{profile_name}-{target_ns}"
    server_url = run_command(["kubectl", "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"])
    ca_data = run_command(["kubectl", "config", "view", "--minify", "--raw", "-o", "jsonpath={.clusters[0].cluster.certificate-authority-data}"])

    manifest = f"""
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {sa_name}
  namespace: {target_ns}
---
apiVersion: v1
kind: Secret
metadata:
  name: {sa_name}-token
  namespace: {target_ns}
  annotations:
    kubernetes.io/service-account.name: {sa_name}
type: kubernetes.io/service-account-token
"""
    run_command(["kubectl", "apply", "-f", "-"], input_data=manifest, silent=True)

    # Paso 4
    print_step(4, "Vinculando políticas RBAC...")
    rbac_manifest = f"""
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: {sa_name}-rb
  namespace: {target_ns}
subjects:
- kind: ServiceAccount
  name: {sa_name}
  namespace: {target_ns}
roleRef:
  kind: ClusterRole
  name: {profile_name}
  apiGroup: rbac.authorization.k8s.io
"""
    run_command(["kubectl", "apply", "-f", "-"], input_data=rbac_manifest, silent=True)

    # Paso 5
    print_step(5, "Compilando archivo Kubeconfig seguro...")
    token_b64 = run_command(["kubectl", "-n", target_ns, "get", "secret", f"{sa_name}-token", "-o", "jsonpath={.data.token}"], silent=True)
    token = base64.b64decode(token_b64).decode("utf-8")

    kubeconfig_content = f"""
apiVersion: v1
kind: Config
clusters:
- name: ifx-cloud-cluster
  cluster:
    certificate-authority-data: {ca_data}
    server: {server_url}
contexts:
- name: ifx-{target_ns}-context
  context:
    cluster: ifx-cloud-cluster
    namespace: {target_ns}
    user: {sa_name}
current-context: ifx-{target_ns}-context
users:
- name: {sa_name}
  user:
    token: {token}
"""
    file_name = f"config-{target_ns}-{profile_name}.yaml"
    Path(file_name).write_text(kubeconfig_content.strip())
    os.chmod(file_name, 0o600)

    print_success(f"Despliegue finalizado. Archivo generado: {Cores.BOLD}{file_name}{Cores.ENDC}")

    # RESUMEN FINAL
    print(f"\n{Cores.OKCYAN}{'='*65}")
    print(f"{Cores.BOLD}  IFX NETWORKS - RESUMEN DE CONEXIÓN{Cores.ENDC}")
    print(f"{'='*65}{Cores.ENDC}")
    print(f"  Namespace:   {target_ns}")
    print(f"  Perfil:      {profile_name.upper()}")
    print(f"  Usuario SA:  {sa_name}")
    print(f"  Comando:     {Cores.OKGREEN}export KUBECONFIG={file_name}{Cores.ENDC}")
    print(f"{Cores.OKCYAN}{'='*65}{Cores.ENDC}")

    print(f"\n{Cores.HEADER}{Cores.BOLD}{Cores.UNDERLINE}By Dlvargas{Cores.ENDC}\n")

if __name__ == "__main__":
    generate_kubeconfig()
