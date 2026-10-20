#!/usr/bin/env bash
# Sube a main los cambios de actuales.json / sonda.json (si los hay).
git config user.name "CdB bot"
git config user.email "actions@users.noreply.github.com"
git add -A actuales.json sonda.json 2>/dev/null || true
if git diff --cached --quiet; then
  echo "Sin cambios"
else
  git commit -q -m "Datos reales automáticos"
  git pull -q --rebase origin main || true
  git push -q origin HEAD:main || echo "No se pudo subir; se reintenta en la siguiente vuelta"
fi
