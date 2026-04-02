---
paths:
  - "mes_intel/ui/**/*.py"
---

# UI Development Rules
- Use QFont.SpacingType.AbsoluteSpacing (NOT QFont.LetterSpacingType)
- NeonLineChart does NOT accept y_label parameter
- All colors should follow the cyberpunk neon aesthetic (dark backgrounds, neon accents)
- Use Menlo font for monospace text
- Session times are in America/Phoenix timezone (UTC-7, no DST)
- Value Area is 40%, not 70%
- Test with `python3 -c "from mes_intel.ui.app import MainWindow; print('OK')"` before launching
