# Coding Convention MCP Server - Complete Setup Guide

## AI Agent Instructions

**READ THIS FIRST:** As an AI agent, you should read this entire file and execute the commands step by step to set up the Coding Convention MCP Server. Each section contains commands you should run in sequence. Pay special attention to paths and replace placeholder values with actual values from the user's system.

**Quick Setup Script:** For automated setup, you can run the included setup script:
```bash
cd /Users/k/mcp-servers/coding_convention_mcp_server
python setup_server.py --setup-opencode
```

If the script fails or you prefer manual setup, follow the step-by-step instructions below.

---

This guide is designed to be read by an AI agent to configure the Coding Convention MCP Server for OpenCode. Follow each section in order.

---

## Overview

The Coding Convention MCP Server provides tools to track, compare, and validate coding conventions across repositories. It can analyze codebases, extract coding standards, store them persistently, and compare conventions between different projects.

**Key Features:**
- Track coding conventions with language, category, severity, and detection patterns
- Analyze local directories or Git repositories to automatically extract conventions
- Compare coding conventions between repositories
- Validate source code against stored conventions
- Persistent storage in SQLite or JSON format

---

## Prerequisites

Before starting, ensure the system has:

1. **Python 3.10+** - Required for the MCP server
2. **Git** - Optional, for analyzing Git repositories
3. **OpenCode** - Already installed and running

No API keys or external credentials are required for basic operation.

---

## Step 1: Install the Coding Convention MCP Server

```bash
cd /Users/k/mcp-servers/coding_convention_mcp_server

# Create virtual environment with Python 3.10+
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install the package
pip install -e .
```

Verify installation:
```bash
# Should print help without errors
.venv/bin/coding-convention-mcp-server --help
```

Expected output should show:
```
usage: coding-convention-mcp-server [-h] [--verbose]

Coding Convention MCP Server

options:
  -h, --help     show this help message and exit
  --verbose, -v  Enable verbose logging
```

---

## Step 2: Configure Storage

The server supports two storage backends: SQLite (default) or JSON. Create a configuration file:

```bash
# Create config directory if needed
mkdir -p ~/.config/opencode

# Create environment configuration
cat > ~/.config/opencode/.coding_convention_env << 'EOF'
# Coding Convention MCP Server Configuration

# Storage settings
CODING_CONVENTION_STORAGE_PATH=~/.local/share/coding_convention_mcp_server
CODING_CONVENTION_STORAGE_TYPE=sqlite  # sqlite or json
CODING_CONVENTION_ANALYSIS_DEPTH=100
EOF

# Make it readable only by the user
chmod 600 ~/.config/opencode/.coding_convention_env
```

**Storage Options:**
- `CODING_CONVENTION_STORAGE_TYPE=sqlite` (default): Uses SQLite database for better performance
- `CODING_CONVENTION_STORAGE_TYPE=json`: Uses JSON files for easier inspection

---

## Step 3: Configure OpenCode

Edit `~/.config/opencode/opencode.jsonc` (create if it doesn't exist):

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "coding-convention": {
      "type": "local",
      "command": [
        "<ABSOLUTE_PATH_TO_VENV>/bin/coding-convention-mcp-server"
      ],
      "environment": {
        "CODING_CONVENTION_STORAGE_PATH": "{file:~/.config/opencode/.coding_convention_env:Coding Convention Storage Path}",
        "CODING_CONVENTION_STORAGE_TYPE": "{file:~/.config/opencode/.coding_convention_env:Coding Convention Storage Type}",
        "CODING_CONVENTION_ANALYSIS_DEPTH": "{file:~/.config/opencode/.coding_convention_env:Coding Convention Analysis Depth}"
      },
      "enabled": true
    }
  }
}
```

Replace `<ABSOLUTE_PATH_TO_VENV>` with the full path to the virtual environment (e.g., `/Users/k/mcp-servers/coding_convention_mcp_server/.venv`).

### Alternative: Environment Variable Source

If you prefer to use environment variables directly without a file:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "coding-convention": {
      "type": "local",
      "command": [
        "<ABSOLUTE_PATH_TO_VENV>/bin/coding-convention-mcp-server"
      ],
      "environment": {
        "CODING_CONVENTION_STORAGE_PATH": "~/.local/share/coding_convention_mcp_server",
        "CODING_CONVENTION_STORAGE_TYPE": "sqlite",
        "CODING_CONVENTION_ANALYSIS_DEPTH": "100"
      },
      "enabled": true
    }
  }
}
```

