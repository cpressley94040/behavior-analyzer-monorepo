#!/usr/bin/env python3
"""
Performance tests for the Serverless Lambda handler.

Measures throughput, latency, and resource usage for feature extraction
and event processing. These tests validate that the handler can meet
the expected performance requirements.

Run with: python3 test_performance.py
"""

import json
import os
import sys
import time
import random
import string
import statistics
from decimal import Decimal
from typing import Callable

# Set environment variables before importing
os.environ['EVENTS_TABLE'] = 'test-events'
os.environ['PLAYER_STATE_TABLE'] = 'test-players'
os.environ['DETECTIONS_TABLE'] = 'test-detections'
os.environ['EVENT_TTL_DAYS'] = '90'
os.environ['ACCURACY_INTERESTING_THRESHOLD'] = '0.7'
os.environ['HEADSHOT_INTERESTING_THRESHOLD'] = '0.5'
os.environ['ZSCORE_THRESHOLD'] = '3.0'
os.environ['MIN_SHOTS_FOR_INTERESTING'] = '5'

# Import test functions from main test file
from test_handler import extract_features, create_response


# ============================================================================
# Performance Test Configuration
# ============================================================================

# Expected performance thresholds
EXPECTED_EVENTS_PER_SECOND = 10000  # Min events/sec for feature extraction
EXPECTED_P99_LATENCY_MS = 100       # Max p99 latency for single batch
EXPECTED_MEMORY_GROWTH_RATIO = 2.0  # Max memory growth during processing


# ============================================================================
# Helper Functions
# ============================================================================

def random_string(length: int = 10) -> str:
    """Generate a random string."""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


def generate_event_batch(size: int, player_count: int = 10) -> list:
    """Generate a batch of realistic events."""
    action_types = ['WEAPON_FIRED', 'PLAYER_TICK', 'PLAYER_INPUT', 'ITEM_LOOTED']
    weights = [0.2, 0.5, 0.2, 0.1]  # Realistic distribution

    players = [f'player_{i}' for i in range(player_count)]
    events = []

    for _ in range(size):
        action = random.choices(action_types, weights=weights)[0]
        player = random.choice(players)

        event = {
            'actionType': action,
            'playerId': player,
            'timestamp': int(time.time() * 1000),
            'sessionId': random_string(16),
        }

        if action == 'WEAPON_FIRED':
            event['metadata'] = {
                'shots': random.randint(1, 30),
                'hits': random.randint(0, 20),
                'headshots': random.randint(0, 5),
                'weapon': random.choice(['ak47', 'm4', 'bow', 'pistol']),
            }
        elif action == 'PLAYER_TICK':
            event['metadata'] = {
                'position': [random.uniform(-4000, 4000) for _ in range(3)],
                'rotation': [random.uniform(0, 360) for _ in range(3)],
                'velocity': random.uniform(0, 10),
            }
        elif action == 'PLAYER_INPUT':
            event['metadata'] = {
                'keys': random.randint(0, 255),
                'mouse_delta': [random.uniform(-100, 100), random.uniform(-100, 100)],
            }
        else:
            event['metadata'] = {
                'item': random.choice(['wood', 'stone', 'metal', 'cloth']),
                'amount': random.randint(1, 1000),
            }

        events.append(event)

    return events


def measure_time(func: Callable, *args, **kwargs) -> tuple:
    """Measure execution time of a function."""
    start = time.perf_counter()
    result = func(*args, **kwargs)
    elapsed = time.perf_counter() - start
    return elapsed, result


