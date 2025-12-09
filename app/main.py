from fastapi import FastAPI, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from app.database import engine, get_db
from app.models import Base, Transaction
from app.schemas import TransactionRequest, RiskCheckResponse, WagerRequest, WagerResponse
from app.structuring_engine import check_structuring, record_wager
from app.redis_client import get_redis
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create tables if they don't exist
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="AML System - I-Betting Platform",
    version="2.0.0",
    description="Real-time Anti-Money Laundering detection with betting activity correlation"
)

# Health check for monitoring
@app.get("/health")
def health_check():
    """
    Health check endpoint for load balancers and monitoring tools.
    """
    try:
        redis_client = get_redis()
        redis_client.ping()
        redis_status = "healthy"
    except Exception as e:
        redis_status = f"unhealthy: {str(e)}"
    
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0",
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
    
    UPGRADED in V2:
    - Quick withdrawal detection (within 1 hour of deposit)
    - Low betting activity check (must wager 5% of deposits)
    - Reverse smurfing detection (many small withdrawals)
    - Enhanced velocity tracking
    
    Flow:
    1. Check idempotency (duplicate transaction_id)
    2. Validate input (Pydantic schemas)
    3. Run enhanced AML checks (Redis-based)
    4. Save audit log to database
    5. Return risk assessment
    """
    # Idempotency check
    existing_txn = db.query(Transaction).filter(
        Transaction.external_txn_id == request.transaction_id
    ).first()
    
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
        f"Amount=${request.amount}, Type={request.type}, TxnID={request.transaction_id}"
    )
    
    try:
        # Run enhanced AML checks
        result = check_structuring(
            request.user_id, 
            request.amount, 
            request.type,
            request.transaction_id
        )
        
        # Save to database for audit trail
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
        
        # Log metrics
        processing_time = (datetime.now() - start_time).total_seconds()
        
        if not result['allowed']:
            logger.warning(
                f"🚫 BLOCKED: User={request.user_id}, Amount=${request.amount}, "
                f"Reason={result['reason']}, Score={result['risk_score']}, Time={processing_time:.3f}s"
            )
        elif result['risk_score'] >= 60:
            logger.warning(
                f"⚠️  HIGH RISK: User={request.user_id}, Amount=${request.amount}, "
                f"Score={result['risk_score']}, Reason={result['reason']}, Time={processing_time:.3f}s"
            )
        else:
            logger.info(
                f"✅ APPROVED: User={request.user_id}, Amount=${request.amount}, Time={processing_time:.3f}s"
            )
        
        return RiskCheckResponse(
            allowed=result['allowed'],
            risk_score=result['risk_score'],
            flag_reason=result['reason'],
            current_24h_total=result['total']
        )
    
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Database error: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail="Transaction processing failed: Database error"
        )
    
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Transaction processing failed: Internal server error"
        )


@app.post("/api/v1/record-wager", response_model=WagerResponse)
def record_user_wager(request: WagerRequest):
    """
    NEW ENDPOINT: Record betting activity for AML compliance.
    
    This endpoint should be called by your betting platform whenever a user:
    - Places a bet
    - Plays a game round
    - Makes any wagering activity
    
    Purpose: Track betting activity to detect money laundering patterns where
    users deposit money, make minimal bets, and quickly withdraw (layering attack).
    
    Example Integration:
    ```python
    # In your betting platform code:
    user_places_bet(user_id="user123", bet_amount=50.00, game="slots")
    
    # Call AML system:
    requests.post("http://aml-api/api/v1/record-wager", json={
        "user_id": "user123",
        "wager_amount": 50.00
    })
    ```
    """
    logger.info(f"Recording wager: User={request.user_id}, Amount=${request.wager_amount}")
    
    try:
        result = record_wager(request.user_id, request.wager_amount)
        
        if not result['success']:
            raise HTTPException(status_code=400, detail=result.get('reason', 'Invalid wager'))
        
        return WagerResponse(
            success=True,
            user_id=request.user_id,
            total_wagered_24h=result['total_wagered']
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error recording wager: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to record wager")


@app.get("/api/v1/user/{user_id}/stats")
def get_user_stats(user_id: str, db: Session = Depends(get_db)):
    """
    Get comprehensive transaction statistics for a user.
    
    UPGRADED in V2: Now includes wagering activity and compliance ratios.
    
    Useful for:
    - Compliance officers investigating suspicious activity
    - Fraud investigation teams
    - Customer support for account reviews
    """
    try:
        redis_client = get_redis()
        
        # Get current 24h totals from Redis
        deposit_total = redis_client.get(f"user:{user_id}:dep_vol_24h")
        withdrawal_total = redis_client.get(f"user:{user_id}:wd_vol_24h")
        wagered_total = redis_client.get(f"user:{user_id}:wagered_24h")  # NEW
        
        deposit_count = redis_client.get(f"user:{user_id}:dep_cnt_24h")  # NEW
        withdrawal_count_1h = redis_client.get(f"user:{user_id}:wd_cnt_1h")
        withdrawal_count_24h = redis_client.get(f"user:{user_id}:wd_cnt_24h")  # NEW
        
        # Get flagged transactions from database
        flagged_count = db.query(Transaction).filter(
            Transaction.user_id == user_id,
            Transaction.is_flagged == True
        ).count()
        
        # Calculate wagering ratio
        deposit_val = float(deposit_total) / 100 if deposit_total else 0.0
        wagered_val = float(wagered_total) / 100 if wagered_total else 0.0
        wagering_ratio = (wagered_val / deposit_val * 100) if deposit_val > 0 else 0.0
        
        return {
            "user_id": user_id,
            "deposits_24h": {
                "total_amount": deposit_val,
                "transaction_count": int(deposit_count) if deposit_count else 0
            },
            "withdrawals_24h": {
                "total_amount": float(withdrawal_total) / 100 if withdrawal_total else 0.0,
                "transaction_count_1h": int(withdrawal_count_1h) if withdrawal_count_1h else 0,
                "transaction_count_24h": int(withdrawal_count_24h) if withdrawal_count_24h else 0
            },
            "betting_activity_24h": {
                "total_wagered": wagered_val,
                "wagering_ratio_percent": round(wagering_ratio, 2),
                "compliant": wagering_ratio >= 5.0  # Meets 5% minimum
            },
            "compliance": {
                "flagged_transactions_total": flagged_count,
                "risk_status": "compliant" if wagering_ratio >= 5.0 and flagged_count == 0 else "review_required"
            }
        }
    
    except Exception as e:
        logger.error(f"Error fetching user stats: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch user statistics")


@app.get("/api/v1/compliance/flagged-transactions")
def get_flagged_transactions(
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """
    NEW ENDPOINT: Get recent flagged transactions for compliance review.
    
    Returns transactions that were blocked or flagged for manual review.
    """
    try:
        transactions = db.query(Transaction).filter(
            Transaction.is_flagged == True
        ).order_by(
            Transaction.timestamp.desc()
        ).limit(limit).all()
        
        return {
            "count": len(transactions),
            "transactions": [
                {
                    "transaction_id": txn.external_txn_id,
                    "user_id": txn.user_id,
                    "amount": txn.amount,
                    "type": txn.type,
                    "timestamp": txn.timestamp.isoformat(),
                    "flag_reason": txn.flag_reason
                }
                for txn in transactions
            ]
        }
        
    except Exception as e:
        logger.error(f"Error fetching flagged transactions: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to fetch flagged transactions")


@app.get("/")
def home():
    """
    Root endpoint - API information.
    """
    return {
        "message": "AML Structuring Detection System - I-Betting Platform Edition",
        "version": "2.0.0",
        "status": "operational",
        "features": [
            "Structuring & Smurfing Detection",
            "Reverse Smurfing Detection (NEW)",
            "Quick Withdrawal Detection (NEW)",
            "Betting Activity Correlation (NEW)",
            "Low Wagering Ratio Detection (NEW)",
            "Enhanced Velocity Checks (NEW)"
        ],
        "endpoints": {
            "health": "/health",
            "check_transaction": "POST /api/v1/check-transaction",
            "record_wager": "POST /api/v1/record-wager (NEW)",
            "user_stats": "GET /api/v1/user/{user_id}/stats",
            "flagged_transactions": "GET /api/v1/compliance/flagged-transactions (NEW)"
        }
    }