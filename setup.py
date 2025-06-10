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

def _parse_pip_error_for_failed_packages(stderr_output: str) -> List[str]:
    """Parses pip stderr output to find names of packages that failed to install."""
    failed_packages = set()
    # Regex for "Could not find a version that satisfies the requirement" and "No matching distribution found for"
    # This pattern tries to capture the package name, which might include extras like 'package[extra]'
    # It looks for the package name following the specific pip error phrases.
    patterns = [
        re.compile(r"Could not find a version that satisfies the requirement\s+([a-zA-Z0-9-_.]+\[?[a-zA-Z0-9-_.,]*]?|[a-zA-Z0-9-_.]+)"),
        re.compile(r"No matching distribution found for\s+([a-zA-Z0-9-_.]+\[?[a-zA-Z0-9-_.,]*]?|[a-zA-Z0-9-_.]+)"),
        # More generic pattern if specific text not found but line contains "Could not find" / "No matching distribution"
        # This is a fallback and might be less precise.
        # re.compile(r"(?:Could not find|No matching distribution).*? for\s*([a-zA-Z0-9-_.]+\[?[a-zA-Z0-9-_.,]*]?|[a-zA-Z0-9-_.]+)")
    ]
    for line in stderr_output.splitlines():
        # Try specific patterns first
        found_specific = False
        for pattern in patterns[:2]: # First two are more specific
            match = pattern.search(line)
            if match:
                failed_packages.add(match.group(1).strip())
                found_specific = True
                break
        # If specific patterns don't match, try a more general one if needed (commented out for now)
        # if not found_specific and len(patterns) > 2:
        #     match = patterns[2].search(line)
        #     if match:
        #         failed_packages.add(match.group(1).strip())

    # A common way pip lists missing packages in summaries is "package1, package2, ... from project"
    # This is a heuristic if the above don't catch structured errors well for some pip versions.
    summary_pattern = re.compile(r"The following required packages are not installed:\s*(.+)")
    summary_match = summary_pattern.search(stderr_output)
    if summary_match:
        pkgs_text = summary_match.group(1)
        # Split by common delimiters like comma, space, 'and'
        potential_pkgs = re.split(r'[,\s]+(?:and\s+)?', pkgs_text)
        for pkg in potential_pkgs:
            pkg_clean = pkg.strip().replace("'", "") # Remove quotes
            if pkg_clean and not any(op in pkg_clean for op in ['<','>','=','!']): # Avoid version constraints
                failed_packages.add(pkg_clean)

    return list(failed_packages)


