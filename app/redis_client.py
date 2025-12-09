import redis
import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


pool = redis.ConnectionPool(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    db=0,
    decode_responses=True,
    socket_timeout=5,        
    socket_connect_timeout=5,
    retry_on_timeout=True    
)

redis_client = redis.Redis(connection_pool=pool)

def get_redis():
    try:
        client = redis.Redis(connection_pool=pool)
        # Ek halka sa ping karke dekhenge ki zinda hai ya nahi
        client.ping()
        return client
    except redis.ConnectionError as e:
        logger.error(f"CRITICAL: Cannot connect to Redis: {e}")
        raise e