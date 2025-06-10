# -*- coding: utf-8 -*-
import platform
import json
import os
import getpass
import subprocess
import shutil
import time
import re
import sys
from typing import Optional, Dict, List, Any # Added for type hints

CONFIG_FILE = ".blinker_config"

class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def print_color(text, color_code):
    if "NO_COLOR" in os.environ:
        print(text)
    else:
        print(f"{color_code}{text}{Colors.ENDC}")

def input_color(prompt_text, color_code=Colors.OKCYAN, input_color_code=Colors.ENDC):
    if "NO_COLOR" in os.environ:
        return input(prompt_text)
    print(f"{color_code}{prompt_text}{Colors.ENDC}", end="")
    return input()

def run_command(command, check=True, shell=False, cwd=None, capture_output_default=False, text_default=False):
    command_list = command if isinstance(command, list) else command.split()
    is_pip_install = (command_list[0] == "pip" or (command_list[0] == sys.executable and "-m" in command_list and "pip" in command_list)) and "install" in command_list
    effective_capture_output = True if is_pip_install else capture_output_default
    effective_text = True if is_pip_install else text_default

    print_color(f"Executing: {' '.join(command_list)}", Colors.OKBLUE)

    processed_command = " ".join(command_list) if shell else command_list

    try:
        process = subprocess.run(
            processed_command,
            check=False,
            shell=shell,
            cwd=cwd,
            capture_output=effective_capture_output,
            text=effective_text,
            executable=None if not shell else ("/bin/bash" if platform.system().lower() in ["linux", "darwin"] else None)
        )
        if effective_capture_output and process.stdout and not (is_pip_install and process.returncode == 0):
            print_color(process.stdout, Colors.OKGREEN)

        if check and process.returncode != 0:
            print_color(f"Error executing command: {' '.join(command_list)}", Colors.FAIL)
            print_color(f"Return code: {process.returncode}", Colors.FAIL)
            if is_pip_install:
                stderr_output = process.stderr or ""
                missing_packages = []
                patterns = [
                    r"ERROR: Could not find a version that satisfies the requirement (.+?)(?: \(from versions: .*\)|$)",
                    r"ERROR: No matching distribution found for (.+?)(?: \(from versions: .*\)|$)"
                ]
                for pattern in patterns:
                    matches = re.findall(pattern, stderr_output)
                    for match in matches:
                        pkg_name = match if isinstance(match, str) else match[0]
                        if pkg_name not in missing_packages: missing_packages.append(pkg_name.strip())
                if missing_packages:
                    print_color("--------------------------------------------------------------------", Colors.FAIL)
                    print_color("ERROR: PIP INSTALLATION FAILED", Colors.FAIL + Colors.BOLD)
                    print_color("--------------------------------------------------------------------", Colors.FAIL)
                    print_color("The following package(s) could not be found or installed:", Colors.WARNING)
                    for pkg in missing_packages: print_color(f"- {pkg}", Colors.WARNING)
                    print_color("\nThis usually means:\n1. Misspelled package name/version in 'backend/requirements.txt'.\n2. Package not on PyPI or configured indexes.\n3. Private package access issues.", Colors.OKCYAN)
                    if stderr_output: print_color(f"\nOriginal pip error:\n{stderr_output.strip()}", Colors.FAIL)
                    print_color("--------------------------------------------------------------------", Colors.FAIL)
                elif stderr_output: print_color(f"Pip installation failed. Error:\n{stderr_output.strip()}", Colors.FAIL)
            elif effective_capture_output:
                if process.stdout and not (is_pip_install and process.returncode == 0): print_color(f"Stdout:\n{process.stdout.strip()}", Colors.FAIL)
                if process.stderr: print_color(f"Stderr:\n{process.stderr.strip()}", Colors.FAIL)
            raise subprocess.CalledProcessError(process.returncode, processed_command, output=process.stdout, stderr=process.stderr)
        return process
    except FileNotFoundError:
        print_color(f"Error: Command not found - {command_list[0]}. Ensure it's installed and in PATH.", Colors.FAIL)
        raise

def get_os():
    system = platform.system().lower()
    if "windows" in system: return "windows"
    if "darwin" in system: return "macos"
    if "linux" in system: return "linux"
    return "unknown"

