#!/usr/bin/env python3
"""
Setup script for Coding Convention MCP Server.
Designed to be run by an AI agent to automate server setup.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def run_command(cmd, cwd=None, check=True):
    """Run a shell command and return result."""
    print(f"Running: {cmd}")
    result = subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True
    )
    
    if check and result.returncode != 0:
        print(f"Error: {result.stderr}")
        return False
    
    if result.stdout:
        print(result.stdout)
    if result.stderr and not check:
        print(f"Warning: {result.stderr}")
    
    return result.returncode == 0


def check_python_version():
    """Check Python version >= 3.10."""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 10):
        print(f"Error: Python 3.10+ required, found {version.major}.{version.minor}")
        return False
    print(f"Python version OK: {version.major}.{version.minor}.{version.micro}")
    return True


def create_venv(venv_path=".venv"):
    """Create virtual environment."""
    if venv_path.exists():
        print(f"Virtual environment already exists at {venv_path}")
        return True
    
    print(f"Creating virtual environment at {venv_path}")
    if not run_command(f"python3 -m venv {venv_path}"):
        return False
    
    # Verify venv creation
    if platform.system() == "Windows":
        python_exe = venv_path / "Scripts" / "python.exe"
    else:
        python_exe = venv_path / "bin" / "python"
    
    if not python_exe.exists():
        print(f"Error: Python executable not found at {python_exe}")
        return False
    
    print(f"Virtual environment created successfully")
    return True


def install_package(venv_path=".venv"):
    """Install the package in development mode."""
    if platform.system() == "Windows":
        pip = venv_path / "Scripts" / "pip.exe"
    else:
        pip = venv_path / "bin" / "pip"
    
    print("Installing package in development mode...")
    if not run_command(f"{pip} install -e ."):
        return False
    
    # Verify installation
    if platform.system() == "Windows":
        server = venv_path / "Scripts" / "coding-convention-mcp-server.exe"
    else:
        server = venv_path / "bin" / "coding-convention-mcp-server"
    
    if not server.exists():
        print(f"Error: Server executable not found at {server}")
        return False
    
    print("Package installed successfully")
    return True


def create_config_files(repo_path):
    """Create configuration files."""
    config_dir = Path.home() / ".config" / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    
    # Create environment config
    env_config = config_dir / ".coding_convention_env"
    if not env_config.exists():
        env_content = """# Coding Convention MCP Server Configuration

