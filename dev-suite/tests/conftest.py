"""Shared test configuration.

Loads .env file so integration tests can access API keys.
"""

from dotenv import load_dotenv

# Load .env before any tests run
load_dotenv()
