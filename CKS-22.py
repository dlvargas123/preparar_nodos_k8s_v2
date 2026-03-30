import subprocess
import os
import json
from datetime import datetime

# Configuración específica para RKE2
CHECK_COMMAND = "ps -ef | grep kubelet | grep -v grep"
KUBELET_JSON_CONFIG = "/var/lib/rancher/rke2/agent/etc/kubelet/config.json"
EXPECTED_PORT = 0
REPORT_FILE = f"evidencia_kubelet_readonly_port_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def run_validation():
    print(f"Iniciando validación de Read-Only Port de Kubelet en RKE2...")
    
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE AUDITORÍA DE SEGURIDAD - {datetime.now()}\n")
        report.write(f"CONTROL: Kubelet Read-Only Port (Port 10255 Hardening)\n")
        report.write(f"ENTORNO: Rancher RKE2 v1.32.13\n")
        report.write("="*60 + "\n\n")

        try:
            # 1. Validar proceso en ejecución (Punto 4 de tu solicitud)
            result = subprocess.run(CHECK_COMMAND, shell=True, capture_output=True, text=True)
            process_output = result.stdout
            
            # 2. Validar archivo de configuración JSON (Punto 1 y 4)
            readonly_port = "No detectado"
            if os.path.exists(KUBELET_JSON_CONFIG):
                with open(KUBELET_JSON_CONFIG, 'r') as f:
                    config_data = json.load(f)
                    # En el JSON de Kubelet, el campo es 'readOnlyPort'
                    readonly_port = config_data.get("readOnlyPort", "No especificado (Default es 0 en RKE2)")
            
            # 3. Validar consistencia
            if readonly_port == 0 or "--read-only-port=0" in process_output or "No especificado" in str(readonly_port):
                status = "PASÓ: El puerto de solo lectura está deshabilitado (Valor: 0)."
            else:
                status = f"REVISAR: El puerto detectado es '{readonly_port}'. Se recomienda 0."

            print(status)
            report.write(f"ESTADO: {status}\n")
            report.write(f"DETALLE: Puerto configurado en JSON: {readonly_port}\n\n")
            report.write(f"EVIDENCIA DEL PROCESO (ps):\n{process_output.strip()}\n\n")

            # 4. Verificar sockets activos (ss) para asegurar que el puerto 10255 no escuche
            net_check = subprocess.run("ss -tpln | grep :10255", shell=True, capture_output=True, text=True)
            if not net_check.stdout:
                report.write("VERIFICACIÓN DE RED: El puerto 10255 no está en escucha. (Correcto)\n")
            else:
                report.write(f"ALERTA DE RED: Se detectó actividad en el puerto 10255:\n{net_check.stdout}\n")

        except Exception as e:
            report.write(f"ERROR DURANTE LA VALIDACIÓN: {e}\n")

    print(f"\nReporte de evidencia generado con éxito: {REPORT_FILE}")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Error: Se requiere privilegios de ROOT para acceder a la configuración de RKE2.")
    else:
        run_validation()
