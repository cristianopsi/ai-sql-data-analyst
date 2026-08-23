from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from random import Random
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from faker import Faker

MONEY_QUANTIZER = Decimal("0.01")
DEFAULT_SEED = 20260823

REGIONS = (
    ("NORTH", "North", 0.08),
    ("NORTHEAST", "Northeast", 0.19),
    ("CENTRAL_WEST", "Central-West", 0.11),
    ("SOUTHEAST", "Southeast", 0.42),
    ("SOUTH", "South", 0.20),
)

CATEGORIES = (
    ("Electronics", 1.20, (80.0, 4500.0)),
    ("Home & Kitchen", 0.94, (25.0, 1800.0)),
    ("Fashion", 1.05, (30.0, 900.0)),
    ("Beauty", 1.08, (15.0, 600.0)),
    ("Sports", 1.07, (25.0, 2200.0)),
    ("Books", 0.88, (18.0, 250.0)),
    ("Toys", 1.03, (20.0, 850.0)),
    ("Automotive", 1.02, (35.0, 2500.0)),
    ("Grocery", 1.10, (8.0, 350.0)),
    ("Pet Supplies", 1.09, (12.0, 700.0)),
    ("Office", 0.97, (10.0, 1200.0)),
    ("Garden", 0.91, (20.0, 1600.0)),
)


@dataclass(frozen=True, slots=True)
class SeedConfig:
    customer_count: int = 5_000
    product_count: int = 1_000
    order_count: int = 50_000
    start_date: date = date(2022, 1, 1)
    reference_date: date = date(2026, 8, 1)
    seed: int = DEFAULT_SEED

    def __post_init__(self) -> None:
        if self.customer_count < len(REGIONS):
            raise ValueError("customer_count must cover every region")

        if self.product_count < len(CATEGORIES):
            raise ValueError("product_count must cover every category")

        if self.order_count < 1:
            raise ValueError("order_count must be positive")

        if self.start_date >= self.reference_date:
            raise ValueError("start_date must be before reference_date")


@dataclass(slots=True)
class TableData:
    columns: tuple[str, ...]
    rows: list[tuple[Any, ...]] = field(default_factory=list)

    def add(self, *values: Any) -> None:
        if len(values) != len(self.columns):
            raise ValueError(f"Expected {len(self.columns)} values, received {len(values)}")

        self.rows.append(values)


@dataclass(slots=True)
class GeneratedDataset:
    tables: dict[str, TableData]
    seed: int
    start_date: date
    reference_date: date

    @property
    def row_counts(self) -> dict[str, int]:
        return {table_name: len(table.rows) for table_name, table in self.tables.items()}


def money(value: Decimal | float | int) -> Decimal:
    return Decimal(str(value)).quantize(
        MONEY_QUANTIZER,
        rounding=ROUND_HALF_UP,
    )


def month_start(value: date) -> date:
    return value.replace(day=1)


def next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)

    return date(value.year, value.month + 1, 1)


def order_date_weight(value: date) -> float:
    year_factor = {
        2022: 0.72,
        2023: 0.84,
        2024: 1.00,
        2025: 1.16,
        2026: 1.28,
    }.get(value.year, 1.0)

    month_factor = {
        1: 0.83,
        2: 0.80,
        3: 0.90,
        4: 0.94,
        5: 0.98,
        6: 1.00,
        7: 1.04,
        8: 1.02,
        9: 1.08,
        10: 1.15,
        11: 1.58,
        12: 1.48,
    }[value.month]

    anomaly_factor = 1.0

    if value.year == 2025 and value.month == 5:
        anomaly_factor = 0.48

    if value.year == 2024 and value.month == 11:
        anomaly_factor = 1.25

    return year_factor * month_factor * anomaly_factor


def _all_dates(start: date, end: date) -> list[date]:
    number_of_days = (end - start).days + 1

    return [start + timedelta(days=offset) for offset in range(number_of_days)]


