#!/usr/bin/env python3
"""
Unit tests for the Serverless (Lambda-Only) handler logic.

Tests the pure functions from handler.py without requiring AWS credentials.
Validates feature extraction, event filtering, running statistics,
and API response formatting.

Run with: python3 test_handler.py
"""

import json
import os
import sys
from decimal import Decimal
from collections import defaultdict

# Set environment variables
os.environ['EVENTS_TABLE'] = 'test-events'
os.environ['PLAYER_STATE_TABLE'] = 'test-players'
os.environ['DETECTIONS_TABLE'] = 'test-detections'
os.environ['EVENT_TTL_DAYS'] = '90'
os.environ['ACCURACY_INTERESTING_THRESHOLD'] = '0.7'
os.environ['HEADSHOT_INTERESTING_THRESHOLD'] = '0.5'
os.environ['ZSCORE_THRESHOLD'] = '3.0'
os.environ['MIN_SHOTS_FOR_INTERESTING'] = '5'

# Inline the pure functions from handler.py for testing
# (avoiding boto3 import issues)

ALWAYS_STORE_EVENTS = {
    'SESSION_START',
    'SESSION_END',
    'PLAYER_KILLED',
    'PLAYER_REPORTED',
    'PLAYER_VIOLATION',
}

ACCURACY_INTERESTING_THRESHOLD = float(os.environ.get('ACCURACY_INTERESTING_THRESHOLD', '0.7'))
HEADSHOT_INTERESTING_THRESHOLD = float(os.environ.get('HEADSHOT_INTERESTING_THRESHOLD', '0.5'))
MIN_SHOTS_FOR_INTERESTING = int(os.environ.get('MIN_SHOTS_FOR_INTERESTING', '5'))

# Risk score calculation thresholds
HIGH_DAMAGE_THRESHOLD = 100  # Damage value considered unusually high for a single attack
ACCURACY_RISK_THRESHOLD = 0.5  # Accuracy above this contributes to risk score (50%)
HEADSHOT_RISK_THRESHOLD = 0.3  # Headshot ratio above this contributes to risk score (30%)


def extract_features(events: list, existing_state: dict) -> tuple:
    """Extract behavioral features from events (copied from handler.py for testing)."""
    features = {}
    interesting_events = []

    shots_fired = 0
    shots_hit = 0
    headshots = 0
    kills = 0

    for evt in events:
        action_type = evt.get('actionType', '')
        metadata = evt.get('metadata', {})

        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}

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
            damage = metadata.get('damage', 0)
            if damage > HIGH_DAMAGE_THRESHOLD:
                evt['_interesting_reason'] = f'high_damage:{damage}'
                interesting_events.append(evt)

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

    risk_score = 0.0
    if features['accuracy'] > ACCURACY_RISK_THRESHOLD:
        risk_score += (features['accuracy'] - ACCURACY_RISK_THRESHOLD) * 100
    if features['headshotRatio'] > HEADSHOT_RISK_THRESHOLD:
        risk_score += (features['headshotRatio'] - HEADSHOT_RISK_THRESHOLD) * 100

    features['risk_score'] = min(risk_score, 100.0)

    return features, interesting_events


def create_response(status_code: int, body: dict) -> dict:
    """Create API Gateway response."""
    class DecimalEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, Decimal):
                return float(obj)
            return super().default(obj)

    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
        },
        'body': json.dumps(body, cls=DecimalEncoder)
    }


# ============================================================================
# Test Cases
# ============================================================================

def test_empty_events():
    """Test with no events."""
    features, interesting = extract_features([], {})
    assert features['totalShots'] == 0
    assert features['totalHits'] == 0
    assert features['accuracy'] == 0.0
    assert len(interesting) == 0
    print("✓ test_empty_events")


def test_session_events_always_stored():
    """Test that session events are always stored."""
    events = [
        {'actionType': 'SESSION_START', 'playerId': 'p1'},
        {'actionType': 'SESSION_END', 'playerId': 'p1'},
    ]
    features, interesting = extract_features(events, {})
    assert len(interesting) == 2
    assert all(e['actionType'] in ALWAYS_STORE_EVENTS for e in interesting)
    print("✓ test_session_events_always_stored")


