# Run the macro
try:
    section_analyzer.close()
except Exception:
    pass

section_analyzer = LiveSectionAnalyzer()
