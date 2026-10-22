# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 5.x | Yes |
| 4.x and earlier | No |

The Windows build is currently Beta. Security fixes are prioritized for the
latest 5.x release on Linux and Windows.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Use GitHub's private vulnerability reporting for this repository:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability**.
3. Include the affected version, platform, installation method, reproduction
   steps, impact, and any relevant logs or proof of concept.

If private reporting is unavailable, contact the maintainer privately through
GitHub (`@Chillalot`). Do not include secrets, personal recordings, or other
sensitive data in a report.

## What to expect

- We aim to acknowledge a report within 72 hours.
- We will confirm the issue, determine the affected versions, and prepare a
  fix and coordinated disclosure when appropriate.
- We will credit reporters who want credit after the fix is published.
- Please give us a reasonable opportunity to investigate and release a fix
  before public disclosure.

## Scope

In scope:

- Code execution or privilege escalation in the Linux packages, AppImage, or
  Windows installer.
- Unsafe update download or checksum verification behavior.
- Path traversal or unsafe file handling in media/config code.
- Exposure of camera, microphone, recordings, configuration, or logs to an
  unintended party.
- Dependency or packaging issues that affect the release artifacts.

Out of scope:

- Vulnerabilities in a user-installed OBS Studio, GStreamer, PipeWire, GTK, or
  kernel package that Center Stage does not control. Report those upstream.
- Physical access attacks, social engineering, or denial of service against a
  local desktop session.
- The Windows Beta's lack of code signing as a vulnerability by itself. It is a
  known limitation; verify the SHA-256 checksum before running releases.
- Reports that require the user to run untrusted code or replace the application
  binary with an attacker-controlled build.

## Release integrity

Every release publishes SHA-256 checksums in `SHA256SUMS.txt`,
`SHA256SUMS-linux.txt`, and `SHA256SUMS-windows.txt`. The AppImage update path
also supports a `.zsync` file. Compare downloaded artifacts with the checksums
on the GitHub Release before installing or running them.

The release workflow builds artifacts from the tagged source in GitHub Actions.
Maintainer credentials and release tokens are never shipped in the app.
