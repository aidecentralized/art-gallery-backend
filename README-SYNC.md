# start the instance1

`docker-compose up -d`

`docker-compose exec web python manage.py createsuperuser`
username: admin@epfl.edu
password: password

# start the instance2

`docker-compose -p mcp_instance2 -f docker-compose.instance2.yml up -d`

Create the super user
`docker-compose -p mcp_instance2 exec web python manage.py createsuperuser`

e.g.
username admin@mit.edu
password: password

to stop
`docker-compose -p mcp_instance2 -f docker-compose.instance2.yml down`

to rebiuld it
`docker-compose -p mcp_instance2 -f docker-compose.instance2.yml up -d --build`

For instance 1
`docker-compose down`
`docker-compose up -d --build`

# to connect to DB running in VM

`docker exec -it mcp_instance2-db-1 psql -U postgres -d mcp_nexus_instance2`

## NOTE TO SELF

`docker-compose -p art-gallery-backend up -d`
`docker-compose -f docker-compose.instance2.yml -p art-gallery-backend-instance2 up -d`

## updated scripts to create superuse

`docker-compose -p art-gallery-backend exec web python manage.py createsuperuser`
`docker-compose -p art-gallery-backend-instance2 exec web python manage.py createsuperuser`
