#!/usr/bin/env bash
# ----------------------------------------------------------------
# build.sh — Docker 이미지를 빌드한다.
#
# 사용법:
#   ./deploy/build.sh           # 태그를 생략하면 "latest" 로 빌드
#   ./deploy/build.sh v1.2.3    # 버전 태그를 직접 지정
#
# 실행 위치는 어디서든 상관없다. 스크립트가 프로젝트 루트를 자동으로 찾는다.
# ----------------------------------------------------------------

set -e

IMAGE_NAME="rescrape-dispatcher"
TAG="${1:-latest}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "▶ 빌드 시작: ${IMAGE_NAME}:${TAG}"
echo "  프로젝트 루트: ${PROJECT_ROOT}"

docker build \
    -t "${IMAGE_NAME}:${TAG}" \
    "${PROJECT_ROOT}"

echo ""
echo "✓ 빌드 완료: ${IMAGE_NAME}:${TAG}"
echo ""
echo "다음 단계:"
echo "  컨테이너 시작 → ./deploy/run.sh <worker_id>"
echo "  이미지 확인   → docker images ${IMAGE_NAME}"
