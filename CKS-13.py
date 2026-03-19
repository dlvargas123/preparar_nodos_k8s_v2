import subprocess
import os
from datetime import datetime

# Configuración de búsqueda para RKE2
CHECK_COMMAND = "ps -ef | grep kube-apiserver | grep -v grep"
FORBIDDEN_PLUGIN = "AlwaysAdmit"
DISABLE_FLAG = "--disable-admission-plugins"
ENABLE_FLAG = "--enable-admission-plugins"
REPORT_FILE = f"evidencia_admission_plugins_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def run_validation():
    print(f"Iniciando validación de Admission Plugins en RKE2...")
    
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE AUDITORÍA DE SEGURIDAD - {datetime.now()}\n")
        report.write(f"CONTROL: Restricción de Plugin AlwaysAdmit\n")
        report.write(f"ENTORNO: Rancher RKE2 v1.32.13\n")
        report.write("="*60 + "\n\n")

        try:
            # 1. Obtener la línea de comandos del proceso activo
            result = subprocess.run(CHECK_COMMAND, shell=True, capture_output=True, text=True)
            process_output = result.stdout

            # 2. Validar que NO esté en enable
            if FORBIDDEN_PLUGIN in process_output and ENABLE_FLAG in process_output:
                status_enable = f"FALLO: Se detectó {FORBIDDEN_PLUGIN} habilitado."
            else:
                status_enable = f"PASÓ: {FORBIDDEN_PLUGIN} no se encuentra habilitado."

            # 3. Validar si está explícitamente en disable (A prueba de auditor)
            if FORBIDDEN_PLUGIN in process_output and DISABLE_FLAG in process_output:
                status_disable = f"EXCELENTE: {FORBIDDEN_PLUGIN} está deshabilitado explícitamente."
            else:
                status_disable = f"INFO: No está en la lista de deshabilitados, pero no está activo (Cumple por omisión)."

            print(status_enable)
            print(status_disable)
            
            report.write(f"ESTADO HABILITADOS: {status_enable}\n")
            report.write(f"ESTADO DESHABILITADOS: {status_disable}\n\n")
            report.write(f"EVIDENCIA DEL PROCESO (ps):\n{process_output.strip()}\n\n")

            # 4. Salud del API Server
            report.write("-" * 30 + "\n")
            report.write("VERIFICACIÓN DE PODS (KUBE-SYSTEM):\n")
            pod_check = subprocess.run(["kubectl", "get", "pods", "-n", "kube-system", "-l", "component=kube-apiserver"], 
                                      capture_output=True, text=True)
            report.write(pod_check.stdout if pod_check.returncode == 0 else "kubectl no disponible.\n")

        except Exception as e:
            report.write(f"ERROR DURANTE LA VALIDACIÓN: {e}\n")

    print(f"\nReporte de evidencia generado: {REPORT_FILE}")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Error: Se requiere root para inspeccionar el proceso del API Server.")
    else:
        run_validation()
