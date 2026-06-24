# benchmark.py
# Measures query runtimes before and after applying suggested fixes.

import sys
import time
import psycopg2

from sql_analyzer.analyzer import Analyzer
from sql_analyzer.parser import QueryParser
from sql_analyzer.rules import MissingIndexRule

from sql_analyzer.config import DB_CONFIG

def get_connection():
    return psycopg2.connect(**DB_CONFIG)

def measure_query_time(query:str, conn, runs: int = 3) -> float:
    """
    Executes query multiple times and returns the average run time.
    Query is executed multiple times because the first run is often slower
    due to disk I/O as PostgreSQL first loads data into buffer cache on first access.
    Subsequent runs hit the cache and are faster.
    """

    times = []
    with conn.cursor() as cur:
        for _ in range(runs):
            start = time.perf_counter()
            cur.execute(query)
            cur.fetchall()
            end = time.perf_counter()
            times.append((end-start)*1000)

    # Return average time excluding first run (first fetch from disk)
    if len(times) > 1:
        return sum(times[1:])/len(times[1:])
    return times[0]

def apply_fix(suggestion: str, conn) -> bool:
    """
    Executes the suggested fixes.
    Returns True if suggested fix is successfully applied, False otherwise.
    """

    if not suggestion.upper().startswith('CREATE INDEX'):
        return False
    
    try: 
        with conn.cursor() as cur:
            cur.execute(suggestion)
        conn.commit()
        return True
    except psycopg2.Error as e:
        conn.rollback()
        print(f"Could not apply fix due to error : {e}.")
        return False
    
def drop_index(suggestion: str, conn) -> None:
    """
    Drops the index created by apply_fix so we can rerun benchmarks
    cleanly without leftover indexes affecting future results.
    """

    import re
    match = re.search(r'ON\s(\w+)\((\w+)\)', suggestion, re.IGNORECASE)
    if not match:
        return
    
    table, column = match.group(1), match.group(2)

    try:
        with conn.cursor() as cur:
            # Find index_name from pg_indexes using column and table name
            cur.execute("""
                SELECT indexname FROM pg_indexes
                WHERE tablename = %s
                AND indexdef LIKE %s
                AND schemaname = 'public'""", (table.lower(), f'%({column.lower()})%'))
            
            row = cur.fetchone()
            if row:
                cur.execute(f'DROP INDEX IF EXISTS {row[0]}')
                conn.commit()
    except psycopg2.Error as e:
        conn.rollback()
        print(f"Could not drop index due to error: {e}")

def run_benchmark(query: str, conn) -> dict:
    """
    Benchmark flow:
    1. Measure initial runtime (no indexes)
    2. Run analyzer to get findings and suggestions
    3. Apply CREATE INDEX suggestions
    4. Measure runtime again (with indexes)
    5. Calculate improvements in runtime
    6. Drop indexes and revert db to original state
    """
    print(f"Benchmarking: {query[:60]}{'...' if len(query) > 60 else ''}")
    print("-"*60)

    # Step 1
    print("Measuring initial runtime (no indexes)...")
    initial_ms = measure_query_time(query, conn)
    print(f"Initial runtime: {initial_ms:.2f}ms")

    # Step 2
    analyzer = Analyzer()
    findings = analyzer.analyze(query, conn)

    if not findings:
        print("No issues found - skipping benchmark.")
        return {
            "query" : query,
            "initial_ms" : initial_ms,
            "optimized_ms" : initial_ms,
            "improvement_pct" : 0,
            "fixes_applied" : []
        }
    
    # Step 3
    fixes_applied = []
    for finding in findings:
        if finding.suggestion.upper().startswith('CREATE INDEX'):
            print(f"Applying fix: {finding.suggestion}")
            if apply_fix(finding.suggestion, conn):
                fixes_applied.append(finding.suggestion)
                print("Fix applied.")
    
    if not fixes_applied:
        print("No auto-applicable fixes for this query.")
        return {
            "query" : query,
            "initial_ms" : initial_ms,
            "optimized_ms" : initial_ms,
            "improvement_pct" : 0,
            "fixes_applied" : []
        }
    
    # Step 4
    print("Measuring optimized runtime...")
    optimized_ms = measure_query_time(query, conn)
    print(f"Optimized runtime: {optimized_ms:.2f}ms")

    # Step 5
    if initial_ms > 0:
        improvement_pct = ((initial_ms-optimized_ms)/initial_ms)*100
    else:
        improvement_pct = 0

    print(f"Improvement percentage: {improvement_pct:.1f}%")

    # Step 6
    for fix in fixes_applied:
        drop_index(fix, conn)

    return {
            "query" : query,
            "initial_ms" : round(initial_ms, 2),
            "optimized_ms" : round(optimized_ms, 2),
            "improvement_pct" : round(improvement_pct, 1),
            "fixes_applied" : fixes_applied
        } 

def print_summary(results: list) -> None:
    """
    Prints a summary of all benchmark results
    """
    print("\n" + "="*60)
    print("BENCHMARK SUMMARY")
    print("="*60)
    print(f"{'Query':<35} {'Before':>8} {'After':>8} {'Improvement':>12}")
    print("-"*60)

    for r in results:
        query_short = r['query'][:32] + '...' if len(r['query']) > 35 else r['query']
        print(
            f"{query_short:<35} "
            f"{r['initial_ms']:>7.1f}ms "
            f"{r['optimized_ms']:>7.1f}ms "
            f"{r['improvement_pct']:>10.1f}% "
        )
    print("="*60)

    avg = sum(r['improvement_pct'] for r in results)/len(results)
    print(f"Average improvement: {avg:.1f}%")

def main():
    """
    Running benchmark on 5 different queries exploring different scenarios.
    These 5 queries are chosen to show clear improvement after indexing.
    All use columns with high cardinality on our 10M row orders table.
    """
    conn = get_connection()

    test_queries = [
        # High cardinality column — will benefit most from index
        "SELECT * FROM orders WHERE customer_id = 5",

        # Two columns — tests composite index scenario
        "SELECT * FROM orders WHERE customer_id = 5 AND product_id = 100",

        # Aggregate query — index helps filter before aggregation
        "SELECT COUNT(*), SUM(amount) FROM orders WHERE customer_id = 42",

        # Range query — index scan with range condition
        "SELECT * FROM orders WHERE customer_id = 7 AND amount > 1000",

        # Join query — index on join column
        "SELECT o.id, o.amount FROM orders o JOIN customers c "
        "ON o.customer_id = c.id WHERE o.customer_id = 99"
    ]

    results = []
    for query in test_queries:
        result = run_benchmark(query, conn)
        results.append(result)        

    print_summary(results)
    conn.close()


if __name__ == "__main__":
    main()