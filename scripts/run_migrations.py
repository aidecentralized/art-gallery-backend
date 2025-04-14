#!/usr/bin/env python
import time
import subprocess
import sys
import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# Wait for database to be ready
max_retries = 10
retry_count = 0
while retry_count < max_retries:
    try:
        print("Checking if database is ready...")
        subprocess.check_call(["python", "manage.py", "check", "--database", "default"])
        print("Database is ready!")
        break
    except subprocess.CalledProcessError:
        retry_count += 1
        print(f"Database not ready yet (attempt {retry_count}/{max_retries}), waiting...")
        time.sleep(5)

if retry_count == max_retries:
    print("Could not connect to database after maximum retries. Exiting.")
    sys.exit(1)

# Run migrations
print("Running migrations...")
subprocess.check_call(["python", "manage.py", "migrate", "--noinput"])

# Create superuser if needed
print("Ensuring superuser exists...")
subprocess.check_call(["python", "manage.py", "ensure_superuser"])

# Collect static files
print("Collecting static files...")
subprocess.check_call(["python", "manage.py", "collectstatic", "--noinput"])

# Set up pglogical replication
def setup_replication():
    conn = None
    cur = None
    try:
        # Get database connection details from environment
        db_host = os.getenv('DB_HOST', 'db')
        db_port = os.getenv('DB_PORT', '5432')
        db_name = os.getenv('DB_NAME', 'postgres')
        db_user = os.getenv('DB_USER', 'postgres')
        db_password = os.getenv('DB_PASSWORD', '')
        
        # Connect to the database
        conn = psycopg2.connect(
            host=db_host,
            port=db_port,
            dbname=db_name,
            user=db_user,
            password=db_password
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        
        # Check if pglogical is installed
        cur.execute("SELECT COUNT(*) FROM pg_extension WHERE extname = 'pglogical'")
        result = cur.fetchone()
        if result is not None and result[0] == 0:
            print("Creating pglogical extension...")
            cur.execute("CREATE EXTENSION IF NOT EXISTS pglogical;")
        
        # Check if we're instance1 or instance2
        is_instance2 = os.getenv('IS_INSTANCE2', 'false').lower() == 'true'
        
        # Get provider host configurations
        this_host = os.getenv('THIS_HOST', 'localhost')
        other_host = os.getenv('OTHER_HOST', 'localhost')
        this_port = os.getenv('THIS_PORT', '5432')
        other_port = os.getenv('OTHER_PORT', '5432')
        
        # Determine node names based on instance
        this_node = 'instance2_node' if is_instance2 else 'instance1_node'
        other_node = 'instance1_node' if is_instance2 else 'instance2_node'
        
        # Try to drop node interface and node if they exist (ignore errors)
        # Check pglogical version
        try:
            cur.execute("SELECT pglogical.alter_node_drop_interface(%s, %s)", (this_node, 'provider_interface'))
        except psycopg2.Error:
            print(f"Node interface for {this_node} doesn't exist or couldn't be dropped - continuing")
            
        try:
            cur.execute("SELECT pglogical.drop_node(%s)", (this_node,))
        except psycopg2.Error:
            print(f"Node {this_node} doesn't exist or couldn't be dropped - continuing")
        
        # Create provider node
        print(f"Creating provider node: {this_node}")
        dsn_string = f"host={this_host} port={this_port} dbname={db_name} user={db_user} password={db_password}"
        cur.execute("""
            SELECT pglogical.create_node(
                node_name := %s,
                dsn := %s
            );
        """, (this_node, dsn_string))
        
        # Add tables to replication set
        print("Adding tables to replication set...")
        cur.execute("""
            SELECT pglogical.replication_set_add_all_tables(
                set_name := 'default',
                schema_names := ARRAY['public'],
                relation_kinds := '{r}'
            );
        """)
        
        # Create subscription to other node if not exists
        print(f"Creating subscription to {other_node}")
        
        # Try to drop subscription if it exists (ignore errors)
        try:
            cur.execute("SELECT pglogical.drop_subscription(%s)", ('subscription_to_' + other_node,))
        except psycopg2.Error:
            print(f"Subscription to {other_node} doesn't exist or couldn't be dropped - continuing")
        
        # Create subscription
        subscription_dsn = f"host={other_host} port={other_port} dbname={db_name} user={db_user} password={db_password}"
        print(f"Connection string: {subscription_dsn}")
        
        cur.execute("""
            SELECT pglogical.create_subscription(
                subscription_name := %s,
                provider_dsn := %s,
                replication_sets := ARRAY['default'],
                synchronize_structure := true,
                synchronize_data := true,
                forward_origins := '{}',
                apply_delay := '0 seconds'
            );
        """, ('subscription_to_' + other_node, subscription_dsn))
        
        print("Bi-directional replication setup completed successfully!")
        
    except Exception as e:
        print(f"Error setting up replication: {e}")
        # Don't raise exception to allow other migration steps to continue
        print("Continuing with other migration steps...")
    finally:
        if cur is not None:
            cur.close()
        if conn is not None:
            conn.close()

# Run replication setup
print("Setting up pglogical replication...")
setup_replication()

print("All migration, static collection, and replication tasks completed successfully!")