def test_kill_events_always_stored():
    """Test that kill events are always stored."""
    events = [
        {'actionType': 'PLAYER_KILLED', 'playerId': 'p1', 'metadata': {}},
    ]
    features, interesting = extract_features(events, {})
    assert len(interesting) == 1
    assert features['totalKills'] == 1
    print("✓ test_kill_events_always_stored")


def test_normal_accuracy_not_stored():
    """Test that normal accuracy events are not stored."""
    events = [
        {
            'actionType': 'WEAPON_FIRED',
            'playerId': 'p1',
            'metadata': {'shots': 10, 'hits': 5, 'headshots': 1}  # 50% accuracy
        },
    ]
    features, interesting = extract_features(events, {})
    assert len(interesting) == 0  # Not interesting, < 70%
    assert features['totalShots'] == 10  # Stats still updated (actual shots count)
    assert features['totalHits'] == 5
    print("✓ test_normal_accuracy_not_stored")


def test_high_accuracy_stored():
    """Test that high accuracy events are stored."""
    events = [
        {
            'actionType': 'WEAPON_FIRED',
            'playerId': 'p1',
            'metadata': {'shots': 10, 'hits': 8, 'headshots': 2}  # 80% accuracy
        },
    ]
    features, interesting = extract_features(events, {})
    assert len(interesting) == 1
    assert '_interesting_reason' in interesting[0]
    assert 'high_accuracy' in interesting[0]['_interesting_reason']
    print("✓ test_high_accuracy_stored")


def test_high_headshot_stored():
    """Test that high headshot ratio events are stored."""
    events = [
        {
            'actionType': 'WEAPON_FIRED',
            'playerId': 'p1',
            'metadata': {'shots': 10, 'hits': 6, 'headshots': 4}  # 67% HS ratio
        },
    ]
    features, interesting = extract_features(events, {})
    assert len(interesting) == 1
    assert 'high_headshot' in interesting[0]['_interesting_reason']
    print("✓ test_high_headshot_stored")


def test_routine_events_skipped():
    """Test that routine events are processed but not stored."""
    events = [
        {'actionType': 'PLAYER_TICK', 'playerId': 'p1', 'metadata': {'position': [1, 2, 3]}},
        {'actionType': 'PLAYER_INPUT', 'playerId': 'p1', 'metadata': {}},
        {'actionType': 'ITEM_LOOTED', 'playerId': 'p1', 'metadata': {'item': 'wood'}},
    ]
    features, interesting = extract_features(events, {})
    assert len(interesting) == 0  # None stored
    print("✓ test_routine_events_skipped")


def test_incremental_stats_update():
    """Test that stats are updated incrementally."""
    existing = {
        'totalShots': 100,
        'totalHits': 50,
        'totalHeadshots': 10,
        'totalKills': 5,
    }
    events = [
        {
            'actionType': 'WEAPON_FIRED',
            'playerId': 'p1',
            'metadata': {'shots': 10, 'hits': 5, 'headshots': 2}
        },
    ]
    features, _ = extract_features(events, existing)
    assert features['totalShots'] == 110  # 100 + 10 shots
    assert features['totalHits'] == 55    # 50 + 5
    assert features['totalHeadshots'] == 12  # 10 + 2
    print("✓ test_incremental_stats_update")


def test_welford_running_stats():
    """Test Welford's algorithm for running statistics."""
    # First batch
    events1 = [
        {'actionType': 'WEAPON_FIRED', 'playerId': 'p1', 'metadata': {'shots': 10, 'hits': 8}}
    ]
    features1, _ = extract_features(events1, {})
    assert features1['accuracySampleCount'] == 1
    assert features1['accuracyMean'] == 0.8

    # Second batch with existing state
    events2 = [
        {'actionType': 'WEAPON_FIRED', 'playerId': 'p1', 'metadata': {'shots': 10, 'hits': 6}}
    ]
    features2, _ = extract_features(events2, features1)
    assert features2['accuracySampleCount'] == 2
    assert features2['accuracyMean'] == 0.7  # (0.8 + 0.6) / 2
    print("✓ test_welford_running_stats")


def test_risk_score_calculation():
    """Test risk score calculation."""
    events = [
        {'actionType': 'WEAPON_FIRED', 'playerId': 'p1', 'metadata': {'shots': 100, 'hits': 90, 'headshots': 45}}
    ]
    features, _ = extract_features(events, {})
    assert features['risk_score'] > 0
    assert features['accuracy'] == 0.9
    assert features['headshotRatio'] == 0.5
    print("✓ test_risk_score_calculation")


def test_create_response():
    """Test API Gateway response creation."""
    response = create_response(200, {'success': True, 'count': 5})
    assert response['statusCode'] == 200
    assert response['headers']['Content-Type'] == 'application/json'
    body = json.loads(response['body'])
    assert body['success'] is True
    assert body['count'] == 5
    print("✓ test_create_response")


def test_decimal_encoding():
    """Test that Decimal values are encoded correctly."""
    response = create_response(200, {'value': Decimal('3.14')})
    body = json.loads(response['body'])
    assert body['value'] == 3.14
    print("✓ test_decimal_encoding")


def test_mixed_events_filtered():
    """Test a realistic batch of mixed events."""
    events = [
        {'actionType': 'SESSION_START', 'playerId': 'p1'},
        {'actionType': 'WEAPON_FIRED', 'playerId': 'p1', 'metadata': {'shots': 10, 'hits': 4}},  # Normal
        {'actionType': 'PLAYER_TICK', 'playerId': 'p1', 'metadata': {}},
        {'actionType': 'PLAYER_TICK', 'playerId': 'p1', 'metadata': {}},
        {'actionType': 'ITEM_LOOTED', 'playerId': 'p1', 'metadata': {}},
        {'actionType': 'WEAPON_FIRED', 'playerId': 'p1', 'metadata': {'shots': 10, 'hits': 9}},  # High accuracy
        {'actionType': 'PLAYER_KILLED', 'playerId': 'p1', 'metadata': {}},
    ]

    features, interesting = extract_features(events, {})

    assert len(interesting) == 3  # SESSION_START, high accuracy WEAPON_FIRED, PLAYER_KILLED
    action_types = [e['actionType'] for e in interesting]
    assert 'SESSION_START' in action_types
    assert 'PLAYER_KILLED' in action_types
    assert action_types.count('WEAPON_FIRED') == 1
    assert features['totalShots'] == 20  # 10 + 10 shots from two WEAPON_FIRED events
    assert features['totalKills'] == 1
    print("✓ test_mixed_events_filtered")


def test_high_volume_filtering():
    """Test that high-volume events are filtered out."""
    events = [
        {'actionType': 'PLAYER_TICK', 'playerId': 'p1', 'metadata': {'position': [i, i, i]}}
        for i in range(100)
    ]
    features, interesting = extract_features(events, {})
    assert len(interesting) == 0
    print("✓ test_high_volume_filtering")


def test_below_min_shots_not_evaluated():
    """Test events with few shots are not evaluated for interestingness."""
    events = [
        {
            'actionType': 'WEAPON_FIRED',
            'playerId': 'p1',
            'metadata': {'shots': 3, 'hits': 3}  # 100% but only 3 shots
        },
    ]
    features, interesting = extract_features(events, {})
    assert len(interesting) == 0  # Below MIN_SHOTS_FOR_INTERESTING (5)
    print("✓ test_below_min_shots_not_evaluated")


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("Serverless Lambda Handler Tests")
    print("=" * 60 + "\n")

    tests = [
        test_empty_events,
        test_session_events_always_stored,
        test_kill_events_always_stored,
        test_normal_accuracy_not_stored,
        test_high_accuracy_stored,
        test_high_headshot_stored,
        test_routine_events_skipped,
        test_incremental_stats_update,
        test_welford_running_stats,
        test_risk_score_calculation,
        test_create_response,
        test_decimal_encoding,
        test_mixed_events_filtered,
        test_high_volume_filtering,
        test_below_min_shots_not_evaluated,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"✗ {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test.__name__}: {type(e).__name__}: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60 + "\n")

    sys.exit(0 if failed == 0 else 1)
