#!/usr/bin/env bash
#
# local-deploy.sh - Deploy Behavior Analyzer locally for development
#
# Usage:
#   ./scripts/local-deploy.sh          # Start all services
#   ./scripts/local-deploy.sh start    # Start all services
#   ./scripts/local-deploy.sh stop     # Stop all services
#   ./scripts/local-deploy.sh status   # Check service status
#   ./scripts/local-deploy.sh restart  # Restart all services
#   ./scripts/local-deploy.sh logs     # Show service logs
#
# Requirements:
#   - Docker (for LocalStack and Redis)
#   - Bazel (for C++ backend)
#   - Node.js/npm (for frontend)

set -e

# =============================================================================
# Configuration
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BACKEND_DIR="${PROJECT_ROOT}/backend"
FRONTEND_DIR="${PROJECT_ROOT}/frontend"
DEPLOY_DIR="${BACKEND_DIR}/deploy"

# PID files for tracking background processes
PID_DIR="${PROJECT_ROOT}/.local-deploy"
BACKEND_PID_FILE="${PID_DIR}/backend.pid"
FRONTEND_PID_FILE="${PID_DIR}/frontend.pid"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# =============================================================================
# Helper Functions
# =============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_command() {
    if ! command -v "$1" &> /dev/null; then
        log_error "$1 is not installed. Please install it first."
        exit 1
    fi
}

ensure_pid_dir() {
    mkdir -p "${PID_DIR}"
}

wait_for_service() {
    local url=$1
    local name=$2
    local max_attempts=${3:-30}
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        if curl -s "$url" > /dev/null 2>&1; then
            return 0
        fi
        sleep 1
        ((attempt++))
    done
    return 1
}

# =============================================================================
# Service Management Functions
# =============================================================================

start_docker_services() {
    log_info "Starting Docker services (LocalStack + Redis)..."

    cd "${DEPLOY_DIR}"

    # Check if containers are already running
    if docker ps --format '{{.Names}}' | grep -q "behavior-analyzer-localstack"; then
        log_warn "Docker services already running"
    else
        docker-compose -f docker-compose.yaml up -d

        # Wait for LocalStack to be healthy
        log_info "Waiting for LocalStack to initialize..."
        local attempt=1
        while [ $attempt -le 60 ]; do
            if curl -s http://localhost:4566/_localstack/health | grep -q '"dynamodb": "running"'; then
                break
            fi
            sleep 2
            ((attempt++))
        done

        # Wait for init container to complete
        sleep 5

        log_success "Docker services started"
    fi

    cd "${PROJECT_ROOT}"
}

stop_docker_services() {
    log_info "Stopping Docker services..."
    cd "${DEPLOY_DIR}"
    docker-compose -f docker-compose.yaml down 2>/dev/null || true
    cd "${PROJECT_ROOT}"
    log_success "Docker services stopped"
}

build_backend() {
    log_info "Building C++ backend..."
    cd "${BACKEND_DIR}"

    if bazel build //src/server:behavior_analyzer_server 2>&1 | tail -5; then
        log_success "Backend built successfully"
    else
        log_error "Backend build failed"
        exit 1
    fi

    cd "${PROJECT_ROOT}"
}

start_backend() {
    log_info "Starting C++ backend server..."

    # Check if already running
    if [ -f "${BACKEND_PID_FILE}" ]; then
        local pid=$(cat "${BACKEND_PID_FILE}")
        if kill -0 "$pid" 2>/dev/null; then
            log_warn "Backend server already running (PID: $pid)"
            return 0
        fi
    fi

    cd "${BACKEND_DIR}"

    # Start server in background
    nohup ./bazel-bin/src/server/behavior_analyzer_server \
        --config config/development.yaml \
        > "${PID_DIR}/backend.log" 2>&1 &

    local pid=$!
    echo "$pid" > "${BACKEND_PID_FILE}"

    # Wait for server to be ready
    if wait_for_service "http://localhost:8080/health/live" "backend" 30; then
        log_success "Backend server started (PID: $pid)"
    else
        log_error "Backend server failed to start. Check ${PID_DIR}/backend.log"
        exit 1
    fi

    cd "${PROJECT_ROOT}"
}