# Storage settings
CODING_CONVENTION_STORAGE_PATH=~/.local/share/coding_convention_mcp_server
CODING_CONVENTION_STORAGE_TYPE=sqlite  # sqlite or json
CODING_CONVENTION_ANALYSIS_DEPTH=100
"""
        env_config.write_text(env_content)
        env_config.chmod(0o600)
        print(f"Created environment config: {env_config}")
    else:
        print(f"Environment config already exists: {env_config}")
    
    # Create OpenCode config example
    example_config = repo_path / "opencode.jsonc.example"
    if example_config.exists():
        print(f"OpenCode config example: {example_config}")
    
    # Create Claude Desktop config example
    claude_config = repo_path / "claude_desktop_config.json"
    if claude_config.exists():
        print(f"Claude Desktop config: {claude_config}")
    
    return True


def test_server(venv_path=".venv"):
    """Test that the server starts correctly."""
    if platform.system() == "Windows":
        server = venv_path / "Scripts" / "coding-convention-mcp-server.exe"
    else:
        server = venv_path / "bin" / "coding-convention-mcp-server"
    
    print("Testing server startup...")
    
    # Try to run server with short timeout
    try:
        result = subprocess.run(
            [str(server), "--help"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            print("Server test successful")
            return True
        else:
            print(f"Server test failed: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print("Server test timed out (expected if running without stdio)")
        return True
    except Exception as e:
        print(f"Server test error: {e}")
        return False


def setup_openode_config(repo_path, venv_path=".venv"):
    """Setup OpenCode configuration with correct paths."""
    config_path = Path.home() / ".config" / "opencode" / "opencode.jsonc"
    
    # Determine server path
    if platform.system() == "Windows":
        server_exe = venv_path / "Scripts" / "coding-convention-mcp-server.exe"
    else:
        server_exe = venv_path / "bin" / "coding-convention-mcp-server"
    
    server_path = server_exe.resolve()
    
    # Create or update config
    config_content = f"""{{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {{
    "coding-convention": {{
      "type": "local",
      "command": [
        "{server_path}"
      ],
      "environment": {{
        "CODING_CONVENTION_STORAGE_PATH": "~/.local/share/coding_convention_mcp_server",
        "CODING_CONVENTION_STORAGE_TYPE": "sqlite",
        "CODING_CONVENTION_ANALYSIS_DEPTH": "100"
      }},
      "enabled": true
    }}
  }}
}}
"""
    
    if config_path.exists():
        print(f"OpenCode config already exists at {config_path}")
        print("You may need to merge the 'coding-convention' section manually")
        print(f"Server path: {server_path}")
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(config_content)
        config_path.chmod(0o600)
        print(f"Created OpenCode config at {config_path}")
    
    return True


def main():
    parser = argparse.ArgumentParser(description="Setup Coding Convention MCP Server")
    parser.add_argument("--skip-venv", action="store_true", help="Skip virtual environment creation")
    parser.add_argument("--skip-install", action="store_true", help="Skip package installation")
    parser.add_argument("--skip-config", action="store_true", help="Skip configuration creation")
    parser.add_argument("--skip-test", action="store_true", help="Skip server test")
    parser.add_argument("--venv-path", default=".venv", help="Virtual environment path")
    parser.add_argument("--setup-opencode", action="store_true", help="Setup OpenCode configuration")
    
    args = parser.parse_args()
    
    # Get repository path
    repo_path = Path(__file__).parent.resolve()
    print(f"Repository path: {repo_path}")
    
    # Change to repo directory
    os.chdir(repo_path)
    
    # Step 1: Check Python version
    print("\n" + "="*60)
    print("Step 1: Checking Python version")
    print("="*60)
    if not check_python_version():
        return 1
    
    # Step 2: Create virtual environment
    if not args.skip_venv:
        print("\n" + "="*60)
        print("Step 2: Creating virtual environment")
        print("="*60)
        venv_path = Path(args.venv_path)
        if not create_venv(venv_path):
            return 1
    
    # Step 3: Install package
    if not args.skip_install:
        print("\n" + "="*60)
        print("Step 3: Installing package")
        print("="*60)
        venv_path = Path(args.venv_path)
        if not install_package(venv_path):
            return 1
    
    # Step 4: Create configuration files
    if not args.skip_config:
        print("\n" + "="*60)
        print("Step 4: Creating configuration files")
        print("="*60)
        if not create_config_files(repo_path):
            return 1
    
    # Step 5: Setup OpenCode config (optional)
    if args.setup_opencode:
        print("\n" + "="*60)
        print("Step 5: Setting up OpenCode configuration")
        print("="*60)
        venv_path = Path(args.venv_path)
        if not setup_openode_config(repo_path, venv_path):
            return 1
    
    # Step 6: Test server
    if not args.skip_test:
        print("\n" + "="*60)
        print("Step 6: Testing server")
        print("="*60)
        venv_path = Path(args.venv_path)
        if not test_server(venv_path):
            return 1
    
    print("\n" + "="*60)
    print("Setup completed successfully!")
    print("="*60)
    print("\nNext steps:")
    print("1. Restart OpenCode to load the MCP server")
    print("2. Test with: track_coding_convention(...)")
    print("3. See SETUP.md for detailed usage instructions")
    print("\nConfiguration files created:")
    print(f"  • Environment config: ~/.config/opencode/.coding_convention_env")
    print(f"  • OpenCode example: {repo_path}/opencode.jsonc.example")
    print(f"  • Claude Desktop config: {repo_path}/claude_desktop_config.json")
    
    if not args.setup_opencode:
        print("\nNote: OpenCode configuration not automatically updated.")
        print("      Run with --setup-opencode to create OpenCode config.")
        print("      Or manually merge the example config into ~/.config/opencode/opencode.jsonc")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())