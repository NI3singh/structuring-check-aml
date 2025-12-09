from redis_client import get_redis
import redis
import logging
import math

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURATION (IN CENTS) ---
# Always calculate money in the smallest unit (Cents) to avoid float math errors
# $10,000.00 -> 1,000,000 cents
DAILY_DEPOSIT_LIMIT_CENTS = 10000 * 100
# $50,000.00 -> 5,000,000 cents
DAILY_WITHDRAWAL_LIMIT_CENTS = 50000 * 100

# --- VELOCITY LIMITS (COUNTS) ---
# If a user deposits more than 15 times in 24h, that's suspicious "Fan-In"
DEPOSIT_VELOCITY_LIMIT_24H = 15 
# Withdrawals per hour limit
WITHDRAWAL_VELOCITY_LIMIT_1H = 5

redis_conn = get_redis()

def check_structuring(user_id: str, amount: float, txn_type: str, transaction_id: str):
    """
    Real-time AML check for structuring/smurfing detection.
    Converts inputs to Cents (Integer) for safe Redis arithmetic.
    """
    try:
        # STEP 1: CONVERT TO CENTS (CRITICAL FIX)
        # Rounding prevents issues like 100.0000001 becoming 10000
        if amount <= 0:
             return {"allowed": False, "risk_score": 100, "reason": "Invalid Amount", "total": 0}
             
        amount_cents = int(round(amount * 100))

        if txn_type == 'WITHDRAWAL':
            return _check_withdrawal(user_id, amount_cents)
        elif txn_type == 'DEPOSIT':
            return _check_deposit(user_id, amount_cents)
        else:
            return {
                "allowed": False,
                "risk_score": 100,
                "reason": f"Invalid transaction type: {txn_type}",
                "total": 0
            }
            
    except redis.RedisError as e:
        logger.error(f"Redis error for user {user_id}: {str(e)}")
        # Fail-Safe: Block transaction if DB is down
        return {
            "allowed": False,
            "risk_score": 100,
            "reason": "System error: Unable to verify transaction history",
            "total": 0
        }
    except Exception as e:
        logger.error(f"Unexpected error for user {user_id}: {str(e)}")
        return {
            "allowed": False,
            "risk_score": 100,
            "reason": "System error: Transaction processing failed",
            "total": 0
        }

def _check_deposit(user_id: str, amount_cents: int):
    """
    Checks for: Hard Limit, Fan-In (Velocity), and Just-Under Patterns.
    """
    # Keys for Volume (Amount) and Velocity (Count)
    vol_key = f"user:{user_id}:dep_vol_24h"
    cnt_key = f"user:{user_id}:dep_cnt_24h"
    
    # ATOMIC OPERATIONS (Using Integers)
    # 1. Update Total Amount
    new_vol_cents = redis_conn.incrby(vol_key, amount_cents)
    redis_conn.expire(vol_key, 86400)
    
    # 2. Update Transaction Count (Velocity)
    new_count = redis_conn.incr(cnt_key)
    redis_conn.expire(cnt_key, 86400)

    # Helper: Convert back to Dollars for Logging/Response
    new_vol_dollars = new_vol_cents / 100.0

    # --- RULE 1: HARD DAILY LIMIT (BLOCK) ---
    if new_vol_cents > DAILY_DEPOSIT_LIMIT_CENTS:
        # Rollback
        redis_conn.incrby(vol_key, -amount_cents)
        redis_conn.decr(cnt_key)
        
        logger.warning(f"BLOCKED DEPOSIT: {user_id}, Total ${new_vol_dollars}")
        return {
            "allowed": False,
            "risk_score": 100,
            "reason": f"Daily Limit Exceeded: ${new_vol_dollars} > $10,000",
            "total": (new_vol_cents - amount_cents) / 100.0
        }

    # --- RULE 2: FAN-IN / SMURFING DETECTION (BLOCK/FLAG) ---
    # Logic: Many small deposits (High Count) accumulating to a significant sum
    # Example: 15 deposits totaling > $5,000
    if new_count > DEPOSIT_VELOCITY_LIMIT_24H and new_vol_cents > (5000 * 100):
        # We flag this as High Risk (Structuring Pattern)
        logger.warning(f"SMURFING DETECTED: {user_id}, Count {new_count}, Total ${new_vol_dollars}")
        return {
            "allowed": False, # Or True with Risk 90 depending on policy
            "risk_score": 95,
            "reason": f"Structuring Alert: High frequency deposits ({new_count}) detected",
            "total": new_vol_dollars
        }

    # --- RULE 3: 'JUST UNDER' PATTERN (WARNING) ---
    # 90% of Limit ($9,000 - $10,000)
    if new_vol_cents >= (DAILY_DEPOSIT_LIMIT_CENTS * 0.90):
        return {
            "allowed": True,
            "risk_score": 80,
            "reason": f"Warning: Cumulative deposits (${new_vol_dollars}) approaching limit",
            "total": new_vol_dollars
        }

    # Safe
    return {
        "allowed": True, 
        "risk_score": 0, 
        "reason": "Safe", 
        "total": new_vol_dollars
    }

def _check_withdrawal(user_id: str, amount_cents: int):
    """
    Checks for: Hard Limit and Withdrawal Velocity.
    """
    vol_key = f"user:{user_id}:wd_vol_24h"
    cnt_key = f"user:{user_id}:wd_cnt_1h"
    
    # Atomic updates
    new_vol_cents = redis_conn.incrby(vol_key, amount_cents)
    redis_conn.expire(vol_key, 86400)
    
    new_count = redis_conn.incr(cnt_key)
    redis_conn.expire(cnt_key, 3600) # 1 Hour Window
    
    new_vol_dollars = new_vol_cents / 100.0

    # Rule 1: Hard Limit
    if new_vol_cents > DAILY_WITHDRAWAL_LIMIT_CENTS:
        # Rollback
        redis_conn.incrby(vol_key, -amount_cents)
        redis_conn.decr(cnt_key)
        return {
            "allowed": False,
            "risk_score": 100,
            "reason": f"Withdrawal Limit Exceeded: ${new_vol_dollars}",
            "total": (new_vol_cents - amount_cents) / 100.0
        }

    # Rule 2: Velocity (Too many withdrawals in 1 hour)
    if new_count > WITHDRAWAL_VELOCITY_LIMIT_1H:
        # Rollback
        redis_conn.incrby(vol_key, -amount_cents)
        redis_conn.decr(cnt_key)
        return {
            "allowed": False,
            "risk_score": 95,
            "reason": f"Velocity Exceeded: {new_count} withdrawals in 1 hour",
            "total": (new_vol_cents - amount_cents) / 100.0
        }

    return {
        "allowed": True, 
        "risk_score": 0, 
        "reason": "Safe", 
        "total": new_vol_dollars
    }