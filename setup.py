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
from typing import Optional, Dict, List, Any
import shlex # For safely splitting commands for Popen if shell=True and command is a string

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
    failed_packages = set()
    patterns = [
        re.compile(r"Could not find a version that satisfies the requirement\s+([a-zA-Z0-9-_.]+\[?[a-zA-Z0-9-_.,]*]?|[a-zA-Z0-9-_.]+)"),
        re.compile(r"No matching distribution found for\s+([a-zA-Z0-9-_.]+\[?[a-zA-Z0-9-_.,]*]?|[a-zA-Z0-9-_.]+)"),
    ]
    for line in stderr_output.splitlines():
        for pattern in patterns:
            match = pattern.search(line)
            if match:
                failed_packages.add(match.group(1).strip()); break
    summary_pattern = re.compile(r"The following required packages are not installed:\s*(.+)")
    summary_match = summary_pattern.search(stderr_output)
    if summary_match:
        pkgs_text = summary_match.group(1)
        potential_pkgs = re.split(r'[,\s]+(?:and\s+)?', pkgs_text)
        for pkg in potential_pkgs:
            pkg_clean = pkg.strip().replace("'", "")
            if pkg_clean and not any(op in pkg_clean for op in ['<','>','=','!']):
                failed_packages.add(pkg_clean)
    return list(failed_packages)

