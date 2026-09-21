#!/usr/bin/env bash
# Downloads the EU digital-law test corpus (19 documents) from the EU Publications
# Office CELLAR service, which serves official consolidated texts over plain HTTP
# content negotiation. Do NOT use eur-lex.europa.eu URLs -- that host sits behind
# an AWS WAF captcha and cannot be scripted.
#
# Output: .html files, which SurfSense parses locally via the DIRECT_CONVERT path
# (app/etl_pipeline/file_classifier.py) -- no LlamaCloud/Unstructured/OCR needed.
#
# Usage: ./download.sh [output-dir]     (default: ./files)
set -euo pipefail

OUT="${1:-$(dirname "$0")/files}"
mkdir -p "$OUT"
BASE="http://publications.europa.eu/resource/celex"

# celex | accept format | output filename
DOCS='
32016R0679|xhtml|01-gdpr-reg-2016-679.html
32018R1725|xhtml|02-eudpr-reg-2018-1725.html
31995L0046|html |03-data-protection-directive-95-46.html
32002L0058|html |04-eprivacy-directive-2002-58.html
62018CJ0311|xhtml|05-schrems-ii-judgment-c-311-18.html
32023D1795|xhtml|06-eu-us-data-privacy-framework-adequacy-2023-1795.html
32022R0868|xhtml|07-data-governance-act-reg-2022-868.html
32023R2854|xhtml|08-data-act-reg-2023-2854.html
32018R1807|xhtml|09-free-flow-non-personal-data-reg-2018-1807.html
32022R2065|xhtml|10-digital-services-act-reg-2022-2065.html
32022R1925|xhtml|11-digital-markets-act-reg-2022-1925.html
32024R1689|xhtml|12-ai-act-reg-2024-1689.html
32016L1148|xhtml|13-nis1-directive-2016-1148.html
32022L2555|xhtml|14-nis2-directive-2022-2555.html
32022R2554|xhtml|15-dora-reg-2022-2554.html
32024R2847|xhtml|16-cyber-resilience-act-reg-2024-2847.html
32019R0881|xhtml|17-cybersecurity-act-reg-2019-881.html
32014R0910|xhtml|18-eidas-reg-2014-910.html
32024R1183|xhtml|19-eidas2-reg-2024-1183.html
'

fail=0
while IFS='|' read -r celex fmt name; do
  [ -z "${celex// }" ] && continue
  celex="${celex// }"; fmt="${fmt// }"; name="${name// }"
  case "$fmt" in
    xhtml) accept="application/xhtml+xml" ;;
    *)     accept="text/html" ;;
  esac
  code=$(curl -sL --max-time 120 --retry 2 \
    -H "Accept: $accept" -H "Accept-Language: eng" \
    -o "$OUT/$name" -w '%{http_code}' "$BASE/$celex")
  size=$(wc -c < "$OUT/$name" | tr -d ' ')
  if [ "$code" = "200" ] && [ "$size" -gt 50000 ]; then
    printf '  ok   %-56s %8s bytes\n' "$name" "$size"
  else
    printf '  FAIL %-56s http=%s size=%s\n' "$name" "$code" "$size"
    fail=1
  fi
done <<< "$DOCS"

echo
echo "Corpus in: $OUT"
exit $fail