def _timestamp_for_date(
    value: date,
    random: Random,
) -> datetime:
    seconds = random.randint(8 * 3600, 22 * 3600)

    return datetime.combine(
        value,
        time.min,
        tzinfo=UTC,
    ) + timedelta(seconds=seconds)


def _channel_weights(year: int) -> tuple[float, ...]:
    mobile_growth = max(0, year - 2022)

    return (
        0.38,
        0.20 + (mobile_growth * 0.035),
        max(0.10, 0.27 - (mobile_growth * 0.025)),
        0.15,
    )


def _order_status(
    order_date: date,
    reference_date: date,
    random: Random,
) -> str:
    age_days = (reference_date - order_date).days

    if age_days <= 7:
        return random.choices(
            ("pending", "paid", "shipped", "delivered", "cancelled"),
            weights=(0.25, 0.30, 0.25, 0.15, 0.05),
            k=1,
        )[0]

    if age_days <= 30:
        return random.choices(
            ("pending", "paid", "shipped", "delivered", "cancelled"),
            weights=(0.04, 0.10, 0.18, 0.63, 0.05),
            k=1,
        )[0]

    return random.choices(
        ("paid", "shipped", "delivered", "cancelled"),
        weights=(0.02, 0.03, 0.90, 0.05),
        k=1,
    )[0]


def _category_weights(year: int) -> list[float]:
    years_since_start = max(0, year - 2022)

    return [annual_trend**years_since_start for _, annual_trend, _ in CATEGORIES]


def _empty_tables() -> dict[str, TableData]:
    return {
        "regions": TableData(("id", "code", "name", "country_code")),
        "categories": TableData(("id", "name", "description")),
        "customers": TableData(
            (
                "id",
                "external_id",
                "region_id",
                "full_name",
                "email",
                "document_number",
                "segment",
                "active",
                "created_at",
            )
        ),
        "products": TableData(
            (
                "id",
                "category_id",
                "sku",
                "name",
                "unit_cost",
                "list_price",
                "active",
                "created_at",
            )
        ),
        "orders": TableData(
            (
                "id",
                "order_number",
                "customer_id",
                "region_id",
                "placed_at",
                "status",
                "channel",
                "discount_amount",
                "shipping_amount",
                "created_at",
            )
        ),
        "order_items": TableData(
            (
                "id",
                "order_id",
                "product_id",
                "quantity",
                "unit_price",
                "unit_cost",
                "discount_amount",
            )
        ),
        "payments": TableData(
            (
                "id",
                "order_id",
                "transaction_reference",
                "method",
                "status",
                "amount",
                "paid_at",
                "created_at",
            )
        ),
        "sales_targets": TableData(
            (
                "id",
                "region_id",
                "target_month",
                "revenue_target",
                "orders_target",
            )
        ),
    }