def run_command(command, check=True, shell=False, cwd=None, capture_output_default=False, text_default=False):
    """Helper function to run a subprocess command with error handling and real-time output for pip install."""

    # Prepare command_list and executable_path based on shell True/False
    if shell:
        # For shell=True, Popen/run expect a string. executable is usually figured out by the shell,
        # but can be specified (e.g. /bin/bash).
        command_str = command if isinstance(command, str) else " ".join(command)
        executable_path = "/bin/bash" if platform.system().lower() in ["linux", "darwin"] else None
        cmd_to_log_and_popen = command_str
    else:
        command_list = command if isinstance(command, list) else command.split()
        executable_path = None # Not typically used when shell=False and command is a list.
                               # First item in list is the executable.
        cmd_to_log_and_popen = command_list

    is_pip_install = isinstance(cmd_to_log_and_popen, list) and \
                     (cmd_to_log_and_popen[0] == "pip" or \
                      (cmd_to_log_and_popen[0] == sys.executable and "-m" in cmd_to_log_and_popen and "pip" in cmd_to_log_and_popen)) and \
                     "install" in cmd_to_log_and_popen

    print_color(f"Executing: {' '.join(cmd_to_log_and_popen) if isinstance(cmd_to_log_and_popen, list) else cmd_to_log_and_popen}", Colors.OKBLUE)

    try:
        if is_pip_install:
            print_color("Streaming output for pip install...", Colors.OKCYAN)
            # Ensure command for Popen is a list if not shell=True
            popen_cmd = cmd_to_log_and_popen if not shell else shlex.split(cmd_to_log_and_popen) if platform.system() != "Windows" else cmd_to_log_and_popen

            process = subprocess.Popen(
                popen_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1, # Line-buffered
                shell=shell, # Use the passed shell value
                cwd=cwd,
                executable=executable_path if shell else None # executable is for shell=True mostly
            )

            if process.stdout:
                for line in iter(process.stdout.readline, ''):
                    print(line, end='') # Print line by line as it comes
                process.stdout.close()

            stderr_output = ""
            if process.stderr:
                stderr_output = process.stderr.read()
                process.stderr.close()

            return_code = process.wait()

            if check and return_code != 0:
                print_color(f"Command failed with exit code {return_code}", Colors.FAIL)

                failed_packages = _parse_pip_error_for_failed_packages(stderr_output)
                if failed_packages:
                    print_color("--------------------------------------------------------------------", Colors.FAIL)
                    print_color("ERROR: PIP INSTALLATION FAILED", Colors.FAIL + Colors.BOLD)
                    print_color("--------------------------------------------------------------------", Colors.FAIL)
                    print_color("The following package(s) seem to have caused issues:", Colors.WARNING)
                    for pkg in failed_packages: print_color(f"  - {pkg}", Colors.WARNING)
                    print_color("\nCommon reasons for pip install failures:", Colors.OKCYAN)
                    print_color("  1. Misspelled package name in 'backend/requirements.txt'.", Colors.OKCYAN)
                    print_color("  2. Package not available on PyPI (public repository) or version doesn't exist.", Colors.OKCYAN)
                    print_color("  3. Package is private and requires special configuration (e.g., custom index URL, auth).", Colors.OKCYAN)
                    print_color("  4. Version conflicts with other packages or your Python version.", Colors.OKCYAN)
                    print_color("  5. Network issues preventing download from package indexes.", Colors.OKCYAN)
                    print_color("  6. Missing system-level dependencies required by the package for compilation.", Colors.OKCYAN)
                    print_color("\nPlease check 'backend/requirements.txt', package availability, and error messages below.", Colors.OKCYAN)
                    if stderr_output: print_color(f"\nFull pip error output:\n{stderr_output.strip()}", Colors.FAIL)
                    print_color("--------------------------------------------------------------------", Colors.FAIL)
                elif stderr_output: # Not a recognized "package not found" error, but still a pip error
                    print_color(f"Pip installation failed. Full error output:\n{stderr_output.strip()}", Colors.FAIL)

                raise subprocess.CalledProcessError(return_code, cmd_to_log_and_popen, output="<stdout streamed>", stderr=stderr_output)

            return subprocess.CompletedProcess(cmd_to_log_and_popen, return_code, stdout="<stdout streamed>", stderr=stderr_output)

        else: # Original subprocess.run logic for non-pip-install commands
            effective_capture_output = capture_output_default
            effective_text = text_default
            process = subprocess.run(
                cmd_to_log_and_popen,
                check=False,
                shell=shell,
                cwd=cwd,
                capture_output=effective_capture_output,
                text=effective_text,
                executable=executable_path if shell else None
            )
            if effective_capture_output and process.stdout:
                print_color(process.stdout, Colors.OKGREEN)

            if check and process.returncode != 0:
                print_color(f"Error executing command: {' '.join(cmd_to_log_and_popen) if isinstance(cmd_to_log_and_popen, list) else cmd_to_log_and_popen}", Colors.FAIL)
                print_color(f"Return code: {process.returncode}", Colors.FAIL)
                if effective_capture_output:
                    if process.stdout: print_color(f"Stdout (if any):\n{process.stdout.strip()}", Colors.FAIL)
                    if process.stderr: print_color(f"Stderr:\n{process.stderr.strip()}", Colors.FAIL)
                raise subprocess.CalledProcessError(process.returncode, cmd_to_log_and_popen, output=process.stdout, stderr=process.stderr)
            return process

    except FileNotFoundError:
        cmd_name = cmd_to_log_and_popen[0] if isinstance(cmd_to_log_and_popen, list) else cmd_to_log_and_popen.split()[0]
        print_color(f"Error: Command not found - {cmd_name}. Ensure it's installed and in PATH.", Colors.FAIL)
        raise
    except Exception as e: # Catch any other unexpected error during command execution
        print_color(f"An unexpected error occurred while trying to run command: {e}", Colors.FAIL)
        raise

