#!/usr/bin/env bash
# 등급 이력 백업/복원 — data/history/*.json ↔ data-backup 브랜치
#
# 왜 필요한가: 등급 이력·참고 가격대 검증 기록·발송 기록은 GitHub Actions 캐시
# 한 곳에만 있다. 워크플로가 7일 넘게 멈추거나 캐시가 정리되면 한 번에 사라지고,
# 정확도 점검·적중률·참고 가격대 검증이 모두 처음부터 다시 쌓여야 한다.
#
#   backup  — 실행이 끝난 뒤 data/history/의 JSON을 data-backup 브랜치에 커밋한다.
#             내용이 같으면 커밋하지 않는다. 브랜치는 main과 이력을 공유하지 않는다(orphan).
#   restore — 캐시가 비어 있을 때 data-backup 브랜치의 최신본을 data/history/로 꺼낸다.
#
# 저장소가 공개 상태라 백업도 공개된다. 그래서 평균 매수 단가(avg_cost)가 섞여
# 있으면 백업하지 않는다 — 단가는 config/holdings.json에만 있고 data/history/에는
# 들어가지 않는 것이 정상이지만, 실수로 섞이는 경우를 막는 마지막 안전장치다.
set -euo pipefail

BRANCH="${BACKUP_BRANCH:-data-backup}"
SRC="${HISTORY_DIR:-data/history}"
REMOTE="${BACKUP_REMOTE:-origin}"

backup() {
  shopt -s nullglob
  files=("$SRC"/*.json)
  if [ ${#files[@]} -eq 0 ]; then
    echo "백업할 이력 없음"
    return 0
  fi
  if grep -l '"avg_cost"' "${files[@]}" >/dev/null 2>&1; then
    echo "::error::이력 파일에 평균 단가(avg_cost)가 섞여 있어 공개 백업을 중단합니다"
    return 1
  fi

  wt="$(mktemp -d)/backup"
  if git fetch -q "$REMOTE" "$BRANCH" 2>/dev/null; then
    git worktree add -q -B "$BRANCH" "$wt" FETCH_HEAD
  else
    git worktree add -q --orphan -b "$BRANCH" "$wt"
  fi
  cp "${files[@]}" "$wt"/
  (
    cd "$wt"
    git add -A
    if git diff --cached --quiet; then
      echo "이력 변경 없음 — 백업 생략"
    else
      git -c user.name="market-flow-bot" \
          -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
          commit -q -m "history backup $(date -u +%Y-%m-%dT%H:%MZ)"
      git push -q "$REMOTE" "$BRANCH"
      echo "이력 백업 완료: ${#files[@]}개 파일 → $BRANCH"
    fi
  )
  git worktree remove --force "$wt"
}

restore() {
  if ! git fetch -q "$REMOTE" "$BRANCH" 2>/dev/null; then
    echo "백업 브랜치 없음 — 이력 없이 시작"
    return 0
  fi
  mkdir -p "$SRC"
  git archive FETCH_HEAD | tar -x -C "$SRC"
  n=$(find "$SRC" -maxdepth 1 -name '*.json' | wc -l)
  echo "::warning::캐시가 비어 백업 브랜치에서 이력 ${n}개 파일을 복원했습니다"
}

case "${1:-}" in
  backup)  backup ;;
  restore) restore ;;
  *) echo "usage: $0 backup|restore" >&2; exit 2 ;;
esac
