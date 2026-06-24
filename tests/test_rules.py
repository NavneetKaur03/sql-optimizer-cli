# test_rules.py
# Unit tests for all 4 rules.
# Each rule has at minimum:
#   - One True Positive (rule should fire)
#   - One True Negative (rule shouldn't fire)

import sys
import pytest
import psycopg2

from sql_analyzer.parser import QueryParser
from sql_analyzer.rules import (
    MissingIndexRule,
    CartesianJoinRule,
    NonSARGableRule,
    UnusedCTERule
)

# ---- FIXTURES ----
@pytest.fixture(scope="module")
def conn():
    connection = psycopg2.connect(
        dbname="sqlanalyzer",
        user="postgres",
        password="pass1234",
        host="localhost",
        port="5432"
    )
    yield connection
    connection.close()

@pytest.fixture(scope="module")
def parser():
    return QueryParser()

# --- Missing Index Rule Tests ---
class TestMissingIndexRule:
    """
    Tests for MissingIndexRule:
    True-positive: high cardinality column with no index → should flag.
    True-negative: low cardinality column → should not flag.
    True-negative: clean query with LIMIT → PostgreSQL won't seq scan.
    """

    def test_flags_high_cardinality_unindexed_columns(self, parser, conn):
        """
        customer_id has ~99k distinct values and no index.
        Rule SHOULD fire and suggest CREATE INDEX.
        """
        query = "SELECT * FROM orders WHERE customer_id = 5"
        parsed = parser.parse(query)
        findings = MissingIndexRule().detect(parsed, conn)

        assert len(findings) > 0
        assert any('customer_id' in f.message for f in findings)
        assert any('CREATE INDEX' in f.suggestion for f in findings)
        assert any(f.severity == "HIGH" for f in findings)
    
    def test_does_not_flag_low_cardinality_column(self, parser, conn):
        """
        status has only 4 distinct values.
        PostgreSQL correctly uses seq scan for low cardinality.
        Rule should not fire.
        """
        query = "SELECT * FROM orders WHERE status = 'completed"
        parsed = parser.parse(query)
        findings = MissingIndexRule().detect(parsed, conn)

        assert len(findings) == 0
    
    def test_does_not_flag_primary_key(self, parser, conn):
        """
        id is the primary key — always indexed automatically.
        Rule should not flag it even though it appears in WHERE.
        """
        query = "SELECT * FROM orders WHERE id = 100"
        parsed = parser.parse(query)
        findings = MissingIndexRule().detect(parsed, conn)

        assert not any ('id' in f.message for f in findings)
    
    def test_flags_join_column_without_index(self, parser, conn):
        """
        customer_id used in JOIN ON clause has no index.
        Rule should flag it with a CREATE INDEX suggestion.
        """
        query = (
            "SELECT o.id, c.name FROM orders o "
            "JOIN customers c ON o.customer_id = c.id "
            "WHERE o.amount > 100"
        )        
        parsed = parser.parse(query)
        assert 'customer_id' in parsed.join_columns

        findings = MissingIndexRule().detect(parsed, conn)
        assert len(findings) > 0
        assert any('amount' in f.message for f in findings)
        assert any('CREATE INDEX' in f.suggestion for f in findings)

# ---- Cartesian Join Rule Tests ----
class TestCartesianJoinRule:
    """
    Tests for CartesianJoinRule.
    Two Cartesian join patterns: explicit CROSS JOIN and implicit comma syntax.
    """

    def test_flags_implicit_cartesian_join(self, parser, conn):
        """
        FROM orders, customers with no JOIN condition = implicit Cartesian.
        Rule should fire.
        """
        query = (
            "SELECT * FROM orders, customers"
        )        
        parsed = parser.parse(query)
        findings = CartesianJoinRule().detect(parsed, conn)

        assert len(findings) > 0
        assert findings[0].severity == 'HIGH'
        assert 'Cartesian' in findings[0].message or 'JOIN' in findings[0].message
    
    def test_flags_explicit_cross_join(self, parser, conn):
        """
        CROSS JOIN explicitly stated -> Cartesian product.
        Rule should fire.
        """
        query = "SELECT * FROM orders CROSS JOIN customers"
        parsed = parser.parse(query)
        findings = CartesianJoinRule().detect(parsed, conn)

        assert len(findings) > 0
        assert findings[0].severity == 'HIGH'

    def test_does_not_flag_proper_inner_join(self, parser, conn):
        """
        INNER JOIN with ON condition is correct —> not a Cartesian join.
        Rule should not fire.
        """
        query = (
            "SELECT * FROM orders "
            "JOIN customers ON orders.customer_id = customers.id"
        )
        parsed = parser.parse(query)
        findings = CartesianJoinRule().detect(parsed, conn)

        assert len(findings) == 0

    def test_does_not_flag_single_table_query(self, parser, conn):
        """
        Single table query is not a Cartesian join.
        Rule should not fire.
        """
        query = "SELECT * FROM orders WHERE customer_id = 5"
        parsed = parser.parse(query)
        findings = CartesianJoinRule().detect(parsed, conn)

        assert len(findings) == 0

    def test_does_not_flag_cte_as_cartesian(self, parser, conn):
        """
        CTE name appearing in FROM should not be treated as a real table
        causing a false Cartesian join detection.
        Rule should not fire.
        """
        query = (
            "WITH recent AS (SELECT * FROM orders) "
            "SELECT * FROM recent"
        )
        parsed = parser.parse(query)
        findings = CartesianJoinRule().detect(parsed, conn)

        assert len(findings) == 0


