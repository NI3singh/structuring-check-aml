from pydantic import BaseModel, Field, validator
from typing import Literal, Optional
from datetime import datetime

class TransactionRequest(BaseModel):
    """
    Request schema for transaction risk assessment.
    """
    transaction_id: str = Field(..., description="Unique ID from betting site")
    user_id: str = Field(..., description="Unique identifier for the user")
    amount: float = Field(..., description="Transaction amount in the specified currency")
    currency: str = Field(default="USD", description="Currency code (ISO 4217)")
    type: Literal['DEPOSIT', 'WITHDRAWAL'] = Field(..., description="Transaction type")
    
    # Validators
    @validator('amount')
    def amount_must_be_positive(cls, v):
        """
        Ensure transaction amount is positive.
        Prevents negative amounts that could be used to manipulate limits.
        """
        if v <= 0:
            raise ValueError('Amount must be greater than 0')
        if v > 1000000:  # Sanity check: $1M max per transaction
            raise ValueError('Amount exceeds maximum allowed ($1,000,000)')
        return round(v, 2)  # Round to 2 decimal places for currency
    
    @validator('user_id')
    def user_id_not_empty(cls, v):
        """
        Ensure user_id is not empty or whitespace-only.
        """
        if not v or not v.strip():
            raise ValueError('User ID cannot be empty')
        if len(v) > 100:  # Sanity check
            raise ValueError('User ID too long (max 100 characters)')
        return v.strip()
    
    @validator('currency')
    def currency_valid(cls, v):
        """
        Validate currency code (basic check).
        """
        allowed_currencies = ['USD', 'EUR', 'GBP', 'INR']
        if v.upper() not in allowed_currencies:
            raise ValueError(f'Currency must be one of: {", ".join(allowed_currencies)}')
        return v.upper()
    
    @validator('transaction_id')
    def transaction_id_not_empty(cls, v):
        """
        Ensure transaction_id is not empty or whitespace-only.
        """
        if not v or not v.strip():
            raise ValueError('Transaction ID cannot be empty')
        if len(v) > 100:  # Sanity check
            raise ValueError('Transaction ID too long (max 100 characters)')
        return v.strip()
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "user_12345",
                "amount": 5000.00,
                "currency": "USD",
                "type": "DEPOSIT",
                "transaction_id": "txn_12345"
            }
        }


class RiskCheckResponse(BaseModel):
    """
    Response schema for transaction risk assessment.
    """
    allowed: bool = Field(..., description="Whether the transaction is approved")
    risk_score: int = Field(..., ge=0, le=100, description="Risk score (0=safe, 100=fraud)")
    flag_reason: Optional[str] = Field(None, description="Reason for flagging (if any)")
    current_24h_total: float = Field(..., description="User's cumulative 24h transaction total")
    
    class Config:
        json_schema_extra = {
            "example": {
                "allowed": True,
                "risk_score": 0,
                "flag_reason": "Safe",
                "current_24h_total": 5000.00
            }
        }


class UserStatsResponse(BaseModel):
    """
    Response schema for user transaction statistics.
    """
    user_id: str
    current_24h_deposits: float
    current_24h_withdrawals: float
    current_1h_withdrawal_count: int
    total_flagged_transactions: int
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "user_12345",
                "current_24h_deposits": 8500.00,
                "current_24h_withdrawals": 2000.00,
                "current_1h_withdrawal_count": 2,
                "total_flagged_transactions": 1
            }
        }