### Configuration Notes:
- No authentication or API keys required
- Storage directory will be created automatically
- The server runs in stdio mode (no network ports needed)

---

## Step 4: Initialize Storage

The storage system initializes automatically on first use, but you can verify it works:

```bash
# Activate the virtual environment
source .venv/bin/activate

# Test the server briefly (will timeout after 5 seconds)
timeout 5s coding-convention-mcp-server --verbose &
sleep 2
echo "Testing server startup..."
```

The server should start without errors. You can stop it with Ctrl+C.

---

## Step 5: Verify Installation

1. **Restart OpenCode** to load the new MCP server

2. **Test the tools** in OpenCode:

   ```python
   # Track a sample coding convention
   track_coding_convention(
       language="python",
       category="naming",
       description="Function names should use snake_case",
       severity="required",
       pattern="def ([a-z][a-z0-9_]*)\\(",
       example="def calculate_total():",
       counter_example="def CalculateTotal():"
   )
   ```

3. **Verify tools are available**:
   The agent should have access to these Coding Convention tools:
   - `track_coding_convention` - Record new conventions
   - `analyze_repository` - Analyze codebases for conventions
   - `compare_repositories` - Compare conventions between repositories
   - `validate_code` - Validate code against conventions
   - `list_conventions` - Browse stored conventions
   - `get_convention_details` - View convention details
   - `merge_conventions` - Combine similar conventions

---

## Step 6: Quick Start Examples

### Analyze a Local Repository
```python
analyze_repository(
    repository_path="/path/to/your/project",
    max_files=50
)
```

### Compare Two Repositories
```python
compare_repositories(
    repository_a="/path/to/project-a",
    repository_b="/path/to/project-b"
)
```

### Validate Python Code
```python
validate_code(
    code="def my_function(): pass",
    language="python",
    check_severity="required"
)
```

### List Stored Conventions
```python
list_conventions(
    language="python",
    category="naming",
    limit=10
)
```

---

## Troubleshooting

### "Module not found" errors
- Ensure the virtual environment is activated
- Reinstall the package: `pip install -e .`

### "Permission denied" for storage directory
- The storage directory is created automatically with user permissions
- Check that `~/.local/share/coding_convention_mcp_server` is writable

### "No conventions found" when analyzing repositories
- The analyzer supports specific languages (Python, JavaScript, TypeScript, Java, Go, Rust, etc.)
- Ensure the repository contains source files in supported languages
- Increase `CODING_CONVENTION_ANALYSIS_DEPTH` to analyze more files

### Server won't start
- Check OpenCode logs for MCP server errors
- Test manually: `.venv/bin/coding-convention-mcp-server`
- Ensure Python 3.10+ is installed

---

## Configuration Summary

After setup, you should have:

| File | Purpose |
|------|---------|
| `~/.config/opencode/.coding_convention_env` | Environment configuration (optional) |
| `~/.config/opencode/opencode.jsonc` | MCP server configuration |
| `~/.local/share/coding_convention_mcp_server/conventions.db` | SQLite database (auto-created) |
| `~/.local/share/coding_convention_mcp_server/conventions.json` | JSON storage (if configured) |

---

## Quick Reference

