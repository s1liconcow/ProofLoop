#!/bin/bash
# cleanup-docker.sh - Kill and clean up old Docker containers from ProofLoop runs

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Default values
DRY_RUN=false
MAX_AGE_MINUTES=""
ONLY_PROOFLOOP=true
CLEAN_IMAGES=false
CLEAN_VOLUMES=false

usage() {
    cat << EOF
Usage: $0 [OPTIONS]

Kill and clean up old Docker containers from ProofLoop runs.

OPTIONS:
    -d, --dry-run           Show what would be done without doing it
    -a, --age MINUTES       Only kill containers older than N minutes
    -p, --all               Clean ALL containers, not just ProofLoop ones
    -i, --images            Also remove unused images
    -v, --volumes           Also remove unused volumes
    -h, --help              Show this help message

EXAMPLES:
    $0                      # Kill all running ProofLoop containers
    $0 -a 30                # Only kill containers older than 30 minutes
    $0 -d                   # Dry run - show what would be killed
    $0 -p -i -v             # Clean ALL containers, images, and volumes
EOF
}

log() {
    echo -e "${GREEN}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -d|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -a|--age)
            MAX_AGE_MINUTES="$2"
            shift 2
            ;;
        -p|--all)
            ONLY_PROOFLOOP=false
            shift
            ;;
        -i|--images)
            CLEAN_IMAGES=true
            shift
            ;;
        -v|--volumes)
            CLEAN_VOLUMES=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Check if docker is available
if ! command -v docker &> /dev/null; then
    error "Docker is not installed or not in PATH"
    exit 1
fi

# Get list of running containers to kill
get_containers() {
    if [[ "$ONLY_PROOFLOOP" == true ]]; then
        # Filter for proofloop images
        docker ps -q --filter "ancestor=proofloop/devperf:latest" 2>/dev/null || true
    else
        # All running containers
        docker ps -q --filter "status=running" 2>/dev/null || true
    fi
}

# Filter containers by age (if MAX_AGE_MINUTES is set)
filter_by_age() {
    local containers="$1"
    
    # If no age filter set, return all containers
    if [[ -z "$MAX_AGE_MINUTES" ]]; then
        echo "$containers"
        return
    fi
    
    local cutoff_time=$(date -d "-${MAX_AGE_MINUTES} minutes" +%s 2>/dev/null || date -v-${MAX_AGE_MINUTES}M +%s)
    
    for container in $containers; do
        # Get container start time
        local start_time=$(docker inspect -f '{{.State.StartedAt}}' "$container" 2>/dev/null | xargs -I {} date -d "{}" +%s 2>/dev/null || echo "0")
        
        if [[ "$start_time" != "0" && "$start_time" -lt "$cutoff_time" ]]; then
            echo "$container"
        fi
    done
}

# Main cleanup logic
main() {
    log "Docker cleanup starting..."
    
    if [[ "$DRY_RUN" == true ]]; then
        warn "DRY RUN MODE - No containers will actually be killed"
    fi
    
    # Get running containers
    log "Finding containers..."
    containers=$(get_containers)
    
    if [[ -z "$containers" ]]; then
        log "No matching running containers found"
    else
        # Filter by age if specified
        target_containers=$(filter_by_age "$containers")
        
        if [[ -z "$target_containers" ]]; then
            if [[ -n "$MAX_AGE_MINUTES" ]]; then
                log "No containers older than ${MAX_AGE_MINUTES} minutes found"
            else
                log "No containers found"
            fi
        else
            container_count=$(echo "$target_containers" | wc -w)
            if [[ -n "$MAX_AGE_MINUTES" ]]; then
                log "Found ${container_count} container(s) older than ${MAX_AGE_MINUTES} minutes to clean up"
            else
                log "Found ${container_count} running container(s) to kill"
            fi
            
            for container in $target_containers; do
                # Get container info for display
                info=$(docker inspect --format='{{.Names}} ({{.Image}}) - started {{.State.StartedAt}}' "$container" 2>/dev/null || echo "$container")
                
                if [[ "$DRY_RUN" == true ]]; then
                    log "[DRY RUN] Would kill: $info"
                else
                    log "Killing: $info"
                    docker kill "$container" 2>/dev/null || true
                    docker rm "$container" 2>/dev/null || true
                fi
            done
        fi
    fi
    
    # Clean up stopped containers
    stopped=$(docker ps -aq --filter "status=exited" 2>/dev/null || true)
    if [[ -n "$stopped" ]]; then
        stopped_count=$(echo "$stopped" | wc -w)
        if [[ "$DRY_RUN" == true ]]; then
            log "[DRY RUN] Would remove ${stopped_count} stopped containers"
        else
            log "Removing ${stopped_count} stopped containers"
            docker rm $stopped 2>/dev/null || true
        fi
    fi
    
    # Clean up unused images if requested
    if [[ "$CLEAN_IMAGES" == true ]]; then
        dangling=$(docker images -q -f "dangling=true" 2>/dev/null || true)
        if [[ -n "$dangling" ]]; then
            if [[ "$DRY_RUN" == true ]]; then
                log "[DRY RUN] Would remove dangling images"
            else
                log "Removing dangling images"
                docker rmi $dangling 2>/dev/null || true
            fi
        fi
    fi
    
    # Clean up volumes if requested
    if [[ "$CLEAN_VOLUMES" == true ]]; then
        if [[ "$DRY_RUN" == true ]]; then
            log "[DRY RUN] Would prune unused volumes"
        else
            log "Pruning unused volumes"
            docker volume prune -f 2>/dev/null || true
        fi
    fi
    
    log "Cleanup complete!"
}

main "$@"
