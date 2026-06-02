# -----------------------------------------------------------------------------
# Canal Dark - sync-vault.ps1
# Sincroniza as 3 paradas (codigo canonico, copia navegavel, vault Obsidian)
# com PROTECAO DE SEGREDO embutida. O vault e PUBLICO no GitHub -> nunca segredo la.
#
# Uso:  powershell -ExecutionPolicy Bypass -File tools\sync-vault.ps1
#       (ou: botao direito > Run with PowerShell)
# Obs: ASCII-only de proposito (Windows PowerShell 5.1 quebra com acento/emoji).
# -----------------------------------------------------------------------------
$ErrorActionPreference = 'Stop'

$HOME_CODE = 'C:\Users\aless\canal-dark'                    # canonico (codigo que roda)
$ONEDRIVE  = 'C:\Users\aless\OneDrive\Desktop\canal-dark'   # copia navegavel (codigo)
$VAULT     = 'C:\Users\aless\obsidian-vault-1\Canal Dark'   # vault PUBLICO (Obsidian)
$DOCS_NAV  = Join-Path $ONEDRIVE '_docs-obsidian'           # copia navegavel dos docs do vault

# Excludes DUROS de segredo / pesados - valem em toda copia
$XF = @('.env', '.env.*', '*.key', '*.pem', '*.pfx')
$XD = @('.venv', 'out', '__pycache__', '.git', '_segredos', 'node_modules', 'video testes')

function Sync-Dir($src, $dst, $label) {
    if (-not (Test-Path $src)) { Write-Host "  (pulado: nao existe $src)" -ForegroundColor DarkGray; return }
    Write-Host "==> $label" -ForegroundColor Cyan
    robocopy $src $dst /E /XO /R:1 /W:1 /NFL /NDL /NJH /NJS /XF $XF /XD $XD | Out-Null
    if ($LASTEXITCODE -ge 8) { Write-Host "  ATENCAO: robocopy retornou $LASTEXITCODE (erro)" -ForegroundColor Yellow }
    $global:LASTEXITCODE = 0
}

# 1) Codigo canonico -> copia navegavel (sem segredo/venv/out)
Sync-Dir $HOME_CODE $ONEDRIVE 'codigo: home (canonico) -> OneDrive'

# 2) Conhecimento de nicho do repo -> vault publico (docs, sem segredo)
Sync-Dir (Join-Path $HOME_CODE 'nichos') (Join-Path $VAULT 'Nichos') 'nichos: repo -> vault publico'

# 2b) TODOS os docs do projeto -> vault publico (canais, prompts, docs). So documentos,
#     codigo .py fica no repo. A guarda final varre segredo de qualquer forma.
Sync-Dir (Join-Path $HOME_CODE 'canais')  (Join-Path $VAULT 'Canais')  'canais: repo -> vault publico'
Sync-Dir (Join-Path $HOME_CODE 'prompts') (Join-Path $VAULT 'Prompts') 'prompts: repo -> vault publico'
Sync-Dir (Join-Path $HOME_CODE 'docs')    (Join-Path $VAULT 'Docs')    'docs: repo -> vault publico'
Copy-Item (Join-Path $HOME_CODE 'README.md')  (Join-Path $VAULT 'README.md')  -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $HOME_CODE 'CLAUDE.md')  (Join-Path $VAULT 'CLAUDE.md')  -Force -ErrorAction SilentlyContinue

# 3) Vault -> copia navegavel dos docs (pra abrir na Area de Trabalho)
Sync-Dir $VAULT $DOCS_NAV 'docs do vault -> _docs-obsidian'

# -- GUARDA FINAL: nenhum segredo pode ter entrado no vault publico --
# Regex casa VALOR real de chave (>=16 chars), nao placeholder tipo KEY=... ou KEY=<your-key>.
Write-Host "==> Checando segredo no vault publico..." -ForegroundColor Cyan
$pattern = '(?:API_KEY|TOKEN|SECRET|PRIVATE_KEY)\s*[:=]\s*[A-Za-z0-9_\-]{16,}|AIza[0-9A-Za-z_\-]{30,}|sk-[A-Za-z0-9]{20,}|[0-9]{8,10}:[A-Za-z0-9_\-]{30,}'
$hits = Get-ChildItem $VAULT -Recurse -File -Include *.md, *.txt -ErrorAction SilentlyContinue |
        Select-String -Pattern $pattern -ErrorAction SilentlyContinue
if ($hits) {
    Write-Host "  [ALERTA] possivel segredo real no vault - REVISE antes de commitar:" -ForegroundColor Red
    foreach ($h in $hits) { Write-Host ("    " + $h.Path + ":" + $h.LineNumber) -ForegroundColor Red }
} else {
    Write-Host "  OK - nenhum segredo detectado no vault." -ForegroundColor Green
}

Write-Host ""
Write-Host "Sync concluido." -ForegroundColor Green
