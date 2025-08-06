# create_tables.py
from main import Base, engine

print("Connecting to the database and dropping existing tables...")
# This will drop all tables defined in your Base.metadata
# Be careful with this command as it will delete all data!
Base.metadata.drop_all(bind=engine)

print("Tables dropped. Now creating new tables...")
# This will create all tables in the correct dependency order
Base.metadata.create_all(bind=engine)

print("Tables created successfully!")