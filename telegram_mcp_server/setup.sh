#!/usr/bin/env bash
set -e

# Telegram MCP Server Setup Script
# This script checks dependencies, installs the package, and configures credentials.

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root (should not)
if [[ $EUID -eq 0 ]]; then
    log_warn "This script should not be run as root. Please run as a regular user."
    exit 1
fi

# Function to check if a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check Python version
check_python() {
    if command_exists python3; then
        PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        PYTHON_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
        PYTHON_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
        if [[ $PYTHON_MAJOR -eq 3 ]] && [[ $PYTHON_MINOR -ge 10 ]]; then
            log_success "Python $PYTHON_VERSION detected (>= 3.10)"
            return 0
        else
            log_error "Python $PYTHON_VERSION detected. Python 3.10 or higher is required."
            return 1
        fi
    else
        log_error "Python 3 not found. Please install Python 3.10 or higher."
        return 1
    fi
}

# Function to check for virtual environment
check_venv() {
    if [[ -d ".venv" ]]; then
        log_info "Virtual environment '.venv' already exists."
        return 0
    else
        log_info "Creating virtual environment..."
        python3 -m venv .venv
        if [[ $? -eq 0 ]]; then
            log_success "Virtual environment created."
            return 0
        else
            log_error "Failed to create virtual environment."
            return 1
        fi
    fi
}

# Function to activate virtual environment and install package
install_package() {
    # Only activate if not already in a virtual environment
    if [[ -z "$VIRTUAL_ENV" ]]; then
        log_info "Activating virtual environment..."
        # Source the activate script based on platform
        if [[ -f ".venv/bin/activate" ]]; then
            source .venv/bin/activate
        elif [[ -f ".venv/Scripts/activate" ]]; then
            source .venv/Scripts/activate
        else
            log_error "Could not find virtual environment activation script."
            return 1
        fi
    else
        log_info "Already in a virtual environment. Skipping activation."
    fi
    
    # Upgrade pip
    log_info "Upgrading pip..."
    pip install --upgrade pip
    
    # Install the package in editable mode
    log_info "Installing telegram-mcp-server package..."
    pip install -e .
    
    if [[ $? -eq 0 ]]; then
        log_success "Package installed successfully."
    else
        log_error "Package installation failed."
        return 1
    fi
}

# Function to get user input with a prompt
get_input() {
    local prompt="$1"
    local var_name="$2"
    local default_value="$3"
    local input
    
    if [[ -n "$default_value" ]]; then
        read -p "$prompt [$default_value]: " input
        if [[ -z "$input" ]]; then
            input="$default_value"
        fi
    else
        read -p "$prompt: " input
    fi
    
    eval "$var_name=\"$input\""
}

# Function to configure environment variables
configure_env() {
    log_info "Configuring environment variables..."
    
    # Check if .env exists
    if [[ -f ".env" ]]; then
        log_warn ".env file already exists. Backing up to .env.backup..."
        cp .env .env.backup
    fi
    
    # Copy .env.example to .env
    if [[ -f ".env.example" ]]; then
        cp .env.example .env
        log_success "Created .env file from .env.example"
    else
        log_error ".env.example not found. Cannot create .env."
        return 1
    fi
    
    # Get bot token
    if [[ -n "$TELEGRAM_BOT_TOKEN" ]]; then
        log_info "Using TELEGRAM_BOT_TOKEN from environment variable."
        BOT_TOKEN="$TELEGRAM_BOT_TOKEN"
    else
        get_input "Enter your Telegram Bot Token (from @BotFather)" BOT_TOKEN ""
        if [[ -z "$BOT_TOKEN" ]]; then
            log_warn "Bot token not provided. You'll need to edit .env manually."
        fi
    fi
    
    # Get chat ID
    if [[ -n "$TELEGRAM_CHAT_ID" ]]; then
        log_info "Using TELEGRAM_CHAT_ID from environment variable."
        CHAT_ID="$TELEGRAM_CHAT_ID"
    else
        get_input "Enter your Telegram Chat ID (from @userinfobot)" CHAT_ID ""
        if [[ -z "$CHAT_ID" ]]; then
            log_warn "Chat ID not provided. You'll need to edit .env manually."
        fi
    fi
    
    # Update .env file with provided values (if any)
    if [[ -n "$BOT_TOKEN" ]]; then
        # Use sed to replace the token line
        if [[ "$OSTYPE" == "darwin"* ]]; then
            # macOS sed requires -i '' for in-place without backup
            sed -i '' "s|TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$BOT_TOKEN|" .env
        else
            sed -i "s|TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$BOT_TOKEN|" .env
        fi
        log_success "Bot token updated in .env"
    fi
    
    if [[ -n "$CHAT_ID" ]]; then
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|TELEGRAM_CHAT_ID=.*|TELEGRAM_CHAT_ID=$CHAT_ID|" .env
        else
            sed -i "s|TELEGRAM_CHAT_ID=.*|TELEGRAM_CHAT_ID=$CHAT_ID|" .env
        fi
        log_success "Chat ID updated in .env"
    fi
    
    # Create secure credential files for OpenCode
    configure_opencode_credentials "$BOT_TOKEN" "$CHAT_ID"
}

