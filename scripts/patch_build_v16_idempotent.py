from pathlib import Path


path = Path(__file__).resolve().parent / "build_sportmaster_full_quality_v16.py"
text = path.read_text(encoding="utf-8")
if 'BASE_HTML = OUTPUT_DIR / "Спортмастер_карта_брендов_визуальный_бенчмарк_v15.html"' not in text:
    text = text.replace(
        'MAIN_HTML = OUTPUT_DIR / "Спортмастер_интерактивный_бенчмарк_дашборд.html"\n',
        'MAIN_HTML = OUTPUT_DIR / "Спортмастер_интерактивный_бенчмарк_дашборд.html"\nBASE_HTML = OUTPUT_DIR / "Спортмастер_карта_брендов_визуальный_бенчмарк_v15.html"\n',
        1,
    )
text = text.replace(
    'for required in (MAIN_HTML, VISUAL_PATH, COVERAGE_PATH, LEGACY_VISUAL_PATH, OVERLAY_PATH):',
    'for required in (BASE_HTML, VISUAL_PATH, COVERAGE_PATH, LEGACY_VISUAL_PATH, OVERLAY_PATH):',
    1,
)
text = text.replace('html = MAIN_HTML.read_text(encoding="utf-8")', 'html = BASE_HTML.read_text(encoding="utf-8")', 1)
path.write_text(text, encoding="utf-8")
print("patched idempotent v15 base")
