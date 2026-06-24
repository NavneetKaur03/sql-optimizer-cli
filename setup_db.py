# setup_db.py
# Runs only once before anything else.
# Creates the synthetic database tables that will be used for testing and benchmarking.

import psycopg2
from faker import Faker
import random
import time

# ------ CONFIG ------
from sql_analyzer.config import DB_CONFIG

TOTAL_ROWS = 10_000_000
BATCH_SIZE = 10_000 # Inserting 10k records at a time to avoid memory issues

fake = Faker()

def get_connection():
    return psycopg2.connect(**DB_CONFIG)

def create_tables(conn):
    with conn.cursor() as cur:
        # Tables will be created fresh so that no indexes exist initially
        cur.execute("DROP TABLE IF EXISTS orders")
        cur.execute("DROP TABLE IF EXISTS customers")

        # No indexes are created here on purpose so as to detect them via rules
        cur.execute("""
            CREATE TABLE orders (
                id SERIAL PRIMARY KEY,
                customer_id INTEGER,
                product_id INTEGER,
                amount NUMERIC(10,2),
                status VARCHAR(20),
                created_at TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE customers (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100),
                city VARCHAR(50),
                email VARCHAR(100)
            );
        """)
    
    conn.commit()
    print("Tables created")

def insert_orders(conn):
    """
    Inserting 10M rows in batches for 10k.
    Batching done because row by row insertion would take hours.
    """

    statuses = ["pending", "completed", "refunded", "cancelled"]

    print(f"Inserting {TOTAL_ROWS:,} rows into orders...")
    start = time.time()

    with conn.cursor() as cur:
        for batch_start in range(0, TOTAL_ROWS, BATCH_SIZE):
            # Building a batch of rows as a list of tuples
            batch = [
                (
                    random.randint(1, 100_000),         # customer_id
                    random.randint(1, 5_000),           # product_id
                    round(random.uniform(10, 5000), 2), # amount
                    random.choice(statuses),            # status
                    fake.date_time_between(             # created_at
                        start_date="-3y", end_date="now"
                    )
                )
                for _ in range(BATCH_SIZE)
            ]

            # executemany sends the whole batch in one DB call
            cur.executemany(
                """
                INSERT INTO ORDERS(customer_id, product_id, amount, status, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """, 
                batch
            )
            conn.commit()
            
            # Progress update after every 500K rows
            rows_done = batch_start + BATCH_SIZE
            if rows_done % 500_000 == 0:
                time_taken = time.time() - start
                print(f"{rows_done:,} rows inserted in {time_taken:.0f}s.")
    
    print(f"Completed. {TOTAL_ROWS:,} rows inserted in {time.time() - start:.0f}s")

def insert_customers(conn):
    "Inserting 100K records into customer table."
    print("Inserting 100,000 records into customers...")

    with conn.cursor() as cur:
        batch = [
            (fake.name(), fake.city(), fake.email())

            for _ in range(100_000)
        ]

        cur.executemany("INSERT INTO customers (name, city, email) VALUES (%s, %s, %s)", batch)

        conn.commit()
        print("100,000 Customers inserted.")


def main():
    conn = get_connection()

    try:
        create_tables(conn)
        insert_customers(conn)
        insert_orders(conn)
        print("Database setup completed.")
    finally:
        conn.close()


if __name__ == '__main__':
    main()