# ... (rest of the script remains the same, ensure shlex is imported if using it for Popen with shell=True)
# import shlex # Add this if shlex.split is used for shell=True with Popen (safer for complex commands)
# For now, the Popen part for shell=True passes the string command directly, which is typical.

# ... (rest of the file from get_os() downwards) ...

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

def gestionar_config(clave: str, descripcion: str, es_secreto: bool,
                     valor_predeterminado: Optional[str], current_config: dict,
                     ask_use_saved: bool = True) -> str:
    valor_actual = current_config.get(clave)
    if ask_use_saved and valor_actual is not None:
        display_val = '(hidden)' if es_secreto else f"'{valor_actual}'"
        prompt_text = f"Saved value for {descripcion} ({display_val}). Use it? (Y/n): "
        if input_color(prompt_text, Colors.WARNING).strip().lower() in ['y', '']:
            return valor_actual
    if valor_predeterminado is not None and valor_actual is None :
        print_color(f"Using default value for {descripcion}: {'(hidden)' if es_secreto else valor_predeterminado}", Colors.OKBLUE)
        current_config[clave] = valor_predeterminado
        return valor_predeterminado
    prompt_text = f"Enter {descripcion}: "
    if es_secreto:
        print_color(prompt_text, Colors.OKCYAN, input_color_code=Colors.ENDC)
        print_color("(Input will be hidden for security)", Colors.OKCYAN)
        new_value = getpass.getpass("")
    else:
        new_value = input_color(prompt_text, Colors.OKCYAN)
    current_config[clave] = new_value
    return new_value

def gestionar_grupo_config(group_name: str, keys_info: List[Dict[str, Any]],
                           global_config: Dict[str, Any], setup_mode: str) -> None:
    print_color(f"\n--- {group_name} Configuration ---", Colors.HEADER)
    all_keys_exist = all(key_info["clave"] in global_config for key_info in keys_info)
    if all_keys_exist:
        print_color(f"Saved settings found for {group_name}:", Colors.OKBLUE)
        for key_info in keys_info:
            display_val = '(hidden)' if key_info["es_secreto"] and global_config.get(key_info["clave"]) else global_config.get(key_info["clave"], 'Not set')
            print_color(f"  - {key_info['descripcion']}: {display_val}", Colors.OKCYAN)
        use_group_saved = input_color(f"Use all saved settings for {group_name}? (Y/n): ", Colors.WARNING).strip().lower()
        if use_group_saved in ['y', '']:
            print_color(f"Using saved settings for {group_name}.", Colors.OKGREEN); return
    print_color(f"Proceeding with individual configuration for {group_name}...", Colors.OKBLUE)
    for key_info in keys_info:
        default_key = 'default_local' if setup_mode == "local" else 'default_daytona'
        valor_predeterminado = key_info.get(default_key)
        gestionar_config(key_info['clave'], key_info['descripcion'],
                         key_info['es_secreto'], valor_predeterminado,
                         global_config, ask_use_saved=True)

