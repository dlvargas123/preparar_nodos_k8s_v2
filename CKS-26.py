import subprocess
import json
from datetime import datetime

REPORT_FILE = f"auditoria_psa_rke2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def run_kubectl(args):
    result = subprocess.run(["kubectl"] + args + ["-o", "json"], capture_output=True, text=True)
    return json.loads(result.stdout) if result.returncode == 0 else None

def audit_pod_security():
    print("Iniciando auditoría de Pod Security Admission en RKE2...")
    
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE SEGURIDAD DE PODS (PSA/PSS) - {datetime.now()}\n")
        report.write(f"ENTORNO: Rancher RKE2 v1.32.13\n")
        report.write("="*60 + "\n\n")

        # 1. Auditoría de Namespaces y Labels PSA
        report.write("--- 1. ESTADO DE POLÍTICAS PSA POR NAMESPACE ---\n")
        namespaces = run_kubectl(["get", "ns"])
        if namespaces:
            for ns in namespaces.get("items", []):
                name = ns['metadata']['name']
                labels = ns['metadata'].get('labels', {})
                psa_level = labels.get("pod-security.kubernetes.io/enforce", "SIN POLÍTICA (Default)")
                report.write(f"Namespace: {name:25} | PSA Level: {psa_level}\n")

        # 2. Detección de Workloads Privilegiados (Punto 3)
        report.write("\n--- 2. WORKLOADS CON CONFIGURACIONES INSEGURAS ---\n")
        pods = run_kubectl(["get", "pods", "--all-namespaces"])
        if pods:
            for pod in pods.get("items", []):
                spec = pod.get("spec", {})
                meta = pod.get("metadata", {})
                
                # Check Host Access
                host_access = []
                if spec.get("hostNetwork"): host_access.append("hostNetwork")
                if spec.get("hostPID"): host_access.append("hostPID")
                if spec.get("hostIPC"): host_access.append("hostIPC")
                
                # Check Privileged Containers
                privileged = False
                for container in spec.get("containers", []):
                    sc = container.get("securityContext", {})
                    if sc.get("privileged"): privileged = True
                
                if host_access or privileged:
                    ns = meta.get("namespace")
                    name = meta.get("name")
                    report.write(f"ALERTA: Pod {name} en NS {ns}\n")
                    if privileged: report.write("  - Contenedor Privilegiado: SI\n")
                    if host_access: report.write(f"  - Acceso al Host: {', '.join(host_access)}\n")

    print(f"\nAuditoría finalizada. Evidencia guardada en: {REPORT_FILE}")

if __name__ == "__main__":
    audit_pod_security()
