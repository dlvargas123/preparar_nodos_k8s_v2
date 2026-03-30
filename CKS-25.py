import subprocess
import json
from datetime import datetime

REPORT_FILE = f"auditoria_rbac_sa_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def run_kubectl(args):
    try:
        result = subprocess.run(["kubectl"] + args + ["-o", "json"], capture_output=True, text=True)
        if result.returncode == 0:
            return json.loads(result.stdout)
        return None
    except Exception as e:
        return f"Error: {e}"

def audit_rbac_sa():
    print(f"Iniciando auditoría de RBAC y ServiceAccounts en RKE2...")
    
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE AUDITORÍA RBAC Y SERVICEACCOUNTS - {datetime.now()}\n")
        report.write(f"ENTORNO: Rancher RKE2 v1.32.13\n")
        report.write("="*60 + "\n\n")

        # 1. Inventario de ClusterRoleBindings con cluster-admin (Punto 1 y 2)
        report.write("--- 1. CLUSTERROLEBINDINGS CON ACCESO ADMIN (CRÍTICO) ---\n")
        crbs = run_kubectl(["get", "clusterrolebindings"])
        if crbs:
            for item in crbs.get("items", []):
                role_ref = item.get("roleRef", {}).get("name", "")
                if role_ref == "cluster-admin":
                    subjects = item.get("subjects", [])
                    sub_list = [f"{s.get('kind')}: {s.get('name')}" for s in subjects if s]
                    report.write(f"Binding: {item['metadata']['name']} | Sujetos: {', '.join(sub_list)}\n")
        
        # 2. Auditoría de ServiceAccounts: Automount (Punto 4)
        report.write("\n--- 2. SERVICEACCOUNTS CON AUTOMOUNT HABILITADO (FUERA DE SISTEMA) ---\n")
        sas = run_kubectl(["get", "serviceaccounts", "--all-namespaces"])
        if sas:
            count = 0
            for sa in sas.get("items", []):
                automount = sa.get("automountServiceAccountToken", True)
                if automount is not False:
                    ns = sa['metadata']['namespace']
                    name = sa['metadata']['name']
                    # Ignorar namespaces del sistema RKE2 para reducir ruido
                    if ns not in ["kube-system", "kube-public", "cattle-system", "rke2-system"]:
                        report.write(f"Namespace: {ns} | SA: {name} | Automount: ENABLED\n")
                        count += 1
            report.write(f"\nTotal ServiceAccounts con Automount habilitado: {count}\n")

        # 3. Detección de Wildcards en Roles (Punto 3 - PoLP)
        report.write("\n--- 3. ROLES/CLUSTERROLES CON USO DE WILDCARDS (*) ---\n")
        all_roles = run_kubectl(["get", "clusterroles"])
        if all_roles:
            for role in all_roles.get("items", []):
                rules = role.get("rules", [])
                for rule in rules:
                    if "*" in rule.get("resources", []) or "*" in rule.get("verbs", []):
                        report.write(f"ClusterRole: {role['metadata']['name']} utiliza '*' en recursos o verbos.\n")

    print(f"\nAuditoría finalizada. Reporte generado: {REPORT_FILE}")

if __name__ == "__main__":
    audit_rbac_sa()
