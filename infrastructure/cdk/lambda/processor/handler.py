"""
Behavior Analyzer Event Processor Lambda

Processes telemetry events from Rust game servers:
1. Updates player state and feature vectors (uses ALL events)
2. Runs anomaly detection
3. Only STORES interesting events (anomalous, combat kills, session bounds)
4. Stores detection results

This is the Option C minimal architecture - optimized for cost (~$50-100/month)
for a single Rust server with up to 500 players.

OPTIMIZATION: Only stores "interesting" events to reduce DynamoDB costs:
- Session boundaries (SESSION_START, SESSION_END)
- Kill events (PLAYER_KILLED)
- Events with anomalous metrics (high accuracy, suspicious patterns)
- All other events update statistics but are NOT stored
"""

import json
import os
import time
import uuid
import logging
from decimal import Decimal
from typing import Any
from collections import defaultdict

import boto3
from boto3.dynamodb.conditions import Key

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')

# Table references
EVENTS_TABLE = os.environ.get('EVENTS_TABLE', 'behavior-analyzer-events-dev')
PLAYER_STATE_TABLE = os.environ.get('PLAYER_STATE_TABLE', 'behavior-analyzer-players-dev')
DETECTIONS_TABLE = os.environ.get('DETECTIONS_TABLE', 'behavior-analyzer-detections-dev')
EVENT_TTL_DAYS = int(os.environ.get('EVENT_TTL_DAYS', '90'))

# Detection thresholds (configurable via environment)
ZSCORE_THRESHOLD = float(os.environ.get('ZSCORE_THRESHOLD', '3.0'))
MIN_SAMPLES_FOR_DETECTION = int(os.environ.get('MIN_SAMPLES_FOR_DETECTION', '100'))

# Interesting event thresholds
ACCURACY_INTERESTING_THRESHOLD = float(os.environ.get('ACCURACY_INTERESTING_THRESHOLD', '0.7'))
HEADSHOT_INTERESTING_THRESHOLD = float(os.environ.get('HEADSHOT_INTERESTING_THRESHOLD', '0.5'))
MIN_SHOTS_FOR_INTERESTING = int(os.environ.get('MIN_SHOTS_FOR_INTERESTING', '5'))

# Risk score calculation thresholds
HIGH_DAMAGE_THRESHOLD = 100  # Damage value considered unusually high for a single attack
ACCURACY_RISK_THRESHOLD = 0.5  # Accuracy above this contributes to risk score (50%)
HEADSHOT_RISK_THRESHOLD = 0.3  # Headshot ratio above this contributes to risk score (30%)

# Events that are ALWAYS stored (session tracking, significant actions)
ALWAYS_STORE_EVENTS = {
    'SESSION_START',
    'SESSION_END',
    'PLAYER_KILLED',      # Kills are always interesting
    'PLAYER_REPORTED',    # Player reports
    'PLAYER_VIOLATION',   # Server violations
}


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal types from DynamoDB."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def lambda_handler(event: dict, context: Any) -> dict:
    """
    Main Lambda handler for event processing.

    Expects events in format:
    {
        "body": {
            "events": [
                {
                    "eventId": "uuid",
                    "owner": "tenant-id",
                    "playerId": "player-123",
                    "actionType": "WEAPON_FIRED",
                    "timestamp": 1234567890000,
                    "sessionId": "session-uuid",
                    "metadata": {"accuracy": 0.85, "headshots": 5}
                }
            ]
        },
        "headers": {
            "Authorization": "Bearer ...",
            "X-Server-Key": "server-key"
        }
    }
    """
    start_time = time.time()
    request_id = context.aws_request_id if context else str(uuid.uuid4())

    logger.info(f"Processing request {request_id}")

    try:
        # Parse request body
        body = event.get('body', {})
        if isinstance(body, str):
            body = json.loads(body)

        events = body.get('events', [])

        if not events:
            return create_response(200, {
                'success': True,
                'eventsProcessed': 0,
                'detectionsCreated': 0,
                'requestId': request_id
            })

        # Extract owner from first event (should be consistent across batch)
        owner = events[0].get('owner', 'unknown')

        logger.info(f"Processing {len(events)} events for owner {owner}")

        # Update player states and extract features (uses ALL events)
        player_updates, interesting_events = update_player_states(events, owner)

        # Run detection on updated players
        detections = run_detection(player_updates, owner)

        # Add events that triggered detections to interesting list
        detection_player_ids = {d['playerId'] for d in detections}
        for evt in events:
            if evt.get('playerId') in detection_player_ids:
                if evt not in interesting_events:
                    interesting_events.append(evt)

        # Only store interesting events (not all events)
        events_stored = 0
        if interesting_events:
            events_stored = store_events(interesting_events, owner)

        # Store any detections
        detections_stored = 0
        if detections:
            detections_stored = store_detections(detections, owner)

        processing_time = (time.time() - start_time) * 1000

        # Log optimization stats
        events_skipped = len(events) - len(interesting_events)
        logger.info(
            f"Processed {len(events)} events: stored {events_stored} interesting, "
            f"skipped {events_skipped} routine, {detections_stored} detections in {processing_time:.2f}ms"
        )

        return create_response(200, {
            'success': True,
            'eventsReceived': len(events),
            'eventsStored': events_stored,
            'eventsSkipped': events_skipped,
            'playersUpdated': len(player_updates),
            'detectionsCreated': detections_stored,
            'processingTimeMs': round(processing_time, 2),
            'requestId': request_id
        })

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON: {e}")
        return create_response(400, {
            'success': False,
            'error': 'Invalid JSON in request body',
            'requestId': request_id
        })
    except Exception as e:
        logger.exception(f"Error processing events: {e}")
        return create_response(500, {
            'success': False,
            'error': str(e),
            'requestId': request_id
        })


