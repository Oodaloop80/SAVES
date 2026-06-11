import os
from dotenv import load_dotenv
from src.config import get_config


def load_credentials(env_path: str = ".env") -> None:
    load_dotenv(dotenv_path=env_path)
    config = get_config()
    required = config.get("credentials", {}).get("keys", [])
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            f"Check your .env file."
        )