stop_backend() {
    log_info "Stopping C++ backend server..."

    if [ -f "${BACKEND_PID_FILE}" ]; then
        local pid=$(cat "${BACKEND_PID_FILE}")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            sleep 2
            # Force kill if still running
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "${BACKEND_PID_FILE}"
    fi

    # Also kill any orphaned processes
    pkill -f "behavior_analyzer_server" 2>/dev/null || true

    log_success "Backend server stopped"
}

install_frontend_deps() {
    log_info "Checking frontend dependencies..."
    cd "${FRONTEND_DIR}"

    if [ ! -d "node_modules" ] || [ "package.json" -nt "node_modules" ]; then
        log_info "Installing frontend dependencies..."
        npm install --silent
        log_success "Frontend dependencies installed"
    else
        log_success "Frontend dependencies up to date"
    fi

    cd "${PROJECT_ROOT}"
}

start_frontend() {
    log_info "Starting frontend dev server..."

    # Check if already running
    if [ -f "${FRONTEND_PID_FILE}" ]; then
        local pid=$(cat "${FRONTEND_PID_FILE}")
        if kill -0 "$pid" 2>/dev/null; then
            log_warn "Frontend server already running (PID: $pid)"
            return 0
        fi
    fi

    cd "${FRONTEND_DIR}"

    # Start Vite dev server in background with Kinesis forwarding enabled
    VITE_KINESIS_FORWARD_ENABLED=true nohup npm run dev -- --host 0.0.0.0 \
        > "${PID_DIR}/frontend.log" 2>&1 &

    local pid=$!
    echo "$pid" > "${FRONTEND_PID_FILE}"

    # Wait for server to be ready
    if wait_for_service "http://localhost:5173" "frontend" 30; then
        log_success "Frontend server started (PID: $pid)"
    else
        log_error "Frontend server failed to start. Check ${PID_DIR}/frontend.log"
        exit 1
    fi

    cd "${PROJECT_ROOT}"
}

stop_frontend() {
    log_info "Stopping frontend dev server..."

    if [ -f "${FRONTEND_PID_FILE}" ]; then
        local pid=$(cat "${FRONTEND_PID_FILE}")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            sleep 2
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "${FRONTEND_PID_FILE}"
    fi

    # Also kill any orphaned Vite processes for this project
    pkill -f "vite.*behavior_analyzer" 2>/dev/null || true

    log_success "Frontend server stopped"
}

# =============================================================================
# Main Commands
# =============================================================================

cmd_start() {
    echo ""
    echo "========================================"
    echo "  Behavior Analyzer Local Deployment"
    echo "========================================"
    echo ""

    # Check prerequisites
    log_info "Checking prerequisites..."
    check_command docker
    check_command bazel
    check_command npm
    check_command curl

    # Ensure Docker is running
    if ! docker info > /dev/null 2>&1; then
        log_error "Docker is not running. Please start Docker first."
        exit 1
    fi

    ensure_pid_dir

    # Start services in order
    start_docker_services
    build_backend
    start_backend
    install_frontend_deps
    start_frontend

    echo ""
    echo "========================================"
    echo "  Deployment Complete!"
    echo "========================================"
    echo ""
    echo "Services running:"
    echo "  - LocalStack (DynamoDB, S3, Kinesis): http://localhost:4566"
    echo "  - Redis:                              localhost:6379"
    echo "  - C++ Backend:                        http://localhost:8080"
    echo "  - Frontend:                           http://localhost:5173"
    echo ""
    echo "Useful endpoints:"
    echo "  - Web App:          http://localhost:5173"
    echo "  - Ingest API:       http://localhost:5173/ingest"
    echo "  - Health Check:     http://localhost:8080/health/ready"
    echo "  - Metrics:          http://localhost:9090/metrics"
    echo ""
    echo "Logs:"
    echo "  - Backend:  ${PID_DIR}/backend.log"
    echo "  - Frontend: ${PID_DIR}/frontend.log"
    echo ""
    echo "To stop: ./scripts/local-deploy.sh stop"
    echo ""
}

