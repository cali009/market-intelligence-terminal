"""
Firebase Hosting Build & Packaging Script
US + Canada Market Intelligence Platform
Prepares the complete zero-cost static edge package inside public/ for instant deployment via Firebase Hosting.
"""

import shutil
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
FEEDS_DIR = ROOT_DIR / "data" / "feeds"
WEB_DIR = ROOT_DIR / "web"
PUBLIC_DIR = ROOT_DIR / "public"
API_DIR = PUBLIC_DIR / "api"
SYMBOLS_DIR = API_DIR / "symbols"


def build_firebase_bundle():
    print(">>> Building Firebase Hosting distribution bundle in public/...")
    
    # 1. Clean and recreate public/
    if PUBLIC_DIR.exists():
        shutil.rmtree(PUBLIC_DIR)
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    API_DIR.mkdir(parents=True, exist_ok=True)
    SYMBOLS_DIR.mkdir(parents=True, exist_ok=True)

    # 2. Copy index.html
    shutil.copy2(WEB_DIR / "index.html", PUBLIC_DIR / "index.html")
    print(f"    ✓ Copied terminal app: public/index.html ({((WEB_DIR / 'index.html').stat().st_size / 1024):.1f} KB)")

    # 3. Copy core JSON feeds and create extensionless copies
    core_feeds = [
        ("daily_summary.json", "summary.json"),
        ("daily_summary.json", "daily_summary.json"),
        ("daily_summary.json", "summary"),
        ("daily_summary.json", "daily_summary"),
        ("leaderboard.json", "leaderboard.json"),
        ("leaderboard.json", "leaderboard"),
        ("scanners.json", "scanners.json"),
        ("scanners.json", "scanners"),
        ("signals.json", "signals.json"),
        ("signals.json", "signals"),
    ]

    for src_name, dest_name in core_feeds:
        src_file = FEEDS_DIR / src_name
        if src_file.exists():
            dest_file = API_DIR / dest_name
            shutil.copy2(src_file, dest_file)
            print(f"    ✓ Staged feed: /api/{dest_name}")

    # 4. Copy individual symbol files
    feed_symbols_dir = FEEDS_DIR / "symbols"
    symbol_count = 0
    if feed_symbols_dir.exists():
        for sym_file in feed_symbols_dir.glob("*.json"):
            shutil.copy2(sym_file, SYMBOLS_DIR / sym_file.name)
            symbol_count += 1
    print(f"    ✓ Staged {symbol_count} symbol dossiers in public/api/symbols/")

    print("\n" + "=" * 70)
    print(" FIREBASE HOSTING BUNDLE READY")
    print(" Directory: public/")
    print(f" Total files staged: {1 + len(core_feeds) * 2 + symbol_count}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    build_firebase_bundle()
