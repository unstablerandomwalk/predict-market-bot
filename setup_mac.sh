#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#   predict-market-bot — One-Click Mac Setup (v2 — fixed)
# ═══════════════════════════════════════════════════════════════

set -e

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[•]${NC} $1"; }
success() { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[⚠]${NC} $1"; }
error()   { echo -e "${RED}[✗]${NC} $1"; exit 1; }

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║       predict-market-bot — Mac Setup (v2)                ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

[ ! -f "SKILL.md" ] && error "Run this from inside the predict-market-bot directory"
BOT_DIR=$(pwd)
info "Installing in: $BOT_DIR"

# ── 1. Homebrew — install if missing, ALWAYS load into PATH ───
info "Checking Homebrew..."
if ! command -v brew &>/dev/null; then
    # Try to load it first (may already be installed but not in PATH)
    if [ -f /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv zsh)"
    elif [ -f /usr/local/bin/brew ]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
fi

if ! command -v brew &>/dev/null; then
    warn "Homebrew not found. Installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi

# Load brew into current shell session regardless
if [ -f /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv zsh)"
    # Persist to .zprofile so new terminals work
    grep -q 'brew shellenv' ~/.zprofile 2>/dev/null || \
        echo 'eval "$(/opt/homebrew/bin/brew shellenv zsh)"' >> ~/.zprofile
elif [ -f /usr/local/bin/brew ]; then
    eval "$(/usr/local/bin/brew shellenv)"
fi

command -v brew &>/dev/null && success "Homebrew ready" || error "Homebrew still not found — restart terminal and re-run"

# ── 2. Python 3.12 via Homebrew ───────────────────────────────
info "Installing Python 3.12 via Homebrew..."
brew install python@3.12 --quiet
PYTHON=$(brew --prefix)/bin/python3.12
success "Using Python: $PYTHON ($($PYTHON --version))"

# ── 3. Virtual environment using Python 3.12 ─────────────────
info "Creating virtual environment with Python 3.12..."
rm -rf .venv  # remove old venv if it used system Python 3.9
$PYTHON -m venv .venv
source .venv/bin/activate
success "Virtual environment ready (.venv/)"

# ── 4. Install all Python packages inside the venv ────────────
info "Installing Python packages..."
pip install --upgrade pip --quiet
pip install requests python-dotenv schedule praw anthropic openai google-generativeai --quiet
success "All packages installed"

# py-clob-client needs Python >=3.9.10 — our 3.12 satisfies this
info "Installing Polymarket CLOB client..."
pip install py-clob-client --quiet && success "py-clob-client installed" || \
    warn "py-clob-client failed — Polymarket read-only mode (Kalshi still fully works)"

# ── 5. Directories & .env ─────────────────────────────────────
mkdir -p data logs references
[ ! -f ".env" ] && cp .env.example .env && warn ".env created — add your API keys before running"

# ── 6. Init database ──────────────────────────────────────────
info "Initializing database..."
python -c "import sys; sys.path.insert(0,'.'); from core.database import init_db; init_db()"
success "Database ready at data/trades.db"

# ── 7. Self-test (runs inside venv) ───────────────────────────
info "Running self-test..."
python -c "
import sys; sys.path.insert(0,'.')
from scripts.kelly_size import calculate_position_size
r = calculate_position_size(0.70, 0.55, 1000, 'BUY_YES')
assert r['position_dollars'] > 0
assert r['position_pct_bankroll'] <= 0.05
print(f'  Kelly: \${r[\"position_dollars\"]} ({r[\"contracts\"]} contracts) ✓')
from scripts.validate_risk import RiskValidator
v = RiskValidator()
ok, msg = v.check_stop_file()
print(f'  Risk validator: {msg}')
from core.database import get_performance_summary
s = get_performance_summary()
print(f'  DB: {s.get(\"total\",0)} trades on record ✓')
"
success "Self-test passed"

# ── 8. Node.js + Claude Code CLI ──────────────────────────────
info "Checking Node.js..."
if ! command -v node &>/dev/null; then
    info "Installing Node.js..."
    brew install node --quiet
fi
success "Node.js: $(node --version)"

info "Installing Claude Code CLI..."
npm install -g @anthropic-ai/claude-code --quiet && success "Claude Code installed" || \
    warn "Claude Code install failed — install manually: npm install -g @anthropic-ai/claude-code"

# ── 9. Link skill to Claude Code ──────────────────────────────
SKILLS_DIR="$HOME/.claude/skills"
mkdir -p "$SKILLS_DIR"
rm -f "$SKILLS_DIR/predict-market-bot"
ln -s "$BOT_DIR" "$SKILLS_DIR/predict-market-bot"
success "Skill linked at $SKILLS_DIR/predict-market-bot"

# ── 10. Shell alias ───────────────────────────────────────────
ALIAS="alias pmbot='cd $BOT_DIR && source .venv/bin/activate && python run.py'"
grep -q "alias pmbot=" ~/.zshrc 2>/dev/null || { echo ""; echo "# predict-market-bot"; echo "$ALIAS"; } >> ~/.zshrc
success "Added 'pmbot' alias to ~/.zshrc"

# ── Done ──────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║                 SETUP COMPLETE ✅                        ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""
echo -e "${YELLOW}NEXT STEPS:${NC}"
echo ""
echo "  1. Add your API keys:"
echo "       nano $BOT_DIR/.env"
echo ""
echo "  2. Required keys:"
echo "       ANTHROPIC_API_KEY  → https://console.anthropic.com"
echo "       KALSHI_API_KEY     → https://kalshi.com/developers"
echo "       NEWS_API_KEY       → https://newsapi.org (free tier)"
echo ""
echo "  3. Test it (scan only — no trades, no real money):"
echo "       cd $BOT_DIR"
echo "       source .venv/bin/activate"
echo "       python run.py --mode scan --once"
echo ""
echo "  4. Paper trade (simulated money):"
echo "       python run.py --mode paper --once"
echo ""
echo -e "  ${RED}⚠ Never run --mode live until you have 50+ paper trades${NC}"
echo ""
