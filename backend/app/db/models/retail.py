from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base

RETAIL_SCHEMA = "retail"


class Region(Base):
    __tablename__ = "regions"
    __table_args__ = (
        UniqueConstraint("code"),
        UniqueConstraint("name"),
        {"schema": RETAIL_SCHEMA, "comment": "Brazilian sales regions."},
    )

    id: Mapped[int] = mapped_column(
        SmallInteger,
        Identity(),
        primary_key=True,
        comment="Surrogate region identifier.",
    )
    code: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        comment="Stable business code for the region.",
    )
    name: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        comment="Human-readable region name.",
    )
    country_code: Mapped[str] = mapped_column(
        String(2),
        nullable=False,
        server_default=text("'BR'"),
        comment="ISO 3166-1 alpha-2 country code.",
    )


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint(
            "segment IN ('consumer', 'small_business', 'enterprise')",
            name="segment_allowed",
        ),
        Index("ix_customers_region_id", "region_id"),
        Index("ix_customers_created_at", "created_at"),
        UniqueConstraint("external_id"),
        UniqueConstraint("email"),
        {
            "schema": RETAIL_SCHEMA,
            "comment": "Synthetic customers used by the retail analytics demo.",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
        comment="Surrogate customer identifier.",
    )
    external_id: Mapped[UUID] = mapped_column(
        Uuid,
        nullable=False,
        comment="Public UUID used outside the database.",
    )
    region_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey(f"{RETAIL_SCHEMA}.regions.id", ondelete="RESTRICT"),
        nullable=False,
        comment="Customer home region.",
    )
    full_name: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
        comment="Synthetic customer name.",
    )
    email: Mapped[str] = mapped_column(
        String(254),
        nullable=False,
        comment="Synthetic email; treated as a restricted column.",
    )
    document_number: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="Synthetic document; always restricted from NL2SQL.",
    )
    segment: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        comment="Commercial customer segment.",
    )
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="Whether the customer is active.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Customer creation timestamp.",
    )


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("name"),
        {
            "schema": RETAIL_SCHEMA,
            "comment": "Product category hierarchy root.",
        },
    )

    id: Mapped[int] = mapped_column(
        SmallInteger,
        Identity(),
        primary_key=True,
        comment="Surrogate category identifier.",
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Unique category name.",
    )
    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        comment="Business description of the category.",
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("unit_cost >= 0", name="unit_cost_nonnegative"),
        CheckConstraint("list_price > 0", name="list_price_positive"),
        Index("ix_products_category_id", "category_id"),
        Index("ix_products_active", "active"),
        UniqueConstraint("sku"),
        {
            "schema": RETAIL_SCHEMA,
            "comment": "Products sold by the demonstration store.",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
        comment="Surrogate product identifier.",
    )
    category_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey(f"{RETAIL_SCHEMA}.categories.id", ondelete="RESTRICT"),
        nullable=False,
        comment="Product category.",
    )
    sku: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        comment="Stable stock keeping unit.",
    )
    name: Mapped[str] = mapped_column(
        String(180),
        nullable=False,
        comment="Product display name.",
    )
    unit_cost: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
        comment="Reference acquisition cost in BRL.",
    )
    list_price: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
        comment="Reference list price in BRL.",
    )
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
        comment="Whether the product is available for sale.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Product creation timestamp.",
    )


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'paid', 'shipped', 'delivered', 'cancelled')",
            name="status_allowed",
        ),
        CheckConstraint(
            "channel IN ('web', 'mobile', 'store', 'marketplace')",
            name="channel_allowed",
        ),
        CheckConstraint(
            "discount_amount >= 0",
            name="discount_nonnegative",
        ),
        CheckConstraint(
            "shipping_amount >= 0",
            name="shipping_nonnegative",
        ),
        Index("ix_orders_customer_id", "customer_id"),
        Index("ix_orders_region_id", "region_id"),
        Index("ix_orders_placed_at", "placed_at"),
        Index("ix_orders_status", "status"),
        UniqueConstraint("order_number"),
        {
            "schema": RETAIL_SCHEMA,
            "comment": "E-commerce and retail sales orders.",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
        comment="Surrogate order identifier.",
    )
    order_number: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="Stable business order number.",
    )
    customer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{RETAIL_SCHEMA}.customers.id", ondelete="RESTRICT"),
        nullable=False,
        comment="Customer who placed the order.",
    )
    region_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey(f"{RETAIL_SCHEMA}.regions.id", ondelete="RESTRICT"),
        nullable=False,
        comment="Sales region attributed at order time.",
    )
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Timestamp when the order was placed.",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Current order lifecycle status.",
    )
    channel: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Sales channel used by the customer.",
    )
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
        server_default=text("0"),
        comment="Order-level discount in BRL.",
    )
    shipping_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
        server_default=text("0"),
        comment="Shipping charge in BRL.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Database creation timestamp.",
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price > 0", name="unit_price_positive"),
        CheckConstraint("unit_cost >= 0", name="unit_cost_nonnegative"),
        CheckConstraint(
            "discount_amount >= 0",
            name="discount_nonnegative",
        ),
        CheckConstraint(
            "discount_amount <= quantity * unit_price",
            name="discount_not_above_gross",
        ),
        Index("ix_order_items_order_id", "order_id"),
        Index("ix_order_items_product_id", "product_id"),
        {
            "schema": RETAIL_SCHEMA,
            "comment": "Line items with price and cost snapshots.",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
        comment="Surrogate order-item identifier.",
    )
    order_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{RETAIL_SCHEMA}.orders.id", ondelete="CASCADE"),
        nullable=False,
        comment="Parent order.",
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{RETAIL_SCHEMA}.products.id", ondelete="RESTRICT"),
        nullable=False,
        comment="Product sold.",
    )
    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Number of units sold.",
    )
    unit_price: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
        comment="Unit sale price snapshot in BRL.",
    )
    unit_cost: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
        comment="Unit cost snapshot in BRL.",
    )
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
        server_default=text("0"),
        comment="Line-level discount in BRL.",
    )


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "method IN ('credit_card', 'debit_card', 'pix', 'bank_slip')",
            name="method_allowed",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'failed', 'refunded')",
            name="status_allowed",
        ),
        Index("ix_payments_order_id", "order_id"),
        Index("ix_payments_paid_at", "paid_at"),
        UniqueConstraint("transaction_reference"),
        {
            "schema": RETAIL_SCHEMA,
            "comment": "Payment attempts and successful settlements.",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
        comment="Surrogate payment identifier.",
    )
    order_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(f"{RETAIL_SCHEMA}.orders.id", ondelete="CASCADE"),
        nullable=False,
        comment="Order associated with the payment.",
    )
    transaction_reference: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        comment="Synthetic payment-provider reference.",
    )
    method: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        comment="Payment method.",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="Payment processing status.",
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2),
        nullable=False,
        comment="Payment amount in BRL.",
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Settlement timestamp when approved.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Payment creation timestamp.",
    )


class SalesTarget(Base):
    __tablename__ = "sales_targets"
    __table_args__ = (
        CheckConstraint(
            "EXTRACT(DAY FROM target_month) = 1",
            name="month_starts_on_first_day",
        ),
        CheckConstraint(
            "revenue_target > 0",
            name="revenue_target_positive",
        ),
        CheckConstraint(
            "orders_target > 0",
            name="orders_target_positive",
        ),
        Index("ix_sales_targets_target_month", "target_month"),
        Index("ix_sales_targets_region_id", "region_id"),
        UniqueConstraint("region_id", "target_month"),
        {
            "schema": RETAIL_SCHEMA,
            "comment": "Monthly revenue and order targets by region.",
        },
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
        comment="Surrogate sales-target identifier.",
    )
    region_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey(f"{RETAIL_SCHEMA}.regions.id", ondelete="CASCADE"),
        nullable=False,
        comment="Region receiving the target.",
    )
    target_month: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="First calendar day of the target month.",
    )
    revenue_target: Mapped[Decimal] = mapped_column(
        Numeric(16, 2),
        nullable=False,
        comment="Monthly net-revenue target in BRL.",
    )
    orders_target: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Monthly completed-order target.",
    )
