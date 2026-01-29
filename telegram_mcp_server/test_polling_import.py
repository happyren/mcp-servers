#!/usr/bin/env python3
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

try:
    from telegram_polling_service.polling_service import TelegramPollingService
    print("✅ TelegramPollingService imports successfully")
    
    # Test config import
    from telegram_mcp_server.config import get_settings
    settings = get_settings()
    print(f"✅ Config loaded: bot_token={settings.bot_token[:10]}..., chat_id={settings.chat_id}")
    
except Exception as e:
    print(f"❌ Import failed: {e}")
    import traceback
    traceback.print_exc()