import platform
import json
import os
import getpass
import subprocess
import shutil
import time

CONFIG_FILE = ".blinker_config"

def run_command(command, check=True, shell=False, cwd=None, capture_output=False, text=False):
    """Helper function to run a subprocess command with error handling."""
    print(f"Ejecutando: {' '.join(command) if isinstance(command, list) else command}")
    if shell: # If shell is True, command should be a string
        if isinstance(command, list): command = " ".join(command)
    else: # If shell is False, command should be a list
        if isinstance(command, str): command = command.split()

    try:
        result = subprocess.run(
            command,
            check=check,
            shell=shell,
            cwd=cwd,
            capture_output=capture_output,
            text=text,
            executable=None if not shell else ("/bin/bash" if "linux" in platform.system().lower() or "darwin" in platform.system().lower() else None) # Specify bash for shell on unix-like
        )
        if capture_output:
            print(result.stdout)
        return result
    except subprocess.CalledProcessError as e:
        print(f"Error al ejecutar el comando: {' '.join(command) if isinstance(command, list) else command}")
        print(f"Código de retorno: {e.returncode}")
        if e.stdout:
            print(f"Salida estándar: {e.stdout}")
        if e.stderr:
            print(f"Error estándar: {e.stderr}")
        # Decide if script should exit or if error can be handled
        # For critical commands, we let main() handle exit or further action.
        raise # Re-raise the exception to be caught by the caller in main()
    except FileNotFoundError:
        print(f"Error: Comando no encontrado - {command[0] if isinstance(command, list) else command.split()[0]}. Asegúrese de que esté instalado y en el PATH.")
        raise


def get_os():
    """Detects the operating system."""
    system = platform.system().lower()
    if "windows" in system:
        return "windows"
    elif "darwin" in system:
        return "macos"
    elif "linux" in system:
        return "linux"
    return "unknown"

def check_docker():
    """Checks if Docker is installed and accessible."""
    print("Paso 1: Verificando Docker...")
    if not shutil.which("docker"):
        print("Docker no encontrado en el PATH.")
        print("Por favor, instale Docker Desktop desde https://www.docker.com/products/docker-desktop/")
        print("Asegúrese de que Docker Desktop se esté ejecutando después de la instalación.")
        return False
    try:
        run_command(["docker", "--version"], capture_output=True, text=True)
        run_command(["docker", "info"], capture_output=True, text=True, check=False) # info can return non-zero if server is off
        print("Docker daemon está respondiendo o al menos el CLI funciona.") # Simplified check
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print("Error al verificar Docker. Asegúrese de que Docker Desktop esté en ejecución.")
        return False


def check_mise():
    """Checks if mise is installed."""
    print("Verificando mise...")
    if shutil.which("mise"):
        print("mise encontrado en el PATH.")
        return True
    print("mise no encontrado en el PATH.")
    return False

def check_supabase_cli():
    """Checks if Supabase CLI is installed."""
    print("Verificando Supabase CLI...")
    if shutil.which("supabase"):
        print("Supabase CLI encontrado en el PATH.")
        return True
    print("Supabase CLI no encontrado en el PATH.")
    return False

def install_mise(os_type):
    """Attempts to install mise."""
    print("\nIntentando instalar mise...")
    try:
        if os_type == "macos":
            run_command(["brew", "install", "mise"])
        elif os_type == "linux":
            run_command("curl -fsSL https://mise.run | sh", shell=True)
            print("\nIMPORTANTE: mise se ha instalado en ~/.local/bin/mise.")
            print("Para activar mise en la sesión actual, ejecute: eval \"$(~/.local/bin/mise activate bash)\"")
            print("Para activación permanente, agregue la línea anterior a su ~/.bashrc, ~/.zshrc o config.fish.")
            os.environ["PATH"] = os.path.expanduser("~/.local/bin") + os.pathsep + os.environ["PATH"]
            if not shutil.which("mise"):
                 print("Mise instalado pero no encontrado inmediatamente en PATH. Intente reiniciar su terminal o configurar su PATH.")
        elif os_type == "windows":
            run_command(["winget", "install", "-e", "--id", "jdx.mise"])
        else:
            print(f"Instalación de mise no soportada automáticamente para: {os_type}")
            return False
        print("Instalación de mise intentada.")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Error durante la instalación de mise: {e}")
        return False
    return check_mise()