def check_docker():
    print_color("\n--- Step 1: Checking Docker ---", Colors.HEADER)
    print_color("Docker is required for local development.", Colors.OKCYAN)
    if not shutil.which("docker"):
        print_color("Docker not found. Please install Docker Desktop: https://www.docker.com/products/docker-desktop/", Colors.FAIL)
        return False
    try:
        run_command(["docker", "--version"], capture_output_default=True, text_default=True)
        result = run_command(["docker", "info"], capture_output_default=True, text_default=True, check=False)
        if result.returncode == 0:
            print_color("Docker is running.", Colors.OKGREEN)
            return True
        else:
            print_color("Docker CLI found, but daemon isn't responding. Start Docker Desktop.", Colors.FAIL)
            if result.stderr: print_color(f"Details: {result.stderr.strip()}", Colors.FAIL)
            return False
    except:
        print_color("Error verifying Docker. Ensure it's installed and running.", Colors.FAIL)
        return False

def check_mise():
    print_color("\n--- Step 2: Checking Mise ---", Colors.HEADER)
    print_color("Mise manages project-specific CLI versions.", Colors.OKCYAN)
    if shutil.which("mise"):
        print_color("Mise found.", Colors.OKGREEN)
        return True
    print_color("Mise not found.", Colors.WARNING)
    return False

def check_supabase_cli():
    print_color("\n--- Step 3: Checking Supabase CLI ---", Colors.HEADER)
    print_color("Supabase CLI manages the local Supabase instance.", Colors.OKCYAN)
    if shutil.which("supabase"):
        print_color("Supabase CLI found.", Colors.OKGREEN)
        return True
    print_color("Supabase CLI not found.", Colors.WARNING)
    return False

def install_mise(os_type):
    print_color("\nAttempting to install Mise...", Colors.OKBLUE)
    try:
        if os_type == "macos": run_command(["brew", "install", "mise"])
        elif os_type == "linux":
            run_command("curl -fsSL https://mise.run | sh", shell=True)
            print_color("Mise installed. Update your PATH or restart terminal if 'mise' not found.", Colors.WARNING)
            os.environ["PATH"] = os.path.expanduser("~/.local/bin") + os.pathsep + os.environ["PATH"]
        elif os_type == "windows": run_command(["winget", "install", "-e", "--id", "jdx.mise"])
        else:
            print_color(f"Mise auto-install not supported for OS: {os_type}", Colors.FAIL); return False
        print_color("Mise installation attempted.", Colors.OKGREEN)
    except: print_color("Error during Mise installation.", Colors.FAIL); return False
    return check_mise()

def install_supabase_cli(os_type):
    print_color("\nAttempting to install Supabase CLI...", Colors.OKBLUE)
    try:
        if os_type == "macos": run_command(["brew", "install", "supabase/tap/supabase"])
        elif os_type == "linux":
            run_command("curl -sSL https://github.com/supabase/cli/releases/latest/download/supabase_linux_amd64.deb -o supabase.deb", shell=True)
            print_color("Downloaded Supabase CLI. Sudo password may be required for installation.", Colors.OKBLUE)
            run_command("sudo apt-get update && sudo apt-get install -y ./supabase.deb", shell=True)
            run_command("rm supabase.deb", shell=True)
        elif os_type == "windows": run_command(["winget", "install", "-e", "--id", "Supabase.CLI"])
        else:
            print_color(f"Supabase CLI auto-install not supported for OS: {os_type}", Colors.FAIL); return False
        print_color("Supabase CLI installation attempted.", Colors.OKGREEN)
    except: print_color("Error during Supabase CLI installation.", Colors.FAIL); return False
    return check_supabase_cli()

def leer_config_completa() -> Dict[str, Any]:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f: return json.load(f)
        except json.JSONDecodeError:
            print_color(f"Warning: Config file '{CONFIG_FILE}' corrupted. Using empty config.", Colors.WARNING)
            return {}
    return {}

