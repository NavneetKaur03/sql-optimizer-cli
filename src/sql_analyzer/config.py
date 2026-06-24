# config.py
# Centralised DB configuration loaded from environment variables.

import os
from dotenv import load_dotenv

# load_dotenv() reads .env file and sets environment variables.
load_dotenv()

DB_CONFIG = {
    "dbname":   os.getenv("DB_NAME", "sqlanalyzer"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     os.getenv("DB_PORT", "5432")
}