def install_supabase_cli(os_type):
    """Attempts to install Supabase CLI."""
    print("\nIntentando instalar Supabase CLI...")
    try:
        if os_type == "macos":
            run_command(["brew", "install", "supabase/tap/supabase"])
        elif os_type == "linux":
            run_command("curl -sSL https://github.com/supabase/cli/releases/latest/download/supabase_linux_amd64.deb -o supabase.deb", shell=True)
            run_command("sudo apt-get update && sudo apt-get install -y ./supabase.deb", shell=True)
            run_command("rm supabase.deb", shell=True)
        elif os_type == "windows":
            run_command(["winget", "install", "-e", "--id", "Supabase.CLI"])
        else:
            print(f"Instalación de Supabase CLI no soportada automáticamente para: {os_type}")
            return False
        print("Instalación de Supabase CLI intentada.")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Error durante la instalación de Supabase CLI: {e}")
        return False
    return check_supabase_cli()

def leer_config_completa():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def gestionar_config(clave: str, descripcion: str, es_secreto: bool = True, valor_predeterminado: str = None) -> str:
    config = leer_config_completa()
    if clave not in config and valor_predeterminado is not None:
        pass
    elif clave in config:
        respuesta = input(f"Valor guardado para {descripcion} ('{config[clave]}'). ¿Usarlo? (S/n): ").strip().lower()
        if respuesta == 's' or respuesta == '':
            return config[clave]

    if valor_predeterminado is not None and clave not in config:
        valor_actual = valor_predeterminado
        print(f"Usando valor predeterminado para {descripcion}: {valor_actual}")
    else:
        prompt = f"Ingrese {descripcion}: "
        valor_actual = getpass.getpass(prompt) if es_secreto else input(prompt)
    
    config[clave] = valor_actual
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
        if not (valor_predeterminado is not None and clave not in config and valor_actual == valor_predeterminado):
             print(f"Configuración '{descripcion}' guardada.")
    except IOError as e:
        print(f"Error al guardar config: {e}")
    return valor_actual

