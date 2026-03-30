import subprocess
import os
import json
from datetime import datetime

# Configuración específica para RKE2
CHECK_COMMAND = "ps -ef | grep kubelet | grep -v grep"
KUBELET_JSON_CONFIG = "/var/lib/rancher/rke2/agent/etc/kubelet/config.json"
EXPECTED_MODE = "Webhook"
REPORT_FILE = f"evidencia_kubelet_auth_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def run_validation():
    print(f"Iniciando validación de Autorización de Kubelet en RKE2...")
    
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE AUDITORÍA DE SEGURIDAD - {datetime.now()}\n")
        report.write(f"CONTROL: Kubelet Authorization Mode (Webhook)\n")
        report.write(f"ENTORNO: Rancher RKE2 v1.32.13\n")
        report.write("="*60 + "\n\n")

        try:
            # 1. Validar proceso en ejecución (Punto 4 de tu solicitud)
            result = subprocess.run(CHECK_COMMAND, shell=True, capture_output=True, text=True)
            process_output = result.stdout
            
            # 2. Validar archivo de configuración JSON (Punto 1 y 4)
            auth_mode = "No detectado"
            if os.path.exists(KUBELET_JSON_CONFIG):
                with open(KUBELET_JSON_CONFIG, 'r') as f:
                    config_data = json.load(f)
                    auth_mode = config_data.get("authorization", {}).get("mode", "Desconocido")
            
            if EXPECTED_MODE in auth_mode or EXPECTED_MODE in process_output:
                status = f"PASÓ: El Kubelet utiliza el modo seguro '{EXPECTED_MODE}'."
            else:
                status = f"ADVERTENCIA: El modo detectado es '{auth_mode}'. Se recomienda '{EXPECTED_MODE}'."

            print(status)
            report.write(f"ESTADO: {status}\n")
            report.write(f"DETALLE: Modo configurado en JSON: {auth_mode}\n\n")
            report.write(f"EVIDENCIA DEL PROCESO (ps):\n{process_output.strip()}\n\n")

            # 3. Salud del servicio RKE2
            report.write("-" * 30 + "\n")
            svc_check = subprocess.run(["systemctl", "is-active", "rke2-server"], capture_output=True, text=True)
            report.write(f"ESTADO SERVICIO RKE2: {svc_check.stdout.strip()}\n")

        except Exception as e:
            report.write(f"ERROR DURANTE LA VALIDACIÓN: {e}\n")

    print(f"\nReporte de evidencia generado con éxito: {REPORT_FILE}")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Error: Se requiere privilegios de ROOT para acceder a la configuración de RKE2.")
    else:
        run_validation()