# ---- Non-SARGable Rule Tests ----

class TestNonSARGableRule:
    """
    Tests for NonSARGableRule.
    Three patterns: function on column, CAST on column, leading wildcard LIKE.
    """

    def test_flags_upper_function_on_column(self, parser, conn):
        """
        UPPER(status) in WHERE prevents index usage.
        Rule should fire.
        """
        query = "SELECT * FROM orders WHERE UPPER(status) = 'ACTIVE'"
        parsed = parser.parse(query)
        findings = NonSARGableRule().detect(parsed, conn)

        assert len(findings) > 0
        assert any(f.severity == 'HIGH' for f in findings)
        assert any('UPPER' in f.message for f in findings)

    def test_flags_lower_function_on_column(self, parser, conn):
        """
        LOWER() on a column is equally non-SARGable.
        Rule should fire.
        """
        query = "SELECT * FROM orders WHERE LOWER(status) = 'active'"
        parsed = parser.parse(query)
        findings = NonSARGableRule().detect(parsed, conn)

        assert any('LOWER' in f.message for f in findings)

    def test_flags_leading_wildcard_like(self, parser, conn):
        """
        LIKE '%value' forces full table scan — leading wildcard.
        Rule should fire.
        """
        query = "SELECT * FROM customers WHERE name LIKE '%smith'"
        parsed = parser.parse(query)
        findings = NonSARGableRule().detect(parsed, conn)

        assert len(findings) > 0
        assert any(f.severity == 'MEDIUM' for f in findings)

    def test_does_not_flag_trailing_wildcard_like(self, parser, conn):
        """
        LIKE 'value%' is SARGable — trailing wildcard uses index.
        Rule should not fire.
        """
        query = "SELECT * FROM customers WHERE name LIKE 'smith%'"
        parsed = parser.parse(query)
        findings = NonSARGableRule().detect(parsed, conn)

        # No leading wildcard finding
        assert not any('wildcard' in f.message.lower() for f in findings)

    def test_flags_cast_on_column(self, parser, conn):
        """
        CAST(customer_id AS TEXT) wraps a column — prevents index usage.
        Rule should fire.
        """
        query = "SELECT * FROM orders WHERE CAST(customer_id AS TEXT) = '5'"
        parsed = parser.parse(query)
        findings = NonSARGableRule().detect(parsed, conn)

        assert any('CAST' in f.message for f in findings)

    def test_does_not_flag_cast_on_literal(self, parser, conn):
        """
        CAST('123' AS INT) wraps a literal value, not a column.
        Rule should not fire.
        """
        query = "SELECT * FROM orders WHERE customer_id = CAST('5' AS INT)"
        parsed = parser.parse(query)
        findings = NonSARGableRule().detect(parsed, conn)

        assert not any('CAST' in f.message for f in findings)


# ---- Unused CTE Rule Tests ----

class TestUnusedCTERule:

    def test_flags_unused_cte(self, parser, conn):
        """
        old_orders is defined but never used in main SELECT.
        Rule should fire.
        """
        query = (
            "WITH recent AS (SELECT * FROM orders WHERE created_at > '2024-01-01'), "
            "old_orders AS (SELECT * FROM orders WHERE created_at < '2020-01-01') "
            "SELECT * FROM recent"
        )
        parsed = parser.parse(query)
        findings = UnusedCTERule().detect(parsed, conn)

        assert len(findings) == 1
        assert 'old_orders' in findings[0].message
        assert findings[0].severity == 'LOW'

    def test_does_not_flag_used_cte(self, parser, conn):
        """
        Both CTEs are used in the main SELECT.
        Rule should not fire.
        """
        query = (
            "WITH recent AS (SELECT * FROM orders WHERE created_at > '2024-01-01'), "
            "old_orders AS (SELECT * FROM orders WHERE created_at < '2020-01-01') "
            "SELECT * FROM recent UNION ALL SELECT * FROM old_orders"
        )
        parsed = parser.parse(query)
        findings = UnusedCTERule().detect(parsed, conn)

        assert len(findings) == 0

    def test_does_not_flag_cte_used_in_another_cte(self, parser, conn):
        """
        base CTE is used inside filtered CTE definition, not main SELECT.
        Should not be flagged — it is being used, just not directly in main query.
        """
        query = (
            "WITH base AS (SELECT * FROM orders), "
            "filtered AS (SELECT * FROM base WHERE amount > 100) "
            "SELECT * FROM filtered"
        )
        parsed = parser.parse(query)
        findings = UnusedCTERule().detect(parsed, conn)

        # base is used inside filtered — should not be flagged
        assert not any('base' in f.message for f in findings)

    def test_no_findings_for_query_without_cte(self, parser, conn):
        """
        Query with no WITH block — nothing to check.
        Rule should not fire.
        """
        query = "SELECT * FROM orders WHERE customer_id = 5"
        parsed = parser.parse(query)
        findings = UnusedCTERule().detect(parsed, conn)

        assert len(findings) == 0

    def test_flags_all_unused_ctes(self, parser, conn):
        """
        Both CTEs defined but neither used in main SELECT.
        Rule should fire twice — once per unused CTE.
        """
        query = (
            "WITH a AS (SELECT * FROM orders), "
            "b AS (SELECT * FROM customers) "
            "SELECT 1"
        )
        parsed = parser.parse(query)
        findings = UnusedCTERule().detect(parsed, conn)

        assert len(findings) == 2