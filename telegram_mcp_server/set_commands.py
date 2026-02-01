#!/usr/bin/env python3
"""Script to manually set Telegram bot commands."""

import asyncio
import sys
import json
from pathlib import Path

# Add src to path
src_dir = Path(__file__).parent / "src"
sys.path.insert(0, str(src_dir))

from telegram_mcp_server.telegram_client import TelegramClient
from telegram_mcp_server.commands import get_bot_commands
from telegram_mcp_server.config import get_settings
import httpx

async def main():
    """Set bot commands."""
    settings = get_settings()
    client = TelegramClient(
        bot_token=settings.bot_token,
        base_url=settings.api_base_url,
    )
    
    commands = get_bot_commands()
    print(f"Preparing to set {len(commands)} bot commands...")
    
    # Validate commands
    print("\nCommand validation:")
    for i, cmd in enumerate(commands, 1):
        cmd_name = cmd['command']
        desc = cmd['description']
        # Check command name format (Telegram: lowercase English letters, digits, underscore)
        valid = True
        for char in cmd_name:
            if not (char.islower() and char.isalpha()) and not char.isdigit() and char != '_':
                print(f"  ❌ Command '{cmd_name}' contains invalid character: '{char}'")
                valid = False
                break
        if not cmd_name[0].isalpha():
            print(f"  ❌ Command '{cmd_name}' doesn't start with a letter")
            valid = False
        if len(desc) > 256:
            print(f"  ❌ Command '{cmd_name}' description too long: {len(desc)} chars")
            valid = False
        if valid:
            print(f"  ✓ {cmd_name}: {desc[:50]}...")
    
    try:
        # Try to set commands directly to see error
        print("\nAttempting to set commands...")
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            url = f"https://api.telegram.org/bot{settings.bot_token}/setMyCommands"
            response = await http_client.post(url, json={"commands": commands})
            print(f"Response status: {response.status_code}")
            if response.status_code != 200:
                print(f"Response body: {response.text}")
                try:
                    error_data = response.json()
                    print(f"Error details: {json.dumps(error_data, indent=2)}")
                except:
                    pass
            response.raise_for_status()
            print("✅ Commands set successfully via direct API call!")
            
    except httpx.HTTPStatusError as e:
        print(f"❌ HTTP error: {e}")
        if e.response:
            print(f"Response: {e.response.text}")
        return 1
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1
    
    # Check current commands
    try:
        existing = await client.get_my_commands()
        print(f"\nCurrent commands ({len(existing)}):")
        for cmd in existing:
            print(f"  /{cmd.get('command')} - {cmd.get('description')}")
    except Exception as e:
        print(f"Error getting commands: {e}")
    
    await client.close()
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))