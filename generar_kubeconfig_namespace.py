#!/usr/bin/env python3
import base64
import subprocess
import sys
import os
from pathlib import Path

# --- UTILIDADES VISUALES ---
class Cores:
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def run_command(cmd, input_data=None):
    try:
        result = subprocess.run(cmd, input=input_data, text=True, capture_output=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"{Cores.FAIL}❌ Error: {e.stderr}{Cores.ENDC}")
        sys.exit(1)

def generate_dynamic_blacklist():
    os.system('clear')
    print(f"{Cores.OKBLUE}{Cores.BOLD}=== IFX CUSTOM BLACKLIST GENERATOR ==={Cores.ENDC}")
    
    # 1. Obtener todos los Namespaces
    output = run_command(["kubectl", "get", "namespaces", "-o", "jsonpath={.items[*].metadata.name}"])
    all_ns = output.split()
    
    print(f"\nSelecciona los namespaces que {Cores.BOLD}NO{Cores.ENDC} tendrán permisos (separa por comas, ej: 1,5,10):")
    for i, ns in enumerate(all_ns, 1):
        print(f"{i:2}) {ns}")
    
    choices = input(f"\n{Cores.WARNING}Números a BLOQUEAR: {Cores.ENDC}").split(',')
    
    blacklist = []
    try:
        for c in choices:
            idx = int(c.strip()) - 1
            if 0 <= idx < len(all_ns):
                blacklist.append(all_ns[idx])
    except ValueError:
        print(f"{Cores.FAIL}Entrada no válida. Usando lista vacía.{Cores.ENDC}")

    # 2. Configuración de Identidad
    sa_name = "ifx-custom-dev"
    main_ns = "default"
    
    print(f"\n{Cores.OKBLUE}ℹ️ Configurando acceso multiespacio...{Cores.ENDC}")
    
    # Crear SA y Token
    sa_manifest = f"""
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {sa_name}
  namespace: {main_ns}
---
apiVersion: v1
kind: Secret
metadata:
  name: {sa_name}-token
  namespace: {main_ns}
  annotations:
    kubernetes.io/service-account.name: {sa_name}
type: kubernetes.io/service-account-token
"""
    run_command(["kubectl", "apply", "-f", "-"], input_data=sa_manifest)

    # 3. Aplicar RoleBindings (Solo a los que NO están en blacklist)
    for ns in all_ns:
        if ns in blacklist:
            # Opcional: Podríamos borrar bindings viejos aquí si existieran
            print(f"{Cores.FAIL}🚫 BLOQUEADO: {ns}{Cores.ENDC}")
            continue
        
        rbac = f"""
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: {sa_name}-binding
  namespace: {ns}
subjects:
- kind: ServiceAccount
  name: {sa_name}
  namespace: {main_ns}
roleRef:
  kind: ClusterRole
  name: edit
  apiGroup: rbac.authorization.k8s.io
"""
        run_command(["kubectl", "apply", "-f", "-"], input_data=rbac)
        print(f"{Cores.OKGREEN}✅ PERMITIDO: {ns}{Cores.ENDC}")

    # 4. Generar Archivo
    server = run_command(["kubectl", "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"])
    ca_data = run_command(["kubectl", "config", "view", "--minify", "--raw", "-o", "jsonpath={.clusters[0].cluster.certificate-authority-data}"])
    token_b64 = run_command(["kubectl", "-n", main_ns, "get", "secret", f"{sa_name}-token", "-o", "jsonpath={.data.token}"])
    token = base64.b64decode(token_b64).decode("utf-8")

    kubeconfig = f"""
apiVersion: v1
kind: Config
clusters:
- name: ifx-cloud
  cluster:
    certificate-authority-data: {ca_data}
    server: {server}
contexts:
- name: ifx-access
  context:
    cluster: ifx-cloud
    user: {sa_name}
current-context: ifx-access
users:
- name: {sa_name}
  user:
    token: {token}
"""
    file_name = "ifx-custom-access.yaml"
    Path(file_name).write_text(kubeconfig.strip())
    os.chmod(file_name, 0o600)
    
    print(f"\n{Cores.OKBLUE}🔥 Archivo '{file_name}' generado con éxito.{Cores.ENDC}")

if __name__ == "__main__":
    generate_dynamic_blacklist()
