# Release checklist

This checklist is for the maintainer. The repository is currently private; do
not make it public, publish a release, or change visibility without the owner's
explicit approval.

## Before tagging

- [ ] All release pull requests are squash-merged to `main`.
- [ ] CI is green on `main` for Linux, the slim AppImage, and Windows.
- [ ] `VERSION`, `center_stage.__version__`, and package metadata agree.
- [ ] `CHANGELOG.md` has the release date and covers every user-visible change.
- [ ] README download names match the release workflow's stable aliases.
- [ ] `LICENSE` and `THIRD_PARTY_NOTICES.md` are current, including the Windows
  GPL components.
- [ ] `po/vi.po` passes `msgfmt --check --check-format`.
- [ ] No generated `build/`, `dist/`, installers, recordings, credentials, or
  personal configuration are staged.
- [ ] A clean clone can run the default test suite.

## Local artifact checks

- [ ] `packaging/build_deb.sh` produces a `.deb` under 75 MiB and `dpkg-deb
  --info` is correct.
- [ ] The `.deb` contains `usr/share/doc/center-stage/copyright` and
  `THIRD_PARTY_NOTICES.md`.
- [ ] The AppImage launches, prints `--version`, imports GTK/GStreamer,
  finds both model files, and loads the Vietnamese catalog.
- [ ] The AppImage contains `usr/share/doc/center-stage/`.
- [ ] The Windows installer and portable ZIP include `LICENSE` and
  `THIRD_PARTY_NOTICES.md`.
- [ ] SHA-256 hashes are recorded for every artifact.

## Repository settings

- [ ] Review repository visibility with the owner.
- [ ] Enable private vulnerability reporting.
- [ ] Protect `main`: require pull requests, require CI, disallow force pushes,
  and allow squash merges only.
- [ ] Enable secret scanning and Dependabot alerts if available.
- [ ] Confirm GitHub Actions permissions are least-privilege and the release
  workflow has `contents: write` only at the job that publishes.
- [ ] Confirm the repository description, topics, README, icon, and social
  preview are set.

## Publish

- [ ] Create and push the annotated tag `v5.0.0`.
- [ ] Let `.github/workflows/release.yml` build and publish the release.
- [ ] Verify the release contains:
  - `center-stage_5.0.0_amd64.deb`
  - `center-stage_amd64.deb`
  - `CenterStage-x86_64.AppImage`
  - `CenterStage-x86_64.AppImage.zsync`
  - `CenterStage-Setup.exe`
  - `CenterStage-win64.zip`
  - `SHA256SUMS.txt`, `SHA256SUMS-linux.txt`, `SHA256SUMS-windows.txt`
- [ ] Download every asset through `releases/latest/download/...` and verify
  checksums.
- [ ] Confirm `SHA256SUMS-linux.txt` includes both `.deb` names and the AppImage.
- [ ] Confirm the release notes call the Windows build **Beta** until hardware
  and OBS validation is complete.

## After publishing

- [ ] Install the `.deb` on a clean VM and confirm the service, `/dev/video10`,
  desktop entry, and icons.
- [ ] Run the AppImage on a clean supported system.
- [ ] Ask the Windows tester to complete
  [WINDOWS_BETA_TESTING.md](WINDOWS_BETA_TESTING.md) and report results.
- [ ] Update the release notes if the Windows validation passes; do not remove
  the Beta label without a recorded hardware pass.
- [ ] Pin a known-issues issue if necessary.
- [ ] Announce the release with the stable download links and checksum
  instructions.
- [ ] Keep the previous release available for rollback.

## If the release workflow fails

- Inspect the failed job before editing the tag. Do not move a published tag.
- If the release exists but an asset is missing, rerun the workflow; the publish
  step uploads with `--clobber`.
- If the tag itself is wrong, delete the release and tag only with owner
  approval, then create a new patch version.
