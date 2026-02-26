import os
import subprocess

# Función para verificar la existencia del directorio y crear si no existe
def ensure_snmp_directory():
    snmp_dir = "/etc/snmp"
    if not os.path.exists(snmp_dir):
        print(f"El directorio {snmp_dir} no existe. Creando...")
        os.makedirs(snmp_dir)

# Función para verificar si snmpd está instalado y si no, instalarlo
def ensure_snmp_installed():
    try:
        # Verifica si snmpd está instalado
        subprocess.run(["which", "snmpd"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print("SNMP ya está instalado.")
    except subprocess.CalledProcessError:
        print("SNMP no está instalado. Instalando...")
        subprocess.run(["sudo", "apt-get", "install", "-y", "snmpd"], check=True)
        print("SNMP instalado correctamente.")

# Define la configuración SNMP en una cadena
snmp_config = """
###############################################################################
# Access Control
###############################################################################

# define RO community
rocommunity     ifxcliente
rwcommunity     ifxcliente

# First, map the community name "ifxpublic" into a "security name"
#       sec.name        source          community
com2sec snmpserver      190.61.4.34   ifxcliente
com2sec snmpserver      190.61.4.35   ifxcliente
com2sec snmpserver      190.61.4.36   ifxcliente
com2sec snmpserver      190.61.4.170  ifxcliente
com2sec snmpserver      201.217.193.220 ifxcliente
com2sec local           localhost     ifxcliente
com2sec snmpserver      200.62.3.206  ifxcliente
#com2sec snmpserveri     200.91.200.106 ifxpublic

# Second, map the security name into a group name:
#       groupName  securityModel  securityName
group   ifxgrouprw   v1             local
group   ifxgrouprw   v2c            local
group   ifxgrouprw   usm            local
group   ifxgroupro   v1             snmpserver
group   ifxgroupro   v2c            snmpserver
group   ifxgroupro   usm            snmpserver
group   ifxgroupro   v1             snmpserveri
group   ifxgroupro   v2c            snmpserveri
group   ifxgroupro   usm            snmpserveri

# Third, create a view for us to let the group have rights to:
#       name         incl/excl     subtree         mask(optional)
view    all          included      .1

# Finally, grant the group read-only access to the systemview view.
#       group      context sec.model sec.level prefix read      write notif
access  ifxgrouprw ""      any       noauth    exact  all       none  none
access  ifxgroupro ""      any       noauth    exact  all       none  none

################################################################################
# System contact information
#
sysdescr        " IFX ORION  "
syscontact      UNIX SYSADMIN  <sysadmin@ifxcorp.com>
sysname         IFX
syslocation     "IFX"
"""

# Ruta donde guardar el archivo de configuración
config_file_path = "/etc/snmp/snmpd.conf"

# Función para escribir la configuración en el archivo
def configure_snmp():
    # Asegurarse de que el directorio exista
    ensure_snmp_directory()
    
    # Asegurarse de que SNMP esté instalado
    ensure_snmp_installed()
    
    # Abre y escribe el archivo de configuración SNMP
    with open(config_file_path, 'w') as file:
        file.write(snmp_config)

    print(f"Archivo SNMP configurado correctamente en {config_file_path}")
    
    # Verificar si el archivo fue creado correctamente
    if os.path.exists(config_file_path):
        # Verificar si contiene las configuraciones clave
        with open(config_file_path, 'r') as file:
            content = file.read()
            if 'rocommunity     ifxcliente' in content and 'rwcommunity     ifxcliente' in content:
                print("Configuración de SNMP verificada correctamente.")
            else:
                print("Error: No se encontraron configuraciones clave en el archivo.")
    else:
        print("Error: No se pudo crear el archivo SNMP.")

# Ejecutar la configuración
if __name__ == "__main__":
    configure_snmp()
