#!/usr/bin/env bash
# ----------------------------------------------------------------
# run.sh — rescrape-dispatcher 컨테이너를 실행한다.
#
# 사용법:
#   ./deploy/run.sh <worker_id>
#
# 인자:
#   worker_id  컨테이너를 구별하는 고유 이름. 로그 파일명에 포함된다.
#              여러 컨테이너를 띄울 때 각각 다른 이름을 사용한다.
#
# 예시:
#   ./deploy/run.sh rescrape-1
#   ./deploy/run.sh rescrape-2
#
# APP_ENV 환경변수:
#   서버에 'export APP_ENV=dev' 를 .bashrc 에 설정해두면 자동으로 읽힌다.
#   설정하지 않은 경우 기본값은 "dev".
# ----------------------------------------------------------------

set -e

WORKER_ID="${1}"

if [[ -z "${WORKER_ID}" ]]; then
    echo "오류: worker_id 인자가 필요합니다."
    echo ""
    echo "사용법: $0 <worker_id>"
    echo ""
    echo "예시:"
    echo "  $0 rescrape-1"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

APP_ENV="${APP_ENV:-dev}"
ENV_FILE="${PROJECT_ROOT}/.env.${APP_ENV}"

LOG_DIR="${HOME}/apps/data/rescrape-dispatcher/logs"
WATERMARK_DIR="${HOME}/apps/data/rescrape-dispatcher/watermark"

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "오류: 환경 설정 파일을 찾을 수 없습니다: ${ENV_FILE}"
    echo "  APP_ENV=${APP_ENV} 로 실행 중입니다."
    echo "  서버에 .env.${APP_ENV} 파일이 있는지 확인하세요."
    exit 1
fi

mkdir -p "${LOG_DIR}"
mkdir -p "${WATERMARK_DIR}"

CONTAINER_NAME="${WORKER_ID}"

if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "▶ 기존 컨테이너 제거: ${CONTAINER_NAME}"
    docker rm -f "${CONTAINER_NAME}"
fi

IMAGE="rescrape-dispatcher:latest"

echo "▶ 컨테이너 시작: ${CONTAINER_NAME}"
echo "  이미지    : ${IMAGE}"
echo "  환경설정  : ${ENV_FILE}"
echo "  로그      : ${LOG_DIR}"
echo "  워터마크  : ${WATERMARK_DIR} (DB 조회 모드 전용, 직접 모드는 미사용)"
echo ""

docker run \
    --detach \
    --name "${CONTAINER_NAME}" \
    --user "$(id -u):$(id -g)" \
    --restart unless-stopped \
    --env-file "${ENV_FILE}" \
    -e APP_ENV="${APP_ENV}" \
    -e WORKER_ID="${WORKER_ID}" \
    -e WATERMARK_DIR="/app/watermark" \
    -v "${LOG_DIR}:/app/logs" \
    -v "${WATERMARK_DIR}:/app/watermark" \
    "${IMAGE}" \
    python -m app

echo "✓ 시작 완료: ${CONTAINER_NAME}"
echo ""
echo "확인 명령어:"
echo "  실시간 로그   → docker logs -f ${CONTAINER_NAME}"
echo "  상태 확인     → docker ps | grep ${CONTAINER_NAME}"
echo "  컨테이너 중지 → docker stop ${CONTAINER_NAME}"
