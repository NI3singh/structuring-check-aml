from app.redis_client import get_redis
import redis
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- CONFIGURATION (IN CENTS) ---
DAILY_DEPOSIT_LIMIT_CENTS = 10000 * 100      # $10,000
DAILY_WITHDRAWAL_LIMIT_CENTS = 50000 * 100   # $50,000

# --- VELOCITY LIMITS ---
DEPOSIT_VELOCITY_LIMIT_24H = 15       # Max deposits per day (Smurfing)
WITHDRAWAL_VELOCITY_LIMIT_1H = 5      # Max withdrawals per hour
WITHDRAWAL_VELOCITY_LIMIT_24H = 12    # Max withdrawals per day (Reverse Smurfing)

# --- I-BETTING SPECIFIC THRESHOLDS ---
MIN_WAGERING_RATIO = 0.05             # User must wager at least 5% of deposits before withdrawing
QUICK_WITHDRAWAL_WINDOW_SECONDS = 3600  # 1 hour (Rapid round-trip detection)

redis_conn = get_redis()

def check_structuring(user_id: str, amount: float, txn_type: str, transaction_id: str):
    """
    Enhanced AML check with i-betting platform specific detection.
    Detects: Structuring, Smurfing, Layering, Quick Withdrawals, Low Betting Activity
    """
    try:
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
    Enhanced deposit check with betting platform context.
    """
    vol_key = f"user:{user_id}:dep_vol_24h"
    cnt_key = f"user:{user_id}:dep_cnt_24h"
    
    # ATOMIC OPERATIONS
    new_vol_cents = redis_conn.incrby(vol_key, amount_cents)
    redis_conn.expire(vol_key, 86400)
    
    new_count = redis_conn.incr(cnt_key)
    redis_conn.expire(cnt_key, 86400)

    new_vol_dollars = new_vol_cents / 100.0

    # --- RULE 1: HARD DAILY LIMIT (BLOCK) ---
    if new_vol_cents > DAILY_DEPOSIT_LIMIT_CENTS:
        _rollback_deposit(user_id, amount_cents)
        logger.warning(f"BLOCKED DEPOSIT [LIMIT]: User={user_id}, Total=${new_vol_dollars:.2f}")
        
        return {
            "allowed": False,
            "risk_score": 100,
            "reason": f"Daily deposit limit exceeded: ${new_vol_dollars:.2f} > $10,000",
            "total": (new_vol_cents - amount_cents) / 100.0
        }

    # --- RULE 2: FAN-IN SMURFING (BLOCK) ---
    # Many small deposits accumulating to large sum
    if new_count > DEPOSIT_VELOCITY_LIMIT_24H and new_vol_cents > (5000 * 100):
        logger.warning(f"BLOCKED DEPOSIT [SMURFING]: User={user_id}, Count={new_count}, Total=${new_vol_dollars:.2f}")
        
        return {
            "allowed": False,
            "risk_score": 95,
            "reason": f"Structuring detected: {new_count} deposits totaling ${new_vol_dollars:.2f}",
            "total": new_vol_dollars
        }

    # --- RULE 3: JUST UNDER THRESHOLD (WARNING) ---
    if new_vol_cents >= (DAILY_DEPOSIT_LIMIT_CENTS * 0.90):
        logger.warning(f"HIGH RISK DEPOSIT: User={user_id}, Total=${new_vol_dollars:.2f}")
        
        return {
            "allowed": True,
            "risk_score": 80,
            "reason": f"Warning: Cumulative deposits (${new_vol_dollars:.2f}) approaching limit",
            "total": new_vol_dollars
        }

    # --- NEW: Store deposit timestamp for rapid withdrawal detection ---
    timestamp_key = f"user:{user_id}:last_deposit_time"
    redis_conn.set(timestamp_key, int(datetime.now(timezone.utc).timestamp()))
    redis_conn.expire(timestamp_key, 86400)

    logger.info(f"APPROVED DEPOSIT: User={user_id}, Amount=${amount_cents/100:.2f}, Total=${new_vol_dollars:.2f}")
    
    return {
        "allowed": True, 
        "risk_score": 0, 
        "reason": "Safe", 
        "total": new_vol_dollars
    }


def _check_withdrawal(user_id: str, amount_cents: int):
    """
    Enhanced withdrawal check with i-betting specific patterns.
    Detects: Hard limits, Velocity, Reverse Smurfing, Quick Withdrawals, Low Betting Activity
    """
    vol_key = f"user:{user_id}:wd_vol_24h"
    cnt_key_1h = f"user:{user_id}:wd_cnt_1h"
    cnt_key_24h = f"user:{user_id}:wd_cnt_24h"  # NEW: Track daily count
    
    # ATOMIC OPERATIONS
    new_vol_cents = redis_conn.incrby(vol_key, amount_cents)
    redis_conn.expire(vol_key, 86400)
    
    new_count_1h = redis_conn.incr(cnt_key_1h)
    redis_conn.expire(cnt_key_1h, 3600)
    
    new_count_24h = redis_conn.incr(cnt_key_24h)  # NEW
    redis_conn.expire(cnt_key_24h, 86400)
    
    new_vol_dollars = new_vol_cents / 100.0

    # --- RULE 1: HARD DAILY LIMIT (BLOCK) ---
    if new_vol_cents > DAILY_WITHDRAWAL_LIMIT_CENTS:
        _rollback_withdrawal(user_id, amount_cents)
        logger.warning(f"BLOCKED WITHDRAWAL [LIMIT]: User={user_id}, Total=${new_vol_dollars:.2f}")
        
        return {
            "allowed": False,
            "risk_score": 100,
            "reason": f"Daily withdrawal limit exceeded: ${new_vol_dollars:.2f} > $50,000",
            "total": (new_vol_cents - amount_cents) / 100.0
        }

    # --- RULE 2: HOURLY VELOCITY (BLOCK) ---
    if new_count_1h > WITHDRAWAL_VELOCITY_LIMIT_1H:
        _rollback_withdrawal(user_id, amount_cents)
        logger.warning(f"BLOCKED WITHDRAWAL [VELOCITY-1H]: User={user_id}, Count={new_count_1h}/hour")
        
        return {
            "allowed": False,
            "risk_score": 95,
            "reason": f"Velocity exceeded: {new_count_1h} withdrawals in 1 hour (limit: {WITHDRAWAL_VELOCITY_LIMIT_1H})",
            "total": (new_vol_cents - amount_cents) / 100.0
        }

    # --- NEW RULE 3: DAILY VELOCITY - REVERSE SMURFING (BLOCK) ---
    # Detects: $9000 deposit → 9×$1000 withdrawals over 24h
    if new_count_24h > WITHDRAWAL_VELOCITY_LIMIT_24H:
        _rollback_withdrawal(user_id, amount_cents)
        logger.warning(f"BLOCKED WITHDRAWAL [REVERSE-SMURFING]: User={user_id}, Count={new_count_24h}/24h")
        
        return {
            "allowed": False,
            "risk_score": 90,
            "reason": f"Suspicious activity: {new_count_24h} withdrawals in 24 hours (reverse smurfing pattern)",
            "total": (new_vol_cents - amount_cents) / 100.0
        }

    # --- NEW RULE 4: QUICK WITHDRAWAL DETECTION (BLOCK) ---
    # Detects: Deposit 10:00 AM → Withdraw 10:05 AM (layering attack)
    timestamp_key = f"user:{user_id}:last_deposit_time"
    last_deposit_time = redis_conn.get(timestamp_key)
    
    if last_deposit_time:
        time_since_deposit = int(datetime.now(timezone.utc).timestamp()) - int(last_deposit_time)
        
        if time_since_deposit < QUICK_WITHDRAWAL_WINDOW_SECONDS:
            _rollback_withdrawal(user_id, amount_cents)
            logger.warning(f"BLOCKED WITHDRAWAL [QUICK-WITHDRAWAL]: User={user_id}, Time={time_since_deposit}s after deposit")
            
            return {
                "allowed": False,
                "risk_score": 90,
                "reason": f"Quick withdrawal detected: Withdrawal {time_since_deposit//60} minutes after deposit (minimum 1 hour required)",
                "total": (new_vol_cents - amount_cents) / 100.0
            }

    # --- NEW RULE 5: LOW BETTING ACTIVITY CHECK (BLOCK) ---
    # Core i-betting rule: Users must actually BET before withdrawing
    dep_vol_key = f"user:{user_id}:dep_vol_24h"
    wager_vol_key = f"user:{user_id}:wagered_24h"
    
    total_deposited = redis_conn.get(dep_vol_key)
    total_wagered = redis_conn.get(wager_vol_key)
    
    if total_deposited:
        total_deposited_cents = int(total_deposited)
        total_wagered_cents = int(total_wagered) if total_wagered else 0
        
        # Calculate wagering ratio
        if total_deposited_cents > 0:
            wagering_ratio = total_wagered_cents / total_deposited_cents
            
            # If user wagered less than 5% of deposits, block withdrawal
            if wagering_ratio < MIN_WAGERING_RATIO:
                _rollback_withdrawal(user_id, amount_cents)
                logger.warning(
                    f"BLOCKED WITHDRAWAL [LOW-ACTIVITY]: User={user_id}, "
                    f"Deposited=${total_deposited_cents/100:.2f}, "
                    f"Wagered=${total_wagered_cents/100:.2f} ({wagering_ratio*100:.1f}%)"
                )
                
                return {
                    "allowed": False,
                    "risk_score": 85,
                    "reason": f"Insufficient betting activity: Only {wagering_ratio*100:.1f}% of deposits wagered (minimum 5% required)",
                    "total": (new_vol_cents - amount_cents) / 100.0
                }

    # --- RULE 6: HIGH WITHDRAWAL FREQUENCY WARNING ---
    if new_count_24h >= (WITHDRAWAL_VELOCITY_LIMIT_24H * 0.8):
        logger.info(f"MEDIUM RISK WITHDRAWAL: User={user_id}, Count={new_count_24h}/24h")
        
        return {
            "allowed": True,
            "risk_score": 70,
            "reason": f"Warning: High withdrawal frequency ({new_count_24h} in 24 hours)",
            "total": new_vol_dollars
        }

    # All checks passed
    logger.info(f"APPROVED WITHDRAWAL: User={user_id}, Amount=${amount_cents/100:.2f}")
    
    return {
        "allowed": True, 
        "risk_score": 0, 
        "reason": "Safe", 
        "total": new_vol_dollars
    }


def record_wager(user_id: str, wager_amount: float):
    """
    NEW FUNCTION: Records betting activity for wagering ratio calculation.
    Called by betting platform when user places a bet.
    """
    try:
        if wager_amount <= 0:
            return {"success": False, "reason": "Invalid wager amount"}
        
        wager_cents = int(round(wager_amount * 100))
        wager_key = f"user:{user_id}:wagered_24h"
        
        # Atomically increment total wagered amount
        new_total_cents = redis_conn.incrby(wager_key, wager_cents)
        redis_conn.expire(wager_key, 86400)
        
        logger.info(f"WAGER RECORDED: User={user_id}, Amount=${wager_amount:.2f}, Total=${new_total_cents/100:.2f}")
        
        return {
            "success": True,
            "total_wagered": new_total_cents / 100.0
        }
        
    except Exception as e:
        logger.error(f"Error recording wager for user {user_id}: {str(e)}")
        return {"success": False, "reason": "System error"}


def _rollback_deposit(user_id: str, amount_cents: int):
    """Helper to rollback deposit counters"""
    redis_conn.incrby(f"user:{user_id}:dep_vol_24h", -amount_cents)
    redis_conn.decr(f"user:{user_id}:dep_cnt_24h")


def _rollback_withdrawal(user_id: str, amount_cents: int):
    """Helper to rollback withdrawal counters"""
    redis_conn.incrby(f"user:{user_id}:wd_vol_24h", -amount_cents)
    redis_conn.decr(f"user:{user_id}:wd_cnt_1h")
    redis_conn.decr(f"user:{user_id}:wd_cnt_24h")
    redis_conn.decr(f"user:{user_id}:wagered_24h")
    redis_conn.delete(f"user:{user_id}:last_deposit_time")