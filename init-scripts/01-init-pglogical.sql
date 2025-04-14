-- Enable pglogical extension
CREATE EXTENSION IF NOT EXISTS pglogical;

-- Create a better setup for pglogical that will be more flexible
CREATE OR REPLACE FUNCTION setup_pglogical() RETURNS void AS $$
DECLARE
    this_host text := current_setting('custom.this_host', true);
    this_port text := current_setting('custom.this_port', true);
    db_name text := current_setting('custom.db_name', true);
    db_user text := current_setting('custom.db_user', true);
    db_password text := current_setting('custom.db_password', true);
    is_instance2 text := current_setting('custom.is_instance2', true);
    node_name text;
    node_exists boolean;
    dsn_string text;
BEGIN
    -- Determine node name based on instance
    IF is_instance2 = 'true' THEN
        node_name := 'instance2_node';
    ELSE
        node_name := 'instance1_node';
    END IF;
    
    -- Check if node exists before attempting to drop it
    SELECT EXISTS (
        SELECT 1 FROM pglogical.node WHERE node_name = node_name
    ) INTO node_exists;
    
    -- Only try to drop node if it exists
    IF node_exists THEN
        -- Try to drop the node directly - pglogical will handle dropping interfaces internally
        BEGIN
            PERFORM pglogical.drop_node(node_name);
            RAISE NOTICE 'Successfully dropped existing node %', node_name;
        EXCEPTION
            WHEN OTHERS THEN
                RAISE NOTICE 'Error dropping node %: %', node_name, SQLERRM;
        END;
    END IF;
    
    -- Build connection string using format() function
    dsn_string := format('host=%s port=%s dbname=%s user=%s password=%s', 
                         this_host, this_port, db_name, db_user, db_password);
    
    -- Create node
    PERFORM pglogical.create_node(
        node_name := node_name,
        dsn := dsn_string
    );
    
    RAISE NOTICE 'Successfully created pglogical node: %', node_name;
END;
$$ LANGUAGE plpgsql;

-- The function will be called from the application when needed
-- with proper environment variable values