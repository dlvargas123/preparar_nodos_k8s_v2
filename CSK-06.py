import subprocess
import os
import yaml
from datetime import datetime

# Configuración de búsqueda para RKE2
TARGET_FLAG = "--encryption-provider-config"
CHECK_COMMAND = "ps -ef | grep kube-apiserver | grep -v grep"
REPORT_FILE = f"evidencia_encryption_at_rest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

def run_validation():
    print(f"Iniciando validación de Encryption at Rest en RKE2...")
    
    with open(REPORT_FILE, "w") as report:
        report.write(f"REPORTE DE AUDITORÍA DE SEGURIDAD - {datetime.now()}\n")
        report.write(f"CONTROL: Validar Cifrado de Secretos (Encryption at Rest)\n")
        report.write(f"ENTORNO: Rancher RKE2 v1.32.13\n")
        report.write("="*60 + "\n\n")

        try:
            # 1. Verificar flags en el proceso (ps)
            result = subprocess.run(CHECK_COMMAND, shell=True, capture_output=True, text=True)
            process_output = result.stdout
            
            if TARGET_FLAG in process_output:
                # Extraer la ruta del archivo de configuración
                parts = process_output.split(TARGET_FLAG + "=")
                config_path = parts[1].split(" ")[0].strip()
                status = "PASÓ: El cifrado de secretos está habilitado en el runtime."
                
                # 2. Validar el archivo físicamente
                if os.path.exists(config_path):
                    file_perms = oct(os.stat(config_path).st_mode & 0o777)
                    report.write(f"ARCHIVO DETECTADO: {config_path}\n")
                    report.write(f"PERMISOS DEL ARCHIVO: {file_perms} (Recomendado 600)\n")
                    
                    with open(config_path, 'r') as f:
                        conf_data = yaml.safe_load(f)
                        # El primer proveedor es el activo para escritura
                        primary_provider = list(conf_data['resources'][0]['providers'][0].keys())[0]
                        report.write(f"PROVEEDOR DE CIFRADO ACTIVO: {primary_provider}\n")
                        
                        if primary_provider == "identity":
                            status = "ADVERTENCIA: identity está de primero; los datos NO se están cifrando."
                else:
                    status = "FALLO: El flag está presente pero el archivo no existe en la ruta indicada."
            else:
                status = "INFO: El cifrado de secretos en reposo NO está configurado (Comportamiento default)."

            print(status)
            report.write(f"ESTADO: {status}\n\n")
            report.write(f"EVIDENCIA DEL PROCESO (ps):\n{process_output.strip()}\n\n")

        except Exception as e:
            report.write(f"ERROR DURANTE LA VALIDACIÓN: {e}\n")

    print(f"\nReporte de evidencia generado con éxito: {REPORT_FILE}")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Error: Se requiere root para inspeccionar procesos y archivos de credenciales.")
    else:
        run_validation()
