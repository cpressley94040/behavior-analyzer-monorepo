#!/bin/bash
# Run all tests for the serverless infrastructure
# Includes CDK tests, Lambda unit tests, fuzz tests, and performance tests

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CDK_DIR="$(dirname "$SCRIPT_DIR")"
LAMBDA_DIR="$CDK_DIR/lambda/processor"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Track results
TESTS_PASSED=0
TESTS_FAILED=0
FAILED_SUITES=()

print_header() {
    echo ""
    echo -e "${BLUE}════════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════════════${NC}"
    echo ""
}

print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✓ $2 PASSED${NC}"
        ((TESTS_PASSED++))
    else
        echo -e "${RED}✗ $2 FAILED${NC}"
        ((TESTS_FAILED++))
        FAILED_SUITES+=("$2")
    fi
}

# Parse arguments
RUN_PERF=false
VERBOSE=false
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --perf|--performance) RUN_PERF=true ;;
        -v|--verbose) VERBOSE=true ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --perf, --performance  Include performance tests (slower)"
            echo "  -v, --verbose          Show verbose output"
            echo "  -h, --help             Show this help message"
            exit 0
            ;;
        *) echo "Unknown parameter: $1"; exit 1 ;;
    esac
    shift
done

# Change to CDK directory
cd "$CDK_DIR"

print_header "Running All Serverless Infrastructure Tests"

# ============================================================================
# CDK Infrastructure Tests (Jest)
# ============================================================================
print_header "CDK Infrastructure Tests (Jest)"

if $VERBOSE; then
    npm test -- --verbose 2>&1
    RESULT=$?
else
    npm test 2>&1
    RESULT=$?
fi
print_result $RESULT "CDK Infrastructure Tests"

# ============================================================================
# Lambda Unit Tests
# ============================================================================
print_header "Lambda Unit Tests"

cd "$LAMBDA_DIR"

if $VERBOSE; then
    python3 test_handler.py 2>&1
    RESULT=$?
else
    python3 test_handler.py 2>&1 | tail -10
    RESULT=${PIPESTATUS[0]}
fi
print_result $RESULT "Lambda Unit Tests"

# ============================================================================
# Lambda Detection Tests
# ============================================================================
print_header "Lambda Detection Tests"

if $VERBOSE; then
    python3 test_detection.py 2>&1
    RESULT=$?
else
    python3 test_detection.py 2>&1 | tail -10
    RESULT=${PIPESTATUS[0]}
fi
print_result $RESULT "Lambda Detection Tests"

# ============================================================================
# Lambda Fuzz Tests
# ============================================================================
print_header "Lambda Fuzz Tests"

if $VERBOSE; then
    python3 test_fuzz.py 2>&1
    RESULT=$?
else
    python3 test_fuzz.py 2>&1 | tail -10
    RESULT=${PIPESTATUS[0]}
fi
print_result $RESULT "Lambda Fuzz Tests"

# ============================================================================
# Lambda Performance Tests (Optional)
# ============================================================================
if $RUN_PERF; then
    print_header "Lambda Performance Tests"

    if $VERBOSE; then
        python3 test_performance.py 2>&1
        RESULT=$?
    else
        python3 test_performance.py 2>&1 | tail -20
        RESULT=${PIPESTATUS[0]}
    fi
    print_result $RESULT "Lambda Performance Tests"
else
    echo ""
    echo -e "${YELLOW}Skipping performance tests (use --perf to include)${NC}"
fi

# ============================================================================
# CDK Synthesis Validation
# ============================================================================
print_header "CDK Synthesis Validation"

cd "$CDK_DIR"

if $VERBOSE; then
    npx cdk synth -a "npx ts-node bin/serverless-app.ts" 2>&1
    RESULT=$?
else
    npx cdk synth -a "npx ts-node bin/serverless-app.ts" --quiet 2>&1
    RESULT=$?
fi
print_result $RESULT "CDK Synthesis"

# ============================================================================
# Summary
# ============================================================================
print_header "Test Summary"

TOTAL=$((TESTS_PASSED + TESTS_FAILED))

echo "Total test suites: $TOTAL"
echo -e "  ${GREEN}Passed: $TESTS_PASSED${NC}"
echo -e "  ${RED}Failed: $TESTS_FAILED${NC}"
echo ""

if [ $TESTS_FAILED -gt 0 ]; then
    echo -e "${RED}Failed suites:${NC}"
    for suite in "${FAILED_SUITES[@]}"; do
        echo "  - $suite"
    done
    echo ""
    exit 1
else
    echo -e "${GREEN}All tests passed!${NC}"
    echo ""
    exit 0
fi
