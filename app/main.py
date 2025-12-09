from fastapi import FastAPI, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.database import engine, get_db
from app.models import Base, Transaction
from app.schemas import TransactionRequest, RiskCheckResponse
from app.structuring_engine import check_structuring
from app.redis_client import get_redis
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="AML System - Structuring Module",
    version="1.0.0",
    description="Real-time Anti-Money Laundering detection for i-betting platforms"
)

# Health check for monitoring
@app.get("/health")
def health_check():
    """
    Health check endpoint for load balancers and monitoring tools.
    """
    try:
        # Check Redis connection
        redis_client = get_redis()
        redis_client.ping()
        redis_status = "healthy"
    except Exception as e:
        redis_status = f"unhealthy: {str(e)}"
    
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "services": {
            "redis": redis_status
        }
    }


@app.post("/api/v1/check-transaction", response_model=RiskCheckResponse)
def check_transaction(
    request: TransactionRequest, 
    db: Session = Depends(get_db)
):
    """
    Main endpoint for real-time transaction risk assessment.
    
    Flow:
    1. Validate input (handled by Pydantic)
    2. Run structuring checks (Redis-based)
    3. Save audit log to database
    4. Return risk assessment
    
    Args:
        request: Transaction details (user_id, amount, currency, type)
        db: Database session (auto-injected)
    
    Returns:
        RiskCheckResponse: Risk assessment with allow/deny decision
    """
    existing_txn = db.query(Transaction).filter(Transaction.external_txn_id == request.transaction_id).first()
    if existing_txn:
        logger.info(f"Duplicate transaction received: {request.transaction_id}")
        return RiskCheckResponse(
            allowed=not existing_txn.is_flagged,
            risk_score=100 if existing_txn.is_flagged else 0,
            flag_reason=existing_txn.flag_reason,
            current_24h_total=0 
        )
    
    start_time = datetime.now()
    
    logger.info(
        f"Processing transaction: User={request.user_id}, "
        f"Amount=${request.amount}, Type={request.type}"
    )
    
    try:
        # Step 1: Run AML checks (Redis-based atomic operations)
        result = check_structuring(
            request.user_id, 
            request.amount, 
            request.type,
            request.transaction_id
        )
        
        # Step 2: Save transaction to database for audit trail
        db_txn = Transaction(
            user_id=request.user_id,
            amount=request.amount,
            currency=request.currency,
            external_txn_id=request.transaction_id,
            type=request.type,
            is_flagged=not result['allowed'],
            flag_reason=result['reason']
        )
        
        db.add(db_txn)
        db.commit()
        db.refresh(db_txn)
        
        # Step 3: Log metrics for monitoring
        processing_time = (datetime.now() - start_time).total_seconds()
        
        if not result['allowed']:
            logger.warning(
                f"BLOCKED: User={request.user_id}, Amount=${request.amount}, "
                f"Reason={result['reason']}, ProcessingTime={processing_time:.3f}s"
            )
        elif result['risk_score'] >= 60:
            logger.warning(
                f"HIGH RISK: User={request.user_id}, Amount=${request.amount}, "
                f"Score={result['risk_score']}, Reason={result['reason']}, "
                f"ProcessingTime={processing_time:.3f}s"
            )
        else:
            logger.info(
                f"APPROVED: User={request.user_id}, Amount=${request.amount}, "
                f"ProcessingTime={processing_time:.3f}s"
            )
        
        # Step 4: Return response
        return RiskCheckResponse(
            allowed=result['allowed'],
            risk_score=result['risk_score'],
            flag_reason=result['reason'],
            current_24h_total=result['total']
        )
    
    except SQLAlchemyError as e:
        # Database error - rollback and return error
        db.rollback()
        logger.error(f"Database error: {str(e)}")
        
        # IMPORTANT: If Redis approved but DB failed, we have inconsistent state
        # In production, consider implementing:
        # 1. Idempotency keys for retries
        # 2. Dead letter queue for failed transactions
        # 3. Compensation transactions to rollback Redis
        
        raise HTTPException(
            status_code=500, 
            detail="Transaction processing failed: Database error"
        )
    
    except Exception as e:
        # Unexpected error
        db.rollback()
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        
        raise HTTPException(
            status_code=500,
            detail="Transaction processing failed: Internal server error"
        )


@app.get("/api/v1/user/{user_id}/stats")
def get_user_stats(user_id: str, db: Session = Depends(get_db)):
    """
    Get transaction statistics for a user.
    Useful for compliance officers and fraud investigation teams.
    """
    try:
        redis_client = get_redis()
        
        # Get current 24h totals from Redis
        deposit_total = redis_client.get(f"user:{user_id}:deposit_24h")
        withdrawal_total = redis_client.get(f"user:{user_id}:withdrawal_24h")
        withdrawal_count = redis_client.get(f"user:{user_id}:withdrawal_1h")
        
        # Get flagged transactions from database
        flagged_count = db.query(Transaction).filter(
            Transaction.user_id == user_id,
            Transaction.is_flagged == True
        ).count()
        
        return {
            "user_id": user_id,
            "current_24h_deposits": float(deposit_total) if deposit_total else 0.0,
            "current_24h_withdrawals": float(withdrawal_total) if withdrawal_total else 0.0,
            "current_1h_withdrawal_count": int(withdrawal_count) if withdrawal_count else 0,
            "total_flagged_transactions": flagged_count
        }
    
    except Exception as e:
        logger.error(f"Error fetching user stats: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch user statistics")


@app.get("/")
def home():
    """
    Root endpoint - API information.
    """
    return {
        "message": "AML Structuring Detection System",
        "version": "1.0.0",
        "status": "operational",
        "endpoints": {
            "health": "/health",
            "check_transaction": "/api/v1/check-transaction",
            "user_stats": "/api/v1/user/{user_id}/stats"
        }
    }