# Refactored gestionar_config
def gestionar_config(clave: str, descripcion: str, es_secreto: bool,
                     valor_predeterminado: Optional[str], current_config: dict,
                     ask_use_saved: bool = True) -> str:
    """Manages a single configuration item by operating on current_config dict."""
    valor_actual = current_config.get(clave)

    if ask_use_saved and valor_actual is not None:
        display_val = '(hidden)' if es_secreto else f"'{valor_actual}'"
        prompt_text = f"Saved value for {descripcion} ({display_val}). Use it? (Y/n): "
        if input_color(prompt_text, Colors.WARNING).strip().lower() in ['y', '']:
            return valor_actual

    if valor_predeterminado is not None and valor_actual is None : # Use default if no current value from file or user declined saved
        print_color(f"Using default value for {descripcion}: {'(hidden)' if es_secreto else valor_predeterminado}", Colors.OKBLUE)
        current_config[clave] = valor_predeterminado
        return valor_predeterminado

    prompt_text = f"Enter {descripcion}: "
    if es_secreto:
        print_color(prompt_text, Colors.OKCYAN, input_color_code=Colors.ENDC) # Print the prompt text first
        print_color("(Input will be hidden for security)", Colors.OKCYAN) # Then the notification
        new_value = getpass.getpass("") # Actual hidden input call
    else:
        new_value = input_color(prompt_text, Colors.OKCYAN)
    
    current_config[clave] = new_value
    return new_value

# New gestionar_grupo_config
def gestionar_grupo_config(group_name: str, keys_info: List[Dict[str, Any]],
                           global_config: Dict[str, Any], setup_mode: str) -> None:
    print_color(f"\n--- {group_name} Configuration ---", Colors.HEADER)

    all_keys_exist = all(key_info["clave"] in global_config for key_info in keys_info)

    if all_keys_exist:
        print_color(f"Saved settings found for {group_name}:", Colors.OKBLUE)
        for key_info in keys_info:
            display_val = '(hidden)' if key_info["es_secreto"] and global_config.get(key_info["clave"]) else global_config.get(key_info["clave"], 'Not set')
            print_color(f"  - {key_info['descripcion']}: {display_val}", Colors.OKCYAN)

        use_group_saved = input_color(f"Do you want to use all currently saved settings for {group_name}? (Y/n): ", Colors.WARNING).strip().lower()
        if use_group_saved in ['y', '']:
            print_color(f"Using saved settings for {group_name}.", Colors.OKGREEN)
            return # Skip individual configuration for this group

    print_color(f"Proceeding with individual configuration for {group_name}...", Colors.OKBLUE)
    for key_info in keys_info:
        default_key = 'default_local' if setup_mode == "local" else 'default_daytona'
        valor_predeterminado = key_info.get(default_key)

        # If group settings were declined, 'ask_use_saved' should be True for individual items.
        # If not all keys existed for group prompt, also ask for individual items.
        ask_individual = True
        gestionar_config(key_info['clave'], key_info['descripcion'],
                         key_info['es_secreto'], valor_predeterminado,
                         global_config, ask_use_saved=ask_individual)

