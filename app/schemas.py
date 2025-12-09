from pydantic import BaseModel, Field, validator
from typing import Literal, Optional
from datetime import datetime

class TransactionRequest(BaseModel):
    """
    Request schema for transaction risk assessment.
    """
    transaction_id: str = Field(..., description="Unique ID from betting site (for idempotency)")
    user_id: str = Field(..., description="Unique identifier for the user")
    amount: float = Field(..., description="Transaction amount in the specified currency")
    currency: str = Field(default="USD", description="Currency code (ISO 4217)")
    type: Literal['DEPOSIT', 'WITHDRAWAL'] = Field(..., description="Transaction type")
    
    @validator('amount')
    def amount_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('Amount must be greater than 0')
        if v > 1000000:
            raise ValueError('Amount exceeds maximum allowed ($1,000,000)')
        return round(v, 2)
    
    @validator('user_id')
    def user_id_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('User ID cannot be empty')
        if len(v) > 100:
            raise ValueError('User ID too long (max 100 characters)')
        return v.strip()
    
    @validator('currency')
    def currency_valid(cls, v):
        allowed_currencies = ['USD', 'EUR', 'GBP', 'INR']
        if v.upper() not in allowed_currencies:
            raise ValueError(f'Currency must be one of: {", ".join(allowed_currencies)}')
        return v.upper()
    
    @validator('transaction_id')
    def transaction_id_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('Transaction ID cannot be empty')
        if len(v) > 100:
            raise ValueError('Transaction ID too long (max 100 characters)')
        return v.strip()
    
    class Config:
        json_schema_extra = {
            "example": {
                "transaction_id": "txn_67890",
                "user_id": "user_12345",
                "amount": 5000.00,
                "currency": "USD",
                "type": "DEPOSIT"
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


class WagerRequest(BaseModel):
    """
    NEW: Request schema for recording betting activity.
    
    Your betting platform should call this endpoint whenever a user:
    - Places a bet
    - Plays a game round
    - Makes any wagering activity
    """
    user_id: str = Field(..., description="Unique identifier for the user")
    wager_amount: float = Field(..., description="Amount wagered/bet in the specified currency")
    
    @validator('wager_amount')
    def wager_must_be_positive(cls, v):
        if v <= 0:
            raise ValueError('Wager amount must be greater than 0')
        if v > 100000:  # Sanity check: $100k max per wager
            raise ValueError('Wager amount exceeds maximum ($100,000)')
        return round(v, 2)
    
    @validator('user_id')
    def user_id_not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('User ID cannot be empty')
        return v.strip()
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "user_12345",
                "wager_amount": 250.00
            }
        }


class WagerResponse(BaseModel):
    """
    NEW: Response schema for wager recording.
    """
    success: bool = Field(..., description="Whether the wager was recorded successfully")
    user_id: str = Field(..., description="User ID")
    total_wagered_24h: float = Field(..., description="Total amount wagered by user in last 24 hours")
    
    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "user_id": "user_12345",
                "total_wagered_24h": 1250.00
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