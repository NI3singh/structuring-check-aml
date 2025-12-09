from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean
from sqlalchemy.sql import func
from app.database import Base

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    external_txn_id = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="USD")
    type = Column(String, nullable=False)  # DEPOSIT / WITHDRAWAL
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
    
    # Audit fields
    is_flagged = Column(Boolean, default=False)
    flag_reason = Column(String, nullable=True)

    def __repr__(self):
        return f"<Transaction(id={self.external_txn_id}, user={self.user_id}, amount={self.amount}, type={self.type}, flagged={self.is_flagged})>"