def main():
    try:
        print("Iniciando configuración Blinker...")
        os_type = get_os()
        print(f"OS: {os_type}")
        if os_type == "unknown": return

        # Dependency Checks
        if not check_docker(): return
        if not check_mise():
            if input("Mise no hallado. ¿Instalar? (S/n): ").lower() in ['s', '']:
                if not install_mise(os_type): return
            else: return
        if not check_supabase_cli():
            if input("Supabase CLI no hallado. ¿Instalar? (S/n): ").lower() in ['s', '']:
                if not install_supabase_cli(os_type): return
            else: return
        print("\n--- Dependencias OK ---")

        # Blinker Setup Mode
        print("\n--- Blinker Setup ---")
        setup_mode = ""
        while True:
            choice = input("¿Instalación? 1. Local (Docker) 2. Daytona: ").strip().lower()
            temp_mode = "local" if choice in ['1', 'local'] else "daytona" if choice in ['2', 'daytona'] else None
            if not temp_mode: continue
            setup_mode = gestionar_config("SETUP_MODE", "Modo Ejecución", False, temp_mode)
            if setup_mode in ["local", "daytona"]: break
            else: # Clear invalid stored mode
                cfg = leer_config_completa(); cfg.pop("SETUP_MODE", None)
                with open(CONFIG_FILE, 'w') as f: json.dump(cfg, f, indent=4)
        
        # Supabase Config
        print("\n--- Config Supabase ---")
        gestionar_config("SUPABASE_URL", "URL Supabase")
        gestionar_config("SUPABASE_ANON_KEY", "Anon Key Supabase")
        gestionar_config("SUPABASE_SERVICE_ROLE_KEY", "Service Role Key Supabase")

        # Optional Tools
        print("\n--- APIs Opcionales ---")
        tools = [{"name": "Zillow", "key": "RAPIDAPI_KEY_ZILLOW", "desc": "RapidAPI Key Zillow"},
                 {"name": "Twitter", "key": "TWITTER_API_KEY", "desc": "Twitter API Key"}]
        for tool in tools:
            if input(f"¿Configurar {tool['name']}? (s/N): ").lower() == 's':
                gestionar_config(tool['key'], tool['desc'])

        # Generate .env
        print("\n--- Generando .env ---")
        config = leer_config_completa()
        env_lines = [f"NEXT_PUBLIC_SUPABASE_URL={config.get('SUPABASE_URL', '')}",
                     f"NEXT_PUBLIC_SUPABASE_ANON_KEY={config.get('SUPABASE_ANON_KEY', '')}",
                     f"SUPABASE_SERVICE_ROLE_KEY={config.get('SUPABASE_SERVICE_ROLE_KEY', '')}",
                     f"BLINKER_SETUP_MODE={config.get('SETUP_MODE', 'local')}"]
        
        # Add NEXT_PUBLIC_API_URL for local mode
        if config.get("SETUP_MODE") == "local":
            env_lines.append("NEXT_PUBLIC_API_URL=http://localhost:8000")

        for tool in tools: # Ensure 'tools' here refers to the list of optional API tools
            if tool['key'] in config: env_lines.append(f"{tool['key']}={config[tool['key']]}")
        
        with open(".env", "w") as f: f.write("\n".join(env_lines) + "\n")
        print(".env generado.")

        # --- Iniciando Servicios ---
        print("\n--- Iniciando Servicios ---")
        
        print("Asegurando versiones de herramientas con Mise... (esto puede tardar unos minutos)")
        run_command(["mise", "install"])
        
        print("Instalando dependencias de Python...")
        run_command(["pip", "install", "-r", "backend/requirements.txt"])
        
        print("Instalando dependencias de Frontend...")
        # Note: npm install can be slow. Consider yarn or pnpm if project supports it for speed.
        run_command(["npm", "install", "--prefix", "frontend"])

        current_setup_mode = config.get("SETUP_MODE")

        if current_setup_mode == "local":
            print("\n--- Configurando Entorno Local con Docker ---")
            
            print("Iniciando instancia local de Supabase...")
            run_command(["supabase", "start"]) # Waits by default
            print("Supabase iniciado. Esperando para estabilización...")
            time.sleep(10)
            
            print("Aplicando migraciones a la base de datos Supabase...")
            # Use --local for db reset to ensure it targets the local DB started by `supabase start`
            run_command(["supabase", "db", "reset", "--local"])
            print("Migraciones aplicadas.")
            
            print("Construyendo y levantando contenedores Docker (backend y frontend)...")
            run_command(["docker-compose", "up", "--build", "-d"])
            print("Contenedores iniciados en segundo plano.")
            
            print("\nMonitoreando el estado de los contenedores...")
            time.sleep(5) # Give services a moment to start before checking
            ps_result = run_command(["docker-compose", "ps"], capture_output=True, text=True)

            # Basic parsing for service status
            # A more robust check would inspect logs for specific messages or query health endpoints
            if ps_result and ps_result.stdout:
                lines = ps_result.stdout.strip().split('\n')
                services_status = {}
                if len(lines) > 2: # Header, separator, then services
                    for line in lines[2:]:
                        parts = line.split()
                        if len(parts) >= 3: # Name, Command, State ...
                             service_name = parts[0]
                             # State can be "Up", "Exit 0", "running (healthy)" etc.
                             # We look for "Up" or "running" as positive indicators
                             service_state = " ".join(parts[2:]) # Rejoin state if it has spaces
                             if "up" in service_state.lower() or "running" in service_state.lower():
                                 services_status[service_name] = "OK"
                             else:
                                 services_status[service_name] = service_state # Store actual state if not OK

                print("Estado de los servicios de Docker Compose:")
                for name, status in services_status.items():
                    print(f"  - {name}: {status}")

                # Check for specific services expected (adjust names if your docker-compose.yml is different)
                expected_services = ["frontend", "backend"] # Add other essential services if any
                all_ok = True
                for srv in expected_services:
                    # Docker compose might prefix with project name, e.g. blinker-frontend-1
                    # So we check if any running service *contains* the expected name
                    found_service = any(srv in k and v == "OK" for k,v in services_status.items())
                    if not found_service:
                        all_ok = False
                        print(f"ADVERTENCIA: El servicio '{srv}' no parece estar ejecutándose correctamente.")

                if all_ok:
                    print("\n--- ¡Blinker está listo! ---")
                    print("Puedes acceder a la aplicación en:")
                    print("  Frontend: http://localhost:3000")
                    print("  Backend API: http://localhost:8000") # or your backend port
                else:
                    print("\nADVERTENCIA: Algunos servicios de Docker Compose no se iniciaron correctamente.")
                    print("Revise los logs con 'docker-compose logs' para más detalles.")

        elif current_setup_mode == "daytona":
            print("\n--- Configuración para Daytona Completada ---")
            print("Por favor, sigue las instrucciones específicas de Daytona para desplegar Blinker en tu servidor.")
            print("El archivo .env generado contiene las configuraciones necesarias.")
            print("Asegúrate de transferir todo el proyecto, incluyendo el archivo .env, a tu entorno Daytona.")

    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        # Generic error catcher for critical commands that weren't caught by specific try-excepts
        # (though run_command re-raises, so this might be redundant if all calls use it)
        print(f"\nError crítico durante la ejecución: {e}")
        print("El script no pudo completarse. Por favor, revise los mensajes de error anteriores.")
    except Exception as e:
        print(f"\nOcurrió un error inesperado: {e}")
        print("El script no pudo completarse.")

if __name__ == "__main__":
    main()
