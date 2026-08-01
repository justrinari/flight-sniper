#!/usr/bin/env bash
# Кладёт базу в ветку data одним orphan-коммитом (без родителя).
# История ветки всегда состоит ровно из одного коммита — репозиторий не растёт.
set -euo pipefail

DB_PATH="${DB_PATH:-data/history.sqlite}"
BRANCH="${DATA_BRANCH:-data}"
MESSAGE="${1:-db update}"

if [ ! -f "$DB_PATH" ]; then
  echo "db_push: $DB_PATH не найден, нечего пушить" >&2
  exit 1
fi

git config user.name "flight-sniper[bot]"
git config user.email "flight-sniper@users.noreply.github.com"

BLOB="$(git hash-object -w "$DB_PATH")"
TMP_INDEX="$(mktemp)"
rm -f "$TMP_INDEX"

GIT_INDEX_FILE="$TMP_INDEX" git update-index --add \
  --cacheinfo "100644,$BLOB,$(basename "$DB_PATH")"
TREE="$(GIT_INDEX_FILE="$TMP_INDEX" git write-tree)"
rm -f "$TMP_INDEX"

COMMIT="$(git commit-tree "$TREE" -m "$MESSAGE")"
git push --force origin "$COMMIT:refs/heads/$BRANCH"
echo "db_push: ветка $BRANCH обновлена ($(wc -c < "$DB_PATH") байт)"
