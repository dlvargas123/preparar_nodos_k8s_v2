import subprocess
import os
from datetime import datetime

# Configuración de búsqueda para RKE2
CHECK_COMMAND = "ps -ef | grep kube-apiserver | grep -v grep"
TARGET_PLUGIN = "AlwaysPullImages"
ENABLE_FLAG = "--enable-admission-plugins"
REPORT_FILE = f"evidencia_always_pull_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def run_validation():
    print(f"Iniciando validación de Admission Plugin: {TARGET_PLUGIN}...")
    
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE AUDITORÍA DE SEGURIDAD - {datetime.now()}\n")
        report.write(f"CONTROL: Admission Plugin - {TARGET_PLUGIN}\n")
        report.write(f"ENTORNO: Rancher RKE2 v1.32.13\n")
        report.write("="*60 + "\n\n")

        try:
            # 1. Obtener el proceso activo
            result = subprocess.run(CHECK_COMMAND, shell=True, capture_output=True, text=True)
            process_output = result.stdout

            # 2. Validar si el plugin está en la lista de habilitados
            if TARGET_PLUGIN in process_output and ENABLE_FLAG in process_output:
                status = f"PASÓ: El plugin {TARGET_PLUGIN} está habilitado correctamente."
            else:
                status = f"FALLO: El plugin {TARGET_PLUGIN} NO se detectó en la ejecución."

            print(status)
            report.write(f"ESTADO: {status}\n\n")
            report.write(f"EVIDENCIA DEL PROCESO (ps):\n{process_output.strip() if process_output else 'Proceso no encontrado'}\n\n")

            # 3. Salud del API Server
            report.write("-" * 30 + "\n")
            report.write("VERIFICACIÓN DE PODS (KUBE-SYSTEM):\n")
            pod_check = subprocess.run(["kubectl", "get", "pods", "-n", "kube-system", "-l", "component=kube-apiserver"], 
                                      capture_output=True, text=True)
            report.write(pod_check.stdout if pod_check.returncode == 0 else "kubectl no disponible.\n")

        except Exception as e:
            report.write(f"ERROR DURANTE LA VALIDACIÓN: {e}\n")

    print(f"\nReporte de evidencia generado con éxito: {REPORT_FILE}")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Error: Se requiere root para inspeccionar el proceso del API Server.")
    else:
        run_validation()
