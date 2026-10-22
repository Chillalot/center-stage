# Changelog

All notable changes to Center Stage are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [5.0.0] - 2026-10-22

### Added

- Native Windows application built with PySide6 and OpenCV Media Foundation.
- Windows virtual camera output through OBS Virtual Camera.
- Windows photo, four-up, and H.264/AAC video capture with FFmpeg.
- Portable Windows installer and portable ZIP distribution.
- Slim Linux AppImage that reuses the system Python 3.12, GTK 3, and
  GStreamer runtime.
- In-app update checker with SHA-256 verification and AppImage zsync support.
- GitHub Actions continuous integration for Linux and Windows.
- Automated GitHub Releases with checksums and stable download aliases.
- MIT license, contributor guide, code of conduct, security policy, issue
  templates, and pull request template.
- Application icon exports for Linux, AppImage, and Windows.

### Changed

- Version is read from a single `VERSION` file across packaging and runtime.
- The engine no longer imports Linux-only modules on Windows.
- Linux package metadata and release documentation now target Center Stage
  5.0.0.

### Fixed

- Configuration paths no longer call Linux-only APIs on Windows.
- Media helpers can be imported without GStreamer on Windows.

## [4.5.1] - 2026-08-13

- Photo Booth media workflow, bilingual UI, and packaging refinements.
