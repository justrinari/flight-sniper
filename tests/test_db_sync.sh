#!/usr/bin/env bash
# Проверяет цикл push → pull на паре локальных репозиториев.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

git init --bare -q "$WORK/remote.git"
git init -q "$WORK/local"
cd "$WORK/local"
git remote add origin "$WORK/remote.git"
git config user.email t@t; git config user.name t
mkdir -p data scripts
cp "$ROOT/scripts/db_pull.sh" "$ROOT/scripts/db_push.sh" scripts/
echo placeholder > README.md
git add -A && git commit -qm init && git push -q origin HEAD:main

# Ветки data ещё нет — pull должен пройти без ошибки
bash scripts/db_pull.sh > /dev/null
[ ! -s data/history.sqlite ] || { echo "FAIL: база не должна была появиться"; exit 1; }

printf 'first' > data/history.sqlite
bash scripts/db_push.sh "first" > /dev/null

rm -f data/history.sqlite
bash scripts/db_pull.sh > /dev/null
[ "$(cat data/history.sqlite)" = "first" ] || { echo "FAIL: контент не совпал"; exit 1; }

printf 'second' > data/history.sqlite
bash scripts/db_push.sh "second" > /dev/null
COUNT="$(git --git-dir="$WORK/remote.git" rev-list --count data)"
[ "$COUNT" = "1" ] || { echo "FAIL: в ветке data $COUNT коммитов, ожидался 1"; exit 1; }

rm -f data/history.sqlite
bash scripts/db_pull.sh > /dev/null
[ "$(cat data/history.sqlite)" = "second" ] || { echo "FAIL: не обновилось"; exit 1; }

echo "OK: db sync"
