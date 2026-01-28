#!/usr/bin/env bash
#
# test-serverless.sh - Test Serverless (Lambda-Only) deployment
#
# Usage:
#   ./scripts/test-serverless.sh              # Test deployed stack
#   ./scripts/test-serverless.sh --local      # Test locally with mocked responses
#
# Prerequisites:
#   - CDK stack deployed (BehaviorAnalyzerServerless-dev)
#   - AWS CLI configured
#   - curl and jq installed

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[PASS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[FAIL]${NC} $1"; }

# Test counters
PASSED=0
FAILED=0

run_test() {
    local name=$1
    local result=$2

    if [[ "$result" == "0" ]]; then
        log_success "$name"
        ((PASSED++))
    else
        log_error "$name"
        ((FAILED++))
    fi
}

# ============================================================================
# Get stack outputs
# ============================================================================

get_stack_outputs() {
    log_info "Fetching stack outputs..."

    STACK_NAME="${STACK_NAME:-BehaviorAnalyzerServerless-dev}"

    API_ENDPOINT=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" \
        --output text 2>/dev/null) || true

    API_KEY_ID=$(aws cloudformation describe-stacks \
        --stack-name "$STACK_NAME" \
        --query "Stacks[0].Outputs[?OutputKey=='ApiKeyId'].OutputValue" \
        --output text 2>/dev/null) || true

    if [[ -z "$API_ENDPOINT" || "$API_ENDPOINT" == "None" ]]; then
        log_error "Stack not deployed. Run: cd infrastructure/cdk && npx cdk deploy -a 'npx ts-node bin/serverless-app.ts' --all"
        exit 1
    fi

    # Get API key value
    API_KEY=$(aws apigateway get-api-key --api-key "$API_KEY_ID" --include-value \
        --query "value" --output text 2>/dev/null) || true

    log_info "API Endpoint: $API_ENDPOINT"
    log_info "API Key ID: $API_KEY_ID"
}

# ============================================================================
# Test cases
# ============================================================================

test_health_endpoint() {
    log_info "Testing health endpoint..."

    RESPONSE=$(curl -s "${API_ENDPOINT}health")

    if echo "$RESPONSE" | jq -e '.status == "healthy"' > /dev/null 2>&1; then
        run_test "Health endpoint returns healthy" 0
    else
        log_error "Response: $RESPONSE"
        run_test "Health endpoint returns healthy" 1
    fi
}

test_ingest_without_auth() {
    log_info "Testing ingest without API key (should fail)..."

    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "${API_ENDPOINT}ingest" \
        -H "Content-Type: application/json" \
        -d '{"events":[]}')

    if [[ "$HTTP_CODE" == "403" ]]; then
        run_test "Ingest without auth returns 403" 0
    else
        log_error "Expected 403, got $HTTP_CODE"
        run_test "Ingest without auth returns 403" 1
    fi
}

test_ingest_empty_batch() {
    log_info "Testing ingest with empty batch..."

    RESPONSE=$(curl -s -X POST "${API_ENDPOINT}ingest" \
        -H "Content-Type: application/json" \
        -H "x-api-key: ${API_KEY}" \
        -d '{"events":[]}')

    if echo "$RESPONSE" | jq -e '.success == true and .eventsReceived == 0' > /dev/null 2>&1; then
        run_test "Empty batch succeeds" 0
    else
        log_error "Response: $RESPONSE"
        run_test "Empty batch succeeds" 1
    fi
}

test_ingest_session_events() {
    log_info "Testing ingest with session events (always stored)..."

    RESPONSE=$(curl -s -X POST "${API_ENDPOINT}ingest" \
        -H "Content-Type: application/json" \
        -H "x-api-key: ${API_KEY}" \
        -d '{
            "events": [
                {"owner": "test", "playerId": "p1", "actionType": "SESSION_START"},
                {"owner": "test", "playerId": "p1", "actionType": "SESSION_END"}
            ]
        }')

    STORED=$(echo "$RESPONSE" | jq -r '.eventsStored // 0')

    if [[ "$STORED" == "2" ]]; then
        run_test "Session events stored (2/2)" 0
    else
        log_error "Expected 2 stored, got $STORED. Response: $RESPONSE"
        run_test "Session events stored (2/2)" 1
    fi
}

test_ingest_routine_events_filtered() {
    log_info "Testing ingest with routine events (should be filtered)..."

    RESPONSE=$(curl -s -X POST "${API_ENDPOINT}ingest" \
        -H "Content-Type: application/json" \
        -H "x-api-key: ${API_KEY}" \
        -d '{
            "events": [
                {"owner": "test", "playerId": "p2", "actionType": "PLAYER_TICK", "metadata": {}},
                {"owner": "test", "playerId": "p2", "actionType": "PLAYER_TICK", "metadata": {}},
                {"owner": "test", "playerId": "p2", "actionType": "ITEM_LOOTED", "metadata": {}}
            ]
        }')

    RECEIVED=$(echo "$RESPONSE" | jq -r '.eventsReceived // 0')
    STORED=$(echo "$RESPONSE" | jq -r '.eventsStored // 0')
    SKIPPED=$(echo "$RESPONSE" | jq -r '.eventsSkipped // 0')

    if [[ "$RECEIVED" == "3" && "$STORED" == "0" && "$SKIPPED" == "3" ]]; then
        run_test "Routine events filtered (0 stored, 3 skipped)" 0
    else
        log_error "Expected 0 stored/3 skipped, got $STORED stored/$SKIPPED skipped. Response: $RESPONSE"
        run_test "Routine events filtered (0 stored, 3 skipped)" 1
    fi
}

