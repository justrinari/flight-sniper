#!/usr/bin/env bash
# Достаёт data/history.sqlite из ветки data. Если ветки нет — тихо стартуем с нуля.
set -euo pipefail

DB_PATH="${DB_PATH:-data/history.sqlite}"
BRANCH="${DATA_BRANCH:-data}"

mkdir -p "$(dirname "$DB_PATH")"

if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  git fetch --depth 1 origin "$BRANCH"
  git show "FETCH_HEAD:$(basename "$DB_PATH")" > "$DB_PATH"
  echo "db_pull: восстановлено $(wc -c < "$DB_PATH") байт из ветки $BRANCH"
else
  echo "db_pull: ветки $BRANCH ещё нет, стартуем с пустой базы"
fi