def run_command(command: List[str] | str, check=True, shell=False, cwd=None,
                capture_output_default=False, text_default=False, stream_output=False):

    cmd_to_log = ""
    actual_cmd_for_popen_or_run = None
    executable_path = None

    if shell:
        cmd_to_log = command if isinstance(command, str) else " ".join(command)
        actual_cmd_for_popen_or_run = cmd_to_log
        executable_path = "/bin/bash" if platform.system().lower() in ["linux", "darwin"] else None
    else:
        cmd_to_log_list = command if isinstance(command, list) else shlex.split(command)
        cmd_to_log = " ".join(cmd_to_log_list)
        actual_cmd_for_popen_or_run = cmd_to_log_list

    is_pip_install = isinstance(actual_cmd_for_popen_or_run, list) and \
                     (actual_cmd_for_popen_or_run[0] == "pip" or \
                      (actual_cmd_for_popen_or_run[0] == sys.executable and "-m" in actual_cmd_for_popen_or_run and "pip" in actual_cmd_for_popen_or_run)) and \
                     "install" in actual_cmd_for_popen_or_run

    try:
        if stream_output or is_pip_install:
            if is_pip_install:
                print_color(f"Executing & Streaming output for pip install: {cmd_to_log}", Colors.OKBLUE)
            elif stream_output:
                print_color(f"Executing & Streaming output for: {cmd_to_log}", Colors.OKBLUE)

            process = subprocess.Popen(
                actual_cmd_for_popen_or_run,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1, # This makes it line-buffered if text=True. For char-by-char, text=False and manual decode might be needed for some platforms.
                           # However, text=True and bufsize=1 with read(1) on stdout often works.
                shell=shell,
                cwd=cwd,
                executable=executable_path if shell else None
            )

            streamed_stdout_chars = []
            if process.stdout:
                while True:
                    char = process.stdout.read(1) # Attempt to read one character
                    if char == '' and process.poll() is not None: # End of stream and process has finished
                        break
                    if char:
                        print(char, end='', flush=True)
                        streamed_stdout_chars.append(char)
                    else: # No char, process might still be running or finished between poll() and read(1)
                        if process.poll() is not None: # Check again if process exited
                            break
                        time.sleep(0.001) # Small sleep to prevent tight loop if process is genuinely paused or slow
                process.stdout.close()

            streamed_stdout_str = "".join(streamed_stdout_chars)

            stderr_output = ""
            if process.stderr:
                stderr_output = process.stderr.read()
                process.stderr.close()

            return_code = process.wait()

            if check and return_code != 0:
                print_color(f"Command failed with exit code {return_code}: {cmd_to_log}", Colors.FAIL)

                if is_pip_install:
                    failed_packages = _parse_pip_error_for_failed_packages(stderr_output)
                    if failed_packages:
                        print_color("--------------------------------------------------------------------", Colors.FAIL)
                        print_color("ERROR: PIP INSTALLATION FAILED", Colors.FAIL + Colors.BOLD)
                        print_color("The following package(s) seem to have caused issues:", Colors.WARNING)
                        for pkg in failed_packages: print_color(f"  - {pkg}", Colors.WARNING)
                        print_color("\nCommon reasons for pip install failures:", Colors.OKCYAN)
                        print_color("  1. Misspelled package name in 'backend/requirements.txt'.", Colors.OKCYAN)
                        print_color("  2. Package not available on PyPI or version doesn't exist.", Colors.OKCYAN)
                        print_color("  3. Private package (requires custom index URL, auth).", Colors.OKCYAN)
                        print_color("  4. Version conflicts or Python version incompatibility.", Colors.OKCYAN)
                        print_color("  5. Network issues or missing system-level dependencies.", Colors.OKCYAN)
                        if stderr_output: print_color(f"\nFull pip error output:\n{stderr_output.strip()}", Colors.FAIL)
                        print_color("--------------------------------------------------------------------", Colors.FAIL)
                    elif stderr_output:
                        print_color(f"Pip installation failed. Full error output:\n{stderr_output.strip()}", Colors.FAIL)
                elif stderr_output:
                     print_color(f"Error output:\n{stderr_output.strip()}", Colors.FAIL)

                raise subprocess.CalledProcessError(return_code, actual_cmd_for_popen_or_run, output=streamed_stdout_str, stderr=stderr_output)

            return subprocess.CompletedProcess(actual_cmd_for_popen_or_run, return_code, stdout=streamed_stdout_str, stderr=stderr_output)

        else:
            print_color(f"Executing: {cmd_to_log}", Colors.OKBLUE)
            effective_capture_output = capture_output_default
            effective_text = text_default
            process_obj = subprocess.run(
                actual_cmd_for_popen_or_run,
                check=False,
                shell=shell,
                cwd=cwd,
                capture_output=effective_capture_output,
                text=effective_text,
                executable=executable_path if shell else None
            )
            if effective_capture_output and process_obj.stdout:
                print_color(process_obj.stdout, Colors.OKGREEN)

            if check and process_obj.returncode != 0:
                print_color(f"Error executing command: {cmd_to_log}", Colors.FAIL)
                print_color(f"Return code: {process_obj.returncode}", Colors.FAIL)
                if effective_capture_output:
                    if process_obj.stdout: print_color(f"Stdout (if any):\n{process_obj.stdout.strip()}", Colors.FAIL)
                    if process_obj.stderr: print_color(f"Stderr:\n{process_obj.stderr.strip()}", Colors.FAIL)
                raise subprocess.CalledProcessError(process_obj.returncode, actual_cmd_for_popen_or_run, output=process_obj.stdout, stderr=process_obj.stderr)
            return process_obj

    except FileNotFoundError:
        cmd_name = actual_cmd_for_popen_or_run[0] if isinstance(actual_cmd_for_popen_or_run, list) and not shell else str(actual_cmd_for_popen_or_run).split()[0]
        print_color(f"Error: Command not found - {cmd_name}. Ensure it's installed and in PATH.", Colors.FAIL)
        raise
    except Exception as e:
        print_color(f"An unexpected error occurred while trying to run command '{cmd_to_log}': {e}", Colors.FAIL)
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

        print_color("\nEnsuring correct tool versions with Mise. This might take a few moments if new versions need to be downloaded/installed...", Colors.OKBLUE)
        run_command(["mise", "install"], stream_output=True)
        print_color("Mise tool versioning complete.", Colors.OKGREEN)

        print_color("\n--- Locating npm via mise ---", Colors.HEADER)
        npm_executable_path = ""
        try:
            mise_which_result = run_command(
                ["mise", "which", "npm"],
                capture_output_default=True,
                text_default=True,
                check=False
            )

            if mise_which_result.returncode == 0 and mise_which_result.stdout and mise_which_result.stdout.strip():
                npm_executable_path = mise_which_result.stdout.strip()
                print_color(f"Found npm executable via 'mise which npm': {npm_executable_path}", Colors.OKGREEN)

                print_color(f"Verifying {npm_executable_path} --version...", Colors.OKBLUE)
                npm_version_result = run_command(
                    [npm_executable_path, "--version"],
                    capture_output_default=True,
                    text_default=True,
                    check=False
                )
                if npm_version_result.returncode == 0:
                    print_color(f"npm version: {npm_version_result.stdout.strip()}", Colors.OKGREEN)
                else:
                    print_color(f"Warning: '{npm_executable_path} --version' failed. Will attempt to use it anyway.", Colors.WARNING)
                    if npm_version_result.stderr:
                         print_color(f"npm --version error: {npm_version_result.stderr.strip()}", Colors.WARNING)
            else:
                npm_executable_path = ""
                print_color("Failed to locate npm via 'mise which npm'.", Colors.FAIL)
                if mise_which_result.stderr:
                    print_color(f"'mise which npm' error: {mise_which_result.stderr.strip()}", Colors.FAIL)

        except Exception as e:
            print_color(f"An error occurred while trying to run 'mise which npm': {e}", Colors.FAIL)
            npm_executable_path = ""

        if not npm_executable_path:
            print_color("\n'npm' (Node Package Manager) could not be located via 'mise which npm'.", Colors.FAIL)
            print_color("This might indicate an issue with the Node.js installation managed by Mise, or 'mise.toml' might not correctly specify Node.js.", Colors.WARNING)
            print_color("\nPlease try the following steps:", Colors.WARNING)
            print_color("  1. Ensure 'mise.toml' in your project root correctly defines a Node.js version (e.g., 'nodejs = \"lts\"').", Colors.WARNING)
            print_color("  2. Run 'mise doctor' in your terminal to check for common issues with your Mise setup.", Colors.WARNING)
            print_color("  3. Try running 'mise install' again manually in your terminal.", Colors.WARNING)
            print_color("  4. As a next step, you might need to close this terminal, open a new one, navigate to the project directory, and re-run 'python setup.py'. This can help if PATH changes haven't taken effect.", Colors.WARNING)
            print_color("  5. If problems persist, verify your system's PATH environment variable includes the Mise shims directory (usually ~/.local/share/mise/shims or similar).", Colors.WARNING)
            print_color("\nSetup cannot continue without a working npm to install frontend dependencies.", Colors.FAIL)
            sys.exit(1)

        print_color("\nInstalling Python dependencies from backend/requirements.txt. This may take several minutes depending on network speed and package complexity...", Colors.OKBLUE)
        run_command([sys.executable, "-m", "pip", "install", "-r", "backend/requirements.txt"], stream_output=True)
        print_color("Python dependencies installation complete.", Colors.OKGREEN)

        print_color("\nInstalling frontend Node.js dependencies from frontend/package.json. This can also take some time...", Colors.OKBLUE)
        run_command([npm_executable_path, "install"], stream_output=True, cwd="frontend")
        print_color("Frontend dependencies installation complete.", Colors.OKGREEN)

        if global_config.get("SETUP_MODE") == "local":
            print_color("\n--- Setting up Local Docker Env ---", Colors.HEADER)

            print_color("\n--- Starting Local Supabase Services ---", Colors.HEADER)
            print_color("This is a crucial step that sets up your local development database, authentication, and other backend services using Docker.", Colors.OKCYAN)
            print_color("IMPORTANT: The *first time* this command runs, it needs to download several Docker images (PostgreSQL, GoTrue, etc.). This can take a significant amount of time (potentially 5-20 minutes or more) depending on your internet connection and system speed.", Colors.WARNING)
            print_color("You should see Docker image download progress messages below if this is the case. Please be patient, as it might seem like nothing is happening for periods if a large image layer is downloading or extracting.", Colors.WARNING)
            print_color("Subsequent starts of Supabase will be much faster.", Colors.OKCYAN)
            print_color("If it seems stuck for a very long time with no network activity shown here or in Docker Desktop, there might be an issue with your Docker setup or network access to Docker Hub.", Colors.WARNING)
            run_command(["supabase", "start"], stream_output=True)
            print_color("Supabase services started. Waiting a few seconds for stabilization...", Colors.OKBLUE); time.sleep(10)

            print_color("\nResetting local Supabase database and applying migrations...", Colors.OKBLUE)
            run_command(["supabase", "db", "reset", "--local"], stream_output=True)
            print_color("Database migrations applied successfully.", Colors.OKGREEN)

            print_color("\nBuilding Docker images. This can take some time, especially on first run. Output below is from the build command...", Colors.OKBLUE)
            run_command(["docker-compose", "build"], stream_output=True)
            print_color("Docker images built. Starting services in detached mode...", Colors.OKBLUE)
            run_command(["docker-compose", "up", "-d"])
            print_color("Docker containers started in background.", Colors.OKGREEN)

            print_color("\nMonitoring container startup...", Colors.OKBLUE); time.sleep(5)
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

[end of setup.py]
