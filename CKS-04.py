import subprocess
import os
from datetime import datetime

# Configuración de búsqueda
FORBIDDEN_FLAG = "--basic-auth-file"
CHECK_COMMAND = "ps -ef | grep kube-apiserver | grep -v grep"
REPORT_FILE = f"evidencia_basic_auth_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def run_validation():
    print(f"Iniciando validación de Autenticación Básica en RKE2...")
    
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE AUDITORÍA DE SEGURIDAD - {datetime.now()}\n")
        report.write(f"CONTROL: Eliminar uso de --basic-auth-file (Insecure Auth)\n")
        report.write(f"ENTORNO: Rancher RKE2 v1.32.13\n")
        report.write("="*60 + "\n\n")

        try:
            # 1. Verificar proceso activo
            result = subprocess.run(CHECK_COMMAND, shell=True, capture_output=True, text=True)
            process_output = result.stdout

            if FORBIDDEN_FLAG in process_output:
                status = "FALLO: Se detectó el parámetro prohibido --basic-auth-file."
                action = "Acción: Eliminar el parámetro inmediatamente del config.yaml o argumentos del servicio."
            else:
                status = "PASÓ: El parámetro --basic-auth-file NO está presente (configuración segura)."
                action = "No se requiere acción. RKE2 v1.32 no utiliza este método de autenticación."

            print(status)
            report.write(f"ESTADO: {status}\n")
            report.write(f"DETALLE: {action}\n\n")
            report.write(f"EVIDENCIA DEL PROCESO:\n{process_output.strip() if process_output else 'Proceso no detectado'}\n\n")

            # 2. Verificar integridad del API Server (Punto 4)
            report.write("-" * 30 + "\n")
            report.write("VERIFICACIÓN DE SALUD DEL API SERVER:\n")
            pod_check = subprocess.run(["kubectl", "get", "pods", "-n", "kube-system", "-l", "component=kube-apiserver", "-o", "wide"], 
                                      capture_output=True, text=True)
            
            if pod_check.returncode == 0:
                report.write(pod_check.stdout)
            else:
                report.write("No se pudo ejecutar kubectl. Verifique permisos de admin.\n")

        except Exception as e:
            report.write(f"ERROR DURANTE LA VALIDACIÓN: {e}\n")

    print(f"Reporte generado exitosamente: {REPORT_FILE}")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Aviso: Se recomienda ejecutar como root para obtener la línea de comandos completa del proceso.")
    run_validation()
