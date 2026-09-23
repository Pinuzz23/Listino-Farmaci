from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
targets = [root / "data" / "listino.db", root / "archive", root / "backup"]

print("ATTENZIONE: questa operazione cancella il database listino locale e gli archivi della demo.")
confirm = input("Scrivi RESET per continuare: ").strip()
if confirm != "RESET":
    print("Operazione annullata.")
    raise SystemExit(0)

for target in targets:
    if target.is_file():
        target.unlink(missing_ok=True)
    elif target.is_dir():
        shutil.rmtree(target, ignore_errors=True)

(root / "archive").mkdir(exist_ok=True)
(root / "backup").mkdir(exist_ok=True)
print("Demo azzerata. Il database verra ricreato al prossimo avvio.")
