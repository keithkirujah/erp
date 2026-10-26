"""Transactional offline operations for stock, sales returns, purchasing, and cash movements."""
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import sqlite3
import json
from datetime import datetime


class OperationError(ValueError):
    """A business rule or validation error suitable for showing to an operator."""


class OperationsService:
    SCHEMA_VERSION = 9

    def __init__(self, connection):
        self.conn = connection
        self.actor_role = "Admin"
        self.actor_username = "system"
        self.permission_provider = lambda role: {"view", "create", "edit", "delete", "export", "print", "configure", "manage_users", "backup", "sync", "manage_inventory", "manage_finance", "process_returns"} if role == "Admin" else set()
        self.module_access_provider = lambda role, module: True
        self.tax_provider = lambda: {"label":"Tax","rate":Decimal("0.00")}
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.initialize()

    def _require(self, capability):
        if capability not in self.permission_provider(self.actor_role):
            raise OperationError(f"The {self.actor_role} role is not allowed to {capability.replace('_', ' ')}.")

    def _require_module(self, module):
        if not self.module_access_provider(self.actor_role,module):
            raise OperationError(f"The {self.actor_role} role cannot access the {module} module.")

    def _require_any_module(self, modules):
        if not any(self.module_access_provider(self.actor_role,module) for module in modules):
            names=" or ".join(modules)
            raise OperationError(f"The {self.actor_role} role has no access to {names}.")

    def initialize(self):
        version=self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version > self.SCHEMA_VERSION:
            raise RuntimeError(f"Database version {version} is newer than this application.")
        with self.conn:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS customers(
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    contact TEXT DEFAULT '',
                    email TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS suppliers(
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    contact TEXT DEFAULT '',
                    email TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS products(
                    id INTEGER PRIMARY KEY,
                    sku TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    name TEXT NOT NULL,
                    unit TEXT NOT NULL DEFAULT 'each',
                    category TEXT NOT NULL DEFAULT '',
                    barcode TEXT NOT NULL DEFAULT '',
                    selling_price TEXT NOT NULL,
                    cost_price TEXT NOT NULL,
                    reorder_level TEXT NOT NULL DEFAULT '0',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stock_movements(
                    id INTEGER PRIMARY KEY,
                    product_id INTEGER NOT NULL REFERENCES products(id),
                    quantity_delta TEXT NOT NULL,
                    movement_type TEXT NOT NULL,
                    reference_type TEXT NOT NULL DEFAULT '',
                    reference_id INTEGER,
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sales(
                    id INTEGER PRIMARY KEY,
                    sale_number TEXT NOT NULL UNIQUE,
                    customer_id INTEGER REFERENCES customers(id),
                    customer_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    payment_status TEXT NOT NULL,
                    total TEXT NOT NULL,
                    paid_total TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    cancelled_at TEXT,
                    cancellation_reason TEXT DEFAULT '',
                    quote_id INTEGER,
                    subtotal TEXT NOT NULL DEFAULT '0.00',
                    tax_rate TEXT NOT NULL DEFAULT '0.00',
                    tax_total TEXT NOT NULL DEFAULT '0.00',
                    tax_label TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS quotes(
                    id INTEGER PRIMARY KEY,
                    quote_number TEXT NOT NULL UNIQUE,
                    customer_id INTEGER REFERENCES customers(id),
                    customer_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    total TEXT NOT NULL,
                    expires_on TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    converted_sale_id INTEGER,
                    created_by TEXT NOT NULL DEFAULT '',
                    subtotal TEXT NOT NULL DEFAULT '0.00',
                    tax_rate TEXT NOT NULL DEFAULT '0.00',
                    tax_total TEXT NOT NULL DEFAULT '0.00',
                    tax_label TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS quote_lines(
                    id INTEGER PRIMARY KEY,
                    quote_id INTEGER NOT NULL REFERENCES quotes(id),
                    product_id INTEGER NOT NULL REFERENCES products(id),
                    sku TEXT NOT NULL,
                    description TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    unit_price TEXT NOT NULL,
                    line_total TEXT NOT NULL,
                    tax_amount TEXT NOT NULL DEFAULT '0.00'
                );
                CREATE TABLE IF NOT EXISTS sale_lines(
                    id INTEGER PRIMARY KEY,
                    sale_id INTEGER NOT NULL REFERENCES sales(id),
                    product_id INTEGER NOT NULL REFERENCES products(id),
                    sku TEXT NOT NULL,
                    description TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    unit_price TEXT NOT NULL,
                    line_total TEXT NOT NULL,
                    tax_amount TEXT NOT NULL DEFAULT '0.00'
                );
                CREATE TABLE IF NOT EXISTS purchases(
                    id INTEGER PRIMARY KEY,
                    purchase_number TEXT NOT NULL UNIQUE,
                    supplier_id INTEGER REFERENCES suppliers(id),
                    supplier_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    payment_status TEXT NOT NULL,
                    total TEXT NOT NULL,
                    paid_total TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    cancelled_at TEXT,
                    cancellation_reason TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS purchase_lines(
                    id INTEGER PRIMARY KEY,
                    purchase_id INTEGER NOT NULL REFERENCES purchases(id),
                    product_id INTEGER NOT NULL REFERENCES products(id),
                    sku TEXT NOT NULL,
                    description TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    unit_cost TEXT NOT NULL,
                    line_total TEXT NOT NULL,
                    purchase_order_line_id INTEGER,
                    prior_cost TEXT
                );
                CREATE TABLE IF NOT EXISTS purchase_orders(
                    id INTEGER PRIMARY KEY,
                    order_number TEXT NOT NULL UNIQUE,
                    supplier_id INTEGER REFERENCES suppliers(id),
                    supplier_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    cancelled_at TEXT,
                    cancellation_reason TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS purchase_order_lines(
                    id INTEGER PRIMARY KEY,
                    purchase_order_id INTEGER NOT NULL REFERENCES purchase_orders(id),
                    product_id INTEGER NOT NULL REFERENCES products(id),
                    sku TEXT NOT NULL,
                    description TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    unit_cost TEXT NOT NULL,
                    received_quantity TEXT NOT NULL DEFAULT '0'
                );
                CREATE TABLE IF NOT EXISTS cash_entries(
                    id INTEGER PRIMARY KEY,
                    direction TEXT NOT NULL CHECK(direction IN ('in','out')),
                    amount TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id INTEGER NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_stock_movements_product ON stock_movements(product_id,id);
                CREATE INDEX IF NOT EXISTS idx_sales_created ON sales(created_at,id);
                CREATE INDEX IF NOT EXISTS idx_purchases_created ON purchases(created_at,id);
                CREATE INDEX IF NOT EXISTS idx_cash_entries_created ON cash_entries(created_at,id);
                CREATE TABLE IF NOT EXISTS sales_returns(
                    id INTEGER PRIMARY KEY,
                    return_number TEXT NOT NULL UNIQUE,
                    sale_id INTEGER NOT NULL REFERENCES sales(id),
                    refund_amount TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS sale_return_lines(
                    id INTEGER PRIMARY KEY,
                    return_id INTEGER NOT NULL REFERENCES sales_returns(id),
                    sale_line_id INTEGER NOT NULL REFERENCES sale_lines(id),
                    product_id INTEGER NOT NULL REFERENCES products(id),
                    sku TEXT NOT NULL,
                    description TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    restocked INTEGER NOT NULL CHECK(restocked IN (0,1)),
                    unit_price TEXT NOT NULL,
                    line_total TEXT NOT NULL,
                    tax_amount TEXT NOT NULL DEFAULT '0.00'
                );
                CREATE INDEX IF NOT EXISTS idx_sales_returns_sale ON sales_returns(sale_id,id);
                CREATE INDEX IF NOT EXISTS idx_sale_return_lines_line ON sale_return_lines(sale_line_id,id);
                CREATE TABLE IF NOT EXISTS repair_jobs(
                    id INTEGER PRIMARY KEY,
                    ticket_number TEXT NOT NULL UNIQUE,
                    customer_id INTEGER REFERENCES customers(id),
                    customer_name TEXT NOT NULL,
                    device TEXT NOT NULL,
                    serial_number TEXT NOT NULL DEFAULT '',
                    reported_fault TEXT NOT NULL,
                    diagnosis TEXT NOT NULL DEFAULT '',
                    warranty_notes TEXT NOT NULL DEFAULT '',
                    technician TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    approval_status TEXT NOT NULL DEFAULT 'Pending',
                    estimate_amount TEXT NOT NULL DEFAULT '0.00',
                    labor_amount TEXT NOT NULL DEFAULT '0.00',
                    subtotal TEXT NOT NULL DEFAULT '0.00',
                    tax_label TEXT NOT NULL DEFAULT '',
                    tax_rate TEXT NOT NULL DEFAULT '0.00',
                    tax_total TEXT NOT NULL DEFAULT '0.00',
                    total TEXT NOT NULL DEFAULT '0.00',
                    paid_total TEXT NOT NULL DEFAULT '0.00',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS repair_job_parts(
                    id INTEGER PRIMARY KEY,
                    job_id INTEGER NOT NULL REFERENCES repair_jobs(id),
                    product_id INTEGER NOT NULL REFERENCES products(id),
                    sku TEXT NOT NULL,
                    description TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    unit_price TEXT NOT NULL,
                    line_total TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS repair_job_payments(
                    id INTEGER PRIMARY KEY,
                    job_id INTEGER NOT NULL REFERENCES repair_jobs(id),
                    amount TEXT NOT NULL,
                    reference TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS repair_job_events(
                    id INTEGER PRIMARY KEY,
                    job_id INTEGER NOT NULL REFERENCES repair_jobs(id),
                    event TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '',
                    actor TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_repair_jobs_created ON repair_jobs(created_at,id);
                CREATE INDEX IF NOT EXISTS idx_repair_jobs_customer ON repair_jobs(customer_id,id);
                CREATE INDEX IF NOT EXISTS idx_repair_parts_job ON repair_job_parts(job_id,id);
                CREATE INDEX IF NOT EXISTS idx_repair_events_job ON repair_job_events(job_id,id);
            """)
            product_columns={row[1] for row in self.conn.execute("PRAGMA table_info(products)").fetchall()}
            if "category" not in product_columns:
                self.conn.execute("ALTER TABLE products ADD COLUMN category TEXT NOT NULL DEFAULT ''")
            if "barcode" not in product_columns:
                self.conn.execute("ALTER TABLE products ADD COLUMN barcode TEXT NOT NULL DEFAULT ''")
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode COLLATE NOCASE) WHERE barcode <> ''")
            return_columns={row[1] for row in self.conn.execute("PRAGMA table_info(sales_returns)").fetchall()}
            if "created_by" not in return_columns:
                self.conn.execute("ALTER TABLE sales_returns ADD COLUMN created_by TEXT NOT NULL DEFAULT ''")
            sale_columns={row[1] for row in self.conn.execute("PRAGMA table_info(sales)").fetchall()}
            if "quote_id" not in sale_columns:
                self.conn.execute("ALTER TABLE sales ADD COLUMN quote_id INTEGER")
            for table, additions in {
                "sales": ("subtotal TEXT NOT NULL DEFAULT '0.00'", "tax_rate TEXT NOT NULL DEFAULT '0.00'", "tax_total TEXT NOT NULL DEFAULT '0.00'", "tax_label TEXT NOT NULL DEFAULT ''"),
                "quotes": ("subtotal TEXT NOT NULL DEFAULT '0.00'", "tax_rate TEXT NOT NULL DEFAULT '0.00'", "tax_total TEXT NOT NULL DEFAULT '0.00'", "tax_label TEXT NOT NULL DEFAULT ''"),
                "sale_lines": ("tax_amount TEXT NOT NULL DEFAULT '0.00'",),
                "quote_lines": ("tax_amount TEXT NOT NULL DEFAULT '0.00'",),
                "sale_return_lines": ("tax_amount TEXT NOT NULL DEFAULT '0.00'",),
            }.items():
                existing={row[1] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
                for declaration in additions:
                    column=declaration.split()[0]
                    if column not in existing:
                        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {declaration}")
            self.conn.execute("UPDATE sales SET subtotal=total WHERE subtotal='0.00' AND CAST(total AS REAL)<>0")
            self.conn.execute("UPDATE quotes SET subtotal=total WHERE subtotal='0.00' AND CAST(total AS REAL)<>0")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_quotes_created ON quotes(created_at,id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_quote_lines_quote ON quote_lines(quote_id,id)")
            purchase_columns={row[1] for row in self.conn.execute("PRAGMA table_info(purchases)").fetchall()}
            if "order_id" not in purchase_columns:
                self.conn.execute("ALTER TABLE purchases ADD COLUMN order_id INTEGER")
            purchase_line_columns={row[1] for row in self.conn.execute("PRAGMA table_info(purchase_lines)").fetchall()}
            if "purchase_order_line_id" not in purchase_line_columns:
                self.conn.execute("ALTER TABLE purchase_lines ADD COLUMN purchase_order_line_id INTEGER")
            if "prior_cost" not in purchase_line_columns:
                self.conn.execute("ALTER TABLE purchase_lines ADD COLUMN prior_cost TEXT")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_purchase_orders_created ON purchase_orders(created_at,id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_purchase_order_lines_order ON purchase_order_lines(purchase_order_id,id)")
            self.conn.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")

    def _enqueue(self, entity, entity_id, payload, timestamp):
        self.conn.execute("INSERT INTO sync_queue(entity,entity_id,operation,payload_json,queued_at) VALUES(?,?,?,?,?)",
                          (entity,entity_id,"upsert",json.dumps(payload,ensure_ascii=False),timestamp))

    @contextmanager
    def _transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    @staticmethod
    def _decimal(value, label, *, minimum=None, places=2):
        try:
            number=Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            raise OperationError(f"{label} must be a valid number.")
        if not number.is_finite():
            raise OperationError(f"{label} must be finite.")
        quantum=Decimal("1").scaleb(-places)
        number=number.quantize(quantum, rounding=ROUND_HALF_UP)
        if minimum is not None and number < minimum:
            raise OperationError(f"{label} must be at least {minimum}.")
        return number

    def _tax_config(self):
        try:
            config=self.tax_provider() or {}
            label=str(config.get("label","Tax")).strip()[:60] or "Tax"
            rate=self._decimal(config.get("rate",0),"Tax rate",minimum=Decimal("0"))
            if rate>Decimal("100"):
                raise OperationError("Tax rate cannot exceed 100%.")
            return label,rate
        except OperationError:
            raise
        except Exception as exc:
            raise OperationError("The configured tax settings are invalid.") from exc

    @staticmethod
    def _tax_amount(amount, rate):
        return (amount*rate/Decimal("100")).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)

    @staticmethod
    def _stamp():
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @staticmethod
    def _money(value):
        return format(value, ".2f")

    @staticmethod
    def _quantity(value):
        return format(value, ".3f")

    @staticmethod
    def _next_number(prefix, row_id):
        return f"{prefix}-{row_id:08d}"

    def create_customer(self, name, contact="", email=""):
        self._require_module("CRM")
        self._require("create")
        name=str(name).strip()
        if not name or len(name)>160:
            raise OperationError("Customer name is required and must be 160 characters or fewer.")
        with self._transaction():
            timestamp=self._stamp()
            cur=self.conn.execute("INSERT INTO customers(name,contact,email,created_at) VALUES(?,?,?,?)",
                                  (name,str(contact).strip()[:100],str(email).strip()[:160],timestamp))
            self._enqueue("customers",cur.lastrowid,{"id":cur.lastrowid,"name":name,"contact":str(contact).strip()[:100],"email":str(email).strip()[:160]},timestamp)
        return cur.lastrowid

    def create_supplier(self, name, contact="", email=""):
        self._require_module("Purchases")
        self._require("manage_inventory")
        name=str(name).strip()
        if not name or len(name)>160:
            raise OperationError("Supplier name is required and must be 160 characters or fewer.")
        with self._transaction():
            timestamp=self._stamp()
            cur=self.conn.execute("INSERT INTO suppliers(name,contact,email,created_at) VALUES(?,?,?,?)",
                                  (name,str(contact).strip()[:100],str(email).strip()[:160],timestamp))
            self._enqueue("suppliers",cur.lastrowid,{"id":cur.lastrowid,"name":name,"contact":str(contact).strip()[:100],"email":str(email).strip()[:160]},timestamp)
        return cur.lastrowid

    def create_product(self, sku, name, selling_price, cost_price=0, opening_stock=0, unit="each", reorder_level=0, category="", barcode=""):

        self._require_module("Inventory")
        self._require("manage_inventory")
        sku=str(sku).strip()
        name=str(name).strip()
        if not sku or len(sku)>80 or not name or len(name)>160:
            raise OperationError("A SKU (up to 80 characters) and product name (up to 160 characters) are required.")
        selling=self._decimal(selling_price,"Selling price",minimum=Decimal("0"))
        cost=self._decimal(cost_price,"Cost price",minimum=Decimal("0"))
        opening=self._decimal(opening_stock,"Opening stock",minimum=Decimal("0"),places=3)
        reorder=self._decimal(reorder_level,"Reorder level",minimum=Decimal("0"),places=3)
        unit=str(unit).strip()[:30] or "each"
        category=str(category).strip()[:80]
        barcode=str(barcode).strip()[:128]
        if any(ord(ch)<32 for ch in barcode):
            raise OperationError("Barcode cannot contain control characters.")
        timestamp=self._stamp()
        try:
            with self._transaction():
                cur=self.conn.execute(
                    "INSERT INTO products(sku,name,unit,category,barcode,selling_price,cost_price,reorder_level,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (sku,name,unit,category,barcode,self._money(selling),self._money(cost),self._quantity(reorder),timestamp))
                product_id=cur.lastrowid
                if opening:
                    self.conn.execute(
                        "INSERT INTO stock_movements(product_id,quantity_delta,movement_type,note,created_at) VALUES(?,?,?,?,?)",
                        (product_id,self._quantity(opening),"opening","Opening balance",timestamp))
                self._enqueue("products",product_id,{"id":product_id,"sku":sku,"name":name,"unit":unit,
                    "category":category,"barcode":barcode,"selling_price":self._money(selling),"cost_price":self._money(cost),
                    "reorder_level":self._quantity(reorder),"opening_stock":self._quantity(opening)},timestamp)
        except sqlite3.IntegrityError as exc:
            raise OperationError(f"SKU {sku!r} or barcode {barcode!r} is already assigned, or the product could not be saved.") from exc
        return product_id

    def customers(self, limit=10000):
        self._require_any_module(("CRM","Sales"))
        return self.conn.execute("SELECT * FROM customers ORDER BY name COLLATE NOCASE,id LIMIT ?",(limit,)).fetchall()

    def suppliers(self, limit=10000):
        self._require_module("Purchases")
        return self.conn.execute("SELECT * FROM suppliers ORDER BY name COLLATE NOCASE,id LIMIT ?",(limit,)).fetchall()

    def update_product(self, product_id, sku, name, selling_price, cost_price, reorder_level=0, unit="each", active=True, category="", barcode=""):

        self._require_module("Inventory")
        self._require("manage_inventory")
        sku=str(sku).strip()
        name=str(name).strip()
        if not sku or len(sku)>80 or not name or len(name)>160:
            raise OperationError("A SKU (up to 80 characters) and product name (up to 160 characters) are required.")
        selling=self._decimal(selling_price,"Selling price",minimum=Decimal("0"))
        cost=self._decimal(cost_price,"Cost price",minimum=Decimal("0"))
        reorder=self._decimal(reorder_level,"Reorder level",minimum=Decimal("0"),places=3)
        unit=str(unit).strip()[:30] or "each"
        category=str(category).strip()[:80]
        barcode=str(barcode).strip()[:128]
        if any(ord(ch)<32 for ch in barcode):
            raise OperationError("Barcode cannot contain control characters.")
        timestamp=self._stamp()
        try:
            with self._transaction():
                cur=self.conn.execute("UPDATE products SET sku=?,name=?,unit=?,category=?,barcode=?,selling_price=?,cost_price=?,reorder_level=?,active=? WHERE id=?",
                                      (sku,name,unit,category,barcode,self._money(selling),self._money(cost),self._quantity(reorder),int(bool(active)),product_id))
                if not cur.rowcount:
                    raise OperationError("The selected product no longer exists.")
                self._enqueue("products",product_id,{"id":product_id,"sku":sku,"name":name,"unit":unit,
                    "category":category,"barcode":barcode,"selling_price":self._money(selling),"cost_price":self._money(cost),
                    "reorder_level":self._quantity(reorder),"active":bool(active)},timestamp)
        except sqlite3.IntegrityError as exc:
            raise OperationError(f"SKU {sku!r} or barcode {barcode!r} is already assigned, or the product could not be saved.") from exc
        return product_id

    def adjust_stock(self, product_id, quantity_delta, reason):
        self._require_module("Inventory")
        self._require("manage_inventory")
        delta=self._decimal(quantity_delta,"Stock adjustment",places=3)
        reason=str(reason).strip()[:240]
        if not delta or not reason:
            raise OperationError("Enter a non-zero stock adjustment and a reason.")
        timestamp=self._stamp()
        with self._transaction():
            product=self.conn.execute("SELECT sku,name,unit FROM products WHERE id=? AND active=1",(product_id,)).fetchone()
            if not product:
                raise OperationError("The selected product is unavailable.")
            new_balance=self.stock_on_hand(product_id)+delta
            if new_balance < 0:
                raise OperationError(f"Stock cannot fall below zero. Current quantity: {self._quantity(self.stock_on_hand(product_id))}.")
            cur=self.conn.execute("INSERT INTO stock_movements(product_id,quantity_delta,movement_type,note,created_at) VALUES(?,?,?,?,?)",
                                  (product_id,self._quantity(delta),"adjustment",reason,timestamp))
            self._enqueue("stock_movements",cur.lastrowid,{"id":cur.lastrowid,"product_id":product_id,
                "sku":product["sku"],"quantity_delta":self._quantity(delta),"reason":reason},timestamp)
        return cur.lastrowid

    def products(self, active_only=True):
        self._require_any_module(("Inventory","Sales","Purchases","Reports","Repair Tickets"))
        query="SELECT * FROM products"
        if active_only:
            query += " WHERE active=1"
        return self.conn.execute(query+" ORDER BY name COLLATE NOCASE,id").fetchall()

    def stock_on_hand(self, product_id):
        self._require_any_module(("Inventory","Sales","Purchases","Reports","Repair Tickets"))
        rows=self.conn.execute("SELECT quantity_delta FROM stock_movements WHERE product_id=? ORDER BY id",(product_id,)).fetchall()
        return sum((Decimal(row[0]) for row in rows),Decimal("0"))

    def low_stock_products(self, limit=10000):
        self._require_module("Inventory")
        try:
            limit=max(1,min(int(limit),10000))
        except (TypeError,ValueError):
            raise OperationError("The low-stock result limit must be a whole number.")
        return self.conn.execute("""
            SELECT p.*, COALESCE(SUM(CAST(m.quantity_delta AS REAL)),0) AS stock_on_hand
            FROM products p LEFT JOIN stock_movements m ON m.product_id=p.id
            WHERE p.active=1
            GROUP BY p.id
            HAVING COALESCE(SUM(CAST(m.quantity_delta AS REAL)),0) <= CAST(p.reorder_level AS REAL)
            ORDER BY stock_on_hand ASC,p.category COLLATE NOCASE,p.name COLLATE NOCASE,p.id
            LIMIT ?
        """,(limit,)).fetchall()

    def low_stock_count(self):
        self._require_module("Inventory")
        return self.conn.execute("""
            SELECT COUNT(*) FROM products p
            WHERE p.active=1 AND COALESCE(
                (SELECT SUM(CAST(m.quantity_delta AS REAL)) FROM stock_movements m WHERE m.product_id=p.id),0
            ) <= CAST(p.reorder_level AS REAL)
        """).fetchone()[0]

    def _normalize_lines(self, lines, price_field):
        if not lines:
            raise OperationError("Add at least one product line.")
        normalized=[]
        for line in lines:
            try:
                product_id=int(line["product_id"])
            except (KeyError,TypeError,ValueError):
                raise OperationError("Every line must select a valid product.")
            qty=self._decimal(line.get("quantity"),"Quantity",minimum=Decimal("0.001"),places=3)
            product=self.conn.execute("SELECT * FROM products WHERE id=? AND active=1",(product_id,)).fetchone()
            if not product:
                raise OperationError(f"Product {product_id} is unavailable.")
            default_price=product["selling_price"] if price_field=="unit_price" else product["cost_price"]
            price=self._decimal(line.get(price_field,default_price),"Unit price" if price_field=="unit_price" else "Unit cost",minimum=Decimal("0"))
            amount=(qty*price).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
            normalized.append((product,qty,price,amount))
        return normalized

    @staticmethod
    def _payment_status(total, paid):
        if paid <= 0:
            return "Unpaid"
        if paid >= total:
            return "Paid"
        return "Partial"

    def record_sale(self, customer_name, lines, amount_paid=0, customer_id=None):
        self._require_module("Sales")
        self._require("create")
        normalized=self._normalize_lines(lines,"unit_price")
        tax_label,tax_rate=self._tax_config()
        subtotal=sum((line[3] for line in normalized),Decimal("0")).quantize(Decimal("0.01"))
        tax_total=sum((self._tax_amount(line[3],tax_rate) for line in normalized),Decimal("0.00"))
        total=subtotal+tax_total
        paid=self._decimal(amount_paid,"Amount paid",minimum=Decimal("0"))
        if paid>total:
            raise OperationError("Amount paid cannot exceed the sale total including tax.")
        return self._complete_sale(normalized,customer_name,paid,customer_id,self._stamp(),tax_label=tax_label,tax_rate=tax_rate)

    def _complete_sale(self, normalized, customer_name, paid, customer_id, timestamp, quote_id=None, tax_label=None, tax_rate=None):
        with self._transaction():
            quote=None
            if quote_id is not None:
                quote=self.conn.execute("SELECT * FROM quotes WHERE id=?",(int(quote_id),)).fetchone()
                if not quote or quote["status"]!="Accepted" or quote["converted_sale_id"] is not None:
                    raise OperationError("Only an accepted, unconverted quote can be invoiced.")
                customer_id=quote["customer_id"]
                customer_name=quote["customer_name"]
                tax_label=quote["tax_label"]
                tax_rate=Decimal(quote["tax_rate"])
            if tax_rate is None:
                tax_label,tax_rate=self._tax_config()
            else:
                tax_rate=self._decimal(tax_rate,"Tax rate",minimum=Decimal("0"))
                tax_label=str(tax_label or "Tax").strip()[:60] or "Tax"
            if tax_rate>Decimal("100"):
                raise OperationError("Tax rate cannot exceed 100%.")
            line_taxes=[self._tax_amount(line[3],tax_rate) for line in normalized]
            subtotal=sum((line[3] for line in normalized),Decimal("0")).quantize(Decimal("0.01"))
            tax_total=sum(line_taxes,Decimal("0.00"))
            total=subtotal+tax_total
            if paid>total:
                raise OperationError("Amount paid cannot exceed the sale total including tax.")
            if customer_id is not None:
                customer=self.conn.execute("SELECT name FROM customers WHERE id=?",(int(customer_id),)).fetchone()
                if not customer:
                    raise OperationError("The selected customer no longer exists.")
                customer_name=customer["name"]
            customer_name=str(customer_name or "").strip()[:160]
            required={}
            products_by_id={}
            for product,qty,price,amount in normalized:
                required[product["id"]]=required.get(product["id"],Decimal("0"))+qty
                products_by_id[product["id"]]=product
            for product_id,needed in required.items():
                product=products_by_id[product_id]
                available=self.stock_on_hand(product_id)
                if available<needed:
                    raise OperationError(f"Not enough stock for {product['name']} ({product['sku']}). Available: {self._quantity(available)} {product['unit']}.")
            cur=self.conn.execute(
                "INSERT INTO sales(sale_number,customer_id,customer_name,status,payment_status,total,paid_total,created_at,quote_id,subtotal,tax_rate,tax_total,tax_label) VALUES('',?,?,?,?,?,?,?,?,?,?,?,?)",
                (customer_id,customer_name,"Completed",self._payment_status(total,paid),self._money(total),self._money(paid),timestamp,quote_id,self._money(subtotal),self._money(tax_rate),self._money(tax_total),tax_label))
            sale_id=cur.lastrowid
            sale_number=self._next_number("SALE",sale_id)
            self.conn.execute("UPDATE sales SET sale_number=? WHERE id=?",(sale_number,sale_id))
            for (product,qty,price,amount),tax_amount in zip(normalized,line_taxes):
                self.conn.execute("INSERT INTO sale_lines(sale_id,product_id,sku,description,quantity,unit_price,line_total,tax_amount) VALUES(?,?,?,?,?,?,?,?)",
                    (sale_id,product["id"],product["sku"],product["name"],self._quantity(qty),self._money(price),self._money(amount),self._money(tax_amount)))
                self.conn.execute("INSERT INTO stock_movements(product_id,quantity_delta,movement_type,reference_type,reference_id,note,created_at) VALUES(?,?,?,?,?,?,?)",
                    (product["id"],self._quantity(-qty),"sale","sale",sale_id,sale_number,timestamp))
            if paid:
                self.conn.execute("INSERT INTO cash_entries(direction,amount,source_type,source_id,description,created_at) VALUES('in',?,?,?,?,?)",
                    (self._money(paid),"sale",sale_id,f"Payment received for {sale_number}",timestamp))
            self._enqueue("sales",sale_id,{"id":sale_id,"sale_number":sale_number,"customer_name":customer_name,
                "status":"Completed","subtotal":self._money(subtotal),"tax_label":tax_label,"tax_rate":self._money(tax_rate),
                "tax_total":self._money(tax_total),"total":self._money(total),"paid_total":self._money(paid),"quote_id":quote_id,
                "lines":[{"product_id":p["id"],"sku":p["sku"],"quantity":self._quantity(q),"unit_price":self._money(price),"line_total":self._money(amount),"tax_amount":self._money(tax)} for (p,q,price,amount),tax in zip(normalized,line_taxes)]},timestamp)
            if quote is not None:
                self.conn.execute("UPDATE quotes SET status='Converted',converted_sale_id=?,updated_at=? WHERE id=?",(sale_id,timestamp,int(quote_id)))
                self._enqueue("quotes",int(quote_id),{"id":int(quote_id),"quote_number":quote["quote_number"],"status":"Converted","sale_id":sale_id},timestamp)
        return sale_id

    def create_quote(self, customer_name, lines, customer_id=None, expires_on=""):
        self._require_module("Sales")
        self._require("create")
        normalized=self._normalize_lines(lines,"unit_price")
        tax_label,tax_rate=self._tax_config()
        subtotal=sum((line[3] for line in normalized),Decimal("0")).quantize(Decimal("0.01"))
        line_taxes=[self._tax_amount(line[3],tax_rate) for line in normalized]
        tax_total=sum(line_taxes,Decimal("0.00"))
        total=subtotal+tax_total
        timestamp=self._stamp()
        with self._transaction():
            if customer_id is not None:
                customer=self.conn.execute("SELECT name FROM customers WHERE id=?",(int(customer_id),)).fetchone()
                if not customer: raise OperationError("The selected customer no longer exists.")
                customer_name=customer["name"]
            customer_name=str(customer_name or "").strip()[:160]
            if not customer_name: raise OperationError("Enter a customer name before saving the quotation.")
            expiry=str(expires_on or "").strip()
            if expiry:
                try: datetime.strptime(expiry,"%Y-%m-%d")
                except ValueError: raise OperationError("Quote expiry must use YYYY-MM-DD format.")
            cur=self.conn.execute("INSERT INTO quotes(quote_number,customer_id,customer_name,status,total,expires_on,created_at,updated_at,created_by,subtotal,tax_rate,tax_total,tax_label) VALUES('',?,?, 'Draft',?,?,?,?,?,?,?,?,?)",
                (customer_id,customer_name,self._money(total),expiry,timestamp,timestamp,self.actor_username,self._money(subtotal),self._money(tax_rate),self._money(tax_total),tax_label))
            quote_id=cur.lastrowid
            quote_number=self._next_number("QUO",quote_id)
            self.conn.execute("UPDATE quotes SET quote_number=? WHERE id=?",(quote_number,quote_id))
            for (product,qty,price,amount),tax_amount in zip(normalized,line_taxes):
                self.conn.execute("INSERT INTO quote_lines(quote_id,product_id,sku,description,quantity,unit_price,line_total,tax_amount) VALUES(?,?,?,?,?,?,?,?)",
                    (quote_id,product["id"],product["sku"],product["name"],self._quantity(qty),self._money(price),self._money(amount),self._money(tax_amount)))
            self._enqueue("quotes",quote_id,{"id":quote_id,"quote_number":quote_number,"customer_name":customer_name,
                "status":"Draft","subtotal":self._money(subtotal),"tax_label":tax_label,"tax_rate":self._money(tax_rate),"tax_total":self._money(tax_total),"total":self._money(total),"expires_on":expiry},timestamp)
        return quote_id

    def quotes(self, limit=500):
        self._require_module("Sales")
        return self.conn.execute("SELECT * FROM quotes ORDER BY id DESC LIMIT ?",(limit,)).fetchall()

    def quote_lines(self, quote_id):
        self._require_module("Sales")
        return self.conn.execute("SELECT * FROM quote_lines WHERE quote_id=? ORDER BY id",(int(quote_id),)).fetchall()

    def set_quote_status(self, quote_id, status):
        self._require_module("Sales")
        if status not in {"Sent","Accepted","Expired","Cancelled"}:
            raise OperationError("Unsupported quote status.")
        self._require("delete" if status=="Cancelled" else "edit")
        timestamp=self._stamp()
        with self._transaction():
            quote=self.conn.execute("SELECT * FROM quotes WHERE id=?",(int(quote_id),)).fetchone()
            if not quote or quote["status"] not in {"Draft","Sent","Accepted"}:
                raise OperationError("This quotation is no longer available for status changes.")
            if quote["status"]=="Draft" and status=="Sent":
                pass
            elif quote["status"] in {"Draft","Sent"} and status in {"Accepted","Expired","Cancelled"}:
                pass
            elif quote["status"]=="Accepted" and status=="Cancelled":
                pass
            else:
                raise OperationError(f"A quotation cannot move from {quote['status']} to {status}.")
            if status=="Accepted" and quote["expires_on"] and quote["expires_on"]<timestamp[:10]:
                raise OperationError("This quotation has expired and cannot be accepted.")
            self.conn.execute("UPDATE quotes SET status=?,updated_at=? WHERE id=?",(status,timestamp,int(quote_id)))
            self._enqueue("quotes",int(quote_id),{"id":int(quote_id),"quote_number":quote["quote_number"],"status":status},timestamp)
        return True

    def convert_quote(self, quote_id, amount_paid=0):
        self._require_module("Sales")
        self._require("create")
        quote=self.conn.execute("SELECT * FROM quotes WHERE id=?",(int(quote_id),)).fetchone()
        if not quote or quote["status"]!="Accepted" or quote["converted_sale_id"] is not None:
            raise OperationError("Only an accepted, unconverted quotation can be invoiced.")
        normalized=[]
        for line in self.quote_lines(quote_id):
            product=self.conn.execute("SELECT * FROM products WHERE id=? AND active=1",(line["product_id"],)).fetchone()
            if not product:
                raise OperationError(f"Quoted product {line['sku']} is no longer active.")
            snapshot=dict(product); snapshot["sku"]=line["sku"]; snapshot["name"]=line["description"]
            qty=Decimal(line["quantity"]); price=Decimal(line["unit_price"]); amount=(qty*price).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
            normalized.append((snapshot,qty,price,amount))
        total=Decimal(quote["total"])
        paid=self._decimal(amount_paid,"Amount paid",minimum=Decimal("0"))
        if paid>total: raise OperationError("Amount paid cannot exceed the quotation total.")
        return self._complete_sale(normalized,quote["customer_name"],paid,quote["customer_id"],self._stamp(),int(quote_id),quote["tax_label"],Decimal(quote["tax_rate"]))

    def create_purchase_order(self, supplier_name, lines, supplier_id=None):
        self._require_module("Purchases")
        self._require("manage_inventory")
        normalized=self._normalize_lines(lines,"unit_cost")
        timestamp=self._stamp()
        with self._transaction():
            if supplier_id is not None:
                supplier=self.conn.execute("SELECT name FROM suppliers WHERE id=?",(int(supplier_id),)).fetchone()
                if not supplier:
                    raise OperationError("The selected supplier no longer exists.")
                supplier_name=supplier["name"]
            supplier_name=str(supplier_name or "").strip()[:160]
            if not supplier_name:
                raise OperationError("Enter a supplier name before saving the purchase order.")
            cur=self.conn.execute("INSERT INTO purchase_orders(order_number,supplier_id,supplier_name,status,created_at,updated_at) VALUES('',?,?, 'Ordered',?,?)",
                (supplier_id,supplier_name,timestamp,timestamp))
            order_id=cur.lastrowid
            order_number=self._next_number("PO",order_id)
            self.conn.execute("UPDATE purchase_orders SET order_number=? WHERE id=?",(order_number,order_id))
            order_lines=[]
            for product,qty,cost,_amount in normalized:
                line_id=self.conn.execute("INSERT INTO purchase_order_lines(purchase_order_id,product_id,sku,description,quantity,unit_cost,received_quantity) VALUES(?,?,?,?,?,?,'0')",
                    (order_id,product["id"],product["sku"],product["name"],self._quantity(qty),self._money(cost))).lastrowid
                order_lines.append({"id":line_id,"product_id":product["id"],"sku":product["sku"],"quantity":self._quantity(qty),"unit_cost":self._money(cost)})
            self._enqueue("purchase_orders",order_id,{"id":order_id,"order_number":order_number,"supplier_name":supplier_name,"status":"Ordered","lines":order_lines},timestamp)
        return order_id

    def purchase_orders(self, limit=500):
        self._require_module("Purchases")
        return self.conn.execute("SELECT * FROM purchase_orders ORDER BY id DESC LIMIT ?",(limit,)).fetchall()

    def purchase_order_lines(self, order_id):
        self._require_module("Purchases")
        return self.conn.execute("SELECT * FROM purchase_order_lines WHERE purchase_order_id=? ORDER BY id",(int(order_id),)).fetchall()

    def receive_purchase_order(self, order_id, lines, amount_paid=0):
        self._require_module("Purchases")
        self._require("manage_inventory")
        if not isinstance(lines,(list,tuple)) or not lines:
            raise OperationError("Select at least one outstanding order line to receive.")
        timestamp=self._stamp()
        with self._transaction():
            order=self.conn.execute("SELECT * FROM purchase_orders WHERE id=?",(int(order_id),)).fetchone()
            if not order or order["status"] not in {"Ordered","Partially received"}:
                raise OperationError("Only open purchase orders can receive goods.")
            source={row["id"]:row for row in self.purchase_order_lines(order_id)}
            requested={}
            for item in lines:
                try:
                    line_id=int(item["purchase_order_line_id"])
                except (KeyError,TypeError,ValueError):
                    raise OperationError("Each received quantity must refer to an order line.")
                if line_id not in source:
                    raise OperationError("A selected line does not belong to this purchase order.")
                qty=self._decimal(item.get("quantity"),"Received quantity",minimum=Decimal("0.001"),places=3)
                if line_id in requested:
                    raise OperationError("An order line can only appear once in a receipt.")
                remaining=Decimal(source[line_id]["quantity"])-Decimal(source[line_id]["received_quantity"])
                if qty>remaining:
                    raise OperationError(f"Received quantity exceeds the outstanding order quantity for {source[line_id]['sku']}.")
                requested[line_id]=qty
            normalized=[]
            total=Decimal("0.00")
            for line_id,qty in requested.items():
                order_line=source[line_id]
                product=self.conn.execute("SELECT * FROM products WHERE id=? AND active=1",(order_line["product_id"],)).fetchone()
                if not product:
                    raise OperationError(f"Product {order_line['sku']} is no longer active.")
                cost=Decimal(order_line["unit_cost"])
                amount=(qty*cost).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
                total+=amount
                normalized.append((product,qty,cost,amount,line_id))
            paid=self._decimal(amount_paid,"Amount paid",minimum=Decimal("0"))
            if paid>total:
                raise OperationError("Amount paid cannot exceed the received goods total.")
            buckets={}
            for product,qty,cost,amount,line_id in normalized:
                item=buckets.setdefault(int(product["id"]),[Decimal("0"),Decimal("0"),product])
                item[0]+=qty
                item[1]+=amount
            weighted_costs={}
            prior_costs={}
            for product_id,(qty,cost_total,product) in buckets.items():
                existing=self.stock_on_hand(product_id)
                new_qty=existing+qty
                old_cost=Decimal(product["cost_price"])
                prior_costs[product_id]=old_cost
                weighted_costs[product_id]=(old_cost*existing+cost_total)/new_qty if new_qty else old_cost
            cur=self.conn.execute("INSERT INTO purchases(purchase_number,order_id,supplier_id,supplier_name,status,payment_status,total,paid_total,created_at) VALUES('',?,?,?,?,?,?,?,?)",
                (int(order_id),order["supplier_id"],order["supplier_name"],"Received",self._payment_status(total,paid),self._money(total),self._money(paid),timestamp))
            purchase_id=cur.lastrowid
            purchase_number=self._next_number("PUR",purchase_id)
            self.conn.execute("UPDATE purchases SET purchase_number=? WHERE id=?",(purchase_number,purchase_id))
            sync_lines=[]
            for product,qty,cost,amount,line_id in normalized:
                self.conn.execute("INSERT INTO purchase_lines(purchase_id,product_id,sku,description,quantity,unit_cost,line_total,purchase_order_line_id,prior_cost) VALUES(?,?,?,?,?,?,?,?,?)",
                    (purchase_id,product["id"],product["sku"],product["name"],self._quantity(qty),self._money(cost),self._money(amount),line_id,self._money(prior_costs[int(product["id"])])))
                self.conn.execute("INSERT INTO stock_movements(product_id,quantity_delta,movement_type,reference_type,reference_id,note,created_at) VALUES(?,?,?,?,?,?,?)",
                    (product["id"],self._quantity(qty),"purchase_receipt","purchase",purchase_id,purchase_number,timestamp))
                self.conn.execute("UPDATE purchase_order_lines SET received_quantity=? WHERE id=?",
                    (self._quantity(Decimal(source[line_id]["received_quantity"])+qty),line_id))
                sync_lines.append({"purchase_order_line_id":line_id,"product_id":product["id"],"quantity":self._quantity(qty),"unit_cost":self._money(cost)})
            for product_id,average_cost in weighted_costs.items():
                self.conn.execute("UPDATE products SET cost_price=? WHERE id=?",(self._money(average_cost),product_id))
            if paid:
                self.conn.execute("INSERT INTO cash_entries(direction,amount,source_type,source_id,description,created_at) VALUES('out',?,?,?,?,?)",
                    (self._money(paid),"purchase",purchase_id,f"Payment for {purchase_number}",timestamp))
            current=self.purchase_order_lines(order_id)
            complete=all(Decimal(line["received_quantity"])==Decimal(line["quantity"]) for line in current)
            new_status="Received" if complete else "Partially received"
            self.conn.execute("UPDATE purchase_orders SET status=?,updated_at=? WHERE id=?",(new_status,timestamp,int(order_id)))
            self._enqueue("purchases",purchase_id,{"id":purchase_id,"purchase_number":purchase_number,"order_id":int(order_id),"supplier_name":order["supplier_name"],"status":"Received","total":self._money(total),"paid_total":self._money(paid),"lines":sync_lines},timestamp)
            self._enqueue("purchase_orders",int(order_id),{"id":int(order_id),"order_number":order["order_number"],"status":new_status,"received_lines":sync_lines},timestamp)
        return purchase_id

    def cancel_purchase_order(self, order_id, reason=""):
        self._require_module("Purchases")
        self._require("delete")
        timestamp=self._stamp()
        with self._transaction():
            order=self.conn.execute("SELECT * FROM purchase_orders WHERE id=?",(int(order_id),)).fetchone()
            if not order or order["status"] not in {"Ordered","Partially received"}:
                raise OperationError("Only an open purchase order can be cancelled.")
            self.conn.execute("UPDATE purchase_orders SET status='Cancelled',cancelled_at=?,cancellation_reason=?,updated_at=? WHERE id=?",
                (timestamp,str(reason).strip()[:300],timestamp,int(order_id)))
            self._enqueue("purchase_orders",int(order_id),{"id":int(order_id),"order_number":order["order_number"],"status":"Cancelled","reason":str(reason).strip()[:300]},timestamp)
        return True

    def receive_purchase(self, supplier_name, lines, amount_paid=0, supplier_id=None):
        self._require_module("Purchases")
        self._require("manage_inventory")
        normalized=self._normalize_lines(lines,"unit_cost")
        total=sum((line[3] for line in normalized),Decimal("0")).quantize(Decimal("0.01"))
        paid=self._decimal(amount_paid,"Amount paid",minimum=Decimal("0"))
        if paid > total:
            raise OperationError("Amount paid cannot exceed the purchase total.")
        timestamp=self._stamp()
        with self._transaction():
            if supplier_id is not None:
                supplier=self.conn.execute("SELECT name FROM suppliers WHERE id=?",(int(supplier_id),)).fetchone()
                if not supplier:
                    raise OperationError("The selected supplier no longer exists.")
                supplier_name=supplier["name"]
            supplier_name=str(supplier_name or "").strip()[:160]
            incoming={}
            products_by_id={}
            for product,qty,price,amount in normalized:
                bucket=incoming.setdefault(product["id"],[Decimal("0"),Decimal("0")])
                bucket[0]+=qty
                bucket[1]+=amount
                products_by_id[product["id"]]=product
            weighted_costs={}
            prior_costs={}
            for product_id,(qty,cost_total) in incoming.items():
                product=products_by_id[product_id]
                existing_qty=self.stock_on_hand(product_id)
                total_qty=existing_qty+qty
                existing_cost=Decimal(product["cost_price"])
                prior_costs[product_id]=existing_cost
                weighted_costs[product_id]=(existing_cost*existing_qty+cost_total)/total_qty if total_qty else existing_cost
            cur=self.conn.execute(
                "INSERT INTO purchases(purchase_number,supplier_id,supplier_name,status,payment_status,total,paid_total,created_at) VALUES('',?,?,?,?,?,?,?)",
                (supplier_id,supplier_name,"Received",self._payment_status(total,paid),self._money(total),self._money(paid),timestamp))
            purchase_id=cur.lastrowid
            purchase_number=self._next_number("PUR",purchase_id)
            self.conn.execute("UPDATE purchases SET purchase_number=? WHERE id=?",(purchase_number,purchase_id))
            for product,qty,price,amount in normalized:
                self.conn.execute("INSERT INTO purchase_lines(purchase_id,product_id,sku,description,quantity,unit_cost,line_total,prior_cost) VALUES(?,?,?,?,?,?,?,?)",
                                  (purchase_id,product["id"],product["sku"],product["name"],self._quantity(qty),self._money(price),self._money(amount),self._money(prior_costs[int(product["id"])])))
                self.conn.execute("INSERT INTO stock_movements(product_id,quantity_delta,movement_type,reference_type,reference_id,note,created_at) VALUES(?,?,?,?,?,?,?)",
                                  (product["id"],self._quantity(qty),"purchase_receipt","purchase",purchase_id,purchase_number,timestamp))
            for product_id,average_cost in weighted_costs.items():
                self.conn.execute("UPDATE products SET cost_price=? WHERE id=?",(self._money(average_cost),product_id))
            if paid:
                self.conn.execute("INSERT INTO cash_entries(direction,amount,source_type,source_id,description,created_at) VALUES('out',?,?,?,?,?)",
                                  (self._money(paid),"purchase",purchase_id,f"Payment made for {purchase_number}",timestamp))
            self._enqueue("purchases",purchase_id,{"id":purchase_id,"purchase_number":purchase_number,"supplier_name":supplier_name,
                "status":"Received","total":self._money(total),"paid_total":self._money(paid),
                "lines":[{"product_id":p["id"],"sku":p["sku"],"quantity":self._quantity(q),"unit_cost":self._money(price),"line_total":self._money(amount)} for p,q,price,amount in normalized]},timestamp)
        return purchase_id

    def record_sale_return(self, sale_id, lines, reason=""):
        self._require_module("Sales")
        self._require("process_returns")
        if not isinstance(lines, (list, tuple)) or not lines:
            raise OperationError("Select at least one sold item to return.")
        timestamp=self._stamp()
        with self._transaction():
            sale=self.conn.execute("SELECT * FROM sales WHERE id=?",(int(sale_id),)).fetchone()
            if not sale or sale["status"] not in {"Completed", "Partially returned"}:
                raise OperationError("Only a completed sale with unreturned items can be returned.")
            source_lines={row["id"]:row for row in self.conn.execute("SELECT * FROM sale_lines WHERE sale_id=?",(int(sale_id),)).fetchall()}
            already={}
            already_tax={}
            already_base={}
            for returned_line in self.conn.execute(
                "SELECT rl.sale_line_id,rl.quantity,rl.tax_amount,rl.line_total FROM sale_return_lines rl JOIN sales_returns sr ON sr.id=rl.return_id WHERE sr.sale_id=?",(int(sale_id),)).fetchall():
                line_id=int(returned_line["sale_line_id"])
                already[line_id]=already.get(line_id,Decimal("0"))+Decimal(returned_line["quantity"])
                already_tax[line_id]=already_tax.get(line_id,Decimal("0.00"))+Decimal(returned_line["tax_amount"] or "0.00")
                already_base[line_id]=already_base.get(line_id,Decimal("0.00"))+Decimal(returned_line["line_total"])
            requested={}
            for item in lines:
                try:
                    line_id=int(item["sale_line_id"])
                except (KeyError,TypeError,ValueError):
                    raise OperationError("Each returned item must refer to a sale line.")
                if line_id not in source_lines:
                    raise OperationError("A selected item does not belong to this sale.")
                qty=self._decimal(item.get("quantity"),"Return quantity",minimum=Decimal("0.001"),places=3)
                if line_id in requested:
                    raise OperationError("A sale line can only appear once in a return.")
                requested[line_id]=(qty,bool(item.get("restock",True)))
            refund_total=Decimal("0.00")
            base_refund_total=Decimal("0.00")
            tax_refund_total=Decimal("0.00")
            return_amounts={}
            new_returned={}
            for line_id,(qty,restock) in requested.items():
                source=source_lines[line_id]
                prior=already.get(line_id,Decimal("0"))
                sold=Decimal(source["quantity"])
                if prior+qty>sold:
                    raise OperationError(f"Return quantity exceeds the remaining sold quantity for {source['sku']}.")
                if prior+qty == sold:
                    base_amount=Decimal(source["line_total"])-already_base.get(line_id,Decimal("0.00"))
                    tax_amount=Decimal(source["tax_amount"] or "0.00")-already_tax.get(line_id,Decimal("0.00"))
                else:
                    base_amount=(qty*Decimal(source["unit_price"])).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
                    tax_amount=(Decimal(source["tax_amount"] or "0.00")*qty/sold).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
                amount=base_amount+tax_amount
                return_amounts[line_id]=(base_amount,tax_amount,amount)
                base_refund_total+=base_amount
                tax_refund_total+=tax_amount
                refund_total+=amount
                new_returned[line_id]=prior+qty
            if refund_total > Decimal(sale["paid_total"]):
                raise OperationError("The returned items exceed the refundable amount already paid. Customer credit returns are not supported yet.")
            return_id=self.conn.execute(
                "INSERT INTO sales_returns(return_number,sale_id,refund_amount,reason,created_at,created_by) VALUES('',?,?,?,?,?)",
                (int(sale_id),self._money(refund_total),str(reason).strip()[:300],timestamp,self.actor_username)).lastrowid
            return_number=self._next_number("RET",return_id)
            self.conn.execute("UPDATE sales_returns SET return_number=? WHERE id=?",(return_number,return_id))
            for line_id,(qty,restock) in requested.items():
                source=source_lines[line_id]
                base_amount,tax_amount,amount=return_amounts[line_id]
                self.conn.execute("INSERT INTO sale_return_lines(return_id,sale_line_id,product_id,sku,description,quantity,restocked,unit_price,line_total,tax_amount) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (return_id,line_id,source["product_id"],source["sku"],source["description"],self._quantity(qty),int(restock),source["unit_price"],self._money(base_amount),self._money(tax_amount)))
                if restock:
                    self.conn.execute("INSERT INTO stock_movements(product_id,quantity_delta,movement_type,reference_type,reference_id,note,created_at) VALUES(?,?,?,?,?,?,?)",
                        (source["product_id"],self._quantity(qty),"sale_return","sales_return",return_id,f"{return_number} · {source['sku']}",timestamp))
            if refund_total:
                self.conn.execute("INSERT INTO cash_entries(direction,amount,source_type,source_id,description,created_at) VALUES('out',?,?,?,?,?)",
                    (self._money(refund_total),"sale_return",return_id,f"Refund for {return_number}",timestamp))
            is_fully_returned=all(new_returned.get(line_id,already.get(line_id,Decimal("0")))==Decimal(row["quantity"]) for line_id,row in source_lines.items())
            new_status="Returned" if is_fully_returned else "Partially returned"
            net_total=Decimal(sale["total"])-refund_total
            net_subtotal=Decimal(sale["subtotal"])-base_refund_total
            net_tax=Decimal(sale["tax_total"])-tax_refund_total
            net_paid=Decimal(sale["paid_total"])-refund_total
            self.conn.execute("UPDATE sales SET status=?,subtotal=?,tax_total=?,total=?,paid_total=?,payment_status=? WHERE id=?",
                (new_status,self._money(net_subtotal),self._money(net_tax),self._money(net_total),self._money(net_paid),self._payment_status(net_total,net_paid),int(sale_id)))
            self._enqueue("sales_returns",return_id,{"id":return_id,"return_number":return_number,"sale_id":int(sale_id),"refund_amount":self._money(refund_total),"reason":str(reason).strip()[:300],"lines":[{"sale_line_id":line_id,"quantity":self._quantity(qty),"restocked":restock} for line_id,(qty,restock) in requested.items()]},timestamp)
            self._enqueue("sales",int(sale_id),{"id":int(sale_id),"sale_number":sale["sale_number"],"status":new_status},timestamp)
        return return_id

    def returned_quantity(self, sale_line_id):
        rows=self.conn.execute("SELECT quantity FROM sale_return_lines WHERE sale_line_id=?",(int(sale_line_id),)).fetchall()
        return sum((Decimal(row["quantity"]) for row in rows),Decimal("0"))

    def sales_returns(self, sale_id=None, limit=1000):
        self._require_module("Sales")
        if sale_id is None:
            return self.conn.execute("SELECT sr.*,s.sale_number,s.customer_name FROM sales_returns sr JOIN sales s ON s.id=sr.sale_id ORDER BY sr.id DESC LIMIT ?",(limit,)).fetchall()
        return self.conn.execute("SELECT * FROM sales_returns WHERE sale_id=? ORDER BY id DESC LIMIT ?",(int(sale_id),limit)).fetchall()

    def cancel_sale(self, sale_id, reason="", refund_amount=0):
        self._require_module("Sales")
        self._require("delete")
        timestamp=self._stamp()
        with self._transaction():
            sale=self.conn.execute("SELECT * FROM sales WHERE id=?",(sale_id,)).fetchone()
            if not sale or sale["status"] not in {"Completed", "Partially returned"}:
                raise OperationError("Only an active sale can be cancelled.")
            refunded=self._decimal(refund_amount,"Refund amount",minimum=Decimal("0"))
            refundable=Decimal(sale["paid_total"])
            if refunded > refundable:
                raise OperationError("Refund amount cannot exceed the remaining amount paid.")
            lines=self.conn.execute("SELECT * FROM sale_lines WHERE sale_id=?",(sale_id,)).fetchall()
            returned={}
            for row in self.conn.execute("SELECT rl.sale_line_id,rl.quantity FROM sale_return_lines rl JOIN sales_returns sr ON sr.id=rl.return_id WHERE sr.sale_id=?",(sale_id,)).fetchall():
                line_id=int(row["sale_line_id"])
                returned[line_id]=returned.get(line_id,Decimal("0"))+Decimal(row["quantity"])
            for line in lines:
                remaining=Decimal(line["quantity"])-returned.get(int(line["id"]),Decimal("0"))
                if remaining>0:
                    self.conn.execute("INSERT INTO stock_movements(product_id,quantity_delta,movement_type,reference_type,reference_id,note,created_at) VALUES(?,?,?,?,?,?,?)",
                        (line["product_id"],self._quantity(remaining),"sale_reversal","sale",sale_id,f"Cancellation {sale['sale_number']}",timestamp))
            self.conn.execute("UPDATE sales SET status='Cancelled',cancelled_at=?,cancellation_reason=? WHERE id=?",
                              (timestamp,str(reason).strip()[:300],sale_id))
            if refunded:
                self.conn.execute("INSERT INTO cash_entries(direction,amount,source_type,source_id,description,created_at) VALUES('out',?,?,?,?,?)",
                                  (self._money(refunded),"sale_refund",sale_id,f"Refund for {sale['sale_number']}",timestamp))
            self._enqueue("sales",sale_id,{"id":sale_id,"sale_number":sale["sale_number"],"status":"Cancelled",
                "reason":str(reason).strip()[:300],"refund_amount":self._money(refunded)},timestamp)
        return True

    def cancel_purchase(self, purchase_id, reason="", refund_amount=0):
        self._require_module("Purchases")
        self._require("delete")
        timestamp=self._stamp()
        with self._transaction():
            purchase=self.conn.execute("SELECT * FROM purchases WHERE id=?",(purchase_id,)).fetchone()
            if not purchase or purchase["status"] != "Received":
                raise OperationError("Only a received purchase can be cancelled.")
            refunded=self._decimal(refund_amount,"Refund amount",minimum=Decimal("0"))
            if refunded > Decimal(purchase["paid_total"]):
                raise OperationError("Refund amount cannot exceed the amount paid.")
            lines=self.conn.execute("SELECT * FROM purchase_lines WHERE purchase_id=?",(purchase_id,)).fetchall()
            required={}
            for line in lines:
                required[line["product_id"]]=required.get(line["product_id"],Decimal("0"))+Decimal(line["quantity"])
            for product_id,received in required.items():
                available=self.stock_on_hand(product_id)
                if available < received:
                    raise OperationError(f"Cannot cancel {purchase['purchase_number']}: later operations have consumed some of its stock.")
            for product_id in required:
                purchase_move=self.conn.execute("SELECT MAX(id) FROM stock_movements WHERE product_id=? AND reference_type='purchase' AND reference_id=?",(product_id,purchase_id)).fetchone()[0]
                later=self.conn.execute("SELECT 1 FROM stock_movements WHERE product_id=? AND id>? LIMIT 1",(product_id,purchase_move or 0)).fetchone()
                if later:
                    raise OperationError(f"Cannot cancel {purchase['purchase_number']} after later stock activity because weighted-average cost can no longer be reversed safely.")
            prior_cost_by_product={}
            for line in lines:
                if line["prior_cost"] is not None:
                    prior_cost_by_product[int(line["product_id"])]=Decimal(line["prior_cost"])
                qty=Decimal(line["quantity"])
                self.conn.execute("INSERT INTO stock_movements(product_id,quantity_delta,movement_type,reference_type,reference_id,note,created_at) VALUES(?,?,?,?,?,?,?)",
                                  (line["product_id"],self._quantity(-qty),"purchase_reversal","purchase",purchase_id,f"Cancellation {purchase['purchase_number']}",timestamp))
            for product_id,prior_cost in prior_cost_by_product.items():
                self.conn.execute("UPDATE products SET cost_price=? WHERE id=?",(self._money(prior_cost),product_id))
            if purchase["order_id"] is not None:
                for line in lines:
                    line_id=line["purchase_order_line_id"]
                    if line_id is None:
                        continue
                    order_line=self.conn.execute("SELECT received_quantity FROM purchase_order_lines WHERE id=?",(line_id,)).fetchone()
                    if order_line:
                        remaining=Decimal(order_line["received_quantity"])-Decimal(line["quantity"])
                        if remaining<0:
                            raise OperationError("Purchase-order received quantity would become negative.")
                        self.conn.execute("UPDATE purchase_order_lines SET received_quantity=? WHERE id=?",(self._quantity(remaining),line_id))
                order_lines=self.purchase_order_lines(purchase["order_id"])
                complete=all(Decimal(row["received_quantity"])==Decimal(row["quantity"]) for row in order_lines)
                any_received=any(Decimal(row["received_quantity"])>0 for row in order_lines)
                order=self.conn.execute("SELECT order_number,status FROM purchase_orders WHERE id=?",(purchase["order_id"],)).fetchone()
                order_status=order["status"] if order["status"]=="Cancelled" else ("Received" if complete else ("Partially received" if any_received else "Ordered"))
                self.conn.execute("UPDATE purchase_orders SET status=?,updated_at=? WHERE id=?",(order_status,timestamp,purchase["order_id"]))
                self._enqueue("purchase_orders",int(purchase["order_id"]),{"id":int(purchase["order_id"]),"order_number":order["order_number"],"status":order_status},timestamp)
            self.conn.execute("UPDATE purchases SET status='Cancelled',cancelled_at=?,cancellation_reason=? WHERE id=?",
                              (timestamp,str(reason).strip()[:300],purchase_id))
            if refunded:
                self.conn.execute("INSERT INTO cash_entries(direction,amount,source_type,source_id,description,created_at) VALUES('in',?,?,?,?,?)",
                                  (self._money(refunded),"purchase_refund",purchase_id,f"Refund for {purchase['purchase_number']}",timestamp))
            self._enqueue("purchases",purchase_id,{"id":purchase_id,"purchase_number":purchase["purchase_number"],"status":"Cancelled",
                "reason":str(reason).strip()[:300],"refund_amount":self._money(refunded)},timestamp)
        return True

    def _repair_event(self, job_id, event, details, timestamp):
        self.conn.execute("INSERT INTO repair_job_events(job_id,event,details,actor,created_at) VALUES(?,?,?,?,?)",
                          (int(job_id),str(event)[:80],str(details)[:500],self.actor_username,timestamp))

    def create_repair_job(self, customer_name, device, reported_fault, serial_number="", customer_id=None, warranty_notes=""):
        self._require_module("Repair Tickets")
        self._require("create")
        device=str(device or "").strip()[:160]
        fault=str(reported_fault or "").strip()[:4000]
        if not device or not fault:
            raise OperationError("Enter the device and the reported fault.")
        if customer_id is not None:
            customer=self.conn.execute("SELECT name FROM customers WHERE id=?",(int(customer_id),)).fetchone()
            if not customer:
                raise OperationError("The selected customer no longer exists.")
            customer_name=customer["name"]
        customer_name=str(customer_name or "").strip()[:160]
        if not customer_name:
            raise OperationError("Enter a customer name before creating the repair ticket.")
        timestamp=self._stamp()
        with self._transaction():
            cur=self.conn.execute("INSERT INTO repair_jobs(ticket_number,customer_id,customer_name,device,serial_number,reported_fault,warranty_notes,status,created_by,created_at,updated_at) VALUES('',?,?,?,?,?,?,'Received',?,?,?)",
                (customer_id,customer_name,device,str(serial_number or "").strip()[:160],fault,str(warranty_notes or "").strip()[:1000],self.actor_username,timestamp,timestamp))
            job_id=cur.lastrowid
            ticket=self._next_number("JOB",job_id)
            self.conn.execute("UPDATE repair_jobs SET ticket_number=? WHERE id=?",(ticket,job_id))
            self._repair_event(job_id,"Ticket received",f"{device} · {customer_name}",timestamp)
            self._enqueue("repair_jobs",job_id,{"id":job_id,"ticket_number":ticket,"customer_name":customer_name,"device":device,"serial_number":str(serial_number or "").strip()[:160],"reported_fault":fault,"status":"Received"},timestamp)
        return job_id

    def update_repair_diagnosis(self, job_id, diagnosis, technician, labor_amount, estimate_amount):
        self._require_module("Repair Tickets")
        self._require("edit")
        diagnosis=str(diagnosis or "").strip()[:4000]
        technician=str(technician or "").strip()[:160]
        if not diagnosis:
            raise OperationError("Enter a diagnosis before requesting customer approval.")
        labor=self._decimal(labor_amount,"Labor charge",minimum=Decimal("0"))
        estimate=self._decimal(estimate_amount,"Pre-tax estimate",minimum=Decimal("0"))
        timestamp=self._stamp()
        with self._transaction():
            job=self.conn.execute("SELECT * FROM repair_jobs WHERE id=?",(int(job_id),)).fetchone()
            if not job or job["status"] not in {"Received","Diagnosing","Declined","In progress"}:
                raise OperationError("This repair ticket cannot be diagnosed in its current state.")
            parts=sum((Decimal(row[0]) for row in self.conn.execute("SELECT line_total FROM repair_job_parts WHERE job_id=?",(int(job_id),)).fetchall()),Decimal("0.00"))
            if labor+parts>estimate:
                raise OperationError("The estimate must cover labor and parts already issued.")
            self.conn.execute("UPDATE repair_jobs SET diagnosis=?,technician=?,labor_amount=?,estimate_amount=?,approval_status='Pending',status='Awaiting approval',updated_at=? WHERE id=?",
                (diagnosis,technician,self._money(labor),self._money(estimate),timestamp,int(job_id)))
            self._repair_event(job_id,"Estimate requested",f"Pre-tax estimate {self._money(estimate)} · technician {technician or 'Unassigned'}",timestamp)
            self._enqueue("repair_jobs",int(job_id),{"id":int(job_id),"diagnosis":diagnosis,"technician":technician,"labor_amount":self._money(labor),"estimate_amount":self._money(estimate),"approval_status":"Pending","status":"Awaiting approval"},timestamp)
        return True

    def approve_repair_estimate(self, job_id, approved, note=""):
        self._require_module("Repair Tickets")
        self._require("edit")
        timestamp=self._stamp()
        with self._transaction():
            job=self.conn.execute("SELECT * FROM repair_jobs WHERE id=?",(int(job_id),)).fetchone()
            if not job or job["status"]!="Awaiting approval":
                raise OperationError("Only a repair awaiting customer approval can be accepted or declined.")
            approval="Approved" if bool(approved) else "Declined"
            status="In progress" if approved else "Declined"
            self.conn.execute("UPDATE repair_jobs SET approval_status=?,status=?,updated_at=? WHERE id=?",(approval,status,timestamp,int(job_id)))
            details=str(note or "").strip()[:400] or ("Customer approved the estimate." if approved else "Customer declined the estimate.")
            self._repair_event(job_id,"Estimate "+approval,details,timestamp)
            self._enqueue("repair_jobs",int(job_id),{"id":int(job_id),"approval_status":approval,"status":status},timestamp)
        return True

    def add_repair_part(self, job_id, product_id, quantity):
        self._require_module("Repair Tickets")
        self._require("manage_inventory")
        qty=self._decimal(quantity,"Part quantity",minimum=Decimal("0.001"),places=3)
        timestamp=self._stamp()
        with self._transaction():
            job=self.conn.execute("SELECT * FROM repair_jobs WHERE id=?",(int(job_id),)).fetchone()
            if not job or job["status"]!="In progress" or job["approval_status"]!="Approved":
                raise OperationError("Parts can only be issued to an approved repair in progress.")
            product=self.conn.execute("SELECT * FROM products WHERE id=? AND active=1",(int(product_id),)).fetchone()
            if not product:
                raise OperationError("The selected part is no longer available.")
            stock=self.stock_on_hand(product_id)
            if stock<qty:
                raise OperationError(f"Not enough stock for {product['name']}. Available: {self._quantity(stock)} {product['unit']}.")
            price=Decimal(product["selling_price"])
            line_total=(qty*price).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
            parts_before=sum((Decimal(row[0]) for row in self.conn.execute("SELECT line_total FROM repair_job_parts WHERE job_id=?",(int(job_id),)).fetchall()),Decimal("0.00"))
            if Decimal(job["labor_amount"])+parts_before+line_total>Decimal(job["estimate_amount"]):
                raise OperationError("This part would exceed the customer-approved pre-tax estimate. Revise the estimate and obtain approval first.")
            part_id=self.conn.execute("INSERT INTO repair_job_parts(job_id,product_id,sku,description,quantity,unit_price,line_total,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (int(job_id),int(product_id),product["sku"],product["name"],self._quantity(qty),self._money(price),self._money(line_total),self.actor_username,timestamp)).lastrowid
            movement_id=self.conn.execute("INSERT INTO stock_movements(product_id,quantity_delta,movement_type,reference_type,reference_id,note,created_at) VALUES(?,?,?,?,?,?,?)",
                (int(product_id),self._quantity(-qty),"repair_part","repair_job",int(job_id),job["ticket_number"],timestamp)).lastrowid
            self._repair_event(job_id,"Part issued",f"{product['sku']} · {self._quantity(qty)} · {self._money(line_total)}",timestamp)
            self._enqueue("repair_job_parts",part_id,{"id":part_id,"job_id":int(job_id),"product_id":int(product_id),"sku":product["sku"],"quantity":self._quantity(qty),"unit_price":self._money(price),"line_total":self._money(line_total)},timestamp)
            self._enqueue("stock_movements",movement_id,{"id":movement_id,"product_id":int(product_id),"sku":product["sku"],"quantity_delta":self._quantity(-qty),"reference_type":"repair_job","reference_id":int(job_id)},timestamp)
        return part_id

    def mark_repair_ready(self, job_id):
        self._require_module("Repair Tickets")
        self._require("edit")
        timestamp=self._stamp()
        with self._transaction():
            job=self.conn.execute("SELECT * FROM repair_jobs WHERE id=?",(int(job_id),)).fetchone()
            if not job or job["status"]!="In progress" or job["approval_status"]!="Approved":
                raise OperationError("Only an approved repair in progress can be marked ready.")
            parts=sum((Decimal(row[0]) for row in self.conn.execute("SELECT line_total FROM repair_job_parts WHERE job_id=?",(int(job_id),)).fetchall()),Decimal("0.00"))
            subtotal=Decimal(job["labor_amount"])+parts
            tax_label,tax_rate=self._tax_config()
            tax_total=self._tax_amount(subtotal,tax_rate)
            total=subtotal+tax_total
            self.conn.execute("UPDATE repair_jobs SET subtotal=?,tax_label=?,tax_rate=?,tax_total=?,total=?,status='Ready',updated_at=? WHERE id=?",
                (self._money(subtotal),tax_label,self._money(tax_rate),self._money(tax_total),self._money(total),timestamp,int(job_id)))
            self._repair_event(job_id,"Repair ready",f"Total {self._money(total)} including configured tax",timestamp)
            self._enqueue("repair_jobs",int(job_id),{"id":int(job_id),"subtotal":self._money(subtotal),"tax_label":tax_label,"tax_rate":self._money(tax_rate),"tax_total":self._money(tax_total),"total":self._money(total),"status":"Ready"},timestamp)
        return True

    def record_repair_payment(self, job_id, amount, reference=""):
        self._require_module("Repair Tickets")
        self._require("manage_finance")
        value=self._decimal(amount,"Payment",minimum=Decimal("0.01"))
        reference=str(reference or "").strip()[:120]
        timestamp=self._stamp()
        with self._transaction():
            job=self.conn.execute("SELECT * FROM repair_jobs WHERE id=?",(int(job_id),)).fetchone()
            if not job or job["status"]!="Ready":
                raise OperationError("Payments can be recorded only when the repair is ready for collection.")
            due=Decimal(job["total"])-Decimal(job["paid_total"])
            if value>due:
                raise OperationError("Payment cannot exceed the outstanding repair balance.")
            payment_id=self.conn.execute("INSERT INTO repair_job_payments(job_id,amount,reference,created_by,created_at) VALUES(?,?,?,?,?)",
                (int(job_id),self._money(value),reference,self.actor_username,timestamp)).lastrowid
            paid=Decimal(job["paid_total"])+value
            self.conn.execute("UPDATE repair_jobs SET paid_total=?,updated_at=? WHERE id=?",(self._money(paid),timestamp,int(job_id)))
            cash_id=self.conn.execute("INSERT INTO cash_entries(direction,amount,source_type,source_id,description,created_at) VALUES('in',?,'repair_job',?,?,?)",
                (self._money(value),int(job_id),f"Repair payment {job['ticket_number']}",timestamp)).lastrowid
            self._repair_event(job_id,"Payment received",f"{self._money(value)} · {reference or 'No reference'}",timestamp)
            self._enqueue("repair_job_payments",payment_id,{"id":payment_id,"job_id":int(job_id),"amount":self._money(value),"reference":reference},timestamp)
            self._enqueue("cash_entries",cash_id,{"id":cash_id,"direction":"in","amount":self._money(value),"source_type":"repair_job","source_id":int(job_id)},timestamp)
            self._enqueue("repair_jobs",int(job_id),{"id":int(job_id),"paid_total":self._money(paid)},timestamp)
        return payment_id

    def collect_repair_job(self, job_id):
        self._require_module("Repair Tickets")
        self._require("edit")
        timestamp=self._stamp()
        with self._transaction():
            job=self.conn.execute("SELECT * FROM repair_jobs WHERE id=?",(int(job_id),)).fetchone()
            if not job or job["status"]!="Ready":
                raise OperationError("Only a ready repair can be collected.")
            if Decimal(job["paid_total"])<Decimal(job["total"]):
                raise OperationError("The repair must be paid in full before collection.")
            self.conn.execute("UPDATE repair_jobs SET status='Collected',updated_at=? WHERE id=?",(timestamp,int(job_id)))
            self._repair_event(job_id,"Device collected","Customer collected the repaired device.",timestamp)
            self._enqueue("repair_jobs",int(job_id),{"id":int(job_id),"status":"Collected"},timestamp)
        return True

    def cancel_repair_job(self, job_id, reason=""):
        self._require_module("Repair Tickets")
        self._require("delete")
        timestamp=self._stamp()
        with self._transaction():
            job=self.conn.execute("SELECT * FROM repair_jobs WHERE id=?",(int(job_id),)).fetchone()
            if not job or job["status"] not in {"Received","Diagnosing","Awaiting approval","Declined"}:
                raise OperationError("This repair can no longer be cancelled after work or parts issuance has started.")
            reason=str(reason or "").strip()[:300]
            self.conn.execute("UPDATE repair_jobs SET status='Cancelled',updated_at=? WHERE id=?",(timestamp,int(job_id)))
            self._repair_event(job_id,"Ticket cancelled",reason or "No reason supplied",timestamp)
            self._enqueue("repair_jobs",int(job_id),{"id":int(job_id),"status":"Cancelled","reason":reason},timestamp)
        return True

    def repair_jobs(self, limit=500, customer_id=None):
        self._require_module("Repair Tickets")
        if customer_id is not None:
            return self.conn.execute("SELECT * FROM repair_jobs WHERE customer_id=? ORDER BY id DESC LIMIT ?",(int(customer_id),limit)).fetchall()
        return self.conn.execute("SELECT * FROM repair_jobs ORDER BY id DESC LIMIT ?",(limit,)).fetchall()

    def repair_job_parts(self, job_id):
        self._require_module("Repair Tickets")
        return self.conn.execute("SELECT * FROM repair_job_parts WHERE job_id=? ORDER BY id",(int(job_id),)).fetchall()

    def repair_job_payments(self, job_id):
        self._require_module("Repair Tickets")
        return self.conn.execute("SELECT * FROM repair_job_payments WHERE job_id=? ORDER BY id",(int(job_id),)).fetchall()

    def repair_job_events(self, job_id):
        self._require_module("Repair Tickets")
        return self.conn.execute("SELECT * FROM repair_job_events WHERE job_id=? ORDER BY id",(int(job_id),)).fetchall()

    def record_cash_entry(self, direction, amount, description):
        self._require_module("Finance")
        self._require("manage_finance")
        if direction not in {"in","out"}:
            raise OperationError("Cash direction must be 'in' or 'out'.")
        value=self._decimal(amount,"Amount",minimum=Decimal("0.01"))
        description=str(description).strip()[:240]
        if not description:
            raise OperationError("Enter a description for the cash movement.")
        timestamp=self._stamp()
        with self._transaction():
            cur=self.conn.execute("INSERT INTO cash_entries(direction,amount,source_type,source_id,description,created_at) VALUES(?,?,?,?,?,?)",
                                  (direction,self._money(value),"manual",0,description,timestamp))
            self._enqueue("cash_entries",cur.lastrowid,{"id":cur.lastrowid,"direction":direction,
                "amount":self._money(value),"description":description},timestamp)
        return cur.lastrowid

    def cash_balance(self):
        self._require_any_module(("Finance","Reports"))
        entries=self.cash_entries(limit=1000000)
        balance=Decimal("0")
        for entry in entries:
            amount=Decimal(entry["amount"])
            balance += amount if entry["direction"] == "in" else -amount
        return balance

    def inventory_value(self):
        self._require_any_module(("Inventory","Reports"))
        total=Decimal("0")
        for product in self.products(active_only=False):
            total += self.stock_on_hand(product["id"])*Decimal(product["cost_price"])
        return total.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)

    def sales(self, limit=500):
        self._require_any_module(("Sales","Reports"))
        return self.conn.execute("SELECT * FROM sales ORDER BY id DESC LIMIT ?",(limit,)).fetchall()

    def purchases(self, limit=500):
        self._require_any_module(("Purchases","Reports"))
        return self.conn.execute("SELECT * FROM purchases ORDER BY id DESC LIMIT ?",(limit,)).fetchall()

    def cash_entries(self, limit=500):
        self._require_any_module(("Finance","Reports"))
        return self.conn.execute("SELECT * FROM cash_entries ORDER BY id DESC LIMIT ?",(limit,)).fetchall()

    def stock_movements(self, limit=500):
        self._require_any_module(("Inventory","Reports"))
        return self.conn.execute("""SELECT m.*,p.sku,p.name,p.unit FROM stock_movements m
                                  JOIN products p ON p.id=m.product_id
                                  ORDER BY m.id DESC LIMIT ?""",(limit,)).fetchall()

