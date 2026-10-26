import sys, os, json, sqlite3, hashlib, secrets, shutil, urllib.request, threading, re
from pathlib import Path
from datetime import datetime
from decimal import Decimal
from PySide6.QtCore import Qt, QSize, QTimer, QRectF, QPointF
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QBrush, QLinearGradient, QAction, QTextDocument, QPageSize
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QFrame, QStackedWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QFormLayout, QComboBox, QDialogButtonBox, QCheckBox,
    QScrollArea, QTextEdit, QPlainTextEdit, QSpinBox, QAbstractItemView, QGraphicsDropShadowEffect, QFileDialog, QDateEdit, QDoubleSpinBox, QInputDialog
)
from PySide6.QtPrintSupport import QPrinter, QPrintDialog, QPrintPreviewDialog
from profiles import BUSINESS_PROFILES, CORE_MODULES, MODULE_DEPENDENCIES, get_profile, missing_dependencies
from operations import OperationsService, OperationError
from operations_ui import StockTransactionDialog, SaleReturnDialog, PurchaseOrderReceiptDialog, RepairJobDialog, RepairDiagnosisDialog, RepairPartDialog, RepairPaymentDialog
from document_templates import render_invoice, render_quote, render_purchase_order, render_goods_received, render_repair_job

APP_NAME = "NextGen ERP"
APP_DIR = Path(os.getenv("APPDATA", str(Path.home()))) / "NextGenERP"
APP_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = APP_DIR / "nextgen_erp.db"

C = {
    "bg": "#080D1B", "sidebar": "#0A1123", "panel": "#101A30", "panel2": "#14213B",
    "border": "#243654", "text": "#F2F6FF", "muted": "#8B9AB8", "blue": "#3978FF",
    "cyan": "#30DDF0", "purple": "#9A6BFF", "green": "#16D9A2", "orange": "#FFB454",
    "red": "#FF647C", "pink": "#FF62BC"
}
MODULES = {
    "Dashboard": ("⌂", "Business overview"),
    "Sales": ("↗", "Customers, quotations, orders and invoices"),
    "Purchases": ("⇩", "Suppliers and purchase orders"),
    "Inventory": ("⬡", "Products, stock and movements"),
    "Finance": ("◉", "Income, expenses and accounting workspace"),
    "HR & Payroll": ("♙", "Employees, leave and payroll records"),
    "CRM": ("◎", "Customer relationships and follow-ups"),
    "Projects": ("▦", "Project planning and delivery"),
    "Repair Tickets": ("⚒", "Device repairs, approvals, parts, and collection"),
    "Reports": ("▤", "Operational reports"),
    "User Management": ("♧", "Users and access roles"),
    "Role Permissions": ("⚿", "Role-based access control"),
    "Module Manager": ("✣", "Enable, disable and configure modules"),
    "Feature Store": ("✧", "Future trusted extension catalog"),
    "Integrations": ("⇄", "External system connectors"),
    "System Settings": ("⚙", "Application and sync settings"),
    "Database & Backup": ("▤", "Local data and backup tools"),
}
ROLE_CAPABILITIES = ("view", "create", "edit", "delete", "export", "print", "configure", "manage_users", "backup", "sync", "manage_inventory", "manage_finance", "process_returns")
DASHBOARD_WIDGETS = {
    "sales_count": "Sales count",
    "purchase_count": "Purchase count",
    "product_count": "Product count",
    "low_stock": "Low-stock products",
    "legacy_count": "Legacy record count",
    "sync_count": "Pending sync count",
    "finance_summary": "Finance summary",
    "sales_trend": "Seven-day sales chart",
    "quick_actions": "Quick actions",
    "recent_activity": "Recent activity",
}
DEFAULT_DASHBOARD_WIDGETS = {
    "Admin": set(DASHBOARD_WIDGETS),
    "Manager": set(DASHBOARD_WIDGETS)-{"sync_count"},
    "Staff": {"sales_count","product_count","low_stock","legacy_count","sales_trend","quick_actions","recent_activity"},
}
ROLE_DEFAULTS = {
    "Admin": set(ROLE_CAPABILITIES),
    "Manager": {"view", "create", "edit", "export", "print", "manage_inventory", "manage_finance", "process_returns"},
    "Staff": {"view", "create", "edit", "print"},
}
PROTECTED_CAPABILITIES = {"configure", "manage_users", "backup", "sync", "delete"}
DEFAULT_FIELDS = {
    "Sales": ["Customer", "Reference", "Amount (KES)", "Status"],
    "Purchases": ["Supplier", "Reference", "Amount (KES)", "Status"],
    "Inventory": ["Product", "SKU", "Quantity", "Unit Price (KES)"],
    "Finance": ["Type", "Description", "Amount (KES)", "Date"],
    "HR & Payroll": ["Employee", "Department", "Amount (KES)", "Status"],
    "CRM": ["Customer", "Contact", "Email", "Status"],
    "Projects": ["Project", "Owner", "Due Date", "Status"],
    "Reports": ["Report Name", "Period", "Notes", "Status"],
}
def stamp(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def hash_password(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    return salt.hex() + ":" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 240_000).hex()
def check_password(pw, saved):
    try:
        salt, digest = saved.split(":", 1)
        return secrets.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 240_000).hex(), digest)
    except Exception: return False

