"""Built-in configuration blueprints for NextGen ERP business profiles.

Blueprints define available workspaces and starter field metadata. They do not
claim to implement transactional industry workflows by themselves.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class BusinessProfile:
    name: str
    modules: tuple[str, ...]
    workspaces: dict[str, tuple[dict, ...]]


CORE_MODULES = (
    "Sales", "Purchases", "Inventory", "Finance", "HR & Payroll",
    "CRM", "Projects", "Reports",
)

MODULE_DEPENDENCIES = {
    "Sales": ("Inventory", "Finance"),
    "Purchases": ("Inventory", "Finance"),
    "Batches & Expiry": ("Inventory",),
    "Menu & Recipes": ("Inventory", "Sales", "Purchases"),
    "Repair Tickets": ("Inventory", "Sales", "CRM"),
    "Appointments": ("CRM",),
    "Students & Enrolment": ("CRM",),
}


def missing_dependencies(enabled_modules):
    enabled=set(enabled_modules)
    missing=set()
    pending=list(enabled)
    checked=set()
    while pending:
        module=pending.pop()
        if module in checked:
            continue
        checked.add(module)
        for dependency in MODULE_DEPENDENCIES.get(module, ()):
            if dependency not in enabled:
                missing.add((module,dependency))
            else:
                pending.append(dependency)
    return sorted(missing)


def _field(name, kind="text", required=False, options=()):
    return {"name": name, "type": kind, "required": required, "options": list(options)}


REPAIR_TICKETS = (
    _field("Customer", required=True), _field("Device", required=True),
    _field("Serial number"), _field("Reported fault", "notes", True),
    _field("Technician"), _field("Estimate (KES)", "number"),
    _field("Status", "choice", True, ("Received", "Diagnosing", "Awaiting approval", "In progress", "Ready", "Collected", "Cancelled")),
    _field("Warranty notes", "notes"),
)
APPOINTMENTS = (
    _field("Customer", required=True), _field("Service", required=True),
    _field("Staff member"), _field("Appointment date", "date", True),
    _field("Start time"), _field("Status", "choice", True, ("Booked", "Confirmed", "Arrived", "Completed", "No show", "Cancelled")),
    _field("Notes", "notes"),
)
MENU_ITEMS = (
    _field("Item name", required=True), _field("Category"),
    _field("Selling price (KES)", "number", True), _field("Recipe / ingredients", "notes"),
    _field("Status", "choice", True, ("Available", "Unavailable", "Seasonal")),
)
BATCHES = (
    _field("Product / medicine", required=True), _field("Batch number", required=True),
    _field("Quantity", "number", True), _field("Expiry date", "date", True),
    _field("Supplier"), _field("Status", "choice", True, ("In stock", "Quarantined", "Expired", "Disposed")),
)
STUDENTS = (
    _field("Student name", required=True), _field("Admission number", required=True),
    _field("Class / course"), _field("Guardian"), _field("Guardian contact"),
    _field("Enrolment date", "date"), _field("Status", "choice", True, ("Active", "On leave", "Completed", "Withdrawn")),
)

ALL_MODULES = CORE_MODULES
BUSINESS_PROFILES = {
    "General business": BusinessProfile("General business", ALL_MODULES, {}),
    "Supermarket / minimart": BusinessProfile("Supermarket / minimart", ("Sales", "Purchases", "Inventory", "Finance", "CRM", "Reports", "Batches & Expiry"), {"Batches & Expiry": BATCHES}),
    "Laptop & computer sales / repair": BusinessProfile("Laptop & computer sales / repair", ("Sales", "Purchases", "Inventory", "Finance", "CRM", "Projects", "Reports", "Repair Tickets"), {"Repair Tickets": REPAIR_TICKETS}),
    "Electronics retail": BusinessProfile("Electronics retail", ("Sales", "Purchases", "Inventory", "Finance", "CRM", "Reports"), {}),
    "General retail shop": BusinessProfile("General retail shop", ("Sales", "Purchases", "Inventory", "Finance", "CRM", "Reports"), {}),
    "Wholesale / distribution": BusinessProfile("Wholesale / distribution", ("Sales", "Purchases", "Inventory", "Finance", "CRM", "Reports"), {}),
    "Restaurant / café": BusinessProfile("Restaurant / café", ("Sales", "Purchases", "Inventory", "Finance", "Reports", "Menu & Recipes"), {"Menu & Recipes": MENU_ITEMS}),
    "Pharmacy": BusinessProfile("Pharmacy", ("Sales", "Purchases", "Inventory", "Finance", "CRM", "Reports", "Batches & Expiry"), {"Batches & Expiry": BATCHES}),
    "Hardware / building supplies": BusinessProfile("Hardware / building supplies", ("Sales", "Purchases", "Inventory", "Finance", "CRM", "Reports"), {}),
    "Salon / barbershop": BusinessProfile("Salon / barbershop", ("Sales", "Inventory", "Finance", "CRM", "HR & Payroll", "Reports", "Appointments"), {"Appointments": APPOINTMENTS}),
    "Professional services": BusinessProfile("Professional services", ("Sales", "Inventory", "Finance", "CRM", "Projects", "Reports"), {}),
    "Repair / service centre": BusinessProfile("Repair / service centre", ("Sales", "Purchases", "Inventory", "Finance", "CRM", "Projects", "Reports", "Repair Tickets"), {"Repair Tickets": REPAIR_TICKETS}),
    "School / training centre": BusinessProfile("School / training centre", ("Finance", "HR & Payroll", "CRM", "Projects", "Reports", "Students & Enrolment"), {"Students & Enrolment": STUDENTS}),
    "Custom business": BusinessProfile("Custom business", ALL_MODULES, {}),
}


def get_profile(name: str) -> BusinessProfile:
    return BUSINESS_PROFILES.get(name, BUSINESS_PROFILES["General business"])
