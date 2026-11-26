import sqlite3
import unittest
from decimal import Decimal

from operations import OperationsService, OperationError


class OperationsCoreTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.ops = OperationsService(self.conn)
        self.product_id = self.ops.create_product(
            "SKU-001", "Test laptop", selling_price="2000",
            cost_price="1500", opening_stock="5",
        )

    def tearDown(self):
        self.conn.close()

    def test_sale_reduces_stock_and_records_payment(self):
        sale_id = self.ops.record_sale(
            "Walk-in", [{"product_id": self.product_id, "quantity": 2}],
            amount_paid="4000",
        )
        sale = self.conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        self.assertEqual(Decimal(sale["total"]), Decimal("4000.00"))
        self.assertEqual(Decimal(sale["paid_total"]), Decimal("4000.00"))
        self.assertEqual(self.ops.stock_on_hand(self.product_id), Decimal("3"))
        self.assertEqual(self.ops.cash_balance(), Decimal("4000.00"))

    def test_overselling_is_rejected_without_mutation(self):
        with self.assertRaises(OperationError):
            self.ops.record_sale(
                "Walk-in", [{"product_id": self.product_id, "quantity": 6}],
                amount_paid="12000",
            )
        self.assertEqual(self.ops.stock_on_hand(self.product_id), Decimal("5"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0], 0)

    def test_return_restocks_and_refunds_paid_amount(self):
        sale_id = self.ops.record_sale(
            "Walk-in", [{"product_id": self.product_id, "quantity": 2}],
            amount_paid="4000",
        )
        sale_line_id = self.conn.execute(
            "SELECT id FROM sale_lines WHERE sale_id=?", (sale_id,)
        ).fetchone()[0]
        return_id = self.ops.record_sale_return(
            sale_id, [{"sale_line_id": sale_line_id, "quantity": 1, "restock": True}],
            "Defective item",
        )
        self.assertGreater(return_id, 0)
        sale = self.conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
        self.assertEqual(sale["status"], "Partially returned")
        self.assertEqual(Decimal(sale["total"]), Decimal("2000.00"))
        self.assertEqual(Decimal(sale["paid_total"]), Decimal("2000.00"))
        self.assertEqual(self.ops.stock_on_hand(self.product_id), Decimal("4"))
        self.assertEqual(self.ops.cash_balance(), Decimal("2000.00"))

    def test_return_quantity_cannot_exceed_sold_quantity(self):
        sale_id = self.ops.record_sale(
            "Walk-in", [{"product_id": self.product_id, "quantity": 1}],
            amount_paid="2000",
        )
        line_id = self.conn.execute(
            "SELECT id FROM sale_lines WHERE sale_id=?", (sale_id,)
        ).fetchone()[0]
        with self.assertRaises(OperationError):
            self.ops.record_sale_return(
                sale_id, [{"sale_line_id": line_id, "quantity": 2, "restock": True}],
                "Too many",
            )
        self.assertEqual(self.ops.stock_on_hand(self.product_id), Decimal("4"))

    def test_schema_migration_is_idempotent(self):
        self.conn.execute("PRAGMA user_version=8")
        self.ops.initialize()
        self.ops.initialize()
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 9)
        self.assertIn("barcode", {row[1] for row in self.conn.execute("PRAGMA table_info(products)")})


if __name__ == "__main__":
    unittest.main()