# Function to create OpenCode credential files
configure_opencode_credentials() {
    local token="$1"
    local chat_id="$2"
    
    log_info "Setting up OpenCode credential files..."
    
    # Create config directory if it doesn't exist
    local config_dir="$HOME/.config/opencode"
    mkdir -p "$config_dir"
    
    # Create bot token file
    if [[ -n "$token" ]]; then
        echo -n "$token" > "$config_dir/.telegram_bot_token"
        chmod 600 "$config_dir/.telegram_bot_token"
        log_success "Created $config_dir/.telegram_bot_token"
    fi
    
    # Create chat ID file
    if [[ -n "$chat_id" ]]; then
        echo -n "$chat_id" > "$config_dir/.telegram_chat_id"
        chmod 600 "$config_dir/.telegram_chat_id"
        log_success "Created $config_dir/.telegram_chat_id"
    fi
}

# Function to optionally register bot commands
register_bot_commands() {
    log_info "Would you like to register bot commands with Telegram? (y/n)"
    read -p "Choice: " choice
    case "$choice" in
        y|Y|yes|YES)
            log_info "Registering bot commands..."
            # Ensure we're in the virtual environment
            if [[ -z "$VIRTUAL_ENV" ]]; then
                if [[ -f ".venv/bin/activate" ]]; then
                    source .venv/bin/activate
                elif [[ -f ".venv/Scripts/activate" ]]; then
                    source .venv/Scripts/activate
                else
                    log_error "Virtual environment not found. Cannot register commands."
                    return 1
                fi
            fi
            
            # Check if set_commands.py exists
            if [[ ! -f "set_commands.py" ]]; then
                log_error "set_commands.py not found. Cannot register commands."
                return 1
            fi
            
            python set_commands.py
            if [[ $? -eq 0 ]]; then
                log_success "Bot commands registered successfully."
            else
                log_error "Failed to register bot commands. You can run manually later: python set_commands.py"
            fi
            ;;
        *)
            log_info "Skipping bot command registration. You can run later: python set_commands.py"
            ;;
    esac
}

# Function to display next steps
display_next_steps() {
    echo ""
    log_success "Setup completed!"
    echo ""
    log_info "Next steps:"
    echo "1. Review the .env file to ensure all settings are correct"
    echo "2. If you didn't provide credentials, edit .env with your TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
    echo "3. Configure your MCP client:"
    echo "   - OpenCode: Edit ~/.config/opencode/opencode.jsonc"
    echo "   - Claude Desktop: Edit ~/Library/Application Support/Claude/claude_desktop_config.json"
    echo ""
    log_info "To start the server manually:"
    echo "  source .venv/bin/activate"
    echo "  telegram-mcp-server --help"
    echo ""
    log_info "For detailed configuration, see SETUP.md"
}

# Function to ensure we're in the correct directory
ensure_correct_directory() {
    # Get the directory where this script is located
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    
    # Change to script directory
    cd "$SCRIPT_DIR"
    
    # Check for pyproject.toml to confirm we're in the right place
    if [[ ! -f "pyproject.toml" ]]; then
        log_error "pyproject.toml not found. Please run this script from the telegram_mcp_server directory."
        exit 1
    fi
    
    log_info "Running setup in $(pwd)"
}

# Main execution
main() {
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}  Telegram MCP Server Setup Script      ${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    
    # Ensure we're in the correct directory
    ensure_correct_directory
    
    # Check dependencies
    log_info "Checking dependencies..."
    check_python || exit 1
    
    # Check/create virtual environment
    check_venv || exit 1
    
    # Install package
    install_package || exit 1
    
    # Configure environment
    configure_env
    
    # Optionally register bot commands
    register_bot_commands
    
    # Display next steps
    display_next_steps
}

# Run main function
main "$@"