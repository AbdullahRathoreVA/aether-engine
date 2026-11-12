"""Build dist/aether-engine-skill.zip: SKILL.md at the root plus the engine.

    python scripts/package_skill.py

Upload the zip to any SKILL.md marketplace, or unzip into ~/.claude/skills/.
Ships only the modules the CLI imports, and swaps the owner's personal
defaults (author, showcase, contact) for neutral ones so a buyer's pages never
advertise the seller. Never includes .env, state/ or generated pages.
"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "aether-engine-skill.zip"
MODULES = ("__init__ brain cli config db generator llm net publisher "
           "scorer seo sources").split()
# (file, personal default, neutral default) - asserted, so drift fails loudly.
NEUTRAL = [
    ("engine/config.py", 'env_bool("SHOWCASE_ENABLED", True)',
     'env_bool("SHOWCASE_ENABLED", False)'),
    ("engine/config.py", '"Muhammad Abdullah Rathore"', '""'),
    ("engine/config.py", '"mailto:abdullahrathore.va@gmail.com"', '""'),
    ("engine/config.py", '"https://careermind2026-project-titan-omega.hf.space"', '""'),
    ("engine/config.py", '"SHOWCASE_NAME", "Titan Omega"', '"SHOWCASE_NAME", ""'),
    (".env.example", "SHOWCASE_NAME=Titan Omega", "SHOWCASE_NAME="),
    (".env.example", "SHOWCASE_ENABLED=1", "SHOWCASE_ENABLED=0"),
    (".env.example", "SHOWCASE_URL=https://careermind2026-project-titan-omega.hf.space",
     "SHOWCASE_URL="),
]


def source(rel: str) -> str:
    text = (ROOT / rel).read_text(encoding="utf-8")
    for f, old, new in NEUTRAL:
        if f == rel:
            assert text.count(old) == 1, f"{rel}: expected one {old!r}"
            text = text.replace(old, new)
    return text


files = {"SKILL.md": "skill/SKILL.md", ".env.example": ".env.example",
         **{f"engine/{m}.py": f"engine/{m}.py" for m in MODULES}}
OUT.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for arc, rel in files.items():
        z.writestr(arc, source(rel))
print(f"{OUT} ({len(files)} files, {OUT.stat().st_size // 1024} KB)")
