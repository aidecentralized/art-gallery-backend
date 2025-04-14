#!/bin/bash
set -e

# Wait for database to be ready
echo "Waiting for database to be ready..."
RETRIES=10

until PGPASSWORD=$DB_PASSWORD psql -h $DB_HOST -U $DB_USER -d $DB_NAME -c "SELECT 1" > /dev/null 2>&1 || [ $RETRIES -eq 0 ]; do
  echo "Waiting for postgres server, $((RETRIES--)) remaining attempts..."
  sleep 5
done

if [ $RETRIES -eq 0 ]; then
  echo "Database connection failed after multiple attempts"
  exit 1
fi

echo "Database is ready, setting up pglogical..."

# Run the pglogical setup script
python /app/scripts/setup_pglogical.py

echo "PGLogical setup completed" 