def main():
    try:
        print_color("--- Welcome to Blinker Setup ---", Colors.HEADER + Colors.BOLD)

        # Ensure .blinker_config and .env are in .gitignore
        gitignore_path = ".gitignore"
        entries_to_ignore = [CONFIG_FILE, ".env"]
        added_to_gitignore = []
        current_gitignore_content = ""

        if os.path.exists(gitignore_path):
            with open(gitignore_path, 'r') as f_read:
                current_gitignore_content = f_read.read()

        with open(gitignore_path, 'a+') as f_append: # Open in append mode, create if not exists
            for entry in entries_to_ignore:
                # Check if entry (as a whole line) is present to avoid partial matches
                if not re.search(rf"^{re.escape(entry)}$", current_gitignore_content, re.MULTILINE):
                    f_append.seek(0, os.SEEK_END) # Go to the end of the file
                    if f_append.tell() > 0 and current_gitignore_content[-1] != '\n' : # Check if file is not empty and last char is not newline
                        f_append.write("\n") # Add newline if file doesn't end with one
                    elif f_append.tell() == 0: # File was empty or just created
                        pass # No newline needed before first entry
                    else: # File not empty and ends with newline
                         pass # No newline needed
                    f_append.write(f"{entry}\n")
                    added_to_gitignore.append(entry)

        if added_to_gitignore:
            print_color(f"Ensured {', '.join(added_to_gitignore)} {'are' if len(added_to_gitignore) > 1 else 'is'} in .gitignore to protect sensitive information.", Colors.OKGREEN)

        os_type = get_os()
        print_color(f"Operating System detected: {os_type}", Colors.OKBLUE)
        if os_type == "unknown": print_color("Unsupported OS. Exiting.", Colors.FAIL); return

        if not check_docker(): return
        if not check_mise() and (input_color("Mise not found. Install? (Y/n): ", Colors.WARNING).lower() not in ['y', ''] or not install_mise(os_type)):
            print_color("Mise required. Install manually and restart.", Colors.FAIL); return
        if not check_supabase_cli() and (input_color("Supabase CLI not found. Install? (Y/n): ", Colors.WARNING).lower() not in ['y', ''] or not install_supabase_cli(os_type)):
            print_color("Supabase CLI required. Install manually and restart.", Colors.FAIL); return
        print_color("\n--- All essential dependencies checked/installed. ---", Colors.OKGREEN + Colors.BOLD)

        global_config = leer_config_completa()

        print_color("\n--- Blinker Setup Mode ---", Colors.HEADER)
        print_color("Choose how Blinker runs: locally via Docker, or for Daytona (advanced).", Colors.OKCYAN)
        
        # SETUP_MODE is handled individually as it determines defaults for other groups
        setup_mode_default = global_config.get("SETUP_MODE", "local") # Default to local if nothing saved
        current_setup_mode = ""
        while True:
            choice_prompt = f"Choose installation mode (current default: {setup_mode_default}): 1. Local (Docker) 2. Daytona: "
            choice = input_color(choice_prompt, Colors.WARNING).strip().lower()
            temp_mode_choice = ""
            if choice == '1' or choice == 'local': temp_mode_choice = "local"
            elif choice == '2' or choice == 'daytona': temp_mode_choice = "daytona"
            elif choice == '' : # User pressed Enter, use current default
                temp_mode_choice = setup_mode_default
                print_color(f"Using default mode: {temp_mode_choice}", Colors.OKBLUE)
            else: print_color("Invalid choice. Enter 1 or 2, or press Enter for default.", Colors.FAIL); continue

            # Update global_config directly with the choice for SETUP_MODE
            global_config["SETUP_MODE"] = temp_mode_choice
            current_setup_mode = temp_mode_choice # This is the chosen/confirmed mode
            print_color(f"Blinker will be set up in '{current_setup_mode}' mode.", Colors.OKGREEN)
            break
        
        supabase_keys_info = [
            {"clave": "SUPABASE_URL", "descripcion": "Supabase Project URL", "es_secreto": False,
             "default_local": "http://localhost:54321", "default_daytona": None},
            {"clave": "SUPABASE_ANON_KEY", "descripcion": "Supabase Anon Key", "es_secreto": False,
             "default_local": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43NBqxLiCRs4MFMSumSBOMזה", "default_daytona": None},
            {"clave": "SUPABASE_SERVICE_ROLE_KEY", "descripcion": "Supabase Service Role Key", "es_secreto": True,
             "default_local": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0.EGIM96RAZx35lJzdJsyH-qQwv8Hdp7fsn3W0YpN81IU", "default_daytona": None}
        ]
        if current_setup_mode == "daytona": # No defaults for daytona mode, prompt user for cloud details
            print_color("For Daytona/Cloud mode, please provide your Supabase Cloud project details.", Colors.OKCYAN)
            for k_info in supabase_keys_info: k_info['default_local'] = None # Nullify local defaults

        gestionar_grupo_config("Supabase", supabase_keys_info, global_config, current_setup_mode)

        print_color("\n--- Optional API Keys ---", Colors.HEADER)
        tools_api_keys = [
            {"name": "Zillow", "key": "RAPIDAPI_KEY_ZILLOW", "desc": "RapidAPI Key for Zillow"},
            {"name": "Twitter", "key": "TWITTER_API_KEY", "desc": "Twitter API Key"}
        ]
        for tool_spec in tools_api_keys:
            prompt_text = f"Configure {tool_spec['name']} API Key? (Y/n): "
            configure_tool = input_color(prompt_text, Colors.WARNING).strip().lower()
            if configure_tool == 'y' or configure_tool == '':
                gestionar_config(tool_spec['key'], tool_spec['desc'], True, None, global_config)
        
        # Save all configurations at the end
        try:
            with open(CONFIG_FILE, 'w') as f: json.dump(global_config, f, indent=4)
            print_color(f"\nAll configurations saved to '{CONFIG_FILE}'.", Colors.OKGREEN + Colors.BOLD)
        except IOError as e:
            print_color(f"Critical Error: Failed to save configurations to '{CONFIG_FILE}': {e}", Colors.FAIL + Colors.BOLD)
            return # Cannot proceed if config saving fails

        print_color("\n--- Generating .env file ---", Colors.HEADER)
        env_lines = [f"NEXT_PUBLIC_SUPABASE_URL={global_config.get('SUPABASE_URL', '')}",
                     f"NEXT_PUBLIC_SUPABASE_ANON_KEY={global_config.get('SUPABASE_ANON_KEY', '')}",
                     f"SUPABASE_SERVICE_ROLE_KEY={global_config.get('SUPABASE_SERVICE_ROLE_KEY', '')}",
                     f"BLINKER_SETUP_MODE={global_config.get('SETUP_MODE', 'local')}"]
        if global_config.get("SETUP_MODE") == "local":
            env_lines.append("NEXT_PUBLIC_API_URL=http://localhost:8000")
        for tool_spec in tools_api_keys:
            if tool_spec['key'] in global_config:
                env_lines.append(f"{tool_spec['key']}={global_config[tool_spec['key']]}")
        with open(".env", "w") as f: f.write("\n".join(env_lines) + "\n")
        print_color(".env file generated successfully.", Colors.OKGREEN)

        print_color("\n--- Starting Services ---", Colors.HEADER + Colors.BOLD)
        print_color("Ensuring tool versions with Mise...", Colors.OKBLUE)
        run_command(["mise", "install"])
        print_color("Installing Python dependencies...", Colors.OKBLUE)
        run_command([sys.executable, "-m", "pip", "install", "-r", "backend/requirements.txt"])
        print_color("Installing Frontend dependencies (this may take a while)...", Colors.OKBLUE)
        run_command(["npm", "install", "--prefix", "frontend"])

        if global_config.get("SETUP_MODE") == "local":
            print_color("\n--- Setting up Local Environment with Docker ---", Colors.HEADER)
            print_color("Starting local Supabase instance...", Colors.OKBLUE)
            run_command(["supabase", "start"])
            print_color("Supabase started. Waiting for stabilization...", Colors.OKBLUE); time.sleep(10)
            print_color("Resetting local DB & applying migrations...", Colors.OKBLUE)
            run_command(["supabase", "db", "reset", "--local"])
            print_color("Building and starting Docker containers...", Colors.OKBLUE)
            run_command(["docker-compose", "up", "--build", "-d"])
            print_color("Docker containers started.", Colors.OKGREEN)
            print_color("\nMonitoring container startup...", Colors.OKBLUE); time.sleep(5)
            ps_result = run_command(["docker-compose", "ps"], capture_output_default=True, text_default=True)
            if ps_result and ps_result.stdout:
                lines = ps_result.stdout.strip().split('\n')
                services_status = {}
                if len(lines) > 2:
                    for line in lines[2:]:
                        parts = line.split(); service_name = parts[0]; service_state = " ".join(parts[2:])
                        ok_color = Colors.OKGREEN if "up" in service_state.lower() or "running" in service_state.lower() or "healthy" in service_state.lower() else Colors.FAIL
                        services_status[service_name] = ok_color + service_state
                print_color("Docker Compose Services Status:", Colors.HEADER)
                for name, status in services_status.items(): print_color(f"  - {name}: {status}", Colors.OKCYAN)
                expected_services = ["frontend", "backend"]
                all_ok = all(any(srv in k and Colors.OKGREEN in v for k,v in services_status.items()) for srv in expected_services)
                if all_ok:
                    print_color("\n--- Blinker is Ready! ---", Colors.OKGREEN + Colors.BOLD)
                    print_color("  Frontend: http://localhost:3000", Colors.OKCYAN)
                    print_color("  Backend API: http://localhost:8000", Colors.OKCYAN)
                else:
                    print_color("\nWARNING: Some services may not be running correctly. Check 'docker-compose logs'.", Colors.WARNING)
        elif global_config.get("SETUP_MODE") == "daytona":
            print_color("\n--- Daytona Mode Setup Complete ---", Colors.OKGREEN + Colors.BOLD)
            print_color("Blinker configured for Daytona. Deploy via Daytona-specific instructions.", Colors.OKCYAN)

        print_color("\n--- Setup Finished ---", Colors.HEADER + Colors.BOLD)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print_color("\nA critical command failed or was not found. Setup cannot continue.", Colors.FAIL + Colors.BOLD)
    except Exception as e:
        print_color(f"\nAn unexpected error occurred: {e}", Colors.FAIL + Colors.BOLD)

if __name__ == "__main__":
    main()
