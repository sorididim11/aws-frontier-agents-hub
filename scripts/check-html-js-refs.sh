#!/bin/bash
# HTML id와 JS getElementById 참조 불일치 검사
set -e

HTML="services/dashboard/templates/index.html"
JS="services/dashboard/static/js/app.js"

echo "=== HTML/JS 참조 불일치 검사 ==="

# HTML에서 id 추출
HTML_IDS=$(grep -o 'id="[^"]*"' "$HTML" | sed 's/id="//;s/"//' | sort -u)

# JS에서 getElementById 참조 추출
JS_REFS=$(grep -o "getElementById('[^']*')" "$JS" | sed "s/getElementById('//;s/')//" | sort -u)

# JS에서 참조하는데 HTML에 없는 id
MISSING=0
for ref in $JS_REFS; do
    if ! echo "$HTML_IDS" | grep -q "^${ref}$"; then
        echo "❌ JS에서 참조하지만 HTML에 없음: $ref"
        grep -n "getElementById.*$ref" "$JS" | head -3 | sed 's/^/   /'
        MISSING=$((MISSING + 1))
    fi
done

if [ $MISSING -eq 0 ]; then
    echo "✅ 모든 JS 참조가 HTML에 존재합니다."
else
    echo ""
    echo "❌ $MISSING개 불일치 발견"
    exit 1
fi
