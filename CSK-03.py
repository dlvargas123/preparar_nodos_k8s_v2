import subprocess
import os
from datetime import datetime

# Configuración de búsqueda para RKE2
TARGET_FLAG = "--authorization-mode"
EXPECTED_VALUE = "Node,RBAC"
CHECK_COMMAND = "ps -ef | grep kube-apiserver | grep -v grep"
REPORT_FILE = f"evidencia_auth_mode_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def run_validation():
    print(f"Iniciando validación de Authorization Mode en RKE2...")
    
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE AUDITORÍA DE SEGURIDAD - {datetime.now()}\n")
        report.write(f"CONTROL: Validar --authorization-mode=Node,RBAC\n")
        report.write(f"ENTORNO: Rancher RKE2 v1.32.13\n")
        report.write("="*60 + "\n\n")

        try:
            # 1. Verificar presencia y valor del flag en el proceso activo
            result = subprocess.run(CHECK_COMMAND, shell=True, capture_output=True, text=True)
            process_output = result.stdout

            if TARGET_FLAG in process_output:
                # Extraer el valor configurado
                parts = process_output.split(TARGET_FLAG + "=")
                current_value = parts[1].split(" ")[0].strip() if len(parts) > 1 else "No detectado"
                
                if EXPECTED_VALUE in current_value:
                    status = f"PASÓ: El modo de autorización es el recomendado ({current_value})."
                else:
                    status = f"ADVERTENCIA: El modo configurado es {current_value}. Se recomienda {EXPECTED_VALUE}."
            else:
                status = "FALLO: No se detectó el parámetro --authorization-mode explícitamente."

            print(status)
            report.write(f"ESTADO: {status}\n")
            report.write(f"EVIDENCIA DEL PROCESO (ps):\n{process_output.strip() if process_output else 'API Server no detectado'}\n\n")

            # 2. Salud del API Server
            report.write("-" * 30 + "\n")
            report.write("VERIFICACIÓN DE PODS DEL SISTEMA:\n")
            pod_check = subprocess.run(["kubectl", "get", "pods", "-n", "kube-system", "-l", "component=kube-apiserver"], 
                                      capture_output=True, text=True)
            report.write(pod_check.stdout if pod_check.returncode == 0 else "kubectl no disponible.\n")

        except Exception as e:
            report.write(f"ERROR DURANTE LA VALIDACIÓN: {e}\n")

    print(f"\nReporte de evidencia generado con éxito: {REPORT_FILE}")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Aviso: Se requiere root para inspeccionar argumentos de procesos del sistema.")
    run_validation()