def store_events(events: list, owner: str) -> int:
    """Store events in DynamoDB with batching."""
    table = dynamodb.Table(EVENTS_TABLE)
    now = int(time.time() * 1000)
    ttl = int(time.time()) + (EVENT_TTL_DAYS * 24 * 60 * 60)
    stored = 0

    with table.batch_writer() as batch:
        for evt in events:
            try:
                event_id = evt.get('eventId') or str(uuid.uuid4())
                player_id = evt.get('playerId', 'unknown')
                timestamp = evt.get('timestamp', now)
                action_type = evt.get('actionType', 'UNKNOWN')

                item = {
                    'pk': f"{owner}#{player_id}",
                    'sk': f"{timestamp}#{event_id}",
                    'eventId': event_id,
                    'owner': owner,
                    'playerId': player_id,
                    'actionType': action_type,
                    'timestamp': timestamp,
                    'sessionId': evt.get('sessionId'),
                    'metadata': json.dumps(evt.get('metadata', {})),
                    'ttl': ttl,
                }

                batch.put_item(Item=item)
                stored += 1

            except Exception as e:
                logger.warning(f"Failed to store event: {e}")

    return stored


def update_player_states(events: list[dict], owner: str) -> tuple[dict[str, dict], list[dict]]:
    """
    Update player state in DynamoDB with new events.

    Groups events by player, retrieves existing state, extracts features,
    and persists updated profiles and feature vectors.

    Args:
        events: List of telemetry event dictionaries to process.
        owner: Tenant/owner identifier for multi-tenancy isolation.

    Returns:
        A tuple of (player_updates, interesting_events) where:
            - player_updates: Dict mapping player_id to updated feature dict
            - interesting_events: List of events worth storing
    """
    table = dynamodb.Table(PLAYER_STATE_TABLE)
    now = int(time.time() * 1000)

    # Group events by player
    player_events = defaultdict(list)
    for evt in events:
        player_id = evt.get('playerId', 'unknown')
        player_events[player_id].append(evt)

    player_updates = {}
    interesting_events = []

    for player_id, p_events in player_events.items():
        pk = f"{owner}#{player_id}"

        try:
            # Get existing player state
            response = table.get_item(
                Key={'pk': pk, 'sk': 'PROFILE'}
            )
            existing = response.get('Item', {})

            # Extract features from new events AND identify interesting events
            features, player_interesting = extract_features(p_events, existing)
            interesting_events.extend(player_interesting)

            # Update profile
            profile_item = {
                'pk': pk,
                'sk': 'PROFILE',
                'owner': owner,
                'playerId': player_id,
                'lastSeen': now,
                'firstSeen': existing.get('firstSeen', now),
                'eventCount': int(existing.get('eventCount', 0)) + len(p_events),
                'riskScore': Decimal(str(features.get('risk_score', 0.0))),
                'status': existing.get('status', 'MONITOR'),
            }
            table.put_item(Item=profile_item)

            # Update features
            features_item = {
                'pk': pk,
                'sk': 'FEATURES',
                'owner': owner,
                'playerId': player_id,
                'updatedAt': now,
                **{k: Decimal(str(v)) if isinstance(v, float) else v
                   for k, v in features.items()}
            }
            table.put_item(Item=features_item)

            player_updates[player_id] = features

        except Exception as e:
            logger.warning(f"Failed to update player {player_id}: {e}")

    return player_updates, interesting_events


