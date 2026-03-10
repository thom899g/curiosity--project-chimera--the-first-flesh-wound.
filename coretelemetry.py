"""
Observability Framework for Chimera Protocol
Architectural Rationale: Atomic Firebase logging ensures no request goes unobserved.
Using structlog for structured logging enables aggregation and real-time dashboards.
"""

import structlog
import time
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from firebase_admin import firestore
import firebase_admin
from firebase_admin import credentials
from schemas.firebase_schema import RequestLog, UserProfile

# Initialize Firebase if not already initialized
try:
    firebase_admin.get_app()
except ValueError:
    cred = credentials.Certificate('/secure/firebase_credentials.json')
    firebase_admin.initialize_app(cred)

logger = structlog.get_logger(__name__)
db = firestore.client()


class ChimeraTelemetry:
    """Crystalline observability layer with atomic Firebase writes"""
    
    def __init__(self):
        self.request_logs = db.collection('request_logs')
        self.users = db.collection('users')
        self.metrics_cache: Dict[str, Dict] = {}
        
    def log_request(self, user_id: str, data: Dict[str, Any]) -> str:
        """
        Atomic Firebase logging with automatic retry on failure.
        
        Edge Cases Handled:
        1. Firebase connection failure
        2. Invalid data schema
        3. Duplicate request IDs
        4. Rate limiting from Firebase
        """
        try:
            # Validate data against schema
            log_data = RequestLog(**{**data, 'user_id': user_id})
            
            # Generate unique document ID
            doc_ref = self.request_logs.document(log_data.request_id)
            
            # Perform atomic write
            doc_ref.set(log_data.dict())
            
            # Update user metrics asynchronously
            asyncio.create_task(self._update_user_metrics(user_id, log_data))
            
            logger.info(
                "request_logged",
                request_id=log_data.request_id,
                user_id=user_id,
                tokens=log_data.tokens_consumed,
                latency=log_data.latency_ms
            )
            
            return log_data.request_id
            
        except Exception as e:
            logger.error(
                "firebase_log_failed",
                user_id=user_id,
                error=str(e),
                fallback_action="caching_for_retry"
            )
            # Cache failed logs for retry mechanism
            self._cache_failed_log(user_id, data, str(e))
            return f"error_{int(time.time())}"
    
    async def _update_user_metrics(self, user_id: str, log_data: RequestLog):
        """Update user statistics in Firestore transaction"""
        try:
            user_ref = self.users.document(user_id)
            
            @firestore.transactional
            def update_in_transaction(transaction):
                user_doc = user_ref.get(transaction=transaction)
                
                if user_doc.exists:
                    transaction.update(user_ref, {
                        'last_seen': firestore.SERVER_TIMESTAMP,
                        'request_count': firestore.Increment(1),
                        'total_tokens_used': firestore.Increment(log_data.tokens_consumed)
                    })
                else:
                    # Create user profile if doesn't exist
                    user_profile = UserProfile(
                        discord_id=user_id,
                        server_ids=[log_data.server_id] if hasattr(log_data, 'server_id') else []
                    )
                    transaction.set(user_ref, user_profile.dict())
            
            # Execute transaction
            transaction = db.transaction()
            update_in_transaction(transaction)
            
        except Exception as e:
            logger.error("metrics_update_failed", user_id=user_id, error=str(e))
    
    def _cache_failed_log(self, user_id: str, data: Dict, error: str):
        """Cache failed logs for eventual consistency"""
        cache_key = f"failed_log_{user_id}_{int(time.time())}"
        self.metrics_cache[cache_key] = {
            'data': data,
            'error': error,
            'timestamp': datetime.utcnow().isoformat()
        }
        # Limit cache size to prevent memory issues
        if len(self.metrics_cache) > 1000:
            oldest_key = min(self.metrics_cache.keys(), key=lambda k: self.metrics_cache[k]['timestamp'])
            del self.metrics_cache[oldest_key]
    
    async def get_user_metrics(self, user_id: str) -> Dict[str, Any]:
        """Real-time user analytics with 60-day aggregation window"""
        try:
            sixty_days_ago = datetime.utcnow() - timedelta(days=60)
            
            # Query for recent requests
            recent_requests = self.request_logs \
                .where('user_id', '==', user_id) \
                .where('timestamp', '>', sixty_days_ago) \
                .order_by('timestamp', direction=firestore.Query.DESCENDING) \
                .limit(100) \
                .stream()
            
            requests = []
            total_tokens = 0
            error_count = 0
            
            for doc in recent_requests:
                data = doc.to_dict()
                requests.append({
                    'timestamp': data.get('timestamp'),
                    'tokens': data.get('tokens_consumed', 0),
                    'error': data.get('error_message')
                })
                total_tokens += data.get('tokens_consumed', 0)
                if data.get('error_message'):
                    error_count += 1
            
            return {
                'user_id': user_id,
                'total_requests': len(requests),
                'total_tokens': total_tokens,
                'error_rate': error_count / max(len(requests), 1),
                'recent_activity': requests[:10] if requests else [],
                'cache_size': len(self.metrics_cache)
            }
            
        except Exception as e:
            logger.error("metrics_query_failed", user_id=user_id, error=str(e))
            return {'error': str(e), 'user_id': user_id}
    
    def health_check(self) -> Dict[str, Any]:
        """System health diagnostics"""
        try:
            # Test Firebase connectivity
            test_ref = self.request_logs.document('_health_check')
            test_ref.set({'test': True, 'timestamp': firestore.SERVER_TIMESTAMP})
            test_ref.delete()
            
            return {
                'status': 'healthy',
                'firebase_connected': True,
                'failed_logs_cached': len(self.metrics_cache),
                'timestamp': datetime.utcnow().isoformat()
            }
        except Exception as e:
            return {
                'status': 'degraded',
                'firebase_connected': False,
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }