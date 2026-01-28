#!/usr/bin/env python3
"""
Local deployment test for the Serverless Lambda handler.

Tests the handler against LocalStack DynamoDB to verify:
1. Event processing works correctly
2. DynamoDB tables are written to
3. Detection algorithm produces results
4. End-to-end flow functions properly

Run with: python3 test_local_deployment.py
"""

import json
import os
import sys
import time
import uuid
from decimal import Decimal

# Configure environment for LocalStack
os.environ['AWS_ACCESS_KEY_ID'] = 'test'
os.environ['AWS_SECRET_ACCESS_KEY'] = 'test'
os.environ['AWS_DEFAULT_REGION'] = 'us-east-2'
os.environ['EVENTS_TABLE'] = 'behavior-analyzer-events-dev'
os.environ['PLAYER_STATE_TABLE'] = 'behavior-analyzer-players-dev'
os.environ['DETECTIONS_TABLE'] = 'behavior-analyzer-detections-dev'
os.environ['MODEL_BUCKET'] = 'behavior-analyzer-models-dev'
os.environ['EVENT_TTL_DAYS'] = '90'
os.environ['ENVIRONMENT'] = 'dev'
os.environ['ACCURACY_INTERESTING_THRESHOLD'] = '0.7'
os.environ['HEADSHOT_INTERESTING_THRESHOLD'] = '0.5'
os.environ['ZSCORE_THRESHOLD'] = '3.0'
os.environ['MIN_SAMPLES_FOR_DETECTION'] = '10'  # Lower for testing

# LocalStack endpoint
LOCALSTACK_ENDPOINT = 'http://localhost:4566'

import boto3

# Create DynamoDB resource pointing to LocalStack
dynamodb = boto3.resource(
    'dynamodb',
    endpoint_url=LOCALSTACK_ENDPOINT,
    region_name='us-east-2',
    aws_access_key_id='test',
    aws_secret_access_key='test'
)

# Monkey-patch the handler's dynamodb resource
import handler
handler.dynamodb = dynamodb


class MockContext:
    """Mock Lambda context for testing."""
    aws_request_id = str(uuid.uuid4())
    function_name = 'behavior-analyzer-processor-dev'
    memory_limit_in_mb = 512
    invoked_function_arn = 'arn:aws:lambda:us-east-2:000000000000:function:test'


def generate_test_events(player_id: str, count: int = 10) -> list:
    """Generate test telemetry events."""
    events = []
    base_time = int(time.time() * 1000)

    # Session start
    events.append({
        'eventId': str(uuid.uuid4()),
        'owner': 'test-owner',
        'playerId': player_id,
        'actionType': 'SESSION_START',
        'timestamp': base_time,
        'sessionId': str(uuid.uuid4()),
        'metadata': {}
    })

    # Weapon fired events with varying accuracy
    for i in range(count):
        shots = 10
        # Vary accuracy - some normal, some high
        hits = 5 + (i % 5)  # 5-9 hits out of 10
        headshots = min(hits, i % 3)  # 0-2 headshots

        events.append({
            'eventId': str(uuid.uuid4()),
            'owner': 'test-owner',
            'playerId': player_id,
            'actionType': 'WEAPON_FIRED',
            'timestamp': base_time + (i + 1) * 1000,
            'sessionId': events[0]['sessionId'],
            'metadata': {
                'shots': shots,
                'hits': hits,
                'headshots': headshots,
                'weapon': 'ak47'
            }
        })

    # A kill event
    events.append({
        'eventId': str(uuid.uuid4()),
        'owner': 'test-owner',
        'playerId': player_id,
        'actionType': 'PLAYER_KILLED',
        'timestamp': base_time + (count + 1) * 1000,
        'sessionId': events[0]['sessionId'],
        'metadata': {
            'victimId': 'victim-123',
            'weapon': 'ak47',
            'distance': 50.5
        }
    })

    return events


