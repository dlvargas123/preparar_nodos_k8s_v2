#!/usr/bin/env python3
import base64
import subprocess
import sys
import os
from pathlib import Path

class Cores:
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def run_command(cmd, input_data=None, allow_fail=False):
    try:
        result = subprocess.run(cmd, input=input_data, text=True, capture_output=True, check=not allow_fail)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        if not allow_fail:
            print(f"{Cores.FAIL}❌ Error Crítico: {e.stderr}{Cores.ENDC}")
            sys.exit(1)
        return None

def setup_custom_named_access():
    os.system('clear')
    print(f"{Cores.OKBLUE}{Cores.BOLD}=== IFX CUSTOM RBAC GENERATOR ==={Cores.ENDC}")

    # 1. Solicitar nombre personalizado
    custom_name = input(f"\n{Cores.BOLD}Escribe el nombre para el ServiceAccount y las Reglas:{Cores.ENDC} ").strip().lower()
    if not custom_name:
        custom_name = "ifx-custom-dev"

    # Limpiar espacios por guiones si el usuario se equivoca
    custom_name = custom_name.replace(" ", "-")

    # 2. Obtener Namespaces
    all_ns = run_command(["kubectl", "get", "namespaces", "-o", "jsonpath={.items[*].metadata.name}"]).split()

    print(f"\nSelecciona los namespaces a {Cores.BOLD}BLOQUEAR{Cores.ENDC}:")
    for i, ns in enumerate(all_ns, 1):
        print(f"{i:2}) {ns}")

    choices = input(f"\n{Cores.WARNING}Números a BLOQUEAR (separa por comas): {Cores.ENDC}").split(',')
    blacklist = []
    try:
        for c in choices:
            if c.strip():
                idx = int(c.strip()) - 1
                if 0 <= idx < len(all_ns): blacklist.append(all_ns[idx])
    except: pass

    main_ns = "default"
    binding_name = f"{custom_name}-admin-binding"

    # 3. CONFIGURACIÓN GLOBAL (Discovery)
    global_manifest = f"""
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {custom_name}
  namespace: {main_ns}
---
apiVersion: v1
kind: Secret
metadata:
  name: {custom_name}-token
  namespace: {main_ns}
  annotations:
    kubernetes.io/service-account.name: {custom_name}
type: kubernetes.io/service-account-token
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: {custom_name}-minimal-discovery
rules:
- apiGroups: [""]
  resources: ["namespaces", "nodes"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: {custom_name}-minimal-discovery-binding
subjects:
- kind: ServiceAccount
  name: {custom_name}
  namespace: {main_ns}
roleRef:
  kind: ClusterRole
  name: {custom_name}-minimal-discovery
  apiGroup: rbac.authorization.k8s.io
"""
    print(f"\n{Cores.OKBLUE}⚙️ Creando Identidad '{custom_name}' y base de seguridad...{Cores.ENDC}")
    run_command(["kubectl", "apply", "-f", "-"], input_data=global_manifest)

    # 4. APLICACIÓN SELECTIVA Y LIMPIEZA
    for ns in all_ns:
        if ns in blacklist:
            # Limpiamos cualquier binding que use este nombre personalizado
            run_command(["kubectl", "delete", "rolebinding", binding_name, "-n", ns], allow_fail=True)
            print(f"{Cores.FAIL}🔒 BLOQUEADO Y LIMPIO: {ns}{Cores.ENDC}")
            continue

        rbac = f"""
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: {binding_name}
  namespace: {ns}
subjects:
- kind: ServiceAccount
  name: {custom_name}
  namespace: {main_ns}
roleRef:
  kind: ClusterRole
  name: admin
  apiGroup: rbac.authorization.k8s.io
"""
        run_command(["kubectl", "apply", "-f", "-"], input_data=rbac)
        print(f"{Cores.OKGREEN}🔑 FULL ACCESS: {ns}{Cores.ENDC}")

    # 5. GENERACIÓN DE KUBECONFIG
    server = run_command(["kubectl", "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"])
    ca_data = run_command(["kubectl", "config", "view", "--minify", "--raw", "-o", "jsonpath={.clusters[0].cluster.certificate-authority-data}"])
    token_b64 = run_command(["kubectl", "-n", main_ns, "get", "secret", f"{custom_name}-token", "-o", "jsonpath={.data.token}"])
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
- name: {custom_name}-context
  context:
    cluster: ifx-cloud
    user: {custom_name}
current-context: {custom_name}-context
users:
- name: {custom_name}
  user:
    token: {token}
"""
    file_name = f"access-{custom_name}.yaml"
    Path(file_name).write_text(kubeconfig.strip())
    os.chmod(file_name, 0o600)

    print(f"\n{Cores.OKGREEN}{Cores.BOLD}🚀 LISTO: Se creó el archivo '{file_name}'{Cores.ENDC}")

if __name__ == "__main__":
    setup_custom_named_access()
