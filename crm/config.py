import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///crm.db")
DAILY_SEND_LIMIT = int(os.getenv("DAILY_SEND_LIMIT", "50"))
HOURLY_SEND_LIMIT = int(os.getenv("HOURLY_SEND_LIMIT", "10"))
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "")
SENDER_NAME = os.getenv("SENDER_NAME", "")
BASE_URL = os.getenv("CRM_BASE_URL", "http://127.0.0.1:8001")
