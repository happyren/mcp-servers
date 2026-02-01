# Coding Convention MCP Server

An MCP server for tracking, comparing, and validating coding conventions across repositories. This server provides tools to analyze codebases, extract coding standards, store them persistently, and compare conventions between different projects.

## Features

- **Track Coding Conventions**: Record coding conventions with language, category, severity, and detection patterns
- **Analyze Repositories**: Scan local directories or Git repositories to automatically extract coding conventions
- **Compare Repositories**: Compare coding conventions between different repositories to identify similarities and differences
- **Validate Code**: Check source code against stored conventions to ensure compliance
- **Persistent Storage**: Store conventions in SQLite or JSON format with confidence scoring
- **Merge Conventions**: Combine similar conventions to build comprehensive standards

## Installation

### Automated Setup (Recommended for AI Agents)

For AI agents setting up the server automatically, use the comprehensive setup guide:

1. **Read the setup guide**: `SETUP.md` contains step-by-step instructions for agents
2. **Run the setup script**: 
   ```bash
   cd /Users/k/mcp-servers/coding_convention_mcp_server
   python setup_server.py --setup-opencode
   ```
3. **Follow manual steps**: If the script fails, follow the detailed instructions in `SETUP.md`

### From Source (Manual)

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd coding_convention_mcp_server
   ```

2. Install dependencies:
   ```bash
   pip install -e .
   ```

3. Configure environment (optional):
   ```bash
   cp .env.example .env
   # Edit .env as needed
   ```

## Configuration

### Environment Variables

- `CODING_CONVENTION_STORAGE_PATH`: Storage directory (default: `~/.local/share/coding_convention_mcp_server`)
- `CODING_CONVENTION_STORAGE_TYPE`: Storage type - `sqlite` or `json` (default: `sqlite`)
- `CODING_CONVENTION_ANALYSIS_DEPTH`: Maximum files to analyze per repository (default: `100`)

### OpenCode Configuration

For use with OpenCode, add to `~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "coding-convention": {
      "type": "local",
      "command": [
        "/full/path/to/venv/bin/coding-convention-mcp-server"
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

See `opencode.jsonc.example` for a complete example.

### Claude Desktop Configuration

For use with Claude Desktop, add to `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS:

```json
{
  "mcpServers": {
    "coding-convention": {
      "command": "coding-convention-mcp-server",
      "args": [],
      "env": {
        "CODING_CONVENTION_STORAGE_PATH": "~/.local/share/coding_convention_mcp_server",
        "CODING_CONVENTION_STORAGE_TYPE": "sqlite"
      }
    }
  }
}
```

## Available Tools

### 1. Track Coding Convention
Record a new coding convention or update an existing one.

**Parameters:**
- `language`: Programming language (python, javascript, typescript, java, etc.)
- `category`: Convention category (naming, formatting, structure, documentation, etc.)
- `description`: Human-readable description
- `severity`: Importance level (required, recommended, suggested, optional)
- `pattern`: Optional regex pattern for detection
- `example`: Example of compliant code
- `counter_example`: Example of non-compliant code
- `source_repository`: Repository where this convention was observed
- `confidence`: Confidence score (0.0-1.0)

### 2. Analyze Repository
Analyze a repository to extract coding conventions automatically.

**Parameters:**
- `repository_path`: Path to repository (local directory or Git URL)
- `max_files`: Maximum number of files to analyze (default: 100)

**Supported Languages:** Python, JavaScript, TypeScript, Java, Go, Rust, C++, C#, Ruby, PHP, Swift, Kotlin, Scala, Dart

### 3. Compare Repositories
Compare coding conventions between two repositories.

**Parameters:**
- `repository_a`: First repository path or analysis ID
- `repository_b`: Second repository path or analysis ID

### 4. Validate Code
Validate source code against stored conventions.

**Parameters:**
- `code`: Source code to validate
- `language`: Programming language of the code
- `check_severity`: Minimum severity to check (required, recommended, suggested, optional)

### 5. List Conventions
List stored coding conventions with optional filtering.

**Parameters:**
- `language`: Filter by programming language
- `category`: Filter by convention category
- `limit`: Maximum number of conventions to return (default: 20)

### 6. Get Convention Details
Get detailed information about a specific convention.

**Parameters:**
- `convention_id`: ID of the convention to retrieve

### 7. Merge Conventions
Merge two similar conventions into one.

**Parameters:**
- `source_convention_id`: ID of the convention to merge from (will be deleted)
- `target_convention_id`: ID of the convention to merge into (will be updated)

## Usage Examples

### Track a Python naming convention:
```
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

### Analyze a local repository:
```
analyze_repository(
    repository_path="/path/to/my/project",
    max_files=50
)
```

### Compare two repositories:
```
compare_repositories(
    repository_a="/path/to/project-a",
    repository_b="/path/to/project-b"
)
```

### Validate Python code:
```
validate_code(
    code="def my_function(): pass",
    language="python",
    check_severity="required"
)
```

## Architecture

### Data Models
- `CodeConvention`: Individual coding convention rule
- `RepositoryAnalysis`: Analysis results for a repository
- `ComparisonResult`: Comparison between two repositories

### Storage
- **SQLiteStorage**: Primary storage using SQLite database
- **JSONStorage**: Alternative JSON file storage
- Configurable via `CODING_CONVENTION_STORAGE_TYPE`

### Analyzer
- Language-specific analyzers for common patterns
- Support for multiple programming languages
- Pattern matching and AST-based analysis

## Development

### Setup Development Environment
```bash
pip install -e ".[dev]"
```

### Run Tests
```bash
pytest
```

### Code Quality
```bash
ruff check .
black .
```

## License

MIT