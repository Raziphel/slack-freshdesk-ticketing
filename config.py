import os
import logging
from dotenv import load_dotenv

# I want logging ready right away, so I'm pulling in env vars first thing.
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def _as_bool(v: str | None, default=False):
    # Tiny helper so I stop rewriting the same truthy checks everywhere.
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}

# Grabbing the essentials from env; can't do much without these.
FRESHDESK_DOMAIN = os.getenv("FRESHDESK_DOMAIN") or ""
FRESHDESK_API_KEY = os.getenv("FRESHDESK_API_KEY") or ""
FRESHDESK_EMAIL  = os.getenv("FRESHDESK_EMAIL") or ""
SLACK_BOT_TOKEN  = os.getenv("SLACK_BOT_TOKEN") or ""
IT_GROUP_ID      = os.getenv("IT_GROUP_ID", "")
PORTAL_TICKET_FORM_URL = os.getenv("PORTAL_TICKET_FORM_URL") or f"https://{FRESHDESK_DOMAIN}.freshdesk.com/support/tickets/new"

# Feature flags I flip on and off when experimenting.
ENABLE_WIZARD    = _as_bool(os.getenv("ENABLE_WIZARD"), True)
WIZARD_CROSS_SECTION_CHILDREN = _as_bool(os.getenv("WIZARD_CROSS_SECTION_CHILDREN"), True)

# Misc knobs I might tweak later.
ALLOWED_FORM_IDS = [s.strip() for s in (os.getenv("ALLOWED_FORM_IDS", "")).split(",") if s.strip()]
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "12.0"))
MAX_BLOCKS   = int(os.getenv("MAX_BLOCKS", "49"))

# I like this order when the portal doesn't force a specific form list.
PORTAL_FORMS_ORDER = [
    "IT Equipment & Facility Support Form",
    "System Access Request",
    "IT Application Assistance (Not Access Related)",
    "Customer Notification Form",
    "Security Incident",
]

# Mapping messy form names to something nicer for my eyes.
FORM_NAME_TO_DISPLAY = {
    "it_equipment_&_facility_support_form": "IT Equipment & Facility Support Form",
    "system_access_request": "System Access Request",
    "it_application_assistance_(not_access_related)": "IT Application Assistance (Not Access Related)",
    "customer_notification_form": "Customer Notification Form",
    "security_incident": "Security Incident",
}

# Converting form names to Freshdesk ticket type values so I don't do it later.
FORM_NAME_TO_TYPE = {
    # Raw form name -> Freshdesk ticket type
    "it_equipment_&_facility_support_form": "IT Equipment Support Form",
    "IT Equipment & Facility Support Form": "IT Equipment Support Form",
    "system_access_request": "System Access Request",
    "System Access Request": "System Access Request",
    "it_application_assistance_(not_access_related)": "IT Application Assistance Request",
    "IT Application Assistance (Not Access Related)": "IT Application Assistance Request",
    "customer_notification_form": "IT Customer Notification Form",
    "Customer Notification Form": "IT Customer Notification Form",
    "security_incident": "Security Incident",
    "Security Incident": "Security Incident",
}
