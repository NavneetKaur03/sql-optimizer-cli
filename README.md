# SQL Query Optimisation Analyzer

A command-line tool that analyses PostgreSQL queries, detects performance anti-patterns, and suggests fixes with concrete index DDL. Benchmarked on a 10 million row synthetic dataset with an average query performance improvement of **99.6%**.

---

## What It Detects

| Rule | Severity | What It Catches |
|---|---|---|
| `MissingIndexRule` | HIGH / MEDIUM | Columns used in WHERE or JOIN clauses that have no index and high cardinality on large tables. Uses `pg_stats` and `EXPLAIN` to avoid false positives on low-cardinality columns like status flags. |
| `CartesianJoinRule` | HIGH | Implicit Cartesian joins from comma-separated tables in FROM clause, and explicit CROSS JOIN usage. |
| `NonSARGableRule` | HIGH / MEDIUM | Functions applied to columns in WHERE clause (UPPER, LOWER, YEAR, CAST) that prevent index usage, and leading wildcard LIKE patterns (`LIKE '%value'`). |
| `UnusedCTERule` | LOW | CTEs defined in WITH block but never referenced in the main query or other CTEs. |

---

## Benchmark Results

Tested on a 10 million row PostgreSQL `orders` table with no indexes. Results show query runtime before and after applying the tool's suggested fixes.

| Query | Before | After | Improvement |
|---|---|---|---|
| `SELECT * FROM orders WHERE customer_id = 5` | 113.4ms | 0.7ms | 99.4% |
| `SELECT * FROM orders WHERE customer_id = 5 AND product_id = 100` | 116.9ms | 0.3ms | 99.7% |
| `SELECT COUNT(*), SUM(amount) FROM orders WHERE customer_id = 42` | 112.5ms | 0.3ms | 99.7% |
| `SELECT * FROM orders WHERE customer_id = 7 AND amount > 1000` | 195.0ms | 0.6ms | 99.7% |
| `SELECT o.id, o.amount FROM orders o JOIN customers c ON o.customer_id = c.id` | 123.4ms | 0.4ms | 99.7% |

**Average improvement: 99.6%**

---

## How It Works

```
cli.py
└── Analyzer
      ├── QueryParser       →  Extracts tables, columns, joins, CTEs from raw SQL
      ├── MissingIndexRule  →  Checks pg_indexes and pg_stats, runs EXPLAIN
      ├── CartesianJoinRule →  Detects missing JOIN conditions
      ├── NonSARGableRule   →  Detects function-wrapped columns and wildcards
      └── UnusedCTERule     →  Tracks CTE definitions vs references
```

Each rule receives a `ParsedQuery` object and a live database connection, and returns a list of `Finding` objects with severity, message, and a suggested fix. Findings are sorted by severity before display — HIGH issues appear first.

---

## Project Structure

```
sql-optimizer-cli/
├── src/
│   └── sql_analyzer/
│       ├── __init__.py
│       ├── config.py        # DB config loaded from environment variables
│       ├── parser.py        # Regex-based SQL parser, returns ParsedQuery
│       ├── rules.py         # 4 rule classes inheriting from abstract Rule base
│       ├── analyzer.py      # Orchestrates parser and all rules
│       └── benchmark.py     # Measures query runtime before and after fixes
├── tests/
│   └── test_rules.py        # 20 unit tests, true-positive and true-negative per rule
├── cli.py                   # Entry point, argparse CLI
├── setup_db.py              # Generates synthetic 10M row dataset
├── setup.py                 # Package setup for clean imports
├── requirements.txt
├── .env.example             # Template for environment variables — copy to .env
└── README.md
```

---

## Setup

**Prerequisites:** Python 3.11+, Docker

**1. Clone the repository**

```bash
git clone https://github.com/NavneetKaur03/sql-optimizer-cli.git
cd sql-optimizer-cli
```

**2. Create and activate virtual environment**

```bash
python3 -m venv venv
source venv/bin/activate
```

**3. Install dependencies**

```bash
pip install -e .
pip install -r requirements.txt
```

**4. Start PostgreSQL via Docker**