### Track a Convention
```python
track_coding_convention(
    language="python",
    category="formatting",
    description="Import statements should be at the top",
    severity="recommended",
    source_repository="/path/to/repo"
)
```

### Analyze Repository
```python
analyze_repository(
    repository_path="https://github.com/username/repo.git",
    max_files=100
)
```

### Compare Repositories
```python
compare_repositories(
    repository_a="project-a-analysis-id",
    repository_b="project-b-analysis-id"
)
```

### Validate Code
```python
validate_code(
    code="function myFunction() {}",
    language="javascript",
    check_severity="recommended"
)
```

---

## How It Works

The Coding Convention MCP Server provides a comprehensive suite for managing coding standards:

```
┌─────────────────────────────────────────────┐
│  coding-convention-mcp-server               │
│                                             │
│  ┌──────────────────────────────────┐      │
│  │     MCP Tools (7 tools)          │      │
│  │  • track_coding_convention       │      │
│  │  • analyze_repository            │      │
│  │  • compare_repositories          │      │
│  │  • validate_code                 │      │
│  │  • list_conventions              │      │
│  │  • get_convention_details        │      │
│  │  • merge_conventions             │      │
│  └──────────────────────────────────┘      │
│                                             │
│  ┌──────────────────────────────────┐      │
│  │     Storage Engine               │      │
│  │  • SQLite (default)              │      │
│  │  • JSON                          │      │
│  └──────────────────────────────────┘      │
│                                             │
│  ┌──────────────────────────────────┐      │
│  │     Repository Analyzer          │      │
│  │  • Language detection            │      │
│  │  • Pattern extraction            │      │
│  │  • Convention scoring            │      │
│  └──────────────────────────────────┘      │
└─────────────────────────────────────────────┘
                    │
                    ▼
              OpenCode Agent
```

### Data Flow:
1. **Analysis**: Repository → Analyzer → Extracted Conventions → Storage
2. **Comparison**: Two Repositories → Convention Sets → Similarity Analysis → Recommendations
3. **Validation**: Source Code → Convention Rules → Compliance Check → Violation Report

### Supported Languages:
- Python, JavaScript, TypeScript, Java, Go, Rust
- C++, C#, Ruby, PHP, Swift, Kotlin, Scala, Dart

---

## Advanced Configuration

### Custom Analysis Rules
You can extend the analyzer by adding custom pattern detectors. The server looks for:
- Naming conventions (snake_case, camelCase, PascalCase)
- Formatting rules (indentation, brace style, line length)
- Documentation patterns (docstrings, comments)
- Structural conventions (import ordering, error handling)
- Testing patterns (test naming, assertion styles)

### Storage Migration
To switch between SQLite and JSON storage:

1. Update `CODING_CONVENTION_STORAGE_TYPE` in configuration
2. The server will automatically use the new backend
3. Existing data will be accessible in the new format

### Performance Tuning
- Increase `CODING_CONVENTION_ANALYSIS_DEPTH` for larger repositories
- Use SQLite for better performance with many conventions
- Use JSON for easier debugging and manual inspection

---

## Maintenance

### Backup Storage
Regularly backup the storage directory:
```bash
# Backup SQLite database
cp ~/.local/share/coding_convention_mcp_server/conventions.db /path/to/backup/

# Backup JSON storage
cp ~/.local/share/coding_convention_mcp_server/conventions.json /path/to/backup/
```

### Update the Server
To update to a new version:
```bash
cd /Users/k/mcp-servers/coding_convention_mcp_server
git pull
source .venv/bin/activate
pip install -e .
```

### Reset Storage
To start fresh (delete all stored conventions):
```bash
rm -rf ~/.local/share/coding_convention_mcp_server
# Storage will be recreated on next server start
```

---

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Verify OpenCode logs for error messages
3. Test the server manually outside OpenCode
4. Review the README.md for additional information

The Coding Convention MCP Server is designed to be self-contained and require minimal maintenance once configured.