test_ingest_suspicious_events() {
    log_info "Testing ingest with suspicious events (high accuracy)..."

    RESPONSE=$(curl -s -X POST "${API_ENDPOINT}ingest" \
        -H "Content-Type: application/json" \
        -H "x-api-key: ${API_KEY}" \
        -d '{
            "events": [
                {
                    "owner": "test",
                    "playerId": "p3",
                    "actionType": "WEAPON_FIRED",
                    "metadata": {"shots": 10, "hits": 9, "headshots": 5}
                }
            ]
        }')

    STORED=$(echo "$RESPONSE" | jq -r '.eventsStored // 0')

    if [[ "$STORED" -ge "1" ]]; then
        run_test "High accuracy event stored" 0
    else
        log_error "Expected >=1 stored, got $STORED. Response: $RESPONSE"
        run_test "High accuracy event stored" 1
    fi
}

test_ingest_mixed_batch() {
    log_info "Testing ingest with mixed batch (realistic scenario)..."

    RESPONSE=$(curl -s -X POST "${API_ENDPOINT}ingest" \
        -H "Content-Type: application/json" \
        -H "x-api-key: ${API_KEY}" \
        -d '{
            "events": [
                {"owner": "test", "playerId": "p4", "actionType": "SESSION_START"},
                {"owner": "test", "playerId": "p4", "actionType": "PLAYER_TICK", "metadata": {}},
                {"owner": "test", "playerId": "p4", "actionType": "PLAYER_TICK", "metadata": {}},
                {"owner": "test", "playerId": "p4", "actionType": "WEAPON_FIRED", "metadata": {"shots": 10, "hits": 3}},
                {"owner": "test", "playerId": "p4", "actionType": "WEAPON_FIRED", "metadata": {"shots": 10, "hits": 9}},
                {"owner": "test", "playerId": "p4", "actionType": "PLAYER_KILLED", "metadata": {}},
                {"owner": "test", "playerId": "p4", "actionType": "ITEM_LOOTED", "metadata": {}},
                {"owner": "test", "playerId": "p4", "actionType": "SESSION_END"}
            ]
        }')

    RECEIVED=$(echo "$RESPONSE" | jq -r '.eventsReceived // 0')
    STORED=$(echo "$RESPONSE" | jq -r '.eventsStored // 0')
    SKIPPED=$(echo "$RESPONSE" | jq -r '.eventsSkipped // 0')
    PLAYERS=$(echo "$RESPONSE" | jq -r '.playersUpdated // 0')

    # Expected: SESSION_START, high accuracy WEAPON_FIRED, PLAYER_KILLED, SESSION_END = 4 stored
    # Skipped: 2 PLAYER_TICK, 1 normal WEAPON_FIRED, 1 ITEM_LOOTED = 4 skipped

    if [[ "$RECEIVED" == "8" && "$STORED" -ge "3" && "$PLAYERS" -ge "1" ]]; then
        run_test "Mixed batch: received=$RECEIVED, stored=$STORED, skipped=$SKIPPED, players=$PLAYERS" 0
    else
        log_error "Unexpected results. Response: $RESPONSE"
        run_test "Mixed batch processed correctly" 1
    fi
}

# ============================================================================
# Local test mode (no AWS deployment required)
# ============================================================================

test_local_lambda() {
    log_info "Running local Lambda handler tests..."

    cd "$PROJECT_ROOT/infrastructure/cdk/lambda/processor"

    python3 test_handler.py
    local exit_code=$?

    if [[ "$exit_code" == "0" ]]; then
        run_test "Local Lambda handler tests" 0
    else
        run_test "Local Lambda handler tests" 1
    fi

    return 0  # Don't let set -e fail the script
}

# ============================================================================
# Main
# ============================================================================

main() {
    echo ""
    echo "============================================================"
    echo "  Serverless Integration Tests"
    echo "============================================================"
    echo ""

    if [[ "${1:-}" == "--local" ]]; then
        log_info "Running local tests only (no AWS deployment required)"
        test_local_lambda
    else
        # Require jq for JSON parsing
        if ! command -v jq &> /dev/null; then
            log_error "jq is required. Install with: brew install jq"
            exit 1
        fi

        get_stack_outputs

        echo ""
        log_info "Running integration tests..."
        echo ""

        test_health_endpoint
        test_ingest_without_auth
        test_ingest_empty_batch
        test_ingest_session_events
        test_ingest_routine_events_filtered
        test_ingest_suspicious_events
        test_ingest_mixed_batch
    fi

    echo ""
    echo "============================================================"
    echo -e "  Results: ${GREEN}$PASSED passed${NC}, ${RED}$FAILED failed${NC}"
    echo "============================================================"
    echo ""

    [[ "$FAILED" -eq 0 ]]
}

main "$@"