class Store:
    def __init__(self):
        self.actor_role = "Admin"
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.setup()
        self.operations=OperationsService(self.conn)
        self.operations.permission_provider=self.role_permissions
        self.operations.module_access_provider=self.role_has_module
        self.operations.tax_provider=self.tax_configuration
    def setup(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL, password_hash TEXT NOT NULL, active INTEGER DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS features(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, description TEXT DEFAULT '', fields_json TEXT NOT NULL, enabled INTEGER DEFAULT 1, custom INTEGER DEFAULT 0, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY, module TEXT NOT NULL, data_json TEXT NOT NULL, status TEXT DEFAULT 'Open', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sync_queue(id INTEGER PRIMARY KEY, entity TEXT NOT NULL, entity_id INTEGER NOT NULL, operation TEXT NOT NULL, payload_json TEXT NOT NULL, queued_at TEXT NOT NULL, attempts INTEGER DEFAULT 0, last_error TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY, username TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
        """)
        for n, fields in DEFAULT_FIELDS.items():
            self.conn.execute("INSERT OR IGNORE INTO features(name,description,fields_json,enabled,custom,created_at) VALUES(?,?,?,?,0,?)",
                              (n, MODULES[n][1], json.dumps(fields), 1, stamp()))
        self.conn.commit()
    def require(self, capability):
        if capability not in self.role_permissions(self.actor_role):
            raise PermissionError(f"The {self.actor_role} role is not allowed to {capability.replace('_', ' ')}.")
    def needs_initial_setup(self):
        users = self.conn.execute("SELECT username,password_hash,role,active FROM users ORDER BY id").fetchall()
        if not users:
            return True
        if not any(r["role"] == "Admin" and r["active"] for r in users):
            return True
        # Earlier prototype databases shipped a public default password. Preserve
        # existing records, but require the owner to replace that credential.
        return any(r["username"].casefold() == "admin" and check_password("ChangeMe123!", r["password_hash"]) for r in users)
    def configure_initial_admin(self, username, display_name, password):
        if not self.needs_initial_setup():
            self.require("manage_users")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", str(username)) or not str(display_name).strip() or len(str(password)) < 12:
            raise ValueError("Use a valid username, display name, and password of at least 12 characters.")
        row = self.conn.execute("SELECT id FROM users WHERE username=? COLLATE NOCASE", ("admin",)).fetchone()
        if row:
            self.conn.execute("UPDATE users SET username=?,display_name=?,role='Admin',password_hash=?,active=1 WHERE id=?",
                              (username, display_name, hash_password(password), row["id"]))
        else:
            self.conn.execute("INSERT INTO users(username,display_name,role,password_hash,active,created_at) VALUES(?,?, 'Admin', ?,1,?)",
                              (username, display_name, hash_password(password), stamp()))
        self.conn.commit()
    def log(self,u,a,d=""):
        self.conn.execute("INSERT INTO activity(username,action,details,created_at) VALUES(?,?,?,?)",(u,a,d,stamp())); self.conn.commit()
    def queue(self, entity, entity_id, op, payload):
        self.conn.execute("INSERT INTO sync_queue(entity,entity_id,operation,payload_json,queued_at) VALUES(?,?,?,?,?)",
                          (entity,entity_id,op,json.dumps(payload,ensure_ascii=False),stamp()))
        self.conn.commit()
    def _validate_record_data(self, module, data):
        if not isinstance(data, dict):
            raise ValueError("Record data must be a field/value object.")
        feature = self.feature(module)
        if not feature:
            raise ValueError(f"The {module} module is not configured.")
        try:
            definitions = json.loads(feature["fields_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"The field configuration for {module} is invalid.") from exc
        for definition in definitions:
            if isinstance(definition, str):
                continue
            if not isinstance(definition, dict) or not definition.get("name"):
                continue
            name = str(definition["name"])
            value = data.get(name)
            if definition.get("required") and (value is None or (isinstance(value, str) and not value.strip())):
                raise ValueError(f"{name} is required.")
            if value is None or value == "":
                continue
            field_type = definition.get("type", "text")
            if field_type == "text" and (not isinstance(value, str) or len(value) > 10000):
                raise ValueError(f"{name} must be text up to 10,000 characters.")
            if field_type == "notes" and (not isinstance(value, str) or len(value) > 100000):
                raise ValueError(f"{name} must be notes up to 100,000 characters.")
            if field_type == "number":
                try:
                    numeric = Decimal(str(value))
                except Exception as exc:
                    raise ValueError(f"{name} must be a number.") from exc
                if isinstance(value, bool) or not numeric.is_finite():
                    raise ValueError(f"{name} must be a finite number.")
            if field_type == "date":
                try:
                    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError
                    datetime.strptime(value, "%Y-%m-%d")
                except ValueError as exc:
                    raise ValueError(f"{name} must be a valid date in YYYY-MM-DD format.") from exc
            if field_type == "choice":
                options = definition.get("options", [])
                if str(value) not in options:
                    raise ValueError(f"{name} must be one of the configured options.")
        try:
            json.dumps(data, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Record data contains a value that cannot be stored safely.") from exc
        return data

    def create_record(self, module, data):
        self.require_module(module)
        self.require("create")
        data = self._validate_record_data(module, data)
        status = str(data.get("Status", data.get("status", "Open")))[:100]
        timestamp=stamp()
        with self.conn:
            cur=self.conn.execute("INSERT INTO records(module,data_json,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                                  (module,json.dumps(data,ensure_ascii=False),status,timestamp,timestamp))
            rid=cur.lastrowid
            payload={"id":rid,"module":module,"data":data,"status":status,"created_at":timestamp}
            self.conn.execute("INSERT INTO sync_queue(entity,entity_id,operation,payload_json,queued_at) VALUES(?,?,?,?,?)",
                              ("records",rid,"upsert",json.dumps(payload,ensure_ascii=False),timestamp))
        return rid
    def update_record(self, record_id, module, data):
        self.require_module(module)
        self.require("edit")
        data = self._validate_record_data(module, data)
        existing=self.conn.execute("SELECT status FROM records WHERE id=? AND module=?",(record_id,module)).fetchone()
        if not existing or existing["status"] == "Cancelled":
            raise ValueError("The record does not exist or has already been cancelled.")
        status = str(data.get("Status", data.get("status", "Open")))[:100]
        timestamp=stamp()
        payload={"id":record_id,"module":module,"data":data,"status":status,"updated_at":timestamp}
        with self.conn:
            self.conn.execute("UPDATE records SET data_json=?,status=?,updated_at=? WHERE id=? AND module=?",
                              (json.dumps(data,ensure_ascii=False),status,timestamp,record_id,module))
            self.conn.execute("INSERT INTO sync_queue(entity,entity_id,operation,payload_json,queued_at) VALUES(?,?,?,?,?)",
                              ("records",record_id,"upsert",json.dumps(payload,ensure_ascii=False),timestamp))
    def cancel_record(self, record_id, module):
        self.require_module(module)
        self.require("delete")
        row=self.conn.execute("SELECT data_json,status FROM records WHERE id=? AND module=?",(record_id,module)).fetchone()
        if not row or row["status"] == "Cancelled":
            return False
        data=json.loads(row["data_json"])
        if "Status" in data:
            data["Status"]="Cancelled"
        elif "status" in data:
            data["status"]="Cancelled"
        timestamp=stamp()
        with self.conn:
            self.conn.execute("UPDATE records SET data_json=?,status='Cancelled',updated_at=? WHERE id=? AND module=?",
                              (json.dumps(data,ensure_ascii=False),timestamp,record_id,module))
            payload={"id":record_id,"module":module,"data":data,"status":"Cancelled","updated_at":timestamp}
            self.conn.execute("INSERT INTO sync_queue(entity,entity_id,operation,payload_json,queued_at) VALUES(?,?,?,?,?)",
                              ("records",record_id,"upsert",json.dumps(payload,ensure_ascii=False),timestamp))
        return True
    def records(self,module=None,limit=500):
        if module:
            self.require_module(module)
            return self.conn.execute("SELECT * FROM records WHERE module=? ORDER BY id DESC LIMIT ?",(module,limit)).fetchall()
        allowed=sorted(name for name in self._known_modules() if self.role_has_module(self.actor_role,name))
        if not allowed:
            return []
        marks=",".join("?" for _ in allowed)
        return self.conn.execute(f"SELECT * FROM records WHERE module IN ({marks}) ORDER BY id DESC LIMIT ?",(*allowed,limit)).fetchall()
    def features(self, enabled_only=False):
        return self.conn.execute("SELECT * FROM features"+(" WHERE enabled=1" if enabled_only else "")+" ORDER BY custom,name").fetchall()
    def feature(self,name): return self.conn.execute("SELECT * FROM features WHERE name=?",(name,)).fetchone()
    def add_feature(self,name,desc,fields):
        self.require("configure")
        name = str(name).strip()
        desc = str(desc).strip()
        if not 2 <= len(name) <= 40 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 &()_-]*", name):
            raise ValueError("Module names must be 2–40 characters and use letters, numbers, spaces, ampersands, parentheses, dashes, or underscores.")
        if len(desc) > 200:
            raise ValueError("Module purpose must be 200 characters or fewer.")
        if not isinstance(fields, list) or not 1 <= len(fields) <= 20:
            raise ValueError("A custom module must define between 1 and 20 fields.")
        clean_fields=[]
        seen=set()
        for item in fields:
            if not isinstance(item, dict):
                raise ValueError("Each custom field must have a valid definition.")
            field_name=str(item.get("name", "")).strip()
            field_type=item.get("type", "text")
            required=item.get("required", False)
            options=item.get("options", [])
            if not field_name or len(field_name) > 60:
                raise ValueError("Field names must contain 1–60 characters.")
            if field_name.casefold() in seen:
                raise ValueError("Field names must be unique within a module.")
            seen.add(field_name.casefold())
            if field_type not in ("text", "number", "date", "choice", "notes"):
                raise ValueError(f"Unsupported field type for {field_name}.")
            if not isinstance(required, bool):
                raise ValueError(f"Required status for {field_name} must be true or false.")
            if field_type == "choice":
                if not isinstance(options, list) or not 1 <= len(options) <= 30:
                    raise ValueError(f"Choice field {field_name} needs 1–30 options.")
                options=[str(option).strip() for option in options]
                if any(not option or len(option) > 60 for option in options) or len({option.casefold() for option in options}) != len(options):
                    raise ValueError(f"Options for {field_name} must be unique, non-empty values of 60 characters or fewer.")
            else:
                options=[]
            clean_fields.append({"name":field_name,"type":field_type,"required":required,"options":options})
        fields=clean_fields
        timestamp=stamp()
        with self.conn:
            cur=self.conn.execute("INSERT INTO features(name,description,fields_json,enabled,custom,created_at) VALUES(?,?,?,?,1,?)",
                                  (name,desc,json.dumps(fields),1,timestamp))
            payload={"name":name,"description":desc,"fields":fields,"enabled":True,"custom":True}
            self.conn.execute("INSERT INTO sync_queue(entity,entity_id,operation,payload_json,queued_at) VALUES(?,?,?,?,?)",
                              ("features",cur.lastrowid,"upsert",json.dumps(payload,ensure_ascii=False),timestamp))
    def set_enabled(self,name,value):
        self.require("configure")
        row=self.feature(name)
        if not row:
            return False
        if not value and self.conn.execute("SELECT 1 FROM records WHERE module=? LIMIT 1",(name,)).fetchone():
            raise ValueError(f"{name} has saved records, so it cannot be disabled. Keep the workspace enabled to preserve access to its history.")
        current={r["name"] for r in self.features(True)}
        desired=set(current)
        if value:
            desired.add(name)
        else:
            desired.discard(name)
        missing=missing_dependencies(desired)
        if missing:
            detail="; ".join(f"{module} requires {dependency}" for module,dependency in missing)
            raise ValueError("Module change would break a required workflow: "+detail+". Disable dependent modules first, or enable the required modules.")
        timestamp=stamp()
        with self.conn:
            self.conn.execute("UPDATE features SET enabled=? WHERE name=?",(int(value),name))
            payload={"name":name,"enabled":bool(value)}
            self.conn.execute("INSERT INTO sync_queue(entity,entity_id,operation,payload_json,queued_at) VALUES(?,?,?,?,?)",
                              ("features",row["id"],"upsert",json.dumps(payload),timestamp))
        return True
    def modules_preserved_by_profile(self, profile_name):
        profile=get_profile(profile_name)
        target=set(profile.modules)
        recorded={row["module"] for row in self.conn.execute("SELECT DISTINCT module FROM records")}
        preserved=(recorded-target) & self._known_modules()
        pending=list(preserved)
        while pending:
            module=pending.pop()
            for dependency in MODULE_DEPENDENCIES.get(module, ()):
                if dependency not in target and dependency not in preserved:
                    preserved.add(dependency)
                    pending.append(dependency)
        return sorted(preserved)
    def apply_profile(self, profile_name):
        self.require("configure")
        profile=get_profile(profile_name)
        enabled=set(profile.modules) | set(self.modules_preserved_by_profile(profile_name))
        missing=missing_dependencies(enabled)
        if missing:
            detail="; ".join(f"{module} requires {dependency}" for module,dependency in missing)
            raise ValueError("This business profile has an incomplete module dependency configuration: "+detail)
        workspace_fields={}
        for blueprint in BUSINESS_PROFILES.values():
            workspace_fields.update(blueprint.workspaces)
        timestamp=stamp()
        with self.conn:
            for name, fields in workspace_fields.items():
                row=self.conn.execute("SELECT id FROM features WHERE name=?",(name,)).fetchone()
                if not row:
                    self.conn.execute("INSERT INTO features(name,description,fields_json,enabled,custom,created_at) VALUES(?,?,?,?,0,?)",
                                      (name,f"Profile workspace · {profile.name}",json.dumps(list(fields),ensure_ascii=False),0,timestamp))
            configured=set(CORE_MODULES) | set(workspace_fields)
            for row in self.conn.execute("SELECT id,name,enabled,custom FROM features").fetchall():
                name=row["name"]
                if name not in configured or (row["custom"] and name not in workspace_fields):
                    continue
                desired=int(name in enabled)
                if row["enabled"] == desired:
                    continue
                self.conn.execute("UPDATE features SET enabled=? WHERE id=?",(desired,row["id"]))
                payload={"name":name,"enabled":bool(desired)}
                self.conn.execute("INSERT INTO sync_queue(entity,entity_id,operation,payload_json,queued_at) VALUES(?,?,?,?,?)",
                                  ("features",row["id"],"upsert",json.dumps(payload),timestamp))
            self.conn.execute("INSERT INTO settings(key,value) VALUES('applied_profile',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(profile.name,))
        return profile.name
    def tax_configuration(self):
        return {"label":self.setting("business_tax_label","Tax"),"rate":self.setting("business_tax_rate","0.00")}
    def require_module(self, module):
        if not self.role_has_module(self.actor_role,module):
            raise PermissionError(f"The {self.actor_role} role cannot access the {module} module.")
    def _known_modules(self):
        return {row["name"] for row in self.features()}
    def custom_roles(self):
        try:
            rows=json.loads(self.setting("custom_roles","[]"))
            return [row["name"] for row in rows if isinstance(row,dict) and isinstance(row.get("name"),str)]
        except (TypeError,ValueError,AttributeError):
            return []
    def assignable_roles(self):
        return ["Manager","Staff"]+self.custom_roles()
    def role_template(self, role):
        if role in {"Manager","Staff"}:
            return role
        try:
            rows=json.loads(self.setting("custom_roles","[]"))
            return next((row.get("template","Staff") for row in rows if isinstance(row,dict) and row.get("name")==role),"Staff")
        except (TypeError,ValueError,AttributeError):
            return "Staff"
    def create_custom_role(self, name, template_role="Staff"):
        self.require("configure")
        name=" ".join(str(name or "").split())
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 &_-]{1,31}",name):
            raise ValueError("Use a role name of 2–32 letters, numbers, spaces, underscores or dashes, starting with a letter.")
        if template_role not in {"Manager","Staff"}:
            raise ValueError("Choose Manager or Staff as the starting permission template.")
        if name.casefold()=="admin" or any(existing.casefold()==name.casefold() for existing in ["Admin"]+self.assignable_roles()):
            raise ValueError("A role with that name already exists or is reserved.")
        try:
            roles=json.loads(self.setting("custom_roles","[]"))
            if not isinstance(roles,list): roles=[]
        except (TypeError,ValueError): roles=[]
        try:
            grants=json.loads(self.setting("role_permissions","{}"))
            modules=json.loads(self.setting("role_modules","{}"))
            widgets=json.loads(self.setting("dashboard_widgets","{}"))
        except (TypeError,ValueError):
            grants,modules,widgets={},{},{}
        if not all(isinstance(value,dict) for value in (grants,modules,widgets)):
            grants,modules,widgets={},{},{}
        roles.append({"name":name,"template":template_role})
        grants[name]=sorted(self.role_permissions(template_role))
        modules[name]=sorted(self.role_modules(template_role))
        widgets[name]=sorted(self.dashboard_widgets(template_role))
        with self.conn:
            for key,value in (("custom_roles",roles),("role_permissions",grants),("role_modules",modules),("dashboard_widgets",widgets)):
                self.conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,json.dumps(value,sort_keys=True)))
        return name
    def delete_custom_role(self, role):
        self.require("configure")
        if role not in self.custom_roles():
            raise ValueError("Only a custom role can be removed.")
        if self.conn.execute("SELECT 1 FROM users WHERE role=? LIMIT 1",(role,)).fetchone():
            raise ValueError("Reassign or remove every account using this role before deleting it.")
        roles=[row for row in json.loads(self.setting("custom_roles","[]")) if not isinstance(row,dict) or row.get("name")!=role]
        with self.conn:
            for key,value in (("custom_roles",roles),):
                self.conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,json.dumps(value,sort_keys=True)))
            for key in ("role_permissions","role_modules","dashboard_widgets"):
                try: saved=json.loads(self.setting(key,"{}"))
                except (TypeError,ValueError): saved={}
                if isinstance(saved,dict):
                    saved.pop(role,None)
                    self.conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,json.dumps(saved,sort_keys=True)))
        return True
    def role_modules(self, role):
        enabled={row["name"] for row in self.features(True)}
        if role == "Admin":
            return enabled
        if role not in self.assignable_roles():
            return set()
        defaults={"Manager":enabled,"Staff":enabled.intersection({"Sales","Inventory","CRM"})}
        default=defaults.get(role,set())
        try:
            saved=json.loads(self.setting("role_modules","{}"))
            assigned=set(saved.get(role,default))
            return assigned.intersection(self._known_modules()).intersection(enabled)
        except (TypeError,ValueError,AttributeError):
            return default
    def role_has_module(self, role, module):
        feature=self.feature(module)
        return bool(feature and feature["enabled"] and module in self.role_modules(role))
    def dashboard_widgets(self, role):
        if role!="Admin" and role not in self.assignable_roles():
            return set()
        fallback=DEFAULT_DASHBOARD_WIDGETS.get(role,DEFAULT_DASHBOARD_WIDGETS["Staff"])
        try:
            saved=json.loads(self.setting("dashboard_widgets","{}"))
            requested=set(saved.get(role,fallback))
            return requested.intersection(DASHBOARD_WIDGETS)
        except (TypeError,ValueError,AttributeError):
            return set(fallback)
    def set_dashboard_widgets(self, role, widgets):
        self.require("configure")
        if role!="Admin" and role not in self.assignable_roles():
            raise ValueError("Unknown dashboard role.")
        requested=set(widgets)
        if not requested.issubset(DASHBOARD_WIDGETS):
            raise ValueError("The widget selection contains an unsupported item.")
        try:
            saved=json.loads(self.setting("dashboard_widgets","{}"))
        except (TypeError,ValueError):
            saved={}
        if not isinstance(saved,dict):
            saved={}
        saved[role]=sorted(requested)
        self.set_setting("dashboard_widgets",json.dumps(saved,sort_keys=True))
    def set_role_modules(self, role, modules):
        self.require("configure")
        if role not in self.assignable_roles():
            raise ValueError("Only non-administrator roles can be configured.")
        requested=set(modules)
        unknown=requested-self._known_modules()
        if unknown:
            raise ValueError("Unknown modules: "+", ".join(sorted(unknown)))
        try:
            saved=json.loads(self.setting("role_modules","{}"))
        except (TypeError,ValueError):
            saved={}
        if not isinstance(saved,dict):
            saved={}
        saved[role]=sorted(requested)
        self.set_setting("role_modules",json.dumps(saved,sort_keys=True))
    def setting(self,k,default=""):
        r=self.conn.execute("SELECT value FROM settings WHERE key=?",(k,)).fetchone(); return r["value"] if r else default
    def set_setting(self,k,v):
        if k.startswith("business_") or k in {"role_permissions", "role_modules", "dashboard_widgets", "custom_roles", "applied_profile", "sync_url", "device_name"}:
            self.require("configure")
        if k == "business_tax_rate":
            try:
                rate=Decimal(str(v))
            except Exception as exc:
                raise ValueError("Tax rate must be a number from 0 to 100%.") from exc
            if not rate.is_finite() or rate < 0 or rate > 100:
                raise ValueError("Tax rate must be between 0 and 100%.")
            v=format(rate.quantize(Decimal("0.01")),"f")
        self.conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,str(v))); self.conn.commit()
    def role_permissions(self, role):
        if role == "Admin":
            return set(ROLE_CAPABILITIES)
        if role not in self.assignable_roles():
            return set()
        fallback=ROLE_DEFAULTS.get(role,set())
        try:
            saved=json.loads(self.setting("role_permissions", "{}"))
            requested=set(saved.get(role, fallback))
            return requested.intersection(set(ROLE_CAPABILITIES)-PROTECTED_CAPABILITIES)
        except (TypeError, ValueError, AttributeError):
            return set(fallback)
    def set_role_permissions(self, role, capabilities):
        self.require("configure")
        if role not in self.assignable_roles():
            raise ValueError("Only non-administrator role permissions can be configured.")
        allowed=set(ROLE_CAPABILITIES)-PROTECTED_CAPABILITIES
        requested=set(capabilities)
        if not requested.issubset(allowed):
            raise ValueError("The permission set contains an unknown or administrator-only capability.")
        try:
            saved=json.loads(self.setting("role_permissions", "{}"))
        except (TypeError, ValueError):
            saved={}
        if not isinstance(saved, dict):
            saved={}
        saved[role]=sorted(requested)
        self.set_setting("role_permissions", json.dumps(saved, sort_keys=True))
    def restore_backup(self, source_path):
        self.require("backup")
        source_path=Path(source_path).resolve()
        if source_path == Path(DB_PATH).resolve():
            raise ValueError("Choose a backup file, not the active database.")
        if not source_path.is_file():
            raise ValueError("The selected backup file does not exist.")
        stamp_id=datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safety_path=APP_DIR/f"nextgen_erp_pre_restore_{stamp_id}.db"
        candidate_path=APP_DIR/f"nextgen_erp_restore_candidate_{secrets.token_hex(6)}.db"
        source=None
        candidate=None
        safe_created=False
        try:
            source=sqlite3.connect(source_path.as_uri()+"?mode=ro",uri=True)
            source.row_factory=sqlite3.Row
            check=source.execute("PRAGMA quick_check").fetchone()[0]
            if check != "ok":
                raise ValueError(f"The selected database failed SQLite integrity check: {check}")
            tables={row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            required={"users","features","records","sync_queue","settings","activity"}
            if not required.issubset(tables):
                missing=", ".join(sorted(required-tables))
                raise ValueError(f"This is not a compatible NextGen ERP backup. Missing tables: {missing}")
            candidate=sqlite3.connect(candidate_path)
            source.backup(candidate)
            candidate.row_factory=sqlite3.Row
            candidate.execute("PRAGMA foreign_keys=ON")
            # Run the additive application migrations on the candidate before
            # replacing the live database.
            temporary_store=Store.__new__(Store)
            temporary_store.conn=candidate
            temporary_store.setup()
            OperationsService(candidate)
            check=candidate.execute("PRAGMA quick_check").fetchone()[0]
            if check != "ok":
                raise ValueError(f"The migrated backup failed SQLite integrity check: {check}")
            candidate.close()
            candidate=None
            safety=sqlite3.connect(str(safety_path))
            self.conn.backup(safety)
            safety.close()
            safe_created=True
            candidate=sqlite3.connect(str(candidate_path))
            candidate.backup(self.conn)
            candidate.close()
            candidate=None
            self.setup()
            self.operations=OperationsService(self.conn)
            self.operations.permission_provider=self.role_permissions
            self.operations.module_access_provider=self.role_has_module
            self.operations.tax_provider=self.tax_configuration
            self.operations.actor_role=self.actor_role
            return str(safety_path)
        except Exception:
            if safe_created:
                recovery=sqlite3.connect(str(safety_path))
                recovery.backup(self.conn)
                recovery.close()
            raise
        finally:
            if source is not None:
                source.close()
            if candidate is not None:
                candidate.close()
            try:
                candidate_path.unlink(missing_ok=True)
            except OSError:
                pass

    def pending(self): return self.conn.execute("SELECT * FROM sync_queue ORDER BY id").fetchall()

DARK_C = dict(C)
# Warm vanilla palette: deliberately avoids the harsh pure-white look.
LIGHT_C = {
    "bg":"#F3EDE2", "sidebar":"#F8F3EA", "panel":"#FFF9EF", "panel2":"#EAE1D3",
    "border":"#D9CCBA", "text":"#263247", "muted":"#786F63", "blue":"#426B8A",
    "cyan":"#438C91", "purple":"#735C91", "green":"#397A61", "orange":"#B8793E",
    "red":"#B95358", "pink":"#B36C8D"
}

ACCENT_COLORS = {
    "Ocean blue": ("#426B8A", "#3069D6"),
    "Forest green": ("#587963", "#16A977"),
    "Warm amber": ("#9B6D3D", "#D9913C"),
    "Royal violet": ("#7B689B", "#9A6BFF"),
    "Teal": ("#39777B", "#30DDF0"),
}


def _relative_luminance(color):
    value=color.lstrip("#")
    channels=[int(value[index:index+2],16)/255 for index in (0,2,4)]
    linear=[channel/12.92 if channel<=0.04045 else ((channel+0.055)/1.055)**2.4 for channel in channels]
    return 0.2126*linear[0]+0.7152*linear[1]+0.0722*linear[2]

def _contrast_ratio(first,second):
    high,low=sorted((_relative_luminance(first),_relative_luminance(second)),reverse=True)
    return (high+0.05)/(low+0.05)

def _contrast_text(background):
    candidates=("#FFFFFF","#172234")
    return max(candidates,key=lambda candidate:_contrast_ratio(candidate,background))

def palette_for(store, theme=None):
    mode=theme or store.setting("theme", "light")
    palette=dict(LIGHT_C if mode == "light" else DARK_C)
    accent=store.setting("business_accent", "Ocean blue")
    light_color,dark_color=ACCENT_COLORS.get(accent, ACCENT_COLORS["Ocean blue"])
    palette["blue"]=light_color if mode == "light" else dark_color
    palette["on_blue"]=_contrast_text(palette["blue"])
    palette["on_purple"]=_contrast_text(palette["purple"])
    # Semantic roles keep widgets and popups on the same theme tokens.
    palette.update({
        "input":palette["panel"], "input_text":palette["text"], "placeholder":palette["muted"],
        "hover":palette["panel2"], "selection":palette["blue"], "focus":palette["blue"],
        "table_alt":palette["panel2"], "tooltip_bg":palette["panel2"],
        "disabled":palette["muted"], "success":palette["green"], "warning":palette["orange"],
        "error":palette["red"], "info":palette["cyan"],
    })
    return palette

def themed_style(palette):
    return f"""
QWidget {{ background:{palette['bg']}; color:{palette['text']}; font-family:'Segoe UI'; font-size:10pt; }}
QDialog, QMenu {{ background:{palette['panel']}; color:{palette['text']}; }}
QLabel {{ background:transparent; }}
QFrame#panel {{ background:{palette['panel']}; border:1px solid {palette['border']}; border-radius:16px; }}
QFrame#panelAlt {{ background:{palette['panel2']}; border:1px solid {palette['border']}; border-radius:14px; }}
QPushButton {{ background:{palette['panel2']}; color:{palette['text']}; border:1px solid {palette['border']}; border-radius:9px; padding:10px 15px; font-weight:600; min-height:18px; }}
QPushButton:hover {{ background:{palette['hover']}; border-color:{palette['blue']}; }}
QPushButton:disabled {{ background:{palette['panel2']}; color:{palette['disabled']}; border-color:{palette['border']}; }}
QPushButton#primary {{ background:{palette['blue']}; border:0; color:{palette['on_blue']}; }}
QPushButton#primary:hover {{ background:{palette['purple']}; color:{palette['on_purple']}; }}
QPushButton#nav {{ text-align:left; padding:12px 14px; background:transparent; border:0; border-radius:9px; color:{palette['text']}; }}
QPushButton#nav:hover {{ background:{palette['hover']}; }}
QPushButton#navActive {{ text-align:left; padding:12px 14px; background:{palette['blue']}; border:0; border-radius:9px; color:{palette['on_blue']}; font-weight:700; }}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit {{ background:{palette['input']}; border:1px solid {palette['border']}; border-radius:9px; padding:10px; color:{palette['input_text']}; selection-background-color:{palette['selection']}; min-height:20px; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus {{ border:1px solid {palette['focus']}; }}
QLineEdit::placeholder {{ color:{palette['placeholder']}; }}
QComboBox QAbstractItemView {{ background:{palette['input']}; color:{palette['input_text']}; selection-background-color:{palette['selection']}; selection-color:{palette['on_blue']}; border:1px solid {palette['border']}; }}
QMenu::item:selected {{ background:{palette['selection']}; color:{palette['on_blue']}; }}
QTableWidget {{ background:{palette['panel']}; alternate-background-color:{palette['table_alt']}; color:{palette['text']}; border:1px solid {palette['border']}; gridline-color:{palette['border']}; selection-background-color:{palette['selection']}; selection-color:{palette['on_blue']}; }}
QHeaderView::section {{ background:{palette['panel2']}; color:{palette['text']}; padding:12px 10px; border:0; border-bottom:1px solid {palette['border']}; font-weight:700; }}
QScrollArea {{ border:0; background:transparent; }}
QScrollBar:vertical {{ background:{palette['bg']}; width:12px; margin:2px; }}
QScrollBar::handle:vertical {{ background:{palette['border']}; min-height:32px; border-radius:6px; }}
QScrollBar:horizontal {{ background:{palette['bg']}; height:12px; margin:2px; }}
QScrollBar::handle:horizontal {{ background:{palette['border']}; min-width:32px; border-radius:6px; }}
QCheckBox {{ spacing:9px; }}
QCheckBox::indicator {{ width:18px; height:18px; }}
QCheckBox::indicator:checked {{ background:{palette['blue']}; border:1px solid {palette['blue']}; }}
QToolTip {{ background:{palette['tooltip_bg']}; color:{palette['text']}; border:1px solid {palette['border']}; padding:6px; }}
"""


class InitialSetupDialog(QDialog):
    """Create or rotate the local administrator credential before opening the ERP."""
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.setWindowTitle("Set up NextGen ERP")
        self.setModal(True)
        self.setMinimumWidth(460)
        palette = palette_for(store)
        self.setStyleSheet(themed_style(palette))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(13)
        title = QLabel("⬡  Set up your business workspace")
        title.setWordWrap(True)
        title.setStyleSheet("font-size:18pt;font-weight:900;padding:4px 0 6px;")
        layout.addWidget(title)
        intro = QLabel("Create the owner account and choose a starter business setup. You can refine business details and modules later in Settings.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.business_name_input = QLineEdit(store.setting("business_name", ""))
        self.business_name_input.setPlaceholderText("Business or organization name")
        self.business_type_input = QComboBox()
        self.business_type_input.addItems(list(BUSINESS_PROFILES))
        saved_profile = store.setting("business_type", "General business")
        profile_index = self.business_type_input.findText(saved_profile)
        self.business_type_input.setCurrentIndex(profile_index if profile_index >= 0 else 0)
        self.currency_input = QComboBox()
        self.currency_input.addItems(["KES - Kenyan Shilling", "USD - US Dollar", "TZS - Tanzanian Shilling", "UGX - Ugandan Shilling", "GBP - British Pound", "EUR - Euro"])
        saved_currency = store.setting("business_currency", "KES - Kenyan Shilling")
        currency_index = self.currency_input.findText(saved_currency)
        self.currency_input.setCurrentIndex(currency_index if currency_index >= 0 else 0)
        self.theme_input = QComboBox()
        self.theme_input.addItem("Warm vanilla light", "light")
        self.theme_input.addItem("Coordinated dark", "dark")
        saved_theme = store.setting("theme", "light")
        theme_index = self.theme_input.findData(saved_theme)
        self.theme_input.setCurrentIndex(theme_index if theme_index >= 0 else 0)
        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("3–32 letters, digits, dots, dashes, or underscores")
        self.display_input = QLineEdit()
        self.display_input.setPlaceholderText("Your name")
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setPlaceholderText("At least 12 characters")
        self.confirm_input = QLineEdit()
        self.confirm_input.setEchoMode(QLineEdit.Password)
        self.confirm_input.setPlaceholderText("Re-enter your password")
        form.addRow("Business name", self.business_name_input)
        form.addRow("Starter business profile", self.business_type_input)
        form.addRow("Default currency", self.currency_input)
        form.addRow("Theme", self.theme_input)
        form.addRow("Administrator username", self.username_input)
        form.addRow("Display name", self.display_input)
        form.addRow("Password", self.password_input)
        form.addRow("Confirm password", self.confirm_input)
        layout.addLayout(form)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color:{palette['red']};")
        layout.addWidget(self.error_label)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Exit")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Create administrator")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def save(self):
        username = self.username_input.text().strip()
        display_name = self.display_input.text().strip()
        password = self.password_input.text()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
            self.error_label.setText("Use 3–32 letters, digits, dots, dashes, or underscores for the username.")
            return
        if not display_name or len(display_name) > 80:
            self.error_label.setText("Enter a display name of up to 80 characters.")
            return
        if len(password) < 12:
            self.error_label.setText("Use a password with at least 12 characters.")
            return
        if password != self.confirm_input.text():
            self.error_label.setText("The passwords do not match.")
            return
        business_name = self.business_name_input.text().strip()
        if not business_name or len(business_name) > 120:
            self.error_label.setText("Enter a business name of up to 120 characters.")
            return
        profile = self.business_type_input.currentText()
        if profile not in BUSINESS_PROFILES:
            self.error_label.setText("Choose a valid starter business profile.")
            return
        try:
            self.store.configure_initial_admin(username, display_name, password)
            self.store.apply_profile(profile)
            self.store.set_setting("business_name", business_name)
            self.store.set_setting("business_type", profile)
            self.store.set_setting("business_currency", self.currency_input.currentText())
            self.store.set_setting("theme", self.theme_input.currentData())
            application = QApplication.instance()
            if application:
                application.setStyleSheet(themed_style(palette_for(self.store)))
        except sqlite3.IntegrityError:
            self.error_label.setText("That username already exists. Choose another one.")
            return
        except (PermissionError, ValueError, sqlite3.DatabaseError) as exc:
            self.error_label.setText(f"The setup could not finish: {exc}")
            return
        self.accept()

class LoginDialog(QDialog):
    """Local account login dialog. Validates credentials against the SQLite user store."""
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.username = ""
        self.role = ""
        palette = palette_for(store)
        self.setWindowTitle("Sign in | NextGen ERP")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setStyleSheet(themed_style(palette))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 24)
        layout.setSpacing(14)
        brand = QLabel("⬡  NextGen ERP")
        brand.setStyleSheet("font-size:22pt;font-weight:900;padding:4px 0 8px;")
        subtitle = QLabel("Sign in to your business workspace")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color:{palette['muted']};")
        layout.addWidget(brand)
        layout.addWidget(subtitle)
        form = QFormLayout()
        form.setSpacing(12)
        self.user_input = QLineEdit()
        self.user_input.setPlaceholderText("Username")
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Password")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.returnPressed.connect(self.attempt_login)
        form.addRow("Username", self.user_input)
        form.addRow("Password", self.password_input)
        layout.addLayout(form)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color:{palette['red']};")
        layout.addWidget(self.error_label)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton("Exit")
        self.cancel_button.clicked.connect(self.reject)
        self.login_button = QPushButton("Sign in")
        self.login_button.setObjectName("primary")
        self.login_button.clicked.connect(self.attempt_login)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.login_button)
        layout.addLayout(buttons)
        self.user_input.setFocus()

    def attempt_login(self):
        username = self.user_input.text().strip()
        password = self.password_input.text()
        row = self.store.conn.execute(
            "SELECT username, role, password_hash, active FROM users WHERE username=? COLLATE NOCASE",
            (username,)
        ).fetchone()
        if not row or not row["active"] or not check_password(password, row["password_hash"]):
            self.error_label.setText("The username or password is incorrect, or this account is inactive.")
            self.password_input.selectAll()
            self.password_input.setFocus()
            return
        self.username = row["username"]
        self.role = row["role"]
        self.accept()

class MetricCard(QFrame):
    """Compact dashboard metric tile using the active application palette."""
    def __init__(self, title, value, status, accent, subtitle, parent=None, palette=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.setMinimumHeight(126)
        self.setSizePolicy(__import__('PySide6.QtWidgets', fromlist=['QSizePolicy']).QSizePolicy.Policy.Expanding,
                           __import__('PySide6.QtWidgets', fromlist=['QSizePolicy']).QSizePolicy.Policy.Minimum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)
        top = QHBoxLayout()
        heading = QLabel(str(title)); heading.setWordWrap(True)
        heading.setStyleSheet("font-size:10pt;font-weight:700;")
        dot = QLabel("●"); dot.setStyleSheet(f"color:{accent};font-size:11pt;")
        top.addWidget(heading, 1); top.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)
        number = QLabel(str(value)); number.setWordWrap(True)
        number.setStyleSheet(f"color:{accent};font-size:23pt;font-weight:900;")
        layout.addWidget(number)
        detail = QLabel(str(subtitle)); detail.setWordWrap(True)
        colors=palette or DARK_C
        detail.setStyleSheet(f"font-size:9pt;color:{colors['muted']};")
        layout.addWidget(detail)
        layout.addStretch(1)


class ChartPanel(QFrame):
    """Seven-day booked-sales chart rendered with the active theme palette."""
    def __init__(self, title, values=None, currency="KES", palette=None, parent=None):
        super().__init__(parent)
        self.title=str(title)
        self.values=list(values or [])
        self.currency=str(currency)
        self.colors=palette or DARK_C
        self.setObjectName("panel")
        self.setMinimumHeight(220)
        self.setSizePolicy(__import__('PySide6.QtWidgets', fromlist=['QSizePolicy']).QSizePolicy.Policy.Expanding,
                           __import__('PySide6.QtWidgets', fromlist=['QSizePolicy']).QSizePolicy.Policy.Minimum)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter=QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left,right,top,bottom=18,16,16,16
        title_font=QFont(self.font()); title_font.setPointSize(12); title_font.setBold(True)
        painter.setFont(title_font); painter.setPen(QColor(self.colors["text"]))
        painter.drawText(QRectF(left,top,self.width()-left-right,24),Qt.AlignLeft|Qt.AlignVCenter,self.title)
        plot=QRectF(left,top+40,max(0,self.width()-left-right),max(0,self.height()-top-bottom-50))
        valid=[(str(label),Decimal(str(value))) for label,value in self.values]
        if not valid or all(value == 0 for _,value in valid):
            painter.setPen(QColor(self.colors["muted"]))
            painter.drawText(plot,Qt.AlignCenter,"Completed sales will appear here as you trade.")
            painter.end(); return
        maximum=max(value for _,value in valid)
        if maximum <= 0: maximum=Decimal("1")
        baseline=plot.bottom()-22
        painter.setPen(QPen(QColor(self.colors["border"]),1))
        painter.drawLine(QPointF(plot.left(),baseline),QPointF(plot.right(),baseline))
        gap=10
        slot=max(1,(plot.width()-gap*(len(valid)-1))/len(valid))
        bar_width=min(42,slot*0.56)
        label_font=QFont(self.font()); label_font.setPointSize(8)
        painter.setFont(label_font)
        for i,(label,value) in enumerate(valid):
            x=plot.left()+i*(slot+gap)+(slot-bar_width)/2
            height=float(value/maximum)*max(1,plot.height()-46)
            bar=QRectF(x,baseline-height,bar_width,height)
            painter.setPen(Qt.NoPen); painter.setBrush(QColor(self.colors["blue"]))
            painter.drawRoundedRect(bar,4,4)
            painter.setPen(QColor(self.colors["muted"]))
            painter.drawText(QRectF(plot.left()+i*(slot+gap),baseline+4,slot,18),Qt.AlignHCenter|Qt.AlignTop,label)
            if value:
                painter.setPen(QColor(self.colors["text"]))
                painter.drawText(QRectF(x-12,baseline-height-18,bar_width+24,16),Qt.AlignCenter,f"{self.currency} {value:,.0f}")
        painter.end()


class App(QMainWindow):
    def __init__(self, store, username, role):
        super().__init__(); self.store=store; self.username=username; self.role=role; self.active="Dashboard"; self.nav={}
        self.store.actor_role=role; self.store.operations.actor_role=role; self.store.operations.actor_username=username; self.setWindowTitle((self.store.setting("business_name", "Your Business").strip() or "Your Business") + " | NextGen ERP"); self.resize(1480,930); self.setMinimumSize(1120,720); self.theme = self.store.setting("theme", "light"); self.palette = palette_for(self.store, self.theme); self.setStyleSheet(themed_style(self.palette))
        root=QWidget(); self.setCentralWidget(root); main=QHBoxLayout(root); main.setContentsMargins(0,0,0,0); main.setSpacing(0)
        self.sidebar=QFrame(); self.sidebar.setFixedWidth(238); self.sidebar.setStyleSheet(f"background:{self.palette['sidebar']};border-right:1px solid {self.palette['border']};")
        sv=QVBoxLayout(self.sidebar); sv.setContentsMargins(13,18,13,15); sv.setSpacing(4)
        self.brand_label=QLabel()
        self.brand_label.setWordWrap(True)
        self.brand_label.setMaximumHeight(58)
        self.brand_label.setStyleSheet("font-size:14pt;font-weight:900;padding:3px 5px;")
        self.update_branding()
        sv.addWidget(self.brand_label)
        tagline=QLabel("SMARTER BUSINESS. BUILT TO ADAPT."); tagline.setStyleSheet(f"color:{self.palette['muted']};font-size:7pt;font-weight:700;padding:0 7px 16px;"); sv.addWidget(tagline)
        scroll=QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        navhost=QWidget(); self.navlay=QVBoxLayout(navhost); self.navlay.setContentsMargins(0,0,0,0); self.navlay.setSpacing(3)
        self.add_nav_section("WORKSPACE",["Dashboard","Sales","Purchases","Inventory","Finance","HR & Payroll","CRM","Projects","Reports"] + [r["name"] for r in self.store.features(True) if r["name"] not in MODULES])
        self.add_nav_section("ADMINISTRATION",["User Management","Role Permissions","Module Manager","Feature Store","Integrations","System Settings","Database & Backup"])
        self.navlay.addStretch(); scroll.setWidget(navhost); sv.addWidget(scroll,1)
        status=QFrame(); status.setObjectName("panelAlt"); sl=QVBoxLayout(status); sl.setContentsMargins(12,10,12,10)
        sl.addWidget(QLabel(f"<span style='color:{self.palette['green']}'>●</span>  LOCAL DATABASE"))
        sl.addWidget(QLabel(f"<span style='color:{self.palette['muted']};font-size:8pt'>{str(DB_PATH)[:34]}…</span>"))
        sv.addWidget(status)
        main.addWidget(self.sidebar)
        right=QWidget(); rv=QVBoxLayout(right); rv.setContentsMargins(0,0,0,0); rv.setSpacing(0); main.addWidget(right,1)
        top=QFrame(); self.topbar=top; top.setFixedHeight(66); top.setStyleSheet(f"background:{self.palette['sidebar']};border-bottom:1px solid {self.palette['border']};")
        th=QHBoxLayout(top); th.setContentsMargins(20,8,18,8)
        self.search=QLineEdit(); self.search.setMaximumWidth(390); self.search.setPlaceholderText("⌕   Search records, modules, customers…"); self.search.returnPressed.connect(self.search_records); th.addWidget(self.search)
        th.addStretch()
        self.theme_btn=QPushButton("☀ Light mode" if self.theme == "dark" else "☾ Dark mode"); self.theme_btn.clicked.connect(self.toggle_theme); th.addWidget(self.theme_btn)
        self.syncpill=QLabel(); self.syncpill.setStyleSheet(f"color:{self.palette['orange']};font-size:9pt;font-weight:700;padding:8px 12px;background:{self.palette['panel']};border:1px solid {self.palette['border']};border-radius:10px;")
        th.addWidget(self.syncpill)
        profile=QLabel(f"  {username}  ·  {role}  "); profile.setStyleSheet("font-weight:700;padding:8px;"); th.addWidget(profile)
        out=QPushButton("Sign out"); out.clicked.connect(self.sign_out); th.addWidget(out)
        rv.addWidget(top)
        self.stack=QStackedWidget(); rv.addWidget(self.stack,1)
        self.pages={}
        self.refresh_syncpill()
        self.show_page("Dashboard")
        self.timer=QTimer(self); self.timer.timeout.connect(self.refresh_syncpill); self.timer.start(8000)
    def closeEvent(self,event):
        timer=getattr(self,"timer",None)
        if timer is not None:
            timer.stop()
        super().closeEvent(event)
    def add_nav_section(self,heading,names):
        lab=QLabel(heading); lab.setStyleSheet(f"color:{self.palette['muted']};font-size:8pt;font-weight:800;letter-spacing:1px;padding:14px 8px 6px;")
        self.navlay.addWidget(lab)
        for name in names:
            if self.role!="Admin" and name in ["User Management","Role Permissions","Module Manager","Feature Store","Integrations","System Settings","Database & Backup"]: continue
            feature=self.store.feature(name)
            if feature and not self.store.role_has_module(self.role,name): continue
            b=QPushButton(f"{MODULES.get(name,('◇',''))[0]}    {name}")
            b.setObjectName("nav"); b.clicked.connect(lambda checked=False,n=name:self.show_page(n))
            self.navlay.addWidget(b); self.nav[name]=b
    def refresh_syncpill(self):
        n=len(self.store.pending())
        self.syncpill.setText(f"●  OFFLINE READY  ·  {n} QUEUED")
        self.syncpill.setStyleSheet(f"color:{self.palette['orange'] if n else self.palette['green']};font-size:9pt;font-weight:700;padding:8px 12px;background:{self.palette['panel']};border:1px solid {self.palette['border']};border-radius:10px;")
    def _authorize(self, capability):
        if capability in self.store.role_permissions(self.role):
            return True
        QMessageBox.warning(self, "Access denied", "Your account does not have permission to perform this action.")
        return False
    def show_page(self,name):
        admin_pages = {"User Management", "Role Permissions", "Module Manager", "Feature Store", "Integrations", "System Settings", "Database & Backup"}
        valid_pages=set(MODULES) | {r["name"] for r in self.store.features()}
        if name not in valid_pages:
            QMessageBox.warning(self, "Unknown workspace", "That workspace is not available in this installation.")
            return
        if (self.role!="Admin" and self.role not in self.store.assignable_roles()) or (name in admin_pages and self.role != "Admin"):
            QMessageBox.warning(self, "Access denied", "Your account does not have access to this workspace.")
            return
        feature = self.store.feature(name)
        if feature and not self.store.role_has_module(self.role,name):
            QMessageBox.warning(self, "Access denied", f"Your role has not been granted access to {name}.")
            return
        if feature and not feature["enabled"] and name != "Dashboard":
            QMessageBox.information(self, "Module disabled", f"{name} is currently disabled by the administrator.")
            return
        self.active=name
        for n,b in self.nav.items():
            active_button=(n==name)
            b.setObjectName("navActive" if active_button else "nav")
            b.setStyleSheet(f"background:{self.palette['blue']};color:{self.palette['on_blue']};font-weight:700;border:0;border-radius:9px;text-align:left;padding:12px 14px;" if active_button else "")
            b.style().unpolish(b); b.style().polish(b)
        if name in self.pages:
            self.stack.setCurrentWidget(self.pages[name]); return
        if name=="Dashboard": page=self.dashboard()
        elif name=="Module Manager": page=self.module_manager()
        elif name=="User Management": page=self.user_management()
        elif name=="System Settings": page=self.settings_page()
        elif name=="Database & Backup": page=self.database_page()
        elif name=="Role Permissions": page=self.role_permissions()
        elif name=="Feature Store": page=self.feature_store()
        elif name=="Integrations": page=self.integrations()
        elif name=="Inventory": page=self.inventory_page()
        elif name=="Sales": page=self.sales_page()
        elif name=="Purchases": page=self.purchases_page()
        elif name=="Finance": page=self.finance_page()
        elif name=="CRM": page=self.crm_page()
        elif name=="Reports": page=self.reports_page()
        elif name=="Repair Tickets": page=self.repair_page()
        else: page=self.records_page(name)
        self.pages[name]=page; self.stack.addWidget(page); self.stack.setCurrentWidget(page)
        saved=getattr(self,"_pending_theme_states",{}).pop(name,None)
        if saved:
            QApplication.processEvents()
            self._restore_page_state(page,saved)
    def page_container(self,title,subtitle):
        # Every page uses the same scrollable content host, eliminating clipped forms/cards.
        outer=QWidget(); outer.setObjectName("pageOuter")
        outer_layout=QVBoxLayout(outer); outer_layout.setContentsMargins(0,0,0,0); outer_layout.setSpacing(0)
        scroll=QScrollArea(); scroll.setObjectName("pageScroll"); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded); scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content=QWidget(); content.setObjectName("pageContent"); content.setMinimumWidth(0)
        v=QVBoxLayout(content); v.setContentsMargins(26,24,26,28); v.setSpacing(18); v.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)
        head=QHBoxLayout(); head.setSpacing(16); col=QVBoxLayout(); col.setSpacing(7)
        h=QLabel(title); h.setWordWrap(True); h.setStyleSheet("font-size:22pt;font-weight:900;"); col.addWidget(h)
        sub=QLabel(subtitle); sub.setWordWrap(True); sub.setStyleSheet(f"color:{self.palette['muted']};font-size:9pt;"); col.addWidget(sub)
        head.addLayout(col,1); v.addLayout(head)
        scroll.setWidget(content); outer_layout.addWidget(scroll)
        return outer,v
    def panel(self):
        f=QFrame(); f.setObjectName("panel"); return f
    def dashboard(self):
        business_name=self.store.setting("business_name", "Your Business").strip() or "Your Business"
        profile_name=self.store.setting("business_type", "General business")
        w,v=self.page_container(f"{business_name} · Good morning, {self.username} ✦",f"{profile_name} workspace · Your records stay on this device, even when the internet doesn't.")
        hero=QFrame(); hero.setObjectName("panelAlt"); hero.setMinimumHeight(105)
        hv=QHBoxLayout(hero); hv.setContentsMargins(22,16,22,16)
        textcol=QVBoxLayout()
        textcol.addWidget(QLabel(f"<span style='font-size:17pt;font-weight:900'>Move business forward.</span>"))
        textcol.addWidget(QLabel(f"<span style='color:{self.palette['muted']}'>One connected workspace. Less busywork. More visibility.</span>"))
        hv.addLayout(textcol); hv.addStretch()
        status=QLabel(f"◉  <span style='color:{self.palette['green']}'>LOCAL MODE ACTIVE</span><br><span style='color:{self.palette['muted']}'>Changes save instantly on this device</span>")
        status.setStyleSheet(f"background:{self.palette['bg']};border:1px solid {self.palette['border']};border-radius:10px;padding:12px;"); hv.addWidget(status)
        v.addWidget(hero)
        visible_modules=[r["name"] for r in self.store.features(True)]
        visible_modules=[m for m in visible_modules if self.store.role_has_module(self.role,m)]
        widgets=self.store.dashboard_widgets(self.role)
        if visible_modules and "legacy_count" in widgets:
            marks=",".join("?" for _ in visible_modules)
            legacy_total=self.store.conn.execute(f"SELECT COUNT(*) FROM records WHERE module IN ({marks})",visible_modules).fetchone()[0]
        else:
            legacy_total=0
        operations=self.store.operations
        sales_count=len(operations.sales(100000)) if "Sales" in visible_modules and "sales_count" in widgets else 0
        purchase_count=len(operations.purchases(100000)) if "Purchases" in visible_modules and "purchase_count" in widgets else 0
        product_count=len(operations.products()) if "Inventory" in visible_modules and "product_count" in widgets else 0
        low_stock_rows=operations.low_stock_products(5) if "Inventory" in visible_modules and "low_stock" in widgets else []
        low_stock_count=operations.low_stock_count() if "Inventory" in visible_modules and "low_stock" in widgets else 0
        metrics=QGridLayout(); metrics.setSpacing(12)
        cards=[]
        if "sales_count" in widgets and "Sales" in visible_modules:
            cards.append(("Sales",str(sales_count),"Stock-backed sales",self.palette["blue"]))
        if "purchase_count" in widgets and "Purchases" in visible_modules:
            cards.append(("Purchases",str(purchase_count),"Received purchase records",self.palette["cyan"]))
        if "product_count" in widgets and "Inventory" in visible_modules:
            cards.append(("Products",str(product_count),"Active stock items",self.palette["purple"]))
        if "low_stock" in widgets and "Inventory" in visible_modules:
            cards.append(("Low stock",str(low_stock_count),"At or below reorder level",self.palette["orange"]))
        if "legacy_count" in widgets:
            cards.append(("Legacy records",str(legacy_total),"Previous record workspace",self.palette["orange"]))
        if "sync_count" in widgets and "sync" in self.store.role_permissions(self.role):
            cards.append(("Waiting to sync",str(len(self.store.pending())),"Queued on this device",self.palette["green"]))
        for i,(title,value,detail,accent) in enumerate(cards):
            metrics.addWidget(MetricCard(title,value,"●  Local data",accent,detail,palette=self.palette),i//2,i%2)
        if cards:
            v.addLayout(metrics)
        if "Inventory" in visible_modules and "low_stock" in widgets:
            stock_panel=self.panel(); stock_layout=QVBoxLayout(stock_panel); stock_layout.setContentsMargins(16,14,16,14); stock_layout.setSpacing(8)
            stock_header=QHBoxLayout(); stock_title=QLabel("<span style='font-size:12pt;font-weight:800'>Reorder attention</span>")
            stock_header.addWidget(stock_title); stock_header.addStretch()
            open_inventory=QPushButton("Open Inventory"); open_inventory.clicked.connect(lambda:self.show_page("Inventory")); stock_header.addWidget(open_inventory)
            stock_layout.addLayout(stock_header)
            if low_stock_rows:
                low_table=self._table(["SKU","BARCODE","PRODUCT","CATEGORY","ON HAND","REORDER"])
                low_table.setRowCount(len(low_stock_rows)); low_table.setMaximumHeight(210)
                for row_index,product in enumerate(low_stock_rows):
                    values=[product["sku"],product["barcode"],product["name"],product["category"],
                            f"{Decimal(str(product['stock_on_hand'])):,.3f}".rstrip("0").rstrip("."),product["reorder_level"]]
                    for column,value in enumerate(values):
                        low_table.setItem(row_index,column,QTableWidgetItem(str(value)))
                stock_layout.addWidget(low_table)
                if low_stock_count > len(low_stock_rows):
                    stock_layout.addWidget(QLabel(f"Showing {len(low_stock_rows)} of {low_stock_count} products that need review."))
            else:
                empty=QLabel("No active products are at or below their reorder level.")
                empty.setStyleSheet(f"color:{self.palette['muted']};padding:8px;"); stock_layout.addWidget(empty)
            v.addWidget(stock_panel)
        if "finance_summary" in widgets and "Finance" in visible_modules:
            cash_entries=operations.cash_entries(100000)
            income=sum((Decimal(row["amount"]) for row in cash_entries if row["direction"]=="in"),Decimal("0"))
            expense=sum((Decimal(row["amount"]) for row in cash_entries if row["direction"]=="out"),Decimal("0"))
            currency=self.store.setting("business_currency","KES - Kenyan Shilling").split(" - ",1)[0]
            finrow=QHBoxLayout(); finrow.setSpacing(12)
            for label,value,accent in [
                ("Money received",f"{currency} {income:,.2f}",self.palette["green"]),
                ("Money paid",f"{currency} {expense:,.2f}",self.palette["red"]),
                ("Recorded cash balance",f"{currency} {income-expense:,.2f}",self.palette["blue"]),
            ]:
                tile=self.panel(); tile.setMinimumHeight(82); tl=QVBoxLayout(tile); tl.setContentsMargins(18,14,18,14); tl.setSpacing(7)
                cap=QLabel(label); cap.setStyleSheet(f"color:{self.palette['muted']};font-size:9pt;")
                val=QLabel(value); val.setStyleSheet(f"color:{accent};font-size:16pt;font-weight:800;")
                tl.addWidget(cap); tl.addWidget(val); finrow.addWidget(tile,1)
            v.addLayout(finrow)
        lower=QGridLayout(); lower.setSpacing(16)
        chart_widget=None; quick_widget=None; activity_widget=None
        today=datetime.now().date()
        chart_values=[]
        if "Sales" in visible_modules and "sales_trend" in widgets:
            by_day={}
            for sale in operations.sales(100000):
                if sale["status"] != "Completed":
                    continue
                try:
                    sale_day=datetime.fromisoformat(sale["created_at"]).date()
                except (TypeError,ValueError):
                    continue
                age=(today-sale_day).days
                if 0 <= age < 7:
                    by_day[sale_day]=by_day.get(sale_day,Decimal("0"))+Decimal(str(sale["total"]))
            for age in range(6,-1,-1):
                day=today.fromordinal(today.toordinal()-age)
                chart_values.append((day.strftime("%a"),by_day.get(day,Decimal("0"))))
            currency=self.store.setting("business_currency","KES - Kenyan Shilling").split(" - ",1)[0]
            chart_widget=ChartPanel("Sales booked · last 7 days",chart_values,currency,self.palette)
        if "quick_actions" in widgets:
            quick_widget=self.panel(); qv=QVBoxLayout(quick_widget); qv.setContentsMargins(16,14,16,16)
            qv.addWidget(QLabel("<span style='font-size:12pt;font-weight:800'>Quick actions</span>"))
            quickgrid=QGridLayout(); quickgrid.setSpacing(8)
            action_specs=[
                ("＋  New sale","Sales","create"),
                ("＋  Add product","Inventory","manage_inventory"),
                ("＋  Receive stock","Purchases","manage_inventory"),
                ("＋  Add customer","CRM","create"),
                ("＋  Cash movement","Finance","manage_finance"),
                ("＋  New project","Projects","create"),
            ]
            actions=[(label,dest) for label,dest,cap in action_specs if dest in visible_modules and cap in self.store.role_permissions(self.role)]
            for i,(label,dest) in enumerate(actions):
                b=QPushButton(label); b.setMinimumHeight(42); b.clicked.connect(lambda checked=False,n=dest:self.show_page(n))
                quickgrid.addWidget(b,i//2,i%2)
            if not actions:
                empty=QLabel("No quick actions are assigned to your role."); empty.setWordWrap(True)
                empty.setStyleSheet(f"color:{self.palette['muted']}"); quickgrid.addWidget(empty,0,0,1,2)
            qv.addLayout(quickgrid); qv.addStretch()
            if "sync" in self.store.role_permissions(self.role):
                syncbtn=QPushButton("↻   Synchronize pending changes"); syncbtn.setObjectName("primary"); syncbtn.clicked.connect(self.sync_now); qv.addWidget(syncbtn)
        if "recent_activity" in widgets:
            activity_widget=self.panel(); av=QVBoxLayout(activity_widget); av.setContentsMargins(16,13,16,13)
            av.addWidget(QLabel("<span style='font-size:12pt;font-weight:800'>Recent activity</span>"))
            if self.role!="Admin" and self.store.role_template(self.role)!="Manager":
                rows=self.store.conn.execute("SELECT * FROM activity WHERE username=? ORDER BY id DESC LIMIT 5",(self.username,)).fetchall()
            else:
                rows=self.store.conn.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 5").fetchall()
            if not rows:
                av.addWidget(QLabel(f"<span style='color:{self.palette['muted']}'>Your activity appears here as you work.</span>"))
            for r in rows:
                lab=QLabel(f"<span style='color:{self.palette['cyan']}'>●</span>  <b>{r['action']}</b><br><span style='color:{self.palette['muted']}'>{r['details'][:52]} · {r['created_at'][11:16]}</span>")
                lab.setWordWrap(True); av.addWidget(lab)
        selected=[widget for widget in (chart_widget,quick_widget,activity_widget) if widget is not None]
        if len(selected)==3:
            lower.addWidget(chart_widget,0,0,2,2)
            lower.addWidget(quick_widget,0,2)
            lower.addWidget(activity_widget,1,2)
        elif chart_widget is not None and quick_widget is not None:
            lower.addWidget(chart_widget,0,0,2,2); lower.addWidget(quick_widget,0,2,2,1)
        elif chart_widget is not None and activity_widget is not None:
            lower.addWidget(chart_widget,0,0,2,2); lower.addWidget(activity_widget,0,2,2,1)
        elif quick_widget is not None and activity_widget is not None:
            lower.addWidget(quick_widget,0,0,1,2); lower.addWidget(activity_widget,0,2,1,1)
        elif len(selected)==1:
            lower.addWidget(selected[0],0,0,1,3)
        if selected:
            lower.setColumnStretch(0,2); lower.setColumnStretch(1,2); lower.setColumnStretch(2,1)
            lower.setRowStretch(0,1); lower.setRowStretch(1,1)
            v.addLayout(lower,1)
        elif not cards and not ("finance_summary" in widgets and "Finance" in visible_modules):
            empty=QLabel("No dashboard widgets are selected for this role. Ask an administrator to update the dashboard settings.")
            empty.setWordWrap(True); empty.setStyleSheet(f"color:{self.palette['muted']};padding:18px")
            v.addWidget(empty)
        return w
    def _field_defs(self, fields):
        defs=[]
        for item in fields:
            if isinstance(item, dict):
                d={"name":str(item.get("name","Field"))[:60],"type":item.get("type","text"),"required":bool(item.get("required",False)),"options":item.get("options",[])}
            else:
                d={"name":str(item),"type":"text","required":False,"options":[]}
            if d["type"] not in ("text","number","date","choice","notes"): d["type"]="text"
            defs.append(d)
        return defs
    def _table(self, headers):
        table=QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        return table

    def _fill_table(self, table, rows, values, record_id_key="id", hide_id=False):
        table.setRowCount(len(rows))
        for row_index,row in enumerate(rows):
            row_values=values(row)
            for col,value in enumerate(row_values):
                item=QTableWidgetItem(str(value))
                if col == 0 and record_id_key in row.keys():
                    item.setData(Qt.UserRole,int(row[record_id_key]))
                table.setItem(row_index,col,item)
        if hide_id:
            table.setColumnHidden(0,True)

    def _selected_table_id(self, table):
        row=table.currentRow()
        if row < 0:
            return None
        item=table.item(row,0)
        return item.data(Qt.UserRole) if item else None

    def _show_legacy_records(self, module):
        key=f"Legacy records · {module}"
        old=self.pages.pop(key,None)
        if old:
            self.stack.removeWidget(old)
            old.deleteLater()
        page=self.records_page(module)
        self.pages[key]=page
        self.stack.addWidget(page)
        self.stack.setCurrentWidget(page)

    def _refresh_operation_page(self, module):
        self.reload_page(module)

    def _product_rows(self):
        rows=[]
        for row in self.store.operations.products(active_only=False):
            item=dict(row)
            item["stock_on_hand"]=self.store.operations.stock_on_hand(item["id"])
            rows.append(item)
        return rows

    def inventory_page(self):
        w,v=self.page_container("Inventory","Product catalogue and stock ledger. Sales and purchase receipts update stock through audited movements.")
        actions=QHBoxLayout()
        if "manage_inventory" in self.store.role_permissions(self.role):
            add=QPushButton("＋ Add product"); add.setObjectName("primary"); add.clicked.connect(self.create_product_dialog); actions.addWidget(add)
            edit=QPushButton("Edit selected"); edit.clicked.connect(self.edit_selected_product); actions.addWidget(edit)
            adjust=QPushButton("Adjust selected stock"); adjust.clicked.connect(self.adjust_selected_stock); actions.addWidget(adjust)
            receive=QPushButton("Receive stock"); receive.clicked.connect(lambda:self.open_stock_transaction("purchase")); actions.addWidget(receive)
        legacy=QPushButton("View legacy inventory records"); legacy.clicked.connect(lambda:self._show_legacy_records("Inventory")); actions.addWidget(legacy)
        actions.addStretch(); v.addLayout(actions)
        table=self._table(["ID","SKU","BARCODE","PRODUCT","CATEGORY","ON HAND","UNIT","SELL PRICE","COST","REORDER LEVEL","ACTIVE"])
        rows=self._product_rows()
        self._fill_table(table,rows,lambda r:[r["id"],r["sku"],r["barcode"],r["name"],r["category"],f"{r['stock_on_hand']:,.3f}".rstrip("0").rstrip("."),r["unit"],r["selling_price"],r["cost_price"],r["reorder_level"],"Yes" if r["active"] else "No"],hide_id=True)
        v.addWidget(QLabel("Products and current stock"))
        v.addWidget(table,1)
        moves=self.store.operations.stock_movements(100)
        movement_table=self._table(["DATE","SKU","PRODUCT","CHANGE","TYPE","REFERENCE","NOTE"])
        self._fill_table(movement_table,moves,lambda r:[r["created_at"],r["sku"],r["name"],r["quantity_delta"],r["movement_type"],f"{r['reference_type']} {r['reference_id'] or ''}".strip(),r["note"]])
        v.addWidget(QLabel("Recent stock movements"))
        v.addWidget(movement_table,1)
        return w

    def _product_dialog(self, existing=None):
        editing=existing is not None
        dlg=QDialog(self); dlg.setWindowTitle("Edit product" if editing else "Add product")
        dlg.setStyleSheet(themed_style(self.palette)); dlg.setMinimumWidth(480)
        layout=QVBoxLayout(dlg); form=QFormLayout()
        sku=QLineEdit(str(existing["sku"]) if editing else "")
        barcode=QLineEdit(str(existing["barcode"]) if editing else "")
        category=QLineEdit(str(existing["category"]) if editing else "")
        name=QLineEdit(str(existing["name"]) if editing else "")
        unit=QLineEdit(str(existing["unit"]) if editing else "each")
        currency=self.store.setting("business_currency","KES - Kenyan Shilling").split(" - ",1)[0]
        selling=QDoubleSpinBox(); selling.setDecimals(2); selling.setMaximum(999999999999.99); selling.setPrefix(currency+" "); selling.setValue(float(existing["selling_price"]) if editing else 0)
        cost=QDoubleSpinBox(); cost.setDecimals(2); cost.setMaximum(999999999999.99); cost.setPrefix(currency+" "); cost.setValue(float(existing["cost_price"]) if editing else 0)
        reorder=QDoubleSpinBox(); reorder.setDecimals(3); reorder.setMaximum(999999999.999); reorder.setValue(float(existing["reorder_level"]) if editing else 0)
        active=QCheckBox("Available for new sales and purchases"); active.setChecked(bool(existing["active"]) if editing else True)
        sku.setPlaceholderText("Unique internal stock code")
        barcode.setPlaceholderText("Optional scan code; leave blank if unused")
        category.setPlaceholderText("e.g. Accessories, Produce, Services")
        form.addRow("SKU",sku); form.addRow("Barcode",barcode); form.addRow("Product name",name); form.addRow("Category",category); form.addRow("Unit",unit)
        form.addRow("Selling price",selling); form.addRow("Unit cost",cost); form.addRow("Reorder level",reorder); form.addRow("",active)
        opening=QDoubleSpinBox(); opening.setDecimals(3); opening.setMaximum(999999999.999)
        if editing:
            opening.setValue(float(existing["stock_on_hand"])); opening.setReadOnly(True)
            form.addRow("On hand (read only)",opening)
        else:
            form.addRow("Opening stock",opening)
        layout.addLayout(form)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); layout.addWidget(buttons); buttons.rejected.connect(dlg.reject)
        def save():
            try:
                if editing:
                    self.store.operations.update_product(existing["id"],sku.text(),name.text(),selling.value(),cost.value(),reorder.value(),unit.text(),active.isChecked(),category.text(),barcode.text())
                    self.store.log(self.username,"Updated product",f"{sku.text().strip()} · {name.text().strip()}")
                else:
                    product_id=self.store.operations.create_product(sku.text(),name.text(),selling.value(),cost.value(),opening.value(),unit.text(),reorder.value(),category.text(),barcode.text())
                    self.store.log(self.username,"Created product",f"{sku.text().strip()} · {name.text().strip()} · id {product_id}")
            except (OperationError,ValueError) as exc:
                QMessageBox.warning(dlg,"Product not saved",str(exc)); return
            dlg.accept()
        buttons.accepted.connect(save)
        if dlg.exec():
            self._refresh_operation_page("Inventory")

    def create_product_dialog(self):
        if not self._authorize("manage_inventory"): return
        self._product_dialog()

    def edit_selected_product(self):
        if not self._authorize("manage_inventory"): return
        table=self.pages["Inventory"].findChild(QTableWidget)
        product_id=self._selected_table_id(table) if table else None
        if product_id is None:
            QMessageBox.information(self,"Select a product","Select a product row first."); return
        product=next((p for p in self._product_rows() if p["id"]==product_id),None)
        if product: self._product_dialog(product)

    def adjust_selected_stock(self):
        if not self._authorize("manage_inventory"): return
        table=self.pages["Inventory"].findChild(QTableWidget)
        product_id=self._selected_table_id(table) if table else None
        if product_id is None:
            QMessageBox.information(self,"Select a product","Select a product row first."); return
        product=next((p for p in self._product_rows() if p["id"]==product_id),None)
        if not product: return
        quantity,ok=QInputDialog.getDouble(self,"Adjust stock",f"Signed quantity change for {product['sku']}:",0,-999999999,999999999,3)
        if not ok: return
        reason,ok=QInputDialog.getText(self,"Adjustment reason","Reason for the stock adjustment:")
        if not ok: return
        try:
            self.store.operations.adjust_stock(product_id,quantity,reason)
            self.store.log(self.username,"Adjusted stock",f"{product['sku']}: {quantity:+.3f} · {reason.strip()}")
            self._refresh_operation_page("Inventory")
        except OperationError as exc:
            QMessageBox.warning(self,"Stock adjustment rejected",str(exc))

    def sales_page(self):
        w,v=self.page_container("Sales","Complete stock-backed sales, record payments, and keep a searchable transaction history.")
        row=QHBoxLayout()
        if "create" in self.store.role_permissions(self.role):
            create=QPushButton("＋ New sale"); create.setObjectName("primary"); create.clicked.connect(lambda:self.open_stock_transaction("sale")); row.addWidget(create)
        if "delete" in self.store.role_permissions(self.role):
            cancel=QPushButton("Cancel selected sale"); cancel.clicked.connect(self.cancel_selected_sale); row.addWidget(cancel)
        if "process_returns" in self.store.role_permissions(self.role):
            return_button=QPushButton("Return selected items"); return_button.clicked.connect(self.return_selected_sale); row.addWidget(return_button)
        if "create" in self.store.role_permissions(self.role):
            quote_button=QPushButton("＋ Create quotation"); quote_button.clicked.connect(self.create_quote); row.addWidget(quote_button)
            convert_button=QPushButton("Convert accepted quote"); convert_button.clicked.connect(self.convert_quote_selected); row.addWidget(convert_button)
        if "print" in self.store.role_permissions(self.role):
            print_invoice=QPushButton("Print selected invoice / receipt"); print_invoice.clicked.connect(lambda:self.print_selected_business_document("invoice")); row.addWidget(print_invoice)
            print_quote=QPushButton("Print selected quotation"); print_quote.clicked.connect(lambda:self.print_selected_business_document("quote")); row.addWidget(print_quote)
        if "edit" in self.store.role_permissions(self.role):
            send_quote=QPushButton("Send selected quote"); send_quote.clicked.connect(lambda:self.change_selected_quote_status("Sent")); row.addWidget(send_quote)
            accept_quote=QPushButton("Accept selected quote"); accept_quote.clicked.connect(lambda:self.change_selected_quote_status("Accepted")); row.addWidget(accept_quote)
        if "delete" in self.store.role_permissions(self.role):
            cancel_quote=QPushButton("Cancel selected quote"); cancel_quote.clicked.connect(self.cancel_selected_quote); row.addWidget(cancel_quote)
        legacy=QPushButton("View legacy sales records"); legacy.clicked.connect(lambda:self._show_legacy_records("Sales")); row.addWidget(legacy); row.addStretch(); v.addLayout(row)
        table=self._table(["ID","SALE NUMBER","CUSTOMER","STATUS","PAYMENT","TOTAL","PAID","CREATED"])
        rows=self.store.operations.sales()
        currency=self.store.setting("business_currency","KES - Kenyan Shilling").split(" - ",1)[0]
        self._fill_table(table,rows,lambda r:[r["id"],r["sale_number"],r["customer_name"],r["status"],r["payment_status"],f"{currency} {r['total']}",f"{currency} {r['paid_total']}",r["created_at"]],hide_id=True)
        self.sales_table=table
        v.addWidget(table,1)
        v.addWidget(QLabel("Returns and refunds"))
        return_table=self._table(["RETURN","SALE","CUSTOMER","REFUND","REASON","CREATED","PROCESSED BY"])
        return_rows=self.store.operations.sales_returns()
        self._fill_table(return_table,return_rows,lambda r:[r["return_number"],r["sale_number"],r["customer_name"],f"{currency} {r['refund_amount']}",r["reason"],r["created_at"],r["created_by"]])
        v.addWidget(return_table,1)
        v.addWidget(QLabel("Quotations"))
        quote_table=self._table(["ID","QUOTE NUMBER","CUSTOMER","STATUS","TOTAL","EXPIRES","CREATED"])
        quote_rows=self.store.operations.quotes()
        self._fill_table(quote_table,quote_rows,lambda r:[r["id"],r["quote_number"],r["customer_name"],r["status"],f"{currency} {r['total']}",r["expires_on"] or "—",r["created_at"]],hide_id=True)
        self.quote_table=quote_table
        v.addWidget(quote_table,1)
        return w

    def purchases_page(self):
        w,v=self.page_container("Purchases","Track supplier orders, receive delivered quantities, and preserve the link from order to stock and payments.")
        row=QHBoxLayout()
        permissions=self.store.role_permissions(self.role)
        if "manage_inventory" in permissions:
            order_button=QPushButton("＋ Create purchase order"); order_button.setObjectName("primary"); order_button.clicked.connect(self.create_purchase_order); row.addWidget(order_button)
            receive_order=QPushButton("Receive selected order"); receive_order.clicked.connect(self.receive_selected_purchase_order); row.addWidget(receive_order)
            receive=QPushButton("＋ Receive unplanned purchase"); receive.clicked.connect(lambda:self.open_stock_transaction("purchase")); row.addWidget(receive)
            supplier=QPushButton("＋ Add supplier"); supplier.clicked.connect(lambda:self.create_party_dialog("supplier")); row.addWidget(supplier)
        if "delete" in permissions:
            cancel=QPushButton("Cancel selected purchase"); cancel.clicked.connect(self.cancel_selected_purchase); row.addWidget(cancel)
            cancel_order=QPushButton("Cancel selected order"); cancel_order.clicked.connect(self.cancel_selected_purchase_order); row.addWidget(cancel_order)
        if "print" in permissions:
            print_order=QPushButton("Print selected purchase order"); print_order.clicked.connect(lambda:self.print_selected_business_document("purchase_order")); row.addWidget(print_order)
            print_receipt=QPushButton("Print selected goods receipt"); print_receipt.clicked.connect(lambda:self.print_selected_business_document("goods_received")); row.addWidget(print_receipt)
        legacy=QPushButton("View legacy purchase records"); legacy.clicked.connect(lambda:self._show_legacy_records("Purchases")); row.addWidget(legacy); row.addStretch(); v.addLayout(row)
        currency=self.store.setting("business_currency","KES - Kenyan Shilling").split(" - ",1)[0]
        v.addWidget(QLabel("Purchase receipts"))
        receipt_table=self._table(["ID","PURCHASE NUMBER","SUPPLIER","STATUS","PAYMENT","TOTAL","PAID","CREATED"])
        rows=self.store.operations.purchases()
        self._fill_table(receipt_table,rows,lambda r:[r["id"],r["purchase_number"],r["supplier_name"],r["status"],r["payment_status"],f"{currency} {r['total']}",f"{currency} {r['paid_total']}",r["created_at"]],hide_id=True)
        self.purchase_receipts_table=receipt_table
        v.addWidget(receipt_table,1)
        v.addWidget(QLabel("Purchase orders"))
        order_table=self._table(["ID","ORDER NUMBER","SUPPLIER","STATUS","CREATED","UPDATED"])
        self._fill_table(order_table,self.store.operations.purchase_orders(),lambda r:[r["id"],r["order_number"],r["supplier_name"],r["status"],r["created_at"],r["updated_at"]],hide_id=True)
        self.purchase_orders_table=order_table
        v.addWidget(order_table,1)
        return w

    def repair_page(self):
        w,v=self.page_container("Repair Tickets","Track customer devices from intake through diagnosis, estimate approval, parts, payment, and collection.")
        permissions=self.store.role_permissions(self.role)
        bar=QHBoxLayout()
        if "create" in permissions:
            new=QPushButton("＋ Receive device"); new.setObjectName("primary"); new.clicked.connect(self.create_repair_ticket); bar.addWidget(new)
        if "edit" in permissions:
            diagnose=QPushButton("Diagnosis & estimate"); diagnose.clicked.connect(self.diagnose_repair_ticket); bar.addWidget(diagnose)
            approve=QPushButton("Approve estimate"); approve.clicked.connect(lambda:self.approve_repair_ticket(True)); bar.addWidget(approve)
            decline=QPushButton("Decline estimate"); decline.clicked.connect(lambda:self.approve_repair_ticket(False)); bar.addWidget(decline)
            ready=QPushButton("Mark ready"); ready.clicked.connect(self.mark_repair_ready); bar.addWidget(ready)
            collect=QPushButton("Collect device"); collect.clicked.connect(self.collect_repair_job); bar.addWidget(collect)
        if "manage_inventory" in permissions:
            part=QPushButton("Issue part from stock"); part.clicked.connect(self.issue_repair_part); bar.addWidget(part)
        if "manage_finance" in permissions:
            payment=QPushButton("Record payment"); payment.clicked.connect(self.record_repair_payment); bar.addWidget(payment)
        if "delete" in permissions:
            cancel=QPushButton("Cancel ticket"); cancel.clicked.connect(self.cancel_repair_ticket); bar.addWidget(cancel)
        if "print" in permissions:
            print_job=QPushButton("Print job sheet"); print_job.clicked.connect(lambda:self.print_selected_business_document("repair_job")); bar.addWidget(print_job)
        refresh=QPushButton("↻ Refresh"); refresh.clicked.connect(lambda:self.reload_page("Repair Tickets")); bar.addWidget(refresh)
        bar.addStretch(); v.addLayout(bar)
        note=QLabel("Parts are taken from on-hand inventory when issued. A technician’s pre-tax estimate must be approved before work and parts can proceed. Collection requires the final balance to be paid.")
        note.setWordWrap(True); note.setStyleSheet(f"color:{self.palette['muted']};padding:10px;background:{self.palette['panel2']};border-radius:8px;"); v.addWidget(note)
        currency=self.store.setting("business_currency","KES - Kenyan Shilling").split(" - ",1)[0]
        table=self._table(["ID","TICKET","CUSTOMER","DEVICE","SERIAL","STATUS","APPROVAL","ESTIMATE","TOTAL","PAID","CREATED"])
        rows=self.store.operations.repair_jobs()
        self._fill_table(table,rows,lambda r:[r["id"],r["ticket_number"],r["customer_name"],r["device"],r["serial_number"],r["status"],r["approval_status"],f"{currency} {r['estimate_amount']}",f"{currency} {r['total']}",f"{currency} {r['paid_total']}",r["created_at"]],hide_id=True)
        table.currentCellChanged.connect(lambda *_:self.refresh_repair_details())
        self.repair_jobs_table=table
        v.addWidget(table,1)
        details=QGridLayout(); details.setSpacing(14)
        parts_panel=self.panel(); parts_layout=QVBoxLayout(parts_panel); parts_layout.setContentsMargins(12,10,12,10)
        parts_layout.addWidget(QLabel("Parts issued"))
        self.repair_parts_table=self._table(["SKU","PART","QTY","UNIT PRICE","LINE TOTAL","ISSUED BY","DATE"])
        parts_layout.addWidget(self.repair_parts_table)
        events_panel=self.panel(); events_layout=QVBoxLayout(events_panel); events_layout.setContentsMargins(12,10,12,10)
        events_layout.addWidget(QLabel("Service history"))
        self.repair_events_table=self._table(["DATE","EVENT","DETAILS","ACTOR"])
        events_layout.addWidget(self.repair_events_table)
        details.addWidget(parts_panel,0,0); details.addWidget(events_panel,0,1)
        v.addLayout(details)
        if rows:
            table.selectRow(0); self.refresh_repair_details()
        return w

    def _selected_repair_job(self):
        table=getattr(self,"repair_jobs_table",None)
        job_id=self._selected_table_id(table) if table else None
        if job_id is None:
            QMessageBox.information(self,"Select a repair ticket","Select a repair ticket row first.")
            return None
        return self.store.conn.execute("SELECT * FROM repair_jobs WHERE id=?",(int(job_id),)).fetchone()

    def refresh_repair_details(self):
        table=getattr(self,"repair_jobs_table",None)
        job_id=self._selected_table_id(table) if table else None
        if job_id is None: return
        currency=self.store.setting("business_currency","KES - Kenyan Shilling").split(" - ",1)[0]
        parts=self.store.operations.repair_job_parts(job_id)
        self._fill_table(self.repair_parts_table,parts,lambda r:[r["sku"],r["description"],r["quantity"],f"{currency} {r['unit_price']}",f"{currency} {r['line_total']}",r["created_by"],r["created_at"]])
        events=self.store.operations.repair_job_events(job_id)
        self._fill_table(self.repair_events_table,events,lambda r:[r["created_at"],r["event"],r["details"],r["actor"]])

    def create_repair_ticket(self):
        if not self._authorize("create"): return
        dialog=RepairJobDialog(self.store.operations.customers(),self)
        if not dialog.exec(): return
        try:
            data=dialog.data()
            job_id=self.store.operations.create_repair_job(data["customer_name"],data["device"],data["reported_fault"],data["serial_number"],data["customer_id"],data["warranty_notes"])
            job=self.store.conn.execute("SELECT ticket_number FROM repair_jobs WHERE id=?",(job_id,)).fetchone()
            self.store.log(self.username,"Received repair device",job["ticket_number"])
            self.reload_page("Repair Tickets")
        except (OperationError,ValueError,TypeError) as exc:
            QMessageBox.warning(self,"Ticket not created",str(exc))

    def diagnose_repair_ticket(self):
        if not self._authorize("edit"): return
        job=self._selected_repair_job()
        if not job: return
        dialog=RepairDiagnosisDialog(job,self.store.setting("business_currency","KES").split(" - ",1)[0],self)
        if not dialog.exec(): return
        try:
            data=dialog.data()
            self.store.operations.update_repair_diagnosis(job["id"],data["diagnosis"],data["technician"],data["labor_amount"],data["estimate_amount"])
            self.store.log(self.username,"Requested repair estimate approval",job["ticket_number"])
            self.reload_page("Repair Tickets")
        except (OperationError,ValueError) as exc:
            QMessageBox.warning(self,"Diagnosis not saved",str(exc))

    def approve_repair_ticket(self,approved):
        if not self._authorize("edit"): return
        job=self._selected_repair_job()
        if not job: return
        decision="approve" if approved else "decline"
        answer=QMessageBox.question(self,f"{decision.title()} repair estimate",f"{decision.title()} the pre-tax estimate for {job['ticket_number']}?",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes: return
        try:
            self.store.operations.approve_repair_estimate(job["id"],approved)
            self.store.log(self.username,"Customer estimate decision",f"{job['ticket_number']}: {decision}")
            self.reload_page("Repair Tickets")
        except OperationError as exc:
            QMessageBox.warning(self,"Estimate unchanged",str(exc))

    def issue_repair_part(self):
        if not self._authorize("manage_inventory"): return
        job=self._selected_repair_job()
        if not job: return
        products=[]
        for row in self.store.operations.products():
            item=dict(row); item["stock_on_hand"]=self.store.operations.stock_on_hand(item["id"]); products.append(item)
        currency=self.store.setting("business_currency","KES - Kenyan Shilling").split(" - ",1)[0]
        dialog=RepairPartDialog(products,currency,self)
        if not dialog.exec(): return
        try:
            data=dialog.data()
            self.store.operations.add_repair_part(job["id"],data["product_id"],data["quantity"])
            self.store.log(self.username,"Issued repair part",f"{job['ticket_number']} · product {data['product_id']}")
            self.reload_page("Repair Tickets")
        except (OperationError,ValueError) as exc:
            QMessageBox.warning(self,"Part not issued",str(exc))

    def mark_repair_ready(self):
        if not self._authorize("edit"): return
        job=self._selected_repair_job()
        if not job: return
        try:
            self.store.operations.mark_repair_ready(job["id"])
            self.store.log(self.username,"Repair ready for collection",job["ticket_number"])
            self.reload_page("Repair Tickets")
        except OperationError as exc:
            QMessageBox.warning(self,"Repair not ready",str(exc))

    def record_repair_payment(self):
        if not self._authorize("manage_finance"): return
        job=self._selected_repair_job()
        if not job: return
        balance=Decimal(job["total"])-Decimal(job["paid_total"])
        if balance<=0:
            QMessageBox.information(self,"No balance due","This repair has no outstanding amount.")
            return
        currency=self.store.setting("business_currency","KES - Kenyan Shilling").split(" - ",1)[0]
        dialog=RepairPaymentDialog(balance,currency,self)
        if not dialog.exec(): return
        try:
            data=dialog.data()
            self.store.operations.record_repair_payment(job["id"],data["amount"],data["reference"])
            self.store.log(self.username,"Recorded repair payment",f"{job['ticket_number']} · {data['amount']:.2f}")
            self.reload_page("Repair Tickets")
        except (OperationError,ValueError) as exc:
            QMessageBox.warning(self,"Payment not saved",str(exc))

    def collect_repair_job(self):
        if not self._authorize("edit"): return
        job=self._selected_repair_job()
        if not job: return
        try:
            self.store.operations.collect_repair_job(job["id"])
            self.store.log(self.username,"Device collected",job["ticket_number"])
            self.reload_page("Repair Tickets")
        except OperationError as exc:
            QMessageBox.warning(self,"Device not collected",str(exc))

    def cancel_repair_ticket(self):
        if not self._authorize("delete"): return
        job=self._selected_repair_job()
        if not job: return
        answer=QMessageBox.question(self,"Cancel repair ticket",f"Cancel {job['ticket_number']}? Tickets with work or parts issued cannot be cancelled.",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes: return
        try:
            self.store.operations.cancel_repair_job(job["id"],"Cancelled by administrator")
            self.store.log(self.username,"Cancelled repair ticket",job["ticket_number"])
            self.reload_page("Repair Tickets")
        except OperationError as exc:
            QMessageBox.warning(self,"Ticket not cancelled",str(exc))

    def create_purchase_order(self):
        if not self._authorize("manage_inventory"): return
        products=[]
        for row in self.store.operations.products():
            item=dict(row); item["stock_on_hand"]=self.store.operations.stock_on_hand(item["id"]); products.append(item)
        suppliers=self.store.operations.suppliers()
        currency=self.store.setting("business_currency","KES - Kenyan Shilling").split(" - ",1)[0]
        dlg=StockTransactionDialog(products,suppliers,"purchase",currency,self,document_type="order")
        if not dlg.exec(): return
        try:
            data=dlg.transaction_data()
            order_id=self.store.operations.create_purchase_order(data["party_name"],data["lines"],data["party_id"])
            order=self.store.conn.execute("SELECT order_number FROM purchase_orders WHERE id=?",(order_id,)).fetchone()
            self.store.log(self.username,"Created purchase order",order["order_number"])
            self.reload_page("Purchases")
        except (OperationError,ValueError) as exc:
            QMessageBox.warning(self,"Purchase order not saved",str(exc))

    def receive_selected_purchase_order(self):
        if not self._authorize("manage_inventory"): return
        table=getattr(self,"purchase_orders_table",None)
        order_id=self._selected_table_id(table) if table else None
        if order_id is None:
            QMessageBox.information(self,"Select an order","Select a purchase order first."); return
        order=self.store.conn.execute("SELECT * FROM purchase_orders WHERE id=?",(order_id,)).fetchone()
        if not order or order["status"] not in {"Ordered","Partially received"}:
            QMessageBox.information(self,"Order unavailable","Only open orders can receive goods."); return
        lines=self.store.operations.purchase_order_lines(order_id)
        currency=self.store.setting("business_currency","KES - Kenyan Shilling").split(" - ",1)[0]
        dlg=PurchaseOrderReceiptDialog(order,lines,currency,self)
        if not dlg.exec(): return
        try:
            data=dlg.receipt_data()
            purchase_id=self.store.operations.receive_purchase_order(order_id,data["lines"],data["amount_paid"])
            purchase=self.store.conn.execute("SELECT purchase_number FROM purchases WHERE id=?",(purchase_id,)).fetchone()
            self.store.log(self.username,"Received purchase order",f"{order['order_number']} · {purchase['purchase_number']}")
            self.reload_page("Purchases")
            self.invalidate_page("Inventory")
        except (OperationError,ValueError) as exc:
            QMessageBox.warning(self,"Goods not received",str(exc))

    def cancel_selected_purchase_order(self):
        if not self._authorize("delete"): return
        table=getattr(self,"purchase_orders_table",None)
        order_id=self._selected_table_id(table) if table else None
        if order_id is None:
            QMessageBox.information(self,"Select an order","Select a purchase order first."); return
        order=self.store.conn.execute("SELECT order_number,status FROM purchase_orders WHERE id=?",(order_id,)).fetchone()
        if not order: return
        reason,ok=QInputDialog.getText(self,"Cancel purchase order",f"Reason for cancelling {order['order_number']}:")
        if not ok: return
        answer=QMessageBox.question(self,"Confirm cancellation",f"Cancel {order['order_number']} and its outstanding quantity? Already received goods and receipts remain in history.",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes: return
        try:
            self.store.operations.cancel_purchase_order(order_id,reason)
            self.store.log(self.username,"Cancelled purchase order",f"{order['order_number']} · {reason.strip()}")
            self.reload_page("Purchases")
        except OperationError as exc:
            QMessageBox.warning(self,"Order not cancelled",str(exc))

    def open_stock_transaction(self, mode):
        capability="create"
        if mode=="purchase" and not self._authorize("manage_inventory"): return
        if mode=="sale" and not self._authorize(capability): return
        products=[]
        for row in self.store.operations.products():
            product=dict(row); product["stock_on_hand"]=self.store.operations.stock_on_hand(product["id"]); products.append(product)
        parties=self.store.operations.customers() if mode=="sale" else self.store.operations.suppliers()
        currency=self.store.setting("business_currency","KES - Kenyan Shilling").split(" - ",1)[0]
        dlg=StockTransactionDialog(products,parties,mode,currency,self,tax_rate=self.store.setting("business_tax_rate","0.00") if mode=="sale" else 0,tax_label=self.store.setting("business_tax_label","Tax"))
        if not dlg.exec(): return
        try:
            data=dlg.transaction_data()
            if mode=="sale":
                record_id=self.store.operations.record_sale(data["party_name"],data["lines"],data["amount_paid"],data["party_id"])
                action,number="Completed sale",f"SALE-{record_id:08d}"
                page="Sales"
            else:
                if not data["party_name"]:
                    raise OperationError("Enter a supplier name before receiving stock.")
                record_id=self.store.operations.receive_purchase(data["party_name"],data["lines"],data["amount_paid"],data["party_id"])
                action,number="Received purchase",f"PUR-{record_id:08d}"
                page="Purchases"
            self.store.log(self.username,action,number)
            self._refresh_operation_page(page)
        except (OperationError,ValueError) as exc:
            QMessageBox.warning(self,"Transaction not saved",str(exc))

    def create_quote(self):
        if not self._authorize("create"): return
        products=[]
        for row in self.store.operations.products():
            item=dict(row); item["stock_on_hand"]=self.store.operations.stock_on_hand(item["id"]); products.append(item)
        currency=self.store.setting("business_currency","KES - Kenyan Shilling").split(" - ",1)[0]
        dlg=StockTransactionDialog(products,self.store.operations.customers(),"sale",currency,self,document_type="quote",tax_rate=self.store.setting("business_tax_rate","0.00"),tax_label=self.store.setting("business_tax_label","Tax"))
        if not dlg.exec(): return
        try:
            data=dlg.transaction_data()
            quote_id=self.store.operations.create_quote(data["party_name"],data["lines"],data["party_id"],data["expires_on"])
            quote=self.store.conn.execute("SELECT quote_number FROM quotes WHERE id=?",(quote_id,)).fetchone()
            self.store.log(self.username,"Created quotation",quote["quote_number"])
            self.reload_page("Sales")
        except (OperationError,ValueError) as exc:
            QMessageBox.warning(self,"Quotation not saved",str(exc))

    def _selected_quote(self):
        table=getattr(self,"quote_table",None)
        quote_id=self._selected_table_id(table) if table else None
        if quote_id is None:
            QMessageBox.information(self,"Select a quotation","Select a quotation row first.")
            return None
        return self.store.conn.execute("SELECT * FROM quotes WHERE id=?",(quote_id,)).fetchone()

    def change_selected_quote_status(self,status):
        if not self._authorize("edit"): return
        quote=self._selected_quote()
        if not quote: return
        try:
            self.store.operations.set_quote_status(quote["id"],status)
            self.store.log(self.username,"Updated quotation status",f"{quote['quote_number']}: {status}")
            self.reload_page("Sales")
        except OperationError as exc:
            QMessageBox.warning(self,"Quotation status unchanged",str(exc))

    def convert_quote_selected(self):
        if not self._authorize("create"): return
        quote=self._selected_quote()
        if not quote: return
        if quote["status"]!="Accepted":
            QMessageBox.information(self,"Quote not accepted","Mark the quotation as accepted before converting it to a sale.")
            return
        currency=self.store.setting("business_currency","KES - Kenyan Shilling").split(" - ",1)[0]
        total=float(quote["total"])
        paid,ok=QInputDialog.getDouble(self,"Convert quotation",f"Amount received for {quote['quote_number']}:",total,0,total,2)
        if not ok: return
        try:
            sale_id=self.store.operations.convert_quote(quote["id"],paid)
            sale=self.store.conn.execute("SELECT sale_number FROM sales WHERE id=?",(sale_id,)).fetchone()
            self.store.log(self.username,"Converted quotation to sale",f"{quote['quote_number']} → {sale['sale_number']}")
            QMessageBox.information(self,"Quotation converted",f"Sale {sale['sale_number']} was created. Inventory and payment records were updated.")
            self.reload_page("Sales")
        except OperationError as exc:
            QMessageBox.warning(self,"Quotation not converted",str(exc))

    def cancel_selected_quote(self):
        if not self._authorize("delete"): return
        quote=self._selected_quote()
        if not quote: return
        answer=QMessageBox.question(self,"Cancel quotation",f"Cancel {quote['quote_number']}? Its history will be kept.",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes: return
        try:
            self.store.operations.set_quote_status(quote["id"],"Cancelled")
            self.store.log(self.username,"Cancelled quotation",quote["quote_number"])
            self.reload_page("Sales")
        except OperationError as exc:
            QMessageBox.warning(self,"Quotation not cancelled",str(exc))

    def return_selected_sale(self):
        if not self._authorize("process_returns"): return
        table=self.pages["Sales"].findChild(QTableWidget)
        sale_id=self._selected_table_id(table) if table else None
        if sale_id is None:
            QMessageBox.information(self,"Select a sale","Select a sale first."); return
        sale=self.store.conn.execute("SELECT * FROM sales WHERE id=?",(sale_id,)).fetchone()
        if not sale or sale["status"] not in {"Completed","Partially returned"}:
            QMessageBox.information(self,"Sale unavailable","Only sales with unreturned items can be returned."); return
        source_lines=self.store.conn.execute("SELECT * FROM sale_lines WHERE sale_id=? ORDER BY id",(sale_id,)).fetchall()
        details=[]
        for row in source_lines:
            item=dict(row); item["sale_line_id"]=int(row["id"]); item["sold_quantity"]=row["quantity"]
            item["returned_quantity"]=self.store.operations.returned_quantity(row["id"])
            details.append(item)
        currency=self.store.setting("business_currency","KES - Kenyan Shilling").split(" - ",1)[0]
        dlg=SaleReturnDialog(sale["sale_number"],details,currency,self)
        if not dlg.exec(): return
        try:
            data=dlg.return_data()
            return_id=self.store.operations.record_sale_return(sale_id,data["lines"],data["reason"])
            self.store.log(self.username,"Returned sale items",f"{sale['sale_number']} · RET-{return_id:08d} · {data['reason']}")
            QMessageBox.information(self,"Return recorded",f"Return RET-{return_id:08d} was saved. The cash refund equals the returned items' original selling value.")
            self._refresh_operation_page("Sales")
        except (OperationError,ValueError) as exc:
            QMessageBox.warning(self,"Return not saved",str(exc))

    def cancel_selected_sale(self):
        if not self._authorize("delete"): return
        table=self.pages["Sales"].findChild(QTableWidget)
        sale_id=self._selected_table_id(table) if table else None
        if sale_id is None:
            QMessageBox.information(self,"Select a sale","Select a sale first."); return
        sale=self.store.conn.execute("SELECT paid_total,sale_number FROM sales WHERE id=?",(sale_id,)).fetchone()
        if not sale: return
        refundable=max(Decimal("0.00"),Decimal(sale["paid_total"]))
        reason,ok=QInputDialog.getText(self,"Cancel sale",f"Reason for cancelling {sale['sale_number']}:")
        if not ok: return
        refund,ok=QInputDialog.getDouble(self,"Sale refund","Remaining amount paid to refund:",float(refundable),0,float(refundable),2)
        if not ok: return
        try:
            self.store.operations.cancel_sale(sale_id,reason,refund)
            self.store.log(self.username,"Cancelled sale",f"{sale['sale_number']} · refund {refund:.2f}")
            self._refresh_operation_page("Sales")
        except OperationError as exc:
            QMessageBox.warning(self,"Sale cancellation rejected",str(exc))

    def cancel_selected_purchase(self):
        if not self._authorize("delete"): return
        table=self.pages["Purchases"].findChild(QTableWidget)
        purchase_id=self._selected_table_id(table) if table else None
        if purchase_id is None:
            QMessageBox.information(self,"Select a purchase","Select a purchase first."); return
        purchase=self.store.conn.execute("SELECT paid_total,purchase_number FROM purchases WHERE id=?",(purchase_id,)).fetchone()
        if not purchase: return
        reason,ok=QInputDialog.getText(self,"Cancel purchase",f"Reason for cancelling {purchase['purchase_number']}:")
        if not ok: return
        refund,ok=QInputDialog.getDouble(self,"Supplier refund","Amount refunded:",float(purchase["paid_total"]),0,float(purchase["paid_total"]),2)
        if not ok: return
        try:
            self.store.operations.cancel_purchase(purchase_id,reason,refund)
            self.store.log(self.username,"Cancelled purchase",f"{purchase['purchase_number']} · refund {refund:.2f}")
            self._refresh_operation_page("Purchases")
        except OperationError as exc:
            QMessageBox.warning(self,"Purchase cancellation rejected",str(exc))

    def finance_page(self):
        w,v=self.page_container("Finance","Cash ledger entries from sales, purchase payments, refunds, and manual movements. This is not double-entry accounting.")
        row=QHBoxLayout()
        if "manage_finance" in self.store.role_permissions(self.role):
            add=QPushButton("＋ Record cash movement"); add.setObjectName("primary"); add.clicked.connect(self.create_cash_entry_dialog); row.addWidget(add)
        legacy=QPushButton("View legacy finance records"); legacy.clicked.connect(lambda:self._show_legacy_records("Finance")); row.addWidget(legacy); row.addStretch()
        balance=self.store.operations.cash_balance()
        currency=self.store.setting("business_currency","KES - Kenyan Shilling").split(" - ",1)[0]
        row.addWidget(QLabel(f"Current recorded cash balance: {currency} {balance:,.2f}"))
        v.addLayout(row)
        table=self._table(["ID","DIRECTION","AMOUNT","SOURCE","DESCRIPTION","CREATED"])
        rows=self.store.operations.cash_entries()
        self._fill_table(table,rows,lambda r:[r["id"],"In" if r["direction"]=="in" else "Out",f"{currency} {r['amount']}",f"{r['source_type']} #{r['source_id']}",r["description"],r["created_at"]],hide_id=True)
        v.addWidget(table,1); return w

    def create_cash_entry_dialog(self):
        if not self._authorize("manage_finance"): return
        dlg=QDialog(self); dlg.setWindowTitle("Record cash movement"); dlg.setStyleSheet(themed_style(self.palette))
        form=QFormLayout(dlg); direction=QComboBox(); direction.addItem("Money in","in"); direction.addItem("Money out","out")
        amount=QDoubleSpinBox(); amount.setDecimals(2); amount.setRange(0,999999999999.99); amount.setSingleStep(100)
        desc=QLineEdit(); form.addRow("Direction",direction); form.addRow("Amount",amount); form.addRow("Description",desc)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); form.addRow(buttons); buttons.rejected.connect(dlg.reject)
        def save():
            try:
                entry_id=self.store.operations.record_cash_entry(direction.currentData(),amount.value(),desc.text())
                self.store.log(self.username,"Recorded cash movement",f"Entry {entry_id}: {direction.currentData()} {amount.value():.2f}")
                dlg.accept()
            except OperationError as exc:
                QMessageBox.warning(dlg,"Cash movement not saved",str(exc))
        buttons.accepted.connect(save)
        if dlg.exec(): self._refresh_operation_page("Finance")

    def crm_page(self):
        w,v=self.page_container("CRM","Customer directory used by stock-backed sales and follow-up records.")
        row=QHBoxLayout()
        if "create" in self.store.role_permissions(self.role):
            add=QPushButton("＋ Add customer"); add.setObjectName("primary"); add.clicked.connect(lambda:self.create_party_dialog("customer")); row.addWidget(add)
        legacy=QPushButton("View legacy CRM records"); legacy.clicked.connect(lambda:self._show_legacy_records("CRM")); row.addWidget(legacy); row.addStretch(); v.addLayout(row)
        table=self._table(["ID","CUSTOMER","CONTACT","EMAIL","CREATED"])
        rows=self.store.operations.customers()
        self._fill_table(table,rows,lambda r:[r["id"],r["name"],r["contact"],r["email"],r["created_at"]],hide_id=True)
        v.addWidget(table,1); return w

    def create_party_dialog(self, kind):
        if not self._authorize("create"): return
        is_customer=kind=="customer"
        dlg=QDialog(self); dlg.setWindowTitle("Add customer" if is_customer else "Add supplier"); dlg.setStyleSheet(themed_style(self.palette))
        form=QFormLayout(dlg); name=QLineEdit(); contact=QLineEdit(); email=QLineEdit()
        form.addRow("Name",name); form.addRow("Phone / contact",contact); form.addRow("Email",email)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); form.addRow(buttons); buttons.rejected.connect(dlg.reject)
        def save():
            try:
                if is_customer: party_id=self.store.operations.create_customer(name.text(),contact.text(),email.text())
                else: party_id=self.store.operations.create_supplier(name.text(),contact.text(),email.text())
                self.store.log(self.username,"Created customer" if is_customer else "Created supplier",f"{name.text().strip()} · id {party_id}")
                dlg.accept()
            except OperationError as exc:
                QMessageBox.warning(dlg,"Contact not saved",str(exc))
        buttons.accepted.connect(save)
        if dlg.exec():
            page="CRM" if is_customer else "Purchases"
            self._refresh_operation_page(page)

    def reports_page(self):
        w,v=self.page_container("Reports","Live summaries from transactional sales, purchasing, stock, and cash tables.")
        legacy=QPushButton("View legacy report records")
        legacy.clicked.connect(lambda:self._show_legacy_records("Reports"))
        v.addWidget(legacy,0,Qt.AlignLeft)
        ops=self.store.operations
        sales=ops.sales(100000); purchases=ops.purchases(100000)
        cards=[
            ("Completed sales",sum(1 for row in sales if row["status"]=="Completed")),
            ("Received purchases",sum(1 for row in purchases if row["status"]=="Received")),
            ("Inventory value",f"{self.store.setting('business_currency','KES - Kenyan Shilling').split(' - ',1)[0]} {ops.inventory_value():,.2f}"),
            ("Cash balance",f"{self.store.setting('business_currency','KES - Kenyan Shilling').split(' - ',1)[0]} {ops.cash_balance():,.2f}"),
        ]
        grid=QGridLayout()
        for i,(label,value) in enumerate(cards):
            card=self.panel(); layout=QVBoxLayout(card); layout.setContentsMargins(18,16,18,16)
            layout.addWidget(QLabel(label)); number=QLabel(str(value)); number.setStyleSheet(f"color:{self.palette['blue']};font-size:18pt;font-weight:800;"); layout.addWidget(number)
            grid.addWidget(card,0,i)
        v.addLayout(grid)
        low=[]
        for product in ops.products():
            stock=ops.stock_on_hand(product["id"])
            if stock <= Decimal(str(product["reorder_level"])):
                low.append((product["sku"],product["name"],stock,product["reorder_level"]))
        table=self._table(["SKU","PRODUCT","ON HAND","REORDER LEVEL"])
        table.setRowCount(len(low))
        for i,(sku,name,stock,reorder) in enumerate(low):
            for j,value in enumerate([sku,name,f"{stock:,.3f}".rstrip("0").rstrip("."),reorder]):
                table.setItem(i,j,QTableWidgetItem(str(value)))
        v.addWidget(QLabel("Products at or below their reorder level"))
        v.addWidget(table,1)
        return w

    def records_page(self,module):
        feature=self.store.feature(module)
        raw_fields=json.loads(feature["fields_json"]) if feature else ["Name","Description","Status"]
        defs=self._field_defs(raw_fields)
        fields=[d["name"] for d in defs]
        w,v=self.page_container(module, MODULES.get(module,("◇","Custom workspace"))[1] + "  ·  Saved locally first")
        if module in ("Sales", "Purchases", "Inventory", "Finance"):
            workflow = QFrame(); workflow.setObjectName("panelAlt")
            wl = QVBoxLayout(workflow); wl.setContentsMargins(14,10,14,10); wl.setSpacing(4)
            titles = {"Sales":"Sales workflow workspace", "Purchases":"Purchasing workflow workspace", "Inventory":"Inventory operations workspace", "Finance":"Finance records workspace"}
            descriptions = {"Sales":"Keep customer, quotation, invoice and payment references in each record. Print a selected record using receipt or standard paper sizes.", "Purchases":"Record supplier, purchase reference, amounts and status; connect receiving records through matching references.", "Inventory":"Track product/SKU and quantity changes as records. For now, stock totals depend on the fields and entries you maintain.", "Finance":"Record income and expenses consistently; use Type values Income/Receipt or Expense/Payment for dashboard summaries."}
            title_label=QLabel(titles[module]); title_label.setStyleSheet("font-weight:800;font-size:11pt;")
            desc_label=QLabel(descriptions[module]); desc_label.setWordWrap(True); desc_label.setStyleSheet(f"color:{self.palette['muted']};")
            wl.addWidget(title_label); wl.addWidget(desc_label); v.addWidget(workflow)
        bar=QHBoxLayout()
        new=QPushButton("＋  New record"); new.setObjectName("primary"); new.clicked.connect(lambda:self.new_record(module,defs))
        bar.addWidget(new)
        edit=QPushButton("Edit selected"); edit.clicked.connect(lambda:self.edit_selected_record(module,table,defs)); bar.addWidget(edit)
        if self.role == "Admin":
            delete=QPushButton("Cancel selected"); delete.clicked.connect(lambda:self.delete_selected_record(module,table)); bar.addWidget(delete)
        if "export" in self.store.role_permissions(self.role):
            export=QPushButton("Export CSV"); export.clicked.connect(lambda:self.export_module_csv(module,defs)); bar.addWidget(export)
        printbtn=QPushButton("⎙  Print selected"); printbtn.clicked.connect(lambda:self.print_selected_record(module,table,defs)); bar.addWidget(printbtn)
        refresh=QPushButton("↻ Refresh"); refresh.clicked.connect(lambda:self.reload_page(module)); bar.addWidget(refresh); bar.addStretch()
        count=QLabel(f"{len(self.store.records(module))} records"); count.setStyleSheet(f"color:{self.palette['muted']}"); bar.addWidget(count)
        v.addLayout(bar)
        table=QTableWidget(); table.setColumnCount(3+len(fields)); table.setHorizontalHeaderLabels(["ID","CREATED","STATUS"]+[f["name"].upper() for f in defs])
        table.setSelectionBehavior(QAbstractItemView.SelectRows); table.setEditTriggers(QAbstractItemView.NoEditTriggers); table.setAlternatingRowColors(True); table.verticalHeader().setVisible(False); table.setSortingEnabled(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        rows=self.store.records(module); table.setRowCount(len(rows))
        for ri,row in enumerate(rows):
            data=json.loads(row["data_json"]); vals=[row["id"],row["created_at"],row["status"]]+[data.get(f,"") for f in fields]
            for ci,val in enumerate(vals):
                item=QTableWidgetItem(str(val)); item.setData(Qt.UserRole,row["id"]); table.setItem(ri,ci,item)
        table.cellDoubleClicked.connect(lambda r,c:self.edit_selected_record(module,table,defs))
        v.addWidget(table,1)
        if not rows:
            empty=QLabel("No records yet. Create your first entry to get started."); empty.setStyleSheet(f"color:{self.palette['muted']};padding:4px;"); v.addWidget(empty)
        return w
    def _selected_record_id(self, table):
        rows=table.selectionModel().selectedRows()
        if not rows: return None
        item=table.item(rows[0].row(),0)
        return int(item.text()) if item else None
    def edit_selected_record(self,module,table,defs):
        if not self._authorize('edit'):
            return
        rid=self._selected_record_id(table)
        if rid is None:
            QMessageBox.information(self,"Select a record","Select a row first, or double-click a row to edit it."); return
        row=self.store.conn.execute("SELECT * FROM records WHERE id=? AND module=?",(rid,module)).fetchone()
        if not row or row["status"] == "Cancelled": return
        if row["status"] == "Cancelled":
            QMessageBox.information(self,"Cancelled record","Cancelled records are read-only to preserve history.")
            return
        data=json.loads(row["data_json"])
        self.new_record(module,defs,record_id=rid,initial=data)
    def delete_selected_record(self,module,table):
        if not self._authorize('delete'):
            return
        rid=self._selected_record_id(table)
        if rid is None:
            QMessageBox.information(self,"Select a record","Select a row first."); return
        answer=QMessageBox.question(self,"Cancel record",f"Mark {module} record #{rid} as Cancelled? The record will remain in history.",QMessageBox.Yes|QMessageBox.No)
        if answer==QMessageBox.Yes:
            if self.store.cancel_record(rid,module):
                self.store.log(self.username,"Cancelled record",f"{module} #{rid}")
            self.reload_page(module)
    def export_module_csv(self,module,defs):
        if not self._authorize('export'):
            return
        path,_=QFileDialog.getSaveFileName(self,"Export records",f"{module.lower().replace(' ','_')}.csv","CSV files (*.csv)")
        if not path: return
        import csv
        rows=self.store.records(module,limit=100000)
        try:
            with open(path,"w",newline="",encoding="utf-8-sig") as f:
                writer=csv.writer(f); writer.writerow(["ID","Created","Status"]+[d["name"] for d in defs])
                for row in rows:
                    data=json.loads(row["data_json"]); writer.writerow([row["id"],row["created_at"],row["status"]]+[data.get(d["name"],"") for d in defs])
            self.store.log(self.username,"Exported CSV",f"{module}: {len(rows)} records")
            QMessageBox.information(self,"Export complete",f"Exported {len(rows)} records to:\n{path}")
        except Exception as e: QMessageBox.critical(self,"Export failed",str(e))
    def print_selected_record(self, module, table, defs):
        if not self._authorize('print'):
            return
        rid = self._selected_record_id(table)
        if rid is None:
            QMessageBox.information(self, "Select a record", "Select a row first, then choose Print selected.")
            return
        row = self.store.conn.execute("SELECT * FROM records WHERE id=? AND module=?", (rid, module)).fetchone()
        if not row or row["status"] == "Cancelled": return
        data = json.loads(row["data_json"])
        dlg = QDialog(self); dlg.setWindowTitle("Print document / receipt"); dlg.setStyleSheet(themed_style(self.palette)); dlg.setMinimumWidth(440)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("<span style='font-size:16pt;font-weight:800'>Print options</span>"))
        layout.addWidget(QLabel("Choose a receipt roll or standard paper size. Your printer may limit supported sizes."))
        form = QFormLayout(); size = QComboBox()
        size.addItems(["Receipt — 58 mm thermal", "Receipt — 80 mm thermal", "A6", "A5", "A4", "A3", "Letter", "Legal", "Use printer default / choose in dialog"])
        form.addRow("Paper size", size)
        preview = QCheckBox("Preview before printing"); preview.setChecked(True); form.addRow("Preview", preview)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Print | QDialogButtonBox.Cancel); layout.addWidget(buttons)
        buttons.rejected.connect(dlg.reject)
        def do_print():
            dlg.accept()
            printer = QPrinter(QPrinter.HighResolution)
            choice = size.currentText()
            pages = {
                "A6": QPageSize(QPageSize.PageSizeId.A6), "A5": QPageSize(QPageSize.PageSizeId.A5),
                "A4": QPageSize(QPageSize.PageSizeId.A4), "A3": QPageSize(QPageSize.PageSizeId.A3),
                "Letter": QPageSize(QPageSize.PageSizeId.Letter), "Legal": QPageSize(QPageSize.PageSizeId.Legal)
            }
            if choice in pages: printer.setPageSize(pages[choice])
            elif choice.startswith("Receipt"):
                # Thermal roll widths are set in millimetres; choose a practical receipt length.
                width = 58 if "58" in choice else 80
                printer.setPageSize(QPageSize(QSize(width * 1000, 200 * 1000), QPageSize.Unit.Millimeter, f"Receipt {width}mm", QPageSize.PageSizeId.Custom))
                printer.setFullPage(False)
            document = QTextDocument()
            business = self.store.setting("business_name", "Your Business")
            address = self.store.setting("business_address", "")
            phone = self.store.setting("business_phone", "")
            email = self.store.setting("business_email", "")
            currency = self.store.setting("business_currency", "KES - Kenyan Shilling").split(" - ",1)[0]
            narrow = choice.startswith("Receipt")
            rows = "".join(f"<tr><td style='padding:5px 4px;border-bottom:1px solid #ddd'>{self._html_escape(str(d['name']))}</td><td style='padding:5px 4px;border-bottom:1px solid #ddd'>{self._html_escape(str(data.get(d['name'], '')))}</td></tr>" for d in defs)
            doc_title = "RECEIPT" if narrow or module == "Finance" else module.upper() + " RECORD"
            html = f"""<html><head><meta charset='utf-8'><style>body{{font-family:Arial,sans-serif;color:#111;font-size:{'9pt' if narrow else '10pt'};}}h1{{font-size:{'15pt' if narrow else '20pt'};margin:0 0 8px}}table{{width:100%;border-collapse:collapse}}th{{text-align:left;padding:5px 4px;background:#eee}}.muted{{color:#555;font-size:9pt}}</style></head><body>
            <h1>{self._html_escape(business)}</h1><div class='muted'>{self._html_escape(address)}<br>{self._html_escape(phone)} {self._html_escape(email)}</div><hr><h2>{doc_title}</h2>
            <p><b>Module:</b> {self._html_escape(module)} &nbsp; <b>Record:</b> #{rid}<br><b>Created:</b> {self._html_escape(row['created_at'])}</p>
            <table><thead><tr><th>Field</th><th>Value</th></tr></thead><tbody>{rows}</tbody></table>
            <p class='muted'>Printed from NextGen ERP · {self._html_escape(currency)} where applicable</p></body></html>"""
            document.setHtml(html)
            if preview.isChecked():
                pd = QPrintPreviewDialog(printer, self); pd.setWindowTitle("Print preview — " + module)
                pd.paintRequested.connect(document.print_)
                pd.exec()
            else:
                pd = QPrintDialog(printer, self)
                if pd.exec() == QDialog.Accepted: document.print_(printer)
            self.store.log(self.username, "Printed record", f"{module} #{rid}; paper={choice}")
        buttons.accepted.connect(do_print)
        dlg.exec()
    def print_selected_business_document(self, document_type):
        if not self._authorize("print"): return
        table_map={"invoice":getattr(self,"sales_table",None),"quote":getattr(self,"quote_table",None),
                   "purchase_order":getattr(self,"purchase_orders_table",None),"goods_received":getattr(self,"purchase_receipts_table",None),"repair_job":getattr(self,"repair_jobs_table",None)}
        table=table_map.get(document_type)
        record_id=self._selected_table_id(table) if table else None
        if record_id is None:
            QMessageBox.information(self,"Select a document","Select a matching transaction, quotation, or order row first.")
            return
        renderers={"invoice":render_invoice,"quote":render_quote,"purchase_order":render_purchase_order,"goods_received":render_goods_received,"repair_job":render_repair_job}
        try:
            title,html=renderers[document_type](self.store,record_id)
            self._print_html_document(title,html,f"{document_type} #{record_id}")
        except (ValueError,sqlite3.Error) as exc:
            QMessageBox.warning(self,"Document unavailable",str(exc))

    def _print_html_document(self,title,html,activity):
        dlg=QDialog(self); dlg.setWindowTitle("Print "+title.title()); dlg.setStyleSheet(themed_style(self.palette)); dlg.setMinimumWidth(440)
        layout=QVBoxLayout(dlg)
        layout.addWidget(QLabel("<span style='font-size:16pt;font-weight:800'>Print business document</span>"))
        layout.addWidget(QLabel("Preview the document and choose a receipt roll or standard paper size. Printer capabilities may vary."))
        form=QFormLayout(); size=QComboBox()
        size.addItems(["Receipt — 58 mm thermal","Receipt — 80 mm thermal","A6","A5","A4","A3","Letter","Legal","Use printer default / choose in dialog"])
        form.addRow("Paper size",size)
        preview=QCheckBox("Preview before printing"); preview.setChecked(True); form.addRow("Preview",preview)
        layout.addLayout(form)
        buttons=QDialogButtonBox(QDialogButtonBox.Print|QDialogButtonBox.Cancel); layout.addWidget(buttons); buttons.rejected.connect(dlg.reject)
        def do_print():
            dlg.accept()
            printer=QPrinter(QPrinter.HighResolution); choice=size.currentText()
            pages={"A6":QPageSize(QPageSize.PageSizeId.A6),"A5":QPageSize(QPageSize.PageSizeId.A5),
                   "A4":QPageSize(QPageSize.PageSizeId.A4),"A3":QPageSize(QPageSize.PageSizeId.A3),
                   "Letter":QPageSize(QPageSize.PageSizeId.Letter),"Legal":QPageSize(QPageSize.PageSizeId.Legal)}
            if choice in pages: printer.setPageSize(pages[choice])
            elif choice.startswith("Receipt"):
                width=58 if "58" in choice else 80
                printer.setPageSize(QPageSize(QSize(width*1000,200*1000),QPageSize.Unit.Millimeter,f"Receipt {width}mm",QPageSize.PageSizeId.Custom))
                printer.setFullPage(False)
            document=QTextDocument(); document.setHtml(html)
            if preview.isChecked():
                preview_dialog=QPrintPreviewDialog(printer,self); preview_dialog.setWindowTitle("Print preview — "+title)
                preview_dialog.paintRequested.connect(document.print_); preview_dialog.exec()
            else:
                print_dialog=QPrintDialog(printer,self)
                if print_dialog.exec()==QDialog.Accepted: document.print_(printer)
            self.store.log(self.username,"Printed business document",f"{activity}; paper={choice}")
        buttons.accepted.connect(do_print); dlg.exec()

    def _html_escape(self, value):
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")

    def reload_page(self,name):
        if name in self.pages:
            old=self.pages.pop(name); self.stack.removeWidget(old); old.deleteLater()
        self.refresh_syncpill(); self.show_page(name)
    def invalidate_page(self,name):
        if name in self.pages:
            old=self.pages.pop(name); self.stack.removeWidget(old); old.deleteLater()
    def new_record(self,module,fields,record_id=None,initial=None):
        if not self._authorize('edit' if record_id else 'create'):
            return
        defs=self._field_defs(fields); initial=initial or {}
        dlg=QDialog(self); dlg.setWindowTitle(("Edit " if record_id else "Create ")+module+" record"); dlg.setMinimumWidth(560); dlg.setMinimumHeight(300); dlg.resize(700, min(820, 300 + len(defs)*62)); dlg.setStyleSheet(themed_style(self.palette))
        v=QVBoxLayout(dlg); v.addWidget(QLabel(f"<span style='font-size:17pt;font-weight:800'>{'Edit' if record_id else 'New'} {module} record</span>"))
        v.addWidget(QLabel(f"<span style='color:{self.palette['muted']}'>This entry is stored on this device first. Required fields are marked *.</span>"))
        form=QFormLayout(); edits={}
        for d in defs:
            name=d["name"]; typ=d["type"]
            label=name+(" *" if d["required"] else "")
            if typ=="choice":
                e=QComboBox(); e.setEditable(False); e.addItems([str(x) for x in d.get("options",[]) if str(x)])
                if not e.count(): e.addItems(["Open","In Progress","Completed","On Hold"])
                ix=e.findText(str(initial.get(name,""))); e.setCurrentIndex(ix if ix>=0 else 0)
            elif typ=="date":
                e=QDateEdit(); e.setCalendarPopup(True); e.setDisplayFormat("yyyy-MM-dd")
                if initial.get(name):
                    from PySide6.QtCore import QDate
                    dt=QDate.fromString(str(initial[name]),"yyyy-MM-dd")
                    if dt.isValid(): e.setDate(dt)
                else: e.setDate(e.date().currentDate())
            elif typ=="number":
                e=QDoubleSpinBox(); e.setRange(-999999999,999999999); e.setDecimals(2); e.setValue(float(initial.get(name,0) or 0))
            elif typ=="notes":
                e=QTextEdit(); e.setMaximumHeight(90); e.setPlainText(str(initial.get(name,"")))
            else:
                e=QLineEdit(str(initial.get(name,""))); e.setPlaceholderText("Enter "+name.lower())
            form.addRow(label,e); edits[name]=(e,d)
        v.addLayout(form); buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); v.addWidget(buttons)
        buttons.rejected.connect(dlg.reject)
        def save():
            data={}
            for name,(e,d) in edits.items():
                if isinstance(e,QComboBox): val=e.currentText().strip()
                elif isinstance(e,QDateEdit): val=e.date().toString("yyyy-MM-dd")
                elif isinstance(e,QDoubleSpinBox): val=e.value()
                elif isinstance(e,QTextEdit): val=e.toPlainText().strip()
                else: val=e.text().strip()
                if d["required"] and (val=="" or val is None):
                    QMessageBox.warning(dlg,"Required field",f"Please complete {name}."); return
                data[name]=val
            if not any(str(x).strip() for x in data.values()): QMessageBox.warning(dlg,"Missing data","Enter at least one value."); return
            if record_id:
                self.store.update_record(record_id,module,data); self.store.log(self.username,"Updated record",f"{module} #{record_id}")
            else:
                rid=self.store.create_record(module,data); self.store.log(self.username,"Created record",f"{module} #{rid}")
            dlg.accept()
        buttons.accepted.connect(save)
        if dlg.exec():
            self.pages.pop(module,None); self.show_page(module); self.refresh_syncpill()
    def module_manager(self):
        if not self._authorize('configure'):
            return
        w,v=self.page_container("Module Manager","Adapt the workspace to your business. Enable modules or create custom data workspaces.")
        info=QLabel("Existing modules can be enabled or disabled here. Custom modules define their own fields; installing arbitrary code extensions will require a trusted plugin framework.")
        info.setWordWrap(True); info.setStyleSheet(f"color:{self.palette['muted']};background:{self.palette['panel2']};padding:14px;border-radius:10px;"); v.addWidget(info)
        for row in self.store.features():
            card=QFrame(); card.setObjectName("panel"); card.setMinimumHeight(76); h=QHBoxLayout(card); h.setContentsMargins(15,12,15,12); h.setSpacing(12)
            icon,name=MODULES.get(row["name"],("◇",""))
            h.addWidget(QLabel(f"<span style='font-size:17pt;color:{self.palette['purple']}'>{icon}</span>"))
            labels=QVBoxLayout(); labels.setContentsMargins(0,2,0,2); labels.setSpacing(5); title_label=QLabel(f"<b>{row['name']}</b>"); title_label.setMinimumHeight(22); desc_label=QLabel(f"<span style='color:{self.palette['muted']}'>{row['description'] or 'Custom workspace'}</span>"); desc_label.setWordWrap(True); desc_label.setMinimumHeight(20); labels.addWidget(title_label); labels.addWidget(desc_label); h.addLayout(labels,1)
            custom=QLabel("CUSTOM MODULE" if row["custom"] else "CORE MODULE"); custom.setStyleSheet(f"color:{self.palette['muted']};font-size:8pt;"); h.addWidget(custom)
            cb=QCheckBox("Enabled"); cb.setChecked(bool(row["enabled"])); cb.stateChanged.connect(lambda state,n=row["name"]:self.toggle_feature(n,state==Qt.Checked))
            h.addWidget(cb); v.addWidget(card)
        add=QPushButton("＋  Create custom module"); add.setObjectName("primary"); add.clicked.connect(self.create_module); v.addWidget(add,0,Qt.AlignLeft); v.addStretch()
        return w
    def toggle_feature(self,name,enabled):
        if not self._authorize('configure'):
            return
        try:
            self.store.set_enabled(name,enabled)
        except ValueError as exc:
            QMessageBox.warning(self,"Module change blocked",str(exc))
            self.reload_page("Module Manager")
            return
        self.store.log(self.username,"Feature changed",f"{name}: {enabled}")
        self.pages.clear(); self.rebuild_nav(); self.show_page("Module Manager"); self.refresh_syncpill()
    def rebuild_nav(self):
        for i in reversed(range(self.navlay.count())):
            item=self.navlay.itemAt(i); widget=item.widget()
            if widget: widget.deleteLater()
        self.nav.clear()
        self.add_nav_section("WORKSPACE",["Dashboard","Sales","Purchases","Inventory","Finance","HR & Payroll","CRM","Projects","Reports"] + [r["name"] for r in self.store.features(True) if r["name"] not in MODULES])
        self.add_nav_section("ADMINISTRATION",["User Management","Role Permissions","Module Manager","Feature Store","Integrations","System Settings","Database & Backup"])
        self.navlay.addStretch()
    def create_module(self):
        if not self._authorize('configure'):
            return
        dlg=QDialog(self); dlg.setWindowTitle("No-code module builder"); dlg.setStyleSheet(themed_style(self.palette)); dlg.setMinimumWidth(650)
        v=QVBoxLayout(dlg); v.addWidget(QLabel("<span style='font-size:18pt;font-weight:800'>Build a custom workspace</span>"))
        v.addWidget(QLabel("Create a module with typed fields and validation—no Python editing required."))
        form=QFormLayout(); name=QLineEdit(); desc=QLineEdit(); fields=QTextEdit()
        fields.setPlaceholderText("One field per line: Field name | type | required | options (for choice)\nCustomer | text | required\nAmount | number | required\nDue date | date\nStatus | choice | required | Open,In Progress,Completed,On Hold\nNotes | notes")
        fields.setMinimumHeight(175)
        form.addRow("Module name",name); form.addRow("Purpose",desc); form.addRow("Field definitions",fields); v.addLayout(form)
        helptext=QLabel("Types: text, number, date, choice, notes. Add 'required' to enforce a value. For choice fields, supply comma-separated options in column four. Maximum 20 fields.")
        helptext.setWordWrap(True); helptext.setStyleSheet(f"color:{self.palette['muted']};padding:8px;background:{self.palette['panel2']};border-radius:8px;"); v.addWidget(helptext)
        bb=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); v.addWidget(bb); bb.rejected.connect(dlg.reject)
        def save():
            n=name.text().strip()
            if len(n)<2 or len(n)>40: QMessageBox.warning(dlg,"Invalid name","Module name must be 2–40 characters."); return
            defs=[]
            raw=fields.toPlainText().strip()
            lines=[line.strip() for line in raw.splitlines() if line.strip()]
            if not lines: lines=["Name | text | required","Description | notes"]
            if len(lines) > 20:
                QMessageBox.warning(dlg,"Too many fields","A custom module can contain up to 20 fields. Remove extra lines and try again."); return
            for line in lines:
                parts=[x.strip() for x in line.split("|")]
                fname=parts[0][:60]
                if len(fname)<1: continue
                typ=(parts[1].lower() if len(parts)>1 and parts[1] else "text")
                if typ not in ("text","number","date","choice","notes"):
                    QMessageBox.warning(dlg,"Invalid field type",f"Unsupported type '{typ}' for {fname}. Use text, number, date, choice, or notes."); return
                required=len(parts)>2 and "required" in parts[2].lower()
                options=[]
                if typ=="choice":
                    options=[x.strip()[:60] for x in (parts[3].split(",") if len(parts)>3 else ["Open","In Progress","Completed"]) if x.strip()][:30]
                    if not options: options=["Open","In Progress","Completed"]
                defs.append({"name":fname,"type":typ,"required":required,"options":options})
            if not defs: QMessageBox.warning(dlg,"No fields","Add at least one field definition."); return
            if len({x['name'].lower() for x in defs})!=len(defs): QMessageBox.warning(dlg,"Duplicate fields","Each field name must be unique."); return
            try:
                self.store.add_feature(n,desc.text().strip()[:200],defs); self.store.log(self.username,"Created module",n); dlg.accept()
            except sqlite3.IntegrityError: QMessageBox.warning(dlg,"Already exists","That module name already exists.")
            except ValueError as exc: QMessageBox.warning(dlg,"Invalid module definition",str(exc))
        bb.accepted.connect(save)
        if dlg.exec():
            self.pages.clear(); self.rebuild_nav(); self.show_page("Module Manager"); self.refresh_syncpill()
    def user_management(self):
        if not self._authorize('manage_users'):
            return
        w,v=self.page_container("User Management","Manage local accounts, status and credentials. Keep administrator access limited.")
        actions=QHBoxLayout()
        btn=QPushButton("＋ Add user"); btn.setObjectName("primary"); btn.clicked.connect(self.add_user); actions.addWidget(btn)
        toggle=QPushButton("Enable / disable selected"); toggle.clicked.connect(self.toggle_selected_user); actions.addWidget(toggle)
        reset=QPushButton("Reset selected password"); reset.clicked.connect(self.reset_selected_user_password); actions.addWidget(reset)
        actions.addStretch(); v.addLayout(actions)
        table=QTableWidget(); table.setColumnCount(5); table.setHorizontalHeaderLabels(["ID","USERNAME","DISPLAY NAME","ROLE","ACTIVE"]); table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); table.verticalHeader().setVisible(False); table.setSelectionBehavior(QAbstractItemView.SelectRows); table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        rows=self.store.conn.execute("SELECT * FROM users ORDER BY id").fetchall(); table.setRowCount(len(rows))
        for i,r in enumerate(rows):
            for j,val in enumerate([r["id"],r["username"],r["display_name"],r["role"],"Yes" if r["active"] else "No"]): table.setItem(i,j,QTableWidgetItem(str(val)))
        table.setColumnHidden(0,True)
        v.addWidget(table,1)
        self.user_table=table
        return w
    def add_user(self):
        if not self._authorize('manage_users'):
            return
        dlg=QDialog(self); dlg.setWindowTitle("Add user"); dlg.setStyleSheet(themed_style(self.palette)); form=QFormLayout(dlg)
        user=QLineEdit(); disp=QLineEdit(); pw=QLineEdit(); pw.setEchoMode(QLineEdit.Password); role=QComboBox(); role.addItems(["Staff","Manager"]+self.store.custom_roles()+["Admin"])
        form.addRow("Username",user); form.addRow("Display name",disp); form.addRow("Temporary password",pw); form.addRow("Role",role)
        bb=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); form.addRow(bb); bb.rejected.connect(dlg.reject)
        def save():
            username=user.text().strip(); display_name=disp.text().strip(); password=pw.text()
            if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}",username) or not display_name or len(display_name)>100 or len(password)<12:
                QMessageBox.warning(dlg,"Check details","Use a 3–40 character username (letters, numbers, dot, underscore or dash), a display name, and a password of at least 12 characters."); return
            try:
                with self.store.conn:
                    self.store.conn.execute("INSERT INTO users(username,display_name,role,password_hash,active,created_at) VALUES(?,?,?,?,1,?)",(username,display_name,role.currentText(),hash_password(password),stamp()))
                self.store.log(self.username,"Added user",username); dlg.accept()
            except sqlite3.IntegrityError:
                QMessageBox.warning(dlg,"Duplicate username","That username already exists.")
        bb.accepted.connect(save)
        if dlg.exec(): self.pages.pop("User Management",None); self.show_page("User Management")
    def _selected_user(self):
        table=getattr(self,"user_table",None)
        if table is None or table.currentRow()<0:
            QMessageBox.information(self,"Select a user","Select a user row first.")
            return None
        item=table.item(table.currentRow(),0)
        if not item: return None
        return self.store.conn.execute("SELECT * FROM users WHERE id=?",(int(item.text()),)).fetchone()
    def toggle_selected_user(self):
        if not self._authorize("manage_users"): return
        user=self._selected_user()
        if not user: return
        if user["username"].casefold()==self.username.casefold() and user["active"]:
            QMessageBox.warning(self,"Cannot disable current account","Sign in as another administrator before disabling this account.")
            return
        if user["role"]=="Admin" and user["active"]:
            active_admins=self.store.conn.execute("SELECT COUNT(*) FROM users WHERE role='Admin' AND active=1").fetchone()[0]
            if active_admins<=1:
                QMessageBox.warning(self,"Keep an administrator","At least one active administrator account is required.")
                return
        next_active=not bool(user["active"])
        answer=QMessageBox.question(self,"Confirm account status",f"{('Enable' if next_active else 'Disable')} account {user['username']}?",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer!=QMessageBox.Yes: return
        with self.store.conn:
            self.store.conn.execute("UPDATE users SET active=? WHERE id=?",(int(next_active),user["id"]))
        self.store.log(self.username,"Changed user status",f"{user['username']}: {'active' if next_active else 'disabled'}")
        self.pages.pop("User Management",None); self.show_page("User Management")
    def reset_selected_user_password(self):
        if not self._authorize("manage_users"): return
        user=self._selected_user()
        if not user: return
        password,ok=QInputDialog.getText(self,"Reset password",f"Enter a temporary password for {user['username']} (12 or more characters):",QLineEdit.Password)
        if not ok: return
        if len(password)<12:
            QMessageBox.warning(self,"Password too short","Use a password with at least 12 characters.")
            return
        with self.store.conn:
            self.store.conn.execute("UPDATE users SET password_hash=? WHERE id=?",(hash_password(password),user["id"]))
        self.store.log(self.username,"Reset user password",user["username"])
        QMessageBox.information(self,"Password reset","The new password was saved. Share it with the account owner through a secure channel.")
    def role_permissions(self):
        if not self._authorize("configure"):
            return
        role_names=self.store.assignable_roles()
        w,v=self.page_container("Role Permissions","Create focused staff roles and assign their actions, modules, and dashboard widgets. Protected administrator powers remain unavailable to every custom role.")
        toolbar=QHBoxLayout()
        add=QPushButton("＋ Create custom role"); add.setObjectName("primary")
        def create_role():
            dialog=QDialog(self); dialog.setWindowTitle("Create custom role"); dialog.setStyleSheet(themed_style(self.palette))
            form=QFormLayout(dialog); name=QLineEdit(); name.setPlaceholderText("e.g. Technician")
            template=QComboBox(); template.addItems(["Staff","Manager"])
            form.addRow("Role name",name); form.addRow("Starting grants",template)
            buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel); form.addRow(buttons)
            buttons.rejected.connect(dialog.reject); buttons.accepted.connect(dialog.accept)
            if not dialog.exec(): return
            try:
                created=self.store.create_custom_role(name.text(),template.currentText())
                self.store.log(self.username,"Created role",created)
                self.pages.pop("Role Permissions",None); self.show_page("Role Permissions")
            except ValueError as exc:
                QMessageBox.warning(self,"Could not create role",str(exc))
        add.clicked.connect(create_role); toolbar.addWidget(add)
        remove=QPushButton("Remove unused custom role")
        def delete_role():
            roles=self.store.custom_roles()
            if not roles:
                QMessageBox.information(self,"No custom roles","Create a custom role first."); return
            role,ok=QInputDialog.getItem(self,"Remove custom role","Select an unused custom role:",roles,0,False)
            if not ok: return
            answer=QMessageBox.question(self,"Confirm role removal",f"Remove role {role}? Accounts must be reassigned first.",QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
            if answer!=QMessageBox.Yes: return
            try:
                self.store.delete_custom_role(role); self.store.log(self.username,"Removed role",role)
                self.pages.pop("Role Permissions",None); self.show_page("Role Permissions")
            except ValueError as exc:
                QMessageBox.warning(self,"Role still in use",str(exc))
        remove.clicked.connect(delete_role); toolbar.addWidget(remove); toolbar.addStretch(); v.addLayout(toolbar)
        editable=[cap for cap in ROLE_CAPABILITIES if cap not in PROTECTED_CAPABILITIES]
        table=QTableWidget(len(role_names),len(editable)+1)
        table.setHorizontalHeaderLabels(["ROLE"]+[cap.replace("_"," ").title() for cap in editable])
        table.verticalHeader().setVisible(False); table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        editors={}
        for row,role in enumerate(role_names):
            table.setItem(row,0,QTableWidgetItem(role))
            current=self.store.role_permissions(role)
            for col,cap in enumerate(editable,1):
                check=QCheckBox(); check.setChecked(cap in current); check.setToolTip(f"Allow {role} to {cap.replace('_',' ')}")
                table.setCellWidget(row,col,check); editors[(role,cap)]=check
        v.addWidget(QLabel("Allowed actions")); v.addWidget(table)
        v.addWidget(QLabel("Module access"))
        module_names=[row["name"] for row in self.store.features()]
        module_table=QTableWidget(len(role_names),len(module_names)+1)
        module_table.setHorizontalHeaderLabels(["ROLE"]+module_names)
        module_table.verticalHeader().setVisible(False); module_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        module_editors={}
        for row,role in enumerate(role_names):
            module_table.setItem(row,0,QTableWidgetItem(role))
            assigned=self.store.role_modules(role)
            for col,module in enumerate(module_names,1):
                box=QCheckBox(); box.setChecked(module in assigned); box.setToolTip(f"Allow {role} to open {module}")
                module_table.setCellWidget(row,col,box); module_editors[(role,module)]=box
        v.addWidget(module_table)
        v.addWidget(QLabel("Dashboard widgets"))
        widget_names=list(DASHBOARD_WIDGETS); dashboard_roles=["Admin"]+role_names
        widget_table=QTableWidget(len(dashboard_roles),len(widget_names)+1)
        widget_table.setHorizontalHeaderLabels(["ROLE"]+[DASHBOARD_WIDGETS[name] for name in widget_names])
        widget_table.verticalHeader().setVisible(False); widget_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        widget_editors={}
        for row,role in enumerate(dashboard_roles):
            widget_table.setItem(row,0,QTableWidgetItem(role)); selected=self.store.dashboard_widgets(role)
            for col,widget in enumerate(widget_names,1):
                box=QCheckBox(); box.setChecked(widget in selected); box.setToolTip(f"Show {DASHBOARD_WIDGETS[widget]} for {role}")
                widget_table.setCellWidget(row,col,box); widget_editors[(role,widget)]=box
        v.addWidget(widget_table)
        note=QLabel("Custom roles start with Staff-level grants. You can add only non-privileged actions. Configure, user administration, backup, synchronization, and destructive deletion are reserved for the built-in Admin role. A role in use cannot be removed.")
        note.setWordWrap(True); note.setStyleSheet(f"color:{self.palette['muted']};padding:12px;"); v.addWidget(note)
        save=QPushButton("Save role permissions"); save.setObjectName("primary")
        def save_permissions():
            try:
                for role in role_names:
                    self.store.set_role_permissions(role,[cap for cap in editable if editors[(role,cap)].isChecked()])
                    self.store.set_role_modules(role,[module for module in module_names if module_editors[(role,module)].isChecked()])
                for role in dashboard_roles:
                    self.store.set_dashboard_widgets(role,[widget for widget in widget_names if widget_editors[(role,widget)].isChecked()])
                self.pages.clear(); self.rebuild_nav()
                self.store.log(self.username,"Updated role permissions",", ".join(role_names))
                QMessageBox.information(self,"Saved","Role actions, module access, and dashboard widgets were saved locally.")
            except (TypeError,ValueError) as exc:
                QMessageBox.warning(self,"Could not save permissions",str(exc))
        save.clicked.connect(save_permissions); v.addWidget(save,0,Qt.AlignLeft); v.addStretch(); return w
    def feature_store(self):
        if not self._authorize('configure'):
            return
        w,v=self.page_container("Feature Store","A future catalog for trusted, signed add-ons.")
        p=self.panel(); l=QVBoxLayout(p); l.setContentsMargins(20,20,20,20)
        l.addWidget(QLabel("<span style='font-size:16pt;font-weight:800'>Extensible by design</span>"))
        l.addWidget(QLabel("The current build supports configurable custom data modules. A secure feature store needs a package format, signing, version compatibility, permissions, migrations, and rollback before third-party code can be installed."))
        l.addWidget(QLabel(f"<span style='color:{self.palette['cyan']}'>Planned:</span> versioned modules · permission manifests · signed packages · one-click upgrades · rollback"))
        v.addWidget(p); v.addStretch(); return w
    def settings_page(self):
        if not self._authorize('configure'):
            return
        w,v=self.page_container("Business & System Settings","Personalize the ERP for your organization. Settings are saved on this computer.")
        business=self.panel(); bf=QFormLayout(business); bf.setContentsMargins(24,24,24,24); bf.setSpacing(16); bf.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        company=QLineEdit(self.store.setting("business_name", "")); company.setPlaceholderText("e.g. Acme Enterprises Ltd")
        tagline=QLineEdit(self.store.setting("business_tagline", "")); tagline.setPlaceholderText("A short description of your business")
        address=QLineEdit(self.store.setting("business_address", "")); phone=QLineEdit(self.store.setting("business_phone", ""))
        email=QLineEdit(self.store.setting("business_email", "")); tax=QLineEdit(self.store.setting("business_tax_id", ""))
        tax_label=QLineEdit(self.store.setting("business_tax_label","Tax")); tax_label.setMaxLength(60)
        tax_rate=QDoubleSpinBox(); tax_rate.setRange(0,100); tax_rate.setDecimals(2); tax_rate.setSuffix(" %")
        try: tax_rate.setValue(float(self.store.setting("business_tax_rate","0.00")))
        except ValueError: tax_rate.setValue(0)
        currency=QComboBox(); currency.addItems(["KES - Kenyan Shilling","USD - US Dollar","TZS - Tanzanian Shilling","UGX - Ugandan Shilling","GBP - British Pound","EUR - Euro"])
        saved_currency=self.store.setting("business_currency", "KES - Kenyan Shilling"); ix=currency.findText(saved_currency); currency.setCurrentIndex(ix if ix >= 0 else 0)
        bf.addRow("Business / organization name",company); bf.addRow("Tagline",tagline); bf.addRow("Business address",address); bf.addRow("Phone number",phone); bf.addRow("Business email",email); bf.addRow("Tax / registration number",tax); bf.addRow("Sales tax label",tax_label); bf.addRow("Sales tax rate (added to prices)",tax_rate); bf.addRow("Default currency",currency)
        business_type=QComboBox(); business_type.addItems(list(BUSINESS_PROFILES))
        saved_type=self.store.setting("business_type", "General business"); ti=business_type.findText(saved_type); business_type.setCurrentIndex(ti if ti>=0 else 0)
        accent=QComboBox(); accent.addItems(["Ocean blue", "Forest green", "Warm amber", "Royal violet", "Teal"])
        saved_accent=self.store.setting("business_accent", "Ocean blue"); ai=accent.findText(saved_accent); accent.setCurrentIndex(ai if ai>=0 else 0)
        bf.addRow("Business profile", business_type); bf.addRow("Brand accent", accent)
        v.addWidget(business)
        sync=self.panel(); sf=QFormLayout(sync); sf.setContentsMargins(24,24,24,24); sf.setSpacing(16); sf.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        url=QLineEdit(self.store.setting("sync_url","")); url.setPlaceholderText("https://your-trusted-server.example/api/sync")
        device=QLineEdit(self.store.setting("device_name",os.environ.get("COMPUTERNAME","Desktop")))
        sf.addRow("HTTPS sync endpoint",url); sf.addRow("Device name",device); v.addWidget(sync)
        note=QLabel("Offline-first: business details and records are stored locally. Cloud synchronization is not active until a compatible, trusted server is deployed.")
        note.setWordWrap(True); note.setStyleSheet(f"color:{self.palette['muted']};padding:8px;"); v.addWidget(note)
        row=QHBoxLayout(); save=QPushButton("Save business settings"); save.setObjectName("primary")
        def do_save():
            values={"business_name":company.text().strip()[:120],"business_tagline":tagline.text().strip()[:200],"business_address":address.text().strip()[:250],"business_phone":phone.text().strip()[:60],"business_email":email.text().strip()[:120],"business_tax_id":tax.text().strip()[:100],"business_tax_label":tax_label.text().strip()[:60] or "Tax","business_tax_rate":format(Decimal(str(tax_rate.value())).quantize(Decimal("0.01")),"f"),"business_currency":currency.currentText(),"business_type":business_type.currentText(),"business_accent":accent.currentText(),"sync_url":url.text().strip(),"device_name":device.text().strip()[:100]}
            profile_applied=self.store.setting("applied_profile", "") == values["business_type"]
            preserved_modules=self.store.modules_preserved_by_profile(values["business_type"])
            if not profile_applied:
                answer=QMessageBox.question(self,"Apply business profile?",
                    "Applying this profile changes which built-in workspaces are enabled and adds its starter workspaces. Existing records and custom field definitions are preserved. Apply now?",
                    QMessageBox.Yes|QMessageBox.No, QMessageBox.Yes)
                if answer == QMessageBox.Yes:
                    try:
                        self.store.apply_profile(values["business_type"])
                        profile_applied=True
                    except ValueError as exc:
                        QMessageBox.warning(self,"Profile not applied",str(exc))
                        return
            for k,val in values.items():
                self.store.set_setting(k,val)
            self.store.log(self.username,"Updated business settings",values["business_name"] or "Business profile")
            self.refresh_appearance()
            profile_note=("The profile's starter workspace layout is active." if profile_applied else "The selected profile is saved; its workspace layout has not been applied yet.")
            preserved_note=(" Workspaces kept enabled to retain existing records: " + ", ".join(preserved_modules) + ".") if profile_applied and preserved_modules else ""
            QMessageBox.information(self,"Saved",f"Business identity and settings were saved locally. {profile_note}{preserved_note} Profile workspaces use configurable records; connected industry workflows are still in progress.")

        save.clicked.connect(do_save); row.addWidget(save); syncbtn=QPushButton("↻  Sync now"); syncbtn.clicked.connect(self.sync_now); row.addWidget(syncbtn); row.addStretch(); v.addLayout(row); v.addStretch(); return w
    def update_branding(self):
        business_name=self.store.setting("business_name", "NextGen").strip() or "NextGen"
        escaped=self._html_escape(business_name)
        self.brand_label.setText(f"⬡  {escaped} <span style='color:{self.palette['blue']}'>ERP</span>")
        self.setWindowTitle((self.store.setting("business_name", "Your Business").strip() or "Your Business") + " | NextGen ERP")

    def _capture_page_state(self,page):
        state={}
        fields=((QLineEdit,"line_edits"),(QTextEdit,"text_edits"),(QPlainTextEdit,"plain_text_edits"),
                (QComboBox,"combos"),(QSpinBox,"spin_boxes"),(QDoubleSpinBox,"double_spin_boxes"),
                (QDateEdit,"date_edits"),(QCheckBox,"checks"),(QTableWidget,"tables"),(QScrollArea,"scroll_areas"))
        for widget_type,key in fields:
            values=[]
            for widget in page.findChildren(widget_type):
                if widget_type is QLineEdit:
                    values.append((widget.text(),widget.cursorPosition()))
                elif widget_type in {QTextEdit,QPlainTextEdit}:
                    values.append((widget.toPlainText(),widget.textCursor().position()))
                elif widget_type is QComboBox:
                    values.append((widget.currentIndex(),widget.currentData(),widget.currentText(),widget.isEditable()))
                elif widget_type in {QSpinBox,QDoubleSpinBox}:
                    values.append(widget.value())
                elif widget_type is QDateEdit:
                    values.append(widget.date())
                elif widget_type is QCheckBox:
                    values.append(widget.isChecked())
                elif widget_type is QTableWidget:
                    values.append((widget.currentRow(),widget.currentColumn(),[row.row() for row in widget.selectionModel().selectedRows()]))
                elif widget_type is QScrollArea:
                    values.append((widget.verticalScrollBar().value(),widget.horizontalScrollBar().value()))
            state[key]=values
        return state

    def _restore_page_state(self,page,state):
        fields=((QLineEdit,"line_edits"),(QTextEdit,"text_edits"),(QPlainTextEdit,"plain_text_edits"),
                (QComboBox,"combos"),(QSpinBox,"spin_boxes"),(QDoubleSpinBox,"double_spin_boxes"),
                (QDateEdit,"date_edits"),(QCheckBox,"checks"),(QTableWidget,"tables"),(QScrollArea,"scroll_areas"))
        for widget_type,key in fields:
            for widget,value in zip(page.findChildren(widget_type),state.get(key,[])):
                signals=widget.blockSignals(True)
                try:
                    if widget_type is QLineEdit:
                        text,cursor=value; widget.setText(text); widget.setCursorPosition(min(cursor,len(text)))
                    elif widget_type in {QTextEdit,QPlainTextEdit}:
                        text,cursor=value; widget.setPlainText(text)
                        text_cursor=widget.textCursor(); text_cursor.setPosition(min(cursor,len(text))); widget.setTextCursor(text_cursor)
                    elif widget_type is QComboBox:
                        index,data,text,editable=value
                        if 0<=index<widget.count(): widget.setCurrentIndex(index)
                        if data is not None:
                            data_index=widget.findData(data)
                            if data_index>=0: widget.setCurrentIndex(data_index)
                        elif text:
                            text_index=widget.findText(text)
                            if text_index>=0: widget.setCurrentIndex(text_index)
                        if editable: widget.setEditText(text)
                    elif widget_type in {QSpinBox,QDoubleSpinBox}:
                        widget.setValue(value)
                    elif widget_type is QDateEdit:
                        widget.setDate(value)
                    elif widget_type is QCheckBox:
                        widget.setChecked(value)
                    elif widget_type is QTableWidget:
                        row,column,selected=value
                        if row>=0 and column>=0 and row<widget.rowCount() and column<widget.columnCount(): widget.setCurrentCell(row,column)
                        else: widget.clearSelection()
                        for selected_row in selected:
                            if 0<=selected_row<widget.rowCount(): widget.selectRow(selected_row)
                    elif widget_type is QScrollArea:
                        vertical,horizontal=value
                        widget.verticalScrollBar().setValue(vertical); widget.horizontalScrollBar().setValue(horizontal)
                finally:
                    widget.blockSignals(signals)

    def refresh_appearance(self):
        pending=dict(getattr(self,"_pending_theme_states",{}))
        for name,page in self.pages.items():
            pending[name]=self._capture_page_state(page)
        self._pending_theme_states=pending
        self.palette=palette_for(self.store, self.theme)
        stylesheet=themed_style(self.palette)
        self.setStyleSheet(stylesheet)
        QApplication.instance().setStyleSheet(stylesheet)
        self.sidebar.setStyleSheet(f"background:{self.palette['sidebar']};border-right:1px solid {self.palette['border']};")
        self.topbar.setStyleSheet(f"background:{self.palette['sidebar']};border-bottom:1px solid {self.palette['border']};")
        self.theme_btn.setText("☀ Light mode" if self.theme == "dark" else "☾ Dark mode")
        self.update_branding()
        active=self.active
        self.rebuild_nav()
        for page in list(self.pages.values()):
            self.stack.removeWidget(page)
            page.deleteLater()
        self.pages.clear()
        self.show_page(active)
        self.refresh_syncpill()

    def toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        self.store.set_setting("theme", self.theme)
        self.refresh_appearance()
    def integrations(self):
        if not self._authorize('configure'):
            return
        w,v=self.page_container("Integrations","Connect external systems only when credentials and secure endpoints are configured.")
        for title,desc in [("Payments","Payment gateway integration and reconciliation"),("Email & notifications","Email delivery, reminders and alert routing"),("Accounting & tax","Country-specific accounting and compliance connectors"),("Commerce & POS","Sales channels, point of sale and e-commerce"),("Cloud sync","Multi-device synchronization and central reporting")]:
            p=self.panel(); l=QHBoxLayout(p); l.setContentsMargins(16,12,16,12); l.addWidget(QLabel(f"<span style='color:{self.palette['cyan']}'>◇</span>  <b>{title}</b>")); l.addStretch(); l.addWidget(QLabel(f"<span style='color:{self.palette['muted']}'>{desc}</span>")); v.addWidget(p)
        v.addStretch(); return w
    def database_page(self):
        if not self._authorize('backup'):
            return
        w,v=self.page_container("Database & Backup","Local-first storage with explicit backup controls.")
        p=self.panel(); f=QVBoxLayout(p); f.setContentsMargins(20,20,20,20)
        vals=[("Database path",str(DB_PATH)),("Stored records",str(self.store.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0])),("Queued changes",str(len(self.store.pending()))),("Enabled modules",str(len(self.store.features(True))))]
        for k,val in vals:
            f.addWidget(QLabel(f"<span style='color:{self.palette['muted']};font-size:8pt'>{k.upper()}</span>")); lab=QLabel(val); lab.setWordWrap(True); f.addWidget(lab)
        v.addWidget(p)
        actions=QHBoxLayout()
        b=QPushButton("Create database backup"); b.setObjectName("primary"); b.clicked.connect(self.backup); actions.addWidget(b)
        restore=QPushButton("Restore from backup"); restore.clicked.connect(self.restore_backup); actions.addWidget(restore)
        actions.addStretch(); v.addLayout(actions); v.addStretch(); return w
    def backup(self):
        if not self._authorize('backup'):
            return
        default_path = APP_DIR / f"nextgen_erp_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        path, _ = QFileDialog.getSaveFileName(self, "Save database backup", str(default_path), "SQLite database (*.db)")
        if not path:
            return
        if Path(path).resolve() == Path(DB_PATH).resolve():
            QMessageBox.warning(self,"Invalid backup destination","Choose a different file from the active database.")
            return
        snapshot = None
        try:
            snapshot = sqlite3.connect(path)
            self.store.conn.backup(snapshot)
            snapshot.close()
            snapshot = None
            self.store.log(self.username, "Database backup", path)
            QMessageBox.information(self, "Backup complete", "A consistent database snapshot was saved to: " + path)
        except Exception as e:
            if snapshot is not None:
                snapshot.close()
            QMessageBox.critical(self, "Backup failed", str(e))
    def restore_backup(self):
        if not self._authorize("backup"): return
        path,_=QFileDialog.getOpenFileName(self,"Choose a NextGen ERP backup",str(APP_DIR),"SQLite database (*.db)")
        if not path: return
        answer=QMessageBox.question(self,"Restore database backup",
            "The selected backup will replace the current database. A safety copy of the current database will be created first. Continue?",
            QMessageBox.Yes|QMessageBox.No,QMessageBox.No)
        if answer != QMessageBox.Yes: return
        try:
            safety_path=self.store.restore_backup(path)
            self.store.log(self.username,"Restored database backup",path)
            QMessageBox.information(self,"Restore complete",
                f"The backup was restored. Your previous database is preserved at:\\n{safety_path}\\n\\nSign in again to continue.")
            self.sign_out()
        except (ValueError,sqlite3.Error,RuntimeError) as exc:
            QMessageBox.critical(self,"Restore failed",f"The live database was not replaced.\\n\\n{exc}")

    def sync_now(self):
        if not self._authorize('sync'):
            return
        endpoint=self.store.setting("sync_url","").strip(); pending=self.store.pending()
        if not pending: QMessageBox.information(self,"Sync status","No pending changes."); return
        if not endpoint: QMessageBox.information(self,"Offline-first mode",f"{len(pending)} change(s) remain safely queued locally.\n\nConfigure your trusted HTTPS sync server in System Settings when it is available."); return
        if not endpoint.lower().startswith("https://"): QMessageBox.warning(self,"Unsafe endpoint","Sync requires HTTPS."); return
        payload={"device_id":self.store.setting("device_name","Desktop"),"changes":[{"queue_id":r["id"],"entity":r["entity"],"entity_id":r["entity_id"],"operation":r["operation"],"payload":json.loads(r["payload_json"]),"queued_at":r["queued_at"]} for r in pending]}
        def finish_success(result):
            accepted=result.get("accepted_ids",[])
            if not isinstance(accepted,list):
                QMessageBox.warning(self,"Sync response","The server response did not contain a valid accepted_ids list. Changes remain queued.")
                return
            try:
                self.store.conn.executemany("DELETE FROM sync_queue WHERE id=?",[(int(i),) for i in accepted])
                self.store.conn.commit()
                self.store.log(self.username,"Sync attempt",f"Server acknowledged {len(accepted)} changes")
                QMessageBox.information(self,"Sync response",f"Server acknowledged {len(accepted)} of {len(pending)} queued changes.")
                self.refresh_syncpill()
            except Exception as e:
                QMessageBox.warning(self,"Sync update failed",f"The response was received but local queue update failed. Keep your backup.\n\n{str(e)[:300]}")
        def finish_error(error_text):
            QMessageBox.warning(self,"Sync failed",f"Changes remain stored locally.\n\n{error_text[:400]}")
        def worker():
            try:
                req=urllib.request.Request(endpoint,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json","User-Agent":"NextGenERP-Desktop/0.2"},method="POST")
                with urllib.request.urlopen(req,timeout=12) as resp: result=json.loads(resp.read().decode())
                self._ui(lambda result=result: finish_success(result))
            except Exception as e:
                self._ui(lambda error_text=str(e): finish_error(error_text))
        threading.Thread(target=worker,daemon=True).start()
    def _ui(self,fn): QTimer.singleShot(0,fn)
    def search_records(self):
        q=self.search.text().strip().lower()
        if not q: return
        allowed_modules={r["name"] for r in self.store.features(True) if self.store.role_has_module(self.role,r["name"])}
        rows=[r for r in self.store.records(limit=1000) if r["module"] in allowed_modules and (q in r["module"].lower() or q in r["data_json"].lower() or q in str(r["id"]))]
        w,v=self.page_container("Search results",f"{len(rows)} local records match “{self.search.text().strip()}”.")
        table=QTableWidget(); table.setColumnCount(4); table.setHorizontalHeaderLabels(["ID","MODULE","DETAILS","CREATED"]); table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setRowCount(len(rows))
        for i,r in enumerate(rows):
            vals=[r["id"],r["module"],r["data_json"][:200],r["created_at"]]
            for j,val in enumerate(vals): table.setItem(i,j,QTableWidgetItem(str(val)))
        v.addWidget(table,1)
        self.pages["Search results"]=w; self.stack.addWidget(w); self.stack.setCurrentWidget(w)
    def sign_out(self):
        self.store.log(self.username,"Sign out","Desktop session ended"); self.close()
        self.login_again()
    def login_again(self):
        if self.store.needs_initial_setup():
            setup=InitialSetupDialog(self.store)
            if not setup.exec():
                QApplication.quit()
                return
        self.login_dlg=LoginDialog(self.store)
        if self.login_dlg.exec():
            self.username=self.login_dlg.username; self.role=self.login_dlg.role; self.store.actor_role=self.role; self.store.operations.actor_role=self.role; self.store.operations.actor_username=self.username
            self.pages.clear(); self.rebuild_nav(); self.show_page("Dashboard")
            self.show()
        else: QApplication.quit()

def main():
    app=QApplication(sys.argv); app.setApplicationName(APP_NAME); app.setStyle("Fusion")
    store=Store()
    app.setStyleSheet(themed_style(palette_for(store)))
    if store.needs_initial_setup():
        setup = InitialSetupDialog(store)
        if not setup.exec():
            store.conn.close()
            return
    login=LoginDialog(store)
    if login.exec():
        store.log(login.username,"Sign in","Desktop session opened")
        window=App(store,login.username,login.role); window.show()
        sys.exit(app.exec())
    store.conn.close()
if __name__=="__main__": main()