def extract_features(events: list[dict], existing_state: dict[str, Any]) -> tuple[dict[str, Any], list[dict]]:
    """
    Extract behavioral features from telemetry events using incremental statistics.

    This function processes a batch of telemetry events and updates running statistics
    for the player. It uses Welford's online algorithm for numerically stable
    computation of mean and variance, allowing accurate statistics even with
    high-volume streaming data.

    Algorithm:
        1. Iterate through events, accumulating combat statistics (shots, hits, headshots)
        2. Identify "interesting" events that should be stored (high accuracy, kills, etc.)
        3. Merge new statistics with existing player state
        4. Update running mean/variance using Welford's algorithm
        5. Calculate a simplified risk score based on accuracy and headshot ratio

    Args:
        events: List of telemetry event dictionaries, each containing:
            - actionType: Event type string (e.g., 'WEAPON_FIRED', 'PLAYER_KILLED')
            - metadata: Dict with event-specific data (shots, hits, headshots, etc.)
        existing_state: Player's existing feature state from DynamoDB, containing:
            - totalShots, totalHits, totalHeadshots, totalKills
            - accuracySampleCount, accuracyMean, accuracyM2, accuracyStdDev

    Returns:
        A tuple of (features, interesting_events) where:
            - features: Dict of updated player statistics and derived metrics
            - interesting_events: List of events worth storing (anomalous or significant)
    """
    features = {}
    interesting_events = []

    # Combat features
    shots_fired = 0
    shots_hit = 0
    headshots = 0
    kills = 0

    # Track per-event stats for interesting detection
    batch_shots = 0
    batch_hits = 0
    batch_headshots = 0

    for evt in events:
        action_type = evt.get('actionType', '')
        metadata = evt.get('metadata', {})

        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}

        # Check if this event type is always stored
        if action_type in ALWAYS_STORE_EVENTS:
            interesting_events.append(evt)
            if action_type == 'PLAYER_KILLED':
                kills += 1
            continue

        if action_type == 'WEAPON_FIRED':
            evt_shots = metadata.get('shots', 1)
            evt_hits = metadata.get('hits', 0)
            evt_headshots = metadata.get('headshots', 0)

            shots_fired += evt_shots  # Count actual shots, not events
            shots_hit += evt_hits
            headshots += evt_headshots
            batch_shots += evt_shots
            batch_hits += evt_hits
            batch_headshots += evt_headshots

            # Check if this specific event is interesting
            if evt_shots >= MIN_SHOTS_FOR_INTERESTING:
                evt_accuracy = evt_hits / evt_shots if evt_shots > 0 else 0
                evt_hs_ratio = evt_headshots / max(evt_hits, 1)

                if evt_accuracy >= ACCURACY_INTERESTING_THRESHOLD:
                    evt['_interesting_reason'] = f'high_accuracy:{evt_accuracy:.2f}'
                    interesting_events.append(evt)
                elif evt_hs_ratio >= HEADSHOT_INTERESTING_THRESHOLD:
                    evt['_interesting_reason'] = f'high_headshot:{evt_hs_ratio:.2f}'
                    interesting_events.append(evt)

        elif action_type == 'PLAYER_ATTACK':
            # Melee/other attacks - check for suspicious damage
            damage = metadata.get('damage', 0)
            if damage > HIGH_DAMAGE_THRESHOLD:
                evt['_interesting_reason'] = f'high_damage:{damage}'
                interesting_events.append(evt)

        # Note: PLAYER_TICK, PLAYER_INPUT, looting, etc. are processed
        # for stats but NOT stored (too high volume, low value)

    # Calculate accuracy (incremental update)
    existing_shots = float(existing_state.get('totalShots', 0))
    existing_hits = float(existing_state.get('totalHits', 0))

    total_shots = existing_shots + shots_fired
    total_hits = existing_hits + shots_hit

    features['totalShots'] = int(total_shots)
    features['totalHits'] = int(total_hits)
    features['totalHeadshots'] = int(existing_state.get('totalHeadshots', 0)) + headshots
    features['totalKills'] = int(existing_state.get('totalKills', 0)) + kills

    if total_shots > 0:
        features['accuracy'] = total_hits / total_shots
        features['headshotRatio'] = features['totalHeadshots'] / max(total_hits, 1)
    else:
        features['accuracy'] = 0.0
        features['headshotRatio'] = 0.0

    # Update running stats for accuracy (Welford's algorithm)
    if shots_fired > 0:
        session_accuracy = shots_hit / shots_fired
        n = float(existing_state.get('accuracySampleCount', 0))
        mean = float(existing_state.get('accuracyMean', 0.0))
        m2 = float(existing_state.get('accuracyM2', 0.0))

        n += 1
        delta = session_accuracy - mean
        mean += delta / n
        delta2 = session_accuracy - mean
        m2 += delta * delta2

        features['accuracySampleCount'] = int(n)
        features['accuracyMean'] = mean
        features['accuracyM2'] = m2
        features['accuracyStdDev'] = (m2 / n) ** 0.5 if n > 1 else 0.0
    else:
        features['accuracySampleCount'] = int(existing_state.get('accuracySampleCount', 0))
        features['accuracyMean'] = float(existing_state.get('accuracyMean', 0.0))
        features['accuracyM2'] = float(existing_state.get('accuracyM2', 0.0))
        features['accuracyStdDev'] = float(existing_state.get('accuracyStdDev', 0.0))

    # Calculate risk score (simplified)
    risk_score = 0.0
    if features['accuracy'] > ACCURACY_RISK_THRESHOLD:
        risk_score += (features['accuracy'] - ACCURACY_RISK_THRESHOLD) * 100
    if features['headshotRatio'] > HEADSHOT_RISK_THRESHOLD:
        risk_score += (features['headshotRatio'] - HEADSHOT_RISK_THRESHOLD) * 100

    features['risk_score'] = min(risk_score, 100.0)

    return features, interesting_events