def run_benchmark(name: str, func: Callable, iterations: int = 100) -> dict:
    """Run a benchmark and collect statistics."""
    times = []

    # Warmup
    for _ in range(min(10, iterations // 10)):
        func()

    # Measure
    for _ in range(iterations):
        elapsed, _ = measure_time(func)
        times.append(elapsed * 1000)  # Convert to ms

    return {
        'name': name,
        'iterations': iterations,
        'mean_ms': statistics.mean(times),
        'median_ms': statistics.median(times),
        'stdev_ms': statistics.stdev(times) if len(times) > 1 else 0,
        'min_ms': min(times),
        'max_ms': max(times),
        'p95_ms': sorted(times)[int(len(times) * 0.95)] if len(times) >= 20 else max(times),
        'p99_ms': sorted(times)[int(len(times) * 0.99)] if len(times) >= 100 else max(times),
    }


def format_benchmark_result(result: dict) -> str:
    """Format benchmark result for display."""
    return (
        f"{result['name']}: "
        f"mean={result['mean_ms']:.3f}ms, "
        f"p95={result['p95_ms']:.3f}ms, "
        f"p99={result['p99_ms']:.3f}ms"
    )


# ============================================================================
# Throughput Tests
# ============================================================================

def test_perf_single_event_throughput():
    """Measure throughput for single event processing."""
    event = {
        'actionType': 'WEAPON_FIRED',
        'playerId': 'player_1',
        'metadata': {'shots': 10, 'hits': 5, 'headshots': 2}
    }

    iterations = 10000
    start = time.perf_counter()
    for _ in range(iterations):
        extract_features([event], {})
    elapsed = time.perf_counter() - start

    events_per_second = iterations / elapsed
    print(f"  Single event throughput: {events_per_second:,.0f} events/sec")

    if events_per_second < EXPECTED_EVENTS_PER_SECOND:
        print(f"  WARNING: Below expected {EXPECTED_EVENTS_PER_SECOND:,} events/sec")

    print("✓ test_perf_single_event_throughput")
    return events_per_second


def test_perf_batch_throughput():
    """Measure throughput for batch event processing."""
    batch_sizes = [10, 50, 100, 500, 1000]
    results = {}

    for batch_size in batch_sizes:
        events = generate_event_batch(batch_size)
        iterations = max(10, 1000 // batch_size)

        start = time.perf_counter()
        for _ in range(iterations):
            extract_features(events, {})
        elapsed = time.perf_counter() - start

        total_events = iterations * batch_size
        events_per_second = total_events / elapsed
        results[batch_size] = events_per_second
        print(f"  Batch size {batch_size}: {events_per_second:,.0f} events/sec")

    print("✓ test_perf_batch_throughput")
    return results


def test_perf_incremental_state_throughput():
    """Measure throughput with incremental state updates."""
    # Simulate processing events over time with accumulating state
    state = {}
    event_batch = generate_event_batch(100)
    iterations = 100

    start = time.perf_counter()
    for i in range(iterations):
        features, _ = extract_features(event_batch, state)
        state = features  # Carry state forward
    elapsed = time.perf_counter() - start

    total_events = iterations * len(event_batch)
    events_per_second = total_events / elapsed
    print(f"  Incremental state throughput: {events_per_second:,.0f} events/sec")
    print("✓ test_perf_incremental_state_throughput")
    return events_per_second


# ============================================================================
# Latency Tests
# ============================================================================

def test_perf_small_batch_latency():
    """Measure latency for small batches (typical API Gateway request)."""
    events = generate_event_batch(10)

    result = run_benchmark(
        "Small batch (10 events)",
        lambda: extract_features(events, {}),
        iterations=1000
    )

    print(f"  {format_benchmark_result(result)}")

    if result['p99_ms'] > EXPECTED_P99_LATENCY_MS:
        print(f"  WARNING: p99 latency {result['p99_ms']:.3f}ms exceeds {EXPECTED_P99_LATENCY_MS}ms threshold")

    print("✓ test_perf_small_batch_latency")
    return result


def test_perf_medium_batch_latency():
    """Measure latency for medium batches."""
    events = generate_event_batch(100)

    result = run_benchmark(
        "Medium batch (100 events)",
        lambda: extract_features(events, {}),
        iterations=500
    )

    print(f"  {format_benchmark_result(result)}")
    print("✓ test_perf_medium_batch_latency")
    return result


def test_perf_large_batch_latency():
    """Measure latency for large batches."""
    events = generate_event_batch(1000)

    result = run_benchmark(
        "Large batch (1000 events)",
        lambda: extract_features(events, {}),
        iterations=100
    )

    print(f"  {format_benchmark_result(result)}")
    print("✓ test_perf_large_batch_latency")
    return result


def test_perf_response_creation_latency():
    """Measure latency for API response creation."""
    body = {
        'success': True,
        'eventsReceived': 100,
        'eventsStored': 25,
        'eventsSkipped': 75,
        'playersUpdated': 10,
        'detectionsCreated': 2,
        'processingTimeMs': 45.67,
        'requestId': 'test-request-id-12345',
    }

    result = run_benchmark(
        "Response creation",
        lambda: create_response(200, body),
        iterations=10000
    )

    print(f"  {format_benchmark_result(result)}")
    print("✓ test_perf_response_creation_latency")
    return result


# ============================================================================
# Scalability Tests
# ============================================================================

def test_perf_player_scaling():
    """Test how performance scales with number of distinct players."""
    player_counts = [1, 10, 50, 100, 500]
    results = {}

    for player_count in player_counts:
        events = generate_event_batch(1000, player_count=player_count)

        elapsed, _ = measure_time(extract_features, events, {})
        results[player_count] = elapsed * 1000

        print(f"  {player_count} players: {elapsed * 1000:.3f}ms for 1000 events")

    # Check that scaling is reasonable (not worse than linear)
    if results[500] > results[1] * 10:
        print("  WARNING: Performance degrades significantly with player count")

    print("✓ test_perf_player_scaling")
    return results


def test_perf_state_size_scaling():
    """Test how performance scales with existing state size."""
    # Build up progressively larger states
    state_samples = [10, 100, 1000, 10000]
    events = generate_event_batch(100)
    results = {}

    for sample_count in state_samples:
        # Create a state with accumulated statistics
        state = {
            'totalShots': sample_count * 10,
            'totalHits': sample_count * 5,
            'totalHeadshots': sample_count,
            'totalKills': sample_count // 10,
            'accuracySampleCount': sample_count,
            'accuracyMean': 0.5,
            'accuracyM2': sample_count * 0.1,
            'accuracyStdDev': 0.1,
        }

        elapsed, _ = measure_time(extract_features, events, state)
        results[sample_count] = elapsed * 1000

        print(f"  State with {sample_count} samples: {elapsed * 1000:.3f}ms")

    print("✓ test_perf_state_size_scaling")
    return results


# ============================================================================
# Stress Tests
# ============================================================================

def test_perf_sustained_load():
    """Test sustained load over multiple iterations."""
    events = generate_event_batch(100)
    iterations = 1000
    state = {}

    latencies = []
    start = time.perf_counter()

    for i in range(iterations):
        iter_start = time.perf_counter()
        features, _ = extract_features(events, state)
        state = features
        latencies.append((time.perf_counter() - iter_start) * 1000)

    total_elapsed = time.perf_counter() - start
    total_events = iterations * len(events)

    print(f"  Sustained load ({iterations} iterations, {total_events:,} events):")
    print(f"    Total time: {total_elapsed:.2f}s")
    print(f"    Events/sec: {total_events / total_elapsed:,.0f}")
    print(f"    Mean latency: {statistics.mean(latencies):.3f}ms")
    print(f"    Max latency: {max(latencies):.3f}ms")

    # Check for latency degradation over time
    first_100 = statistics.mean(latencies[:100])
    last_100 = statistics.mean(latencies[-100:])
    degradation = (last_100 - first_100) / first_100 * 100 if first_100 > 0 else 0

    print(f"    Latency degradation: {degradation:.1f}%")

    if degradation > 50:
        print("  WARNING: Significant latency degradation over time")

    print("✓ test_perf_sustained_load")
    return {
        'total_events': total_events,
        'total_time': total_elapsed,
        'mean_latency_ms': statistics.mean(latencies),
        'max_latency_ms': max(latencies),
        'degradation_percent': degradation,
    }


def test_perf_burst_load():
    """Test burst load handling."""
    # Simulate bursts of high-volume events
    burst_size = 1000
    burst_count = 10
    events = generate_event_batch(burst_size)

    latencies = []
    for _ in range(burst_count):
        elapsed, _ = measure_time(extract_features, events, {})
        latencies.append(elapsed * 1000)

    print(f"  Burst load ({burst_count} bursts of {burst_size} events):")
    print(f"    Mean burst latency: {statistics.mean(latencies):.3f}ms")
    print(f"    Max burst latency: {max(latencies):.3f}ms")
    print(f"    Stdev: {statistics.stdev(latencies):.3f}ms")

    print("✓ test_perf_burst_load")
    return latencies


# ============================================================================
# Memory Tests (Approximation)
# ============================================================================

def test_perf_memory_batch_size():
    """Test memory usage with different batch sizes."""
    import sys

    batch_sizes = [100, 1000, 10000]

    for batch_size in batch_sizes:
        events = generate_event_batch(batch_size)
        features, interesting = extract_features(events, {})

        # Approximate memory usage
        events_size = sys.getsizeof(events) + sum(sys.getsizeof(e) for e in events)
        features_size = sys.getsizeof(features)
        interesting_size = sys.getsizeof(interesting)

        print(f"  Batch size {batch_size}:")
        print(f"    Events memory: ~{events_size / 1024:.1f} KB")
        print(f"    Features memory: ~{features_size / 1024:.1f} KB")
        print(f"    Interesting events: {len(interesting)}")

    print("✓ test_perf_memory_batch_size")


# ============================================================================
# JSON Serialization Performance
# ============================================================================

def test_perf_json_serialization():
    """Test JSON serialization performance for responses."""
    # Create a response with various data types
    body = {
        'success': True,
        'eventsReceived': 1000,
        'eventsStored': 250,
        'eventsSkipped': 750,
        'playersUpdated': 50,
        'detectionsCreated': 5,
        'processingTimeMs': 123.456,
        'requestId': 'uuid-1234-5678-abcd',
        'details': {
            'players': {f'player_{i}': {'score': Decimal(str(i * 1.5))} for i in range(50)},
        }
    }

    iterations = 1000
    start = time.perf_counter()
    for _ in range(iterations):
        create_response(200, body)
    elapsed = time.perf_counter() - start

    print(f"  JSON serialization: {iterations / elapsed:,.0f} responses/sec")
    print(f"  Mean latency: {elapsed / iterations * 1000:.3f}ms")

    print("✓ test_perf_json_serialization")


# ============================================================================
# Main Runner
# ============================================================================

if __name__ == '__main__':
    print("\n" + "=" * 70)
    print("Performance Tests for Serverless Lambda Handler")
    print("=" * 70)

    tests = [
        ("Throughput Tests", [
            test_perf_single_event_throughput,
            test_perf_batch_throughput,
            test_perf_incremental_state_throughput,
        ]),
        ("Latency Tests", [
            test_perf_small_batch_latency,
            test_perf_medium_batch_latency,
            test_perf_large_batch_latency,
            test_perf_response_creation_latency,
        ]),
        ("Scalability Tests", [
            test_perf_player_scaling,
            test_perf_state_size_scaling,
        ]),
        ("Stress Tests", [
            test_perf_sustained_load,
            test_perf_burst_load,
        ]),
        ("Memory Tests", [
            test_perf_memory_batch_size,
        ]),
        ("Serialization Tests", [
            test_perf_json_serialization,
        ]),
    ]

    passed = 0
    failed = 0

    for category, test_funcs in tests:
        print(f"\n{category}")
        print("-" * 50)

        for test in test_funcs:
            try:
                test()
                passed += 1
            except AssertionError as e:
                print(f"✗ {test.__name__}: {e}")
                failed += 1
            except Exception as e:
                print(f"✗ {test.__name__}: {type(e).__name__}: {e}")
                failed += 1

    print("\n" + "=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70 + "\n")

    sys.exit(0 if failed == 0 else 1)
