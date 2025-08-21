import os
import logging
from dotenv import load_dotenv

# Load env and set logging once, early
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def _as_bool(v: str | None, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}

# Core env
FRESHDESK_DOMAIN = os.getenv("FRESHDESK_DOMAIN") or ""
FRESHDESK_API_KEY = os.getenv("FRESHDESK_API_KEY") or ""
FRESHDESK_EMAIL  = os.getenv("FRESHDESK_EMAIL") or ""
SLACK_BOT_TOKEN  = os.getenv("SLACK_BOT_TOKEN") or ""
IT_GROUP_ID      = os.getenv("IT_GROUP_ID", "")

# Behavior toggles
ENABLE_WIZARD    = _as_bool(os.getenv("ENABLE_WIZARD"), True)
WIZARD_CROSS_SECTION_CHILDREN = _as_bool(os.getenv("WIZARD_CROSS_SECTION_CHILDREN"), True)

# Misc
ALLOWED_FORM_IDS = [s.strip() for s in (os.getenv("ALLOWED_FORM_IDS", "")).split(",") if s.strip()]
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "12.0"))
MAX_BLOCKS   = int(os.getenv("MAX_BLOCKS", "49"))

# Preferred form names (only used when ALLOWED_FORM_IDS is empty)
PORTAL_FORMS_ORDER = [
    "IT Equipment & Facility Support Form",
    "System Access Request",
    "IT Application Assistance (Not Access Related)",
    "Customer Notification Form",
    "Security Incident",
]

# Map Freshdesk form names to valid ticket type values
FORM_NAME_TO_TYPE = {
    "IT Equipment & Facility Support Form": "IT Equipment Support Form",
}