def main():
    try:
        print_color("--- Welcome to Blinker Setup ---", Colors.HEADER + Colors.BOLD)
        gitignore_path = ".gitignore"; entries_to_ignore = [CONFIG_FILE, ".env"]; added_to_gitignore = []
        current_gitignore_content = ""
        if os.path.exists(gitignore_path):
            with open(gitignore_path, 'r') as f_read: current_gitignore_content = f_read.read()
        with open(gitignore_path, 'a+') as f_append:
            for entry in entries_to_ignore:
                if not re.search(rf"^{re.escape(entry)}$", current_gitignore_content, re.MULTILINE):
                    f_append.seek(0, os.SEEK_END)
                    if f_append.tell() > 0 and current_gitignore_content and current_gitignore_content[-1] != '\n': f_append.write("\n")
                    f_append.write(f"{entry}\n"); added_to_gitignore.append(entry)
        if added_to_gitignore: print_color(f"Ensured {', '.join(added_to_gitignore)} in .gitignore.", Colors.OKGREEN)

        os_type = get_os()
        print_color(f"OS detected: {os_type}", Colors.OKBLUE)
        if os_type == "unknown": print_color("Unsupported OS. Exiting.", Colors.FAIL); return

        if not check_docker(): return
        if not check_mise() and (input_color("Mise not found. Install? (Y/n): ", Colors.WARNING).lower() not in ['y', ''] or not install_mise(os_type)):
            print_color("Mise required. Install manually.", Colors.FAIL); return
        if not check_supabase_cli() and (input_color("Supabase CLI not found. Install? (Y/n): ", Colors.WARNING).lower() not in ['y', ''] or not install_supabase_cli(os_type)):
            print_color("Supabase CLI required. Install manually.", Colors.FAIL); return
        print_color("\n--- Dependencies OK. ---", Colors.OKGREEN + Colors.BOLD)

        global_config = leer_config_completa()
        print_color("\n--- Blinker Setup Mode ---", Colors.HEADER)
        setup_mode_default = global_config.get("SETUP_MODE", "local")
        current_setup_mode = ""
        while True:
            choice = input_color(f"Mode (default: {setup_mode_default}): 1. Local 2. Daytona: ", Colors.WARNING).strip().lower()
            temp_mode_choice = {"1": "local", "local": "local", "2": "daytona", "daytona": "daytona", "": setup_mode_default}.get(choice)
            if not temp_mode_choice: print_color("Invalid choice.", Colors.FAIL); continue
            global_config["SETUP_MODE"] = temp_mode_choice; current_setup_mode = temp_mode_choice
            print_color(f"Mode set to '{current_setup_mode}'.", Colors.OKGREEN); break

        supabase_keys = [
            {"clave": "SUPABASE_URL", "desc": "URL", "sec": False, "loc": "http://localhost:54321"},
            {"clave": "SUPABASE_ANON_KEY", "desc": "Anon Key", "sec": False, "loc": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43NBqxLiCRs4MFMSumSBOMזה"},
            {"clave": "SUPABASE_SERVICE_ROLE_KEY", "desc": "Service Role Key", "sec": True, "loc": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0.EGIM96RAZx35lJzdJsyH-qQwv8Hdp7fsn3W0YpN81IU"}
        ]
        supabase_keys_info_updated = [{"clave": k["clave"], "descripcion": f"Supabase {k['desc']}", "es_secreto": k["sec"],
                                   "default_local": k["loc"], "default_daytona": None} for k in supabase_keys]
        if current_setup_mode == "daytona":
            print_color("For Daytona mode, provide Supabase Cloud details.", Colors.OKCYAN)
            for k_info in supabase_keys_info_updated: k_info['default_local'] = None
        gestionar_grupo_config("Supabase", supabase_keys_info_updated, global_config, current_setup_mode)

        print_color("\n--- Optional API Keys ---", Colors.HEADER)
        tools_api_keys = [{"name": "Zillow", "key": "RAPIDAPI_KEY_ZILLOW", "desc": "RapidAPI Key for Zillow"},
                          {"name": "Twitter", "key": "TWITTER_API_KEY", "desc": "Twitter API Key"}]
        for tool in tools_api_keys:
            if input_color(f"Configure {tool['name']} API Key? (Y/n): ", Colors.WARNING).lower() in ['y', '']:
                gestionar_config(tool['key'], tool['desc'], True, None, global_config)

        with open(CONFIG_FILE, 'w') as f: json.dump(global_config, f, indent=4)
        print_color(f"\nConfigs saved to '{CONFIG_FILE}'.", Colors.OKGREEN + Colors.BOLD)

        print_color("\n--- Generating .env file ---", Colors.HEADER)
        env_vars = {f"NEXT_PUBLIC_SUPABASE_URL": global_config.get('SUPABASE_URL'),
                    f"NEXT_PUBLIC_SUPABASE_ANON_KEY": global_config.get('SUPABASE_ANON_KEY'),
                    f"SUPABASE_SERVICE_ROLE_KEY": global_config.get('SUPABASE_SERVICE_ROLE_KEY'),
                    f"BLINKER_SETUP_MODE": global_config.get('SETUP_MODE')}
        if global_config.get("SETUP_MODE") == "local": env_vars["NEXT_PUBLIC_API_URL"] = "http://localhost:8000"
        for tool in tools_api_keys:
            if tool['key'] in global_config: env_vars[tool['key']] = global_config[tool['key']]
        env_lines = [f"{k}={v}" for k, v in env_vars.items() if v is not None]
        with open(".env", "w") as f: f.write("\n".join(env_lines) + "\n")
        print_color(".env generated.", Colors.OKGREEN)

        print_color("\n--- Starting Services ---", Colors.HEADER + Colors.BOLD)
        print_color("Mise ensuring tool versions...", Colors.OKBLUE); run_command(["mise", "install"])
        print_color("Installing Python deps...", Colors.OKBLUE); run_command([sys.executable, "-m", "pip", "install", "-r", "backend/requirements.txt"])
        print_color("Installing Frontend deps...", Colors.OKBLUE); run_command(["npm", "install", "--prefix", "frontend"])

        if global_config.get("SETUP_MODE") == "local":
            print_color("\n--- Setting up Local Docker Env ---", Colors.HEADER)
            print_color("Starting Supabase...", Colors.OKBLUE); run_command(["supabase", "start"])
            print_color("Supabase started. Stabilizing...", Colors.OKBLUE); time.sleep(10)
            print_color("Resetting DB & migrations...", Colors.OKBLUE); run_command(["supabase", "db", "reset", "--local"])
            print_color("Starting Docker containers...", Colors.OKBLUE); run_command(["docker-compose", "up", "--build", "-d"])
            print_color("Docker containers started.", Colors.OKGREEN)
            print_color("\nMonitoring containers...", Colors.OKBLUE); time.sleep(5)
            ps_result = run_command(["docker-compose", "ps"], capture_output_default=True, text_default=True)
            if ps_result and ps_result.stdout:
                lines = ps_result.stdout.strip().split('\n')
                services_status = {}
                if len(lines) > 2:
                    for line in lines[2:]:
                        parts = line.split(); s_name = parts[0]; s_state = " ".join(parts[2:])
                        ok_col = Colors.OKGREEN if any(k in s_state.lower() for k in ["up", "running", "healthy"]) else Colors.FAIL
                        services_status[s_name] = ok_col + s_state
                print_color("Docker Services Status:", Colors.HEADER)
                for name, status in services_status.items(): print_color(f"  - {name}: {status}", Colors.OKCYAN)
                expected = ["frontend", "backend"]
                all_ok = all(any(s in k and Colors.OKGREEN in v for k,v in services_status.items()) for s in expected)
                if all_ok:
                    print_color("\n--- Blinker is Ready! ---", Colors.OKGREEN + Colors.BOLD)
                    print_color("  Frontend: http://localhost:3000", Colors.OKCYAN)
                    print_color("  Backend API: http://localhost:8000", Colors.OKCYAN)
                else: print_color("\nWARNING: Some services may not be running. Check 'docker-compose logs'.", Colors.WARNING)
        elif global_config.get("SETUP_MODE") == "daytona":
            print_color("\n--- Daytona Mode Setup Complete ---", Colors.OKGREEN + Colors.BOLD)
            print_color("Blinker configured for Daytona. Deploy via Daytona instructions.", Colors.OKCYAN)

        print_color("\n--- Setup Finished ---", Colors.HEADER + Colors.BOLD)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print_color("\nCritical command error. Setup failed.", Colors.FAIL + Colors.BOLD)
    except Exception as e:
        print_color(f"\nUnexpected error: {e}", Colors.FAIL + Colors.BOLD)

if __name__ == "__main__":
    main()