```bash
docker run --name sql-analyzer-pg \
  -e POSTGRES_PASSWORD=${DB_PASSWORD} \
  -e POSTGRES_DB=${DB_NAME} \
  -p 5432:5432 -d postgres:15
```

Set `DB_PASSWORD` and `DB_NAME` from your `.env` file before running this command.

**5. Configure environment variables**

```bash
cp .env.example .env
```

Open `.env` and set your values. The defaults match the Docker command above.

```
DB_NAME=sqlanalyzer
DB_USER=postgres
DB_PASSWORD=your_password_here
DB_HOST=localhost
DB_PORT=5432
```

**6. Generate the synthetic dataset**

```bash
python3 setup_db.py
```

This inserts 10 million rows into the `orders` table and 100,000 rows into `customers`. Takes 5-10 minutes.

---

## Usage

**Analyse a query — text output**

```bash
python3 cli.py --query "SELECT * FROM orders WHERE customer_id = 5"
```

```
Found 1 issue(s):

------------------------------------------------------------
1. [HIGH] MissingIndexRule
   Issue:      Column 'customer_id' on table 'orders' (10,000,000 rows) has no
               index and ~98,999 distinct values. PostgreSQL is using a Sequential
               Scan — an index would significantly speed up this query.
   Suggestion: CREATE INDEX ON orders(customer_id);
------------------------------------------------------------
```

**Analyse a query — JSON output**

```bash
python3 cli.py --query "SELECT * FROM orders WHERE customer_id = 5" --output json
```

```json
[
  {
    "rule_name": "MissingIndexRule",
    "severity": "HIGH",
    "message": "Column 'customer_id' on table 'orders' (10,000,000 rows) has no index and ~98,999 distinct values. PostgreSQL is using a Sequential Scan — an index would significantly speed up this query.",
    "suggestion": "CREATE INDEX ON orders(customer_id);"
  }
]
```

**Detect a Cartesian join**

```bash
python3 cli.py --query "SELECT * FROM orders, customers"
```

```
Found 1 issue(s):

------------------------------------------------------------
1. [HIGH] CartesianJoinRule
   Issue:      Multiple tables (orders, customers) in FROM clause with no JOIN
               condition. This is an implicit Cartesian join.
   Suggestion: Use explicit JOIN ... ON syntax instead of comma-separated tables.
------------------------------------------------------------
```

**Detect a non-SARGable predicate**

```bash
python3 cli.py --query "SELECT * FROM orders WHERE UPPER(status) = 'ACTIVE'"
```

```
Found 1 issue(s):

------------------------------------------------------------
1. [HIGH] NonSARGableRule
   Issue:      Function UPPER() in WHERE clause prevents index usage. PostgreSQL
               cannot use a B-tree index on a function-wrapped column.
   Suggestion: Store data in a consistent format to avoid UPPER() at query time,
               or create a functional index: CREATE INDEX ON table(upper(column));
------------------------------------------------------------
```

**Detect an unused CTE**

```bash
python3 cli.py --query "WITH recent AS (SELECT * FROM orders), old AS (SELECT * FROM orders WHERE created_at < '2020-01-01') SELECT * FROM recent"
```

```
Found 1 issue(s):

------------------------------------------------------------
1. [LOW] UnusedCTERule
   Issue:      CTE 'old' is defined but never referenced in the main query
               or other CTEs.
   Suggestion: Remove the 'old' CTE to keep the query clean and avoid
               unnecessary parsing overhead.
------------------------------------------------------------
```

**Run benchmarks**

```bash
python3 src/sql_analyzer/benchmark.py
```

---

## Run Tests

```bash
pytest tests/ -v
```

20 tests covering true-positive and true-negative cases for all 4 rules.

---

## Known Limitations

- Subqueries are not parsed — only the main query body is analysed
- Quoted identifiers such as `"table"."column"` are not handled
- Table aliases in WHERE clause are partially supported
- Regex-based parsing is not suitable for deeply nested or complex SQL

---

## Tech Stack

Python 3.11 · PostgreSQL 15 · psycopg2 · pytest · Docker