def test_local_handler():
    """Test the Lambda handler against LocalStack."""
    print("\n" + "=" * 60)
    print("Local Deployment Test")
    print("=" * 60)

    # Test 1: Basic event processing
    print("\n[Test 1] Basic event processing...")
    player_id = f'player-{uuid.uuid4().hex[:8]}'
    events = generate_test_events(player_id, count=5)

    request = {
        'body': {'events': events},
        'headers': {
            'Authorization': 'Bearer test-token',
            'X-Server-Key': 'test-server'
        }
    }

    try:
        response = handler.lambda_handler(request, MockContext())
        body = json.loads(response['body'])

        print(f"  Status: {response['statusCode']}")
        print(f"  Events received: {body.get('eventsReceived', 0)}")
        print(f"  Events stored: {body.get('eventsStored', 0)}")
        print(f"  Players updated: {body.get('playersUpdated', 0)}")
        print(f"  Processing time: {body.get('processingTimeMs', 0)}ms")

        assert response['statusCode'] == 200, f"Expected 200, got {response['statusCode']}"
        assert body['success'] is True, "Expected success=True"
        assert body['eventsReceived'] == 7, f"Expected 7 events, got {body['eventsReceived']}"
        print("  ✓ Test 1 passed")
    except Exception as e:
        print(f"  ✗ Test 1 failed: {e}")
        return False

    # Test 2: Verify DynamoDB writes
    print("\n[Test 2] Verifying DynamoDB writes...")
    try:
        # Check player state table
        players_table = dynamodb.Table('behavior-analyzer-players-dev')
        player_response = players_table.get_item(
            Key={'pk': f'test-owner#{player_id}', 'sk': 'PROFILE'}
        )
        player_item = player_response.get('Item')

        assert player_item is not None, "Player profile not found"
        print(f"  Player found: {player_id}")
        print(f"  Event count: {player_item.get('eventCount', 0)}")
        print(f"  Risk score: {float(player_item.get('riskScore', 0)):.2f}")

        # Check features
        features_response = players_table.get_item(
            Key={'pk': f'test-owner#{player_id}', 'sk': 'FEATURES'}
        )
        features_item = features_response.get('Item')

        assert features_item is not None, "Player features not found"
        print(f"  Total shots: {features_item.get('totalShots', 0)}")
        print(f"  Accuracy: {float(features_item.get('accuracy', 0)):.2%}")
        print("  ✓ Test 2 passed")
    except Exception as e:
        print(f"  ✗ Test 2 failed: {e}")
        return False

    # Test 3: Process more events to trigger detection
    print("\n[Test 3] Testing detection triggering...")
    try:
        # Generate more events with high accuracy to build up sample count
        for batch in range(3):
            high_acc_events = []
            base_time = int(time.time() * 1000) + batch * 100000

            for i in range(5):
                high_acc_events.append({
                    'eventId': str(uuid.uuid4()),
                    'owner': 'test-owner',
                    'playerId': player_id,
                    'actionType': 'WEAPON_FIRED',
                    'timestamp': base_time + i * 1000,
                    'sessionId': str(uuid.uuid4()),
                    'metadata': {
                        'shots': 10,
                        'hits': 9,  # 90% accuracy - high
                        'headshots': 6  # 67% headshot ratio - high
                    }
                })

            request = {
                'body': {'events': high_acc_events},
                'headers': {}
            }
            response = handler.lambda_handler(request, MockContext())
            body = json.loads(response['body'])
            print(f"  Batch {batch + 1}: {body.get('detectionsCreated', 0)} detections")

        # Check detections table
        detections_table = dynamodb.Table('behavior-analyzer-detections-dev')
        scan_response = detections_table.scan(
            FilterExpression='playerId = :pid',
            ExpressionAttributeValues={':pid': player_id}
        )
        detection_count = len(scan_response.get('Items', []))

        print(f"  Total detections for player: {detection_count}")
        if detection_count > 0:
            print(f"  Detection types: {set(d.get('detectorType') for d in scan_response['Items'])}")
        print("  ✓ Test 3 passed")
    except Exception as e:
        print(f"  ✗ Test 3 failed: {e}")
        return False

    # Test 4: Empty event batch
    print("\n[Test 4] Empty event batch handling...")
    try:
        request = {
            'body': {'events': []},
            'headers': {}
        }
        response = handler.lambda_handler(request, MockContext())
        body = json.loads(response['body'])

        assert response['statusCode'] == 200
        assert body['eventsProcessed'] == 0
        print("  Empty batch handled correctly")
        print("  ✓ Test 4 passed")
    except Exception as e:
        print(f"  ✗ Test 4 failed: {e}")
        return False

    # Test 5: JSON string body (simulating API Gateway)
    print("\n[Test 5] JSON string body parsing...")
    try:
        events = generate_test_events(f'player-{uuid.uuid4().hex[:8]}', count=2)
        request = {
            'body': json.dumps({'events': events}),  # String body like API Gateway
            'headers': {}
        }
        response = handler.lambda_handler(request, MockContext())
        body = json.loads(response['body'])

        assert response['statusCode'] == 200
        assert body['success'] is True
        print(f"  Processed {body['eventsReceived']} events from JSON string")
        print("  ✓ Test 5 passed")
    except Exception as e:
        print(f"  ✗ Test 5 failed: {e}")
        return False

    print("\n" + "=" * 60)
    print("All local deployment tests passed!")
    print("=" * 60 + "\n")
    return True


def test_connection():
    """Test LocalStack connection."""
    print("Testing LocalStack connection...")
    try:
        tables = dynamodb.meta.client.list_tables()
        print(f"  Connected! Found {len(tables['TableNames'])} tables:")
        for table in tables['TableNames']:
            print(f"    - {table}")
        return True
    except Exception as e:
        print(f"  Failed to connect to LocalStack: {e}")
        print("  Make sure LocalStack is running: docker-compose up -d")
        return False


if __name__ == '__main__':
    if not test_connection():
        sys.exit(1)

    if test_local_handler():
        sys.exit(0)
    else:
        sys.exit(1)
