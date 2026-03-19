import subprocess
import os
from datetime import datetime

# Configuración de búsqueda
FORBIDDEN_FLAG = "--token-auth-file"
CHECK_COMMAND = "ps -ef | grep kube-apiserver | grep -v grep"
REPORT_FILE = f"evidencia_token_auth_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def run_validation():
    print(f"Iniciando validación de Tokens Estáticos en RKE2...")
    
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE AUDITORÍA DE SEGURIDAD - {datetime.now()}\n")
        report.write(f"CONTROL: Eliminar uso de --token-auth-file (Static Tokens)\n")
        report.write(f"ENTORNO: Rancher RKE2 v1.32.13\n")
        report.write("="*60 + "\n\n")

        try:
            # 1. Verificar proceso activo para detectar el flag
            result = subprocess.run(CHECK_COMMAND, shell=True, capture_output=True, text=True)
            process_output = result.stdout

            if FORBIDDEN_FLAG in process_output:
                status = "FALLO: Se detectó el parámetro inseguro --token-auth-file."
                action = "Acción: Debe eliminarse del archivo de configuración de RKE2."
            else:
                status = "PASÓ: El parámetro --token-auth-file NO está presente."
                action = "No se requiere acción. El cluster utiliza Service Accounts seguras."

            print(status)
            report.write(f"ESTADO: {status}\n")
            report.write(f"DETALLE: {action}\n\n")
            report.write(f"EVIDENCIA DEL COMANDO (ps):\n{process_output.strip() if process_output else 'API Server no detectado'}\n\n")

            # 2. Verificar estado de los componentes
            report.write("-" * 30 + "\n")
            report.write("VERIFICACIÓN DE PODS DEL SISTEMA:\n")
            pod_check = subprocess.run(["kubectl", "get", "pods", "-n", "kube-system", "-l", "component=kube-apiserver"], 
                                      capture_output=True, text=True)
            
            if pod_check.returncode == 0:
                report.write(pod_check.stdout)
            else:
                report.write("kubectl no disponible o sin permisos de administración.\n")

        except Exception as e:
            report.write(f"ERROR DURANTE LA EJECUCIÓN: {e}\n")

    print(f"Reporte de evidencia generado: {REPORT_FILE}")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Aviso: Se requiere root para inspeccionar los argumentos completos del proceso kube-apiserver.")
    run_validation()