def generate_dataset(
    config: SeedConfig | None = None,
) -> GeneratedDataset:
    active_config = config or SeedConfig()
    random = Random(active_config.seed)
    faker = Faker("pt_BR")
    faker.seed_instance(active_config.seed)
    tables = _empty_tables()

    region_ids = list(range(1, len(REGIONS) + 1))
    region_weights = [region[2] for region in REGIONS]

    for region_id, (code, name, _) in enumerate(
        REGIONS,
        start=1,
    ):
        tables["regions"].add(region_id, code, name, "BR")

    for category_id, (
        category_name,
        annual_trend,
        _,
    ) in enumerate(CATEGORIES, start=1):
        tables["categories"].add(
            category_id,
            category_name,
            (
                f"Synthetic {category_name.lower()} category; "
                f"annual demand factor {annual_trend:.2f}."
            ),
        )

    product_details: dict[
        int,
        tuple[int, Decimal, Decimal],
    ] = {}
    products_by_category: dict[int, list[int]] = {
        category_id: [] for category_id in range(1, len(CATEGORIES) + 1)
    }

    product_created_base = datetime.combine(
        active_config.start_date - timedelta(days=365),
        time.min,
        tzinfo=UTC,
    )

    for product_id in range(
        1,
        active_config.product_count + 1,
    ):
        category_id = ((product_id - 1) % len(CATEGORIES)) + 1
        category_name, _, price_range = CATEGORIES[category_id - 1]

        list_price = money(random.uniform(*price_range))
        margin_rate = Decimal(str(random.uniform(0.22, 0.52)))
        unit_cost = money(list_price * (Decimal("1") - margin_rate))
        active = random.random() >= 0.035
        created_at = product_created_base - timedelta(days=random.randint(0, 730))

        tables["products"].add(
            product_id,
            category_id,
            f"SKU-{product_id:06d}",
            f"{category_name} Product {product_id:04d}",
            unit_cost,
            list_price,
            active,
            created_at,
        )

        product_details[product_id] = (
            category_id,
            unit_cost,
            list_price,
        )
        products_by_category[category_id].append(product_id)

    customer_regions: dict[int, int] = {}
    customer_created_base = datetime.combine(
        active_config.start_date,
        time.min,
        tzinfo=UTC,
    )

    for customer_id in range(
        1,
        active_config.customer_count + 1,
    ):
        region_id = random.choices(
            region_ids,
            weights=region_weights,
            k=1,
        )[0]
        segment = random.choices(
            ("consumer", "small_business", "enterprise"),
            weights=(0.80, 0.16, 0.04),
            k=1,
        )[0]
        created_at = customer_created_base - timedelta(days=random.randint(30, 730))

        tables["customers"].add(
            customer_id,
            uuid5(
                NAMESPACE_URL,
                (f"ai-sql-data-analyst/customer/{customer_id}"),
            ),
            region_id,
            faker.name(),
            f"customer{customer_id:05d}@example.test",
            (f"{(active_config.seed + customer_id) % 100_000_000_000:011d}"),
            segment,
            random.random() >= 0.04,
            created_at,
        )

        customer_regions[customer_id] = region_id

    candidate_dates = _all_dates(
        active_config.start_date,
        active_config.reference_date,
    )
    date_weights = [order_date_weight(candidate_date) for candidate_date in candidate_dates]
    order_dates = sorted(
        random.choices(
            candidate_dates,
            weights=date_weights,
            k=active_config.order_count,
        )
    )

    customer_ids = list(range(1, active_config.customer_count + 1))
    customer_repeat_weights = [1 / (customer_id**0.32) for customer_id in customer_ids]

    item_id = 1
    payment_id = 1
    revenue_by_region_month: dict[
        tuple[int, date],
        Decimal,
    ] = {}
    orders_by_region_month: dict[
        tuple[int, date],
        int,
    ] = {}

    for order_id, order_date in enumerate(
        order_dates,
        start=1,
    ):
        customer_id = random.choices(
            customer_ids,
            weights=customer_repeat_weights,
            k=1,
        )[0]
        region_id = customer_regions[customer_id]
        placed_at = _timestamp_for_date(
            order_date,
            random,
        )
        status = _order_status(
            order_date,
            active_config.reference_date,
            random,
        )
        channel = random.choices(
            ("web", "mobile", "store", "marketplace"),
            weights=_channel_weights(order_date.year),
            k=1,
        )[0]
        item_count = random.choices(
            (1, 2, 3, 4, 5),
            weights=(0.08, 0.17, 0.38, 0.25, 0.12),
            k=1,
        )[0]

        selected_products: set[int] = set()
        order_subtotal = Decimal("0")
        category_ids = list(range(1, len(CATEGORIES) + 1))
        category_weights = _category_weights(order_date.year)

        while len(selected_products) < item_count:
            category_id = random.choices(
                category_ids,
                weights=category_weights,
                k=1,
            )[0]
            product_id = random.choice(products_by_category[category_id])
            selected_products.add(product_id)

        for product_id in sorted(selected_products):
            _, reference_cost, reference_price = product_details[product_id]
            year_offset = order_date.year - 2022
            price_factor = Decimal(str(random.uniform(0.93, 1.06) * (1 + (0.045 * year_offset))))
            cost_factor = Decimal(str(1 + (0.035 * year_offset)))
            unit_price = money(reference_price * price_factor)
            unit_cost = money(reference_cost * cost_factor)
            quantity = random.choices(
                (1, 2, 3),
                weights=(0.72, 0.22, 0.06),
                k=1,
            )[0]
            discount_rate = Decimal(
                str(
                    random.choices(
                        (0.0, 0.05, 0.10, 0.15),
                        weights=(0.56, 0.24, 0.15, 0.05),
                        k=1,
                    )[0]
                )
            )
            gross_amount = unit_price * quantity
            line_discount = money(gross_amount * discount_rate)
            order_subtotal += gross_amount - line_discount

            tables["order_items"].add(
                item_id,
                order_id,
                product_id,
                quantity,
                unit_price,
                unit_cost,
                line_discount,
            )
            item_id += 1

        order_discount_rate = Decimal(
            str(
                random.choices(
                    (0.0, 0.03, 0.05),
                    weights=(0.76, 0.16, 0.08),
                    k=1,
                )[0]
            )
        )
        order_discount = money(order_subtotal * order_discount_rate)
        shipping_amount = (
            Decimal("0.00")
            if order_subtotal >= Decimal("300")
            else money(random.choice((15.90, 19.90, 24.90)))
        )
        order_total = money(order_subtotal - order_discount + shipping_amount)

        tables["orders"].add(
            order_id,
            f"ORD-{order_date:%Y%m}-{order_id:08d}",
            customer_id,
            region_id,
            placed_at,
            status,
            channel,
            order_discount,
            shipping_amount,
            placed_at,
        )

        if status == "cancelled":
            payment_status = "failed"
        elif status == "pending":
            payment_status = "pending"
        else:
            payment_status = random.choices(
                ("approved", "refunded"),
                weights=(0.985, 0.015),
                k=1,
            )[0]

        paid_at = (
            placed_at + timedelta(minutes=random.randint(1, 1440))
            if payment_status in {"approved", "refunded"}
            else None
        )

        tables["payments"].add(
            payment_id,
            order_id,
            f"PAY-{order_id:010d}",
            random.choices(
                (
                    "credit_card",
                    "debit_card",
                    "pix",
                    "bank_slip",
                ),
                weights=(0.46, 0.14, 0.32, 0.08),
                k=1,
            )[0],
            payment_status,
            order_total,
            paid_at,
            placed_at,
        )
        payment_id += 1

        if payment_status == "approved":
            aggregation_key = (
                region_id,
                month_start(order_date),
            )
            revenue_by_region_month[aggregation_key] = (
                revenue_by_region_month.get(
                    aggregation_key,
                    Decimal("0"),
                )
                + order_total
            )
            orders_by_region_month[aggregation_key] = (
                orders_by_region_month.get(
                    aggregation_key,
                    0,
                )
                + 1
            )

    target_id = 1
    target_month = month_start(active_config.start_date)
    final_target_month = month_start(active_config.reference_date)

    while target_month <= final_target_month:
        for region_id in region_ids:
            aggregation_key = (
                region_id,
                target_month,
            )
            actual_revenue = revenue_by_region_month.get(
                aggregation_key,
                Decimal("0"),
            )
            actual_orders = orders_by_region_month.get(
                aggregation_key,
                0,
            )
            target_factor = Decimal(str(random.uniform(0.94, 1.10)))
            revenue_target = max(
                Decimal("10000.00"),
                money(
                    max(
                        actual_revenue,
                        Decimal("10000"),
                    )
                    * target_factor
                ),
            )
            orders_target = max(
                25,
                round(max(actual_orders, 25) * float(target_factor)),
            )

            tables["sales_targets"].add(
                target_id,
                region_id,
                target_month,
                revenue_target,
                orders_target,
            )
            target_id += 1

        target_month = next_month(target_month)

    return GeneratedDataset(
        tables=tables,
        seed=active_config.seed,
        start_date=active_config.start_date,
        reference_date=active_config.reference_date,
    )
