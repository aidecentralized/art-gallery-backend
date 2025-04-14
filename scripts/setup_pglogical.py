#!/usr/bin/env python
"""
Setup script for pglogical replication between database instances.
This script reads environment variables and configures pglogical accordingly.
"""
import os
import sys
import psycopg2
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get database configuration from environment
DB_NAME = os.getenv('DB_NAME', 'mcp_nexus')
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', '5432')

# Get PGLogical specific configuration
IS_INSTANCE2 = os.getenv('IS_INSTANCE2', 'false').lower() == 'true'
THIS_HOST = os.getenv('THIS_HOST', 'localhost')
THIS_PORT = os.getenv('THIS_PORT', '5432')
OTHER_HOST = os.getenv('OTHER_HOST', 'localhost')
OTHER_PORT = os.getenv('OTHER_PORT', '5432')

def setup_pglogical_config():
    """Configure pglogical with the current environment values"""
    try:
        # Connect to the database
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT
        )
        conn.autocommit = True
        cur = conn.cursor()

        # First ensure pglogical extension is created
        cur.execute("CREATE EXTENSION IF NOT EXISTS pglogical;")
        
        # Set custom parameters for our function to use
        cur.execute("SELECT set_config('custom.this_host', %s, false);", (THIS_HOST,))
        cur.execute("SELECT set_config('custom.this_port', %s, false);", (THIS_PORT,))
        cur.execute("SELECT set_config('custom.db_name', %s, false);", (DB_NAME,))
        cur.execute("SELECT set_config('custom.db_user', %s, false);", (DB_USER,))
        cur.execute("SELECT set_config('custom.db_password', %s, false);", (DB_PASSWORD,))
        cur.execute("SELECT set_config('custom.is_instance2', %s, false);", 
                   ('true' if IS_INSTANCE2 else 'false',))
        
        # Call our setup function
        cur.execute("SELECT setup_pglogical();")
        
        # Setup replication if needed
        setup_replication(cur)
        
        # Close connection
        cur.close()
        conn.close()
        
        print("PGLogical configuration completed successfully.")
        return True
        
    except Exception as e:
        print(f"Error setting up pglogical: {e}")
        return False

def setup_replication(cur):
    """Set up replication between nodes"""
    try:
        node_name = "instance2_node" if IS_INSTANCE2 else "instance1_node"
        other_node = "instance1_node" if IS_INSTANCE2 else "instance2_node"
        
        # Create replication set if this is instance 1 (provider)
        if not IS_INSTANCE2:
            # Provider setup
            # First check if replication set exists
            cur.execute("SELECT COUNT(*) FROM pglogical.replication_set WHERE set_name = 'default_set';")
            if cur.fetchone()[0] == 0:
                print("Creating replication set 'default_set'...")
                # Create a replication set
                cur.execute("""
                SELECT pglogical.create_replication_set(
                    set_name := 'default_set',
                    replicate_insert := true,
                    replicate_update := true,
                    replicate_delete := true,
                    replicate_truncate := true
                );
                """)
                
                print("Adding all tables to replication set...")
                # Add all tables to the replication set
                cur.execute("""
                SELECT pglogical.replication_set_add_all_tables(
                    set_name := 'default_set',
                    schema_names := ARRAY['public'],
                    synchronize_data := true
                );
                """)
        else:
            # Subscriber setup - create subscription to the provider
            # First check if subscription exists
            cur.execute("SELECT COUNT(*) FROM pglogical.subscription WHERE sub_name = 'subscription_to_instance1';")
            if cur.fetchone()[0] == 0:
                # Create subscription connection string without quotes in the middle
                provider_dsn = f"host={OTHER_HOST} port={OTHER_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASSWORD}"
                
                print(f"Creating subscription to {OTHER_HOST} with DSN: {provider_dsn}")
                
                # Create the subscription using parameter binding for the DSN
                cur.execute("""
                SELECT pglogical.create_subscription(
                    subscription_name := 'subscription_to_instance1',
                    provider_dsn := %s,
                    replication_sets := ARRAY['default_set'],
                    synchronize_structure := true,
                    synchronize_data := true,
                    forward_origins := '{}'
                );
                """, (provider_dsn,))
                
        print(f"Replication setup completed for {node_name}")
        
    except Exception as e:
        print(f"Error setting up replication: {e}")
        raise

if __name__ == "__main__":
    success = setup_pglogical_config()
    sys.exit(0 if success else 1) 