def run_detection(player_updates: dict[str, dict], owner: str) -> list[dict]:
    """
    Run anomaly detection algorithms on updated player features.

    Applies multiple detection strategies:
        1. Z-score detection: Flags players whose current accuracy deviates
           significantly from their historical mean (ZSCORE_THRESHOLD standard deviations)
        2. Threshold detection: Flags players with headshot ratio above 50%

    Args:
        player_updates: Dict mapping player_id to their updated feature dict.
        owner: Tenant/owner identifier (unused in current implementation).

    Returns:
        List of detection dictionaries, each containing:
            - playerId: The flagged player's ID
            - detectorType: Type of detector that triggered (e.g., 'ZSCORE_ACCURACY')
            - score: Numeric detection score
            - threshold: Threshold that was exceeded
            - features: Dict of relevant feature values
            - explanation: Human-readable description
    """
    detections = []

    for player_id, features in player_updates.items():
        # Skip players with insufficient data
        sample_count = features.get('accuracySampleCount', 0)
        if sample_count < MIN_SAMPLES_FOR_DETECTION:
            continue

        # Z-score detection for accuracy
        mean = features.get('accuracyMean', 0.0)
        std_dev = features.get('accuracyStdDev', 0.0)
        current = features.get('accuracy', 0.0)

        if std_dev > 0.01:  # Avoid division by near-zero
            z_score = (current - mean) / std_dev

            if abs(z_score) > ZSCORE_THRESHOLD:
                detections.append({
                    'playerId': player_id,
                    'detectorType': 'ZSCORE_ACCURACY',
                    'score': abs(z_score),
                    'threshold': ZSCORE_THRESHOLD,
                    'features': {
                        'accuracy': current,
                        'mean': mean,
                        'stdDev': std_dev,
                        'zScore': z_score,
                    },
                    'explanation': f"Accuracy z-score {z_score:.2f} exceeds threshold {ZSCORE_THRESHOLD}"
                })

        # Headshot ratio detection
        headshot_ratio = features.get('headshotRatio', 0.0)
        if headshot_ratio > 0.5:  # 50% headshot ratio is suspicious
            detections.append({
                'playerId': player_id,
                'detectorType': 'THRESHOLD_HEADSHOT',
                'score': headshot_ratio * 100,
                'threshold': 50.0,
                'features': {
                    'headshotRatio': headshot_ratio,
                    'totalHeadshots': features.get('totalHeadshots', 0),
                    'totalHits': features.get('totalHits', 0),
                },
                'explanation': f"Headshot ratio {headshot_ratio:.1%} exceeds 50% threshold"
            })

    return detections


def store_detections(detections: list, owner: str) -> int:
    """Store detections in DynamoDB."""
    table = dynamodb.Table(DETECTIONS_TABLE)
    now = int(time.time() * 1000)
    ttl = int(time.time()) + (EVENT_TTL_DAYS * 24 * 60 * 60)
    stored = 0

    with table.batch_writer() as batch:
        for detection in detections:
            try:
                detection_id = str(uuid.uuid4())
                player_id = detection['playerId']

                item = {
                    'pk': f"{owner}#{player_id}",
                    'sk': f"{now}#{detection_id}",
                    'detectionId': detection_id,
                    'owner': owner,
                    'playerId': player_id,
                    'detectorType': detection['detectorType'],
                    'score': Decimal(str(detection['score'])),
                    'threshold': Decimal(str(detection['threshold'])),
                    'features': json.dumps(detection['features']),
                    'explanation': detection['explanation'],
                    'status': 'OPEN',
                    'createdAt': now,
                    'ttl': ttl,
                }

                batch.put_item(Item=item)
                stored += 1

            except Exception as e:
                logger.warning(f"Failed to store detection: {e}")

    return stored


def create_response(status_code: int, body: dict) -> dict:
    """Create API Gateway response."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
        },
        'body': json.dumps(body, cls=DecimalEncoder)
    }
