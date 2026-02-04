## connect to postgres database hosted in railway

import psycopg2

connection_url = "postgresql://postgres:wAQnovYEHldUZBxlvZaUfKekRLsthkMf@yamabiko.proxy.rlwy.net:36616/railway"
conn = psycopg2.connect(connection_url)

print("Connected to database")

conn.close()