import json
import os
from dotenv import load_dotenv

load_dotenv()


def get_boolean(var):
    if os.environ.get(var) == "TRUE":
        return True
    else:
        return False


def get_string(var):
    return os.getenv(var)


def get_int(var):
    if os.getenv(var):
        return int(os.getenv(var))
    else:
        return None


def get_json(var):
    if os.getenv(var):
        return json.loads(os.getenv(var))
    else:
        return []
