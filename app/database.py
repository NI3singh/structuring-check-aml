from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os
from dotenv import load_dotenv

# .env file se password/url uthayega
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Engine create karna (Connection)
engine = create_engine(DATABASE_URL)

# SessionLocal class: Har request ke liye alag database session banayega
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class: Sare models (tables) isse inherit karenge
Base = declarative_base()

# Ye helper function hai jo API ko database dega aur kaam khatam hone par band karega
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()