cmd_stop() {
    echo ""
    log_info "Stopping all services..."
    echo ""

    stop_frontend
    stop_backend
    stop_docker_services

    echo ""
    log_success "All services stopped"
    echo ""
}

cmd_restart() {
    cmd_stop
    sleep 2
    cmd_start
}

cmd_status() {
    echo ""
    echo "========================================"
    echo "  Service Status"
    echo "========================================"
    echo ""

    # LocalStack
    echo -n "LocalStack:     "
    if curl -s http://localhost:4566/_localstack/health | grep -q '"dynamodb": "running"'; then
        echo -e "${GREEN}Running${NC}"
        echo "                - DynamoDB: $(curl -s http://localhost:4566/_localstack/health | grep -o '"dynamodb": "[^"]*"')"
        echo "                - S3:       $(curl -s http://localhost:4566/_localstack/health | grep -o '"s3": "[^"]*"')"
        echo "                - Kinesis:  $(curl -s http://localhost:4566/_localstack/health | grep -o '"kinesis": "[^"]*"')"
    else
        echo -e "${RED}Not Running${NC}"
    fi

    # Redis
    echo -n "Redis:          "
    if docker exec behavior-analyzer-redis redis-cli ping 2>/dev/null | grep -q "PONG"; then
        echo -e "${GREEN}Running${NC}"
    else
        echo -e "${RED}Not Running${NC}"
    fi

    # Backend
    echo -n "C++ Backend:    "
    if curl -s http://localhost:8080/health/live > /dev/null 2>&1; then
        echo -e "${GREEN}Running${NC}"
        local node_id=$(curl -s http://localhost:8080/health/live | grep -o '"node_id":"[^"]*"' | cut -d'"' -f4)
        echo "                - Node ID: $node_id"
    else
        echo -e "${RED}Not Running${NC}"
    fi

    # Frontend
    echo -n "Frontend:       "
    if curl -s http://localhost:5173 > /dev/null 2>&1; then
        echo -e "${GREEN}Running${NC}"
        echo "                - URL: http://localhost:5173"
    else
        echo -e "${RED}Not Running${NC}"
    fi

    echo ""
}

cmd_logs() {
    local service=${1:-all}

    case "$service" in
        backend)
            if [ -f "${PID_DIR}/backend.log" ]; then
                tail -f "${PID_DIR}/backend.log"
            else
                log_error "Backend log not found"
            fi
            ;;
        frontend)
            if [ -f "${PID_DIR}/frontend.log" ]; then
                tail -f "${PID_DIR}/frontend.log"
            else
                log_error "Frontend log not found"
            fi
            ;;
        docker|localstack)
            docker-compose -f "${DEPLOY_DIR}/docker-compose.yaml" logs -f
            ;;
        all|*)
            echo "Available log commands:"
            echo "  ./scripts/local-deploy.sh logs backend"
            echo "  ./scripts/local-deploy.sh logs frontend"
            echo "  ./scripts/local-deploy.sh logs docker"
            ;;
    esac
}

cmd_help() {
    echo ""
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  start    Start all services (default)"
    echo "  stop     Stop all services"
    echo "  restart  Restart all services"
    echo "  status   Show service status"
    echo "  logs     Show service logs (backend|frontend|docker)"
    echo "  help     Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0                    # Start all services"
    echo "  $0 status             # Check what's running"
    echo "  $0 logs backend       # Tail backend logs"
    echo "  $0 stop               # Stop everything"
    echo ""
}

# =============================================================================
# Main Entry Point
# =============================================================================

main() {
    local command=${1:-start}

    case "$command" in
        start)
            cmd_start
            ;;
        stop)
            cmd_stop
            ;;
        restart)
            cmd_restart
            ;;
        status)
            cmd_status
            ;;
        logs)
            cmd_logs "$2"
            ;;
        help|--help|-h)
            cmd_help
            ;;
        *)
            log_error "Unknown command: $command"
            cmd_help
            exit 1
            ;;
    esac
}

main "$@"
