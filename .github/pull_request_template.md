## Summary

<!-- What changed and why? Link the issue with "Fixes #..." when applicable. -->

## User impact

<!-- What can users do now that they could not do before? Include screenshots
for UI changes and migration notes for config or packaging changes. -->

## Validation

<!-- List exact commands and results. Do not claim hardware checks you did not run. -->

- [ ] Linux default suite: `.venv/bin/python -m unittest discover -s tests -v`
- [ ] Vietnamese catalog: `msgfmt --check --check-format -o /dev/null po/vi.po`
- [ ] Windows Qt selftest: `python -m center_stage.gui_qt --selftest`
- [ ] Packaging/installer check when packaging changed
- [ ] Hardware/opt-in checks (describe what ran and what was skipped)

## Checklist

- [ ] The change is focused and does not include generated `build/` or `dist/` files.
- [ ] A regression test was added or updated for behavior changes.
- [ ] Documentation and translations were updated when needed.
- [ ] The PR is based on `main` and is ready for squash merge.
- [ ] I have read the contributing guide and code of conduct.
