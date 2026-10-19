#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

for f in socket-fsm-1-dispatch socket-fsm-2-states socket-fsm-3-interleave; do
  xelatex -interaction=nonstopmode -halt-on-error "$f.tex"
  cp "$f.pdf" "../figures/02-socket-fsm-${f#socket-fsm-}.pdf"
  pdftoppm -png -r 300 -singlefile "$f.pdf" "../figures/02-socket-fsm-${f#socket-fsm-}"
done

pdfjam --paper a4paper --landscape --outfile ../figures/02-socket-fsm-all.pdf \
  ../figures/02-socket-fsm-1-dispatch.pdf \
  ../figures/02-socket-fsm-2-states.pdf \
  ../figures/02-socket-fsm-3-interleave.pdf
rm -f ./*.aux ./*.log ./*.pdf
echo "built: ../figures/02-socket-fsm-*.{png,pdf}"
