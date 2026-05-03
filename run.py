import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from telegram_bark_client import main_sync

main_sync()
